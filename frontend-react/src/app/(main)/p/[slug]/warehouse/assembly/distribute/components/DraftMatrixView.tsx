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
    applyCellBoxDelta,
    buildDraftSkus,
    buildPinnedRowsFf,
    buildTopUpRowsFf,
    enrichArticles,
    finalizeDraftDistribution,
    planBoxTopUpFf,
    rowsToPreDistRows,
    splitByKratnost,
    type AcceptanceSplitMap,
    type CellEdits,
    type DraftDistInput,
    type EnrichedSku,
    type PinnedPkgOf,
} from '@/lib/assembly/draftDistribution';
import { buildPrebookGroups } from '@/lib/assembly/buildPrebookGroups';
import NeedMatrixCell, { type CellMark } from './NeedMatrixCell';
import AcceptanceBanner, { type AcceptanceSummary } from './AcceptanceBanner';
import PrebookView, { type PrebookAcceptanceMark } from './PrebookView';
import type {
    AcceptanceCheckPerItem,
    AcceptanceFlags,
    AssemblyDraftRow,
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

type SortKey = 'label' | 'avail' | 'inAssembly' | 'stocksWb' | 'ship' | 'boxes' | 'stays' | `wb:${string}`;

/** Ключ строки распределения для замены по SKU (nm × баркод × упаковка × as_is) —
 *  зеркало бэкенд-ключа `_merge_rows`/`_dedupe_rows`. */
const keyOfRow = (r: AssemblyDraftRow) => `${r.nm_id}::${r.barcode || ''}::${r.package_type || 'BOX'}::${r.as_is ? 1 : 0}`;

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
    onWritten?: () => void;
}

/** Ручная раскладка ЧЕРНОВИКА по WB-складам — зеркало экрана «Распределить машину»,
 *  но ИСТОЧНИК = весь свободный ФФ-сток проекта (мульти-склад), а не пул одной машины.
 *  Матрица со степперами −/+; правка → пин → общий движок (целые паллеты в черновик,
 *  под-паллетные хвосты в 🅿️ Предбронь: Дозабить / Оставить так / На ФФ). */
export default function DraftMatrixView({ draftId, ffNameById, onWritten }: DraftMatrixViewProps) {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    const [stockNeed, setStockNeed] = useState<StockNeedResponse | null>(null);
    const [newcomerSet, setNewcomerSet] = useState<Set<number>>(new Set());
    const [nmPpb, setNmPpb] = useState<Map<number, number | null>>(new Map());
    const [nmBoxSize, setNmBoxSize] = useState<Map<number, string | null>>(new Map());
    const [palletOverrides, setPalletOverrides] = useState<Record<string, number>>({});
    const [geomReady, setGeomReady] = useState(false);

    const [distRows, setDistRows] = useState<AssemblyDraftRow[] | null>(null);
    const [distRowsBox, setDistRowsBox] = useState<AssemblyDraftRow[]>([]);
    const [prebookRows, setPrebookRows] = useState<AssemblyDraftRow[]>([]);
    const [subTab, setSubTab] = useState<'dist' | 'prebook'>('dist');
    const [promotedDirs, setPromotedDirs] = useState<Set<string>>(new Set());
    const [hiddenDirs, setHiddenDirs] = useState<Set<string>>(new Set());
    const [hiddenSkus, setHiddenSkus] = useState<Set<string>>(new Set());
    const [manualTopUp, setManualTopUp] = useState<Map<string, Record<string, number>>>(new Map());
    const [cellEdits, setCellEdits] = useState<CellEdits>(new Map());
    const [editMode, setEditMode] = useState(false);
    const acceptanceCacheRef = useRef<{ sig: string; splitMap: AcceptanceSplitMap | null; summary: AcceptanceSummary | null; accByNm: Map<number, AcceptanceCheckPerItem> } | null>(null);
    const [prebookOpKey, setPrebookOpKey] = useState<string | null>(null);
    const [acceptanceByNm, setAcceptanceByNm] = useState<Map<number, AcceptanceCheckPerItem>>(new Map());
    const [preorderWbs, setPreorderWbs] = useState<Set<string>>(new Set());
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
                const [need, cold, boxMult, palletOv, preorder] = await Promise.all([
                    // Первичный источник экрана — НЕ глушим (иначе 500 покажет пустое «нечего
                    // отгружать» вместо ошибки). Отказ → reject Promise.all → error-стейт.
                    api.getStockNeed(14, 14, 'actual', true, true, 0) as Promise<StockNeedResponse>,
                    api.getColdStartTable(14).catch(() => null),
                    api.getBoxMultiplicity().catch(() => null),
                    api.getPalletBoxesBySize().catch(() => ({} as Record<string, number>)),
                    api.getPreorderAllowedWarehouses().catch(() => [] as string[]),
                ]);
                if (controller.signal.aborted) return;
                setStockNeed(need);
                setPreorderWbs(new Set(preorder));
                setPromotedDirs(new Set());
                setHiddenDirs(new Set());
                setHiddenSkus(new Set());
                setManualTopUp(new Map());
                setCellEdits(new Map());
                acceptanceCacheRef.current = null;

                const ncs = new Set<number>();
                for (const r of cold?.rows ?? []) if (r.is_newcomer) ncs.add(r.nm_id);
                setNewcomerSet(ncs);

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
        if (articles.length === 0 && cellEdits.size === 0) { setDistRows([]); setPrebookRows([]); setDistRowsBox([]); setAcceptanceNote(null); return; }
        setDistComputing(true);
        try {
            const distInput: DraftDistInput = { articles, stockNeed, nmPpb, nmBoxSize, palletOverrides, newcomerSet };
            const { skus: allSkus } = buildDraftSkus(distInput);
            const { shippable: skus } = splitByKratnost(allSkus, (nm) => nmPpb.get(nm));

            // Ручной режим = АВТО + пер-строчные override: артикулы БЕЗ пина остаются
            // авто-раскладкой, а запинённые (юзер тронул ячейку) — ручные. Так «вручную»
            // помечается ТОЛЬКО реально отредактированная строка, а не весь список.
            const activeEdits: CellEdits = cellEdits;
            const nmByBarcode = new Map<string, number>();
            for (const a of articles) nmByBarcode.set(a.barcode, a.nm_id);
            const autoSkus = skus.filter((s) => !activeEdits.has(s.barcode));
            const pinnedItems: { nm_id: number; barcode: string; distribution: Record<string, number> }[] = [];
            for (const [bc, whBoxes] of activeEdits) {
                const nm = nmByBarcode.get(bc) ?? 0;
                const ppb = nmPpb.get(nm) || 0;
                if (!nm || ppb <= 0) continue;
                const dist: Record<string, number> = {};
                for (const [wb, boxes] of Object.entries(whBoxes)) if ((boxes || 0) > 0) dist[wb] = Math.floor(boxes) * ppb;
                if (Object.keys(dist).length > 0) pinnedItems.push({ nm_id: nm, barcode: bc, distribution: dist });
            }
            const accSig = JSON.stringify([
                ...autoSkus.map((s) => [s.nm_id, s.barcode, s.target]),
                ...pinnedItems.map((p) => [p.nm_id, p.barcode, Object.keys(p.distribution).sort()]),
            ]);
            let splitMap: AcceptanceSplitMap | null = null;
            let summary: AcceptanceSummary | null = null;
            let accByNm = new Map<number, AcceptanceCheckPerItem>();
            const accCache = acceptanceCacheRef.current;
            if (accCache && accCache.sig === accSig) {
                splitMap = accCache.splitMap; summary = accCache.summary; accByNm = accCache.accByNm;
            } else if (autoSkus.length === 0 && pinnedItems.length === 0) {
                splitMap = new Map();
                acceptanceCacheRef.current = { sig: accSig, splitMap, summary, accByNm };
            } else {
                try {
                    const resp = await api.checkWbAcceptance({
                        items: [
                            ...autoSkus.map((s) => ({ nm_id: s.nm_id, barcode: s.barcode, distribution: s.target })),
                            ...pinnedItems.map((p) => ({ nm_id: p.nm_id, barcode: p.barcode, distribution: p.distribution })),
                        ],
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

            const pinnedPkgOf: PinnedPkgOf = (nm, wb) => {
                const f = accByNm.get(nm)?.availability?.[wb];
                if (!f) return 'BOX';
                return f.can_box ? 'BOX' : f.can_monopallet ? 'MONOPALLET' : f.can_supersafe ? 'SUPERSAFE' : 'BOX';
            };
            const pinnedRows = buildPinnedRowsFf(activeEdits, articles, nmPpb, pinnedPkgOf);

            // Резерв per баркод (Σsrc потребности + пинов) — кап ручного дозабора.
            // minOneBoxPerWh=true — как у финальных box/whole, иначе резерв недооценён.
            const needBoxRows = finalizeDraftDistribution(effective, distInput, false, [], true).rows;
            const reservedByBc = new Map<string, number>();
            for (const r of [...needBoxRows, ...pinnedRows]) {
                const s = Object.values(r.src).reduce((a, v) => a + (v || 0), 0);
                reservedByBc.set(r.barcode, (reservedByBc.get(r.barcode) ?? 0) + s);
            }
            const topUpRows = buildTopUpRowsFf(manualTopUp, articles, nmPpb, reservedByBc);
            const extra = [...topUpRows, ...pinnedRows];

            const whole = finalizeDraftDistribution(effective, distInput, true, extra, true);
            const box = finalizeDraftDistribution(effective, distInput, false, extra, true);
            if (!signal.aborted) {
                setDistRows(whole.rows);
                setPrebookRows(whole.prebook);
                setDistRowsBox(box.rows);
                setAcceptanceNote(summary);
                setAcceptanceByNm(accByNm);
            }
        } catch (e) {
            if (!signal.aborted) { setDistRows([]); setPrebookRows([]); setDistRowsBox([]); showToast(e instanceof Error ? e.message : 'Ошибка раскладки', 'error'); }
        } finally {
            if (!signal.aborted) setDistComputing(false);
        }
    }, [articles, stockNeed, nmPpb, nmBoxSize, palletOverrides, manualTopUp, cellEdits, newcomerSet, showToast]);

    useEffect(() => {
        if (!geomReady) return;
        const controller = new AbortController();
        computeDistribution(controller.signal);
        return () => controller.abort();
    }, [geomReady, computeDistribution]);

    // ─── Предбронь: атомарные направления (per ФФ×WB через allocatePairs) ────
    const dirKeyOf = (r: AssemblyDraftRow): string =>
        `${r.package_type ?? 'BOX'}::${Object.keys(r.tgt)[0] ?? ''}::${Object.keys(r.src)[0] ?? ''}`;
    const atomicPrebook = useMemo<AssemblyDraftRow[]>(() => {
        const out: AssemblyDraftRow[] = [];
        for (const r of prebookRows) {
            const pkg = r.package_type ?? 'BOX';
            for (const [pair, q] of allocatePairs(r.src, r.tgt)) {
                if ((q || 0) <= 0) continue;
                const [ff, wb] = pair.split('::');
                if (hiddenSkus.has(`${r.nm_id}::${wb}::${pkg}`)) continue;
                out.push({ nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [ff]: q }, tgt: { [wb]: q }, package_type: pkg });
            }
        }
        return out;
    }, [prebookRows, hiddenSkus]);
    const effPrebook = useMemo(
        () => atomicPrebook.filter((r) => { const k = dirKeyOf(r); return !promotedDirs.has(k) && !hiddenDirs.has(k); }),
        [atomicPrebook, promotedDirs, hiddenDirs],
    );
    const promotedRows = useMemo(() => atomicPrebook.filter((r) => promotedDirs.has(dirKeyOf(r))), [atomicPrebook, promotedDirs]);
    const shipRows = useMemo(() => [...(distRows ?? []), ...promotedRows], [distRows, promotedRows]);
    const prebookUnits = useMemo(() => effPrebook.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0), [effPrebook]);

    const nmByBc = useMemo(() => {
        const m = new Map<string, number>();
        for (const a of articles) m.set(a.barcode, a.nm_id);
        return m;
    }, [articles]);
    const availByBc = useMemo(() => {
        const m = new Map<string, number>();
        for (const a of articles) m.set(a.barcode, Object.values(a.rf_stocks || {}).reduce((s, st) => s + Math.max(0, Math.floor(Number(st?.available) || 0)), 0));
        return m;
    }, [articles]);

    const commit = useMemo(() => buildDistAgg(shipRows, nmByBc, nmPpb, nmBoxSize, palletOverrides), [shipRows, nmByBc, nmPpb, nmBoxSize, palletOverrides]);
    const matrix = useMemo(() => buildDistAgg(distRowsBox, nmByBc, nmPpb, nmBoxSize, palletOverrides), [distRowsBox, nmByBc, nmPpb, nmBoxSize, palletOverrides]);
    const submitRows = commit.submitRows;

    const enrichMap = useMemo(() => enrichArticles(articles, stockNeed, newcomerSet), [articles, stockNeed, newcomerSet]);

    const wbCols = useMemo(() => {
        const distByWh = new Map<string, string>();
        for (const w of stockNeed?.warehouses ?? []) if (w.name) distByWh.set(w.name, w.district_key || 'unknown');
        const names = new Set<string>();
        for (const r of matrix.submitRows) names.add(r.wb_warehouse_name);
        for (const e of enrichMap.values()) for (const [wh, c] of Object.entries(e.byWh)) if (c.need > 0) names.add(wh);
        const arr = [...names].map((name) => ({ name, district: distByWh.get(name) || 'unknown' }));
        arr.sort((a, b) => {
            const ra = districtRank(a.district), rb = districtRank(b.district);
            return ra !== rb ? ra - rb : a.name.localeCompare(b.name, 'ru');
        });
        return arr;
    }, [matrix, enrichMap, stockNeed]);

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
        () => articles.reduce((s, a) => s + Math.max(0, (availByBc.get(a.barcode) ?? 0) - (commit.allocByBc.get(a.barcode) ?? 0)), 0),
        [articles, availByBc, commit],
    );

    const noKratnostArticles = useMemo(() => {
        const out: { nm_id: number; vendor: string; qty: number }[] = [];
        for (const a of articles) {
            if ((nmPpb.get(a.nm_id) || 0) > 0) continue;
            const qty = availByBc.get(a.barcode) ?? 0;
            if (qty > 0) out.push({ nm_id: a.nm_id, vendor: a.vendor_code || String(a.nm_id), qty });
        }
        return out.sort((x, y) => y.qty - x.qty);
    }, [articles, nmPpb, availByBc]);

    // Предбронь: кандидаты дозабора = свободный ФФ-сток per nm (мульти-склад).
    const prebookArticles = useMemo(() => {
        return articles.map((a) => {
            const rfStocks: Record<number, number> = {};
            for (const [ff, st] of Object.entries(a.rf_stocks || {})) {
                const av = Math.max(0, Math.floor(Number(st?.available) || 0));
                if (av > 0) rfStocks[Number(ff)] = av;
            }
            return { nm_id: a.nm_id, vendor_code: a.vendor_code || String(a.nm_id), rfStocks };
        });
    }, [articles]);

    const prebookGroups = useMemo(() => {
        return buildPrebookGroups({
            prebook: effPrebook,
            usedRows: shipRows,
            articles: prebookArticles,
            ffName: (ff) => ffNameById.get(ff) || `ФФ ${ff}`,
            ppbOf: (nm) => nmPpb.get(nm) || 0,
            ppbAt: (nm) => nmPpb.get(nm) || 0,
            boxSizeOf: (nm) => nmBoxSize.get(nm) ?? null,
            palletOverrides,
        });
    }, [effPrebook, shipRows, prebookArticles, ffNameById, nmPpb, nmBoxSize, palletOverrides]);

    const prebookAcceptanceMarks = useMemo<Map<string, PrebookAcceptanceMark>>(() => {
        const out = new Map<string, PrebookAcceptanceMark>();
        if (acceptanceByNm.size === 0) return out;
        for (const g of prebookGroups) {
            const key = `${g.pkg}::${g.wb}::${g.ffId}`;
            let flags: AcceptanceFlags | undefined;
            for (const it of g.items) { const f = acceptanceByNm.get(it.nm_id)?.availability?.[g.wb]; if (f) { flags = f; break; } }
            if (!flags) { out.set(key, { checked: false, open: false, closed: false, noLimit: false }); continue; }
            const closed = !flags.can_box && !flags.can_monopallet && !flags.can_supersafe;
            const meta = g.pkg === 'MONOPALLET' ? flags.mono_meta : g.pkg === 'SUPERSAFE' ? flags.super_meta : flags.box_meta;
            const open = g.pkg === 'MONOPALLET' ? !!flags.can_monopallet : g.pkg === 'SUPERSAFE' ? !!flags.can_supersafe : !!flags.can_box;
            const freeDays = meta?.free_days_14, paidDays = meta?.paid_days_14;
            const noLimit = open && (freeDays ?? 0) + (paidDays ?? 0) <= 0;
            out.set(key, { checked: true, open, closed, freeDays, paidDays, noLimit });
        }
        return out;
    }, [prebookGroups, acceptanceByNm]);

    const promoteDir = useCallback((pkg: PackageType, wb: string, ffId: number) => {
        const k = `${pkg}::${wb}::${ffId}`;
        setPrebookOpKey(k);
        setPromotedDirs((s) => new Set(s).add(k));
        setTimeout(() => setPrebookOpKey(null), 250);
    }, []);
    const hideDir = useCallback((pkg: PackageType, wb: string, ffId: number) => {
        setHiddenDirs((s) => new Set(s).add(`${pkg}::${wb}::${ffId}`));
    }, []);

    const topUpBox = useCallback((pkg: PackageType, wb: string, ffId: number) => {
        if (pkg !== 'BOX') { promoteDir(pkg, wb, ffId); return; }
        const g = prebookGroups.find((x) => x.pkg === 'BOX' && x.wb === wb && x.ffId === ffId);
        const adds = g?.topUp ? planBoxTopUpFf(g.topUp.candidates, wb, articles, commit.allocByBc, nmPpb) : [];
        if (adds.length === 0) { showToast('Нет свободного ФФ-остатка, чтобы дозабрать паллету', 'error'); return; }
        setPrebookOpKey(`${pkg}::${wb}::${ffId}`);
        setManualTopUp((prev) => {
            const next = new Map(prev);
            for (const a of adds) {
                const wbMap = { ...(next.get(a.barcode) ?? {}) };
                wbMap[a.wb] = (wbMap[a.wb] ?? 0) + a.units;
                next.set(a.barcode, wbMap);
            }
            return next;
        });
        setTimeout(() => setPrebookOpKey(null), 250);
    }, [prebookGroups, articles, commit, nmPpb, promoteDir, showToast]);

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

    const editCellBoxes = useCallback((barcode: string, nm: number, wh: string, delta: number) => {
        const ppb = nmPpb.get(nm) || 0;
        if (ppb <= 0) return;
        const avail = availByBc.get(barcode) ?? 0;
        setCellEdits((prev) => {
            const next = new Map(prev);
            let rec = next.get(barcode);
            if (!rec) {
                // Первая правка строки — замораживаем её ТЕКУЩУЮ авто-раскладку в пины,
                // чтобы правка одной ячейки не обнулила остальные склады этой строки.
                rec = {};
                const cells = matrix.cellByBc.get(barcode);
                if (cells) for (const [w, c] of cells) { const b = boxesOf(c.qty, ppb); if (b > 0) rec[w] = b; }
            }
            next.set(barcode, applyCellBoxDelta(rec, wh, delta, ppb, avail));
            return next;
        });
    }, [nmPpb, availByBc, matrix]);
    const resetRowEdits = useCallback((barcode: string) => {
        setCellEdits((prev) => { if (!prev.has(barcode)) return prev; const n = new Map(prev); n.delete(barcode); return n; });
    }, []);
    const resetAllEdits = useCallback(() => setCellEdits(new Map()), []);

    // Вход в ручной режим = просто показать степперы поверх авто-раскладки. НЕ сеем пины:
    // нетронутые строки остаются авто, «вручную» помечается лишь та, где юзер кликнул −/+.
    const enterManual = useCallback(() => setEditMode(true), []);

    const sortValue = useCallback((a: StockNeedArticle, key: SortKey): number | string => {
        const nm = a.nm_id;
        const e = enrichMap.get(nm);
        const ship = matrix.allocByBc.get(a.barcode) ?? 0;
        const avail = availByBc.get(a.barcode) ?? 0;
        switch (key) {
            case 'label': return (a.vendor_code || a.barcode).toLowerCase();
            case 'avail': return avail;
            case 'inAssembly': return e?.inAssembly ?? 0;
            case 'stocksWb': return e?.stocksWb ?? 0;
            case 'ship': return ship;
            case 'boxes': return boxesOf(ship, nmPpb.get(nm));
            case 'stays': return Math.max(0, avail - ship);
            default: return matrix.cellByBc.get(a.barcode)?.get(key.slice(3))?.qty ?? 0;
        }
    }, [enrichMap, matrix, availByBc, nmPpb]);

    const sortedRows = useMemo(() => {
        const rows = [...articles];
        const dir = sortDir === 'asc' ? 1 : -1;
        rows.sort((a, b) => {
            const va = sortValue(a, sortKey), vb = sortValue(b, sortKey);
            const cmp = typeof va === 'string' || typeof vb === 'string' ? String(va).localeCompare(String(vb), 'ru') : va - vb;
            if (cmp !== 0) return cmp * dir;
            return (a.vendor_code || a.barcode).localeCompare(b.vendor_code || b.barcode, 'ru');
        });
        return rows;
    }, [articles, sortKey, sortDir, sortValue]);

    // ─── Фильтр таблицы: предмет / бренд / скрыть нераспределённые ───────────
    // Лупа по ОТОБРАЖЕНИЮ (не по расчёту) — раскладка/паллеты/KPI считаются по всему
    // ФФ-стоку, фильтр лишь сужает видимые строки (черновик масштаба = сотни артикулов).
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
            if (filterFf && (Number(a.rf_stocks?.[Number(filterFf)]?.available) || 0) <= 0) return false;
            if (hideEmpty && (matrix.allocByBc.get(a.barcode) ?? 0) <= 0) return false;
            return true;
        }),
        [sortedRows, filterSubject, filterBrand, filterFf, hideEmpty, matrix],
    );

    // Итоги колонок («Сдаём» / футер) — по ВИДИМЫМ строкам, чтобы суммы под складами
    // соответствовали фильтру. Без фильтра === полная матрица (переиспользуем `matrix`).
    const isFiltered = !!(filterSubject || filterBrand || filterFf || hideEmpty);
    const visibleBarcodes = useMemo(() => new Set(visibleRows.map((a) => a.barcode)), [visibleRows]);
    const matrixView = useMemo(
        () => (isFiltered
            ? buildDistAgg(distRowsBox.filter((r) => visibleBarcodes.has(r.barcode)), nmByBc, nmPpb, nmBoxSize, palletOverrides)
            : matrix),
        [isFiltered, distRowsBox, visibleBarcodes, nmByBc, nmPpb, nmBoxSize, palletOverrides, matrix],
    );
    const matrixViewOnHold = useMemo(
        () => visibleRows.reduce((s, a) => s + Math.max(0, (availByBc.get(a.barcode) ?? 0) - (matrixView.allocByBc.get(a.barcode) ?? 0)), 0),
        [visibleRows, availByBc, matrixView],
    );

    // «Записать в черновик» — ЗАМЕНА по SKU (не сложение): строки черновика по SKU, которые
    // разложила матрица, заменяются её результатом (целые паллеты → rows, под-паллетные хвосты
    // → prebook «дозабить»), прочие строки черновика сохраняются. Так повторная запись
    // идемпотентна и нет двойного счёта/двойного использования остатка. Дальше штатная
    // логика черновика (self-heal: целые коробы/паллеты, дозабор хвостов) доводит до ума.
    const handleWrite = useCallback(async () => {
        if ((shipRows.length === 0 && effPrebook.length === 0) || writing) return;
        setWriting(true);
        try {
            const cur = await api.getAssemblyDraft(draftId);
            const dist = cur.distribution;
            // «Владение» матрицы = все SKU, по которым она дала отгрузку ИЛИ предбронь.
            const owned = new Set<string>([...shipRows.map(keyOfRow), ...effPrebook.map(keyOfRow)]);
            const keptRows = (dist?.rows ?? []).filter((r) => !owned.has(keyOfRow(r)));
            const keptPrebook = (dist?.prebook ?? []).filter((r) => !owned.has(keyOfRow(r)));
            const newRows = [...keptRows, ...shipRows];
            const newPrebook = [...keptPrebook, ...effPrebook];
            // Пересчёт списков складов из новых строк (иначе останутся stale от cur).
            const srcIds = new Set<number>();
            const tgtNames = new Set<string>();
            for (const r of [...newRows, ...newPrebook]) {
                for (const ff of Object.keys(r.src)) srcIds.add(Number(ff));
                for (const wb of Object.keys(r.tgt)) tgtNames.add(wb);
            }
            await api.updateAssemblyDraft(draftId, {
                distribution: {
                    ...dist,
                    rows: newRows,
                    prebook: newPrebook,
                    source_warehouse_ids: [...srcIds],
                    target_warehouse_names: [...tgtNames],
                },
            });
            showToast(`Записано в черновик: ${formatNumber(commit.totalShip, 0)} шт (замена по SKU)`, 'success');
            onWritten?.();
        } catch (e) {
            showToast(e instanceof Error ? e.message : 'Ошибка записи в черновик', 'error');
        } finally {
            setWriting(false);
        }
    }, [shipRows, effPrebook, writing, draftId, commit, showToast, onWritten]);

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
    if (articles.length === 0) {
        return <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>Нет свободного остатка на ФФ для распределения</div>;
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
                <span>К отправке: <b style={{ fontSize: 18 }}>{formatNumber(commit.totalShip, 0)}</b> шт</span>
                <span>📦 Коробов: <b style={{ fontSize: 18 }}>{formatNumber(commit.totalBoxes, 0)}</b></span>
                <span>🚚 Паллет: <b style={{ fontSize: 18 }}>{formatNumber(commit.totalPallets, 0)}</b></span>
                <span style={{ color: 'var(--color-muted)' }}>На хранение (ФФ): <b style={{ color: 'var(--color-text)' }}>{formatNumber(onHoldQty, 0)}</b> шт</span>
                {prebookUnits > 0 && (
                    <span style={{ color: 'var(--color-muted)' }}>🅿️ Предбронь: <b style={{ color: 'var(--color-text)' }}>{formatNumber(prebookUnits, 0)}</b> шт</span>
                )}
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 12, alignItems: 'center' }}>
                    <span style={{ fontSize: 12, color: 'var(--color-muted)' }} title="Матрица «Раскладка» = коробное покрытие потребности. В черновик пишутся только целые паллеты; неполные — в 🅿️ Предбронь (Дозабить / Оставить так / На ФФ).">
                        📦 Матрица — покрытие · 🚚 Черновик — целые паллеты
                    </span>
                    <button className="btn btn-primary" onClick={handleWrite} disabled={writing || (shipRows.length === 0 && effPrebook.length === 0)}>
                        {writing ? 'Запись…' : `Записать в черновик (${formatNumber(commit.totalShip, 0)} шт)`}
                    </button>
                </div>
            </div>

            <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                <button className={`btn ${subTab === 'dist' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setSubTab('dist')}>🚚 Раскладка</button>
                <button className={`btn ${subTab === 'prebook' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setSubTab('prebook')}>
                    🅿️ Предбронь{prebookUnits > 0 ? ` (${formatNumber(prebookUnits, 0)})` : ''}
                </button>
            </div>

            {subTab === 'prebook' ? (
                prebookGroups.length === 0 ? (
                    <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-muted)' }}>
                        Предброни нет — вся раскладка набирается целыми паллетами (или ничего не разложено).
                    </div>
                ) : (
                    <PrebookView
                        groups={prebookGroups}
                        toppingUpKey={prebookOpKey}
                        shipAsIsKey={prebookOpKey}
                        deletingKey={null}
                        prebookingKey={prebookOpKey}
                        tailTopUpKey={prebookOpKey}
                        trimTailKey={null}
                        palletOpKey={null}
                        acceptanceMarks={prebookAcceptanceMarks}
                        acceptanceLoading={false}
                        preorderWbs={preorderWbs}
                        onTopUp={topUpBox}
                        onShipAsIs={promoteDir}
                        onDelete={(nm, wb, pkg) => setHiddenSkus((s) => new Set(s).add(`${nm}::${wb}::${pkg}`))}
                        onDeleteDirection={hideDir}
                        onCreatePrebooking={promoteDir}
                        onTopUpPrebook={(wb, ffId) => promoteDir('MONOPALLET', wb, ffId)}
                        onTrimTail={(wb, ffId) => hideDir('MONOPALLET', wb, ffId)}
                        onBookPallets={(wb, ffId) => promoteDir('MONOPALLET', wb, ffId)}
                        onReleasePallets={(wb, ffId) => hideDir('MONOPALLET', wb, ffId)}
                        onDraftPallets={(wb, ffId) => promoteDir('MONOPALLET', wb, ffId)}
                    />
                )
            ) : (<>
                <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
                        <button className={`btn btn-sm ${editMode ? 'btn-primary' : 'btn-secondary'}`} onClick={editMode ? () => setEditMode(false) : enterManual}>
                            {editMode ? '✓ Готово с ручной раскладкой' : '✏️ Разложить вручную'}
                        </button>
                        {editMode && (
                            <span style={{ fontSize: 12, color: 'var(--color-warning)' }}>
                                Ручной режим: правь короба (−/+) поверх авто-раскладки. Тронутая строка помечается «вручную», нетронутые остаются авто. Лимит приёмки склада проверяется (⌛ предзаявка · ⛔ закрыт).
                            </span>
                        )}
                        {cellEdits.size > 0 && (
                            <button className="btn btn-sm btn-secondary" style={{ marginLeft: 'auto' }} onClick={resetAllEdits}>
                                ↺ Сбросить все правки ({formatNumber(cellEdits.size, 0)})
                            </button>
                        )}
                    </div>
                    <div style={{ color: 'var(--color-muted)', fontSize: 13 }}>
                        Матрица показывает <b style={{ color: 'var(--color-text)' }}>коробное покрытие</b> потребности (целые коробы под потребность каждого WB-склада), источник — весь свободный остаток ФФ.
                        Колонки складов: 🏬 остаток на WB · 🚚 в сборке/в пути · потребность · что сдаём коробом.
                        В <b style={{ color: 'var(--color-text)' }}>черновик</b> пишутся только целые паллеты; неполные — в 🅿️ Предбронь. Поэтому числа матрицы и черновика могут расходиться.
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
                    </div>
                </div>

                {computing ? (
                    <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-muted)' }}>Считаю раскладку (потребность · приёмка · коробы · паллеты)…</div>
                ) : matrix.totalShip === 0 && !editMode ? (
                    <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-muted)' }}>
                        Нечего разложить: ни у одного артикула нет потребности по WB-складам (или не задана кратность короба).
                    </div>
                ) : (
                    <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                            <thead>
                                <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                                    <th colSpan={4} style={{ position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 2 }} />
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
                                    <td colSpan={3} />
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
                                    <td style={{ padding: '6px 8px', color: 'var(--color-accent)' }} title="Всего штук к отправке">{formatNumber(matrixView.totalShip, 0)} шт</td>
                                    <td style={{ padding: '6px 8px', color: 'var(--color-muted)' }} title="Всего коробов">{formatNumber(matrixView.totalBoxes, 0)} кор</td>
                                    <td style={{ padding: '6px 8px', color: 'var(--color-muted)' }} title="Остаётся на хранении ФФ">{formatNumber(matrixViewOnHold, 0)}</td>
                                </tr>
                            </thead>
                            <tbody>
                                {visibleRows.map((a) => {
                                    const nm = a.nm_id;
                                    const e: EnrichedSku | undefined = enrichMap.get(nm);
                                    const avail = availByBc.get(a.barcode) ?? 0;
                                    const ship = matrix.allocByBc.get(a.barcode) ?? 0;
                                    const stays = Math.max(0, avail - ship);
                                    const cells = matrix.cellByBc.get(a.barcode);
                                    const ppb = nmPpb.get(nm);
                                    const label = a.vendor_code || String(nm) || a.barcode;
                                    const rowBoxes = boxesOf(ship, ppb);
                                    const editRec = cellEdits.get(a.barcode);
                                    const rowEditable = editMode && !!ppb && ppb > 0;
                                    const rowAtCap = ship + (ppb || 0) > avail;
                                    return (
                                        <tr key={a.barcode} style={{ borderBottom: '1px solid var(--color-border)', background: editRec ? 'rgba(255,159,10,0.07)' : ship > 0 ? 'rgba(59,130,246,0.04)' : undefined }}>
                                            <td style={{ padding: '6px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 1 }}>
                                                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                                                    <span style={{ fontWeight: 600 }}>{label}</span>
                                                    {e?.isNew && <span className="badge" style={{ background: 'rgba(168,85,247,0.16)', color: '#a855f7', fontSize: 10, padding: '1px 6px' }}>🆕 новинка</span>}
                                                    {ppb ? <span className="badge badge-secondary" style={{ fontSize: 10, padding: '1px 6px' }}>📦 кратно {formatNumber(ppb, 0)}</span> : <span className="badge" style={{ background: 'rgba(255,159,10,0.14)', color: 'var(--color-warning)', fontSize: 10, padding: '1px 6px' }}>без кратности</span>}
                                                    {editRec && (
                                                        <button type="button" className="badge" title="Сбросить ручные правки строки"
                                                            style={{ background: 'rgba(255,159,10,0.16)', color: 'var(--color-warning)', fontSize: 10, padding: '1px 6px', border: 'none', cursor: 'pointer' }}
                                                            onClick={() => resetRowEdits(a.barcode)}>✏️ вручную · ↺</button>
                                                    )}
                                                </div>
                                                <div style={{ fontSize: 11, color: 'var(--color-muted)' }}>{a.subject ? `${a.subject} · ` : ''}ШК {a.barcode}</div>
                                            </td>
                                            <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(avail, 0)}</td>
                                            <td style={{ padding: '6px 8px', textAlign: 'right', color: e && e.inAssembly > 0 ? 'var(--color-text)' : 'var(--color-dim)' }}>{e && e.inAssembly > 0 ? formatNumber(e.inAssembly, 0) : '·'}</td>
                                            <td style={{ padding: '6px 8px', textAlign: 'right', color: e && e.stocksWb > 0 ? 'var(--color-text)' : 'var(--color-dim)' }}>{e && e.stocksWb > 0 ? formatNumber(e.stocksWb, 0) : '·'}</td>
                                            {wbCols.map((c) => {
                                                const cell = cells?.get(c.name);
                                                const ctx = e?.byWh[c.name];
                                                const ctxBusy = (ctx?.asm ?? 0) + (ctx?.transit ?? 0);
                                                return (
                                                    <NeedMatrixCell
                                                        key={c.name}
                                                        ship={cell ?? null}
                                                        stock={ctx?.stock ?? 0}
                                                        onWay={ctxBusy}
                                                        tint={districtTint(c.district)}
                                                        mark={markFor(nm, c.name, cell?.pkg ?? null)}
                                                        edit={rowEditable && ppb ? {
                                                            boxes: editRec ? (editRec[c.name] ?? 0) : boxesOf(cell?.qty ?? 0, ppb),
                                                            ppb,
                                                            onDelta: (d: number) => editCellBoxes(a.barcode, nm, c.name, d),
                                                            disableInc: rowAtCap,
                                                        } : null}
                                                    />
                                                );
                                            })}
                                            <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600 }}>{ship > 0 ? formatNumber(ship, 0) : '·'}</td>
                                            <td style={{ padding: '6px 8px', textAlign: 'right' }}>{rowBoxes > 0 ? formatNumber(rowBoxes, 0) : '·'}</td>
                                            <td style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--color-muted)' }}>{formatNumber(stays, 0)}</td>
                                        </tr>
                                    );
                                })}
                                {visibleRows.length === 0 && (
                                    <tr>
                                        <td colSpan={wbCols.length + 7} style={{ padding: 24, textAlign: 'center', color: 'var(--color-muted)' }}>
                                            Ничего не найдено по выбранному фильтру
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                            <tfoot>
                                <tr style={{ borderTop: '2px solid var(--color-border)', fontWeight: 600 }}>
                                    <td style={{ padding: '8px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)' }}>Отправить, шт</td>
                                    <td colSpan={3} />
                                    {wbCols.map((c) => (
                                        <td key={c.name} style={{ padding: '8px 8px', textAlign: 'right', color: 'var(--color-accent)' }}>{formatNumber(matrixView.shipByWh.get(c.name) ?? 0, 0)}</td>
                                    ))}
                                    <td style={{ padding: '8px 8px', textAlign: 'right' }}>{formatNumber(matrixView.totalShip, 0)}</td>
                                    <td colSpan={2} />
                                </tr>
                                <tr style={{ color: 'var(--color-muted)' }}>
                                    <td style={{ padding: '6px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)' }}>📦 Коробов</td>
                                    <td colSpan={3} />
                                    {wbCols.map((c) => (
                                        <td key={c.name} style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(matrixView.boxesByWh.get(c.name) ?? 0, 0)}</td>
                                    ))}
                                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(matrixView.totalBoxes, 0)}</td>
                                    <td colSpan={2} />
                                </tr>
                                <tr style={{ color: 'var(--color-muted)' }}>
                                    <td style={{ padding: '6px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)' }}>🚚 Паллет</td>
                                    <td colSpan={3} />
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
