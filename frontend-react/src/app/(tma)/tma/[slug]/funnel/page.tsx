'use client';

import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { haptic } from '@/lib/telegram';

/**
 * TMA Funnel page — visual sales funnel, ad metrics, top/bottom products.
 * Period switcher (7d/14d/30d) + brand filter.
 */

// ─── Types ───────────────────────────────────────────────────────────────────

type Period = '7' | '14' | '30';

interface FunnelSummary {
    open_card: number;
    add_to_cart: number;
    orders_count: number;
    orders_sum_rub: number;
    adv_sum: number;
    adv_views: number;
    adv_clicks: number;
    drr: number;
}

interface FunnelFilters {
    brands: string[];
    subjects: string[];
    min_date: string;
    max_date: string;
}

interface FunnelRow {
    nm_id?: number;
    vendor_code?: string;
    brand?: string;
    subject?: string;
    date?: string;
    open_card: number;
    add_to_cart: number;
    orders_count: number;
    orders_sum_rub: number;
    adv_sum: number;
    adv_views: number;
    adv_clicks: number;
    ctr: number;
    cpc: number;
    drr: number;
    revenue: number;
    profit: number;
    margin: number;
    cost_total: number;
    avg_price: number;
    buyout_percent: number;
    add_to_cart_pct: number;
    cart_to_order_pct: number;
}

interface FunnelDataResponse {
    data: FunnelRow[];
    has_bdr: boolean;
    group_by: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function compactNum(n: number): string {
    const abs = Math.abs(n);
    if (abs >= 1_000_000) return (n / 1_000_000).toFixed(1).replace('.0', '') + 'M';
    if (abs >= 1_000) return (n / 1_000).toFixed(1).replace('.0', '') + 'K';
    return formatNumber(n, 0);
}

function getDateRange(days: number): { dateFrom: string; dateTo: string } {
    const now = new Date();
    const to = now.toISOString().slice(0, 10);
    const from = new Date(now.getTime() - days * 86400000).toISOString().slice(0, 10);
    return { dateFrom: from, dateTo: to };
}

const PERIODS: { key: Period; label: string }[] = [
    { key: '7', label: '7 дн' },
    { key: '14', label: '14 дн' },
    { key: '30', label: '30 дн' },
];

// ─── Visual Funnel ───────────────────────────────────────────────────────────

function VisualFunnel({ summary }: { summary: FunnelSummary }) {
    const steps = [
        { label: 'Просмотры', value: summary.open_card, icon: '👁' },
        { label: 'Корзина', value: summary.add_to_cart, icon: '🛒' },
        { label: 'Заказы', value: summary.orders_count, icon: '📦' },
    ];

    return (
        <div className="tma-card">
            <div className="tma-card-title">Воронка продаж</div>
            <div className="tma-funnel-visual">
                {steps.map((step, i) => {
                    const prevValue = i > 0 ? steps[i - 1].value : 0;
                    const convRate = i > 0 && prevValue > 0
                        ? ((step.value / prevValue) * 100).toFixed(1) + '%'
                        : null;
                    const widthPct = steps[0].value > 0
                        ? Math.max(25, (step.value / steps[0].value) * 100)
                        : 100;

                    return (
                        <div key={i} className="tma-funnel-step-wrapper">
                            {convRate && (
                                <div className="tma-funnel-conv">
                                    <span className="tma-funnel-conv-arrow">↓</span> {convRate}
                                </div>
                            )}
                            <div className="tma-funnel-step" style={{ width: `${widthPct}%` }}>
                                <span className="tma-funnel-step-icon">{step.icon}</span>
                                <span className="tma-funnel-step-value">{compactNum(step.value)}</span>
                                <span className="tma-funnel-step-label">{step.label}</span>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

// ─── Daily Bar Chart ─────────────────────────────────────────────────────────

function DailyBars({ data, days }: { data: FunnelRow[]; days: number }) {
    if (data.length === 0) return null;
    const maxVal = Math.max(...data.map(d => d.orders_sum_rub), 1);
    const shown = data.slice(-days);

    return (
        <div className="tma-card">
            <div className="tma-card-title">Выручка по дням</div>
            <div className="tma-funnel-bars">
                {shown.map((d, i) => {
                    const h = Math.max(4, (d.orders_sum_rub / maxVal) * 60);
                    const label = d.date
                        ? new Date(d.date).toLocaleDateString('ru-RU', { day: 'numeric' })
                        : '';
                    return (
                        <div key={i} className="tma-funnel-bar-col">
                            <div className="tma-funnel-bar-value">
                                {d.orders_sum_rub >= 1000 ? compactNum(d.orders_sum_rub) : ''}
                            </div>
                            <div className="tma-funnel-bar" style={{ height: h }} />
                            <div className="tma-funnel-bar-label">{label}</div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

// ─── Product List ────────────────────────────────────────────────────────────

function ProductList({ title, products, metric }: {
    title: string;
    products: FunnelRow[];
    metric: 'revenue' | 'drr';
}) {
    return (
        <div className="tma-card">
            <div className="tma-card-title">{title}</div>
            {products.map((p, i) => (
                <div key={i} className="tma-wh-alert-row">
                    <div className="tma-wh-alert-left">
                        <div className="tma-wh-alert-info">
                            <div className="tma-wh-alert-name">{p.vendor_code || `#${p.nm_id}`}</div>
                            <div className="tma-wh-alert-sub">
                                {p.subject}{p.brand ? ` · ${p.brand}` : ''}
                            </div>
                        </div>
                    </div>
                    <div className="tma-wh-alert-right">
                        {metric === 'revenue' ? (
                            <>
                                <div className="tma-wh-alert-days">{compactNum(p.revenue)}</div>
                                <div className="tma-wh-alert-stock">
                                    <span className={p.margin >= 0 ? 'tma-stat-positive' : 'tma-stat-negative'}>
                                        {formatNumber(p.margin, 1)}%
                                    </span>
                                </div>
                            </>
                        ) : (
                            <>
                                <div className={`tma-wh-alert-days ${p.drr > 15 ? 'tma-stat-negative' : ''}`}>
                                    {formatNumber(p.drr, 1)}%
                                </div>
                                <div className="tma-wh-alert-stock">ДРР</div>
                            </>
                        )}
                    </div>
                </div>
            ))}
        </div>
    );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export default function TmaFunnelPage() {
    const [period, setPeriod] = useState<Period>('30');
    const [brand, setBrand] = useState<string>('');
    const [brands, setBrands] = useState<string[]>([]);

    const [summary, setSummary] = useState<FunnelSummary | null>(null);
    const [daily, setDaily] = useState<FunnelRow[]>([]);
    const [products, setProducts] = useState<FunnelRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Load filters once
    useEffect(() => {
        api.request<FunnelFilters>('GET', '/api/v1/funnel/filters')
            .then(f => setBrands(f.brands || []))
            .catch(() => {});
    }, []);

    const loadData = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const days = Number(period);
            const { dateFrom, dateTo } = getDateRange(days);
            const brandParam = brand ? `&brand=${encodeURIComponent(brand)}` : '';

            const [sum, skuData, dayData] = await Promise.all([
                api.request<FunnelSummary>('GET',
                    `/api/v1/funnel/summary?date_from=${dateFrom}&date_to=${dateTo}${brandParam}`),
                api.request<FunnelDataResponse>('GET',
                    `/api/v1/funnel/data?date_from=${dateFrom}&date_to=${dateTo}&group_by=sku${brandParam}`),
                api.request<FunnelDataResponse>('GET',
                    `/api/v1/funnel/data?date_from=${dateFrom}&date_to=${dateTo}&group_by=day${brandParam}`),
            ]);

            setSummary(sum);
            setProducts(skuData.data || []);
            setDaily(dayData.data || []);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [period, brand]);

    useEffect(() => { loadData(); }, [loadData]);

    const handlePeriod = useCallback((p: Period) => { haptic('selection'); setPeriod(p); }, []);
    const handleBrand = useCallback((b: string) => { haptic('selection'); setBrand(b); }, []);

    // Computed
    const ctr = summary && summary.adv_views > 0 ? (summary.adv_clicks / summary.adv_views) * 100 : 0;
    const cpc = summary && summary.adv_clicks > 0 ? summary.adv_sum / summary.adv_clicks : 0;
    const sorted = [...products].filter(p => p.revenue > 0);
    const topByRevenue = [...sorted].sort((a, b) => b.revenue - a.revenue).slice(0, 5);
    const worstMargin = [...sorted].filter(p => p.margin < 0).sort((a, b) => a.margin - b.margin).slice(0, 5);
    const highDrr = [...sorted].filter(p => p.drr > 15).sort((a, b) => b.drr - a.drr).slice(0, 5);

    return (
        <div className="tma-page">
            <div className="tma-page-header">
                <div className="tma-page-title">Воронка</div>
                <div className="tma-page-subtitle">
                    {brand || 'Все бренды'} · {period} дней
                </div>
            </div>

            {/* Period switcher */}
            <div className="tma-period-switch">
                {PERIODS.map(({ key, label }) => (
                    <button
                        key={key}
                        className={`tma-period-btn${period === key ? ' active' : ''}`}
                        onClick={() => handlePeriod(key)}
                    >
                        {label}
                    </button>
                ))}
            </div>

            {/* Brand filter */}
            {brands.length > 1 && (
                <div className="tma-funnel-brand-filter">
                    <select
                        className="tma-input tma-funnel-select"
                        value={brand}
                        onChange={(e) => handleBrand(e.target.value)}
                    >
                        <option value="">Все бренды</option>
                        {brands.map(b => (
                            <option key={b} value={b}>{b}</option>
                        ))}
                    </select>
                </div>
            )}

            {loading ? (
                <div className="tma-loading"><div className="tma-spinner" /><div className="tma-loading-text">Загрузка...</div></div>
            ) : error ? (
                <div className="tma-empty">
                    <div className="tma-empty-icon">⚠️</div>
                    <div className="tma-empty-text">{error}</div>
                    <button className="tma-btn tma-btn-primary" style={{ marginTop: 16 }}
                        onClick={() => { haptic('light'); loadData(); }}>Повторить</button>
                </div>
            ) : !summary ? (
                <div className="tma-empty"><div className="tma-empty-icon">📭</div><div className="tma-empty-text">Нет данных</div></div>
            ) : (
                <>
                    {/* KPI strip */}
                    <div className="tma-stat-grid">
                        <div className="tma-stat-cell">
                            <div className="tma-stat-cell-value">{compactNum(summary.orders_sum_rub)}</div>
                            <div className="tma-stat-cell-label">Выручка</div>
                        </div>
                        <div className="tma-stat-cell">
                            <div className={`tma-stat-cell-value ${summary.drr > 10 ? 'tma-stat-negative' : ''}`}>
                                {formatNumber(summary.drr, 1)}%
                            </div>
                            <div className="tma-stat-cell-label">ДРР</div>
                        </div>
                        <div className="tma-stat-cell">
                            <div className="tma-stat-cell-value">{formatNumber(ctr, 1)}%</div>
                            <div className="tma-stat-cell-label">CTR</div>
                        </div>
                        <div className="tma-stat-cell">
                            <div className="tma-stat-cell-value">{formatNumber(cpc, 0)} ₽</div>
                            <div className="tma-stat-cell-label">CPC</div>
                        </div>
                    </div>

                    <VisualFunnel summary={summary} />

                    {/* Ad metrics */}
                    <div className="tma-card">
                        <div className="tma-card-title">Реклама</div>
                        <div className="tma-funnel-ad-grid">
                            <div className="tma-funnel-ad-item">
                                <div className="tma-funnel-ad-value">{compactNum(summary.adv_sum)} ₽</div>
                                <div className="tma-funnel-ad-label">Расход</div>
                            </div>
                            <div className="tma-funnel-ad-item">
                                <div className="tma-funnel-ad-value">{compactNum(summary.adv_views)}</div>
                                <div className="tma-funnel-ad-label">Показы</div>
                            </div>
                            <div className="tma-funnel-ad-item">
                                <div className="tma-funnel-ad-value">{compactNum(summary.adv_clicks)}</div>
                                <div className="tma-funnel-ad-label">Клики</div>
                            </div>
                        </div>
                    </div>

                    <DailyBars data={daily} days={Number(period)} />

                    {topByRevenue.length > 0 && (
                        <ProductList title={`Топ по выручке (${topByRevenue.length})`} products={topByRevenue} metric="revenue" />
                    )}
                    {worstMargin.length > 0 && (
                        <ProductList title={`Убыточные (${worstMargin.length})`} products={worstMargin} metric="revenue" />
                    )}
                    {highDrr.length > 0 && (
                        <ProductList title={`Высокий ДРР (${highDrr.length})`} products={highDrr} metric="drr" />
                    )}
                </>
            )}
        </div>
    );
}
