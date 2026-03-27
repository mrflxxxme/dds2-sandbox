'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import { usePermissions } from '@/lib/hooks/usePermissions';
import { OrderItemsDetail } from './OrderItemsDetail';

export function CostOrders() {
    const { canEdit } = usePermissions();
    const [orders, setOrders] = useState<any[]>([]);
    const [items, setItems] = useState<any[]>([]);
    const [selected, setSelected] = useState<string | null>(null);
    const [showCreate, setShowCreate] = useState(false);
    const [editOrder, setEditOrder] = useState<string | null>(null);
    const emptyForm = {
        order_no: '', invoice_no: '', transport_type: 'AUTO', ship_date: '',
        actual_arrival_date: '', delivery_cost_cny: '0', delivery_cost_usd: '0',
        rate_cny: '12.5', rate_eur: '100', rate_usd: '90', dt_number: '', note: ''
    };
    const [form, setForm] = useState(emptyForm);
    const [msg, setMsg] = useState('');

    useEffect(() => { load(); }, []);
    const load = async () => { try { setOrders(await api.getCostOrders()); } catch { } };
    const loadItems = async (orderNo: string) => {
        setSelected(orderNo);
        try { setItems(await api.getCostOrderItems(orderNo)); } catch { setItems([]); }
    };

    const create = async () => {
        try {
            await api.createCostOrder({
                order_no: form.order_no, invoice_no: form.invoice_no || null,
                transport_type: form.transport_type, ship_date: form.ship_date || null,
                actual_arrival_date: form.actual_arrival_date || null,
                delivery_cost_cny: parseFloat(form.delivery_cost_cny) || 0,
                delivery_cost_usd: parseFloat(form.delivery_cost_usd) || 0,
                rate_cny: parseFloat(form.rate_cny) || 0, rate_eur: parseFloat(form.rate_eur) || 0,
                rate_usd: parseFloat(form.rate_usd) || 0, dt_number: form.dt_number || null,
                note: form.note || null,
            });
            setMsg('✅ Заказ создан!'); setShowCreate(false); setForm(emptyForm); load();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };

    const openEdit = (r: any) => {
        if (editOrder === String(r.order_no)) { setEditOrder(null); return; }
        setEditOrder(String(r.order_no));
        setForm({
            order_no: String(r.order_no), invoice_no: r.invoice_no || '',
            transport_type: r.transport_type || 'AUTO', ship_date: r.ship_date || '',
            actual_arrival_date: r.actual_arrival_date || '',
            delivery_cost_cny: String(r.delivery_cost_cny ?? 0),
            delivery_cost_usd: String(r.delivery_cost_usd ?? 0),
            rate_cny: String(r.rate_cny ?? 0), rate_eur: String(r.rate_eur ?? 0),
            rate_usd: String(r.rate_usd ?? 0), dt_number: r.dt_number || '', note: r.note || '',
        });
    };

    const saveEdit = async () => {
        if (!editOrder) return;
        try {
            await api.updateCostOrder(editOrder, {
                order_no: form.order_no || editOrder, invoice_no: form.invoice_no || null,
                transport_type: form.transport_type, ship_date: form.ship_date || null,
                actual_arrival_date: form.actual_arrival_date || null,
                delivery_cost_cny: parseFloat(form.delivery_cost_cny) || 0,
                delivery_cost_usd: parseFloat(form.delivery_cost_usd) || 0,
                rate_cny: parseFloat(form.rate_cny) || 0, rate_eur: parseFloat(form.rate_eur) || 0,
                rate_usd: parseFloat(form.rate_usd) || 0, dt_number: form.dt_number || null,
                note: form.note || null,
            });
            setMsg('✅ Сохранено!'); setEditOrder(null); load();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };

    const del = async (orderNo: string) => {
        if (!confirm('Удалить заказ?')) return;
        try { await api.deleteCostOrder(orderNo); load(); setSelected(null); setEditOrder(null); } catch (e: any) { setMsg(e.message); }
    };
    const generatePlan = async (orderNo: string) => {
        try { await api.generatePlan(orderNo); setMsg('✅ План сгенерирован!'); load(); } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };

    const renderField = (label: string, field: string, type = 'text', step?: string) => (
        <div className="form-group">
            <label className="form-label" style={{ fontSize: 11 }}>{label}</label>
            <input className="form-input" type={type} step={step} value={(form as any)[field]}
                onChange={e => setForm(prev => ({ ...prev, [field]: e.target.value }))} style={{ fontSize: 13 }} />
        </div>
    );

    const renderForm = (title: string, onSave: () => void, onCancel: () => void) => (
        <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16, marginTop: editOrder ? 16 : 0 }}>
            <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>{title}</h4>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
                {renderField("Номер заказа *", "order_no")}
                {renderField("Инвойс", "invoice_no")}
                <div className="form-group">
                    <label className="form-label" style={{ fontSize: 11 }}>Транспорт</label>
                    <select className="form-input" value={form.transport_type}
                        onChange={e => setForm(prev => ({ ...prev, transport_type: e.target.value }))} style={{ fontSize: 13 }}>
                        <option>AUTO</option><option>RAIL</option><option>AIR</option><option>SEA</option>
                    </select>
                </div>
                {renderField("Дата отправки", "ship_date", "date")}
                {renderField("Дата прибытия", "actual_arrival_date", "date")}
                {renderField("Доставка ¥", "delivery_cost_cny", "number", "0.01")}
                {renderField("Доставка $", "delivery_cost_usd", "number", "0.01")}
                {renderField("Курс ¥/₽", "rate_cny", "number", "0.01")}
                {renderField("Курс €/₽", "rate_eur", "number", "0.01")}
                {renderField("Курс $/₽", "rate_usd", "number", "0.01")}
                {renderField("Номер ДТ", "dt_number")}
                {renderField("Примечание", "note")}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                <button className="btn btn-primary btn-sm" onClick={onSave}>💾 {editOrder ? 'Сохранить' : 'Создать'}</button>
                <button className="btn btn-secondary btn-sm" onClick={onCancel}>Отмена</button>
            </div>
        </div>
    );

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Заказы</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(orders, 'cost_orders')}>📥 Excel</button>
                    {canEdit() && <button className="btn btn-primary btn-sm" onClick={() => { setShowCreate(!showCreate); setEditOrder(null); }}>+ Создать заказ</button>}
                </div>
            </div>
            {msg && <div style={{ fontSize: 13, marginBottom: 8, padding: '6px 12px', borderRadius: 6, background: msg.startsWith('✅') ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)', color: msg.startsWith('✅') ? 'var(--color-success)' : 'var(--color-danger)' }}>{msg} <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span></div>}

            {showCreate && renderForm('Новый заказ', create, () => setShowCreate(false))}

            {orders.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr>
                            <th>Заказ</th><th>Инвойс</th><th>ДТ</th><th>Отправка</th><th>Транспорт</th>
                            <th>Позиций</th><th>Кол-во</th><th>Товар ₽</th><th>Доставка ₽</th>
                            <th>Пошлина ₽</th><th>НДС ₽</th><th>Утиль ₽</th><th>Итого ₽</th>
                            <th>Курс ¥</th><th>План</th><th></th>
                        </tr></thead>
                        <tbody>{orders.map(r => (
                            <tr key={r.order_no} style={{ cursor: 'pointer', background: selected === String(r.order_no) ? 'rgba(139,92,246,0.1)' : editOrder === String(r.order_no) ? 'rgba(59,130,246,0.08)' : undefined }}
                                onClick={() => loadItems(String(r.order_no))}>
                                <td style={{ fontWeight: 600 }}>{r.order_no}</td>
                                <td><span className="badge badge-info" style={{ fontSize: 11 }}>{r.invoice_no || '—'}</span></td>
                                <td style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--color-text-dim)' }}>{r.dt_number || '—'}</td>
                                <td style={{ fontSize: 12 }}>{r.ship_date ? formatDate(r.ship_date) : '—'}</td>
                                <td><span className="badge badge-warning">{r.transport_type || 'AUTO'}</span></td>
                                <td>{r.items_count ?? '—'}</td>
                                <td>{r.total_qty != null ? formatNumber(r.total_qty) : '—'}</td>
                                <td style={{ color: 'var(--color-success)', fontWeight: 500 }}>{r.total_cost_rub != null ? formatNumber(r.total_cost_rub) : '—'}</td>
                                <td>{r.total_delivery_rub != null ? formatNumber(r.total_delivery_rub) : '—'}</td>
                                <td>{r.total_duty_rub != null ? formatNumber(r.total_duty_rub) : '—'}</td>
                                <td>{r.total_vat_rub != null ? formatNumber(r.total_vat_rub) : '—'}</td>
                                <td>{r.total_util_rub != null ? formatNumber(r.total_util_rub) : '—'}</td>
                                <td style={{ fontWeight: 600 }}>{r.total_rub != null ? formatNumber(r.total_rub) : '—'}</td>
                                <td style={{ fontSize: 12 }}>{r.rate_cny != null ? Number(r.rate_cny).toFixed(2) : '—'}</td>
                                <td>{r.has_plan ? <span className="badge badge-success">✓</span> : <span className="badge badge-secondary">—</span>}</td>
                                <td style={{ display: 'flex', gap: 4 }} onClick={e => e.stopPropagation()}>
                                    <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => openEdit(r)}>✎</button>
                                    <button className="btn btn-primary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => generatePlan(String(r.order_no))}>{r.has_plan ? '🔄' : '📋'}</button>
                                    <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(String(r.order_no))}>✕</button>
                                </td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет заказов. Нажмите «+ Создать заказ».</div></div>}

            {editOrder && renderForm(`Редактирование заказа #${editOrder}`, saveEdit, () => setEditOrder(null))}

            {selected && <OrderItemsDetail selected={selected} orders={orders} items={items} onItemsReload={loadItems} onOrdersReload={load} onMsg={setMsg} />}
        </div>
    );
}
