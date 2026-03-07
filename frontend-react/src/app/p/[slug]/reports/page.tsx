'use client';
import React, { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

export default function ReportsPage() {
    const [tab, setTab] = useState<'dds' | 'balance' | 'fx' | 'customs'>('dds');

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">📊 Отчёты</h1>
                    <p className="page-subtitle">ДДС за месяц, баланс, FX, таможня</p>
                </div>
            </div>

            <div style={{ display: 'flex', gap: 4, marginBottom: 20 }}>
                {[
                    { key: 'dds' as const, label: 'ДДС за месяц' },
                    { key: 'balance' as const, label: 'Баланс по дням' },
                    { key: 'fx' as const, label: 'FX Контроль' },
                    { key: 'customs' as const, label: 'Таможня' },
                ].map(t => (
                    <button key={t.key} className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                        onClick={() => setTab(t.key)}>{t.label}</button>
                ))}
            </div>

            {tab === 'dds' && <DDSPnL />}
            {tab === 'balance' && <BalanceDaily />}
            {tab === 'fx' && <FxControl />}
            {tab === 'customs' && <CustomsControl />}
        </div>
    );
}

function DDSPnL() {
    const now = new Date();
    const [year, setYear] = useState(now.getFullYear());
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [expanded, setExpanded] = useState<Set<string>>(new Set());

    const load = async () => {
        setLoading(true);
        try {
            const res = await api.getDDSPnL(year);
            setData(res);
        } catch { }
        setLoading(false);
    };

    useEffect(() => { load(); }, [year]);

    const toggle = (name: string) => {
        setExpanded(prev => {
            const next = new Set(prev);
            if (next.has(name)) next.delete(name); else next.add(name);
            return next;
        });
    };

    const pct = (val: number, rev: number) => rev > 0 ? ((val / rev) * 100).toFixed(2) + '%' : '';

    if (loading) return <div className="glass-card" style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-muted)' }}>Загрузка отчёта...</div>;
    if (!data) return <div className="glass-card"><div className="empty-state"><div className="empty-state-text">Нажмите год для загрузки</div></div></div>;

    const months: Array<{ key: number; label: string }> = data.months || [];
    const cats: any[] = data.categories || [];
    const summary = data.summary || {};
    const revenue = data.revenue || {};

    // Flatten for Excel export
    const exportRows: any[] = [];
    exportRows.push({ Статья: 'Доходы итого', [`${year}`]: summary.total_income?.total || 0, ...Object.fromEntries(months.map(m => [m.label, summary.total_income?.[String(m.key)] || 0])) });
    cats.filter(c => c.type === 'income').forEach(c => {
        exportRows.push({ Статья: `  ${c.name}`, [`${year}`]: c.monthly?.total || 0, ...Object.fromEntries(months.map(m => [m.label, c.monthly?.[String(m.key)] || 0])) });
    });
    exportRows.push({ Статья: 'Расходы итого', [`${year}`]: summary.total_expense?.total || 0, ...Object.fromEntries(months.map(m => [m.label, summary.total_expense?.[String(m.key)] || 0])) });
    cats.filter(c => c.type === 'expense').forEach(c => {
        exportRows.push({ Статья: `  ${c.name}`, [`${year}`]: c.monthly?.total || 0, ...Object.fromEntries(months.map(m => [m.label, c.monthly?.[String(m.key)] || 0])) });
        (c.counterparties || []).forEach((cp: any) => {
            exportRows.push({ Статья: `    ${cp.name}`, [`${year}`]: cp.monthly?.total || 0, ...Object.fromEntries(months.map(m => [m.label, cp.monthly?.[String(m.key)] || 0])) });
        });
    });
    exportRows.push({ Статья: 'Чистая прибыль', [`${year}`]: summary.net_profit?.total || 0, ...Object.fromEntries(months.map(m => [m.label, summary.net_profit?.[String(m.key)] || 0])) });

    const reversedMonths = [...months].reverse();

    const ValCell = ({ val, rev, color, bold }: { val: number; rev: number; color?: string; bold?: boolean }) => (
        <>
            <td style={{ textAlign: 'right', fontWeight: bold ? 700 : 500, color: color || 'var(--color-text)', whiteSpace: 'nowrap', fontSize: 13 }}>
                {formatNumber(val)}
            </td>
            <td style={{ textAlign: 'right', color: 'var(--color-text-dim)', fontSize: 12, whiteSpace: 'nowrap', paddingRight: 16 }}>
                {pct(val, rev)}
            </td>
        </>
    );

    const incomeCats = cats.filter(c => c.type === 'income');
    const expenseCats = cats.filter(c => c.type === 'expense');

    return (
        <div className="glass-card" style={{ padding: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 20px', borderBottom: '1px solid var(--color-border)' }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => setYear(y => y - 1)}>◀</button>
                    <span style={{ fontWeight: 700, fontSize: 16 }}>{year}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => setYear(y => y + 1)}>▶</button>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(exportRows, `dds_pnl_${year}`)}>📥 Excel</button>
            </div>

            <div style={{ overflowX: 'auto' }}>
                <table className="data-table" style={{ fontSize: 13, minWidth: 800, borderCollapse: 'collapse' }}>
                    <thead>
                        <tr style={{ borderBottom: '2px solid var(--color-border)' }}>
                            <th style={{ textAlign: 'left', minWidth: 250, position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 2, paddingLeft: 20 }}>СТАТЬЯ</th>
                            <th style={{ textAlign: 'right' }}>{year}</th>
                            <th style={{ textAlign: 'right', paddingRight: 16, color: 'var(--color-text-dim)', fontSize: 11 }}>%</th>
                            {reversedMonths.map(m => (
                                <React.Fragment key={m.key}>
                                    <th style={{ textAlign: 'right' }}>{m.label.split(' ')[0].toUpperCase()}</th>
                                    <th style={{ textAlign: 'right', paddingRight: 16, color: 'var(--color-text-dim)', fontSize: 11 }}>%</th>
                                </React.Fragment>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {/* === ДОХОДЫ === */}
                        <tr style={{ background: 'rgba(34,197,94,0.06)', borderBottom: '1px solid var(--color-border)' }}>
                            <td style={{ fontWeight: 700, paddingLeft: 20, position: 'sticky', left: 0, background: 'rgba(34,197,94,0.06)', zIndex: 1 }}>Доходы</td>
                            <td style={{ textAlign: 'right', fontWeight: 700, color: 'var(--color-success)' }}>{formatNumber(summary.total_income?.total || 0)}</td>
                            <td style={{ textAlign: 'right', paddingRight: 16 }}></td>
                            {reversedMonths.map(m => (
                                <React.Fragment key={m.key}>
                                    <td style={{ textAlign: 'right', fontWeight: 700, color: 'var(--color-success)' }}>{formatNumber(summary.total_income?.[String(m.key)] || 0)}</td>
                                    <td style={{ textAlign: 'right', paddingRight: 16 }}></td>
                                </React.Fragment>
                            ))}
                        </tr>

                        {incomeCats.map(cat => (
                            <tr key={cat.name} style={{ borderBottom: '1px solid var(--color-border-light, rgba(0,0,0,0.05))' }}>
                                <td style={{ paddingLeft: 36, color: 'var(--color-text-muted)', position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 1 }}>{cat.name}</td>
                                <td style={{ textAlign: 'right', color: 'var(--color-success)' }}>{formatNumber(cat.monthly?.total || 0)}</td>
                                <td style={{ textAlign: 'right', paddingRight: 16, color: 'var(--color-text-dim)', fontSize: 12 }}>{pct(cat.monthly?.total || 0, revenue.total || 0)}</td>
                                {reversedMonths.map(m => (
                                    <React.Fragment key={m.key}>
                                        <td style={{ textAlign: 'right', color: 'var(--color-success)' }}>{formatNumber(cat.monthly?.[String(m.key)] || 0)}</td>
                                        <td style={{ textAlign: 'right', paddingRight: 16, color: 'var(--color-text-dim)', fontSize: 12 }}>{pct(cat.monthly?.[String(m.key)] || 0, revenue[String(m.key)] || 0)}</td>
                                    </React.Fragment>
                                ))}
                            </tr>
                        ))}

                        {/* === РАСХОДЫ === */}
                        <tr style={{ background: 'rgba(239,68,68,0.06)', borderBottom: '1px solid var(--color-border)', borderTop: '2px solid var(--color-border)' }}>
                            <td style={{ fontWeight: 700, paddingLeft: 20, position: 'sticky', left: 0, background: 'rgba(239,68,68,0.06)', zIndex: 1 }}>Расходы</td>
                            <td style={{ textAlign: 'right', fontWeight: 700, color: 'var(--color-danger)' }}>{formatNumber(summary.total_expense?.total || 0)}</td>
                            <td style={{ textAlign: 'right', paddingRight: 16, color: 'var(--color-danger)', fontSize: 12, fontWeight: 600 }}>{pct(summary.total_expense?.total || 0, revenue.total || 0)}</td>
                            {reversedMonths.map(m => (
                                <React.Fragment key={m.key}>
                                    <td style={{ textAlign: 'right', fontWeight: 700, color: 'var(--color-danger)' }}>{formatNumber(summary.total_expense?.[String(m.key)] || 0)}</td>
                                    <td style={{ textAlign: 'right', paddingRight: 16, color: 'var(--color-danger)', fontSize: 12, fontWeight: 600 }}>{pct(summary.total_expense?.[String(m.key)] || 0, revenue[String(m.key)] || 0)}</td>
                                </React.Fragment>
                            ))}
                        </tr>

                        {expenseCats.map(cat => {
                            const isOpen = expanded.has(cat.name);
                            const cps: any[] = cat.counterparties || [];
                            return (
                                <React.Fragment key={cat.name}>
                                    <tr
                                        style={{ cursor: cps.length > 0 ? 'pointer' : 'default', borderBottom: '1px solid var(--color-border-light, rgba(0,0,0,0.05))' }}
                                        onClick={() => cps.length > 0 && toggle(cat.name)}
                                    >
                                        <td style={{ paddingLeft: 28, fontWeight: 600, position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 1 }}>
                                            {cps.length > 0 && <span style={{ marginRight: 6, fontSize: 10, color: 'var(--color-text-dim)' }}>{isOpen ? '▼' : '▶'}</span>}
                                            {cat.name}
                                        </td>
                                        <td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--color-danger)' }}>{formatNumber(cat.monthly?.total || 0)}</td>
                                        <td style={{ textAlign: 'right', paddingRight: 16, color: 'var(--color-text-dim)', fontSize: 12 }}>{pct(cat.monthly?.total || 0, revenue.total || 0)}</td>
                                        {reversedMonths.map(m => (
                                            <React.Fragment key={m.key}>
                                                <td style={{ textAlign: 'right', color: 'var(--color-danger)' }}>{formatNumber(cat.monthly?.[String(m.key)] || 0)}</td>
                                                <td style={{ textAlign: 'right', paddingRight: 16, color: 'var(--color-text-dim)', fontSize: 12 }}>{pct(cat.monthly?.[String(m.key)] || 0, revenue[String(m.key)] || 0)}</td>
                                            </React.Fragment>
                                        ))}
                                    </tr>

                                    {isOpen && cps.map((cp, ci) => (
                                        <tr key={ci} style={{ background: 'rgba(0,0,0,0.02)', borderBottom: '1px solid var(--color-border-light, rgba(0,0,0,0.03))' }}>
                                            <td style={{ paddingLeft: 52, fontSize: 12, color: 'var(--color-text-muted)', position: 'sticky', left: 0, background: 'rgba(0,0,0,0.02)', zIndex: 1 }}>{cp.name}</td>
                                            <td style={{ textAlign: 'right', fontSize: 12 }}>{formatNumber(cp.monthly?.total || 0)}</td>
                                            <td style={{ textAlign: 'right', paddingRight: 16, fontSize: 11, color: 'var(--color-text-dim)' }}>{pct(cp.monthly?.total || 0, revenue.total || 0)}</td>
                                            {reversedMonths.map(m => (
                                                <React.Fragment key={m.key}>
                                                    <td style={{ textAlign: 'right', fontSize: 12 }}>{formatNumber(cp.monthly?.[String(m.key)] || 0)}</td>
                                                    <td style={{ textAlign: 'right', paddingRight: 16, fontSize: 11, color: 'var(--color-text-dim)' }}>{pct(cp.monthly?.[String(m.key)] || 0, revenue[String(m.key)] || 0)}</td>
                                                </React.Fragment>
                                            ))}
                                        </tr>
                                    ))}
                                </React.Fragment>
                            );
                        })}

                        {/* === ЧИСТАЯ ПРИБЫЛЬ === */}
                        <tr style={{ background: 'rgba(99,102,241,0.06)', borderTop: '2px solid var(--color-border)' }}>
                            <td style={{ fontWeight: 700, paddingLeft: 20, position: 'sticky', left: 0, background: 'rgba(99,102,241,0.06)', zIndex: 1 }}>Чистая прибыль</td>
                            <td style={{ textAlign: 'right', fontWeight: 700, color: (summary.net_profit?.total || 0) >= 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>
                                {formatNumber(summary.net_profit?.total || 0)}
                            </td>
                            <td style={{ textAlign: 'right', paddingRight: 16, fontWeight: 600, fontSize: 12, color: (summary.net_profit?.total || 0) >= 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>
                                {pct(Math.abs(summary.net_profit?.total || 0), revenue.total || 0)}
                            </td>
                            {reversedMonths.map(m => {
                                const v = summary.net_profit?.[String(m.key)] || 0;
                                return (
                                    <React.Fragment key={m.key}>
                                        <td style={{ textAlign: 'right', fontWeight: 700, color: v >= 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>{formatNumber(v)}</td>
                                        <td style={{ textAlign: 'right', paddingRight: 16, fontSize: 12, fontWeight: 600, color: v >= 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>{pct(Math.abs(v), revenue[String(m.key)] || 0)}</td>
                                    </React.Fragment>
                                );
                            })}
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    );
}

function BalanceDaily() {
    const [accounts, setAccounts] = useState<any[]>([]);
    const [accIdx, setAccIdx] = useState(0);
    const [start, setStart] = useState(new Date().getFullYear() + '-01-01');
    const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);
    const [loaded, setLoaded] = useState(false);

    const loadAccounts = async () => {
        if (accounts.length > 0) return;
        try { const a = await api.getAccounts(); setAccounts(a.filter((x: any) => x.is_our_account)); } catch { }
    };
    if (accounts.length === 0) loadAccounts();

    const acc = accounts[accIdx];

    const load = async () => {
        if (!acc) return;
        setLoading(true);
        try {
            const res = await api.getBalanceDaily(acc.account, acc.currency, start, end);
            setData(res || []);
        } catch { }
        setLoading(false);
        setLoaded(true);
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', gap: 12, alignItems: 'end', marginBottom: 16, flexWrap: 'wrap' }}>
                <div className="form-group" style={{ minWidth: 200 }}>
                    <label className="form-label">Счёт</label>
                    <select className="form-input" value={accIdx} onChange={e => setAccIdx(parseInt(e.target.value))}>
                        {accounts.map((a, i) => <option key={i} value={i}>{a.account_name} ({a.currency})</option>)}
                    </select>
                </div>
                <div className="form-group"><label className="form-label">С</label><input className="form-input" type="date" value={start} onChange={e => setStart(e.target.value)} /></div>
                <div className="form-group"><label className="form-label">По</label><input className="form-input" type="date" value={end} onChange={e => setEnd(e.target.value)} /></div>
                <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '...' : 'Показать'}</button>
                {data.length > 0 && <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'balance_daily')}>📥 Excel</button>}
            </div>

            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr><th>Дата</th><th style={{ textAlign: 'right' }}>Нетто за день</th><th style={{ textAlign: 'right' }}>Баланс</th></tr></thead>
                        <tbody>
                            {data.map((r, i) => (
                                <tr key={i}>
                                    <td>{formatDate(r.date)}</td>
                                    <td style={{ textAlign: 'right', color: (r.daily_net || 0) >= 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>{formatNumber(r.daily_net)}</td>
                                    <td style={{ textAlign: 'right', fontWeight: 600 }}>{formatNumber(r.balance)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : loaded ? (
                <div className="empty-state"><div className="empty-state-text">Нет данных за период</div></div>
            ) : (
                <div className="empty-state"><div className="empty-state-text">Выберите счёт и нажмите «Показать»</div></div>
            )}
        </div>
    );
}

function FxControl() {
    const [start, setStart] = useState(new Date().getFullYear() + '-01-01');
    const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);

    const load = async () => {
        setLoading(true);
        try { setData(await api.getFxControl(start, end) || []); } catch { }
        setLoading(false);
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', gap: 12, alignItems: 'end', marginBottom: 16 }}>
                <div className="form-group"><label className="form-label">С</label><input className="form-input" type="date" value={start} onChange={e => setStart(e.target.value)} /></div>
                <div className="form-group"><label className="form-label">По</label><input className="form-input" type="date" value={end} onChange={e => setEnd(e.target.value)} /></div>
                <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '...' : 'Загрузить FX'}</button>
                {data.length > 0 && <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'fx_control')}>📥 Excel</button>}
            </div>

            {data.length > 0 ? (
                <>
                    <div className="stat-card" style={{ marginBottom: 12 }}>
                        <div className="stat-card-label">Всего FX операций</div>
                        <div className="stat-card-value">{data.length}</div>
                    </div>
                    <div style={{ overflowX: 'auto' }}>
                        <table className="data-table">
                            <thead><tr>{Object.keys(data[0]).map(k => <th key={k}>{k}</th>)}</tr></thead>
                            <tbody>{data.map((r, i) => <tr key={i}>{Object.values(r).map((v: any, j) => <td key={j}>{typeof v === 'number' ? formatNumber(v) : v ?? '—'}</td>)}</tr>)}</tbody>
                        </table>
                    </div>
                </>
            ) : <div className="empty-state"><div className="empty-state-text">Нажмите «Загрузить FX»</div></div>}
        </div>
    );
}

function CustomsControl() {
    const [start, setStart] = useState(new Date().getFullYear() + '-01-01');
    const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);

    const load = async () => {
        setLoading(true);
        try { setData(await api.getCustomsControl(start, end) || []); } catch { }
        setLoading(false);
    };

    const totalExpense = data.reduce((s, r) => s + (parseFloat(r.expense) || 0), 0);

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', gap: 12, alignItems: 'end', marginBottom: 16 }}>
                <div className="form-group"><label className="form-label">С</label><input className="form-input" type="date" value={start} onChange={e => setStart(e.target.value)} /></div>
                <div className="form-group"><label className="form-label">По</label><input className="form-input" type="date" value={end} onChange={e => setEnd(e.target.value)} /></div>
                <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '...' : 'Загрузить'}</button>
                {data.length > 0 && <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'customs_control')}>📥 Excel</button>}
            </div>

            {data.length > 0 ? (
                <>
                    <div className="stat-card" style={{ marginBottom: 12, borderLeft: '3px solid var(--color-danger)' }}>
                        <div className="stat-card-label">Итого оплачено таможне</div>
                        <div className="stat-card-value" style={{ color: 'var(--color-danger)' }}>{formatNumber(totalExpense)} ₽</div>
                    </div>
                    <div style={{ overflowX: 'auto' }}>
                        <table className="data-table">
                            <thead><tr>{Object.keys(data[0]).map(k => <th key={k}>{k}</th>)}</tr></thead>
                            <tbody>{data.map((r, i) => <tr key={i}>{Object.values(r).map((v: any, j) => <td key={j}>{typeof v === 'number' ? formatNumber(v) : v ?? '—'}</td>)}</tr>)}</tbody>
                        </table>
                    </div>
                </>
            ) : <div className="empty-state"><div className="empty-state-text">Нажмите «Загрузить»</div></div>}
        </div>
    );
}
