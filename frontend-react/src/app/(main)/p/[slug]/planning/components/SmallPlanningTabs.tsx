'use client';
import { useEffect, useState, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

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

    const autoColumns: Column[] = useMemo(() => {
        if (data.length === 0) return [];
        return Object.keys(data[0]).map(k => ({
            key: k,
            label: k,
            render: (v: any, row: any) => {
                if (k === 'id') return (
                    <>
                        <span>{v}</span>
                        <button className="btn btn-danger btn-sm" style={{ padding: '1px 6px', fontSize: 10, marginLeft: 4 }} onClick={() => del(v)}>✕</button>
                    </>
                );
                return typeof v === 'number' ? formatNumber(v) : v ?? '—';
            },
        }));
    }, [data]);

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
                </div>
            </div>
            {msg && <div style={{ color: msg.startsWith('✅') ? 'var(--color-success, #4ade80)' : 'var(--color-danger)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}
            {data.length > 0 ? (
                <TanStackDataTable
                    columns={autoColumns}
                    data={data}
                    enableSorting
                    enablePagination
                    pageSize={50}
                    exportName="plan_incomes"
                />
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

    const autoColumns: Column[] = useMemo(() => {
        if (data.length === 0) return [];
        return Object.keys(data[0]).map(k => ({
            key: k,
            label: k,
            render: (v: any) => {
                if (k === 'id') return (
                    <>
                        <span>{v}</span>
                        <button className="btn btn-danger btn-sm" style={{ padding: '1px 6px', fontSize: 10, marginLeft: 4 }} onClick={() => del(v)}>✕</button>
                    </>
                );
                return typeof v === 'number' ? formatNumber(v) : v ?? '—';
            },
        }));
    }, [data]);

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>WB Payouts</h3>
            </div>
            {msg && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}
            {data.length > 0 ? (
                <TanStackDataTable
                    columns={autoColumns}
                    data={data}
                    enableSorting
                    enablePagination
                    pageSize={50}
                    exportName="wb_payouts"
                />
            ) : <div className="empty-state"><div className="empty-state-text">Нет WB payouts</div></div>}
        </div>
    );
}

export function Cashflow() {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    useEffect(() => { (async () => { try { setData(await api.getCashflowDaily()); } catch { } setLoading(false); })(); }, []);

    const autoColumns: Column[] = useMemo(() => {
        if (data.length === 0) return [];
        return Object.keys(data[0]).map(k => ({
            key: k,
            label: k,
            render: (v: any) => typeof v === 'number' ? formatNumber(v) : v ?? '—',
        }));
    }, [data]);

    if (loading) return <div style={{ padding: 20, color: 'var(--color-text-muted)' }}>Загрузка...</div>;
    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Кэшфлоу (план по дням)</h3>
            </div>
            {data.length > 0 ? (
                <TanStackDataTable
                    columns={autoColumns}
                    data={data}
                    enableSorting
                    enablePagination
                    pageSize={50}
                    exportName="cashflow_daily"
                    maxHeight={600}
                />
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

    const columns: Column[] = [
        { key: 'dt_number', label: '№ ДТ', render: (v: any) => <span style={{ fontSize: 12, fontFamily: 'monospace' }}>{v}</span> },
        { key: 'dt_date', label: 'Дата', render: (v: any) => <span style={{ fontSize: 12 }}>{v}</span> },
        { key: 'amount_rub', label: 'Сумма ₽', render: (v: any) => <span style={{ fontSize: 12, fontWeight: 600 }}>{formatNumber(v)}</span> },
        {
            key: 'order_no', label: 'Заказ',
            sortable: false,
            render: (_v: any, row: any) => (
                <span style={{ fontSize: 12 }}>
                    <select value={row.order_no ?? ''} onChange={e => handleBindOrder(row.id, e.target.value)} disabled={saving === row.id}
                        style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', fontSize: 12, minWidth: 100, cursor: 'pointer' }}>
                        <option value="">—</option>
                        {orders.map((o: any) => <option key={o.order_no} value={orderNoToInt(String(o.order_no))}>#{o.order_no}{o.invoice_no ? ` (${o.invoice_no})` : ''}</option>)}
                    </select>
                    {saving === row.id && <span style={{ marginLeft: 4 }}>⏳</span>}
                </span>
            ),
        },
        { key: 'note', label: 'Примечание', render: (v: any) => <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{v ?? '—'}</span> },
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
                </div>
                <TanStackDataTable
                    columns={columns}
                    data={data}
                    emptyText="Нет таможенных ДТ. Загрузите PDF-отчёт ФТС через «Импорт документов»."
                    enableSorting
                    enablePagination={false}
                    exportName="customs_dt"
                />
            </div>
        </div>
    );
}
