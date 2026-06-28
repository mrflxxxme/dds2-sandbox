'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDateTime, exportToExcel } from '@/lib/utils';
import type { PricingResponse, PricingGroup, PricingRow } from '@/types/api';

// ─── format helpers ──────────────────────────────────────────────────────
const fmtMoney = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 0));
const fmtPct = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 1) + '%');
const fmtCoef = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 2) + '×');
const fmtInt = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 0));

const pctColor = (n: number | null | undefined) =>
    n == null ? 'var(--color-text-dim)' : n >= 0 ? 'var(--color-success)' : 'var(--color-danger)';

const today = () => new Date().toISOString().slice(0, 10);
const monthStart = () => {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
};

const th: React.CSSProperties = { padding: '8px 10px', textAlign: 'right', fontSize: 12, color: 'var(--color-text-dim)', whiteSpace: 'nowrap' };
const thL: React.CSSProperties = { ...th, textAlign: 'left' };
const td: React.CSSProperties = { padding: '7px 10px', textAlign: 'right', fontSize: 13, whiteSpace: 'nowrap' };
const tdL: React.CSSProperties = { ...td, textAlign: 'left' };

export default function PricingPage() {
    const [resp, setResp] = useState<PricingResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const [dateFrom, setDateFrom] = useState(monthStart());
    const [dateTo, setDateTo] = useState(today());
    const [brand, setBrand] = useState('');
    const [category, setCategory] = useState('');
    const [search, setSearch] = useState('');
    const [minOrders, setMinOrders] = useState(0);
    const [groupBy, setGroupBy] = useState<'category' | 'sku'>('category');

    const [categoryOptions, setCategoryOptions] = useState<string[]>([]);
    const [expanded, setExpanded] = useState<Set<string>>(new Set());
    const [syncing, setSyncing] = useState(false);
    const [syncMsg, setSyncMsg] = useState('');

    const reqRef = useRef(0);
    const loadData = useCallback(async () => {
        const myReq = ++reqRef.current; // защита от устаревших/перегоняющих ответов
        setLoading(true);
        setError('');
        try {
            const res = await api.getPricingMarkup({
                date_from: dateFrom,
                date_to: dateTo,
                brand: brand || undefined,
                category: category || undefined,
                search: search || undefined,
                min_orders: minOrders || undefined,
                group_by: groupBy,
            });
            if (reqRef.current !== myReq) return; // пришёл ответ не последнего запроса — игнор
            setResp(res);
            // Категории для фильтра берём, только когда фильтр по категории не активен
            if (!category && res.group_by === 'category') {
                setCategoryOptions(res.data_groups.map((g) => g.category));
            }
        } catch (e) {
            if (reqRef.current !== myReq) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (reqRef.current === myReq) setLoading(false);
        }
    }, [dateFrom, dateTo, brand, category, search, minOrders, groupBy]);

    useEffect(() => {
        const t = setTimeout(loadData, 250); // debounce фильтров
        return () => clearTimeout(t);
    }, [loadData]);

    const doSync = async () => {
        setSyncing(true);
        setSyncMsg('');
        try {
            const r = await api.syncPricing();
            setSyncMsg(r.status === 'OK' ? `Синхронизировано: ${fmtInt(r.rows)} цен` : `Ошибка: ${r.message || r.status}`);
            await loadData();
        } catch (e) {
            setSyncMsg(e instanceof Error ? e.message : 'Ошибка синка');
        } finally {
            setSyncing(false);
        }
    };

    const toggle = (cat: string) => {
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(cat)) next.delete(cat);
            else next.add(cat);
            return next;
        });
    };

    const flatRows: PricingRow[] = useMemo(() => {
        if (!resp) return [];
        return resp.group_by === 'category' ? resp.data_groups.flatMap((g) => g.children) : resp.data_rows;
    }, [resp]);

    const doExport = () => {
        const rows = flatRows.map((r) => ({
            'Артикул': r.vendor_code || '',
            'nm_id': r.nm_id,
            'Категория': r.category,
            'Бренд': r.brand || '',
            'Цена витрины': r.current_price,
            'Себестоимость': r.cost_price,
            'Наценка коэф': r.markup_coef,
            'Наценка %': r.markup_pct,
            'Доля себест %': r.cost_share_pct,
            'СПП %': r.spp_rate,
            'Цена покупателю': r.buyer_price,
            'Заказы': r.orders_count,
            'Выручка': r.revenue,
            'Расходы ВБ': r.wb_expenses,
            'Прибыль': r.profit,
            'Маржа %': r.margin_pct,
            'Чистая наценка %': r.net_markup_pct,
        }));
        exportToExcel(rows, 'Ценообразование');
    };

    const s = resp?.summary;
    const isEmpty = !loading && !error && flatRows.length === 0;

    return (
        <div className="animate-in" style={{ padding: 24 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
                <h1 style={{ fontSize: 28, fontWeight: 700, margin: 0 }}>💲 Ценообразование</h1>
                <span style={{ color: 'var(--color-text-dim)', fontSize: 13 }}>
                    Коэффициент наценки по артикулам: текущая цена ВБ ÷ себестоимость, с учётом расходов ВБ и СПП
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
                    {categoryOptions.map((c) => (
                        <option key={c} value={c}>{c}</option>
                    ))}
                </select>
                <input
                    className="btn btn-sm"
                    placeholder="Бренд"
                    value={brand}
                    onChange={(e) => setBrand(e.target.value)}
                    style={{ width: 120 }}
                />
                <input
                    className="btn btn-sm"
                    placeholder="🔍 Артикул / nm_id"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    style={{ width: 160 }}
                />
                <label style={{ fontSize: 12, color: 'var(--color-text-dim)', display: 'flex', gap: 4, alignItems: 'center' }}>
                    мин. заказов
                    <input
                        type="number"
                        min={0}
                        className="btn btn-sm"
                        value={minOrders}
                        onChange={(e) => setMinOrders(Math.max(0, Number(e.target.value) || 0))}
                        style={{ width: 64 }}
                    />
                </label>
                <div style={{ flex: 1 }} />
                <div style={{ display: 'flex', gap: 4 }}>
                    <button
                        className={`btn btn-sm ${groupBy === 'category' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setGroupBy('category')}
                    >
                        По категориям
                    </button>
                    <button
                        className={`btn btn-sm ${groupBy === 'sku' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setGroupBy('sku')}
                    >
                        По артикулам
                    </button>
                </div>
                <button className="btn btn-sm btn-secondary" onClick={doExport} disabled={flatRows.length === 0}>
                    📥 Excel
                </button>
                <button className="btn btn-sm btn-primary" onClick={doSync} disabled={syncing}>
                    {syncing ? '⏳ Обновление…' : '🔄 Обновить цены'}
                </button>
            </div>

            {/* KPI */}
            {s && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8, marginBottom: 16 }}>
                    <Kpi label="Артикулов" value={fmtInt(s.total_articles)} sub={`с ценой ${fmtInt(s.priced_articles)} · с себест. ${fmtInt(s.costed_articles)}`} />
                    <Kpi label="Наценка (портфель)" value={fmtPct(s.markup_pct)} color={pctColor(s.markup_pct)} />
                    <Kpi label="Доля себестоимости" value={fmtPct(s.cost_share_pct)} />
                    <Kpi label="Выручка за период" value={fmtMoney(s.revenue) + ' ₽'} />
                    <Kpi label="Прибыль за период" value={fmtMoney(s.profit) + ' ₽'} color={pctColor(s.profit)} />
                    <Kpi label="Маржа" value={fmtPct(s.margin_pct)} color={pctColor(s.margin_pct)} />
                </div>
            )}

            {/* Состояния */}
            {loading && <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>⏳ Загрузка…</div>}
            {error && <div style={{ padding: 16, color: 'var(--color-danger)' }}>❌ {error}</div>}
            {isEmpty && (
                <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                    Нет данных. Убедитесь, что цены синхронизированы («Обновить цены») и заданы себестоимости в воронке.
                </div>
            )}

            {/* Таблица */}
            {!loading && !error && flatRows.length > 0 && (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                                <th style={thL}>{groupBy === 'category' ? 'Категория / Артикул' : 'Артикул'}</th>
                                <th style={th}>Цена ВБ</th>
                                <th style={th}>Себест.</th>
                                <th style={th}>Коэф.</th>
                                <th style={th}>Наценка %</th>
                                <th style={th}>Доля себест.</th>
                                <th style={th}>СПП %</th>
                                <th style={th}>Покупателю</th>
                                <th style={th}>Заказы</th>
                                <th style={th}>Выручка</th>
                                <th style={th}>Расходы ВБ</th>
                                <th style={th}>Прибыль</th>
                                <th style={th}>Маржа %</th>
                                <th style={th}>Чист. наценка</th>
                            </tr>
                        </thead>
                        <tbody>
                            {groupBy === 'category'
                                ? resp!.data_groups.map((g) => (
                                      <GroupRows key={g.category} group={g} open={expanded.has(g.category)} onToggle={() => toggle(g.category)} />
                                  ))
                                : resp!.data_rows.map((r) => <SkuRow key={r.nm_id} r={r} showCategory />)}
                        </tbody>
                    </table>
                </div>
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

function GroupRows({ group, open, onToggle }: { group: PricingGroup; open: boolean; onToggle: () => void }) {
    return (
        <>
            <tr style={{ borderBottom: '1px solid var(--color-border)', cursor: 'pointer', background: 'var(--color-bg-card)' }} onClick={onToggle}>
                <td style={{ ...tdL, fontWeight: 600 }}>
                    <span style={{ marginRight: 6 }}>{open ? '▾' : '▸'}</span>
                    {group.category}
                    <span style={{ color: 'var(--color-text-dim)', fontWeight: 400, marginLeft: 8, fontSize: 12 }}>
                        {fmtInt(group.articles)} арт. · с ценой {fmtInt(group.priced_articles)}
                    </span>
                </td>
                <td style={td}>—</td>
                <td style={td}>—</td>
                <td style={{ ...td, fontWeight: 600 }}>{fmtCoef(group.markup_coef)}</td>
                <td style={{ ...td, fontWeight: 600, color: pctColor(group.markup_pct) }}>{fmtPct(group.markup_pct)}</td>
                <td style={td}>{fmtPct(group.cost_share_pct)}</td>
                <td style={td}>—</td>
                <td style={td}>—</td>
                <td style={td}>—</td>
                <td style={td}>{fmtMoney(group.revenue)}</td>
                <td style={td}>{fmtMoney(group.wb_expenses)}</td>
                <td style={{ ...td, color: pctColor(group.profit) }}>{fmtMoney(group.profit)}</td>
                <td style={{ ...td, color: pctColor(group.margin_pct) }}>{fmtPct(group.margin_pct)}</td>
                <td style={td}>—</td>
            </tr>
            {open && group.children.map((r) => <SkuRow key={r.nm_id} r={r} indent />)}
        </>
    );
}

function SkuRow({ r, indent, showCategory }: { r: PricingRow; indent?: boolean; showCategory?: boolean }) {
    return (
        <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
            <td style={{ ...tdL, paddingLeft: indent ? 28 : 10 }}>
                <span style={{ fontWeight: 500 }}>{r.vendor_code || r.nm_id}</span>
                <span style={{ color: 'var(--color-text-dim)', fontSize: 11, marginLeft: 6 }}>
                    {r.nm_id}{showCategory ? ` · ${r.category}` : ''}
                </span>
                {!r.has_price && <span style={{ color: 'var(--color-warning)', fontSize: 11, marginLeft: 6 }}>нет цены</span>}
                {!r.has_cost && <span style={{ color: 'var(--color-warning)', fontSize: 11, marginLeft: 6 }}>нет себест.</span>}
            </td>
            <td style={td}>{fmtMoney(r.current_price)}</td>
            <td style={td}>{fmtMoney(r.cost_price)}</td>
            <td style={{ ...td, fontWeight: 600 }}>{fmtCoef(r.markup_coef)}</td>
            <td style={{ ...td, fontWeight: 600, color: pctColor(r.markup_pct) }}>{fmtPct(r.markup_pct)}</td>
            <td style={td}>{fmtPct(r.cost_share_pct)}</td>
            <td style={td}>{fmtPct(r.spp_rate)}</td>
            <td style={td}>{fmtMoney(r.buyer_price)}</td>
            <td style={td}>{fmtInt(r.orders_count)}</td>
            <td style={td}>{fmtMoney(r.revenue)}</td>
            <td style={td}>{fmtMoney(r.wb_expenses)}</td>
            <td style={{ ...td, color: pctColor(r.profit) }}>{fmtMoney(r.profit)}</td>
            <td style={{ ...td, color: pctColor(r.margin_pct) }}>{fmtPct(r.margin_pct)}</td>
            <td style={{ ...td, color: pctColor(r.net_markup_pct) }}>{fmtPct(r.net_markup_pct)}</td>
        </tr>
    );
}
