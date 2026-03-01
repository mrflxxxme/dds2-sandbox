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
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Заказы (Себестоимость)</h3>
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
                const itemKeys = items.length > 0 ? Object.keys(items[0]) : [];

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
    const [rules, setRules] = useState<any[]>([]);
    const [categories, setCategories] = useState<string[]>([]);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ subject: '', basis: 'INVOICE', rate: '0', util_collect_rub: '0', note: '' });
    const [msg, setMsg] = useState('');

    useEffect(() => { loadRules(); loadCategories(); }, []);
    const loadRules = async () => { try { setRules(await api.getDutyRules()); } catch { } };
    const loadCategories = async () => {
        try {
            const nom = await api.getNomenclature();
            const subjects = [...new Set(nom.map((n: any) => n.subject).filter(Boolean))].sort() as string[];
            setCategories(subjects);
        } catch { }
    };

    const save = async () => {
        if (!form.subject) { setMsg('❌ Выберите категорию'); return; }
        try {
            await api.addDutyRule({
                subject: form.subject,
                basis: form.basis,
                rate: parseFloat(form.rate) || 0,
                util_collect_rub: parseFloat(form.util_collect_rub) || 0,
                note: form.note || null,
            });
            setMsg('✅ Правило сохранено!');
            setShowForm(false);
            setForm({ subject: '', basis: 'INVOICE', rate: '0', util_collect_rub: '0', note: '' });
            loadRules();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };
    const del = async (id: number) => {
        if (!confirm('Удалить правило?')) return;
        try { await api.deleteDutyRule(id); loadRules(); } catch (e: any) { setMsg(e.message); }
    };
    const editRule = (r: any) => {
        setForm({ subject: r.subject, basis: r.basis, rate: String(r.rate), util_collect_rub: String(r.util_collect_rub), note: r.note || '' });
        setShowForm(true);
    };

    const basisLabels: Record<string, string> = {
        INVOICE: 'От инвойса (%)',
        WEIGHT_EUR_KG: 'От веса (€/кг)',
        SQUARE_METER: 'За м² (€/м²)',
    };

    // Categories without a rule yet
    const ruledSubjects = new Set(rules.map(r => r.subject));
    const unruled = categories.filter(c => !ruledSubjects.has(c));

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div>
                    <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Пошлина / Утиль — по категориям</h3>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 2 }}>
                        Категорий: {categories.length} | С правилами: {rules.length} | Без правил: {unruled.length}
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(rules, 'duty_rules')}>📥 Excel</button>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Добавить правило</button>
                </div>
            </div>
            {msg && <div style={{ fontSize: 13, marginBottom: 8, padding: '6px 12px', borderRadius: 6, background: msg.startsWith('✅') ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)', color: msg.startsWith('✅') ? 'var(--color-success)' : 'var(--color-danger)' }}>{msg} <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span></div>}

            {showForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16 }}>
                    <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Правило пошлины</h4>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12 }}>
                        <div className="form-group">
                            <label className="form-label" style={{ fontSize: 11 }}>Категория (subject)*</label>
                            <select className="form-input" value={form.subject} onChange={e => setForm({ ...form, subject: e.target.value })} style={{ fontSize: 13 }}>
                                <option value="">— Выбрать —</option>
                                {categories.map(c => <option key={c} value={c}>{c}</option>)}
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label" style={{ fontSize: 11 }}>Базис расчёта</label>
                            <select className="form-input" value={form.basis} onChange={e => setForm({ ...form, basis: e.target.value })} style={{ fontSize: 13 }}>
                                <option value="INVOICE">От инвойса (%)</option>
                                <option value="WEIGHT_EUR_KG">От веса (€/кг)</option>
                                <option value="SQUARE_METER">За м² (€/м²)</option>
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label" style={{ fontSize: 11 }}>Ставка</label>
                            <input className="form-input" type="number" step="0.01" value={form.rate}
                                onChange={e => setForm({ ...form, rate: e.target.value })} style={{ fontSize: 13 }} />
                        </div>
                        <div className="form-group">
                            <label className="form-label" style={{ fontSize: 11 }}>Утиль. сбор (₽)</label>
                            <input className="form-input" type="number" step="0.01" value={form.util_collect_rub}
                                onChange={e => setForm({ ...form, util_collect_rub: e.target.value })} style={{ fontSize: 13 }} />
                        </div>
                        <div className="form-group">
                            <label className="form-label" style={{ fontSize: 11 }}>Примечание</label>
                            <input className="form-input" value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} style={{ fontSize: 13 }} />
                        </div>
                    </div>
                    <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                        <button className="btn btn-primary btn-sm" onClick={save}>💾 Сохранить</button>
                        <button className="btn btn-secondary btn-sm" onClick={() => setShowForm(false)}>Отмена</button>
                    </div>
                </div>
            )}

            {rules.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr><th>ID</th><th>Категория</th><th>Базис</th><th>Ставка</th><th>Утиль ₽</th><th>Примечание</th><th></th></tr></thead>
                        <tbody>{rules.map(r => (
                            <tr key={r.id}>
                                <td style={{ fontSize: 12 }}>{r.id}</td>
                                <td style={{ fontWeight: 500 }}>{r.subject}</td>
                                <td><span className="badge badge-info" style={{ fontSize: 10 }}>{basisLabels[r.basis] || r.basis}</span></td>
                                <td style={{ fontFamily: 'monospace' }}>{r.rate}</td>
                                <td style={{ fontFamily: 'monospace' }}>{r.util_collect_rub}</td>
                                <td style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>{r.note || '—'}</td>
                                <td style={{ display: 'flex', gap: 4 }}>
                                    <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => editRule(r)}>✎</button>
                                    <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(r.id)}>✕</button>
                                </td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет правил. Добавьте правила для категорий из Номенклатуры.</div></div>}

            {unruled.length > 0 && (
                <div style={{ marginTop: 16, padding: 12, background: 'rgba(245,158,11,0.08)', borderRadius: 8, border: '1px solid rgba(245,158,11,0.2)' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-warning)', marginBottom: 8 }}>⚠️ Категории без правил ({unruled.length}):</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {unruled.slice(0, 30).map(c => (
                            <span key={c} className="badge badge-warning" style={{ fontSize: 10, cursor: 'pointer' }}
                                onClick={() => { setForm({ ...form, subject: c }); setShowForm(true); }}>{c}</span>
                        ))}
                        {unruled.length > 30 && <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>...и ещё {unruled.length - 30}</span>}
                    </div>
                </div>
            )}
        </div>
    );
}
