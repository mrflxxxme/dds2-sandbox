'use client';
import React, { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

export default function ReportsPage() {
    const [tab, setTab] = useState<'dds' | 'bdr' | 'balance' | 'fx' | 'customs' | 'stock'>('dds');

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">📊 Отчёты</h1>
                    <p className="page-subtitle">ДДС, БДР, баланс, FX, таможня</p>
                </div>
            </div>

            <div style={{ display: 'flex', gap: 4, marginBottom: 20 }}>
                {[
                    { key: 'dds' as const, label: 'ДДС за месяц' },
                    { key: 'bdr' as const, label: 'БДР (WB)' },
                    { key: 'balance' as const, label: 'Баланс по дням' },
                    { key: 'fx' as const, label: 'FX Контроль' },
                    { key: 'customs' as const, label: 'Таможня' },
                    { key: 'stock' as const, label: '📦 Остатки' },
                ].map(t => (
                    <button key={t.key} className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                        onClick={() => setTab(t.key)}>{t.label}</button>
                ))}
            </div>

            {tab === 'dds' && <DDSPnL />}
            {tab === 'bdr' && <WbBdr />}
            {tab === 'balance' && <BalanceDaily />}
            {tab === 'fx' && <FxControl />}
            {tab === 'customs' && <CustomsControl />}
            {tab === 'stock' && <StockAnalytics />}
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

/* ═══════════════  WB БДР  ═══════════════ */

function WbBdr() {
    const fmt = (d: Date) => d.toISOString().split('T')[0];

    // Auto-select last week (Mon-Sun)
    const getLastWeek = () => {
        const now = new Date();
        const day = now.getDay(); // 0=Sun
        const diffToLastSun = day === 0 ? 7 : day;
        const lastSun = new Date(now);
        lastSun.setDate(now.getDate() - diffToLastSun);
        const lastMon = new Date(lastSun);
        lastMon.setDate(lastSun.getDate() - 6);
        return { from: fmt(lastMon), to: fmt(lastSun) };
    };
    const lw = getLastWeek();

    const [dateFrom, setDateFrom] = useState(lw.from);
    const [dateTo, setDateTo] = useState(lw.to);
    const [brand, setBrand] = useState('');
    const [articleSearch, setArticleSearch] = useState('');

    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [syncStatus, setSyncStatus] = useState<any>(null);
    const [syncing, setSyncing] = useState(false);
    const [availableDates, setAvailableDates] = useState<string[]>([]);
    const [sortKey, setSortKey] = useState<string>('profit');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

    // Load sync status + available dates on mount
    React.useEffect(() => {
        api.getWbBdrSyncStatus().then(setSyncStatus).catch(() => {});
        api.getWbBdrAvailableWeeks().then(r => setAvailableDates(r.available_dates || [])).catch(() => {});
    }, []);

    const loadData = React.useCallback(async () => {
        setLoading(true); setError('');
        try {
            const res = await api.getWbBdr(dateFrom, dateTo, brand || undefined, articleSearch || undefined);
            setData(res);
            if (res?.sync_status) setSyncStatus(res.sync_status);
        } catch (e: any) { setError(e.message || 'Ошибка загрузки'); }
        finally { setLoading(false); }
    }, [dateFrom, dateTo, brand, articleSearch]);

    // Auto-load data on mount
    React.useEffect(() => { loadData(); }, []);

    const handleSync = React.useCallback(async () => {
        setSyncing(true);
        try {
            await api.triggerWbBdrSync();
            const st = await api.getWbBdrSyncStatus();
            setSyncStatus(st);
            // Reload data after sync
            await loadData();
        } catch (e: any) {
            setError('Ошибка синхронизации: ' + (e.message || ''));
        } finally { setSyncing(false); }
    }, [loadData]);

    const s = data?.summary;
    const rawArticles = data?.articles || [];
    const brands = data?.brands || [];
    const taxInfo = data?.tax_info || {};

    // ── BDR column definitions ──
    const bdrColumns: { key: string; label: string; color?: string; sticky?: boolean }[] = [
        { key: 'sa_name', label: 'Артикул', sticky: true },
        { key: 'to_pay', label: 'К оплате' },
        { key: 'brand', label: 'Бренд' },
        { key: 'subject', label: 'Категория' },
        { key: 'nm_id', label: 'Арт. МП' },
        { key: 'cost_price', label: 'Ср. С/С' },
        { key: 'other_deduction', label: 'Проч. удерж.' },
        { key: 'avg_retail_price', label: 'Ср. цена до скидок' },
        { key: 'avg_sale_price', label: 'Ср. цена продажи' },
        { key: 'realization', label: 'Реализация' },
        { key: 'turnover_days', label: 'Оборач. (дн.)' },
        { key: 'sales_amount', label: 'Продажи' },
        { key: 'ppvz_for_pay', label: 'К переч.' },
        { key: 'returns_amount', label: 'Возвраты' },
        { key: 'cost_total', label: 'С/С продаж' },
        { key: 'penalties', label: 'Штрафы' },
        { key: 'orders_count', label: 'Заказы шт' },
        { key: 'orders_sum', label: 'Заказы ₽' },
        { key: 'commission', label: 'Комиссия' },
        { key: 'total_wb_reward', label: 'Возн. ВБ' },
        { key: 'compensation', label: 'Компенсация' },
        { key: 'avg_logistics', label: 'Ср. логист.' },
        { key: 'cap_cost', label: 'Кап. по С/С' },
        { key: 'cap_retail', label: 'Кап. по розн.' },
        { key: 'gmroi', label: 'GMROI' },
        { key: 'gmroi_year', label: 'GMROI Year' },
        { key: 'ret_qty', label: 'Откз.+возвр.' },
        { key: 'sale_qty', label: 'Продаж шт' },
        { key: 'buyout_pct', label: '% выкупа' },
        { key: 'avg_profit_per_item', label: 'Ср. приб./шт' },
        { key: 'tax_total', label: 'Налоги' },
        { key: 'tax_base', label: 'Нал. база' },
        { key: 'profit', label: 'Прибыль', color: '#22c55e' },
        { key: 'roi', label: 'ROI %' },
        { key: 'revenue_share', label: 'Доля выр. %' },
        { key: 'margin_pct', label: 'Маржа %' },
        { key: 'adv_sum', label: 'Реклама', color: '#f59e0b' },
        { key: 'drr', label: 'ДРР %' },
        { key: 'drr_orders', label: 'ДРР заказы %' },
        { key: 'acceptance', label: 'Плат. приёмка' },
        { key: 'logistics', label: 'Логистика' },
        { key: 'storage', label: 'Хранение' },
        { key: 'abc_profit', label: 'ABC приб.' },
        { key: 'abc_revenue', label: 'ABC выр.' },
    ];

    // ── Sort logic ──
    const toggleSort = (key: string) => {
        if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
        else { setSortKey(key); setSortDir('desc'); }
    };

    const articles = React.useMemo(() => {
        const arr = [...rawArticles];
        arr.sort((a: any, b: any) => {
            let va = a[sortKey] ?? 0, vb = b[sortKey] ?? 0;
            if (typeof va === 'string') va = va.toLowerCase();
            if (typeof vb === 'string') vb = vb.toLowerCase();
            if (va < vb) return sortDir === 'asc' ? -1 : 1;
            if (va > vb) return sortDir === 'asc' ? 1 : -1;
            return 0;
        });
        return arr;
    }, [rawArticles, sortKey, sortDir]);

    // Sortable header style
    const thStyle = (col: { key: string; color?: string; sticky?: boolean }): React.CSSProperties => ({
        cursor: 'pointer',
        userSelect: 'none' as const,
        whiteSpace: 'nowrap' as const,
        background: '#f9fafb',
        color: col.color || '#4b5563',
        borderBottom: '2px solid #e5e7eb',
        ...(col.sticky ? { position: 'sticky' as const, left: 0, zIndex: 22, borderRight: '1px solid #e5e7eb' } : {}),
    });
    const sortIcon = (key: string) => sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';

    const pct = (val: number, base: number) => base ? ((val / base) * 100).toFixed(2) + '%' : '—';

    const handleExcel = () => {
        if (!articles.length) return;
        const rows = articles.map((a: any, i: number) => {
            const row: Record<string, any> = { '№': i + 1 };
            bdrColumns.forEach(col => { row[col.label] = a[col.key] ?? ''; });
            return row;
        });
        exportToExcel(rows, `BDR_${dateFrom}_${dateTo}`);
    };

    // Format sync time
    const syncTime = syncStatus?.last_sync
        ? new Date(syncStatus.last_sync).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
        : null;
    const isAllSynced = syncStatus?.total_rows > 0;

    return (
        <div>
            {/* ── Sync Status Badge ── */}
            <div className="glass-card" style={{ padding: '10px 16px', marginBottom: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 13 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span>🔄 Последняя синхронизация: {syncTime || 'нет данных'}</span>
                    {syncStatus?.last_status === 'OK' && <span style={{ color: '#22c55e' }}>● авто</span>}
                    {syncStatus?.last_status === 'ERROR' && <span style={{ color: '#f43f5e' }}>● ошибка</span>}
                    {isAllSynced && <span style={{ color: '#22c55e' }}>✅ Все дни синхронизированы</span>}
                    {!isAllSynced && syncTime && <span style={{ color: '#eab308' }}>⚠️ Нет данных</span>}
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    {taxInfo?.tax_regime && (
                        <span className="badge badge-info" style={{ fontSize: 11 }}>
                            {taxInfo.tax_regime === 'usn_income' ? 'УСН Доходы' : 'УСН Д-Р'}
                            {taxInfo.usn_rate > 0 && ` ${taxInfo.usn_rate}%`}
                            {taxInfo.nds_rate > 0 && ` + НДС ${taxInfo.nds_rate}%`}
                        </span>
                    )}
                    <button className="btn btn-secondary btn-sm" onClick={handleSync} disabled={syncing}
                        style={{ fontSize: 12, padding: '4px 10px' }}>
                        {syncing ? '⏳ Синхр...' : '🔄 Синхронизировать'}
                    </button>
                    <button className="btn btn-secondary btn-sm" onClick={handleExcel} disabled={!articles.length}
                        style={{ fontSize: 12, padding: '4px 10px' }}>
                        📥 Excel
                    </button>
                </div>
            </div>

            {/* ── Filters ── */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
                    <div>
                        <label style={{ fontSize: 12, opacity: 0.7, display: 'block', marginBottom: 4 }}>С</label>
                        <input type="date" className="input" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={{ width: 150 }} />
                    </div>
                    <div>
                        <label style={{ fontSize: 12, opacity: 0.7, display: 'block', marginBottom: 4 }}>По</label>
                        <input type="date" className="input" value={dateTo} onChange={e => setDateTo(e.target.value)} style={{ width: 150 }} />
                    </div>
                    <div>
                        <label style={{ fontSize: 12, opacity: 0.7, display: 'block', marginBottom: 4 }}>Бренд</label>
                        <select className="input" value={brand} onChange={e => setBrand(e.target.value)} style={{ width: 170 }}>
                            <option value="">Все бренды</option>
                            {brands.map((b: string) => <option key={b} value={b}>{b}</option>)}
                        </select>
                    </div>
                    <div>
                        <label style={{ fontSize: 12, opacity: 0.7, display: 'block', marginBottom: 4 }}>Артикул</label>
                        <input className="input" placeholder="Поиск..." value={articleSearch} onChange={e => setArticleSearch(e.target.value)} style={{ width: 160 }} />
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={loadData} disabled={loading}
                        style={{ height: 38 }}>{loading ? '⏳ Загрузка...' : '📊 Загрузить'}</button>
                    {articles.length > 0 && (
                        <button className="btn btn-secondary btn-sm" onClick={handleExcel} style={{ height: 38 }}>📥 Excel</button>
                    )}
                </div>
            </div>

            {error && <div className="glass-card" style={{ padding: 16, color: '#ff6b6b' }}>⚠️ {error}</div>}

            {loading && <div className="glass-card" style={{ padding: 40, textAlign: 'center' }}>
                <div style={{ fontSize: 32, marginBottom: 12 }}>⏳</div>
                <div style={{ opacity: 0.7 }}>Загрузка данных...</div>
            </div>}

            {s && !loading && (
                <>
                    {/* ── KPI Cards ── */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12, marginBottom: 16 }}>
                        <KpiCard label="Итого к оплате" value={formatNumber(s.to_pay)} sub="₽" />
                        <KpiCard label="Реализация" value={formatNumber(s.realization)} sub="₽" />
                        <KpiCard label="Продажи" value={formatNumber(s.sales_amount)} sub={`₽ / ${formatNumber(s.sale_qty)} шт`} />
                        <KpiCard label="Возвраты" value={formatNumber(s.returns_amount)} sub={`₽ / ${formatNumber(s.ret_qty)} шт`} />
                        <KpiCard label="Комиссия" value={formatNumber(s.commission)} sub={pct(s.commission, s.realization)} color={s.commission < 0 ? '#ff6b6b' : undefined} />
                        <KpiCard label="Логистика" value={formatNumber(s.logistics)} sub={pct(s.logistics, s.realization)} />
                        <KpiCard label="Хранение" value={formatNumber(s.storage)} sub={pct(s.storage, s.realization)} />
                        <KpiCard label="Реклама" value={formatNumber(s.adv_sum || 0)} sub={pct(s.adv_sum || 0, s.realization)} color="#f59e0b" />
                        <KpiCard label="Прочие удержания" value={formatNumber(s.other_deduction || 0)} sub={pct(s.other_deduction || 0, s.realization)} />
                        <KpiCard label="Себестоимость" value={formatNumber(s.cost_total || 0)} sub={pct(s.cost_total || 0, s.realization)} color="#8b5cf6" />
                        <KpiCard label="Налог" value={formatNumber(s.tax_total || 0)} sub={`НДС ${formatNumber(s.tax_nds || 0)} + УСН ${formatNumber(s.tax_usn || 0)}`} color="#ef4444" />
                        <KpiCard label="Чистая прибыль" value={formatNumber(s.profit || 0)} sub={pct(s.profit || 0, s.realization)} color={s.profit >= 0 ? '#22c55e' : '#ff6b6b'} />
                        <KpiCard label="% выкупа" value={s.buyout_pct?.toFixed(2) + '%'} sub="" />
                    </div>

                    {/* ── Top 10 Products Widget ── */}
                    <TopProductsWidget articles={rawArticles} />

                    {/* ── No cost warning ── */}
                    {(() => { const n = articles.filter((a: any) => !a.cost_price || a.cost_price === 0).length; return n > 0 ? (
                        <div style={{ padding: '10px 16px', marginBottom: 12, background: 'rgba(245,158,11,0.15)', border: '1px solid rgba(245,158,11,0.4)', borderRadius: 8, color: '#f59e0b', fontSize: 13 }}>
                            ⚠️ Товары без себестоимости — {n} шт
                        </div>
                    ) : null; })()}

                    {/* ── Articles Table ── */}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                        <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 280px)' }}>
                            <table className="data-table" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                                <thead>
                                    <tr>
                                        {bdrColumns.map(col => (
                                            <th key={col.key} style={thStyle(col)} onClick={() => toggleSort(col.key)}>
                                                {col.label}{sortIcon(col.key)}
                                            </th>
                                        ))}
                                    </tr>
                            </thead>
                            <tbody>
                                {/* Summary row */}
                                {(() => { const r = s; return (
                                <tr style={{ fontWeight: 700, background: '#eef2ff', color: '#111827' }}>
                                    <td style={{ position: 'sticky', left: 0, background: '#e0e7ff', zIndex: 11, borderRight: '1px solid #c7d2fe' }}>Итого:</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.to_pay)}</td>
                                    <td>-</td><td>-</td><td>-</td>
                                    <td style={{ textAlign: 'right' }}>—</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.other_deduction || 0)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.avg_retail_price)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.avg_sale_price)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.realization)}</td>
                                    <td style={{ textAlign: 'right' }}>{r.turnover_days || '—'}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.sales_amount)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.ppvz_for_pay)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.returns_amount)}</td>
                                    <td style={{ textAlign: 'right', color: '#8b5cf6' }}>{formatNumber(r.cost_total || 0)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.penalties)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.orders_count || 0)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.orders_sum || 0)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.commission)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.total_wb_reward)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.compensation)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.avg_logistics)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.cap_cost || 0)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.cap_retail || 0)}</td>
                                    <td style={{ textAlign: 'right' }}>{r.gmroi || '—'}</td>
                                    <td style={{ textAlign: 'right' }}>{r.gmroi_year || '—'}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.ret_qty)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.sale_qty)}</td>
                                    <td style={{ textAlign: 'right' }}>{r.buyout_pct?.toFixed(2)}%</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.avg_profit_per_item || 0)}</td>
                                    <td style={{ textAlign: 'right', color: '#ef4444' }}>{formatNumber(r.tax_total || 0)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.tax_base || r.sales_amount || 0)}</td>
                                    <td style={{ textAlign: 'right', color: r.profit >= 0 ? '#22c55e' : '#ff6b6b', fontWeight: 700 }}>{formatNumber(r.profit || 0)}</td>
                                    <td style={{ textAlign: 'right' }}>{r.roi || '—'}%</td>
                                    <td style={{ textAlign: 'right' }}>100%</td>
                                    <td style={{ textAlign: 'right' }}>{r.margin_pct?.toFixed(2) || '—'}%</td>
                                    <td style={{ textAlign: 'right', color: '#f59e0b' }}>{formatNumber(r.adv_sum || 0)}</td>
                                    <td style={{ textAlign: 'right' }}>{r.drr?.toFixed(2) || '—'}%</td>
                                    <td style={{ textAlign: 'right' }}>{r.drr_orders?.toFixed(2) || '—'}%</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.acceptance || 0)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.logistics)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(r.storage)}</td>
                                    <td>-</td><td>-</td>
                                </tr>
                                ); })()}
                                {/* Article rows */}
                                {articles.map((a: any, i: number) => {
                                    const rowBg = i % 2 === 0 ? '#ffffff' : '#f9fafb';
                                    return (
                                    <tr key={a.sa_name || i} style={{ background: rowBg, color: '#111827' }}>
                                        <td style={{ position: 'sticky', left: 0, background: rowBg, zIndex: 11, fontWeight: 500, borderRight: '1px solid #e5e7eb' }}>
                                            {a.sa_name || '—'}
                                        </td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.to_pay)}</td>
                                        <td>{a.brand || '—'}</td>
                                        <td>{a.subject || '—'}</td>
                                        <td>{a.nm_id || '—'}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.cost_price || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.other_deduction || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_retail_price)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_sale_price)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.realization)}</td>
                                        <td style={{ textAlign: 'right' }}>{a.turnover_days || '—'}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.sales_amount)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.ppvz_for_pay)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.returns_amount)}</td>
                                        <td style={{ textAlign: 'right', color: '#8b5cf6' }}>{formatNumber(a.cost_total || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.penalties)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.orders_count || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.orders_sum || 0)}</td>
                                        <td style={{ textAlign: 'right', color: a.commission < 0 ? '#ff6b6b' : undefined }}>{formatNumber(a.commission)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.total_wb_reward)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.compensation)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_logistics)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.cap_cost || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.cap_retail || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{a.gmroi || '—'}</td>
                                        <td style={{ textAlign: 'right' }}>{a.gmroi_year || '—'}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.ret_qty)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.sale_qty)}</td>
                                        <td style={{ textAlign: 'right' }}>{a.buyout_pct?.toFixed(2) || '—'}%</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_profit_per_item || 0)}</td>
                                        <td style={{ textAlign: 'right', color: '#ef4444' }}>{formatNumber(a.tax_total || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.tax_base || 0)}</td>
                                        <td style={{ textAlign: 'right', color: a.profit >= 0 ? '#22c55e' : '#ff6b6b', fontWeight: 600 }}>{formatNumber(a.profit || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{a.roi || '—'}%</td>
                                        <td style={{ textAlign: 'right' }}>{a.revenue_share?.toFixed(2) || '—'}%</td>
                                        <td style={{ textAlign: 'right' }}>{a.margin_pct?.toFixed(2) || '—'}%</td>
                                        <td style={{ textAlign: 'right', color: '#f59e0b' }}>{formatNumber(a.adv_sum || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{a.drr?.toFixed(2) || '—'}%</td>
                                        <td style={{ textAlign: 'right' }}>{a.drr_orders?.toFixed(2) || '—'}%</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.acceptance || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.logistics)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(a.storage)}</td>
                                        <td style={{ textAlign: 'center' }}><span className={`badge-${a.abc_profit === 'A' ? 'green' : a.abc_profit === 'B' ? 'yellow' : 'red'}`}>{a.abc_profit}</span></td>
                                        <td style={{ textAlign: 'center' }}><span className={`badge-${a.abc_revenue === 'A' ? 'green' : a.abc_revenue === 'B' ? 'yellow' : 'red'}`}>{a.abc_revenue}</span></td>
                                    </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                        {articles.length === 0 && <div className="empty-state" style={{ padding: 20 }}>Нет данных за выбранный период</div>}
                    </div>
                </div>

                    {/* ── Tax Summary ── */}
                    {s.tax_total > 0 && (
                        <div className="glass-card" style={{ padding: 16, marginTop: 12 }}>
                            <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 14 }}>📋 Налоговая нагрузка</div>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8, fontSize: 13 }}>
                                <div>Доходы (Продажи): <b>{formatNumber(s.sales_amount)} ₽</b></div>
                                {s.tax_nds > 0 && <div>Сумма НДС: <b style={{ color: '#ef4444' }}>{formatNumber(s.tax_nds)} ₽</b></div>}
                                <div>Сумма УСН: <b style={{ color: '#ef4444' }}>{formatNumber(s.tax_usn)} ₽</b></div>
                                <div>Итого налог: <b style={{ color: '#ef4444' }}>{formatNumber(s.tax_total)} ₽</b></div>
                                {s.expenses_total > 0 && <div>Расходы (для базы): <b>{formatNumber(s.expenses_total)} ₽</b></div>}
                            </div>
                        </div>
                    )}

                    <div style={{ marginTop: 8, opacity: 0.5, fontSize: 12 }}>
                        Строк в БД: {data?.total_rows || 0} · Артикулов: {articles.length}
                    </div>
                </>
            )}

            {!data && !loading && !error && (
                <div className="glass-card" style={{ padding: 40, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>📈</div>
                    <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>БДР — Бюджет Доходов и Расходов</div>
                    <div style={{ opacity: 0.7 }}>Выберите период и нажмите «Загрузить» для получения данных</div>
                    {!isAllSynced && (
                        <div style={{ marginTop: 12, opacity: 0.6, fontSize: 13 }}>
                            💡 Данные загрузятся автоматически при первом запросе
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

/* ──────────────── История себестоимости ──────────────── */
function CostHistory() {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [search, setSearch] = useState('');
    const [brand, setBrand] = useState('');

    const load = React.useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const r = await api.getCostHistory(search || undefined, brand || undefined);
            setData(r);
        } catch (e: any) { setError(e.message || 'Ошибка'); }
        setLoading(false);
    }, [search, brand]);

    useEffect(() => { load(); }, [load]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>⏳ Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--danger)' }}>❌ {error}</div>;
    if (!data || !data.articles?.length) return <div className="glass-card" style={{ padding: 32, textAlign: 'center', opacity: 0.6 }}>Нет данных о себестоимости</div>;

    const orders: Array<{ order_no: string; ship_date: string }> = data.orders || [];
    const articles: any[] = data.articles || [];
    const brands: string[] = data.brands || [];

    const handleExport = () => {
        const rows = articles.map((a: any) => {
            const row: Record<string, any> = {
                'Артикул': a.article_seller,
                'Артикул WB': a.article_wb || '',
                'Баркод': a.barcode,
                'Бренд': a.brand || '',
                'Категория': a.subject,
            };
            orders.forEach((o: any) => {
                const c = a.costs?.[o.order_no];
                row[`Заказ ${o.order_no}`] = c ? c.cost : '';
                row[`Кол-во ${o.order_no}`] = c ? c.qty : '';
            });
            row['Средняя'] = a.avg_cost;
            row['Последняя'] = a.latest_cost;
            return row;
        });
        exportToExcel(rows, `Себестоимость`);
    };

    const selectStyle: React.CSSProperties = {
        padding: '8px 12px', borderRadius: 8,
        border: '1px solid var(--border)',
        background: 'var(--bg-secondary)', color: 'var(--text-primary)',
    };

    return (
        <div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                <input
                    type="text" placeholder="🔍 Поиск по артикулу / WB артикулу"
                    value={search}
                    onChange={(e: any) => setSearch(e.target.value)}
                    onKeyDown={(e: any) => e.key === 'Enter' && load()}
                    style={{ ...selectStyle, width: 280 }}
                />
                <select value={brand} onChange={(e: any) => setBrand(e.target.value)} style={selectStyle}>
                    <option value="">Все бренды</option>
                    {brands.map((b: string) => <option key={b} value={b}>{b}</option>)}
                </select>
                <button className="btn btn-secondary btn-sm" onClick={load}>🔄</button>
                <button className="btn btn-secondary btn-sm" onClick={handleExport}>📥 Excel</button>
                <span style={{ opacity: 0.5, fontSize: 13 }}>{articles.length} артикулов × {orders.length} заказов</span>
            </div>

            <div className="glass-card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 220px)' }}>
                <table className="data-table" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
                    <thead>
                        <tr>
                            <th style={{ position: 'sticky', left: 0, background: 'var(--bg-secondary)', zIndex: 2, minWidth: 180 }}>Артикул</th>
                            <th style={{ minWidth: 100 }}>WB Артикул</th>
                            <th style={{ minWidth: 80 }}>Бренд</th>
                            <th style={{ minWidth: 100 }}>Категория</th>
                            <th style={{ textAlign: 'right', fontWeight: 700, color: 'var(--primary)' }}>Средняя ₽</th>
                            <th style={{ textAlign: 'right', fontWeight: 700, color: 'var(--success)' }}>Последняя ₽</th>
                            {orders.map((o: any) => (
                                <th key={o.order_no} style={{ textAlign: 'right', minWidth: 100 }}>
                                    <div>{o.order_no}</div>
                                    <div style={{ fontSize: 10, opacity: 0.5 }}>{o.ship_date ? formatDate(o.ship_date) : ''}</div>
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {articles.map((a: any, i: number) => {
                            return (
                                <tr key={i}>
                                    <td style={{ position: 'sticky', left: 0, background: 'var(--bg-primary)', zIndex: 1, fontWeight: 600 }}>{a.article_seller}</td>
                                    <td style={{ opacity: 0.7, fontSize: 12 }}>{a.article_wb || '—'}</td>
                                    <td><span className="badge badge-info" style={{ fontSize: 11 }}>{a.brand || '—'}</span></td>
                                    <td style={{ opacity: 0.7 }}>{a.subject || '—'}</td>
                                    <td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--primary)' }}>{a.avg_cost ? formatNumber(a.avg_cost) : '—'}</td>
                                    <td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--success)' }}>{a.latest_cost ? formatNumber(a.latest_cost) : '—'}</td>
                                    {orders.map((o: any, j: number) => {
                                        const c = a.costs?.[o.order_no];
                                        if (!c) return <td key={j} style={{ textAlign: 'right', opacity: 0.2 }}>—</td>;
                                        const prev = j < orders.length - 1 ? (a.costs?.[orders[j + 1]?.order_no]?.cost || 0) : 0;
                                        const diff = prev > 0 ? ((c.cost - prev) / prev * 100) : 0;
                                        const color = diff > 5 ? 'var(--danger)' : diff < -5 ? 'var(--success)' : 'var(--text-primary)';
                                        return (
                                            <td key={j} style={{ textAlign: 'right', color }}>
                                                <div>{formatNumber(c.cost)}</div>
                                                {diff !== 0 && <div style={{ fontSize: 10, opacity: 0.6 }}>{diff > 0 ? '↑' : '↓'}{Math.abs(diff).toFixed(1)}%</div>}
                                                <div style={{ fontSize: 10, opacity: 0.4 }}>{c.qty} шт</div>
                                            </td>
                                        );
                                    })}
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

function TopProductsWidget({ articles }: { articles: any[] }) {
    const [mode, setMode] = useState<'profit' | 'loss'>('profit');

    const sorted = React.useMemo(() => {
        const arr = articles.filter((a: any) => a.profit !== undefined && a.profit !== null && a.sa_name);
        if (mode === 'profit') {
            return [...arr].sort((a, b) => (b.profit || 0) - (a.profit || 0)).slice(0, 10);
        }
        return [...arr].sort((a, b) => (a.profit || 0) - (b.profit || 0)).slice(0, 10).filter(a => (a.profit || 0) < 0);
    }, [articles, mode]);

    if (!articles.length) return null;

    const isProfit = mode === 'profit';
    const accentColor = isProfit ? '#22c55e' : '#ef4444';
    const accentBg = isProfit ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)';
    const accentBorder = isProfit ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)';

    return (
        <div className="glass-card" style={{ marginBottom: 16, padding: 0, overflow: 'hidden' }}>
            {/* Header with toggle */}
            <div style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '14px 20px', borderBottom: `1px solid ${accentBorder}`,
                background: accentBg, transition: 'all 0.3s ease',
            }}>
                <div style={{ fontWeight: 700, fontSize: 15, color: accentColor, display: 'flex', alignItems: 'center', gap: 8 }}>
                    {isProfit ? '📈' : '📉'} Топ-10 {isProfit ? 'прибыльных' : 'убыточных'} товаров
                </div>
                <div style={{
                    display: 'flex', borderRadius: 8, overflow: 'hidden',
                    border: '1px solid #e5e7eb', background: '#f3f4f6',
                }}>
                    <button
                        onClick={() => setMode('profit')}
                        style={{
                            padding: '6px 16px', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer',
                            background: isProfit ? '#22c55e' : 'transparent',
                            color: isProfit ? '#fff' : '#6b7280',
                            transition: 'all 0.2s ease',
                        }}
                    >
                        💰 Прибыльные
                    </button>
                    <button
                        onClick={() => setMode('loss')}
                        style={{
                            padding: '6px 16px', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer',
                            background: !isProfit ? '#ef4444' : 'transparent',
                            color: !isProfit ? '#fff' : '#6b7280',
                            transition: 'all 0.2s ease',
                        }}
                    >
                        📉 Убыточные
                    </button>
                </div>
            </div>

            {/* Table */}
            {sorted.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            <tr style={{ borderBottom: '2px solid #e5e7eb', background: '#f9fafb' }}>
                                <th style={{ textAlign: 'left', padding: '10px 16px', color: '#6b7280', fontWeight: 600, width: 40 }}>№</th>
                                <th style={{ textAlign: 'left', padding: '10px 12px', color: '#6b7280', fontWeight: 600 }}>Артикул</th>
                                <th style={{ textAlign: 'left', padding: '10px 12px', color: '#6b7280', fontWeight: 600 }}>Категория</th>
                                <th style={{ textAlign: 'right', padding: '10px 12px', color: accentColor, fontWeight: 700 }}>Прибыль ₽</th>
                                <th style={{ textAlign: 'right', padding: '10px 12px', color: '#6b7280', fontWeight: 600 }}>Маржа %</th>
                                <th style={{ textAlign: 'right', padding: '10px 12px', color: '#6b7280', fontWeight: 600 }}>ROI %</th>
                                <th style={{ textAlign: 'right', padding: '10px 12px', color: '#6b7280', fontWeight: 600 }}>Продажи ₽</th>
                                <th style={{ textAlign: 'right', padding: '10px 12px', color: '#f59e0b', fontWeight: 600 }}>Реклама ₽</th>
                                <th style={{ textAlign: 'right', padding: '10px 16px', color: '#6b7280', fontWeight: 600 }}>ДРР %</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sorted.map((a: any, i: number) => {
                                const profitColor = (a.profit || 0) >= 0 ? '#22c55e' : '#ef4444';
                                return (
                                    <tr key={a.sa_name || i}
                                        style={{
                                            borderBottom: '1px solid #f3f4f6',
                                            transition: 'background 0.15s',
                                        }}
                                        onMouseEnter={(e) => (e.currentTarget.style.background = '#f8fafc')}
                                        onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                                    >
                                        <td style={{ padding: '10px 16px', color: '#9ca3af', fontWeight: 600 }}>{i + 1}</td>
                                        <td style={{ padding: '10px 12px', fontWeight: 600, color: '#111827' }}>{a.sa_name || '—'}</td>
                                        <td style={{ padding: '10px 12px', color: '#6b7280', fontSize: 12 }}>{a.subject || '—'}</td>
                                        <td style={{ padding: '10px 12px', textAlign: 'right', fontWeight: 700, color: profitColor }}>
                                            {formatNumber(a.profit || 0)}
                                        </td>
                                        <td style={{ padding: '10px 12px', textAlign: 'right', color: '#374151' }}>
                                            {a.margin_pct?.toFixed(1) || '—'}%
                                        </td>
                                        <td style={{ padding: '10px 12px', textAlign: 'right', color: '#374151' }}>
                                            {a.roi || '—'}%
                                        </td>
                                        <td style={{ padding: '10px 12px', textAlign: 'right', color: '#374151' }}>
                                            {formatNumber(a.sales_amount || 0)}
                                        </td>
                                        <td style={{ padding: '10px 12px', textAlign: 'right', color: '#f59e0b', fontWeight: 500 }}>
                                            {formatNumber(a.adv_sum || 0)}
                                        </td>
                                        <td style={{ padding: '10px 16px', textAlign: 'right', color: '#374151' }}>
                                            {a.drr?.toFixed(1) || '—'}%
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            ) : (
                <div style={{ padding: '24px 20px', textAlign: 'center', color: '#9ca3af', fontSize: 14 }}>
                    {mode === 'loss' ? 'Нет убыточных товаров 🎉' : 'Нет данных'}
                </div>
            )}
        </div>
    );
}

function KpiCard({ label, value, sub, color }: { label: string; value: string; sub: string; color?: string }) {
    return (
        <div className="glass-card" style={{ padding: '14px 16px' }}>
            <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: color || 'var(--text-primary)' }}>{value}</div>
            {sub && <div style={{ fontSize: 12, opacity: 0.5, marginTop: 2 }}>{sub}</div>}
        </div>
    );
}


// ═══════════════════════════════════════════════════════════════════════════════
// StockAnalytics — Аналитика остатков
// ═══════════════════════════════════════════════════════════════════════════════

function StockAnalytics() {
    const [subView, setSubView] = useState<'articles' | 'warehouses' | 'need' | 'settings'>('articles');
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [trendDays, setTrendDays] = useState(7);
    const [subjectFilter, setSubjectFilter] = useState('');
    const [brandFilter, setBrandFilter] = useState('');
    const [articleFilter, setArticleFilter] = useState('');
    const [trafficFilter, setTrafficFilter] = useState<string | null>(null);
    const [page, setPage] = useState(0);
    const [perPage, setPerPage] = useState(25);

    const load = async () => {
        setLoading(true);
        try {
            const res = await api.getStockAnalytics(
                trendDays,
                subjectFilter || undefined,
                brandFilter || undefined,
                articleFilter || undefined,
            );
            setData(res);
            setPage(0);
        } catch { }
        setLoading(false);
    };

    useEffect(() => { load(); }, [trendDays, subjectFilter, brandFilter]);

    // Debounced article search
    const [searchTimeout, setSearchTimeout] = useState<any>(null);
    const handleArticleSearch = (val: string) => {
        setArticleFilter(val);
        if (searchTimeout) clearTimeout(searchTimeout);
        setSearchTimeout(setTimeout(() => load(), 500));
    };

    if (subView === 'warehouses') return <WarehouseStocksView />;
    if (subView === 'need') return <WarehouseNeedView />;
    if (subView === 'settings') return <WarehouseExclusionSettings />;

    if (loading && !data) return <div className="glass-card" style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-muted)' }}>Загрузка аналитики остатков...</div>;

    if (!data || !data.articles) return <div className="glass-card"><div className="empty-state"><div className="empty-state-text">Нет данных. Синхронизируйте воронку продаж.</div></div></div>;

    const filteredArticles = trafficFilter
        ? data.articles.filter((a: any) => a.traffic_light === trafficFilter)
        : data.articles;

    const totalPages = Math.ceil(filteredArticles.length / perPage);
    const pageArticles = filteredArticles.slice(page * perPage, (page + 1) * perPage);

    const tl = data.traffic_light || { red: 0, orange: 0, yellow: 0, green: 0 };

    const trafficColors: Record<string, { bg: string; text: string; label: string }> = {
        red: { bg: '#ff4444', text: '#fff', label: `< 7 дн (критично) ${tl.red}` },
        orange: { bg: '#ff9800', text: '#fff', label: `8–14 дн (опасно) ${tl.orange}` },
        yellow: { bg: '#ffd600', text: '#333', label: `15–29 дн (норма) ${tl.yellow}` },
        green: { bg: '#4caf50', text: '#fff', label: `≥ 30 дн (избыток) ${tl.green}` },
    };

    const shortDate = (d: string) => {
        const dt = new Date(d);
        const days = ['ВС', 'ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ'];
        return `${days[dt.getDay()]}, ${String(dt.getDate()).padStart(2, '0')}.${String(dt.getMonth() + 1).padStart(2, '0')}`;
    };

    const daysLeftBg = (dl: number) => {
        if (dl < 7) return '#ff4444';
        if (dl <= 14) return '#ff9800';
        if (dl <= 29) return '#ffd600';
        return '#4caf50';
    };

    const daysLeftColor = (dl: number) => dl <= 14 ? '#fff' : (dl <= 29 ? '#333' : '#fff');

    // Color for forecast cell based on projected stock level
    const forecastCellStyle = (projected: number, avgDaily: number) => {
        if (projected <= 0) return { color: '#ff4444', background: 'rgba(255,68,68,0.12)', fontWeight: 700 };
        const daysOfStock = avgDaily > 0 ? projected / avgDaily : 999;
        if (daysOfStock < 7) return { color: '#ff4444', background: 'rgba(255,68,68,0.06)' };
        if (daysOfStock < 14) return { color: '#ff9800' };
        return {};
    };

    const handleExport = () => {
        const rows = filteredArticles.map((a: any) => {
            const row: Record<string, any> = {
                'Артикул': a.vendor_code,
                'Предмет': a.subject,
                'Бренд': a.brand,
                'Заказы 30д': a.orders_30d,
                'Тренд %': a.trend_pct,
                [`Ср ${trendDays}д`]: a.avg_daily,
                'Остатки': a.stocks_wb,
                'Хватит дн': a.days_left,
            };
            (data.dates || []).forEach((d: string, i: number) => {
                row[d] = (a.forecast || [])[i] ?? 0;
            });
            return row;
        });
        exportToExcel(rows, `stock_forecast_${trendDays}d`);
    };

    return (
        <div>
            {/* Header + Sub-view toggle */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div>
                    <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>📦 Аналитика остатков</h2>
                    <span style={{ fontSize: 13, opacity: 0.6 }}>{data.total_articles} артикулов · данные на {data.data_date}</span>
                </div>
                <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                    <span style={{ fontSize: 13, opacity: 0.6, marginRight: 8 }}>Горизонт прогноза</span>
                    {[7, 14, 30].map(d => (
                        <button key={d} className={`btn btn-sm ${trendDays === d ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setTrendDays(d)}>{d}д</button>
                    ))}
                </div>
            </div>

            {/* Sub-view toggle */}
            <div style={{ display: 'flex', gap: 4, marginBottom: 16 }}>
                {[
                    { key: 'articles' as const, label: '📋 По артикулам' },
                    { key: 'warehouses' as const, label: '🏭 По складам' },
                    { key: 'need' as const, label: '📦 Потребность' },
                    { key: 'settings' as const, label: '⚙️ Настройки' },
                ].map(v => (
                    <button key={v.key}
                        className={`btn btn-sm ${subView === v.key ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setSubView(v.key)}>{v.label}</button>
                ))}
            </div>

            {/* Filters */}
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                <select className="input" style={{ maxWidth: 180, fontSize: 13 }} value={subjectFilter} onChange={e => setSubjectFilter(e.target.value)}>
                    <option value="">Предмет: Все</option>
                    {(data.subjects || []).map((s: string) => <option key={s} value={s}>{s}</option>)}
                </select>
                <select className="input" style={{ maxWidth: 180, fontSize: 13 }} value={brandFilter} onChange={e => setBrandFilter(e.target.value)}>
                    <option value="">Бренд: Все</option>
                    {(data.brands || []).map((b: string) => <option key={b} value={b}>{b}</option>)}
                </select>
                <input className="input" style={{ maxWidth: 200, fontSize: 13 }} placeholder="🔍 Поиск артикула" value={articleFilter}
                    onChange={e => handleArticleSearch(e.target.value)} />
                <button className="btn btn-sm btn-secondary" onClick={handleExport} style={{ marginLeft: 'auto' }}>📥 Excel</button>
            </div>

            {/* KPI Cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
                <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '4px solid var(--color-primary)' }}>
                    <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 4 }}>ЗАКАЗЫ ЗА 30 ДНЕЙ</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{formatNumber(data.orders_30d)}</div>
                    <div style={{ fontSize: 12, opacity: 0.5, marginTop: 2 }}>~ {data.total_articles} артикулов</div>
                </div>
                <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '4px solid #4caf50' }}>
                    <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 4 }}>СРЕДНЕЕ ЗА {trendDays} ДНЕЙ</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{formatNumber(data.avg_daily)}</div>
                    <div style={{ fontSize: 12, opacity: 0.5, marginTop: 2 }}>заказов/день (сумма)</div>
                </div>
                <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '4px solid #ff4444' }}>
                    <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 4 }}>КРИТИЧЕСКИХ ОСТАТКОВ</div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: tl.red > 0 ? '#ff4444' : undefined }}>{tl.red}</div>
                    <div style={{ fontSize: 12, opacity: 0.5, marginTop: 2 }}>≤ 7 дней до обнуления</div>
                </div>
                <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '4px solid #ff9800' }}>
                    <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 4 }}>САМЫЙ КРИТИЧНЫЙ</div>
                    <div style={{ fontSize: 16, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {data.most_critical ? data.most_critical.article : '—'}
                    </div>
                    <div style={{ fontSize: 12, opacity: 0.5, marginTop: 2 }}>
                        {data.most_critical ? `${data.most_critical.days_left} дн до обнуления` : 'нет критичных'}
                    </div>
                </div>
            </div>

            {/* Traffic light filter pills */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, opacity: 0.6 }}>Светофор запасов</span>
                <button className={`btn btn-sm ${!trafficFilter ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setTrafficFilter(null)}>Все ({data.total_articles})</button>
                {Object.entries(trafficColors).map(([key, c]) => (
                    <button key={key}
                        style={{
                            padding: '4px 12px', borderRadius: 12, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: 'none',
                            background: trafficFilter === key ? c.bg : 'var(--color-bg-tertiary)',
                            color: trafficFilter === key ? c.text : 'var(--text-primary)',
                            opacity: trafficFilter === key ? 1 : 0.7,
                        }}
                        onClick={() => setTrafficFilter(trafficFilter === key ? null : key)}>
                        {c.label}
                    </button>
                ))}
            </div>

            {/* Table */}
            <div className="glass-card" style={{ overflow: 'auto', padding: 0 }}>
                <table className="data-table" style={{ fontSize: 12, width: '100%' }}>
                    <thead>
                        <tr>
                            <th style={{ position: 'sticky', left: 0, background: 'var(--color-bg-secondary)', zIndex: 2, minWidth: 170 }}>АРТИКУЛ</th>
                            <th style={{ textAlign: 'right', minWidth: 80 }}>ЗАКАЗЫ 30Д</th>
                            <th style={{ textAlign: 'right', minWidth: 65 }}>ТРЕНД</th>
                            <th style={{ textAlign: 'right', minWidth: 60 }}>СР {trendDays}Д</th>
                            <th style={{ textAlign: 'right', minWidth: 65 }}>ОСТАТКИ</th>
                            <th style={{ textAlign: 'center', minWidth: 75 }}>ХВАТИТ ДН</th>
                            {(data.dates || []).map((d: string) => (
                                <th key={d} style={{ textAlign: 'right', minWidth: 60, fontSize: 10, whiteSpace: 'nowrap' }}>{shortDate(d)}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {/* ИТОГО row */}
                        <tr style={{ fontWeight: 700, background: 'var(--color-bg-tertiary)' }}>
                            <td style={{ position: 'sticky', left: 0, background: 'var(--color-bg-tertiary)', zIndex: 1 }}>ИТОГО</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(filteredArticles.reduce((s: number, a: any) => s + a.orders_30d, 0))}</td>
                            <td></td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(filteredArticles.reduce((s: number, a: any) => s + a.avg_daily, 0))}</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(filteredArticles.reduce((s: number, a: any) => s + a.stocks_wb, 0))}</td>
                            <td></td>
                            {(data.dates || []).map((d: string, idx: number) => {
                                const dayTotal = filteredArticles.reduce((s: number, a: any) => {
                                    return s + ((a.forecast || [])[idx] ?? 0);
                                }, 0);
                                return <td key={d} style={{ textAlign: 'right' }}>{formatNumber(dayTotal, 0)}</td>;
                            })}
                        </tr>
                        {pageArticles.map((a: any) => (
                            <tr key={a.nm_id} style={{ transition: 'background 0.15s' }}
                                onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-bg-tertiary)')}
                                onMouseLeave={e => (e.currentTarget.style.background = '')}>
                                <td style={{ position: 'sticky', left: 0, background: 'inherit', zIndex: 1, fontWeight: 600 }}>
                                    {a.vendor_code || `#${a.nm_id}`}
                                </td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(a.orders_30d)}</td>
                                <td style={{ textAlign: 'right', color: a.trend_pct > 0 ? '#4caf50' : a.trend_pct < 0 ? '#ff4444' : undefined }}>
                                    {a.trend_pct > 0 ? '↑' : a.trend_pct < 0 ? '↓' : ''}{a.trend_pct}%
                                </td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_daily)}</td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(a.stocks_wb)}</td>
                                <td style={{ textAlign: 'center' }}>
                                    <span style={{
                                        display: 'inline-block', padding: '2px 8px', borderRadius: 8,
                                        fontWeight: 700, fontSize: 11, minWidth: 30,
                                        background: daysLeftBg(a.days_left), color: daysLeftColor(a.days_left),
                                    }}>{a.days_left}</span>
                                </td>
                                {(data.dates || []).map((d: string, idx: number) => {
                                    const projected = (a.forecast || [])[idx] ?? 0;
                                    const style = forecastCellStyle(projected, a.avg_daily);
                                    return (
                                        <td key={d} style={{
                                            textAlign: 'right',
                                            ...style,
                                        }}>
                                            {formatNumber(projected, 0)}
                                        </td>
                                    );
                                })}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {/* Pagination */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, fontSize: 13 }}>
                <div style={{ opacity: 0.6 }}>
                    {filteredArticles.length} артикулов
                    {[25, 50, 100].map(n => (
                        <button key={n} className={`btn btn-sm ${perPage === n ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ marginLeft: 4, padding: '2px 8px', fontSize: 11 }}
                            onClick={() => { setPerPage(n); setPage(0); }}>{n}</button>
                    ))}
                    <span style={{ marginLeft: 4 }}>строк</span>
                </div>
                <div style={{ display: 'flex', gap: 4 }}>
                    <button className="btn btn-sm btn-secondary" disabled={page === 0} onClick={() => setPage(p => p - 1)}>←</button>
                    {Array.from({ length: Math.min(totalPages, 5) }, (_, i) => {
                        const p = page < 3 ? i : page - 2 + i;
                        if (p >= totalPages) return null;
                        return <button key={p} className={`btn btn-sm ${page === p ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setPage(p)}>{p + 1}</button>;
                    })}
                    <button className="btn btn-sm btn-secondary" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>→</button>
                </div>
            </div>
        </div>
    );
}

/* ─────────── Warehouse Stocks View ─────────── */
function WarehouseStocksView() {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [syncing, setSyncing] = useState(false);

    const load = async () => {
        setLoading(true);
        try { setData(await api.getWarehouseStocks()); } catch { }
        setLoading(false);
    };

    const sync = async () => {
        setSyncing(true);
        try {
            const res = await api.syncWarehouseStocks();
            alert(`Синхронизировано ${res.synced} записей`);
            await load();
        } catch (e: any) {
            alert(e?.message || 'Ошибка синхронизации');
        }
        setSyncing(false);
    };

    useEffect(() => { load(); }, []);

    if (loading && !data) return <div className="glass-card" style={{ textAlign: 'center', padding: 40 }}>Загрузка складов...</div>;

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div>
                    <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>🏭 Остатки по складам</h2>
                    <span style={{ fontSize: 13, opacity: 0.6 }}>
                        {data ? `${data.total_warehouses} складов · ${formatNumber(data.total_qty)} шт` : 'Нет данных'}
                    </span>
                </div>
                <button className="btn btn-sm btn-primary" onClick={sync} disabled={syncing}>
                    {syncing ? '⏳ Синхронизация...' : '🔄 Синхронизировать с WB'}
                </button>
            </div>

            {data && data.warehouses && data.warehouses.length > 0 ? (
                <div className="glass-card" style={{ overflowX: 'auto' }}>
                    <table className="data-table" style={{ width: '100%', fontSize: 13 }}>
                        <thead>
                            <tr>
                                <th style={{ textAlign: 'left' }}>Склад</th>
                                <th style={{ textAlign: 'right' }}>Остаток (шт)</th>
                                <th style={{ textAlign: 'right' }}>Артикулов</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr style={{ fontWeight: 700, background: 'rgba(0,0,0,0.03)' }}>
                                <td>ИТОГО</td>
                                <td style={{ textAlign: 'right' }}>{formatNumber(data.total_qty)}</td>
                                <td style={{ textAlign: 'right' }}>{data.total_warehouses}</td>
                            </tr>
                            {data.warehouses.map((wh: any) => (
                                <tr key={wh.name}>
                                    <td>{wh.name}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(wh.total_qty)}</td>
                                    <td style={{ textAlign: 'right' }}>{wh.articles_count}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">Нет данных по складам. Нажмите «Синхронизировать с WB».</div>
                    </div>
                </div>
            )}
        </div>
    );
}

/* ─────────── Warehouse Need View ─────────── */
function WarehouseNeedView() {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [supplyDays, setSupplyDays] = useState(14);
    const [analysisDays, setAnalysisDays] = useState(14);
    const [mode, setMode] = useState<'actual' | 'hypothetical'>('actual');
    const [brandFilter, setBrandFilter] = useState('');
    const [subjectFilter, setSubjectFilter] = useState('');
    const [sortCol, setSortCol] = useState<string>('total_need');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

    const load = async () => {
        setLoading(true);
        try { setData(await api.getStockNeed(supplyDays, analysisDays, mode)); } catch { }
        setLoading(false);
    };

    useEffect(() => { load(); }, [supplyDays, analysisDays, mode]);

    // Compute per-article total need & per-warehouse need
    const getArticleNeed = (a: any, whName?: string) => {
        if (!data?.warehouses) return 0;
        if (whName) return data.warehouses.find((w: any) => w.name === whName)?.articles?.[a.nm_id]?.need || 0;
        let total = 0;
        data.warehouses.forEach((wh: any) => { total += wh.articles?.[a.nm_id]?.need || 0; });
        return total;
    };

    // Filter articles
    const filteredArticles = (data?.articles || []).filter((a: any) => {
        if (brandFilter && a.brand !== brandFilter) return false;
        if (subjectFilter && a.subject !== subjectFilter) return false;
        return true;
    });

    // Sort articles
    const sortedArticles = [...filteredArticles].sort((a: any, b: any) => {
        let va: any, vb: any;
        if (sortCol === 'vendor_code') { va = a.vendor_code; vb = b.vendor_code; }
        else if (sortCol === 'total_need') { va = getArticleNeed(a); vb = getArticleNeed(b); }
        else { va = getArticleNeed(a, sortCol); vb = getArticleNeed(b, sortCol); }
        if (typeof va === 'string') return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        return sortDir === 'asc' ? va - vb : vb - va;
    });

    // Warehouse totals (sum of need for filtered articles)
    const getWhTotal = (whName: string) => {
        let sum = 0;
        filteredArticles.forEach((a: any) => { sum += getArticleNeed(a, whName); });
        return sum;
    };

    const grandTotal = (() => {
        let sum = 0;
        filteredArticles.forEach((a: any) => { sum += getArticleNeed(a); });
        return sum;
    })();

    // Sort handler
    const handleSort = (col: string) => {
        if (sortCol === col) setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
        else { setSortCol(col); setSortDir('desc'); }
    };

    const sortIcon = (col: string) => sortCol === col ? (sortDir === 'asc' ? ' ↑' : ' ↓') : '';

    // Excel export
    const handleExport = () => {
        if (!data) return;
        const whs = data.warehouses || [];
        const header = ['Артикул', 'Бренд', 'Категория', 'Потребность', ...whs.map((w: any) => w.name)];
        const rows = sortedArticles.map((a: any) => [
            a.vendor_code, a.brand || '', a.subject || '',
            getArticleNeed(a),
            ...whs.map((wh: any) => getArticleNeed(a, wh.name) || 0),
        ]);
        const totalRow = ['ИТОГО', '', '', grandTotal, ...whs.map((w: any) => getWhTotal(w.name))];
        rows.push(totalRow);
        exportToExcel([header, ...rows], `Потребность_запас${supplyDays}д_анализ${analysisDays}д`);
    };

    if (loading && !data) return <div className="glass-card" style={{ textAlign: 'center', padding: 40 }}>Расчёт потребности...</div>;

    const modeLabel = mode === 'actual' ? 'Фактический' : 'Гипотетический';

    const thStyle: any = { textAlign: 'right', minWidth: 85, cursor: 'pointer', userSelect: 'none', fontSize: 11, whiteSpace: 'nowrap', padding: '10px 8px', borderBottom: '2px solid var(--color-border)' };
    const thStickyStyle: any = { ...thStyle, textAlign: 'left', position: 'sticky', left: 0, background: 'var(--color-bg)', zIndex: 2, minWidth: 180 };
    const tdStyle: any = { padding: '8px', textAlign: 'right', fontSize: 12, borderBottom: '1px solid var(--color-border)' };
    const tdStickyStyle: any = { ...tdStyle, textAlign: 'left', fontWeight: 600, position: 'sticky', left: 0, background: 'var(--color-bg)', zIndex: 1 };

    return (
        <div>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
                <div>
                    <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>📦 Потребность по складам</h2>
                    <span style={{ fontSize: 13, opacity: 0.6 }}>
                        {data ? `${data.total_warehouses} складов · ${filteredArticles.length} артикулов · запас ${supplyDays} дн · анализ ${analysisDays} дн · ${modeLabel}` : 'Нет данных'}
                    </span>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    {/* Brand filter */}
                    {data?.brands?.length > 0 && (
                        <select value={brandFilter} onChange={e => setBrandFilter(e.target.value)}
                            style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg)', fontSize: 12 }}>
                            <option value="">Все бренды</option>
                            {data.brands.map((b: string) => <option key={b} value={b}>{b}</option>)}
                        </select>
                    )}
                    {/* Subject filter */}
                    {data?.subjects?.length > 0 && (
                        <select value={subjectFilter} onChange={e => setSubjectFilter(e.target.value)}
                            style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg)', fontSize: 12 }}>
                            <option value="">Все категории</option>
                            {data.subjects.map((s: string) => <option key={s} value={s}>{s}</option>)}
                        </select>
                    )}
                    {/* Mode toggle */}
                    <div style={{ display: 'flex', gap: 2, background: 'var(--color-border)', borderRadius: 8, padding: 2 }}>
                        <button className={`btn btn-sm ${mode === 'actual' ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ borderRadius: 6, fontSize: 11 }}
                            onClick={() => setMode('actual')}>📊 Факт</button>
                        <button className={`btn btn-sm ${mode === 'hypothetical' ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ borderRadius: 6, fontSize: 11 }}
                            onClick={() => setMode('hypothetical')}>🗺️ Гипотез.</button>
                    </div>
                    {/* Upload order cities (hypothetical only) */}
                    {mode === 'hypothetical' && (
                        <label className="btn btn-sm btn-secondary" style={{ cursor: 'pointer', fontSize: 11 }}
                            title="Загрузить Excel «Лента заказов» из ЛК WB для точного определения городов">
                            📤 Загрузить ленту
                            <input type="file" accept=".xlsx" style={{ display: 'none' }} onChange={async (e) => {
                                const f = e.target.files?.[0];
                                if (!f) return;
                                try {
                                    const result = await api.uploadOrderCities(f);
                                    alert(`✅ Загружено ${result.total_mappings} городов`);
                                    await load();
                                } catch (err: any) {
                                    alert(`❌ Ошибка: ${err.message}`);
                                }
                                e.target.value = '';
                            }} />
                        </label>
                    )}
                    {/* Supply days selector */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ fontSize: 11, opacity: 0.6, whiteSpace: 'nowrap' }}>Запас:</span>
                        {[7, 14, 30, 60].map(d => (
                            <button key={d} className={`btn btn-sm ${supplyDays === d ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setSupplyDays(d)}>{d}д</button>
                        ))}
                    </div>
                    {/* Analysis days selector */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ fontSize: 11, opacity: 0.6, whiteSpace: 'nowrap' }}>Анализ:</span>
                        {[7, 14, 30].map(d => (
                            <button key={d} className={`btn btn-sm ${analysisDays === d ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setAnalysisDays(d)}>{d}д</button>
                        ))}
                    </div>
                    {/* Excel export */}
                    <button className="btn btn-sm btn-secondary" onClick={handleExport} title="Экспорт в Excel">📥 Excel</button>
                </div>
            </div>

            {/* Table */}
            {data && sortedArticles.length > 0 ? (
                <div className="glass-card" style={{ overflowX: 'auto', padding: 0 }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                        <thead>
                            <tr style={{ background: 'var(--color-bg)' }}>
                                <th style={thStickyStyle} onClick={() => handleSort('vendor_code')}>
                                    Артикул{sortIcon('vendor_code')}
                                </th>
                                <th style={thStyle} onClick={() => handleSort('total_need')}>
                                    Потребность{sortIcon('total_need')}
                                </th>
                                {(data.warehouses || []).map((wh: any) => (
                                    <th key={wh.name} style={thStyle} onClick={() => handleSort(wh.name)}>
                                        {wh.name.length > 16 ? wh.name.slice(0, 16) + '…' : wh.name}
                                        {sortIcon(wh.name)}
                                    </th>
                                ))}
                            </tr>
                            {/* Totals row */}
                            <tr style={{ background: 'rgba(var(--color-primary-rgb, 59,130,246), 0.06)', fontWeight: 700 }}>
                                <td style={{ ...tdStickyStyle, fontWeight: 700, background: 'rgba(var(--color-primary-rgb, 59,130,246), 0.06)', borderBottom: '2px solid var(--color-border)' }}>ИТОГО</td>
                                <td style={{ ...tdStyle, fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>{grandTotal > 0 ? formatNumber(grandTotal, 0) : '—'}</td>
                                {(data.warehouses || []).map((wh: any) => {
                                    const t = getWhTotal(wh.name);
                                    return (
                                        <td key={wh.name} style={{ ...tdStyle, fontWeight: 700, borderBottom: '2px solid var(--color-border)', color: t > 0 ? '#ef4444' : 'var(--color-text-muted)' }}>
                                            {t > 0 ? formatNumber(t, 0) : '—'}
                                        </td>
                                    );
                                })}
                            </tr>
                        </thead>
                        <tbody>
                            {sortedArticles.map((a: any) => {
                                const totalNeed = getArticleNeed(a);
                                return (
                                    <tr key={a.nm_id} style={{ transition: 'background 0.15s' }}
                                        onMouseEnter={e => (e.currentTarget.style.background = 'rgba(var(--color-primary-rgb, 59,130,246), 0.03)')}
                                        onMouseLeave={e => (e.currentTarget.style.background = '')}>
                                        <td style={tdStickyStyle}>
                                            <div>{a.vendor_code}</div>
                                            {(a.brand || a.subject) && (
                                                <div style={{ fontSize: 10, opacity: 0.5, fontWeight: 400 }}>
                                                    {[a.brand, a.subject].filter(Boolean).join(' · ')}
                                                </div>
                                            )}
                                        </td>
                                        <td style={{ ...tdStyle, fontWeight: 700, color: totalNeed > 0 ? '#ef4444' : 'var(--color-text-muted)' }}>
                                            {totalNeed > 0 ? formatNumber(totalNeed, 0) : '—'}
                                        </td>
                                        {(data.warehouses || []).map((wh: any) => {
                                            const need = getArticleNeed(a, wh.name);
                                            return (
                                                <td key={wh.name} style={{
                                                    ...tdStyle,
                                                    background: need > 0 ? 'rgba(239,68,68,0.08)' : undefined,
                                                    color: need > 0 ? '#ef4444' : 'var(--color-text-muted)',
                                                    fontWeight: need > 0 ? 600 : 400,
                                                }}>
                                                    {need > 0 ? formatNumber(need, 0) : '—'}
                                                </td>
                                            );
                                        })}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            ) : (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">{data ? 'Нет артикулов по выбранным фильтрам' : 'Нет данных. Сначала синхронизируйте склады (вкладка «По складам»).'}</div>
                    </div>
                </div>
            )}
        </div>
    );
}

// ─── Warehouse Exclusion Settings ────────────────────────────────────────────

function WarehouseExclusionSettings() {
    const [warehouses, setWarehouses] = useState<Array<{ name: string; lat: number; lng: number }>>([]);
    const [excluded, setExcluded] = useState<string[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState('');
    const [hasChanges, setHasChanges] = useState(false);

    useEffect(() => {
        (async () => {
            try {
                const [wh, ex] = await Promise.all([
                    api.getWarehouses(),
                    api.getExcludedWarehouses(),
                ]);
                setWarehouses(wh);
                setExcluded(ex);
            } catch {
                setMsg('Ошибка загрузки складов');
            } finally {
                setLoading(false);
            }
        })();
    }, []);

    const toggle = (name: string) => {
        setExcluded(prev => {
            const next = prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name];
            return next;
        });
        setHasChanges(true);
    };

    const save = async () => {
        setSaving(true);
        setMsg('');
        try {
            await api.setExcludedWarehouses(excluded);
            setMsg('✅ Сохранено');
            setHasChanges(false);
        } catch {
            setMsg('❌ Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    if (loading) return <div className="glass-card" style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-muted)' }}>Загрузка складов...</div>;

    const active = warehouses.filter(w => !excluded.includes(w.name));
    const excludedList = warehouses.filter(w => excluded.includes(w.name));

    return (
        <div>
            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <h3 style={{ margin: '0 0 8px' }}>🏭 Исключение складов</h3>
                <p style={{ color: 'var(--color-text-muted)', margin: '0 0 16px', fontSize: 14 }}>
                    Исключённые склады не участвуют в расчёте потребностей. Заказы из их регионов перераспределяются на ближайшие оставшиеся склады.
                </p>

                <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Активных: <strong>{active.length}</strong> / {warehouses.length}
                    </span>
                    {excluded.length > 0 && (
                        <span style={{ fontSize: 13, color: 'var(--color-danger)', fontWeight: 500 }}>
                            ⛔ Исключено: {excluded.length}
                        </span>
                    )}
                </div>

                <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
                    gap: 8,
                }}>
                    {warehouses.map(w => {
                        const isExcluded = excluded.includes(w.name);
                        return (
                            <label
                                key={w.name}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: 8,
                                    padding: '8px 12px',
                                    borderRadius: 8,
                                    border: `1px solid ${isExcluded ? 'var(--color-danger)' : 'var(--color-border)'}`,
                                    background: isExcluded ? 'rgba(239,68,68,0.08)' : 'var(--color-bg-card)',
                                    cursor: 'pointer',
                                    transition: 'all 0.15s',
                                    opacity: isExcluded ? 0.7 : 1,
                                }}
                            >
                                <input
                                    type="checkbox"
                                    checked={!isExcluded}
                                    onChange={() => toggle(w.name)}
                                    style={{ accentColor: 'var(--color-primary)' }}
                                />
                                <span style={{
                                    fontSize: 14,
                                    textDecoration: isExcluded ? 'line-through' : 'none',
                                    color: isExcluded ? 'var(--color-danger)' : 'var(--color-text)',
                                }}>
                                    {w.name}
                                </span>
                            </label>
                        );
                    })}
                </div>
            </div>

            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                <button
                    className="btn btn-primary"
                    onClick={save}
                    disabled={saving || !hasChanges}
                >
                    {saving ? 'Сохранение...' : '💾 Сохранить'}
                </button>
                {msg && <span style={{ fontSize: 14 }}>{msg}</span>}
            </div>

            {excludedList.length > 0 && (
                <div className="glass-card" style={{ padding: 16, marginTop: 16 }}>
                    <p style={{ fontSize: 13, color: 'var(--color-text-muted)', margin: 0 }}>
                        ⚠️ Исключены: <strong>{excludedList.map(w => w.name).join(', ')}</strong>.
                        Заказы из этих регионов будут распределены на ближайшие активные склады.
                    </p>
                </div>
            )}
        </div>
    );
}
