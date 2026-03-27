'use client';

import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { AnomaliesResponse, AnomalyItem } from '@/types/api';

interface AnomaliesTabProps {
    trendDays: number;
    brand: string;
    onTrendDaysChange: (days: number) => void;
    onBrandChange: (brand: string) => void;
    brands: string[];
}

function SummaryCard({ label, value, subtitle, accentColor, bgColor }: {
    label: string;
    value: string;
    subtitle: string;
    accentColor: string;
    bgColor: string;
}) {
    return (
        <div
            className="glass-card"
            style={{
                padding: '20px 24px',
                borderLeft: `4px solid ${accentColor}`,
                background: bgColor,
                flex: '1 1 220px',
                minWidth: 200,
            }}
        >
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4, fontWeight: 500 }}>
                {label}
            </div>
            <div style={{ fontSize: 26, fontWeight: 700, color: accentColor, marginBottom: 4 }}>
                {value}
            </div>
            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                {subtitle}
            </div>
        </div>
    );
}

function MetricPill({ label, value, color, wasValue }: {
    label: string;
    value: string;
    color?: string;
    wasValue?: string;
}) {
    return (
        <div style={{ textAlign: 'center', minWidth: 80 }}>
            <div style={{ fontSize: 10, color: 'var(--color-text-dim)', marginBottom: 2 }}>{label}</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: color || 'var(--color-text)' }}>{value}</div>
            {wasValue && (
                <div style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>
                    было {wasValue}
                </div>
            )}
        </div>
    );
}

function AnomalyCard({ item, index }: { item: AnomalyItem; index: number }) {
    const isCritical = item.severity === 'critical';
    const borderColor = isCritical ? '#E02424' : '#D97706';
    const bgColor = isCritical ? 'rgba(254, 202, 202, 0.15)' : 'rgba(253, 246, 178, 0.15)';
    const badgeBg = isCritical ? 'rgba(224, 36, 36, 0.1)' : 'rgba(217, 119, 6, 0.1)';
    const badgeColor = isCritical ? '#E02424' : '#D97706';
    const badgeIcon = isCritical ? '\ud83d\udd34' : '\ud83d\udfe1';

    const m = item.metrics;

    const lossDisplay = item.loss_amount != null
        ? (item.loss_amount < 0
            ? `-${formatNumber(Math.abs(item.loss_amount), 0)} \u20bd`
            : `${formatNumber(item.loss_amount, 0)} \u20bd`)
        : null;

    const isFrozen = item.anomaly_type === 'dead_stock' || item.anomaly_type === 'frozen_capital';

    return (
        <div
            className="glass-card"
            style={{
                padding: 0,
                borderLeft: `4px solid ${borderColor}`,
                background: bgColor,
                marginBottom: 12,
                overflow: 'hidden',
            }}
        >
            <div style={{
                display: 'flex',
                alignItems: 'stretch',
                flexWrap: 'wrap',
                gap: 0,
            }}>
                {/* Row number */}
                <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    padding: '16px 12px',
                    minWidth: 40,
                    color: 'var(--color-text-dim)',
                    fontSize: 13,
                    fontWeight: 600,
                }}>
                    #{index + 1}
                </div>

                {/* Article info */}
                <div style={{
                    padding: '14px 16px',
                    minWidth: 160,
                    maxWidth: 200,
                    borderRight: '1px solid var(--color-border)',
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'center',
                }}>
                    <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--color-text)' }}>
                        {item.vendor_code}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 2 }}>
                        {item.brand} &middot; {item.subject}
                    </div>
                </div>

                {/* Problem */}
                <div style={{
                    padding: '14px 16px',
                    flex: '1 1 250px',
                    borderRight: '1px solid var(--color-border)',
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'center',
                }}>
                    <span style={{
                        display: 'inline-block',
                        padding: '3px 10px',
                        borderRadius: 6,
                        fontSize: 12,
                        fontWeight: 600,
                        background: badgeBg,
                        color: badgeColor,
                        marginBottom: 4,
                        width: 'fit-content',
                    }}>
                        {badgeIcon} {item.title}
                    </span>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', lineHeight: 1.4 }}>
                        {item.description}
                    </div>
                </div>

                {/* Loss / Frozen amount */}
                <div style={{
                    padding: '14px 16px',
                    minWidth: 130,
                    borderRight: '1px solid var(--color-border)',
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'center',
                    alignItems: 'flex-end',
                }}>
                    <div style={{ fontSize: 10, color: 'var(--color-text-dim)', marginBottom: 2 }}>
                        {isFrozen ? 'ЗАМОРОЖЕНО' : 'УБЫТОК'}
                    </div>
                    {lossDisplay ? (
                        <div style={{
                            fontSize: 16,
                            fontWeight: 700,
                            color: isFrozen ? '#3B82F6' : '#E02424',
                        }}>
                            {isFrozen ? '\ud83e\uddf2 ' : ''}{lossDisplay}
                        </div>
                    ) : (
                        <div style={{ fontSize: 14, color: 'var(--color-text-dim)' }}>&mdash;</div>
                    )}
                </div>

                {/* Metrics */}
                <div style={{
                    padding: '14px 16px',
                    display: 'flex',
                    gap: 16,
                    alignItems: 'center',
                    flexWrap: 'wrap',
                    flex: '0 1 auto',
                }}>
                    {m.margin != null && (
                        <MetricPill
                            label="МАРЖА %"
                            value={`${formatNumber(m.margin, 1)}%`}
                            color={m.margin < 0 ? '#E02424' : m.margin < 10 ? '#D97706' : '#10b981'}
                            wasValue={m.was_margin != null ? `${formatNumber(m.was_margin, 1)}%` : undefined}
                        />
                    )}
                    {m.drr != null && (
                        <MetricPill
                            label="ДРР %"
                            value={`${formatNumber(m.drr, 1)}%`}
                            color={m.drr > 30 ? '#E02424' : m.drr > 15 ? '#D97706' : '#10b981'}
                        />
                    )}
                    {m.turnover_days != null && (
                        <MetricPill
                            label="ОБОРАЧ."
                            value={`${Math.round(m.turnover_days)} дн`}
                            color={m.turnover_days > 60 ? '#E02424' : m.turnover_days > 40 ? '#D97706' : '#10b981'}
                        />
                    )}
                    {m.orders_sum != null && (
                        <MetricPill
                            label="ЗАКАЗЫ (7Д)"
                            value={`${formatNumber(m.orders_sum, 0)} \u20bd`}
                        />
                    )}
                    {m.days_left != null && (
                        <MetricPill
                            label="ДН. ОСТАТКА"
                            value={`${Math.round(m.days_left)} дн`}
                            color={m.days_left < 7 ? '#E02424' : m.days_left < 14 ? '#D97706' : '#10b981'}
                        />
                    )}
                    {m.stocks_wb != null && (
                        <MetricPill
                            label="ОСТАТОК WB"
                            value={`${formatNumber(m.stocks_wb, 0)} шт`}
                        />
                    )}
                    {m.buyout_pct != null && (
                        <MetricPill
                            label="ВЫКУП %"
                            value={`${formatNumber(m.buyout_pct, 1)}%`}
                            color={m.buyout_pct < 30 ? '#E02424' : m.buyout_pct < 50 ? '#D97706' : '#10b981'}
                        />
                    )}
                </div>
            </div>
        </div>
    );
}

function SkeletonCards() {
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {[1, 2, 3, 4, 5].map(i => (
                <div
                    key={i}
                    className="glass-card"
                    style={{
                        height: 80,
                        background: 'var(--color-bg-card)',
                        borderLeft: '4px solid var(--color-border)',
                        animation: 'pulse 1.5s ease-in-out infinite',
                    }}
                />
            ))}
            <style>{`
                @keyframes pulse {
                    0%, 100% { opacity: 1; }
                    50% { opacity: 0.5; }
                }
            `}</style>
        </div>
    );
}

export default function AnomaliesTab({ trendDays, brand, onTrendDaysChange, onBrandChange, brands }: AnomaliesTabProps) {
    const [data, setData] = useState<AnomaliesResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [includeRfStocks, setIncludeRfStocks] = useState(false);
    const [severityFilter, setSeverityFilter] = useState<'all' | 'critical' | 'warning'>('all');

    const loadData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.getAnomalies({
                period_days: trendDays,
                brand: brand || undefined,
                include_rf_stocks: includeRfStocks,
            });
            setData(res);
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : 'Failed to load anomalies';
            setError(message);
            console.error('Anomalies error:', err);
        } finally {
            setLoading(false);
        }
    }, [trendDays, brand, includeRfStocks]);

    useEffect(() => { loadData(); }, [loadData]);

    const anomalies = data?.anomalies || [];
    const criticalCount = anomalies.filter(a => a.severity === 'critical').length;
    const warningCount = anomalies.filter(a => a.severity === 'warning').length;

    const filteredAnomalies = severityFilter === 'all'
        ? anomalies
        : anomalies.filter(a => a.severity === severityFilter);

    // Count of frozen items for subtitle
    const frozenCount = anomalies.filter(a =>
        a.anomaly_type === 'dead_stock' || a.anomaly_type === 'frozen_capital'
    ).length;

    return (
        <div>
            {/* Summary cards */}
            {!loading && !error && data && (
                <div style={{ display: 'flex', gap: 16, marginBottom: 20, flexWrap: 'wrap' }}>
                    <SummaryCard
                        label="Убыток от аномалий"
                        value={`${formatNumber(data.summary.total_loss, 0)} \u20bd`}
                        subtitle="Токсичная реклама + Падение маржи"
                        accentColor="#E02424"
                        bgColor="rgba(254, 202, 202, 0.12)"
                    />
                    <SummaryCard
                        label="Риск Out-of-Stock"
                        value={`${data.summary.oos_risk_count} ТОПА`}
                        subtitle="Остатков на < 7 дней у хитов"
                        accentColor="#D97706"
                        bgColor="rgba(253, 230, 138, 0.12)"
                    />
                    <SummaryCard
                        label="Омертвление капитала"
                        value={`${formatNumber(data.summary.frozen_capital, 0)} \u20bd`}
                        subtitle={`Заморожено в ${frozenCount} неликвидных товарах (оборач. > 60 дн)`}
                        accentColor="#6B7280"
                        bgColor="rgba(107, 114, 128, 0.06)"
                    />
                    <SummaryCard
                        label="Здоровые товары"
                        value={`${data.summary.healthy_count} шт`}
                        subtitle="Все метрики в норме"
                        accentColor="#10b981"
                        bgColor="rgba(16, 185, 129, 0.06)"
                    />
                </div>
            )}

            {/* Filter bar */}
            <div className="glass-card" style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '10px 16px', marginBottom: 16, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Период:</span>
                {[7, 14, 30].map(d => (
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
                        {d} дней
                    </button>
                ))}

                <span style={{ width: 1, height: 24, background: 'var(--color-border)', margin: '0 4px' }} />

                <select value={brand} onChange={e => onBrandChange(e.target.value)}
                    style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '5px 8px', color: 'var(--color-text)', fontSize: 13 }}>
                    <option value="">Все бренды</option>
                    {brands.map(b => <option key={b} value={b}>{b}</option>)}
                </select>

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

                {/* Severity filter buttons */}
                <button
                    onClick={() => setSeverityFilter('all')}
                    className={`btn btn-sm ${severityFilter === 'all' ? 'btn-primary' : 'btn-secondary'}`}
                    style={{ padding: '4px 12px', fontSize: 12 }}
                >
                    Все аномалии
                </button>
                <button
                    onClick={() => setSeverityFilter('critical')}
                    className={`btn btn-sm ${severityFilter === 'critical' ? 'btn-danger' : 'btn-secondary'}`}
                    style={{ padding: '4px 12px', fontSize: 12 }}
                >
                    Критические ({criticalCount})
                </button>
                <button
                    onClick={() => setSeverityFilter('warning')}
                    className={`btn btn-sm ${severityFilter === 'warning' ? 'btn-success' : 'btn-secondary'}`}
                    style={{
                        padding: '4px 12px',
                        fontSize: 12,
                        ...(severityFilter === 'warning' ? {
                            background: 'rgba(217, 119, 6, 0.1)',
                            color: '#D97706',
                            border: '1px solid rgba(217, 119, 6, 0.2)',
                        } : {}),
                    }}
                >
                    Предупреждения ({warningCount})
                </button>
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

            {!loading && !error && filteredAnomalies.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 60 }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>&#x2705;</div>
                    <div style={{ fontSize: 18, fontWeight: 600, color: '#10b981', marginBottom: 4 }}>
                        Все товары в норме!
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Аномалий не обнаружено.
                    </div>
                </div>
            )}

            {!loading && !error && filteredAnomalies.length > 0 && (
                <div>
                    {filteredAnomalies.map((item, i) => (
                        <AnomalyCard key={`${item.nm_id}-${item.anomaly_type}`} item={item} index={i} />
                    ))}
                </div>
            )}

            {!loading && !error && data && (
                <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-dim)' }}>
                    Всего товаров: {data.total_products} &middot; Период: {data.period_days} дней &middot;
                    Аномалий: {anomalies.length} (критических: {criticalCount}, предупреждений: {warningCount})
                </div>
            )}
        </div>
    );
}
