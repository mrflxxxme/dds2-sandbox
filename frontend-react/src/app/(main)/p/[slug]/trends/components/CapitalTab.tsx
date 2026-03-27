'use client';

import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import {
    AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
    PieChart, Pie, Cell,
} from 'recharts';
import type {
    CapitalResponse, CapitalGroupRow, CapitalTrendDay,
    ChartTooltipPayloadItem,
} from '@/types/api';

// ─── Props ─────────────────────────────────────────────────────────────────

interface CapitalTabProps {
    trendDays: number;
    brand: string;
    onTrendDaysChange: (days: number) => void;
    onBrandChange: (brand: string) => void;
    brands: string[];
}

// ─── Helpers ───────────────────────────────────────────────────────────────

const fmtMoney = (n: number) => `${formatNumber(n, 0)} \u20bd`;
const fmtPct = (n: number) => `${formatNumber(n, 1)}%`;

const RECOMMENDATION_STYLES: Record<string, { bg: string; color: string; icon: string }> = {
    star:          { bg: '#D1FAE5', color: '#065F46', icon: '\u2b50' },
    reduce_10:     { bg: '#DBEAFE', color: '#1E40AF', icon: '\ud83d\udcc9' },
    reduce_20:     { bg: '#FED7AA', color: '#9A3412', icon: '\ud83d\udcc9' },
    liquidate:     { bg: '#FEE2E2', color: '#991B1B', icon: '\ud83d\udd25' },
    cut_ads:       { bg: '#FEF3C7', color: '#92400E', icon: '\ud83d\udeab' },
    check_quality: { bg: '#F3F4F6', color: '#374151', icon: '\u26a0\ufe0f' },
    hold:          { bg: '#F9FAFB', color: '#6B7280', icon: '\u2014' },
};

const GROUP_BY_LEVELS: Record<string, string> = {
    brand: 'category',
    category: 'article',
};

// ─── Summary Card ──────────────────────────────────────────────────────────

function SummaryCard({ label, value, subtitle, accentColor, bgColor, badge }: {
    label: string;
    value: string;
    subtitle?: string;
    accentColor: string;
    bgColor: string;
    badge?: string;
}) {
    return (
        <div
            className="glass-card"
            style={{
                padding: '20px 24px',
                borderLeft: `4px solid ${accentColor}`,
                background: bgColor,
                flex: '1 1 180px',
                minWidth: 180,
            }}
        >
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, fontWeight: 500 }}>
                {label}
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <div style={{ fontSize: 26, fontWeight: 700, color: accentColor }}>
                    {value}
                </div>
                {badge && (
                    <span style={{
                        fontSize: 12, fontWeight: 600,
                        padding: '2px 8px', borderRadius: 10,
                        background: `${accentColor}18`, color: accentColor,
                    }}>
                        {badge}
                    </span>
                )}
            </div>
            {subtitle && (
                <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 2 }}>
                    {subtitle}
                </div>
            )}
        </div>
    );
}

// ─── Chart Tooltip ─────────────────────────────────────────────────────────

function ChartTooltipContent({ active, payload, label }: {
    active?: boolean;
    payload?: ChartTooltipPayloadItem[];
    label?: string;
}) {
    if (!active || !payload?.length) return null;
    const NAMES: Record<string, string> = {
        liquid: 'Ликвидный',
        transition: 'Переходный',
        illiquid: 'Неликвидный',
    };
    return (
        <div style={{
            background: 'var(--color-bg-card)', border: '1px solid var(--color-border)',
            borderRadius: 8, padding: '10px 14px', fontSize: 12, boxShadow: '0 2px 8px rgba(0,0,0,.12)',
        }}>
            <div style={{ fontWeight: 600, marginBottom: 6, color: 'var(--color-text)' }}>
                {label ? formatDate(label) : ''}
            </div>
            {payload.map((p) => (
                <div key={p.name} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, color: 'var(--color-text)' }}>
                    <span style={{ color: p.color || p.fill }}>{NAMES[p.name] || p.name}</span>
                    <span style={{ fontWeight: 600 }}>{fmtMoney(p.value)}</span>
                </div>
            ))}
        </div>
    );
}

// ─── Skeleton ──────────────────────────────────────────────────────────────

function SkeletonCards() {
    return (
        <div>
            <div style={{ display: 'flex', gap: 16, marginBottom: 20, flexWrap: 'wrap' }}>
                {[1, 2, 3, 4, 5].map(i => (
                    <div key={i} className="glass-card" style={{
                        height: 90, flex: '1 1 180px', minWidth: 180,
                        background: 'var(--color-bg-card)',
                        borderLeft: '4px solid var(--color-border)',
                        animation: 'pulse 1.5s ease-in-out infinite',
                    }} />
                ))}
            </div>
            <div className="glass-card" style={{
                height: 250, marginBottom: 20,
                animation: 'pulse 1.5s ease-in-out infinite',
            }} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {[1, 2, 3, 4, 5].map(i => (
                    <div key={i} className="glass-card" style={{
                        height: 44,
                        animation: 'pulse 1.5s ease-in-out infinite',
                    }} />
                ))}
            </div>
            <style>{`
                @keyframes pulse {
                    0%, 100% { opacity: 1; }
                    50% { opacity: 0.5; }
                }
            `}</style>
        </div>
    );
}

// ─── Recommendation Badge ──────────────────────────────────────────────────

function RecommendationBadge({ type, label }: { type: string; label: string }) {
    const style = RECOMMENDATION_STYLES[type] || RECOMMENDATION_STYLES.hold;
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            padding: '3px 10px', borderRadius: 6,
            fontSize: 11, fontWeight: 600,
            background: style.bg, color: style.color,
            whiteSpace: 'nowrap',
        }}>
            {style.icon} {label}
        </span>
    );
}

// ─── Color helpers ─────────────────────────────────────────────────────────

function liquidColor(pct: number) {
    if (pct > 60) return '#10b981';
    if (pct >= 30) return '#D97706';
    return '#E02424';
}

function illiquidColor(pct: number) {
    return pct > 30 ? '#E02424' : pct > 15 ? '#D97706' : '#6B7280';
}

function roiColor(roi: number) {
    if (roi > 20) return '#10b981';
    if (roi >= 10) return '#D97706';
    return '#E02424';
}

function turnoverColor(days: number) {
    if (days < 30) return '#10b981';
    if (days < 60) return '#D97706';
    return '#E02424';
}

// ─── Liquid % progress bar ────────────────────────────────────────────────

function LiquidBar({ pct }: { pct: number }) {
    const color = liquidColor(pct);
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ flex: 1, height: 6, borderRadius: 3, background: 'var(--color-border)', minWidth: 40 }}>
                <div style={{ width: `${Math.min(pct, 100)}%`, height: '100%', borderRadius: 3, background: color, transition: 'width 0.3s' }} />
            </div>
            <span style={{ fontSize: 12, fontWeight: 600, color, minWidth: 38, textAlign: 'right' }}>
                {formatNumber(pct, 0)}%
            </span>
        </div>
    );
}

// ─── Main Component ────────────────────────────────────────────────────────

export default function CapitalTab({ trendDays, brand, onTrendDaysChange, onBrandChange, brands }: CapitalTabProps) {
    const [data, setData] = useState<CapitalResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [groupBy, setGroupBy] = useState<string>('brand');
    const [includeRfStocks, setIncludeRfStocks] = useState(false);
    const [elasticity, setElasticity] = useState(1.8);
    const [illiquidThreshold, setIlliquidThreshold] = useState(60);
    const [expandedRows, setExpandedRows] = useState<Record<string, CapitalGroupRow[]>>({});
    const [loadingRows, setLoadingRows] = useState<Set<string>>(new Set());

    const loadData = useCallback(async () => {
        setLoading(true);
        setError(null);
        setExpandedRows({});
        try {
            const res = await api.getCapital({
                period_days: trendDays,
                brand: brand || undefined,
                group_by: groupBy,
                include_rf_stocks: includeRfStocks,
                elasticity,
                illiquid_threshold: illiquidThreshold,
            });
            setData(res);
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : 'Failed to load capital data';
            setError(message);
            console.error('Capital error:', err);
        } finally {
            setLoading(false);
        }
    }, [trendDays, brand, groupBy, includeRfStocks, elasticity, illiquidThreshold]);

    useEffect(() => { loadData(); }, [loadData]);

    // ─── Drill-down ────────────────────────────────────────────────────────

    const toggleRow = useCallback(async (row: CapitalGroupRow) => {
        const key = row.group_key;
        if (expandedRows[key]) {
            setExpandedRows(prev => {
                const next = { ...prev };
                delete next[key];
                return next;
            });
            return;
        }

        const nextLevel = GROUP_BY_LEVELS[groupBy];
        if (!nextLevel) return;

        setLoadingRows(prev => new Set(prev).add(key));
        try {
            const res = await api.getCapital({
                period_days: trendDays,
                brand: brand || undefined,
                group_by: nextLevel,
                parent_filter: key,
                include_rf_stocks: includeRfStocks,
                elasticity,
            });
            setExpandedRows(prev => ({ ...prev, [key]: res.groups }));
        } catch (err) {
            console.error('Drill-down error:', err);
        } finally {
            setLoadingRows(prev => {
                const next = new Set(prev);
                next.delete(key);
                return next;
            });
        }
    }, [expandedRows, groupBy, trendDays, brand, includeRfStocks, elasticity]);

    // ─── Chart data formatting ─────────────────────────────────────────────

    const chartData = (data?.trend || []).map((d: CapitalTrendDay) => ({
        ...d,
        date: formatDate(d.date),
    }));

    const summary = data?.summary;

    // ─── Trend arrow ───────────────────────────────────────────────────────

    const trendArrow = (val: number) => {
        if (val > 0) return `\u25b2 +${formatNumber(val, 1)}%`;
        if (val < 0) return `\u25bc ${formatNumber(val, 1)}%`;
        return '\u2192 0%';
    };

    const trendColor = (val: number) => val > 0 ? '#10b981' : val < 0 ? '#E02424' : '#6B7280';

    // ─── Render ────────────────────────────────────────────────────────────

    return (
        <div>
            {/* Summary cards */}
            {!loading && !error && summary && (
                <div style={{ display: 'flex', gap: 16, marginBottom: 20, flexWrap: 'wrap' }}>
                    <SummaryCard
                        label="Весь капитал"
                        value={fmtMoney(summary.total_capital)}
                        accentColor="#6B7280"
                        bgColor="rgba(107, 114, 128, 0.06)"
                    />
                    <SummaryCard
                        label="Ликвидный (<30 дн)"
                        value={fmtMoney(summary.liquid_capital)}
                        badge={fmtPct(summary.liquid_pct)}
                        accentColor="#10B981"
                        bgColor="rgba(16, 185, 129, 0.06)"
                    />
                    <SummaryCard
                        label={`Переходный (30-${illiquidThreshold} дн)`}
                        value={fmtMoney(summary.transition_capital)}
                        badge={fmtPct(summary.transition_pct)}
                        accentColor="#D97706"
                        bgColor="rgba(217, 119, 6, 0.06)"
                    />
                    <SummaryCard
                        label={`Неликвидный (>${illiquidThreshold} дн)`}
                        value={fmtMoney(summary.illiquid_capital)}
                        badge={fmtPct(summary.illiquid_pct)}
                        accentColor="#E02424"
                        bgColor="rgba(224, 36, 36, 0.06)"
                    />
                    <SummaryCard
                        label="ROI капитала"
                        value={`${formatNumber(summary.roi_monthly, 1)}%/\u043c\u0435\u0441`}
                        subtitle={trendArrow(summary.roi_trend)}
                        accentColor="#7C3AED"
                        bgColor="rgba(124, 58, 237, 0.06)"
                    />
                </div>
            )}

            {/* Pie chart — capital distribution */}
            {!loading && !error && data && data.summary.total_capital > 0 && (
                <div className="glass-card" style={{ padding: '16px 20px', marginBottom: 20 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text)', marginBottom: 12 }}>
                        Распределение капитала
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 40 }}>
                        <ResponsiveContainer width={220} height={220}>
                            <PieChart>
                                <Pie
                                    data={[
                                        { name: 'Ликвидный', value: data.summary.liquid_capital, pct: data.summary.liquid_pct },
                                        { name: 'Переходный', value: data.summary.transition_capital, pct: data.summary.transition_pct },
                                        { name: 'Неликвидный', value: data.summary.illiquid_capital, pct: data.summary.illiquid_pct },
                                    ]}
                                    cx="50%"
                                    cy="50%"
                                    innerRadius={55}
                                    outerRadius={90}
                                    dataKey="value"
                                    stroke="none"
                                >
                                    <Cell fill="#10B981" />
                                    <Cell fill="#D97706" />
                                    <Cell fill="#E02424" />
                                </Pie>
                                <Tooltip
                                    formatter={(value: number) => fmtMoney(value)}
                                    contentStyle={{ fontSize: 12, borderRadius: 8 }}
                                />
                            </PieChart>
                        </ResponsiveContainer>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                            {[
                                { label: 'Ликвидный (<30 дн)', value: data.summary.liquid_capital, pct: data.summary.liquid_pct, color: '#10B981' },
                                { label: `Переходный (30-${illiquidThreshold} дн)`, value: data.summary.transition_capital, pct: data.summary.transition_pct, color: '#D97706' },
                                { label: `Неликвидный (>${illiquidThreshold} дн)`, value: data.summary.illiquid_capital, pct: data.summary.illiquid_pct, color: '#E02424' },
                            ].map(item => (
                                <div key={item.label} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                                    <div style={{ width: 12, height: 12, borderRadius: 3, backgroundColor: item.color, flexShrink: 0 }} />
                                    <div>
                                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>{item.label}</div>
                                        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--color-text)' }}>
                                            {fmtMoney(item.value)} <span style={{ fontSize: 12, color: item.color, fontWeight: 500 }}>{fmtPct(item.pct)}</span>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                        {/* Brand bars — like mockup */}
                        {data.groups.length > 0 && groupBy === 'brand' && (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 200 }}>
                                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-dim)', marginBottom: 4 }}>По брендам</div>
                                {data.groups.slice(0, 5).map(g => {
                                    const liq = g.liquid_pct;
                                    const trans = 100 - g.liquid_pct - g.illiquid_pct;
                                    const illiq = g.illiquid_pct;
                                    return (
                                        <div key={g.group_key}>
                                            <div style={{ fontSize: 11, color: 'var(--color-text)', marginBottom: 2 }}>{g.group_key}</div>
                                            <div style={{ display: 'flex', height: 14, borderRadius: 4, overflow: 'hidden', background: '#f3f4f6' }}>
                                                {liq > 0 && <div style={{ width: `${liq}%`, background: '#10B981' }} />}
                                                {trans > 0 && <div style={{ width: `${trans}%`, background: '#D97706' }} />}
                                                {illiq > 0 && <div style={{ width: `${illiq}%`, background: '#E02424' }} />}
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Trend chart */}
            {!loading && !error && chartData.length > 1 && (
                <div className="glass-card" style={{ padding: '16px 20px', marginBottom: 20 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text)', marginBottom: 12 }}>
                        Динамика капитала
                    </div>
                    <ResponsiveContainer width="100%" height={250}>
                        <AreaChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                            <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'var(--color-text-dim)' }} />
                            <YAxis
                                tick={{ fontSize: 11, fill: 'var(--color-text-dim)' }}
                                tickFormatter={(v: number) => {
                                    if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
                                    if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
                                    return String(v);
                                }}
                            />
                            <Tooltip content={<ChartTooltipContent />} />
                            <Legend
                                formatter={(value: string) => {
                                    const names: Record<string, string> = {
                                        liquid: '\u041b\u0438\u043a\u0432\u0438\u0434\u043d\u044b\u0439',
                                        transition: '\u041f\u0435\u0440\u0435\u0445\u043e\u0434\u043d\u044b\u0439',
                                        illiquid: '\u041d\u0435\u043b\u0438\u043a\u0432\u0438\u0434\u043d\u044b\u0439',
                                    };
                                    return names[value] || value;
                                }}
                            />
                            <Area type="monotone" dataKey="liquid" stackId="1" stroke="#10B981" fill="#10B981" fillOpacity={0.6} />
                            <Area type="monotone" dataKey="transition" stackId="1" stroke="#D97706" fill="#D97706" fillOpacity={0.5} />
                            <Area type="monotone" dataKey="illiquid" stackId="1" stroke="#E02424" fill="#E02424" fillOpacity={0.4} />
                        </AreaChart>
                    </ResponsiveContainer>
                </div>
            )}

            {/* Filter bar */}
            <div className="glass-card" style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '10px 16px', marginBottom: 16, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Период:</span>
                {[7, 14, 30, 90].map(d => (
                    <button key={d}
                        onClick={() => onTrendDaysChange(d)}
                        style={{
                            padding: '5px 14px', fontSize: 13, borderRadius: 6, cursor: 'pointer',
                            border: trendDays === d ? '1px solid var(--color-accent)' : '1px solid var(--color-border)',
                            background: trendDays === d ? 'rgba(139,92,246,0.15)' : 'var(--color-bg-card)',
                            color: trendDays === d ? 'var(--color-accent)' : 'var(--color-text)',
                            fontWeight: trendDays === d ? 600 : 400,
                            transition: 'all 0.15s',
                        }}
                    >
                        {d} дн
                    </button>
                ))}

                <span style={{ width: 1, height: 24, background: 'var(--color-border)', margin: '0 4px' }} />

                <select value={brand} onChange={e => onBrandChange(e.target.value)}
                    style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '5px 8px', color: 'var(--color-text)', fontSize: 13 }}>
                    <option value="">Все бренды</option>
                    {brands.map(b => <option key={b} value={b}>{b}</option>)}
                </select>

                <span style={{ width: 1, height: 24, background: 'var(--color-border)', margin: '0 4px' }} />

                <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Группировка:</span>
                {(['brand', 'category', 'article'] as const).map(g => {
                    const labels: Record<string, string> = { brand: 'Бренд', category: 'Категория', article: 'Артикул' };
                    return (
                        <button key={g}
                            onClick={() => setGroupBy(g)}
                            style={{
                                padding: '5px 14px', fontSize: 13, borderRadius: 6, cursor: 'pointer',
                                border: groupBy === g ? '1px solid var(--color-accent)' : '1px solid var(--color-border)',
                                background: groupBy === g ? 'rgba(139,92,246,0.15)' : 'var(--color-bg-card)',
                                color: groupBy === g ? 'var(--color-accent)' : 'var(--color-text)',
                                fontWeight: groupBy === g ? 600 : 400,
                                transition: 'all 0.15s',
                            }}
                        >
                            {labels[g]}
                        </button>
                    );
                })}

                <span style={{ width: 1, height: 24, background: 'var(--color-border)', margin: '0 4px' }} />

                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text)', cursor: 'pointer' }}>
                    <input
                        type="checkbox"
                        checked={includeRfStocks}
                        onChange={e => setIncludeRfStocks(e.target.checked)}
                        style={{ cursor: 'pointer' }}
                    />
                    Учитывать склады РФ
                </label>

                <span style={{ width: 1, height: 24, background: 'var(--color-border)', margin: '0 4px' }} />

                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text)' }}>
                    Эластичность:
                    <input
                        type="number"
                        value={elasticity}
                        onChange={e => {
                            const v = parseFloat(e.target.value);
                            if (!isNaN(v) && v >= 0.5 && v <= 5.0) setElasticity(v);
                        }}
                        min={0.5}
                        max={5.0}
                        step={0.1}
                        style={{
                            width: 60, padding: '4px 8px', fontSize: 13, borderRadius: 6,
                            border: '1px solid var(--color-border)',
                            background: 'var(--color-bg-card)',
                            color: 'var(--color-text)',
                        }}
                    />
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-dim)' }}>
                    Порог неликвида:
                    <input
                        type="number"
                        value={illiquidThreshold}
                        onChange={e => {
                            const v = parseInt(e.target.value);
                            if (!isNaN(v) && v >= 30 && v <= 120) setIlliquidThreshold(v);
                        }}
                        min={30}
                        max={120}
                        step={5}
                        style={{
                            width: 55, padding: '4px 8px', fontSize: 13, borderRadius: 6,
                            border: '1px solid var(--color-border)',
                            background: 'var(--color-bg-card)',
                            color: 'var(--color-text)',
                        }}
                    />
                    дн
                </label>
            </div>

            {/* Content */}
            {loading && <SkeletonCards />}

            {error && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 40 }}>
                    <div style={{ fontSize: 18, marginBottom: 8, color: 'var(--color-danger)' }}>
                        Ошибка загрузки
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16 }}>
                        {error}
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={loadData}>
                        Повторить
                    </button>
                </div>
            )}

            {!loading && !error && data && data.groups.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 60 }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>{'\ud83d\udce6'}</div>
                    <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 4 }}>
                        Нет данных по товарам за выбранный период
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>
                        Попробуйте изменить фильтры или период.
                    </div>
                </div>
            )}

            {!loading && !error && data && data.groups.length > 0 && (
                <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                    <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 400px)' }}>
                        <table className="data-table" style={{ minWidth: 1200, borderCollapse: 'separate', borderSpacing: 0 }}>
                            <thead>
                                <tr>
                                    <th style={thStyle}>Группа</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Капитал \u20bd</th>
                                    <th style={{ ...thStyle, textAlign: 'center', minWidth: 120 }}>Ликвид%</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Неликвид%</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Заморожено \u20bd</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>ROI %/мес</th>
                                    <th style={{ ...thStyle, textAlign: 'center' }}>Оборач.</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Продаж/день</th>
                                    <th style={{ ...thStyle, textAlign: 'center' }}>Рекомендация</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>ROI -10%</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.groups.map((row, i) => (
                                    <GroupRows
                                        key={row.group_key}
                                        row={row}
                                        index={i}
                                        indent={0}
                                        canExpand={row.children_count > 0 && !!GROUP_BY_LEVELS[groupBy]}
                                        isExpanded={!!expandedRows[row.group_key]}
                                        isLoading={loadingRows.has(row.group_key)}
                                        onToggle={toggleRow}
                                        children={expandedRows[row.group_key]}
                                    />
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {!loading && !error && data && (
                <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-dim)' }}>
                    Всего товаров: {data.total_products} &middot; Период: {data.period_days} дней &middot;
                    Эластичность: {data.elasticity}
                </div>
            )}
        </div>
    );
}

// ─── Table header style ────────────────────────────────────────────────────

const thStyle: React.CSSProperties = {
    position: 'sticky', top: 0, background: '#f9fafb', zIndex: 10,
    whiteSpace: 'nowrap', fontSize: 11,
    borderBottom: '1px solid #e5e7eb', color: '#4b5563',
    padding: '8px 12px',
};

// ─── Group table rows (parent + children) ──────────────────────────────────

function GroupRows({ row, index, indent, canExpand, isExpanded, isLoading, onToggle, children }: {
    row: CapitalGroupRow;
    index: number;
    indent: number;
    canExpand: boolean;
    isExpanded: boolean;
    isLoading: boolean;
    onToggle: (row: CapitalGroupRow) => void;
    children?: CapitalGroupRow[];
}) {
    const rowBg = index % 2 === 0 ? '#ffffff' : '#f9fafb';
    const rec = row.recommendation;
    const roiMinus10 = rec?.roi_at_minus_10;

    return (
        <>
            <tr
                style={{
                    background: rowBg, color: '#111827',
                    cursor: canExpand ? 'pointer' : 'default',
                }}
                onClick={() => canExpand && onToggle(row)}
            >
                {/* Group name */}
                <td style={{ ...tdStyle, paddingLeft: 12 + indent * 20 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        {canExpand && (
                            <span style={{ fontSize: 10, color: 'var(--color-text-dim)', transition: 'transform 0.2s', transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)' }}>
                                {isLoading ? '\u23f3' : '\u25b6'}
                            </span>
                        )}
                        <span style={{ fontWeight: 600, fontSize: 13 }}>{row.group_key}</span>
                        {row.children_count > 0 && (
                            <span style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>({row.children_count})</span>
                        )}
                    </div>
                </td>
                {/* Capital */}
                <td style={{ ...tdStyle, textAlign: 'right', fontWeight: 600 }}>
                    {fmtMoney(row.capital)}
                </td>
                {/* Liquid % */}
                <td style={{ ...tdStyle, textAlign: 'center' }}>
                    <LiquidBar pct={row.liquid_pct} />
                </td>
                {/* Illiquid % */}
                <td style={{ ...tdStyle, textAlign: 'right' }}>
                    <span style={{ fontWeight: 600, color: illiquidColor(row.illiquid_pct) }}>
                        {fmtPct(row.illiquid_pct)}
                    </span>
                </td>
                {/* Frozen */}
                <td style={{ ...tdStyle, textAlign: 'right' }}>
                    <span style={{ color: row.frozen_amount > 0 ? '#E02424' : 'var(--color-text-dim)', fontWeight: 600 }}>
                        {row.frozen_amount > 0 ? fmtMoney(row.frozen_amount) : '\u2014'}
                    </span>
                </td>
                {/* ROI */}
                <td style={{ ...tdStyle, textAlign: 'right' }}>
                    <span style={{ fontWeight: 700, color: roiColor(row.roi_monthly) }}>
                        {formatNumber(row.roi_monthly, 1)}%
                    </span>
                </td>
                {/* Turnover */}
                <td style={{ ...tdStyle, textAlign: 'center' }}>
                    <span style={{ fontWeight: 600, color: turnoverColor(row.turnover_days) }}>
                        {Math.round(row.turnover_days)} дн
                    </span>
                </td>
                {/* Sales/day */}
                <td style={{ ...tdStyle, textAlign: 'right' }}>
                    {formatNumber(row.sales_per_day, 1)} шт
                </td>
                {/* Recommendation */}
                <td style={{ ...tdStyle, textAlign: 'center' }}>
                    {rec && <RecommendationBadge type={rec.type} label={rec.label} />}
                </td>
                {/* ROI -10% */}
                <td style={{ ...tdStyle, textAlign: 'right' }}>
                    {roiMinus10 != null ? (
                        <span style={{ fontSize: 12, color: roiMinus10 > row.roi_monthly ? '#10b981' : roiMinus10 < row.roi_monthly ? '#E02424' : 'var(--color-text-dim)' }}>
                            {roiMinus10 > row.roi_monthly ? '\u25b2' : roiMinus10 < row.roi_monthly ? '\u25bc' : ''} {formatNumber(roiMinus10, 1)}%
                        </span>
                    ) : (
                        <span style={{ color: 'var(--color-text-dim)' }}>\u2014</span>
                    )}
                </td>
            </tr>
            {/* Children rows */}
            {isExpanded && children && children.map((child, ci) => (
                <tr key={child.group_key} style={{ background: ci % 2 === 0 ? '#fafbfc' : '#f5f6f8', color: '#374151' }}>
                    <td style={{ ...tdStyle, paddingLeft: 12 + (indent + 1) * 20 }}>
                        <span style={{ fontSize: 12 }}>{child.vendor_code || child.group_key}</span>
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>{fmtMoney(child.capital)}</td>
                    <td style={{ ...tdStyle, textAlign: 'center' }}><LiquidBar pct={child.liquid_pct} /></td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                        <span style={{ color: illiquidColor(child.illiquid_pct) }}>{fmtPct(child.illiquid_pct)}</span>
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                        <span style={{ color: child.frozen_amount > 0 ? '#E02424' : 'var(--color-text-dim)' }}>
                            {child.frozen_amount > 0 ? fmtMoney(child.frozen_amount) : '\u2014'}
                        </span>
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                        <span style={{ fontWeight: 600, color: roiColor(child.roi_monthly) }}>{formatNumber(child.roi_monthly, 1)}%</span>
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'center' }}>
                        <span style={{ color: turnoverColor(child.turnover_days) }}>{Math.round(child.turnover_days)} дн</span>
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>{formatNumber(child.sales_per_day, 1)} шт</td>
                    <td style={{ ...tdStyle, textAlign: 'center' }}>
                        {child.recommendation && <RecommendationBadge type={child.recommendation.type} label={child.recommendation.label} />}
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                        {child.recommendation?.roi_at_minus_10 != null ? (
                            <span style={{ fontSize: 12, color: child.recommendation.roi_at_minus_10 > child.roi_monthly ? '#10b981' : '#E02424' }}>
                                {child.recommendation.roi_at_minus_10 > child.roi_monthly ? '\u25b2' : '\u25bc'} {formatNumber(child.recommendation.roi_at_minus_10, 1)}%
                            </span>
                        ) : '\u2014'}
                    </td>
                </tr>
            ))}
        </>
    );
}

const tdStyle: React.CSSProperties = {
    padding: '10px 12px',
    borderBottom: '1px solid #f3f4f6',
    fontSize: 13,
};
