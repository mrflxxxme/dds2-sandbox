'use client';
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import { distributeByBoxMultiple } from '@/lib/utils/boxDistribution';
import { DISTRICT_LABELS, DISTRICT_COLORS, DISTRICT_ORDER } from '@/lib/constants/localization';
import type {
    AcceptanceCheckPerItem,
    AssemblyDraftRow,
    ColdStartMainWarehouse,
    ColdStartTableResponse,
    ColdStartTableRow,
    PackageType,
    RedistributionMove,
} from '@/types/api';

/* ── Types ── */

interface RfWarehouse {
    id: number;
    name: string;
    assembly_days: number;
}

interface ArticleRfStock {
    stock: number;
    available: number;
}

interface NeedArticle {
    nm_id: number;
    vendor_code: string;
    barcode: string;
    brand: string;
    subject: string;
    total_need: number;
    revenue_30d: number;
    rf_stocks: Record<number, ArticleRfStock>;
    in_assembly: number;
    in_transit: number;
    in_transit_date: string | null;
    can_send: number;
    deficit: number;
    stocks_wb: number;
    /** Per-WB-склад: сколько уже в сборке на этот склад (не учтено в need). */
    asm_by_warehouse?: Record<string, number>;
    /** Per-WB-склад: сколько уже едет на этот склад транзитом. */
    transit_by_warehouse?: Record<string, number>;
}

interface WbWarehouseNeed {
    name: string;
    total_need: number;
    articles: Record<number, { need: number; stock: number; avg_daily: number }>;
    /** Ключ ФО (central|south_caucasus|volga|ural|far_east_siberia|northwest|abroad|unknown).
     *  Backend заполняет через `warehouse_to_district`. UI рендерит label под названием. */
    district_key?: string;
}

interface NeedSummary {
    total_need: number;
    total_can_send: number;
    total_deficit: number;
    avg_delivery_days: number;
    deficit_count: number;
    can_send_count: number;
    no_wb_count: number;
}

interface StockNeedResponse {
    warehouses: WbWarehouseNeed[];
    articles: NeedArticle[];
    rf_warehouses: RfWarehouse[];
    brands: string[];
    subjects: string[];
    supply_days: number;
    analysis_days: number;
    mode: string;
    total_warehouses: number;
    total_articles: number;
    summary: NeedSummary;
}

/* ── Helpers ── */

function formatRevenue(v: number): string {
    if (!v) return '\u2014';
    if (v >= 1_000_000) return `\u20BD${(v / 1_000_000).toFixed(1)}M`;
    if (v >= 1_000) return `\u20BD${Math.round(v / 1_000)}K`;
    return `\u20BD${v}`;
}

function formatTransitDate(dateStr: string | null): string {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    return `${dd}.${mm}`;
}

// Фильтры разделены на 2 независимые группы — статус (по реальной возможности
// отправки) и тип упаковки (по acceptance check). Можно нажать «Могу отправить»
// + «Моно» одновременно: остаются SKU где can_send>0 И есть моно-split.
type StatusFilter = 'all' | 'deficit' | 'can_send' | 'no_wb' | 'no_stock';
type PackageFilter = 'none' | 'box' | 'mono';

/** Persist WB acceptance check result so F5 не теряет состояние.
 *  TTL соответствует backend Redis-кэшу (5 мин). */
const ACCEPTANCE_LS_TTL_MS = 5 * 60 * 1000;
function acceptanceLsKey(slug: string | undefined): string {
    return `dds:acceptance:${slug || 'unknown'}`;
}
interface AcceptancePersist {
    items: AcceptanceCheckPerItem[];
    moves: RedistributionMove[];
    checked_at: string;
    saved_at: number;
}

// Cold-start клиентский pro-rata: qty распределяется между всеми main_warehouses
// пропорционально share_pct, с min_pack pool внутри округа (склады < min_pack →
// крупнейший склад того же округа). Эквивалент backend distribute_multi.
function recomputeColdStartAlloc(
    qty: number,
    mainWarehouses: ColdStartMainWarehouse[],
    minPack: number,
): { alloc: Record<string, number>; total: number } {
    if (qty <= 0 || mainWarehouses.length === 0) return { alloc: {}, total: 0 };
    const totalShare = mainWarehouses.reduce((s, w) => s + w.share_pct, 0) || 1;
    const raw: Record<string, number> = {};
    for (const w of mainWarehouses) {
        raw[w.warehouse] = Math.floor((qty * w.share_pct) / totalShare);
    }
    const sumRaw = Object.values(raw).reduce((s, v) => s + v, 0);
    const leftover = qty - sumRaw;
    if (leftover > 0) {
        const biggest = mainWarehouses.reduce((a, b) => (a.share_pct > b.share_pct ? a : b));
        raw[biggest.warehouse] += leftover;
    }
    // Pool внутри округа
    const byDistrict: Record<string, ColdStartMainWarehouse[]> = {};
    for (const w of mainWarehouses) {
        (byDistrict[w.district_key] ||= []).push(w);
    }
    for (const whs of Object.values(byDistrict)) {
        const biggestInDistrict = whs.reduce((a, b) => (a.share_pct > b.share_pct ? a : b)).warehouse;
        let pool = 0;
        for (const w of whs) {
            if (w.warehouse !== biggestInDistrict && raw[w.warehouse] < minPack) {
                pool += raw[w.warehouse];
                raw[w.warehouse] = 0;
            }
        }
        if (pool > 0) raw[biggestInDistrict] += pool;
    }
    const alloc: Record<string, number> = {};
    for (const [k, v] of Object.entries(raw)) {
        if (v > 0) alloc[k] = v;
    }
    return { alloc, total: Object.values(alloc).reduce((s, v) => s + v, 0) };
}

/* ── Component ── */

type HypoMode = 'region' | 'city';

interface OrderCitiesStatus {
    has_data: boolean;
    total_mappings: number;
    date_from: string | null;
    date_to: string | null;
    last_updated: string | null;
}

export function WarehouseNeedView() {
    const params = useParams();
    const router = useRouter();
    const slug = params?.slug as string | undefined;

    const [data, setData] = useState<StockNeedResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    /** Map nm_id → {margin_pct, stocks_wb, days_left} из getStockAnalytics(14).
     *  Используется для столбцов «Маржа / WB / Хватит дн» в обеих таблицах. */
    const [analyticsMap, setAnalyticsMap] = useState<Map<number, {
        margin_pct: number | null;
        stocks_wb: number;
        days_left: number;
    }>>(new Map());
    /** Map nm_id → loc_pct (% локализации) из getLocalizationSkus(14d).
     *  Используется для столбца «% лок» в обеих таблицах. */
    const [locMap, setLocMap] = useState<Map<number, number>>(new Map());
    const [creatingAssembly, setCreatingAssembly] = useState(false);
    const [supplyDays, setSupplyDays] = useState(14);
    const [analysisDays, setAnalysisDays] = useState(14);
    /** «Дораспределить»: bump top-1 главного склада каждого ФО до N шт.
     *  0 = выключено (используется только operational план).
     *  N > 0 = добавляем bump после жадного cap, если у SKU остались свободные
     *  FF-остатки и главный склад ФО имеет < N шт (WB-stock + asm + transit + cell). */
    const [minStockPerMain, setMinStockPerMain] = useState(0);
    const [showMinStockInput, setShowMinStockInput] = useState(false);
    const [mode, setMode] = useState<'actual' | 'hypothetical'>('actual');
    /** Идеальная локализация: распределяем потребность по ближайшим доступным
     *  WB-складам (по координатам региона покупателя). Исключённые склады
     *  автоматически перебрасываются на ближайшие available по haversine.
     *  Дополнительно: district-pooling сборок и транзита (asm в Электросталь
     *  снижает потребность всех складов ЦФО пропорционально, не только Эл-сталь). */
    const [localizationOptimized] = useState(true);
    /** Только реально могу отправить: каждая клетка урезана greedy по
     *  ФФ-остатку артикула — сумма needs во всех WB-колонках ≤ available. */
    // Алгоритм работает только в режиме «По дефициту» — operational план.
    // Идеальная локализация удалена как режим (was confusing: cells > rf_avail).
    const onlyAvailable = true;
    const [hypoMode, setHypoMode] = useState<HypoMode>('region');
    const [showHypoMenu, setShowHypoMenu] = useState(false);
    const [citiesStatus, setCitiesStatus] = useState<OrderCitiesStatus | null>(null);
    const [uploading, setUploading] = useState(false);
    const [brandFilter, setBrandFilter] = useState('');
    const [subjectFilter, setSubjectFilter] = useState('');
    const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
    const [packageFilter, setPackageFilter] = useState<PackageFilter>('none');
    const [searchQuery, setSearchQuery] = useState('');
    const [sortCol, setSortCol] = useState<string>('revenue_30d');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
    const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set());
    const [assemblyWarehouseId, setAssemblyWarehouseId] = useState<number | null>(null);
    const hypoMenuRef = useRef<HTMLDivElement>(null);

    /* ── WB Acceptance check (доступность складов через WB API) ── */
    const [acceptanceLoading, setAcceptanceLoading] = useState(false);
    const [acceptanceError, setAcceptanceError] = useState<string | null>(null);
    const [acceptanceMap, setAcceptanceMap] = useState<Map<number, AcceptanceCheckPerItem>>(new Map());
    const [acceptanceMoves, setAcceptanceMoves] = useState<RedistributionMove[]>([]);
    const [acceptanceCheckedAt, setAcceptanceCheckedAt] = useState<string | null>(null);

    /* ── Box-multiplicity (кратность коробки per nm_id, с per-RF override) ── */
    // Для каждого SKU храним ppb в зависимости от выбранного RF-источника.
    // Структура: Map<nm_id, { default: ppb|null, perRf: Map<warehouseId, {ppb, use}> }>
    type BoxMultEntry = {
        skuPpb: number | null;
        skuUse: boolean;
        perRf: Map<number, { ppb: number | null; use: boolean }>;
    };
    const [boxMultiplicityData, setBoxMultiplicityData] = useState<Map<number, BoxMultEntry>>(new Map());

    useEffect(() => {
        let cancelled = false;
        api.getBoxMultiplicity()
            .then(resp => {
                if (cancelled) return;
                const m = new Map<number, BoxMultEntry>();
                for (const r of resp.items) {
                    const perRf = new Map<number, { ppb: number | null; use: boolean }>();
                    for (const p of r.per_warehouse) {
                        perRf.set(p.warehouse_id, { ppb: p.box_qty, use: p.use_box_multiplicity });
                    }
                    m.set(r.nm_id, {
                        skuPpb: r.effective_box_qty,
                        skuUse: r.use_box_multiplicity,
                        perRf,
                    });
                }
                setBoxMultiplicityData(m);
            })
            .catch(() => {
                // best-effort: если кратности нет — fallback на старую логику без округления
            });
        return () => { cancelled = true; };
    }, []);

    // Резолвит (ppb, use) для выбранного RF-склада.
    // Per-RF override побеждает SKU-level если задан.
    const resolveBoxMult = useCallback((nmId: number, rfWarehouseId: number | null): { ppb: number | null; use: boolean } => {
        const entry = boxMultiplicityData.get(nmId);
        if (!entry) return { ppb: null, use: true };
        if (rfWarehouseId !== null) {
            const perRf = entry.perRf.get(rfWarehouseId);
            if (perRf?.ppb && perRf.ppb > 0) {
                return { ppb: perRf.ppb, use: perRf.use };
            }
            // RF-row может задавать только use-flag без override ppb → его флаг важнее
            if (perRf && entry.skuPpb && entry.skuPpb > 0) {
                return { ppb: entry.skuPpb, use: perRf.use };
            }
        }
        if (entry.skuPpb && entry.skuPpb > 0) {
            return { ppb: entry.skuPpb, use: entry.skuUse };
        }
        return { ppb: null, use: entry.skuUse };
    }, [boxMultiplicityData]);

    /* ── Restore acceptance state from localStorage on mount (5 min TTL) ── */
    useEffect(() => {
        if (typeof window === 'undefined' || !slug) return;
        try {
            const raw = window.localStorage.getItem(acceptanceLsKey(slug));
            if (!raw) return;
            const parsed: AcceptancePersist = JSON.parse(raw);
            if (!parsed?.saved_at || Date.now() - parsed.saved_at > ACCEPTANCE_LS_TTL_MS) {
                window.localStorage.removeItem(acceptanceLsKey(slug));
                return;
            }
            const map = new Map<number, AcceptanceCheckPerItem>();
            for (const it of parsed.items || []) map.set(it.nm_id, it);
            setAcceptanceMap(map);
            setAcceptanceMoves(parsed.moves || []);
            setAcceptanceCheckedAt(parsed.checked_at);
        } catch {
            // corrupt entry — wipe
            window.localStorage.removeItem(acceptanceLsKey(slug));
        }
    }, [slug]);

    /* ── Cold-start режим ── */
    const [coldStartMode, setColdStartMode] = useState(false);
    const [coldStartData, setColdStartData] = useState<ColdStartTableResponse | null>(null);
    const [coldStartMinPack, setColdStartMinPack] = useState(5);
    const [coldStartLoading, setColdStartLoading] = useState(false);
    const [coldStartQtyOverrides, setColdStartQtyOverrides] = useState<Record<number, number>>({});
    const [coldStartSortCol, setColdStartSortCol] = useState<string>('revenue_30d');
    const [coldStartSortDir, setColdStartSortDir] = useState<'asc' | 'desc'>('desc');

    useEffect(() => {
        // Сброс overrides при перезагрузке cold-start данных
        setColdStartQtyOverrides({});
    }, [coldStartData?.bench_source, coldStartData?.bench_total_orders]);

    // Грузим coldStartData ВСЕГДА (не только при открытии вкладки «Новинки»):
    // newcomerSet нужен на основной таблице, чтобы исключить новинок из
    // общего списка (у них total_need=0, и они создают шум — 121 строка
    // с прочерками в WB-колонках).
    useEffect(() => {
        let cancelled = false;
        (async () => {
            if (coldStartMode) setColdStartLoading(true);
            try {
                const resp = await api.getColdStartTable(analysisDays, coldStartMinPack);
                if (!cancelled) setColdStartData(resp);
            } catch (e) {
                if (!cancelled) console.error('cold-start load failed', e);
            } finally {
                if (!cancelled) setColdStartLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [coldStartMode, analysisDays, coldStartMinPack]);

    /* ── Close hypo menu on outside click ── */
    useEffect(() => {
        if (!showHypoMenu) return;
        const handler = (e: MouseEvent) => {
            if (hypoMenuRef.current && !hypoMenuRef.current.contains(e.target as Node)) {
                setShowHypoMenu(false);
            }
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [showHypoMenu]);

    /* ── Load cities status ── */
    const loadCitiesStatus = useCallback(async () => {
        try {
            const resp = await api.getOrderCitiesStatus();
            setCitiesStatus(resp);
        } catch { /* ignore */ }
    }, []);

    useEffect(() => { loadCitiesStatus(); }, [loadCitiesStatus]);

    /* ── Upload order cities file ── */
    const [uploadResult, setUploadResult] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleUploadCities = useCallback(async (file: File) => {
        setUploading(true);
        setUploadResult(null);
        setError(null);
        try {
            const resp = await api.uploadOrderCities(file);
            await loadCitiesStatus();
            setUploadResult(`Загружено: ${resp.total_mappings} заказов`);
            setTimeout(() => setUploadResult(null), 5000);
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : 'Ошибка загрузки файла';
            setError(message);
        }
        setUploading(false);
    }, [loadCitiesStatus]);

    const triggerFileSelect = useCallback(() => {
        console.log('[DDS] triggerFileSelect, ref:', fileInputRef.current);
        fileInputRef.current?.click();
    }, []);

    const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const f = e.target.files?.[0];
        console.log('[DDS] handleFileChange, file:', f?.name, f?.size);
        if (f) handleUploadCities(f);
        e.target.value = '';
    }, [handleUploadCities]);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const actualMode = mode === 'hypothetical' ? 'hypothetical' : 'actual';
            // min_stock_per_main_warehouse=0 — bump делается на клиенте (с учётом
            // acceptance: только склады где WB реально принимает), а не на backend
            // (он знает только top-1 ФО, но не знает acceptance-флагов).
            // Окно для аналитики/локализации — 14 дней назад → сегодня.
            // ВАЖНО: использовать локальную дату (Y-M-D местного timezone), не UTC.
            // `toISOString()` сдвинул бы окно на день для UTC+3 если сейчас вечер.
            const _today = new Date();
            const _from = new Date(_today.getTime() - 14 * 86_400_000);
            const fmtLocal = (d: Date) => {
                const y = d.getFullYear();
                const m = String(d.getMonth() + 1).padStart(2, '0');
                const day = String(d.getDate()).padStart(2, '0');
                return `${y}-${m}-${day}`;
            };
            const dateFrom = fmtLocal(_from);
            const dateTo = fmtLocal(_today);

            const [resp, analyticsResp, locResp] = await Promise.all([
                api.getStockNeed(
                    supplyDays,
                    analysisDays,
                    actualMode,
                    localizationOptimized,
                    onlyAvailable,
                    0,
                ) as Promise<StockNeedResponse>,
                // Параллельный fetch для столбцов «Маржа / WB / Хватит дн».
                // trend_days=14 чтобы avg_daily и days_left были по тому же
                // окну что analysis_days в основном fetch'е. Тип ответа — any
                // (см. api.reports.getStockAnalytics).
                (api.getStockAnalytics(14) as Promise<{
                    articles?: Array<{ nm_id: number; margin_pct?: number | null; stocks_wb?: number; days_left?: number }>;
                }>).catch(() => ({ articles: [] })),
                // % локализации из индекса локализации (то же окно 14д).
                api.getLocalizationSkus(dateFrom, dateTo).catch(() => [] as Array<{ nm_id: number; loc_pct: number }>),
            ]);
            setData(resp);
            const aMap = new Map<number, { margin_pct: number | null; stocks_wb: number; days_left: number }>();
            for (const a of analyticsResp.articles ?? []) {
                aMap.set(a.nm_id, {
                    margin_pct: a.margin_pct ?? null,
                    stocks_wb: a.stocks_wb ?? 0,
                    days_left: a.days_left ?? 0,
                });
            }
            setAnalyticsMap(aMap);
            const lMap = new Map<number, number>();
            for (const r of locResp ?? []) {
                if (typeof r.nm_id === 'number') lMap.set(r.nm_id, Number(r.loc_pct) || 0);
            }
            setLocMap(lMap);
            if (resp.rf_warehouses?.length && !assemblyWarehouseId) {
                setAssemblyWarehouseId(resp.rf_warehouses[0].id);
            }
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : 'Ошибка загрузки';
            setError(message);
        }
        setLoading(false);
    }, [supplyDays, analysisDays, mode, localizationOptimized, onlyAvailable, assemblyWarehouseId]);

    useEffect(() => { load(); }, [load]);

    /* ── Derived data ── */

    // Спец-склады (СГТ/Питание/Горючее/виртуальные/СЦ) — для крупногабаритки и
    // спец-товаров. На основном экране и в новинках для обычной FBO-поставки
    // они не нужны. includes() вместо regex \b: \b в JS работает только с
    // ASCII \w, для кириллицы (Ярославль СГТ) word-boundary не срабатывает.
    const isSpecWarehouse = useCallback((name: string): boolean => {
        if (name.startsWith('Виртуальный ')) return true;
        if (name.startsWith('СЦ ')) return true;
        if (name.includes(' СГТ')) return true;
        if (name.includes(': Питание') || name.includes(':Питание')) return true;
        if (name.includes(': Горючее') || name.includes(':Горючее')) return true;
        return false;
    }, []);

    // Filtered cold-start main_warehouses: убираем спец-склады и share=0%
    // (Виртуальный Челябинск etc.). Используется и в основной таблице, и в
    // вкладке «Новинки», и во всех вызовах recomputeColdStartAlloc.
    const filteredMainWarehouses = useMemo(() => {
        if (!coldStartData?.main_warehouses?.length) return [];
        const base = coldStartData.main_warehouses.filter(
            w => w.share_pct > 0 && !isSpecWarehouse(w.warehouse),
        );
        // Сортировка по округу (DISTRICT_ORDER) — чтобы group-header'ы во вкладке
        // «Новинки» были непрерывными, как в основной таблице.
        const districtIdx = (k?: string): number => {
            const i = DISTRICT_ORDER.indexOf((k || 'unknown') as typeof DISTRICT_ORDER[number]);
            return i < 0 ? DISTRICT_ORDER.length : i;
        };
        return base
            .map((w, idx) => ({ w, idx, di: districtIdx(w.district_key) }))
            .sort((a, b) => a.di - b.di || a.idx - b.idx)
            .map(x => x.w);
    }, [coldStartData, isSpecWarehouse]);

    const wbWarehouses = useMemo(() => {
        const main = (data?.warehouses ?? []).filter(w => w.total_need > 0 && !isSpecWarehouse(w.name));
        const have = new Set(main.map(w => w.name));
        // Lookup district_key для дополняемых складов: сначала из data.warehouses
        // (backend заполняет через warehouse_to_district), затем из cold-start
        // (там district_key всегда есть для main_warehouses).
        const districtLookup = new Map<string, string>();
        for (const w of data?.warehouses ?? []) {
            if (w.district_key) districtLookup.set(w.name, w.district_key);
        }
        for (const cs of filteredMainWarehouses) {
            if (cs.district_key) districtLookup.set(cs.warehouse, cs.district_key);
        }
        // (1) Cold-start главные склады округов (новинки без истории).
        for (const cs of filteredMainWarehouses) {
            if (!have.has(cs.warehouse)) {
                main.push({
                    name: cs.warehouse, total_need: 0, articles: {},
                    district_key: cs.district_key || districtLookup.get(cs.warehouse),
                });
                have.add(cs.warehouse);
            }
        }
        // (2) Склады, в которые после acceptance redistribute уехал qty
        // (Невинномысск и т.д. — открытые соседи по ФО, изначально не в нашем
        // раскладе). СГТ/виртуальные пропускаем тут тоже.
        for (const it of acceptanceMap.values()) {
            for (const [wh, q] of Object.entries(it.distribution || {})) {
                if (q > 0 && !have.has(wh) && !isSpecWarehouse(wh)) {
                    main.push({
                        name: wh, total_need: 0, articles: {},
                        district_key: districtLookup.get(wh),
                    });
                    have.add(wh);
                }
            }
        }
        return main;
    }, [data, filteredMainWarehouses, acceptanceMap, isSpecWarehouse]);

    /** Set nm_id'ов которые являются новинками (cold-start). Объявлен здесь,
     *  до bumpedCells и filteredArticles — нужен для исключения новинок. */
    const newcomerSet = useMemo(() => {
        const s = new Set<number>();
        for (const r of coldStartData?.rows ?? []) s.add(r.nm_id);
        return s;
    }, [coldStartData]);

    /** Базовый per-cell need из backend Hamilton-cap / cold-start allocations.
     *  НЕ включает client-side bump (см. bumpedCells ниже).
     *
     *  ВАЖНО: acceptance.splits.distribution НЕ используется как источник qty —
     *  в WB API мы шлём probe через share_pct ФО (Σ = total_need, например 173 шт
     *  для 120х160_пепельный), чтобы получить флаги доступности на дальние склады
     *  без orders (ДВ, СЗФО). Если читать qty из splits — это число протекает
     *  в рендер матрицы и показывает «идеальный план на total_need» вместо
     *  «реального плана на can_send». Backend warehouse_need_service в шаге
     *  `only_available` (Hamilton-cap по rf_avail) — единственный авторитетный
     *  источник per-cell qty. acceptance используется только для:
     *    1) Флаги package_type per склад (рендер иконки 📦/📐 — отдельный путь)
     *    2) Bump-логика «Дораспределить» (см. bumpedCells)
     *  Прецедент: project_id=4 (default), 120х160_пепельный (nm_id=599016991):
     *  Σ Hamilton-cap = 87 (= can_send), Σ share_pct = 173 (= total_need),
     *  раньше матрица показывала ~173 после нажатия «Проверить приёмку WB». */
    const getBaseArticleWbNeed = useCallback((article: NeedArticle, whName: string): number => {
        // Для cold-start новинок (нет истории продаж) — allocations из cold-start.
        const newcomer = coldStartData?.rows.find(r => r.nm_id === article.nm_id);
        if (newcomer) {
            const overrideQty = coldStartQtyOverrides[article.nm_id];
            const useOverride = overrideQty !== undefined && overrideQty !== newcomer.total_allocated;
            const alloc = useOverride
                ? recomputeColdStartAlloc(overrideQty, filteredMainWarehouses, coldStartMinPack).alloc
                : newcomer.allocations;
            return alloc[whName] || 0;
        }
        // Обычный SKU — Hamilton-capped per-WB-need из backend (Σ = can_send).
        if (!data?.warehouses) return 0;
        const wh = data.warehouses.find(w => w.name === whName);
        return wh?.articles?.[article.nm_id]?.need || 0;
    }, [data, coldStartData, filteredMainWarehouses, coldStartQtyOverrides, coldStartMinPack]);

    /** Текущий WB-сток per-cell — из data.warehouses[].articles[nm_id].stock. */
    const getArticleWbStock = useCallback((article: NeedArticle, whName: string): number => {
        if (!data?.warehouses) return 0;
        const wh = data.warehouses.find(w => w.name === whName);
        return wh?.articles?.[article.nm_id]?.stock || 0;
    }, [data]);

    /** Map склад → district_key (взят из coldStartData.main_warehouses).
     *  Покрывает только top-3 main_warehouses каждого ФО — для остальных WB-складов
     *  district = 'unknown' (они в bump-фильтре не участвуют). */
    const warehouseToDistrict = useMemo(() => {
        const m = new Map<string, string>();
        for (const w of coldStartData?.main_warehouses ?? []) {
            m.set(w.warehouse, w.district_key);
        }
        return m;
    }, [coldStartData]);

    /** Client-side bump: «Дораспределить излишки» — bump в underserved ФО
     *  ТОЛЬКО для SKU с дисбалансом локализации ИЛИ с FF-излишком.
     *  Алгоритм покрывает 2 кейса:
     *    (A) WB-overstock в одном ФО + пустой в другом → перебросить из FF
     *        в underserved (классический ребаланс ИЛ)
     *    (B) FF-излишек ≥ 3×min_pack (мёртвый груз на FF, общий профицит SKU)
     *        → распределить избыток на underserved ФО, чтобы не зависал на FF
     *
     *  Целевое qty для bump = max(min_pack, avg_d_district × supply_days):
     *    - есть продажи в ФО (avg_d > 0): target = реальная потребность ФО
     *      (например ЦФО avg_d=2 → 2×14=28 шт)
     *    - нет продаж (avg_d=0, ФО без истории): target = min_pack=N — bootstrap
     *      стартового запаса для дальних регионов
     *
     *  Условия bump для SKU:
     *    1. acceptance проверен (без него не знаем, принимает ли WB).
     *    2. (WB-overstock в ≥1 ФО) ИЛИ (FF-излишек ≥ 3×min_pack).
     *    3. Хотя бы один ФО **underserved**: stock+asm+transit+baseNeed < target_qty
     *       И acceptance на main_wh этого ФО разрешает наш package_type.
     *    4. FF-излишек > 0 (rf_avail - usedFf > 0).
     *
     *  Bump идёт ТОЛЬКО в main_wh underserved ФО (top-1 по orders / share_pct).
     *  Возвращает Map<nm_id, Map<wh, additional_qty>>. */
    const bumpedCells = useMemo(() => {
        const result = new Map<number, Map<string, number>>();
        if (minStockPerMain <= 0 || !data?.articles || acceptanceMap.size === 0) return result;

        const OVERSTOCK_DAYS = 21; // ФО считается перетарен если >= 3 нед запаса
        // Reverse map: district → main_wh (top-1 по share_pct из cold-start).
        const mainWhPerDistrict = new Map<string, string>();
        for (const w of coldStartData?.main_warehouses ?? []) {
            if (!mainWhPerDistrict.has(w.district_key)) {
                mainWhPerDistrict.set(w.district_key, w.warehouse);
            }
        }

        for (const a of data.articles) {
            if (newcomerSet.has(a.nm_id)) continue;
            const acceptanceItem = acceptanceMap.get(a.nm_id);
            if (!acceptanceItem) continue;
            const skuPackages = new Set<string>(
                (acceptanceItem.splits ?? []).map(s => s.package_type),
            );

            // Шаг 1: агрегируем coverage per district по всем wbWarehouses.
            type DistAgg = { stock: number; asm: number; transit: number; need: number; avgD: number };
            const byDistrict = new Map<string, DistAgg>();
            for (const wh of wbWarehouses) {
                const d = warehouseToDistrict.get(wh.name);
                if (!d || d === 'unknown' || d === 'abroad') continue;
                const whData = data.warehouses.find(w => w.name === wh.name);
                const cell = whData?.articles?.[a.nm_id];
                const prev = byDistrict.get(d) ?? { stock: 0, asm: 0, transit: 0, need: 0, avgD: 0 };
                prev.stock += cell?.stock || 0;
                prev.asm += a.asm_by_warehouse?.[wh.name] || 0;
                prev.transit += a.transit_by_warehouse?.[wh.name] || 0;
                prev.need += getBaseArticleWbNeed(a, wh.name);
                prev.avgD += cell?.avg_daily || 0;
                byDistrict.set(d, prev);
            }

            // Шаг 2: классифицируем ФО на overstocked / understocked.
            // Target qty для каждого ФО = max(min_pack, avg_d_district × supply_days):
            //   - есть продажи → реальная потребность ФО на горизонт планирования
            //   - нет продаж (avg_d=0) → bootstrap min_pack=N (старт с пустого)
            // Understocked = coverage < target. Это даёт правильный «bump до спроса»,
            // а не «5 шт всем» — ЦФО с avg_d=2 получит 28 шт (2×14), а ДВ без
            // продаж получит 5 шт (bootstrap).
            let hasWbOverstock = false;
            const understockedDistricts: { d: string; targetQty: number }[] = [];
            for (const [d, agg] of byDistrict.entries()) {
                const total = agg.stock + agg.asm + agg.transit;
                const daysCovered = agg.avgD > 0 ? total / agg.avgD : Infinity;
                const isOverstock = (
                    daysCovered >= OVERSTOCK_DAYS
                    || (agg.avgD === 0 && total >= 3 * minStockPerMain)
                );
                if (isOverstock) hasWbOverstock = true;
                const totalWithPlan = total + agg.need;
                // Target по спросу ФО; min_pack — нижняя граница для bootstrap.
                const targetQty = Math.max(minStockPerMain, Math.round(agg.avgD * supplyDays));
                if (totalWithPlan < targetQty) {
                    understockedDistricts.push({ d, targetQty });
                }
            }

            // Шаг 3: остаток FF после Hamilton-cap.
            const totalRfAvail = Object.values(a.rf_stocks).reduce((s, v) => s + (v?.available || 0), 0);
            let usedFf = 0;
            for (const wh of wbWarehouses) {
                usedFf += getBaseArticleWbNeed(a, wh.name);
            }
            const ffSurplus = Math.max(0, totalRfAvail - usedFf);
            // FF-излишек = есть мёртвый груз на FF, который Hamilton не запланировал
            // (total_need < rf_avail, профицит SKU в целом). Активирует bump
            // независимо от того, перетарен ли где-то WB — иначе SKU с FF-стоком
            // 500шт но без WB-overstock «зависает» в FF без распределения.
            const hasFfSurplus = ffSurplus >= 3 * minStockPerMain;

            // Дисбаланс = (WB-overstock в ФО ИЛИ FF-излишек) И understocked ФО.
            // Только понижение без overstock — bump не нужен (общий дефицит SKU).
            // Без understocked — bump некуда.
            if ((!hasWbOverstock && !hasFfSurplus) || understockedDistricts.length === 0) continue;
            if (ffSurplus <= 0) continue;
            let remaining = ffSurplus;

            // Шаг 4: bump ТОЛЬКО в main_wh underserved ФО, по убыванию приоритета
            // (ДВ+СФО первым — самые проблемные; потом УФО, ПФО, СЗФО).
            const priorityOrder = ['far_east_siberia', 'ural', 'volga', 'northwest', 'south_caucasus', 'central'];
            const orderedUnderstocked = understockedDistricts
                .slice()
                .sort((x, y) => priorityOrder.indexOf(x.d) - priorityOrder.indexOf(y.d));

            const perSku = new Map<string, number>();
            for (const { d, targetQty } of orderedUnderstocked) {
                if (remaining <= 0) break;
                const mainWh = mainWhPerDistrict.get(d);
                if (!mainWh) continue;
                const acceptance = acceptanceItem.availability?.[mainWh];
                if (!acceptance) continue;
                const canSendThisSku = (
                    (skuPackages.has('BOX') && acceptance.can_box)
                    || (skuPackages.has('MONOPALLET') && acceptance.can_monopallet)
                    || (skuPackages.has('SUPERSAFE') && acceptance.can_supersafe)
                );
                if (!canSendThisSku) continue;
                const agg = byDistrict.get(d);
                if (!agg) continue;
                const currentAtDistrict = agg.stock + agg.asm + agg.transit + agg.need;
                if (currentAtDistrict >= targetQty) continue;
                const bump = Math.min(targetQty - currentAtDistrict, remaining);
                if (bump > 0) {
                    perSku.set(mainWh, bump);
                    remaining -= bump;
                }
            }
            if (perSku.size > 0) result.set(a.nm_id, perSku);
        }
        return result;
    }, [minStockPerMain, supplyDays, data, newcomerSet, wbWarehouses, acceptanceMap, getBaseArticleWbNeed, coldStartData, warehouseToDistrict]);

    /** Итоговый per-cell need: base + client-side bump (если активен «Дораспределить»). */
    const getArticleWbNeed = useCallback((article: NeedArticle, whName: string): number => {
        const base = getBaseArticleWbNeed(article, whName);
        const bump = bumpedCells.get(article.nm_id)?.get(whName) || 0;
        return base + bump;
    }, [getBaseArticleWbNeed, bumpedCells]);

    /** Per-SKU total к отгрузке (Hamilton-cap + bump из «Дораспределить»).
     *  Это итоговый план отгрузки, который пойдёт в сборку. */
    const getSendWithBump = useCallback((nmId: number, canSend: number): number => {
        const bumpMap = bumpedCells.get(nmId);
        if (!bumpMap) return canSend;
        let bumpSum = 0;
        for (const q of bumpMap.values()) bumpSum += q;
        return canSend + bumpSum;
    }, [bumpedCells]);

    /** Сколько шт добавлено через «Дораспределить» суммарно. Показывается в кнопке
     *  чтобы пользователь видел реальное действие фичи. */
    const bumpTotal = useMemo(() => {
        let sum = 0;
        for (const skuMap of bumpedCells.values()) {
            for (const q of skuMap.values()) sum += q;
        }
        return sum;
    }, [bumpedCells]);

    /** Returns 'box' | 'mono' | 'super' | 'closed' | null (null = не проверяли). */
    const getCellAcceptanceMarks = useCallback(
        (nmId: number, whName: string): {
            box: boolean; mono: boolean; super: boolean; closed: boolean; checked: boolean;
            box_free?: number; mono_free?: number; super_free?: number;
            box_paid?: number; mono_paid?: number; super_paid?: number;
            box_min?: number | null; mono_min?: number | null; super_min?: number | null;
        } => {
            const checked = acceptanceMap.get(nmId);
            if (!checked) return { box: false, mono: false, super: false, closed: false, checked: false };
            const flags = checked.availability?.[whName];
            if (!flags) return { box: false, mono: false, super: false, closed: true, checked: true };
            // Все доступные на складе типы сразу — пользователь видит полный набор,
            // а не «лучший по приоритету». Иначе при фильтре «Моно» bi-modal склады
            // (Электросталь/Краснодар) показывают только 📦 и 📐 не виден совсем.
            return {
                box: !!flags.can_box,
                mono: !!flags.can_monopallet,
                super: !!flags.can_supersafe,
                closed: !flags.can_box && !flags.can_monopallet && !flags.can_supersafe,
                checked: true,
                box_free: flags.box_meta?.free_days_14,
                mono_free: flags.mono_meta?.free_days_14,
                super_free: flags.super_meta?.free_days_14,
                box_paid: flags.box_meta?.paid_days_14,
                mono_paid: flags.mono_meta?.paid_days_14,
                super_paid: flags.super_meta?.paid_days_14,
                box_min: flags.box_meta?.min_coefficient ?? null,
                mono_min: flags.mono_meta?.min_coefficient ?? null,
                super_min: flags.super_meta?.min_coefficient ?? null,
            };
        },
        [acceptanceMap],
    );

    /** Счётчики кнопок-фильтров — без учёта новинок (они идут отдельно). */
    const nonNewcomerCounts = useMemo(() => {
        const result = { all: 0, deficit: 0, can_send: 0, no_wb: 0, no_stock: 0 };
        if (!data?.articles) return result;
        for (const a of data.articles) {
            if (newcomerSet.has(a.nm_id)) continue;
            result.all++;
            if (a.deficit > 0) result.deficit++;
            if (a.can_send > 0) result.can_send++;
            // «Нет на WB» = stocks_wb=0 И есть у нас (RF / в сборке / в пути).
            // Имеет смысл смотреть на эти SKU: WB-сток пуст, но есть чем дослать.
            // «Нет остатков» = stocks_wb=0 И RF=0 И в сборке=0 И в пути=0 —
            // реально нет нигде, нужно производство/закупка, отправлять нечего.
            if (a.stocks_wb === 0) {
                const totalRf = Object.values(a.rf_stocks).reduce((s, v) => s + (v?.stock || 0), 0);
                const haveOurs = totalRf > 0 || (a.in_assembly || 0) > 0 || (a.in_transit || 0) > 0;
                if (haveOurs) result.no_wb++;
                else result.no_stock++;
            }
        }
        return result;
    }, [data, newcomerSet]);

    const filteredArticles = useMemo(() => {
        if (!data?.articles) return [];
        return data.articles.filter(a => {
            // Новинки (cold-start) рендерятся отдельно на вкладке «🆕 Новинки» —
            // на основной таблице их скрываем. У них total_need=0 (нет orders),
            // в WB-колонках прочерки, создают визуальный шум.
            if (newcomerSet.has(a.nm_id)) return false;
            if (brandFilter && a.brand !== brandFilter) return false;
            if (subjectFilter && a.subject !== subjectFilter) return false;
            if (searchQuery) {
                const q = searchQuery.toLowerCase();
                if (!a.vendor_code.toLowerCase().includes(q)) return false;
            }
            if (statusFilter === 'deficit' && a.deficit <= 0) return false;
            if (statusFilter === 'can_send' && a.can_send <= 0) return false;
            if (statusFilter === 'no_wb') {
                if (a.stocks_wb > 0) return false;
                // Должно быть хоть что-то наше (RF / в сборке / в пути).
                const totalRf = Object.values(a.rf_stocks).reduce((s, v) => s + (v?.stock || 0), 0);
                const haveOurs = totalRf > 0 || (a.in_assembly || 0) > 0 || (a.in_transit || 0) > 0;
                if (!haveOurs) return false;
            }
            if (statusFilter === 'no_stock') {
                if (a.stocks_wb > 0) return false;
                const totalRf = Object.values(a.rf_stocks).reduce((s, v) => s + (v?.stock || 0), 0);
                if (totalRf > 0 || (a.in_assembly || 0) > 0 || (a.in_transit || 0) > 0) return false;
            }
            if (packageFilter !== 'none') {
                const it = acceptanceMap.get(a.nm_id);
                if (!it) return false; // не проверяли — вне фильтра
                const expected = packageFilter === 'box' ? 'BOX' : 'MONOPALLET';
                // SKU попадает в фильтр если ХОТЯ БЫ ОДИН split имеет такой package_type
                const hasType = (it.splits ?? []).some(s => s.package_type === expected);
                if (!hasType) return false;
            }
            return true;
        });
    }, [data, brandFilter, subjectFilter, searchQuery, statusFilter, packageFilter, acceptanceMap, newcomerSet]);

    /** WB-колонки в матрице, отфильтрованные по packageFilter.
     *  При packageFilter='mono' — оставляем только склады где для хотя бы одного
     *  отфильтрованного SKU acceptanceMap.availability[wh].can_monopallet=true.
     *  При packageFilter='box' — аналогично can_box. Если acceptance не запускали
     *  (acceptanceMap пустой) — фильтр не работает, возвращаем все склады. */
    const visibleWbWarehouses = useMemo(() => {
        const base = (packageFilter === 'none' || acceptanceMap.size === 0)
            ? wbWarehouses
            : wbWarehouses.filter(wh => {
                // «Моно» = склад принимает ТОЛЬКО моно (короб запрещён): can_monopallet && !can_box.
                // Это даёт «чистый моно» (Владивосток, Хабаровск). Bi-modal склады
                // (Краснодар, Электросталь — принимают и короб и моно) скрываются,
                // потому что пользователь видя 📦 рядом с цифрой думал «это короб».
                // «Короб» = склад принимает коробом: can_box (моно может тоже быть, не критично).
                for (const a of filteredArticles) {
                    const flags = acceptanceMap.get(a.nm_id)?.availability?.[wh.name];
                    if (!flags) continue;
                    if (packageFilter === 'box' && flags.can_box) return true;
                    if (packageFilter === 'mono' && flags.can_monopallet && !flags.can_box) return true;
                }
                return false;
            });
        // Сортируем по федеральному округу (DISTRICT_ORDER), сохраняя внутренний порядок
        // (stable sort через index). Нужно для group-header'а — склады одного ФО рядом,
        // одна цветная полоса на округ как в индексе локализации.
        const districtIdx = (k?: string): number => {
            const i = DISTRICT_ORDER.indexOf((k || 'unknown') as typeof DISTRICT_ORDER[number]);
            return i < 0 ? DISTRICT_ORDER.length : i;  // unknown — в конец
        };
        return base
            .map((wh, idx) => ({ wh, idx, di: districtIdx(wh.district_key) }))
            .sort((a, b) => a.di - b.di || a.idx - b.idx)
            .map(x => x.wh);
    }, [wbWarehouses, packageFilter, filteredArticles, acceptanceMap]);

    /* Counters для бэйджей фильтров: SKU имеющий ХОТЯ БЫ ОДИН split такого типа */
    const acceptanceCounts = useMemo(() => {
        let box = 0, mono = 0;
        for (const it of acceptanceMap.values()) {
            const types = new Set((it.splits ?? []).map(s => s.package_type));
            if (types.has('BOX')) box++;
            if (types.has('MONOPALLET')) mono++;
        }
        return { box, mono };
    }, [acceptanceMap]);

    const sortedArticles = useMemo(() => {
        return [...filteredArticles].sort((a, b) => {
            let va: number | string;
            let vb: number | string;

            if (sortCol === 'vendor_code') {
                va = a.vendor_code; vb = b.vendor_code;
            } else if (sortCol === 'revenue_30d') {
                // Articles without revenue sort last regardless of direction
                if (!a.revenue_30d && !b.revenue_30d) return 0;
                if (!a.revenue_30d) return 1;
                if (!b.revenue_30d) return -1;
                va = a.revenue_30d; vb = b.revenue_30d;
            } else if (sortCol === 'total_need') {
                va = a.total_need; vb = b.total_need;
            } else if (sortCol === 'in_assembly') {
                va = a.in_assembly; vb = b.in_assembly;
            } else if (sortCol === 'in_transit') {
                va = a.in_transit; vb = b.in_transit;
            } else if (sortCol === 'can_send') {
                va = a.can_send; vb = b.can_send;
            } else if (sortCol === 'send_with_bump') {
                va = getSendWithBump(a.nm_id, a.can_send);
                vb = getSendWithBump(b.nm_id, b.can_send);
            } else if (sortCol === 'deficit') {
                va = a.deficit; vb = b.deficit;
            } else if (sortCol.startsWith('rf_')) {
                const whId = parseInt(sortCol.replace('rf_', ''), 10);
                va = a.rf_stocks[whId]?.available || 0;
                vb = b.rf_stocks[whId]?.available || 0;
            } else if (sortCol.startsWith('wb_')) {
                const whName = sortCol.replace('wb_', '');
                va = getArticleWbNeed(a, whName);
                vb = getArticleWbNeed(b, whName);
            } else {
                va = 0; vb = 0;
            }
            if (typeof va === 'string' && typeof vb === 'string') {
                return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
            }
            return sortDir === 'asc' ? (va as number) - (vb as number) : (vb as number) - (va as number);
        });
    }, [filteredArticles, sortCol, sortDir, getArticleWbNeed, getSendWithBump]);

    /* ── Totals ── */

    /* Cold-start: сортировка + ИТОГО (под колонками) */
    const sortedColdStartRows = useMemo(() => {
        if (!coldStartData) return [];
        const rows = [...coldStartData.rows];
        const dir = coldStartSortDir === 'asc' ? 1 : -1;
        const col = coldStartSortCol;
        rows.sort((a, b) => {
            if (col === 'vendor_code') {
                return dir * (a.article_seller || '').localeCompare(b.article_seller || '');
            }
            let va = 0, vb = 0;
            if (col === 'revenue_30d') { va = a.revenue_30d; vb = b.revenue_30d; }
            else if (col === 'rf_qty') { va = a.rf_qty; vb = b.rf_qty; }
            else if (col === 'wb_qty') { va = a.wb_qty; vb = b.wb_qty; }
            else if (col === 'in_assembly_total') { va = a.in_assembly_total; vb = b.in_assembly_total; }
            else if (col === 'total_allocated') { va = a.total_allocated; vb = b.total_allocated; }
            else if (col.startsWith('cs_rf_')) {
                const id = parseInt(col.replace('cs_rf_', ''), 10);
                va = a.rf_by_warehouse?.[id] || 0;
                vb = b.rf_by_warehouse?.[id] || 0;
            }
            else if (col.startsWith('cs_wb_')) {
                const wh = col.replace('cs_wb_', '');
                va = a.allocations?.[wh] || 0;
                vb = b.allocations?.[wh] || 0;
            }
            return dir * (va - vb);
        });
        return rows;
    }, [coldStartData, coldStartSortCol, coldStartSortDir]);

    const coldStartTotals = useMemo(() => {
        const t = {
            revenue_30d: 0,
            rf_qty: 0,
            wb_qty: 0,
            in_assembly_total: 0,
            total_allocated: 0,
            rf: {} as Record<number, number>,
            wb: {} as Record<string, number>,
        };
        if (!coldStartData) return t;
        for (const row of coldStartData.rows) {
            t.revenue_30d += row.revenue_30d || 0;
            t.rf_qty += row.rf_qty || 0;
            t.wb_qty += row.wb_qty || 0;
            t.in_assembly_total += row.in_assembly_total || 0;
            const overrideQty = coldStartQtyOverrides[row.nm_id];
            t.total_allocated += overrideQty !== undefined ? overrideQty : (row.total_allocated || 0);
            for (const [whId, qty] of Object.entries(row.rf_by_warehouse || {})) {
                const id = Number(whId);
                t.rf[id] = (t.rf[id] || 0) + (qty || 0);
            }
            for (const [wh, qty] of Object.entries(row.allocations || {})) {
                t.wb[wh] = (t.wb[wh] || 0) + (qty || 0);
            }
        }
        return t;
    }, [coldStartData, coldStartQtyOverrides]);

    const handleColdStartSort = (col: string) => {
        if (coldStartSortCol === col) setColdStartSortDir(prev => prev === 'asc' ? 'desc' : 'asc');
        else { setColdStartSortCol(col); setColdStartSortDir('desc'); }
    };
    const csSortArrow = (col: string) => coldStartSortCol === col ? (coldStartSortDir === 'asc' ? ' ↑' : ' ↓') : '';

    const totals = useMemo(() => {
        const t = {
            total_need: 0,
            revenue_30d: 0,
            in_assembly: 0,
            in_transit: 0,
            can_send: 0,
            send_with_bump: 0,
            deficit: 0,
            rf: {} as Record<number, number>,
            wb: {} as Record<string, number>,
        };
        for (const a of filteredArticles) {
            t.total_need += a.total_need;
            t.revenue_30d += a.revenue_30d || 0;
            t.in_assembly += a.in_assembly;
            t.in_transit += a.in_transit;
            t.can_send += a.can_send;
            t.send_with_bump += getSendWithBump(a.nm_id, a.can_send);
            t.deficit += a.deficit;
            if (data?.rf_warehouses) {
                for (const wh of data.rf_warehouses) {
                    t.rf[wh.id] = (t.rf[wh.id] || 0) + (a.rf_stocks[wh.id]?.available || 0);
                }
            }
            for (const wh of visibleWbWarehouses) {
                let n = getArticleWbNeed(a, wh.name);
                // При packageFilter обнуляем ячейки где для этой пары (SKU, склад)
                // нет нужного пакета. «Моно» = любой склад принимающий моно
                // (в т.ч. bi-modal). Должно совпадать с per-cell render выше.
                if (packageFilter !== 'none') {
                    const flags = acceptanceMap.get(a.nm_id)?.availability?.[wh.name];
                    const allowed = packageFilter === 'mono'
                        ? !!flags?.can_monopallet
                        : !!flags?.can_box;
                    if (!allowed) n = 0;
                }
                t.wb[wh.name] = (t.wb[wh.name] || 0) + n;
            }
        }
        return t;
    }, [filteredArticles, data, visibleWbWarehouses, getArticleWbNeed, getSendWithBump, packageFilter, acceptanceMap]);

    /* ── Checkbox logic ── */

    const allChecked = filteredArticles.length > 0 && filteredArticles.every(a => checkedIds.has(a.nm_id));

    const toggleAll = () => {
        if (allChecked) {
            setCheckedIds(new Set());
        } else {
            setCheckedIds(new Set(filteredArticles.map(a => a.nm_id)));
        }
    };

    const toggleOne = (nmId: number) => {
        setCheckedIds(prev => {
            const next = new Set(prev);
            if (next.has(nmId)) next.delete(nmId);
            else next.add(nmId);
            return next;
        });
    };

    const checkedCount = checkedIds.size;

    const assemblyTotal = useMemo(() => {
        if (!data) return 0;
        // Multi-source: суммируем по ВСЕМ ФФ-складам (как делает handleCreateAssembly).
        // Берём min(total_avail_по_всем_RF, plan = can_send + bump).
        let sum = 0;
        for (const nmId of checkedIds) {
            const newcomer = coldStartData?.rows.find(r => r.nm_id === nmId);
            if (newcomer) {
                const overrideQty = coldStartQtyOverrides[nmId];
                const useOverride = overrideQty !== undefined && overrideQty !== newcomer.total_allocated;
                const total = useOverride
                    ? recomputeColdStartAlloc(overrideQty, filteredMainWarehouses, coldStartMinPack).total
                    : newcomer.total_allocated;
                sum += total;
                continue;
            }
            const article = data.articles.find(a => a.nm_id === nmId);
            if (!article) continue;
            const totalAvail = (data.rf_warehouses ?? []).reduce(
                (s, w) => s + (article.rf_stocks[w.id]?.available || 0), 0,
            );
            const plan = getSendWithBump(nmId, article.can_send);
            sum += Math.min(totalAvail, plan);
        }
        return sum;
    }, [checkedIds, data, coldStartData, filteredMainWarehouses, coldStartQtyOverrides, coldStartMinPack, getSendWithBump]);

    const checkedNewcomersCount = useMemo(() => {
        let n = 0;
        for (const id of checkedIds) if (newcomerSet.has(id)) n++;
        return n;
    }, [checkedIds, newcomerSet]);

    /* ── Create assembly draft (unified: обычные + новинки в один draft) ── */

    const handleCreateAssembly = useCallback(async () => {
        if (!data || checkedIds.size === 0) return;
        setCreatingAssembly(true);

        try {
            const draftRows: AssemblyDraftRow[] = [];
            const skippedNoBarcode: string[] = [];
            const skippedNoQty: string[] = [];
            const skippedNewcomer: string[] = [];
            const newcomerByNm = new Map<number, ColdStartTableRow>();
            for (const r of coldStartData?.rows ?? []) newcomerByNm.set(r.nm_id, r);

            // Все FF-склады проекта — для multi-source аллокации.
            // assemblyWarehouseId (если выбран) идёт первым как «приоритетный»,
            // остальные подключаются greedy по available если на приоритетном не хватило.
            const allRfWarehouses = data.rf_warehouses || [];

            /** Greedy-распределение qty по RF-складам. preferred первым, потом по убыванию available.
             *  Возвращает {String(rf_id): take} только для складов где take > 0. */
            const allocateFromSources = (
                qty: number,
                rfStocks: Record<number, { available: number }>,
            ): Record<string, number> => {
                if (qty <= 0) return {};
                const sorted = [...allRfWarehouses].sort((a, b) => {
                    if (assemblyWarehouseId != null) {
                        if (a.id === assemblyWarehouseId) return -1;
                        if (b.id === assemblyWarehouseId) return 1;
                    }
                    return (rfStocks[b.id]?.available || 0) - (rfStocks[a.id]?.available || 0);
                });
                const out: Record<string, number> = {};
                let remaining = qty;
                for (const w of sorted) {
                    if (remaining <= 0) break;
                    const avail = rfStocks[w.id]?.available || 0;
                    if (avail <= 0) continue;
                    const take = Math.min(remaining, avail);
                    out[String(w.id)] = take;
                    remaining -= take;
                }
                return out;
            };

            for (const nmId of checkedIds) {
                const article = data.articles.find(a => a.nm_id === nmId);
                const newcomer = newcomerByNm.get(nmId);
                const barcode = article?.barcode;
                const vendor = article?.vendor_code || newcomer?.article_seller || `nm=${nmId}`;
                if (!barcode) {
                    skippedNoBarcode.push(vendor);
                    continue;
                }

                // Новинки cold-start идут ОТДЕЛЬНОЙ заявкой — пропускаем здесь.
                // Для них есть свой workflow в вкладке «Новинки».
                if (newcomer) {
                    skippedNewcomer.push(vendor);
                    continue;
                }

                let qty = 0;
                let tgt: Record<string, number> = {};
                let srcAlloc: Record<string, number> = {};

                if (article) {
                    // Обычный SKU → используем итоговый план = can_send + bump из «Дораспределить»
                    // (то же что видно в колонке «Отправить» матрицы). Без этого SKU с
                    // total_need=0 но активным bump попадали в skippedNoQty.
                    // available = Σ по всем FF-складам (multi-source).
                    const totalAvail = allRfWarehouses.reduce(
                        (s, w) => s + (article.rf_stocks[w.id]?.available || 0), 0,
                    );
                    const planTotal = getSendWithBump(nmId, article.can_send);
                    qty = Math.min(totalAvail, planTotal);
                    if (qty > 0) {
                        // Определяем preferred RF для resolveBoxMult — assemblyWarehouseId если он
                        // имеет сток, иначе RF с максимальным available.
                        const sortedRf = [...allRfWarehouses].sort((a, b) =>
                            (article.rf_stocks[b.id]?.available || 0) - (article.rf_stocks[a.id]?.available || 0),
                        );
                        const ppbRfId = (assemblyWarehouseId != null
                            && (article.rf_stocks[assemblyWarehouseId]?.available || 0) > 0)
                            ? assemblyWarehouseId
                            : (sortedRf[0]?.id ?? 0);
                        const { ppb, use } = resolveBoxMult(nmId, ppbRfId);
                        // whNeeds = per-cell qty матрицы (base + bump), а не сырой backend need.
                        const whNeeds = data.warehouses
                            .map(wh => ({ name: wh.name, need: getArticleWbNeed(article, wh.name) }))
                            .filter(w => w.need > 0);

                        if (ppb && ppb > 0 && use && qty >= ppb) {
                            // Кратность задана — раздаём целыми коробками,
                            // минимум 1 коробка каждому нуждающемуся складу.
                            tgt = distributeByBoxMultiple(whNeeds, qty, ppb);
                            qty = Object.values(tgt).reduce((s, v) => s + v, 0);
                        } else {
                            // Fallback: текущая логика без округления.
                            let remaining = qty;
                            for (const w of whNeeds) {
                                if (remaining <= 0) break;
                                const give = Math.min(remaining, w.need);
                                tgt[w.name] = give;
                                remaining -= give;
                            }
                            if (remaining > 0 && Object.keys(tgt).length > 0) {
                                const firstKey = Object.keys(tgt)[0];
                                tgt[firstKey] = (tgt[firstKey] || 0) + remaining;
                            }
                        }
                        srcAlloc = allocateFromSources(qty, article.rf_stocks);
                    }
                }

                if (qty <= 0 || Object.keys(tgt).length === 0 || Object.keys(srcAlloc).length === 0) {
                    skippedNoQty.push(vendor);
                    continue;
                }

                // Если acceptance проверен и вернул splits — используем post-redistribute
                // распределение per package_type (одна транспортная единица = один тип).
                // Иначе — fallback на исходный tgt с package_type=BOX.
                // src на каждый split распределяем пропорционально доле splitQty/qty.
                const acceptanceItem = acceptanceMap.get(nmId);
                const allSplits = acceptanceItem?.splits ?? [];
                // Уважаем фильтр Короб/Моно из шапки: если выбран короб — не пихаем
                // моно-части в draft (и наоборот). Без этого SKU с двумя splits
                // (BOX+MONOPALLET) попадали в draft обеими частями, даже когда
                // пользователь явно выбрал только один тип.
                const splits = packageFilter === 'box'
                    ? allSplits.filter(s => s.package_type === 'BOX')
                    : packageFilter === 'mono'
                    ? allSplits.filter(s => s.package_type === 'MONOPALLET')
                    : allSplits;
                if (acceptanceItem && splits.length > 0) {
                    for (const split of splits) {
                        const splitQty = Object.values(split.distribution).reduce((a, b) => a + b, 0);
                        if (splitQty <= 0) continue;
                        // Пропорционально распределяем srcAlloc на split через largest-remainder:
                        // floor по каждому ФФ + раздаём остаток по самым большим дробным частям.
                        // Без этого `Math.round` для .5-долей округлял обе стороны вверх и
                        // Σ ист становился > Σ цель (та самая разница 12543 vs 12541).
                        const splitSrc: Record<string, number> = {};
                        const fractions: Array<{ rfId: string; frac: number }> = [];
                        let allocated = 0;
                        for (const [rfId, take] of Object.entries(srcAlloc)) {
                            const exact = (take * splitQty) / qty;
                            const floor = Math.floor(exact);
                            splitSrc[rfId] = floor;
                            allocated += floor;
                            fractions.push({ rfId, frac: exact - floor });
                        }
                        fractions.sort((a, b) => b.frac - a.frac || a.rfId.localeCompare(b.rfId));
                        let remainder = splitQty - allocated;
                        for (let i = 0; i < fractions.length && remainder > 0; i++) {
                            splitSrc[fractions[i].rfId] += 1;
                            remainder -= 1;
                        }
                        // Cleanup: убираем нулевые записи, чтобы backend не отвергал
                        // (commit_draft требует Σ src > 0 и Σ src == Σ tgt).
                        for (const k of Object.keys(splitSrc)) {
                            if (splitSrc[k] <= 0) delete splitSrc[k];
                        }
                        draftRows.push({
                            nm_id: nmId,
                            barcode,
                            vendor_code: vendor,
                            src: splitSrc,
                            tgt: { ...split.distribution },
                            package_type: split.package_type,
                        });
                    }
                } else if (!acceptanceItem || allSplits.length === 0) {
                    // Acceptance не проверялся (или вернул пустой splits) — fallback.
                    // packageFilter уважаем: если выбран Моно, default MONOPALLET, иначе BOX.
                    const fallbackPkg: PackageType = packageFilter === 'mono'
                        ? 'MONOPALLET'
                        : (acceptanceItem?.package_type ?? 'BOX') as PackageType;
                    draftRows.push({
                        nm_id: nmId,
                        barcode,
                        vendor_code: vendor,
                        src: srcAlloc,
                        tgt,
                        package_type: fallbackPkg,
                    });
                }
                // Если acceptance был, splits были, но после фильтрации все отсеялись —
                // пропускаем SKU (не подходит под текущий packageFilter).
            }

            if (draftRows.length === 0) {
                const lines: string[] = ['Не удалось собрать ни одной позиции:'];
                if (skippedNoBarcode.length) {
                    lines.push('', `Нет barcode (${skippedNoBarcode.length}):`, ...skippedNoBarcode.slice(0, 10).map(s => `  • ${s}`));
                    if (skippedNoBarcode.length > 10) lines.push(`  …и ещё ${skippedNoBarcode.length - 10}`);
                }
                if (skippedNoQty.length) {
                    lines.push('', `Нечего отправлять (${skippedNoQty.length}):`, ...skippedNoQty.slice(0, 10).map(s => `  • ${s}`));
                    if (skippedNoQty.length > 10) lines.push(`  …и ещё ${skippedNoQty.length - 10}`);
                }
                if (skippedNewcomer.length) {
                    lines.push('', `Новинки (создавай отдельной заявкой во вкладке «Новинки») — ${skippedNewcomer.length}:`,
                        ...skippedNewcomer.slice(0, 10).map(s => `  • ${s}`));
                    if (skippedNewcomer.length > 10) lines.push(`  …и ещё ${skippedNewcomer.length - 10}`);
                }
                alert(lines.join('\n'));
                setCreatingAssembly(false);
                return;
            }

            // Newcomer'ы создаются отдельной заявкой (см. вкладку «Новинки»).
            const targetNames = Array.from(new Set(draftRows.flatMap(r => Object.keys(r.tgt))));

            // Union всех использованных RF-источников по всем строкам draft.
            const usedSourceIds = new Set<number>();
            for (const r of draftRows) {
                for (const k of Object.keys(r.src)) {
                    const n = Number(k);
                    if (Number.isFinite(n) && n > 0) usedSourceIds.add(n);
                }
            }
            const sourceIdsList = usedSourceIds.size > 0
                ? [...usedSourceIds]
                : (assemblyWarehouseId != null ? [assemblyWarehouseId] : []);

            const draft = await api.createAssemblyDraft({
                distribution: {
                    source_warehouse_ids: sourceIdsList,
                    target_warehouse_names: targetNames,
                    rows: draftRows,
                    pallets_count: 1,
                    pallet_weight_kg: 0,
                    estimated_ready_date: null,
                    cold_start_shares: null,
                },
            });

            if (slug) {
                router.push(`/p/${slug}/warehouse/assembly/distribute?draft=${draft.id}`);
            }
        } catch (e: unknown) {
            alert(`Ошибка создания черновика: ${e instanceof Error ? e.message : String(e)}`);
        } finally {
            setCreatingAssembly(false);
        }
    }, [data, assemblyWarehouseId, checkedIds, router, slug, acceptanceMap, packageFilter,
        coldStartData, filteredMainWarehouses, coldStartQtyOverrides, coldStartMinPack,
        getSendWithBump, getArticleWbNeed]);

    /* (cold-start теперь идёт через объединённый handleCreateAssembly выше) */

    /* ── Check WB acceptance availability (Проверить приёмку WB) ── */

    const handleCheckAcceptance = useCallback(async () => {
        if (!data?.articles?.length || !data.warehouses?.length) {
            alert('Нет данных для проверки');
            return;
        }
        setAcceptanceLoading(true);
        setAcceptanceError(null);
        try {
            // Lazy-fetch cold-start если ещё не загружен (для проверки новинок
            // вне вкладки «Новинки»). Без этого новинки проверены не будут.
            let coldStart = coldStartData;
            if (!coldStart) {
                try {
                    coldStart = await api.getColdStartTable(analysisDays, coldStartMinPack);
                    setColdStartData(coldStart);
                } catch (e) {
                    console.warn('cold-start fetch failed during acceptance check', e);
                }
            }

            // Барcоды: основная таблица — из data.articles. Новинки —
            // из coldStart.rows (.barcode там нет, тянем через data.articles по nm_id).
            const barcodeByNm = new Map<number, string>();
            for (const a of data.articles) {
                if (a.barcode) barcodeByNm.set(a.nm_id, a.barcode);
            }

            const items: { nm_id: number; barcode: string; distribution: Record<string, number> }[] = [];
            const skippedNoBarcode: number[] = [];

            // Обычные SKU: distribution полностью пересчитываем через cold-start
            // share_pct округов (если cold_start доступен), а не only own-need.
            // Зачем: warehouse_need_service считает need только по складам где у
            // пользователя есть продажи. Для одеяла нет продаж в ДВ → need[Владивосток]=0,
            // хотя WB кабинет рекомендует моно туда (ДВ = 18% продаж проекта).
            // Распределение через share_pct даёт qty на ВСЕ main_warehouses
            // пропорционально доле округа, без double-counting (sum = total_need).
            for (const a of data.articles) {
                if (!a.barcode) {
                    if ((a.total_need || 0) > 0) skippedNoBarcode.push(a.nm_id);
                    continue;
                }
                let distribution: Record<string, number> = {};
                const totalNeed = a.total_need || 0;
                // Используем filteredCs (тот же фильтр что и filteredMainWarehouses, но
                // на локальном lazy-fetched coldStart — он может не совпадать с coldStartData).
                const filteredCs = coldStart?.main_warehouses?.filter(
                    w => w.share_pct > 0 && !isSpecWarehouse(w.warehouse),
                ) || [];
                if (filteredCs.length > 0 && totalNeed > 0) {
                    // Replace own-need with share_pct distribution.
                    distribution = recomputeColdStartAlloc(
                        totalNeed, filteredCs, coldStartMinPack,
                    ).alloc;
                } else {
                    // Fallback: own-need (без cold-start доступного).
                    for (const wh of data.warehouses) {
                        const need = wh.articles?.[a.nm_id]?.need || 0;
                        if (need > 0) distribution[wh.name] = need;
                    }
                }
                // Перезатаренные SKU (total_need=0 И own-need везде 0) — раньше пропускались.
                // Теперь шлём в acceptance с фиктивным probe-distribution (5 шт по share_pct
                // главных складов ФО) чтобы получить availability-флаги. Нужно для
                // «Дораспределить»: bump на пустые региональные склады из FF-излишка.
                if (Object.keys(distribution).length === 0) {
                    const totalRfAvail = Object.values(a.rf_stocks).reduce((s, v) => s + (v?.available || 0), 0);
                    if (totalRfAvail > 0 && filteredCs.length > 0) {
                        distribution = recomputeColdStartAlloc(5 * filteredCs.length, filteredCs, coldStartMinPack).alloc;
                    }
                }
                if (Object.keys(distribution).length > 0) {
                    items.push({ nm_id: a.nm_id, barcode: a.barcode, distribution });
                }
            }

            // Новинки (cold-start): distribution = .allocations (или recompute от override).
            // Они часто не в data.articles потому что у них wb_qty=0 и нет orders;
            // barcode берём напрямую из coldStart row (backend отдаёт его в row.barcode).
            if (coldStart?.rows?.length) {
                for (const row of coldStart.rows) {
                    const barcode = row.barcode || barcodeByNm.get(row.nm_id);
                    if (!barcode) {
                        skippedNoBarcode.push(row.nm_id);
                        continue;
                    }
                    if (items.some(x => x.nm_id === row.nm_id)) continue; // dedupe
                    const overrideQty = coldStartQtyOverrides[row.nm_id];
                    const useOverride = overrideQty !== undefined && overrideQty !== row.total_allocated;
                    const filteredCs = coldStart?.main_warehouses?.filter(
                        w => w.share_pct > 0 && !isSpecWarehouse(w.warehouse),
                    ) || [];
                    const alloc = useOverride
                        ? recomputeColdStartAlloc(overrideQty, filteredCs, coldStartMinPack).alloc
                        : row.allocations;
                    const distribution: Record<string, number> = {};
                    for (const [wh, q] of Object.entries(alloc)) {
                        if (q > 0) distribution[wh] = q;
                    }
                    if (Object.keys(distribution).length > 0) {
                        items.push({ nm_id: row.nm_id, barcode, distribution });
                    }
                }
            }

            if (skippedNoBarcode.length > 0) {
                console.warn(`[acceptance] skipped ${skippedNoBarcode.length} SKU без barcode:`, skippedNoBarcode.slice(0, 20));
            }

            if (items.length === 0) {
                alert('Нет SKU с ненулевой потребностью — нечего проверять.');
                return;
            }

            // force=true если пользователь уже видит результат (т.е. жмёт «🔄 Обновить»)
            // чтобы пропустить Redis-кэш и получить fresh данные.
            const force = acceptanceMap.size > 0;
            const resp = await api.checkWbAcceptance({ items }, force);
            const map = new Map<number, AcceptanceCheckPerItem>();
            for (const it of resp.items) map.set(it.nm_id, it);
            setAcceptanceMap(map);
            setAcceptanceMoves(resp.moves || []);
            setAcceptanceCheckedAt(resp.checked_at);
            // Persist for F5 — TTL 5 min, согласован с backend Redis-кэшем
            if (typeof window !== 'undefined' && slug) {
                try {
                    window.localStorage.setItem(acceptanceLsKey(slug), JSON.stringify({
                        items: resp.items,
                        moves: resp.moves || [],
                        checked_at: resp.checked_at,
                        saved_at: Date.now(),
                    } satisfies AcceptancePersist));
                } catch { /* quota exceeded — silently skip */ }
            }
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : 'Ошибка проверки приёмки';
            setAcceptanceError(msg);
        } finally {
            setAcceptanceLoading(false);
        }
    }, [data, slug, coldStartData, coldStartQtyOverrides, coldStartMinPack, analysisDays]);

    const handleResetAcceptance = useCallback(() => {
        setAcceptanceMap(new Map());
        setAcceptanceMoves([]);
        setAcceptanceCheckedAt(null);
        setAcceptanceError(null);
        if (typeof window !== 'undefined' && slug) {
            window.localStorage.removeItem(acceptanceLsKey(slug));
        }
        if (packageFilter !== 'none') setPackageFilter('none');
    }, [slug, packageFilter]);

    /* ── Sort ── */

    const handleSort = (col: string) => {
        if (sortCol === col) setSortDir(prev => prev === 'asc' ? 'desc' : 'asc');
        else { setSortCol(col); setSortDir('desc'); }
    };

    const sortArrow = (col: string) => sortCol === col ? (sortDir === 'asc' ? ' \u2191' : ' \u2193') : '';

    /* ── Yellow highlight check ── */

    const isHighlighted = (a: NeedArticle): boolean => {
        if (a.stocks_wb !== 0) return false;
        if (a.in_transit > 0) return false;
        const rfSum = Object.values(a.rf_stocks).reduce((s, v) => s + (v.available || 0), 0);
        return rfSum > 0;
    };

    /* ── Export ── */

    const handleExport = () => {
        if (!data) return;
        const rfWhs = data.rf_warehouses || [];
        const wbWhs = wbWarehouses;
        const header = [
            'Артикул', 'Бренд', 'Категория', `Реализация ${analysisDays}д`, 'Потребность',
            ...rfWhs.map(w => w.name),
            'В сборке', 'В пути', 'Могу отпр.', 'Отправить', 'Дефицит',
            ...wbWhs.map(w => w.name),
        ];
        const rows = sortedArticles.map(a => [
            a.vendor_code, a.brand || '', a.subject || '',
            a.revenue_30d || 0, a.total_need,
            ...rfWhs.map(w => a.rf_stocks[w.id]?.available || 0),
            a.in_assembly, a.in_transit, a.can_send, getSendWithBump(a.nm_id, a.can_send), a.deficit,
            ...wbWhs.map(w => getArticleWbNeed(a, w.name)),
        ]);
        const totalRow = [
            'ИТОГО', '', '', totals.revenue_30d, totals.total_need,
            ...rfWhs.map(w => totals.rf[w.id] || 0),
            totals.in_assembly, totals.in_transit, totals.can_send, totals.send_with_bump, totals.deficit,
            ...wbWhs.map(w => totals.wb[w.name] || 0),
        ];
        rows.push(totalRow);
        exportToExcel([header, ...rows], `Потребность_запас${supplyDays}д_анализ${analysisDays}д`);
    };

    /* ── Styles ── */

    const stickyCheckbox: React.CSSProperties = {
        position: 'sticky', left: 0, zIndex: 3,
        background: '#f5f5f7', padding: '8px 6px', textAlign: 'center',
        width: 36, minWidth: 36, borderBottom: '1px solid var(--color-border)',
    };

    const stickyArticle: React.CSSProperties = {
        position: 'sticky', left: 36, zIndex: 3,
        background: '#f5f5f7', padding: '8px', textAlign: 'left',
        minWidth: 180, fontWeight: 600, fontSize: 12,
        boxShadow: '2px 0 4px rgba(0,0,0,0.05)',
        borderBottom: '1px solid var(--color-border)',
    };

    const thBase: React.CSSProperties = {
        textAlign: 'right', minWidth: 75, cursor: 'pointer', userSelect: 'none',
        fontSize: 11, whiteSpace: 'nowrap', padding: '8px 6px',
        borderBottom: '2px solid var(--color-border)',
    };

    const tdBase: React.CSSProperties = {
        padding: '7px 6px', textAlign: 'right', fontSize: 12,
        borderBottom: '1px solid var(--color-border)',
    };

    /* ── Render ── */

    if (loading && !data) {
        return <div className="glass-card" style={{ textAlign: 'center', padding: 40 }}>Расчёт потребности...</div>;
    }

    if (error && !data) {
        return (
            <div className="glass-card" style={{ textAlign: 'center', padding: 40, color: '#ef4444' }}>
                Ошибка: {error}
                <div style={{ marginTop: 12 }}>
                    <button className="btn btn-sm btn-primary" onClick={load}>Повторить</button>
                </div>
            </div>
        );
    }

    const summary = data?.summary;
    const rfWarehouses = data?.rf_warehouses || [];

    return (
        <div>
            {/* Header */}
            <div className="page-header" style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
                <div>
                <h2 className="page-title">Потребность по складам</h2>
                <p className="page-subtitle">
                    {data
                        ? `${data.total_warehouses} складов \u00B7 ${nonNewcomerCounts.all} артикулов${newcomerSet.size > 0 ? ` + ${newcomerSet.size} новинок` : ''} \u00B7 запас ${supplyDays} дн`
                        : 'Нет данных'}
                </p>
                </div>
                <Link href={`/p/${slug}/warehouse/box-multiplicity`} className="btn btn-secondary btn-sm">
                    📦 Кратность коробок
                </Link>
            </div>

            {/* KPI Cards */}
            {summary && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
                    <div className="glass-card" style={{ padding: '14px 16px' }}>
                        <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>Общая потребность</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{formatNumber(summary.total_need, 0)}</div>
                    </div>
                    <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '3px solid #22c55e' }}>
                        <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>
                            {bumpTotal > 0 ? 'Отправить (с bump)' : 'Могу отправить'}
                        </div>
                        <div style={{ fontSize: 22, fontWeight: 700, color: '#22c55e' }}>
                            {formatNumber(totals.send_with_bump || summary.total_can_send, 0)}
                            {bumpTotal > 0 && (
                                <span style={{ fontSize: 11, fontWeight: 500, color: '#8b5cf6', marginLeft: 6 }}>
                                    +{formatNumber(bumpTotal, 0)}
                                </span>
                            )}
                        </div>
                    </div>
                    {summary.total_deficit > 0 && (
                        <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '3px solid #ef4444' }}>
                            <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>Дефицит</div>
                            <div style={{ fontSize: 22, fontWeight: 700, color: '#ef4444' }}>{formatNumber(summary.total_deficit, 0)}</div>
                        </div>
                    )}
                    {summary.total_deficit <= 0 && (
                        <div className="glass-card" style={{ padding: '14px 16px' }}>
                            <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>Дефицит</div>
                            <div style={{ fontSize: 22, fontWeight: 700, color: '#22c55e' }}>0</div>
                        </div>
                    )}
                    <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '3px solid #3b82f6' }}>
                        <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>Время до WB</div>
                        <div style={{ fontSize: 22, fontWeight: 700, color: '#3b82f6' }}>~{summary.avg_delivery_days} дн</div>
                    </div>
                </div>
            )}

            {/* Filter Panel - Row 1 */}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
                {data?.brands && data.brands.length > 0 && (
                    <select value={brandFilter} onChange={e => setBrandFilter(e.target.value)}
                        style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg)', fontSize: 12 }}>
                        <option value="">Все бренды</option>
                        {data.brands.map(b => <option key={b} value={b}>{b}</option>)}
                    </select>
                )}
                {data?.subjects && data.subjects.length > 0 && (
                    <select value={subjectFilter} onChange={e => setSubjectFilter(e.target.value)}
                        style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg)', fontSize: 12 }}>
                        <option value="">Все категории</option>
                        {data.subjects.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                )}

                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <span style={{ fontSize: 11, opacity: 0.6, whiteSpace: 'nowrap' }}>Запас:</span>
                    {[7, 14, 30, 60].map(d => (
                        <button key={d} className={`btn btn-sm ${supplyDays === d ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setSupplyDays(d)}>{d}д</button>
                    ))}
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <span style={{ fontSize: 11, opacity: 0.6, whiteSpace: 'nowrap' }}>Анализ:</span>
                    {[7, 14, 30].map(d => (
                        <button key={d} className={`btn btn-sm ${analysisDays === d ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setAnalysisDays(d)}>{d}д</button>
                    ))}
                </div>

                {/* Дораспределить: bump главных складов ФО до N шт, если у SKU
                    остались свободные FF-остатки после жадного cap. Лекарство против
                    «пустой склад → 0 заказов → 0 спроса → ничего не предлагаем»
                    для дальних регионов (ДВ, Сибирь). */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <button
                        className={`btn btn-sm ${minStockPerMain > 0 ? 'btn-primary' : 'btn-secondary'}`}
                        disabled={acceptanceMap.size === 0}
                        style={{
                            ...(minStockPerMain === 0 && acceptanceMap.size > 0 ? { borderColor: '#8b5cf6', color: '#7c3aed' } : {}),
                            ...(acceptanceMap.size === 0 ? { opacity: 0.5, cursor: 'not-allowed' } : {}),
                        }}
                        onClick={() => {
                            if (acceptanceMap.size === 0) return;
                            if (minStockPerMain > 0) {
                                setMinStockPerMain(0);
                                setShowMinStockInput(false);
                            } else {
                                setShowMinStockInput(v => !v);
                            }
                        }}
                        title={acceptanceMap.size === 0
                            ? 'Сначала нажми «Проверить приёмку WB» — без acceptance check не знаем, какие склады принимают этот SKU. Иначе дораспределим на склады куда WB не везёт.'
                            : 'Дораспределить излишки FF на underserved ФО. Срабатывает для SKU с (a) дисбалансом WB (overstock в одном ФО, пустой в другом) ИЛИ (b) FF-излишком ≥ 3×N (мёртвый груз). Bump до реальной потребности ФО (avg_d × запас_дн); для ФО без продаж — до N шт bootstrap. ДВ+СФО приоритет.'}
                    >
                        📤 Дораспределить{minStockPerMain > 0 ? ` ≥${minStockPerMain}` : ''}
                        {minStockPerMain > 0 && bumpTotal > 0 && (
                            <span style={{ marginLeft: 4, fontSize: 10, opacity: 0.85 }}>
                                +{formatNumber(bumpTotal, 0)}шт
                            </span>
                        )}
                    </button>
                    {showMinStockInput && (
                        <input
                            type="number" min={1} max={1000}
                            placeholder="N шт"
                            defaultValue={minStockPerMain || 5}
                            autoFocus
                            onKeyDown={e => {
                                if (e.key === 'Enter') {
                                    const v = Math.max(0, Number((e.target as HTMLInputElement).value) || 0);
                                    setMinStockPerMain(v);
                                    setShowMinStockInput(false);
                                } else if (e.key === 'Escape') {
                                    setShowMinStockInput(false);
                                }
                            }}
                            onBlur={e => {
                                const v = Math.max(0, Number(e.target.value) || 0);
                                setMinStockPerMain(v);
                                setShowMinStockInput(false);
                            }}
                            style={{
                                width: 60, padding: '4px 8px', fontSize: 12,
                                borderRadius: 8, border: '1px solid var(--color-border)',
                                background: 'var(--color-bg)',
                            }}
                        />
                    )}
                </div>

                <button
                    className="btn btn-sm btn-primary"
                    disabled={checkedCount === 0 || creatingAssembly}
                    onClick={handleCreateAssembly}
                    style={{ opacity: (checkedCount === 0 || creatingAssembly) ? 0.5 : 1 }}
                >
                    {creatingAssembly ? 'Создание...' : `Создать сборку (${checkedCount})`}
                </button>

                <button
                    className="btn btn-sm btn-secondary"
                    onClick={handleCheckAcceptance}
                    disabled={acceptanceLoading || !data?.articles?.length}
                    title="Запросить у WB API доступность складов для каждого артикула. Закрытые склады автоматически перераспределятся."
                    style={{
                        opacity: (acceptanceLoading || !data?.articles?.length) ? 0.5 : 1,
                        ...(acceptanceMap.size > 0 ? { borderColor: '#22c55e', color: '#16a34a' } : {}),
                    }}
                >
                    {acceptanceLoading
                        ? 'Проверяем...'
                        : acceptanceMap.size > 0
                            ? `🔄 Обновить (${acceptanceMap.size})`
                            : '📦 Проверить приёмку WB'}
                </button>
                {acceptanceMap.size > 0 && !acceptanceLoading && (
                    <button
                        className="btn btn-sm btn-secondary"
                        onClick={handleResetAcceptance}
                        title="Сбросить результаты проверки"
                        style={{ padding: '4px 8px', fontSize: 14, lineHeight: 1 }}
                    >
                        ✕
                    </button>
                )}

                <button className="btn btn-sm btn-secondary" onClick={handleExport} title="Экспорт в Excel">Excel</button>
            </div>

            {/* Filter Panel - Row 2: quick filters + search */}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 16 }}>
                <button
                    className={`btn btn-sm ${statusFilter === 'all' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setStatusFilter('all')}
                >
                    Все ({nonNewcomerCounts.all})
                </button>
                <button
                    className={`btn btn-sm ${statusFilter === 'deficit' ? 'btn-primary' : 'btn-secondary'}`}
                    style={statusFilter !== 'deficit' ? { borderColor: '#ef4444', color: '#ef4444' } : {}}
                    onClick={() => setStatusFilter('deficit')}
                >
                    С дефицитом ({nonNewcomerCounts.deficit})
                </button>
                <button
                    className={`btn btn-sm ${statusFilter === 'can_send' ? 'btn-primary' : 'btn-secondary'}`}
                    style={statusFilter !== 'can_send' ? { borderColor: '#22c55e', color: '#22c55e' } : {}}
                    onClick={() => setStatusFilter('can_send')}
                >
                    Могу отправить ({nonNewcomerCounts.can_send})
                </button>
                <button
                    className={`btn btn-sm ${statusFilter === 'no_wb' ? 'btn-primary' : 'btn-secondary'}`}
                    style={statusFilter !== 'no_wb' ? { borderColor: '#eab308', color: '#eab308' } : {}}
                    onClick={() => setStatusFilter('no_wb')}
                    title="WB пуст, но у нас есть остатки (ФФ / в сборке / в пути) — можно дослать"
                >
                    Нет на WB ({nonNewcomerCounts.no_wb})
                </button>
                <button
                    className={`btn btn-sm ${statusFilter === 'no_stock' ? 'btn-primary' : 'btn-secondary'}`}
                    style={statusFilter !== 'no_stock' ? { borderColor: '#94a3b8', color: '#64748b' } : {}}
                    onClick={() => setStatusFilter('no_stock')}
                    title="WB=0, ФФ=0, в сборке=0, в пути=0 — нигде нет, нужно производство"
                >
                    Нет остатков ({nonNewcomerCounts.no_stock})
                </button>
                {acceptanceMap.size > 0 && (
                    <>
                        <button
                            className={`btn btn-sm ${packageFilter === 'box' ? 'btn-primary' : 'btn-secondary'}`}
                            style={packageFilter !== 'box' ? { borderColor: '#16a34a', color: '#16a34a' } : {}}
                            onClick={() => setPackageFilter(packageFilter === 'box' ? 'none' : 'box')}
                            title="Только SKU, которые WB примет коробом. Комбинируется со статусом."
                        >
                            📦 Короб ({acceptanceCounts.box})
                        </button>
                        <button
                            className={`btn btn-sm ${packageFilter === 'mono' ? 'btn-primary' : 'btn-secondary'}`}
                            style={packageFilter !== 'mono' ? { borderColor: '#a16207', color: '#a16207' } : {}}
                            onClick={() => setPackageFilter(packageFilter === 'mono' ? 'none' : 'mono')}
                            title="SKU с моно-split. Скрывает WB-склады, которые не принимают моно. Комбинируется со статусом."
                        >
                            📐 Моно ({acceptanceCounts.mono})
                        </button>
                    </>
                )}
                <button
                    className={`btn btn-sm ${coldStartMode ? 'btn-primary' : 'btn-secondary'}`}
                    style={!coldStartMode ? { borderColor: '#a855f7', color: '#a855f7' } : {}}
                    onClick={() => setColdStartMode(v => !v)}
                    title="Новинки с остатком (нет данных для автоматической локализации): распределить по бенчмарку проекта"
                >
                    🆕 Новинки ({coldStartData?.rows.length ?? '…'})
                </button>

                <input
                    type="text"
                    placeholder="Поиск артикула..."
                    value={searchQuery}
                    onChange={e => setSearchQuery(e.target.value)}
                    style={{
                        padding: '6px 10px', borderRadius: 8, border: '1px solid var(--color-border)',
                        background: 'var(--color-bg)', fontSize: 12, width: 180,
                    }}
                />

                {loading && <span style={{ fontSize: 11, opacity: 0.5 }}>Обновление...</span>}
            </div>

            {/* Hidden file input for upload */}
            <input ref={fileInputRef} type="file" accept=".xlsx,.xls" style={{ display: 'none' }}
                onChange={handleFileChange} />

            {/* WB Acceptance check banner */}
            {acceptanceError && (
                <div style={{
                    padding: '10px 14px', borderRadius: 8, marginBottom: 12, fontSize: 12,
                    background: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b',
                }}>
                    Ошибка проверки приёмки WB: {acceptanceError}
                </div>
            )}
            {acceptanceMap.size > 0 && acceptanceCheckedAt && (() => {
                const skuChecked = acceptanceMap.size;
                const movedSkus = new Set(acceptanceMoves.map(m => m.nm_id)).size;
                const movedQty = acceptanceMoves.reduce((s, m) => s + m.quantity, 0);
                const droppedQty = acceptanceMoves
                    .filter(m => m.to_warehouse === null)
                    .reduce((s, m) => s + m.quantity, 0);
                const monoCount = Array.from(acceptanceMap.values()).filter(it => (it.splits ?? []).some(s => s.package_type === 'MONOPALLET')).length;
                const superCount = Array.from(acceptanceMap.values()).filter(it => (it.splits ?? []).some(s => s.package_type === 'SUPERSAFE')).length;
                const splitCount = Array.from(acceptanceMap.values()).filter(it => (it.splits ?? []).length > 1).length;
                const t = new Date(acceptanceCheckedAt);
                return (
                    <div style={{
                        padding: '10px 14px', borderRadius: 8, marginBottom: 12, fontSize: 12,
                        background: '#f0fdf4', border: '1px solid #bbf7d0', color: '#166534',
                        display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                    }}>
                        <span><strong>WB-приёмка проверена</strong></span>
                        <span style={{ opacity: 0.5 }}>|</span>
                        <span>SKU: {skuChecked}</span>
                        {monoCount > 0 && <span title="Хотя бы один склад принимает моно-паллетой">📐 моно: {monoCount}</span>}
                        {superCount > 0 && <span title="Super-safe">🔒 super: {superCount}</span>}
                        {splitCount > 0 && (
                            <span title="SKU где склады требуют разные типы упаковки — будут созданы отдельные сборки на каждый тип">
                                📦+📐 split: {splitCount}
                            </span>
                        )}
                        {movedSkus > 0 && (
                            <>
                                <span style={{ opacity: 0.5 }}>|</span>
                                <span>Перемещено qty: <strong>{formatNumber(movedQty, 0)}</strong> у {movedSkus} SKU</span>
                            </>
                        )}
                        {droppedQty > 0 && (
                            <span style={{ color: '#991b1b' }} title="Нет открытых складов для этих SKU">
                                ⚠ потеряно {formatNumber(droppedQty, 0)} шт
                            </span>
                        )}
                        <span style={{ marginLeft: 'auto', opacity: 0.6 }}>
                            {String(t.getHours()).padStart(2, '0')}:{String(t.getMinutes()).padStart(2, '0')}
                        </span>
                    </div>
                );
            })()}

            {/* Hypothetical mode info banner */}
            {mode === 'hypothetical' && (
                <div style={{
                    padding: '10px 14px', borderRadius: 8, marginBottom: 12, fontSize: 12,
                    background: hypoMode === 'city' && citiesStatus?.has_data ? '#f0fdf4' : hypoMode === 'city' ? '#fef9c3' : '#eff6ff',
                    border: `1px solid ${hypoMode === 'city' && citiesStatus?.has_data ? '#bbf7d0' : hypoMode === 'city' ? '#fde68a' : '#bfdbfe'}`,
                    display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                }}>
                    {hypoMode === 'region' && (
                        <>
                            <span>Точность: по регионам (~150 км). Загрузите ленту заказов для точности по городам.</span>
                        </>
                    )}
                    {hypoMode === 'city' && citiesStatus?.has_data && (() => {
                        const fmtD = (s: string) => { const d = new Date(s); return `${String(d.getDate()).padStart(2,'0')}.${String(d.getMonth()+1).padStart(2,'0')}.${d.getFullYear()}`; };
                        const dateRange = citiesStatus.date_from && citiesStatus.date_to
                            ? `${fmtD(citiesStatus.date_from)} \u2014 ${fmtD(citiesStatus.date_to)}`
                            : null;
                        // Coverage warning
                        let coverageWarn: string | null = null;
                        if (citiesStatus.date_from && citiesStatus.date_to) {
                            const from = new Date(citiesStatus.date_from);
                            const to = new Date(citiesStatus.date_to);
                            const dataDays = Math.round((to.getTime() - from.getTime()) / 86400000) + 1;
                            if (dataDays < analysisDays) {
                                coverageWarn = `Данные ленты: ${dateRange}. Анализ: ${analysisDays} дн. Покрытие: ${dataDays} из ${analysisDays} дней.`;
                            }
                        }
                        return (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1 }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                                    <span style={{ color: '#16a34a' }}>
                                        Лента: {formatNumber(citiesStatus.total_mappings)} заказов
                                        {dateRange && ` \u00b7 ${dateRange}`}
                                    </span>
                                    <button className="btn btn-sm btn-secondary" disabled={uploading}
                                        style={{ fontSize: 11 }} onClick={triggerFileSelect}>
                                        {uploading ? 'Загрузка...' : 'Обновить ленту'}
                                    </button>
                                </div>
                                {coverageWarn && (
                                    <span style={{ color: '#92400e', fontSize: 11 }}>
                                        {coverageWarn}
                                    </span>
                                )}
                            </div>
                        );
                    })()}
                    {hypoMode === 'city' && !citiesStatus?.has_data && (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, width: '100%' }}>
                            <span style={{ color: '#92400e' }}>
                                Для этого режима нужна «Лента заказов» из WB
                            </span>
                            <span style={{ fontSize: 11, opacity: 0.7 }}>
                                Скачайте: WB ЛК &rarr; Аналитика &rarr; Заказы &rarr; Все заказы &rarr; Excel
                            </span>
                            <button className="btn btn-sm btn-primary" disabled={uploading}
                                style={{ fontSize: 11, alignSelf: 'flex-start' }}
                                onClick={triggerFileSelect}>
                                {uploading ? 'Загрузка...' : 'Загрузить ленту заказов'}
                            </button>
                        </div>
                    )}
                    {uploadResult && (
                        <span style={{ color: '#16a34a', fontSize: 12, fontWeight: 600 }}>{uploadResult}</span>
                    )}
                </div>
            )}

            {/* Cold-start таблица — отдельный режим */}
            {coldStartMode && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 12 }}>
                    <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
                        <span style={{
                            padding: '4px 10px', borderRadius: 24, fontSize: 11, fontWeight: 600,
                            background: coldStartData?.bench_source === 'own' ? '#22c55e' : '#f59e0b',
                            color: '#fff',
                        }}>
                            {coldStartData?.bench_source === 'own'
                                ? `Свои данные: ${formatNumber(coldStartData?.bench_total_orders || 0, 0)} заказов за ${analysisDays}д`
                                : coldStartData?.bench_source?.startsWith('neighbor')
                                ? `Соседний проект: ${coldStartData?.bench_total_orders} заказов`
                                : 'Фолбэк: общероссийский WB'}
                        </span>
                        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                            Min pack:
                            <input
                                type="number" min={1} max={1000}
                                value={coldStartMinPack}
                                onChange={e => setColdStartMinPack(Math.max(1, Number(e.target.value) || 5))}
                                style={{
                                    padding: '4px 8px', borderRadius: 6, border: '1px solid var(--color-border)',
                                    width: 70, fontSize: 12,
                                }}
                            />
                        </label>
                        {coldStartLoading && <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Считаю…</span>}
                        {coldStartData && coldStartData.meta.excluded_warehouses.length > 0 && (
                            <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                Исключено складов: {coldStartData.meta.excluded_warehouses.length}
                            </span>
                        )}
                        {coldStartData && coldStartData.rows.length > 0 && (() => {
                            const allChecked = coldStartData.rows.every(r => checkedIds.has(r.nm_id));
                            return (
                                <button
                                    className="btn btn-sm btn-secondary"
                                    onClick={() => {
                                        setCheckedIds(prev => {
                                            const next = new Set(prev);
                                            if (allChecked) {
                                                for (const r of coldStartData.rows) next.delete(r.nm_id);
                                            } else {
                                                for (const r of coldStartData.rows) {
                                                    const ov = coldStartQtyOverrides[r.nm_id];
                                                    const total = ov !== undefined ? ov : r.total_allocated;
                                                    if (total > 0) next.add(r.nm_id);
                                                }
                                            }
                                            return next;
                                        });
                                    }}
                                    style={{ marginLeft: 'auto' }}
                                    title="Выбрать/снять все новинки для общей сборки"
                                >
                                    {allChecked ? '☑ Снять новинки' : `☐ Выбрать все (${coldStartData.rows.filter(r => {
                                        const ov = coldStartQtyOverrides[r.nm_id];
                                        return (ov !== undefined ? ov : r.total_allocated) > 0;
                                    }).length})`}
                                </button>
                            );
                        })()}
                    </div>
                    {coldStartData && coldStartData.rows.length > 0 ? (
                        <div style={{ overflowX: 'auto' }}>
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                                <thead>
                                    {/* Group-header: цветные плашки округов над WB-колонками (как в основной таблице).
                                        colSpan = 9 + N: checkbox + Артикул + Реализ + Маржа + Хватит + % лок + N×RF + WB + В сборке + Распределить */}
                                    <tr>
                                        <th colSpan={9 + (coldStartData.rf_warehouses?.length ?? 0)} style={{ background: '#f5f5f7' }}>&nbsp;</th>
                                        {(() => {
                                            const groups: Array<{ key: string; count: number }> = [];
                                            for (const w of filteredMainWarehouses) {
                                                const k = w.district_key || 'unknown';
                                                const last = groups[groups.length - 1];
                                                if (last && last.key === k) last.count += 1;
                                                else groups.push({ key: k, count: 1 });
                                            }
                                            return groups.map((g, i) => (
                                                <th
                                                    key={`cs-grp-${i}-${g.key}`}
                                                    colSpan={g.count}
                                                    style={{
                                                        background: DISTRICT_COLORS[g.key] ?? '#475569',
                                                        color: '#fff',
                                                        fontSize: 10, fontWeight: 700,
                                                        letterSpacing: 0.5, textTransform: 'uppercase',
                                                        textAlign: 'center', padding: '6px 8px',
                                                        borderBottom: '1px solid var(--color-border)',
                                                    }}
                                                >
                                                    {DISTRICT_LABELS[g.key] ?? g.key}
                                                </th>
                                            ));
                                        })()}
                                        <th style={{ background: '#f5f5f7' }}>&nbsp;</th>
                                    </tr>
                                    <tr style={{ borderBottom: '2px solid var(--color-border)', background: '#f5f5f7' }}>
                                        <th style={{ padding: '8px 6px', width: 30 }}></th>
                                        <th style={{ padding: '8px 10px', textAlign: 'left', cursor: 'pointer' }} onClick={() => handleColdStartSort('vendor_code')}>
                                            Артикул{csSortArrow('vendor_code')}
                                        </th>
                                        <th style={{ padding: '8px 10px', textAlign: 'right', cursor: 'pointer' }} onClick={() => handleColdStartSort('revenue_30d')}>
                                            Реализ. 30д{csSortArrow('revenue_30d')}
                                        </th>
                                        <th style={{ padding: '8px 10px', textAlign: 'right' }} title="Маржа % — из аналитики остатков">
                                            Маржа %
                                        </th>
                                        <th style={{ padding: '8px 10px', textAlign: 'right' }} title="На сколько дней хватит при текущей скорости продаж">
                                            Хватит дн
                                        </th>
                                        <th style={{ padding: '8px 10px', textAlign: 'right' }} title="Процент локализации — из индекса локализации (период 14д)">
                                            % лок
                                        </th>
                                        {(coldStartData.rf_warehouses ?? []).map(wh => (
                                            <th key={`cs_rf_${wh.id}`} style={{ padding: '8px 10px', textAlign: 'right', whiteSpace: 'nowrap', background: 'rgba(59,130,246,0.04)', cursor: 'pointer' }}
                                                onClick={() => handleColdStartSort(`cs_rf_${wh.id}`)}>
                                                {wh.name.length > 12 ? wh.name.slice(0, 12) + '…' : wh.name}
                                                {csSortArrow(`cs_rf_${wh.id}`)}
                                            </th>
                                        ))}
                                        <th style={{ padding: '8px 10px', textAlign: 'right', cursor: 'pointer' }} onClick={() => handleColdStartSort('wb_qty')}>
                                            WB{csSortArrow('wb_qty')}
                                        </th>
                                        <th style={{ padding: '8px 10px', textAlign: 'right', cursor: 'pointer' }} onClick={() => handleColdStartSort('in_assembly_total')}>
                                            В сборке{csSortArrow('in_assembly_total')}
                                        </th>
                                        <th style={{ padding: '8px 10px', textAlign: 'right', whiteSpace: 'nowrap', cursor: 'pointer' }} onClick={() => handleColdStartSort('total_allocated')}>
                                            Распределить{csSortArrow('total_allocated')}
                                            <div style={{ fontSize: 10, color: 'var(--color-text-muted)', fontWeight: 400 }}>
                                                ✏️ редактируй
                                            </div>
                                        </th>
                                        {filteredMainWarehouses.map(w => (
                                            <th key={w.warehouse} style={{ padding: '8px 10px', textAlign: 'right', whiteSpace: 'nowrap', cursor: 'pointer' }}
                                                onClick={() => handleColdStartSort(`cs_wb_${w.warehouse}`)}>
                                                <div style={{ fontSize: 11 }}>{w.warehouse}{csSortArrow(`cs_wb_${w.warehouse}`)}</div>
                                                <div style={{ fontSize: 10, color: 'var(--color-text-muted)', fontWeight: 400 }}>
                                                    {w.share_pct.toFixed(1)}%
                                                </div>
                                            </th>
                                        ))}
                                        <th style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700 }}>Итого</th>
                                    </tr>
                                    {/* ИТОГО — сумма по всем новинкам, под колонками */}
                                    <tr style={{ background: 'rgba(59,130,246,0.06)', fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>
                                        <td style={{ padding: '8px 6px' }}>&nbsp;</td>
                                        <td style={{ padding: '8px 10px' }}>ИТОГО</td>
                                        <td style={{ padding: '8px 10px', textAlign: 'right' }}>
                                            {coldStartTotals.revenue_30d > 0 ? `₽${formatNumber(coldStartTotals.revenue_30d, 0)}` : '—'}
                                        </td>
                                        <td style={{ padding: '8px 10px', textAlign: 'right', color: 'var(--color-text-muted)' }}>—</td>
                                        <td style={{ padding: '8px 10px', textAlign: 'right', color: 'var(--color-text-muted)' }}>—</td>
                                        <td style={{ padding: '8px 10px', textAlign: 'right', color: 'var(--color-text-muted)' }}>—</td>
                                        {(coldStartData.rf_warehouses ?? []).map(wh => (
                                            <td key={`tot_cs_rf_${wh.id}`} style={{ padding: '8px 10px', textAlign: 'right', background: 'rgba(59,130,246,0.04)' }}>
                                                {(coldStartTotals.rf[wh.id] || 0) > 0 ? formatNumber(coldStartTotals.rf[wh.id], 0) : '—'}
                                            </td>
                                        ))}
                                        <td style={{ padding: '8px 10px', textAlign: 'right' }}>
                                            {coldStartTotals.wb_qty > 0 ? formatNumber(coldStartTotals.wb_qty, 0) : '—'}
                                        </td>
                                        <td style={{ padding: '8px 10px', textAlign: 'right' }}>
                                            {coldStartTotals.in_assembly_total > 0 ? formatNumber(coldStartTotals.in_assembly_total, 0) : '—'}
                                        </td>
                                        <td style={{ padding: '8px 10px', textAlign: 'right' }}>
                                            {coldStartTotals.total_allocated > 0 ? formatNumber(coldStartTotals.total_allocated, 0) : '—'}
                                        </td>
                                        {filteredMainWarehouses.map(w => (
                                            <td key={`tot_cs_wb_${w.warehouse}`} style={{ padding: '8px 10px', textAlign: 'right' }}>
                                                {(coldStartTotals.wb[w.warehouse] || 0) > 0 ? formatNumber(coldStartTotals.wb[w.warehouse], 0) : '—'}
                                            </td>
                                        ))}
                                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700 }}>
                                            {coldStartTotals.total_allocated > 0 ? formatNumber(coldStartTotals.total_allocated, 0) : '—'}
                                        </td>
                                    </tr>
                                </thead>
                                <tbody>
                                    {sortedColdStartRows.map(row => {
                                        const overrideQty = coldStartQtyOverrides[row.nm_id];
                                        const useOverride = overrideQty !== undefined && overrideQty !== row.total_allocated;
                                        const effective = useOverride
                                            ? recomputeColdStartAlloc(overrideQty, filteredMainWarehouses, coldStartMinPack)
                                            : { alloc: row.allocations, total: row.total_allocated };
                                        const inputValue = overrideQty !== undefined ? overrideQty : row.total_allocated;
                                        return (
                                        <tr key={row.nm_id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                            <td style={{ padding: '6px 6px', textAlign: 'center' }}>
                                                <input
                                                    type="checkbox"
                                                    checked={checkedIds.has(row.nm_id)}
                                                    onChange={() => toggleOne(row.nm_id)}
                                                    disabled={effective.total <= 0}
                                                    title={effective.total <= 0 ? 'Распределение = 0 — нечего собирать' : 'Включить в общую сборку'}
                                                />
                                            </td>
                                            <td style={{ padding: '8px 10px' }}>
                                                <div style={{ fontWeight: 500 }}>
                                                    <span style={{
                                                        marginRight: 6, padding: '1px 6px', borderRadius: 6,
                                                        background: '#a855f7', color: '#fff', fontSize: 9, fontWeight: 700,
                                                    }}>🆕</span>
                                                    {row.article_seller || '—'}
                                                </div>
                                                <div style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>
                                                    {[row.brand, row.subject].filter(Boolean).join(' · ')}
                                                </div>
                                            </td>
                                            <td style={{ padding: '8px 10px', textAlign: 'right', color: row.revenue_30d > 0 ? 'var(--color-text)' : 'var(--color-text-muted)' }}>
                                                {row.revenue_30d > 0 ? `₽${formatNumber(row.revenue_30d, 0)}` : '—'}
                                            </td>
                                            {/* Margin % / Хватит дн — для новинок из analyticsMap */}
                                            {(() => {
                                                const m = analyticsMap.get(row.nm_id)?.margin_pct;
                                                const color = m == null ? 'var(--color-text-muted)'
                                                    : m < 0 ? '#ef4444' : m < 20 ? '#a16207' : '#16a34a';
                                                return (
                                                    <td style={{ padding: '8px 10px', textAlign: 'right', color, fontWeight: 600 }}>
                                                        {m == null ? '—' : `${formatNumber(m, 1)}%`}
                                                    </td>
                                                );
                                            })()}
                                            {(() => {
                                                const dl = analyticsMap.get(row.nm_id)?.days_left;
                                                if (dl == null || dl <= 0) {
                                                    return <td style={{ padding: '8px 10px', textAlign: 'right', color: 'var(--color-text-muted)' }}>—</td>;
                                                }
                                                const bg = dl < 7 ? '#ff4444' : dl <= 14 ? '#ff9800' : dl <= 29 ? '#ffd600' : '#4caf50';
                                                const color = dl <= 29 && dl > 14 ? '#333' : '#fff';
                                                return (
                                                    <td style={{ padding: '8px 10px', textAlign: 'right' }}>
                                                        <span style={{ background: bg, color, padding: '2px 8px', borderRadius: 10, fontWeight: 600, fontSize: 11 }}>
                                                            {dl >= 999 ? '∞' : dl}
                                                        </span>
                                                    </td>
                                                );
                                            })()}
                                            {(() => {
                                                const p = locMap.get(row.nm_id);
                                                if (p == null) return <td style={{ padding: '8px 10px', textAlign: 'right', color: 'var(--color-text-muted)' }}>—</td>;
                                                const color = p >= 80 ? '#16a34a' : p >= 50 ? '#a16207' : '#ef4444';
                                                return (
                                                    <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 600, color }}>
                                                        {formatNumber(p, 1)}%
                                                    </td>
                                                );
                                            })()}
                                            {(coldStartData.rf_warehouses ?? []).map(wh => {
                                                const v = row.rf_by_warehouse?.[wh.id] ?? 0;
                                                return (
                                                    <td key={`cs_rf_cell_${wh.id}`} style={{
                                                        padding: '8px 10px', textAlign: 'right',
                                                        background: 'rgba(59,130,246,0.04)',
                                                        color: v > 0 ? 'var(--color-text)' : 'var(--color-text-muted)',
                                                        fontWeight: v > 0 ? 500 : 400,
                                                    }}>
                                                        {v > 0 ? formatNumber(v, 0) : '—'}
                                                    </td>
                                                );
                                            })}
                                            <td style={{ padding: '8px 10px', textAlign: 'right' }}>{formatNumber(row.wb_qty, 0)}</td>
                                            <td
                                                style={{
                                                    padding: '8px 10px', textAlign: 'right',
                                                    cursor: row.in_assembly_total > 0 ? 'help' : 'default',
                                                    textDecoration: row.in_assembly_total > 0 ? 'underline dotted var(--color-text-muted)' : undefined,
                                                }}
                                                title={
                                                    row.in_assembly_total > 0 && row.asm_by_warehouse
                                                        ? 'В сборке по складам:\n' + Object.entries(row.asm_by_warehouse)
                                                            .sort((a, b) => b[1] - a[1])
                                                            .map(([wh, q]) => `• ${wh}: ${formatNumber(q, 0)}`)
                                                            .join('\n')
                                                        : undefined
                                                }
                                            >
                                                {row.in_assembly_total > 0 ? formatNumber(row.in_assembly_total, 0) : '—'}
                                            </td>
                                            <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                                                <input
                                                    type="number" min={0} max={100000}
                                                    value={inputValue}
                                                    onChange={e => {
                                                        const v = Math.max(0, Number(e.target.value) || 0);
                                                        setColdStartQtyOverrides(prev => ({ ...prev, [row.nm_id]: v }));
                                                    }}
                                                    title="Распределить столько штук пропорционально долям ФО (округление + min_pack pool)"
                                                    style={{
                                                        padding: '3px 6px', borderRadius: 6,
                                                        border: useOverride ? '1px solid #a855f7' : '1px solid var(--color-border)',
                                                        background: useOverride ? '#faf5ff' : 'var(--color-bg)',
                                                        width: 64, fontSize: 12, textAlign: 'right',
                                                        fontWeight: useOverride ? 600 : 400,
                                                    }}
                                                />
                                            </td>
                                            {filteredMainWarehouses.map(w => {
                                                const v = effective.alloc[w.warehouse] || 0;
                                                const asmQty = row.asm_by_warehouse?.[w.warehouse] ?? 0;
                                                const m = getCellAcceptanceMarks(row.nm_id, w.warehouse);
                                                const onlyMono = m.checked && !m.box && m.mono;
                                                const onlySuper = m.checked && !m.box && !m.mono && m.super;
                                                const cellBg = m.closed
                                                    ? 'rgba(148,163,184,0.18)'
                                                    : onlyMono
                                                        ? 'rgba(234,179,8,0.10)'
                                                        : onlySuper
                                                            ? 'rgba(168,85,247,0.10)'
                                                            : undefined;
                                                const cellColor = m.closed
                                                    ? '#64748b'
                                                    : onlyMono
                                                        ? '#a16207'
                                                        : onlySuper
                                                            ? '#7e22ce'
                                                            : v > 0 ? '#16a34a' : 'var(--color-text-muted)';
                                                // Приоритет: короб → моно → super. Показываем только высший доступный
                                                // тип (короб не нужно дублировать с моно — он дешевле и в приоритете).
                                                const badgeParts: string[] = [];
                                                if (m.closed) badgeParts.push('⛔');
                                                else if (m.box) badgeParts.push('📦');
                                                else if (m.mono) badgeParts.push('📐');
                                                else if (m.super) badgeParts.push('🔒');
                                                const badge = badgeParts.length ? ' ' + badgeParts.join('') : '';
                                                const tipParts: string[] = [];
                                                if (m.closed) tipParts.push(`WB не принимает на «${w.warehouse}» в ближайшие 14 дней`);
                                                else {
                                                    // Tooltip с разделением free/paid/quota — пользователь видит всё что
                                                    // принимает склад, даже если бесплатных слотов нет (платные квоты).
                                                    const slotsLabel = (free?: number, paid?: number, min?: number | null): string => {
                                                        const f = free ?? 0;
                                                        const p = paid ?? 0;
                                                        if (f > 0 && p > 0) return ` (бесплатно ${f}/14 + платно ${p}/14, мин ×${min})`;
                                                        if (f > 0) return ` (бесплатно ${f}/14 дн)`;
                                                        if (p > 0) return ` (только платно ${p}/14 дн, мин ×${min})`;
                                                        if (free === undefined && paid === undefined) return '';
                                                        return ' (по индивидуальной квоте)';
                                                    };
                                                    // Приоритет: короб дешевле, поэтому если есть — другие варианты не показываем.
                                                    if (m.box) tipParts.push(`коробом${slotsLabel(m.box_free, m.box_paid, m.box_min)}`);
                                                    else if (m.mono) tipParts.push(`моно-паллетой${slotsLabel(m.mono_free, m.mono_paid, m.mono_min)}`);
                                                    else if (m.super) tipParts.push(`super-safe${slotsLabel(m.super_free, m.super_paid, m.super_min)}`);
                                                }
                                                const baseTip = tipParts.length ? (m.closed ? tipParts[0] : `Принимает: ${tipParts.join(', ')}`) : '';
                                                const asmTip = asmQty > 0 ? `Уже в сборке: ${formatNumber(asmQty, 0)} шт` : '';
                                                const tip = [baseTip, asmTip].filter(Boolean).join('\n');
                                                return (
                                                    <td key={w.warehouse} title={tip} style={{
                                                        padding: '8px 10px', textAlign: 'right',
                                                        color: cellColor,
                                                        background: cellBg,
                                                        fontWeight: v > 0 ? 500 : 400,
                                                        textDecoration: m.closed && v > 0 ? 'line-through' : undefined,
                                                    }}>
                                                        <div>
                                                            {v > 0 ? formatNumber(v, 0) : '—'}
                                                            {/* Бейдж приёмки: при v>0 — справа от qty; при v=0 + acceptance проверен и
                                                                склад доступен — рядом с прочерком (видно куда ещё МОЖНО, даже без потребности). */}
                                                            {badge && (v > 0 || (m.checked && !m.closed)) && (
                                                                <span style={{ marginLeft: 4, fontSize: 10, opacity: v > 0 ? 1 : 0.55 }}>{badge}</span>
                                                            )}
                                                        </div>
                                                        {asmQty > 0 && (
                                                            <div style={{ fontSize: 10, color: '#16a34a', fontWeight: 600, lineHeight: 1.1, marginTop: 2 }}>
                                                                🚚 {formatNumber(asmQty, 0)}
                                                            </div>
                                                        )}
                                                    </td>
                                                );
                                            })}
                                            <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700 }}>
                                                {effective.total > 0 ? formatNumber(effective.total, 0) : '—'}
                                            </td>
                                        </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    ) : !coldStartLoading ? (
                        <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                            Нет новинок с остатком — все SKU имеют историю продаж, для них работает обычная локализация
                        </div>
                    ) : null}
                </div>
            )}

            {/* Table */}
            {!coldStartMode && data && sortedArticles.length > 0 ? (
                <div className="glass-card" style={{ overflowX: 'auto', padding: 0, position: 'relative' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                        {/* Level 1: Group Headers */}
                        <thead>
                            <tr>
                                {/* Sticky checkbox cell */}
                                <th style={{
                                    ...thBase, cursor: 'default',
                                    position: 'sticky', left: 0, zIndex: 4, background: '#f5f5f7',
                                    borderBottom: '1px solid var(--color-border)',
                                    width: 36, minWidth: 36,
                                }}>&nbsp;</th>
                                {/* Sticky article cell */}
                                <th style={{
                                    ...thBase, cursor: 'default', textAlign: 'left',
                                    position: 'sticky', left: 36, zIndex: 4, background: '#f5f5f7',
                                    boxShadow: '2px 0 4px rgba(0,0,0,0.05)',
                                    borderBottom: '1px solid var(--color-border)',
                                    minWidth: 180,
                                }}>&nbsp;</th>
                                {/* Revenue + Маржа% + WB + Хватит дн + % лок + Need (not sticky) */}
                                <th colSpan={6} style={{
                                    ...thBase, cursor: 'default',
                                    background: '#f5f5f7',
                                    borderBottom: '1px solid var(--color-border)',
                                }}>&nbsp;</th>

                                {/* МОИ СКЛАДЫ group: RF + В сборке + В пути + Могу + ОТПРАВИТЬ = +4 */}
                                <th colSpan={rfWarehouses.length + 4} style={{
                                    ...thBase, cursor: 'default', textAlign: 'center',
                                    background: 'rgba(59,130,246,0.08)', fontSize: 10, fontWeight: 700,
                                    letterSpacing: 1, borderBottom: '1px solid var(--color-border)',
                                }}>МОИ СКЛАДЫ</th>
                                {/* Дефицит — отдельная колонка между «МОИ СКЛАДЫ» и WB-группами,
                                    пустая ячейка в group-header чтобы WB-группы не съезжали влево. */}
                                <th style={{
                                    ...thBase, cursor: 'default',
                                    background: '#f5f5f7',
                                    borderBottom: '1px solid var(--color-border)',
                                }}>&nbsp;</th>

                                {/* СКЛАДЫ WB group — разбито по федеральным округам как в индексе локализации.
                                    Run-length encoding по visibleWbWarehouses (порядок сохраняется): соседние склады
                                    одного district_key объединяются в один цветной group-cell. */}
                                {visibleWbWarehouses.length > 0 && (() => {
                                    const groups: Array<{ key: string; count: number }> = [];
                                    for (const wh of visibleWbWarehouses) {
                                        const k = wh.district_key || 'unknown';
                                        const last = groups[groups.length - 1];
                                        if (last && last.key === k) last.count += 1;
                                        else groups.push({ key: k, count: 1 });
                                    }
                                    return groups.map((g, i) => (
                                        <th
                                            key={`wb-grp-${i}-${g.key}`}
                                            colSpan={g.count}
                                            style={{
                                                ...thBase, cursor: 'default', textAlign: 'center',
                                                background: DISTRICT_COLORS[g.key] ?? '#475569',
                                                color: '#fff',
                                                fontSize: 10, fontWeight: 700,
                                                letterSpacing: 0.5, textTransform: 'uppercase',
                                                borderBottom: '1px solid var(--color-border)',
                                                padding: '6px 8px',
                                            }}
                                        >
                                            {DISTRICT_LABELS[g.key] ?? g.key}
                                        </th>
                                    ));
                                })()}
                            </tr>

                            {/* Level 2: Column Headers */}
                            <tr style={{ background: '#f5f5f7' }}>
                                {/* Checkbox */}
                                <th style={{ ...stickyCheckbox, borderBottom: '2px solid var(--color-border)', zIndex: 4, cursor: 'pointer' }}
                                    onClick={toggleAll}>
                                    <input type="checkbox" checked={allChecked} onChange={toggleAll}
                                        style={{ cursor: 'pointer', accentColor: '#3b82f6' }} />
                                </th>
                                {/* Article */}
                                <th style={{
                                    ...stickyArticle, borderBottom: '2px solid var(--color-border)', zIndex: 4,
                                    cursor: 'pointer', fontSize: 11,
                                }}
                                    onClick={() => handleSort('vendor_code')}>
                                    АРТИКУЛ{sortArrow('vendor_code')}
                                </th>
                                {/* Revenue */}
                                <th style={{ ...thBase }} onClick={() => handleSort('revenue_30d')}>
                                    РЕАЛИЗ. {analysisDays}д{sortArrow('revenue_30d')}
                                </th>
                                {/* Margin / WB stock / Days left — данные из getStockAnalytics(14) */}
                                <th style={{ ...thBase }} title="Маржа % — из аналитики остатков">
                                    МАРЖА %
                                </th>
                                <th style={{ ...thBase }} title="Текущие остатки на всех WB-складах">
                                    WB
                                </th>
                                <th style={{ ...thBase }} title="На сколько дней хватит при текущей скорости продаж">
                                    ХВАТИТ ДН
                                </th>
                                <th style={{ ...thBase }} title="Процент локализации — из индекса локализации (период 14д)">
                                    % ЛОК
                                </th>
                                {/* Total Need */}
                                <th style={{ ...thBase }} onClick={() => handleSort('total_need')}>
                                    ПОТРЕБН.{sortArrow('total_need')}
                                </th>

                                {/* RF Warehouses */}
                                {rfWarehouses.map(wh => (
                                    <th key={`rf_${wh.id}`} style={{ ...thBase, background: 'rgba(59,130,246,0.04)' }}
                                        onClick={() => handleSort(`rf_${wh.id}`)}>
                                        {wh.name.length > 12 ? wh.name.slice(0, 12) + '\u2026' : wh.name}
                                        {sortArrow(`rf_${wh.id}`)}
                                    </th>
                                ))}
                                {/* In Assembly */}
                                <th style={{ ...thBase, background: 'rgba(59,130,246,0.04)' }}
                                    onClick={() => handleSort('in_assembly')}>
                                    В СБОРКЕ{sortArrow('in_assembly')}
                                </th>
                                {/* In Transit */}
                                <th style={{ ...thBase, background: 'rgba(59,130,246,0.04)' }}
                                    onClick={() => handleSort('in_transit')}>
                                    В ПУТИ{sortArrow('in_transit')}
                                </th>
                                {/* Can Send */}
                                <th style={{ ...thBase, background: 'rgba(59,130,246,0.04)' }}
                                    onClick={() => handleSort('can_send')}>
                                    МОГУ ОТПР.{sortArrow('can_send')}
                                </th>
                                {/* Send with bump — итоговый план (Hamilton + «Дораспределить») */}
                                <th style={{ ...thBase, background: 'rgba(34,197,94,0.06)' }}
                                    onClick={() => handleSort('send_with_bump')}
                                    title="План к отгрузке = Могу отправить + bump из «Дораспределить». Если bump неактивен — равен «Могу отпр.»">
                                    ОТПРАВИТЬ{sortArrow('send_with_bump')}
                                </th>
                                {/* Deficit */}
                                <th style={{ ...thBase, background: 'rgba(59,130,246,0.04)' }}
                                    onClick={() => handleSort('deficit')}>
                                    ДЕФИЦИТ{sortArrow('deficit')}
                                </th>

                                {/* WB Warehouses \u2014 \u043e\u043a\u0440\u0443\u0433 \u043f\u043e\u043a\u0430\u0437\u0430\u043d \u0432 group-header \u0441\u0432\u0435\u0440\u0445\u0443, \u0442\u0443\u0442 \u0442\u043e\u043b\u044c\u043a\u043e \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 */}
                                {visibleWbWarehouses.map(wh => {
                                    const dKey = wh.district_key || 'unknown';
                                    const dLabel = DISTRICT_LABELS[dKey] ?? dKey;
                                    return (
                                        <th key={`wb_${wh.name}`} style={{ ...thBase, background: 'rgba(245,158,11,0.04)' }}
                                            onClick={() => handleSort(`wb_${wh.name}`)}
                                            title={`${wh.name} \u00b7 ${dLabel}`}>
                                            {wh.name.length > 12 ? wh.name.slice(0, 12) + '\u2026' : wh.name}
                                            {sortArrow(`wb_${wh.name}`)}
                                        </th>
                                    );
                                })}
                            </tr>

                            {/* ИТОГО row */}
                            <tr style={{ background: 'rgba(59,130,246,0.06)', fontWeight: 700 }}>
                                <td style={{ ...stickyCheckbox, background: 'rgba(59,130,246,0.06)', borderBottom: '2px solid var(--color-border)' }}>&nbsp;</td>
                                <td style={{ ...stickyArticle, background: 'rgba(59,130,246,0.06)', borderBottom: '2px solid var(--color-border)' }}>ИТОГО</td>
                                <td style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>
                                    {formatRevenue(totals.revenue_30d)}
                                </td>
                                {/* \u041c\u0430\u0440\u0436\u0430% / WB / \u0425\u0432\u0430\u0442\u0438\u0442 \u0434\u043d \u2014 totals \u043d\u0435 \u0438\u043c\u0435\u0435\u0442 \u0441\u043c\u044b\u0441\u043b\u0430, \u043f\u0443\u0441\u0442\u043e. */}
                                <td style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)', color: 'var(--color-text-muted)' }}>&mdash;</td>
                                <td style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>
                                    {(() => {
                                        let sum = 0;
                                        for (const a of filteredArticles) sum += analyticsMap.get(a.nm_id)?.stocks_wb || 0;
                                        return sum > 0 ? formatNumber(sum, 0) : '\u2014';
                                    })()}
                                </td>
                                <td style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)', color: 'var(--color-text-muted)' }}>&mdash;</td>
                                {/* % \u043b\u043e\u043a \u2014 weighted avg \u043f\u043e total (\u043a\u0430\u043a \u0432 \u0438\u043d\u0434\u0435\u043a\u0441\u0435 \u043b\u043e\u043a\u0430\u043b\u0438\u0437\u0430\u0446\u0438\u0438) */}
                                <td style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>
                                    {(() => {
                                        let wSum = 0, denom = 0;
                                        for (const a of filteredArticles) {
                                            const p = locMap.get(a.nm_id);
                                            const w = a.revenue_30d || 0;
                                            if (p != null && w > 0) { wSum += p * w; denom += w; }
                                        }
                                        if (denom <= 0) return <span style={{ color: 'var(--color-text-muted)' }}>{'\u2014'}</span>;
                                        const v = wSum / denom;
                                        const color = v >= 80 ? '#16a34a' : v >= 50 ? '#a16207' : '#ef4444';
                                        return <span style={{ color }}>{formatNumber(v, 1)}%</span>;
                                    })()}
                                </td>
                                <td style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>
                                    {totals.total_need > 0 ? formatNumber(totals.total_need, 0) : '\u2014'}
                                </td>

                                {rfWarehouses.map(wh => (
                                    <td key={`tot_rf_${wh.id}`} style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>
                                        {(totals.rf[wh.id] || 0) > 0 ? formatNumber(totals.rf[wh.id], 0) : '\u2014'}
                                    </td>
                                ))}
                                <td style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>
                                    {totals.in_assembly > 0 ? formatNumber(totals.in_assembly, 0) : '\u2014'}
                                </td>
                                <td style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>
                                    {totals.in_transit > 0 ? formatNumber(totals.in_transit, 0) : '\u2014'}
                                </td>
                                <td style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)', color: '#22c55e' }}>
                                    {totals.can_send > 0 ? formatNumber(totals.can_send, 0) : '\u2014'}
                                </td>
                                <td style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)', color: '#16a34a', background: 'rgba(34,197,94,0.08)' }}>
                                    {totals.send_with_bump > 0 ? formatNumber(totals.send_with_bump, 0) : '\u2014'}
                                </td>
                                <td style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)', color: totals.deficit > 0 ? '#ef4444' : '#22c55e' }}>
                                    {totals.deficit > 0 ? formatNumber(totals.deficit, 0) : '\u2014'}
                                </td>

                                {visibleWbWarehouses.map(wh => (
                                    <td key={`tot_wb_${wh.name}`} style={{
                                        ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)',
                                        color: (totals.wb[wh.name] || 0) > 0 ? '#ef4444' : 'var(--color-text-muted)',
                                    }}>
                                        {(totals.wb[wh.name] || 0) > 0 ? formatNumber(totals.wb[wh.name], 0) : '\u2014'}
                                    </td>
                                ))}
                            </tr>
                        </thead>

                        <tbody>
                            {sortedArticles.map(a => {
                                const highlighted = isHighlighted(a);
                                const rowBg = highlighted ? 'rgba(255,159,10,0.08)' : undefined;
                                const checked = checkedIds.has(a.nm_id);

                                return (
                                    <tr key={a.nm_id}
                                        style={{ background: rowBg, transition: 'background 0.15s' }}
                                        onMouseEnter={e => { if (!highlighted) e.currentTarget.style.background = 'rgba(59,130,246,0.03)'; }}
                                        onMouseLeave={e => { e.currentTarget.style.background = rowBg || ''; }}
                                    >
                                        {/* Checkbox */}
                                        <td style={{ ...stickyCheckbox, zIndex: 2, background: highlighted ? 'rgba(255,159,10,0.08)' : '#f5f5f7' }}>
                                            <input type="checkbox" checked={checked} onChange={() => toggleOne(a.nm_id)}
                                                style={{ cursor: 'pointer', accentColor: '#3b82f6' }} />
                                        </td>
                                        {/* Vendor Code */}
                                        <td style={{ ...stickyArticle, zIndex: 2, background: highlighted ? 'rgba(255,159,10,0.08)' : '#f5f5f7' }}>
                                            <div>
                                                {newcomerSet.has(a.nm_id) && (
                                                    <span style={{
                                                        marginRight: 6, padding: '1px 5px', borderRadius: 6,
                                                        background: '#a855f7', color: '#fff', fontSize: 9, fontWeight: 700,
                                                        verticalAlign: 'middle',
                                                    }} title="\u041D\u043E\u0432\u0438\u043D\u043A\u0430 \u2014 \u0440\u0430\u0441\u043F\u0440\u0435\u0434\u0435\u043B\u0435\u043D\u0438\u0435 \u043F\u043E cold-start \u0431\u0435\u043D\u0447\u043C\u0430\u0440\u043A\u0443">\uD83C\uDD95</span>
                                                )}
                                                {a.vendor_code}
                                            </div>
                                            {(a.brand || a.subject) && (
                                                <div style={{ fontSize: 10, opacity: 0.5, fontWeight: 400 }}>
                                                    {[a.brand, a.subject].filter(Boolean).join(' \u00B7 ')}
                                                </div>
                                            )}
                                        </td>
                                        {/* Revenue */}
                                        <td style={{ ...tdBase, fontWeight: 500 }}>
                                            {formatRevenue(a.revenue_30d)}
                                        </td>
                                        {/* Margin % \u2014 \u0446\u0432\u0435\u0442 \u043f\u043e \u0437\u043d\u0430\u043a\u0443 */}
                                        {(() => {
                                            const am = analyticsMap.get(a.nm_id);
                                            const m = am?.margin_pct;
                                            const color = m == null ? 'var(--color-text-muted)'
                                                : m < 0 ? '#ef4444'
                                                : m < 20 ? '#a16207'
                                                : '#16a34a';
                                            return (
                                                <td style={{ ...tdBase, fontWeight: 600, color }}>
                                                    {m == null ? '\u2014' : `${formatNumber(m, 1)}%`}
                                                </td>
                                            );
                                        })()}
                                        {/* WB stock (\u043e\u0431\u0449\u0438\u0439) */}
                                        <td style={{ ...tdBase, fontWeight: 500 }}>
                                            {(() => {
                                                const v = analyticsMap.get(a.nm_id)?.stocks_wb ?? 0;
                                                return v > 0 ? formatNumber(v, 0) : '\u2014';
                                            })()}
                                        </td>
                                        {/* \u0425\u0432\u0430\u0442\u0438\u0442 \u0434\u043d\u0435\u0439 \u2014 \u0446\u0432\u0435\u0442\u043d\u043e\u0439 \u0431\u0435\u0439\u0434\u0436 \u043a\u0430\u043a \u0432 StockAnalytics */}
                                        {(() => {
                                            const dl = analyticsMap.get(a.nm_id)?.days_left;
                                            if (dl == null || dl <= 0) {
                                                return <td style={{ ...tdBase, color: 'var(--color-text-muted)' }}>{'\u2014'}</td>;
                                            }
                                            const bg = dl < 7 ? '#ff4444' : dl <= 14 ? '#ff9800' : dl <= 29 ? '#ffd600' : '#4caf50';
                                            const color = dl <= 29 && dl > 14 ? '#333' : '#fff';
                                            return (
                                                <td style={{ ...tdBase }}>
                                                    <span style={{
                                                        background: bg, color, padding: '2px 8px', borderRadius: 10,
                                                        fontWeight: 600, fontSize: 11,
                                                    }}>
                                                        {dl >= 999 ? '\u221e' : dl}
                                                    </span>
                                                </td>
                                            );
                                        })()}
                                        {/* % \u043b\u043e\u043a \u2014 \u0438\u0437 \u0438\u043d\u0434\u0435\u043a\u0441\u0430 \u043b\u043e\u043a\u0430\u043b\u0438\u0437\u0430\u0446\u0438\u0438 */}
                                        {(() => {
                                            const p = locMap.get(a.nm_id);
                                            if (p == null) return <td style={{ ...tdBase, color: 'var(--color-text-muted)' }}>{'\u2014'}</td>;
                                            const color = p >= 80 ? '#16a34a' : p >= 50 ? '#a16207' : '#ef4444';
                                            return (
                                                <td style={{ ...tdBase, fontWeight: 600, color }}>
                                                    {formatNumber(p, 1)}%
                                                </td>
                                            );
                                        })()}
                                        {/* Total Need */}
                                        <td style={{ ...tdBase, fontWeight: 700 }}>
                                            {a.total_need > 0 ? formatNumber(a.total_need, 0) : '\u2014'}
                                        </td>

                                        {/* RF Stock per warehouse */}
                                        {rfWarehouses.map(wh => {
                                            const avail = a.rf_stocks[wh.id]?.available || 0;
                                            const hasStock = avail > 0;
                                            return (
                                                <td key={`rf_${wh.id}`} style={{
                                                    ...tdBase,
                                                    color: hasStock ? '#22c55e' : (a.rf_stocks[wh.id] !== undefined ? '#ef4444' : 'var(--color-text-muted)'),
                                                    fontWeight: hasStock ? 600 : 400,
                                                }}>
                                                    {hasStock ? formatNumber(avail, 0) : (a.rf_stocks[wh.id] !== undefined ? '0' : '\u2014')}
                                                </td>
                                            );
                                        })}

                                        {/* In Assembly */}
                                        <td style={{ ...tdBase }}>
                                            {a.in_assembly > 0 ? formatNumber(a.in_assembly, 0) : '\u2014'}
                                        </td>

                                        {/* In Transit */}
                                        <td style={{ ...tdBase }}>
                                            {a.in_transit > 0
                                                ? (a.in_transit_date
                                                    ? `${formatNumber(a.in_transit, 0)}(${formatTransitDate(a.in_transit_date)})`
                                                    : formatNumber(a.in_transit, 0))
                                                : '\u2014'}
                                        </td>

                                        {/* Can Send */}
                                        <td style={{ ...tdBase, color: a.can_send > 0 ? '#22c55e' : 'var(--color-text-muted)', fontWeight: a.can_send > 0 ? 600 : 400 }}>
                                            {a.can_send > 0 ? formatNumber(a.can_send, 0) : '\u2014'}
                                        </td>

                                        {/* Send with bump = \u0438\u0442\u043e\u0433\u043e\u0432\u044b\u0439 \u043f\u043b\u0430\u043d (Hamilton + \u00ab\u0414\u043e\u0440\u0430\u0441\u043f\u0440\u0435\u0434\u0435\u043b\u0438\u0442\u044c\u00bb) */}
                                        {(() => {
                                            const sendTotal = getSendWithBump(a.nm_id, a.can_send);
                                            const bumpDelta = sendTotal - a.can_send;
                                            return (
                                                <td style={{
                                                    ...tdBase,
                                                    color: sendTotal > 0 ? '#16a34a' : 'var(--color-text-muted)',
                                                    fontWeight: sendTotal > 0 ? 700 : 400,
                                                    background: bumpDelta > 0 ? 'rgba(34,197,94,0.06)' : undefined,
                                                }}
                                                title={bumpDelta > 0
                                                    ? `\u0411\u0430\u0437\u043e\u0432\u044b\u0439 \u043f\u043b\u0430\u043d: ${formatNumber(a.can_send, 0)} + bump \u00ab\u0414\u043e\u0440\u0430\u0441\u043f\u0440\u0435\u0434\u0435\u043b\u0438\u0442\u044c\u00bb: +${formatNumber(bumpDelta, 0)}`
                                                    : '\u041f\u043b\u0430\u043d \u043a \u043e\u0442\u0433\u0440\u0443\u0437\u043a\u0435 (= \u041c\u043e\u0433\u0443 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c, \u0442.\u043a. bump \u043d\u0435 \u0441\u0440\u0430\u0431\u043e\u0442\u0430\u043b)'}>
                                                    {sendTotal > 0 ? formatNumber(sendTotal, 0) : '\u2014'}
                                                    {bumpDelta > 0 && (
                                                        <span style={{
                                                            marginLeft: 4, fontSize: 9, fontWeight: 600,
                                                            color: '#8b5cf6', verticalAlign: 'super',
                                                        }}>
                                                            +{formatNumber(bumpDelta, 0)}
                                                        </span>
                                                    )}
                                                </td>
                                            );
                                        })()}

                                        {/* Deficit */}
                                        <td style={{ ...tdBase }}>
                                            {a.deficit > 0 ? (
                                                <span style={{
                                                    background: 'rgba(239,68,68,0.12)', color: '#ef4444',
                                                    padding: '2px 8px', borderRadius: 10, fontWeight: 600, fontSize: 11,
                                                }}>
                                                    {formatNumber(a.deficit, 0)}
                                                </span>
                                            ) : (
                                                <span style={{ color: '#22c55e' }}>{'\u2705'}</span>
                                            )}
                                        </td>

                                        {/* WB Warehouse needs */}
                                        {visibleWbWarehouses.map(wh => {
                                            const rawNeed = getArticleWbNeed(a, wh.name);
                                            const bumpQty = bumpedCells.get(a.nm_id)?.get(wh.name) || 0;
                                            const m = getCellAcceptanceMarks(a.nm_id, wh.name);
                                            // При packageFilter — обнуляем ячейку если для пары (SKU, склад)
                                            // нет нужного пакета. «Моно» = любой склад принимающий моно
                                            // (в т.ч. bi-modal Краснодар/Электросталь), «Короб» = любой принимающий короб.
                                            const need = packageFilter === 'mono' && !m.mono ? 0
                                                : packageFilter === 'box' && !m.box ? 0
                                                : rawNeed;
                                            // Подсветка bumped ячейки фиолетовым — пользователь видит куда
                                            // ушёл свободный FF-сток через «Дораспределить».
                                            const isBumped = bumpQty > 0 && need > 0;
                                            const onlyMono = m.checked && !m.box && m.mono;
                                            const onlySuper = m.checked && !m.box && !m.mono && m.super;
                                            const cellBg = m.closed
                                                ? 'rgba(148,163,184,0.18)'
                                                : isBumped
                                                    ? 'rgba(139,92,246,0.12)'
                                                    : onlyMono
                                                        ? 'rgba(234,179,8,0.10)'
                                                        : onlySuper
                                                            ? 'rgba(168,85,247,0.10)'
                                                            : need > 0 ? 'rgba(239,68,68,0.08)' : undefined;
                                            const cellColor = m.closed
                                                ? '#64748b'
                                                : onlyMono
                                                    ? '#a16207'
                                                    : onlySuper
                                                        ? '#7e22ce'
                                                        : need > 0 ? '#ef4444' : 'var(--color-text-muted)';
                                            // \u041f\u0440\u0438\u043e\u0440\u0438\u0442\u0435\u0442: \u043a\u043e\u0440\u043e\u0431 \u2192 \u043c\u043e\u043d\u043e \u2192 super. \u041f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u043c \u0442\u043e\u043b\u044c\u043a\u043e \u0432\u044b\u0441\u0448\u0438\u0439 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0439 \u0442\u0438\u043f.
                                            const badgeParts: string[] = [];
                                            if (m.closed) badgeParts.push('\u26d4');
                                            else if (m.box) badgeParts.push('\ud83d\udce6');
                                            else if (m.mono) badgeParts.push('\ud83d\udcd0');
                                            else if (m.super) badgeParts.push('\ud83d\udd12');
                                            const badge = badgeParts.length ? ' ' + badgeParts.join('') : '';
                                            const tipParts: string[] = [];
                                            if (m.closed) tipParts.push(`WB \u043d\u0435 \u043f\u0440\u0438\u043d\u0438\u043c\u0430\u0435\u0442 \u043d\u0430 \u00ab${wh.name}\u00bb \u0432 \u0431\u043b\u0438\u0436\u0430\u0439\u0448\u0438\u0435 14 \u0434\u043d\u0435\u0439`);
                                            else if (m.box) tipParts.push(`\u043a\u043e\u0440\u043e\u0431\u043e\u043c${m.box_free !== undefined ? ` (\u0441\u0432\u043e\u0431\u043e\u0434\u043d\u043e ${m.box_free}/14 \u0434\u043d)` : ''}`);
                                            else if (m.mono) tipParts.push(`\u043c\u043e\u043d\u043e-\u043f\u0430\u043b\u043b\u0435\u0442\u043e\u0439${m.mono_free !== undefined ? ` (\u0441\u0432\u043e\u0431\u043e\u0434\u043d\u043e ${m.mono_free}/14 \u0434\u043d)` : ''}`);
                                            else if (m.super) tipParts.push(`super-safe${m.super_free !== undefined ? ` (\u0441\u0432\u043e\u0431\u043e\u0434\u043d\u043e ${m.super_free}/14 \u0434\u043d)` : ''}`);
                                            const tooltipBase = tipParts.length
                                                ? (m.closed ? tipParts[0] : `\u041f\u0440\u0438\u043d\u0438\u043c\u0430\u0435\u0442: ${tipParts.join(', ')}`)
                                                : '';
                                            // \u0412 \u0441\u0431\u043e\u0440\u043a\u0435 + \u0432 \u043f\u0443\u0442\u0438 \u043d\u0430 \u044d\u0442\u043e\u0442 WB-\u0441\u043a\u043b\u0430\u0434 (target \u2014 assembly_target_map/transit_target_map).
                                            // \u041f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u043c \ud83d\ude9a N \u0435\u0441\u043b\u0438 \u0445\u043e\u0442\u044f \u0431\u044b \u043e\u0434\u043d\u043e >0 \u2014 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u0432\u0438\u0434\u0438\u0442, \u043a\u0443\u0434\u0430 \u0443\u0436\u0435 \u0435\u0434\u0435\u0442 \u0442\u043e\u0432\u0430\u0440.
                                            const asmAtWh = a.asm_by_warehouse?.[wh.name] || 0;
                                            const transitAtWh = a.transit_by_warehouse?.[wh.name] || 0;
                                            const onWayAtWh = asmAtWh + transitAtWh;
                                            const stockAtWh = getArticleWbStock(a, wh.name);
                                            const tooltip = [
                                                tooltipBase,
                                                isBumped ? `\ud83d\udce4 \u0414\u043e\u0440\u0430\u0441\u043f\u0440\u0435\u0434\u0435\u043b\u0435\u043d\u043e +${formatNumber(bumpQty, 0)} \u0448\u0442 (\u0438\u0437 \u0441\u0432\u043e\u0431\u043e\u0434\u043d\u043e\u0433\u043e FF-\u0441\u0442\u043e\u043a\u0430)` : '',
                                                stockAtWh > 0 ? `\ud83c\udfec \u0421\u0435\u0439\u0447\u0430\u0441 \u043d\u0430 \u00ab${wh.name}\u00bb: ${formatNumber(stockAtWh, 0)} \u0448\u0442` : '',
                                                onWayAtWh > 0 ? `\ud83d\ude9a \u0423\u0436\u0435 \u0435\u0434\u0435\u0442 \u0441\u044e\u0434\u0430: ${formatNumber(onWayAtWh, 0)} \u0448\u0442${asmAtWh > 0 && transitAtWh > 0 ? ` (\u0432 \u0441\u0431\u043e\u0440\u043a\u0435 ${formatNumber(asmAtWh, 0)} + \u0432 \u043f\u0443\u0442\u0438 ${formatNumber(transitAtWh, 0)})` : ''}` : '',
                                            ].filter(Boolean).join('\n');
                                            return (
                                                <td key={`wb_${wh.name}`} title={tooltip} style={{
                                                    ...tdBase,
                                                    background: cellBg,
                                                    color: cellColor,
                                                    fontWeight: need > 0 ? 600 : 400,
                                                    textDecoration: m.closed && need > 0 ? 'line-through' : undefined,
                                                }}>
                                                    <div>
                                                        {need > 0 ? formatNumber(need, 0) : '\u2014'}
                                                        {/* \u0411\u0435\u0439\u0434\u0436 \u043f\u0440\u0438\u0451\u043c\u043a\u0438. \u0415\u0441\u043b\u0438 packageFilter \u043d\u0435 \u0441\u043e\u0432\u043f\u0430\u0434\u0430\u0435\u0442 \u0441 \u0440\u0435\u0430\u043b\u044c\u043d\u044b\u043c
                                                            \u043f\u0430\u043a\u0435\u0442\u043e\u043c \u0434\u043b\u044f \u044d\u0442\u043e\u0439 \u043f\u0430\u0440\u044b (SKU, \u0441\u043a\u043b\u0430\u0434) \u2014 \u0431\u0435\u0439\u0434\u0436 \u0441\u043a\u0440\u044b\u0432\u0430\u0435\u043c, \u0447\u0442\u043e\u0431\u044b
                                                            \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u0432\u0438\u0434\u0435\u043b \u0442\u043e\u043b\u044c\u043a\u043e \u0440\u0435\u043b\u0435\u0432\u0430\u043d\u0442\u043d\u0443\u044e \u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044e (\u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440,
                                                            \u043f\u0440\u0438 \u00ab\u041c\u043e\u043d\u043e\u00bb \u043d\u0435 \u043f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u043c \ud83d\udce6). */}
                                                        {badge && (need > 0 || (m.checked && !m.closed))
                                                            && !(packageFilter === 'mono' && !m.mono)
                                                            && !(packageFilter === 'box' && !m.box) && (
                                                            <span style={{ marginLeft: 4, fontSize: 10, opacity: need > 0 ? 1 : 0.55 }}>{badge}</span>
                                                        )}
                                                    </div>
                                                    {/* \u0423\u0436\u0435 \u0432 \u0441\u0431\u043e\u0440\u043a\u0435 + \u0432 \u043f\u0443\u0442\u0438 \u043d\u0430 \u044d\u0442\u043e\u0442 \u0441\u043a\u043b\u0430\u0434 \u2014 \u043a\u0430\u043a \u0432 \u00ab\u041d\u043e\u0432\u0438\u043d\u043a\u0438\u00bb. */}
                                                    {/* \u0422\u0435\u043a\u0443\u0449\u0438\u0439 WB-\u0441\u0442\u043e\u043a \u043d\u0430 \u044d\u0442\u043e\u0442 \u0441\u043a\u043b\u0430\u0434. \u0421\u0438\u043d\u0438\u0439, \u0447\u0442\u043e\u0431\u044b \u043e\u0442\u043b\u0438\u0447\u0430\u0442\u044c \u043e\u0442 \ud83d\ude9a (\u0437\u0435\u043b\u0451\u043d\u044b\u0439). */}
                                                    {stockAtWh > 0 && (
                                                        <div style={{ fontSize: 10, color: '#0071e3', fontWeight: 600, lineHeight: 1.1, marginTop: 2 }}>
                                                            \ud83c\udfec {formatNumber(stockAtWh, 0)}
                                                        </div>
                                                    )}
                                                    {onWayAtWh > 0 && (
                                                        <div style={{ fontSize: 10, color: '#16a34a', fontWeight: 600, lineHeight: 1.1, marginTop: 2 }}>
                                                            \ud83d\ude9a {formatNumber(onWayAtWh, 0)}
                                                        </div>
                                                    )}
                                                </td>
                                            );
                                        })}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            ) : !coldStartMode ? (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">
                            {data ? 'Нет артикулов по выбранным фильтрам' : 'Нет данных. Сначала синхронизируйте склады (вкладка \"По складам\").'}
                        </div>
                    </div>
                </div>
            ) : null}

            {/* Floating Action Bar */}
            {checkedCount > 0 && (
                <div style={{
                    position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 100,
                    background: 'rgba(255,255,255,0.95)', backdropFilter: 'blur(12px)',
                    borderTop: '1px solid var(--color-border)',
                    padding: '12px 24px',
                    display: 'flex', alignItems: 'center', gap: 16,
                    boxShadow: '0 -4px 16px rgba(0,0,0,0.08)',
                }}>
                    <span style={{ fontWeight: 600, fontSize: 13 }}>
                        Выбрано: {checkedCount} артикула
                        {checkedNewcomersCount > 0 && (
                            <span style={{
                                marginLeft: 6, padding: '1px 6px', borderRadius: 6,
                                background: '#a855f7', color: '#fff', fontSize: 10, fontWeight: 700,
                            }} title="Из них новинок (cold-start)">
                                🆕 {checkedNewcomersCount}
                            </span>
                        )}
                    </span>
                    <span style={{ opacity: 0.4 }}>|</span>
                    <span style={{ fontSize: 13 }} title="Multi-source: товар берётся со всех ФФ-складов, greedy по доступности">
                        Итого: <strong>{formatNumber(assemblyTotal, 0)} шт</strong>
                    </span>
                    <button
                        className="btn btn-sm btn-primary"
                        onClick={handleCreateAssembly}
                        disabled={creatingAssembly}
                    >
                        {creatingAssembly ? 'Создание...' : 'Создать сборку'}
                    </button>
                </div>
            )}

        </div>
    );
}
