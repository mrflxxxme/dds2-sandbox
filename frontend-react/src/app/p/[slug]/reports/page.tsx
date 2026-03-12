'use client';
import React, { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

export default function ReportsPage() {
    const [tab, setTab] = useState<'dds' | 'bdr' | 'balance' | 'fx' | 'customs'>('dds');

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
    const articles = data?.articles || [];
    const brands = data?.brands || [];
    const taxInfo = data?.tax_info || {};

    const pct = (val: number, base: number) => base ? ((val / base) * 100).toFixed(2) + '%' : '—';

    const handleExcel = () => {
        if (!articles.length) return;
        const rows = articles.map((a: any, i: number) => ({
            '№': i + 1,
            'Артикул': a.sa_name,
            'К оплате': a.to_pay,
            'Бренд': a.brand,
            'Категория': a.subject,
            'Арт. МП': a.nm_id,
            'Ср. С/С': a.cost_price || 0,
            'Проч. удерж.': a.other_deduction || 0,
            'Ср. цена до скидок': a.avg_retail_price,
            'Ср. цена продажи': a.avg_sale_price,
            'Реализация': a.realization,
            'Оборач. (дн.)': a.turnover_days || 0,
            'Продажи': a.sales_amount,
            'К перечислению': a.ppvz_for_pay,
            'Возвраты ₽': a.returns_amount,
            'С/С продаж': a.cost_total || 0,
            'Штрафы': a.penalties,
            'Заказы шт': a.orders_count || 0,
            'Заказы ₽': a.orders_sum || 0,
            'Комиссия': a.commission,
            'Возн. ВБ': a.total_wb_reward,
            'Компенсация': a.compensation,
            'Ср. логист.': a.avg_logistics,
            'Кап. по С/С': a.cap_cost || 0,
            'Кап. по розн.': a.cap_retail || 0,
            'GMROI': a.gmroi || 0,
            'GMROI Year': a.gmroi_year || 0,
            'Откз.+возвр. шт': a.ret_qty,
            'Продаж шт': a.sale_qty,
            '% выкупа': a.buyout_pct ? +a.buyout_pct.toFixed(2) : 0,
            'Ср. приб./шт': a.avg_profit_per_item || 0,
            'Налоги': a.tax_total || 0,
            'Нал. база': a.tax_base || 0,
            'Прибыль': a.profit || 0,
            'ROI %': a.roi || 0,
            'Доля выр. %': a.revenue_share || 0,
            'Маржа %': a.margin_pct || 0,
            'Реклама': a.adv_sum || 0,
            'ДРР %': a.drr || 0,
            'ДРР заказы %': a.drr_orders || 0,
            'Плат. приёмка': a.acceptance || 0,
            'Логистика': a.logistics,
            'Хранение': a.storage,
            'ABC приб.': a.abc_profit || '',
            'ABC выр.': a.abc_revenue || '',
        }));
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
                                        <th style={{ position: 'sticky', left: 0, background: '#f9fafb', zIndex: 22, borderRight: '1px solid #e5e7eb', borderBottom: '1px solid #e5e7eb' }}>Артикул</th>
                                        <th>К оплате</th>
                                    <th>Бренд</th>
                                    <th>Категория</th>
                                    <th>Арт. МП</th>
                                    <th>Ср. С/С</th>
                                    <th>Проч. удерж.</th>
                                    <th>Ср. цена до скидок</th>
                                    <th>Ср. цена продажи</th>
                                    <th>Реализация</th>
                                    <th>Оборач. (дн.)</th>
                                    <th>Продажи</th>
                                    <th>К переч.</th>
                                    <th>Возвраты</th>
                                    <th>С/С продаж</th>
                                    <th>Штрафы</th>
                                    <th>Заказы шт</th>
                                    <th>Заказы ₽</th>
                                    <th>Комиссия</th>
                                    <th>Возн. ВБ</th>
                                    <th>Компенсация</th>
                                    <th>Ср. логист.</th>
                                    <th>Кап. по С/С</th>
                                    <th>Кап. по розн.</th>
                                    <th>GMROI</th>
                                    <th>GMROI Year</th>
                                    <th>Откз.+возвр.</th>
                                    <th>Продаж шт</th>
                                    <th>% выкупа</th>
                                    <th>Ср. приб./шт</th>
                                    <th>Налоги</th>
                                    <th>Нал. база</th>
                                    <th style={{ color: '#22c55e' }}>Прибыль</th>
                                    <th>ROI %</th>
                                    <th>Доля выр. %</th>
                                    <th>Маржа %</th>
                                    <th style={{ color: '#f59e0b' }}>Реклама</th>
                                    <th>ДРР %</th>
                                    <th>ДРР заказы %</th>
                                    <th>Плат. приёмка</th>
                                    <th>Логистика</th>
                                    <th>Хранение</th>
                                    <th>ABC приб.</th>
                                    <th>ABC выр.</th>
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

function KpiCard({ label, value, sub, color }: { label: string; value: string; sub: string; color?: string }) {
    return (
        <div className="glass-card" style={{ padding: '14px 16px' }}>
            <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: color || 'var(--text-primary)' }}>{value}</div>
            {sub && <div style={{ fontSize: 12, opacity: 0.5, marginTop: 2 }}>{sub}</div>}
        </div>
    );
}
