'use client';
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { haptic } from '@/lib/telegram';

type Period = '7d' | '30d' | 'month';

function getDateRange(period: Period): { from: string; to: string } {
    const now = new Date();
    const to = now.toISOString().slice(0, 10);

    if (period === 'month') {
        const from = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
        return { from, to };
    }

    const days = period === '7d' ? 7 : 30;
    const fromDate = new Date(now);
    fromDate.setDate(fromDate.getDate() - days);
    return { from: fromDate.toISOString().slice(0, 10), to };
}

function compactNumber(n: number): string {
    if (n == null) return '—';
    const abs = Math.abs(n);
    if (abs >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
    if (abs >= 1_000) return (n / 1_000).toFixed(1) + 'K';
    return formatNumber(n, 0);
}

export default function TmaDashboard() {
    const [period, setPeriod] = useState<Period>('7d');
    const [loading, setLoading] = useState(true);
    const [data, setData] = useState<any>(null);
    const [error, setError] = useState('');

    const loadData = useCallback(async () => {
        setError('');
        try {
            const { from, to } = getDateRange(period);
            const res = await api.getDashboardSummary(from, to);
            setData(res);
        } catch (e: any) {
            setError(e.message || 'Ошибка загрузки');
        }
        setLoading(false);
    }, [period]);

    useEffect(() => { setLoading(true); loadData(); }, [loadData]);

    function changePeriod(p: Period) {
        haptic('selection');
        setPeriod(p);
    }

    if (loading && !data) {
        return (
            <div className="tma-center" style={{ minHeight: '60vh' }}>
                <div className="tma-spinner" />
            </div>
        );
    }

    if (error && !data) {
        return (
            <div className="tma-error" style={{ paddingTop: '30vh' }}>
                <div className="tma-error-icon">😕</div>
                <div>{error}</div>
            </div>
        );
    }

    const projectName = typeof window !== 'undefined' ? localStorage.getItem('dds_project_name') || 'Проект' : 'Проект';

    return (
        <div className="tma-page">
            <div className="tma-page-title">{projectName}</div>

            {/* Period filter */}
            <div className="tma-filter">
                {([['7d', '7 дней'], ['30d', '30 дней'], ['month', 'Месяц']] as [Period, string][]).map(([key, label]) => (
                    <button
                        key={key}
                        className={`tma-filter-chip ${period === key ? 'active' : ''}`}
                        onClick={() => changePeriod(key)}
                    >
                        {label}
                    </button>
                ))}
            </div>

            {data && (
                <>
                    {/* Key metrics */}
                    <div className="tma-metrics">
                        <div className="tma-metric">
                            <span className="tma-metric-icon">💰</span>
                            <span className="tma-metric-value">{compactNumber(data.balance_rub)}</span>
                            <span className="tma-metric-label">Баланс, ₽</span>
                        </div>
                        <div className="tma-metric">
                            <span className="tma-metric-icon">💴</span>
                            <span className="tma-metric-value">{compactNumber(data.balance_cny)}</span>
                            <span className="tma-metric-label">Баланс, ¥</span>
                        </div>
                        <div className="tma-metric">
                            <span className="tma-metric-icon">📈</span>
                            <span className="tma-metric-value" style={{ color: '#34c759' }}>
                                +{compactNumber(data.month_income)}
                            </span>
                            <span className="tma-metric-label">Доходы</span>
                        </div>
                        <div className="tma-metric">
                            <span className="tma-metric-icon">📉</span>
                            <span className="tma-metric-value" style={{ color: '#ff3b30' }}>
                                -{compactNumber(data.month_expense)}
                            </span>
                            <span className="tma-metric-label">Расходы</span>
                        </div>
                    </div>

                    {/* Orders */}
                    {data.orders_count > 0 && (
                        <>
                            <div className="tma-section-title">Заказы</div>
                            <div className="tma-card">
                                <div className="tma-card-row">
                                    <span className="tma-card-label">Заказов</span>
                                    <span className="tma-card-value">{data.orders_count}</span>
                                </div>
                                <div className="tma-card-row">
                                    <span className="tma-card-label">На сумму (¥)</span>
                                    <span className="tma-card-value">{compactNumber(data.orders_total_cny)}</span>
                                </div>
                            </div>
                        </>
                    )}

                    {/* Debts */}
                    {(data.debt_rub > 0 || data.debt_cny > 0) && (
                        <>
                            <div className="tma-section-title">Задолженности</div>
                            <div className="tma-card">
                                {data.debt_rub > 0 && (
                                    <div className="tma-card-row">
                                        <span className="tma-card-label">Долг ₽</span>
                                        <span className="tma-card-value" style={{ color: '#ff3b30' }}>
                                            {compactNumber(data.debt_rub)}
                                        </span>
                                    </div>
                                )}
                                {data.debt_cny > 0 && (
                                    <div className="tma-card-row">
                                        <span className="tma-card-label">Долг ¥</span>
                                        <span className="tma-card-value" style={{ color: '#ff3b30' }}>
                                            {compactNumber(data.debt_cny)}
                                        </span>
                                    </div>
                                )}
                            </div>
                        </>
                    )}

                    {/* Top expense categories */}
                    {data.expense_by_category?.length > 0 && (
                        <>
                            <div className="tma-section-title">Расходы по категориям</div>
                            <div className="tma-card">
                                {data.expense_by_category.slice(0, 5).map((cat: any, i: number) => (
                                    <div className="tma-card-row" key={i}>
                                        <span className="tma-card-label">{cat.name}</span>
                                        <span className="tma-card-value">{compactNumber(cat.value)} ₽</span>
                                    </div>
                                ))}
                            </div>
                        </>
                    )}

                    {/* Stats */}
                    <div className="tma-section-title">Статистика</div>
                    <div className="tma-card">
                        <div className="tma-card-row">
                            <span className="tma-card-label">Неразнесённых</span>
                            <span className="tma-card-value">{data.inbox_count || 0}</span>
                        </div>
                        <div className="tma-card-row">
                            <span className="tma-card-label">Счетов</span>
                            <span className="tma-card-value">{data.accounts_count || 0}</span>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}
