'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';

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

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">ДДС отчёт</h1>
                    <p className="page-subtitle">Движение денежных средств</p>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(rows, 'dds_report')}
                    disabled={rows.length === 0}>
                    📥 Экспорт Excel
                </button>
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

            <div className="glass-card">
                {loading ? (
                    <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                ) : rows.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-icon">📈</div>
                        <div className="empty-state-text">Нет данных за выбранный период</div>
                    </div>
                ) : (
                    <div style={{ overflowX: 'auto' }}>
                        <table className="data-table">
                            <thead>
                                <tr>
                                    {Object.keys(rows[0] || {}).map(key => (
                                        <th key={key}>{key}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row: any, i: number) => (
                                    <tr key={i}>
                                        {Object.values(row).map((val: any, j: number) => (
                                            <td key={j} style={{
                                                textAlign: typeof val === 'number' ? 'right' : 'left',
                                                fontWeight: typeof val === 'number' ? 600 : 400,
                                                color: typeof val === 'number' ? (val >= 0 ? 'var(--color-success)' : 'var(--color-danger)') : undefined,
                                            }}>
                                                {typeof val === 'number' ? formatNumber(val) : (val ?? '—')}
                                            </td>
                                        ))}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}
