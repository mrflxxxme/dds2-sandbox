'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import TabLayout from '@/components/TabLayout';
import type { Warehouse, StockSummaryRow, UnifiedStockRow, TrendPeriodData } from '@/types/api';
import type { Column } from '@/components/DataTable';

function StockCell({ qty, reserved }: { qty: number; reserved: number }) {
    if (!qty && !reserved) return <span style={{ color: 'var(--color-text-muted)' }}>{'\u2014'}</span>;
    const available = Math.max(0, qty - reserved);
    if (!reserved) return <>{formatNumber(qty)}</>;
    return (
        <>
            <span>{formatNumber(available)}</span>
            <span style={{ color: 'var(--color-warning)' }}> / {formatNumber(reserved)}</span>
        </>
    );
}

// Единый прочерк пустых ячеек — одинаковый приглушённый вид во всех таблицах
const DASH = <span style={{ color: 'var(--color-text-muted)' }}>{'\u2014'}</span>;

// ─── Summary tab (original view) ─────────────────────────────────────────

function SummaryTab({
    warehouses,
    summary,
}: {
    warehouses: Warehouse[];
    summary: StockSummaryRow[];
}) {
    const [search, setSearch] = useState('');
    const [filter, setFilter] = useState<'all' | 'reserved'>('all');

    let filtered = search
        ? summary.filter(r => r.barcode.includes(search))
        : summary;

    if (filter === 'reserved') {
        filtered = filtered.filter(r => (r.total_reserved || 0) > 0);
    }

    const cols: Column[] = [
        { key: 'barcode', label: 'ШК' },
    ];

    for (const wh of warehouses) {
        cols.push({
            key: `wh_${wh.id}`,
            label: wh.name,
            headerTitle: `Товар физически на складе «${wh.name}». После «/» жёлтым — резерв под активные заявки`,
            align: 'right',
            getValue: (row: StockSummaryRow) => row.warehouses[wh.id] || 0,
            exportValue: (row: StockSummaryRow) => row.warehouses[wh.id] || 0,
            render: (_: unknown, row: StockSummaryRow) => {
                const qty = row.warehouses[wh.id] || 0;
                const res = (row.reserved || {})[wh.id] || 0;
                return <StockCell qty={qty} reserved={res} />;
            },
        });
    }

    cols.push(
        {
            key: 'total_defect',
            label: 'Брак',
            headerTitle: 'Бракованный товар — в «Итого» не входит',
            align: 'right',
            render: (v: number) => v > 0
                ? <span style={{ color: 'var(--color-warning)', fontWeight: 600 }}>{formatNumber(v, 0)}</span>
                : DASH,
        },
        {
            key: 'total_in_transit',
            label: 'В пути',
            headerTitle: 'Перемещения между нашими складами — отправлено, но ещё не принято',
            align: 'right',
            render: (v: number) => v > 0 ? formatNumber(v, 0) : DASH,
        },
        {
            key: 'total',
            label: 'Итого',
            headerTitle: 'Итого по всем нашим складам. При резерве: доступно / резерв',
            align: 'right',
            render: (_: unknown, row: StockSummaryRow) => {
                const res = row.total_reserved || 0;
                if (!res) return <strong>{formatNumber(row.total)}</strong>;
                return (
                    <strong>
                        <span>{formatNumber(row.total_available || 0)}</span>
                        <span style={{ color: 'var(--color-warning)' }}> / {formatNumber(res)}</span>
                    </strong>
                );
            },
        },
    );

    return (
        <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                <input
                    className="form-input"
                    placeholder="Поиск по баркоду..."
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    style={{ maxWidth: 300 }}
                />
                <div style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
                    <button
                        onClick={() => setFilter('all')}
                        style={{
                            padding: '6px 14px', fontSize: 13, border: 'none', cursor: 'pointer',
                            background: filter === 'all' ? 'var(--color-primary)' : 'var(--color-bg)',
                            color: filter === 'all' ? '#fff' : 'var(--color-text)',
                        }}
                    >Все остатки</button>
                    <button
                        onClick={() => setFilter('reserved')}
                        style={{
                            padding: '6px 14px', fontSize: 13, border: 'none', cursor: 'pointer',
                            borderLeft: '1px solid var(--color-border)',
                            background: filter === 'reserved' ? 'var(--color-warning)' : 'var(--color-bg)',
                            color: filter === 'reserved' ? '#fff' : 'var(--color-text)',
                        }}
                    >Зарезервировано</button>
                </div>
            </div>

            {filtered.length === 0 ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', opacity: 0.6 }}>
                    <div style={{ fontSize: 48 }}>{'\uD83D\uDCE6'}</div>
                    <div>{filter === 'reserved' ? 'Нет зарезервированных позиций' : 'Нет данных по остаткам'}</div>
                </div>
            ) : (
                <TanStackDataTable
                    columns={cols}
                    data={filtered}
                    exportName="stock_summary"
                    enableSorting
                    enablePagination
                    pageSize={50}
                    actions={
                        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                            {filtered.length} позиций
                        </span>
                    }
                />
            )}
        </>
    );
}

// ─── Unified tab ─────────────────────────────────────────────────────────

function WbDetailRow({ row, wbWarehouses, mode }: { row: UnifiedStockRow; wbWarehouses: string[]; mode: string }) {
    return (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: '8px 0' }}>
            {wbWarehouses.map(wh => {
                const v = row.wb_stocks[wh] || 0;
                if (v <= 0) return null;
                let display = formatNumber(v, 0);
                let color = '#7c3aed';
                let bg = 'rgba(175, 82, 222, 0.08)';
                if (mode === 'cost' && row.avg_cost > 0) {
                    display = formatNumber(v * row.avg_cost) + '\u00A0\u20BD';
                } else if (mode === 'revenue' && row.avg_price > 0) {
                    display = formatNumber(v * row.avg_price) + '\u00A0\u20BD';
                } else if (mode === 'profit' && row.avg_profit) {
                    const val = v * row.avg_profit;
                    display = formatNumber(val) + '\u00A0\u20BD';
                    color = val >= 0 ? 'var(--color-success)' : 'var(--color-danger)';
                    bg = val >= 0 ? 'rgba(52, 199, 89, 0.08)' : 'rgba(255, 59, 48, 0.08)';
                }
                return (
                    <span key={wh} style={{ fontSize: 12, padding: '2px 8px', borderRadius: 6, background: bg, color, whiteSpace: 'nowrap' }}>
                        {wh}: {display}
                    </span>
                );
            })}
        </div>
    );
}

function UnifiedTab({ data, onRefresh, groupBy, onGroupChange, brand, onBrandChange, isLoading, includeForecast, onForecastChange }: {
    data: UnifiedStockRow[];
    onRefresh: () => void;
    groupBy: string;
    onGroupChange: (groupBy: string) => void;
    brand: string;
    onBrandChange: (brand: string) => void;
    isLoading: boolean;
    includeForecast: boolean;
    onForecastChange: (v: boolean) => void;
}) {
    const [search, setSearch] = useState('');
    const [expanded, setExpanded] = useState<Set<number>>(new Set());
    const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
    const [expandedSubGroups, setExpandedSubGroups] = useState<Set<string>>(new Set());
    const [syncing, setSyncing] = useState(false);
    const [syncMsg, setSyncMsg] = useState<{ ok: boolean; text: string } | null>(null);
    const [mode, setMode] = useState<'qty' | 'cost' | 'revenue' | 'profit'>('qty');
    const [variant, setVariant] = useState<1 | 2 | 3>(2);
    const [trendPeriod, setTrendPeriod] = useState<7 | 14 | 30>(14);
    // Stock-cover filter: items with запас <= N days. 0 = no filter.
    const [stockDaysFilter, setStockDaysFilter] = useState<0 | 7 | 30 | 60>(0);
    // Новинки KPI breakdown — null=collapsed, 'category'/'brand'=which grouping to show
    const [noveltyBreakdown, setNoveltyBreakdown] = useState<null | 'category' | 'brand'>(null);
    // Per-group expansion inside the breakdown table — keys = group names
    const [expandedNoveltyGroups, setExpandedNoveltyGroups] = useState<Set<string>>(new Set());
    const toggleNoveltyGroup = useCallback((key: string) => {
        setExpandedNoveltyGroups(prev => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
        });
    }, []);
    const isGrouped = groupBy !== 'sku' && groupBy !== 'abc';

    const getVariantTotal = useCallback((row: UnifiedStockRow): number => {
        // «В пути до получателей» НЕ входит в Итого (товар уже уехал к покупателям).
        // Учитываем склады WB + возвраты (вернутся и снова станут доступны).
        const wbAll = (row.total_wb || 0) + (row.wb_in_way_from_client || 0);
        if (variant === 1) return wbAll;
        if (variant === 2) return (row.total_own || 0) + wbAll + (row.in_transit || 0);
        return (row.total_own || 0) + wbAll + (row.in_transit || 0)
             + (row.factory_qty || 0) + (row.vehicle_forming_qty || 0) + (row.vehicle_transit_qty || 0);
    }, [variant]);

    const getTrendData = useCallback((row: UnifiedStockRow): TrendPeriodData => {
        const empty: TrendPeriodData = { avg_daily_qty: 0, revenue: 0, profit: 0, date_from: '', date_to: '' };
        if (trendPeriod === 7) return row.trend_7 || empty;
        if (trendPeriod === 14) return row.trend_14 || empty;
        return row.trend_30 || empty;
    }, [trendPeriod]);

    // Column visibility based on selected variant
    const showOwn = variant >= 2;
    const showFactory = variant === 3;
    const showVehicles = variant === 3;

    const toggleExpand = (nomId: number) => {
        setExpanded(prev => {
            const next = new Set(prev);
            if (next.has(nomId)) next.delete(nomId); else next.add(nomId);
            return next;
        });
    };

    const toggleGroup = (key: string) => {
        setExpandedGroups(prev => {
            const next = new Set(prev);
            next.has(key) ? next.delete(key) : next.add(key);
            return next;
        });
    };

    const toggleSubGroup = (key: string) => {
        setExpandedSubGroups(prev => {
            const next = new Set(prev);
            next.has(key) ? next.delete(key) : next.add(key);
            return next;
        });
    };

    const handleSync = async () => {
        setSyncing(true);
        setSyncMsg(null);
        try {
            // Главное для колонки «WB склады» — отчёт кабинета «Остатки на складах».
            // Отчёт готовится на стороне WB до ~90с, поэтому ждём его первым.
            const res = await api.syncWarehouseRemains();
            // Старый источник (доступно к продаже) — для прочих разделов; не критичен,
            // ошибку тут глотаем, чтобы не перебить успех основного синка.
            try { await api.syncWarehouseStocks(); } catch { /* ignore */ }
            onRefresh();
            setSyncMsg({ ok: true, text: `Остатки WB обновлены (${formatNumber(res.synced, 0)} строк)` });
        } catch (e) {
            const detail = e instanceof Error ? e.message : 'неизвестная ошибка';
            setSyncMsg({ ok: false, text: `Не удалось обновить остатки WB: ${detail}` });
        }
        setSyncing(false);
    };

    const stockDaysFor = useCallback((row: UnifiedStockRow): number => {
        const trend = trendPeriod === 7 ? row.trend_7 : trendPeriod === 14 ? row.trend_14 : row.trend_30;
        const daily = trend?.avg_daily_qty || 0;
        if (daily <= 0) return Infinity;
        return getVariantTotal(row) / daily;
    }, [trendPeriod, getVariantTotal]);

    // Dates of the active trend window — taken from the first row (backend
    // returns the same window on every row, so row 0 is canonical). Used to
    // show "С 17.03 по 14.04" label so the user knows exactly which period
    // the realization/profit numbers cover.
    const activePeriodDates = useMemo(() => {
        if (!data.length) return null;
        const first = data[0];
        const trend = trendPeriod === 7 ? first.trend_7 : trendPeriod === 14 ? first.trend_14 : first.trend_30;
        if (!trend?.date_from || !trend?.date_to) return null;
        return { from: trend.date_from, to: trend.date_to };
    }, [data, trendPeriod]);

    const filtered = useMemo(() => {
        let rows = data;
        if (search) {
            const q = search.toLowerCase();
            if (isGrouped) {
                rows = rows.filter(r =>
                    (r.group_name || '').toLowerCase().includes(q) ||
                    (r.children || []).some(c =>
                        (c.barcode || '').toLowerCase().includes(q) ||
                        (c.article_seller || '').toLowerCase().includes(q)
                    )
                );
            } else {
                rows = rows.filter(r =>
                    (r.barcode || '').toLowerCase().includes(q) ||
                    (r.article_seller || '').toLowerCase().includes(q) ||
                    (r.group_name || '').toLowerCase().includes(q)
                );
            }
        }
        if (stockDaysFilter > 0) {
            // Show items with запас <= N days AND sales > 0 (Infinity means no sales — exclude)
            rows = rows.filter(r => {
                const days = stockDaysFor(r);
                return Number.isFinite(days) && days <= stockDaysFilter;
            });
        }
        return rows;
    }, [data, search, isGrouped, stockDaysFilter, stockDaysFor]);

    // Предвычисленные ЧИСЛОВЫЕ ключи сортировки для метрик-колонок. Сортировка по
    // готовому полю (а не по замыканию, считающему на каждое сравнение) — надёжна
    // и повторяет проверенный порядок. Товары без продаж уезжают в конец при обеих
    // сортировках (тренд/реализ/маржа → -1, запас → большое число).
    const rowsForTable = useMemo(() => {
        return filtered.map((r) => {
            const t = getTrendData(r);
            const vt = getVariantTotal(r);
            const daily = t?.avg_daily_qty || 0;
            const rev = t?.revenue || 0;
            return {
                ...r,
                _sortTrend: daily,
                _sortRevenue: rev,
                _sortMargin: rev > 0 ? ((t?.profit || 0) / rev) * 100 : -1,
                _sortStockDays: daily > 0 ? vt / daily : Number.MAX_SAFE_INTEGER,
            };
        });
    }, [filtered, getTrendData, getVariantTotal]);

    // «Новинки» KPI — aggregates SKUs flagged as novelties by the backend
    // (no sales recorded in the last 60 days). Counts ALL current and incoming
    // quantity (shelf own + WB + in-transit + factory + vehicles) since none
    // of it has been sold yet — it's all "potential". Flattens grouped views
    // to SKU level so numbers are identical regardless of «Группировка» mode.
    // Also produces per-category and per-brand breakdowns (sorted by revenue
    // potential desc) for the optional expandable section in the card.
    const noveltyKpi = useMemo(() => {
        const collected: UnifiedStockRow[] = [];
        const walk = (rows: UnifiedStockRow[]) => {
            for (const r of rows) {
                if (r.children && r.children.length > 0) {
                    walk(r.children);
                } else {
                    collected.push(r);
                }
            }
        };
        walk(data);
        let skuCount = 0;
        let qtyTotal = 0;
        let costTotal = 0;
        let revenuePotential = 0;
        let profitPotential = 0;
        type NoveltyItem = {
            nomenclature_id: number;
            article_seller: string | null;
            barcode: string;
            qty: number;
            cost: number;
            revenue: number;
            profit: number;
        };
        type GroupRow = {
            key: string;
            skuCount: number;
            qty: number;
            cost: number;
            revenue: number;
            profit: number;
            items: NoveltyItem[];
        };
        const byCategoryMap = new Map<string, GroupRow>();
        const byBrandMap = new Map<string, GroupRow>();
        const bump = (map: Map<string, GroupRow>, key: string, item: NoveltyItem) => {
            let g = map.get(key);
            if (!g) {
                g = { key, skuCount: 0, qty: 0, cost: 0, revenue: 0, profit: 0, items: [] };
                map.set(key, g);
            }
            g.skuCount += 1;
            g.qty += item.qty;
            g.cost += item.cost;
            g.revenue += item.revenue;
            g.profit += item.profit;
            g.items.push(item);
        };
        for (const r of collected) {
            if (!r.is_novelty) continue;
            const available = (r.total_own || 0) + (r.total_wb || 0)
                + (r.wb_in_way_to_client || 0) + (r.wb_in_way_from_client || 0) + (r.in_transit || 0);
            const incoming = (r.factory_qty || 0) + (r.vehicle_forming_qty || 0) + (r.vehicle_transit_qty || 0);
            const qty = available + incoming;
            if (qty <= 0) continue;
            skuCount += 1;
            qtyTotal += qty;
            const unitCost = r.avg_cost || r.cost_factory_unit || 0;
            const cost = qty * unitCost;
            const revenue = qty * (r.novelty_unit_revenue || 0);
            const profit = qty * (r.novelty_unit_profit || 0);
            costTotal += cost;
            revenuePotential += revenue;
            profitPotential += profit;
            const item: NoveltyItem = {
                nomenclature_id: r.nomenclature_id,
                article_seller: r.article_seller,
                barcode: r.barcode || '',
                qty,
                cost,
                revenue,
                profit,
            };
            bump(byCategoryMap, r.subject || 'Без категории', item);
            bump(byBrandMap, r.brand || 'Без бренда', item);
        }
        const sortByRevenueDesc = <T extends { revenue: number }>(a: T, b: T) => b.revenue - a.revenue;
        // Sort children inside each group too — top earners first.
        for (const g of byCategoryMap.values()) g.items.sort(sortByRevenueDesc);
        for (const g of byBrandMap.values()) g.items.sort(sortByRevenueDesc);
        const byCategory = Array.from(byCategoryMap.values()).sort(sortByRevenueDesc);
        const byBrand = Array.from(byBrandMap.values()).sort(sortByRevenueDesc);
        return { skuCount, qtyTotal, costTotal, revenuePotential, profitPotential, byCategory, byBrand };
    }, [data]);

    const availableBrands = useMemo(() => {
        const set = new Set<string>();
        for (const row of data) {
            if (row.brand) set.add(row.brand);
            for (const child of row.children || []) {
                if (child.brand) set.add(child.brand);
            }
        }
        return Array.from(set).sort((a, b) => a.localeCompare(b, 'ru'));
    }, [data]);

    const { ownWarehouses, wbWarehouses } = useMemo(() => {
        const ownSet = new Set<string>();
        const wbSet = new Set<string>();
        for (const row of data) {
            for (const k of Object.keys(row.warehouses)) ownSet.add(k);
            for (const k of Object.keys(row.wb_stocks)) wbSet.add(k);
        }
        return {
            ownWarehouses: Array.from(ownSet).sort(),
            wbWarehouses: Array.from(wbSet).sort(),
        };
    }, [data]);

    const totals = useMemo(() => {
        let ownTotal = 0, wbTotal = 0, inTransit = 0, total = 0;
        let wbToClient = 0, wbFromClient = 0, wbToClientMoney = 0, wbFromClientMoney = 0;
        let ownMoney = 0, wbMoney = 0, transitMoney = 0, totalMoney = 0;
        let factoryTotal = 0, vehicleFormingTotal = 0, vehicleTransitTotal = 0;
        let factoryMoney = 0, vehicleFormingMoney = 0, vehicleTransitMoney = 0;
        let variantTotal = 0, variantMoney = 0;
        let trendDailySum = 0, bdrRevenueSum = 0, bdrProfitSum = 0, defectSum = 0, defectMoney = 0;
        for (const row of filtered) {
            let multiplier = 0;
            if (mode === 'cost') multiplier = row.avg_cost || 0;
            else if (mode === 'revenue') multiplier = row.avg_price || 0;
            else if (mode === 'profit') multiplier = row.avg_profit || 0;
            // Factory uses cost_factory_unit for cost mode
            const factoryMultiplier = mode === 'cost'
                ? (row.cost_factory_unit || row.avg_cost || 0)
                : multiplier;
            trendDailySum += getTrendData(row).avg_daily_qty || 0;
            bdrRevenueSum += getTrendData(row).revenue || 0;
            bdrProfitSum += getTrendData(row).profit || 0;
            defectSum += row.total_defect || 0;
            defectMoney += (row.total_defect || 0) * multiplier;
            ownTotal += row.total_own || 0;
            wbTotal += row.total_wb || 0;
            wbToClient += row.wb_in_way_to_client || 0;
            wbFromClient += row.wb_in_way_from_client || 0;
            inTransit += row.in_transit || 0;
            total += row.total || 0;
            factoryTotal += row.factory_qty || 0;
            vehicleFormingTotal += row.vehicle_forming_qty || 0;
            vehicleTransitTotal += row.vehicle_transit_qty || 0;
            variantTotal += getVariantTotal(row);
            if (mode !== 'qty') {
                // «В пути до получателей» исключён из Итого (см. getVariantTotal)
                const wbAll = (row.total_wb || 0) + (row.wb_in_way_from_client || 0);
                ownMoney += (row.total_own || 0) * multiplier;
                wbMoney += (row.total_wb || 0) * multiplier;
                wbToClientMoney += (row.wb_in_way_to_client || 0) * multiplier;
                wbFromClientMoney += (row.wb_in_way_from_client || 0) * multiplier;
                transitMoney += (row.in_transit || 0) * multiplier;
                totalMoney += (row.total || 0) * multiplier;
                factoryMoney += (row.factory_qty || 0) * factoryMultiplier;
                vehicleFormingMoney += (row.vehicle_forming_qty || 0) * multiplier;
                vehicleTransitMoney += (row.vehicle_transit_qty || 0) * multiplier;
                // For variant=3 итого, factory uses estimated cost
                let rowVariantMoney = 0;
                if (variant === 1) {
                    rowVariantMoney = wbAll * multiplier;
                } else if (variant === 2) {
                    rowVariantMoney = ((row.total_own || 0) + wbAll + (row.in_transit || 0)) * multiplier;
                } else {
                    rowVariantMoney = ((row.total_own || 0) + wbAll + (row.in_transit || 0)
                        + (row.vehicle_forming_qty || 0) + (row.vehicle_transit_qty || 0)) * multiplier
                        + (row.factory_qty || 0) * factoryMultiplier;
                }
                variantMoney += rowVariantMoney;
            }
        }
        return {
            ownTotal, wbTotal, inTransit, total,
            wbToClient, wbFromClient, wbToClientMoney, wbFromClientMoney,
            ownMoney, wbMoney, transitMoney, totalMoney,
            factoryTotal, vehicleFormingTotal, vehicleTransitTotal,
            factoryMoney, vehicleFormingMoney, vehicleTransitMoney,
            variantTotal, variantMoney,
            trendDailySum, bdrRevenueSum, bdrProfitSum, defectSum, defectMoney,
        };
    }, [filtered, mode, variant, getVariantTotal, getTrendData]);

    // Multiplier: qty=1, cost=avg_cost, revenue=qty*avg_price, profit=qty*avg_profit
    /** Get numeric sort value for a cell (used by TanStack sorting) */
    const getSortVal = useCallback((qty: number, row: UnifiedStockRow): number => {
        if (qty <= 0) return 0;
        if (mode === 'qty') return qty;
        if (mode === 'cost') return qty * (row.avg_cost || 0);
        if (mode === 'revenue') return qty * (row.avg_price || 0);
        if (mode === 'profit') return qty * (row.avg_profit || 0);
        return qty;
    }, [mode]);

    const fmtVal = useCallback((qty: number, row: UnifiedStockRow) => {
        if (qty <= 0) return DASH;
        if (mode === 'qty') return formatNumber(qty, 0);
        if (mode === 'cost') {
            const cost = row.avg_cost || 0;
            if (cost <= 0) return <span style={{ color: 'var(--color-text-dim)' }}>{formatNumber(qty, 0)}</span>;
            const est = row.is_cost_estimated ? '\u2248\u00A0' : '';
            const color = row.is_cost_estimated ? 'var(--color-text-muted)' : undefined;
            return <span style={{ color, whiteSpace: 'nowrap' }} title={row.is_cost_estimated ? 'Оценка по средней наценке категории' : undefined}>{est}{formatNumber(qty * cost)}{'\u00A0\u20BD'}</span>;
        }
        if (mode === 'revenue') {
            const price = row.avg_price || 0;
            if (price <= 0) return <span style={{ color: 'var(--color-text-dim)' }}>{formatNumber(qty, 0)}</span>;
            return <span style={{ whiteSpace: 'nowrap' }}>{formatNumber(qty * price)}{'\u00A0\u20BD'}</span>;
        }
        if (mode === 'profit') {
            const prof = row.avg_profit || 0;
            if (!prof) return <span style={{ color: 'var(--color-text-dim)' }}>{formatNumber(qty, 0)}</span>;
            const val = qty * prof;
            const color = val >= 0 ? 'var(--color-success)' : 'var(--color-danger)';
            return <span style={{ color, whiteSpace: 'nowrap' }}>{formatNumber(val)}{'\u00A0\u20BD'}</span>;
        }
        return formatNumber(qty, 0);
    }, [mode]);

    const fmtGroupVal = useCallback((qty: number, avgCost: number, row?: UnifiedStockRow): React.ReactNode => {
        if (qty <= 0) return DASH;
        if (mode === 'qty') return formatNumber(qty, 0);
        if (mode === 'cost' && avgCost > 0) return formatNumber(qty * avgCost) + '\u00A0\u20BD';
        if (mode === 'revenue') {
            const price = row ? row.avg_price || 0 : 0;
            if (price <= 0) return formatNumber(qty, 0);
            return formatNumber(qty * price) + '\u00A0\u20BD';
        }
        if (mode === 'profit') {
            const prof = row ? row.avg_profit || 0 : 0;
            if (!prof) return formatNumber(qty, 0);
            return formatNumber(qty * prof) + '\u00A0\u20BD';
        }
        return formatNumber(qty, 0);
    }, [mode]);

    const cols: Column[] = useMemo(() => {
        const c: Column[] = [];

        if (isGrouped) {
            c.push(
                { key: 'group_name', label: 'Группа' },
                { key: 'items_count', label: 'Товаров', align: 'right' },
            );
            if (groupBy === 'abc') {
                c.push({
                    key: 'abc_class',
                    label: 'ABC',
                    render: (v: string) => {
                        const colors: Record<string, string> = { A: 'var(--color-success)', B: 'var(--color-warning)', C: 'var(--color-danger)' };
                        return <span className="badge" style={{ background: colors[v] || 'var(--color-text-muted)', color: '#fff' }}>{v}</span>;
                    },
                });
            }
        } else {
            c.push(
                {
                    key: 'article_seller',
                    label: 'Товар',
                    render: (_: unknown, row: UnifiedStockRow) => (
                        <div>
                            <div style={{ fontWeight: 500 }}>{row.article_seller || '\u2014'}</div>
                            {row.subject && (
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{row.subject}</div>
                            )}
                        </div>
                    ),
                },
                { key: 'barcode', label: 'ШК' },
            );
            if (groupBy === 'abc') {
                c.push({
                    key: 'abc_class',
                    label: 'ABC',
                    render: (v: string) => {
                        const colors: Record<string, string> = { A: 'var(--color-success)', B: 'var(--color-warning)', C: 'var(--color-danger)' };
                        return <span className="badge" style={{ background: colors[v] || 'var(--color-text-muted)', color: '#fff', padding: '2px 10px', borderRadius: 12, fontWeight: 600, fontSize: 12 }}>{v}</span>;
                    },
                });
            }
        }

        // Sales metrics — moved to front (right after identity columns)
        c.push({
            key: 'trend_daily',
            label: 'Тренд шт/д',
            headerTitle: 'Средние заказы в день за выбранный период (7/14/30 дн) по данным WB',
            align: 'right',
            sortable: true,
            sortingFn: 'basic',
            getValue: (row) => (row as UnifiedStockRow & { _sortTrend?: number })._sortTrend ?? 0,
            render: (_: unknown, row: UnifiedStockRow) => {
                const v = getTrendData(row).avg_daily_qty;
                if (v <= 0) return DASH;
                return formatNumber(v, 1);
            },
        });
        c.push({
            key: 'bdr_revenue',
            label: 'Реализ. БДР',
            headerTitle: 'Реализация за выбранный период — сходится с отчётом БДР',
            align: 'right',
            sortable: true,
            sortingFn: 'basic',
            getValue: (row) => (row as UnifiedStockRow & { _sortRevenue?: number })._sortRevenue ?? 0,
            render: (_: unknown, row: UnifiedStockRow) => {
                const v = getTrendData(row).revenue;
                if (v <= 0) return DASH;
                const est = row.is_revenue_estimated;
                const prefix = est ? '\u2248\u00A0' : '';
                const color = est ? 'var(--color-text-muted)' : undefined;
                return <span style={{ color, whiteSpace: 'nowrap' }} title={est ? 'Оценка по средней реализации категории' : undefined}>{prefix}{formatNumber(v)}{'\u00A0\u20BD'}</span>;
            },
        });
        c.push({
            key: 'margin_pct',
            label: 'Маржа %',
            headerTitle: 'Прибыль ÷ реализация за выбранный период',
            align: 'right',
            sortable: true,
            sortingFn: 'basic',
            getValue: (row) => (row as UnifiedStockRow & { _sortMargin?: number })._sortMargin ?? -1,
            render: (_: unknown, row: UnifiedStockRow) => {
                const t = getTrendData(row);
                if (t.revenue <= 0) return DASH;
                const margin = (t.profit / t.revenue) * 100;
                const color = margin >= 0 ? 'var(--color-success)' : 'var(--color-danger)';
                const est = row.is_revenue_estimated;
                const prefix = est ? '\u2248\u00A0' : '';
                return <span style={{ color, fontWeight: 600, opacity: est ? 0.7 : 1 }} title={est ? 'Оценка по средней марже категории' : undefined}>{prefix}{formatNumber(margin, 1)}%</span>;
            },
        });
        c.push({
            key: 'total_defect',
            label: 'Брак',
            headerTitle: 'Бракованный товар на наших складах. В «Итого» не входит',
            align: 'right',
            sortable: true,
            getValue: (row: UnifiedStockRow) => getSortVal(row.total_defect || 0, row),
            render: (_: unknown, row: UnifiedStockRow) => {
                const v = row.total_defect || 0;
                if (v <= 0) return DASH;
                const val = fmtVal(v, row);
                return <span style={{ color: 'var(--color-warning)', fontWeight: 600 }}>{val}</span>;
            },
        });
        c.push({
            key: 'stock_days',
            label: 'Запас дн',
            headerTitle: 'На сколько дней хватит остатка при текущем темпе заказов (Итого ÷ тренд шт/д)',
            align: 'right',
            sortable: true,
            sortingFn: 'basic',
            getValue: (row) => (row as UnifiedStockRow & { _sortStockDays?: number })._sortStockDays ?? Number.MAX_SAFE_INTEGER,
            render: (_: unknown, row: UnifiedStockRow) => {
                const daily = getTrendData(row).avg_daily_qty;
                if (daily <= 0) return DASH;
                const days = getVariantTotal(row) / daily;
                const color = days < 14 ? 'var(--color-danger)' : days < 30 ? 'var(--color-warning)' : 'var(--color-text)';
                return <span style={{ color, fontWeight: 600 }}>{formatNumber(days, 0)}</span>;
            },
        });

        c.push({
            key: 'total',
            label: 'Итого',
            headerTitle: variant === 1
                ? 'Всё у WB: на складах + в пути до получателей + возвраты'
                : variant === 2
                    ? 'Наши склады + всё у WB + наши отгрузки в пути'
                    : 'Наши склады + всё у WB + наши отгрузки + на фабрике + в машинах',
            align: 'right',
            sortable: true,
            getValue: (row: UnifiedStockRow) => getSortVal(getVariantTotal(row), row),
            render: (_: unknown, row: UnifiedStockRow) => {
                const v = getVariantTotal(row);
                if (v <= 0) return <strong>{DASH}</strong>;
                const val = fmtVal(v, row);
                if (typeof val === 'string') {
                    return <strong style={{ color: 'var(--color-accent)' }}>{val}</strong>;
                }
                return <strong>{val}</strong>;
            },
        });

        // Own warehouse columns (only for variant 2/3)
        if (showOwn) {
            for (const wh of ownWarehouses) {
                c.push({
                    key: `own_${wh}`,
                    label: `${wh}`,
                    headerTitle: `Товар физически на складе «${wh}» (наш/фулфилмент), включая резерв под заявки`,
                    align: 'right',
                    getValue: (row: UnifiedStockRow) => getSortVal(row.warehouses[wh] || 0, row),
                    render: (_: unknown, row: UnifiedStockRow) => fmtVal(row.warehouses[wh] || 0, row),
                });
            }
        }

        // WB stocks — always shown
        c.push({
            key: 'total_wb',
            label: 'WB склады',
            headerTitle: 'Физически на складах WB — как «Всего находится на складах» в кабинете (включая приёмку и транзит между складами WB). Клик по цифре — разбивка по складам',
            align: 'right',
            getValue: (row: UnifiedStockRow) => getSortVal(row.total_wb || 0, row),
            render: (_: unknown, row: UnifiedStockRow) => {
                const v = row.total_wb || 0;
                if (v <= 0) return DASH;
                const isOpen = expanded.has(row.nomenclature_id);
                return (
                    <div>
                        <span
                            onClick={(e) => { e.stopPropagation(); toggleExpand(row.nomenclature_id); }}
                            style={{ color: 'var(--color-accent)', fontWeight: 500, cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}
                        >
                            {fmtVal(v, row)} {isOpen ? '\u25BE' : '\u25B8'}
                        </span>
                        {isOpen && <WbDetailRow row={row} wbWarehouses={wbWarehouses} mode={mode} />}
                    </div>
                );
            },
        });

        // WB-транзит — отдельные колонки, как в отчёте кабинета «Остатки на складах»
        c.push({
            key: 'wb_in_way_to_client',
            label: 'В пути до получателей',
            headerTitle: 'Заказы едут от склада WB к покупателям. Невыкупленное вернётся на склад WB',
            headerWrap: true,
            align: 'right',
            sortable: true,
            getValue: (row: UnifiedStockRow) => getSortVal(row.wb_in_way_to_client || 0, row),
            render: (_: unknown, row: UnifiedStockRow) => fmtVal(row.wb_in_way_to_client || 0, row),
        });
        c.push({
            key: 'wb_in_way_from_client',
            label: 'Возвраты на склад WB',
            headerTitle: 'Возвраты от покупателей — едут обратно на склад WB',
            headerWrap: true,
            align: 'right',
            sortable: true,
            getValue: (row: UnifiedStockRow) => getSortVal(row.wb_in_way_from_client || 0, row),
            render: (_: unknown, row: UnifiedStockRow) => fmtVal(row.wb_in_way_from_client || 0, row),
        });

        // В пути на WB — always shown (assembly requests SHIPPED to WB)
        c.push({
            key: 'in_transit',
            label: 'В пути',
            headerTitle: 'Наши отгрузки на WB: заявка отгружена со склада, WB ещё не принял поставку',
            align: 'right',
            getValue: (row: UnifiedStockRow) => getSortVal(row.in_transit || 0, row),
            render: (_: unknown, row: UnifiedStockRow) => fmtVal(row.in_transit || 0, row),
        });

        // Supply chain columns (only for variant 3)
        if (showFactory) {
            c.push({
                key: 'factory_qty',
                label: 'На фабрике',
                headerTitle: 'Заказано у фабрики и ещё не распределено по машинам (заказ не закрыт)',
                align: 'right',
                sortable: true,
                getValue: (row: UnifiedStockRow) => {
                    const fq = row.factory_qty || 0;
                    if (fq <= 0) return 0;
                    if (mode === 'cost') {
                        const c = row.cost_factory_unit || row.avg_cost || 0;
                        return fq * c;
                    }
                    return getSortVal(fq, row);
                },
                render: (_: unknown, row: UnifiedStockRow) => {
                    const fq = row.factory_qty || 0;
                    if (fq <= 0) return DASH;
                    if (mode === 'cost') {
                        const c = row.cost_factory_unit || row.avg_cost || 0;
                        if (c <= 0) return <span style={{ color: 'var(--color-text-dim)' }}>{formatNumber(fq, 0)}</span>;
                        return <span style={{ color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }} title="Оценка по средней наценке категории (только цена закупа × курс × коэф.)">{'\u2248\u00A0'}{formatNumber(fq * c)}{'\u00A0\u20BD'}</span>;
                    }
                    return fmtVal(fq, row);
                },
            });
        }
        if (showVehicles) {
            c.push({
                key: 'vehicle_forming_qty',
                label: 'Маш. (форм.)',
                headerTitle: 'Распределено в машину, машина ещё формируется — не выехала с фабрики',
                align: 'right',
                sortable: true,
                getValue: (row: UnifiedStockRow) => getSortVal(row.vehicle_forming_qty || 0, row),
                render: (_: unknown, row: UnifiedStockRow) => fmtVal(row.vehicle_forming_qty || 0, row),
            });
            c.push({
                key: 'vehicle_transit_qty',
                label: 'Маш. (в пути)',
                headerTitle: 'Машина едет на склад: отгружена с фабрики / таможня / отправлена на склад',
                align: 'right',
                sortable: true,
                getValue: (row: UnifiedStockRow) => getSortVal(row.vehicle_transit_qty || 0, row),
                render: (_: unknown, row: UnifiedStockRow) => fmtVal(row.vehicle_transit_qty || 0, row),
            });
        }

        return c;
    }, [ownWarehouses, wbWarehouses, expanded, mode, fmtVal, getSortVal, isGrouped, groupBy, getTrendData, getVariantTotal, showOwn, showFactory, showVehicles]);

    // Helper: factory cell content (always ≈ in cost mode, using cost_factory_unit)
    const fmtFactoryCell = useCallback((fq: number, row: UnifiedStockRow): React.ReactNode => {
        if (fq <= 0) return DASH;
        if (mode === 'qty') return formatNumber(fq, 0);
        if (mode === 'cost') {
            const c = row.cost_factory_unit || row.avg_cost || 0;
            if (c <= 0) return formatNumber(fq, 0);
            return <span style={{ color: 'var(--color-text-muted)' }} title="Оценка по средней наценке категории (только цена закупа × курс × коэф.)">{'\u2248\u00A0'}{formatNumber(fq * c)}{'\u00A0\u20BD'}</span>;
        }
        if (mode === 'revenue') {
            const p = row.avg_price || 0;
            if (p <= 0) return formatNumber(fq, 0);
            return <span>{formatNumber(fq * p)}{'\u00A0\u20BD'}</span>;
        }
        if (mode === 'profit') {
            const p = row.avg_profit || 0;
            if (!p) return formatNumber(fq, 0);
            return <span>{formatNumber(fq * p)}{'\u00A0\u20BD'}</span>;
        }
        return formatNumber(fq, 0);
    }, [mode]);

    // Render a single article row for the grouped table
    const renderArticleRow = (row: UnifiedStockRow, indent: number) => {
        const cost = row.avg_cost || 0;
        const trend = getTrendData(row);
        const variantTotal = getVariantTotal(row);
        const margin = trend.revenue > 0 ? (trend.profit / trend.revenue) * 100 : 0;
        const stockDays = trend.avg_daily_qty > 0 ? variantTotal / trend.avg_daily_qty : 0;
        const stockDaysColor = stockDays > 0
            ? stockDays < 14 ? 'var(--color-danger)' : stockDays < 30 ? 'var(--color-warning)' : 'var(--color-text)'
            : 'var(--color-text-muted)';

        return (
            <tr key={row.barcode || `${row.article_seller}-${row.nomenclature_id}`}>
                <td style={{ paddingLeft: indent }}>
                    <span style={{ fontSize: 13 }}>{row.article_seller || '\u2014'}</span>
                    <span style={{ color: 'var(--color-text-muted)', marginLeft: 8, fontSize: 12 }}>
                        {row.barcode}
                    </span>
                </td>
                <td style={{ textAlign: 'right' }}>{DASH}</td>
                {groupBy === 'abc' && (
                    <td>
                        {row.abc_class && (
                            <span className="badge" style={{
                                background: ({ A: 'var(--color-success)', B: 'var(--color-warning)', C: 'var(--color-danger)' } as Record<string, string>)[row.abc_class] || 'var(--color-text-muted)',
                                color: '#fff',
                            }}>{row.abc_class}</span>
                        )}
                    </td>
                )}
                {/* Sales metrics — moved to front */}
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {trend.avg_daily_qty > 0 ? formatNumber(trend.avg_daily_qty, 1) : DASH}
                </td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {trend.revenue > 0 ? formatNumber(trend.revenue) + '\u00A0\u20BD' : DASH}
                </td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {trend.revenue > 0 ? (
                        <span style={{ color: margin >= 0 ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                            {formatNumber(margin, 1)}%
                        </span>
                    ) : DASH}
                </td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {stockDays > 0 ? (
                        <span style={{ color: stockDaysColor, fontWeight: 600 }}>{formatNumber(stockDays, 0)}</span>
                    ) : DASH}
                </td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <strong style={{ color: 'var(--color-accent)' }}>
                        {fmtGroupVal(variantTotal, cost, row)}
                    </strong>
                </td>
                {showOwn && (
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(row.total_own || 0, cost, row)}</td>
                )}
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(row.total_wb || 0, cost, row)}</td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(row.wb_in_way_to_client || 0, cost, row)}</td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(row.wb_in_way_from_client || 0, cost, row)}</td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(row.in_transit || 0, cost, row)}</td>
                {showFactory && (
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtFactoryCell(row.factory_qty || 0, row)}</td>
                )}
                {showVehicles && (
                    <>
                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(row.vehicle_forming_qty || 0, cost, row)}</td>
                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(row.vehicle_transit_qty || 0, cost, row)}</td>
                    </>
                )}
            </tr>
        );
    };

    // Render the grouped expandable table
    const renderGroupedTable = () => {
        return (
            <div className="glass-card" style={{ overflow: 'auto' }}>
                <table className="data-table" style={{ width: '100%' }}>
                    <thead>
                        <tr>
                            <th>Группа</th>
                            <th style={{ textAlign: 'right' }}>Товаров</th>
                            {groupBy === 'abc' && <th>ABC</th>}
                            <th title="Средние заказы в день за выбранный период (7/14/30 дн) по данным WB" style={{ textAlign: 'right' }}>Тренд шт/д</th>
                            <th title="Реализация за выбранный период — сходится с отчётом БДР" style={{ textAlign: 'right' }}>Реализ.</th>
                            <th title="Прибыль ÷ реализация за выбранный период" style={{ textAlign: 'right' }}>Маржа %</th>
                            <th title="На сколько дней хватит остатка при текущем темпе заказов (Итого ÷ тренд шт/д)" style={{ textAlign: 'right' }}>Запас дн</th>
                            <th title="Суммарный остаток по выбранному виду" style={{ textAlign: 'right' }}>Итого</th>
                            {showOwn && <th title="Товар физически на наших/фулфилмент складах" style={{ textAlign: 'right' }}>Свои</th>}
                            <th title="Физически на складах WB — как «Всего находится на складах» в кабинете" style={{ textAlign: 'right' }}>WB</th>
                            <th title="Заказы едут от склада WB к покупателям. Невыкупленное вернётся на склад WB" style={{ textAlign: 'right' }}>В пути до получателей</th>
                            <th title="Возвраты от покупателей — едут обратно на склад WB" style={{ textAlign: 'right' }}>Возвраты на склад WB</th>
                            <th title="Наши отгрузки на WB: заявка отгружена со склада, WB ещё не принял поставку" style={{ textAlign: 'right' }}>В пути</th>
                            {showFactory && <th title="Заказано у фабрики и ещё не распределено по машинам (заказ не закрыт)" style={{ textAlign: 'right' }}>На фабрике</th>}
                            {showVehicles && <th title="Распределено в машину, машина ещё формируется — не выехала с фабрики" style={{ textAlign: 'right' }}>Маш. (форм.)</th>}
                            {showVehicles && <th title="Машина едет на склад: отгружена с фабрики / таможня / отправлена на склад" style={{ textAlign: 'right' }}>Маш. (в пути)</th>}
                        </tr>
                    </thead>
                    <tbody>
                        {/* Итого — первая строка */}
                        <tr style={{ background: 'var(--color-bg)', fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>
                            <td>Итого</td>
                            <td style={{ textAlign: 'right' }}>{formatNumber(filtered.reduce((s, g) => s + (g.items_count || 0), 0), 0)}</td>
                            {groupBy === 'abc' && <td />}
                            <td style={{ textAlign: 'right' }}>{DASH}</td>
                            <td style={{ textAlign: 'right' }}>{DASH}</td>
                            <td style={{ textAlign: 'right' }}>{DASH}</td>
                            <td style={{ textAlign: 'right' }}>{DASH}</td>
                            <td style={{ textAlign: 'right', color: 'var(--color-accent)', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.variantTotal, 0) : formatNumber(totals.variantMoney) + '\u00A0\u20BD'}
                            </td>
                            {showOwn && (
                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                    {mode === 'qty' ? formatNumber(totals.ownTotal, 0) : formatNumber(totals.ownMoney) + '\u00A0\u20BD'}
                                </td>
                            )}
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.wbTotal, 0) : formatNumber(totals.wbMoney) + '\u00A0\u20BD'}
                            </td>
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.wbToClient, 0) : formatNumber(totals.wbToClientMoney) + '\u00A0\u20BD'}
                            </td>
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.wbFromClient, 0) : formatNumber(totals.wbFromClientMoney) + '\u00A0\u20BD'}
                            </td>
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.inTransit, 0) : formatNumber(totals.transitMoney) + '\u00A0\u20BD'}
                            </td>
                            {showFactory && (
                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                    {mode === 'qty' ? formatNumber(totals.factoryTotal, 0) : formatNumber(totals.factoryMoney) + '\u00A0\u20BD'}
                                </td>
                            )}
                            {showVehicles && (
                                <>
                                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {mode === 'qty' ? formatNumber(totals.vehicleFormingTotal, 0) : formatNumber(totals.vehicleFormingMoney) + '\u00A0\u20BD'}
                                    </td>
                                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {mode === 'qty' ? formatNumber(totals.vehicleTransitTotal, 0) : formatNumber(totals.vehicleTransitMoney) + '\u00A0\u20BD'}
                                    </td>
                                </>
                            )}
                        </tr>
                        {filtered.map(group => {
                            const groupKey = group.group_name || '';
                            const isExp = expandedGroups.has(groupKey);
                            const children = group.children || [];
                            const cost = group.avg_cost || 0;
                            const groupTrend = getTrendData(group as UnifiedStockRow);
                            const groupVariantTotal = getVariantTotal(group as UnifiedStockRow);
                            const groupMargin = groupTrend.revenue > 0 ? (groupTrend.profit / groupTrend.revenue) * 100 : 0;
                            const groupStockDays = groupTrend.avg_daily_qty > 0 ? groupVariantTotal / groupTrend.avg_daily_qty : 0;
                            const groupStockColor = groupStockDays > 0
                                ? groupStockDays < 14 ? 'var(--color-danger)' : groupStockDays < 30 ? 'var(--color-warning)' : 'var(--color-text)'
                                : 'var(--color-text-muted)';

                            return (
                                <React.Fragment key={groupKey}>
                                    {/* Group header row */}
                                    <tr
                                        onClick={() => toggleGroup(groupKey)}
                                        style={{ cursor: 'pointer', background: 'var(--color-bg-hover)' }}
                                    >
                                        <td style={{ fontWeight: 600 }}>
                                            <span style={{ marginRight: 8, fontSize: 12 }}>{isExp ? '\u25BC' : '\u25B6'}</span>
                                            {groupKey}
                                            {group.items_count != null && (
                                                <span style={{ color: 'var(--color-text-muted)', fontWeight: 400, marginLeft: 8, fontSize: 13 }}>
                                                    ({group.items_count})
                                                </span>
                                            )}
                                        </td>
                                        <td style={{ textAlign: 'right' }}>{group.items_count != null ? formatNumber(group.items_count, 0) : DASH}</td>
                                        {groupBy === 'abc' && (
                                            <td>
                                                {group.abc_class && (
                                                    <span className="badge" style={{
                                                        background: ({ A: 'var(--color-success)', B: 'var(--color-warning)', C: 'var(--color-danger)' } as Record<string, string>)[group.abc_class] || 'var(--color-text-muted)',
                                                        color: '#fff',
                                                    }}>{group.abc_class}</span>
                                                )}
                                            </td>
                                        )}
                                        {/* Sales metrics — moved to front */}
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                            {groupTrend.avg_daily_qty > 0 ? formatNumber(groupTrend.avg_daily_qty, 1) : DASH}
                                        </td>
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                            {groupTrend.revenue > 0 ? formatNumber(groupTrend.revenue) + '\u00A0\u20BD' : DASH}
                                        </td>
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                            {groupTrend.revenue > 0 ? (
                                                <span style={{ color: groupMargin >= 0 ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                                                    {formatNumber(groupMargin, 1)}%
                                                </span>
                                            ) : DASH}
                                        </td>
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                            {groupStockDays > 0 ? (
                                                <span style={{ color: groupStockColor, fontWeight: 600 }}>{formatNumber(groupStockDays, 0)}</span>
                                            ) : DASH}
                                        </td>
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                            <strong style={{ color: 'var(--color-accent)' }}>
                                                {fmtGroupVal(groupVariantTotal, cost, group as UnifiedStockRow)}
                                            </strong>
                                        </td>
                                        {showOwn && (
                                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(group.total_own || 0, cost, group as UnifiedStockRow)}</td>
                                        )}
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(group.total_wb || 0, cost, group as UnifiedStockRow)}</td>
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(group.wb_in_way_to_client || 0, cost, group as UnifiedStockRow)}</td>
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(group.wb_in_way_from_client || 0, cost, group as UnifiedStockRow)}</td>
                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(group.in_transit || 0, cost, group as UnifiedStockRow)}</td>
                                        {showFactory && (
                                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtFactoryCell(group.factory_qty || 0, group as UnifiedStockRow)}</td>
                                        )}
                                        {showVehicles && (
                                            <>
                                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(group.vehicle_forming_qty || 0, cost, group as UnifiedStockRow)}</td>
                                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(group.vehicle_transit_qty || 0, cost, group as UnifiedStockRow)}</td>
                                            </>
                                        )}
                                    </tr>

                                    {/* Expanded children */}
                                    {isExp && children.map(child => {
                                        const hasSubChildren = child.children && child.children.length > 0;
                                        const childKey = `${groupKey}:${child.group_name || child.barcode}`;
                                        const subExp = expandedSubGroups.has(childKey);
                                        const childCost = child.avg_cost || 0;
                                        const childTrend = getTrendData(child);
                                        const childVariantTotal = getVariantTotal(child);
                                        const childMargin = childTrend.revenue > 0 ? (childTrend.profit / childTrend.revenue) * 100 : 0;
                                        const childStockDays = childTrend.avg_daily_qty > 0 ? childVariantTotal / childTrend.avg_daily_qty : 0;
                                        const childStockColor = childStockDays > 0
                                            ? childStockDays < 14 ? 'var(--color-danger)' : childStockDays < 30 ? 'var(--color-warning)' : 'var(--color-text)'
                                            : 'var(--color-text-muted)';

                                        if (hasSubChildren) {
                                            // Sub-group row (e.g., subject within brand)
                                            return (
                                                <React.Fragment key={childKey}>
                                                    <tr
                                                        onClick={() => toggleSubGroup(childKey)}
                                                        style={{ cursor: 'pointer' }}
                                                    >
                                                        <td style={{ paddingLeft: 32, fontWeight: 500 }}>
                                                            <span style={{ marginRight: 8, fontSize: 11 }}>{subExp ? '\u25BC' : '\u25B6'}</span>
                                                            {child.group_name || child.article_seller || '\u2014'}
                                                            {child.items_count != null && (
                                                                <span style={{ color: 'var(--color-text-muted)', fontWeight: 400, marginLeft: 8, fontSize: 12 }}>
                                                                    ({child.items_count})
                                                                </span>
                                                            )}
                                                        </td>
                                                        <td style={{ textAlign: 'right' }}>{child.items_count != null ? formatNumber(child.items_count, 0) : DASH}</td>
                                                        {groupBy === 'abc' && <td>{DASH}</td>}
                                                        {/* Sales metrics — moved to front */}
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                            {childTrend.avg_daily_qty > 0 ? formatNumber(childTrend.avg_daily_qty, 1) : DASH}
                                                        </td>
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                            {childTrend.revenue > 0 ? formatNumber(childTrend.revenue) + '\u00A0\u20BD' : DASH}
                                                        </td>
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                            {childTrend.revenue > 0 ? (
                                                                <span style={{ color: childMargin >= 0 ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                                                                    {formatNumber(childMargin, 1)}%
                                                                </span>
                                                            ) : DASH}
                                                        </td>
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                            {childStockDays > 0 ? (
                                                                <span style={{ color: childStockColor, fontWeight: 600 }}>{formatNumber(childStockDays, 0)}</span>
                                                            ) : DASH}
                                                        </td>
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                            <strong style={{ color: 'var(--color-accent)' }}>
                                                                {fmtGroupVal(childVariantTotal, childCost, child as UnifiedStockRow)}
                                                            </strong>
                                                        </td>
                                                        {showOwn && (
                                                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(child.total_own || 0, childCost, child as UnifiedStockRow)}</td>
                                                        )}
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(child.total_wb || 0, childCost, child as UnifiedStockRow)}</td>
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(child.wb_in_way_to_client || 0, childCost, child as UnifiedStockRow)}</td>
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(child.wb_in_way_from_client || 0, childCost, child as UnifiedStockRow)}</td>
                                                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(child.in_transit || 0, childCost, child as UnifiedStockRow)}</td>
                                                        {showFactory && (
                                                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtFactoryCell(child.factory_qty || 0, child as UnifiedStockRow)}</td>
                                                        )}
                                                        {showVehicles && (
                                                            <>
                                                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(child.vehicle_forming_qty || 0, childCost, child as UnifiedStockRow)}</td>
                                                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtGroupVal(child.vehicle_transit_qty || 0, childCost, child as UnifiedStockRow)}</td>
                                                            </>
                                                        )}
                                                    </tr>

                                                    {/* Level 2 articles */}
                                                    {subExp && child.children!.map(article => renderArticleRow(article, 64))}
                                                </React.Fragment>
                                            );
                                        }

                                        // Direct article child (no sub-children)
                                        return renderArticleRow(child, 32);
                                    })}
                                </React.Fragment>
                            );
                        })}
                    </tbody>
                </table>
                <div style={{ padding: '12px 16px', fontSize: 13, color: 'var(--color-text-muted)' }}>
                    {filtered.length} групп
                </div>
            </div>
        );
    };

    return (
        <>
            {/* Summary */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16, display: 'flex', gap: 24, flexWrap: 'wrap', alignItems: 'center' }}>
                {mode === 'qty' ? (
                    <>
                        <span title="Товар физически на наших/фулфилмент складах" style={{ fontSize: 14, fontWeight: 500 }}>Свои: {formatNumber(totals.ownTotal)}</span>
                        <span title="Физически на складах WB — как в кабинете" style={{ fontSize: 14, fontWeight: 500, color: 'var(--color-accent)' }}>WB: {formatNumber(totals.wbTotal)}</span>
                        {totals.wbToClient > 0 && <span title="Заказы едут от склада WB к покупателям" style={{ fontSize: 14, fontWeight: 500 }}>До получателей: {formatNumber(totals.wbToClient)}</span>}
                        {totals.wbFromClient > 0 && <span title="Возвраты от покупателей — едут обратно на склад WB" style={{ fontSize: 14, fontWeight: 500 }}>Возвраты на WB: {formatNumber(totals.wbFromClient)}</span>}
                        {totals.inTransit > 0 && <span title="Наши отгрузки на WB: отгружено, WB ещё не принял" style={{ fontSize: 14, fontWeight: 500 }}>В пути: {formatNumber(totals.inTransit)}</span>}
                        {variant === 3 && totals.factoryTotal > 0 && (
                            <span style={{ fontSize: 14, fontWeight: 500 }}>На фабрике: {formatNumber(totals.factoryTotal)}</span>
                        )}
                        {variant === 3 && (totals.vehicleFormingTotal + totals.vehicleTransitTotal) > 0 && (
                            <span style={{ fontSize: 14, fontWeight: 500 }}>В машинах: {formatNumber(totals.vehicleFormingTotal + totals.vehicleTransitTotal)}</span>
                        )}
                        <span style={{ fontSize: 14, fontWeight: 700 }}>Итого: {formatNumber(totals.variantTotal)} шт</span>
                    </>
                ) : (
                    <>
                        <span style={{ fontSize: 14, fontWeight: 500 }}>Свои: {formatNumber(totals.ownMoney)} {'\u20BD'}</span>
                        <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--color-accent)' }}>WB: {formatNumber(totals.wbMoney)} {'\u20BD'}</span>
                        {totals.wbToClientMoney > 0 && <span style={{ fontSize: 14, fontWeight: 500 }}>До получателей: {formatNumber(totals.wbToClientMoney)} {'\u20BD'}</span>}
                        {totals.wbFromClientMoney > 0 && <span style={{ fontSize: 14, fontWeight: 500 }}>Возвраты на WB: {formatNumber(totals.wbFromClientMoney)} {'\u20BD'}</span>}
                        {totals.transitMoney > 0 && <span style={{ fontSize: 14, fontWeight: 500 }}>В пути: {formatNumber(totals.transitMoney)} {'\u20BD'}</span>}
                        {variant === 3 && totals.factoryMoney > 0 && (
                            <span style={{ fontSize: 14, fontWeight: 500 }}>На фабрике: {formatNumber(totals.factoryMoney)} {'\u20BD'}</span>
                        )}
                        {variant === 3 && (totals.vehicleFormingMoney + totals.vehicleTransitMoney) > 0 && (
                            <span style={{ fontSize: 14, fontWeight: 500 }}>В машинах: {formatNumber(totals.vehicleFormingMoney + totals.vehicleTransitMoney)} {'\u20BD'}</span>
                        )}
                        <span style={{ fontSize: 14, fontWeight: 700 }}>Итого: {formatNumber(totals.variantMoney)} {'\u20BD'}</span>
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                            ({mode === 'cost' ? 'себестоимость' : mode === 'revenue' ? 'выручка при продаже' : 'прибыль при продаже'})
                        </span>
                    </>
                )}
            </div>

            {/* Новинки KPI — SKUs with no sales in last 60 days */}
            {noveltyKpi.skuCount > 0 && (
                <div
                    className="glass-card"
                    style={{ padding: 16, marginBottom: 16 }}
                    title="Новинка = товар без единой продажи за последние 60 дней (новый артикул или давно не продававшийся). Учитываются остатки везде: свой склад + WB + в пути + фабрика + машины."
                >
                    <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap', alignItems: 'center' }}>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                            <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                                {'\u{1F4E6}'} Новинки
                            </span>
                            <span style={{ fontSize: 20, fontWeight: 700 }}>
                                {formatNumber(noveltyKpi.skuCount, 0)} SKU
                            </span>
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                {formatNumber(noveltyKpi.qtyTotal, 0)} шт суммарно
                            </span>
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                            <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                                Себестоимость
                            </span>
                            <span style={{ fontSize: 20, fontWeight: 700 }} title="Сумма себестоимости по всем новинкам: склад + WB + в пути + фабрика + машины">
                                {formatNumber(noveltyKpi.costTotal)} {'\u20BD'}
                            </span>
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                заморожено в новинках
                            </span>
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                            <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                                Потенциал выручки
                            </span>
                            <span
                                style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-accent)' }}
                                title="qty × средняя цена продажи в категории (за 30д). Оценка: если новинки продадутся как средний товар их категории, они принесут столько выручки."
                            >
                                {'\u2248'} {formatNumber(noveltyKpi.revenuePotential)} {'\u20BD'}
                            </span>
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                оценка по категории
                            </span>
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                            <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                                Ожидаемая прибыль
                            </span>
                            <span
                                style={{ fontSize: 20, fontWeight: 700, color: noveltyKpi.profitPotential >= 0 ? 'var(--color-success)' : 'var(--color-danger)' }}
                                title="qty × средняя прибыль на единицу в категории (после комиссии/логистики/налога). Оценка."
                            >
                                {'\u2248'} {formatNumber(noveltyKpi.profitPotential)} {'\u20BD'}
                            </span>
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                {noveltyKpi.revenuePotential > 0
                                    ? `${formatNumber((noveltyKpi.profitPotential / noveltyKpi.revenuePotential) * 100, 1)}% маржи`
                                    : DASH}
                            </span>
                        </div>
                        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
                            <div style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
                                {([
                                    { key: 'category' as const, label: 'По категории' },
                                    { key: 'brand' as const, label: 'По бренду' },
                                ]).map((btn, i) => {
                                    const isActive = noveltyBreakdown === btn.key;
                                    return (
                                        <button
                                            key={btn.key}
                                            onClick={() => {
                                                setNoveltyBreakdown(isActive ? null : btn.key);
                                                setExpandedNoveltyGroups(new Set());  // reset expansions on grouping change
                                            }}
                                            style={{
                                                padding: '6px 12px', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer',
                                                borderLeft: i > 0 ? '1px solid var(--color-border)' : 'none',
                                                background: isActive ? 'var(--color-accent)' : 'var(--color-bg-card)',
                                                color: isActive ? '#fff' : 'var(--color-text)',
                                            }}
                                        >{btn.label}</button>
                                    );
                                })}
                            </div>
                        </div>
                    </div>

                    {/* Breakdown table — only rendered when a grouping is selected */}
                    {noveltyBreakdown && (
                        <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--color-border)' }}>
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                                <thead>
                                    <tr style={{ textAlign: 'left', color: 'var(--color-text-muted)', fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                                        <th style={{ padding: '6px 8px' }}>{noveltyBreakdown === 'category' ? 'Категория' : 'Бренд'}</th>
                                        <th style={{ padding: '6px 8px', textAlign: 'right' }}>SKU</th>
                                        <th style={{ padding: '6px 8px', textAlign: 'right' }}>Шт</th>
                                        <th style={{ padding: '6px 8px', textAlign: 'right' }}>Себест., ₽</th>
                                        <th style={{ padding: '6px 8px', textAlign: 'right' }}>Потенциал, ₽</th>
                                        <th style={{ padding: '6px 8px', textAlign: 'right' }}>Прибыль, ₽</th>
                                        <th style={{ padding: '6px 8px', textAlign: 'right' }}>Маржа %</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {(noveltyBreakdown === 'category' ? noveltyKpi.byCategory : noveltyKpi.byBrand).map((g) => {
                                        const isOpen = expandedNoveltyGroups.has(g.key);
                                        return (
                                            <React.Fragment key={g.key}>
                                                <tr
                                                    onClick={() => toggleNoveltyGroup(g.key)}
                                                    style={{ borderTop: '1px solid var(--color-border)', cursor: 'pointer', userSelect: 'none' }}
                                                    title="Клик — показать товары в этой группе"
                                                >
                                                    <td style={{ padding: '6px 8px', fontWeight: 500 }}>
                                                        <span style={{ display: 'inline-block', width: 16, color: 'var(--color-text-muted)' }}>{isOpen ? '\u25BE' : '\u25B8'}</span>
                                                        {g.key}
                                                    </td>
                                                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(g.skuCount, 0)}</td>
                                                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(g.qty, 0)}</td>
                                                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(g.cost)}</td>
                                                    <td style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--color-accent)', fontWeight: 600 }}>{'\u2248'}&nbsp;{formatNumber(g.revenue)}</td>
                                                    <td style={{ padding: '6px 8px', textAlign: 'right', color: g.profit >= 0 ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>{'\u2248'}&nbsp;{formatNumber(g.profit)}</td>
                                                    <td style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--color-text-muted)' }}>
                                                        {g.revenue > 0 ? `${formatNumber((g.profit / g.revenue) * 100, 1)}%` : DASH}
                                                    </td>
                                                </tr>
                                                {isOpen && g.items.map((item) => (
                                                    <tr key={`${g.key}__${item.nomenclature_id}`} style={{ background: 'rgba(0,0,0,0.02)', fontSize: 12 }}>
                                                        <td style={{ padding: '5px 8px 5px 32px', color: 'var(--color-text)' }}>
                                                            <div style={{ fontWeight: 500 }}>{item.article_seller || '\u2014'}</div>
                                                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{item.barcode || '\u2014'}</div>
                                                        </td>
                                                        <td style={{ padding: '5px 8px', textAlign: 'right', color: 'var(--color-text-muted)' }}>{DASH}</td>
                                                        <td style={{ padding: '5px 8px', textAlign: 'right' }}>{formatNumber(item.qty, 0)}</td>
                                                        <td style={{ padding: '5px 8px', textAlign: 'right' }}>{formatNumber(item.cost)}</td>
                                                        <td style={{ padding: '5px 8px', textAlign: 'right', color: 'var(--color-accent)' }}>{'\u2248'}&nbsp;{formatNumber(item.revenue)}</td>
                                                        <td style={{ padding: '5px 8px', textAlign: 'right', color: item.profit >= 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>{'\u2248'}&nbsp;{formatNumber(item.profit)}</td>
                                                        <td style={{ padding: '5px 8px', textAlign: 'right', color: 'var(--color-text-muted)' }}>
                                                            {item.revenue > 0 ? `${formatNumber((item.profit / item.revenue) * 100, 1)}%` : DASH}
                                                        </td>
                                                    </tr>
                                                ))}
                                            </React.Fragment>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}

            {/* Search + Grouping + Mode toggle + Refresh */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                <input
                    className="form-input"
                    placeholder="Поиск по баркоду / артикулу..."
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    style={{ maxWidth: 300 }}
                />
                <select
                    className="form-input"
                    value={groupBy}
                    onChange={e => onGroupChange(e.target.value)}
                    style={{ maxWidth: 200 }}
                >
                    <option value="sku">По товарам</option>
                    <option value="brand">По брендам</option>
                    <option value="subject">По категориям</option>
                    <option value="imt">По склейкам</option>
                    <option value="tag">По якорям</option>
                    <option value="abc">ABC анализ</option>
                </select>
                {/* Brand filter */}
                <select
                    className="form-input"
                    value={brand}
                    onChange={e => onBrandChange(e.target.value)}
                    title="Фильтр по бренду"
                    style={{ maxWidth: 180 }}
                >
                    <option value="">Все бренды</option>
                    {availableBrands.map(b => (
                        <option key={b} value={b}>{b}</option>
                    ))}
                </select>
                {/* Variant toggle */}
                <div style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
                    {([
                        { key: 1 as const, label: 'WB', title: 'Только остатки WB' },
                        { key: 2 as const, label: 'WB+Свои', title: 'WB + Свои склады + В пути на WB' },
                        { key: 3 as const, label: 'Всё', title: 'Всё включая фабрику и машины' },
                    ]).map((btn, i) => (
                        <button
                            key={btn.key}
                            onClick={() => setVariant(btn.key)}
                            title={btn.title}
                            style={{
                                padding: '6px 12px', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer',
                                borderLeft: i > 0 ? '1px solid var(--color-border)' : 'none',
                                background: variant === btn.key ? 'var(--color-accent)' : 'var(--color-bg-card)',
                                color: variant === btn.key ? '#fff' : 'var(--color-text)',
                            }}
                        >{btn.label}</button>
                    ))}
                </div>
                <div style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
                    {([
                        { key: 'qty', label: 'шт' },
                        { key: 'cost', label: 'Себест.' },
                        { key: 'revenue', label: 'Реализ.' },
                        { key: 'profit', label: 'Прибыль' },
                    ] as { key: typeof mode; label: string }[]).map((btn, i) => (
                        <button
                            key={btn.key}
                            onClick={() => setMode(btn.key)}
                            style={{
                                padding: '6px 12px', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer',
                                borderLeft: i > 0 ? '1px solid var(--color-border)' : 'none',
                                background: mode === btn.key ? 'var(--color-accent)' : 'var(--color-bg-card)',
                                color: mode === btn.key ? '#fff' : 'var(--color-text)',
                            }}
                        >{btn.label}</button>
                    ))}
                </div>
                {/* Trend period toggle */}
                <div style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}>
                    {([7, 14, 30] as const).map((d, i) => (
                        <button
                            key={d}
                            onClick={() => setTrendPeriod(d)}
                            title={`Тренд продаж за ${d} дней`}
                            style={{
                                padding: '6px 10px', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer',
                                borderLeft: i > 0 ? '1px solid var(--color-border)' : 'none',
                                background: trendPeriod === d ? 'var(--color-accent)' : 'var(--color-bg-card)',
                                color: trendPeriod === d ? '#fff' : 'var(--color-text)',
                            }}
                        >{d}д</button>
                    ))}
                </div>
                {/* Fact / Forecast toggle */}
                <div
                    title="Факт — суммы сходятся с БДР. С прогнозом — для товаров в пути (фабрика / машины) подставляется средняя реализация категории."
                    style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}
                >
                    {([
                        { key: false, label: 'Факт' },
                        { key: true, label: 'С прогнозом' },
                    ] as const).map((btn, i) => (
                        <button
                            key={String(btn.key)}
                            onClick={() => onForecastChange(btn.key)}
                            style={{
                                padding: '6px 12px', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer',
                                borderLeft: i > 0 ? '1px solid var(--color-border)' : 'none',
                                background: includeForecast === btn.key ? 'var(--color-accent)' : 'var(--color-bg-card)',
                                color: includeForecast === btn.key ? '#fff' : 'var(--color-text)',
                            }}
                        >{btn.label}</button>
                    ))}
                </div>
                {includeForecast && (
                    <span
                        title="В итоги включена оценка по товарам в пути (фабрика / машины) по средней реализации категории. Суммы НЕ совпадают с БДР."
                        style={{
                            fontSize: 11,
                            fontWeight: 600,
                            padding: '3px 10px',
                            borderRadius: 12,
                            background: 'rgba(255, 159, 10, 0.12)',
                            color: 'var(--color-warning)',
                            whiteSpace: 'nowrap',
                        }}
                    >
                        {'\u26A0'} Прогноз включён
                    </span>
                )}
                {activePeriodDates && (
                    <span
                        title={`Реализация и прибыль рассчитаны за период ${formatDate(activePeriodDates.from)} — ${formatDate(activePeriodDates.to)}. Окно заканчивается вчерашним днём (за сегодня данных WB ещё нет).`}
                        style={{
                            fontSize: 12,
                            color: 'var(--color-text-muted)',
                            whiteSpace: 'nowrap',
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 4,
                        }}
                    >
                        {'\u{1F4C5}'} {formatDate(activePeriodDates.from)} {'\u2014'} {formatDate(activePeriodDates.to)}
                    </span>
                )}
                {/* Stock-cover filter: показать только товары с запасом ≤ N дней */}
                <div
                    title="Показать товары с запасом ≤ N дней (по выбранному тренду)"
                    style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden' }}
                >
                    {([
                        { key: 0 as const, label: 'Все' },
                        { key: 7 as const, label: '≤7д' },
                        { key: 30 as const, label: '≤30д' },
                        { key: 60 as const, label: '≤60д' },
                    ]).map((btn, i) => (
                        <button
                            key={btn.key}
                            onClick={() => setStockDaysFilter(btn.key)}
                            title={btn.key === 0 ? 'Без фильтра по запасу' : `Запас ≤ ${btn.key} дней`}
                            style={{
                                padding: '6px 10px', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer',
                                borderLeft: i > 0 ? '1px solid var(--color-border)' : 'none',
                                background: stockDaysFilter === btn.key ? 'var(--color-warning)' : 'var(--color-bg-card)',
                                color: stockDaysFilter === btn.key ? '#fff' : 'var(--color-text)',
                            }}
                        >{btn.label}</button>
                    ))}
                </div>
                <button
                    className="btn btn-secondary btn-sm"
                    onClick={handleSync}
                    disabled={syncing}
                    title="Скачать свежий отчёт WB «Остатки на складах» (готовится до ~90с) и обновить колонку «WB склады»"
                >
                    {syncing ? '\u23F3 Обновление (до 90с)...' : '\uD83D\uDD04 Обновить WB остатки'}
                </button>
                {syncMsg && (
                    <span style={{ fontSize: 13, fontWeight: 500, color: syncMsg.ok ? 'var(--color-success)' : 'var(--color-danger)' }}>
                        {syncMsg.ok ? '✓ ' : '⚠ '}{syncMsg.text}
                    </span>
                )}
                {isLoading && <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{'\u23F3'} Загрузка...</span>}
            </div>

            {filtered.length === 0 ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', opacity: 0.6 }}>
                    <div style={{ fontSize: 48 }}>{'\uD83D\uDCE6'}</div>
                    <div>Нет данных по единым остаткам</div>
                </div>
            ) : isGrouped ? (
                renderGroupedTable()
            ) : (
                <TanStackDataTable
                    columns={cols}
                    data={rowsForTable}
                    exportName="unified_stock"
                    enableSorting
                    enablePagination={false}
                    actions={
                        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                            {filtered.length} позиций
                        </span>
                    }
                    summaryRow={
                        <tr style={{ background: 'var(--color-bg)', fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>
                            <td>Итого</td>
                            <td />
                            {groupBy === 'abc' && <td />}
                            {/* Sales metrics */}
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{formatNumber(totals.trendDailySum, 1)}</td>
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{formatNumber(totals.bdrRevenueSum)}{'\u00A0\u20BD'}</td>
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {totals.bdrRevenueSum > 0
                                    ? <span style={{ color: totals.bdrProfitSum >= 0 ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                                        {formatNumber((totals.bdrProfitSum / totals.bdrRevenueSum) * 100, 1)}%
                                      </span>
                                    : DASH}
                            </td>
                            <td style={{ textAlign: 'right', color: totals.defectSum > 0 ? 'var(--color-warning)' : undefined, fontWeight: 600, whiteSpace: 'nowrap' }}>
                                {totals.defectSum > 0
                                    ? (mode === 'qty' ? formatNumber(totals.defectSum, 0) : formatNumber(totals.defectMoney) + '\u00A0\u20BD')
                                    : DASH}
                            </td>
                            <td />
                            <td style={{ textAlign: 'right', color: 'var(--color-accent)', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.variantTotal, 0) : formatNumber(totals.variantMoney) + '\u00A0\u20BD'}
                            </td>
                            {showOwn && ownWarehouses.map(wh => {
                                const qty = filtered.reduce((s, r) => s + (r.warehouses[wh] || 0), 0);
                                const money = filtered.reduce((s, r) => {
                                    const q = r.warehouses[wh] || 0;
                                    const m = mode === 'cost' ? r.avg_cost : mode === 'revenue' ? r.avg_price : mode === 'profit' ? r.avg_profit : 0;
                                    return s + q * (m || 0);
                                }, 0);
                                return (
                                    <td key={wh} style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {mode === 'qty' ? formatNumber(qty, 0) : formatNumber(money) + '\u00A0\u20BD'}
                                    </td>
                                );
                            })}
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.wbTotal, 0) : formatNumber(totals.wbMoney) + '\u00A0\u20BD'}
                            </td>
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.wbToClient, 0) : formatNumber(totals.wbToClientMoney) + '\u00A0\u20BD'}
                            </td>
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.wbFromClient, 0) : formatNumber(totals.wbFromClientMoney) + '\u00A0\u20BD'}
                            </td>
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {mode === 'qty' ? formatNumber(totals.inTransit, 0) : formatNumber(totals.transitMoney) + '\u00A0\u20BD'}
                            </td>
                            {showFactory && (
                                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                    {mode === 'qty' ? formatNumber(totals.factoryTotal, 0) : formatNumber(totals.factoryMoney) + '\u00A0\u20BD'}
                                </td>
                            )}
                            {showVehicles && (
                                <>
                                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {mode === 'qty' ? formatNumber(totals.vehicleFormingTotal, 0) : formatNumber(totals.vehicleFormingMoney) + '\u00A0\u20BD'}
                                    </td>
                                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {mode === 'qty' ? formatNumber(totals.vehicleTransitTotal, 0) : formatNumber(totals.vehicleTransitMoney) + '\u00A0\u20BD'}
                                    </td>
                                </>
                            )}
                        </tr>
                    }
                />
            )}
        </>
    );
}

// ─── Main page ───────────────────────────────────────────────────────────

export default function StockSummaryPage() {
    const [tab, setTab] = useState('summary');
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [summary, setSummary] = useState<StockSummaryRow[]>([]);
    const [unified, setUnified] = useState<UnifiedStockRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [unifiedLoading, setUnifiedLoading] = useState(false);
    const [error, setError] = useState('');
    const [unifiedGroupBy, setUnifiedGroupBy] = useState('sku');
    const [brandFilter, setBrandFilter] = useState<string>('');
    const [includeForecast, setIncludeForecast] = useState<boolean>(false);

    const fetchUnified = useCallback(async (gb: string, brand: string, forecast: boolean) => {
        setUnifiedLoading(true);
        try {
            const un = await api.getUnifiedStock(gb, brand || undefined, forecast);
            setUnified(un);
        } catch { /* ignore */ }
        setUnifiedLoading(false);
    }, []);

    const handleGroupChange = useCallback(async (gb: string) => {
        setUnified([]);          // clear stale data BEFORE switching mode
        setUnifiedGroupBy(gb);
        await fetchUnified(gb, brandFilter, includeForecast);
    }, [fetchUnified, brandFilter, includeForecast]);

    const handleBrandChange = useCallback(async (brand: string) => {
        setUnified([]);
        setBrandFilter(brand);
        await fetchUnified(unifiedGroupBy, brand, includeForecast);
    }, [fetchUnified, unifiedGroupBy, includeForecast]);

    const handleForecastChange = useCallback(async (forecast: boolean) => {
        setUnified([]);
        setIncludeForecast(forecast);
        await fetchUnified(unifiedGroupBy, brandFilter, forecast);
    }, [fetchUnified, unifiedGroupBy, brandFilter]);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [wh, sm, un] = await Promise.all([
                api.getWarehouses(),
                api.getStockSummary(),
                api.getUnifiedStock('sku', undefined, false),
            ]);
            setWarehouses(wh);
            setSummary(sm);
            setUnified(un);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Остатки</h1>
                    <p className="page-subtitle">Остатки по всем складам</p>
                </div>
            </div>

            <TabLayout
                tabs={[
                    { key: 'summary', label: 'Сводные остатки' },
                    { key: 'unified', label: 'Единые остатки' },
                ]}
                active={tab}
                onChange={setTab}
            />

            {tab === 'summary' && (
                <SummaryTab warehouses={warehouses} summary={summary} />
            )}
            {tab === 'unified' && (
                <UnifiedTab
                    data={unified}
                    onRefresh={load}
                    groupBy={unifiedGroupBy}
                    onGroupChange={handleGroupChange}
                    brand={brandFilter}
                    onBrandChange={handleBrandChange}
                    isLoading={unifiedLoading}
                    includeForecast={includeForecast}
                    onForecastChange={handleForecastChange}
                />
            )}
        </div>
    );
}
