'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

export default function PlanningPage() {
    const [tab, setTab] = useState<'orders' | 'payments' | 'incomes' | 'wb' | 'cashflow' | 'customs'>('orders');

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
                ].map(t => (
                    <button key={t.key} className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                        onClick={() => setTab(t.key)}>{t.label}</button>
                ))}
            </div>
            {tab === 'orders' && <CostOrders />}
            {tab === 'payments' && <PlanPayments />}
            {tab === 'incomes' && <PlanIncomes />}
            {tab === 'wb' && <WbPayouts />}
            {tab === 'cashflow' && <Cashflow />}
            {tab === 'customs' && <CustomsDt />}
        </div>
    );
}

function CostOrders() {
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
    const [file, setFile] = useState<File | null>(null);
    const [expanded, setExpanded] = useState<string | null>(null);
    const [summary, setSummary] = useState<any>(null);

    useEffect(() => { load(); }, []);
    const load = async () => { try { setOrders(await api.getCostOrders()); } catch { } };

    const loadItems = async (orderNo: string) => {
        setSelected(orderNo);
        try { setItems(await api.getCostOrderItems(orderNo)); } catch { setItems([]); }
    };

    const create = async () => {
        try {
            await api.createCostOrder({
                order_no: form.order_no,
                invoice_no: form.invoice_no || null,
                transport_type: form.transport_type,
                ship_date: form.ship_date || null,
                actual_arrival_date: form.actual_arrival_date || null,
                delivery_cost_cny: parseFloat(form.delivery_cost_cny) || 0,
                delivery_cost_usd: parseFloat(form.delivery_cost_usd) || 0,
                rate_cny: parseFloat(form.rate_cny) || 0,
                rate_eur: parseFloat(form.rate_eur) || 0,
                rate_usd: parseFloat(form.rate_usd) || 0,
                dt_number: form.dt_number || null,
                note: form.note || null,
            });
            setMsg('✅ Заказ создан!'); setShowCreate(false); setForm(emptyForm); load();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };

    const openEdit = (r: any) => {
        if (editOrder === String(r.order_no)) { setEditOrder(null); return; }
        setEditOrder(String(r.order_no));
        setForm({
            order_no: String(r.order_no),
            invoice_no: r.invoice_no || '',
            transport_type: r.transport_type || 'AUTO',
            ship_date: r.ship_date || '',
            actual_arrival_date: r.actual_arrival_date || '',
            delivery_cost_cny: String(r.delivery_cost_cny ?? 0),
            delivery_cost_usd: String(r.delivery_cost_usd ?? 0),
            rate_cny: String(r.rate_cny ?? 0),
            rate_eur: String(r.rate_eur ?? 0),
            rate_usd: String(r.rate_usd ?? 0),
            dt_number: r.dt_number || '',
            note: r.note || '',
        });
    };

    const saveEdit = async () => {
        if (!editOrder) return;
        try {
            await api.updateCostOrder(editOrder, {
                order_no: form.order_no || editOrder,
                invoice_no: form.invoice_no || null,
                transport_type: form.transport_type,
                ship_date: form.ship_date || null,
                actual_arrival_date: form.actual_arrival_date || null,
                delivery_cost_cny: parseFloat(form.delivery_cost_cny) || 0,
                delivery_cost_usd: parseFloat(form.delivery_cost_usd) || 0,
                rate_cny: parseFloat(form.rate_cny) || 0,
                rate_eur: parseFloat(form.rate_eur) || 0,
                rate_usd: parseFloat(form.rate_usd) || 0,
                dt_number: form.dt_number || null,
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
    const uploadFile = async () => {
        if (!file || !selected) return;
        try { await api.uploadCostFile(selected, file); setMsg('✅ Файл загружен! Пересчёт выполнен.'); loadItems(selected); load(); } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };

    const showSummary = async (orderNo: string) => {
        if (expanded === orderNo) { setExpanded(null); return; }
        setExpanded(orderNo);
        try { setSummary(await api.getPlanningOrderSummary(orderNo)); } catch { setSummary(null); }
    };

    const F = ({ label, field, type = 'text', step }: { label: string; field: string; type?: string; step?: string }) => (
        <div className="form-group">
            <label className="form-label" style={{ fontSize: 11 }}>{label}</label>
            <input className="form-input" type={type} step={step} value={(form as any)[field]}
                onChange={e => setForm({ ...form, [field]: e.target.value })}
                style={{ fontSize: 13 }} />
        </div>
    );

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Заказы</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(orders, 'cost_orders')}>📥 Excel</button>
                    <button className="btn btn-primary btn-sm" onClick={() => { setShowCreate(!showCreate); setEditOrder(null); }}>+ Создать заказ</button>
                </div>
            </div>
            {msg && <div style={{ fontSize: 13, marginBottom: 8, padding: '6px 12px', borderRadius: 6, background: msg.startsWith('✅') ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)', color: msg.startsWith('✅') ? 'var(--color-success)' : 'var(--color-danger)' }}>{msg} <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span></div>}

            {/* Create form */}
            {showCreate && (
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16 }}>
                    <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Новый заказ</h4>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
                        <F label="Номер заказа *" field="order_no" />
                        <F label="Инвойс" field="invoice_no" />
                        <div className="form-group">
                            <label className="form-label" style={{ fontSize: 11 }}>Транспорт</label>
                            <select className="form-input" value={form.transport_type}
                                onChange={e => setForm({ ...form, transport_type: e.target.value })} style={{ fontSize: 13 }}>
                                <option>AUTO</option><option>RAIL</option><option>AIR</option><option>SEA</option>
                            </select>
                        </div>
                        <F label="Дата отправки" field="ship_date" type="date" />
                        <F label="Дата прибытия" field="actual_arrival_date" type="date" />
                        <F label="Доставка ¥" field="delivery_cost_cny" type="number" step="0.01" />
                        <F label="Доставка $" field="delivery_cost_usd" type="number" step="0.01" />
                        <F label="Курс ¥/₽" field="rate_cny" type="number" step="0.01" />
                        <F label="Курс €/₽" field="rate_eur" type="number" step="0.01" />
                        <F label="Курс $/₽" field="rate_usd" type="number" step="0.01" />
                        <F label="Номер ДТ" field="dt_number" />
                        <F label="Примечание" field="note" />
                    </div>
                    <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                        <button className="btn btn-primary btn-sm" onClick={create}>💾 Создать</button>
                        <button className="btn btn-secondary btn-sm" onClick={() => setShowCreate(false)}>Отмена</button>
                    </div>
                </div>
            )}

            {/* Orders table */}
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
                            <tr key={r.order_no}
                                style={{ cursor: 'pointer', background: selected === String(r.order_no) ? 'rgba(139,92,246,0.1)' : editOrder === String(r.order_no) ? 'rgba(59,130,246,0.08)' : undefined }}
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
                                    <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }}
                                        onClick={() => openEdit(r)}>✎</button>
                                    <button className="btn btn-primary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }}
                                        onClick={() => generatePlan(String(r.order_no))}>{r.has_plan ? '🔄' : '📋'}</button>
                                    <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }}
                                        onClick={() => del(String(r.order_no))}>✕</button>
                                </td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет заказов. Нажмите «+ Создать заказ».</div></div>}

            {/* Edit form */}
            {editOrder && (
                <div style={{ marginTop: 16, background: 'var(--color-bg-input)', padding: 16, borderRadius: 8 }}>
                    <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Редактирование заказа #{editOrder}</h4>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
                        <F label="Номер заказа" field="order_no" />
                        <F label="Инвойс" field="invoice_no" />
                        <div className="form-group">
                            <label className="form-label" style={{ fontSize: 11 }}>Транспорт</label>
                            <select className="form-input" value={form.transport_type}
                                onChange={e => setForm({ ...form, transport_type: e.target.value })} style={{ fontSize: 13 }}>
                                <option>AUTO</option><option>RAIL</option><option>AIR</option><option>SEA</option>
                            </select>
                        </div>
                        <F label="Дата отправки" field="ship_date" type="date" />
                        <F label="Дата прибытия" field="actual_arrival_date" type="date" />
                        <F label="Доставка ¥" field="delivery_cost_cny" type="number" step="0.01" />
                        <F label="Доставка $" field="delivery_cost_usd" type="number" step="0.01" />
                        <F label="Курс ¥/₽" field="rate_cny" type="number" step="0.01" />
                        <F label="Курс €/₽" field="rate_eur" type="number" step="0.01" />
                        <F label="Курс $/₽" field="rate_usd" type="number" step="0.01" />
                        <F label="Номер ДТ" field="dt_number" />
                        <F label="Примечание" field="note" />
                    </div>
                    <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                        <button className="btn btn-primary btn-sm" onClick={saveEdit}>💾 Сохранить</button>
                        <button className="btn btn-secondary btn-sm" onClick={() => setEditOrder(null)}>Отмена</button>
                    </div>
                </div>
            )}

            {/* Items detail */}
            {selected && (() => {
                const ord = orders.find(o => String(o.order_no) === selected);
                const totalRub = ord?.total_rub ?? 0;
                const pct = (v: number) => totalRub > 0 ? ((v / totalRub) * 100).toFixed(1) + '%' : '';
                const colMap: Record<string, string> = {
                    id: '№', barcode: 'Баркод', subject: 'Категория', article_seller: 'Артикул',
                    article_wb: 'WB', qty: 'Кол-во', price_cny: 'Цена CNY', weight_kg: 'Вес кг',
                    area_m2: 'Площ. м²', volume_m3: 'Объём м³', cost_cny: 'Себ. CNY',
                    cost_rub: 'Себ. ₽/шт', delivery_rub: 'Дост. ₽/шт', duty_rub: 'Пошл. ₽/шт',
                    vat_rub: 'НДС ₽/шт', util_rub: 'Утиль ₽/шт', total_rub: 'Итого ₽/шт',
                    total_cost_rub: 'Итого себ. ₽', total_delivery_rub: 'Итого дост. ₽',
                    total_duty_rub: 'Итого пошл. ₽', total_vat_rub: 'Итого НДС ₽',
                    total_util_rub: 'Итого утиль ₽', grand_total_rub: 'Всего ₽',
                    name: 'Название', description: 'Описание', order_no: 'Заказ',
                    nm_id: 'nm_id', imt_id: 'imt_id', sku: 'SKU',
                    unrecognized: 'Не распознано',
                };
                const tr = (key: string) => colMap[key.toLowerCase()] || key;
                const intCols = new Set(['id', 'nm_id', 'imt_id', 'order_no']);
                const wbCol = 'article_wb';
                const renderCell = (key: string, v: any) => {
                    if (key === wbCol && v != null) {
                        const nmId = typeof v === 'number' ? Math.round(v) : parseInt(String(v).replace(/\s/g, ''), 10);
                        if (!isNaN(nmId)) return (<a href={`https://www.wildberries.ru/catalog/${nmId}/detail.aspx`} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--color-info)', textDecoration: 'underline' }}>{nmId}</a>);
                    }
                    if (intCols.has(key) && typeof v === 'number') return Math.round(v);
                    if (typeof v === 'number') return formatNumber(v);
                    return v ?? '—';
                };
                const hasCarpets = items.some((r: any) => {
                    const s = String(r.subject || '').toLowerCase();
                    return s.includes('ковр') || s.includes('палас') || s.includes('дорожк') || s.includes('carpet');
                });
                const hiddenCols = new Set(['volume_m3', ...(hasCarpets ? [] : ['area_m2'])]);
                const itemKeys = items.length > 0 ? Object.keys(items[0]).filter(k => !hiddenCols.has(k)) : [];

                return (
                    <div style={{ marginTop: 16, borderTop: '1px solid var(--color-border)', paddingTop: 16 }}>
                        <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>📊 Расчёт себестоимости по позициям</h4>
                        {ord && (
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginBottom: 16 }}>
                                {[
                                    { label: 'Кол-во (шт)', value: formatNumber(ord.total_qty ?? 0) },
                                    { label: 'Себестоимость', value: `${formatNumber(ord.total_cost_rub ?? 0)} ₽`, color: 'var(--color-success)', sub: `↑ ${pct(ord.total_cost_rub ?? 0)}` },
                                    { label: 'Доставка', value: `${formatNumber(ord.total_delivery_rub ?? 0)} ₽`, sub: `↑ ${pct(ord.total_delivery_rub ?? 0)}` },
                                    { label: 'Пошлина', value: `${formatNumber(ord.total_duty_rub ?? 0)} ₽`, color: 'var(--color-warning)', sub: `↑ ${pct(ord.total_duty_rub ?? 0)}` },
                                    { label: 'НДС', value: `${formatNumber(ord.total_vat_rub ?? 0)} ₽`, color: 'var(--color-danger)', sub: `↑ ${pct(ord.total_vat_rub ?? 0)}` },
                                ].map((c, i) => (
                                    <div key={i} style={{ background: 'var(--color-bg-input)', padding: '12px 16px', borderRadius: 8, border: '1px solid var(--color-border)' }}>
                                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>{c.label}</div>
                                        <div style={{ fontSize: 18, fontWeight: 700, color: c.color || 'var(--color-text)' }}>{c.value}</div>
                                        {c.sub && <div style={{ fontSize: 10, color: 'var(--color-text-dim)', marginTop: 2 }}>{c.sub}</div>}
                                    </div>
                                ))}
                            </div>
                        )}
                        {ord && (
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginBottom: 16 }}>
                                <div style={{ background: 'var(--color-bg-input)', padding: '12px 16px', borderRadius: 8, border: '1px solid var(--color-border)' }}>
                                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>Утильсбор</div>
                                    <div style={{ fontSize: 18, fontWeight: 700 }}>{formatNumber(ord.total_util_rub ?? 0)} ₽</div>
                                    <div style={{ fontSize: 10, color: 'var(--color-text-dim)', marginTop: 2 }}>↑ {pct(ord.total_util_rub ?? 0)}</div>
                                </div>
                                <div style={{ background: 'linear-gradient(135deg, rgba(139,92,246,0.15), rgba(236,72,153,0.15))', padding: '12px 16px', borderRadius: 8, border: '1px solid rgba(139,92,246,0.3)', gridColumn: 'span 2' }}>
                                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>🔥 ИТОГО</div>
                                    <div style={{ fontSize: 22, fontWeight: 800, color: 'var(--color-success)' }}>{formatNumber(totalRub)} ₽</div>
                                </div>
                                <div style={{ background: 'var(--color-bg-input)', padding: '12px 16px', borderRadius: 8, border: '1px solid var(--color-border)' }}>
                                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>Курс ¥/₽</div>
                                    <div style={{ fontSize: 18, fontWeight: 700 }}>{ord.rate_cny ? Number(ord.rate_cny).toFixed(2) : '—'}</div>
                                </div>
                                <div style={{ background: 'var(--color-bg-input)', padding: '12px 16px', borderRadius: 8, border: '1px solid var(--color-border)' }}>
                                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>Доставка ¥</div>
                                    <div style={{ fontSize: 18, fontWeight: 700 }}>{formatNumber(ord.delivery_cost_cny ?? 0)}</div>
                                </div>
                            </div>
                        )}

                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                            <h4 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Позиции заказа #{selected} ({items.length})</h4>
                            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                                <input type="file" accept=".xlsx,.xls,.csv" onChange={e => setFile(e.target.files?.[0] || null)} style={{ fontSize: 12 }} />
                                <button className="btn btn-primary btn-sm" onClick={uploadFile} disabled={!file}>📤 Загрузить</button>
                                {items.length > 0 && <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(items, `order_${selected}_items`)}>📥 Excel</button>}
                            </div>
                        </div>
                        {items.length > 0 ? (
                            <div style={{ overflowX: 'auto', maxHeight: 400, overflowY: 'auto' }}>
                                <table className="data-table">
                                    <thead><tr>{itemKeys.map(k => <th key={k} style={{ fontSize: 11 }}>{tr(k)}</th>)}</tr></thead>
                                    <tbody>{items.map((r: any, i: number) => <tr key={i}>{itemKeys.map((k, j) => <td key={j} style={{ fontSize: 12 }}>{renderCell(k, r[k])}</td>)}</tr>)}</tbody>
                                </table>
                            </div>
                        ) : <div style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Нет позиций. Загрузите файл Excel.</div>}
                    </div>
                );
            })()}

            {/* Order summary (plan vs fact) */}
            <div style={{ borderTop: '1px solid var(--color-border)', marginTop: 16, paddingTop: 16 }}>
                <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>📊 Сводка по заказу (план vs факт)</h4>
                {orders.length > 0 && (
                    <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
                        <select className="form-input" style={{ maxWidth: 200, fontSize: 13 }}
                            value={expanded || ''} onChange={e => showSummary(e.target.value)}>
                            <option value="">Выберите заказ</option>
                            {orders.map(o => <option key={o.order_no} value={String(o.order_no)}>№{o.order_no}</option>)}
                        </select>
                    </div>
                )}
                {expanded && summary && (
                    <div style={{ padding: 16, background: 'var(--color-bg-input)', borderRadius: 8 }}>
                        {summary.totals && (
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 16 }}>
                                <div><div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>План заказ</div><div style={{ fontSize: 18, fontWeight: 600 }}>{formatNumber(summary.totals.plan_order || 0)} ₽</div></div>
                                <div><div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>План логистика</div><div style={{ fontSize: 18, fontWeight: 600 }}>{formatNumber(summary.totals.plan_logistics_cny || 0)} ¥</div></div>
                                <div><div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>План таможня</div><div style={{ fontSize: 18, fontWeight: 600 }}>{formatNumber(summary.totals.plan_customs_rub || 0)} ₽</div></div>
                                <div><div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Факт заказ</div><div style={{ fontSize: 18, fontWeight: 600 }}>{formatNumber(summary.totals.fact_order || 0)} ₽</div></div>
                                <div></div>
                                <div><div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Факт таможня</div><div style={{ fontSize: 18, fontWeight: 600 }}>{formatNumber(summary.totals.fact_customs || 0)} ₽</div></div>
                            </div>
                        )}
                        {summary.fact_order_payments?.length > 0 && (
                            <div style={{ marginTop: 12 }}>
                                <h5 style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Факт оплаты заказа:</h5>
                                <table className="data-table"><thead><tr><th>Дата</th><th>Контрагент</th><th>Сумма</th><th>Валюта</th></tr></thead>
                                    <tbody>{summary.fact_order_payments.map((p: any, i: number) => (
                                        <tr key={i}><td style={{ fontSize: 12 }}>{p.date}</td><td style={{ fontSize: 12 }}>{p.counterparty}</td><td style={{ fontSize: 12 }}>{formatNumber(p.expense)}</td><td style={{ fontSize: 12 }}>{p.currency}</td></tr>
                                    ))}</tbody></table>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

function PlanPayments() {
    const [data, setData] = useState<any[]>([]);
    const [msg, setMsg] = useState('');
    const [showAddForm, setShowAddForm] = useState(false);
    const [showLinkForm, setShowLinkForm] = useState(false);
    const [payForm, setPayForm] = useState({ pay_date: '', order_no: '', direction: 'ЗАКАЗ', amount: '', currency: 'RUB', fx_rate: '', amount_rub: '' });
    const [candidates, setCandidates] = useState<any[]>([]);
    const [accounts, setAccounts] = useState<any[]>([]);
    const [selPayId, setSelPayId] = useState<number | null>(null);
    const [selTxnId, setSelTxnId] = useState<string>('');
    const [linkAmount, setLinkAmount] = useState('');
    const [selAccount, setSelAccount] = useState<string>('');

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
        try { await api.syncPlanPayments(); setMsg('✅ Факт синхронизирован!'); load(); } catch (e: any) { setMsg(e.message); }
    };
    const addPayment = async () => {
        try {
            await api.upsertPlanningPayment({
                pay_date: payForm.pay_date || null,
                order_no: payForm.order_no ? parseInt(payForm.order_no) : null,
                direction: payForm.direction,
                amount: payForm.amount ? parseFloat(payForm.amount) : null,
                currency: payForm.currency,
                fx_rate: payForm.fx_rate ? parseFloat(payForm.fx_rate) : null,
                amount_rub: payForm.amount_rub ? parseFloat(payForm.amount_rub) : null,
            });
            setMsg('✅ Платёж добавлен!'); setShowAddForm(false); load();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };
    const loadCandidates = async (account?: string) => {
        try { setCandidates(await api.getCandidateTransactions(account || undefined)); } catch { setCandidates([]); }
    };
    const openLinkForm = async () => {
        setShowLinkForm(!showLinkForm);
        if (!showLinkForm) {
            await loadCandidates(selAccount);
            try { setAccounts(await api.getAccountsList()); } catch { setAccounts([]); }
        }
    };
    const handleAccountChange = async (acc: string) => {
        setSelAccount(acc);
        await loadCandidates(acc);
    };
    const linkTransaction = async () => {
        if (!selPayId || !selTxnId) return;
        try {
            await api.createFactLink({ payment_id: selPayId, txn_id: selTxnId, amount_rub: linkAmount ? parseFloat(linkAmount) : null });
            setMsg('✅ Привязано!'); await sync(); setShowLinkForm(false);
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };

    const _status = (r: any) => {
        const amt = parseFloat(r.amount_rub || 0);
        const paid = parseFloat(r.paid_rub || 0);
        if (r.is_paid || (amt > 0 && paid >= amt)) return { label: '✅ Оплачено', cls: 'success' };
        if (paid > 0 && amt > 0) return { label: `🟠 ${(paid / amt * 100).toFixed(0)}%`, cls: 'warning' };
        const d = r.pay_date;
        if (d) {
            try {
                const dt = new Date(d); const today = new Date(); today.setHours(0, 0, 0, 0);
                if (dt < today) return { label: '🔴 Просрочено', cls: 'danger' };
                if (dt.toDateString() === today.toDateString()) return { label: '🟡 Сегодня', cls: 'warning' };
                return { label: '🔵 Будущее', cls: 'info' };
            } catch { }
        }
        return { label: '—', cls: 'secondary' };
    };

    const totalPlan = data.reduce((s, r) => s + parseFloat(r.amount_rub || 0), 0);
    const totalPaid = data.reduce((s, r) => s + parseFloat(r.paid_rub || 0), 0);
    const totalRemain = totalPlan - totalPaid;
    const progress = totalPlan > 0 ? (totalPaid / totalPlan * 100).toFixed(0) + '%' : '—';

    const unpaidPayments = data.filter(p => !p.is_paid);

    const [txnSearch, setTxnSearch] = useState('');
    const filteredCandidates = candidates.filter((c: any) => {
        if (!txnSearch) return true;
        const s = txnSearch.toLowerCase();
        return (c.counterparty || '').toLowerCase().includes(s) ||
            String(c.expense || '').includes(s) ||
            (c.date || '').includes(s) ||
            (c.purpose || '').toLowerCase().includes(s);
    });

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Плановые платежи</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={sync}>🔄 Синхронизировать факт с выписками</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'plan_payments')}>📥 Excel</button>
                </div>
            </div>
            {msg && <div style={{ fontSize: 13, marginBottom: 8, padding: '6px 12px', borderRadius: 6, background: msg.startsWith('✅') ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)', color: msg.startsWith('✅') ? 'var(--color-success)' : 'var(--color-danger)' }}>{msg} <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span></div>}

            {/* Compact summary – inline row */}
            {data.length > 0 && (
                <div style={{ display: 'flex', gap: 24, marginBottom: 12, padding: '8px 12px', background: 'var(--color-bg-input)', borderRadius: 8, fontSize: 13, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span><span style={{ color: 'var(--color-text-muted)' }}>План:</span> <b>{formatNumber(totalPlan)} ₽</b></span>
                    <span><span style={{ color: 'var(--color-text-muted)' }}>Оплачено:</span> <b style={{ color: 'var(--color-success)' }}>{formatNumber(totalPaid)} ₽</b></span>
                    <span><span style={{ color: 'var(--color-text-muted)' }}>Остаток:</span> <b style={{ color: totalRemain > 0 ? 'var(--color-danger)' : 'var(--color-success)' }}>{formatNumber(totalRemain)} ₽</b></span>
                    <span><span style={{ color: 'var(--color-text-muted)' }}>Прогресс:</span> <b>{progress}</b></span>
                </div>
            )}

            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr>
                            <th>ID</th><th>Дата</th><th>Заказ</th><th>Направление</th><th>Сумма</th><th>Вал.</th>
                            <th>План ₽</th><th>Факт ₽</th><th>Остаток</th><th>Статус</th><th></th>
                        </tr></thead>
                        <tbody>{data.map(r => {
                            const st = _status(r);
                            const amt = parseFloat(r.amount_rub || 0);
                            const paid = parseFloat(r.paid_rub || 0);
                            const remain = amt - paid;
                            return (
                                <tr key={r.id}>
                                    <td>{r.id}</td>
                                    <td style={{ fontSize: 12 }}>{r.pay_date ? formatDate(r.pay_date) : '—'}</td>
                                    <td>{r.order_no || '—'}</td>
                                    <td><span className="badge badge-info">{r.direction || '—'}</span></td>
                                    <td>{formatNumber(r.amount || 0)}</td>
                                    <td><span className={`badge badge-${r.currency === 'RUB' ? 'success' : 'warning'}`}>{r.currency}</span></td>
                                    <td style={{ fontWeight: 500 }}>{formatNumber(amt)} ₽</td>
                                    <td>{paid > 0 ? `${formatNumber(paid)} ₽` : '—'}</td>
                                    <td>{remain > 0 ? `${formatNumber(remain)} ₽` : '—'}</td>
                                    <td><span className={`badge badge-${st.cls}`}>{st.label}</span></td>
                                    <td style={{ display: 'flex', gap: 4 }}>
                                        {!r.is_paid && <button className="btn btn-primary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => markPaid(r.id)}>✓</button>}
                                        <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(r.id)}>✕</button>
                                    </td>
                                </tr>
                            );
                        })}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет платежей</div></div>}

            {/* Action buttons row */}
            <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button className="btn btn-secondary btn-sm" onClick={() => setShowAddForm(!showAddForm)}>➕ Добавить платёж</button>
                <button className="btn btn-secondary btn-sm" onClick={openLinkForm}>🔗 Привязать транзакцию</button>
            </div>

            {/* Add payment form */}
            {showAddForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginTop: 8 }}>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Дата оплаты</label>
                            <input className="form-input" type="date" value={payForm.pay_date} onChange={e => setPayForm({ ...payForm, pay_date: e.target.value })} style={{ fontSize: 12 }} /></div>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Номер заказа</label>
                            <input className="form-input" type="number" value={payForm.order_no} onChange={e => setPayForm({ ...payForm, order_no: e.target.value })} style={{ fontSize: 12 }} /></div>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Направление</label>
                            <select className="form-input" value={payForm.direction} onChange={e => setPayForm({ ...payForm, direction: e.target.value })} style={{ fontSize: 12 }}>
                                <option>ЗАКАЗ</option><option>ДОСТАВКА</option><option>ТАМОЖНЯ</option><option>ДЕПОЗИТ</option><option>ДРУГОЕ</option>
                            </select></div>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Валюта</label>
                            <select className="form-input" value={payForm.currency} onChange={e => setPayForm({ ...payForm, currency: e.target.value })} style={{ fontSize: 12 }}>
                                <option>RUB</option><option>CNY</option><option>USD</option>
                            </select></div>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Сумма</label>
                            <input className="form-input" type="number" value={payForm.amount} onChange={e => setPayForm({ ...payForm, amount: e.target.value })} style={{ fontSize: 12 }} /></div>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Курс</label>
                            <input className="form-input" type="number" step="0.01" value={payForm.fx_rate} onChange={e => setPayForm({ ...payForm, fx_rate: e.target.value })} style={{ fontSize: 12 }} /></div>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Сумма ₽</label>
                            <input className="form-input" type="number" value={payForm.amount_rub} onChange={e => setPayForm({ ...payForm, amount_rub: e.target.value })} style={{ fontSize: 12 }} /></div>
                        <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                            <button className="btn btn-primary btn-sm" onClick={addPayment}>💾 Добавить</button>
                        </div>
                    </div>
                </div>
            )}

            {/* Link transaction – dropdown-based panel (Streamlit style) */}
            {showLinkForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 20, borderRadius: 8, marginTop: 8 }}>
                    {/* Row: Платёж + Счёт */}
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
                        <div className="form-group" style={{ margin: 0 }}>
                            <label className="form-label" style={{ fontSize: 12, fontWeight: 600 }}>Платёж</label>
                            <select className="form-input" value={selPayId ?? ''} onChange={e => setSelPayId(e.target.value ? parseInt(e.target.value) : null)}
                                style={{ fontSize: 12 }}>
                                <option value="">— Выберите платёж —</option>
                                {unpaidPayments.map((p: any) => (
                                    <option key={p.id} value={p.id}>
                                        ID:{p.id} | №{p.order_no || '—'} | {p.direction} | {formatNumber(p.amount || 0)} {p.currency} ({formatNumber(p.amount_rub || 0)}₽)
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div className="form-group" style={{ margin: 0 }}>
                            <label className="form-label" style={{ fontSize: 12, fontWeight: 600 }}>Счёт</label>
                            <select className="form-input" value={selAccount} onChange={e => handleAccountChange(e.target.value)}
                                style={{ fontSize: 12 }}>
                                <option value="">Все счета</option>
                                {accounts.map((a: any) => (
                                    <option key={a.id} value={a.account}>{a.bank} ({a.currency})</option>
                                ))}
                            </select>
                        </div>
                    </div>

                    {/* Транзакция из выписки */}
                    <div className="form-group" style={{ margin: '0 0 12px 0' }}>
                        <label className="form-label" style={{ fontSize: 12, fontWeight: 600 }}>Транзакция из выписки</label>
                        <select className="form-input" value={selTxnId} onChange={e => setSelTxnId(e.target.value)}
                            style={{ fontSize: 12 }}>
                            <option value="">— Выберите транзакцию —</option>
                            {filteredCandidates.map((c: any) => (
                                <option key={c.txn_id} value={c.txn_id}>
                                    {c.date?.slice(0, 10)} | {c.currency} {formatNumber(c.expense)} | {c.counterparty || '—'}
                                </option>
                            ))}
                        </select>
                    </div>

                    {/* Назначение платежа (shown when transaction selected) */}
                    {selTxnId && (() => {
                        const sel = candidates.find((c: any) => c.txn_id === selTxnId);
                        return sel?.purpose ? (
                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 12, padding: '8px 12px', background: 'var(--color-bg)', borderRadius: 6, border: '1px solid var(--color-border)' }}>
                                <span style={{ opacity: 0.6 }}>📋 Назначение:</span> {sel.purpose}
                            </div>
                        ) : null;
                    })()}

                    {/* Сумма привязки + Привязать */}
                    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end' }}>
                        <div className="form-group" style={{ margin: 0 }}>
                            <label className="form-label" style={{ fontSize: 12, fontWeight: 600 }}>
                                Сумма привязки{selPayId ? ` (${unpaidPayments.find((p: any) => p.id === selPayId)?.currency || 'RUB'})` : ''}
                            </label>
                            <input className="form-input" type="number" value={linkAmount} onChange={e => setLinkAmount(e.target.value)}
                                style={{ fontSize: 12, width: 200 }} />
                        </div>
                        <button className="btn btn-primary btn-sm" onClick={linkTransaction}
                            disabled={!selPayId || !selTxnId}
                        >🔗 Привязать</button>
                    </div>
                </div>
            )}
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

