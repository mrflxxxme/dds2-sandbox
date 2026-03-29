'use client';

import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { haptic } from '@/lib/telegram';
import type { CapitalResponse, CapitalGroupRow, CapitalTrendDay } from '@/types/api';

/**
 * TMA Capital page — working capital analysis with liquidity breakdown.
 * Period switcher (7/14/30/90) + brand filter + drill-down.
 */

// ─── Types ───────────────────────────────────────────────────────────────────

type Period = '7' | '14' | '30' | '90';
type GroupBy = 'brand' | 'category' | 'article';

interface FunnelFilters {
    brands: string[];
    subjects: string[];
    min_date: string;
    max_date: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function compactNum(n: number): string {
    const abs = Math.abs(n);
    if (abs >= 1_000_000) return (n / 1_000_000).toFixed(1).replace('.0', '') + 'M';
    if (abs >= 1_000) return (n / 1_000).toFixed(1).replace('.0', '') + 'K';
    return formatNumber(n, 0);
}

const PERIODS: { key: Period; label: string }[] = [
    { key: '7', label: '7 дн' },
    { key: '14', label: '14 дн' },
    { key: '30', label: '30 дн' },
    { key: '90', label: '90 дн' },
];

const RECOMMENDATION_STYLES: Record<string, { color: string; bg: string; icon: string }> = {
    star:          { color: '#065F46', bg: '#D1FAE5', icon: '⭐' },
    reduce_10:     { color: '#1E40AF', bg: '#DBEAFE', icon: '📉' },
    reduce_20:     { color: '#9A3412', bg: '#FED7AA', icon: '📉' },
    liquidate:     { color: '#991B1B', bg: '#FEE2E2', icon: '🔥' },
    cut_ads:       { color: '#92400E', bg: '#FEF3C7', icon: '🚫' },
    check_quality: { color: '#374151', bg: '#F3F4F6', icon: '⚠️' },
    hold:          { color: '#6B7280', bg: '#F9FAFB', icon: '—' },
};

const GROUP_LABELS: Record<GroupBy, string> = {
    brand: 'Бренды',
    category: 'Категории',
    article: 'Артикулы',
};

const NEXT_LEVEL: Record<string, GroupBy | null> = {
    brand: 'category',
    category: 'article',
    article: null,
};

// ─── Summary Cards ───────────────────────────────────────────────────────────

function SummaryStrip({ data }: { data: CapitalResponse }) {
    const s = data.summary;
    return (
        <div className="tma-stat-grid">
            <div className="tma-stat-cell">
                <div className="tma-stat-cell-value">{compactNum(s.total_capital)}</div>
                <div className="tma-stat-cell-label">Капитал</div>
            </div>
            <div className="tma-stat-cell">
                <div className="tma-stat-cell-value" style={{ color: s.liquid_pct > 60 ? '#10b981' : s.liquid_pct >= 30 ? '#d97706' : '#e02424' }}>
                    {formatNumber(s.liquid_pct, 0)}%
                </div>
                <div className="tma-stat-cell-label">Ликвид</div>
            </div>
            <div className="tma-stat-cell">
                <div className="tma-stat-cell-value" style={{ color: s.illiquid_pct > 30 ? '#e02424' : s.illiquid_pct > 15 ? '#d97706' : '#6b7280' }}>
                    {formatNumber(s.illiquid_pct, 0)}%
                </div>
                <div className="tma-stat-cell-label">Неликвид</div>
            </div>
            <div className="tma-stat-cell">
                <div className="tma-stat-cell-value" style={{ color: s.roi_monthly > 20 ? '#10b981' : s.roi_monthly >= 10 ? '#d97706' : '#e02424' }}>
                    {formatNumber(s.roi_monthly, 1)}%
                </div>
                <div className="tma-stat-cell-label">ROI/мес</div>
            </div>
        </div>
    );
}

// ─── Capital Distribution Bar ────────────────────────────────────────────────

function DistributionBar({ data }: { data: CapitalResponse }) {
    const s = data.summary;
    if (s.total_capital <= 0) return null;

    const items = [
        { label: 'Ликвидный', pct: s.liquid_pct, amount: s.liquid_capital, color: '#10B981' },
        { label: 'Переходный', pct: s.transition_pct, amount: s.transition_capital, color: '#D97706' },
        { label: 'Неликвидный', pct: s.illiquid_pct, amount: s.illiquid_capital, color: '#E02424' },
    ];

    return (
        <div className="tma-card">
            <div className="tma-card-title">Распределение капитала</div>
            <div className="tma-capital-dist-bar">
                {items.map(item => item.pct > 0 && (
                    <div
                        key={item.label}
                        className="tma-capital-dist-segment"
                        style={{ width: `${Math.max(item.pct, 3)}%`, backgroundColor: item.color }}
                    />
                ))}
            </div>
            <div className="tma-capital-dist-legend">
                {items.map(item => (
                    <div key={item.label} className="tma-capital-dist-item">
                        <div className="tma-capital-dist-dot" style={{ backgroundColor: item.color }} />
                        <div>
                            <div className="tma-capital-dist-label">{item.label}</div>
                            <div className="tma-capital-dist-value">
                                {compactNum(item.amount)} <span style={{ color: item.color }}>{formatNumber(item.pct, 0)}%</span>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

// ─── Trend Chart (CSS bars) ──────────────────────────────────────────────────

function TrendChart({ trend }: { trend: CapitalTrendDay[] }) {
    if (trend.length < 2) return null;

    const maxTotal = Math.max(...trend.map(d => d.total), 1);
    const shown = trend.slice(-30); // last 30 days max

    return (
        <div className="tma-card">
            <div className="tma-card-title">Динамика капитала</div>
            <div className="tma-capital-trend">
                {shown.map((d, i) => {
                    const h = 60; // max bar height
                    const liqH = (d.liquid / maxTotal) * h;
                    const transH = (d.transition / maxTotal) * h;
                    const illiqH = (d.illiquid / maxTotal) * h;
                    const showLabel = shown.length <= 14 || i % Math.ceil(shown.length / 10) === 0;
                    const dateLabel = new Date(d.date).toLocaleDateString('ru-RU', { day: 'numeric' });

                    return (
                        <div key={d.date} className="tma-capital-trend-col">
                            <div className="tma-capital-trend-stack" style={{ height: h }}>
                                <div className="tma-capital-trend-seg" style={{ height: illiqH, backgroundColor: '#E02424' }} />
                                <div className="tma-capital-trend-seg" style={{ height: transH, backgroundColor: '#D97706' }} />
                                <div className="tma-capital-trend-seg" style={{ height: liqH, backgroundColor: '#10B981' }} />
                            </div>
                            {showLabel && <div className="tma-capital-trend-label">{dateLabel}</div>}
                        </div>
                    );
                })}
            </div>
            <div className="tma-capital-trend-legend">
                <span><span className="tma-capital-trend-dot" style={{ backgroundColor: '#10B981' }} /> Ликвид</span>
                <span><span className="tma-capital-trend-dot" style={{ backgroundColor: '#D97706' }} /> Перех</span>
                <span><span className="tma-capital-trend-dot" style={{ backgroundColor: '#E02424' }} /> Неликв</span>
            </div>
        </div>
    );
}

// ─── Group Row ───────────────────────────────────────────────────────────────

function GroupRow({ row, onTap, expanded, children }: {
    row: CapitalGroupRow;
    onTap: () => void;
    expanded: boolean;
    children?: React.ReactNode;
}) {
    const rec = row.recommendation;
    const recStyle = RECOMMENDATION_STYLES[rec?.type] || RECOMMENDATION_STYLES.hold;

    return (
        <div className={`tma-capital-group ${expanded ? 'tma-capital-group-expanded' : ''}`}>
            <div className="tma-capital-group-row" onClick={() => { haptic('light'); onTap(); }}>
                <div className="tma-capital-group-left">
                    <div className="tma-capital-group-name">
                        {row.vendor_code || row.group_key}
                        {row.nm_id && row.vendor_code && (
                            <span className="tma-capital-group-nm">#{row.nm_id}</span>
                        )}
                    </div>
                    <div className="tma-capital-group-meta">
                        {compactNum(row.capital)} ₽ · {Math.round(row.turnover_days)}дн
                    </div>
                </div>
                <div className="tma-capital-group-right">
                    <div className="tma-capital-group-liq">
                        <div className="tma-capital-liq-bar">
                            <div className="tma-capital-liq-fill" style={{
                                width: `${Math.min(row.liquid_pct, 100)}%`,
                                backgroundColor: row.liquid_pct > 60 ? '#10B981' : row.liquid_pct >= 30 ? '#D97706' : '#E02424',
                            }} />
                        </div>
                        <span className="tma-capital-liq-text">{formatNumber(row.liquid_pct, 0)}%</span>
                    </div>
                    {rec && (
                        <span className="tma-capital-rec-badge" style={{ color: recStyle.color, backgroundColor: recStyle.bg }}>
                            {recStyle.icon} {rec.label}
                        </span>
                    )}
                </div>
            </div>

            {expanded && (
                <div className="tma-capital-group-details">
                    <div className="tma-capital-detail-grid">
                        <div className="tma-capital-detail-item">
                            <div className="tma-capital-detail-label">ROI/мес</div>
                            <div className="tma-capital-detail-value" style={{
                                color: row.roi_monthly > 20 ? '#10b981' : row.roi_monthly >= 10 ? '#d97706' : '#e02424',
                            }}>{formatNumber(row.roi_monthly, 1)}%</div>
                        </div>
                        <div className="tma-capital-detail-item">
                            <div className="tma-capital-detail-label">Маржа</div>
                            <div className="tma-capital-detail-value">{formatNumber(row.margin, 1)}%</div>
                        </div>
                        <div className="tma-capital-detail-item">
                            <div className="tma-capital-detail-label">ДРР</div>
                            <div className="tma-capital-detail-value" style={{
                                color: row.drr > 12 ? '#e02424' : row.drr > 8 ? '#d97706' : '#10b981',
                            }}>{formatNumber(row.drr, 1)}%</div>
                        </div>
                        <div className="tma-capital-detail-item">
                            <div className="tma-capital-detail-label">Продаж/дн</div>
                            <div className="tma-capital-detail-value">{formatNumber(row.sales_per_day, 1)}</div>
                        </div>
                        <div className="tma-capital-detail-item">
                            <div className="tma-capital-detail-label">Неликвид</div>
                            <div className="tma-capital-detail-value" style={{
                                color: row.illiquid_pct > 30 ? '#e02424' : '#6b7280',
                            }}>{formatNumber(row.illiquid_pct, 0)}%</div>
                        </div>
                        <div className="tma-capital-detail-item">
                            <div className="tma-capital-detail-label">Заморож</div>
                            <div className="tma-capital-detail-value">{compactNum(row.frozen_amount)} ₽</div>
                        </div>
                    </div>
                    {children}
                </div>
            )}
        </div>
    );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export default function TmaCapitalPage() {
    const [period, setPeriod] = useState<Period>('30');
    const [brand, setBrand] = useState('');
    const [brands, setBrands] = useState<string[]>([]);
    const [groupBy, setGroupBy] = useState<GroupBy>('brand');

    const [includeRf, setIncludeRf] = useState(false);

    const [data, setData] = useState<CapitalResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [drillChildren, setDrillChildren] = useState<Record<string, CapitalGroupRow[]>>({});
    const [loadingDrill, setLoadingDrill] = useState<Set<string>>(new Set());

    // Load brands once
    useEffect(() => {
        api.request<FunnelFilters>('GET', '/api/v1/funnel/filters')
            .then(f => setBrands(f.brands || []))
            .catch(() => {});
    }, []);

    const loadData = useCallback(async () => {
        setLoading(true);
        setError('');
        setExpandedId(null);
        setDrillChildren({});
        try {
            const res = await api.getCapital({
                period_days: Number(period),
                brand: brand || undefined,
                group_by: groupBy,
                include_rf_stocks: includeRf,
            });
            setData(res);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [period, brand, groupBy, includeRf]);

    useEffect(() => { loadData(); }, [loadData]);

    // Drill-down
    const toggleRow = useCallback(async (row: CapitalGroupRow) => {
        const key = row.group_key;

        if (expandedId === key) {
            setExpandedId(null);
            return;
        }

        setExpandedId(key);

        const nextLevel = NEXT_LEVEL[groupBy];
        if (!nextLevel || drillChildren[key]) return;

        setLoadingDrill(prev => new Set(prev).add(key));
        try {
            const res = await api.getCapital({
                period_days: Number(period),
                brand: brand || undefined,
                group_by: nextLevel,
                parent_filter: key,
            });
            setDrillChildren(prev => ({ ...prev, [key]: res.groups }));
        } catch {
            // ignore drill error
        } finally {
            setLoadingDrill(prev => {
                const next = new Set(prev);
                next.delete(key);
                return next;
            });
        }
    }, [expandedId, groupBy, period, brand, drillChildren]);

    const handlePeriod = useCallback((p: Period) => { haptic('selection'); setPeriod(p); }, []);
    const handleBrand = useCallback((b: string) => { haptic('selection'); setBrand(b); }, []);
    const handleGroup = useCallback((g: GroupBy) => {
        haptic('selection');
        setGroupBy(g);
        setExpandedId(null);
        setDrillChildren({});
    }, []);

    return (
        <div className="tma-page">
            <div className="tma-page-header">
                <div className="tma-page-title">Капитал</div>
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
                        {brands.map(b => <option key={b} value={b}>{b}</option>)}
                    </select>
                </div>
            )}

            {/* Group switcher */}
            <div className="tma-capital-group-switch">
                {(['brand', 'category', 'article'] as const).map(g => (
                    <button
                        key={g}
                        className={`tma-period-btn${groupBy === g ? ' active' : ''}`}
                        onClick={() => handleGroup(g)}
                    >
                        {GROUP_LABELS[g]}
                    </button>
                ))}
            </div>

            {/* RF stocks toggle */}
            <label className="tma-capital-rf-toggle" onClick={() => { haptic('selection'); setIncludeRf(!includeRf); }}>
                <span className={`tma-capital-rf-check ${includeRf ? 'active' : ''}`}>
                    {includeRf ? '✓' : ''}
                </span>
                <span>Учитывать остатки на складах в РФ</span>
            </label>

            {loading ? (
                <div className="tma-loading"><div className="tma-spinner" /><div className="tma-loading-text">Загрузка...</div></div>
            ) : error ? (
                <div className="tma-empty">
                    <div className="tma-empty-icon">⚠️</div>
                    <div className="tma-empty-text">{error}</div>
                    <button className="tma-btn tma-btn-primary" style={{ marginTop: 16 }}
                        onClick={() => { haptic('light'); loadData(); }}>Повторить</button>
                </div>
            ) : !data ? (
                <div className="tma-empty"><div className="tma-empty-icon">📭</div><div className="tma-empty-text">Нет данных</div></div>
            ) : (
                <>
                    <SummaryStrip data={data} />
                    <DistributionBar data={data} />
                    <TrendChart trend={data.trend} />

                    {/* Groups list */}
                    <div className="tma-card">
                        <div className="tma-card-title">{GROUP_LABELS[groupBy]} ({data.groups.length})</div>
                        {data.groups.length === 0 ? (
                            <div className="tma-empty-text" style={{ padding: 16, textAlign: 'center' }}>Нет данных</div>
                        ) : (
                            <div className="tma-capital-groups">
                                {data.groups.map(row => (
                                    <GroupRow
                                        key={row.group_key}
                                        row={row}
                                        expanded={expandedId === row.group_key}
                                        onTap={() => toggleRow(row)}
                                    >
                                        {/* Drill-down children */}
                                        {expandedId === row.group_key && NEXT_LEVEL[groupBy] && (
                                            loadingDrill.has(row.group_key) ? (
                                                <div className="tma-capital-drill-loading">
                                                    <div className="tma-spinner" style={{ width: 20, height: 20 }} />
                                                </div>
                                            ) : drillChildren[row.group_key]?.length ? (
                                                <div className="tma-capital-drill-list">
                                                    {drillChildren[row.group_key].map(child => (
                                                        <div key={child.group_key} className="tma-capital-drill-row">
                                                            <div className="tma-capital-drill-name">
                                                                {child.vendor_code || child.group_key}
                                                                {child.nm_id && child.vendor_code && (
                                                                    <span className="tma-capital-group-nm">#{child.nm_id}</span>
                                                                )}
                                                            </div>
                                                            <div className="tma-capital-drill-meta">
                                                                {compactNum(child.capital)} ₽ · {Math.round(child.turnover_days)}дн ·{' '}
                                                                <span style={{
                                                                    color: child.liquid_pct > 60 ? '#10b981' : child.liquid_pct >= 30 ? '#d97706' : '#e02424',
                                                                }}>
                                                                    {formatNumber(child.liquid_pct, 0)}% ликв
                                                                </span>
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>
                                            ) : null
                                        )}
                                    </GroupRow>
                                ))}
                            </div>
                        )}
                    </div>
                </>
            )}
        </div>
    );
}
