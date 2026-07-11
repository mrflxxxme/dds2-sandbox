'use client';
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { parseBoxSize, palletsForLines, maxPalletHeightCm, type PalletLine } from '@/lib/utils/boxPallet';
import { allocatePairs } from '@/lib/utils/assemblyPreview';
import { DISTRICT_ORDER, DISTRICT_LABELS, DISTRICT_COLORS } from '@/lib/constants/localization';
import { Toast } from '@/components';
import {
    applyAcceptanceSplits,
    applyDraftCellEdit,
    buildAutoSyncPlan,
    buildDraftSkus,
    buildWriteDistribution,
    enrichArticles,
    finalizeDraftDistribution,
    rowsToPreDistRows,
    sliceRowsByFf,
    splitByKratnost,
    type AcceptanceSplitMap,
    type DraftDistInput,
    type EnrichedSku,
} from '@/lib/assembly/draftDistribution';
import NeedMatrixCell, { type CellMark } from './NeedMatrixCell';
import AcceptanceBanner, { type AcceptanceSummary } from './AcceptanceBanner';
import type {
    AcceptanceCheckPerItem,
    AssemblyDraft,
    AssemblyDraftRow,
    LocalizationSkuRow,
    PackageType,
    StockNeedArticle,
    StockNeedResponse,
} from '@/types/api';

/** Сколько коробов из штук при кратности `ppb`. */
const boxesOf = (qty: number, ppb: number | null | undefined): number =>
    ppb && ppb > 0 ? Math.ceil(qty / ppb) : 0;

const districtRank = (d: string): number => {
    const i = (DISTRICT_ORDER as readonly string[]).indexOf(d);
    return i < 0 ? DISTRICT_ORDER.length : i;
};
const districtTint = (d: string): string | undefined => {
    const c = DISTRICT_COLORS[d];
    return c ? `color-mix(in srgb, ${c} 6%, transparent)` : undefined;
};

type SortKey = 'label' | 'avail' | 'inAssembly' | 'stocksWb' | 'need' | 'revenue' | 'loc' | 'ship' | 'boxes' | 'stays' | `wb:${string}`;

// Цвет «Лок %» по статусу КТР (excellent/neutral/weak/critical) — как на странице «Локализация».
const LOC_STATUS_COLOR: Record<string, string> = {
    excellent: 'var(--color-success)',
    neutral: 'var(--color-text)',
    weak: 'var(--color-warning)',
    critical: 'var(--color-danger)',
};

interface DistAgg {
    submitRows: { barcode: string; wb_warehouse_name: string; qty: number; package_type: PackageType }[];
    allocByBc: Map<string, number>;
    cellByBc: Map<string, Map<string, { qty: number; pkg: PackageType }>>;
    requestCount: number;
    totalShip: number;
    totalBoxes: number;
    shipByWh: Map<string, number>;
    boxesByWh: Map<string, number>;
    palletsByWh: Map<string, number>;
    totalPallets: number;
}
function buildDistAgg(
    rows: AssemblyDraftRow[],
    nmByBc: Map<string, number>,
    nmPpb: Map<number, number | null>,
    nmBoxSize: Map<number, string | null>,
    palletOverrides: Record<string, number>,
): DistAgg {
    const submitRows = rowsToPreDistRows(rows);
    const allocByBc = new Map<string, number>();
    const cellByBc = new Map<string, Map<string, { qty: number; pkg: PackageType }>>();
    const shipByWh = new Map<string, number>();
    const boxesByWh = new Map<string, number>();
    const linesByWhPkg = new Map<string, { wh: string; pkg: PackageType; lines: PalletLine[] }>();
    for (const r of submitRows) {
        const nm = nmByBc.get(r.barcode) ?? 0;
        allocByBc.set(r.barcode, (allocByBc.get(r.barcode) ?? 0) + r.qty);
        const cell = cellByBc.get(r.barcode) ?? new Map();
        const cur = cell.get(r.wb_warehouse_name);
        cell.set(r.wb_warehouse_name, { qty: (cur?.qty ?? 0) + r.qty, pkg: r.package_type });
        cellByBc.set(r.barcode, cell);
        shipByWh.set(r.wb_warehouse_name, (shipByWh.get(r.wb_warehouse_name) ?? 0) + r.qty);
        boxesByWh.set(r.wb_warehouse_name, (boxesByWh.get(r.wb_warehouse_name) ?? 0) + boxesOf(r.qty, nmPpb.get(nm)));
        const key = `${r.wb_warehouse_name}::${r.package_type}`;
        const g = linesByWhPkg.get(key) ?? { wh: r.wb_warehouse_name, pkg: r.package_type, lines: [] };
        g.lines.push({ units: r.qty, boxQty: nmPpb.get(nm), boxSize: nmBoxSize.get(nm) ?? null });
        linesByWhPkg.set(key, g);
    }
    const palletsByWh = new Map<string, number>();
    let totalPallets = 0;
    for (const [, g] of linesByWhPkg) {
        const p = palletsForLines(g.lines, maxPalletHeightCm(g.wh), g.pkg === 'BOX' ? 'box' : 'mono', palletOverrides).pallets;
        palletsByWh.set(g.wh, (palletsByWh.get(g.wh) ?? 0) + p);
        totalPallets += p;
    }
    const groupKeys = new Set(submitRows.map((r) => `${r.wb_warehouse_name}::${r.package_type}`));
    const totalShip = submitRows.reduce((s, r) => s + r.qty, 0);
    const totalBoxes = submitRows.reduce((s, r) => s + boxesOf(r.qty, nmPpb.get(nmByBc.get(r.barcode) ?? 0)), 0);
    return { submitRows, allocByBc, cellByBc, requestCount: groupKeys.size, totalShip, totalBoxes, shipByWh, boxesByWh, palletsByWh, totalPallets };
}

interface DraftMatrixViewProps {
    /** ID черновика (?draft=) — куда пишем результат ручной раскладки. */
    draftId: number;
    /** Справочник WB-складов (id→имя) для резолва имени ФФ-источника в предброни. */
    ffNameById: Map<number, string>;
    /** Черновик изменён редактором (автосейв степпера / ✕ / авто-синк с расчётом).
     *  Родитель обновляет свой стейт БЕЗ смены вкладки и БЕЗ полного self-heal
     *  (план редактора = точное состояние). */
    onDraftChanged?: (draft: AssemblyDraft) => void;
}

/** РЕДАКТОР черновика в виде матрицы SKU × WB-склады. Таблица показывает сам
 *  черновик (rows + 🅿️ предбронь единой суммой); степперы −/+ правят черновик
 *  напрямую с автосейвом; «⟳ Пересчитать от потребности» пишет живой расчёт
 *  (need-канал + новинки cold-start с гвардом пересорта) заменой по SKU:
 *  целые паллеты → строки, хвосты → предбронь. */
export default function DraftMatrixView({ draftId, ffNameById, onDraftChanged }: DraftMatrixViewProps) {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    const [stockNeed, setStockNeed] = useState<StockNeedResponse | null>(null);
    const [locByNm, setLocByNm] = useState<Map<number, LocalizationSkuRow>>(new Map());
    const [newcomerSet, setNewcomerSet] = useState<Set<number>>(new Set());
    // Канал новинок: авто-раздача из backend cold-start (allocations per nm) и
    // причины гварда пересорта (посев лежит на WB без продаж → авто-досев 0).
    const [newcomerAlloc, setNewcomerAlloc] = useState<Map<number, Record<string, number>>>(new Map());
    const [guardByNm, setGuardByNm] = useState<Map<number, string>>(new Map());
    // Текущее содержимое черновика (rows+prebook) — колонка «В черновике» и чистка per-SKU.
    const [draftRows, setDraftRowsState] = useState<AssemblyDraftRow[]>([]);
    const [draftPrebook, setDraftPrebook] = useState<AssemblyDraftRow[]>([]);
    const [removingNm, setRemovingNm] = useState<number | null>(null);
    // РУЧНЫЕ SKU (правлены степпером/✕; персистятся в distribution.manual_nms) —
    // авто-синк с расчётом их не трогает, пока юзер не вернёт SKU «в авто» (↺).
    const [manualNms, setManualNms] = useState<Set<number>>(new Set());
    const [nmPpb, setNmPpb] = useState<Map<number, number | null>>(new Map());
    const [nmBoxSize, setNmBoxSize] = useState<Map<number, string | null>>(new Map());
    const [palletOverrides, setPalletOverrides] = useState<Record<string, number>>({});
    const [geomReady, setGeomReady] = useState(false);

    const [distRows, setDistRows] = useState<AssemblyDraftRow[] | null>(null);
    const [prebookRows, setPrebookRows] = useState<AssemblyDraftRow[]>([]);
    const [editMode, setEditMode] = useState(false);
    const acceptanceCacheRef = useRef<{ sig: string; splitMap: AcceptanceSplitMap | null; summary: AcceptanceSummary | null; accByNm: Map<number, AcceptanceCheckPerItem> } | null>(null);
    const [acceptanceByNm, setAcceptanceByNm] = useState<Map<number, AcceptanceCheckPerItem>>(new Map());
    const [distComputing, setDistComputing] = useState(false);
    const [acceptanceNote, setAcceptanceNote] = useState<AcceptanceSummary | null>(null);
    const [writing, setWriting] = useState(false);

    const [sortKey, setSortKey] = useState<SortKey>('ship');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
    const [filterSubject, setFilterSubject] = useState('');
    const [filterBrand, setFilterBrand] = useState('');
    const [filterFf, setFilterFf] = useState('');
    const [hideEmpty, setHideEmpty] = useState(false);
    const toggleSort = useCallback((key: SortKey) => {
        setSortKey((prev) => {
            if (prev === key) { setSortDir((d) => (d === 'asc' ? 'desc' : 'asc')); return prev; }
            setSortDir(key === 'label' ? 'asc' : 'desc');
            return key;
        });
    }, []);
    const sortArrow = (key: SortKey): string => (sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '');
    const sortableTh: CSSProperties = { cursor: 'pointer', userSelect: 'none' };

    const showToast = useCallback((message: string, type: 'success' | 'error') => setToast({ message, type }), []);

    // ─── Загрузка потребности + справочников (источник = весь ФФ-сток) ──────
    useEffect(() => {
        const controller = new AbortController();
        (async () => {
            setLoading(true);
            setError(null);
            try {
                // Индекс локализации — то же 14-дневное окно, что analysis_days потребности.
                const locTo = new Date();
                const locFrom = new Date(locTo.getTime() - 13 * 86_400_000);
                const isoDay = (d: Date) => d.toISOString().slice(0, 10);
                const [need, cold, boxMult, palletOv, locSkus] = await Promise.all([
                    // Первичный источник экрана — НЕ глушим (иначе 500 покажет пустое «нечего
                    // отгружать» вместо ошибки). Отказ → reject Promise.all → error-стейт.
                    api.getStockNeed(14, 14, 'actual', true, true, 0) as Promise<StockNeedResponse>,
                    // Параметры новинок — те же, что настроены на экране «Потребность»
                    // (персистятся в localStorage), иначе два экрана считали бы новинки
                    // разными настройками (min-pack / % отгрузки / floor).
                    (() => {
                        let csp: { minPack?: number; shipPct?: number; floor?: number } = {};
                        try { csp = JSON.parse(localStorage.getItem('dds.coldStartParams') || '{}'); } catch { /* дефолты */ }
                        return api.getColdStartTable(14, csp.minPack ?? 5, csp.shipPct ?? 55, csp.floor ?? 50).catch(() => null);
                    })(),
                    api.getBoxMultiplicity().catch(() => null),
                    api.getPalletBoxesBySize().catch(() => ({} as Record<string, number>)),
                    api.getLocalizationSkus(isoDay(locFrom), isoDay(locTo)).catch(() => [] as LocalizationSkuRow[]),
                ]);
                if (controller.signal.aborted) return;
                setStockNeed(need);
                setLocByNm(new Map(locSkus.map((r) => [r.nm_id, r])));
                acceptanceCacheRef.current = null;

                const ncs = new Set<number>();
                for (const r of cold?.rows ?? []) if (r.is_newcomer) ncs.add(r.nm_id);
                setNewcomerSet(ncs);

                // Раздача новинок и гвард пересорта — из тех же cold-start строк.
                const nAlloc = new Map<number, Record<string, number>>();
                const guards = new Map<number, string>();
                for (const r of cold?.rows ?? []) {
                    if (r.oversort_guard) {
                        guards.set(r.nm_id, r.guard_reason || 'посев уже лежит на WB и не продаётся');
                        continue;
                    }
                    if (r.allocations && Object.keys(r.allocations).length > 0) nAlloc.set(r.nm_id, r.allocations);
                }
                setNewcomerAlloc(nAlloc);
                setGuardByNm(guards);

                const ppbMap = new Map<number, number | null>();
                const sizeMap = new Map<number, string | null>();
                for (const r of boxMult?.items ?? []) {
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
                    ppbMap.set(r.nm_id, ppb);
                    sizeMap.set(r.nm_id, boxSize);
                }
                setNmPpb(ppbMap);
                setNmBoxSize(sizeMap);
                setPalletOverrides(palletOv || {});
                setGeomReady(true);
            } catch (e) {
                if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка загрузки потребности');
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        })();
        return () => controller.abort();
    }, []);

    // Артикулы со свободным ФФ-остатком — источник раскладки черновика.
    const articles = useMemo<StockNeedArticle[]>(() => {
        const out: StockNeedArticle[] = [];
        for (const a of stockNeed?.articles ?? []) {
            const avail = Object.values(a.rf_stocks || {}).reduce((s, st) => s + Math.max(0, Number(st?.available) || 0), 0);
            if (avail > 0) out.push(a);
        }
        return out;
    }, [stockNeed]);

    // ─── Авто-раскладка: потребность × ФФ-сток → приёмка → коробы/паллеты ────
    const computeDistribution = useCallback(async (signal: AbortSignal) => {
        if (articles.length === 0) { setDistRows([]); setPrebookRows([]); setAcceptanceNote(null); return; }
        setDistComputing(true);
        try {
            const distInput: DraftDistInput = { articles, stockNeed, nmPpb, nmBoxSize, palletOverrides, newcomerSet, newcomerAlloc };
            const { skus: allSkus } = buildDraftSkus(distInput);
            const { shippable: autoSkus } = splitByKratnost(allSkus, (nm) => nmPpb.get(nm));
            const accSig = JSON.stringify(autoSkus.map((s) => [s.nm_id, s.barcode, s.target]));
            let splitMap: AcceptanceSplitMap | null = null;
            let summary: AcceptanceSummary | null = null;
            let accByNm = new Map<number, AcceptanceCheckPerItem>();
            const accCache = acceptanceCacheRef.current;
            if (accCache && accCache.sig === accSig) {
                splitMap = accCache.splitMap; summary = accCache.summary; accByNm = accCache.accByNm;
            } else if (autoSkus.length === 0) {
                splitMap = new Map();
                acceptanceCacheRef.current = { sig: accSig, splitMap, summary, accByNm };
            } else {
                try {
                    const resp = await api.checkWbAcceptance({
                        items: autoSkus.map((s) => ({ nm_id: s.nm_id, barcode: s.barcode, distribution: s.target })),
                    });
                    splitMap = new Map();
                    let moved = 0, dropped = 0, monoCount = 0, splitCount = 0;
                    for (const it of resp.items) {
                        const splits = it.splits?.length
                            ? it.splits.map((sp) => ({ package_type: sp.package_type, distribution: sp.distribution }))
                            : [{ package_type: it.package_type, distribution: it.distribution }];
                        splitMap.set(`${it.nm_id}::${it.barcode}`, splits);
                        accByNm.set(it.nm_id, it);
                        if (splits.some((s) => s.package_type === 'MONOPALLET')) monoCount++;
                        if (splits.length > 1) splitCount++;
                    }
                    for (const m of resp.moves ?? []) { if (m.to_warehouse) moved += m.quantity; else dropped += m.quantity; }
                    summary = { checked: true, failed: false, skuCount: resp.items.length, monoCount, splitCount, movedQty: moved, droppedQty: dropped, checkedAt: resp.checked_at ?? null };
                } catch {
                    summary = { checked: false, failed: true, skuCount: 0, monoCount: 0, splitCount: 0, movedQty: 0, droppedQty: 0, checkedAt: null };
                }
                acceptanceCacheRef.current = { sig: accSig, splitMap, summary, accByNm };
            }
            const effective = applyAcceptanceSplits(autoSkus, splitMap);
            const whole = finalizeDraftDistribution(effective, distInput, true, [], true);
            if (!signal.aborted) {
                setDistRows(whole.rows);
                setPrebookRows(whole.prebook);
                setAcceptanceNote(summary);
                setAcceptanceByNm(accByNm);
            }
        } catch (e) {
            if (!signal.aborted) { setDistRows([]); setPrebookRows([]); showToast(e instanceof Error ? e.message : 'Ошибка раскладки', 'error'); }
        } finally {
            if (!signal.aborted) setDistComputing(false);
        }
    }, [articles, stockNeed, nmPpb, nmBoxSize, palletOverrides, newcomerSet, newcomerAlloc, showToast]);

    // ─── Черновик: ИСТОЧНИК таблицы (матрица = редактор черновика) ──────────
    const draftDistRef = useRef<AssemblyDraft['distribution'] | null>(null);
    const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const refreshDraftInfo = useCallback(async () => {
        try {
            const d = await api.getAssemblyDraft(draftId);
            draftDistRef.current = d.distribution ?? null;
            setDraftRowsState(d.distribution?.rows ?? []);
            setDraftPrebook(d.distribution?.prebook ?? []);
            setManualNms(new Set(d.distribution?.manual_nms ?? []));
        } catch {
            // Черновик мог быть удалён/пересоздан — таблица остаётся пустой.
        }
    }, [draftId]);
    useEffect(() => { refreshDraftInfo(); }, [refreshDraftInfo]);

    /** Автосейв правок степперов. Свойства:
     *  - дебаунс 1.2с, PUT-ы СЕРИАЛИЗОВАНЫ (новый не стартует, пока летит текущий);
     *  - база PUT — СВЕЖИЙ GET черновика (не затираем чужие изменения:
     *    handed_units, правки другой вкладки), rows/prebook — локальные;
     *  - списки складов пересчитываются из отправляемых строк;
     *  - ответ сервера применяется ТОЛЬКО если поколение правок не изменилось
     *    (иначе стейл-ответ откатил бы более свежие клики);
     *  - серверный гейт мог вычесть «уже едущее» — про это говорит тост. */
    const editGenRef = useRef(0);
    const saveInFlightRef = useRef(false);
    const pendingSaveRef = useRef<{ rows: AssemblyDraftRow[]; prebook: AssemblyDraftRow[]; manualNms: number[] } | null>(null);
    const sumUnits = (rows: AssemblyDraftRow[]) => rows.reduce((s, r) => s + Object.values(r.tgt || {}).reduce((a, v) => a + (v || 0), 0), 0);
    const flushDraftSave = useCallback(async (): Promise<void> => {
        if (saveInFlightRef.current) return; // доедет из finally текущего PUT
        const pending = pendingSaveRef.current;
        if (!pending) return;
        pendingSaveRef.current = null;
        saveInFlightRef.current = true;
        const gen = editGenRef.current;
        try {
            let base = draftDistRef.current;
            try {
                const fresh = await api.getAssemblyDraft(draftId);
                base = fresh.distribution ?? base;
            } catch { /* сеть мигнула — шлём от последней известной базы */ }
            if (!base) {
                showToast('Черновик недоступен — правка НЕ сохранена', 'error');
                return;
            }
            const next = buildWriteDistribution({ rows: [], prebook: [] }, pending.rows, pending.prebook);
            const sent = sumUnits(pending.rows) + sumUnits(pending.prebook);
            // Событие истории: правка степпером — значимое изменение (снапшот для
            // отката). Дебаунс+сериализация PUT-ов не дают спама на серию кликов.
            const d = await api.updateAssemblyDraft(draftId, {
                distribution: { ...base, ...next, manual_nms: pending.manualNms },
                event: { event_type: 'MATRIX_EDIT', summary: `Степпер: план ${formatNumber(sent, 0)} шт` },
            });
            draftDistRef.current = d.distribution ?? null;
            const got = sumUnits(d.distribution?.rows ?? []) + sumUnits(d.distribution?.prebook ?? []);
            if (got < sent) {
                showToast(`⚠ Сервер вычел уже едущее в заявках: −${formatNumber(sent - got, 0)} шт (защита от дублей)`, 'success');
            }
            if (editGenRef.current === gen) {
                // Новых правок не было — ответ сервера канон.
                setDraftRowsState(d.distribution?.rows ?? []);
                setDraftPrebook(d.distribution?.prebook ?? []);
                setManualNms(new Set(d.distribution?.manual_nms ?? pending.manualNms));
                onDraftChanged?.(d);
            }
            // Были новые правки → базу обновили, стейт не трогаем; pending уже ждёт.
        } catch (err) {
            showToast(err instanceof Error ? err.message : 'Не удалось сохранить черновик', 'error');
        } finally {
            saveInFlightRef.current = false;
            if (pendingSaveRef.current) void flushDraftSave();
        }
    }, [draftId, onDraftChanged, showToast]);
    const scheduleDraftSave = useCallback((rows: AssemblyDraftRow[], prebook: AssemblyDraftRow[], manual: number[]) => {
        editGenRef.current += 1;
        pendingSaveRef.current = { rows, prebook, manualNms: manual };
        if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
        saveTimerRef.current = setTimeout(() => {
            saveTimerRef.current = null;
            void flushDraftSave();
        }, 1200);
    }, [flushDraftSave]);
    // Анмаунт: незаписанный дебаунс ФЛАШИТСЯ (fire-and-forget), а не выбрасывается.
    useEffect(() => () => {
        if (saveTimerRef.current) {
            clearTimeout(saveTimerRef.current);
            saveTimerRef.current = null;
        }
        if (pendingSaveRef.current) void flushDraftSave();
    }, [flushDraftSave]);

    /** SKU с замороженными draft-юнитами (set_unit_items вырезал поток из rows в
     *  снимок): правка степпером пересобрала бы rows и _reconcile_handed_with_rows
     *  на сейве вырезал бы снимок — молчаливая потеря. Такие SKU правим только на
     *  странице склада. */
    const [handedNms, setHandedNms] = useState<Set<number>>(new Set());
    useEffect(() => {
        const s = new Set<number>();
        const units = (draftDistRef.current?.handed_units ?? []) as { status?: string; items?: { nm_id: number }[] }[];
        for (const u of units) if (u.status === 'draft') for (const it of u.items ?? []) if (it.nm_id) s.add(it.nm_id);
        setHandedNms(s);
    }, [draftRows, draftPrebook]);

    const draftByNm = useMemo(() => {
        const m = new Map<number, { rows: number; prebook: number; vendor: string }>();
        const sumTgt = (r: AssemblyDraftRow) => Object.values(r.tgt || {}).reduce((s, v) => s + (v || 0), 0);
        const acc = (list: AssemblyDraftRow[], key: 'rows' | 'prebook') => {
            for (const r of list) {
                if (!r.nm_id) continue;
                const cur = m.get(r.nm_id) ?? { rows: 0, prebook: 0, vendor: r.vendor_code || String(r.nm_id) };
                cur[key] += sumTgt(r);
                m.set(r.nm_id, cur);
            }
        };
        acc(draftRows, 'rows');
        acc(draftPrebook, 'prebook');
        return m;
    }, [draftRows, draftPrebook]);
    /** SKU черновика без свободного остатка на ФФ — их нет среди articles, но видеть и чистить их нужно. */
    const draftOnlyNms = useMemo(() => {
        const inArticles = new Set(articles.map((a) => a.nm_id));
        return [...draftByNm.entries()].filter(([nm, v]) => !inArticles.has(nm) && v.rows + v.prebook > 0);
    }, [draftByNm, articles]);

    const removeFromDraft = useCallback(async (nm: number, label: string) => {
        if (!window.confirm(`Убрать «${label}» из черновика? Удалятся строки и предбронь этого SKU.`)) return;
        setRemovingNm(nm);
        try {
            // Висящий автосейв дожимаем ДО удаления — иначе стейл-PUT воскресил бы SKU.
            if (saveTimerRef.current) {
                clearTimeout(saveTimerRef.current);
                saveTimerRef.current = null;
            }
            await flushDraftSave();
            const removed = await api.removeAssemblyDraftRows(draftId, [nm]);
            // ✕ — ручное решение «не отправлять»: помечаем SKU ручным, иначе
            // авто-синк с расчётом вернул бы его на следующем заходе.
            const withManual = [...new Set([...(removed.distribution?.manual_nms ?? []), nm])];
            const d = await api.updateAssemblyDraft(draftId, { distribution: { ...removed.distribution, manual_nms: withManual } });
            draftDistRef.current = d.distribution ?? null;
            setDraftRowsState(d.distribution?.rows ?? []);
            setDraftPrebook(d.distribution?.prebook ?? []);
            setManualNms(new Set(withManual));
            showToast(`«${label}» убран из черновика (✋ ручное решение — авто-синк не вернёт)`, 'success');
            onDraftChanged?.(d);
        } catch (err) {
            showToast(err instanceof Error ? err.message : 'Не удалось убрать из черновика', 'error');
        } finally {
            setRemovingNm(null);
        }
    }, [draftId, flushDraftSave, showToast, onDraftChanged]);

    useEffect(() => {
        if (!geomReady) return;
        const controller = new AbortController();
        computeDistribution(controller.signal);
        return () => controller.abort();
    }, [geomReady, computeDistribution]);

    // ─── Предбронь РАСЧЁТА: атомарные направления (per ФФ×WB, allocatePairs) —
    //     хвосты < паллеты, которые «Пересчитать» положит в prebook черновика. ──
    const effPrebook = useMemo<AssemblyDraftRow[]>(() => {
        const out: AssemblyDraftRow[] = [];
        for (const r of prebookRows) {
            const pkg = r.package_type ?? 'BOX';
            for (const [pair, q] of allocatePairs(r.src, r.tgt)) {
                if ((q || 0) <= 0) continue;
                const [ff, wb] = pair.split('::');
                out.push({ nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [ff]: q }, tgt: { [wb]: q }, package_type: pkg });
            }
        }
        return out;
    }, [prebookRows]);
    const shipRows = useMemo(() => distRows ?? [], [distRows]);
    const prebookUnits = useMemo(() => effPrebook.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0), [effPrebook]);

    const nmByBc = useMemo(() => {
        const m = new Map<string, number>();
        for (const a of articles) m.set(a.barcode, a.nm_id);
        // Черновик может держать SKU без свободного остатка (их нет в articles) —
        // их баркоды тоже нужны агрегатам (кратность, коробы).
        for (const r of [...draftRows, ...draftPrebook]) if (r.barcode && r.nm_id) m.set(r.barcode, r.nm_id);
        return m;
    }, [articles, draftRows, draftPrebook]);
    const availByBc = useMemo(() => {
        const m = new Map<string, number>();
        for (const a of articles) m.set(a.barcode, Object.values(a.rf_stocks || {}).reduce((s, st) => s + Math.max(0, Math.floor(Number(st?.available) || 0)), 0));
        return m;
    }, [articles]);

    // Расчёт от потребности (фоновый). commit = что поедет строками (для confirm);
    // calcAgg = строки+предбронь расчёта ВМЕСТЕ — тот же слой, что и план черновика
    // (rows+prebook), иначе колонка «Расчёт» врала бы «расходится» сразу после записи.
    const commit = useMemo(() => buildDistAgg(shipRows, nmByBc, nmPpb, nmBoxSize, palletOverrides), [shipRows, nmByBc, nmPpb, nmBoxSize, palletOverrides]);
    const calcAgg = useMemo(() => buildDistAgg([...shipRows, ...effPrebook], nmByBc, nmPpb, nmBoxSize, palletOverrides), [shipRows, effPrebook, nmByBc, nmPpb, nmBoxSize, palletOverrides]);
    const submitRows = commit.submitRows;

    // ─── ТАБЛИЦА = ЧЕРНОВИК: строки + предбронь единой суммой ────────────────
    const draftAll = useMemo(() => [...draftRows, ...draftPrebook], [draftRows, draftPrebook]);
    // Draft-only SKU (в черновике, но без свободного остатка на ФФ) — полноценные
    // строки таблицы: метрики потребности прочерком, план виден, ✕ работает.
    const draftOnlyArticles = useMemo<StockNeedArticle[]>(() => draftOnlyNms.map(([nm, v]) => {
        const row = draftAll.find((r) => r.nm_id === nm);
        return {
            nm_id: nm,
            vendor_code: v.vendor,
            barcode: row?.barcode || '',
            brand: '',
            subject: '',
            total_need: 0,
            revenue_30d: 0,
            rf_stocks: {},
            in_assembly: 0,
            in_transit: 0,
            in_transit_date: null,
            can_send: 0,
            deficit: 0,
            stocks_wb: 0,
        } as StockNeedArticle;
    }), [draftOnlyNms, draftAll]);

    const draftAgg = useMemo(() => buildDistAgg(draftAll, nmByBc, nmPpb, nmBoxSize, palletOverrides), [draftAll, nmByBc, nmPpb, nmBoxSize, palletOverrides]);
    /** Предбронь-доля per (barcode, wh) — для тултипа «из них 🅿️» (числа единые). */
    const prebookCellByBc = useMemo(() => {
        const m = new Map<string, Map<string, number>>();
        for (const r of draftPrebook) {
            if (!r.barcode) continue;
            const cell = m.get(r.barcode) ?? new Map<string, number>();
            for (const [wh, q] of Object.entries(r.tgt || {})) if ((q || 0) > 0) cell.set(wh, (cell.get(wh) || 0) + (q || 0));
            m.set(r.barcode, cell);
        }
        return m;
    }, [draftPrebook]);
    const draftPrebookUnits = useMemo(
        () => draftPrebook.reduce((s, r) => s + Object.values(r.tgt || {}).reduce((a, v) => a + (v || 0), 0), 0),
        [draftPrebook],
    );

    // ─── ФФ-срез (лупа «Склад забора»): показываем ДОЛЮ выбранного ФФ ────────
    // Чистое отображение черновика, запись не трогает. В ручном режиме срез
    // ПРИОСТАНОВЛЕН (степперы правят ПОЛНЫЙ черновик), видимость строк — по срезу.
    const ffFilterId = filterFf ? Number(filterFf) : null;
    const ffSliceActive = ffFilterId != null && !editMode;
    const slicedDraft = useMemo(
        () => (ffFilterId != null ? sliceRowsByFf(draftAll, ffFilterId) : draftAll),
        [ffFilterId, draftAll],
    );
    const sliceAgg = useMemo(
        () => (ffFilterId != null ? buildDistAgg(slicedDraft, nmByBc, nmPpb, nmBoxSize, palletOverrides) : draftAgg),
        [ffFilterId, slicedDraft, nmByBc, nmPpb, nmBoxSize, palletOverrides, draftAgg],
    );
    /** Агрегат ЗНАЧЕНИЙ таблицы: доля ФФ при активном срезе, иначе весь черновик. */
    const viewAgg = ffSliceActive ? sliceAgg : draftAgg;
    /** Наличие строки: остаток ИМЕННО выбранного ФФ при срезе, иначе Σ всех ФФ. */
    const availOf = useCallback((a: StockNeedArticle): number => (
        ffSliceActive
            ? Math.max(0, Math.floor(Number(a.rf_stocks?.[ffFilterId!]?.available) || 0))
            : (availByBc.get(a.barcode) ?? 0)
    ), [ffSliceActive, ffFilterId, availByBc]);

    const enrichMap = useMemo(() => enrichArticles(articles, stockNeed, newcomerSet), [articles, stockNeed, newcomerSet]);

    const wbCols = useMemo(() => {
        const distByWh = new Map<string, string>();
        for (const w of stockNeed?.warehouses ?? []) if (w.name) distByWh.set(w.name, w.district_key || 'unknown');
        const names = new Set<string>();
        for (const r of draftAgg.submitRows) names.add(r.wb_warehouse_name); // черновик
        for (const r of commit.submitRows) names.add(r.wb_warehouse_name); // расчёт (чтобы правкой можно было добрать)
        for (const e of enrichMap.values()) for (const [wh, c] of Object.entries(e.byWh)) if (c.need > 0) names.add(wh);
        const arr = [...names].map((name) => ({ name, district: distByWh.get(name) || 'unknown' }));
        arr.sort((a, b) => {
            const ra = districtRank(a.district), rb = districtRank(b.district);
            return ra !== rb ? ra - rb : a.name.localeCompare(b.name, 'ru');
        });
        return arr;
    }, [draftAgg, commit, enrichMap, stockNeed]);

    const districtGroups = useMemo(() => {
        const groups: { label: string; color: string; count: number }[] = [];
        for (const c of wbCols) {
            const label = DISTRICT_LABELS[c.district] || 'Прочие';
            const color = DISTRICT_COLORS[c.district] || 'var(--color-muted)';
            const last = groups[groups.length - 1];
            if (last && last.label === label) last.count++;
            else groups.push({ label, color, count: 1 });
        }
        return groups;
    }, [wbCols]);

    const onHoldQty = useMemo(
        () => articles.reduce((s, a) => s + Math.max(0, (availByBc.get(a.barcode) ?? 0) - (draftAgg.allocByBc.get(a.barcode) ?? 0)), 0),
        [articles, availByBc, draftAgg],
    );

    const noKratnostArticles = useMemo(() => {
        const out: { nm_id: number; vendor: string; qty: number }[] = [];
        for (const a of articles) {
            if ((nmPpb.get(a.nm_id) || 0) > 0) continue;
            const qty = availByBc.get(a.barcode) ?? 0;
            if (qty > 0) out.push({ nm_id: a.nm_id, vendor: a.vendor_code || String(a.nm_id), qty });
        }
        // Draft-only SKU без кратности: их план есть в черновике, но «Мест»/паллеты
        // для них не считаются — тоже показываем в баннере (иначе потеря молчаливая).
        for (const [nm, v] of draftByNm) {
            if ((nmPpb.get(nm) || 0) > 0) continue;
            if (v.rows + v.prebook <= 0) continue;
            if (out.some((x) => x.nm_id === nm) || articles.some((a) => a.nm_id === nm)) continue;
            out.push({ nm_id: nm, vendor: v.vendor, qty: v.rows + v.prebook });
        }
        return out.sort((x, y) => y.qty - x.qty);
    }, [articles, nmPpb, availByBc, draftByNm]);

    const markFor = useCallback((nm: number, wh: string, shipPkg: PackageType | null): CellMark | null => {
        const flags = acceptanceByNm.get(nm)?.availability?.[wh];
        if (!flags) return null;
        if (!flags.can_box && !flags.can_monopallet && !flags.can_supersafe) return { noLimit: false, closed: true };
        const canOf = (t: 'box' | 'mono' | 'super') => (t === 'box' ? flags.can_box : t === 'mono' ? flags.can_monopallet : flags.can_supersafe);
        const wanted: 'box' | 'mono' | 'super' | null = shipPkg === 'MONOPALLET' ? 'mono' : shipPkg === 'SUPERSAFE' ? 'super' : shipPkg === 'BOX' ? 'box' : null;
        const type = wanted && canOf(wanted) ? wanted : flags.can_box ? 'box' : flags.can_monopallet ? 'mono' : 'super';
        const meta = type === 'box' ? flags.box_meta : type === 'mono' ? flags.mono_meta : flags.super_meta;
        return { noLimit: ((meta?.free_days_14 ?? 0) + (meta?.paid_days_14 ?? 0)) <= 0 };
    }, [acceptanceByNm]);

    // Правка ячейки = правка ЧЕРНОВИКА: BOX-строки и предбронь SKU сливаются в
    // одну строку, tgt[склада] двигается на ±короб, автосейв дебаунсом. Кап —
    // свободный ФФ-остаток SKU (превышение = клик игнорируется с тостом).
    const editDraftCell = useCallback((barcode: string, nm: number, wh: string, delta: number) => {
        const ppb = nmPpb.get(nm) || 0;
        if (ppb <= 0) return;
        if (handedNms.has(nm)) {
            showToast('Раскладка этого SKU частично заморожена юнитами на странице склада — правь её там', 'error');
            return;
        }
        // Для draft-only SKU (нет свободного остатка) article-заглушка позволяет
        // ДЕКРЕМЕНТ (кап держит только рост плана).
        const article = articles.find((a) => a.nm_id === nm) ?? draftOnlyArticles.find((a) => a.nm_id === nm);
        if (!article) { showToast('SKU не найден среди остатков и черновика', 'error'); return; }
        const skuPrebook = draftPrebook.filter((r) => r.nm_id === nm);
        const out = applyDraftCellEdit(
            draftRows.filter((r) => r.nm_id === nm),
            skuPrebook,
            article, wh, delta, ppb,
        );
        if (!out) { if (delta > 0) showToast('Не хватает свободного остатка на ФФ', 'error'); return; }
        // Моно-предбронь при ручной правке сливается в КОРОБНЫЙ план — если склад
        // принимает только монопаллеты, приёмка на создании заявки не пропустит.
        if (skuPrebook.some((r) => r.package_type === 'MONOPALLET')) {
            const flags = acceptanceByNm.get(nm)?.availability?.[wh];
            if (flags && !flags.can_box) {
                showToast(`⚠ «${wh}» по последней проверке принимает только монопаллеты — коробный план может не пройти приёмку`, 'error');
            }
        }
        const nextRows = [...draftRows.filter((r) => r.nm_id !== nm), ...out.rows];
        const nextPrebook = [...draftPrebook.filter((r) => r.nm_id !== nm), ...out.prebook];
        const nextManual = manualNms.has(nm) ? manualNms : new Set([...manualNms, nm]);
        setDraftRowsState(nextRows);
        setDraftPrebook(nextPrebook);
        if (nextManual !== manualNms) setManualNms(nextManual);
        scheduleDraftSave(nextRows, nextPrebook, [...nextManual]);
    }, [nmPpb, articles, draftOnlyArticles, handedNms, manualNms, draftRows, draftPrebook, acceptanceByNm, scheduleDraftSave, showToast]);

    // Вход в ручной режим = просто показать степперы поверх авто-раскладки. НЕ сеем пины:
    // нетронутые строки остаются авто, «вручную» помечается лишь та, где юзер кликнул −/+.
    const enterManual = useCallback(() => setEditMode(true), []);

    const sortValue = useCallback((a: StockNeedArticle, key: SortKey): number | string => {
        const nm = a.nm_id;
        const e = enrichMap.get(nm);
        const ship = viewAgg.allocByBc.get(a.barcode) ?? 0;
        const avail = availOf(a);
        switch (key) {
            case 'label': return (a.vendor_code || a.barcode).toLowerCase();
            case 'avail': return avail;
            case 'inAssembly': return e?.inAssembly ?? 0;
            case 'stocksWb': return e?.stocksWb ?? 0;
            case 'need': return Number(a.total_need) || 0;
            case 'revenue': return Number(a.revenue_30d) || 0;
            case 'loc': return locByNm.has(nm) ? Number(locByNm.get(nm)!.loc_pct) || 0 : -1;
            case 'ship': return ship;
            case 'boxes': return boxesOf(ship, nmPpb.get(nm));
            case 'stays': return Math.max(0, avail - ship);
            default: return viewAgg.cellByBc.get(a.barcode)?.get(key.slice(3))?.qty ?? 0;
        }
    }, [enrichMap, viewAgg, availOf, nmPpb, locByNm, calcAgg]);

    const sortedRows = useMemo(() => {
        const rows = [...articles, ...draftOnlyArticles];
        const dir = sortDir === 'asc' ? 1 : -1;
        rows.sort((a, b) => {
            const va = sortValue(a, sortKey), vb = sortValue(b, sortKey);
            const cmp = typeof va === 'string' || typeof vb === 'string' ? String(va).localeCompare(String(vb), 'ru') : va - vb;
            if (cmp !== 0) return cmp * dir;
            return (a.vendor_code || a.barcode).localeCompare(b.vendor_code || b.barcode, 'ru');
        });
        return rows;
    }, [articles, draftOnlyArticles, sortKey, sortDir, sortValue]);

    // ─── Фильтр таблицы: предмет / бренд / скрыть нераспределённые ───────────
    // Лупа по ОТОБРАЖЕНИЮ (не по расчёту) — раскладка/паллеты/KPI считаются по всему
    // ФФ-стоку, фильтр лишь сужает видимые строки (черновик масштаба = сотни артикулов).
    // ФФ-фильтр вдобавок СРЕЗАЕТ значения до доли этого склада (viewAgg/availOf выше).
    const subjectOptions = useMemo(
        () => [...new Set(articles.map((a) => a.subject).filter(Boolean))].sort((x, y) => x.localeCompare(y, 'ru')),
        [articles],
    );
    const brandOptions = useMemo(
        () => [...new Set(articles.map((a) => a.brand).filter(Boolean))].sort((x, y) => x.localeCompare(y, 'ru')),
        [articles],
    );
    // Склады забора (ФФ) — по ключам rf_stocks артикулов, имя из справочника складов.
    const ffOptions = useMemo(() => {
        const ids = new Set<number>();
        for (const a of articles) for (const [ff, st] of Object.entries(a.rf_stocks || {})) {
            if ((Number(st?.available) || 0) > 0) ids.add(Number(ff));
        }
        return [...ids].map((id) => ({ id, name: ffNameById.get(id) || `ФФ ${id}` })).sort((x, y) => x.name.localeCompare(y.name, 'ru'));
    }, [articles, ffNameById]);
    const visibleRows = useMemo(
        () => sortedRows.filter((a) => {
            if (filterSubject && a.subject !== filterSubject) return false;
            if (filterBrand && a.brand !== filterBrand) return false;
            if (ffFilterId != null) {
                // Виден, если на этом ФФ есть остаток ИЛИ раскладка что-то с него забирает
                // (sliceAgg, не viewAgg — набор строк стабилен при входе в ручной режим).
                const availFf = Number(a.rf_stocks?.[ffFilterId]?.available) || 0;
                const shipFf = sliceAgg.allocByBc.get(a.barcode) ?? 0;
                if (availFf <= 0 && shipFf <= 0) return false;
            }
            if (hideEmpty && (viewAgg.allocByBc.get(a.barcode) ?? 0) <= 0) return false;
            return true;
        }),
        [sortedRows, filterSubject, filterBrand, ffFilterId, hideEmpty, sliceAgg, viewAgg],
    );

    // Итоги колонок («Сдаём» / футер) — по ВИДИМЫМ строкам, чтобы суммы под складами
    // соответствовали фильтру; при активном ФФ-срезе — по срезанным строкам (доля ФФ).
    // Без фильтра === полная матрица (переиспользуем `viewAgg`).
    const isFiltered = !!(filterSubject || filterBrand || filterFf || hideEmpty);
    const visibleBarcodes = useMemo(() => new Set(visibleRows.map((a) => a.barcode)), [visibleRows]);
    const matrixView = useMemo(() => {
        if (!isFiltered) return viewAgg;
        const base = ffSliceActive ? slicedDraft : draftAll;
        return buildDistAgg(base.filter((r) => visibleBarcodes.has(r.barcode)), nmByBc, nmPpb, nmBoxSize, palletOverrides);
    }, [isFiltered, viewAgg, ffSliceActive, slicedDraft, draftAll, visibleBarcodes, nmByBc, nmPpb, nmBoxSize, palletOverrides]);
    const matrixViewOnHold = useMemo(
        () => visibleRows.reduce((s, a) => s + Math.max(0, availOf(a) - (matrixView.allocByBc.get(a.barcode) ?? 0)), 0),
        [visibleRows, availOf, matrixView],
    );

    // «Записать в черновик» — ЗАМЕНА по SKU (не сложение): строки черновика по SKU, которые
    // разложила матрица, заменяются её результатом (целые паллеты → rows, под-паллетные хвосты
    /** СИНК плана с живым расчётом (авто-режим черновика): авто-SKU приводится к
     *  расчёту (замена по nm; целые паллеты → строки, хвосты → предбронь),
     *  guarded-новинки вычищаются, РУЧНЫЕ SKU (✋) не тронуты. Запускается сам
     *  один раз при заходе (trigger='auto', молчит без дельты) и кнопкой
     *  «⟳ Обновить сейчас». Confirm не нужен: ручные решения защищены флагом. */
    const runAutoSync = useCallback(async (trigger: 'auto' | 'manual') => {
        if (writing || distRows === null) return;
        setWriting(true);
        try {
            // Висящий автосейв дожимаем ДО синка — иначе стейл-PUT затёр бы запись.
            if (saveTimerRef.current) {
                clearTimeout(saveTimerRef.current);
                saveTimerRef.current = null;
            }
            await flushDraftSave();
            const cur = await api.getAssemblyDraft(draftId);
            const dist = cur.distribution;
            const curManual = new Set<number>(dist?.manual_nms ?? []);
            const guarded = new Set<number>(guardByNm.keys());
            const plan = buildAutoSyncPlan(dist ?? {}, shipRows, effPrebook, guarded, curManual);
            if (!plan) {
                if (trigger === 'manual') showToast('План уже соответствует расчёту', 'success');
                return;
            }
            const before = sumUnits(dist?.rows ?? []) + sumUnits(dist?.prebook ?? []);
            const after = sumUnits(plan.rows) + sumUnits(plan.prebook);
            const d = await api.updateAssemblyDraft(draftId, {
                distribution: { ...dist, ...plan },
                event: {
                    event_type: 'AUTO_SYNC',
                    summary: `Авто-синк с расчётом: ${formatNumber(before, 0)} → ${formatNumber(after, 0)} шт` +
                        (curManual.size > 0 ? ` (✋ ручных не тронуто: ${curManual.size})` : ''),
                },
            });
            draftDistRef.current = d.distribution ?? null;
            setDraftRowsState(d.distribution?.rows ?? []);
            setDraftPrebook(d.distribution?.prebook ?? []);
            setManualNms(new Set(d.distribution?.manual_nms ?? []));
            showToast(`⟳ План синхронизирован с расчётом: ${formatNumber(before, 0)} → ${formatNumber(after, 0)} шт${curManual.size > 0 ? ` · ✋ ручных не тронуто: ${curManual.size}` : ''}`, 'success');
            onDraftChanged?.(d);
        } catch (e) {
            showToast(e instanceof Error ? e.message : 'Ошибка синка с расчётом', 'error');
        } finally {
            setWriting(false);
        }
    }, [writing, distRows, draftId, guardByNm, shipRows, effPrebook, flushDraftSave, showToast, onDraftChanged]);

    // Авто-запуск синка: один раз за заход, когда готовы И расчёт, И черновик.
    const autoSyncDoneRef = useRef(false);
    useEffect(() => {
        if (autoSyncDoneRef.current || distRows === null || draftDistRef.current === null) return;
        autoSyncDoneRef.current = true;
        void runAutoSync('auto');
    }, [distRows, draftRows, runAutoSync]);

    /** ↺ Вернуть SKU в авто: снять ✋ и сразу синкануть его к расчёту. */
    const returnToAuto = useCallback(async (nm: number) => {
        try {
            const cur = await api.getAssemblyDraft(draftId);
            const without = (cur.distribution?.manual_nms ?? []).filter((x) => x !== nm);
            const d = await api.updateAssemblyDraft(draftId, { distribution: { ...cur.distribution, manual_nms: without } });
            draftDistRef.current = d.distribution ?? null;
            setManualNms(new Set(without));
            await runAutoSync('manual');
        } catch (e) {
            showToast(e instanceof Error ? e.message : 'Не удалось вернуть в авто', 'error');
        }
    }, [draftId, runAutoSync, showToast]);

    // ─── States ────────────────────────────────────────────────────────────
    if (loading) {
        return <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>Загрузка потребности и остатков ФФ…</div>;
    }
    if (error) {
        return (
            <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>
                <div style={{ color: 'var(--color-danger)', marginBottom: 16 }}>{error}</div>
                <button className="btn btn-secondary" onClick={() => window.location.reload()}>Повторить</button>
            </div>
        );
    }
    if (articles.length === 0 && draftAll.length === 0) {
        return <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>Черновик пуст, и свободного остатка на ФФ нет.</div>;
    }

    // Спиннер — только на ПЕРВИЧНом расчёте (distRows===null). Пересчёт при клике степпера
    // держит таблицу смонтированной (иначе мигает и сбрасывается горизонтальный скролл).
    const computing = distRows === null;

    return (
        <div className="animate-in">
            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}

            <AcceptanceBanner summary={acceptanceNote} />

            {noKratnostArticles.length > 0 && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
                        <span style={{ fontWeight: 700, color: 'var(--color-warning)' }}>🧩 Без кратности короба — {formatNumber(noKratnostArticles.length, 0)} арт.</span>
                        <span style={{ fontSize: 12, color: 'var(--color-muted)' }}>
                            кратность короба не задана → отгружать нечем (россыпь запрещена), остаются на ФФ; укажите кратность на вкладке «Кратность»
                        </span>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                        {noKratnostArticles.slice(0, 40).map((a) => (
                            <span key={a.nm_id} title={`nm ${a.nm_id} · остаток на ФФ: ${formatNumber(a.qty, 0)} шт`}
                                style={{ fontSize: 11, padding: '1px 7px', borderRadius: 5, background: 'rgba(255,159,10,0.14)', color: 'var(--color-warning)' }}>
                                {a.vendor} · {formatNumber(a.qty, 0)} шт
                            </span>
                        ))}
                        {noKratnostArticles.length > 40 && (
                            <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>…ещё {formatNumber(noKratnostArticles.length - 40, 0)}</span>
                        )}
                    </div>
                </div>
            )}

            <div className="glass-card" style={{ padding: 16, marginBottom: 16, display: 'flex', gap: 28, flexWrap: 'wrap', alignItems: 'center', fontSize: 14 }}>
                <span>В черновике: <b style={{ fontSize: 18 }}>{formatNumber(draftAgg.totalShip, 0)}</b> шт</span>
                <span>📦 Коробов: <b style={{ fontSize: 18 }}>{formatNumber(draftAgg.totalBoxes, 0)}</b></span>
                <span>🚚 Паллет: <b style={{ fontSize: 18 }}>{formatNumber(draftAgg.totalPallets, 0)}</b></span>
                {draftPrebookUnits > 0 && (
                    <span style={{ color: 'var(--color-muted)' }} title="Часть плана черновика, лежащая в предброни (хвосты < паллеты). Входит в общую сумму — здесь всё единым числом.">
                        в т.ч. 🅿️ {formatNumber(draftPrebookUnits, 0)} шт
                    </span>
                )}
                <span style={{ color: 'var(--color-muted)' }}>На хранение (ФФ): <b style={{ color: 'var(--color-text)' }}>{formatNumber(onHoldQty, 0)}</b> шт</span>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 12, alignItems: 'center' }}>
                    <span style={{ fontSize: 12, color: 'var(--color-muted)' }} title="План сам синхронизируется с живым расчётом при заходе (need-канал + новинки cold-start с гвардом пересорта): целые паллеты → строки, хвосты → предбронь. ✋ ручные SKU (правленные степпером/✕) авто-синк не трогает — вернуть SKU в авто можно кнопкой ↺ на его строке.">
                        {computing ? 'расчёт от потребности…' : `авто-синк с расчётом включён${manualNms.size > 0 ? ` · ✋ ручных SKU: ${formatNumber(manualNms.size, 0)}` : ''}`}
                    </span>
                    <button className="btn btn-primary" onClick={() => runAutoSync('manual')} disabled={writing || computing}>
                        {writing ? 'Синк…' : '⟳ Обновить сейчас'}
                    </button>
                </div>
            </div>

            {(<>
                <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
                        <button className={`btn btn-sm ${editMode ? 'btn-primary' : 'btn-secondary'}`} onClick={editMode ? () => setEditMode(false) : enterManual}>
                            {editMode ? '✓ Готово с правкой' : '✏️ Править черновик'}
                        </button>
                        {editMode && (
                            <span style={{ fontSize: 12, color: 'var(--color-warning)' }}>
                                Правь короба (−/+) — изменения сразу сохраняются в черновик (строки и предбронь SKU сливаются в точный план). Метки приёмки (⛔ закрыт и др.) — из последнего расчёта потребности.
                            </span>
                        )}
                    </div>
                    <div style={{ color: 'var(--color-muted)', fontSize: 13 }}>
                        Таблица показывает <b style={{ color: 'var(--color-text)' }}>черновик</b>: строки и 🅿️ предбронь каждого SKU единой суммой (ячейки с предбронью подсвечены, доля — в подсказке «Σ отпр.»).
                        Правки степперами сохраняются в черновик сразу; ✕ убирает SKU целиком.
                        План <b style={{ color: 'var(--color-text)' }}>сам синхронизируется</b> с живым расчётом при заходе (need-канал + новинки cold-start с гвардом пересорта ⚠): целые паллеты → строки, хвосты → предбронь. SKU, правленный руками, помечается ✋ и авто-синком не трогается (↺ на строке возвращает в авто); «⟳ Обновить сейчас» — форс-синк без перезахода.
                        Колонки складов: 🏬 остаток на WB · 🚚 в сборке/в пути · потребность. Метрики SKU за 14 дней: <b style={{ color: 'var(--color-text)' }}>Потр. 14д</b> · <b style={{ color: 'var(--color-text)' }}>₽ 14д</b> · <b style={{ color: 'var(--color-text)' }}>Лок %</b> (цвет — статус КТР).
                        Колонка <b style={{ color: 'var(--color-text)' }}>«Расчёт»</b> — что предлагает живой расчёт (для сравнения с планом).
                    </div>
                    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--color-border)' }}>
                        <span style={{ fontSize: 13, color: 'var(--color-muted)' }}>🔎 Фильтр:</span>
                        <select className="form-input" style={{ maxWidth: 240 }} value={filterSubject} onChange={(e) => setFilterSubject(e.target.value)}>
                            <option value="">Все предметы</option>
                            {subjectOptions.map((s) => <option key={s} value={s}>{s}</option>)}
                        </select>
                        <select className="form-input" style={{ maxWidth: 240 }} value={filterBrand} onChange={(e) => setFilterBrand(e.target.value)}>
                            <option value="">Все бренды</option>
                            {brandOptions.map((b) => <option key={b} value={b}>{b}</option>)}
                        </select>
                        <select className="form-input" style={{ maxWidth: 240 }} value={filterFf} onChange={(e) => setFilterFf(e.target.value)} title="Показать товары, лежащие на этом складе забора (ФФ)">
                            <option value="">Все склады ФФ (забор)</option>
                            {ffOptions.map((f) => <option key={f.id} value={String(f.id)}>{f.name}</option>)}
                        </select>
                        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                            <input type="checkbox" checked={hideEmpty} onChange={(e) => setHideEmpty(e.target.checked)} />
                            Скрыть нераспределённые
                        </label>
                        {(filterSubject || filterBrand || filterFf || hideEmpty) && (
                            <>
                                <button className="btn btn-sm btn-secondary" onClick={() => { setFilterSubject(''); setFilterBrand(''); setFilterFf(''); setHideEmpty(false); }}>Сбросить фильтр</button>
                                <span style={{ fontSize: 12, color: 'var(--color-muted)' }}>показано {formatNumber(visibleRows.length, 0)} из {formatNumber(sortedRows.length, 0)}</span>
                            </>
                        )}
                        {ffSliceActive && ffFilterId != null && (
                            <span style={{ fontSize: 12, color: 'var(--color-accent)' }} title="Ячейки, «Σ отпр.», «На ФФ», «Остаётся ФФ» и итоги показывают долю ЭТОГО склада забора (как в карточке черновика). Шапка «В черновике», колонка «Расчёт» и «⟳ Пересчитать» — вся матрица.">
                                📍 срез: доля забора с «{ffNameById.get(ffFilterId) || `ФФ ${ffFilterId}`}» · шапка/расчёт — вся матрица
                            </span>
                        )}
                        {editMode && filterFf && (
                            <span style={{ fontSize: 12, color: 'var(--color-warning)' }}>
                                ФФ-срез приостановлен на время ручной правки — показана полная раскладка
                            </span>
                        )}
                    </div>
                </div>

                {draftAgg.totalShip === 0 && !editMode && sortedRows.length === 0 ? (
                    <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-muted)' }}>
                        Черновик пуст и свободного остатка на ФФ нет.
                    </div>
                ) : (
                    <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                            <thead>
                                <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                                    <th colSpan={7} style={{ position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 2 }} />
                                    {districtGroups.map((g, i) => (
                                        <th key={i} colSpan={g.count} style={{ padding: '6px 8px', textAlign: 'center', color: '#fff', background: g.color, fontSize: 11, letterSpacing: 0.4, textTransform: 'uppercase' }}>
                                            {g.label}
                                        </th>
                                    ))}
                                    <th colSpan={3} />
                                </tr>
                                <tr style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-muted)', textAlign: 'right' }}>
                                    <th style={{ padding: '8px 12px', textAlign: 'left', position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 2, ...sortableTh }} onClick={() => toggleSort('label')} title="Сортировать по названию">Товар{sortArrow('label')}</th>
                                    <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('avail')} title="Сортировать по остатку на ФФ">На ФФ{sortArrow('avail')}</th>
                                    <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('inAssembly')} title="Уже в сборке на WB">В сборке{sortArrow('inAssembly')}</th>
                                    <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('stocksWb')} title="Остаток на Wildberries">На WB{sortArrow('stocksWb')}</th>
                                    <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('need')} title="Потребность на горизонт 14 дней (движок раскладки: скорость заказов за 14 дней; глобальная метрика SKU — ФФ-фильтр не влияет)">Потр. 14д{sortArrow('need')}</th>
                                    <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('revenue')} title="Выручка за 14 дней (продажи − возвраты, окно тренда движка потребности)">₽ 14д{sortArrow('revenue')}</th>
                                    <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('loc')} title="Индекс локализации артикула за 14 дней (доля локальных заказов; цвет — статус КТР)">Лок %{sortArrow('loc')}</th>
                                    {wbCols.map((c) => (
                                        <th key={c.name} style={{ padding: '8px 8px', whiteSpace: 'nowrap', background: districtTint(c.district), ...sortableTh }} onClick={() => toggleSort(`wb:${c.name}`)} title={`Сортировать по отправке в «${c.name}»`}>{c.name}{sortArrow(`wb:${c.name}`)}</th>
                                    ))}
                                    <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('ship')} title="Сортировать по сумме отправки">Σ отпр.{sortArrow('ship')}</th>
                                    <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('boxes')} title="Коробов к отправке">Мест{sortArrow('boxes')}</th>
                                    <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('stays')} title="Сортировать по остатку на ФФ">Остаётся ФФ{sortArrow('stays')}</th>
                                </tr>
                                <tr style={{ borderBottom: '2px solid var(--color-border)', fontWeight: 700, background: 'rgba(59,130,246,0.06)', textAlign: 'right' }}>
                                    <td style={{ padding: '6px 12px', textAlign: 'left', position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 2 }}>
                                        Сдаём <span style={{ fontWeight: 400, fontSize: 11, color: 'var(--color-muted)' }}>шт · кор</span>
                                    </td>
                                    <td colSpan={6} />
                                    {wbCols.map((c) => {
                                        const u = matrixView.shipByWh.get(c.name) ?? 0;
                                        const bx = matrixView.boxesByWh.get(c.name) ?? 0;
                                        return (
                                            <td key={c.name} style={{ padding: '6px 8px', background: districtTint(c.district) }} title={`${formatNumber(u, 0)} штук · ${formatNumber(bx, 0)} коробов`}>
                                                <div style={{ color: 'var(--color-accent)' }}>{u > 0 ? `${formatNumber(u, 0)} шт` : '·'}</div>
                                                {bx > 0 && <div style={{ fontWeight: 400, fontSize: 11, color: 'var(--color-muted)' }}>{formatNumber(bx, 0)} кор</div>}
                                            </td>
                                        );
                                    })}
                                    <td style={{ padding: '6px 8px', color: 'var(--color-accent)' }} title="Всего штук в черновике (строки + предбронь)">{formatNumber(matrixView.totalShip, 0)} шт</td>
                                    <td style={{ padding: '6px 8px', color: 'var(--color-muted)' }} title="Всего коробов">{formatNumber(matrixView.totalBoxes, 0)} кор</td>
                                    <td style={{ padding: '6px 8px', color: 'var(--color-muted)' }} title="Остаётся на хранении ФФ">{formatNumber(matrixViewOnHold, 0)}</td>
                                </tr>
                            </thead>
                            <tbody>
                                {visibleRows.map((a) => {
                                    const nm = a.nm_id;
                                    const e: EnrichedSku | undefined = enrichMap.get(nm);
                                    const avail = availOf(a);
                                    const ship = viewAgg.allocByBc.get(a.barcode) ?? 0;
                                    const stays = Math.max(0, avail - ship);
                                    const cells = viewAgg.cellByBc.get(a.barcode);
                                    const ppb = nmPpb.get(nm);
                                    const label = a.vendor_code || String(nm) || a.barcode;
                                    const rowBoxes = boxesOf(ship, ppb);
                                    const rowEditable = editMode && !!ppb && ppb > 0;
                                    return (
                                        <tr key={a.barcode} style={{ borderBottom: '1px solid var(--color-border)', background: ship > 0 ? 'rgba(59,130,246,0.04)' : undefined }}>
                                            <td style={{ padding: '6px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 1 }}>
                                                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                                                    <span style={{ fontWeight: 600 }}>{label}</span>
                                                    {e?.isNew && <span className="badge" style={{ background: 'rgba(168,85,247,0.16)', color: '#a855f7', fontSize: 10, padding: '1px 6px' }}>🆕 новинка</span>}
                                                    {guardByNm.has(nm) && (
                                                        <span className="badge" title={`Гвард пересорта: ${guardByNm.get(nm)}. Авто-досев выключен; ручные −/+ работают.`}
                                                            style={{ background: 'rgba(255,159,10,0.14)', color: 'var(--color-warning)', fontSize: 10, padding: '1px 6px' }}>
                                                            ⚠ посев лежит без продаж
                                                        </span>
                                                    )}
                                                    {manualNms.has(nm) && (
                                                        <button type="button" className="badge"
                                                            title={`План этого SKU задан вручную — авто-синк его не трогает. Живой расчёт предлагает: ${formatNumber(calcAgg.allocByBc.get(a.barcode) ?? 0, 0)} шт. Клик — вернуть SKU в авто (план заменится расчётом).`}
                                                            style={{ background: 'rgba(59,130,246,0.12)', color: 'var(--color-accent)', fontSize: 10, padding: '1px 6px', border: 'none', cursor: 'pointer' }}
                                                            onClick={() => returnToAuto(nm)}>
                                                            ✋ вручную · ↺
                                                        </button>
                                                    )}
                                                    {ppb ? <span className="badge badge-secondary" style={{ fontSize: 10, padding: '1px 6px' }}>📦 кратно {formatNumber(ppb, 0)}</span> : <span className="badge" style={{ background: 'rgba(255,159,10,0.14)', color: 'var(--color-warning)', fontSize: 10, padding: '1px 6px' }}>без кратности</span>}
                                                </div>
                                                <div style={{ fontSize: 11, color: 'var(--color-muted)' }}>{a.subject ? `${a.subject} · ` : ''}ШК {a.barcode}</div>
                                            </td>
                                            <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(avail, 0)}</td>
                                            <td style={{ padding: '6px 8px', textAlign: 'right', color: e && e.inAssembly > 0 ? 'var(--color-text)' : 'var(--color-dim)' }}>{e && e.inAssembly > 0 ? formatNumber(e.inAssembly, 0) : '·'}</td>
                                            <td style={{ padding: '6px 8px', textAlign: 'right', color: e && e.stocksWb > 0 ? 'var(--color-text)' : 'var(--color-dim)' }}>{e && e.stocksWb > 0 ? formatNumber(e.stocksWb, 0) : '·'}</td>
                                            {(() => {
                                                const needQty = Number(a.total_need) || 0;
                                                const rev = Number(a.revenue_30d) || 0;
                                                const loc = locByNm.get(nm);
                                                const locPct = loc ? Number(loc.loc_pct) || 0 : null;
                                                return (
                                                    <>
                                                        <td style={{ padding: '6px 8px', textAlign: 'right', color: needQty > 0 ? 'var(--color-text)' : 'var(--color-dim)' }}>{needQty > 0 ? formatNumber(needQty, 0) : '·'}</td>
                                                        <td style={{ padding: '6px 8px', textAlign: 'right', color: rev > 0 ? 'var(--color-muted)' : 'var(--color-dim)' }}>{rev > 0 ? formatNumber(rev, 0) : '·'}</td>
                                                        <td style={{ padding: '6px 8px', textAlign: 'right', color: locPct != null ? (LOC_STATUS_COLOR[loc!.status] || 'var(--color-text)') : 'var(--color-dim)' }}
                                                            title={loc ? `Заказов 14д: ${formatNumber(loc.total, 0)} · локальных: ${formatNumber(loc.local, 0)} · КТР ${formatNumber(Number(loc.ktr), 2)}` : 'Нет данных локализации за окно 14 дней'}>
                                                            {locPct != null ? `${formatNumber(locPct, 0)}%` : '·'}
                                                        </td>
                                                    </>
                                                );
                                            })()}
                                            {wbCols.map((c) => {
                                                const cell = cells?.get(c.name);
                                                const ctx = e?.byWh[c.name];
                                                const ctxBusy = (ctx?.asm ?? 0) + (ctx?.transit ?? 0);
                                                const pbPart = prebookCellByBc.get(a.barcode)?.get(c.name) ?? 0;
                                                return (
                                                    <NeedMatrixCell
                                                        key={c.name}
                                                        ship={cell ?? null}
                                                        stock={ctx?.stock ?? 0}
                                                        onWay={ctxBusy}
                                                        tint={pbPart > 0 ? 'color-mix(in srgb, var(--color-accent) 8%, transparent)' : districtTint(c.district)}
                                                        mark={markFor(nm, c.name, cell?.pkg ?? null)}
                                                        edit={rowEditable && ppb ? {
                                                            boxes: boxesOf(cell?.qty ?? 0, ppb),
                                                            ppb,
                                                            onDelta: (d: number) => editDraftCell(a.barcode, nm, c.name, d),
                                                            disableInc: false,
                                                        } : null}
                                                    />
                                                );
                                            })}
                                            {(() => {
                                                const pbCell = prebookCellByBc.get(a.barcode);
                                                const pbNm = pbCell ? [...pbCell.values()].reduce((s2, v) => s2 + v, 0) : 0;
                                                return (
                                                    <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600, whiteSpace: 'nowrap' }}
                                                        title={ship > 0 ? `План черновика: ${formatNumber(ship, 0)} шт${pbNm > 0 ? ` (в т.ч. 🅿️ ${formatNumber(pbNm, 0)} в предброни)` : ''}` : 'В черновике этого SKU нет'}>
                                                        {ship > 0 ? (
                                                            <>
                                                                {formatNumber(ship, 0)}
                                                                {pbNm > 0 && (
                                                                    <span style={{ color: 'var(--color-accent)', fontWeight: 400, fontSize: 11 }}
                                                                        title={pbNm >= ship
                                                                            ? 'Весь план SKU — в предброни: заявкой НЕ станет, пока не «Дозабить»/«Отправить как есть» (вкладка 🅿️ Предбронь черновика)'
                                                                            : `${formatNumber(pbNm, 0)} шт из плана — в предброни (не станут заявками без дозабора)`}>
                                                                        {' '}{pbNm >= ship ? '🅿️!' : `(${formatNumber(pbNm, 0)}🅿️)`}
                                                                    </span>
                                                                )}
                                                                <button type="button" title="Убрать SKU из черновика (строки + предбронь)" disabled={removingNm === nm}
                                                                    style={{ marginLeft: 6, border: 'none', background: 'transparent', color: 'var(--color-danger)', cursor: 'pointer', fontSize: 12, padding: 0 }}
                                                                    onClick={() => removeFromDraft(nm, label)}>
                                                                    {removingNm === nm ? '…' : '✕'}
                                                                </button>
                                                            </>
                                                        ) : '·'}
                                                    </td>
                                                );
                                            })()}
                                            <td style={{ padding: '6px 8px', textAlign: 'right' }}>{rowBoxes > 0 ? formatNumber(rowBoxes, 0) : '·'}</td>
                                            <td style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--color-muted)' }}>{formatNumber(stays, 0)}</td>
                                        </tr>
                                    );
                                })}
                                {visibleRows.length === 0 && (
                                    <tr>
                                        <td colSpan={wbCols.length + 10} style={{ padding: 24, textAlign: 'center', color: 'var(--color-muted)' }}>
                                            Ничего не найдено по выбранному фильтру
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                            <tfoot>
                                <tr style={{ borderTop: '2px solid var(--color-border)', fontWeight: 600 }}>
                                    <td style={{ padding: '8px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)' }}>Отправить, шт</td>
                                    <td colSpan={6} />
                                    {wbCols.map((c) => (
                                        <td key={c.name} style={{ padding: '8px 8px', textAlign: 'right', color: 'var(--color-accent)' }}>{formatNumber(matrixView.shipByWh.get(c.name) ?? 0, 0)}</td>
                                    ))}
                                    <td style={{ padding: '8px 8px', textAlign: 'right' }}>{formatNumber(matrixView.totalShip, 0)}</td>
                                    <td colSpan={2} />
                                </tr>
                                <tr style={{ color: 'var(--color-muted)' }}>
                                    <td style={{ padding: '6px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)' }}>📦 Коробов</td>
                                    <td colSpan={6} />
                                    {wbCols.map((c) => (
                                        <td key={c.name} style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(matrixView.boxesByWh.get(c.name) ?? 0, 0)}</td>
                                    ))}
                                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(matrixView.totalBoxes, 0)}</td>
                                    <td colSpan={2} />
                                </tr>
                                <tr style={{ color: 'var(--color-muted)' }}>
                                    <td style={{ padding: '6px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)' }}>🚚 Паллет</td>
                                    <td colSpan={6} />
                                    {wbCols.map((c) => (
                                        <td key={c.name} style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(matrixView.palletsByWh.get(c.name) ?? 0, 0)}</td>
                                    ))}
                                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(matrixView.totalPallets, 0)}</td>
                                    <td colSpan={2} />
                                </tr>
                            </tfoot>
                        </table>
                    </div>
                )}
            </>)}
        </div>
    );
}
