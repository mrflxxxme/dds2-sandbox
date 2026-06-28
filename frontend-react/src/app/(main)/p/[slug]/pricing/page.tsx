'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDateTime, exportToExcel } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { PricingResponse, PricingRow } from '@/types/api';

// ─── format helpers ──────────────────────────────────────────────────────
const money = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 0));
const pct = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 1) + '%');
const int0 = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 0));
const num2 = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 2));
const signColor = (n: number | null | undefined) =>
    n == null ? 'var(--color-text-dim)' : n >= 0 ? 'var(--color-success)' : 'var(--color-danger)';

const today = () => new Date().toISOString().slice(0, 10);
const monthStart = () => {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
};

const R = (v: React.ReactNode, color?: string): React.ReactNode => (
    <span style={color ? { color } : undefined}>{v}</span>
);

export default function PricingPage() {
    const [resp, setResp] = useState<PricingResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const [dateFrom, setDateFrom] = useState(monthStart());
    const [dateTo, setDateTo] = useState(today());
    const [brand, setBrand] = useState('');
    const [category, setCategory] = useState('');
    const [search, setSearch] = useState('');
    const [onlyInStock, setOnlyInStock] = useState(true);
    const [anomalyOnly, setAnomalyOnly] = useState(false);

    const [categoryOptions, setCategoryOptions] = useState<string[]>([]);
    const [syncing, setSyncing] = useState(false);
    const [syncMsg, setSyncMsg] = useState('');

    const reqRef = useRef(0);
    const loadData = useCallback(async () => {
        const myReq = ++reqRef.current;
        setLoading(true);
        setError('');
        try {
            const res = await api.getPricingMarkup({
                date_from: dateFrom,
                date_to: dateTo,
                brand: brand || undefined,
                category: category || undefined,
                search: search || undefined,
                only_in_stock: onlyInStock || undefined,
                anomaly_only: anomalyOnly || undefined,
                group_by: 'sku',
            });
            if (reqRef.current !== myReq) return;
            setResp(res);
            if (!category && !anomalyOnly) {
                // полный список категорий берём из несуженного среза
                const cats = Array.from(new Set(res.data_rows.map((r) => r.category))).sort();
                if (cats.length) setCategoryOptions((prev) => (prev.length >= cats.length ? prev : cats));
            }
        } catch (e) {
            if (reqRef.current !== myReq) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (reqRef.current === myReq) setLoading(false);
        }
    }, [dateFrom, dateTo, brand, category, search, onlyInStock, anomalyOnly]);

    useEffect(() => {
        const t = setTimeout(loadData, 250);
        return () => clearTimeout(t);
    }, [loadData]);

    const doSync = async () => {
        setSyncing(true);
        setSyncMsg('');
        try {
            const r = await api.syncPricing();
            setSyncMsg(r.status === 'OK' ? `Синхронизировано: ${int0(r.rows)} цен` : `Ошибка: ${r.message || r.status}`);
            await loadData();
        } catch (e) {
            setSyncMsg(e instanceof Error ? e.message : 'Ошибка синка');
        } finally {
            setSyncing(false);
        }
    };

    const rows = resp?.data_rows ?? [];
    const s = resp?.summary;

    const doExport = () => {
        const out = rows.map((r) => ({
            'Артикул': r.vendor_code || '', 'nm_id': r.nm_id, 'Категория': r.category, 'ABC': r.abc || '',
            'Бренд': r.brand || '', 'Базовая цена': r.base_price, 'Скидка продавца %': r.discount,
            'Цена ВБ': r.current_price, 'Себестоимость': r.cost_price, 'Наценка коэф': r.markup_coef,
            'Наценка %': r.markup_pct, 'Доля себест %': r.cost_share_pct, 'Маржа %': r.margin_pct,
            'Мин. цена (безубыток)': r.breakeven_price, 'Запас прочности %': r.safety_margin_pct,
            'Эластичность': r.elasticity, 'Тип спроса': r.elasticity_label, 'Реком. цена': r.optimal_price,
            'СПП %': r.spp_rate, 'Цена покупателю': r.buyer_price, 'Заказы': r.orders_count,
            'Остаток ВБ': r.wb_stock, 'Дней до исчерпания': r.days_left, 'Продаж/мес': r.sales_per_month,
            'Sell-through %': r.sell_through_pct, 'GMROI': r.gmroi, 'Заморожено (себест)': r.stock_value_cost,
            'Потенц. прибыль остатка': r.stock_potential_profit, 'Потенц. выручка остатка': r.stock_potential_revenue,
            'Выручка': r.revenue, 'Расходы ВБ': r.wb_expenses, 'Реклама': r.adv_sum, 'Налог': r.tax,
            'Прибыль': r.profit, 'Чист. наценка %': r.net_markup_pct, 'ДРР %': r.drr,
            'Аномалия': r.anomaly || '', 'Рекомендация': r.recommendation,
        }));
        exportToExcel(out, 'Ценообразование');
    };

    const columns: Column[] = useMemo(() => [
        {
            key: 'vendor_code', label: 'Артикул', width: '230px', sortable: true,
            getValue: (r: PricingRow) => r.vendor_code || String(r.nm_id),
            render: (_v, r: PricingRow) => (
                <div style={{ lineHeight: 1.3 }}>
                    <div style={{ fontWeight: 500 }}>{r.vendor_code || r.nm_id}</div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                        {r.nm_id} · {r.category}
                    </div>
                    {r.anomaly && (
                        <div style={{ fontSize: 11, color: 'var(--color-danger)', fontWeight: 600 }}>⚠ {r.anomaly}</div>
                    )}
                </div>
            ),
        },
        {
            key: 'abc', label: 'ABC', align: 'center', sortable: true,
            render: (v: string | null) =>
                R(v || '—', v === 'A' ? 'var(--color-success)' : v === 'C' ? 'var(--color-text-dim)' : undefined),
        },
        { key: 'current_price', label: 'Цена ВБ', align: 'right', sortable: true, render: (v) => money(v) },
        { key: 'cost_price', label: 'Себест.', align: 'right', sortable: true, render: (v) => money(v) },
        { key: 'markup_pct', label: 'Наценка %', align: 'right', sortable: true, render: (v) => pct(v) },
        { key: 'margin_pct', label: 'Маржа %', align: 'right', sortable: true, render: (v) => R(pct(v), signColor(v)) },
        { key: 'breakeven_price', label: 'Мин. цена', align: 'right', sortable: true, render: (v) => money(v) },
        {
            key: 'safety_margin_pct', label: 'Запас %', align: 'right', sortable: true,
            render: (v: number | null) =>
                R(pct(v), v == null ? undefined : v < 0 ? 'var(--color-danger)' : v < 15 ? 'var(--color-warning)' : 'var(--color-success)'),
        },
        {
            key: 'elasticity', label: 'Эласт.', align: 'right', sortable: true,
            render: (v: number | null, r: PricingRow) =>
                v == null ? '—' : (
                    <span title={r.elasticity_label} style={{ color: r.elasticity_label === 'эластичный' ? 'var(--color-warning)' : 'var(--color-text)' }}>
                        {num2(v)}
                    </span>
                ),
        },
        {
            key: 'optimal_price', label: 'Реком. цена', align: 'right', sortable: true,
            render: (v: number | null, r: PricingRow) => {
                if (v == null) return '—';
                const c = r.current_price;
                const color = c ? (v > c * 1.05 ? 'var(--color-success)' : v < c * 0.95 ? 'var(--color-danger)' : undefined) : undefined;
                return R(money(v), color);
            },
        },
        { key: 'wb_stock', label: 'Остаток', align: 'right', sortable: true, render: (v) => int0(v) },
        { key: 'days_left', label: 'Дней', align: 'right', sortable: true, render: (v) => int0(v) },
        { key: 'sell_through_pct', label: 'Sell-thr %', align: 'right', sortable: true, render: (v) => pct(v) },
        {
            key: 'gmroi', label: 'GMROI', align: 'right', sortable: true,
            render: (v: number | null) =>
                R(num2(v), v == null ? undefined : v >= 3 ? 'var(--color-success)' : v < 1 ? 'var(--color-warning)' : undefined),
        },
        { key: 'stock_value_cost', label: 'Заморожено', align: 'right', sortable: true, render: (v) => money(v) },
        { key: 'revenue', label: 'Выручка', align: 'right', sortable: true, render: (v) => money(v) },
        { key: 'profit', label: 'Прибыль', align: 'right', sortable: true, render: (v) => R(money(v), signColor(v)) },
        { key: 'drr', label: 'ДРР %', align: 'right', sortable: true, render: (v) => pct(v) },
        {
            key: 'recommendation', label: 'Рекомендация', width: '210px', sortable: true,
            render: (v: string, r: PricingRow) =>
                R(v, r.anomaly ? 'var(--color-danger)' : v && v !== 'OK' ? 'var(--color-warning)' : 'var(--color-text-dim)'),
        },
    ], []);

    return (
        <div className="animate-in" style={{ padding: 24 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
                <h1 style={{ fontSize: 28, fontWeight: 700, margin: 0 }}>💲 Ценообразование</h1>
                <span style={{ color: 'var(--color-text-dim)', fontSize: 13 }}>
                    Наценка, юнит-экономика и сигналы для решений о цене по каждому артикулу
                </span>
            </div>
            <div style={{ color: 'var(--color-text-dim)', fontSize: 12, marginTop: 6 }}>
                {resp?.price_synced_at
                    ? `Цены синхронизированы: ${formatDateTime(resp.price_synced_at)}`
                    : 'Цены ещё не синхронизированы — нажмите «Обновить цены»'}
                {syncMsg ? ` · ${syncMsg}` : ''}
            </div>

            {/* Фильтры */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', margin: '16px 0' }}>
                <input type="date" className="btn btn-sm" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
                <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                <input type="date" className="btn btn-sm" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
                <select className="btn btn-sm" value={category} onChange={(e) => setCategory(e.target.value)}>
                    <option value="">Все категории</option>
                    {categoryOptions.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
                <input className="btn btn-sm" placeholder="Бренд" value={brand} onChange={(e) => setBrand(e.target.value)} style={{ width: 110 }} />
                <input className="btn btn-sm" placeholder="🔍 Артикул / nm_id" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 150 }} />
                <label style={{ fontSize: 12, display: 'flex', gap: 4, alignItems: 'center', cursor: 'pointer' }}>
                    <input type="checkbox" checked={onlyInStock} onChange={(e) => setOnlyInStock(e.target.checked)} />
                    с остатком ВБ
                </label>
                <label style={{ fontSize: 12, display: 'flex', gap: 4, alignItems: 'center', cursor: 'pointer', color: anomalyOnly ? 'var(--color-danger)' : undefined }}>
                    <input type="checkbox" checked={anomalyOnly} onChange={(e) => setAnomalyOnly(e.target.checked)} />
                    ⚠ только аномалии{s?.anomalies ? ` (${s.anomalies})` : ''}
                </label>
                <div style={{ flex: 1 }} />
                <button className="btn btn-sm btn-secondary" onClick={doExport} disabled={rows.length === 0}>📥 Excel</button>
                <button className="btn btn-sm btn-primary" onClick={doSync} disabled={syncing}>
                    {syncing ? '⏳ Обновление…' : '🔄 Обновить цены'}
                </button>
            </div>

            {/* KPI */}
            {s && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8, marginBottom: 16 }}>
                    <Kpi label="Артикулов" value={int0(s.total_articles)} sub={`с ценой ${int0(s.priced_articles)} · с себест. ${int0(s.costed_articles)}`} />
                    <Kpi label="Наценка (портфель)" value={pct(s.markup_pct)} color={signColor(s.markup_pct)} />
                    <Kpi label="Маржа" value={pct(s.margin_pct)} color={signColor(s.margin_pct)} />
                    <Kpi label="Выручка" value={money(s.revenue) + ' ₽'} />
                    <Kpi label="Прибыль" value={money(s.profit) + ' ₽'} color={signColor(s.profit)} />
                    <Kpi label="Остаток ВБ, шт" value={int0(s.wb_stock_units)} />
                    <Kpi label="Заморожено" value={money(s.stock_value_cost) + ' ₽'} sub="по себестоимости" />
                    <Kpi label="Аномалии" value={int0(s.anomalies)} color={s.anomalies > 0 ? 'var(--color-danger)' : 'var(--color-success)'} />
                </div>
            )}

            {error && <div style={{ padding: 16, color: 'var(--color-danger)' }}>❌ {error}</div>}
            {!error && (
                <TanStackDataTable
                    columns={columns}
                    data={rows}
                    loading={loading}
                    emptyIcon="💲"
                    emptyText="Нет данных. Проверьте, что цены синхронизированы и заданы себестоимости."
                    pageSize={50}
                    maxHeight={640}
                />
            )}
        </div>
    );
}

function Kpi({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
    return (
        <div className="glass-card" style={{ padding: 14 }}>
            <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>{label}</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: color || 'var(--color-text)' }}>{value}</div>
            {sub && <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 2 }}>{sub}</div>}
        </div>
    );
}
