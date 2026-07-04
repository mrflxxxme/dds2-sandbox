'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { dropCommittedRows } from '@/lib/utils/assemblyDraftReconcile';
import { parseBoxSize, effectiveBoxesPerPallet, maxPalletHeightCm, packMonoPallets, MONO_MAX_PALLET_ARTICLES } from '@/lib/utils/boxPallet';
import { normalizeDraft, consolidatePrebookWholePallets, reconcileFillWithReserved, type NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';
import { topUpPrebookLooseBoxes, releaseUnfixableLooseBoxes } from '@/lib/utils/assemblyRoundBoxes';
import { allocatePairs } from '@/lib/utils/assemblyPreview';
import { scopedNormalizeDraft, mergeDraftRows, directionKey } from '@/lib/utils/scopedNormalizeDraft';
import { palletFootprint, planTopUpBoxes, type TopUpCandidate } from '@/lib/assembly/prebookFootprint';
import { applyAcceptanceRedistToPrebook } from '@/lib/assembly/prebookRedistribute';
import { Toast } from '@/components';
import TabLayout from '@/components/TabLayout';
import DraftPreview from './components/DraftPreview';
import AddFromNeedPanel from './components/AddFromNeedPanel';
import { WarehouseNeedView } from '../../analytics/components/WarehouseNeedView';
import { BoxMultiplicityView } from '../../box-multiplicity/BoxMultiplicityView';
import { PalletSizesView } from '../../pallet-sizes/PalletSizesView';
import ForecastView from './components/ForecastView';
import PreDistributionView from './components/PreDistributionView';
import PrebookView, { type PrebookGroup, type PrebookTopUp, type PrebookAcceptanceMark, type PrebookMonoPallet } from './components/PrebookView';
import { WarehouseExclusionSettings } from '../../analytics/components/WarehouseExclusionSettings';
import type {
    AssemblyDraft,
    AssemblyDraftDistribution,
    AssemblyDraftRow,
    PackageType,
    HandedUnit,
    StockNeedResponse,
    Warehouse,
    AcceptanceCheckPerItem,
    AcceptanceFlags,
} from '@/types/api';

const AUTOSAVE_DEBOUNCE_MS = 5000;

type AssemblyTab = 'draft' | 'need' | 'box' | 'pallets' | 'forecast' | 'settings' | 'pre-dist' | 'prebook';
const TABS: { key: AssemblyTab; label: string }[] = [
    { key: 'draft', label: '📝 Черновик сборки' },
    { key: 'need', label: '🏬 Потребность по складам' },
    { key: 'box', label: '📦 Кратность' },
    { key: 'pallets', label: '🚚 Паллеты' },
    { key: 'pre-dist', label: '🚚 Предраспределение' },
    { key: 'prebook', label: '🅿️ Предбронь' },
    { key: 'forecast', label: '📊 Прогноз / Локализация' },
    { key: 'settings', label: '⚙️ Настройки складов' },
];

/** Схлопнуть дубли строк по (nm_id, упаковка, баркод), оставляя первую.
 *  Баркод В КЛЮЧЕ обязателен (зеркало backend `_dedupe_rows`): одна WB-карточка
 *  (nm_id) может нести несколько баркодов (размерные варианты), а карточка без
 *  article_wb уходит в nm_id=0 — без баркода в ключе разные физические товары
 *  схлопнулись бы в один и при сохранении/коммите потерялись/уехали не туда. */
function dedupeRows(rows: AssemblyDraftRow[]): AssemblyDraftRow[] {
    const seen = new Set<string>();
    return rows.filter(r => {
        // as_is в ключе (зеркало backend `_dedupe_rows`): частичная «Оставить так»
        // и обычная строка того же SKU — разные строки, keep-first их не схлопывает.
        const k = `${r.nm_id}-${r.package_type || 'BOX'}-${r.barcode || ''}-${r.as_is ? 1 : 0}`;
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
    });
}

/** Единая страница «Сборка»: вкладки Черновик / Потребность по складам / Кратность /
 *  Паллеты. Черновик — синглтон проекта (getOrCreateCurrentDraft), редактор строк +
 *  предпросмотр и создание заявок на одном экране. */
export default function AssemblyDraftPage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const slug = params.slug as string;

    // Активная вкладка ← из ?tab=. useSearchParams пуст на 1-м рендере — дефолт 'draft'.
    const tabParam = searchParams.get('tab');
    const activeTab: AssemblyTab =
        tabParam === 'need' || tabParam === 'box' || tabParam === 'pallets' || tabParam === 'forecast' || tabParam === 'settings' || tabParam === 'pre-dist' || tabParam === 'prebook'
            ? tabParam
            : 'draft';

    const setTab = useCallback((key: string) => {
        const sp = new URLSearchParams(searchParams.toString());
        if (key === 'draft') sp.delete('tab');
        else sp.set('tab', key);
        const qs = sp.toString();
        router.replace(qs ? `?${qs}` : `/p/${slug}/warehouse/assembly/distribute`, { scroll: false });
    }, [searchParams, router, slug]);

    // ─── State ───────────────────────────────────────────────────────────
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    const [draft, setDraft] = useState<AssemblyDraft | null>(null);
    const [name, setName] = useState('');
    const [editingName, setEditingName] = useState(false);
    const [comment, setComment] = useState('');
    const [estimatedReadyDate, setEstimatedReadyDate] = useState<string>('');
    const [palletsCount, setPalletsCount] = useState<number>(1);
    const [palletWeightKg, setPalletWeightKg] = useState<number>(0);

    const [sourceWarehouseIds, setSourceWarehouseIds] = useState<number[]>([]);
    const [targetWarehouseNames, setTargetWarehouseNames] = useState<string[]>([]);
    const [rows, setRows] = useState<AssemblyDraftRow[]>([]);
    const [prebook, setPrebook] = useState<AssemblyDraftRow[]>([]);
    const [nmPpb, setNmPpb] = useState<Map<number, number | null>>(new Map());
    /** Кратность per (nm × ФФ-склад) — короб может отличаться по складам (22 Хамза / 30 Газпром). */
    const [nmPpbByWh, setNmPpbByWh] = useState<Map<number, Record<number, number>>>(new Map());
    const [nmBoxSize, setNmBoxSize] = useState<Map<number, string | null>>(new Map());
    const [nmMeta, setNmMeta] = useState<Map<number, { subject: string; brand: string }>>(new Map());
    const [palletOverrides, setPalletOverrides] = useState<Record<string, number>>({});
    const [geomState, setGeomState] = useState<'loading' | 'ready' | 'error'>('loading');
    const [coldStartShares, setColdStartShares] = useState<Record<string, number> | null>(null);
    const [handedUnits, setHandedUnits] = useState<HandedUnit[]>([]);
    const [newcomerNmIds, setNewcomerNmIds] = useState<Set<number>>(new Set());

    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [stockNeed, setStockNeed] = useState<StockNeedResponse | null>(null);

    // Приёмка WB для предброни (⌛ на карточках) — грузится при наличии предброни.
    const [prebookAcceptance, setPrebookAcceptance] = useState<Map<number, AcceptanceCheckPerItem>>(new Map());
    const [prebookAccLoading, setPrebookAccLoading] = useState(false);
    const prebookAccSigRef = useRef<string>('');
    const prebookAccFetchingRef = useRef(false);
    // Тик автообновления приёмки (10 мин = TTL пер-баркод кэша бэка): входит в
    // сигнатуру проверки → бейджи не протухают в долгой сессии без изменений.
    const [accRefreshTick, setAccRefreshTick] = useState(0);
    // Whitelist складов, куда можно делать предзаявку без лимита приёмки (для кнопки «Создать предзаявку»).
    const [preorderWbs, setPreorderWbs] = useState<Set<string>>(new Set());
    const [preorderLoaded, setPreorderLoaded] = useState(false);
    const redistBusyRef = useRef(false);
    const redistSigRef = useRef<string>('');

    const lastSavedJsonRef = useRef<string>('');
    const initialLoadRef = useRef(false);

    // id текущего черновика (синглтон). null до первой загрузки.
    const draftId = draft?.id ?? null;

    const showToast = useCallback((message: string, type: 'success' | 'error') => setToast({ message, type }), []);

    // Провенанс «из предброни»: набор `${nm_id}::${wb}`, чей контент попал в rows из
    // предброни. Живёт в ref (не в rows — normalizeDraft пересобирает строки и стёр бы
    // флаг), персистится в distribution.prebook_origin. Обновляется хендлерами prebook→
    // draft ПЕРЕД updateAssemblyDraft (buildDistribution читает ref), сбрасывается на re-fill.
    const prebookOriginRef = useRef<Set<string>>(new Set());

    // Сужение self-heal до тронутых направлений `${pkg}::${wb}`, привязанное к версии
    // черновика (`updated_at`). Пер-направленческий хендлер ставит scope ПЕРЕД applyDraft;
    // self-heal читает его ТОЛЬКО если версия совпала → нормализует лишь тронутое (пустой
    // набор = вычитающая операция «На ФФ»/«Удалить» → no-op). На любую иную версию (загрузка,
    // внешний reload, PUT самого self-heal) scope не совпадёт → ПОЛНЫЙ проход (сеть
    // безопасности инварианта). additive-хендлер после своего PUT кладёт scope на новую
    // версию, subtractive — пустой scope, чтобы трейлинг-проход self-heal не рескан-нул всё.
    const healScopeRef = useRef<{ ts: string; only: Set<string> } | null>(null);

    // ─── Apply a draft payload into state (initial load + reload after commit) ─
    const applyDraft = useCallback((d: AssemblyDraft) => {
        setDraft(d);
        setName(d.name);
        setComment(d.comment || '');
        setEstimatedReadyDate(d.distribution.estimated_ready_date || '');
        setPalletsCount(d.distribution.pallets_count || 1);
        setPalletWeightKg(d.distribution.pallet_weight_kg || 0);
        setSourceWarehouseIds(d.distribution.source_warehouse_ids || []);
        setTargetWarehouseNames(d.distribution.target_warehouse_names || []);
        setRows(dedupeRows(d.distribution.rows || []));
        setColdStartShares(d.distribution.cold_start_shares || null);
        setHandedUnits(d.distribution.handed_units || []);
        setPrebook(d.distribution.prebook || []);
        prebookOriginRef.current = new Set(d.distribution.prebook_origin || []);
        setNewcomerNmIds(new Set(d.newcomer_nm_ids || []));
        lastSavedJsonRef.current = JSON.stringify(d.distribution);
    }, []);

    // ─── Load current draft (singleton) + reference data ─────────────────
    useEffect(() => {
        let cancelled = false;
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const [draftResp, whs, stockNeedResp] = await Promise.all([
                    api.getOrCreateCurrentDraft(),
                    api.getWarehouses(),
                    api.getStockNeed(14, 14, 'actual').catch(() => null) as Promise<StockNeedResponse | null>,
                ]);
                if (cancelled) return;
                applyDraft(draftResp);
                setWarehouses(whs);
                setStockNeed(stockNeedResp);
                initialLoadRef.current = true;
            } catch (e: unknown) {
                if (!cancelled) setError(e instanceof Error ? e.message : 'Ошибка загрузки черновика');
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        load();
        return () => { cancelled = true; };
    }, [applyDraft]);

    // Кратность короба + размер + предмет/бренд per nm (для редактора и предпросмотра).
    const loadGeometry = useCallback((cancelledRef?: { current: boolean }) => {
        api.getBoxMultiplicity()
            .then(resp => {
                if (cancelledRef?.current) return;
                const m = new Map<number, number | null>();
                const byWh = new Map<number, Record<number, number>>();
                const sizes = new Map<number, string | null>();
                const meta = new Map<number, { subject: string; brand: string }>();
                for (const r of resp.items) {
                    let ppb: number | null = null;
                    if (r.box_qty_override && r.box_qty_override > 0 && r.use_box_multiplicity) {
                        ppb = r.box_qty_override;
                        // override глобальный — per-ФФ карта не нужна (везде одинаково).
                    } else {
                        let best = 0;
                        let perWh: Record<number, number> | null = null;
                        for (const p of r.per_warehouse) {
                            if (p.box_qty && p.box_qty > 0 && p.use_box_multiplicity) {
                                if (best === 0 || p.box_qty < best) best = p.box_qty;
                                (perWh ??= {})[p.warehouse_id] = p.box_qty;
                            }
                        }
                        ppb = best > 0 ? best : null;
                        // Кратность может отличаться по складам (22 на Хамзе / 30 на Газпроме):
                        // расчёт порций обязан мерить коробом СВОЕГО ФФ, иначе min резал бы
                        // физически целые коробы чужого склада.
                        if (perWh) byWh.set(r.nm_id, perWh);
                    }
                    let boxSize: string | null = null;
                    let bestStock = -1;
                    for (const p of r.per_warehouse) {
                        if (!p.box_size || !parseBoxSize(p.box_size)) continue;
                        if (p.rf_stock > bestStock) { boxSize = p.box_size; bestStock = p.rf_stock; }
                    }
                    m.set(r.nm_id, ppb);
                    sizes.set(r.nm_id, boxSize);
                    meta.set(r.nm_id, { subject: r.subject || '', brand: r.brand || '' });
                }
                setNmPpb(m);
                setNmPpbByWh(byWh);
                setNmBoxSize(sizes);
                setNmMeta(meta);
                setGeomState('ready');
            })
            .catch(() => { if (!cancelledRef?.current) setGeomState('error'); });
    }, []);

    useEffect(() => {
        const cancelled = { current: false };
        loadGeometry(cancelled);
        return () => { cancelled.current = true; };
    }, [loadGeometry]);

    // Возврат с вкладки «Кратность» → перечитать кратности: юзер только что их правил,
    // а карта nmPpb иначе живёт до F5 — расчёт «не видел» свежезаданную кратность
    // (симптом: SKU с только что указанной кратностью всё ещё «без кратности»/россыпью).
    const prevTabRef = useRef<AssemblyTab>(activeTab);
    useEffect(() => {
        if (prevTabRef.current === 'box' && activeTab !== 'box') loadGeometry();
        prevTabRef.current = activeTab;
    }, [activeTab, loadGeometry]);

    useEffect(() => {
        let cancelled = false;
        api.getPalletBoxesBySize()
            .then(ov => { if (!cancelled) setPalletOverrides(ov || {}); })
            .catch(() => { /* best-effort */ });
        return () => { cancelled = true; };
    }, []);

    // ─── Build current distribution snapshot ─────────────────────────────
    const buildDistribution = useCallback((): AssemblyDraftDistribution => ({
        source_warehouse_ids: sourceWarehouseIds,
        target_warehouse_names: targetWarehouseNames,
        rows,
        pallets_count: palletsCount,
        pallet_weight_kg: palletWeightKg,
        estimated_ready_date: estimatedReadyDate || null,
        cold_start_shares: coldStartShares,
        handed_units: handedUnits,
        prebook,
        prebook_origin: [...prebookOriginRef.current],
    }), [sourceWarehouseIds, targetWarehouseNames, rows, palletsCount, palletWeightKg, estimatedReadyDate, coldStartShares, handedUnits, prebook]);

    // ─── Нормализатор инварианта «целые коробы + целые паллеты» ───────────
    // Контекст из текущего состояния страницы: геометрия (ppb/размер), округа,
    // свободный ФФ (доступно − уже в черновике) для добивки коробов вверх. Новинки
    // cold-start (ppb=null) — россыпь (исключение), их не палетизируем и не добиваем.
    const buildNormalizeCtx = useCallback((freshRows: AssemblyDraftRow[]): NormalizeDraftCtx => {
        const inDraft: Record<number, Record<number, number>> = {};
        for (const r of freshRows) {
            const m = (inDraft[r.nm_id] ??= {});
            for (const [ff, q] of Object.entries(r.src)) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0);
        }
        const freeByNm: Record<number, Record<number, number>> = {};
        for (const a of stockNeed?.articles ?? []) {
            const pool: Record<number, number> = {};
            for (const [ff, st] of Object.entries(a.rf_stocks || {})) {
                const free = (st.available || 0) - (inDraft[a.nm_id]?.[Number(ff)] || 0);
                if (free > 0) pool[Number(ff)] = free;
            }
            if (Object.keys(pool).length) freeByNm[a.nm_id] = pool;
        }
        return {
            ppbOf: (nm) => nmPpb.get(nm),
            ppbAt: (nm, ffId) => nmPpbByWh.get(nm)?.[ffId] ?? null,
            boxSizeOf: (nm) => nmBoxSize.get(nm) ?? null,
            overrides: palletOverrides,
            isNewcomer: (nm) => newcomerNmIds.has(nm),
            freeByNm,
        };
    }, [stockNeed, nmPpb, nmPpbByWh, nmBoxSize, palletOverrides, newcomerNmIds]);

    // ЛОКАЛЬНАЯ нормализация (rows+prebook) без I/O — единый конвейер для self-heal
    // (normalizeAndSave) и ЛЮБОГО сохранения (saveDraft/autosave). Инвариант «целые
    // коробы + целые паллеты» обязан держать КАЖДЫЙ PUT, а не только applyDraft-пути:
    // сырой автосейв записывал под-паллетный хвост в rows, и в открытой вкладке его
    // никто не чинил до F5 (ловили живьём: хамза→СПБ Шушары, 9 коробов = 0.9 паллеты
    // висели в черновике «неполной паллетой»).
    const normalizeLocal = useCallback((
        baseRows: AssemblyDraftRow[],
        basePrebook: AssemblyDraftRow[],
        only?: Set<string>,
    ) => {
        // Пул добора (freeByNm) СТРОИТСЯ ОТ ПОЛНОГО черновика (вычитает занятое И rows, И
        // prebook по ВСЕМ направлениям) — даже при сужении `only` конкуренция за короб
        // честная. Сама нормализация — только тронутых `${pkg}::${wb}` (см.
        // scopedNormalizeDraft); `only=undefined` — полный проход (загрузка, saveDraft).
        const ctx = buildNormalizeCtx([...baseRows, ...basePrebook]);
        return scopedNormalizeDraft(baseRows, basePrebook, ctx, only);
    }, [buildNormalizeCtx]);

    // ─── Save draft (manual + autosave) ──────────────────────────────────
    const saveDraft = useCallback(async (silent = false): Promise<boolean> => {
        if (!draftId) return false;
        // Re-shipment guard (stale page / second tab): reconcile against server and
        // drop rows a partial commit already turned into AssemblyRequests.
        let effectiveRows = rows;
        try {
            const server = await api.getAssemblyDraft(draftId);
            const reconciled = dropCommittedRows(rows, server.distribution.rows || []);
            if (reconciled.length !== rows.length) {
                effectiveRows = reconciled;
                setRows(reconciled);
            }
        } catch { /* keep local rows */ }

        // Инвариант на КАЖДЫЙ сейв (включая автосейв): сырые строки не пишем —
        // под-паллетные хвосты уезжают в предбронь тем же конвейером, что self-heal.
        // Без геометрии — сейв как есть (self-heal добьёт на следующей загрузке).
        let effPrebook = prebook;
        if (geomState === 'ready') {
            const norm = normalizeLocal(effectiveRows, prebook);
            if (norm.changed) {
                effectiveRows = norm.rows;
                effPrebook = norm.prebook;
                setRows(norm.rows);
                setPrebook(norm.prebook);
            }
        }
        const dist: AssemblyDraftDistribution = { ...buildDistribution(), rows: effectiveRows, prebook: effPrebook };
        const json = JSON.stringify(dist);
        if (json === lastSavedJsonRef.current && !silent) return true;

        setSaving(true);
        try {
            const updated = await api.updateAssemblyDraft(draftId, { name, distribution: dist, comment: comment || null });
            lastSavedJsonRef.current = JSON.stringify(updated.distribution);
            if (!silent) showToast('Черновик сохранён', 'success');
            return true;
        } catch (e: unknown) {
            showToast(e instanceof Error ? e.message : 'Ошибка сохранения', 'error');
            return false;
        } finally {
            setSaving(false);
        }
    }, [draftId, buildDistribution, name, comment, rows, prebook, geomState, normalizeLocal, showToast]);

    const ensureSaved = useCallback(() => saveDraft(true), [saveDraft]);

    // Перезагрузить текущий черновик (после партиального commit или долива из потребности).
    const reloadDraft = useCallback(async () => {
        try {
            const d = await api.getOrCreateCurrentDraft();
            applyDraft(d);
        } catch { /* ignore */ }
    }, [applyDraft]);

    // Привести черновик `id` к инварианту (целые коробы + целые паллеты + моно ≤3) и
    // сохранить, если раскладка изменилась. Гейт по готовности геометрии (без габаритов
    // нормализация снесла бы строки) → null. `fresh` передаётся вызывающим, у которого
    // уже есть свежий черновик (add/remove возвращают его) — экономит лишний GET. Возвращает
    // финальный черновик (для `applyDraft` вместо ещё одного reload-GET). Бросает наверх.
    const normalizeAndSave = useCallback(async (
        id: number,
        fresh?: AssemblyDraft,
        only?: Set<string>,
    ): Promise<{ changed: boolean; droppedUnits: number; droppedToPrebook: number; prebookFilledUp: number; releasedTotal: number; draft: AssemblyDraft } | null> => {
        if (geomState !== 'ready') return null;
        const base = fresh ?? await api.getAssemblyDraft(id);
        const norm = normalizeLocal(base.distribution.rows || [], base.distribution.prebook || [], only);
        const draft = norm.changed
            ? await api.updateAssemblyDraft(id, { distribution: { ...base.distribution, rows: norm.rows, prebook: norm.prebook } })
            : base;
        return {
            changed: norm.changed,
            droppedUnits: norm.droppedUnits,
            droppedToPrebook: norm.droppedToPrebook,
            prebookFilledUp: norm.prebookFilledUp,
            releasedTotal: norm.releasedTotal,
            draft,
        };
    }, [geomState, normalizeLocal]);

    // ── SELF-HEAL: привести черновик к инварианту «целые коробы + целые паллеты».
    // Прогон на КАЖДОЕ изменение черновика (ключ draftId::updated_at), а не только на
    // загрузку: заполнение/добавление могло проскочить без нормализации (гонка с
    // загрузкой геометрии — ловили живьём), self-heal чинит следом. Идемпотентно:
    // на чистом черновике changed=false → PUT не идёт → цикла нет. Локальные ручные
    // правки НЕ триггерят (autosave не трогает draft-стейт), только applyDraft-пути.
    // Строки `as_is` («Оставить так») нормализатор пропускает насквозь.
    const selfHealKeyRef = useRef<string>('');
    const selfHealBusyRef = useRef(false);
    useEffect(() => {
        if (!draftId || geomState !== 'ready' || loading || selfHealBusyRef.current || redistBusyRef.current) return;
        const key = `${draftId}::${draft?.updated_at || ''}`;
        if (selfHealKeyRef.current === key) return;
        selfHealKeyRef.current = key;
        selfHealBusyRef.current = true;
        // Сужение: scope, взведённый пер-направленческим хендлером на ЭТУ версию черновика
        // → нормализуем лишь тронутые направления. Иначе (загрузка / внешний reload /
        // несовпадение версии) — undefined = полный проход (сеть безопасности инварианта).
        const scope = healScopeRef.current;
        const only = scope && scope.ts === (draft?.updated_at || '') ? scope.only : undefined;
        void (async () => {
            try {
                const res = await normalizeAndSave(draftId, undefined, only);
                if (res?.changed) {
                    // Трейлинг-проход по НОВОЙ версии не должен рескан-нуть весь черновик:
                    // пустой scope на неё → self-heal@новая = no-op (черновик уже целый).
                    healScopeRef.current = { ts: res.draft.updated_at, only: new Set() };
                    applyDraft(res.draft);
                    const parts: string[] = [];
                    if (res.droppedToPrebook > 0) parts.push(`${formatNumber(res.droppedToPrebook, 0)} шт недобора → в предбронь`);
                    if (res.prebookFilledUp > 0) parts.push(`хвосты предброни добиты до целых коробов свободным ФФ (+${formatNumber(res.prebookFilledUp, 0)} шт)`);
                    if (res.releasedTotal > 0) parts.push(`некратные остатки (добить нечем) → на ФФ, без резерва (−${formatNumber(res.releasedTotal, 0)} шт)`);
                    if (parts.length) {
                        showToast(`Черновик приведён к целым коробам и паллетам: ${parts.join(' · ')}`, 'success');
                    }
                }
            } catch { /* best-effort — не блокируем страницу */ }
            finally { selfHealBusyRef.current = false; }
        })();
    }, [draftId, draft, geomState, loading, normalizeAndSave, applyDraft, showToast]);

    // Дозалив строк из панелей A/B: флашим локальные правки, merge на бэке (возвращает
    // обновлённый черновик), нормализуем к целым коробам+паллетам и применяем результат
    // (без лишних reload-GET). Бросает — панель ловит и тостит.
    const handleAddRows = useCallback(async (newRows: AssemblyDraftRow[]) => {
        // ЖЁСТКИЙ ГЕЙТ (зеркало handleFillAllRows): без геометрии не добавляем — сырые
        // некратные строки молча легли бы в черновик.
        if (geomState !== 'ready') throw new Error('Кратности коробов не загружены — обновите страницу и дождитесь загрузки');
        // БЕЗ КРАТНОСТИ — НЕ УЧАСТВУЮТ (правило юзера):
        // SKU без «шт/короб» не добавляется — укажите кратность (вкладка «Кратность»).
        const usable = newRows.filter(r => (nmPpb.get(r.nm_id) || 0) > 0);
        const skippedNoPpb = newRows.length - usable.length;
        if (usable.length === 0) throw new Error('У выбранных артикулов нет кратности короба — заполните вкладку «Кратность»');
        newRows = usable;
        if (skippedNoPpb > 0) showToast(`⚠️ ${formatNumber(skippedNoPpb, 0)} арт. без кратности короба пропущены — укажите кратность (вкладка «Кратность»)`, 'error');
        // Гарантируем id черновика. Если state ещё не прогрузил draft (ремоунт/гонка
        // StrictMode — в логах виден шквал POST /current), тянем синглтон. Иначе add
        // молча возвращался (`return`), а панель ЛОЖНО рапортовала «Добавлено» — POST
        // не уходил, ничего не сохранялось (баг «новинки не добавляются»).
        let id = draftId;
        if (!id) {
            const cur = await api.getOrCreateCurrentDraft();
            applyDraft(cur);
            id = cur.id;
        } else {
            // Флашим локальные правки редактора перед merge (иначе reload их затрёт).
            const ok = await ensureSaved();
            if (!ok) throw new Error('Не удалось сохранить текущие правки перед добавлением');
        }
        // merge возвращает обновлённый черновик → передаём его в нормализатор (без лишнего GET).
        const merged = await api.addAssemblyDraftRows(id, newRows);
        // Инвариант: приводим ВЕСЬ черновик к целым коробам + целым паллетам (короб —
        // смешанные паллеты; моно — ≤3 артикула). Идемпотентно. Best-effort: сбой не блокирует.
        try {
            const norm = await normalizeAndSave(id, merged);
            if (norm === null) {
                // geom ещё не загружена → нормализация пропущена (не молча): предупреждаем.
                showToast('Геометрия коробок ещё грузится — нажмите «Пересчитать», чтобы привести к целым коробам и паллетам', 'error');
                applyDraft(merged);
            } else {
                applyDraft(norm.draft);
            }
        } catch {
            applyDraft(merged); // нормализация упала — показываем хотя бы добавленное
        }
    }, [draftId, geomState, nmPpb, ensureSaved, normalizeAndSave, applyDraft, showToast]);

    // Долив строк из встроенной вкладки «Потребность» → перезагрузить черновик и
    // переключиться на вкладку 'draft', чтобы пользователь увидел добавленное.
    const handleRowsAdded = useCallback(async () => {
        await reloadDraft();
        setTab('draft');
    }, [reloadDraft, setTab]);

    // ── «Заполнить черновик из потребности»: ЗАМЕНИТЬ весь черновик раскладкой, которую
    // считает вкладка «Потребность по складам» (с bump/локализацией) — точное соответствие.
    // Кнопка переключает на вкладку потребности (там монтируется расчёт + грузятся лимиты,
    // показывается скелетон), та строит строки для ВСЕХ SKU и отдаёт сюда через onFillAllRows.
    const [fillSignal, setFillSignal] = useState(0);
    const [filling, setFilling] = useState(false);

    const handleFillFromNeed = useCallback(() => {
        if (filling) return;
        // Гонка «кликнул раньше кратностей»: расчёт потребности идёт десятки секунд, но
        // если геометрия к моменту коллбэка не готова — заполнение всё равно упрётся в
        // жёсткий гейт. Не запускаем расчёт вовсе, пока кратности не загружены.
        if (geomState !== 'ready') {
            showToast(geomState === 'error'
                ? 'Кратности коробов не загрузились — нажмите «Повторить загрузку» в красном баннере'
                : 'Кратности коробов ещё грузятся — подождите пару секунд и нажмите снова', 'error');
            return;
        }
        if (rows.length > 0 && !window.confirm('Заменить весь черновик раскладкой из «Потребность по складам»? Текущие строки и ручные правки будут удалены.')) return;
        // НЕ переключаем вкладку: расчёт идёт в скрытом инстансе WarehouseNeedView под
        // блокирующим оверлеем (иначе прыжок на вкладку + ожидание лимитов сбивали с толку).
        setFilling(true);
        setFillSignal(s => s + 1);
    }, [filling, rows.length, geomState, showToast]);

    const handleFillAllRows = useCallback(async (newRows: AssemblyDraftRow[]) => {
        try {
            if (!draftId) return;
            if (newRows.length === 0) { showToast('В потребности нечего отгрузить', 'error'); return; }
            // ЖЁСТКИЙ ГЕЙТ: без загруженной геометрии заполнение ЗАПРЕЩЕНО — иначе сырые
            // строки потребности (некратные хвосты) молча легли бы в черновик без
            // округления и без сплита в предбронь (ловили живьём: «38 строк россыпью»).
            if (geomState !== 'ready') {
                showToast('Кратности коробов не загружены — обновите страницу и дождитесь загрузки, потом заполняйте', 'error');
                return;
            }
            // БЕЗ КРАТНОСТИ — НЕ УЧАСТВУЮТ В РАСЧЁТЕ (правило юзера): у SKU нет данных
            // «шт/короб» → ни коробами (нечем округлить), ни россыпью (невыгодно) не едет.
            // Такие видны в блоке «Без кратности» на вкладке черновика → «Указать кратность».
            const hasPpb = (nm: number) => (nmPpb.get(nm) || 0) > 0;
            const skippedNoPpb = newRows.filter(r => !hasPpb(r.nm_id));
            const usableRows = newRows.filter(r => hasPpb(r.nm_id));
            if (usableRows.length === 0) { showToast('У всех артикулов потребности нет кратности короба — заполните вкладку «Кратность»', 'error'); return; }
            const skippedUnits = skippedNoPpb.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            // РЕЗЕРВ: зарезервированная предбронь (текущая) ПИНится к своим направлениям —
            // при заполнении сначала кладём эти коробы туда (вычитая из свежей потребности,
            // без задвоения), излишек/устаревшее — отпускается. Итог по (nm,склад)=потребность.
            const seeded = prebook.length ? reconcileFillWithReserved(usableRows, prebook) : usableRows;
            // ЦЕЛЫЕ ПАЛЛЕТЫ в черновик; целые коробы, не собравшие паллету (под-паллетный
            // хвост) → ПРЕДБРОНЬ (не теряем на ФФ). Пересчитывается при каждом заполнении.
            let keptRows = seeded;
            let newPrebook: AssemblyDraftRow[] = [];
            if (geomState === 'ready') {
                const norm = normalizeDraft(seeded, buildNormalizeCtx(seeded));
                keptRows = norm.rows;
                newPrebook = norm.dropped;
            }
            const targetNames = Array.from(new Set(keptRows.flatMap(r => Object.keys(r.tgt))));
            const sourceIds = Array.from(new Set(
                keptRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0)),
            ));
            // Полное заполнение = свежая раскладка: целые паллеты из потребности (не из
            // предброни). Сбрасываем провенанс — новые пометки добавят topup/консолидация.
            prebookOriginRef.current = new Set();
            const dist: AssemblyDraftDistribution = {
                ...buildDistribution(),
                rows: keptRows,
                prebook: newPrebook,
                source_warehouse_ids: sourceIds,
                target_warehouse_names: targetNames,
            };
            const units = keptRows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            const pbUnits = newPrebook.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            const updated = await api.updateAssemblyDraft(draftId, { distribution: dist });
            applyDraft(updated);
            showToast(
                `Черновик: ${formatNumber(keptRows.length, 0)} строк · Σ ${formatNumber(units, 0)} шт`
                + (newPrebook.length ? ` · предбронь ${formatNumber(newPrebook.length, 0)} поз. (${formatNumber(pbUnits, 0)} шт)` : '')
                + (skippedNoPpb.length ? ` · ⚠️ без кратности пропущено ${formatNumber(skippedNoPpb.length, 0)} арт. (${formatNumber(skippedUnits, 0)} шт) — см. блок «Без кратности»` : ''),
                'success',
            );
        } catch (e: unknown) {
            showToast(e instanceof Error ? e.message : 'Ошибка заполнения черновика', 'error');
        } finally {
            setFillSignal(0);   // сброс сигнала, чтобы повторный расчёт не само-запускался
            setFilling(false);
        }
    }, [draftId, prebook, buildDistribution, applyDraft, showToast, geomState, nmPpb, buildNormalizeCtx]);

    // ── БЕЗ КРАТНОСТИ: артикулы со стоком на ФФ, у которых нет данных «шт/короб». ──
    // По правилу юзера НЕ участвуют в расчёте (fill/add их отфильтровывают): россыпью
    // возить невыгодно. Показываем списком с кнопкой «Указать кратность» (вкладка box).
    const noPpbArticles = useMemo(() => {
        if (geomState !== 'ready') return [];
        const out: { nm_id: number; vendor: string; rf: number }[] = [];
        for (const a of stockNeed?.articles ?? []) {
            if ((nmPpb.get(a.nm_id) || 0) > 0) continue;
            const rf = Object.values(a.rf_stocks || {}).reduce((s, st) => s + (st.available || 0), 0);
            if (rf > 0) out.push({ nm_id: a.nm_id, vendor: a.vendor_code || `nm ${a.nm_id}`, rf });
        }
        return out.sort((x, y) => y.rf - x.rf);
    }, [geomState, stockNeed, nmPpb]);

    // ── ЧАСТИЧНАЯ КРАТНОСТЬ: кратность задана НЕ на всех складах, где лежит остаток. ──
    // Товар едет со склада без своей кратности (машинная кратность есть на ЧУЖОМ складе,
    // где остатка нет) → эта порция поедет россыпью/псевдо-кратно. Показываем, чтобы
    // проставить кратность на складе-источнике. `nmPpbByWh` пуст = глобальный override
    // (кратно везде) → не частичная.
    const partialPpbArticles = useMemo(() => {
        if (geomState !== 'ready') return [];
        const out: { nm_id: number; vendor: string; rf: number }[] = [];
        for (const a of stockNeed?.articles ?? []) {
            if ((nmPpb.get(a.nm_id) || 0) <= 0) continue;   // совсем без кратности — соседний блок
            const perWh = nmPpbByWh.get(a.nm_id);
            if (!perWh) continue;                            // глобальный override → кратно везде
            const uncovered = Object.entries(a.rf_stocks || {})
                .filter(([ff, st]) => (st.available || 0) > 0 && !(Number(ff) in perWh));
            if (uncovered.length === 0) continue;            // кратность на всех складах с остатком
            const rf = uncovered.reduce((s, [, st]) => s + (st.available || 0), 0);
            out.push({ nm_id: a.nm_id, vendor: a.vendor_code || `nm ${a.nm_id}`, rf });
        }
        return out.sort((x, y) => y.rf - x.rf);
    }, [geomState, stockNeed, nmPpb, nmPpbByWh]);

    // ── БЕЗ РАЗМЕРА КОРОБКИ: кратность есть, а габаритов нет. Короба собрать можно,
    // паллету — нет (уедет монопаллетой/крупногабаритом). Показываем, чтобы задать размер.
    const noBoxSizeArticles = useMemo(() => {
        if (geomState !== 'ready') return [];
        const out: { nm_id: number; vendor: string; rf: number }[] = [];
        for (const a of stockNeed?.articles ?? []) {
            if ((nmPpb.get(a.nm_id) || 0) <= 0) continue;   // без кратности — соседний блок
            if (nmBoxSize.get(a.nm_id)) continue;           // размер коробки есть
            const rf = Object.values(a.rf_stocks || {}).reduce((s, st) => s + (st.available || 0), 0);
            if (rf > 0) out.push({ nm_id: a.nm_id, vendor: a.vendor_code || `nm ${a.nm_id}`, rf });
        }
        return out.sort((x, y) => y.rf - x.rf);
    }, [geomState, stockNeed, nmPpb, nmBoxSize]);

    // ── ПРЕДБРОНЬ: целые коробы, не собравшие паллету при заполнении. ──
    // Группы предброни: (упаковка × направление), с оценкой возможности дозабора.
    const prebookGroups = useMemo<PrebookGroup[]>(() => {
        if (prebook.length === 0) return [];
        const ffName = new Map<number, string>();
        for (const w of stockNeed?.rf_warehouses ?? []) ffName.set(w.id, w.name);
        const nmVendor = new Map<number, string>();
        const rfAvailByNm = new Map<number, Record<number, number>>();
        for (const a of stockNeed?.articles ?? []) {
            nmVendor.set(a.nm_id, a.vendor_code || '');
            const rec: Record<number, number> = {};
            for (const [ff, st] of Object.entries(a.rf_stocks || {})) rec[Number(ff)] = st.available || 0;
            rfAvailByNm.set(a.nm_id, rec);
        }
        const inUse: Record<number, Record<number, number>> = {};
        for (const r of [...rows, ...prebook]) { const m = (inUse[r.nm_id] ??= {}); for (const [ff, q] of Object.entries(r.src)) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0); }
        // Свободные целые коробы любого box-SKU per ФФ (смешанная паллета — любой SKU),
        // отсортированные по убыванию запаса. Footprint кандидата зависит от склада-цели
        // (высота паллеты), поэтому bpp считаем на месте, ниже — по каждому направлению.
        const freeBoxesByFf = new Map<number, { nmId: number; ppb: number; freeBoxes: number }[]>();
        for (const a of stockNeed?.articles ?? []) {
            const gppb = nmPpb.get(a.nm_id) || 0;
            if (gppb <= 0 || !nmBoxSize.get(a.nm_id)) continue;
            for (const [ff, st] of Object.entries(a.rf_stocks || {})) {
                // Короб кандидата — кратность ЕГО ФФ (может отличаться по складам).
                const ppb = nmPpbByWh.get(a.nm_id)?.[Number(ff)] ?? gppb;
                const boxes = Math.floor(((st.available || 0) - (inUse[a.nm_id]?.[Number(ff)] || 0)) / ppb);
                if (boxes > 0) {
                    const arr = freeBoxesByFf.get(Number(ff)) ?? [];
                    arr.push({ nmId: a.nm_id, ppb, freeBoxes: boxes });
                    freeBoxesByFf.set(Number(ff), arr);
                }
            }
        }
        for (const arr of freeBoxesByFf.values()) arr.sort((x, y) => y.freeBoxes - x.freeBoxes);
        type Acc = { pkg: PackageType; wb: string; ffId: number; items: { nm_id: number; vendor_code: string; ff: string; boxes: number; qty: number; looseUnits: number; ppb: number; freeUnits: number }[]; qty: number };
        const map = new Map<string, Acc>();
        for (const r of prebook) {
            const pkg = r.package_type || 'BOX';
            const gppb = nmPpb.get(r.nm_id) || 0;
            // Разбиваем строку по парам (ФФ→склад) тем же allocatePairs, что и коммит:
            // одна строка может сорсить один склад с НЕСКОЛЬКИХ ФФ — каждая порция идёт
            // в СВОЮ карточку ФФ (иначе весь tgt приписался бы первому ФФ → неверный
            // источник в заявке и завышенный footprint группы).
            for (const [pairKey, q] of allocatePairs(r.src, r.tgt)) {
                if ((q || 0) <= 0) continue;
                const sep = pairKey.indexOf('::');
                const ffId = Number(pairKey.slice(0, sep));
                const wb = pairKey.slice(sep + 2);
                // Кратность порции — короб ЕЁ ФФ (глобальный min давал псевдо-россыпь).
                const ppb = nmPpbByWh.get(r.nm_id)?.[ffId] ?? gppb;
                const ffLabel = ffId >= 0 ? (ffName.get(ffId) || `ФФ ${ffId}`) : '—';
                const key = `${pkg}::${wb}::${ffId}`;
                let g = map.get(key);
                if (!g) { g = { pkg, wb, ffId, items: [], qty: 0 }; map.set(key, g); }
                // ТОЛЬКО ЦЕЛЫЕ коробы (floor, не round — round маскировал неполный короб:
                // 70/18=3.9→4). Остаток россыпью (`q % ppb`) — не кратен коробу, не едет
                // целым коробом (правило «кратность только коробки»), показываем отдельно.
                const fullBoxes = ppb > 0 ? Math.floor(q / ppb) : 0;
                const looseUnits = ppb > 0 ? q - fullBoxes * ppb : q;
                // freeUnits: свободно этого SKU на ЭТОМ ФФ — «почему остаток не добит»
                // (добор автоматический только когда свободного хватает до целого короба).
                const freeUnits = Math.max(0, (rfAvailByNm.get(r.nm_id)?.[ffId] || 0) - (inUse[r.nm_id]?.[ffId] || 0));
                g.items.push({ nm_id: r.nm_id, vendor_code: r.vendor_code, ff: ffLabel, boxes: fullBoxes, qty: q, looseUnits, ppb, freeUnits });
                g.qty += q;
            }
        }
        const out: PrebookGroup[] = [];
        for (const g of map.values()) {
            // ИСТИННЫЙ footprint смешанной паллеты: КАЖДЫЙ SKU по своей геометрии
            // короба (Σ qty_i / upp_i), зеркало snapToWholePallets. Показ по одному
            // репрезентативному SKU врал до 3× на смешанных группах (замер на живых).
            // Кратность/вместимость — короба ФФ ГРУППЫ (показ = авторитет normalize).
            const ppbOfG = (nm: number): number => nmPpbByWh.get(nm)?.[g.ffId] ?? (nmPpb.get(nm) || 0);
            const uppOf = (nm: number): number => {
                const bpp = effectiveBoxesPerPallet(nmBoxSize.get(nm) ?? null, maxPalletHeightCm(g.wb), palletOverrides);
                const ppb = ppbOfG(nm);
                return bpp && ppb ? bpp * ppb : 0;
            };
            // BOX — объёмный footprint смешанной паллеты (Σ qty_i/upp_i, зеркало snapToWholePallets).
            // МОНО — по ГОТОВЫМ паллетам (packMonoPallets: 100% ИЛИ полный 3-арт слот): объёмный
            // footprint игнорировал ≤3 арт (8 SKU показывались как <1 паллеты, хотя это 3 готовые
            // 3-арт паллеты). Итог = готовых паллет + доля недобранного остатка (в «Дозабить»).
            let footprint: number;
            let monoPallets: PrebookMonoPallet[] | undefined;
            let monoPartials: PrebookMonoPallet[] | undefined;
            let monoTailFrac: number | undefined;
            let pmDropped: Record<string, number> | null = null;
            let monoWhole = 0;
            if (g.pkg === 'MONOPALLET') {
                const km: Record<string, number> = {};
                for (const i of g.items) km[String(i.nm_id)] = (km[String(i.nm_id)] || 0) + i.qty;
                const pm = packMonoPallets(km, (k) => uppOf(Number(k)) || null, MONO_MAX_PALLET_ARTICLES, (k) => ppbOfG(Number(k)));
                const remFrac = Object.entries(pm.dropped).reduce((s, [k, v]) => { const u = uppOf(Number(k)); return s + (u > 0 ? v / u : 0); }, 0);
                // «Целых» = ТОЛЬКО реально упакованные (≤3 арт). Объём недобора (remFrac)
                // может быть >1 паллеты (много мелких хвостов РАЗНЫХ артикулов), но по
                // правилу ≤3 он в целую НЕ собирается — раньше уходил в floor(footprint)
                // и рисовал фантомную «целую» (Воронеж: 8 хвостов = «1 целая», Казань:
                // 6 реальных + 1.6 объёма = «7 целых»). Кап дроби <1 держит floor честным;
                // сырой объём недобора — в monoTailFrac (бейдж на карточке).
                monoWhole = pm.pallets.length;
                pmDropped = pm.dropped;
                monoTailFrac = remFrac;
                footprint = pm.pallets.length + Math.min(0.99, remFrac);
                // Попаллетная раскладка (какой SKU в какой паллете) для карточки предброни —
                // из ТОГО ЖЕ авторитета packMonoPallets, что и footprint/бронь: показ = бронь.
                monoPallets = pm.pallets.map(bin => ({
                    fillPct: Math.min(1, bin.reduce((s, b) => { const u = uppOf(Number(b.key)); return s + (u > 0 ? b.units / u : 0); }, 0)),
                    items: bin.map(b => {
                        const nm = Number(b.key);
                        const ppb = nmPpb.get(nm) || 0;
                        return { nm_id: nm, vendor: nmVendor.get(nm) || `nm ${nm}`, units: b.units, boxes: ppb > 0 ? Math.round(b.units / ppb) : 0 };
                    }),
                }));
                // НЕДОБОР — ТОЖЕ в структуре паллет ≤3 арт (правило WB, строго): хвосты
                // сортируются по убыванию и нарезаются по 3 → это ЧАСТИЧНЫЕ паллеты,
                // которые реально поедут/ждут добора. Никакого «объёма по >3 артикулам».
                const tailsAll = Object.entries(pm.dropped)
                    .map(([k, v]) => { const nm = Number(k); const u = uppOf(nm); const ppb = nmPpb.get(nm) || 0; return { nm, units: v, fp: u > 0 ? v / u : 0, ppb }; })
                    .filter(t => t.units > 0)
                    .sort((a, b) => b.fp - a.fp);
                monoPartials = [];
                for (let i = 0; i < tailsAll.length; i += MONO_MAX_PALLET_ARTICLES) {
                    const chunk = tailsAll.slice(i, i + MONO_MAX_PALLET_ARTICLES);
                    monoPartials.push({
                        fillPct: Math.min(1, chunk.reduce((s, t) => s + t.fp, 0)),
                        items: chunk.map(t => ({ nm_id: t.nm, vendor: nmVendor.get(t.nm) || `nm ${t.nm}`, units: t.units, boxes: t.ppb > 0 ? Math.floor(t.units / t.ppb) : 0 })),
                    });
                }
            } else {
                footprint = palletFootprint(g.items.map(i => ({ nmId: i.nm_id, qty: i.qty })), uppOf);
            }
            const boxes = g.items.reduce((s, i) => s + i.boxes, 0);
            const frac = footprint - Math.floor(footprint);
            const fillPct = footprint > 0 ? Math.min(0.99, frac || 0.99) : 0.5;
            // Дозабор per-ФФ: неполную паллету этого ФФ на wb дособрать до целой из
            // свободных коробов ЭТОГО ЖЕ ФФ (короб с двух ФФ собрать нельзя). Оценка
            // ОПТИМИСТИЧНА (приёмку WB не дёргаем на рендере — сеть); точная проверка
            // приёмки — при клике «Дозабить».
            let topUp: PrebookTopUp | null = null;
            if (g.pkg === 'BOX' && footprint > 0 && g.ffId >= 0) {
                const shortfall = Math.ceil(footprint) - footprint;
                const candidates: TopUpCandidate[] = (freeBoxesByFf.get(g.ffId) ?? []).map(c => ({
                    nmId: c.nmId, ppb: c.ppb, freeBoxes: c.freeBoxes,
                    bpp: effectiveBoxesPerPallet(nmBoxSize.get(c.nmId) ?? null, maxPalletHeightCm(g.wb), palletOverrides),
                }));
                const plan = planTopUpBoxes(shortfall, candidates);
                if (shortfall > 1e-9 && plan.feasible) {
                    topUp = {
                        ff: ffName.get(g.ffId) || `ФФ ${g.ffId}`,
                        needBoxes: plan.needBoxes,
                        pallets: Math.max(1, Math.ceil(footprint)),
                        candidates: plan.rows.map(pr => ({ vendor: nmVendor.get(pr.nmId) || `nm ${pr.nmId}`, boxes: pr.boxes })),
                    };
                }
            }
            // МОНО-хвост: чем дозабрать до ещё одной ЦЕЛОЙ из свободного ФФ. Цель — НЕ
            // объёмная дробь (объём, размазанный по >3 артикулам, в целую не собирается
            // никогда — правило WB «≤3 арт»), а ТОП-3 крупнейших хвоста недобора: добираем
            // коробами ИМЕННО этих артикулов до Σ(top-3) ≥ 1. Кандидаты — только они.
            // Оценка оптимистична (лимит приёмки ⌛ по складу — предзаявкой), точный срез до
            // целых — при «Создать предзаявку»/«Убрать неполную» (consolidatePrebookWholePallets).
            let tailTopUp: PrebookTopUp | null = null;
            if (g.pkg === 'MONOPALLET' && g.ffId >= 0 && pmDropped) {
                const tails = Object.entries(pmDropped)
                    .map(([k, v]) => { const u = uppOf(Number(k)); return { nm: Number(k), fp: u > 0 ? v / u : 0 }; })
                    .filter(t => t.fp > 0)
                    .sort((a, b) => b.fp - a.fp)
                    .slice(0, MONO_MAX_PALLET_ARTICLES);
                const topFp = tails.reduce((s, t) => s + t.fp, 0);
                const shortfall = topFp > 1e-9 ? 1 - topFp : 0;
                if (shortfall > 1e-9) {
                    const topNm = new Set(tails.map(t => t.nm));
                    const cands: TopUpCandidate[] = (freeBoxesByFf.get(g.ffId) ?? [])
                        .filter(c => topNm.has(c.nmId))
                        .map(c => ({
                            nmId: c.nmId, ppb: c.ppb, freeBoxes: c.freeBoxes,
                            bpp: effectiveBoxesPerPallet(nmBoxSize.get(c.nmId) ?? null, maxPalletHeightCm(g.wb), palletOverrides),
                        }));
                    const plan = planTopUpBoxes(shortfall, cands);
                    if (plan.feasible && plan.rows.length > 0) {
                        tailTopUp = {
                            ff: ffName.get(g.ffId) || `ФФ ${g.ffId}`,
                            needBoxes: plan.needBoxes,
                            pallets: monoWhole + 1,
                            candidates: plan.rows.map(pr => ({ vendor: nmVendor.get(pr.nmId) || `nm ${pr.nmId}`, boxes: pr.boxes })),
                        };
                    }
                }
            }
            const ffLabel = ffName.get(g.ffId) || (g.ffId >= 0 ? `ФФ ${g.ffId}` : '—');
            const looseUnits = g.items.reduce((s, i) => s + (i.looseUnits || 0), 0);
            out.push({ pkg: g.pkg, wb: g.wb, ff: ffLabel, ffId: g.ffId, items: g.items, boxes, qty: g.qty, looseUnits, footprint, fillPct, topUp, tailTopUp, monoPallets, monoPartials, monoTailFrac });
        }
        return out;
    }, [prebook, rows, stockNeed, nmPpb, nmPpbByWh, nmBoxSize, palletOverrides]);

    // Направления (упаковка × склад), ГОТОВЫЕ К СДАЧЕ по живой приёмке WB: тип
    // принимается И есть лимит (свободные ИЛИ платные дни > 0). Правило: черновик
    // содержит только готовое к сдаче наполнение — авто-вынос из предброни идёт
    // ТОЛЬКО по таким направлениям (все упаковки, не только моно). Остальное
    // (⌛ без лимита / приём закрыт / нет данных) держится в предброни: моно —
    // под предзаявку, короб — ждать открытия приёмки (или явные «Дозабить»/
    // «Оставить так» по клику). Флаги — уровня склада, берём из любого nm с данными.
    const readyPkgWbs = useMemo<Set<string>>(() => {
        const out = new Set<string>();
        const days = (m?: { free_days_14?: number; paid_days_14?: number } | null) =>
            (m?.free_days_14 ?? 0) + (m?.paid_days_14 ?? 0);
        for (const per of prebookAcceptance.values()) {
            for (const [wb, f] of Object.entries(per.availability || {})) {
                if (f.can_box && days(f.box_meta) > 0) out.add(`BOX::${wb}`);
                if (f.can_monopallet && days(f.mono_meta) > 0) out.add(`MONOPALLET::${wb}`);
                if (f.can_supersafe && days(f.super_meta) > 0) out.add(`SUPERSAFE::${wb}`);
            }
        }
        return out;
    }, [prebookAcceptance]);

    // Направления, по которым приёмка ПРОВЕРЕНА (данные есть). Демоция rows→prebook
    // делается ТОЛЬКО по проверенным: «нет данных» ≠ «не готов» (иначе при сбое
    // проверки весь черновик уехал бы в предбронь).
    const checkedPkgWbs = useMemo<Set<string>>(() => {
        const out = new Set<string>();
        for (const per of prebookAcceptance.values()) {
            for (const wb of Object.keys(per.availability || {})) {
                out.add(`BOX::${wb}`); out.add(`MONOPALLET::${wb}`); out.add(`SUPERSAFE::${wb}`);
            }
        }
        return out;
    }, [prebookAcceptance]);

    // ЕДИНАЯ синхронизация «черновик = ТОЛЬКО готовое к сдаче» (по живой приёмке).
    // Ключевой принцип: пул КАЖДОГО проверенного направления = его порции в rows И в
    // prebook, упакованные ОДНОЙ упаковкой (упаковка суммы двух валидных наборов ≠
    // объединение их упаковок — раздельные проходы стравливали хвосты в rows, ловили
    // живьём 58 шт «без целой» в Казани). Раскладка результата:
    //   • ГОТОВОЕ направление (приём + лимит): целые паллеты → rows, хвост → prebook;
    //   • проверенное НЕ готовое (⌛/закрыто): всё → prebook (моно — под предзаявку);
    //   • непроверенное или as_is («Оставить так») — не трогаем.
    // PUT только при реальном изменении (канон-подпись) → идемпотентно, цикла нет.
    const consolidatingRef = useRef(false);
    // Канон-подпись приёмки (проверенные + готовые направления). Меняется ТОЛЬКО при новой
    // приёмке WB — не при клике юзера. Гейт консолидации: реагируем на приёмку и полный
    // fill/load, но НЕ на пер-направленческий клик (его обрабатывают хендлер + scoped self-heal).
    const consAccSigRef = useRef<string>('');
    const [consRearmTick, setConsRearmTick] = useState(0);
    useEffect(() => {
        if (!draftId || geomState !== 'ready' || consolidatingRef.current || redistBusyRef.current) return;
        // Приёмка ещё не загружена → ждём (эффект перезапустится по смене prebookAcceptance).
        if (prebookAcceptance.size === 0) return;
        if (rows.length === 0 && prebook.length === 0) return;
        // ГЕЙТ ИЗОЛЯЦИИ: если приёмка та же И на ЭТУ версию черновика взведён scope (клик по
        // одному направлению — additive scope={dir} или subtractive scope={}), консолидацию
        // НЕ гоняем: направление уже приведено хендлером + scoped self-heal, соседей не трогаем.
        // Полный fill/load scope не ставит (undefined) → идём. Новая приёмка (accChanged) →
        // идём всегда (промоция/демоция по живой приёмке — её прямая задача).
        // БЕЗОПАСНОСТЬ СКИПА держится на инварианте: self-heal НИКОГДА не собирает промотируемую
        // ЦЕЛУЮ ПАЛЛЕТУ (topUpPrebookLooseBoxes добивает предбронь лишь до целого КОРОБА; промоцию
        // целых паллет в rows делает ТОЛЬКО consolidatePrebookWholePallets — здесь). После скипа
        // промотировать нечего. Инвариант закреплён тестом scopedNormalize.test.ts («self-heal НЕ
        // промотирует предбронь в rows») — если round-boxes однажды научат паллет-уровневому
        // добору, тест упадёт: тогда этот гейт нельзя скипать без re-trigger.
        const accSig = [...checkedPkgWbs].sort().join(';') + '||' + [...readyPkgWbs].sort().join(';');
        const accChanged = consAccSigRef.current !== accSig;
        const scope = healScopeRef.current;
        const scopedMutation = !!scope && scope.ts === (draft?.updated_at || '');
        if (!accChanged && scopedMutation) return;
        consolidatingRef.current = true;
        let cancelled = false;
        void (async () => {
            try {
                // self-heal в полёте: считать сейчас — значит посчитать от устаревшего
                // стейта и перекрыть его PUT (ловили живьём: конс-PUT отменил добивку
                // 14→22). ДОЖИДАЕМСЯ, а не return: если self-heal закончится без PUT,
                // deps не изменятся и пропущенный прогон не повторился бы никогда
                // (демоция/промоция по свежей приёмке молча зависала). Если self-heal
                // всё же PUT-нул — deps изменились, наш прогон устарел → cancelled.
                while (selfHealBusyRef.current) await new Promise((r) => setTimeout(r, 120));
                if (cancelled) return;
                // Фиксируем обработанную приёмку ТОЛЬКО пройдя ожидание (не на отменённом
                // прогоне — иначе acceptance-driven консолидация после re-arm ложно скипнулась бы).
                consAccSigRef.current = accSig;
                // 1. Карвим из rows порции всех ПРОВЕРЕННЫХ направлений (кроме as_is) в пул.
                const keepRows: AssemblyDraftRow[] = [];   // as_is + непроверенные направления
                const pool: AssemblyDraftRow[] = [];
                for (const r of rows) {
                    const pkg = r.package_type || 'BOX';
                    if (r.as_is) { keepRows.push(r); continue; }
                    const checkedWbs = Object.keys(r.tgt).filter(wb => checkedPkgWbs.has(`${pkg}::${wb}`));
                    if (checkedWbs.length === 0) { keepRows.push(r); continue; }
                    const cur = { ...r, src: { ...r.src }, tgt: { ...r.tgt } };
                    for (const wb of checkedWbs) {
                        for (const [pairKey, q] of allocatePairs(cur.src, cur.tgt)) {
                            if ((q || 0) <= 0 || pairKey.slice(pairKey.indexOf('::') + 2) !== wb) continue;
                            const ff = pairKey.slice(0, pairKey.indexOf('::'));
                            pool.push({ nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [ff]: q }, tgt: { [wb]: q }, package_type: pkg });
                            cur.tgt[wb] = Math.max(0, (cur.tgt[wb] || 0) - q);
                            cur.src[ff] = Math.max(0, (cur.src[ff] || 0) - q);
                        }
                        if ((cur.tgt[wb] || 0) <= 0) delete cur.tgt[wb];
                    }
                    for (const ff of Object.keys(cur.src)) if (cur.src[ff] <= 0) delete cur.src[ff];
                    if (Object.keys(cur.tgt).length > 0) keepRows.push(cur);
                }
                // 2. Единая упаковка пула (rows-порции + ВСЯ предбронь): готовые целые →
                //    черновик, всё прочее (хвосты, ⌛, закрытые, без геометрии) → предбронь.
                const fullPool = mergeDraftRows([...pool, ...prebook]);
                const keepInPrebook = (nm: number, wb: string, pkg: PackageType) =>
                    !readyPkgWbs.has(`${pkg}::${wb}`);
                const ctx = buildNormalizeCtx([...keepRows, ...fullPool]);
                // Добор до ЦЕЛОГО КОРОБА из свободного ФФ (строго с ФФ порции) — с ОБЕИХ
                // сторон упаковки. ДО: некратный хвост пула иначе уехал бы «россыпью
                // внутри целой паллеты» в rows (безопасно: срез упаковки теперь
                // короб-гранулярный и добитый короб не распилит). ПОСЛЕ: страховка для
                // хвостов, оставшихся в предброни. Именно в этом эффекте, а не только в
                // self-heal: этот PUT — последний в цепочке, добор мимо него терялся.
                // Пул ctx консистентен: первый добор его расходует, упаковка консервативна
                // per (nm, ff) и freeByNm не трогает, второй добор берёт остаток.
                const topPool = topUpPrebookLooseBoxes(fullPool, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
                // «Добить нечем» → НЕ резервируем (решение юзера): безнадёжная россыпь
                // снимается ДО упаковки (иначе уехала бы «внутри целой паллеты» в rows)
                // и возвращается на ФФ; целые коробы остаются.
                const relPool = releaseUnfixableLooseBoxes(topPool.changed ? topPool.rows : fullPool, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
                const packInput = relPool.changed ? relPool.rows : (topPool.changed ? topPool.rows : fullPool);
                const cons = consolidatePrebookWholePallets(packInput, ctx, keepInPrebook);
                const extractedRows = mergeDraftRows([...keepRows, ...cons.toDraft]);
                // Страховка на хвосты после упаковки (обычно no-op: пул уже строгий).
                const topTail = topUpPrebookLooseBoxes(cons.prebook, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
                const release = releaseUnfixableLooseBoxes(topTail.changed ? topTail.rows : cons.prebook, ctx.ppbOf, ctx.freeByNm ?? {}, ctx.ppbAt);
                const tailPrebook = release.changed ? release.rows : (topTail.changed ? topTail.rows : cons.prebook);
                // ГВАРД СТАБИЛЬНОСТИ (адверсарное ревью, CRITICAL): пул-упаковка моно может
                // извлечь в rows бины, которые rows-only re-pack self-heal'а НЕ воспроизводит
                // (упаковка популяционно-зависима) → вечный пинг-понг rows↔prebook (128 шт,
                // цикл 22 PUT на живом черновике). Пере-нормализуем извлечённое В ПАМЯТИ:
                // нестабильные извлечения откатываются в предбронь ДО PUT → фикс-поинт.
                const verify = normalizeDraft(extractedRows, buildNormalizeCtx([...extractedRows, ...tailPrebook]));
                const newRows = verify.changed ? verify.rows : extractedRows;
                const newPrebook = verify.dropped.length ? mergeDraftRows([...tailPrebook, ...verify.dropped]) : tailPrebook;
                const filledUpUnits = topPool.filledUp + topTail.filledUp;
                const releasedUnits = relPool.releasedUnits + release.releasedUnits + verify.releasedUnits;
                // 3. Реально ли что-то изменилось (канон-подпись обеих частей).
                const canon = (rs: AssemblyDraftRow[]) => rs
                    .map(r => `${r.nm_id}|${r.barcode}|${r.package_type || 'BOX'}|${r.as_is ? 1 : 0}|`
                        + Object.entries(r.src).filter(([, q]) => (q || 0) > 0).sort(([a], [b]) => (a < b ? -1 : 1)).map(([k, q]) => `${k}:${q}`).join(',') + '|'
                        + Object.entries(r.tgt).filter(([, q]) => (q || 0) > 0).sort(([a], [b]) => (a < b ? -1 : 1)).map(([k, q]) => `${k}:${q}`).join(','))
                    .sort().join(';');
                if (canon(newRows) === canon(rows) && canon(newPrebook) === canon(prebook)) return;
                // Бейдж «из предброни» = ТОЛЬКО ручной перенос (Дозабить / Оставить так /
                // Перенести паллеты). Авто-консолидация по приёмке WB поднимает целые
                // паллеты в черновик молча, БЕЗ метки провенанса — по требованию юзера.
                const targetNames = Array.from(new Set(newRows.flatMap(r => Object.keys(r.tgt))));
                const sourceIds = Array.from(new Set(newRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0))));
                const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), rows: newRows, prebook: newPrebook, source_warehouse_ids: sourceIds, target_warehouse_names: targetNames } });
                applyDraft(updated);
                // Сводка перемещений по складам (дельта rows): + приехало в черновик, − уехало.
                const sumBy = (rs: AssemblyDraftRow[]) => {
                    const m: Record<string, number> = {};
                    for (const r of rs) for (const [wb, q] of Object.entries(r.tgt)) m[wb] = (m[wb] || 0) + (q || 0);
                    return m;
                };
                const before = sumBy(rows); const after = sumBy(newRows);
                const toDraftList: string[] = []; const toPrebookList: string[] = [];
                for (const wb of new Set([...Object.keys(before), ...Object.keys(after)])) {
                    const d = (after[wb] || 0) - (before[wb] || 0);
                    if (d > 0) toDraftList.push(`${wb} +${formatNumber(d, 0)} шт`);
                    if (d < 0) toPrebookList.push(`${wb} −${formatNumber(-d, 0)} шт`);
                }
                const parts: string[] = [];
                if (toDraftList.length) parts.push(`в ЧЕРНОВИК (готово к сдаче, целыми паллетами): ${toDraftList.join(' · ')}`);
                if (toPrebookList.length) parts.push(`в ПРЕДБРОНЬ (хвосты / ⌛ / закрыто): ${toPrebookList.join(' · ')}`);
                if (filledUpUnits > 0) parts.push(`хвосты добиты до целых коробов свободным ФФ (+${formatNumber(filledUpUnits, 0)} шт)`);
                if (releasedUnits > 0) parts.push(`остатки меньше короба (добить нечем) возвращены на ФФ (−${formatNumber(releasedUnits, 0)} шт)`);
                if (parts.length) showToast(`Синхронизация с приёмкой WB — ${parts.join(' | ')}`, 'success');
            } catch { /* best-effort — не критично */ }
            finally {
                consolidatingRef.current = false;
                // Отменённый прогон (deps сменились, пока ждали self-heal): новая
                // инвокация эффекта могла отсечься по consolidatingRef — перевзводим,
                // иначе свежее состояние осталось бы не синхронизированным навсегда.
                if (cancelled) setConsRearmTick((t) => t + 1);
            }
        })();
        return () => { cancelled = true; };
    }, [draftId, draft, geomState, prebook, prebookGroups, rows, prebookAcceptance, readyPkgWbs, checkedPkgWbs, consRearmTick, buildNormalizeCtx, buildDistribution, applyDraft, showToast]);

    // АВТО-РЕДИСТРИБУЦИЯ ПРЕДБРОНИ по приёмке WB (self-heal на загрузке).
    // ⌛-склад (нет лимита приёмки), которого НЕТ в whitelist «Предзаявка без лимита»,
    // отгрузить нельзя — товар «висит» в под-вкладке «Предзаявка». Бэк
    // (check_acceptance_and_redistribute) уводит его на свободный/whitelist склад по
    // приоритету; здесь применяем splits к предброни, СОХРАНЯЯ источник ФФ. Триггер —
    // наличие ⌛-не-whitelist направления (checked, не ready, не в whitelist). Идемпотентно:
    // после переноса на whitelist/open splits цель не меняют → PUT не идёт. Координация с
    // self-heal/консолидацией — общие busy-refs (не PUT-им одновременно) + wait-loop.
    useEffect(() => {
        if (!draftId || geomState !== 'ready' || loading) return;
        if (redistBusyRef.current || consolidatingRef.current || selfHealBusyRef.current) return;
        if (prebookAcceptance.size === 0 || !preorderLoaded) return;   // ждём приёмку И whitelist
        if (prebook.length === 0) return;
        const isDead = (r: AssemblyDraftRow) => !r.as_is && Object.keys(r.tgt).some(wb => {
            const k = `${r.package_type || 'BOX'}::${wb}`;
            return checkedPkgWbs.has(k) && !readyPkgWbs.has(k) && !preorderWbs.has(wb);
        });
        if (!prebook.some(isDead)) return;
        // Прогон, не давший изменений, взводит sig и больше не бежит (до смены приёмки/whitelist/версии).
        const sig = `${draft?.updated_at || ''}|${[...checkedPkgWbs].sort().join(',')}|${[...readyPkgWbs].sort().join(',')}|${[...preorderWbs].sort().join(',')}`;
        if (redistSigRef.current === sig) return;
        redistSigRef.current = sig;
        redistBusyRef.current = true;
        let cancelled = false;
        void (async () => {
            try {
                while (selfHealBusyRef.current || consolidatingRef.current) await new Promise((r) => setTimeout(r, 120));
                if (cancelled) return;
                // Один item на баркод: суммируем цель по всем строкам предброни (as_is не трогаем).
                const bySku = new Map<string, { nm_id: number; barcode: string; distribution: Record<string, number> }>();
                for (const r of prebook) {
                    if (r.as_is) continue;
                    const key = `${r.nm_id}::${r.barcode}`;
                    const e = bySku.get(key) ?? { nm_id: r.nm_id, barcode: r.barcode, distribution: {} };
                    for (const [wb, q] of Object.entries(r.tgt)) e.distribution[wb] = (e.distribution[wb] || 0) + (q || 0);
                    bySku.set(key, e);
                }
                const items = [...bySku.values()].filter(it => Object.keys(it.distribution).length > 0);
                if (items.length === 0) return;
                const resp = await api.checkWbAcceptance({ items });
                if (cancelled) return;
                const byKey = new Map(resp.items.map(it => [`${it.nm_id}::${it.barcode}`, it]));
                const { rows: newPrebook, changed } = applyAcceptanceRedistToPrebook(prebook, byKey);
                if (!changed || cancelled) return;
                const merged = mergeDraftRows(newPrebook);
                const before: Record<string, number> = {};
                for (const r of prebook) for (const [wb, q] of Object.entries(r.tgt)) before[wb] = (before[wb] || 0) + (q || 0);
                const after: Record<string, number> = {};
                for (const r of merged) for (const [wb, q] of Object.entries(r.tgt)) after[wb] = (after[wb] || 0) + (q || 0);
                const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: merged } });
                applyDraft(updated);
                const off: string[] = []; const onto: string[] = [];
                for (const wb of new Set([...Object.keys(before), ...Object.keys(after)])) {
                    const d = (after[wb] || 0) - (before[wb] || 0);
                    if (d < 0) off.push(`${wb} −${formatNumber(-d, 0)} шт`);
                    if (d > 0) onto.push(`${wb} +${formatNumber(d, 0)} шт`);
                }
                if (off.length || onto.length) {
                    showToast(`Предбронь синхронизирована с приёмкой WB — ⌛-склады без права предзаявки перераспределены: ${off.join(' · ')} → ${onto.join(' · ')}`, 'success');
                }
            } catch { /* best-effort — не блокируем страницу */ }
            finally {
                redistBusyRef.current = false;
                // StrictMode/отменённый прогон: sig уже взведён на ЭТУ версию, но PUT не
                // случился → без re-arm эффект не перезапустится сам. Сбрасываем sig, чтобы
                // следующая инвокация переоценила (idempotent: нет ⌛-не-whitelist → no-op).
                if (cancelled) redistSigRef.current = '';
            }
        })();
        return () => { cancelled = true; };
    }, [draftId, draft, geomState, loading, prebook, prebookAcceptance, preorderLoaded, checkedPkgWbs, readyPkgWbs, preorderWbs, buildDistribution, applyDraft, showToast]);

    // Whitelist «складов с разрешённой предзаявкой» — грузим один раз (для кнопки «Создать
    // предзаявку» И для авто-редистрибуции предброни). `preorderLoaded` отличает «ещё не
    // загружен» от «загружен пустой» — редистрибуция не должна считать ВСЁ не-whitelist до загрузки.
    useEffect(() => {
        let cancelled = false;
        void api.getPreorderAllowedWarehouses()
            .then(list => { if (!cancelled) { setPreorderWbs(new Set(list)); setPreorderLoaded(true); } })
            .catch(() => { if (!cancelled) setPreorderLoaded(true); /* пустой whitelist — кнопка неактивна */ });
        return () => { cancelled = true; };
    }, []);

    // Приёмка WB для предброни И ЧЕРНОВИКА: нужна консолидации (prebook→rows только
    // готовое) и ДЕМОЦИИ (rows→prebook не-готовых направлений — «черновик = готовое к
    // сдаче»), плюс карточкам для ⌛-бейджа. Сигнатура (nm × barcode × склады, без
    // количеств) — не перезапрашиваем без изменения структуры направлений.
    useEffect(() => {
        const id = setInterval(() => setAccRefreshTick(t => t + 1), 600_000);
        return () => clearInterval(id);
    }, []);
    useEffect(() => {
        const combined = [...rows, ...prebook];
        if (combined.length === 0) return;
        const sig = `${accRefreshTick}|` + combined.map(r => `${r.nm_id}:${r.barcode || ''}:${Object.keys(r.tgt).sort().join(',')}`).sort().join('|');
        if (sig === prebookAccSigRef.current || prebookAccFetchingRef.current) return;
        prebookAccFetchingRef.current = true;
        setPrebookAccLoading(true);
        void (async () => {
            try {
                // Один item на nm (строки rows/prebook одного nm сливаются): флаги приёмки —
                // уровня (склад × упаковка), количества на них не влияют.
                const byNm = new Map<number, { barcode: string; distribution: Record<string, number> }>();
                for (const r of combined) {
                    if (!r.barcode || Object.keys(r.tgt).length === 0) continue;
                    let e = byNm.get(r.nm_id);
                    if (!e) { e = { barcode: r.barcode, distribution: {} }; byNm.set(r.nm_id, e); }
                    for (const [wb, q] of Object.entries(r.tgt)) e.distribution[wb] = (e.distribution[wb] || 0) + (q || 0);
                }
                const items = [...byNm.entries()].map(([nm, e]) => ({ nm_id: nm, barcode: e.barcode, distribution: e.distribution }));
                if (items.length === 0) { prebookAccSigRef.current = sig; return; }
                const resp = await api.checkWbAcceptance({ items });
                const map = new Map<number, AcceptanceCheckPerItem>();
                for (const it of resp.items || []) map.set(it.nm_id, it);
                setPrebookAcceptance(map);
                prebookAccSigRef.current = sig;
            } catch { /* приёмка не критична — карточки покажут статус по клику */ }
            finally { prebookAccFetchingRef.current = false; setPrebookAccLoading(false); }
        })();
    }, [rows, prebook, accRefreshTick]);

    // Метки приёмки по направлению (`${pkg}::${wb}::${ffId}`) для бейджей предброни.
    // Флаги склада — уровня (склад × тип упаковки), одинаковы по SKU → берём первый с
    // данными. meta берём ИМЕННО для g.pkg (моно → mono_meta) — без priority-схлопа
    // «Потребности», поэтому моно-лимит виден корректно.
    const prebookAcceptanceMarks = useMemo<Map<string, PrebookAcceptanceMark>>(() => {
        const out = new Map<string, PrebookAcceptanceMark>();
        if (prebookAcceptance.size === 0) return out;
        for (const g of prebookGroups) {
            const key = `${g.pkg}::${g.wb}::${g.ffId}`;
            let flags: AcceptanceFlags | undefined;
            for (const it of g.items) {
                const f = prebookAcceptance.get(it.nm_id)?.availability?.[g.wb];
                if (f) { flags = f; break; }
            }
            if (!flags) { out.set(key, { checked: false, open: false, closed: false, noLimit: false }); continue; }
            const closed = !flags.can_box && !flags.can_monopallet && !flags.can_supersafe;
            const meta = g.pkg === 'MONOPALLET' ? flags.mono_meta : g.pkg === 'SUPERSAFE' ? flags.super_meta : flags.box_meta;
            const open = g.pkg === 'MONOPALLET' ? !!flags.can_monopallet : g.pkg === 'SUPERSAFE' ? !!flags.can_supersafe : !!flags.can_box;
            const freeDays = meta?.free_days_14;
            const paidDays = meta?.paid_days_14;
            const noLimit = open && (freeDays ?? 0) + (paidDays ?? 0) <= 0;
            out.set(key, { checked: true, open, closed, freeDays, paidDays, noLimit });
        }
        return out;
    }, [prebookGroups, prebookAcceptance]);

    // Удалить позицию (SKU×направление) из предброни — коробы остаются на ФФ, не едут.
    const handleDeletePrebookItem = useCallback(async (nm_id: number, wb: string, pkg: PackageType) => {
        if (!draftId) return;
        const next = prebook
            .map(r => {
                if (r.nm_id !== nm_id || (r.package_type || 'BOX') !== pkg || !(r.tgt[wb] > 0)) return r;
                const tgt = { ...r.tgt }; const removed = tgt[wb]; delete tgt[wb];
                const src = { ...r.src }; let rem = removed;
                for (const ff of Object.keys(src)) { const take = Math.min(src[ff], rem); src[ff] -= take; rem -= take; if (src[ff] <= 0) delete src[ff]; if (rem <= 0) break; }
                return { ...r, tgt, src };
            })
            .filter(r => Object.keys(r.tgt).length > 0);
        try {
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: next } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set() }; // вычитающая → self-heal no-op
            applyDraft(updated);
            showToast('Удалено из предброни', 'success');
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка', 'error'); }
    }, [draftId, prebook, buildDistribution, applyDraft, showToast]);

    // Дозабить МИНИМАЛЬНО, с ОДНОГО ФФ: добираем целыми коробами до целых паллет
    // ровно на том ФФ, где лежит предбронь направления (короб с двух ФФ не собрать).
    // КОРОБ — смешанная паллета из ЛЮБЫХ box-SKU (склад принимает короб — доказано
    // тем, что предбронь туда уже разложена). МОНО — только SKU самой предброни.
    // Собранные целые паллеты уходят в черновик; хвост исходной предброни остаётся.
    const [toppingUp, setToppingUp] = useState<string | null>(null);
    const handleTopUpDirection = useCallback(async (pkg: PackageType, wb: string, ffId: number) => {
        if (!draftId || geomState !== 'ready') return;
        const chosenFf = ffId;
        const chosenKey = String(chosenFf);
        // Паллета собирается с ОДНОГО ФФ — работаем только с предбронью этого ФФ на wb.
        const pbOnFf = prebook.filter(r => (r.package_type || 'BOX') === pkg && (r.tgt[wb] || 0) > 0 && (r.src[chosenKey] || 0) > 0);
        if (pbOnFf.length === 0) return;
        const key = `${pkg}::${wb}::${ffId}`;
        setToppingUp(key);
        try {
            const inUse: Record<number, Record<number, number>> = {};
            for (const r of [...rows, ...prebook]) { const m = (inUse[r.nm_id] ??= {}); for (const [ff, q] of Object.entries(r.src)) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0); }
            const pbNm = new Set(pbOnFf.map(r => r.nm_id));
            // ИСТИННЫЙ footprint предброни направления (Σ qty_i / upp_i, каждый SKU по своей
            // геометрии) — так же, как snapToWholePallets решает, что уедет. shortfall — доля
            // паллеты до целой; добираем ровно её (не по одному репрезентативному SKU).
            const uppAt = (nm: number): number => {
                const b = effectiveBoxesPerPallet(nmBoxSize.get(nm) ?? null, maxPalletHeightCm(wb), palletOverrides);
                const ppb = nmPpb.get(nm) || 0;
                return b && ppb ? b * ppb : 0;
            };
            // Порция именно chosenFf на wb (строка может сорсить wb с нескольких ФФ).
            const ffWbQty = (r: AssemblyDraftRow): number => allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
            const footprint = palletFootprint(pbOnFf.map(r => ({ nmId: r.nm_id, qty: ffWbQty(r) })), uppAt);
            const shortfallFp = Math.ceil(footprint) - footprint;
            // Пул кандидатов: box-SKU со свободным ФФ на chosenFf (топ по остатку).
            const pool = (stockNeed?.articles ?? [])
                .map(a => {
                    const ppb = nmPpb.get(a.nm_id) || 0;
                    if (ppb <= 0 || !nmBoxSize.get(a.nm_id)) return null;
                    if (pkg !== 'BOX' && !pbNm.has(a.nm_id)) return null;
                    const freeBoxes = Math.floor(((a.rf_stocks?.[chosenFf]?.available || 0) - (inUse[a.nm_id]?.[chosenFf] || 0)) / ppb);
                    return freeBoxes > 0 ? { nm_id: a.nm_id, barcode: a.barcode, vendor_code: a.vendor_code, ppb, freeBoxes } : null;
                })
                .filter((x): x is NonNullable<typeof x> => x != null)
                .sort((a, b) => b.freeBoxes - a.freeBoxes)
                .slice(0, 30);
            // ПРОВЕРКА ПРИЁМКИ WB: SKU считается пригодным при открытом приёме И лимите
            // (дни>0) — «черновик = готовое к сдаче», ⌛-направления в черновик не едут.
            // Force-проверка может упасть (WB лимитит ~6 зап/мин, а страница фоново тоже
            // проверяет) → фолбэк на фоновый кэш приёмки (обновляется при изменениях).
            const okNm = new Set<number>();
            let dirOpen = false;
            let dirNoLimit = false;
            {
                // Приёмка из фонового кэша (НЕ force — суб-лимит 6/мин, серия кликов = 429;
                // см. коммент в handleShipPrebookAsIs).
                const accNm = [...pool.map(c => c.nm_id), ...pbOnFf.map(r => r.nm_id)];
                let perNm: Map<number, AcceptanceFlags | undefined> = new Map(
                    accNm.map(nm => [nm, prebookAcceptance.get(nm)?.availability?.[wb]]),
                );
                if (![...perNm.values()].some(Boolean)) {
                    try {
                        const items = [
                            ...pool.map(c => ({ nm_id: c.nm_id, barcode: c.barcode, distribution: { [wb]: c.ppb } })),
                            ...pbOnFf.map(r => ({ nm_id: r.nm_id, barcode: r.barcode, distribution: { [wb]: r.tgt[wb] || 0 } })),
                        ];
                        const resp = await api.checkWbAcceptance({ items });
                        perNm = new Map((resp.items || []).map(it => [it.nm_id, it.availability?.[wb]]));
                    } catch {
                        showToast('WB ограничивает частоту проверок приёмки — подождите минуту и нажмите ещё раз', 'error');
                        return;
                    }
                }
                for (const [nm, av] of perNm) {
                    const open = !!av && (pkg === 'BOX' ? !!av.can_box : pkg === 'MONOPALLET' ? !!av.can_monopallet : !!av.can_supersafe);
                    const meta = pkg === 'MONOPALLET' ? av?.mono_meta : pkg === 'SUPERSAFE' ? av?.super_meta : av?.box_meta;
                    const days = (meta?.free_days_14 ?? 0) + (meta?.paid_days_14 ?? 0);
                    if (open && days > 0) { okNm.add(nm); if (pbNm.has(nm)) dirOpen = true; }
                    else if (open && pbNm.has(nm)) dirNoLimit = true;
                }
            }
            if (!dirOpen) {
                showToast(dirNoLimit
                    ? `«${wb}»: приём открыт, но ЛИМИТА нет (⌛) — дозабор в черновик заблокирован (там только готовое к сдаче). ${pkg === 'MONOPALLET' ? 'Моно сдаётся предзаявкой.' : 'Дождитесь лимита.'}`
                    : `WB не принимает на «${wb}» — направление закрыто, дозабор невозможен`, 'error');
                return;
            }
            // Планируем добор целыми коробами по геометрии КАЖДОГО кандидата (короб = 1/bpp
            // паллеты) из ОТКРЫТЫХ по приёмке — ровно на shortfall до целой паллеты.
            const poolByNm = new Map(pool.map(c => [c.nm_id, c]));
            const plan = planTopUpBoxes(
                shortfallFp,
                pool.filter(c => okNm.has(c.nm_id)).map(c => ({
                    nmId: c.nm_id, ppb: c.ppb, freeBoxes: c.freeBoxes,
                    bpp: effectiveBoxesPerPallet(nmBoxSize.get(c.nm_id) ?? null, maxPalletHeightCm(wb), palletOverrides),
                })),
            );
            const candidates: AssemblyDraftRow[] = plan.rows.map(pr => {
                const c = poolByNm.get(pr.nmId)!;
                return { nm_id: c.nm_id, barcode: c.barcode, vendor_code: c.vendor_code, src: { [chosenKey]: pr.units }, tgt: { [wb]: pr.units }, package_type: pkg };
            });
            // В combined кладём ТОЛЬКО порцию (chosenFf→wb) каждой строки предброни, а не
            // строку целиком — иначе normalizeDraft затянул бы её другие склады/ФФ.
            const pbPortions: AssemblyDraftRow[] = pbOnFf
                .map(r => ({ nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [chosenKey]: ffWbQty(r) }, tgt: { [wb]: ffWbQty(r) }, package_type: pkg }))
                .filter(r => (r.tgt[wb] || 0) > 0);
            const combined = [...pbPortions, ...candidates];
            const norm = normalizeDraft(combined, buildNormalizeCtx([...rows, ...combined]));
            // Новинки без кратности normalizeDraft оставляет «россыпью» (исключение из
            // инварианта) — это НЕ собранная паллета. Успех дозабора считаем ТОЛЬКО по
            // палетизированным строкам; россыпь-новинки возвращаем в предбронь. Иначе
            // направление уезжало в черновик с ложным тостом «целыми паллетами».
            const isLooseNewcomer = (nm: number) => newcomerNmIds.has(nm) && !((nmPpb.get(nm) || 0) > 0);
            const palletizedRows = norm.rows.filter(r => !isLooseNewcomer(r.nm_id));
            const looseKept = norm.rows.filter(r => isLooseNewcomer(r.nm_id));
            const keptUnits = palletizedRows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            if (keptUnits <= 0) { showToast('Не удалось дособрать целую паллету — не хватает свободного ФФ', 'error'); return; }
            // Провенанс: дозабранные паллеты собраны с участием предброни направления.
            for (const r of palletizedRows) prebookOriginRef.current.add(`${r.nm_id}::${wb}`);
            const mergedRows = mergeDraftRows([...rows, ...palletizedRows]);
            // Новая предбронь: снимаем ТОЛЬКО порцию (chosenFf→wb) из затронутых строк
            // (остаток других складов/ФФ строки остаётся) + добавляем срез дозабора.
            const nextPrebook = prebook
                .map(r => {
                    if ((r.package_type || 'BOX') !== pkg || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                    const removed = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                    if (removed <= 0) return r;
                    const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - removed); if (tgt[wb] <= 0) delete tgt[wb];
                    const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - removed); if (src[chosenKey] <= 0) delete src[chosenKey];
                    return { ...r, tgt, src };
                })
                .filter(r => Object.keys(r.tgt).length > 0);
            nextPrebook.push(...norm.dropped.filter(r => pbNm.has(r.nm_id)), ...looseKept.filter(r => pbNm.has(r.nm_id)));
            const targetNames = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.tgt))));
            const sourceIds = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0))));
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), rows: mergedRows, prebook: nextPrebook, source_warehouse_ids: sourceIds, target_warehouse_names: targetNames } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set([directionKey(pkg, wb)]) };
            applyDraft(updated);
            showToast(`Дособрано на «${wb}»: +${formatNumber(keptUnits, 0)} шт целыми паллетами`, 'success');
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка дозабивки', 'error'); }
        finally { setToppingUp(null); }
    }, [draftId, geomState, prebook, rows, stockNeed, nmPpb, nmBoxSize, palletOverrides, newcomerNmIds, prebookAcceptance, buildNormalizeCtx, buildDistribution, applyDraft, showToast]);

    // Отгрузить неполную паллету направления КАК ЕСТЬ: переносим предбронь этой отгрузки
    // (упаковка × склад × ФФ) в черновик частичной паллетой (без дозабора до целой) и
    // сохраняем НАПРЯМУЮ, без normalizeDraft (иначе он срезал бы неполную обратно).
    // Коммит паллеты не режет → частичная доедет в заявку.
    // ПРОВЕРКА ПРИЁМКИ WB (как в дозаборе): грузим только SKU с открытым приёмом И
    // лимитом (дни>0); прочие остаются в предброни; всё направление не готово → блок.
    // ⌛ (приём без лимита) ТОЖЕ блокирует: черновик = готовое к сдаче; моно — предзаявкой.
    const [shippingAsIs, setShippingAsIs] = useState<string | null>(null);
    const handleShipPrebookAsIs = useCallback(async (pkg: PackageType, wb: string, ffId: number) => {
        if (!draftId || shippingAsIs) return;
        const chosenKey = String(ffId);
        const key = `${pkg}::${wb}::${ffId}`;
        const affected = prebook.filter(r => (r.package_type || 'BOX') === pkg && (r.tgt[wb] || 0) > 0 && (r.src[chosenKey] || 0) > 0);
        if (affected.length === 0) return;
        setShippingAsIs(key);
        try {
            // Живая проверка приёмки по SKU направления: нужен приём И ЛИМИТ (дни>0).
            // «Черновик = готовое к сдаче»: ⌛ (приём без лимита) в черновик НЕ переносим —
            // моно сдаётся предзаявкой, короб ждёт лимита. При сбое force-проверки (WB
            // лимитит частоту) — фолбэк на фоновый кэш приёмки страницы.
            const okNm = new Set<number>();
            let openNoLimit = false;
            {
                // Приёмка из фонового кэша страницы (пер-баркод, ≤10 мин) — НЕ force.
                // force сидит на суб-лимите 6/мин: серия «Оставить так»/«Дозабить» его
                // выжигала → 429 и на кнопках, и на фоновой проверке (та переставала
                // грузиться → «черновик = готовое к сдаче» не досинхронивался, недобор
                // застревал в rows). Секундная свежесть коэффициентов для локального
                // переноса rows↔prebook не нужна — данные обновляются фоново каждые 10 мин.
                let perNm: Map<number, AcceptanceFlags | undefined> = new Map(
                    affected.map(r => [r.nm_id, prebookAcceptance.get(r.nm_id)?.availability?.[wb]]),
                );
                if (![...perNm.values()].some(Boolean)) {
                    // Кэш ещё не прогрет (приёмка не догрузилась) — ОДИН обычный запрос
                    // (не force): пер-баркод кэш бэка сам его закэширует.
                    try {
                        const items = affected.map(r => ({ nm_id: r.nm_id, barcode: r.barcode, distribution: { [wb]: allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0 } }));
                        const resp = await api.checkWbAcceptance({ items });
                        perNm = new Map((resp.items || []).map(it => [it.nm_id, it.availability?.[wb]]));
                    } catch {
                        showToast('WB ограничивает частоту проверок приёмки — подождите минуту и нажмите ещё раз', 'error');
                        return;
                    }
                }
                for (const [nm, av] of perNm) {
                    const open = !!av && (pkg === 'BOX' ? !!av.can_box : pkg === 'MONOPALLET' ? !!av.can_monopallet : !!av.can_supersafe);
                    const meta = pkg === 'MONOPALLET' ? av?.mono_meta : pkg === 'SUPERSAFE' ? av?.super_meta : av?.box_meta;
                    const days = (meta?.free_days_14 ?? 0) + (meta?.paid_days_14 ?? 0);
                    if (open && days > 0) okNm.add(nm);
                    else if (open) openNoLimit = true;
                }
            }
            if (okNm.size === 0) {
                showToast(openNoLimit
                    ? `«${wb}»: приём открыт, но ЛИМИТА нет (⌛) — в черновик не переносим (там только готовое к сдаче). ${pkg === 'MONOPALLET' ? 'Моно сдаётся предзаявкой: под-вкладка «Предзаявка» или кнопка «📋 Предзаявка» у паллеты.' : 'Дождитесь лимита приёмки — направление останется в предброни.'}`
                    : `WB не принимает на «${wb}» — направление закрыто, отгрузка невозможна`, 'error');
                return;
            }

            const shipRows: AssemblyDraftRow[] = [];
            let skipped = 0;
            let looseLeft = 0;   // остаток россыпью, оставшийся в предброни (не кратен коробу)
            let looseShipped = 0; // уехало россыпью (SKU без кратности — резать не на что)
            const nextPrebook = prebook
                .map(r => {
                    if ((r.package_type || 'BOX') !== pkg || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                    if (!okNm.has(r.nm_id)) { skipped += 1; return r; }   // закрытый SKU — остаётся в предброни
                    // Порция ИМЕННО этого ФФ на этот склад (строка может сорсить wb с
                    // нескольких ФФ) — берём allocatePairs, а не весь tgt[wb], иначе
                    // перелили бы с одного ФФ и выкинули порцию другого.
                    const portion = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                    if (portion <= 0) return r;
                    // «Кратность только коробки»: отгружаем ЦЕЛЫЕ коробы, остаток россыпью
                    // (portion % ppb) оставляем в предброни (не везём неполный короб).
                    // SKU без кратности (ppb неизвестен — товар лежит россыпью) везём порцию
                    // целиком, но считаем отдельно — тост не рапортует её «целыми коробами».
                    const ppb = nmPpb.get(r.nm_id) || 0;
                    const moved = ppb > 0 ? Math.floor(portion / ppb) * ppb : portion;
                    if (moved <= 0) { looseLeft += portion; return r; }  // целого короба нет — остаётся остатком
                    if (ppb <= 0) looseShipped += moved;
                    looseLeft += portion - moved;
                    // as_is: сознательно отгруженная частичная — self-heal её не откатывает.
                    shipRows.push({ nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [chosenKey]: moved }, tgt: { [wb]: moved }, package_type: pkg, as_is: true });
                    const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - moved); if (tgt[wb] <= 0) delete tgt[wb];
                    const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - moved); if (src[chosenKey] <= 0) delete src[chosenKey];
                    return { ...r, tgt, src };
                })
                .filter(r => Object.keys(r.tgt).length > 0);
            if (shipRows.length === 0) {
                showToast(skipped
                    ? 'Все SKU направления закрыты WB — отгружать нечего'
                    : 'Нет целых коробов — в направлении только остатки россыпью (неполные коробы)', 'error');
                return;
            }
            const movedUnits = shipRows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            // Провенанс: эти строки уехали в черновик ИЗ предброни («Оставить так»).
            for (const r of shipRows) prebookOriginRef.current.add(`${r.nm_id}::${wb}`);
            const mergedRows = mergeDraftRows([...rows, ...shipRows]);
            const targetNames = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.tgt))));
            const sourceIds = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0))));
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), rows: mergedRows, prebook: nextPrebook, source_warehouse_ids: sourceIds, target_warehouse_names: targetNames } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set([directionKey(pkg, wb)]) };
            applyDraft(updated);
            const boxUnits = movedUnits - looseShipped;
            showToast(
                `Отгружено как есть на «${wb}» (неполная паллета): `
                + [
                    boxUnits > 0 ? `+${formatNumber(boxUnits, 0)} шт целыми коробами` : '',
                    looseShipped > 0 ? `+${formatNumber(looseShipped, 0)} шт россыпью (нет кратности короба)` : '',
                ].filter(Boolean).join(' · ')
                + (looseLeft ? ` · остаток ${formatNumber(looseLeft, 0)} шт остался в предброни (неполный короб)` : '')
                + (skipped ? ` · ⛔ ${formatNumber(skipped, 0)} SKU закрыты WB — оставлены в предброни` : ''),
                'success',
            );
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка отгрузки', 'error'); }
        finally { setShippingAsIs(null); }
    }, [draftId, shippingAsIs, prebook, rows, nmPpb, prebookAcceptance, buildDistribution, applyDraft, showToast]);

    // Удалить ВСЁ направление (упаковка × склад × ФФ) из предброни одним действием.
    // Снимаем порцию (ffId→wb) со ВСЕХ строк направления (скоуп по ffId через allocatePairs,
    // не весь tgt[wb] — иначе при мульти-ФФ строке снесли бы порцию чужого ФФ). Мутируем
    // ТОЛЬКО prebook (rows не трогаем — иначе снесли бы собранные паллеты черновика);
    // освобождённые коробы автоматически станут свободны на ФФ (inUse считается по prebook).
    const [deletingDir, setDeletingDir] = useState<string | null>(null);
    const handleDeleteDirection = useCallback(async (pkg: PackageType, wb: string, ffId: number) => {
        if (!draftId || deletingDir) return;
        const key = `${pkg}::${wb}::${ffId}`;
        const grp = prebookGroups.find(g => g.pkg === pkg && g.wb === wb && g.ffId === ffId);
        const ffLabel = grp?.ff || (ffId >= 0 ? `ФФ ${ffId}` : '—');
        if (!window.confirm(
            `Удалить всё направление «${wb}» (ФФ «${ffLabel}») из предброни?`
            + (grp ? ` ${formatNumber(grp.boxes, 0)} кор · ${formatNumber(grp.qty, 0)} шт.` : '')
            + ' Коробы останутся свободными на ФФ.',
        )) return;
        const chosenKey = String(ffId);
        setDeletingDir(key);
        try {
            const next = prebook
                .map(r => {
                    if ((r.package_type || 'BOX') !== pkg || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                    const removed = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                    if (removed <= 0) return r;
                    const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - removed); if (tgt[wb] <= 0) delete tgt[wb];
                    const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - removed); if (src[chosenKey] <= 0) delete src[chosenKey];
                    return { ...r, tgt, src };
                })
                .filter(r => Object.keys(r.tgt).length > 0);
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: next } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set() }; // вычитающая → self-heal no-op
            applyDraft(updated);
            showToast(`Направление «${wb}» удалено из предброни — коробы свободны на ФФ`, 'success');
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка удаления', 'error'); }
        finally { setDeletingDir(null); }
    }, [draftId, deletingDir, prebook, prebookGroups, buildDistribution, applyDraft, showToast]);

    // Создать ПРЕДЗАЯВКУ на моно: готовые целые моно-паллеты на WB-склад без лимита
    // приёмки (⌛) сдаются предзаявкой (бронью). Создаём заявки на сборку сразу с флагом
    // is_prebooking (бэк, обычный сток) и убираем их из предброни. Скоуп по (ffId→wb).
    // В БРОНЬ УХОДЯТ ТОЛЬКО ЦЕЛЫЕ ПАЛЛЕТЫ (авторитетный трим-сплит, моно ≤3 арт) —
    // неполный хвост остаётся в предброни (дозабрать/убрать в под-вкладке «Предзаявка»).
    const [creatingPrebooking, setCreatingPrebooking] = useState<string | null>(null);
    const handleCreatePrebooking = useCallback(async (pkg: PackageType, wb: string, ffId: number) => {
        if (!draftId || creatingPrebooking || pkg !== 'MONOPALLET') return;
        const chosenKey = String(ffId);
        const affected = prebook.filter(r => (r.package_type || 'BOX') === 'MONOPALLET' && (r.tgt[wb] || 0) > 0 && (r.src[chosenKey] || 0) > 0);
        if (affected.length === 0) return;
        // Порции ИМЕННО этого направления (ffId→wb) — строка может сорсить wb с неск. ФФ.
        const dirPortions: AssemblyDraftRow[] = affected
            .map((r): AssemblyDraftRow | null => { const q = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0; return q > 0 ? { nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [chosenKey]: q }, tgt: { [wb]: q }, package_type: 'MONOPALLET' as PackageType } : null; })
            .filter((x): x is AssemblyDraftRow => x != null);
        if (dirPortions.length === 0) return;
        // Целые паллеты (в бронь) vs хвост (остаётся) — тем же сплитом, что консолидация/коммит.
        const split = consolidatePrebookWholePallets(dirPortions, buildNormalizeCtx([...rows, ...prebook]));
        const wholeUnits = split.toDraft.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
        if (wholeUnits <= 0) { showToast('Нет целой паллеты для предзаявки — сначала дозаберите хвост до целой или уберите неполную', 'error'); return; }
        const tailUnits = split.prebook.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
        if (!window.confirm(
            `Создать предзаявку на моно «${wb}»? В бронь уйдут ТОЛЬКО целые паллеты (${formatNumber(wholeUnits, 0)} шт).`
            + (tailUnits ? ` Неполный хвост (${formatNumber(tailUnits, 0)} шт) останется в предброни.` : ''),
        )) return;
        const key = `${pkg}::${wb}::${ffId}`;
        setCreatingPrebooking(key);
        try {
            const bookRows = split.toDraft
                .map(r => ({ warehouse_id: ffId, barcode: r.barcode, wb_warehouse_name: wb, qty: r.tgt[wb] || 0, package_type: 'MONOPALLET' as PackageType }))
                .filter(r => r.qty > 0);
            if (bookRows.length === 0) return;
            const res = await api.createPrebooking({ rows: bookRows });
            // Снимаем ВСЮ порцию направления из предброни, возвращаем ТОЛЬКО хвост (не целые).
            const stripped = prebook
                .map(r => {
                    if ((r.package_type || 'BOX') !== pkg || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                    const moved = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                    if (moved <= 0) return r;
                    const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - moved); if (tgt[wb] <= 0) delete tgt[wb];
                    const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - moved); if (src[chosenKey] <= 0) delete src[chosenKey];
                    return { ...r, tgt, src };
                })
                .filter(r => Object.keys(r.tgt).length > 0);
            const nextPrebook = mergeDraftRows([...stripped, ...split.prebook]);
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: nextPrebook } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set() }; // бронь убирает из предброни, rows не трогает
            applyDraft(updated);
            const numbers = (res.requests || []).map(r => r.number).filter(Boolean);
            showToast(
                `Предзаявка на моно «${wb}» создана: ${formatNumber(res.created, 0)} заявок ${numbers.length ? numbers.join(', ') : ''} (${formatNumber(wholeUnits, 0)} шт целыми паллетами)`
                + (tailUnits ? ` · хвост ${formatNumber(tailUnits, 0)} шт остался в предброни` : '')
                + ' — ищите их в списке «Заявки на сборку» с бейджем 🅿️',
                'success',
            );
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка создания предзаявки', 'error'); }
        finally { setCreatingPrebooking(null); }
    }, [draftId, creatingPrebooking, prebook, rows, buildNormalizeCtx, buildDistribution, applyDraft, showToast]);

    // «Дозабить до целой» (под-вкладка «Предзаявка»): добрать МОНО-хвост целыми коробами
    // из свободного ФФ ЭТОГО направления до ещё одной целой паллеты. Результат ОСТАЁТСЯ в
    // предброни (⌛-моно под предзаявку, НЕ в черновик). Кандидаты = SKU направления (уже
    // приняты WB как моно → повторную приёмку не дёргаем; лимит ⌛ снимается предзаявкой).
    const [toppingUpPrebook, setToppingUpPrebook] = useState<string | null>(null);
    const handleTopUpPrebookMono = useCallback(async (wb: string, ffId: number) => {
        if (!draftId || toppingUpPrebook || ffId < 0) return;
        const g = prebookGroups.find(x => x.pkg === 'MONOPALLET' && x.wb === wb && x.ffId === ffId);
        if (!g || !g.tailTopUp) return;
        const chosenKey = String(ffId);
        const key = `MONOPALLET::${wb}::${ffId}`;
        setToppingUpPrebook(key);
        try {
            // Прицел дозабора = ТОП-3 крупнейших хвоста недобора (правило «≤3 арт на
            // моно-паллете»): объёмная цель «1 − дробь» по ВСЕМ артикулам направления
            // размазывала добор по >3 SKU — целая паллета не собиралась никогда.
            const km: Record<string, number> = {};
            for (const i of g.items) km[String(i.nm_id)] = (km[String(i.nm_id)] || 0) + i.qty;
            const ppbAtFf = (nm: number): number => nmPpbByWh.get(nm)?.[ffId] ?? (nmPpb.get(nm) || 0);
            const uppAt = (nm: number): number => {
                const b = effectiveBoxesPerPallet(nmBoxSize.get(nm) ?? null, maxPalletHeightCm(wb), palletOverrides);
                const ppb = ppbAtFf(nm);
                return b && ppb ? b * ppb : 0;
            };
            const pm = packMonoPallets(km, (k) => uppAt(Number(k)) || null, MONO_MAX_PALLET_ARTICLES, (k) => ppbAtFf(Number(k)));
            const tails = Object.entries(pm.dropped)
                .map(([k, v]) => { const u = uppAt(Number(k)); return { nm: Number(k), fp: u > 0 ? v / u : 0 }; })
                .filter(t => t.fp > 0)
                .sort((a, b) => b.fp - a.fp)
                .slice(0, MONO_MAX_PALLET_ARTICLES);
            const topFp = tails.reduce((s, t) => s + t.fp, 0);
            const dirNm = new Set(tails.map(t => t.nm));
            const inUse: Record<number, Record<number, number>> = {};
            for (const r of [...rows, ...prebook]) { const m = (inUse[r.nm_id] ??= {}); for (const [ff, q] of Object.entries(r.src)) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0); }
            const pool = (stockNeed?.articles ?? [])
                .filter(a => dirNm.has(a.nm_id))
                .map(a => {
                    const ppb = nmPpb.get(a.nm_id) || 0;
                    if (ppb <= 0 || !nmBoxSize.get(a.nm_id)) return null;
                    const freeBoxes = Math.floor(((a.rf_stocks?.[ffId]?.available || 0) - (inUse[a.nm_id]?.[ffId] || 0)) / ppb);
                    if (freeBoxes <= 0) return null;
                    return { nmId: a.nm_id, ppb, freeBoxes, bpp: effectiveBoxesPerPallet(nmBoxSize.get(a.nm_id) ?? null, maxPalletHeightCm(wb), palletOverrides), barcode: a.barcode, vendor_code: a.vendor_code || '' };
                })
                .filter((x): x is TopUpCandidate & { barcode: string; vendor_code: string } => x != null)
                .sort((a, b) => b.freeBoxes - a.freeBoxes);
            const plan = planTopUpBoxes(topFp > 1e-9 ? 1 - topFp : 0, pool);
            if (!plan.feasible || plan.rows.length === 0) { showToast('Нечем дозабрать до целой — не хватает свободного ФФ на артикулы направления', 'error'); return; }
            const byNm = new Map(pool.map(c => [c.nmId, c]));
            const additions: AssemblyDraftRow[] = plan.rows.map(pr => {
                const c = byNm.get(pr.nmId)!;
                return { nm_id: pr.nmId, barcode: c.barcode, vendor_code: c.vendor_code, src: { [chosenKey]: pr.units }, tgt: { [wb]: pr.units }, package_type: 'MONOPALLET' as PackageType };
            });
            const addedUnits = additions.reduce((s, r) => s + (r.tgt[wb] || 0), 0);
            const nextPrebook = mergeDraftRows([...prebook, ...additions]);
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: nextPrebook } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set([directionKey('MONOPALLET', wb)]) };
            applyDraft(updated);
            showToast(`Дозаброшено в предбронь «${wb}»: +${formatNumber(addedUnits, 0)} шт целыми коробами до целой паллеты`, 'success');
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка дозабора', 'error'); }
        finally { setToppingUpPrebook(null); }
    }, [draftId, toppingUpPrebook, prebookGroups, rows, prebook, stockNeed, nmPpb, nmPpbByWh, nmBoxSize, palletOverrides, buildDistribution, applyDraft, showToast]);

    // «Убрать неполную» (под-вкладка «Предзаявка»): оставить только ЦЕЛЫЕ моно-паллеты
    // направления, неполный хвост убрать (освободить на ФФ). Сплит — авторитетный (≤3 арт).
    const [trimmingTail, setTrimmingTail] = useState<string | null>(null);
    const handleTrimPrebookTail = useCallback(async (wb: string, ffId: number) => {
        if (!draftId || trimmingTail || ffId < 0) return;
        const chosenKey = String(ffId);
        const dirPortions: AssemblyDraftRow[] = prebook
            .filter(r => (r.package_type || 'BOX') === 'MONOPALLET' && (r.tgt[wb] || 0) > 0 && (r.src[chosenKey] || 0) > 0)
            .map((r): AssemblyDraftRow | null => { const q = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0; return q > 0 ? { nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [chosenKey]: q }, tgt: { [wb]: q }, package_type: 'MONOPALLET' as PackageType } : null; })
            .filter((x): x is AssemblyDraftRow => x != null);
        if (dirPortions.length === 0) return;
        const split = consolidatePrebookWholePallets(dirPortions, buildNormalizeCtx([...rows, ...prebook]));
        const tailUnits = split.prebook.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
        if (tailUnits <= 0) { showToast('Неполной паллеты нет — направление уже целыми паллетами', 'success'); return; }
        if (!window.confirm(`Убрать неполную паллету на «${wb}» (${formatNumber(tailUnits, 0)} шт)? Останутся только целые паллеты; хвост освободится на ФФ.`)) return;
        const key = `MONOPALLET::${wb}::${ffId}`;
        setTrimmingTail(key);
        try {
            const stripped = prebook
                .map(r => {
                    if ((r.package_type || 'BOX') !== 'MONOPALLET' || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                    const removed = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                    if (removed <= 0) return r;
                    const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - removed); if (tgt[wb] <= 0) delete tgt[wb];
                    const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - removed); if (src[chosenKey] <= 0) delete src[chosenKey];
                    return { ...r, tgt, src };
                })
                .filter(r => Object.keys(r.tgt).length > 0);
            // Возвращаем в предбронь ТОЛЬКО целые паллеты (split.toDraft) — ⌛-моно под
            // предзаявку. Хвост (split.prebook) НЕ возвращаем → освобождается на ФФ.
            const nextPrebook = mergeDraftRows([...stripped, ...split.toDraft]);
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: nextPrebook } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set() }; // вычитающая → self-heal no-op
            applyDraft(updated);
            const wholeKept = split.toDraft.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            showToast(
                `Неполная паллета убрана с «${wb}»: ${formatNumber(tailUnits, 0)} шт освобождены на ФФ`
                + (wholeKept ? ` · целые паллеты (${formatNumber(wholeKept, 0)} шт) остались в предброни (если приём WB открыт — уедут в черновик автоматически)` : ''),
                'success',
            );
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка', 'error'); }
        finally { setTrimmingTail(null); }
    }, [draftId, trimmingTail, prebook, rows, buildNormalizeCtx, buildDistribution, applyDraft, showToast]);

    // ── ПОПАЛЛЕТНЫЕ действия (моно): предзаявка / освобождение выбранных паллет
    // раскладки (целых и частичных — юзер решает сам; можно НЕСКОЛЬКО за раз, тогда
    // предзаявка уходит ОДНОЙ заявкой и всё укладывается в один запрос + один PUT —
    // не съедает write-лимит). Снимаем из предброни РОВНО units паллет per nm
    // (порция ffId→wb через allocatePairs — мульти-src цел).
    const [palletOp, setPalletOp] = useState<string | null>(null);
    const stripPalletsFromPrebook = useCallback((pallets: PrebookMonoPallet[], wb: string, ffId: number): AssemblyDraftRow[] => {
        const chosenKey = String(ffId);
        const need = new Map<number, number>();
        for (const p of pallets) for (const it of p.items) need.set(it.nm_id, (need.get(it.nm_id) || 0) + it.units);
        return prebook
            .map(r => {
                if ((r.package_type || 'BOX') !== 'MONOPALLET' || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                const want = need.get(r.nm_id) || 0;
                if (want <= 0) return r;
                const portion = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                const take = Math.min(portion, want);
                if (take <= 0) return r;
                need.set(r.nm_id, want - take);
                const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - take); if (tgt[wb] <= 0) delete tgt[wb];
                const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - take); if (src[chosenKey] <= 0) delete src[chosenKey];
                return { ...r, tgt, src };
            })
            .filter(r => Object.keys(r.tgt).length > 0);
    }, [prebook]);

    const palletsLabel = (sel: { pallet: PrebookMonoPallet; palletNo: number }[]) =>
        sel.map(s => `№${s.palletNo}`).join(', ');

    // Предзаявка из выбранных паллет раскладки ОДНОЙ заявкой (частичные — по явному
    // решению юзера, confirm называет их количество).
    const handleBookPallets = useCallback(async (wb: string, ffId: number, sel: { pallet: PrebookMonoPallet; palletNo: number }[]) => {
        if (!draftId || palletOp || ffId < 0 || sel.length === 0) return;
        const units = sel.reduce((s, x) => s + x.pallet.items.reduce((a, it) => a + it.units, 0), 0);
        if (units <= 0) return;
        // Один SKU может лежать на нескольких выбранных паллетах — предзаявке отдаём
        // суммарные units per nm (backend группирует по ФФ×WB×упаковке → одна заявка).
        const unitsByNm = new Map<number, number>();
        for (const x of sel) for (const it of x.pallet.items) unitsByNm.set(it.nm_id, (unitsByNm.get(it.nm_id) || 0) + it.units);
        // Баркод берём из строк ЭТОГО направления (wb+ffId) — у мульти-баркодного SKU
        // на другом направлении может лежать другой barcode.
        const bcByNm = new Map<number, string>();
        for (const r of prebook) {
            if ((r.package_type || 'BOX') !== 'MONOPALLET' || !r.barcode || bcByNm.has(r.nm_id)) continue;
            if (!(r.tgt[wb] > 0) || !(r.src[String(ffId)] > 0)) continue;
            bcByNm.set(r.nm_id, r.barcode);
        }
        const bookRows = [...unitsByNm.entries()]
            .map(([nm, qty]) => ({ warehouse_id: ffId, barcode: bcByNm.get(nm) || '', wb_warehouse_name: wb, qty, package_type: 'MONOPALLET' as PackageType }))
            .filter(r => r.qty > 0 && r.barcode);
        if (bookRows.length === 0) { showToast('Не нашёл баркоды артикулов паллет в предброни', 'error'); return; }
        const partials = sel.filter(x => x.pallet.fillPct < 0.999).length;
        const artCount = unitsByNm.size;
        const confirmText = sel.length === 1
            ? `Создать предзаявку на «${wb}» из паллеты ${palletsLabel(sel)}${partials ? ` — ЧАСТИЧНАЯ (${Math.round(sel[0].pallet.fillPct * 100)}%)` : ' (целая)'}?`
              + ` ${formatNumber(units, 0)} шт · ${formatNumber(artCount, 0)} арт. Остальные паллеты направления останутся в предброни.`
            : `Создать ОДНУ предзаявку на «${wb}» из ${formatNumber(sel.length, 0)} паллет (${palletsLabel(sel)})${partials ? ` — в т.ч. ${formatNumber(partials, 0)} частичн.` : ''}?`
              + ` Всего ${formatNumber(units, 0)} шт · ${formatNumber(artCount, 0)} арт. Остальные паллеты направления останутся в предброни.`;
        if (!window.confirm(confirmText)) return;
        const key = `${wb}::${ffId}::#${sel.map(x => x.palletNo).join('+')}`;
        setPalletOp(key);
        try {
            const res = await api.createPrebooking({ rows: bookRows });
            const nextPrebook = stripPalletsFromPrebook(sel.map(x => x.pallet), wb, ffId);
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: nextPrebook } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set() }; // бронь убирает из предброни, rows не трогает
            applyDraft(updated);
            const numbers = (res.requests || []).map(r => r.number).filter(Boolean);
            showToast(
                `Предзаявка из ${sel.length === 1 ? `паллеты ${palletsLabel(sel)}` : `${formatNumber(sel.length, 0)} паллет (${palletsLabel(sel)})`} на «${wb}» создана: заявка ${numbers.join(', ')} (${formatNumber(units, 0)} шт${partials ? `, ${formatNumber(partials, 0)} частичн.` : ''})`
                + ' — ищите в списке «Заявки на сборку» с бейджем 🅿️',
                'success',
            );
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка создания предзаявки', 'error'); }
        finally { setPalletOp(null); }
    }, [draftId, palletOp, prebook, stripPalletsFromPrebook, buildDistribution, applyDraft, showToast]);

    // Освободить выбранные паллеты раскладки на ФФ (убрать их содержимое из предброни,
    // остальные паллеты направления не трогаем) — один PUT на всю пачку.
    const handleReleasePallets = useCallback(async (wb: string, ffId: number, sel: { pallet: PrebookMonoPallet; palletNo: number }[]) => {
        if (!draftId || palletOp || ffId < 0 || sel.length === 0) return;
        const units = sel.reduce((s, x) => s + x.pallet.items.reduce((a, it) => a + it.units, 0), 0);
        if (units <= 0) return;
        const arts = new Set(sel.flatMap(x => x.pallet.items.map(it => it.nm_id))).size;
        const what = sel.length === 1 ? `паллету ${palletsLabel(sel)}` : `${formatNumber(sel.length, 0)} паллет (${palletsLabel(sel)})`;
        if (!window.confirm(`Оставить ${what} на ФФ (${formatNumber(units, 0)} шт · ${formatNumber(arts, 0)} арт.)? ${sel.length === 1 ? 'Она уйдёт' : 'Они уйдут'} из предброни, коробы освободятся; остальные паллеты направления останутся.`)) return;
        const key = `${wb}::${ffId}::#${sel.map(x => x.palletNo).join('+')}`;
        setPalletOp(key);
        try {
            const nextPrebook = stripPalletsFromPrebook(sel.map(x => x.pallet), wb, ffId);
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), prebook: nextPrebook } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set() }; // «На ФФ» — вычитающая → self-heal no-op, соседей не трогаем
            applyDraft(updated);
            showToast(`${sel.length === 1 ? `Паллета ${palletsLabel(sel)}` : `Паллеты ${palletsLabel(sel)}`} (${formatNumber(units, 0)} шт) оставлены на ФФ — убраны из предброни`, 'success');
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка', 'error'); }
        finally { setPalletOp(null); }
    }, [draftId, palletOp, stripPalletsFromPrebook, buildDistribution, applyDraft, showToast]);

    // Перенести выбранные моно-паллеты предброни В ЧЕРНОВИК как есть (для направлений с
    // ОТКРЫТЫМ лимитом: моно-недобор, дозабрать нечем — «Оставить так» точечно по паллетам).
    // as_is=true: частичная моно-паллета едет в черновик, normalizeDraft её не режет.
    const handleDraftPallets = useCallback(async (wb: string, ffId: number, sel: { pallet: PrebookMonoPallet; palletNo: number }[]) => {
        if (!draftId || palletOp || ffId < 0 || sel.length === 0) return;
        const chosenKey = String(ffId);
        // Сколько штук per nm просят выбранные паллеты.
        const wantByNm = new Map<number, number>();
        for (const x of sel) for (const it of x.pallet.items) wantByNm.set(it.nm_id, (wantByNm.get(it.nm_id) || 0) + it.units);
        // Баркод/вендор + РЕАЛЬНО доступная порция направления (ffId→wb) per nm — грузим в
        // черновик min(want, avail), ровно столько снимет stripPalletsFromPrebook (консервация).
        const meta = new Map<number, { barcode: string; vendor_code: string; avail: number }>();
        for (const r of prebook) {
            if ((r.package_type || 'BOX') !== 'MONOPALLET' || !r.barcode || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) continue;
            const portion = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
            const e = meta.get(r.nm_id);
            if (e) e.avail += portion;
            else meta.set(r.nm_id, { barcode: r.barcode, vendor_code: r.vendor_code, avail: portion });
        }
        const shipRows: AssemblyDraftRow[] = [...wantByNm.entries()]
            .map(([nm, want]): AssemblyDraftRow | null => {
                const m = meta.get(nm);
                const qty = Math.min(want, m?.avail ?? 0);
                return m && qty > 0
                    ? { nm_id: nm, barcode: m.barcode, vendor_code: m.vendor_code, src: { [chosenKey]: qty }, tgt: { [wb]: qty }, package_type: 'MONOPALLET' as PackageType, as_is: true }
                    : null;
            })
            .filter((r): r is AssemblyDraftRow => r != null);
        if (shipRows.length === 0) { showToast('Не нашёл баркоды артикулов паллет в предброни', 'error'); return; }
        const units = shipRows.reduce((s, r) => s + (r.tgt[wb] || 0), 0);
        const partials = sel.filter(x => x.pallet.fillPct < 0.999).length;
        const what = sel.length === 1 ? `паллету ${palletsLabel(sel)}` : `${formatNumber(sel.length, 0)} паллет (${palletsLabel(sel)})`;
        if (!window.confirm(`Перенести ${what} в черновик как есть (${formatNumber(units, 0)} шт${partials ? `, в т.ч. ${formatNumber(partials, 0)} частичн.` : ''})? Моно поедет частичными паллетами; остальные паллеты направления останутся в предброни.`)) return;
        const key = `${wb}::${ffId}::#${sel.map(x => x.palletNo).join('+')}`;
        setPalletOp(key);
        try {
            for (const r of shipRows) prebookOriginRef.current.add(`${r.nm_id}::${wb}`);
            const nextPrebook = stripPalletsFromPrebook(sel.map(x => x.pallet), wb, ffId);
            const mergedRows = mergeDraftRows([...rows, ...shipRows]);
            const targetNames = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.tgt))));
            const sourceIds = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0))));
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), rows: mergedRows, prebook: nextPrebook, source_warehouse_ids: sourceIds, target_warehouse_names: targetNames } });
            healScopeRef.current = { ts: updated.updated_at, only: new Set([directionKey('MONOPALLET', wb)]) };
            applyDraft(updated);
            showToast(`${sel.length === 1 ? `Паллета ${palletsLabel(sel)}` : `Паллеты ${palletsLabel(sel)}`} перенесены в черновик (${formatNumber(units, 0)} шт как есть) — бейдж «из предброни» в раскладке`, 'success');
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка переноса в черновик', 'error'); }
        finally { setPalletOp(null); }
    }, [draftId, palletOp, prebook, rows, stripPalletsFromPrebook, buildDistribution, applyDraft, showToast]);

    // ── «Очистить черновик»: удалить всё наполнение (строки + источники/цели). ──
    const [clearing, setClearing] = useState(false);
    const handleClearDraft = useCallback(async () => {
        if (!draftId || clearing) return;
        if (rows.length === 0 && prebook.length === 0) { showToast('Черновик уже пуст', 'success'); return; }
        if (!window.confirm('Очистить черновик? Всё наполнение и предбронь будут удалены.')) return;
        setClearing(true);
        try {
            const dist: AssemblyDraftDistribution = {
                ...buildDistribution(),
                rows: [],
                prebook: [],
                source_warehouse_ids: [],
                target_warehouse_names: [],
                cold_start_shares: null,
            };
            const updated = await api.updateAssemblyDraft(draftId, { distribution: dist });
            applyDraft(updated);
            showToast('Черновик очищен', 'success');
        } catch (e: unknown) {
            showToast(e instanceof Error ? e.message : 'Ошибка очистки черновика', 'error');
        } finally {
            setClearing(false);
        }
    }, [draftId, clearing, rows.length, prebook.length, buildDistribution, applyDraft, showToast]);

    // Удаление неликвида из вкладки «Прогноз»: убрать SKU из черновика на бэке, затем
    // АВТО-дозабивка — пере-собрать остатки округов в целые паллеты (как «Дозабить»).
    // Возвращает {removed, consolidated} для тоста. Бросает — вызывающий ловит.
    const handleRemoveSkus = useCallback(async (nmIds: number[]): Promise<{ removed: number; consolidated: boolean }> => {
        if (!draftId || nmIds.length === 0) return { removed: 0, consolidated: false };
        const after = await api.removeAssemblyDraftRows(draftId, nmIds); // возвращает обновлённый черновик
        let consolidated = false;
        try {
            const norm = await normalizeAndSave(draftId, after); // целые коробы + целые паллеты после удаления
            if (norm) { consolidated = norm.changed; applyDraft(norm.draft); }
            else applyDraft(after); // geom не готова
        } catch { applyDraft(after); }
        return { removed: nmIds.length, consolidated };
    }, [draftId, normalizeAndSave, applyDraft]);

    // ─── Autosave: debounce 5s after any change ──────────────────────────
    useEffect(() => {
        if (!initialLoadRef.current || !draftId) return;
        const timer = setTimeout(() => {
            const json = JSON.stringify(buildDistribution());
            if (json !== lastSavedJsonRef.current) saveDraft(true).catch(() => {});
        }, AUTOSAVE_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [rows, sourceWarehouseIds, targetWarehouseNames, palletsCount, palletWeightKg, estimatedReadyDate, name, comment, draftId, buildDistribution, saveDraft]);

    // Переименование черновика (как на предпросмотре): сохраняем только name.
    const skipSaveNameRef = useRef(false);
    const saveDraftName = useCallback(async () => {
        setEditingName(false);
        if (skipSaveNameRef.current) { skipSaveNameRef.current = false; setName(draft?.name ?? ''); return; }
        if (!draftId || !draft) return;
        const trimmed = name.trim();
        if (trimmed === '' || trimmed === draft.name) { setName(draft.name); return; }
        try {
            const updated = await api.updateAssemblyDraft(draftId, { name: trimmed });
            setDraft(updated);
            setName(updated.name);
            showToast('Название сохранено', 'success');
        } catch (e: unknown) {
            setName(draft.name);
            showToast(e instanceof Error ? e.message : 'Не удалось переименовать', 'error');
        }
    }, [draftId, draft, name, showToast]);

    // WB-склад → ключ округа (для кросс-SKU палет-консолидации в панелях добавления).
    const districtRecord = useMemo(() => {
        const m: Record<string, string> = {};
        for (const w of stockNeed?.warehouses ?? []) if (w.district_key) m[w.name] = w.district_key;
        return m;
    }, [stockNeed]);

    // nm_id уже в черновике (строки + переданные на ФФ юниты) — вычитаем из кандидатов.
    const existingNmIds = useMemo(() => {
        const s = new Set<number>();
        for (const r of rows) s.add(r.nm_id);
        for (const h of handedUnits) for (const it of h.items) s.add(it.nm_id);
        return s;
    }, [rows, handedUnits]);

    // nm_id строк черновика — скрыть из таблицы потребности во встроенной вкладке.
    const draftNmIds = useMemo(() => {
        const s = new Set<number>();
        for (const r of rows) s.add(r.nm_id);
        return s;
    }, [rows]);

    const geomReady = geomState === 'ready';
    const backToList = useCallback(() => router.push(`/p/${slug}/warehouse/assembly`), [router, slug]);

    // ─── Render ──────────────────────────────────────────────────────────
    if (loading) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка черновика…</div>
            </div>
        );
    }
    if (error || !draft) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>
                    {error || 'Не удалось загрузить черновик'}
                    <div style={{ marginTop: 12 }}>
                        <button className="btn btn-secondary btn-sm" onClick={backToList}>← К заявкам на сборку</button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="animate-in">
            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}

            {/* Header */}
            <div className="page-header" style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, flexWrap: 'wrap' }}>
                    <button className="btn btn-secondary btn-sm" onClick={backToList}>← Назад</button>
                    <h1 className="page-title" style={{ margin: 0 }}>Сборка</h1>
                    {editingName ? (
                        <input
                            className="form-input"
                            autoFocus
                            value={name}
                            onChange={e => setName(e.target.value)}
                            onBlur={saveDraftName}
                            onKeyDown={e => {
                                if (e.key === 'Enter') e.currentTarget.blur();
                                if (e.key === 'Escape') { skipSaveNameRef.current = true; e.currentTarget.blur(); }
                            }}
                            style={{ maxWidth: 320, fontSize: 14 }}
                        />
                    ) : (
                        <button className="btn btn-secondary btn-sm" onClick={() => setEditingName(true)} title="Изменить название черновика">
                            ✏️ {name || 'Без названия'}
                        </button>
                    )}
                    {activeTab === 'draft' && (
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{saving ? 'Сохранение…' : 'Автосохранение включено'}</span>
                    )}
                </div>
            </div>

            <TabLayout tabs={TABS.map(t => t.key === 'prebook' && prebook.length > 0 ? { ...t, label: `🅿️ Предбронь (${prebook.length})` } : t)} active={activeTab} onChange={setTab} />

            {/* Вкладка «Черновик сборки» — редактор строк + предпросмотр + commit */}
            {activeTab === 'draft' && (
                <>
                    <div className="glass-card" style={{ padding: 12, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                        <button className="btn btn-primary btn-sm" onClick={handleFillFromNeed} disabled={filling}>
                            {filling ? 'Заполнение…' : '⚡ Заполнить черновик из потребности'}
                        </button>
                        {rows.length > 0 && (
                            <button className="btn btn-danger btn-sm" onClick={handleClearDraft} disabled={clearing}>
                                {clearing ? 'Очистка…' : '🗑 Очистить черновик'}
                            </button>
                        )}
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                            Соберёт черновик заново строго из «Потребность по складам» (с bump). Текущие строки будут заменены.
                        </span>
                    </div>
                    {prebook.length > 0 && (
                        <div className="glass-card" style={{ padding: 12, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                            <span style={{ fontWeight: 700 }}>🅿️ Предбронь: {formatNumber(prebook.length, 0)} поз.</span>
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>целые коробы, не собравшие паллету — дозабор/удаление на вкладке «Предбронь»</span>
                            <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setTab('prebook')}>Открыть предбронь →</button>
                        </div>
                    )}
                    {geomState === 'error' && (
                        <div className="glass-card" style={{ padding: 12, marginBottom: 12, borderLeft: '3px solid var(--color-danger)', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                            <span style={{ color: 'var(--color-danger)', fontWeight: 700 }}>⛔ Кратности коробов не загрузились</span>
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                заполнение/добавление и самоочистка черновика заблокированы — без кратностей строки легли бы россыпью
                            </span>
                            <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => loadGeometry()}>↻ Повторить загрузку</button>
                        </div>
                    )}
                    {noPpbArticles.length > 0 && (
                        <div className="glass-card" style={{ padding: 12, marginBottom: 12 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
                                <span style={{ fontWeight: 700, color: 'var(--color-warning)' }}>⚠️ Без кратности короба — {formatNumber(noPpbArticles.length, 0)} арт.</span>
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    нет данных «шт/короб» → НЕ участвуют в расчёте (россыпью не едут), пока не указана кратность
                                </span>
                                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setTab('box')}>
                                    📦 Указать кратность →
                                </button>
                            </div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                {noPpbArticles.slice(0, 40).map(a => (
                                    <span key={a.nm_id} title={`nm ${a.nm_id} · свободный сток ФФ: ${formatNumber(a.rf, 0)} шт`}
                                        style={{ fontSize: 11, padding: '1px 7px', borderRadius: 5, background: 'rgba(245,158,11,0.12)', color: 'var(--color-warning)' }}>
                                        {a.vendor} · {formatNumber(a.rf, 0)} шт
                                    </span>
                                ))}
                                {noPpbArticles.length > 40 && (
                                    <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>…ещё {formatNumber(noPpbArticles.length - 40, 0)}</span>
                                )}
                            </div>
                        </div>
                    )}
                    {partialPpbArticles.length > 0 && (
                        <div className="glass-card" style={{ padding: 12, marginBottom: 12 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
                                <span style={{ fontWeight: 700, color: 'var(--color-warning)' }}>🧩 Частичная кратность — {formatNumber(partialPpbArticles.length, 0)} арт.</span>
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    кратность есть, но НЕ на складе с остатком → эта порция уедет россыпью; задайте кратность на складе-источнике
                                </span>
                                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setTab('box')}>
                                    📦 Указать кратность →
                                </button>
                            </div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                {partialPpbArticles.slice(0, 40).map(a => (
                                    <span key={a.nm_id} title={`nm ${a.nm_id} · остаток на складах без кратности: ${formatNumber(a.rf, 0)} шт`}
                                        style={{ fontSize: 11, padding: '1px 7px', borderRadius: 5, background: 'rgba(245,158,11,0.12)', color: 'var(--color-warning)' }}>
                                        {a.vendor} · {formatNumber(a.rf, 0)} шт
                                    </span>
                                ))}
                                {partialPpbArticles.length > 40 && (
                                    <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>…ещё {formatNumber(partialPpbArticles.length - 40, 0)}</span>
                                )}
                            </div>
                        </div>
                    )}
                    {noBoxSizeArticles.length > 0 && (
                        <div className="glass-card" style={{ padding: 12, marginBottom: 12 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
                                <span style={{ fontWeight: 700, color: 'var(--color-warning)' }}>📐 Без размера коробки — {formatNumber(noBoxSizeArticles.length, 0)} арт.</span>
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    кратность есть, а габаритов нет → короба соберутся, но паллета не сформируется (моно/крупногабарит); задайте размер
                                </span>
                                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setTab('box')}>
                                    📐 Указать размер →
                                </button>
                            </div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                {noBoxSizeArticles.slice(0, 40).map(a => (
                                    <span key={a.nm_id} title={`nm ${a.nm_id} · свободный сток ФФ: ${formatNumber(a.rf, 0)} шт`}
                                        style={{ fontSize: 11, padding: '1px 7px', borderRadius: 5, background: 'rgba(245,158,11,0.12)', color: 'var(--color-warning)' }}>
                                        {a.vendor} · {formatNumber(a.rf, 0)} шт
                                    </span>
                                ))}
                                {noBoxSizeArticles.length > 40 && (
                                    <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>…ещё {formatNumber(noBoxSizeArticles.length - 40, 0)}</span>
                                )}
                            </div>
                        </div>
                    )}
                    <AddFromNeedPanel
                        stockNeed={stockNeed}
                        existingNmIds={existingNmIds}
                        nmPpb={nmPpb}
                        nmBoxSize={nmBoxSize}
                        palletOverrides={palletOverrides}
                        districtMap={districtRecord}
                        onAddRows={handleAddRows}
                        onToast={showToast}
                    />
                    <DraftPreview
                        slug={slug}
                        draftId={draft.id}
                        rows={rows}
                        newcomerNmIds={newcomerNmIds}
                        warehouses={warehouses}
                        nmPpb={nmPpb}
                        nmPpbByWh={nmPpbByWh}
                        nmMeta={nmMeta}
                        nmBoxSize={nmBoxSize}
                        palletOverrides={palletOverrides}
                        geomReady={geomReady}
                        prebookOrigin={prebookOriginRef.current}
                        ensureSaved={ensureSaved}
                        onToast={showToast}
                        onReloadDraft={reloadDraft}
                    />
                </>
            )}

            {/* Вкладка «Потребность по складам» — встроенный WarehouseNeedView */}
            {activeTab === 'need' && (
                <WarehouseNeedView
                    embeddedDraftId={draft.id}
                    hiddenNmIds={draftNmIds}
                    onRowsAddedToDraft={handleRowsAdded}
                    autoCheckAcceptance
                    fillAllSignal={fillSignal}
                    onFillAllRows={handleFillAllRows}
                />
            )}

            {/* Вкладка «Кратность» */}
            {activeTab === 'box' && <BoxMultiplicityView />}

            {/* Вкладка «Паллеты» */}
            {activeTab === 'pallets' && <PalletSizesView />}

            {/* Вкладка «Предраспределение машины в пути» — раздача груза по WB-складам до приёмки */}
            {activeTab === 'pre-dist' && <PreDistributionView />}

            {/* Вкладка «Предбронь» — коробы, не собравшие паллету, + минимальный дозабор */}
            {activeTab === 'prebook' && (
                <PrebookView
                    groups={prebookGroups}
                    toppingUpKey={toppingUp}
                    shipAsIsKey={shippingAsIs}
                    deletingKey={deletingDir}
                    prebookingKey={creatingPrebooking}
                    tailTopUpKey={toppingUpPrebook}
                    trimTailKey={trimmingTail}
                    acceptanceMarks={prebookAcceptanceMarks}
                    acceptanceLoading={prebookAccLoading}
                    preorderWbs={preorderWbs}
                    onTopUp={handleTopUpDirection}
                    onShipAsIs={handleShipPrebookAsIs}
                    onDelete={handleDeletePrebookItem}
                    onDeleteDirection={handleDeleteDirection}
                    onCreatePrebooking={handleCreatePrebooking}
                    onTopUpPrebook={handleTopUpPrebookMono}
                    onTrimTail={handleTrimPrebookTail}
                    onBookPallets={handleBookPallets}
                    onReleasePallets={handleReleasePallets}
                    onDraftPallets={handleDraftPallets}
                    palletOpKey={palletOp}
                />
            )}

            {/* Вкладка «Прогноз / Локализация» — загрузка WB-складов с учётом черновика */}
            {activeTab === 'forecast' && (
                <ForecastView draftId={draft.id} onRemoveSkus={handleRemoveSkus} onToast={showToast} />
            )}

            {/* Вкладка «Настройки складов» — исключение/закрытие складов + время РФ→WB */}
            {activeTab === 'settings' && <WarehouseExclusionSettings />}

            {/* «Заполнить черновик из потребности»: скрытый инстанс WarehouseNeedView считает
                раскладку ВСЕЙ потребности (data + свободные лимиты приёмки) и через onFillAllRows
                ЗАМЕНЯЕТ черновик. Блокирующий оверлей — чтобы не ушли во время расчёта. */}
            {filling && (
                <>
                    <div style={{
                        position: 'fixed', inset: 0, zIndex: 200,
                        background: 'rgba(0,0,0,0.18)', backdropFilter: 'blur(6px)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                        <div className="glass-card" style={{ padding: 28, textAlign: 'center', maxWidth: 360 }}>
                            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 12 }}>⚡ Собираю черновик из всей потребности…</div>
                            <div className="skeleton" style={{ width: '80%', height: 12, margin: '0 auto 8px' }} />
                            <div className="skeleton" style={{ width: '60%', height: 12, margin: '0 auto 14px' }} />
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Проверяю свободные лимиты приёмки WB — несколько секунд, не закрывайте страницу</div>
                        </div>
                    </div>
                    <div style={{ display: 'none' }} aria-hidden>
                        <WarehouseNeedView
                            embeddedDraftId={draft.id}
                            autoCheckAcceptance
                            fillAllSignal={fillSignal}
                            onFillAllRows={handleFillAllRows}
                        />
                    </div>
                </>
            )}
        </div>
    );
}
