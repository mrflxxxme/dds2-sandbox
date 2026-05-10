'use client';
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
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
}

interface WbWarehouseNeed {
    name: string;
    total_need: number;
    articles: Record<number, { need: number; stock: number; avg_daily: number }>;
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

type QuickFilter = 'all' | 'deficit' | 'can_send' | 'no_wb' | 'box' | 'mono';

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
    const [creatingAssembly, setCreatingAssembly] = useState(false);
    const [supplyDays, setSupplyDays] = useState(14);
    const [analysisDays, setAnalysisDays] = useState(14);
    const [mode, setMode] = useState<'actual' | 'hypothetical'>('actual');
    /** Идеальная локализация: распределяем потребность по ближайшим доступным
     *  WB-складам (по координатам региона покупателя). Исключённые склады
     *  автоматически перебрасываются на ближайшие available по haversine.
     *  Дополнительно: district-pooling сборок и транзита (asm в Электросталь
     *  снижает потребность всех складов ЦФО пропорционально, не только Эл-сталь). */
    const [localizationOptimized] = useState(true);
    /** Только реально могу отправить: каждая клетка урезана greedy по
     *  ФФ-остатку артикула — сумма needs во всех WB-колонках ≤ available. */
    const [onlyAvailable, setOnlyAvailable] = useState(false);
    const [hypoMode, setHypoMode] = useState<HypoMode>('region');
    const [showHypoMenu, setShowHypoMenu] = useState(false);
    const [citiesStatus, setCitiesStatus] = useState<OrderCitiesStatus | null>(null);
    const [uploading, setUploading] = useState(false);
    const [brandFilter, setBrandFilter] = useState('');
    const [subjectFilter, setSubjectFilter] = useState('');
    const [quickFilter, setQuickFilter] = useState<QuickFilter>('all');
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

    useEffect(() => {
        // Сброс overrides при перезагрузке cold-start данных
        setColdStartQtyOverrides({});
    }, [coldStartData?.bench_source, coldStartData?.bench_total_orders]);

    useEffect(() => {
        if (!coldStartMode) return;
        let cancelled = false;
        (async () => {
            setColdStartLoading(true);
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
            const resp = await api.getStockNeed(
                supplyDays,
                analysisDays,
                actualMode,
                localizationOptimized,
                onlyAvailable,
            ) as StockNeedResponse;
            setData(resp);
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
        return coldStartData.main_warehouses.filter(
            w => w.share_pct > 0 && !isSpecWarehouse(w.warehouse),
        );
    }, [coldStartData, isSpecWarehouse]);

    const wbWarehouses = useMemo(() => {
        const main = (data?.warehouses ?? []).filter(w => w.total_need > 0 && !isSpecWarehouse(w.name));
        const have = new Set(main.map(w => w.name));
        // (1) Cold-start главные склады округов (новинки без истории).
        for (const cs of filteredMainWarehouses) {
            if (!have.has(cs.warehouse)) {
                main.push({ name: cs.warehouse, total_need: 0, articles: {} });
                have.add(cs.warehouse);
            }
        }
        // (2) Склады, в которые после acceptance redistribute уехал qty
        // (Невинномысск и т.д. — открытые соседи по ФО, изначально не в нашем
        // раскладе). СГТ/виртуальные пропускаем тут тоже.
        for (const it of acceptanceMap.values()) {
            for (const [wh, q] of Object.entries(it.distribution || {})) {
                if (q > 0 && !have.has(wh) && !isSpecWarehouse(wh)) {
                    main.push({ name: wh, total_need: 0, articles: {} });
                    have.add(wh);
                }
            }
        }
        return main;
    }, [data, filteredMainWarehouses, acceptanceMap, isSpecWarehouse]);

    const getArticleWbNeed = useCallback((article: NeedArticle, whName: string): number => {
        // Если acceptance-check был запущен — суммируем qty по ВСЕМ splits.
        // primary distribution содержит только самый большой split (обычно BOX),
        // а Владивосток / Великий Камень / Хабаровск (canMonopallet=true, canBox=false)
        // живут в отдельном MONOPALLET split — без суммы они скрываются.
        const checked = acceptanceMap.get(article.nm_id);
        if (checked) {
            const fromSplits = (checked.splits || []).reduce(
                (sum, s) => sum + (s.distribution?.[whName] || 0), 0,
            );
            if (fromSplits > 0) return fromSplits;
            return checked.distribution?.[whName] || 0;
        }
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
        // Обычный SKU — per-WB-need.
        if (!data?.warehouses) return 0;
        const wh = data.warehouses.find(w => w.name === whName);
        return wh?.articles?.[article.nm_id]?.need || 0;
    }, [data, acceptanceMap, coldStartData, filteredMainWarehouses, coldStartQtyOverrides, coldStartMinPack]);

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

    const filteredArticles = useMemo(() => {
        if (!data?.articles) return [];
        return data.articles.filter(a => {
            if (brandFilter && a.brand !== brandFilter) return false;
            if (subjectFilter && a.subject !== subjectFilter) return false;
            if (searchQuery) {
                const q = searchQuery.toLowerCase();
                if (!a.vendor_code.toLowerCase().includes(q)) return false;
            }
            if (quickFilter === 'deficit' && a.deficit <= 0) return false;
            if (quickFilter === 'can_send' && a.can_send <= 0) return false;
            if (quickFilter === 'no_wb' && a.stocks_wb > 0) return false;
            if (quickFilter === 'box' || quickFilter === 'mono') {
                const it = acceptanceMap.get(a.nm_id);
                if (!it) return false; // не проверяли — вне фильтра
                const expected = quickFilter === 'box' ? 'BOX' : 'MONOPALLET';
                // SKU попадает в фильтр если ХОТЯ БЫ ОДИН split имеет такой package_type
                const hasType = (it.splits ?? []).some(s => s.package_type === expected);
                if (!hasType) return false;
            }
            return true;
        });
    }, [data, brandFilter, subjectFilter, searchQuery, quickFilter, acceptanceMap]);

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
    }, [filteredArticles, sortCol, sortDir, getArticleWbNeed]);

    /* ── Totals ── */

    const totals = useMemo(() => {
        const t = {
            total_need: 0,
            revenue_30d: 0,
            in_assembly: 0,
            in_transit: 0,
            can_send: 0,
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
            t.deficit += a.deficit;
            if (data?.rf_warehouses) {
                for (const wh of data.rf_warehouses) {
                    t.rf[wh.id] = (t.rf[wh.id] || 0) + (a.rf_stocks[wh.id]?.available || 0);
                }
            }
            for (const wh of wbWarehouses) {
                t.wb[wh.name] = (t.wb[wh.name] || 0) + (getArticleWbNeed(a, wh.name));
            }
        }
        return t;
    }, [filteredArticles, data, wbWarehouses, getArticleWbNeed]);

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

    /** Set nm_id'ов которые являются новинками (cold-start). */
    const newcomerSet = useMemo(() => {
        const s = new Set<number>();
        for (const r of coldStartData?.rows ?? []) s.add(r.nm_id);
        return s;
    }, [coldStartData]);

    const assemblyTotal = useMemo(() => {
        if (!assemblyWarehouseId || !data) return 0;
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
            const available = article.rf_stocks[assemblyWarehouseId]?.available || 0;
            const need = article.total_need;
            sum += Math.floor(Math.min(available, need) / 10) * 10;
        }
        return sum;
    }, [checkedIds, assemblyWarehouseId, data, coldStartData, filteredMainWarehouses, coldStartQtyOverrides, coldStartMinPack]);

    const checkedNewcomersCount = useMemo(() => {
        let n = 0;
        for (const id of checkedIds) if (newcomerSet.has(id)) n++;
        return n;
    }, [checkedIds, newcomerSet]);

    /* ── Create assembly draft (unified: обычные + новинки в один draft) ── */

    const handleCreateAssembly = useCallback(async () => {
        if (!data || !assemblyWarehouseId || checkedIds.size === 0) return;
        setCreatingAssembly(true);

        try {
            const draftRows: AssemblyDraftRow[] = [];
            const skippedNoBarcode: string[] = [];
            const skippedNoQty: string[] = [];
            const newcomerByNm = new Map<number, ColdStartTableRow>();
            for (const r of coldStartData?.rows ?? []) newcomerByNm.set(r.nm_id, r);
            const coldStartShares: Record<string, number> = {};
            for (const w of filteredMainWarehouses) {
                coldStartShares[w.warehouse] = w.share_pct / 100;
            }

            for (const nmId of checkedIds) {
                const article = data.articles.find(a => a.nm_id === nmId);
                const newcomer = newcomerByNm.get(nmId);
                const barcode = article?.barcode;
                const vendor = article?.vendor_code || newcomer?.article_seller || `nm=${nmId}`;
                if (!barcode) {
                    skippedNoBarcode.push(vendor);
                    continue;
                }

                let qty = 0;
                let tgt: Record<string, number> = {};

                if (newcomer && coldStartData) {
                    // Новинка → cold-start распределение
                    const overrideQty = coldStartQtyOverrides[nmId];
                    const useOverride = overrideQty !== undefined && overrideQty !== newcomer.total_allocated;
                    const eff = useOverride
                        ? recomputeColdStartAlloc(overrideQty, filteredMainWarehouses, coldStartMinPack)
                        : { alloc: newcomer.allocations, total: newcomer.total_allocated };
                    qty = eff.total;
                    tgt = { ...eff.alloc };
                } else if (article) {
                    // Обычный SKU → wbNeed-based
                    const available = article.rf_stocks[assemblyWarehouseId]?.available || 0;
                    const need = article.total_need;
                    qty = Math.min(available, need);
                    if (qty > 0) {
                        let remaining = qty;
                        for (const wh of data.warehouses) {
                            const wbNeed = wh.articles?.[nmId]?.need || 0;
                            if (wbNeed > 0 && remaining > 0) {
                                const give = Math.min(remaining, wbNeed);
                                tgt[wh.name] = give;
                                remaining -= give;
                            }
                        }
                        if (remaining > 0 && Object.keys(tgt).length > 0) {
                            const firstKey = Object.keys(tgt)[0];
                            tgt[firstKey] = (tgt[firstKey] || 0) + remaining;
                        }
                    }
                }

                if (qty <= 0 || Object.keys(tgt).length === 0) {
                    skippedNoQty.push(vendor);
                    continue;
                }

                // Если acceptance проверен и вернул splits — используем post-redistribute
                // распределение per package_type (одна транспортная единица = один тип).
                // Иначе — fallback на исходный tgt с package_type=BOX.
                const acceptanceItem = acceptanceMap.get(nmId);
                const splits = acceptanceItem?.splits ?? [];
                if (acceptanceItem && splits.length > 0) {
                    for (const split of splits) {
                        const splitQty = Object.values(split.distribution).reduce((a, b) => a + b, 0);
                        if (splitQty <= 0) continue;
                        draftRows.push({
                            nm_id: nmId,
                            barcode,
                            vendor_code: vendor,
                            src: { [String(assemblyWarehouseId)]: splitQty },
                            tgt: { ...split.distribution },
                            package_type: split.package_type,
                        });
                    }
                } else {
                    draftRows.push({
                        nm_id: nmId,
                        barcode,
                        vendor_code: vendor,
                        src: { [String(assemblyWarehouseId)]: qty },
                        tgt,
                        package_type: (acceptanceItem?.package_type ?? 'BOX') as PackageType,
                    });
                }
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
                alert(lines.join('\n'));
                setCreatingAssembly(false);
                return;
            }

            // Если в draft есть новинки — добавляем все cold-start склады (даже 0-qty),
            // чтобы distribute page показала колонки и cold_start_shares работали.
            const hasNewcomers = draftRows.some(r => newcomerByNm.has(r.nm_id));
            const allColdStartTargets = hasNewcomers
                ? filteredMainWarehouses.map(w => w.warehouse)
                : [];
            const targetNames = Array.from(new Set([
                ...allColdStartTargets,
                ...draftRows.flatMap(r => Object.keys(r.tgt)),
            ]));

            const draft = await api.createAssemblyDraft({
                distribution: {
                    source_warehouse_ids: [assemblyWarehouseId],
                    target_warehouse_names: targetNames,
                    rows: draftRows,
                    pallets_count: 1,
                    pallet_weight_kg: 0,
                    estimated_ready_date: null,
                    cold_start_shares: hasNewcomers ? coldStartShares : null,
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
    }, [data, assemblyWarehouseId, checkedIds, router, slug, acceptanceMap,
        coldStartData, filteredMainWarehouses, coldStartQtyOverrides, coldStartMinPack]);

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
        if (quickFilter === 'box' || quickFilter === 'mono') setQuickFilter('all');
    }, [slug, quickFilter]);

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
            'В сборке', 'В пути', 'Могу отпр.', 'Дефицит',
            ...wbWhs.map(w => w.name),
        ];
        const rows = sortedArticles.map(a => [
            a.vendor_code, a.brand || '', a.subject || '',
            a.revenue_30d || 0, a.total_need,
            ...rfWhs.map(w => a.rf_stocks[w.id]?.available || 0),
            a.in_assembly, a.in_transit, a.can_send, a.deficit,
            ...wbWhs.map(w => getArticleWbNeed(a, w.name)),
        ]);
        const totalRow = [
            'ИТОГО', '', '', totals.revenue_30d, totals.total_need,
            ...rfWhs.map(w => totals.rf[w.id] || 0),
            totals.in_assembly, totals.in_transit, totals.can_send, totals.deficit,
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
            <div className="page-header" style={{ marginBottom: 16 }}>
                <h2 className="page-title">Потребность по складам</h2>
                <p className="page-subtitle">
                    {data
                        ? `${data.total_warehouses} складов \u00B7 ${data.total_articles} артикулов \u00B7 запас ${supplyDays} дн`
                        : 'Нет данных'}
                </p>
            </div>

            {/* KPI Cards */}
            {summary && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
                    <div className="glass-card" style={{ padding: '14px 16px' }}>
                        <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>Общая потребность</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{formatNumber(summary.total_need, 0)}</div>
                    </div>
                    <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '3px solid #22c55e' }}>
                        <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>Могу отправить</div>
                        <div style={{ fontSize: 22, fontWeight: 700, color: '#22c55e' }}>{formatNumber(summary.total_can_send, 0)}</div>
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

                <button
                    className={`btn btn-sm ${onlyAvailable ? 'btn-primary' : 'btn-secondary'}`}
                    style={{ borderRadius: 8, fontSize: 11, whiteSpace: 'nowrap' }}
                    onClick={() => setOnlyAvailable(v => !v)}
                    title="Каждая клетка матрицы урезана по ФФ-остатку артикула. Сумма needs во всех WB-колонках ≤ available на ФФ. Показывает «реально могу отправить» вместо «идеальная потребность»."
                >
                    📦 Только могу отправить {onlyAvailable ? 'ON' : 'OFF'}
                </button>

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
                    className={`btn btn-sm ${quickFilter === 'all' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setQuickFilter('all')}
                >
                    Все ({data?.total_articles || 0})
                </button>
                <button
                    className={`btn btn-sm ${quickFilter === 'deficit' ? 'btn-primary' : 'btn-secondary'}`}
                    style={quickFilter !== 'deficit' ? { borderColor: '#ef4444', color: '#ef4444' } : {}}
                    onClick={() => setQuickFilter('deficit')}
                >
                    С дефицитом ({summary?.deficit_count || 0})
                </button>
                <button
                    className={`btn btn-sm ${quickFilter === 'can_send' ? 'btn-primary' : 'btn-secondary'}`}
                    style={quickFilter !== 'can_send' ? { borderColor: '#22c55e', color: '#22c55e' } : {}}
                    onClick={() => setQuickFilter('can_send')}
                >
                    Могу отправить ({summary?.can_send_count || 0})
                </button>
                <button
                    className={`btn btn-sm ${quickFilter === 'no_wb' ? 'btn-primary' : 'btn-secondary'}`}
                    style={quickFilter !== 'no_wb' ? { borderColor: '#eab308', color: '#eab308' } : {}}
                    onClick={() => setQuickFilter('no_wb')}
                >
                    Нет на WB ({summary?.no_wb_count || 0})
                </button>
                {acceptanceMap.size > 0 && (
                    <>
                        <button
                            className={`btn btn-sm ${quickFilter === 'box' ? 'btn-primary' : 'btn-secondary'}`}
                            style={quickFilter !== 'box' ? { borderColor: '#16a34a', color: '#16a34a' } : {}}
                            onClick={() => setQuickFilter(quickFilter === 'box' ? 'all' : 'box')}
                            title="Только SKU, которые WB примет коробом на ВСЕ выбранные склады"
                        >
                            📦 Короб ({acceptanceCounts.box})
                        </button>
                        <button
                            className={`btn btn-sm ${quickFilter === 'mono' ? 'btn-primary' : 'btn-secondary'}`}
                            style={quickFilter !== 'mono' ? { borderColor: '#a16207', color: '#a16207' } : {}}
                            onClick={() => setQuickFilter(quickFilter === 'mono' ? 'all' : 'mono')}
                            title="SKU, которые WB принимает только моно-паллетой"
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
                                    <tr style={{ borderBottom: '2px solid var(--color-border)', background: '#f5f5f7' }}>
                                        <th style={{ padding: '8px 6px', width: 30 }}></th>
                                        <th style={{ padding: '8px 10px', textAlign: 'left' }}>Артикул</th>
                                        <th style={{ padding: '8px 10px', textAlign: 'right' }}>Реализ. 30д</th>
                                        <th style={{ padding: '8px 10px', textAlign: 'right' }}>ФФ</th>
                                        <th style={{ padding: '8px 10px', textAlign: 'right' }}>WB</th>
                                        <th style={{ padding: '8px 10px', textAlign: 'right' }}>В сборке</th>
                                        <th style={{ padding: '8px 10px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                            Распределить
                                            <div style={{ fontSize: 10, color: 'var(--color-text-muted)', fontWeight: 400 }}>
                                                ✏️ редактируй
                                            </div>
                                        </th>
                                        {filteredMainWarehouses.map(w => (
                                            <th key={w.warehouse} style={{ padding: '8px 10px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                <div style={{ fontSize: 11 }}>{w.warehouse}</div>
                                                <div style={{ fontSize: 10, color: 'var(--color-text-muted)', fontWeight: 400 }}>
                                                    {w.share_pct.toFixed(1)}%
                                                </div>
                                            </th>
                                        ))}
                                        <th style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700 }}>Итого</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {coldStartData.rows.map(row => {
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
                                            <td style={{ padding: '8px 10px', textAlign: 'right' }}>{formatNumber(row.rf_qty, 0)}</td>
                                            <td style={{ padding: '8px 10px', textAlign: 'right' }}>{formatNumber(row.wb_qty, 0)}</td>
                                            <td style={{ padding: '8px 10px', textAlign: 'right' }}>
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
                                                const badgeParts: string[] = [];
                                                if (m.closed) badgeParts.push('⛔');
                                                else {
                                                    if (m.box) badgeParts.push('📦');
                                                    if (m.mono) badgeParts.push('📐');
                                                    if (m.super) badgeParts.push('🔒');
                                                }
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
                                                    if (m.box) tipParts.push(`коробом${slotsLabel(m.box_free, m.box_paid, m.box_min)}`);
                                                    if (m.mono) tipParts.push(`моно-паллетой${slotsLabel(m.mono_free, m.mono_paid, m.mono_min)}`);
                                                    if (m.super) tipParts.push(`super-safe${slotsLabel(m.super_free, m.super_paid, m.super_min)}`);
                                                }
                                                const tip = tipParts.length ? (m.closed ? tipParts[0] : `Принимает: ${tipParts.join(', ')}`) : '';
                                                return (
                                                    <td key={w.warehouse} title={tip} style={{
                                                        padding: '8px 10px', textAlign: 'right',
                                                        color: cellColor,
                                                        background: cellBg,
                                                        fontWeight: v > 0 ? 500 : 400,
                                                        textDecoration: m.closed && v > 0 ? 'line-through' : undefined,
                                                    }}>
                                                        {v > 0 ? formatNumber(v, 0) : '—'}
                                                        {/* Бейдж приёмки: при v>0 — справа от qty; при v=0 + acceptance проверен и
                                                            склад доступен — рядом с прочерком (видно куда ещё МОЖНО, даже без потребности). */}
                                                        {badge && (v > 0 || (m.checked && !m.closed)) && (
                                                            <span style={{ marginLeft: 4, fontSize: 10, opacity: v > 0 ? 1 : 0.55 }}>{badge}</span>
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
                                {/* Revenue + Need (not sticky) */}
                                <th colSpan={2} style={{
                                    ...thBase, cursor: 'default',
                                    background: '#f5f5f7',
                                    borderBottom: '1px solid var(--color-border)',
                                }}>&nbsp;</th>

                                {/* МОИ СКЛАДЫ group */}
                                <th colSpan={rfWarehouses.length + 4} style={{
                                    ...thBase, cursor: 'default', textAlign: 'center',
                                    background: 'rgba(59,130,246,0.08)', fontSize: 10, fontWeight: 700,
                                    letterSpacing: 1, borderBottom: '1px solid var(--color-border)',
                                }}>МОИ СКЛАДЫ</th>

                                {/* СКЛАДЫ WB group */}
                                {wbWarehouses.length > 0 && (
                                    <th colSpan={wbWarehouses.length} style={{
                                        ...thBase, cursor: 'default', textAlign: 'center',
                                        background: 'rgba(245,158,11,0.08)', fontSize: 10, fontWeight: 700,
                                        letterSpacing: 1, borderBottom: '1px solid var(--color-border)',
                                    }}>СКЛАДЫ WB</th>
                                )}
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
                                {/* Deficit */}
                                <th style={{ ...thBase, background: 'rgba(59,130,246,0.04)' }}
                                    onClick={() => handleSort('deficit')}>
                                    ДЕФИЦИТ{sortArrow('deficit')}
                                </th>

                                {/* WB Warehouses */}
                                {wbWarehouses.map(wh => (
                                    <th key={`wb_${wh.name}`} style={{ ...thBase, background: 'rgba(245,158,11,0.04)' }}
                                        onClick={() => handleSort(`wb_${wh.name}`)}>
                                        {wh.name.length > 12 ? wh.name.slice(0, 12) + '\u2026' : wh.name}
                                        {sortArrow(`wb_${wh.name}`)}
                                    </th>
                                ))}
                            </tr>

                            {/* ИТОГО row */}
                            <tr style={{ background: 'rgba(59,130,246,0.06)', fontWeight: 700 }}>
                                <td style={{ ...stickyCheckbox, background: 'rgba(59,130,246,0.06)', borderBottom: '2px solid var(--color-border)' }}>&nbsp;</td>
                                <td style={{ ...stickyArticle, background: 'rgba(59,130,246,0.06)', borderBottom: '2px solid var(--color-border)' }}>ИТОГО</td>
                                <td style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>
                                    {formatRevenue(totals.revenue_30d)}
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
                                <td style={{ ...tdBase, fontWeight: 700, borderBottom: '2px solid var(--color-border)', color: totals.deficit > 0 ? '#ef4444' : '#22c55e' }}>
                                    {totals.deficit > 0 ? formatNumber(totals.deficit, 0) : '\u2014'}
                                </td>

                                {wbWarehouses.map(wh => (
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
                                        {wbWarehouses.map(wh => {
                                            const need = getArticleWbNeed(a, wh.name);
                                            const m = getCellAcceptanceMarks(a.nm_id, wh.name);
                                            const onlyMono = m.checked && !m.box && m.mono;
                                            const onlySuper = m.checked && !m.box && !m.mono && m.super;
                                            const cellBg = m.closed
                                                ? 'rgba(148,163,184,0.18)'
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
                                            const badgeParts: string[] = [];
                                            if (m.closed) badgeParts.push('\u26d4');
                                            else {
                                                if (m.box) badgeParts.push('\ud83d\udce6');
                                                if (m.mono) badgeParts.push('\ud83d\udcd0');
                                                if (m.super) badgeParts.push('\ud83d\udd12');
                                            }
                                            const badge = badgeParts.length ? ' ' + badgeParts.join('') : '';
                                            const tipParts: string[] = [];
                                            if (m.closed) tipParts.push(`WB \u043d\u0435 \u043f\u0440\u0438\u043d\u0438\u043c\u0430\u0435\u0442 \u043d\u0430 \u00ab${wh.name}\u00bb \u0432 \u0431\u043b\u0438\u0436\u0430\u0439\u0448\u0438\u0435 14 \u0434\u043d\u0435\u0439`);
                                            else {
                                                if (m.box) tipParts.push(`\u043a\u043e\u0440\u043e\u0431\u043e\u043c${m.box_free !== undefined ? ` (\u0441\u0432\u043e\u0431\u043e\u0434\u043d\u043e ${m.box_free}/14 \u0434\u043d)` : ''}`);
                                                if (m.mono) tipParts.push(`\u043c\u043e\u043d\u043e-\u043f\u0430\u043b\u043b\u0435\u0442\u043e\u0439${m.mono_free !== undefined ? ` (\u0441\u0432\u043e\u0431\u043e\u0434\u043d\u043e ${m.mono_free}/14 \u0434\u043d)` : ''}`);
                                                if (m.super) tipParts.push(`super-safe${m.super_free !== undefined ? ` (\u0441\u0432\u043e\u0431\u043e\u0434\u043d\u043e ${m.super_free}/14 \u0434\u043d)` : ''}`);
                                            }
                                            const tooltip = tipParts.length
                                                ? (m.closed ? tipParts[0] : `\u041f\u0440\u0438\u043d\u0438\u043c\u0430\u0435\u0442: ${tipParts.join(', ')}`)
                                                : '';
                                            return (
                                                <td key={`wb_${wh.name}`} title={tooltip} style={{
                                                    ...tdBase,
                                                    background: cellBg,
                                                    color: cellColor,
                                                    fontWeight: need > 0 ? 600 : 400,
                                                    textDecoration: m.closed && need > 0 ? 'line-through' : undefined,
                                                }}>
                                                    {need > 0 ? formatNumber(need, 0) : '\u2014'}
                                                    {/* \u0411\u0435\u0439\u0434\u0436 \u043f\u0440\u0438\u0451\u043c\u043a\u0438: \u043f\u0440\u0438 need>0 \u2014 \u0441\u043f\u0440\u0430\u0432\u0430 \u043e\u0442 qty; \u043f\u0440\u0438 need=0 + acceptance \u043f\u0440\u043e\u0432\u0435\u0440\u0435\u043d \u0438
                                                        \u0441\u043a\u043b\u0430\u0434 \u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d \u2014 \u0440\u044f\u0434\u043e\u043c \u0441 \u043f\u0440\u043e\u0447\u0435\u0440\u043a\u043e\u043c (\u0432\u0438\u0434\u043d\u043e \u043a\u0443\u0434\u0430 \u0435\u0449\u0451 \u041c\u041e\u0416\u041d\u041e). */}
                                                    {badge && (need > 0 || (m.checked && !m.closed)) && (
                                                        <span style={{ marginLeft: 4, fontSize: 10, opacity: need > 0 ? 1 : 0.55 }}>{badge}</span>
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
                    <span style={{ fontSize: 13 }}>С какого склада:</span>
                    <select
                        value={assemblyWarehouseId ?? ''}
                        onChange={e => setAssemblyWarehouseId(Number(e.target.value))}
                        style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg)', fontSize: 12 }}
                    >
                        {rfWarehouses.map(wh => (
                            <option key={wh.id} value={wh.id}>{wh.name}</option>
                        ))}
                    </select>
                    <span style={{ opacity: 0.4 }}>|</span>
                    <span style={{ fontSize: 13 }}>
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
