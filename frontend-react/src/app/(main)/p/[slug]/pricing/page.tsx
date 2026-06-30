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

// стили для дерева группировок
const thT: React.CSSProperties = { padding: '8px 10px', textAlign: 'right', fontSize: 12, color: 'var(--color-text-dim)', whiteSpace: 'nowrap' };
const thTL: React.CSSProperties = { ...thT, textAlign: 'left' };
const tdT: React.CSSProperties = { padding: '7px 10px', textAlign: 'right', fontSize: 13, whiteSpace: 'nowrap' };
const tdTL: React.CSSProperties = { ...tdT, textAlign: 'left' };
const TREE_METRIC_COLS = ['Цена ВБ', 'Себест.', 'Коэф.', 'Наценка %', 'Маржа %', 'Мин. цена', 'Остаток ВБ', 'Всего', 'Заморожено', 'Выручка', 'Прибыль'];

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
    const [groupBy, setGroupBy] = useState<'sku' | 'category' | 'size'>('category');
    const [expanded, setExpanded] = useState<Set<string>>(new Set());

    const [categoryOptions, setCategoryOptions] = useState<string[]>([]);
    const [syncing, setSyncing] = useState(false);
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
            if (!category && !anomalyOnly) {
                // полный список категорий: из строк (sku) или из групп (дерево)
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
            'Бренд': r.brand || '', 'Базовая цена': r.base_price, 'Скидка продавца %': r.discount,
            'Цена ВБ': r.current_price, 'Себестоимость': r.cost_price, 'Наценка коэф': r.markup_coef,
            'Наценка %': r.markup_pct, 'Доля себест %': r.cost_share_pct, 'Маржа %': r.margin_pct,
            'Мин. цена (безубыток)': r.breakeven_price, 'Запас прочности %': r.safety_margin_pct,
            'Эластичность': r.elasticity, 'Тип спроса': r.elasticity_label, 'Реком. цена': r.optimal_price,
            'СПП %': r.spp_rate, 'Цена покупателю': r.buyer_price, 'Заказы': r.orders_count,
            'Остаток ВБ': r.wb_stock, 'Наш склад': r.own_stock, 'В сборке': r.assembly_stock, 'В пути на ВБ': r.transit_stock, 'Всего товара': r.total_stock,
            'Новинка': r.is_new ? 'да' : '', 'Дней до исчерпания': r.days_left, 'Продаж/мес': r.sales_per_month,
            'Sell-through %': r.sell_through_pct, 'GMROI': r.gmroi, 'Заморожено (себест)': r.stock_value_cost,
            'Потенц. прибыль остатка': r.stock_potential_profit, 'Потенц. выручка остатка': r.stock_potential_revenue,
            'Выручка': r.revenue, 'Расходы ВБ': r.wb_expenses, 'Реклама': r.adv_sum, 'Налог': r.tax,
            'Прибыль': r.profit, 'Чист. наценка %': r.net_markup_pct, 'ДРР %': r.drr, 'CR (конверсия) %': r.cr,
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
        { key: 'cost_price', label: 'Себест.', align: 'right', sortable: true, render: (v) => money(v) },
        { key: 'markup_coef', label: 'Коэф.', align: 'right', sortable: true, render: (v: number | null) => (v == null ? '—' : formatNumber(v, 2) + '×') },
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
                <select className="btn btn-sm" value={groupBy} onChange={(e) => setGroupBy(e.target.value as 'sku' | 'category' | 'size')} title="Группировка">
                    <option value="category">📂 По категориям</option>
                    <option value="size">📏 По размеру</option>
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
                    {aiLoading && <div style={{ color: 'var(--color-text-dim)' }}>⏳ Claude анализирует портфель (10–20 сек)…</div>}
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

function PricingTree({ groups, mode, expanded, onToggle, loading }: {
    groups: PricingGroup[]; mode: 'category' | 'size'; expanded: Set<string>; onToggle: (k: string) => void; loading: boolean;
}) {
    if (loading) return <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>⏳ Загрузка…</div>;
    if (!groups.length) return <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>💲 Нет данных</div>;
    return (
        <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                    <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                        <th style={thTL}>{mode === 'size' ? 'Категория / Размер / Артикул' : 'Категория / Артикул'}</th>
                        {TREE_METRIC_COLS.map((h) => <th key={h} style={thT}>{h}</th>)}
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
    group: PricingGroup; mode: 'category' | 'size'; parentKey: string; expanded: Set<string>; onToggle: (k: string) => void;
}) {
    const key = parentKey ? `${parentKey}|${group.category}` : group.category;
    const open = expanded.has(key);
    const level = parentKey ? 1 : 0;
    return (
        <>
            <tr onClick={() => onToggle(key)} style={{ cursor: 'pointer', borderBottom: '1px solid var(--color-border)', background: level === 0 ? 'var(--color-bg-card)' : 'transparent' }}>
                <td style={{ ...tdTL, paddingLeft: 10 + level * 22, fontWeight: level === 0 ? 700 : 600 }}>
                    <span style={{ marginRight: 6 }}>{open ? '▾' : '▸'}</span>{group.category}
                    <span style={{ color: 'var(--color-text-dim)', fontWeight: 400, fontSize: 11, marginLeft: 8 }}>{int0(group.articles)} арт.</span>
                </td>
                <td style={tdT}>—</td>
                <td style={tdT}>—</td>
                <td style={{ ...tdT, fontWeight: 600 }}>{coef(group.markup_coef)}</td>
                <td style={{ ...tdT, color: signColor(group.markup_pct) }}>{pct(group.markup_pct)}</td>
                <td style={{ ...tdT, color: signColor(group.margin_pct) }}>{pct(group.margin_pct)}</td>
                <td style={tdT}>—</td>
                <td style={tdT}>{int0(group.wb_stock)}</td>
                <td style={tdT}>—</td>
                <td style={tdT}>{money(group.stock_value_cost)}</td>
                <td style={tdT}>{money(group.revenue)}</td>
                <td style={{ ...tdT, color: signColor(group.profit) }}>{money(group.profit)}</td>
            </tr>
            {open && group.subgroups.map((sg) => <GroupNode key={sg.category} group={sg} mode={mode} parentKey={key} expanded={expanded} onToggle={onToggle} />)}
            {open && group.children.map((r) => <LeafRow key={r.nm_id} row={r} level={level + 1} />)}
        </>
    );
}

function LeafRow({ row, level }: { row: PricingRow; level: number }) {
    return (
        <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
            <td style={{ ...tdTL, paddingLeft: 10 + level * 22 }}>
                <span style={{ fontWeight: 500 }}>{row.vendor_code || row.nm_id}</span>
                <span style={{ color: 'var(--color-text-dim)', fontSize: 11, marginLeft: 6 }}>{row.nm_id}</span>
                {row.is_new && <span style={{ color: 'var(--color-accent)', fontSize: 11, marginLeft: 6 }}>🆕</span>}
                {row.anomaly && <span style={{ color: 'var(--color-danger)', fontSize: 11, marginLeft: 6 }}>⚠ {row.anomaly}</span>}
            </td>
            <td style={tdT}>{money(row.current_price)}</td>
            <td style={tdT}>{money(row.cost_price)}</td>
            <td style={{ ...tdT, fontWeight: 600 }}>{coef(row.markup_coef)}</td>
            <td style={tdT}>{pct(row.markup_pct)}</td>
            <td style={{ ...tdT, color: signColor(row.margin_pct) }}>{pct(row.margin_pct)}</td>
            <td style={tdT}>{money(row.breakeven_price)}</td>
            <td style={tdT}>{int0(row.wb_stock)}</td>
            <td style={tdT}>{int0(row.total_stock)}</td>
            <td style={tdT}>{money(row.stock_value_cost)}</td>
            <td style={tdT}>{money(row.revenue)}</td>
            <td style={{ ...tdT, color: signColor(row.profit) }}>{money(row.profit)}</td>
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
