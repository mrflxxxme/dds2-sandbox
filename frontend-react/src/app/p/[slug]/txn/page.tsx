'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

export default function TransactionsPage() {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [start, setStart] = useState('');
    const [end, setEnd] = useState('');

    useEffect(() => { load(); }, []);

    const load = async () => {
        setLoading(true);
        try {
            const res = await api.searchTransactions({
                date_from: start || undefined,
                date_to: end || undefined
            });
            setData(Array.isArray(res) ? res : res.transactions || []);
        } catch { }
        setLoading(false);
    };

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Операции</h1>
                    <p className="page-subtitle">Все движения денежных средств</p>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'transactions')}
                    disabled={data.length === 0}>
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
                ) : data.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-icon">💳</div>
                        <div className="empty-state-text">Нет транзакций за выбранный период</div>
                    </div>
                ) : (
                    <div style={{ overflowX: 'auto' }}>
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Дата</th>
                                    <th>Счёт</th>
                                    <th>Контрагент</th>
                                    <th>Категория</th>
                                    <th style={{ textAlign: 'right' }}>Сумма</th>
                                    <th>Валюта</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.slice(0, 200).map((t: any, i: number) => (
                                    <tr key={i}>
                                        <td style={{ fontSize: 13, whiteSpace: 'nowrap' }}>{formatDate(t.date)}</td>
                                        <td style={{ fontSize: 13 }}>{t.account}</td>
                                        <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {t.counterparty || '—'}
                                        </td>
                                        <td>{t.cat_lvl1 || t.event_type || '—'}</td>
                                        <td style={{
                                            textAlign: 'right', fontWeight: 600, fontFamily: 'monospace',
                                            color: parseFloat(t.income) > 0 ? 'var(--color-success)' : 'var(--color-danger)'
                                        }}>
                                            {formatNumber(parseFloat(t.income) > 0 ? t.income : t.expense)}
                                        </td>
                                        <td><span className="badge badge-info">{t.currency || 'RUB'}</span></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        {data.length > 200 && (
                            <div style={{ padding: 12, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>
                                Показано 200 из {data.length}. Используйте фильтр для уточнения.
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}
