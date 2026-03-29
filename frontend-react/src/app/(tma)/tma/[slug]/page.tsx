'use client';

import { useEffect, useState, useCallback } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { haptic } from '@/lib/telegram';

/**
 * TMA Dashboard — balance, income/expense, orders, top categories.
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
    if (abs >= 1_000_000) {
        return (n / 1_000_000).toFixed(1).replace('.0', '') + 'M';
    }
    if (abs >= 1_000) {
        return (n / 1_000).toFixed(1).replace('.0', '') + 'K';
    }
    return formatNumber(n, 0);
}

export default function TmaDashboardPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const [data, setData] = useState<DashboardData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [projectName, setProjectName] = useState('');

    const loadData = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const result = await api.request<DashboardData>('GET', '/api/v1/reports/dashboard_summary');
            setData(result);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        // Read project name from localStorage (set during auth)
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

    if (error) {
        return (
            <div className="tma-page">
                <div className="tma-empty">
                    <div className="tma-empty-icon">⚠️</div>
                    <div className="tma-empty-text">{error}</div>
                    <button
                        className="tma-btn tma-btn-primary"
                        style={{ marginTop: 16 }}
                        onClick={() => {
                            haptic('light');
                            loadData();
                        }}
                    >
                        Повторить
                    </button>
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
                        : 'Текущий месяц'
                    }
                </div>
            </div>

            {/* Balance */}
            <div className="tma-card">
                <div className="tma-card-title">Баланс</div>
                <div className="tma-big-number">
                    {formatNumber(data.balance_rub, 0)} ₽
                </div>
                {data.balance_cny > 0 && (
                    <div className="tma-big-label">
                        + {formatNumber(data.balance_cny, 0)} ¥
                    </div>
                )}
            </div>

            {/* Pulse link */}
            <div
                className="tma-pulse-link"
                onClick={() => { haptic('light'); router.push(`/tma/${slug}/pulse`); }}
            >
                <div>
                    <div className="tma-pulse-link-text">Пульс-монитор</div>
                    <div className="tma-pulse-link-sub">Бренды &middot; Категории &middot; Артикулы</div>
                </div>
                <span className="tma-pulse-link-arrow">→</span>
            </div>

            {/* Funnel link */}
            <div
                className="tma-pulse-link"
                style={{ background: 'linear-gradient(135deg, #0c4a6e, #0369a1)' }}
                onClick={() => { haptic('light'); router.push(`/tma/${slug}/funnel`); }}
            >
                <div>
                    <div className="tma-pulse-link-text">Воронка продаж</div>
                    <div className="tma-pulse-link-sub">Конверсии &middot; Реклама &middot; Товары</div>
                </div>
                <span className="tma-pulse-link-arrow">→</span>
            </div>

            {/* Stats Grid */}
            <div className="tma-stat-grid">
                <div className="tma-stat-cell">
                    <div className="tma-stat-cell-value tma-stat-positive">
                        +{compactNumber(data.month_income)}
                    </div>
                    <div className="tma-stat-cell-label">Доходы</div>
                </div>
                <div className="tma-stat-cell">
                    <div className="tma-stat-cell-value tma-stat-negative">
                        -{compactNumber(Math.abs(data.month_expense))}
                    </div>
                    <div className="tma-stat-cell-label">Расходы</div>
                </div>
                <div className="tma-stat-cell">
                    <div className={`tma-stat-cell-value ${profit >= 0 ? 'tma-stat-positive' : 'tma-stat-negative'}`}>
                        {compactNumber(profit)}
                    </div>
                    <div className="tma-stat-cell-label">Прибыль</div>
                </div>
                <div className="tma-stat-cell">
                    <div className="tma-stat-cell-value">
                        {data.orders_count}
                    </div>
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
                            <span className="tma-cat-value">
                                {compactNumber(Math.abs(cat.value))} ₽
                            </span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
