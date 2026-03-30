'use client';
import { useEffect, useState, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

export default function DDSPage() {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [start, setStart] = useState('');
    const [end, setEnd] = useState('');

    useEffect(() => { load(); }, []);

    const load = async () => {
        setLoading(true);
        try {
            const res = await api.getDDS({ start: start || undefined, end: end || undefined });
            setData(res);
        } catch { }
        setLoading(false);
    };

    const rows = data ? (Array.isArray(data) ? data : data.rows || data.report || []) : [];

    const columns: Column[] = useMemo(() => {
        if (rows.length === 0) return [];
        return Object.keys(rows[0]).map(key => ({
            key,
            label: key,
            align: (typeof rows[0][key] === 'number' ? 'right' : 'left') as 'right' | 'left',
            render: (val: any) => (
                <span style={{
                    fontWeight: typeof val === 'number' ? 600 : 400,
                    color: typeof val === 'number' ? (val >= 0 ? 'var(--color-success)' : 'var(--color-danger)') : undefined,
                }}>
                    {typeof val === 'number' ? formatNumber(val) : (val ?? '—')}
                </span>
            ),
        }));
    }, [rows]);

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">ДДС отчёт</h1>
                    <p className="page-subtitle">Движение денежных средств</p>
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

            <TanStackDataTable
                columns={columns}
                data={rows}
                loading={loading}
                emptyIcon="📈"
                emptyText="Нет данных за выбранный период"
                exportName="dds_report"
                enableSorting
                enablePagination={false}
            />
        </div>
    );
}
