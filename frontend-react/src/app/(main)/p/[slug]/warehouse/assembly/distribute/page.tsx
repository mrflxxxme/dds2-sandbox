'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { dropCommittedRows } from '@/lib/utils/assemblyDraftReconcile';
import { parseBoxSize, effectiveBoxesPerPallet, maxPalletHeightCm } from '@/lib/utils/boxPallet';
import { normalizeDraft, consolidatePrebookWholePallets, reconcileFillWithReserved, type NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';
import { allocatePairs } from '@/lib/utils/assemblyPreview';
import { palletFootprint, planTopUpBoxes, type TopUpCandidate } from '@/lib/assembly/prebookFootprint';
import { Toast } from '@/components';
import TabLayout from '@/components/TabLayout';
import DraftPreview from './components/DraftPreview';
import AddFromNeedPanel from './components/AddFromNeedPanel';
import AddByBarcodePanel from './components/AddByBarcodePanel';
import { WarehouseNeedView } from '../../analytics/components/WarehouseNeedView';
import { BoxMultiplicityView } from '../../box-multiplicity/BoxMultiplicityView';
import { PalletSizesView } from '../../pallet-sizes/PalletSizesView';
import ForecastView from './components/ForecastView';
import PreDistributionView from './components/PreDistributionView';
import PrebookView, { type PrebookGroup, type PrebookTopUp } from './components/PrebookView';
import { WarehouseExclusionSettings } from '../../analytics/components/WarehouseExclusionSettings';
import type {
    AssemblyDraft,
    AssemblyDraftDistribution,
    AssemblyDraftRow,
    PackageType,
    HandedUnit,
    StockNeedResponse,
    Warehouse,
} from '@/types/api';

const AUTOSAVE_DEBOUNCE_MS = 5000;

/** Слить строки распределения по ключу (nm_id, barcode, package_type): src/tgt
 *  суммируются поэлементно. Зеркало backend merge — предпросмотр = коммит. */
function mergeDraftRows(rs: AssemblyDraftRow[]): AssemblyDraftRow[] {
    const m = new Map<string, AssemblyDraftRow>();
    for (const r of rs) {
        const k = `${r.nm_id}::${r.barcode}::${r.package_type || 'BOX'}`;
        let e = m.get(k);
        if (!e) { e = { nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: {}, tgt: {}, package_type: r.package_type || 'BOX' }; m.set(k, e); }
        for (const [ff, q] of Object.entries(r.src)) e.src[ff] = (e.src[ff] || 0) + (q || 0);
        for (const [w2, q] of Object.entries(r.tgt)) e.tgt[w2] = (e.tgt[w2] || 0) + (q || 0);
    }
    return [...m.values()];
}

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
        const k = `${r.nm_id}-${r.package_type || 'BOX'}-${r.barcode || ''}`;
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
    const [nmBoxSize, setNmBoxSize] = useState<Map<number, string | null>>(new Map());
    const [nmMeta, setNmMeta] = useState<Map<number, { subject: string; brand: string }>>(new Map());
    const [palletOverrides, setPalletOverrides] = useState<Record<string, number>>({});
    const [geomState, setGeomState] = useState<'loading' | 'ready' | 'error'>('loading');
    const [coldStartShares, setColdStartShares] = useState<Record<string, number> | null>(null);
    const [handedUnits, setHandedUnits] = useState<HandedUnit[]>([]);
    const [newcomerNmIds, setNewcomerNmIds] = useState<Set<number>>(new Set());

    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [stockNeed, setStockNeed] = useState<StockNeedResponse | null>(null);

    const lastSavedJsonRef = useRef<string>('');
    const initialLoadRef = useRef(false);

    // id текущего черновика (синглтон). null до первой загрузки.
    const draftId = draft?.id ?? null;

    const showToast = useCallback((message: string, type: 'success' | 'error') => setToast({ message, type }), []);

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
    useEffect(() => {
        let cancelled = false;
        api.getBoxMultiplicity()
            .then(resp => {
                if (cancelled) return;
                const m = new Map<number, number | null>();
                const sizes = new Map<number, string | null>();
                const meta = new Map<number, { subject: string; brand: string }>();
                for (const r of resp.items) {
                    let ppb: number | null = null;
                    if (r.box_qty_override && r.box_qty_override > 0 && r.use_box_multiplicity) {
                        ppb = r.box_qty_override;
                    } else {
                        let best = 0;
                        for (const p of r.per_warehouse) {
                            if (p.box_qty && p.box_qty > 0 && p.use_box_multiplicity && (best === 0 || p.box_qty < best)) best = p.box_qty;
                        }
                        ppb = best > 0 ? best : null;
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
                setNmBoxSize(sizes);
                setNmMeta(meta);
                setGeomState('ready');
            })
            .catch(() => { if (!cancelled) setGeomState('error'); });
        return () => { cancelled = true; };
    }, []);

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
    }), [sourceWarehouseIds, targetWarehouseNames, rows, palletsCount, palletWeightKg, estimatedReadyDate, coldStartShares, handedUnits, prebook]);

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

        const dist: AssemblyDraftDistribution = { ...buildDistribution(), rows: effectiveRows };
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
    }, [draftId, buildDistribution, name, comment, rows, showToast]);

    const ensureSaved = useCallback(() => saveDraft(true), [saveDraft]);

    // Перезагрузить текущий черновик (после партиального commit или долива из потребности).
    const reloadDraft = useCallback(async () => {
        try {
            const d = await api.getOrCreateCurrentDraft();
            applyDraft(d);
        } catch { /* ignore */ }
    }, [applyDraft]);

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
            boxSizeOf: (nm) => nmBoxSize.get(nm) ?? null,
            overrides: palletOverrides,
            isNewcomer: (nm) => newcomerNmIds.has(nm),
            freeByNm,
        };
    }, [stockNeed, nmPpb, nmBoxSize, palletOverrides, newcomerNmIds]);

    // Привести черновик `id` к инварианту (целые коробы + целые паллеты + моно ≤3) и
    // сохранить, если раскладка изменилась. Гейт по готовности геометрии (без габаритов
    // нормализация снесла бы строки) → null. `fresh` передаётся вызывающим, у которого
    // уже есть свежий черновик (add/remove возвращают его) — экономит лишний GET. Возвращает
    // финальный черновик (для `applyDraft` вместо ещё одного reload-GET). Бросает наверх.
    const normalizeAndSave = useCallback(async (
        id: number,
        fresh?: AssemblyDraft,
    ): Promise<{ changed: boolean; droppedUnits: number; draft: AssemblyDraft } | null> => {
        if (geomState !== 'ready') return null;
        const base = fresh ?? await api.getAssemblyDraft(id);
        const baseRows = base.distribution.rows || [];
        const res = normalizeDraft(baseRows, buildNormalizeCtx(baseRows));
        // Срезанные до-паллетные хвосты → в предбронь (не теряем на ФФ и не «съедаем»
        // молча частичную паллету, отгруженную кнопкой «Оставить так»). Хвосты
        // сливаются с уже лежащей предбронью по (nm,barcode,pkg).
        const nextPrebook = res.dropped.length
            ? mergeDraftRows([...(base.distribution.prebook || []), ...res.dropped])
            : (base.distribution.prebook || []);
        const draft = res.changed
            ? await api.updateAssemblyDraft(id, { distribution: { ...base.distribution, rows: res.rows, prebook: nextPrebook } })
            : base;
        return { changed: res.changed, droppedUnits: res.droppedUnits, draft };
    }, [geomState, buildNormalizeCtx]);

    // Дозалив строк из панелей A/B: флашим локальные правки, merge на бэке (возвращает
    // обновлённый черновик), нормализуем к целым коробам+паллетам и применяем результат
    // (без лишних reload-GET). Бросает — панель ловит и тостит.
    const handleAddRows = useCallback(async (newRows: AssemblyDraftRow[]) => {
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
    }, [draftId, ensureSaved, normalizeAndSave, applyDraft, showToast]);

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
        if (rows.length > 0 && !window.confirm('Заменить весь черновик раскладкой из «Потребность по складам»? Текущие строки и ручные правки будут удалены.')) return;
        // НЕ переключаем вкладку: расчёт идёт в скрытом инстансе WarehouseNeedView под
        // блокирующим оверлеем (иначе прыжок на вкладку + ожидание лимитов сбивали с толку).
        setFilling(true);
        setFillSignal(s => s + 1);
    }, [filling, rows.length]);

    const handleFillAllRows = useCallback(async (newRows: AssemblyDraftRow[]) => {
        try {
            if (!draftId) return;
            if (newRows.length === 0) { showToast('В потребности нечего отгрузить', 'error'); return; }
            // РЕЗЕРВ: зарезервированная предбронь (текущая) ПИНится к своим направлениям —
            // при заполнении сначала кладём эти коробы туда (вычитая из свежей потребности,
            // без задвоения), излишек/устаревшее — отпускается. Итог по (nm,склад)=потребность.
            const seeded = prebook.length ? reconcileFillWithReserved(newRows, prebook) : newRows;
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
                + (newPrebook.length ? ` · предбронь ${formatNumber(newPrebook.length, 0)} поз. (${formatNumber(pbUnits, 0)} шт)` : ''),
                'success',
            );
        } catch (e: unknown) {
            showToast(e instanceof Error ? e.message : 'Ошибка заполнения черновика', 'error');
        } finally {
            setFillSignal(0);   // сброс сигнала, чтобы повторный расчёт не само-запускался
            setFilling(false);
        }
    }, [draftId, prebook, buildDistribution, applyDraft, showToast, geomState, buildNormalizeCtx]);

    // ── ПРЕДБРОНЬ: целые коробы, не собравшие паллету при заполнении. ──
    // Группы предброни: (упаковка × направление), с оценкой возможности дозабора.
    const prebookGroups = useMemo<PrebookGroup[]>(() => {
        if (prebook.length === 0) return [];
        const ffName = new Map<number, string>();
        for (const w of stockNeed?.rf_warehouses ?? []) ffName.set(w.id, w.name);
        const nmVendor = new Map<number, string>();
        for (const a of stockNeed?.articles ?? []) nmVendor.set(a.nm_id, a.vendor_code || '');
        const inUse: Record<number, Record<number, number>> = {};
        for (const r of [...rows, ...prebook]) { const m = (inUse[r.nm_id] ??= {}); for (const [ff, q] of Object.entries(r.src)) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0); }
        // Свободные целые коробы любого box-SKU per ФФ (смешанная паллета — любой SKU),
        // отсортированные по убыванию запаса. Footprint кандидата зависит от склада-цели
        // (высота паллеты), поэтому bpp считаем на месте, ниже — по каждому направлению.
        const freeBoxesByFf = new Map<number, { nmId: number; ppb: number; freeBoxes: number }[]>();
        for (const a of stockNeed?.articles ?? []) {
            const ppb = nmPpb.get(a.nm_id) || 0;
            if (ppb <= 0 || !nmBoxSize.get(a.nm_id)) continue;
            for (const [ff, st] of Object.entries(a.rf_stocks || {})) {
                const boxes = Math.floor(((st.available || 0) - (inUse[a.nm_id]?.[Number(ff)] || 0)) / ppb);
                if (boxes > 0) {
                    const arr = freeBoxesByFf.get(Number(ff)) ?? [];
                    arr.push({ nmId: a.nm_id, ppb, freeBoxes: boxes });
                    freeBoxesByFf.set(Number(ff), arr);
                }
            }
        }
        for (const arr of freeBoxesByFf.values()) arr.sort((x, y) => y.freeBoxes - x.freeBoxes);
        type Acc = { pkg: PackageType; wb: string; ffId: number; items: { nm_id: number; vendor_code: string; ff: string; boxes: number; qty: number }[]; qty: number };
        const map = new Map<string, Acc>();
        for (const r of prebook) {
            const pkg = r.package_type || 'BOX';
            const ppb = nmPpb.get(r.nm_id) || 0;
            // Разбиваем строку по парам (ФФ→склад) тем же allocatePairs, что и коммит:
            // одна строка может сорсить один склад с НЕСКОЛЬКИХ ФФ — каждая порция идёт
            // в СВОЮ карточку ФФ (иначе весь tgt приписался бы первому ФФ → неверный
            // источник в заявке и завышенный footprint группы).
            for (const [pairKey, q] of allocatePairs(r.src, r.tgt)) {
                if ((q || 0) <= 0) continue;
                const sep = pairKey.indexOf('::');
                const ffId = Number(pairKey.slice(0, sep));
                const wb = pairKey.slice(sep + 2);
                const ffLabel = ffId >= 0 ? (ffName.get(ffId) || `ФФ ${ffId}`) : '—';
                const key = `${pkg}::${wb}::${ffId}`;
                let g = map.get(key);
                if (!g) { g = { pkg, wb, ffId, items: [], qty: 0 }; map.set(key, g); }
                g.items.push({ nm_id: r.nm_id, vendor_code: r.vendor_code, ff: ffLabel, boxes: ppb > 0 ? Math.round(q / ppb) : 0, qty: q });
                g.qty += q;
            }
        }
        const out: PrebookGroup[] = [];
        for (const g of map.values()) {
            // ИСТИННЫЙ footprint смешанной паллеты: КАЖДЫЙ SKU по своей геометрии
            // короба (Σ qty_i / upp_i), зеркало snapToWholePallets. Показ по одному
            // репрезентативному SKU врал до 3× на смешанных группах (замер на живых).
            const uppOf = (nm: number): number => {
                const bpp = effectiveBoxesPerPallet(nmBoxSize.get(nm) ?? null, maxPalletHeightCm(g.wb), palletOverrides);
                const ppb = nmPpb.get(nm) || 0;
                return bpp && ppb ? bpp * ppb : 0;
            };
            const footprint = palletFootprint(g.items.map(i => ({ nmId: i.nm_id, qty: i.qty })), uppOf);
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
            const ffLabel = ffName.get(g.ffId) || (g.ffId >= 0 ? `ФФ ${g.ffId}` : '—');
            out.push({ pkg: g.pkg, wb: g.wb, ff: ffLabel, ffId: g.ffId, items: g.items, boxes, qty: g.qty, footprint, fillPct, topUp });
        }
        return out;
    }, [prebook, rows, stockNeed, nmPpb, nmBoxSize, palletOverrides]);

    // Авто-вынос СОБРАВШИХСЯ целых паллет из предброни в черновик: в предброни остаются
    // только неполные (<1 паллеты) хвосты. Предбронь-группа могла накопить >1 паллеты
    // (сложение хвостов при дозаборе/добавлении) — целые должны ехать, а не «висеть».
    // Триггер: любая группа footprint≥1. Идемпотентно (после выноса групп ≥1 нет).
    const consolidatingRef = useRef(false);
    useEffect(() => {
        if (!draftId || geomState !== 'ready' || prebook.length === 0 || consolidatingRef.current) return;
        if (!prebookGroups.some(g => g.footprint >= 1)) return;
        consolidatingRef.current = true;
        void (async () => {
            try {
                const cons = consolidatePrebookWholePallets(prebook, buildNormalizeCtx([...rows, ...prebook]));
                if (!cons.changed) return;
                const mergedRows = mergeDraftRows([...rows, ...cons.toDraft]);
                const targetNames = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.tgt))));
                const sourceIds = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0))));
                const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), rows: mergedRows, prebook: cons.prebook, source_warehouse_ids: sourceIds, target_warehouse_names: targetNames } });
                applyDraft(updated);
                showToast(`Целые паллеты из предброни (${formatNumber(cons.extractedUnits, 0)} шт) перенесены в черновик`, 'success');
            } catch { /* best-effort — не критично */ }
            finally { consolidatingRef.current = false; }
        })();
    }, [draftId, geomState, prebook, prebookGroups, rows, buildNormalizeCtx, buildDistribution, applyDraft, showToast]);

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
            // ПРОВЕРКА ПРИЁМКИ WB: берём только SKU, реально открытые на wb (нужный тип упаковки),
            // и убеждаемся, что само направление открыто (хотя бы один предбронь-SKU принимается).
            const okNm = new Set<number>();
            let dirOpen = false;
            try {
                const items = [
                    ...pool.map(c => ({ nm_id: c.nm_id, barcode: c.barcode, distribution: { [wb]: c.ppb } })),
                    ...pbOnFf.map(r => ({ nm_id: r.nm_id, barcode: r.barcode, distribution: { [wb]: r.tgt[wb] || 0 } })),
                ];
                const resp = await api.checkWbAcceptance({ items }, true);
                for (const it of resp.items || []) {
                    const av = it.availability?.[wb];
                    const open = !!av && (pkg === 'BOX' ? !!av.can_box : pkg === 'MONOPALLET' ? !!av.can_monopallet : !!av.can_supersafe);
                    if (open) { okNm.add(it.nm_id); if (pbNm.has(it.nm_id)) dirOpen = true; }
                }
            } catch {
                showToast('Приёмку проверить не удалось — дозабор отменён', 'error');
                return;
            }
            if (!dirOpen) { showToast(`WB не принимает на «${wb}» — направление закрыто, дозабор невозможен`, 'error'); return; }
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
            const keptUnits = norm.rows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            if (keptUnits <= 0) { showToast('Не удалось дособрать целую паллету — не хватает свободного ФФ', 'error'); return; }
            const mergedRows = mergeDraftRows([...rows, ...norm.rows]);
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
            nextPrebook.push(...norm.dropped.filter(r => pbNm.has(r.nm_id)));
            const targetNames = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.tgt))));
            const sourceIds = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0))));
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), rows: mergedRows, prebook: nextPrebook, source_warehouse_ids: sourceIds, target_warehouse_names: targetNames } });
            applyDraft(updated);
            showToast(`Дособрано на «${wb}»: +${formatNumber(keptUnits, 0)} шт целыми паллетами`, 'success');
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка дозабивки', 'error'); }
        finally { setToppingUp(null); }
    }, [draftId, geomState, prebook, rows, stockNeed, nmPpb, nmBoxSize, palletOverrides, buildNormalizeCtx, buildDistribution, applyDraft, showToast]);

    // Отгрузить неполную паллету направления КАК ЕСТЬ: переносим предбронь этой отгрузки
    // (упаковка × склад × ФФ) в черновик частичной паллетой (без дозабора до целой) и
    // сохраняем НАПРЯМУЮ, без normalizeDraft (иначе он срезал бы неполную обратно).
    // Коммит паллеты не режет → частичная доедет в заявку.
    // ПРОВЕРКА ПРИЁМКИ WB (как в дозаборе): грузим только SKU, реально открытые на wb в
    // нужной упаковке; закрытые оставляем в предброни; всё направление закрыто → блок.
    // ⌛-лимит (0 свободных дней, платная приёмка) НЕ блокирует — «нужна предзаявка».
    const [shippingAsIs, setShippingAsIs] = useState<string | null>(null);
    const handleShipPrebookAsIs = useCallback(async (pkg: PackageType, wb: string, ffId: number) => {
        if (!draftId || shippingAsIs) return;
        const chosenKey = String(ffId);
        const key = `${pkg}::${wb}::${ffId}`;
        const affected = prebook.filter(r => (r.package_type || 'BOX') === pkg && (r.tgt[wb] || 0) > 0 && (r.src[chosenKey] || 0) > 0);
        if (affected.length === 0) return;
        setShippingAsIs(key);
        try {
            // Живая проверка приёмки по SKU направления.
            const okNm = new Set<number>();
            try {
                const items = affected.map(r => ({ nm_id: r.nm_id, barcode: r.barcode, distribution: { [wb]: allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0 } }));
                const resp = await api.checkWbAcceptance({ items }, true);
                for (const it of resp.items || []) {
                    const av = it.availability?.[wb];
                    const open = !!av && (pkg === 'BOX' ? !!av.can_box : pkg === 'MONOPALLET' ? !!av.can_monopallet : !!av.can_supersafe);
                    if (open) okNm.add(it.nm_id);
                }
            } catch {
                showToast('Приёмку проверить не удалось — отгрузка отменена', 'error');
                return;
            }
            if (okNm.size === 0) { showToast(`WB не принимает на «${wb}» — направление закрыто, отгрузка невозможна`, 'error'); return; }

            const shipRows: AssemblyDraftRow[] = [];
            let skipped = 0;
            const nextPrebook = prebook
                .map(r => {
                    if ((r.package_type || 'BOX') !== pkg || !(r.tgt[wb] > 0) || !(r.src[chosenKey] > 0)) return r;
                    if (!okNm.has(r.nm_id)) { skipped += 1; return r; }   // закрытый SKU — остаётся в предброни
                    // Порция ИМЕННО этого ФФ на этот склад (строка может сорсить wb с
                    // нескольких ФФ) — берём allocatePairs, а не весь tgt[wb], иначе
                    // перелили бы с одного ФФ и выкинули порцию другого.
                    const moved = allocatePairs(r.src, r.tgt).get(`${ffId}::${wb}`) || 0;
                    if (moved <= 0) return r;
                    shipRows.push({ nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [chosenKey]: moved }, tgt: { [wb]: moved }, package_type: pkg });
                    const tgt = { ...r.tgt }; tgt[wb] = Math.max(0, (tgt[wb] || 0) - moved); if (tgt[wb] <= 0) delete tgt[wb];
                    const src = { ...r.src }; src[chosenKey] = Math.max(0, (src[chosenKey] || 0) - moved); if (src[chosenKey] <= 0) delete src[chosenKey];
                    return { ...r, tgt, src };
                })
                .filter(r => Object.keys(r.tgt).length > 0);
            if (shipRows.length === 0) { showToast('Все SKU направления закрыты WB — отгружать нечего', 'error'); return; }
            const movedUnits = shipRows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            const mergedRows = mergeDraftRows([...rows, ...shipRows]);
            const targetNames = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.tgt))));
            const sourceIds = Array.from(new Set(mergedRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0))));
            const updated = await api.updateAssemblyDraft(draftId, { distribution: { ...buildDistribution(), rows: mergedRows, prebook: nextPrebook, source_warehouse_ids: sourceIds, target_warehouse_names: targetNames } });
            applyDraft(updated);
            showToast(
                `Отгружено как есть на «${wb}»: +${formatNumber(movedUnits, 0)} шт (неполная паллета)`
                + (skipped ? ` · ⛔ ${formatNumber(skipped, 0)} SKU закрыты WB — оставлены в предброни` : ''),
                'success',
            );
        } catch (e) { showToast(e instanceof Error ? e.message : 'Ошибка отгрузки', 'error'); }
        finally { setShippingAsIs(null); }
    }, [draftId, shippingAsIs, prebook, rows, buildDistribution, applyDraft, showToast]);

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
                    <AddByBarcodePanel
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
                        nmMeta={nmMeta}
                        nmBoxSize={nmBoxSize}
                        palletOverrides={palletOverrides}
                        geomReady={geomReady}
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
                    onTopUp={handleTopUpDirection}
                    onShipAsIs={handleShipPrebookAsIs}
                    onDelete={handleDeletePrebookItem}
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
