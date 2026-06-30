'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDateTime, exportToExcel } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import { sanitizeAIHtml } from '@/lib/sanitize';
import type { Column } from '@/components/DataTable';
import type { PricingResponse, PricingRow, PricingGroup } from '@/types/api';

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
const coef = (v: number | null | undefined) => (v == null ? '—' : formatNumber(v, 2) + '×');

// ─── стиль дерева «как в воронке» (светлая таблица, секции, цвета) ──────────
const FT_NAME_H: React.CSSProperties = { position: 'sticky', left: 0, top: 0, background: '#ffffff', color: '#374151', zIndex: 22, verticalAlign: 'bottom', borderBottom: '2px solid #e5e7eb', minWidth: 250, borderRight: '1px solid #e5e7eb', padding: '8px 12px', boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.08)', textAlign: 'left', fontSize: 12, fontWeight: 700 };
const FT_SEC: React.CSSProperties = { position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb', fontSize: 12, fontWeight: 600, padding: '6px 8px', whiteSpace: 'nowrap' };
const FT_COLH: React.CSSProperties = { position: 'sticky', top: 33, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', padding: '6px 8px', textAlign: 'right', whiteSpace: 'nowrap' };
const SL: React.CSSProperties = { borderLeft: '1px solid #eef0f4' };
const nc = (bg: string, extra?: React.CSSProperties): React.CSSProperties => ({ textAlign: 'right', borderBottom: '1px solid #f3f4f6', padding: '7px 10px', fontSize: 13, color: '#111827', background: bg, whiteSpace: 'nowrap', ...(extra || {}) });
const drrCol = (v: number | null) => (v == null ? '#9ca3af' : v > 30 ? '#ef4444' : v > 15 ? '#f59e0b' : v > 0 ? '#10b981' : '#9ca3af');
const marginCol = (v: number | null) => (v == null ? '#9ca3af' : v > 20 ? '#10b981' : v > 0 ? '#65a30d' : '#ef4444');
const profitCol = (v: number | null) => ((v || 0) > 0 ? '#10b981' : (v || 0) < 0 ? '#ef4444' : '#111827');
const gmroiCol = (v: number | null) => (v == null ? '#9ca3af' : v >= 3 ? '#10b981' : v < 1 ? '#f59e0b' : '#111827');

interface TreeVM {
    price: number | null; buyerPrice: number | null; cost: number | null; coef: number | null; markup: number | null; margin: number | null; minPrice: number | null;
    adv: number; drr: number; ctr: number; cpc: number; views: number; clicks: number; wbStock: number; own: number; asm: number; transit: number; total: number; frozen: number | null; days: number | null;
    revenue: number; profit: number; gmroi: number | null; bg: string;
}
const TREE_SECTIONS: Array<[string, number]> = [['ЦЕНООБРАЗОВАНИЕ', 7], ['РЕКЛАМА', 6], ['ОСТАТКИ', 7], ['ФИНАНСЫ', 3]];
const TREE_COLS: Array<[string, boolean]> = [
    ['Цена ВБ', false], ['Цена с СПП', false], ['Себест.', false], ['Коэф.', false], ['Наценка %', false], ['Маржа %', false], ['Мин. цена', false],
    ['Расходы ₽', true], ['ДРР %', false], ['CTR %', false], ['CPC', false], ['Показы', false], ['Клики', false],
    ['Остаток ВБ', true], ['Наш склад', false], ['В сборке', false], ['В пути ВБ', false], ['Всего', false], ['Заморож. ₽', false], ['Дней', false],
    ['Выручка ₽', true], ['Прибыль ₽', false], ['GMROI', false],
];

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
    const [newOnly, setNewOnly] = useState(false);
    const [groupBy, setGroupBy] = useState<'sku' | 'category' | 'size' | 'imt'>('category');
    const [expanded, setExpanded] = useState<Set<string>>(new Set());

    const [categoryOptions, setCategoryOptions] = useState<string[]>([]);
    const [syncing, setSyncing] = useState(false);
    const [sppSyncing, setSppSyncing] = useState(false);
    const [syncMsg, setSyncMsg] = useState('');
    const [aiHtml, setAiHtml] = useState('');
    const [aiLoading, setAiLoading] = useState(false);
    const [aiErr, setAiErr] = useState('');

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
                group_by: groupBy,
            });
            if (reqRef.current !== myReq) return;
            setResp(res);
            if (!category && !anomalyOnly && res.group_by !== 'imt') {
                // полный список категорий: из строк (sku) или из групп (дерево).
                // Для склейки g.category = имя склейки — в фильтр категорий не годится.
                const raw = res.group_by === 'sku' ? res.data_rows.map((r) => r.category) : res.data_groups.map((g) => g.category);
                const cats = Array.from(new Set(raw)).sort();
                if (cats.length) setCategoryOptions((prev) => (prev.length >= cats.length ? prev : cats));
            }
        } catch (e) {
            if (reqRef.current !== myReq) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (reqRef.current === myReq) setLoading(false);
        }
    }, [dateFrom, dateTo, brand, category, search, onlyInStock, anomalyOnly, groupBy]);

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

    const doSyncSpp = async () => {
        setSppSyncing(true);
        setSyncMsg('');
        try {
            const r = await api.syncPricingSpp();
            setSyncMsg(`СПП обновлён: ${int0(r.fetched)} из ${int0(r.requested)} товаров (card-API)`);
            await loadData();
        } catch (e) {
            setSyncMsg(e instanceof Error ? e.message : 'Ошибка синка СПП');
        } finally {
            setSppSyncing(false);
        }
    };

    const doAi = async () => {
        setAiLoading(true);
        setAiErr('');
        setAiHtml('');
        try {
            const r = await api.getPricingAiRecommendations({ date_from: dateFrom, date_to: dateTo, only_in_stock: onlyInStock });
            setAiHtml(r.html);
        } catch (e) {
            setAiErr(e instanceof Error ? e.message : 'Ошибка AI');
        } finally {
            setAiLoading(false);
        }
    };

    const rows = resp?.data_rows ?? [];
    const s = resp?.summary;
    const tableRows = useMemo(() => (newOnly ? rows.filter((r) => r.is_new) : rows), [rows, newOnly]);
    // все листовые строки (для Excel) — из плоского среза или из дерева групп
    const allLeafRows = useMemo(() => {
        if (!resp) return [];
        if (resp.group_by === 'sku') return resp.data_rows;
        const out: PricingRow[] = [];
        for (const g of resp.data_groups) {
            out.push(...g.children);
            for (const sg of g.subgroups) out.push(...sg.children);
        }
        return out;
    }, [resp]);

    const doExport = () => {
        const out = allLeafRows.map((r) => ({
            'Артикул': r.vendor_code || '', 'nm_id': r.nm_id, 'Категория': r.category, 'Размер': r.size, 'ABC': r.abc || '',
            'Склейка': r.sklejka, 'Роль в склейке': r.sklejka_role, 'Доля выручки склейки %': r.rev_share_pct, 'Доля рекламы склейки %': r.adv_share_pct,
            'Бренд': r.brand || '', 'Базовая цена': r.base_price, 'Скидка продавца %': r.discount,
            'Цена ВБ': r.current_price, 'Себестоимость': r.cost_price, 'Наценка коэф': r.markup_coef,
            'Наценка %': r.markup_pct, 'Доля себест %': r.cost_share_pct, 'Маржа %': r.margin_pct,
            'Мин. цена (с рекламой)': r.breakeven_with_adv, 'Запас прочности %': r.safety_margin_pct,
            'Эластичность': r.elasticity, 'Тип спроса': r.elasticity_label, 'Реком. цена': r.optimal_price,
            'СПП сейчас %': r.spp_rate, 'Цена с СПП': r.buyer_price, 'Заказы': r.orders_count,
            'Остаток ВБ': r.wb_stock, 'Наш склад': r.own_stock, 'В сборке': r.assembly_stock, 'В пути на ВБ': r.transit_stock, 'Всего товара': r.total_stock,
            'Новинка': r.is_new ? 'да' : '', 'Дней до исчерпания': r.days_left, 'Продаж/мес': r.sales_per_month,
            'Sell-through %': r.sell_through_pct, 'GMROI': r.gmroi, 'Заморожено (себест)': r.stock_value_cost,
            'Потенц. прибыль остатка': r.stock_potential_profit, 'Потенц. выручка остатка': r.stock_potential_revenue,
            'Выручка': r.revenue, 'Расходы ВБ': r.wb_expenses, 'Реклама': r.adv_sum, 'Налог': r.tax,
            'Прибыль': r.profit, 'Чист. наценка %': r.net_markup_pct, 'ДРР %': r.drr, 'CR (конверсия) %': r.cr,
            'CTR %': r.ctr, 'CPC': r.cpc, 'Показы': r.adv_views, 'Клики': r.adv_clicks,
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
                    <div style={{ fontWeight: 500 }}>
                        {r.vendor_code || r.nm_id}
                        {r.is_new && <span style={{ color: 'var(--color-accent)', fontSize: 11, marginLeft: 6 }}>🆕 новинка</span>}
                    </div>
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
        {
            key: 'buyer_price', label: 'Цена с СПП', align: 'right', sortable: true,
            render: (v: number | null, r: PricingRow) => (
                <span title={`СПП сейчас ${pct(r.spp_rate)}`} style={{ fontWeight: 600 }}>{money(v)}</span>
            ),
        },
        { key: 'cost_price', label: 'Себест.', align: 'right', sortable: true, render: (v) => money(v) },
        { key: 'markup_coef', label: 'Коэф.', align: 'right', sortable: true, render: (v: number | null) => (v == null ? '—' : formatNumber(v, 2) + '×') },
        { key: 'markup_pct', label: 'Наценка %', align: 'right', sortable: true, render: (v) => pct(v) },
        { key: 'margin_pct', label: 'Маржа %', align: 'right', sortable: true, render: (v) => R(pct(v), signColor(v)) },
        { key: 'breakeven_with_adv', label: 'Мин. цена', align: 'right', sortable: true, render: (v) => money(v) },
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
        { key: 'wb_stock', label: 'Остаток ВБ', align: 'right', sortable: true, render: (v) => int0(v) },
        { key: 'own_stock', label: 'Наш склад', align: 'right', sortable: true, render: (v) => int0(v) },
        { key: 'assembly_stock', label: 'В сборке', align: 'right', sortable: true, render: (v) => int0(v) },
        { key: 'transit_stock', label: 'В пути ВБ', align: 'right', sortable: true, render: (v) => int0(v) },
        { key: 'total_stock', label: 'Всего', align: 'right', sortable: true, render: (v) => R(int0(v), 'var(--color-text)') },
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
        { key: 'cr', label: 'CR %', align: 'right', sortable: true, render: (v) => pct(v) },
        { key: 'ctr', label: 'CTR %', align: 'right', sortable: true, render: (v) => pct(v) },
        { key: 'cpc', label: 'CPC', align: 'right', sortable: true, render: (v) => money(v) },
        { key: 'adv_views', label: 'Показы', align: 'right', sortable: true, render: (v) => int0(v) },
        { key: 'adv_clicks', label: 'Клики', align: 'right', sortable: true, render: (v) => int0(v) },
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
                <select className="btn btn-sm" value={groupBy} onChange={(e) => setGroupBy(e.target.value as 'sku' | 'category' | 'size' | 'imt')} title="Группировка">
                    <option value="category">📂 По категориям</option>
                    <option value="size">📏 По размеру</option>
                    <option value="imt">🧬 По склейке</option>
                    <option value="sku">📋 По артикулам</option>
                </select>
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
                <label style={{ fontSize: 12, display: 'flex', gap: 4, alignItems: 'center', cursor: 'pointer', color: newOnly ? 'var(--color-accent)' : undefined }}>
                    <input type="checkbox" checked={newOnly} onChange={(e) => setNewOnly(e.target.checked)} />
                    🆕 только новинки
                </label>
                <div style={{ flex: 1 }} />
                <button className="btn btn-sm btn-secondary" onClick={doExport} disabled={allLeafRows.length === 0}>📥 Excel</button>
                <button className="btn btn-sm btn-success" onClick={doAi} disabled={aiLoading || allLeafRows.length === 0}>
                    {aiLoading ? '🤖 Анализ…' : '🤖 AI-рекомендации'}
                </button>
                <button className="btn btn-sm btn-secondary" onClick={doSyncSpp} disabled={sppSyncing} title="Реальная цена покупателя с СПП из card-API">
                    {sppSyncing ? '⏳ СПП…' : '🔄 СПП'}
                </button>
                <button className="btn btn-sm btn-primary" onClick={doSync} disabled={syncing}>
                    {syncing ? '⏳ Обновление…' : '🔄 Обновить цены'}
                </button>
            </div>

            {/* AI-панель */}
            {(aiLoading || aiHtml || aiErr) && (
                <div className="glass-card animate-in" style={{ padding: 18, marginBottom: 16, borderLeft: '3px solid var(--color-success)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                        <span style={{ fontSize: 15, fontWeight: 600 }}>🤖 AI-рекомендации</span>
                        {aiHtml && (
                            <button className="btn btn-sm btn-secondary" style={{ marginLeft: 'auto' }} onClick={() => setAiHtml('')}>Скрыть</button>
                        )}
                    </div>
                    {aiLoading && <div style={{ color: 'var(--color-text-dim)' }}>⏳ Claude детально анализирует портфель и динамику (30–60 сек)…</div>}
                    {aiErr && <div style={{ color: 'var(--color-danger)' }}>❌ {aiErr}</div>}
                    {aiHtml && (
                        <div style={{ fontSize: 14, lineHeight: 1.55 }} dangerouslySetInnerHTML={{ __html: sanitizeAIHtml(aiHtml) }} />
                    )}
                </div>
            )}

            {/* KPI */}
            {s && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8, marginBottom: 16 }}>
                    <Kpi label="Артикулов" value={int0(s.total_articles)} sub={`с ценой ${int0(s.priced_articles)} · с себест. ${int0(s.costed_articles)}`} />
                    <Kpi label="Наценка (портфель)" value={pct(s.markup_pct)} color={signColor(s.markup_pct)} />
                    <Kpi label="Маржа" value={pct(s.margin_pct)} color={signColor(s.margin_pct)} />
                    <Kpi label="Выручка" value={money(s.revenue) + ' ₽'} />
                    <Kpi label="Прибыль" value={money(s.profit) + ' ₽'} color={signColor(s.profit)} />
                    <Kpi label="Остаток, шт" value={int0(s.total_stock_units)} sub={`на ВБ ${int0(s.wb_stock_units)}`} />
                    <Kpi label="Заморожено" value={money(s.stock_value_cost) + ' ₽'} sub="весь товар по себест." />
                    <Kpi label="Аномалии" value={int0(s.anomalies)} color={s.anomalies > 0 ? 'var(--color-danger)' : 'var(--color-success)'} />
                </div>
            )}

            {error && <div style={{ padding: 16, color: 'var(--color-danger)' }}>❌ {error}</div>}
            {!error && groupBy === 'sku' && (
                <TanStackDataTable
                    columns={columns}
                    data={tableRows}
                    loading={loading}
                    emptyIcon="💲"
                    emptyText="Нет данных. Проверьте, что цены синхронизированы и заданы себестоимости."
                    pageSize={50}
                    maxHeight={640}
                />
            )}
            {!error && groupBy !== 'sku' && (
                <PricingTree
                    groups={resp?.data_groups ?? []}
                    mode={groupBy}
                    expanded={expanded}
                    onToggle={(k) => setExpanded((prev) => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n; })}
                    loading={loading}
                />
            )}
        </div>
    );
}

function MetricTds({ vm }: { vm: TreeVM }) {
    return (
        <>
            <td style={nc(vm.bg)}>{money(vm.price)}</td>
            <td style={nc(vm.bg, { fontWeight: 600 })}>{money(vm.buyerPrice)}</td>
            <td style={nc(vm.bg)}>{money(vm.cost)}</td>
            <td style={nc(vm.bg, { fontWeight: 600 })}>{coef(vm.coef)}</td>
            <td style={nc(vm.bg)}>{pct(vm.markup)}</td>
            <td style={nc(vm.bg, { color: marginCol(vm.margin) })}>{pct(vm.margin)}</td>
            <td style={nc(vm.bg)}>{money(vm.minPrice)}</td>
            <td style={nc(vm.bg, { ...SL, color: (vm.adv || 0) > 0 ? '#f97316' : '#9ca3af' })}>{money(vm.adv)}</td>
            <td style={nc(vm.bg, { color: drrCol(vm.drr), fontWeight: (vm.drr || 0) > 30 ? 600 : 400 })}>{pct(vm.drr)}</td>
            <td style={nc(vm.bg)}>{pct(vm.ctr)}</td>
            <td style={nc(vm.bg)}>{money(vm.cpc)}</td>
            <td style={nc(vm.bg)}>{int0(vm.views)}</td>
            <td style={nc(vm.bg)}>{int0(vm.clicks)}</td>
            <td style={nc(vm.bg, SL)}>{int0(vm.wbStock)}</td>
            <td style={nc(vm.bg)}>{int0(vm.own)}</td>
            <td style={nc(vm.bg)}>{int0(vm.asm)}</td>
            <td style={nc(vm.bg)}>{int0(vm.transit)}</td>
            <td style={nc(vm.bg, { fontWeight: 600 })}>{int0(vm.total)}</td>
            <td style={nc(vm.bg)}>{money(vm.frozen)}</td>
            <td style={nc(vm.bg)}>{vm.days == null ? '—' : int0(vm.days)}</td>
            <td style={nc(vm.bg, SL)}>{money(vm.revenue)}</td>
            <td style={nc(vm.bg, { fontWeight: 700, color: profitCol(vm.profit), background: (vm.profit || 0) > 0 ? '#f0fdf4' : (vm.profit || 0) < 0 ? '#fef2f2' : vm.bg })}>{money(vm.profit)}</td>
            <td style={nc(vm.bg, { color: gmroiCol(vm.gmroi) })}>{vm.gmroi == null ? '—' : num2(vm.gmroi)}</td>
        </>
    );
}

function PricingTree({ groups, mode, expanded, onToggle, loading }: {
    groups: PricingGroup[]; mode: 'category' | 'size' | 'imt'; expanded: Set<string>; onToggle: (k: string) => void; loading: boolean;
}) {
    if (loading) return <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>⏳ Загрузка…</div>;
    if (!groups.length) return <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>💲 Нет данных</div>;
    return (
        <div className="glass-card" style={{ padding: 0, overflow: 'auto', maxHeight: 'calc(100vh - 360px)' }}>
            <table style={{ minWidth: 1800, borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#ffffff' }}>
                <thead>
                    <tr>
                        <th rowSpan={2} style={FT_NAME_H}>{mode === 'size' ? 'КАТЕГОРИЯ → РАЗМЕР → SKU' : mode === 'imt' ? 'СКЛЕЙКА → ВАРИАНТ' : 'КАТЕГОРИЯ → SKU'}</th>
                        {TREE_SECTIONS.map(([label, span], i) => (
                            <th key={label} colSpan={span} style={i === 0 ? FT_SEC : { ...FT_SEC, borderLeft: '1px solid #e5e7eb' }}>{label}</th>
                        ))}
                    </tr>
                    <tr>
                        {TREE_COLS.map(([label, left]) => (
                            <th key={label} style={left ? { ...FT_COLH, borderLeft: '1px solid #e5e7eb' } : FT_COLH}>{label}</th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {groups.map((g) => <GroupNode key={g.category} group={g} mode={mode} parentKey="" expanded={expanded} onToggle={onToggle} />)}
                </tbody>
            </table>
        </div>
    );
}

function GroupNode({ group, mode, parentKey, expanded, onToggle }: {
    group: PricingGroup; mode: 'category' | 'size' | 'imt'; parentKey: string; expanded: Set<string>; onToggle: (k: string) => void;
}) {
    const key = parentKey ? `${parentKey}|${group.category}` : group.category;
    const open = expanded.has(key);
    const level = parentKey ? 1 : 0;
    const bg = level === 0 ? '#eef2ff' : '#f5f7fb';
    const gmroi = group.stock_value_cost > 0 ? (group.revenue - group.cost_total) / group.stock_value_cost : null;
    const vm: TreeVM = {
        price: null, buyerPrice: null, cost: null, coef: group.markup_coef, markup: group.markup_pct, margin: group.margin_pct, minPrice: null,
        adv: group.adv_sum, drr: group.drr, ctr: group.ctr ?? 0, cpc: group.cpc ?? 0, views: group.adv_views ?? 0, clicks: group.adv_clicks ?? 0, wbStock: group.wb_stock, own: group.own_stock, asm: group.assembly_stock, transit: group.transit_stock,
        total: group.total_stock, frozen: group.stock_value_cost, days: null, revenue: group.revenue, profit: group.profit, gmroi, bg,
    };
    return (
        <>
            <tr onClick={() => onToggle(key)} style={{ cursor: 'pointer' }}>
                <td style={{ position: 'sticky', left: 0, background: bg, zIndex: 2, padding: '8px 12px', paddingLeft: 12 + level * 20, borderRight: '1px solid #e5e7eb', borderBottom: '1px solid #f3f4f6', boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.05)', minWidth: 250, fontWeight: level === 0 ? 700 : 600, color: '#111827', whiteSpace: 'nowrap' }}>
                    <span style={{ marginRight: 6 }}>{open ? '▾' : '▸'}</span>{group.category}
                    <span style={{ color: '#9ca3af', fontWeight: 400, fontSize: 11, marginLeft: 8 }}>{int0(group.articles)} арт.</span>
                    {mode === 'imt' && (
                        <span style={{ color: '#9ca3af', fontWeight: 400, fontSize: 11, marginLeft: 8 }}>
                            рекл. {int0(group.advertised_variants)}/{int0(group.articles)} · продаёт {int0(group.converting_variants)}
                        </span>
                    )}
                </td>
                <MetricTds vm={vm} />
            </tr>
            {open && group.subgroups.map((sg) => <GroupNode key={sg.category} group={sg} mode={mode} parentKey={key} expanded={expanded} onToggle={onToggle} />)}
            {open && group.children.map((r) => <LeafRow key={r.nm_id} row={r} level={level + 1} />)}
        </>
    );
}

function LeafRow({ row, level }: { row: PricingRow; level: number }) {
    const bg = '#ffffff';
    const vm: TreeVM = {
        price: row.current_price, buyerPrice: row.buyer_price, cost: row.cost_price, coef: row.markup_coef, markup: row.markup_pct, margin: row.margin_pct, minPrice: row.breakeven_with_adv,
        adv: row.adv_sum, drr: row.drr, ctr: row.ctr, cpc: row.cpc, views: row.adv_views, clicks: row.adv_clicks, wbStock: row.wb_stock, own: row.own_stock, asm: row.assembly_stock, transit: row.transit_stock,
        total: row.total_stock, frozen: row.stock_value_cost, days: row.days_left, revenue: row.revenue, profit: row.profit, gmroi: row.gmroi, bg,
    };
    return (
        <tr>
            <td style={{ position: 'sticky', left: 0, background: bg, zIndex: 2, padding: '7px 12px', paddingLeft: 12 + level * 20, borderRight: '1px solid #e5e7eb', borderBottom: '1px solid #f3f4f6', boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.05)', minWidth: 250, whiteSpace: 'nowrap' }}>
                <span style={{ fontWeight: 500, fontSize: 13, color: '#111827' }}>{row.vendor_code || row.nm_id}</span>
                <span style={{ color: '#9ca3af', fontSize: 11, marginLeft: 6 }}>#{row.nm_id}</span>
                {row.is_new && <span style={{ color: '#3b82f6', fontSize: 11, marginLeft: 6 }}>🆕</span>}
                {row.sklejka_role && (
                    <span style={{ marginLeft: 6, fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 24, color: '#ffffff', background: row.sklejka_role === 'якорь' ? '#3b82f6' : '#f59e0b' }}>
                        {row.sklejka_role === 'якорь' ? '⚓ якорь' : '🩸 донор'}
                    </span>
                )}
                {row.rev_share_pct != null && (
                    <span style={{ color: '#9ca3af', fontSize: 10, marginLeft: 6 }}>
                        реклама {pct(row.adv_share_pct)} · выручка {pct(row.rev_share_pct)} склейки
                    </span>
                )}
                {row.anomaly && <div style={{ color: '#ef4444', fontSize: 11 }}>⚠ {row.anomaly}</div>}
            </td>
            <MetricTds vm={vm} />
        </tr>
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
