'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';

export function PlanIncomes() {
    const [data, setData] = useState<any[]>([]);
    const [msg, setMsg] = useState('');
    const [refreshing, setRefreshing] = useState(false);
    const [trendDays, setTrendDays] = useState(7);
    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getPlanningIncomes()); } catch { } };
    const del = async (id: number) => {
        if (!confirm('Удалить?')) return;
        try { await api.deletePlanningIncome(id); load(); } catch (e: any) { setMsg(e.message); }
    };
    const refreshForecast = async (days: number) => {
        setTrendDays(days); setRefreshing(true); setMsg('');
        try {
            const res = await api.refreshWbForecast(days);
            const pattern = res.weekday_pattern ? Object.entries(res.weekday_pattern).map(([d, v]) => `${d}: ${formatNumber(v as number)}`).join(', ') : '';
            setMsg(`✅ Тренд ${days}д: создано ${res.created} записей. Среднее/день: ${formatNumber(res.daily_avg)} ₽. Паттерн: ${pattern}`);
            load();
        } catch (e: any) { setMsg(e.message); }
        setRefreshing(false);
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Поступления WB</h3>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <span style={{ fontSize: 12, opacity: 0.7, marginRight: 4 }}>Тренд:</span>
                    {[7, 14, 30].map(d => (
                        <button key={d} className={`btn btn-sm ${d === trendDays ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => refreshForecast(d)} disabled={refreshing} style={{ minWidth: 50 }}>
                            {refreshing && d === trendDays ? '⏳' : `${d}д`}
                        </button>
                    ))}
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'plan_incomes')}>📥 Excel</button>
                </div>
            </div>
            {msg && <div style={{ color: msg.startsWith('✅') ? 'var(--color-success, #4ade80)' : 'var(--color-danger)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}
            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr>{Object.keys(data[0]).map(k => <th key={k}>{k}</th>)}</tr></thead>
                        <tbody>{data.map((r, i) => <tr key={i}>{Object.entries(r).map(([k, v]: any, j) => (
                            <td key={j}>{k === 'id' ? <><span>{v}</span> <button className="btn btn-danger btn-sm" style={{ padding: '1px 6px', fontSize: 10, marginLeft: 4 }} onClick={() => del(v)}>✕</button></> : typeof v === 'number' ? formatNumber(v) : v ?? '—'}</td>
                        ))}</tr>)}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет поступлений</div></div>}
        </div>
    );
}

export function WbPayouts() {
    const [data, setData] = useState<any[]>([]);
    const [msg, setMsg] = useState('');
    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getWbPayouts()); } catch { } };
    const del = async (id: number) => {
        if (!confirm('Удалить?')) return;
        try { await api.deleteWbPayout(id); load(); } catch (e: any) { setMsg(e.message); }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>WB Payouts</h3>
                <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'wb_payouts')}>📥 Excel</button>
            </div>
            {msg && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}
            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr>{Object.keys(data[0]).map(k => <th key={k}>{k}</th>)}</tr></thead>
                        <tbody>{data.map((r, i) => <tr key={i}>{Object.entries(r).map(([k, v]: any, j) => (
                            <td key={j}>{k === 'id' ? <><span>{v}</span> <button className="btn btn-danger btn-sm" style={{ padding: '1px 6px', fontSize: 10, marginLeft: 4 }} onClick={() => del(v)}>✕</button></> : typeof v === 'number' ? formatNumber(v) : v ?? '—'}</td>
                        ))}</tr>)}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет WB payouts</div></div>}
        </div>
    );
}

export function Cashflow() {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    useEffect(() => { (async () => { try { setData(await api.getCashflowDaily()); } catch { } setLoading(false); })(); }, []);

    if (loading) return <div style={{ padding: 20, color: 'var(--color-text-muted)' }}>Загрузка...</div>;
    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Кэшфлоу (план по дням)</h3>
                {data.length > 0 && <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'cashflow_daily')}>📥 Excel</button>}
            </div>
            {data.length > 0 ? (
                <div style={{ overflowX: 'auto', maxHeight: 600, overflowY: 'auto' }}>
                    <table className="data-table">
                        <thead><tr>{Object.keys(data[0]).map(k => <th key={k} style={{ fontSize: 11 }}>{k}</th>)}</tr></thead>
                        <tbody>{data.map((r, i) => <tr key={i}>{Object.values(r).map((v: any, j) => <td key={j} style={{ fontSize: 12 }}>{typeof v === 'number' ? formatNumber(v) : v ?? '—'}</td>)}</tr>)}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет данных кэшфлоу</div></div>}
        </div>
    );
}

export function CustomsDt() {
    const [data, setData] = useState<any[]>([]);
    const [topups, setTopups] = useState<any[]>([]);
    const [orders, setOrders] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState<number | null>(null);

    useEffect(() => { loadAll(); }, []);
    const loadAll = async () => {
        try {
            const [dtList, topupList, orderList] = await Promise.all([api.getCustomsDt(), api.getCustomsTopup(), api.getCostOrders()]);
            setData(dtList); setTopups(topupList); setOrders(orderList);
        } catch { }
        setLoading(false);
    };

    const orderNoToInt = (s: string): number => {
        s = s.trim();
        if (s.includes('/')) { const parts = s.split('/'); const a = parseInt(parts[0], 10); const b = parseInt(parts[1], 10); if (!isNaN(a) && !isNaN(b)) return a * 100 + b; }
        const n = parseInt(s, 10); return isNaN(n) ? 0 : n;
    };

    const handleBindOrder = async (dtId: number, orderNo: string) => {
        setSaving(dtId);
        try { const encoded = orderNo ? Number(orderNo) : null; await api.updateCustomsDt(dtId, { order_no: encoded }); setData(prev => prev.map(d => d.id === dtId ? { ...d, order_no: encoded } : d)); } catch { }
        setSaving(null);
    };

    if (loading) return <div style={{ padding: 20, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    const totalTopup = topups.reduce((s: number, t: any) => s + Number(t.amount_rub || 0), 0);
    const totalDt = data.reduce((s: number, d: any) => s + Number(d.amount_rub || 0), 0);
    const boundDt = data.filter((d: any) => d.order_no).reduce((s: number, d: any) => s + Number(d.amount_rub || 0), 0);
    const unboundDt = totalDt - boundDt;
    const balance = totalTopup - totalDt;

    const cards = [
        { label: 'Перевели на таможню', value: totalTopup, icon: '💰', color: '#818cf8' },
        { label: 'Привязано к заказам', value: boundDt, icon: '✅', color: '#10b981' },
        { label: 'Не привязано', value: unboundDt, icon: '⏳', color: '#f59e0b' },
        { label: 'Остаток на счёте', value: balance, icon: '🏦', color: balance >= 0 ? '#10b981' : '#ef4444' },
    ];

    return (
        <div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
                {cards.map((c, i) => (
                    <div key={i} className="glass-card" style={{ padding: '14px 16px', textAlign: 'center' }}>
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>{c.icon} {c.label}</div>
                        <div style={{ fontSize: 20, fontWeight: 700, color: c.color }}>{formatNumber(c.value)} ₽</div>
                    </div>
                ))}
            </div>
            <div className="glass-card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Таможенные ДТ</h3>
                    {data.length > 0 && <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'customs_dt')}>📥 Excel</button>}
                </div>
                {data.length > 0 ? (
                    <div style={{ overflowX: 'auto' }}>
                        <table className="data-table">
                            <thead><tr><th style={{ fontSize: 11 }}>№ ДТ</th><th style={{ fontSize: 11 }}>Дата</th><th style={{ fontSize: 11 }}>Сумма ₽</th><th style={{ fontSize: 11 }}>Заказ</th><th style={{ fontSize: 11 }}>Примечание</th></tr></thead>
                            <tbody>{data.map((r: any) => (
                                <tr key={r.id}>
                                    <td style={{ fontSize: 12, fontFamily: 'monospace' }}>{r.dt_number}</td>
                                    <td style={{ fontSize: 12 }}>{r.dt_date}</td>
                                    <td style={{ fontSize: 12, fontWeight: 600 }}>{formatNumber(r.amount_rub)}</td>
                                    <td style={{ fontSize: 12 }}>
                                        <select value={r.order_no ?? ''} onChange={e => handleBindOrder(r.id, e.target.value)} disabled={saving === r.id}
                                            style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', fontSize: 12, minWidth: 100, cursor: 'pointer' }}>
                                            <option value="">—</option>
                                            {orders.map((o: any) => <option key={o.order_no} value={orderNoToInt(String(o.order_no))}>#{o.order_no}{o.invoice_no ? ` (${o.invoice_no})` : ''}</option>)}
                                        </select>
                                        {saving === r.id && <span style={{ marginLeft: 4 }}>⏳</span>}
                                    </td>
                                    <td style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{r.note ?? '—'}</td>
                                </tr>
                            ))}</tbody>
                        </table>
                    </div>
                ) : <div className="empty-state"><div className="empty-state-text">Нет таможенных ДТ. Загрузите PDF-отчёт ФТС через «Импорт документов».</div></div>}
            </div>
        </div>
    );
}
