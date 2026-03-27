'use client';
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { haptic } from '@/lib/telegram';

interface OpiuRow {
    key: string;
    label: string;
    level: number;
    bold: boolean;
    total: number;
    total_pct: number | null;
}

type Period = 'month' | 'quarter' | 'year';

function getDateRange(period: Period): { from: string; to: string } {
    const now = new Date();
    const to = now.toISOString().slice(0, 10);
    const y = now.getFullYear();
    const m = now.getMonth() + 1;

    if (period === 'month') {
        return { from: `${y}-${String(m).padStart(2, '0')}-01`, to };
    }
    if (period === 'quarter') {
        const qm = Math.floor((m - 1) / 3) * 3 + 1;
        return { from: `${y}-${String(qm).padStart(2, '0')}-01`, to };
    }
    return { from: `${y}-01-01`, to };
}

function compactMoney(n: number): string {
    if (n == null) return '—';
    const abs = Math.abs(n);
    if (abs >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
    if (abs >= 1_000) return (n / 1_000).toFixed(1) + 'K';
    return formatNumber(n, 0);
}

// Which rows to show (hide deeply nested detail)
const VISIBLE_KEYS = new Set([
    'revenue', 'mp_discount', 'net_sales',
    'direct_costs', 'cost_of_sales', 'logistics', 'commission',
    'storage', 'advertising', 'deductions', 'penalties', 'acceptance',
    'gross_profit', 'compensation', 'taxes', 'operating_expenses',
    'operating_profit', 'financial', 'net_profit',
]);

export default function TmaPnlPage() {
    const [period, setPeriod] = useState<Period>('month');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [rows, setRows] = useState<OpiuRow[]>([]);
    const [computing, setComputing] = useState(false);

    const loadData = useCallback(async () => {
        setError('');
        try {
            const { from, to } = getDateRange(period);
            const res = await api.getOpiu(from, to);
            if (res.computing) {
                setComputing(true);
                setLoading(false);
                setTimeout(() => loadData(), 3000);
                return;
            }
            setComputing(false);
            setRows(res.rows || []);
        } catch (e: any) {
            setError(e.message || 'Ошибка');
        }
        setLoading(false);
    }, [period]);

    useEffect(() => { setLoading(true); loadData(); }, [loadData]);

    function changePeriod(p: Period) {
        haptic('selection');
        setPeriod(p);
    }

    const visibleRows = rows.filter(r => VISIBLE_KEYS.has(r.key));

    return (
        <div className="tma-page">
            <div className="tma-page-title">Отчет о прибылях и убытках</div>

            <div className="tma-filter">
                {([['month', 'Месяц'], ['quarter', 'Квартал'], ['year', 'Год']] as [Period, string][]).map(([key, label]) => (
                    <button
                        key={key}
                        className={`tma-filter-chip ${period === key ? 'active' : ''}`}
                        onClick={() => changePeriod(key)}
                    >
                        {label}
                    </button>
                ))}
            </div>

            {loading && !rows.length && (
                <div className="tma-center" style={{ minHeight: '40vh' }}>
                    <div className="tma-spinner" />
                    {computing && <div style={{ marginTop: 12, fontSize: 13, color: 'var(--tg-theme-hint-color, #999)' }}>Расчет данных...</div>}
                </div>
            )}

            {error && !rows.length && (
                <div className="tma-error">
                    <div className="tma-error-icon">😕</div>
                    <div>{error}</div>
                </div>
            )}

            {visibleRows.length > 0 && (
                <div className="tma-card">
                    {visibleRows.map((row) => {
                        const isTotal = row.key === 'net_profit' || row.key === 'gross_profit' || row.key === 'operating_profit';
                        const isSection = row.level === 0 && row.bold;
                        const isNegative = row.total < 0;
                        const isProfit = row.key.includes('profit') || row.key === 'net_sales' || row.key === 'revenue';

                        return (
                            <div
                                key={row.key}
                                className={`tma-opiu-row ${isTotal ? 'total' : ''} ${row.bold ? 'bold' : ''}`}
                                style={{ paddingLeft: row.level > 1 ? (row.level - 1) * 12 : 0 }}
                            >
                                <span className="tma-opiu-label">
                                    {row.label}
                                </span>
                                <span
                                    className="tma-opiu-value"
                                    style={{
                                        color: isNegative ? '#ff3b30' :
                                               isProfit && row.total > 0 ? '#34c759' :
                                               undefined
                                    }}
                                >
                                    {compactMoney(row.total)} ₽
                                </span>
                                <span className="tma-opiu-pct">
                                    {row.total_pct != null ? `${row.total_pct.toFixed(1)}%` : ''}
                                </span>
                            </div>
                        );
                    })}
                </div>
            )}

            {!loading && !error && visibleRows.length === 0 && (
                <div className="tma-error">
                    <div className="tma-error-icon">📊</div>
                    <div>Нет данных за выбранный период</div>
                </div>
            )}
        </div>
    );
}
