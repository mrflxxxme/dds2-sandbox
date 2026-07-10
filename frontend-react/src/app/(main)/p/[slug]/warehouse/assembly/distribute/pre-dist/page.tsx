'use client';
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import { parseBoxSize, palletsForLines, maxPalletHeightCm, type PalletLine } from '@/lib/utils/boxPallet';
import { DISTRICT_ORDER, DISTRICT_LABELS, DISTRICT_COLORS } from '@/lib/constants/localization';
import { Toast } from '@/components';
import {
    applyAcceptanceSplits,
    applyCellBoxDelta,
    buildPinnedRows,
    buildPoolSkus,
    buildTopUpRows,
    enrichPoolRows,
    finalizePoolDistribution,
    planBoxTopUp,
    rowsToPreDistRows,
    splitByKratnost,
    type AcceptanceSplitMap,
    type CellEdits,
    type EnrichedSku,
    type PinnedPkgOf,
    type PoolDistInput,
} from '@/lib/assembly/preDistribution';
import { buildPrebookGroups } from '@/lib/assembly/buildPrebookGroups';
import { applyAcceptanceRedistToPrebook } from '@/lib/assembly/prebookRedistribute';
import NeedMatrixCell, { type CellMark } from '../components/NeedMatrixCell';
import AcceptanceBanner, { type AcceptanceSummary } from '../components/AcceptanceBanner';
import PrebookView, { type PrebookAcceptanceMark } from '../components/PrebookView';
import DraftPreview from '../components/DraftPreview';
import { seedNewcomerWholeBoxes, type SeedAnchor } from '@/lib/assembly/coldStartSeed';
import type {
    AcceptanceCheckPerItem,
    AcceptanceFlags,
    AssemblyDraftRow,
    PackageType,
    PreDistVehiclePool,
    StockNeedResponse,
    Warehouse,
} from '@/types/api';

/** Сколько коробов из штук при кратности `ppb`. */
const boxesOf = (qty: number, ppb: number | null | undefined): number =>
    ppb && ppb > 0 ? Math.ceil(qty / ppb) : 0;

const districtRank = (d: string): number => {
    const i = (DISTRICT_ORDER as readonly string[]).indexOf(d);
    return i < 0 ? DISTRICT_ORDER.length : i;
};

/** Полупрозрачная тонировка фона колонки по округу (как в «Потребность по складам»). */
const districtTint = (d: string): string | undefined => {
    const c = DISTRICT_COLORS[d];
    return c ? `color-mix(in srgb, ${c} 6%, transparent)` : undefined;
};

/** Ключ сортировки таблицы раскладки. Фиксированные колонки + `wb:<склад>` (сколько сдаём туда). */
type SortKey = 'label' | 'avail' | 'inAssembly' | 'stocksWb' | 'ship' | 'boxes' | 'stays' | `wb:${string}`;

/** Агрегаты одной раскладки (позиции · ячейки матрицы · итоги по складам · коробы/паллеты).
 *  Считается дважды: для коммита (целые паллеты → Заявки) и для матрицы (коробное покрытие). */
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
    /** Паллеты по группе `"{wb-склад}::{package_type}"` — для передачи в create (бэк-группировка). */
    palletsByGroup: Map<string, number>;
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
    const palletsByGroup = new Map<string, number>();
    let totalPallets = 0;
    for (const [key, g] of linesByWhPkg) {
        const p = palletsForLines(g.lines, maxPalletHeightCm(g.wh), g.pkg === 'BOX' ? 'box' : 'mono', palletOverrides).pallets;
        palletsByWh.set(g.wh, (palletsByWh.get(g.wh) ?? 0) + p);
        palletsByGroup.set(key, p);  // key = `${wh}::${pkg}` — совпадает с бэк-группировкой
        totalPallets += p;
    }
    const groupKeys = new Set(submitRows.map(r => `${r.wb_warehouse_name}::${r.package_type}`));
    const totalShip = submitRows.reduce((s, r) => s + r.qty, 0);
    const totalBoxes = submitRows.reduce((s, r) => s + boxesOf(r.qty, nmPpb.get(nmByBc.get(r.barcode) ?? 0)), 0);
    return { submitRows, allocByBc, cellByBc, requestCount: groupKeys.size, totalShip, totalBoxes, shipByWh, boxesByWh, palletsByWh, palletsByGroup, totalPallets };
}

/** Экран «Распределить машину» — открывается из вкладки «🚚 Предраспределение» (?vehicle=<id>).
 *  Полноэкранная матрица как «Потребность по складам», но источник = остатки машины (пул):
 *  per-WB-склад остаток 🏬 / в сборке-в пути 🚚 / потребность + что отправляем (коробá),
 *  бейджи «новинка» / «кратно N», счётчик коробов и паллет. Заявки создаются со статусом
 *  «Предраспределение» (без фейкового стока); при разгрузке станут обычными сборками. */
export default function PreDistVehiclePage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const slug = params.slug as string;
    const vehicleId = Number(searchParams.get('vehicle')) || null;

    const [pool, setPool] = useState<PreDistVehiclePool | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    // Справочники движка (зеркало PreDistributionView / distribute page).
    const [stockNeed, setStockNeed] = useState<StockNeedResponse | null>(null);
    const [newcomerSet, setNewcomerSet] = useState<Set<number>>(new Set());
    const [anchors, setAnchors] = useState<SeedAnchor[]>([]);
    const [nmPpb, setNmPpb] = useState<Map<number, number | null>>(new Map());
    const [nmBoxSize, setNmBoxSize] = useState<Map<number, string | null>>(new Map());
    const [palletOverrides, setPalletOverrides] = useState<Record<string, number>>({});
    const [geomReady, setGeomReady] = useState(false);

    // Раскладка. `distRows` — целые паллеты (коммит/Заявки); `distRowsBox` — коробное
    // покрытие для матрицы (частичные паллеты ок). Две независимые раскладки одного пула.
    const [distRows, setDistRows] = useState<AssemblyDraftRow[] | null>(null);
    const [distRowsBox, setDistRowsBox] = useState<AssemblyDraftRow[]>([]);
    // Предбронь машины (под-паллетные хвосты pallet-mode): короб=«Дозабить» / моно=«Предзаявка».
    const [prebookRows, setPrebookRows] = useState<AssemblyDraftRow[]>([]);
    const [subTab, setSubTab] = useState<'dist' | 'preview' | 'prebook'>('dist');
    // Интерактив предброни — клиентские решения (сбрасываются при пересчёте раскладки).
    // Машина = один источник (ФФ разгрузки) + всё уходит в PRE_DISTRIBUTED-заявки, поэтому
    // «положительные» действия (Оставить так/Дозабить/Предзаявка) = ПЕРЕВОД направления в
    // отгрузку, «отрицательные» (Удалить/Освободить) = скрыть (остаётся на машине).
    const [promotedDirs, setPromotedDirs] = useState<Set<string>>(new Set());  // → в отгрузку
    const [hiddenDirs, setHiddenDirs] = useState<Set<string>>(new Set());       // остаётся на машине
    const [hiddenSkus, setHiddenSkus] = useState<Set<string>>(new Set());       // убран один ШК
    // Ручной дозабор из остатка машины: barcode → { wb → добранные штуки (целыми коробами) }.
    // Вливается в раскладку как extraRows → неполная паллета набирается и уходит в отгрузку.
    const [manualTopUp, setManualTopUp] = useState<Map<string, Record<string, number>>>(new Map());
    // Ручное редактирование ячеек матрицы «Раскладка»: barcode → { WB-склад → коробов }.
    // Абсолютный пин (не дельта). Баркод с любым пином полностью управляется вручную
    // (исключается из авто-раскладки), проходит приёмку и общий движок паллет/предброни.
    const [cellEdits, setCellEdits] = useState<CellEdits>(new Map());
    // Режим редактирования матрицы (тумблер «✏️») — показывает +/− степперы в ячейках.
    const [editMode, setEditMode] = useState(false);
    // Кэш приёмки WB по сигнатуре skus (приёмка НЕ зависит от дозабора) — клик «Дозабить»
    // (меняет только manualTopUp) пересчитывает раскладку БЕЗ повторного сетевого запроса.
    const acceptanceCacheRef = useRef<{ sig: string; splitMap: AcceptanceSplitMap | null; summary: AcceptanceSummary | null; accByNm: Map<number, AcceptanceCheckPerItem> } | null>(null);
    // Кэш приёмки ЗАСЕВА новинок (отдельный запрос: засев не в autoSkus) — чтобы уводить
    // закрытые склады-якоря на открытые того же округа, не дёргая сеть на каждый пересчёт.
    const seedAccCacheRef = useRef<{ sig: string; byKey: Map<string, AcceptanceCheckPerItem> } | null>(null);
    const [prebookOpKey, setPrebookOpKey] = useState<string | null>(null);
    const [acceptanceByNm, setAcceptanceByNm] = useState<Map<number, AcceptanceCheckPerItem>>(new Map());
    const [preorderWbs, setPreorderWbs] = useState<Set<string>>(new Set());
    const [distComputing, setDistComputing] = useState(false);
    const [acceptanceNote, setAcceptanceNote] = useState<AcceptanceSummary | null>(null);
    const [submitting, setSubmitting] = useState(false);

    // Сортировка таблицы раскладки (клик по заголовку). Дефолт — Σ отпр. по убыванию
    // (прежнее поведение: сначала то, что отправляем).
    const [sortKey, setSortKey] = useState<SortKey>('ship');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
    const toggleSort = useCallback((key: SortKey) => {
        setSortKey(prev => {
            if (prev === key) { setSortDir(d => (d === 'asc' ? 'desc' : 'asc')); return prev; }
            setSortDir(key === 'label' ? 'asc' : 'desc');  // текст — по возрастанию, числа — по убыванию
            return key;
        });
    }, []);
    const sortArrow = (key: SortKey): string => (sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '');
    const sortableTh: CSSProperties = { cursor: 'pointer', userSelect: 'none' };

    const showToast = useCallback((message: string, type: 'success' | 'error') => setToast({ message, type }), []);
    const backToList = useCallback(
        () => router.push(`/p/${slug}/warehouse/assembly/distribute?tab=pre-dist`),
        [router, slug],
    );

    // ─── Загрузка пула + всех справочников разом ───────────────────────────
    useEffect(() => {
        if (!vehicleId) { setLoading(false); return; }
        const controller = new AbortController();
        (async () => {
            setLoading(true);
            setError(null);
            try {
                const [poolData, need, cold, boxMult, palletOv, preorder] = await Promise.all([
                    api.getPreDistVehiclePool(vehicleId),
                    // Форма спроса ТА ЖЕ, что у «Черновика сборки»: localizationOptimized=true
                    // (локализованное распределение по округам). НО onlyAvailable=false — машинный
                    // кап нельзя стекать поверх серверного ФФ-капа (источник = пул машины, не ФФ);
                    // кап применяется клиентски в buildDraftRows (min(pool, need)). См. план, часть A.
                    (api.getStockNeed(14, 14, 'actual', true, false, 0) as Promise<StockNeedResponse | null>).catch(() => null),
                    api.getColdStartTable(14).catch(() => null),  // окно 14д — как getStockNeed(14,14) и «Потребность»
                    api.getBoxMultiplicity().catch(() => null),
                    api.getPalletBoxesBySize().catch(() => ({} as Record<string, number>)),
                    api.getPreorderAllowedWarehouses().catch(() => [] as string[]),  // whitelist предзаявки (⌛)
                ]);
                if (controller.signal.aborted) return;
                setPool(poolData);
                setStockNeed(need);
                setPreorderWbs(new Set(preorder));
                // Новый пул машины → сбрасываем клиентские решения предброни и ручной дозабор
                // (пересчёт раскладки их больше НЕ трогает — дозабор должен переживать recompute).
                setPromotedDirs(new Set());
                setHiddenDirs(new Set());
                setHiddenSkus(new Set());
                setManualTopUp(new Map());
                setCellEdits(new Map());             // новая машина → сбросить ручные правки ячеек
                acceptanceCacheRef.current = null;   // новая машина → приёмку перепроверить
                seedAccCacheRef.current = null;       // новая машина → приёмку засева перепроверить

                const ncs = new Set<number>();
                for (const r of cold?.rows ?? []) if (r.is_newcomer) ncs.add(r.nm_id);
                // Новинки САМОЙ машины (нет ФФ-остатка → cold-start-справочник их не видит,
                // требует rf_qty>0). Засеваем их с остатка машины по главным складам округов.
                for (const pr of poolData.rows) {
                    const nm = pr.article_wb ? Number(pr.article_wb) : 0;
                    if (nm && pr.is_newcomer) ncs.add(nm);
                }
                setNewcomerSet(ncs);
                // district — для гарантии СЗФО в seedNewcomerWholeBoxes (≥4 коробов → 1 короб СЗФО).
                setAnchors((cold?.main_warehouses ?? []).map(w => ({ warehouse: w.warehouse, share_pct: w.share_pct, district: w.district_key })));

                // Кратность/габарит короба — приоритет: pool-row машины → справочник.
                // Машина ещё в пути → её кратность НЕ попала в справочник принятых приёмок
                // (getBoxMultiplicity), поэтому кратность прямо со строк cost_order (pool.box_qty)
                // старше и НЕ гейтится use_box_multiplicity справочника (тот флаг про приёмку).
                const ppbMap = new Map<number, number | null>();
                const sizeMap = new Map<number, string | null>();
                // 1) Справочник — fallback (принятые приёмки, per-склад/override).
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
                // 2) Пул машины — приоритетнее справочника. Несколько ШК на один nm →
                //    берём строку с наибольшим доступным остатком (детерминированно).
                const poolBestAvail = new Map<number, number>();
                for (const pr of poolData.rows) {
                    const nm = pr.article_wb ? Number(pr.article_wb) : 0;
                    if (!nm || !pr.box_qty || pr.box_qty <= 0) continue;
                    const avail = Math.max(0, Number(pr.available_qty) || 0);
                    if (avail > (poolBestAvail.get(nm) ?? -1)) {
                        poolBestAvail.set(nm, avail);
                        ppbMap.set(nm, pr.box_qty);
                        if (pr.box_size && parseBoxSize(pr.box_size)) sizeMap.set(nm, pr.box_size);
                    }
                }
                setNmPpb(ppbMap);
                setNmBoxSize(sizeMap);
                setPalletOverrides(palletOv || {});
                setGeomReady(true);
            } catch (e) {
                if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка загрузки машины');
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        })();
        return () => controller.abort();
    }, [vehicleId]);

    const vehicle = pool?.vehicle ?? null;

    // ─── Авто-раскладка: пул × потребность → приёмка → целые коробы/паллеты ──
    const computeDistribution = useCallback(async (poolData: PreDistVehiclePool, signal: AbortSignal) => {
        const targetWh = poolData.vehicle.target_warehouse_id;
        if (targetWh == null) { setDistRows([]); setAcceptanceNote(null); return; }
        setDistComputing(true);
        try {
            const distInput: PoolDistInput = {
                poolRows: poolData.rows,
                targetWarehouseId: targetWh,
                stockNeed,
                nmPpb,
                nmBoxSize,
                palletOverrides,
            };
            const { skus: allSkus } = buildPoolSkus(distInput);
            // Россыпь запрещена (как в «Черновике»): SKU без кратности короба округлять нечем
            // → НЕ отгружаем; остаются на машине, показываются баннером «без кратности».
            const { shippable: skus } = splitByKratnost(allSkus, (nm) => nmPpb.get(nm));
            if (skus.length === 0 && cellEdits.size === 0 && anchors.length === 0) {
                if (!signal.aborted) { setDistRows([]); setPrebookRows([]); setAcceptanceNote(null); }
                return;
            }
            // Режим правки = ПОЛНОСТЬЮ ручной: авто-раскладка ВЫКЛючена (чистый лист), матрицу
            // формируют ТОЛЬКО ручные пины (`cellEdits`). Вне режима правки — авто-раскладка
            // «по потребности», а пины дремлют (не влияют, но сохраняются до включения правки).
            // Приёмку по пинам проверяем отдельными item'ами (nm→pkg-флаги для pinnedPkgOf/меток).
            // Ручной режим активен, пока (а) включена правка ИЛИ (б) есть хоть один пин —
            // чтобы «Готово» (спрятать степперы) НЕ откатывало ручную раскладку в авто. Возврат
            // к авто — только «↺ Сбросить все правки» (очищает cellEdits).
            const manualMode = editMode || cellEdits.size > 0;
            const activeEdits: CellEdits = manualMode ? cellEdits : new Map();
            const nmByBarcodePool = new Map<string, number>();
            for (const pr of poolData.rows) nmByBarcodePool.set(pr.barcode, pr.article_wb ? Number(pr.article_wb) : 0);
            const autoSkus = manualMode ? [] : skus.filter(s => !activeEdits.has(s.barcode));
            const pinnedItems: { nm_id: number; barcode: string; distribution: Record<string, number> }[] = [];
            for (const [bc, whBoxes] of activeEdits) {
                const nm = nmByBarcodePool.get(bc) ?? 0;
                const ppb = nmPpb.get(nm) || 0;
                if (!nm || ppb <= 0) continue;
                const dist: Record<string, number> = {};
                for (const [wb, boxes] of Object.entries(whBoxes)) {
                    if ((boxes || 0) > 0) dist[wb] = Math.floor(boxes) * ppb;
                }
                if (Object.keys(dist).length > 0) pinnedItems.push({ nm_id: nm, barcode: bc, distribution: dist });
            }
            // Приёмка WB зависит ТОЛЬКО от skus (не от дозабора) → кэшируем по сигнатуре: клик
            // «Дозабить» (меняет лишь manualTopUp) пересчитывает раскладку без запроса к сети.
            // Сигнатура приёмки: авто-скусы по target + пины ТОЛЬКО по набору складов (не по
            // числу коробов) — правка кратности того же склада переиспользует кэш (без сети),
            // приёмка зависит от набора складов, а не количества.
            const accSig = JSON.stringify([
                ...autoSkus.map(s => [s.nm_id, s.barcode, s.target]),
                ...pinnedItems.map(p => [p.nm_id, p.barcode, Object.keys(p.distribution).sort()]),
            ]);
            let splitMap: AcceptanceSplitMap | null = null;
            let summary: AcceptanceSummary | null = null;
            let accByNm = new Map<number, AcceptanceCheckPerItem>();  // для меток приёмки предброни
            const accCache = acceptanceCacheRef.current;
            if (accCache && accCache.sig === accSig) {
                splitMap = accCache.splitMap; summary = accCache.summary; accByNm = accCache.accByNm;
            } else if (autoSkus.length === 0 && pinnedItems.length === 0) {
                // Ничего проверять (пустой ручной лист) — не дёргаем сеть пустым запросом.
                splitMap = new Map();
                acceptanceCacheRef.current = { sig: accSig, splitMap, summary, accByNm };
            } else {
                try {
                    const resp = await api.checkWbAcceptance({
                        items: [
                            ...autoSkus.map(s => ({ nm_id: s.nm_id, barcode: s.barcode, distribution: s.target })),
                            ...pinnedItems.map(p => ({ nm_id: p.nm_id, barcode: p.barcode, distribution: p.distribution })),
                        ],
                    });
                    splitMap = new Map();
                    let moved = 0, dropped = 0, monoCount = 0, splitCount = 0;
                    for (const it of resp.items) {
                        const splits = it.splits?.length
                            ? it.splits.map(sp => ({ package_type: sp.package_type, distribution: sp.distribution }))
                            : [{ package_type: it.package_type, distribution: it.distribution }];
                        splitMap.set(`${it.nm_id}::${it.barcode}`, splits);
                        accByNm.set(it.nm_id, it);
                        if (splits.some(s => s.package_type === 'MONOPALLET')) monoCount++;
                        if (splits.length > 1) splitCount++;
                    }
                    for (const m of resp.moves ?? []) { if (m.to_warehouse) moved += m.quantity; else dropped += m.quantity; }
                    summary = {
                        checked: true, failed: false, skuCount: resp.items.length,
                        monoCount, splitCount, movedQty: moved, droppedQty: dropped,
                        checkedAt: resp.checked_at ?? null,
                    };
                } catch {
                    summary = { checked: false, failed: true, skuCount: 0, monoCount: 0, splitCount: 0, movedQty: 0, droppedQty: 0, checkedAt: null };
                }
                acceptanceCacheRef.current = { sig: accSig, splitMap, summary, accByNm };
            }
            const effective = applyAcceptanceSplits(autoSkus, splitMap);

            // Пин-строки ручных правок: тип упаковки — из приёмки (короб > моно > сейф),
            // источник = ФФ разгрузки, Σ капится остатком машины. Вливаются в extraRows →
            // паллеты/предбронь считает общий движок (как авто): неполная паллета уйдёт в
            // 🅿️ Предбронь, где решается дозабор из остатка машины либо предзаявка (⌛).
            const pinnedPkgOf: PinnedPkgOf = (nm, wb) => {
                const f = accByNm.get(nm)?.availability?.[wb];
                if (!f) return 'BOX';
                return f.can_box ? 'BOX' : f.can_monopallet ? 'MONOPALLET' : f.can_supersafe ? 'SUPERSAFE' : 'BOX';
            };
            const pinnedRows = buildPinnedRows(activeEdits, poolData.rows, targetWh, nmPpb, pinnedPkgOf);

            // Раскладка ВСЕГДА строго целыми паллетами (как «Черновик»): под-паллетные хвосты
            // уходят в ПРЕДБРОНЬ (там — дозабор из остатка машины), россыпь запрещена. Засев
            // новинок вливается в нормализацию как extraRows — паллетизируется вместе с
            // потребностью, а его хвост < паллеты тоже уходит в предбронь (не частичной паллетой).

            // Предварительная box-раскладка потребности — для ПОКРЫТИЯ засева (сколько уже
            // уедет по потребности per баркод/склад, чтобы не сеять поверх того же склада).
            const needBoxRows = finalizePoolDistribution(effective, distInput, false).rows;
            const seededRows: AssemblyDraftRow[] = [];
            if (!manualMode && anchors.length > 0) {
                const shippedByBc = new Map<string, number>();
                const needShipByBcWh = new Map<string, Map<string, number>>();
                for (const r of rowsToPreDistRows(needBoxRows)) {
                    shippedByBc.set(r.barcode, (shippedByBc.get(r.barcode) ?? 0) + r.qty);
                    const wh = needShipByBcWh.get(r.barcode) ?? new Map<string, number>();
                    wh.set(r.wb_warehouse_name, (wh.get(r.wb_warehouse_name) ?? 0) + r.qty);
                    needShipByBcWh.set(r.barcode, wh);
                }
                // Покрытие per nm per WB-склад (остаток WB + в сборке + в пути + уже по потребности).
                const enrich = enrichPoolRows(poolData.rows, stockNeed, newcomerSet);
                for (const pr of poolData.rows) {
                    const nm = pr.article_wb ? Number(pr.article_wb) : 0;
                    if (!nm || !newcomerSet.has(nm)) continue;
                    const avail = Math.max(0, Math.floor(Number(pr.available_qty) || 0));
                    const remaining = avail - (shippedByBc.get(pr.barcode) ?? 0);
                    const byWh = enrich.get(nm)?.byWh;
                    const needWh = needShipByBcWh.get(pr.barcode);
                    const covAnchors = anchors.map(a => {
                        const c = byWh?.[a.warehouse];
                        const existing = (c ? c.stock + c.asm + c.transit : 0) + (needWh?.get(a.warehouse) ?? 0);
                        return { warehouse: a.warehouse, share_pct: a.share_pct, existing, district: a.district };
                    });
                    const seeded = seedNewcomerWholeBoxes(remaining, nmPpb.get(nm), covAnchors);
                    const tot = Object.values(seeded).reduce((s, v) => s + v, 0);
                    if (tot > 0) {
                        seededRows.push({
                            nm_id: nm, barcode: pr.barcode, vendor_code: pr.article_seller || String(nm),
                            src: { [String(targetWh)]: tot }, tgt: seeded, package_type: 'BOX',
                        });
                    }
                }
            }
            // Засев новинок НЕ проходил проверку приёмки (новинки не в autoSkus) → мог лечь на
            // ЗАКРЫТЫЙ склад-якорь округа, куда сдать нельзя. Прогоняем засев через приёмку и
            // уводим закрытые склады на ОТКРЫТЫЕ (backend: консолидация в Центр/Электросталь —
            // «WB везёт этот регион из Москвы»), ровно как в черновике (`applyAcceptanceRedistToPrebook`).
            // ДО паллетизации — чтобы закрытый склад не попал ни в Заявки, ни в Предбронь.
            // Приёмка не ответила → засев оставляем как есть (товар не теряем).
            let seededEffective = seededRows;
            if (seededRows.length > 0) {
                const bySku = new Map<string, { nm_id: number; barcode: string; distribution: Record<string, number> }>();
                for (const r of seededRows) {
                    const k = `${r.nm_id}::${r.barcode}`;
                    const e = bySku.get(k) ?? { nm_id: r.nm_id, barcode: r.barcode, distribution: {} };
                    for (const [wb, q] of Object.entries(r.tgt)) e.distribution[wb] = (e.distribution[wb] || 0) + (q || 0);
                    bySku.set(k, e);
                }
                const seedItems = [...bySku.values()];
                const seedSig = JSON.stringify(seedItems.map(i => [i.nm_id, i.barcode, i.distribution]));
                let seedByKey: Map<string, AcceptanceCheckPerItem> | null = null;
                const seedCache = seedAccCacheRef.current;
                if (seedCache && seedCache.sig === seedSig) {
                    seedByKey = seedCache.byKey;
                    for (const it of seedByKey.values()) accByNm.set(it.nm_id, it);
                } else {
                    try {
                        const seedResp = await api.checkWbAcceptance({ items: seedItems });
                        seedByKey = new Map(seedResp.items.map(it => [`${it.nm_id}::${it.barcode}`, it]));
                        for (const it of seedResp.items) accByNm.set(it.nm_id, it);  // метки приёмки новинок в матрице
                        seedAccCacheRef.current = { sig: seedSig, byKey: seedByKey };
                    } catch {
                        seedByKey = null;  // приёмка не ответила — засев оставляем как есть (товар не теряем)
                    }
                }
                if (seedByKey) seededEffective = applyAcceptanceRedistToPrebook(seededRows, seedByKey).rows;
            }
            // Ручной дозабор из остатка машины (кнопка «Дозабить» предброни) — целые коробы.
            // Кап по ОСТАТКУ баркода (available − занятое потребностью/засевом) не даёт гонке
            // двойного клика пере-подписать баркод сверх наличия (Σsrc баркода ≤ available).
            const reservedByBc = new Map<string, number>();
            for (const r of [...needBoxRows, ...seededEffective, ...pinnedRows]) {
                const s = Object.values(r.src).reduce((a, v) => a + (v || 0), 0);
                reservedByBc.set(r.barcode, (reservedByBc.get(r.barcode) ?? 0) + s);
            }
            const topUpRows = manualMode ? [] : buildTopUpRows(manualTopUp, poolData.rows, targetWh, nmPpb, reservedByBc);
            const extra = [...seededEffective, ...topUpRows, ...pinnedRows];
            // ДВЕ независимые раскладки одного пула (по решению юзера):
            //  • whole — строго целые паллеты → ЗАЯВКИ (коммит) + под-паллетные хвосты в ПРЕДБРОНЬ.
            //  • box   — целые коробы под потребность (частичные паллеты ок) → МАТРИЦА «Раскладка»
            //            (коробное покрытие: Краснодар/Шушары с потребностью видят короб).
            // Числа матрицы и Заявок могут расходиться — матрица шлёт короб, а в Заявку он попадёт
            // только целой паллетой либо после решения в Предброни (Дозабить/Оставить так/На ФФ).
            // minOneBoxPerWh=true: каждый склад с потребностью получает ≥1 целый короб
            // (перебор над потребностью, кап машинным стоком) — по требованию юзера, как
            // speed-локализация «но не менее одной коробки на склад». Действует на обе
            // раскладки: матрица показывает короб, в whole неполная паллета уйдёт в Предбронь.
            const whole = finalizePoolDistribution(effective, distInput, true, extra, true);
            const box = finalizePoolDistribution(effective, distInput, false, extra, true);
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
    }, [stockNeed, nmPpb, nmBoxSize, palletOverrides, manualTopUp, cellEdits, editMode, newcomerSet, anchors, showToast]);

    useEffect(() => {
        if (!pool || !geomReady) return;
        const controller = new AbortController();
        computeDistribution(pool, controller.signal);
        return () => controller.abort();
    }, [pool, geomReady, computeDistribution]);

    // ─── Предбронь: атомарные направления (pkg×WB×ФФ) + решения юзера ───────
    const targetWh = pool?.vehicle.target_warehouse_id ?? null;
    const dirKeyOf = (r: AssemblyDraftRow): string =>
        `${r.package_type ?? 'BOX'}::${Object.keys(r.tgt)[0] ?? ''}::${Object.keys(r.src)[0] ?? ''}`;
    // Разбиваем каждую строку предброни на атомарные (один WB-склад): источник машины
    // один (ФФ разгрузки), поэтому src порции = её же qty. Скрытые ШК отсеиваем.
    const atomicPrebook = useMemo<AssemblyDraftRow[]>(() => {
        const out: AssemblyDraftRow[] = [];
        for (const r of prebookRows) {
            const ff = Object.keys(r.src)[0] ?? String(targetWh ?? '');
            const pkg = r.package_type ?? 'BOX';
            for (const [wb, q] of Object.entries(r.tgt)) {
                if ((q || 0) <= 0 || hiddenSkus.has(`${r.nm_id}::${wb}::${pkg}`)) continue;
                out.push({ nm_id: r.nm_id, barcode: r.barcode, vendor_code: r.vendor_code, src: { [ff]: q }, tgt: { [wb]: q }, package_type: pkg });
            }
        }
        return out;
    }, [prebookRows, hiddenSkus, targetWh]);
    const effPrebook = useMemo(
        () => atomicPrebook.filter(r => { const k = dirKeyOf(r); return !promotedDirs.has(k) && !hiddenDirs.has(k); }),
        [atomicPrebook, promotedDirs, hiddenDirs],
    );
    const promotedRows = useMemo(() => atomicPrebook.filter(r => promotedDirs.has(dirKeyOf(r))), [atomicPrebook, promotedDirs]);
    // Отгрузка = авто-раскладка + направления предброни, переведённые юзером в отгрузку.
    const shipRows = useMemo(() => [...(distRows ?? []), ...promotedRows], [distRows, promotedRows]);
    const prebookUnits = useMemo(() => effPrebook.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0), [effPrebook]);

    // barcode → nm_id (для геометрии коробов/паллет по строкам отправки).
    const nmByBc = useMemo(() => {
        const m = new Map<string, number>();
        for (const r of pool?.rows ?? []) m.set(r.barcode, r.article_wb ? Number(r.article_wb) : 0);
        return m;
    }, [pool]);
    // barcode → доступный остаток машины (шт) — кап ручных правок ячеек.
    const availByBc = useMemo(() => {
        const m = new Map<string, number>();
        for (const r of pool?.rows ?? []) m.set(r.barcode, Math.max(0, Math.floor(Number(r.available_qty) || 0)));
        return m;
    }, [pool]);

    // ─── Производные: коммит (целые паллеты → Заявки) vs матрица (коробное покрытие) ──
    // Две независимые раскладки. `commit` = целые паллеты (Заявки/создание/KPI), `matrix` =
    // коробное покрытие (ячейки/колонки/сортировка/итоги «Сдаём»). Числа могут расходиться.
    const commit = useMemo(() => buildDistAgg(shipRows, nmByBc, nmPpb, nmBoxSize, palletOverrides), [shipRows, nmByBc, nmPpb, nmBoxSize, palletOverrides]);
    const matrix = useMemo(() => buildDistAgg(distRowsBox, nmByBc, nmPpb, nmBoxSize, palletOverrides), [distRowsBox, nmByBc, nmPpb, nmBoxSize, palletOverrides]);
    const submitRows = commit.submitRows;

    // ─── Пропсы для реального «Предпросмотра заявок» (DraftPreview в машинном режиме) ──
    const previewWarehouses = useMemo<Warehouse[]>(() => {
        const tw = pool?.vehicle.target_warehouse_id;
        return tw != null ? [{ id: tw, name: pool?.vehicle.target_warehouse_name || `ФФ ${tw}` } as Warehouse] : [];
    }, [pool]);
    const nmMeta = useMemo(() => {
        const m = new Map<number, { subject: string; brand: string }>();
        for (const pr of pool?.rows ?? []) {
            const nm = pr.article_wb ? Number(pr.article_wb) : 0;
            if (nm) m.set(nm, { subject: pr.name || '', brand: pr.brand || '' });
        }
        return m;
    }, [pool]);
    // «Из предброни»: направления, которые юзер промоутнул из предброни в отгрузку — бейдж в раскладке.
    const prebookPromotedOrigin = useMemo(() => {
        const s = new Set<string>();
        for (const r of promotedRows) for (const wb of Object.keys(r.tgt)) s.add(`${r.nm_id}::${wb}`);
        return s;
    }, [promotedRows]);

    const enrichMap = useMemo(
        () => enrichPoolRows(pool?.rows ?? [], stockNeed, newcomerSet),
        [pool, stockNeed, newcomerSet],
    );

    // Колонки WB-складов матрицы (куда шлём короба ИЛИ где есть потребность) + округа.
    // Источник — matrix (коробное покрытие), чтобы box-only склады (Краснодар/Шушары) были колонками.
    const wbCols = useMemo(() => {
        const distByWh = new Map<string, string>();
        for (const w of stockNeed?.warehouses ?? []) if (w.name) distByWh.set(w.name, w.district_key || 'unknown');
        const names = new Set<string>();
        for (const r of matrix.submitRows) names.add(r.wb_warehouse_name);
        for (const e of enrichMap.values()) {
            for (const [wh, c] of Object.entries(e.byWh)) if (c.need > 0) names.add(wh);
        }
        const arr = [...names].map(name => ({ name, district: distByWh.get(name) || 'unknown' }));
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

    // Остаётся на ФФ по коммиту (целые паллеты) — для KPI «На хранение».
    const onHoldQty = useMemo(
        () => (pool?.rows ?? []).reduce((s, r) => s + Math.max(0, (Number(r.available_qty) || 0) - (commit.allocByBc.get(r.barcode) ?? 0)), 0),
        [pool, commit],
    );
    // Остаётся на ФФ по матрице (коробное покрытие) — для строки итогов «Сдаём».
    const matrixOnHold = useMemo(
        () => (pool?.rows ?? []).reduce((s, r) => s + Math.max(0, (Number(r.available_qty) || 0) - (matrix.allocByBc.get(r.barcode) ?? 0)), 0),
        [pool, matrix],
    );

    // Артикулы машины БЕЗ кратности короба: округлять до целого короба нечем → россыпь
    // запрещена, их НЕ отгружаем (остаются на машине) и показываем баннером — как «Черновик».
    const noKratnostArticles = useMemo(() => {
        const byNm = new Map<number, { vendor: string; qty: number }>();
        for (const pr of pool?.rows ?? []) {
            const nm = pr.article_wb ? Number(pr.article_wb) : 0;
            if (!nm || (nmPpb.get(nm) || 0) > 0) continue;
            const avail = Math.max(0, Math.floor(Number(pr.available_qty) || 0));
            if (avail <= 0) continue;
            const cur = byNm.get(nm) ?? { vendor: pr.article_seller || String(nm), qty: 0 };
            cur.qty += avail;
            byNm.set(nm, cur);
        }
        return [...byNm.entries()].map(([nm, v]) => ({ nm_id: nm, vendor: v.vendor, qty: v.qty })).sort((a, b) => b.qty - a.qty);
    }, [pool, nmPpb]);

    // ─── Предбронь машины: карточки направлений через ТОТ ЖЕ движок, что и раздел ──
    // Кандидаты дозабора = ОСТАТОК машины per nm (Σ по баркодам) на ФФ разгрузки; движок сам
    // вычтет уже разложенное (inUse) → свободные целые коробы = onHold → кнопка «Дозабить».
    const prebookArticles = useMemo(() => {
        if (targetWh == null) return [];
        const byNm = new Map<number, { vendor: string; avail: number }>();
        for (const pr of pool?.rows ?? []) {
            const nm = pr.article_wb ? Number(pr.article_wb) : 0;
            if (!nm) continue;
            const cur = byNm.get(nm) ?? { vendor: pr.article_seller || String(nm), avail: 0 };
            cur.avail += Math.max(0, Math.floor(Number(pr.available_qty) || 0));
            byNm.set(nm, cur);
        }
        return [...byNm.entries()].map(([nm, v]) => ({ nm_id: nm, vendor_code: v.vendor, rfStocks: { [targetWh]: v.avail } }));
    }, [pool, targetWh]);

    const prebookGroups = useMemo(() => {
        if (targetWh == null) return [];
        return buildPrebookGroups({
            prebook: effPrebook,
            usedRows: shipRows,
            articles: prebookArticles,
            ffName: (ff) => (ff === targetWh ? (vehicle?.target_warehouse_name || `ФФ ${ff}`) : `ФФ ${ff}`),
            ppbOf: (nm) => nmPpb.get(nm) || 0,
            ppbAt: (nm) => nmPpb.get(nm) || 0,
            boxSizeOf: (nm) => nmBoxSize.get(nm) ?? null,
            palletOverrides,
        });
    }, [effPrebook, shipRows, prebookArticles, targetWh, vehicle, nmPpb, nmBoxSize, palletOverrides]);

    // Метки приёмки предброни (зеркало раздела) из уже проверенной приёмки раскладки.
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

    // Действия предброни (машина): «+» → перевод направления в отгрузку, «−» → скрыть
    // (остаётся на машине). Всё уедет в PRE_DISTRIBUTED на «Создать заявки».
    const promoteDir = useCallback((pkg: PackageType, wb: string, ffId: number) => {
        const k = `${pkg}::${wb}::${ffId}`;
        setPrebookOpKey(k);
        setPromotedDirs(s => new Set(s).add(k));
        setTimeout(() => setPrebookOpKey(null), 250);
    }, []);
    const hideDir = useCallback((pkg: PackageType, wb: string, ffId: number) => {
        setHiddenDirs(s => new Set(s).add(`${pkg}::${wb}::${ffId}`));
    }, []);

    // «Дозабить» (BOX): добрать неполную паллету направления ЦЕЛЫМИ коробами из ОСТАТКА
    // машины → накопить в manualTopUp → пересчёт вольёт коробы в раскладку, паллета
    // наберётся и уедет в отгрузку (каноника «Черновика»: дозабор из остатка). Нечем — тост.
    const topUpBox = useCallback((pkg: PackageType, wb: string, ffId: number) => {
        if (pkg !== 'BOX') { promoteDir(pkg, wb, ffId); return; }
        const g = prebookGroups.find(x => x.pkg === 'BOX' && x.wb === wb && x.ffId === ffId);
        const adds = g?.topUp ? planBoxTopUp(g.topUp.candidates, wb, pool?.rows ?? [], commit.allocByBc, nmPpb) : [];
        if (adds.length === 0) { showToast('Нет остатка машины, чтобы дозабрать паллету', 'error'); return; }
        setPrebookOpKey(`${pkg}::${wb}::${ffId}`);
        setManualTopUp(prev => {
            const next = new Map(prev);
            for (const a of adds) {
                const wbMap = { ...(next.get(a.barcode) ?? {}) };
                wbMap[a.wb] = (wbMap[a.wb] ?? 0) + a.units;
                next.set(a.barcode, wbMap);
            }
            return next;
        });
        setTimeout(() => setPrebookOpKey(null), 250);
    }, [prebookGroups, pool, commit, nmPpb, promoteDir, showToast]);

    // Метка приёмки WB ячейки отгрузки: ⌛ нет лимита приёмки (нужна предзаявка).
    // Тип для выбора meta — по факту отправки (shipPkg), иначе высший доступный.
    const markFor = useCallback((nm: number, wh: string, shipPkg: PackageType | null): CellMark | null => {
        const flags = acceptanceByNm.get(nm)?.availability?.[wh];
        if (!flags) return null;
        // Закрыто по всем типам → ⛔ (важно для ручных пинов: авто-раскладка сюда не поедет,
        // а пин юзера — да, поэтому предупреждаем, что склад физически не принимает).
        if (!flags.can_box && !flags.can_monopallet && !flags.can_supersafe) return { noLimit: false, closed: true };
        // Тип для meta — заявленный shipPkg, если он реально доступен; иначе лучший доступный.
        const canOf = (t: 'box' | 'mono' | 'super') => t === 'box' ? flags.can_box : t === 'mono' ? flags.can_monopallet : flags.can_supersafe;
        const wanted: 'box' | 'mono' | 'super' | null = shipPkg === 'MONOPALLET' ? 'mono' : shipPkg === 'SUPERSAFE' ? 'super' : shipPkg === 'BOX' ? 'box' : null;
        const type = wanted && canOf(wanted) ? wanted : flags.can_box ? 'box' : flags.can_monopallet ? 'mono' : 'super';
        const meta = type === 'box' ? flags.box_meta : type === 'mono' ? flags.mono_meta : flags.super_meta;
        return { noLimit: ((meta?.free_days_14 ?? 0) + (meta?.paid_days_14 ?? 0)) <= 0 };
    }, [acceptanceByNm]);

    // Правка ячейки матрицы на ±1 короб. Первая правка баркода «замораживает» текущую
    // авто-раскладку строки в пины (соседние ячейки не прыгают — меняется только кликнутая).
    // Σ коробов баркода капится остатком машины (нельзя дорисовать больше, чем стоит).
    const editCellBoxes = useCallback((barcode: string, nm: number, wh: string, delta: number) => {
        const ppb = nmPpb.get(nm) || 0;
        if (ppb <= 0) return;
        const avail = availByBc.get(barcode) ?? 0;
        setCellEdits(prev => {
            const next = new Map(prev);
            // Режим правки = чистый лист: новый баркод стартует ПУСТЫМ (0 везде), ставишь короба
            // сам из остатка машины. Уже тронутый — продолжаем с его пинов.
            const rec = next.get(barcode) ?? {};
            next.set(barcode, applyCellBoxDelta(rec, wh, delta, ppb, avail));
            return next;
        });
    }, [nmPpb, availByBc]);
    const resetRowEdits = useCallback((barcode: string) => {
        setCellEdits(prev => { if (!prev.has(barcode)) return prev; const n = new Map(prev); n.delete(barcode); return n; });
    }, []);
    const resetAllEdits = useCallback(() => setCellEdits(new Map()), []);

    // Вход в ручной режим: «замораживаем» ТЕКУЩУЮ авто-раскладку в редактируемые пины,
    // чтобы правка начиналась с уже разложенных данных (а не с чистого листа). Сеем только
    // если правок ещё нет (повторный вход не перетирает ручные изменения). `matrix.cellByBc`
    // тут = авто-покрытие (editMode ещё false в момент клика).
    const enterManual = useCallback(() => {
        setCellEdits(prev => {
            if (prev.size > 0) return prev;  // уже есть ручные правки — не перетираем
            const seeded: CellEdits = new Map();
            for (const [bc, cells] of matrix.cellByBc) {
                const nm = nmByBc.get(bc) ?? 0;
                const ppb = nmPpb.get(nm) || 0;
                if (ppb <= 0) continue;
                const rec: Record<string, number> = {};
                for (const [wh, c] of cells) { const b = boxesOf(c.qty, ppb); if (b > 0) rec[wh] = b; }
                if (Object.keys(rec).length > 0) seeded.set(bc, rec);
            }
            return seeded;
        });
        setEditMode(true);
    }, [matrix, nmByBc, nmPpb]);

    // Значение строки для сортировки по ключу колонки.
    const sortValue = useCallback((row: PreDistVehiclePool['rows'][number], key: SortKey): number | string => {
        const nm = nmByBc.get(row.barcode) ?? 0;
        const e = enrichMap.get(nm);
        const ship = matrix.allocByBc.get(row.barcode) ?? 0;
        switch (key) {
            case 'label': return (row.article_seller || row.article_wb || row.barcode).toLowerCase();
            case 'avail': return Number(row.available_qty) || 0;
            case 'inAssembly': return e?.inAssembly ?? 0;
            case 'stocksWb': return e?.stocksWb ?? 0;
            case 'ship': return ship;
            case 'boxes': return boxesOf(ship, nmPpb.get(nm));
            case 'stays': return Math.max(0, (Number(row.available_qty) || 0) - ship);
            default:  // wb:<склад> — сколько сдаём в этот WB-склад
                return matrix.cellByBc.get(row.barcode)?.get(key.slice(3))?.qty ?? 0;
        }
    }, [nmByBc, enrichMap, matrix, nmPpb]);

    // Строки таблицы, отсортированные по активной колонке (клик по заголовку).
    const sortedRows = useMemo(() => {
        const rows = [...(pool?.rows ?? [])];
        const dir = sortDir === 'asc' ? 1 : -1;
        rows.sort((a, b) => {
            const va = sortValue(a, sortKey), vb = sortValue(b, sortKey);
            const cmp = typeof va === 'string' || typeof vb === 'string'
                ? String(va).localeCompare(String(vb), 'ru')
                : va - vb;
            if (cmp !== 0) return cmp * dir;
            // Тай-брейк — по названию (стабильный порядок).
            return (a.article_seller || a.barcode).localeCompare(b.article_seller || b.barcode, 'ru');
        });
        return rows;
    }, [pool, sortKey, sortDir, sortValue]);

    const handleSubmit = useCallback(async () => {
        if (!vehicleId || submitRows.length === 0 || submitting) return;
        setSubmitting(true);
        try {
            const res = await api.createPreDistribution({
                vehicle_id: vehicleId,
                rows: submitRows,
                // Паллеты по группе (бэк-группировка `${wb}::${pkg}`) — чтобы «Палеты»/«Общий вес»
                // проставились при создании, как у обычных поставок (вес бэк досчитает сам).
                pallets_by_group: Object.fromEntries(commit.palletsByGroup),
            });
            showToast(`Создано ${formatNumber(res.created, 0)} заявок`, 'success');
            backToList();
        } catch (e) {
            showToast(e instanceof Error ? e.message : 'Ошибка создания заявок', 'error');
        } finally {
            setSubmitting(false);
        }
    }, [vehicleId, submitRows, commit, submitting, showToast, backToList]);

    // ─── States ────────────────────────────────────────────────────────────
    const header = (
        <div className="page-header" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <button className="btn btn-secondary btn-sm" onClick={backToList}>← Назад</button>
            <h1 className="page-title" style={{ margin: 0 }}>
                Распределение машины{vehicle ? ` ${vehicle.order_no}` : ''}
            </h1>
            {vehicle && (
                <>
                    <span className="badge badge-info">{vehicle.status}</span>
                    <span style={{ fontSize: 13, color: 'var(--color-muted)' }}>
                        Склад: <b style={{ color: 'var(--color-text)' }}>{vehicle.target_warehouse_name || '—'}</b>
                        {vehicle.eta ? ` · ETA ${formatDate(vehicle.eta)}` : ''}
                    </span>
                </>
            )}
        </div>
    );

    if (!vehicleId) {
        return (
            <div className="animate-in">
                {header}
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>
                    Машина не выбрана. <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={backToList}>К списку машин</button>
                </div>
            </div>
        );
    }
    if (loading) {
        return (
            <div className="animate-in">
                {header}
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>Загрузка пула и потребности…</div>
            </div>
        );
    }
    if (error) {
        return (
            <div className="animate-in">
                {header}
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>
                    <div style={{ color: 'var(--color-danger)', marginBottom: 16 }}>{error}</div>
                    <button className="btn btn-secondary" onClick={() => router.refresh()}>Повторить</button>
                </div>
            </div>
        );
    }
    if (!pool || pool.rows.length === 0) {
        return (
            <div className="animate-in">
                {header}
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>На машине нет товара для распределения</div>
            </div>
        );
    }

    const computing = distComputing || distRows === null;

    return (
        <div className="animate-in">
            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
            {header}

            <AcceptanceBanner summary={acceptanceNote} />

            {noKratnostArticles.length > 0 && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
                        <span style={{ fontWeight: 700, color: 'var(--color-warning)' }}>🧩 Без кратности короба — {formatNumber(noKratnostArticles.length, 0)} арт.</span>
                        <span style={{ fontSize: 12, color: 'var(--color-muted)' }}>
                            кратность короба не задана → отгружать нечем (россыпь запрещена), остаются на машине; укажите кратность на вкладке «Кратность»
                        </span>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                        {noKratnostArticles.slice(0, 40).map(a => (
                            <span key={a.nm_id} title={`nm ${a.nm_id} · остаток на машине: ${formatNumber(a.qty, 0)} шт`}
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

            {/* Машинная KPI-сводка + управление раскладкой — общая над табами. */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16, display: 'flex', gap: 28, flexWrap: 'wrap', alignItems: 'center', fontSize: 14 }}>
                <span>К отправке: <b style={{ fontSize: 18 }}>{formatNumber(commit.totalShip, 0)}</b> шт</span>
                <span>📦 Коробов: <b style={{ fontSize: 18 }}>{formatNumber(commit.totalBoxes, 0)}</b></span>
                <span>🚚 Паллет: <b style={{ fontSize: 18 }}>{formatNumber(commit.totalPallets, 0)}</b></span>
                <span style={{ color: 'var(--color-muted)' }}>Заявок: <b style={{ color: 'var(--color-text)' }}>{formatNumber(commit.requestCount, 0)}</b></span>
                <span style={{ color: 'var(--color-muted)' }}>На хранение (ФФ): <b style={{ color: 'var(--color-text)' }}>{formatNumber(onHoldQty, 0)}</b> шт</span>
                {prebookUnits > 0 && (
                    <span style={{ color: 'var(--color-muted)' }}>🅿️ Предбронь: <b style={{ color: 'var(--color-text)' }}>{formatNumber(prebookUnits, 0)}</b> шт</span>
                )}
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 12, alignItems: 'center' }}>
                    <span style={{ fontSize: 12, color: 'var(--color-muted)' }} title="Матрица «Раскладка» = коробное покрытие потребности. В Заявки идут только целые паллеты; неполные — в 🅿️ Предбронь (Дозабить / Оставить так / На ФФ).">
                        📦 Матрица — покрытие · 🚚 Заявки — целые паллеты
                    </span>
                    <button className="btn btn-primary" onClick={handleSubmit} disabled={submitting || submitRows.length === 0}>
                        {submitting ? 'Создание…' : `Создать заявки (${formatNumber(commit.requestCount, 0)})`}
                    </button>
                </div>
            </div>

            {/* Под-табы в скоупе машины (зеркалят раздел, данные — из машины). */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                <button className={`btn ${subTab === 'dist' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setSubTab('dist')}>🚚 Раскладка</button>
                <button className={`btn ${subTab === 'preview' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setSubTab('preview')}>📋 Заявки</button>
                <button className={`btn ${subTab === 'prebook' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setSubTab('prebook')}>
                    🅿️ Предбронь{prebookUnits > 0 ? ` (${formatNumber(prebookUnits, 0)})` : ''}
                </button>
            </div>

            {subTab === 'prebook' ? (
                prebookGroups.length === 0 ? (
                    <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-muted)' }}>
                        Предброни нет — вся раскладка машины набирается целыми паллетами (или ничего не разложено).
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
                        onDelete={(nm, wb, pkg) => setHiddenSkus(s => new Set(s).add(`${nm}::${wb}::${pkg}`))}
                        onDeleteDirection={hideDir}
                        onCreatePrebooking={promoteDir}
                        onTopUpPrebook={(wb, ffId) => promoteDir('MONOPALLET', wb, ffId)}
                        onTrimTail={(wb, ffId) => hideDir('MONOPALLET', wb, ffId)}
                        onBookPallets={(wb, ffId) => promoteDir('MONOPALLET', wb, ffId)}
                        onReleasePallets={(wb, ffId) => hideDir('MONOPALLET', wb, ffId)}
                        onDraftPallets={(wb, ffId) => promoteDir('MONOPALLET', wb, ffId)}
                    />
                )
            ) : subTab === 'preview' ? (
                <DraftPreview
                    slug={slug}
                    rows={shipRows}
                    newcomerNmIds={newcomerSet}
                    warehouses={previewWarehouses}
                    nmPpb={nmPpb}
                    nmMeta={nmMeta}
                    nmBoxSize={nmBoxSize}
                    palletOverrides={palletOverrides}
                    geomReady={geomReady}
                    prebookOrigin={prebookPromotedOrigin}
                    predist={{ commitAll: handleSubmit }}
                    onToast={showToast}
                />
            ) : (<>
            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
                    <button className={`btn btn-sm ${editMode ? 'btn-primary' : 'btn-secondary'}`} onClick={editMode ? () => setEditMode(false) : enterManual}>
                        {editMode ? '✓ Готово с ручной раскладкой' : '✏️ Разложить вручную'}
                    </button>
                    {editMode && (
                        <span style={{ fontSize: 12, color: 'var(--color-warning)' }}>
                            Ручной режим: стартует с текущей авто-раскладки — правь короба (−/+) под себя из остатка машины; лимит приёмки склада проверяется (⌛ предзаявка · ⛔ закрыт).
                        </span>
                    )}
                    {cellEdits.size > 0 && (
                        <button className="btn btn-sm btn-secondary" style={{ marginLeft: 'auto' }} onClick={resetAllEdits}>
                            ↺ Сбросить все правки ({formatNumber(cellEdits.size, 0)})
                        </button>
                    )}
                </div>
                <div style={{ color: 'var(--color-muted)', fontSize: 13 }}>
                    Матрица показывает <b style={{ color: 'var(--color-text)' }}>коробное покрытие</b> потребности (целые коробы под потребность каждого WB-склада, как «Потребность по складам»),
                    источник — остатки этой машины. Колонки складов: 🏬 остаток на WB · 🚚 в сборке/в пути · потребность · что сдаём коробом.
                    В <b style={{ color: 'var(--color-text)' }}>Заявки</b> идут только целые паллеты; неполные — в 🅿️ Предбронь (Дозабить / Оставить так / На ФФ). Поэтому числа матрицы и Заявок могут расходиться.
                </div>
            </div>

            {computing ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-muted)' }}>Считаю раскладку (потребность · приёмка · коробы · паллеты)…</div>
            ) : matrix.totalShip === 0 && !editMode ? (
                // В ручном режиме матрица стартует пустой (0 распределено) — таблицу ВСЕГДА
                // показываем как чистый холст со степперами; заглушка только для авто-режима.
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-muted)' }}>
                    Нечего разложить: ни у одного артикула машины нет потребности по WB-складам (или не задана кратность короба).
                </div>
            ) : (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            {/* Шапка округов */}
                            <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                                <th colSpan={4} style={{ position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 2 }} />
                                {districtGroups.map((g, i) => (
                                    <th key={i} colSpan={g.count} style={{ padding: '6px 8px', textAlign: 'center', color: '#fff', background: g.color, fontSize: 11, letterSpacing: 0.4, textTransform: 'uppercase' }}>
                                        {g.label}
                                    </th>
                                ))}
                                {/* хвост: Σ отпр. + Мест + Остаётся ФФ = 3 колонки */}
                                <th colSpan={3} />
                            </tr>
                            {/* Шапка колонок — клик по заголовку сортирует строки */}
                            <tr style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-muted)', textAlign: 'right' }}>
                                <th style={{ padding: '8px 12px', textAlign: 'left', position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 2, ...sortableTh }} onClick={() => toggleSort('label')} title="Сортировать по названию">Товар{sortArrow('label')}</th>
                                <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('avail')} title="Сортировать по остатку на машине">На машине{sortArrow('avail')}</th>
                                <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('inAssembly')} title="Уже в сборке на WB">В сборке{sortArrow('inAssembly')}</th>
                                <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('stocksWb')} title="Остаток на Wildberries">На WB{sortArrow('stocksWb')}</th>
                                {wbCols.map(c => (
                                    <th key={c.name} style={{ padding: '8px 8px', whiteSpace: 'nowrap', background: districtTint(c.district), ...sortableTh }} onClick={() => toggleSort(`wb:${c.name}`)} title={`Сортировать по отправке в «${c.name}»`}>{c.name}{sortArrow(`wb:${c.name}`)}</th>
                                ))}
                                <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('ship')} title="Сортировать по сумме отправки">Σ отпр.{sortArrow('ship')}</th>
                                <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('boxes')} title="Коробов к отправке">Мест{sortArrow('boxes')}</th>
                                <th style={{ padding: '8px 8px', ...sortableTh }} onClick={() => toggleSort('stays')} title="Сортировать по остатку на ФФ">Остаётся ФФ{sortArrow('stays')}</th>
                            </tr>
                            {/* Итоги сдачи на склад — сразу под шапкой (зеркало нижнего футера, чтобы видеть без скролла) */}
                            <tr style={{ borderBottom: '2px solid var(--color-border)', fontWeight: 700, background: 'rgba(59,130,246,0.06)', textAlign: 'right' }}>
                                <td style={{ padding: '6px 12px', textAlign: 'left', position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 2 }}>Сдаём, шт</td>
                                <td colSpan={3} />
                                {wbCols.map(c => (
                                    <td key={c.name} style={{ padding: '6px 8px', color: 'var(--color-accent)', background: districtTint(c.district) }}>
                                        {formatNumber(matrix.shipByWh.get(c.name) ?? 0, 0)}
                                    </td>
                                ))}
                                <td style={{ padding: '6px 8px', color: 'var(--color-accent)' }}>{formatNumber(matrix.totalShip, 0)}</td>
                                <td style={{ padding: '6px 8px', color: 'var(--color-muted)' }} title="Всего коробов к отправке">{formatNumber(matrix.totalBoxes, 0)}</td>
                                <td style={{ padding: '6px 8px', color: 'var(--color-muted)' }} title="Остаётся на хранении ФФ">{formatNumber(matrixOnHold, 0)}</td>
                            </tr>
                        </thead>
                        <tbody>
                            {sortedRows.map(row => {
                                const nm = nmByBc.get(row.barcode) ?? 0;
                                const e: EnrichedSku | undefined = enrichMap.get(nm);
                                const avail = Number(row.available_qty) || 0;
                                const ship = matrix.allocByBc.get(row.barcode) ?? 0;
                                const stays = Math.max(0, avail - ship);
                                const cells = matrix.cellByBc.get(row.barcode);
                                const ppb = nmPpb.get(nm);
                                const label = row.article_seller || row.article_wb || row.barcode;
                                const rowBoxes = boxesOf(ship, ppb);
                                const editRec = cellEdits.get(row.barcode);
                                const rowEditable = editMode && !!ppb && ppb > 0;
                                const rowAtCap = ship + (ppb || 0) > avail;  // «+» упрётся в остаток машины
                                return (
                                    <tr key={row.barcode} style={{ borderBottom: '1px solid var(--color-border)', background: editRec ? 'rgba(255,159,10,0.07)' : ship > 0 ? 'rgba(59,130,246,0.04)' : undefined }}>
                                        <td style={{ padding: '6px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 1 }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                                                <span style={{ fontWeight: 600 }}>{label}</span>
                                                {e?.isNew && <span className="badge" style={{ background: 'rgba(168,85,247,0.16)', color: '#a855f7', fontSize: 10, padding: '1px 6px' }}>🆕 новинка</span>}
                                                {ppb ? <span className="badge badge-secondary" style={{ fontSize: 10, padding: '1px 6px' }}>📦 кратно {formatNumber(ppb, 0)}</span> : <span className="badge" style={{ background: 'rgba(255,159,10,0.14)', color: 'var(--color-warning)', fontSize: 10, padding: '1px 6px' }}>без кратности</span>}
                                                {editRec && (
                                                    <button type="button" className="badge" title="Сбросить ручные правки строки"
                                                        style={{ background: 'rgba(255,159,10,0.16)', color: 'var(--color-warning)', fontSize: 10, padding: '1px 6px', border: 'none', cursor: 'pointer' }}
                                                        onClick={() => resetRowEdits(row.barcode)}>✏️ вручную · ↺</button>
                                                )}
                                            </div>
                                            <div style={{ fontSize: 11, color: 'var(--color-muted)' }}>{row.name ? `${row.name} · ` : ''}ШК {row.barcode}</div>
                                        </td>
                                        <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(avail, 0)}</td>
                                        <td style={{ padding: '6px 8px', textAlign: 'right', color: e && e.inAssembly > 0 ? 'var(--color-text)' : 'var(--color-dim)' }}>{e && e.inAssembly > 0 ? formatNumber(e.inAssembly, 0) : '·'}</td>
                                        <td style={{ padding: '6px 8px', textAlign: 'right', color: e && e.stocksWb > 0 ? 'var(--color-text)' : 'var(--color-dim)' }}>{e && e.stocksWb > 0 ? formatNumber(e.stocksWb, 0) : '·'}</td>
                                        {wbCols.map(c => {
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
                                                        onDelta: (d: number) => editCellBoxes(row.barcode, nm, c.name, d),
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
                        </tbody>
                        <tfoot>
                            <tr style={{ borderTop: '2px solid var(--color-border)', fontWeight: 600 }}>
                                <td style={{ padding: '8px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)' }}>Отправить, шт</td>
                                <td colSpan={3} />
                                {wbCols.map(c => (
                                    <td key={c.name} style={{ padding: '8px 8px', textAlign: 'right', color: 'var(--color-accent)' }}>{formatNumber(matrix.shipByWh.get(c.name) ?? 0, 0)}</td>
                                ))}
                                <td style={{ padding: '8px 8px', textAlign: 'right' }}>{formatNumber(matrix.totalShip, 0)}</td>
                                <td colSpan={2} />
                            </tr>
                            <tr style={{ color: 'var(--color-muted)' }}>
                                <td style={{ padding: '6px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)' }}>📦 Коробов</td>
                                <td colSpan={3} />
                                {wbCols.map(c => (
                                    <td key={c.name} style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(matrix.boxesByWh.get(c.name) ?? 0, 0)}</td>
                                ))}
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(matrix.totalBoxes, 0)}</td>
                                <td colSpan={2} />
                            </tr>
                            <tr style={{ color: 'var(--color-muted)' }}>
                                <td style={{ padding: '6px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)' }}>🚚 Паллет</td>
                                <td colSpan={3} />
                                {wbCols.map(c => (
                                    <td key={c.name} style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(matrix.palletsByWh.get(c.name) ?? 0, 0)}</td>
                                ))}
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(matrix.totalPallets, 0)}</td>
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
