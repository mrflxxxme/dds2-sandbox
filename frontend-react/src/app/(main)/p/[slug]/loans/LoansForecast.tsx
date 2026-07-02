'use client';

import { useCallback, useEffect, useState } from 'react';
import {
    ResponsiveContainer, ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts';
import { api } from '@/lib/api';
import type { LoanForecastResponse } from '@/types/api';
import { money, fmtDate } from './loanFmt';

export default function LoansForecast({ nonce }: { nonce: number }) {
    const [data, setData] = useState<LoanForecastResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setData(await api.loanForecast(12));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load, nonce]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>;
    if (error) {
        return (
            <div className="glass-card" style={{ padding: 16, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between' }}>
                <span>{error}</span>
                <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
            </div>
        );
    }
    if (!data) return null;

    const months = data.months.map((m) => ({
        month: m.month,
        interest: Number(m.interest),
        principal: Number(m.principal_due),
    }));
    const totalInterest = months.reduce((s, m) => s + m.interest, 0);
    const totalPrincipal = months.reduce((s, m) => s + m.principal, 0);

    return (
        <div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 220px' }}>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Проценты за 12 мес (прогноз)</div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--color-warning)' }}>{money(totalInterest)} ₽</div>
                </div>
                <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 220px' }}>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Возврат тела за 12 мес</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{money(totalPrincipal)} ₽</div>
                </div>
            </div>

            <div className="glass-card" style={{ marginBottom: 16 }}>
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>Прогноз выплат по месяцам</h3>
                <ResponsiveContainer width="100%" height={300}>
                    <ComposedChart data={months} margin={{ top: 8, right: 12, left: 8, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                        <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => money(v)} width={70} />
                        <Tooltip formatter={(v: number, n) => [`${money(v)} ₽`, n === 'interest' ? 'Проценты' : 'Возврат тела']} />
                        <Legend formatter={(v) => (v === 'interest' ? 'Проценты' : 'Возврат тела')} />
                        <Bar dataKey="principal" fill="#0071e3" barSize={18} radius={[4, 4, 0, 0]} />
                        <Line type="monotone" dataKey="interest" stroke="#ff9500" strokeWidth={2} dot={{ r: 3 }} />
                    </ComposedChart>
                </ResponsiveContainer>
            </div>

            <div className="glass-card">
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>Ближайшие события</h3>
                {data.upcoming.length === 0 ? (
                    <div style={{ color: 'var(--color-text-dim)', padding: 12 }}>Нет предстоящих выплат.</div>
                ) : (
                    <div style={{ display: 'grid', gap: 8 }}>
                        {data.upcoming.map((e, i) => (
                            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 12px', border: '1px solid var(--color-border)', borderRadius: 12 }}>
                                <span className={`badge ${e.kind === 'MATURITY' ? 'badge-info' : 'badge-warning'}`} style={{ minWidth: 88, textAlign: 'center' }}>
                                    {e.kind === 'MATURITY' ? 'Возврат тела' : 'Выплата %'}
                                </span>
                                <span style={{ width: 96, color: 'var(--color-text-dim)', fontSize: 13 }}>{fmtDate(e.date)}</span>
                                <span style={{ flex: 1, fontWeight: 500 }}>{e.counterparty_name}</span>
                                <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--color-text-dim)' }}>{e.contract_number}</span>
                                <span style={{ fontWeight: 700 }}>{money(e.amount)} ₽</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
