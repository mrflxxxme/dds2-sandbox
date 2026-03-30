'use client';
import { useEffect, useState, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import { usePermissions } from '@/lib/hooks/usePermissions';
import { OrderItemsDetail } from './OrderItemsDetail';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

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

    const columns: Column[] = useMemo(() => [
        { key: 'order_no', label: 'Заказ', render: (v: any) => <span style={{ fontWeight: 600 }}>{v}</span> },
        { key: 'invoice_no', label: 'Инвойс', render: (v: any) => <span className="badge badge-info" style={{ fontSize: 11 }}>{v || '—'}</span> },
        { key: 'dt_number', label: 'ДТ', render: (v: any) => <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--color-text-dim)' }}>{v || '—'}</span> },
        { key: 'ship_date', label: 'Отправка', render: (v: any) => <span style={{ fontSize: 12 }}>{v ? formatDate(v) : '—'}</span> },
        { key: 'transport_type', label: 'Транспорт', render: (v: any) => <span className="badge badge-warning">{v || 'AUTO'}</span> },
        { key: 'items_count', label: 'Позиций', render: (v: any) => v ?? '—' },
        { key: 'total_qty', label: 'Кол-во', format: 'number' as const },
        { key: 'total_cost_rub', label: 'Товар ₽', render: (v: any) => <span style={{ color: 'var(--color-success)', fontWeight: 500 }}>{v != null ? formatNumber(v) : '—'}</span> },
        { key: 'total_delivery_rub', label: 'Доставка ₽', render: (v: any) => v != null ? formatNumber(v) : '—' },
        { key: 'total_duty_rub', label: 'Пошлина ₽', render: (v: any) => v != null ? formatNumber(v) : '—' },
        { key: 'total_vat_rub', label: 'НДС ₽', render: (v: any) => v != null ? formatNumber(v) : '—' },
        { key: 'total_util_rub', label: 'Утиль ₽', render: (v: any) => v != null ? formatNumber(v) : '—' },
        { key: 'total_rub', label: 'Итого ₽', render: (v: any) => <span style={{ fontWeight: 600 }}>{v != null ? formatNumber(v) : '—'}</span> },
        { key: 'rate_cny', label: 'Курс ¥', render: (v: any) => <span style={{ fontSize: 12 }}>{v != null ? Number(v).toFixed(2) : '—'}</span> },
        { key: 'has_plan', label: 'План', render: (v: any) => v ? <span className="badge badge-success">✓</span> : <span className="badge badge-secondary">—</span> },
        {
            key: '_actions', label: '', sortable: false,
            render: (_v: any, row: any) => (
                <div style={{ display: 'flex', gap: 4 }} onClick={e => e.stopPropagation()}>
                    <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => openEdit(row)}>✎</button>
                    <button className="btn btn-primary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => generatePlan(String(row.order_no))}>{row.has_plan ? '🔄' : '📋'}</button>
                    <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(String(row.order_no))}>✕</button>
                </div>
            ),
        },
    ], []);

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

            <TanStackDataTable
                columns={columns}
                data={orders}
                emptyText="Нет заказов. Нажмите «+ Создать заказ»."
                enableSorting
                enablePagination={false}
                onRowClick={(row) => loadItems(String(row.order_no))}
                selectedIndex={orders.findIndex(o => String(o.order_no) === selected)}
                rowClassName={(row) => editOrder === String(row.order_no) ? 'edit-highlight' : ''}
            />

            {editOrder && renderForm(`Редактирование заказа #${editOrder}`, saveEdit, () => setEditOrder(null))}

            {selected && <OrderItemsDetail selected={selected} orders={orders} items={items} onItemsReload={loadItems} onOrdersReload={load} onMsg={setMsg} />}
        </div>
    );
}
