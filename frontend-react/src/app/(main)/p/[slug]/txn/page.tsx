'use client';
import { useEffect, useState, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

export default function TransactionsPage() {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [start, setStart] = useState('');
    const [end, setEnd] = useState('');

    useEffect(() => { load(); }, []);

    const load = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.searchTransactions({
                date_from: start || undefined,
                date_to: end || undefined
            });
            setData(Array.isArray(res) ? res : res.transactions || []);
        } catch (e: any) {
            setError(e?.message || 'Ошибка загрузки');
        }
        setLoading(false);
    };

    const columns: Column[] = useMemo(() => [
        { key: 'date', label: 'Дата', render: (v: any) => <span style={{ fontSize: 13, whiteSpace: 'nowrap' }}>{formatDate(v)}</span> },
        { key: 'account', label: 'Счёт', render: (v: any) => <span style={{ fontSize: 13 }}>{v}</span> },
        { key: 'counterparty', label: 'Контрагент', render: (v: any) => <span style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block' }}>{v || '—'}</span> },
        { key: 'cat_lvl1', label: 'Категория', render: (_v: any, row: any) => <>{row.cat_lvl1 || row.event_type || '—'}</> },
        {
            key: 'income', label: 'Сумма', align: 'right' as const,
            render: (_v: any, row: any) => {
                const isIncome = parseFloat(row.income) > 0;
                return (
                    <span style={{ fontWeight: 600, fontFamily: 'monospace', color: isIncome ? 'var(--color-success)' : 'var(--color-danger)' }}>
                        {formatNumber(isIncome ? row.income : row.expense)}
                    </span>
                );
            },
        },
        { key: 'currency', label: 'Валюта', render: (v: any) => <span className="badge badge-info">{v || 'RUB'}</span> },
    ], []);

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Операции</h1>
                    <p className="page-subtitle">Все движения денежных средств</p>
                </div>
            </div>

            <div className="glass-card" style={{ marginBottom: 16, padding: 16 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap' }}>
                    <div className="form-group">
                        <label className="form-label">С даты</label>
                        <input className="form-input" type="date" value={start} onChange={e => setStart(e.target.value)} />
                    </div>
                    <div className="form-group">
                        <label className="form-label">По дату</label>
                        <input className="form-input" type="date" value={end} onChange={e => setEnd(e.target.value)} />
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={load}>Применить</button>
                </div>
            </div>

            {error && (
                <div className="glass-card" style={{ color: 'var(--color-danger)', marginBottom: 16, padding: 16 }}>
                    {error}
                </div>
            )}

            <TanStackDataTable
                columns={columns}
                data={data}
                loading={loading}
                emptyIcon="💳"
                emptyText="Нет транзакций за выбранный период"
                exportName="transactions"
                enableSorting
                enablePagination
                pageSize={50}
            />
        </div>
    );
}
