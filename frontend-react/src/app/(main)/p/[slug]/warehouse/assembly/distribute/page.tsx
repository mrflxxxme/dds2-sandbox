'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { dropCommittedRows } from '@/lib/utils/assemblyDraftReconcile';
import { parseBoxSize } from '@/lib/utils/boxPallet';
import { normalizeDraft, type NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';
import { Toast } from '@/components';
import TabLayout from '@/components/TabLayout';
import DraftPreview from './components/DraftPreview';
import AddFromNeedPanel from './components/AddFromNeedPanel';
import AddByBarcodePanel from './components/AddByBarcodePanel';
import { WarehouseNeedView } from '../../analytics/components/WarehouseNeedView';
import { BoxMultiplicityView } from '../../box-multiplicity/BoxMultiplicityView';
import { PalletSizesView } from '../../pallet-sizes/PalletSizesView';
import ForecastView from './components/ForecastView';
import { WarehouseExclusionSettings } from '../../analytics/components/WarehouseExclusionSettings';
import type {
    AssemblyDraft,
    AssemblyDraftDistribution,
    AssemblyDraftRow,
    HandedUnit,
    StockNeedResponse,
    Warehouse,
} from '@/types/api';

const AUTOSAVE_DEBOUNCE_MS = 5000;

type AssemblyTab = 'draft' | 'need' | 'box' | 'pallets' | 'forecast' | 'settings';
const TABS: { key: AssemblyTab; label: string }[] = [
    { key: 'draft', label: '📝 Черновик сборки' },
    { key: 'need', label: '🏬 Потребность по складам' },
    { key: 'box', label: '📦 Кратность' },
    { key: 'pallets', label: '🚚 Паллеты' },
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
        tabParam === 'need' || tabParam === 'box' || tabParam === 'pallets' || tabParam === 'forecast' || tabParam === 'settings'
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
    }), [sourceWarehouseIds, targetWarehouseNames, rows, palletsCount, palletWeightKg, estimatedReadyDate, coldStartShares, handedUnits]);

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
        const draft = res.changed
            ? await api.updateAssemblyDraft(id, { distribution: { ...base.distribution, rows: res.rows } })
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
            const targetNames = Array.from(new Set(newRows.flatMap(r => Object.keys(r.tgt))));
            const sourceIds = Array.from(new Set(
                newRows.flatMap(r => Object.keys(r.src).map(Number).filter(n => Number.isFinite(n) && n > 0)),
            ));
            const dist: AssemblyDraftDistribution = {
                ...buildDistribution(),
                rows: newRows,
                source_warehouse_ids: sourceIds,
                target_warehouse_names: targetNames,
            };
            const units = newRows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            const updated = await api.updateAssemblyDraft(draftId, { distribution: dist });
            applyDraft(updated);
            showToast(`Черновик заполнен из потребности: ${formatNumber(newRows.length, 0)} строк · Σ ${formatNumber(units, 0)} шт`, 'success');
        } catch (e: unknown) {
            showToast(e instanceof Error ? e.message : 'Ошибка заполнения черновика', 'error');
        } finally {
            setFillSignal(0);   // сброс сигнала, чтобы повторный расчёт не само-запускался
            setFilling(false);
        }
    }, [draftId, buildDistribution, applyDraft, showToast]);

    // ── «Очистить черновик»: удалить всё наполнение (строки + источники/цели). ──
    const [clearing, setClearing] = useState(false);
    const handleClearDraft = useCallback(async () => {
        if (!draftId || clearing) return;
        if (rows.length === 0) { showToast('Черновик уже пуст', 'success'); return; }
        if (!window.confirm('Очистить черновик? Всё наполнение будет удалено.')) return;
        setClearing(true);
        try {
            const dist: AssemblyDraftDistribution = {
                ...buildDistribution(),
                rows: [],
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
    }, [draftId, clearing, rows.length, buildDistribution, applyDraft, showToast]);

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

            <TabLayout tabs={TABS} active={activeTab} onChange={setTab} />

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
