'use client';
import { useState, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

interface Props {
    selected: string;
    orders: any[];
    items: any[];
    onItemsReload: (orderNo: string) => void;
    onOrdersReload: () => void;
    onMsg: (msg: string) => void;
}

export function OrderItemsDetail({ selected, orders, items, onItemsReload, onOrdersReload, onMsg }: Props) {
    const [file, setFile] = useState<File | null>(null);
    const [expanded, setExpanded] = useState<string | null>(null);
    const [summary, setSummary] = useState<any>(null);

    const uploadFile = async () => {
        if (!file) return;
        try {
            await api.uploadCostFile(selected, file);
            onMsg('✅ Файл загружен! Пересчёт выполнен.');
            onItemsReload(selected);
            onOrdersReload();
        } catch (e: any) { onMsg(`❌ ${e.message}`); }
    };

    const showSummary = async (orderNo: string) => {
        if (expanded === orderNo) { setExpanded(null); return; }
        setExpanded(orderNo);
        try { setSummary(await api.getPlanningOrderSummary(orderNo)); } catch { setSummary(null); }
    };

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
        nm_id: 'nm_id', imt_id: 'imt_id', sku: 'SKU', unrecognized: 'Не распознано',
    };
    const tr = (key: string) => colMap[key.toLowerCase()] || key;
    const intCols = new Set(['id', 'nm_id', 'imt_id', 'order_no']);

    const renderCell = (key: string, v: any) => {
        if (key === 'article_wb' && v != null) {
            const nmId = typeof v === 'number' ? Math.round(v) : parseInt(String(v).replace(/\s/g, ''), 10);
            if (!isNaN(nmId)) return <a href={`https://www.wildberries.ru/catalog/${nmId}/detail.aspx`} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--color-info)', textDecoration: 'underline' }}>{nmId}</a>;
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

    const itemColumns: Column[] = useMemo(() => {
        return itemKeys.map(k => ({
            key: k,
            label: tr(k),
            render: (v: any) => <span style={{ fontSize: 12 }}>{renderCell(k, v)}</span>,
        }));
    }, [itemKeys]);

    const factPaymentColumns: Column[] = useMemo(() => [
        { key: 'date', label: 'Дата', render: (v: any) => <span style={{ fontSize: 12 }}>{v}</span> },
        { key: 'counterparty', label: 'Контрагент', render: (v: any) => <span style={{ fontSize: 12 }}>{v}</span> },
        { key: 'expense', label: 'Сумма', format: 'number' as const },
        { key: 'currency', label: 'Валюта', render: (v: any) => <span style={{ fontSize: 12 }}>{v}</span> },
    ], []);

    return (
        <>
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
                    </div>
                </div>
                {items.length > 0 ? (
                    <TanStackDataTable
                        columns={itemColumns}
                        data={items}
                        enableSorting
                        enablePagination
                        pageSize={50}
                        exportName={`order_${selected}_items`}
                        maxHeight={400}
                    />
                ) : <div style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Нет позиций. Загрузите файл Excel.</div>}
            </div>

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
                                <TanStackDataTable
                                    columns={factPaymentColumns}
                                    data={summary.fact_order_payments}
                                    enableSorting
                                    enablePagination={false}
                                />
                            </div>
                        )}
                    </div>
                )}
            </div>
        </>
    );
}
