'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { haptic } from '@/lib/telegram';

/**
 * TMA P&L page — two tabs: ОПИУ (P&L) and БДР (WB P&L).
 * Period switcher (month/quarter/year) + brand & category filters.
 *
 * FIX #5: setTimeout cleanup via useRef + max retry count for polling.
 */

type Tab = 'opiu' | 'bdr';
type Period = 'month' | 'quarter' | 'year';

// ─── Types ───────────────────────────────────────────────────────────────────

interface PnlSection {
    label: string;
    rows: Array<{ label: string; value: number }>;
    total?: { label: string; value: number };
}

interface OpiuRow {
    key: string;
    label: string;
    total: number;
    level: number;
    bold: boolean;
}

interface OpiuResponse {
    rows: OpiuRow[];
    months: string[];
    computing?: boolean;
    message?: string;
}

interface BdrArticle {
    nm_id?: number;
    vendor_code?: string;
    brand?: string;
    subject?: string;
    realization?: number;
    to_pay?: number;
    profit?: number;
    margin_pct?: number;
    sale_qty?: number;
    logistics?: number;
    commission?: number;
    adv_sum?: number;
    drr?: number;
    cost_total?: number;
    storage?: number;
    penalties?: number;
    roi?: number;
}

interface BdrResponse {
    summary: BdrArticle;
    articles: BdrArticle[];
    brands?: string[];
}

interface FunnelFilters {
    brands: string[];
    subjects: string[];
    min_date: string;
    max_date: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

const MAX_RETRIES = 10;
const RETRY_DELAY_MS = 3000;

function getDateRange(period: Period): { dateFrom: string; dateTo: string } {
    const now = new Date();
    const year = now.getFullYear();
    const month = now.getMonth();
    let dateFrom: Date, dateTo: Date;

    switch (period) {
        case 'month':
            dateFrom = new Date(year, month, 1);
            dateTo = new Date(year, month + 1, 0);
            break;
        case 'quarter': {
            const qStart = Math.floor(month / 3) * 3;
            dateFrom = new Date(year, qStart, 1);
            dateTo = new Date(year, qStart + 3, 0);
            break;
        }
        case 'year':
            dateFrom = new Date(year, 0, 1);
            dateTo = new Date(year, 11, 31);
            break;
    }

    const fmt = (d: Date) => d.toISOString().slice(0, 10);
    return { dateFrom: fmt(dateFrom), dateTo: fmt(dateTo) };
}

function compactNum(n: number): string {
    const abs = Math.abs(n);
    if (abs >= 1_000_000) return (n / 1_000_000).toFixed(1).replace('.0', '') + 'M';
    if (abs >= 1_000) return (n / 1_000).toFixed(1).replace('.0', '') + 'K';
    return formatNumber(n, 0);
}

// ─── OPIU Tab ────────────────────────────────────────────────────────────────

function OpiuTab({ period, brand }: { period: Period; brand: string }) {
    const [sections, setSections] = useState<PnlSection[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const timeoutRef = useRef<NodeJS.Timeout>();
    const retryCountRef = useRef(0);

    const loadData = useCallback(async () => {
        setLoading(true);
        setError('');
        retryCountRef.current = 0;

        const { dateFrom, dateTo } = getDateRange(period);
        const brandParam = brand ? `&brand=${encodeURIComponent(brand)}` : '';

        const fetchOpiu = async () => {
            try {
                const data = await api.request<OpiuResponse>(
                    'GET',
                    `/api/v1/reports/opiu?date_from=${dateFrom}&date_to=${dateTo}${brandParam}`
                );

                if (data.computing) {
                    if (retryCountRef.current < MAX_RETRIES) {
                        retryCountRef.current++;
                        timeoutRef.current = setTimeout(fetchOpiu, RETRY_DELAY_MS);
                    } else {
                        setError('Расчёт занимает слишком много времени');
                        setLoading(false);
                    }
                    return;
                }

                const rowMap = new Map<string, OpiuRow>();
                for (const r of data.rows || []) rowMap.set(r.key, r);
                const val = (key: string) => rowMap.get(key)?.total ?? 0;

                const parsed: PnlSection[] = [
                    {
                        label: 'ВЫРУЧКА',
                        rows: [
                            { label: 'Реализация', value: val('realization') },
                            { label: 'Скидка МП', value: val('spp_discount') },
                        ],
                        total: { label: 'Продажи', value: val('sales_amount') },
                    },
                    {
                        label: 'РАСХОДЫ',
                        rows: [
                            { label: 'Себестоимость', value: val('cost_price') },
                            { label: 'Логистика', value: val('logistics') },
                            { label: 'Комиссия', value: val('commission') },
                            { label: 'Штрафы', value: val('penalties') },
                            { label: 'Хранение', value: val('storage') },
                            { label: 'Реклама', value: val('advertising') },
                            { label: 'Удержания', value: val('deductions') },
                            { label: 'Приёмка', value: val('acceptance') },
                        ].filter(r => r.value !== 0),
                        total: { label: 'Валовая маржа', value: val('gross_margin') },
                    },
                    {
                        label: 'РЕЗУЛЬТАТ',
                        rows: [
                            { label: 'EBITDA', value: val('ebitda') },
                            { label: 'Налоги', value: val('taxes') },
                        ],
                        total: { label: 'Чистая прибыль', value: val('net_profit') },
                    },
                ];

                setSections(parsed);
                setLoading(false);
            } catch (err) {
                setError(err instanceof Error ? err.message : 'Ошибка загрузки');
                setLoading(false);
            }
        };

        fetchOpiu();

        return () => {
            if (timeoutRef.current) clearTimeout(timeoutRef.current);
        };
    }, [period, brand]);

    useEffect(() => {
        const cleanup = loadData();
        return () => {
            if (timeoutRef.current) clearTimeout(timeoutRef.current);
        };
    }, [loadData]);

    if (loading) return <div className="tma-loading"><div className="tma-spinner" /><div className="tma-loading-text">Загрузка...</div></div>;
    if (error) return (
        <div className="tma-empty">
            <div className="tma-empty-icon">⚠️</div>
            <div className="tma-empty-text">{error}</div>
            <button className="tma-btn tma-btn-primary" style={{ marginTop: 16 }}
                onClick={() => { haptic('light'); loadData(); }}>Повторить</button>
        </div>
    );
    if (sections.length === 0) return <div className="tma-empty"><div className="tma-empty-icon">📭</div><div className="tma-empty-text">Нет данных</div></div>;

    return (
        <>
            {sections.map((section, si) => (
                <div key={si} className="tma-card">
                    <div className="tma-pnl-section-header">{section.label}</div>
                    {section.rows.map((row, ri) => (
                        <div key={ri} className="tma-pnl-row">
                            <span className="tma-pnl-label">{row.label}</span>
                            <span className={`tma-pnl-value ${row.value >= 0 ? 'tma-stat-positive' : 'tma-stat-negative'}`}>
                                {formatNumber(row.value, 0)} ₽
                            </span>
                        </div>
                    ))}
                    {section.total && (
                        <div className="tma-pnl-row tma-pnl-total">
                            <span className="tma-pnl-label">{section.total.label}</span>
                            <span className={`tma-pnl-value ${section.total.value >= 0 ? 'tma-stat-positive' : 'tma-stat-negative'}`}>
                                {formatNumber(section.total.value, 0)} ₽
                            </span>
                        </div>
                    )}
                </div>
            ))}
        </>
    );
}

// ─── BDR Tab ─────────────────────────────────────────────────────────────────

function BdrTab({ period, brand, category }: { period: Period; brand: string; category: string }) {
    const [data, setData] = useState<BdrArticle[]>([]);
    const [summary, setSummary] = useState<BdrArticle | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [groupBy, setGroupBy] = useState<'article' | 'brand' | 'subject'>('article');

    const loadData = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const { dateFrom, dateTo } = getDateRange(period);
            let params = `date_from=${dateFrom}&date_to=${dateTo}&group_by=${groupBy}`;
            if (brand) params += `&brand=${encodeURIComponent(brand)}`;

            const resp = await api.request<BdrResponse>('GET', `/api/v1/reports/wb_bdr?${params}`);
            setSummary(resp.summary || null);

            let articles = resp.articles || [];
            // Client-side filter by category
            if (category && groupBy === 'article') {
                articles = articles.filter(a => a.subject === category);
            }
            setData(articles);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [period, brand, category, groupBy]);

    useEffect(() => { loadData(); }, [loadData]);

    if (loading) return <div className="tma-loading"><div className="tma-spinner" /><div className="tma-loading-text">Загрузка...</div></div>;
    if (error) return (
        <div className="tma-empty">
            <div className="tma-empty-icon">⚠️</div>
            <div className="tma-empty-text">{error}</div>
            <button className="tma-btn tma-btn-primary" style={{ marginTop: 16 }}
                onClick={() => { haptic('light'); loadData(); }}>Повторить</button>
        </div>
    );

    // Summary card
    const s = summary;

    return (
        <>
            {/* Group switcher */}
            <div className="tma-period-switch" style={{ marginBottom: 12 }}>
                {([
                    { key: 'article' as const, label: 'Артикулы' },
                    { key: 'brand' as const, label: 'Бренды' },
                    { key: 'subject' as const, label: 'Категории' },
                ]).map(({ key, label }) => (
                    <button key={key}
                        className={`tma-period-btn${groupBy === key ? ' active' : ''}`}
                        onClick={() => { haptic('selection'); setGroupBy(key); }}
                    >{label}</button>
                ))}
            </div>

            {/* Summary */}
            {s && (
                <div className="tma-stat-grid">
                    <div className="tma-stat-cell">
                        <div className="tma-stat-cell-value">{compactNum(s.realization || 0)}</div>
                        <div className="tma-stat-cell-label">Выручка</div>
                    </div>
                    <div className="tma-stat-cell">
                        <div className={`tma-stat-cell-value ${(s.profit || 0) >= 0 ? 'tma-stat-positive' : 'tma-stat-negative'}`}>
                            {compactNum(s.profit || 0)}
                        </div>
                        <div className="tma-stat-cell-label">Прибыль</div>
                    </div>
                    <div className="tma-stat-cell">
                        <div className={`tma-stat-cell-value ${(s.margin_pct || 0) >= 0 ? 'tma-stat-positive' : 'tma-stat-negative'}`}>
                            {formatNumber(s.margin_pct || 0, 1)}%
                        </div>
                        <div className="tma-stat-cell-label">Маржа</div>
                    </div>
                    <div className="tma-stat-cell">
                        <div className={`tma-stat-cell-value ${(s.drr || 0) > 10 ? 'tma-stat-negative' : ''}`}>
                            {formatNumber(s.drr || 0, 1)}%
                        </div>
                        <div className="tma-stat-cell-label">ДРР</div>
                    </div>
                </div>
            )}

            {/* Breakdown */}
            {s && (s.logistics || s.commission || s.adv_sum || s.cost_total) && (
                <div className="tma-card">
                    <div className="tma-card-title">Расходы</div>
                    {[
                        { label: 'Себестоимость', value: s.cost_total },
                        { label: 'Логистика', value: s.logistics },
                        { label: 'Комиссия', value: s.commission },
                        { label: 'Реклама', value: s.adv_sum },
                        { label: 'Хранение', value: s.storage },
                        { label: 'Штрафы', value: s.penalties },
                    ].filter(r => r.value && r.value !== 0).map((r, i) => (
                        <div key={i} className="tma-pnl-row">
                            <span className="tma-pnl-label">{r.label}</span>
                            <span className="tma-pnl-value tma-stat-negative">
                                {compactNum(Math.abs(r.value!))} ₽
                            </span>
                        </div>
                    ))}
                </div>
            )}

            {/* Articles/Brands/Subjects list */}
            {data.length > 0 && (
                <div className="tma-card">
                    <div className="tma-card-title">
                        {groupBy === 'article' ? 'Артикулы' : groupBy === 'brand' ? 'Бренды' : 'Категории'}
                        {' '}({data.length})
                    </div>
                    {data.slice(0, 20).map((a, i) => {
                        const name = groupBy === 'article'
                            ? (a.vendor_code || `#${a.nm_id}`)
                            : groupBy === 'brand'
                                ? (a.brand || '—')
                                : (a.subject || '—');
                        return (
                            <div key={i} className="tma-wh-alert-row">
                                <div className="tma-wh-alert-left">
                                    <div className="tma-wh-alert-info">
                                        <div className="tma-wh-alert-name">{name}</div>
                                        {groupBy === 'article' && a.subject && (
                                            <div className="tma-wh-alert-sub">{a.subject}</div>
                                        )}
                                    </div>
                                </div>
                                <div className="tma-wh-alert-right">
                                    <div className="tma-wh-alert-days">{compactNum(a.realization || 0)}</div>
                                    <div className="tma-wh-alert-stock">
                                        <span className={(a.margin_pct || 0) >= 0 ? 'tma-stat-positive' : 'tma-stat-negative'}>
                                            {formatNumber(a.margin_pct || 0, 1)}%
                                        </span>
                                    </div>
                                </div>
                            </div>
                        );
                    })}
                    {data.length > 20 && <div className="tma-wh-more">+{data.length - 20} ещё</div>}
                </div>
            )}

            {data.length === 0 && !loading && (
                <div className="tma-empty"><div className="tma-empty-icon">📭</div><div className="tma-empty-text">Нет данных</div></div>
            )}
        </>
    );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

const TABS: { key: Tab; label: string }[] = [
    { key: 'opiu', label: 'ОПИУ' },
    { key: 'bdr', label: 'БДР' },
];

export default function TmaPnlPage() {
    const [tab, setTab] = useState<Tab>('opiu');
    const [period, setPeriod] = useState<Period>('month');
    const [brand, setBrand] = useState('');
    const [category, setCategory] = useState('');
    const [brands, setBrands] = useState<string[]>([]);
    const [categories, setCategories] = useState<string[]>([]);

    // Load filters
    useEffect(() => {
        api.request<FunnelFilters>('GET', '/api/v1/funnel/filters')
            .then(f => {
                setBrands(f.brands || []);
                setCategories(f.subjects || []);
            })
            .catch(() => {});
    }, []);

    return (
        <div className="tma-page">
            <div className="tma-page-header">
                <div className="tma-page-title">P&L</div>
                <div className="tma-page-subtitle">
                    {brand || 'Все бренды'}
                    {category ? ` · ${category}` : ''}
                </div>
            </div>

            {/* Tab switcher: ОПИУ / БДР */}
            <div className="tma-period-switch" style={{ marginBottom: 8 }}>
                {TABS.map(({ key, label }) => (
                    <button key={key}
                        className={`tma-period-btn${tab === key ? ' active' : ''}`}
                        onClick={() => { haptic('selection'); setTab(key); }}
                    >{label}</button>
                ))}
            </div>

            {/* Period switcher */}
            <div className="tma-period-switch">
                {([
                    { key: 'month' as Period, label: 'Месяц' },
                    { key: 'quarter' as Period, label: 'Квартал' },
                    { key: 'year' as Period, label: 'Год' },
                ]).map(({ key, label }) => (
                    <button key={key}
                        className={`tma-period-btn${period === key ? ' active' : ''}`}
                        onClick={() => { haptic('selection'); setPeriod(key); }}
                    >{label}</button>
                ))}
            </div>

            {/* Filters */}
            <div className="tma-funnel-filters">
                {brands.length > 1 && (
                    <select className="tma-input tma-funnel-select"
                        value={brand} onChange={(e) => { haptic('selection'); setBrand(e.target.value); }}>
                        <option value="">Все бренды</option>
                        {brands.map(b => <option key={b} value={b}>{b}</option>)}
                    </select>
                )}
                {categories.length > 1 && tab === 'bdr' && (
                    <select className="tma-input tma-funnel-select"
                        value={category} onChange={(e) => { haptic('selection'); setCategory(e.target.value); }}>
                        <option value="">Все категории</option>
                        {categories.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                )}
            </div>

            {/* Content */}
            {tab === 'opiu' && <OpiuTab period={period} brand={brand} />}
            {tab === 'bdr' && <BdrTab period={period} brand={brand} category={category} />}
        </div>
    );
}
