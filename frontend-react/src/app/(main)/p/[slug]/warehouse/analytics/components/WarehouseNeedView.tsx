'use client';
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import type { AssemblyDraftRow } from '@/types/api';

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

type QuickFilter = 'all' | 'deficit' | 'can_send' | 'no_wb';

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
            const resp = await api.getStockNeed(supplyDays, analysisDays, actualMode) as StockNeedResponse;
            setData(resp);
            if (resp.rf_warehouses?.length && !assemblyWarehouseId) {
                setAssemblyWarehouseId(resp.rf_warehouses[0].id);
            }
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : 'Ошибка загрузки';
            setError(message);
        }
        setLoading(false);
    }, [supplyDays, analysisDays, mode, assemblyWarehouseId]);

    useEffect(() => { load(); }, [load]);

    /* ── Derived data ── */

    const wbWarehouses = useMemo(() => {
        if (!data?.warehouses) return [];
        return data.warehouses.filter(w => w.total_need > 0);
    }, [data]);

    const getArticleWbNeed = useCallback((article: NeedArticle, whName: string): number => {
        if (!data?.warehouses) return 0;
        const wh = data.warehouses.find(w => w.name === whName);
        return wh?.articles?.[article.nm_id]?.need || 0;
    }, [data]);

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
            return true;
        });
    }, [data, brandFilter, subjectFilter, searchQuery, quickFilter]);

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

    const assemblyTotal = useMemo(() => {
        if (!assemblyWarehouseId || !data) return 0;
        let sum = 0;
        for (const nmId of checkedIds) {
            const article = data.articles.find(a => a.nm_id === nmId);
            if (!article) continue;
            const available = article.rf_stocks[assemblyWarehouseId]?.available || 0;
            const need = article.total_need;
            sum += Math.floor(Math.min(available, need) / 10) * 10;
        }
        return sum;
    }, [checkedIds, assemblyWarehouseId, data]);

    /* ── Create assembly draft ── */

    const handleCreateAssembly = useCallback(async () => {
        if (!data || !assemblyWarehouseId || checkedIds.size === 0) return;
        setCreatingAssembly(true);

        try {
            const draftRows: AssemblyDraftRow[] = [];
            for (const nmId of checkedIds) {
                const article = data.articles.find(a => a.nm_id === nmId);
                if (!article) continue;
                const barcode = article.barcode;
                if (!barcode) continue;

                const available = article.rf_stocks[assemblyWarehouseId]?.available || 0;
                const need = article.total_need;
                const qty = Math.min(available, need);
                if (qty <= 0) continue;

                // Default tgt: pro-rata by per-WB need from data.warehouses
                const tgt: Record<string, number> = {};
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

                draftRows.push({
                    nm_id: nmId,
                    barcode,
                    vendor_code: article.vendor_code,
                    src: { [String(assemblyWarehouseId)]: qty },
                    tgt,
                });
            }

            if (draftRows.length === 0) {
                alert('Не удалось собрать позиции (нет barcode или нечего отправлять)');
                setCreatingAssembly(false);
                return;
            }

            const targetNames = Object.keys(
                draftRows.reduce<Record<string, true>>((acc, r) => {
                    for (const k of Object.keys(r.tgt)) acc[k] = true;
                    return acc;
                }, {}),
            );

            const draft = await api.createAssemblyDraft({
                distribution: {
                    source_warehouse_ids: [assemblyWarehouseId],
                    target_warehouse_names: targetNames,
                    rows: draftRows,
                    pallets_count: 1,
                    pallet_weight_kg: 0,
                    estimated_ready_date: null,
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
    }, [data, assemblyWarehouseId, checkedIds, router, slug]);

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

                <div ref={hypoMenuRef} style={{ display: 'flex', gap: 2, background: 'var(--color-border)', borderRadius: 8, padding: 2, position: 'relative' }}>
                    <button className={`btn btn-sm ${mode === 'actual' ? 'btn-primary' : 'btn-secondary'}`}
                        style={{ borderRadius: 6, fontSize: 11 }}
                        onClick={() => { setMode('actual'); setShowHypoMenu(false); }}>Факт</button>
                    <button className={`btn btn-sm ${mode === 'hypothetical' ? 'btn-primary' : 'btn-secondary'}`}
                        style={{ borderRadius: 6, fontSize: 11 }}
                        onClick={() => setShowHypoMenu(v => !v)}>
                        Гипотез. {mode === 'hypothetical' ? (hypoMode === 'city' ? '(города)' : '(регионы)') : ''} &#9662;
                    </button>
                    {showHypoMenu && (
                        <div style={{
                            position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 100,
                            background: 'var(--color-bg)', border: '1px solid var(--color-border)',
                            borderRadius: 8, boxShadow: '0 4px 12px rgba(0,0,0,0.15)', minWidth: 260, padding: 4,
                        }}>
                            <button
                                className={`btn btn-sm ${mode === 'hypothetical' && hypoMode === 'region' ? 'btn-primary' : 'btn-secondary'}`}
                                style={{ width: '100%', textAlign: 'left', borderRadius: 6, fontSize: 11, marginBottom: 2, padding: '8px 10px' }}
                                onClick={() => { setMode('hypothetical'); setHypoMode('region'); setShowHypoMenu(false); }}
                            >
                                По регионам (из API)
                                <div style={{ fontSize: 10, opacity: 0.6, marginTop: 2 }}>Работает сразу, точность ~150 км</div>
                            </button>
                            <button
                                className={`btn btn-sm ${mode === 'hypothetical' && hypoMode === 'city' ? 'btn-primary' : 'btn-secondary'}`}
                                style={{ width: '100%', textAlign: 'left', borderRadius: 6, fontSize: 11, padding: '8px 10px' }}
                                onClick={() => {
                                    setMode('hypothetical'); setHypoMode('city'); setShowHypoMenu(false);
                                }}
                            >
                                По городам (лента заказов)
                                <div style={{ fontSize: 10, opacity: 0.6, marginTop: 2 }}>
                                    {citiesStatus?.has_data ? `Точность ~3 км` : 'Требует загрузки Excel'}
                                </div>
                            </button>
                        </div>
                    )}
                </div>

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

            {/* Table */}
            {data && sortedArticles.length > 0 ? (
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
                                            <div>{a.vendor_code}</div>
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
                                            return (
                                                <td key={`wb_${wh.name}`} style={{
                                                    ...tdBase,
                                                    background: need > 0 ? 'rgba(239,68,68,0.08)' : undefined,
                                                    color: need > 0 ? '#ef4444' : 'var(--color-text-muted)',
                                                    fontWeight: need > 0 ? 600 : 400,
                                                }}>
                                                    {need > 0 ? formatNumber(need, 0) : '\u2014'}
                                                </td>
                                            );
                                        })}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            ) : (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">
                            {data ? 'Нет артикулов по выбранным фильтрам' : 'Нет данных. Сначала синхронизируйте склады (вкладка \"По складам\").'}
                        </div>
                    </div>
                </div>
            )}

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
