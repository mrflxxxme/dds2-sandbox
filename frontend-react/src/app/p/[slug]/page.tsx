'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';

export default function DashboardPage() {
    const [balance, setBalance] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        api.getBalance().then(setBalance).catch(() => { }).finally(() => setLoading(false));
    }, []);

    const totalRub = balance.filter(b => b.currency === 'RUB').reduce((s, b) => s + b.balance, 0);
    const totalCny = balance.filter(b => b.currency === 'CNY').reduce((s, b) => s + b.balance, 0);
    const accountCount = balance.length;

    if (loading) return <div style={{ padding: 40, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Дашборд</h1>
                    <p className="page-subtitle">Общая сводка по проекту</p>
                </div>
            </div>

            <div className="stats-grid">
                <div className="stat-card" style={{ borderLeft: '3px solid var(--color-success)' }}>
                    <div className="stat-card-label">Баланс RUB</div>
                    <div className="stat-card-value" style={{ color: 'var(--color-success)' }}>{formatNumber(totalRub)} ₽</div>
                </div>
                <div className="stat-card" style={{ borderLeft: '3px solid var(--color-warning)' }}>
                    <div className="stat-card-label">Баланс CNY</div>
                    <div className="stat-card-value" style={{ color: 'var(--color-warning)' }}>{formatNumber(totalCny)} ¥</div>
                </div>
                <div className="stat-card" style={{ borderLeft: '3px solid var(--color-accent)' }}>
                    <div className="stat-card-label">Счета</div>
                    <div className="stat-card-value" style={{ color: 'var(--color-accent)' }}>{accountCount}</div>
                    <div className="stat-card-sub">активных счетов</div>
                </div>
            </div>

            <div className="glass-card">
                <div className="table-toolbar">
                    <h3 style={{ fontSize: 16, fontWeight: 600 }}>Остатки на счетах</h3>
                    <button className="btn btn-secondary btn-sm"
                        onClick={() => exportToExcel(balance, 'balance')}>
                        📥 Экспорт Excel
                    </button>
                </div>
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>Счёт</th>
                            <th>Название</th>
                            <th>Валюта</th>
                            <th style={{ textAlign: 'right' }}>Остаток</th>
                        </tr>
                    </thead>
                    <tbody>
                        {balance.map((b, i) => (
                            <tr key={i}>
                                <td style={{ fontFamily: 'monospace', fontSize: 13 }}>{b.account}</td>
                                <td>{b.account_name || '—'}</td>
                                <td><span className="badge badge-info">{b.currency}</span></td>
                                <td style={{
                                    textAlign: 'right', fontWeight: 600,
                                    color: b.balance >= 0 ? 'var(--color-success)' : 'var(--color-danger)'
                                }}>
                                    {formatNumber(b.balance)} {b.currency === 'RUB' ? '₽' : '¥'}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
