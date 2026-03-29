'use client';

import { useEffect, useState, useCallback, useMemo } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { haptic } from '@/lib/telegram';

/**
 * TMA Pulse Monitor — drill-down: Brands → Categories → Articles.
 *
 * Brands: plan% from brand_plans (plan-fact/brands endpoint).
 * Categories & Articles: Δ% vs previous month from BDR.
 */

// ─── Types ───────────────────────────────────────────────────────────────────

type Level = 'brands' | 'categories' | 'articles';

interface PlanFactBrand {
    brand: string;
    plan_month: number;
    plan_adjusted: number;
    fact_mtd: number;
    pct: number;
    forecast: number;
    days_in_month: number;
    current_day: number;
    debt_prev: number;
    surplus_prev: number;
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
    cost_total?: number;
    adv_sum?: number;
    drr?: number;
    roi?: number;
    turnover_days?: number;
    abc_profit?: string;
    stocks_wb?: number;
    days_left?: number;
}

interface BdrResponse {
    summary: BdrArticle;
    articles: BdrArticle[];
    brands?: string[];
}

interface PulseCard {
    id: string;
    name: string;
    pct: number;           // plan% or Δ% vs prev month
    revenue: number;       // current period revenue
    prevRevenue?: number;  // previous period revenue
    margin?: number;       // margin %
    trend: 'up' | 'down' | 'flat';
    trendValue: number;    // absolute change %
    // detail fields (shown on expand)
    profit?: number;
    saleQty?: number;
    drr?: number;
    roi?: number;
    turnover?: number;
    abc?: string;
    stocksWb?: number;
    daysLeft?: number;
    forecast?: number;
    planAmount?: number;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

type PulseColor = 'purple' | 'green' | 'yellow' | 'orange' | 'red';

function getPulseColor(pct: number): PulseColor {
    if (pct >= 110) return 'purple';
    if (pct >= 100) return 'green';
    if (pct >= 90) return 'yellow';
    if (pct >= 80) return 'orange';
    return 'red';
}

function compactNum(n: number): string {
    const abs = Math.abs(n);
    if (abs >= 1_000_000) return (n / 1_000_000).toFixed(1).replace('.0', '') + 'M';
    if (abs >= 1_000) return (n / 1_000).toFixed(1).replace('.0', '') + 'K';
    return formatNumber(n, 0);
}

function getMonthRange(offset: number = 0): { dateFrom: string; dateTo: string } {
    const now = new Date();
    const y = now.getFullYear();
    const m = now.getMonth() + offset;
    const start = new Date(y, m, 1);
    const end = new Date(y, m + 1, 0);
    const fmt = (d: Date) => d.toISOString().slice(0, 10);
    return { dateFrom: fmt(start), dateTo: fmt(end) };
}

function currentYearMonth(): { year: number; month: number } {
    const now = new Date();
    return { year: now.getFullYear(), month: now.getMonth() + 1 };
}

const LEVEL_LABELS: Record<Level, string> = {
    brands: 'Бренды',
    categories: 'Категории',
    articles: 'Артикулы',
};

// ─── Pulse Card Component ────────────────────────────────────────────────────

function PulseCardView({ card, expanded, onTap, onDrillDown, canDrillDown }: {
    card: PulseCard;
    expanded: boolean;
    onTap: () => void;
    onDrillDown: () => void;
    canDrillDown: boolean;
}) {
    const color = getPulseColor(card.pct);

    return (
        <div
            className={`tma-pulse-card tma-pulse-${color} ${expanded ? 'tma-pulse-expanded' : ''}`}
            onClick={() => { haptic('light'); onTap(); }}
        >
            <div className="tma-pulse-card-name">{card.name}</div>
            <div className="tma-pulse-card-pct">{Math.round(card.pct)}%</div>
            <div className="tma-pulse-card-footer">
                <span className="tma-pulse-card-trend">
                    {card.trend === 'up' ? '▲' : card.trend === 'down' ? '▼' : '●'}{' '}
                    {Math.round(card.trendValue)}%
                </span>
                <span className="tma-pulse-card-rev">{compactNum(card.revenue)}</span>
            </div>

            {expanded && (
                <div className="tma-pulse-card-details" onClick={(e) => e.stopPropagation()}>
                    <div className="tma-pulse-detail-divider" />

                    <div className="tma-pulse-detail-grid">
                        {card.margin != null && (
                            <div className="tma-pulse-detail-item">
                                <div className="tma-pulse-detail-label">Маржа</div>
                                <div className="tma-pulse-detail-value">{formatNumber(card.margin, 1)}%</div>
                            </div>
                        )}
                        {card.turnover != null && card.turnover > 0 && (
                            <div className="tma-pulse-detail-item">
                                <div className="tma-pulse-detail-label">Оборач</div>
                                <div className="tma-pulse-detail-value">{Math.round(card.turnover)}дн</div>
                            </div>
                        )}
                        {card.stocksWb != null && (
                            <div className="tma-pulse-detail-item">
                                <div className="tma-pulse-detail-label">Остаток</div>
                                <div className="tma-pulse-detail-value">{compactNum(card.stocksWb)}</div>
                            </div>
                        )}
                        {card.planAmount != null && card.planAmount > 0 && (
                            <div className="tma-pulse-detail-item">
                                <div className="tma-pulse-detail-label">План</div>
                                <div className="tma-pulse-detail-value">{compactNum(card.planAmount)}</div>
                            </div>
                        )}
                        {card.forecast != null && card.forecast > 0 && (
                            <div className="tma-pulse-detail-item">
                                <div className="tma-pulse-detail-label">Прогноз</div>
                                <div className="tma-pulse-detail-value">{compactNum(card.forecast)}</div>
                            </div>
                        )}
                        {card.saleQty != null && (
                            <div className="tma-pulse-detail-item">
                                <div className="tma-pulse-detail-label">Продажи</div>
                                <div className="tma-pulse-detail-value">{compactNum(card.saleQty)} шт</div>
                            </div>
                        )}
                        {card.drr != null && card.drr > 0 && (
                            <div className="tma-pulse-detail-item">
                                <div className="tma-pulse-detail-label">ДРР</div>
                                <div className="tma-pulse-detail-value">{formatNumber(card.drr, 1)}%</div>
                            </div>
                        )}
                        {card.roi != null && (
                            <div className="tma-pulse-detail-item">
                                <div className="tma-pulse-detail-label">ROI</div>
                                <div className="tma-pulse-detail-value">{formatNumber(card.roi, 0)}%</div>
                            </div>
                        )}
                        {card.profit != null && (
                            <div className="tma-pulse-detail-item">
                                <div className="tma-pulse-detail-label">Прибыль</div>
                                <div className="tma-pulse-detail-value">{compactNum(card.profit)}</div>
                            </div>
                        )}
                        {card.daysLeft != null && card.daysLeft > 0 && (
                            <div className="tma-pulse-detail-item">
                                <div className="tma-pulse-detail-label">Запас</div>
                                <div className="tma-pulse-detail-value">{card.daysLeft} дн</div>
                            </div>
                        )}
                        {card.abc && (
                            <div className="tma-pulse-detail-item">
                                <div className="tma-pulse-detail-label">ABC</div>
                                <div className="tma-pulse-detail-value">{card.abc}</div>
                            </div>
                        )}
                    </div>

                    {canDrillDown && (
                        <button
                            className="tma-btn tma-btn-primary tma-btn-block tma-pulse-drill-btn"
                            onClick={(e) => { e.stopPropagation(); haptic('medium'); onDrillDown(); }}
                        >
                            Подробнее →
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export default function TmaPulsePage() {
    const params = useParams();
    const slug = params.slug as string;

    // Navigation state
    const [level, setLevel] = useState<Level>('brands');
    const [selectedBrand, setSelectedBrand] = useState<string | null>(null);
    const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
    const [expandedId, setExpandedId] = useState<string | null>(null);

    // Data
    const [cards, setCards] = useState<PulseCard[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Summary
    const [totalRevenue, setTotalRevenue] = useState(0);
    const [avgPct, setAvgPct] = useState(0);
    const [avgMargin, setAvgMargin] = useState(0);
    const [alertCount, setAlertCount] = useState(0);

    // ─── Load Brands ─────────────────────────────────────────────────────────

    const loadBrands = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const { year, month } = currentYearMonth();
            const cur = getMonthRange(0);

            const [planFact, bdr] = await Promise.all([
                api.request<PlanFactBrand[]>('GET', `/api/v1/planning/plan-fact/brands?year=${year}&month=${month}`),
                api.request<BdrResponse>('GET', `/api/v1/reports/wb_bdr?date_from=${cur.dateFrom}&date_to=${cur.dateTo}&group_by=brand`),
            ]);

            // Build lookup: brand → BDR metrics
            const bdrMap = new Map<string, BdrArticle>();
            for (const a of bdr.articles || []) {
                if (a.brand) bdrMap.set(a.brand, a);
            }

            const result: PulseCard[] = [];
            let sumRev = 0;
            let sumPct = 0;
            let sumMargin = 0;
            let alerts = 0;

            // Brands with plans
            const brandsWithPlan = new Set(planFact.map(p => p.brand));

            for (const pf of planFact) {
                const bdrData = bdrMap.get(pf.brand);
                const revenue = pf.fact_mtd || bdrData?.realization || 0;
                const pct = pf.pct || 0;
                const margin = bdrData?.margin_pct ?? 0;

                result.push({
                    id: pf.brand,
                    name: pf.brand,
                    pct,
                    revenue,
                    margin,
                    trend: pct >= 100 ? 'up' : 'down',
                    trendValue: Math.abs(pct - 100),
                    profit: bdrData?.profit,
                    saleQty: bdrData?.sale_qty,
                    drr: bdrData?.drr,
                    roi: bdrData?.roi,
                    turnover: bdrData?.turnover_days,
                    forecast: pf.forecast,
                    planAmount: pf.plan_adjusted,
                });

                sumRev += revenue;
                sumPct += pct;
                sumMargin += margin;
                if (pct < 80) alerts++;
            }

            // Brands without plans (from BDR only) — skip or show with 0% plan
            for (const a of bdr.articles || []) {
                if (a.brand && !brandsWithPlan.has(a.brand) && (a.realization || 0) > 0) {
                    const revenue = a.realization || 0;
                    result.push({
                        id: a.brand,
                        name: a.brand,
                        pct: 0,
                        revenue,
                        margin: a.margin_pct ?? 0,
                        trend: 'flat',
                        trendValue: 0,
                        profit: a.profit,
                        saleQty: a.sale_qty,
                        drr: a.drr,
                        roi: a.roi,
                        turnover: a.turnover_days,
                    });
                    sumRev += revenue;
                    sumMargin += a.margin_pct ?? 0;
                }
            }

            result.sort((a, b) => b.revenue - a.revenue);

            const count = result.length || 1;
            setCards(result);
            setTotalRevenue(sumRev);
            setAvgPct(planFact.length > 0 ? sumPct / planFact.length : 0);
            setAvgMargin(sumMargin / count);
            setAlertCount(alerts);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    // ─── Load Categories (Δ% vs prev month) ─────────────────────────────────

    const loadCategories = useCallback(async (brand: string) => {
        setLoading(true);
        setError('');
        try {
            const cur = getMonthRange(0);
            const prev = getMonthRange(-1);

            const [curBdr, prevBdr] = await Promise.all([
                api.request<BdrResponse>('GET',
                    `/api/v1/reports/wb_bdr?date_from=${cur.dateFrom}&date_to=${cur.dateTo}&brand=${encodeURIComponent(brand)}&group_by=subject`),
                api.request<BdrResponse>('GET',
                    `/api/v1/reports/wb_bdr?date_from=${prev.dateFrom}&date_to=${prev.dateTo}&brand=${encodeURIComponent(brand)}&group_by=subject`),
            ]);

            const prevMap = new Map<string, BdrArticle>();
            for (const a of prevBdr.articles || []) {
                if (a.subject) prevMap.set(a.subject, a);
            }

            const result: PulseCard[] = [];
            let sumRev = 0;
            let sumMargin = 0;
            let alerts = 0;

            for (const a of curBdr.articles || []) {
                const name = a.subject || 'Без категории';
                const revenue = a.realization || 0;
                const prevData = prevMap.get(name);
                const prevRev = prevData?.realization || 0;
                const pct = prevRev > 0 ? (revenue / prevRev) * 100 : (revenue > 0 ? 999 : 0);
                const margin = a.margin_pct ?? 0;

                result.push({
                    id: name,
                    name,
                    pct: Math.min(pct, 999),
                    revenue,
                    prevRevenue: prevRev,
                    margin,
                    trend: pct >= 100 ? 'up' : pct < 100 ? 'down' : 'flat',
                    trendValue: Math.abs(pct - 100),
                    profit: a.profit,
                    saleQty: a.sale_qty,
                    drr: a.drr,
                    roi: a.roi,
                    turnover: a.turnover_days,
                    stocksWb: a.stocks_wb,
                    daysLeft: a.days_left,
                });

                sumRev += revenue;
                sumMargin += margin;
                if (pct < 80) alerts++;
            }

            result.sort((a, b) => b.revenue - a.revenue);

            const count = result.length || 1;
            setCards(result);
            setTotalRevenue(sumRev);
            setAvgPct(count > 0 ? result.reduce((s, c) => s + c.pct, 0) / count : 0);
            setAvgMargin(sumMargin / count);
            setAlertCount(alerts);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    // ─── Load Articles (Δ% vs prev month) ────────────────────────────────────

    const loadArticles = useCallback(async (brand: string, category: string) => {
        setLoading(true);
        setError('');
        try {
            const cur = getMonthRange(0);
            const prev = getMonthRange(-1);

            const [curBdr, prevBdr] = await Promise.all([
                api.request<BdrResponse>('GET',
                    `/api/v1/reports/wb_bdr?date_from=${cur.dateFrom}&date_to=${cur.dateTo}&brand=${encodeURIComponent(brand)}&group_by=article`),
                api.request<BdrResponse>('GET',
                    `/api/v1/reports/wb_bdr?date_from=${prev.dateFrom}&date_to=${prev.dateTo}&brand=${encodeURIComponent(brand)}&group_by=article`),
            ]);

            // Filter by category (subject)
            const curFiltered = (curBdr.articles || []).filter(a => (a.subject || 'Без категории') === category);
            const prevMap = new Map<string, BdrArticle>();
            for (const a of prevBdr.articles || []) {
                const key = a.vendor_code || String(a.nm_id);
                if (key) prevMap.set(key, a);
            }

            const result: PulseCard[] = [];
            let sumRev = 0;
            let sumMargin = 0;
            let alerts = 0;

            for (const a of curFiltered) {
                const key = a.vendor_code || String(a.nm_id);
                const name = a.vendor_code || `#${a.nm_id}`;
                const revenue = a.realization || 0;
                const prevData = prevMap.get(key);
                const prevRev = prevData?.realization || 0;
                const pct = prevRev > 0 ? (revenue / prevRev) * 100 : (revenue > 0 ? 999 : 0);
                const margin = a.margin_pct ?? 0;

                result.push({
                    id: key,
                    name,
                    pct: Math.min(pct, 999),
                    revenue,
                    prevRevenue: prevRev,
                    margin,
                    trend: pct >= 100 ? 'up' : pct < 100 ? 'down' : 'flat',
                    trendValue: Math.abs(pct - 100),
                    profit: a.profit,
                    saleQty: a.sale_qty,
                    drr: a.drr,
                    roi: a.roi,
                    turnover: a.turnover_days,
                    stocksWb: a.stocks_wb,
                    daysLeft: a.days_left,
                    abc: a.abc_profit,
                });

                sumRev += revenue;
                sumMargin += margin;
                if (pct < 80) alerts++;
            }

            result.sort((a, b) => b.revenue - a.revenue);

            const count = result.length || 1;
            setCards(result);
            setTotalRevenue(sumRev);
            setAvgPct(count > 0 ? result.reduce((s, c) => s + c.pct, 0) / count : 0);
            setAvgMargin(sumMargin / count);
            setAlertCount(alerts);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    // ─── Navigation ──────────────────────────────────────────────────────────

    const navigateTo = useCallback((newLevel: Level, brand?: string, category?: string) => {
        setExpandedId(null);
        setLevel(newLevel);
        setSelectedBrand(brand ?? null);
        setSelectedCategory(category ?? null);
    }, []);

    // Load data on level change
    useEffect(() => {
        if (level === 'brands') loadBrands();
        else if (level === 'categories' && selectedBrand) loadCategories(selectedBrand);
        else if (level === 'articles' && selectedBrand && selectedCategory) loadArticles(selectedBrand, selectedCategory);
    }, [level, selectedBrand, selectedCategory, loadBrands, loadCategories, loadArticles]);

    const handleDrillDown = useCallback((card: PulseCard) => {
        if (level === 'brands') {
            navigateTo('categories', card.id);
        } else if (level === 'categories' && selectedBrand) {
            navigateTo('articles', selectedBrand, card.id);
        }
    }, [level, selectedBrand, navigateTo]);

    const handleBack = useCallback(() => {
        haptic('light');
        if (level === 'articles') navigateTo('categories', selectedBrand!);
        else if (level === 'categories') navigateTo('brands');
    }, [level, selectedBrand, navigateTo]);

    // ─── Breadcrumbs ─────────────────────────────────────────────────────────

    const breadcrumbs = useMemo(() => {
        const parts: { label: string; onClick?: () => void }[] = [];
        parts.push({
            label: 'Бренды',
            onClick: level !== 'brands' ? () => { haptic('light'); navigateTo('brands'); } : undefined,
        });
        if (selectedBrand) {
            parts.push({
                label: selectedBrand,
                onClick: level === 'articles' ? () => { haptic('light'); navigateTo('categories', selectedBrand); } : undefined,
            });
        }
        if (selectedCategory) {
            parts.push({ label: selectedCategory });
        }
        return parts;
    }, [level, selectedBrand, selectedCategory, navigateTo]);

    // Subtitle
    const subtitle = level === 'brands'
        ? 'План%'
        : 'Δ% vs прошлый месяц';

    const now = new Date();
    const monthName = now.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });

    return (
        <div className="tma-page">
            {/* Header */}
            <div className="tma-page-header">
                <div className="tma-pulse-header-row">
                    {level !== 'brands' && (
                        <button className="tma-pulse-back" onClick={handleBack}>←</button>
                    )}
                    <div>
                        <div className="tma-page-title">Пульс</div>
                        <div className="tma-page-subtitle">{subtitle} &middot; {monthName}</div>
                    </div>
                </div>
            </div>

            {/* Breadcrumbs */}
            {level !== 'brands' && (
                <div className="tma-pulse-breadcrumbs">
                    {breadcrumbs.map((b, i) => (
                        <span key={i}>
                            {i > 0 && <span className="tma-pulse-bc-sep"> › </span>}
                            {b.onClick ? (
                                <span className="tma-pulse-bc-link" onClick={b.onClick}>{b.label}</span>
                            ) : (
                                <span className="tma-pulse-bc-current">{b.label}</span>
                            )}
                        </span>
                    ))}
                </div>
            )}

            {/* Summary bar */}
            {!loading && !error && cards.length > 0 && (
                <div className="tma-pulse-summary">
                    <div className="tma-pulse-summary-item">
                        <div className="tma-pulse-summary-label">ВЫРУЧ</div>
                        <div className="tma-pulse-summary-value">{compactNum(totalRevenue)}</div>
                    </div>
                    <div className="tma-pulse-summary-item">
                        <div className="tma-pulse-summary-label">{level === 'brands' ? 'ПЛАН' : 'Δ СР'}</div>
                        <div className={`tma-pulse-summary-value ${avgPct < 80 ? 'tma-stat-negative' : avgPct >= 100 ? 'tma-stat-positive' : ''}`}>
                            {Math.round(avgPct)}%
                        </div>
                    </div>
                    <div className="tma-pulse-summary-item">
                        <div className="tma-pulse-summary-label">МАРЖА</div>
                        <div className="tma-pulse-summary-value">{formatNumber(avgMargin, 1)}%</div>
                    </div>
                    <div className="tma-pulse-summary-item">
                        <div className="tma-pulse-summary-label">АЛЕРТ</div>
                        <div className={`tma-pulse-summary-value ${alertCount > 0 ? 'tma-stat-negative' : ''}`}>
                            {alertCount}
                        </div>
                    </div>
                </div>
            )}

            {/* Content */}
            {loading ? (
                <div className="tma-loading"><div className="tma-spinner" /><div className="tma-loading-text">Загрузка...</div></div>
            ) : error ? (
                <div className="tma-empty">
                    <div className="tma-empty-icon">⚠️</div>
                    <div className="tma-empty-text">{error}</div>
                    <button className="tma-btn tma-btn-primary" style={{ marginTop: 16 }}
                        onClick={() => { haptic('light'); if (level === 'brands') loadBrands(); else if (selectedBrand && level === 'categories') loadCategories(selectedBrand); else if (selectedBrand && selectedCategory) loadArticles(selectedBrand, selectedCategory); }}>
                        Повторить
                    </button>
                </div>
            ) : cards.length === 0 ? (
                <div className="tma-empty">
                    <div className="tma-empty-icon">📭</div>
                    <div className="tma-empty-text">Нет данных за этот период</div>
                </div>
            ) : (
                <>
                    <div className="tma-pulse-grid">
                        {cards.map((card) => (
                            <PulseCardView
                                key={card.id}
                                card={card}
                                expanded={expandedId === card.id}
                                onTap={() => setExpandedId(expandedId === card.id ? null : card.id)}
                                onDrillDown={() => handleDrillDown(card)}
                                canDrillDown={level !== 'articles'}
                            />
                        ))}
                    </div>

                    {/* Legend */}
                    <div className="tma-pulse-legend">
                        <span className="tma-pulse-legend-item"><span className="tma-pulse-dot tma-pulse-dot-purple" /> 110+</span>
                        <span className="tma-pulse-legend-item"><span className="tma-pulse-dot tma-pulse-dot-green" /> 100+</span>
                        <span className="tma-pulse-legend-item"><span className="tma-pulse-dot tma-pulse-dot-yellow" /> 90+</span>
                        <span className="tma-pulse-legend-item"><span className="tma-pulse-dot tma-pulse-dot-orange" /> 80+</span>
                        <span className="tma-pulse-legend-item"><span className="tma-pulse-dot tma-pulse-dot-red" /> &lt;80</span>
                    </div>
                </>
            )}
        </div>
    );
}
