'use client';

import { useEffect, useState, useCallback } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { haptic } from '@/lib/telegram';
import type {
    AnomaliesResponse, AnomalyItem,
    PlanFactBrandRow,
} from '@/types/api';

/**
 * TMA Dashboard — balance, plan-fact progress, anomalies, stats.
 */

interface DashboardData {
    balance_rub: number;
    balance_cny: number;
    month_income: number;
    month_expense: number;
    orders_count: number;
    orders_total_cny: number;
    expense_by_category: Array<{ name: string; value: number }>;
    date_from: string;
    date_to: string;
}

function compactNumber(n: number): string {
    const abs = Math.abs(n);
    if (abs >= 1_000_000) return (n / 1_000_000).toFixed(1).replace('.0', '') + 'M';
    if (abs >= 1_000) return (n / 1_000).toFixed(1).replace('.0', '') + 'K';
    return formatNumber(n, 0);
}

// ─── Anomaly type labels & styles ────────────────────────────────────────────

const ANOMALY_STYLES: Record<string, { icon: string; color: string; bg: string }> = {
    hit_loss:    { icon: '📉', color: '#991B1B', bg: '#FEE2E2' },
    hit_oos:     { icon: '📦', color: '#92400E', bg: '#FEF3C7' },
    toxic_ads:   { icon: '🔥', color: '#9A3412', bg: '#FED7AA' },
    dead_stock:  { icon: '🧊', color: '#1E40AF', bg: '#DBEAFE' },
    low_buyout:  { icon: '↩️', color: '#374151', bg: '#F3F4F6' },
};

// ─── Plan-Fact Card ──────────────────────────────────────────────────────────

function PlanFactSection({ brands }: { brands: PlanFactBrandRow[] }) {
    if (brands.length === 0) return null;

    return (
        <div className="tma-card">
            <div className="tma-card-title">План-факт</div>
            {brands.map((b) => {
                const pct = b.pct ?? 0;
                const color = pct >= 100 ? '#10b981' : pct >= 80 ? '#d97706' : '#e02424';
                const barWidth = Math.min(pct, 120);

                return (
                    <div key={b.brand} className="tma-pf-row">
                        <div className="tma-pf-header">
                            <span className="tma-pf-brand">{b.brand}</span>
                            <span className="tma-pf-pct" style={{ color }}>
                                {Math.round(pct)}%
                            </span>
                        </div>
                        <div className="tma-pf-bar-bg">
                            <div
                                className="tma-pf-bar-fill"
                                style={{ width: `${Math.min(barWidth, 100)}%`, backgroundColor: color }}
                            />
                            {/* Plan marker at 100% */}
                            <div className="tma-pf-bar-marker" />
                        </div>
                        <div className="tma-pf-details">
                            <span>Факт: {compactNumber(b.fact_mtd)} ₽</span>
                            <span>План: {compactNumber(b.plan_adjusted)} ₽</span>
                            {b.forecast > 0 && (
                                <span style={{ color: b.forecast >= b.plan_adjusted ? '#10b981' : '#d97706' }}>
                                    Прогноз: {compactNumber(b.forecast)} ₽
                                </span>
                            )}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}

// ─── Anomalies Section ───────────────────────────────────────────────────────

function AnomaliesSection({ data }: { data: AnomaliesResponse }) {
    const [expanded, setExpanded] = useState(false);

    if (data.anomalies.length === 0) {
        return (
            <div className="tma-card">
                <div className="tma-card-title">Аномалии</div>
                <div className="tma-anomaly-ok">
                    <span className="tma-anomaly-ok-icon">✅</span>
                    <span>Проблем не обнаружено</span>
                </div>
            </div>
        );
    }

    const critical = data.anomalies.filter(a => a.severity === 'critical');
    const warnings = data.anomalies.filter(a => a.severity === 'warning');
    const shown = expanded ? data.anomalies : data.anomalies.slice(0, 3);

    return (
        <div className="tma-card">
            <div className="tma-card-title">
                Аномалии
                {data.summary.total_loss > 0 && (
                    <span className="tma-anomaly-loss">
                        −{compactNumber(data.summary.total_loss)} ₽
                    </span>
                )}
            </div>

            {/* Summary strip */}
            <div className="tma-anomaly-summary">
                {critical.length > 0 && (
                    <span className="tma-anomaly-badge tma-anomaly-badge-crit">
                        {critical.length} крит
                    </span>
                )}
                {warnings.length > 0 && (
                    <span className="tma-anomaly-badge tma-anomaly-badge-warn">
                        {warnings.length} внимание
                    </span>
                )}
                {data.summary.oos_risk_count > 0 && (
                    <span className="tma-anomaly-badge tma-anomaly-badge-oos">
                        {data.summary.oos_risk_count} дефицит
                    </span>
                )}
            </div>

            {/* Anomaly list */}
            {shown.map((a, i) => (
                <AnomalyRow key={`${a.nm_id}-${a.anomaly_type}-${i}`} item={a} />
            ))}

            {data.anomalies.length > 3 && (
                <button
                    className="tma-anomaly-toggle"
                    onClick={() => { haptic('light'); setExpanded(!expanded); }}
                >
                    {expanded ? 'Свернуть' : `Ещё ${data.anomalies.length - 3} проблем`}
                </button>
            )}
        </div>
    );
}

function AnomalyRow({ item }: { item: AnomalyItem }) {
    const [open, setOpen] = useState(false);
    const style = ANOMALY_STYLES[item.anomaly_type] || ANOMALY_STYLES.hit_loss;

    return (
        <div
            className={`tma-anomaly-row ${open ? 'tma-anomaly-row-open' : ''}`}
            onClick={() => { haptic('light'); setOpen(!open); }}
        >
            <div className="tma-anomaly-row-header">
                <span className="tma-anomaly-icon" style={{ backgroundColor: style.bg, color: style.color }}>
                    {style.icon}
                </span>
                <div className="tma-anomaly-row-info">
                    <div className="tma-anomaly-row-title">{item.title}</div>
                    <div className="tma-anomaly-row-sku">
                        {item.vendor_code || `#${item.nm_id}`}
                        {item.subject ? ` · ${item.subject}` : ''}
                    </div>
                </div>
                {item.loss_amount != null && item.loss_amount > 0 && (
                    <span className="tma-anomaly-row-loss">−{compactNumber(item.loss_amount)} ₽</span>
                )}
            </div>

            {open && (
                <div className="tma-anomaly-row-details">
                    <div className="tma-anomaly-row-desc">{item.description}</div>
                    <div className="tma-anomaly-row-action">
                        <b>Действие:</b> {item.action}
                    </div>
                    {item.metrics && (
                        <div className="tma-anomaly-metrics">
                            {item.metrics.margin != null && <span>Маржа: {formatNumber(item.metrics.margin, 1)}%</span>}
                            {item.metrics.drr != null && <span>ДРР: {formatNumber(item.metrics.drr, 1)}%</span>}
                            {item.metrics.days_left != null && <span>Запас: {Math.round(item.metrics.days_left)} дн</span>}
                            {item.metrics.buyout_pct != null && <span>Выкуп: {formatNumber(item.metrics.buyout_pct, 0)}%</span>}
                            {item.metrics.turnover_days != null && <span>Оборач: {Math.round(item.metrics.turnover_days)} дн</span>}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export default function TmaDashboardPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const [data, setData] = useState<DashboardData | null>(null);
    const [anomalies, setAnomalies] = useState<AnomaliesResponse | null>(null);
    const [planFact, setPlanFact] = useState<PlanFactBrandRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [projectName, setProjectName] = useState('');

    const loadData = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const now = new Date();
            const year = now.getFullYear();
            const month = now.getMonth() + 1;

            const [dashboard, anomalyData, pfData] = await Promise.allSettled([
                api.request<DashboardData>('GET', '/api/v1/reports/dashboard_summary'),
                api.getAnomalies({ period_days: 7 }),
                api.getPlanFactBrands(year, month),
            ]);

            if (dashboard.status === 'fulfilled') setData(dashboard.value);
            else setError('Ошибка загрузки дашборда');

            if (anomalyData.status === 'fulfilled') setAnomalies(anomalyData.value);
            if (pfData.status === 'fulfilled') setPlanFact(pfData.value);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        const name = localStorage.getItem('dds_project_name');
        if (name) setProjectName(name);
        loadData();
    }, [loadData]);

    if (loading) {
        return (
            <div className="tma-loading">
                <div className="tma-spinner" />
                <div className="tma-loading-text">Загрузка...</div>
            </div>
        );
    }

    if (error && !data) {
        return (
            <div className="tma-page">
                <div className="tma-empty">
                    <div className="tma-empty-icon">⚠️</div>
                    <div className="tma-empty-text">{error}</div>
                    <button className="tma-btn tma-btn-primary" style={{ marginTop: 16 }}
                        onClick={() => { haptic('light'); loadData(); }}>Повторить</button>
                </div>
            </div>
        );
    }

    if (!data) {
        return (
            <div className="tma-page">
                <div className="tma-empty">
                    <div className="tma-empty-icon">📭</div>
                    <div className="tma-empty-text">Нет данных</div>
                </div>
            </div>
        );
    }

    const profit = data.month_income - Math.abs(data.month_expense);

    return (
        <div className="tma-page">
            <div className="tma-page-header">
                <div className="tma-page-title">{projectName || slug}</div>
                <div className="tma-page-subtitle">
                    {data.date_from && data.date_to
                        ? `${data.date_from} — ${data.date_to}`
                        : 'Текущий месяц'}
                </div>
            </div>

            {/* Balance */}
            <div className="tma-card">
                <div className="tma-card-title">Баланс</div>
                <div className="tma-big-number">{formatNumber(data.balance_rub, 0)} ₽</div>
                {data.balance_cny > 0 && (
                    <div className="tma-big-label">+ {formatNumber(data.balance_cny, 0)} ¥</div>
                )}
            </div>

            {/* Plan-Fact */}
            <PlanFactSection brands={planFact} />

            {/* Anomalies */}
            {anomalies && <AnomaliesSection data={anomalies} />}

            {/* Quick links */}
            <div
                className="tma-pulse-link"
                onClick={() => { haptic('light'); router.push(`/tma/${slug}/pulse`); }}
            >
                <div>
                    <div className="tma-pulse-link-text">Пульс-монитор</div>
                    <div className="tma-pulse-link-sub">Бренды · Категории · Артикулы</div>
                </div>
                <span className="tma-pulse-link-arrow">→</span>
            </div>
            <div
                className="tma-pulse-link"
                style={{ background: 'linear-gradient(135deg, #0c4a6e, #0369a1)' }}
                onClick={() => { haptic('light'); router.push(`/tma/${slug}/funnel`); }}
            >
                <div>
                    <div className="tma-pulse-link-text">Воронка продаж</div>
                    <div className="tma-pulse-link-sub">Конверсии · Реклама · Товары</div>
                </div>
                <span className="tma-pulse-link-arrow">→</span>
            </div>

            {/* Stats Grid */}
            <div className="tma-stat-grid">
                <div className="tma-stat-cell">
                    <div className="tma-stat-cell-value tma-stat-positive">+{compactNumber(data.month_income)}</div>
                    <div className="tma-stat-cell-label">Доходы</div>
                </div>
                <div className="tma-stat-cell">
                    <div className="tma-stat-cell-value tma-stat-negative">-{compactNumber(Math.abs(data.month_expense))}</div>
                    <div className="tma-stat-cell-label">Расходы</div>
                </div>
                <div className="tma-stat-cell">
                    <div className={`tma-stat-cell-value ${profit >= 0 ? 'tma-stat-positive' : 'tma-stat-negative'}`}>
                        {compactNumber(profit)}
                    </div>
                    <div className="tma-stat-cell-label">Прибыль</div>
                </div>
                <div className="tma-stat-cell">
                    <div className="tma-stat-cell-value">{data.orders_count}</div>
                    <div className="tma-stat-cell-label">Заказы</div>
                </div>
            </div>

            {/* Top expense categories */}
            {data.expense_by_category && data.expense_by_category.length > 0 && (
                <div className="tma-card">
                    <div className="tma-card-title">Расходы по категориям</div>
                    {data.expense_by_category.slice(0, 5).map((cat, i) => (
                        <div key={i} className="tma-cat-row">
                            <span className="tma-cat-name">{cat.name}</span>
                            <span className="tma-cat-value">{compactNumber(Math.abs(cat.value))} ₽</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
