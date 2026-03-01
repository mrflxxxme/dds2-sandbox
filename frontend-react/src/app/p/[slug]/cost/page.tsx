'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

export default function CostPage() {
    const [tab, setTab] = useState<'orders' | 'nomenclature' | 'duties'>('orders');

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">🧮 Себестоимость</h1>
                    <p className="page-subtitle">Заказы, номенклатура, пошлины и утилизация</p>
                </div>
            </div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 20 }}>
                {[
                    { key: 'orders' as const, label: '📦 Заказы' },
                    { key: 'nomenclature' as const, label: '📋 Номенклатура' },
                    { key: 'duties' as const, label: '⚖️ Пошлины / Утиль' },
                ].map(t => (
                    <button key={t.key} className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                        onClick={() => setTab(t.key)}>{t.label}</button>
                ))}
            </div>
            {tab === 'orders' && <CostOrders />}
            {tab === 'nomenclature' && <Nomenclature />}
            {tab === 'duties' && <DutyRules />}
        </div>
    );
}

function CostOrders() {
    const [orders, setOrders] = useState<any[]>([]);
    const [items, setItems] = useState<any[]>([]);
    const [selected, setSelected] = useState<string | null>(null);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ order_no: '', invoice: '', transport: 'AUTO', ship_date: '' });
    const [msg, setMsg] = useState('');
    const [file, setFile] = useState<File | null>(null);

    useEffect(() => { load(); }, []);
    const load = async () => { try { setOrders(await api.getCostOrders()); } catch { } };

    const loadItems = async (orderNo: string) => {
        setSelected(orderNo);
        try { setItems(await api.getCostOrderItems(orderNo)); } catch { setItems([]); }
    };

    const create = async () => {
        try { await api.createCostOrder(form); setMsg('✅ Создан!'); setShowForm(false); load(); } catch (e: any) { setMsg(e.message); }
    };
    const del = async (orderNo: string) => {
        if (!confirm('Удалить заказ?')) return;
        try { await api.deleteCostOrder(orderNo); load(); setSelected(null); } catch (e: any) { setMsg(e.message); }
    };
    const generatePlan = async (orderNo: string) => {
        try { await api.generatePlan(orderNo); setMsg('✅ План сгенерирован!'); } catch (e: any) { setMsg(e.message); }
    };
    const uploadFile = async () => {
        if (!file || !selected) return;
        try { await api.uploadCostFile(selected, file); setMsg('✅ Файл загружен!'); loadItems(selected); } catch (e: any) { setMsg(e.message); }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Заказы (Себестоимость)</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(orders, 'cost_orders')}>📥 Excel</button>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Создать заказ</button>
                </div>
            </div>
            {msg && <div style={{ color: 'var(--color-success)', fontSize: 13, marginBottom: 8 }}>{msg}<span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span></div>}

            {showForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 12 }}>
                    <div className="form-group"><label className="form-label">Номер заказа*</label><input className="form-input" value={form.order_no} onChange={e => setForm({ ...form, order_no: e.target.value })} /></div>
                    <div className="form-group"><label className="form-label">Инвойс</label><input className="form-input" value={form.invoice} onChange={e => setForm({ ...form, invoice: e.target.value })} /></div>
                    <div className="form-group"><label className="form-label">Транспорт</label>
                        <select className="form-input" value={form.transport} onChange={e => setForm({ ...form, transport: e.target.value })}>
                            <option>AUTO</option><option>RAIL</option><option>AIR</option><option>SEA</option>
                        </select>
                    </div>
                    <div className="form-group"><label className="form-label">Дата отправки</label><input className="form-input" type="date" value={form.ship_date} onChange={e => setForm({ ...form, ship_date: e.target.value })} /></div>
                    <div><button className="btn btn-primary btn-sm" onClick={create}>💾 Создать</button></div>
                </div>
            )}

            {/* Orders table */}
            {orders.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr><th>Заказ</th><th>Инвойс</th><th>Дата отправки</th><th>Транспорт</th><th>Позиций</th><th>Кол-во</th><th>Себестоимость</th><th>Доставка</th><th>Пошлина</th><th>НДС</th><th></th></tr></thead>
                        <tbody>{orders.map(r => (
                            <tr key={r.order_no} style={{ cursor: 'pointer', background: selected === String(r.order_no) ? 'rgba(139,92,246,0.1)' : undefined }}
                                onClick={() => loadItems(String(r.order_no))}>
                                <td style={{ fontWeight: 600 }}>{r.order_no}</td>
                                <td>{r.invoice}</td>
                                <td>{r.ship_date ? formatDate(r.ship_date) : '—'}</td>
                                <td><span className="badge badge-info">{r.transport || 'AUTO'}</span></td>
                                <td>{r.positions_count ?? '—'}</td>
                                <td>{r.total_qty != null ? formatNumber(r.total_qty) : '—'}</td>
                                <td style={{ color: 'var(--color-success)', fontWeight: 500 }}>{r.total_cost != null ? formatNumber(r.total_cost) + ' ₽' : '—'}</td>
                                <td>{r.total_delivery != null ? formatNumber(r.total_delivery) + ' ₽' : '—'}</td>
                                <td>{r.total_duty != null ? formatNumber(r.total_duty) + ' ₽' : '—'}</td>
                                <td>{r.total_vat != null ? formatNumber(r.total_vat) + ' ₽' : '—'}</td>
                                <td style={{ display: 'flex', gap: 4 }}>
                                    <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={e => { e.stopPropagation(); generatePlan(String(r.order_no)); }}>📋 План</button>
                                    <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={e => { e.stopPropagation(); del(String(r.order_no)); }}>✕</button>
                                </td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет заказов</div></div>}

            {/* Items detail */}
            {selected && (
                <div style={{ marginTop: 16, borderTop: '1px solid var(--color-border)', paddingTop: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                        <h4 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Позиции заказа #{selected} ({items.length})</h4>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                            <input type="file" accept=".xlsx,.xls,.csv" onChange={e => setFile(e.target.files?.[0] || null)} style={{ fontSize: 12 }} />
                            <button className="btn btn-primary btn-sm" onClick={uploadFile} disabled={!file}>📤 Загрузить</button>
                            {items.length > 0 && <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(items, `order_${selected}_items`)}>📥 Excel</button>}
                        </div>
                    </div>
                    {items.length > 0 ? (
                        <div style={{ overflowX: 'auto', maxHeight: 300, overflowY: 'auto' }}>
                            <table className="data-table">
                                <thead><tr>{Object.keys(items[0]).map(k => <th key={k} style={{ fontSize: 11 }}>{k}</th>)}</tr></thead>
                                <tbody>{items.map((r, i) => <tr key={i}>{Object.values(r).map((v: any, j) => <td key={j} style={{ fontSize: 12 }}>{typeof v === 'number' ? formatNumber(v) : v ?? '—'}</td>)}</tr>)}</tbody>
                            </table>
                        </div>
                    ) : <div style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Нет позиций. Загрузите файл.</div>}
                </div>
            )}
        </div>
    );
}

function Nomenclature() {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [syncMsg, setSyncMsg] = useState('');
    const [lastSyncAt, setLastSyncAt] = useState<string | null>(null);

    const load = async () => { try { setData(await api.getNomenclature()); } catch { } setLoading(false); };
    const loadLastSync = async () => {
        try {
            const logs = await api.getSyncLog();
            const nomLog = logs.find((l: any) => l.sync_type === 'nomenclature' && l.status === 'OK');
            if (nomLog?.finished_at) {
                setLastSyncAt(nomLog.finished_at);
            }
        } catch { }
    };
    useEffect(() => { load(); loadLastSync(); }, []);

    const handleSync = async () => {
        setSyncing(true);
        setSyncMsg('');
        try {
            const result = await api.syncNomenclature();
            if (result.status === 'OK') {
                setSyncMsg(`✅ Синхронизация завершена: ${result.error_msg || 'OK'}`);
                if (result.finished_at) setLastSyncAt(result.finished_at);
            } else {
                setSyncMsg(`❌ Ошибка: ${result.error_msg || 'unknown'}`);
            }
            await load();
        } catch (e: any) {
            setSyncMsg(`❌ ${e.message}`);
        }
        setSyncing(false);
    };

    const formatSyncTime = (iso: string) => {
        const d = new Date(iso);
        return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    };

    if (loading) return <div style={{ padding: 20, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <div>
                    <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Номенклатура ({data.length})</h3>
                    {lastSyncAt && <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 4 }}>🕐 Последняя синхронизация: {formatSyncTime(lastSyncAt)}</div>}
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <button
                        className="btn btn-primary btn-sm"
                        onClick={handleSync}
                        disabled={syncing}
                        style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                    >
                        {syncing ? (
                            <><span className="spinner" style={{ width: 14, height: 14, border: '2px solid rgba(255,255,255,0.3)', borderTop: '2px solid #fff', borderRadius: '50%', animation: 'spin 0.8s linear infinite', display: 'inline-block' }} /> Синхронизация...</>
                        ) : '🔄 Синхронизация WB'}
                    </button>
                    {data.length > 0 && <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'nomenclature')}>📥 Excel</button>}
                </div>
            </div>
            {syncMsg && <div style={{ fontSize: 13, marginBottom: 12, padding: '8px 12px', borderRadius: 6, background: syncMsg.startsWith('✅') ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)', color: syncMsg.startsWith('✅') ? 'var(--color-success)' : 'var(--color-danger)' }}>{syncMsg}</div>}
            {data.length > 0 ? (
                <div style={{ overflowX: 'auto', maxHeight: 500, overflowY: 'auto' }}>
                    <table className="data-table">
                        <thead><tr>{Object.keys(data[0]).map(k => <th key={k} style={{ fontSize: 11 }}>{k}</th>)}</tr></thead>
                        <tbody>{data.map((r, i) => <tr key={i}>{Object.values(r).map((v: any, j) => <td key={j} style={{ fontSize: 12 }}>{typeof v === 'number' ? formatNumber(v) : v ?? '—'}</td>)}</tr>)}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Номенклатура пуста. Нажмите «🔄 Синхронизация WB» для загрузки из WB</div></div>}
        </div>
    );
}

function DutyRules() {
    const [data, setData] = useState<any[]>([]);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ hs_code: '', duty_pct: 0, vat_pct: 20, util_pct: 0, description: '' });
    const [msg, setMsg] = useState('');

    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getDutyRules()); } catch { } };
    const save = async () => {
        try { await api.addDutyRule({ ...form, duty_pct: parseFloat(String(form.duty_pct)), vat_pct: parseFloat(String(form.vat_pct)), util_pct: parseFloat(String(form.util_pct)) }); setMsg('✅ Добавлено!'); setShowForm(false); load(); } catch (e: any) { setMsg(e.message); }
    };
    const del = async (id: number) => {
        if (!confirm('Удалить?')) return;
        try { await api.deleteDutyRule(id); load(); } catch (e: any) { setMsg(e.message); }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Пошлины / Утилизация</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'duty_rules')}>📥 Excel</button>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Добавить</button>
                </div>
            </div>
            {msg && <div style={{ color: 'var(--color-success)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}

            {showForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr 1fr', gap: 12 }}>
                    <div className="form-group"><label className="form-label">HS Code</label><input className="form-input" value={form.hs_code} onChange={e => setForm({ ...form, hs_code: e.target.value })} /></div>
                    <div className="form-group"><label className="form-label">Пошлина %</label><input className="form-input" type="number" value={form.duty_pct} onChange={e => setForm({ ...form, duty_pct: parseFloat(e.target.value) || 0 })} /></div>
                    <div className="form-group"><label className="form-label">НДС %</label><input className="form-input" type="number" value={form.vat_pct} onChange={e => setForm({ ...form, vat_pct: parseFloat(e.target.value) || 0 })} /></div>
                    <div className="form-group"><label className="form-label">Утиль %</label><input className="form-input" type="number" value={form.util_pct} onChange={e => setForm({ ...form, util_pct: parseFloat(e.target.value) || 0 })} /></div>
                    <div className="form-group"><label className="form-label">Описание</label><input className="form-input" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} /></div>
                    <div><button className="btn btn-primary btn-sm" onClick={save}>💾 Добавить</button></div>
                </div>
            )}

            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr><th>ID</th><th>HS Code</th><th>Пошлина %</th><th>НДС %</th><th>Утиль %</th><th>Описание</th><th></th></tr></thead>
                        <tbody>{data.map(r => (
                            <tr key={r.id}>
                                <td>{r.id}</td><td style={{ fontFamily: 'monospace' }}>{r.hs_code}</td>
                                <td>{r.duty_pct}%</td><td>{r.vat_pct}%</td><td>{r.util_pct}%</td>
                                <td style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>{r.description}</td>
                                <td><button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(r.id)}>✕</button></td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет правил</div></div>}
        </div>
    );
}
