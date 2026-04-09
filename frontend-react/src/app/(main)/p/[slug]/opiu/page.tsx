'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';

interface OpiuRow {
    key: string;
    label: string;
    level: number;
    bold: boolean;
    expandable: boolean;
    total: number;
    total_pct: number | null;
    monthly: Record<string, number>;
    monthly_pct: Record<string, number | null>;
}

const MONTH_NAMES: Record<string, string> = {
    '01': 'Январь', '02': 'Февраль', '03': 'Март', '04': 'Апрель',
    '05': 'Май', '06': 'Июнь', '07': 'Июль', '08': 'Август',
    '09': 'Сентябрь', '10': 'Октябрь', '11': 'Ноябрь', '12': 'Декабрь',
};

function monthLabel(mk: string): string {
    const [y, m] = mk.split('-');
    return `${MONTH_NAMES[m] || m} ${y}`;
}

// Rows that are children of expandable groups
const GROUP_CHILDREN: Record<string, string[]> = {
    direct_costs: ['cost_of_sales', 'cost_price', 'logistics', 'commission', 'penalties', 'storage', 'advertising', 'deductions', 'acceptance'],
    cost_of_sales: ['cost_price'],
    compensation: ['comp_bonus', 'comp_voluntary'],
    taxes: [],
};

export default function OpiuPage() {
    const { slug } = useParams();
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [data, setData] = useState<any>(null);
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');
    const [brand, setBrand] = useState('');
    const [article, setArticle] = useState('');
    const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
    const [computing, setComputing] = useState(false);
    const retryTimeoutRef = useRef<NodeJS.Timeout>();

    // Default: Jan 1 of current year → today
    useEffect(() => {
        const now = new Date();
        const y = now.getFullYear();
        setDateFrom(`${y}-01-01`);
        setDateTo(now.toISOString().slice(0, 10));
    }, []);

    const loadData = useCallback(async () => {
        if (!dateFrom || !dateTo) return;
        if (!data) setLoading(true);
        setError('');
        try {
            const res = await api.getOpiu(dateFrom, dateTo, brand || undefined, article || undefined);
            if (res.computing) {
                // Backend is computing in background — retry in 3s
                setComputing(true);
                setLoading(false);
                retryTimeoutRef.current = setTimeout(() => loadData(), 3000);
                return;
            }
            setComputing(false);
            setData(res);
        } catch (e: any) {
            setComputing(false);
            setError(e.message || 'Ошибка загрузки');
        }
        setLoading(false);
    }, [dateFrom, dateTo, brand, article]);

    useEffect(() => {
        loadData();
        return () => { clearTimeout(retryTimeoutRef.current); };
    }, [loadData]);

    const toggleGroup = (key: string) => {
        setCollapsed(prev => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
        });
    };

    // Determine which rows to hide based on collapsed state
    const isRowHidden = (row: OpiuRow): boolean => {
        for (const [parent, children] of Object.entries(GROUP_CHILDREN)) {
            if (children.includes(row.key) && collapsed.has(parent)) {
                return true;
            }
        }
        return false;
    };

    const handleExport = () => {
        if (!data) return;
        const rows = data.rows as OpiuRow[];
        const months = data.months as string[];

        const header = ['Статья', `${data.year_label}`, '%'];
        months.forEach((m: string) => { header.push(monthLabel(m)); header.push('%'); });

        const csvRows = [header.join('\t')];
        rows.forEach((r: OpiuRow) => {
            const indent = '  '.repeat(r.level);
            const line = [
                `${indent}${r.label}`,
                r.total.toString(),
                r.total_pct != null ? r.total_pct.toString() : '',
            ];
            months.forEach((m: string) => {
                line.push((r.monthly[m] || 0).toString());
                line.push(r.monthly_pct[m] != null ? (r.monthly_pct[m] as number).toString() : '');
            });
            csvRows.push(line.join('\t'));
        });

        const blob = new Blob([csvRows.join('\n')], { type: 'text/tab-separated-values' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `opiu_${dateFrom}_${dateTo}.tsv`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const months: string[] = data?.months || [];
    const rows: OpiuRow[] = data?.rows || [];
    const brands: string[] = data?.brands || [];

    return (
        <div style={{ padding: '20px 24px' }}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
                <div>
                    <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📊 ОПИУ</h1>
                    <p style={{ fontSize: 13, color: 'var(--color-text-dim)', margin: '4px 0 0' }}>
                        Отчёт о прибылях и убытках — помесячная разбивка
                    </p>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={handleExport} disabled={!data}>
                    📥 Excel
                </button>
            </div>

            {/* Filters */}
            <div className="glass-card" style={{ padding: '12px 16px', marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
                        style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' }} />
                    <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                    <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
                        style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' }} />
                </div>

                {brands.length > 0 && (
                    <select value={brand} onChange={e => setBrand(e.target.value)}
                        style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)', minWidth: 130 }}>
                        <option value="">Все бренды</option>
                        {brands.map((b: string) => <option key={b} value={b}>{b}</option>)}
                    </select>
                )}

                <input
                    type="text"
                    placeholder="Артикул..."
                    value={article}
                    onChange={e => setArticle(e.target.value)}
                    style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)', width: 150 }}
                />
            </div>

            {/* Loading / Error */}
            {loading && !computing && (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-dim)' }}>
                    ⏳ Загрузка ОПИУ...
                </div>
            )}
            {computing && (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-dim)' }}>
                    ⚙️ Отчёт рассчитывается в фоне... Обновление через несколько секунд
                    <div style={{ marginTop: 8 }}>
                        <div style={{ display: 'inline-block', width: 24, height: 24, border: '3px solid var(--color-border)', borderTopColor: 'var(--color-primary)', borderRadius: '50%', animation: 'spin 1s linear infinite' }} />
                    </div>
                    <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
                </div>
            )}
            {error && (
                <div style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 8, padding: 12, marginBottom: 12, fontSize: 13, color: '#ef4444' }}>
                    ❌ {error}
                </div>
            )}

            {/* Table */}
            {/* TODO: migrate to TanStackDataTable — hierarchical P&L layout with expandable rows, colSpan, sticky columns */}
            {!loading && !error && data && (
                <div className="glass-card" style={{ overflow: 'auto', padding: 0 }}>
                    <table className="data-table" style={{ marginBottom: 0, borderCollapse: 'collapse', width: '100%' }}>
                        <thead>
                            <tr>
                                <th style={{ minWidth: 280, position: 'sticky', left: 0, background: 'var(--color-surface)', zIndex: 2, textAlign: 'left', padding: '10px 12px', borderBottom: '2px solid var(--color-border)' }}>
                                    СТАТЬЯ
                                </th>
                                {/* Year total */}
                                <th colSpan={2} style={{ textAlign: 'center', padding: '10px 8px', borderBottom: '2px solid var(--color-border)', minWidth: 180 }}>
                                    {data.year_label}
                                </th>
                                {/* Monthly columns */}
                                {months.map((m: string) => (
                                    <th key={m} colSpan={2} style={{ textAlign: 'center', padding: '10px 8px', borderBottom: '2px solid var(--color-border)', minWidth: 180, textTransform: 'uppercase', fontSize: 12 }}>
                                        {monthLabel(m).toUpperCase()}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((row: OpiuRow) => {
                                if (isRowHidden(row)) return null;

                                const isBold = row.bold;
                                const isGroup = row.expandable;
                                const isCollapsed = collapsed.has(row.key);
                                const isNegative = row.total < 0;
                                const isHighlight = ['sales_amount', 'gross_margin', 'ebitda', 'net_profit'].includes(row.key);

                                const rowStyle: any = {
                                    fontWeight: isBold ? 600 : 400,
                                    fontSize: row.level >= 2 ? 12 : 13,
                                    borderBottom: '1px solid var(--color-border)',
                                    background: isHighlight ? 'rgba(59,130,246,0.04)' : undefined,
                                };

                                const cellStyle: any = {
                                    padding: '8px 10px',
                                    textAlign: 'right',
                                    fontVariantNumeric: 'tabular-nums',
                                    whiteSpace: 'nowrap',
                                };

                                const pctStyle: any = {
                                    ...cellStyle,
                                    fontSize: 11,
                                    color: 'var(--color-text-dim)',
                                    width: 55,
                                    paddingLeft: 2,
                                };

                                return (
                                    <tr key={row.key} style={rowStyle}>
                                        {/* Label */}
                                        <td style={{
                                            position: 'sticky', left: 0, background: isHighlight ? 'rgba(59,130,246,0.04)' : 'var(--color-surface)',
                                            zIndex: 1, padding: '8px 12px', paddingLeft: 12 + row.level * 20,
                                            cursor: isGroup ? 'pointer' : undefined,
                                            borderBottom: '1px solid var(--color-border)',
                                        }}
                                            onClick={() => isGroup && toggleGroup(row.key)}
                                        >
                                            {isGroup && (
                                                <span style={{ display: 'inline-block', width: 16, fontSize: 10, color: 'var(--color-text-dim)' }}>
                                                    {isCollapsed ? '▶' : '▼'}
                                                </span>
                                            )}
                                            {row.label}
                                        </td>

                                        {/* Year total */}
                                        <td style={{ ...cellStyle, color: isNegative ? '#ef4444' : undefined }}>
                                            {formatNumber(row.total)}
                                        </td>
                                        <td style={pctStyle}>
                                            {row.total_pct != null ? `${row.total_pct.toFixed(2)}%` : ''}
                                        </td>

                                        {/* Monthly */}
                                        {months.map((m: string) => {
                                            const val = row.monthly[m] || 0;
                                            const pct = row.monthly_pct[m];
                                            const neg = val < 0;
                                            return (
                                                <>
                                                    <td key={`${m}-v`} style={{ ...cellStyle, color: neg ? '#ef4444' : undefined }}>
                                                        {formatNumber(val)}
                                                    </td>
                                                    <td key={`${m}-p`} style={pctStyle}>
                                                        {pct != null ? `${pct.toFixed(2)}%` : ''}
                                                    </td>
                                                </>
                                            );
                                        })}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}

            {/* Empty state */}
            {!loading && !error && data && rows.length === 0 && (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-dim)' }}>
                    Нет данных за выбранный период
                </div>
            )}
        </div>
    );
}
