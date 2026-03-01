'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

export default function PlanningPage() {
    const [tab, setTab] = useState<'orders' | 'payments' | 'incomes' | 'wb' | 'cashflow' | 'customs' | 'leadtimes'>('orders');

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">📦 Планирование</h1>
                    <p className="page-subtitle">Заказы, платежи, поступления WB, кэшфлоу, таможня</p>
                </div>
            </div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 20, flexWrap: 'wrap' }}>
                {[
                    { key: 'orders' as const, label: '📦 Заказы' },
                    { key: 'payments' as const, label: '💳 Платежи' },
                    { key: 'incomes' as const, label: '📥 Поступления WB' },
                    { key: 'wb' as const, label: '💰 WB Payouts' },
                    { key: 'cashflow' as const, label: '📊 Кэшфлоу' },
                    { key: 'customs' as const, label: '🛃 Таможня' },
                    { key: 'leadtimes' as const, label: '⏱ Lead Times' },
                ].map(t => (
                    <button key={t.key} className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                        onClick={() => setTab(t.key)}>{t.label}</button>
                ))}
            </div>
            {tab === 'orders' && <PlanOrders />}
            {tab === 'payments' && <PlanPayments />}
            {tab === 'incomes' && <PlanIncomes />}
            {tab === 'wb' && <WbPayouts />}
            {tab === 'cashflow' && <Cashflow />}
            {tab === 'customs' && <CustomsDt />}
            {tab === 'leadtimes' && <LeadTimes />}
        </div>
    );
}

function PlanOrders() {
    const [data, setData] = useState<any[]>([]);
    const [expanded, setExpanded] = useState<string | null>(null);
    const [summary, setSummary] = useState<any>(null);
    const [msg, setMsg] = useState('');

    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getPlanningOrders()); } catch { } };
    const del = async (id: number) => {
        if (!confirm('Удалить?')) return;
        try { await api.deletePlanningOrder(id); load(); } catch (e: any) { setMsg(e.message); }
    };
    const showSummary = async (orderNo: string) => {
        if (expanded === orderNo) { setExpanded(null); return; }
        setExpanded(orderNo);
        try { setSummary(await api.getPlanningOrderSummary(orderNo)); } catch { setSummary(null); }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Заказы (Планирование)</h3>
                <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'plan_orders')}>📥 Excel</button>
            </div>
            {msg && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}

            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr><th>№</th><th>Инвойс</th><th>Транспорт</th><th>Отгрузка</th><th>Прибытие</th><th>Кол-во</th><th>Товар ₽</th><th>Доставка ¥</th><th>Доставка ₽</th><th>Пошлина ₽</th><th>НДС ₽</th><th>Итого ₽</th><th></th></tr></thead>
                        <tbody>{data.map(r => (
                            <tr key={r.id} style={{ cursor: 'pointer' }} onClick={() => showSummary(String(r.order_no || r.id))}>
                                <td style={{ fontWeight: 600 }}>{r.order_no ?? r.id}</td>
                                <td>{r.invoice}</td>
                                <td><span className="badge badge-info">{r.transport}</span></td>
                                <td style={{ fontSize: 12 }}>{r.ship_date ? formatDate(r.ship_date) : '—'}</td>
                                <td style={{ fontSize: 12 }}>{r.arrival_date ? formatDate(r.arrival_date) : '—'}</td>
                                <td>{r.total_qty != null ? formatNumber(r.total_qty) : '—'}</td>
                                <td style={{ color: 'var(--color-success)' }}>{r.total_goods_rub != null ? formatNumber(r.total_goods_rub) : '—'}</td>
                                <td>{r.delivery_cny != null ? formatNumber(r.delivery_cny) : '—'}</td>
                                <td>{r.delivery_rub != null ? formatNumber(r.delivery_rub) : '—'}</td>
                                <td>{r.duty_rub != null ? formatNumber(r.duty_rub) : '—'}</td>
                                <td>{r.vat_rub != null ? formatNumber(r.vat_rub) : '—'}</td>
                                <td style={{ fontWeight: 700 }}>{r.total_rub != null ? formatNumber(r.total_rub) : '—'}</td>
                                <td><button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={e => { e.stopPropagation(); del(r.id); }}>✕</button></td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет заказов</div></div>}

            {expanded && summary && (
                <div style={{ marginTop: 16, padding: 16, background: 'var(--color-bg-input)', borderRadius: 8 }}>
                    <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Сводка по заказу #{expanded} (план vs факт)</h4>
                    <pre style={{ fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'pre-wrap' }}>{JSON.stringify(summary, null, 2)}</pre>
                </div>
            )}
        </div>
    );
}

function PlanPayments() {
    const [data, setData] = useState<any[]>([]);
    const [msg, setMsg] = useState('');

    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getPlanningPayments()); } catch { } };
    const del = async (id: number) => {
        if (!confirm('Удалить?')) return;
        try { await api.deletePlanningPayment(id); load(); } catch (e: any) { setMsg(e.message); }
    };
    const markPaid = async (id: number) => {
        try { await api.markPaymentPaid(id); setMsg('✅ Оплачено!'); load(); } catch (e: any) { setMsg(e.message); }
    };
    const sync = async () => {
        try { await api.syncPlanPayments(); setMsg('✅ Синхронизировано!'); load(); } catch (e: any) { setMsg(e.message); }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Платежи</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={sync}>🔄 Синхронизация</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'plan_payments')}>📥 Excel</button>
                </div>
            </div>
            {msg && <div style={{ color: 'var(--color-success)', fontSize: 13, marginBottom: 8 }}>{msg}<span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span></div>}

            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr><th>ID</th><th>Заказ</th><th>Тип</th><th>Дата</th><th>Сумма</th><th>Валюта</th><th>Статус</th><th>Описание</th><th></th></tr></thead>
                        <tbody>{data.map(r => (
                            <tr key={r.id}>
                                <td>{r.id}</td>
                                <td>{r.order_no || '—'}</td>
                                <td>{r.payment_type || '—'}</td>
                                <td style={{ fontSize: 12 }}>{r.planned_date ? formatDate(r.planned_date) : '—'}</td>
                                <td style={{ fontWeight: 600, color: (r.amount || 0) < 0 ? 'var(--color-danger)' : 'var(--color-success)' }}>{formatNumber(r.amount)}</td>
                                <td><span className={`badge badge-${r.currency === 'RUB' ? 'success' : 'warning'}`}>{r.currency}</span></td>
                                <td>
                                    <span className={`badge badge-${r.is_paid ? 'success' : 'warning'}`}>
                                        {r.is_paid ? '✓ Оплачен' : '⏳ Ожидает'}
                                    </span>
                                </td>
                                <td style={{ fontSize: 12, color: 'var(--color-text-dim)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.description}</td>
                                <td style={{ display: 'flex', gap: 4 }}>
                                    {!r.is_paid && <button className="btn btn-primary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => markPaid(r.id)}>✓</button>}
                                    <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(r.id)}>✕</button>
                                </td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет платежей</div></div>}
        </div>
    );
}

function PlanIncomes() {
    const [data, setData] = useState<any[]>([]);
    const [msg, setMsg] = useState('');
    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getPlanningIncomes()); } catch { } };
    const del = async (id: number) => {
        if (!confirm('Удалить?')) return;
        try { await api.deletePlanningIncome(id); load(); } catch (e: any) { setMsg(e.message); }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Поступления WB</h3>
                <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'plan_incomes')}>📥 Excel</button>
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
            ) : <div className="empty-state"><div className="empty-state-text">Нет поступлений</div></div>}
        </div>
    );
}

function WbPayouts() {
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

function Cashflow() {
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

function CustomsDt() {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    useEffect(() => { (async () => { try { setData(await api.getCustomsDt()); } catch { } setLoading(false); })(); }, []);

    if (loading) return <div style={{ padding: 20, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Таможенные ДТ</h3>
                {data.length > 0 && <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'customs_dt')}>📥 Excel</button>}
            </div>
            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr>{Object.keys(data[0]).map(k => <th key={k} style={{ fontSize: 11 }}>{k}</th>)}</tr></thead>
                        <tbody>{data.map((r, i) => <tr key={i}>{Object.values(r).map((v: any, j) => <td key={j} style={{ fontSize: 12 }}>{typeof v === 'number' ? formatNumber(v) : v ?? '—'}</td>)}</tr>)}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет таможенных ДТ</div></div>}
        </div>
    );
}

function LeadTimes() {
    const [data, setData] = useState<any[]>([]);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ transport: 'AUTO', stage: '', days: 0 });
    const [msg, setMsg] = useState('');

    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getLeadTimes()); } catch { } };
    const save = async () => {
        try { await api.upsertLeadTime({ ...form, days: parseInt(String(form.days)) }); setMsg('✅ Сохранено!'); setShowForm(false); load(); } catch (e: any) { setMsg(e.message); }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Lead Times</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'lead_times')}>📥 Excel</button>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Добавить</button>
                </div>
            </div>
            {msg && <div style={{ color: 'var(--color-success)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}

            {showForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                    <div className="form-group"><label className="form-label">Транспорт</label>
                        <select className="form-input" value={form.transport} onChange={e => setForm({ ...form, transport: e.target.value })}>
                            <option>AUTO</option><option>RAIL</option><option>AIR</option><option>SEA</option>
                        </select>
                    </div>
                    <div className="form-group"><label className="form-label">Этап</label><input className="form-input" value={form.stage} onChange={e => setForm({ ...form, stage: e.target.value })} /></div>
                    <div className="form-group"><label className="form-label">Дней</label><input className="form-input" type="number" value={form.days} onChange={e => setForm({ ...form, days: parseInt(e.target.value) || 0 })} /></div>
                    <div><button className="btn btn-primary btn-sm" onClick={save}>💾 Сохранить</button></div>
                </div>
            )}

            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr>{Object.keys(data[0]).map(k => <th key={k}>{k}</th>)}</tr></thead>
                        <tbody>{data.map((r, i) => <tr key={i}>{Object.values(r).map((v: any, j) => <td key={j}>{typeof v === 'number' ? formatNumber(v) : v ?? '—'}</td>)}</tr>)}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет данных</div></div>}
        </div>
    );
}
