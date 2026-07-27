'use client';
import React, { useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';

// Ключи персиста БДР — с привязкой к текущему проекту, чтобы каждый проект
// (напр. этот и «Вяткин») помнил свой режим/группировку/категории отдельно.
const prefKey = (k: string) => `wbbdr_${k}_p${api.getProjectId() ?? 'x'}`;

/* ─── KPI Card ──────────────────────────────────── */
function KpiCard({ label, value, sub, color }: { label: string; value: string; sub: string; color?: string }) {
    return (
        <div className="glass-card" style={{ padding: '14px 16px' }}>
            <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: color || 'var(--text-primary)' }}>{value}</div>
            {sub && <div style={{ fontSize: 12, opacity: 0.5, marginTop: 2 }}>{sub}</div>}
        </div>
    );
}

/* ─── Top Products Widget ──────────────────────── */
function TopProductsWidget({ articles }: { articles: any[] }) {
    const [mode, setMode] = useState<'profit' | 'loss'>('profit');
    const sorted = React.useMemo(() => {
        const arr = articles.filter((a: any) => a.profit !== undefined && a.profit !== null && a.sa_name);
        if (mode === 'profit') return [...arr].sort((a, b) => (b.profit || 0) - (a.profit || 0)).slice(0, 10);
        return [...arr].sort((a, b) => (a.profit || 0) - (b.profit || 0)).slice(0, 10).filter(a => (a.profit || 0) < 0);
    }, [articles, mode]);

    if (!articles.length) return null;
    const isProfit = mode === 'profit';
    const accentColor = isProfit ? '#22c55e' : '#ef4444';
    const accentBg = isProfit ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)';
    const accentBorder = isProfit ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)';

    return (
        <div className="glass-card" style={{ marginBottom: 16, padding: 0, overflow: 'hidden' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 20px', borderBottom: `1px solid ${accentBorder}`, background: accentBg, transition: 'all 0.3s ease' }}>
                <div style={{ fontWeight: 700, fontSize: 15, color: accentColor, display: 'flex', alignItems: 'center', gap: 8 }}>
                    {isProfit ? '📈' : '📉'} Топ-10 {isProfit ? 'прибыльных' : 'убыточных'} товаров
                </div>
                <div style={{ display: 'flex', borderRadius: 8, overflow: 'hidden', border: '1px solid #e5e7eb', background: '#f3f4f6' }}>
                    <button onClick={() => setMode('profit')} style={{ padding: '6px 16px', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer', background: isProfit ? '#22c55e' : 'transparent', color: isProfit ? '#fff' : '#6b7280', transition: 'all 0.2s ease' }}>💰 Прибыльные</button>
                    <button onClick={() => setMode('loss')} style={{ padding: '6px 16px', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer', background: !isProfit ? '#ef4444' : 'transparent', color: !isProfit ? '#fff' : '#6b7280', transition: 'all 0.2s ease' }}>📉 Убыточные</button>
                </div>
            </div>
            {sorted.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            <tr style={{ borderBottom: '2px solid #e5e7eb', background: '#f9fafb' }}>
                                <th style={{ textAlign: 'left', padding: '10px 16px', color: '#6b7280', fontWeight: 600, width: 40 }}>№</th>
                                <th style={{ textAlign: 'left', padding: '10px 12px', color: '#6b7280', fontWeight: 600 }}>Артикул</th>
                                <th style={{ textAlign: 'left', padding: '10px 12px', color: '#6b7280', fontWeight: 600 }}>Категория</th>
                                <th style={{ textAlign: 'right', padding: '10px 12px', color: accentColor, fontWeight: 700 }}>Прибыль ₽</th>
                                <th style={{ textAlign: 'right', padding: '10px 12px', color: '#6b7280', fontWeight: 600 }}>Маржа %</th>
                                <th style={{ textAlign: 'right', padding: '10px 12px', color: '#6b7280', fontWeight: 600 }}>ROI %</th>
                                <th style={{ textAlign: 'right', padding: '10px 12px', color: '#6b7280', fontWeight: 600 }}>Продажи ₽</th>
                                <th style={{ textAlign: 'right', padding: '10px 12px', color: '#f59e0b', fontWeight: 600 }}>Реклама ₽</th>
                                <th style={{ textAlign: 'right', padding: '10px 16px', color: '#6b7280', fontWeight: 600 }}>ДРР %</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sorted.map((a: any, i: number) => {
                                const profitColor = (a.profit || 0) >= 0 ? '#22c55e' : '#ef4444';
                                return (
                                    <tr key={a.sa_name || i} style={{ borderBottom: '1px solid #f3f4f6', transition: 'background 0.15s' }}
                                        onMouseEnter={(e) => (e.currentTarget.style.background = '#f8fafc')}
                                        onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}>
                                        <td style={{ padding: '10px 16px', color: '#9ca3af', fontWeight: 600 }}>{i + 1}</td>
                                        <td style={{ padding: '10px 12px', fontWeight: 600, color: '#111827' }}>{a.sa_name || '—'}</td>
                                        <td style={{ padding: '10px 12px', color: '#6b7280', fontSize: 12 }}>{a.subject || '—'}</td>
                                        <td style={{ padding: '10px 12px', textAlign: 'right', fontWeight: 700, color: profitColor }}>{formatNumber(a.profit || 0)}</td>
                                        <td style={{ padding: '10px 12px', textAlign: 'right', color: '#374151' }}>{a.margin_pct?.toFixed(1) || '—'}%</td>
                                        <td style={{ padding: '10px 12px', textAlign: 'right', color: '#374151' }}>{a.roi?.toFixed(2) || '—'}%</td>
                                        <td style={{ padding: '10px 12px', textAlign: 'right', color: '#374151' }}>{formatNumber(a.sales_amount || 0)}</td>
                                        <td style={{ padding: '10px 12px', textAlign: 'right', color: '#f59e0b', fontWeight: 500 }}>{formatNumber(a.adv_sum || 0)}</td>
                                        <td style={{ padding: '10px 16px', textAlign: 'right', color: '#374151' }}>{a.drr?.toFixed(1) || '—'}%</td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            ) : (
                <div style={{ padding: '24px 20px', textAlign: 'center', color: '#9ca3af', fontSize: 14 }}>
                    {mode === 'loss' ? 'Нет убыточных товаров 🎉' : 'Нет данных'}
                </div>
            )}
        </div>
    );
}

/* ═══════════════  WB БДР  ═══════════════ */
export function WbBdr() {
    const fmt = (d: Date) => d.toISOString().split('T')[0];
    const getLastWeek = () => {
        const now = new Date();
        const day = now.getDay();
        const diffToLastSun = day === 0 ? 7 : day;
        const lastSun = new Date(now);
        lastSun.setDate(now.getDate() - diffToLastSun);
        const lastMon = new Date(lastSun);
        lastMon.setDate(lastSun.getDate() - 6);
        return { from: fmt(lastMon), to: fmt(lastSun) };
    };
    const lw = getLastWeek();

    const [dateFrom, setDateFrom] = useState(lw.from);
    const [dateTo, setDateTo] = useState(lw.to);
    const [brand, setBrand] = useState('');
    const [articleSearch, setArticleSearch] = useState('');
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [syncStatus, setSyncStatus] = useState<any>(null);
    const [syncing, setSyncing] = useState(false);
    const [availableDates, setAvailableDates] = useState<string[]>([]);
    const [groupBy, setGroupBy] = useState<'article' | 'brand' | 'subject' | 'tag' | 'imt' | 'abc'>('article');
    const [periodMode, setPeriodMode] = useState<'sale' | 'report'>('sale');
    const [sortKey, setSortKey] = useState<string>('profit');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
    const [expandedAbc, setExpandedAbc] = useState<Record<string, boolean>>({});
    const [selectedCats, setSelectedCats] = useState<string[]>([]);
    const [catOpen, setCatOpen] = useState(false);
    const catRef = React.useRef<HTMLDivElement>(null);

    // Персист режима свода, группировки и выбранных категорий: заходишь — раздел
    // уже в твоём виде (напр. «По отчётам ВБ» + «По категориям» + твои категории),
    // лишних кликов нет.
    React.useEffect(() => {
        try {
            const pm = localStorage.getItem(prefKey('period_mode'));
            if (pm === 'sale' || pm === 'report') setPeriodMode(pm);
            const gb = localStorage.getItem(prefKey('group_by'));
            if (gb && ['article', 'brand', 'subject', 'tag', 'imt', 'abc'].includes(gb)) setGroupBy(gb as typeof groupBy);
            const cats = localStorage.getItem(prefKey('selected_cats'));
            if (cats) { const arr = JSON.parse(cats); if (Array.isArray(arr)) setSelectedCats(arr.filter((x: unknown) => typeof x === 'string')); }
        } catch { /* localStorage недоступен — тихо игнорируем */ }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Закрытие выпадашки категорий по клику вне её
    React.useEffect(() => {
        if (!catOpen) return;
        const onDown = (e: MouseEvent) => { if (catRef.current && !catRef.current.contains(e.target as Node)) setCatOpen(false); };
        document.addEventListener('mousedown', onDown);
        return () => document.removeEventListener('mousedown', onDown);
    }, [catOpen]);

    const persistCats = React.useCallback((next: string[]) => {
        setSelectedCats(next);
        try { localStorage.setItem(prefKey('selected_cats'), JSON.stringify(next)); } catch { /* noop */ }
    }, []);

    React.useEffect(() => {
        api.getWbBdrSyncStatus(dateFrom, dateTo).then(setSyncStatus).catch(() => {});
        api.getWbBdrAvailableWeeks().then(r => setAvailableDates(r.available_dates || [])).catch(() => {});
    }, [dateFrom, dateTo]);

    const loadData = React.useCallback(async () => {
        setLoading(true); setError('');
        try {
            const res = await api.getWbBdr(dateFrom, dateTo, brand || undefined, articleSearch || undefined, groupBy !== 'article' ? groupBy : undefined, periodMode !== 'sale' ? periodMode : undefined);
            setData(res);
            if (res?.sync_status) setSyncStatus(res.sync_status);
            else {
                api.getWbBdrSyncStatus(dateFrom, dateTo).then(setSyncStatus).catch(() => {});
            }
        } catch (e: any) { setError(e.message || 'Ошибка загрузки'); }
        finally { setLoading(false); }
    }, [dateFrom, dateTo, brand, articleSearch, groupBy, periodMode]);

    React.useEffect(() => { loadData(); }, [groupBy, periodMode]);

    const handleSync = React.useCallback(async () => {
        setSyncing(true);
        try {
            await api.triggerWbBdrSync();
            const st = await api.getWbBdrSyncStatus();
            setSyncStatus(st);
            await loadData();
        } catch (e: any) { setError('Ошибка синхронизации: ' + (e.message || '')); }
        finally { setSyncing(false); }
    }, [loadData]);

    const s = data?.summary;
    const rawArticles = data?.articles || [];
    const brands = data?.brands || [];
    const taxInfo = data?.tax_info || {};

    const groupLabel = groupBy === 'brand' ? 'Бренд' : groupBy === 'subject' ? 'Категория' : groupBy === 'tag' ? 'Ярлык' : groupBy === 'imt' ? 'Склейка' : groupBy === 'abc' ? 'Артикул' : 'Артикул';

    const bdrColumns: { key: string; label: string; color?: string; sticky?: boolean }[] = [
        { key: 'sa_name', label: groupLabel, sticky: true },
        { key: 'to_pay', label: 'К оплате' },
        { key: 'net_payout', label: 'Чистая выплата' },
        ...(groupBy !== 'brand' && groupBy !== 'tag' && groupBy !== 'imt' ? [{ key: 'brand', label: 'Бренд' }] : []),
        ...(groupBy !== 'subject' && groupBy !== 'tag' && groupBy !== 'imt' ? [{ key: 'subject', label: 'Категория' }] : []),
        ...(groupBy === 'article' ? [{ key: 'nm_id', label: 'Арт. МП' }] : []),
        { key: 'cost_price', label: 'Ср. С/С' },
        { key: 'other_deduction', label: 'Проч. удерж.' },
        { key: 'avg_retail_price', label: 'Ср. цена до скидок' },
        { key: 'avg_sale_price', label: 'Ср. цена продажи' },
        { key: 'realization', label: 'Реализация' },
        { key: 'turnover_days', label: 'Оборач. (дн.)' },
        { key: 'sales_amount', label: 'Продажи' },
        { key: 'ppvz_for_pay', label: 'К переч.' },
        { key: 'returns_amount', label: 'Возвраты' },
        { key: 'cost_total', label: 'С/С продаж' },
        { key: 'penalties', label: 'Штрафы' },
        { key: 'orders_count', label: 'Заказы шт' },
        { key: 'orders_sum', label: 'Заказы ₽' },
        { key: 'commission', label: 'Комиссия' },
        { key: 'total_wb_reward', label: 'Возн. ВБ' },
        { key: 'compensation', label: 'Компенсация' },
        { key: 'avg_logistics', label: 'Ср. логист.' },
        { key: 'cap_cost', label: 'Кап. по С/С' },
        { key: 'cap_retail', label: 'Кап. по розн.' },
        { key: 'gmroi', label: 'GMROI' },
        { key: 'gmroi_year', label: 'GMROI Year' },
        { key: 'ret_qty', label: 'Откз.+возвр.' },
        { key: 'sale_qty', label: 'Продаж шт' },
        { key: 'buyout_pct', label: '% выкупа' },
        { key: 'avg_profit_per_item', label: 'Ср. приб./шт' },
        { key: 'tax_total', label: 'Налоги' },
        { key: 'tax_base', label: 'Нал. база' },
        { key: 'profit', label: 'Прибыль', color: '#22c55e' },
        { key: 'roi', label: 'ROI %' },
        { key: 'revenue_share', label: 'Доля выр. %' },
        { key: 'margin_pct', label: 'Маржа %' },
        { key: 'adv_sum', label: 'Реклама', color: '#f59e0b' },
        { key: 'drr', label: 'ДРР %' },
        { key: 'drr_orders', label: 'ДРР заказы %' },
        { key: 'acceptance', label: 'Плат. приёмка' },
        { key: 'logistics', label: 'Логистика' },
        { key: 'storage', label: 'Хранение' },
        { key: 'abc_profit', label: 'ABC приб.' },
        { key: 'abc_revenue', label: 'ABC выр.' },
    ];

    const toggleSort = (key: string) => {
        if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
        else { setSortKey(key); setSortDir('desc'); }
    };

    const articles = React.useMemo(() => {
        const arr = [...rawArticles];
        arr.sort((a: any, b: any) => {
            let va = a[sortKey] ?? 0, vb = b[sortKey] ?? 0;
            if (typeof va === 'string') va = va.toLowerCase();
            if (typeof vb === 'string') vb = vb.toLowerCase();
            if (va < vb) return sortDir === 'asc' ? -1 : 1;
            if (va > vb) return sortDir === 'asc' ? 1 : -1;
            return 0;
        });
        return arr;
    }, [rawArticles, sortKey, sortDir]);

    // Категория строки: в группировке «По категориям» это sa_name, иначе поле subject.
    const catOf = (a: any) => (a.subject || (groupBy === 'subject' ? a.sa_name : '')) as string;

    // Список категорий для чекбоксов — из загруженных данных за период.
    const catOptions = React.useMemo(() => {
        const set = new Set<string>();
        for (const a of rawArticles) { const c = catOf(a); if (c) set.add(c); }
        return Array.from(set).sort((x, y) => x.localeCompare(y, 'ru'));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [rawArticles, groupBy]);

    // Фильтр по категориям применим только там, где строка = категория/имеет предмет.
    const catApplicable = groupBy === 'subject' || groupBy === 'article';

    // Строки после фильтра по выбранным категориям.
    const shownArticles = React.useMemo(() => {
        if (!selectedCats.length || !catApplicable) return articles;
        const sel = new Set(selectedCats);
        return articles.filter((a: any) => sel.has(catOf(a)));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [articles, selectedCats, groupBy, catApplicable]);

    // Итог по выбранным категориям (аддитивные поля суммируются корректно).
    const selTotals = React.useMemo(() => {
        if (!selectedCats.length || !catApplicable) return null;
        let net = 0, pay = 0, adv = 0;
        for (const a of shownArticles) {
            net += Number(a.net_payout) || 0; pay += Number(a.to_pay) || 0; adv += Number(a.adv_sum) || 0;
        }
        return { net, pay, adv, count: shownArticles.length };
    }, [shownArticles, selectedCats, catApplicable]);

    const abcGroups = React.useMemo(() => {
        if (groupBy !== 'abc') return [];
        const groups: { label: string; color: string; bg: string; articles: any[] }[] = [
            { label: 'A', color: '#22c55e', bg: 'rgba(34,197,94,0.08)', articles: [] },
            { label: 'B', color: '#f59e0b', bg: 'rgba(245,158,11,0.08)', articles: [] },
            { label: 'C', color: '#ef4444', bg: 'rgba(239,68,68,0.08)', articles: [] },
        ];
        for (const a of articles) {
            const cat = a.abc_revenue || 'C';
            const g = groups.find(g => g.label === cat);
            if (g) g.articles.push(a);
        }
        return groups.map(g => {
            const sum: Record<string, number> = {};
            const numKeys = bdrColumns.filter(c => c.key !== 'sa_name' && c.key !== 'brand' && c.key !== 'subject' && c.key !== 'nm_id' && c.key !== 'abc_profit' && c.key !== 'abc_revenue');
            for (const col of numKeys) {
                sum[col.key] = g.articles.reduce((acc, a) => acc + (parseFloat(a[col.key]) || 0), 0);
            }
            // Recalculate averages and percentages
            const saleQty = sum.sale_qty || 0;
            const realization = sum.realization || 0;
            const salesAmount = sum.sales_amount || 0;
            const costTotal = sum.cost_total || 0;
            const profit = sum.profit || 0;
            const advSum = sum.adv_sum || 0;
            const ordersSum = sum.orders_sum || 0;
            const addToCart = sum.add_to_cart || 0;
            const ordersCount = sum.orders_count || 0;
            sum.avg_sale_price = saleQty > 0 ? salesAmount / saleQty : 0;
            sum.avg_retail_price = saleQty > 0 ? realization / saleQty : 0;
            sum.avg_logistics = saleQty > 0 ? (sum.logistics || 0) / saleQty : 0;
            sum.avg_profit_per_item = saleQty > 0 ? profit / saleQty : 0;
            sum.margin_pct = realization > 0 ? profit / realization * 100 : 0;
            sum.roi = costTotal > 0 ? profit / costTotal * 100 : 0;
            sum.drr = salesAmount > 0 ? advSum / salesAmount * 100 : 0;
            sum.drr_orders = ordersSum > 0 ? advSum / ordersSum * 100 : 0;
            sum.conversion_to_order = addToCart > 0 ? ordersCount / addToCart * 100 : 0;
            const totalRealAll = rawArticles.reduce((acc: number, a: any) => acc + (parseFloat(a.realization) || 0), 0);
            sum.revenue_share = totalRealAll > 0 ? realization / totalRealAll * 100 : 0;
            // Buyout %: weighted average
            const totalSaleQtyGross = sum.sale_qty_gross || sum.sale_qty || 0;
            const totalRetQty = sum.ret_qty || 0;
            const totalQty = totalSaleQtyGross + totalRetQty;
            sum.buyout_pct = totalQty > 0 ? totalSaleQtyGross / totalQty * 100 : 0;
            return { ...g, summary: sum, count: g.articles.length };
        });
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [articles, groupBy, rawArticles]);

    const thStyle = (col: { key: string; color?: string; sticky?: boolean }): React.CSSProperties => ({
        cursor: 'pointer', userSelect: 'none' as const, whiteSpace: 'nowrap' as const,
        background: '#f9fafb', color: col.color || '#4b5563', borderBottom: '2px solid #e5e7eb',
        ...(col.sticky ? { position: 'sticky' as const, left: 0, zIndex: 22, borderRight: '1px solid #e5e7eb' } : {}),
    });
    const sortIcon = (key: string) => sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';
    const pct = (val: number, base: number) => base ? ((val / base) * 100).toFixed(2) + '%' : '—';

    const handleExcel = () => {
        if (!shownArticles.length) return;
        const rows = shownArticles.map((a: any, i: number) => {
            const row: Record<string, any> = { '№': i + 1 };
            bdrColumns.forEach(col => { row[col.label] = a[col.key] ?? ''; });
            return row;
        });
        exportToExcel(rows, `BDR_${dateFrom}_${dateTo}`);
    };

    const syncTime = syncStatus?.last_sync
        ? new Date(syncStatus.last_sync).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
        : null;
    // Real coverage: trust backend's is_period_covered when date range is known.
    // Fallback to legacy total_rows>0 only if backend didn't return the new field.
    const isAllSynced = syncStatus?.is_period_covered === true
        || (syncStatus?.is_period_covered == null && syncStatus?.total_rows > 0);

    return (
        <div>
            {/* Sync Status Badge */}
            <div className="glass-card" style={{ padding: '10px 16px', marginBottom: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 13 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span>🔄 Последняя синхронизация: {syncTime || 'нет данных'}</span>
                    {syncStatus?.last_status === 'OK' && <span style={{ color: '#22c55e' }}>● авто</span>}
                    {syncStatus?.last_status === 'ERROR' && <span style={{ color: '#f43f5e' }}>● ошибка</span>}
                    {isAllSynced && <span style={{ color: '#22c55e' }}>✅ Все дни синхронизированы</span>}
                    {!isAllSynced && syncTime && (
                        <span style={{ color: '#eab308' }}>
                            ⚠️ Нет данных за период
                            {syncStatus?.coverage_to && ` (есть до ${new Date(syncStatus.coverage_to).toLocaleDateString('ru-RU')})`}
                        </span>
                    )}
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    {taxInfo?.tax_regime && (
                        <span className="badge badge-info" style={{ fontSize: 11 }}>
                            {taxInfo.tax_regime === 'usn_income' ? 'УСН Доходы' : 'УСН Д-Р'}
                            {taxInfo.usn_rate > 0 && ` ${taxInfo.usn_rate}%`}
                            {taxInfo.nds_rate > 0 && ` + НДС ${taxInfo.nds_rate}%`}
                        </span>
                    )}
                    <button className="btn btn-secondary btn-sm" onClick={handleSync} disabled={syncing} style={{ fontSize: 12, padding: '4px 10px' }}>
                        {syncing ? '⏳ Синхр...' : '🔄 Синхронизировать'}
                    </button>
                    <button className="btn btn-secondary btn-sm" onClick={handleExcel} disabled={!articles.length} style={{ fontSize: 12, padding: '4px 10px' }}>📥 Excel</button>
                </div>
            </div>

            {/* Filters */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
                    <div><label style={{ fontSize: 12, opacity: 0.7, display: 'block', marginBottom: 4 }}>С</label><input type="date" className="input" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={{ width: 150 }} /></div>
                    <div><label style={{ fontSize: 12, opacity: 0.7, display: 'block', marginBottom: 4 }}>По</label><input type="date" className="input" value={dateTo} onChange={e => setDateTo(e.target.value)} style={{ width: 150 }} /></div>
                    <div><label style={{ fontSize: 12, opacity: 0.7, display: 'block', marginBottom: 4 }}>Бренд</label>
                        <select className="input" value={brand} onChange={e => setBrand(e.target.value)} style={{ width: 170 }}>
                            <option value="">Все бренды</option>
                            {brands.map((b: string) => <option key={b} value={b}>{b}</option>)}
                        </select>
                    </div>
                    {catApplicable && <div ref={catRef} style={{ position: 'relative' }}>
                        <label style={{ fontSize: 12, opacity: 0.7, display: 'block', marginBottom: 4 }}>Категории</label>
                        <button type="button" className="input" onClick={() => setCatOpen(o => !o)}
                            style={{ width: 190, textAlign: 'left', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6 }}>
                            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {selectedCats.length ? `Выбрано: ${selectedCats.length}` : 'Все категории'}
                            </span>
                            <span style={{ opacity: 0.5 }}>▾</span>
                        </button>
                        {catOpen && (
                            <div style={{ position: 'absolute', zIndex: 50, top: '100%', left: 0, marginTop: 4, width: 240, maxHeight: 320, overflowY: 'auto', background: '#fff', border: '1px solid #e5e7eb', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', padding: 6 }}>
                                <div style={{ display: 'flex', gap: 8, padding: '4px 8px 8px', borderBottom: '1px solid #f3f4f6', marginBottom: 4 }}>
                                    <button type="button" onClick={() => persistCats(catOptions)} style={{ fontSize: 12, color: '#6366f1', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 600 }}>Все</button>
                                    <button type="button" onClick={() => persistCats([])} style={{ fontSize: 12, color: '#6b7280', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 600 }}>Сбросить</button>
                                </div>
                                {catOptions.length === 0 && <div style={{ padding: '6px 8px', fontSize: 12, color: '#9ca3af' }}>Загрузите отчёт</div>}
                                {catOptions.map(c => (
                                    <label key={c} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 8px', fontSize: 13, cursor: 'pointer', borderRadius: 6 }}>
                                        <input type="checkbox" checked={selectedCats.includes(c)}
                                            onChange={() => persistCats(selectedCats.includes(c) ? selectedCats.filter(x => x !== c) : [...selectedCats, c])} />
                                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c}</span>
                                    </label>
                                ))}
                            </div>
                        )}
                    </div>}
                    <div><label style={{ fontSize: 12, opacity: 0.7, display: 'block', marginBottom: 4 }}>Артикул</label><input className="input" placeholder="Поиск..." value={articleSearch} onChange={e => setArticleSearch(e.target.value)} style={{ width: 160 }} /></div>
                    <div>
                        <label style={{ fontSize: 12, opacity: 0.7, display: 'block', marginBottom: 4 }}>Группировка</label>
                        <div style={{ display: 'flex', borderRadius: 8, overflow: 'hidden', border: '1px solid #e5e7eb', background: '#f3f4f6', height: 38 }}>
                            {([['article', 'По артикулам'], ['brand', 'По брендам'], ['subject', 'По категориям'], ['tag', 'По ярлыкам'], ['imt', 'По склейкам'], ['abc', 'ABC анализ']] as const).map(([val, lbl]) => (
                                <button key={val} onClick={() => { setGroupBy(val); try { localStorage.setItem(prefKey('group_by'), val); } catch { /* noop */ } }}
                                    style={{ padding: '0 14px', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer', background: groupBy === val ? '#6366f1' : 'transparent', color: groupBy === val ? '#fff' : '#6b7280', transition: 'all 0.2s ease', whiteSpace: 'nowrap' }}>
                                    {lbl}
                                </button>
                            ))}
                        </div>
                    </div>
                    <div>
                        <label style={{ fontSize: 12, opacity: 0.7, display: 'block', marginBottom: 4 }} title="«По дате продажи» — учёт по дате реализации (БДР accrual). «По отчётам ВБ» — по отчётному периоду, сходится с «Итого к оплате» в кабинете ВБ.">Свод</label>
                        <div style={{ display: 'flex', borderRadius: 8, overflow: 'hidden', border: '1px solid #e5e7eb', background: '#f3f4f6', height: 38 }}>
                            {([['sale', 'По дате продажи'], ['report', 'По отчётам ВБ']] as const).map(([val, lbl]) => (
                                <button key={val} onClick={() => { setPeriodMode(val); try { localStorage.setItem(prefKey('period_mode'), val); } catch { /* noop */ } }}
                                    style={{ padding: '0 14px', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer', background: periodMode === val ? '#6366f1' : 'transparent', color: periodMode === val ? '#fff' : '#6b7280', transition: 'all 0.2s ease', whiteSpace: 'nowrap' }}>
                                    {lbl}
                                </button>
                            ))}
                        </div>
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={loadData} disabled={loading} style={{ height: 38 }}>{loading ? '⏳ Загрузка...' : '📊 Загрузить'}</button>
                    {articles.length > 0 && <button className="btn btn-secondary btn-sm" onClick={handleExcel} style={{ height: 38 }}>📥 Excel</button>}
                </div>
            </div>

            {error && <div className="glass-card" style={{ padding: 16, color: '#ff6b6b' }}>⚠️ {error}</div>}
            {loading && <div className="glass-card" style={{ padding: 40, textAlign: 'center' }}><div style={{ fontSize: 32, marginBottom: 12 }}>⏳</div><div style={{ opacity: 0.7 }}>Загрузка данных...</div></div>}

            {s && !loading && (
                <>
                    {/* KPI Cards — hide for ABC view */}
                    {groupBy !== 'abc' && (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12, marginBottom: 16 }}>
                        <KpiCard label="Итого к оплате" value={formatNumber(s.to_pay)} sub="₽" />
                        <KpiCard label="Чистая выплата" value={formatNumber(s.net_payout)} sub="₽ (− реклама)" color="#0ea5e9" />
                        <KpiCard label="Реализация" value={formatNumber(s.realization)} sub="₽" />
                        <KpiCard label="Продажи" value={formatNumber(s.sales_amount)} sub={`₽ / ${formatNumber(s.sale_qty)} шт`} />
                        <KpiCard label="Возвраты" value={formatNumber(s.returns_amount)} sub={`₽ / ${formatNumber(s.ret_qty)} шт`} />
                        <KpiCard label="Комиссия" value={formatNumber(s.commission)} sub={pct(s.commission, s.realization)} color={s.commission < 0 ? '#ff6b6b' : undefined} />
                        <KpiCard label="Логистика" value={formatNumber(s.logistics)} sub={pct(s.logistics, s.realization)} />
                        <KpiCard label="Хранение" value={formatNumber(s.storage)} sub={pct(s.storage, s.realization)} />
                        <KpiCard label="Реклама" value={formatNumber(s.adv_sum || 0)} sub={pct(s.adv_sum || 0, s.realization)} color="#f59e0b" />
                        <KpiCard label="Прочие удержания" value={formatNumber(s.other_deduction || 0)} sub={pct(s.other_deduction || 0, s.realization)} />
                        <KpiCard label="Списание ВБ" value={formatNumber(s.wb_deductions || 0)} sub="реклама + кредит" color="#7c3aed" />
                        <KpiCard label="Себестоимость" value={formatNumber(s.cost_total || 0)} sub={pct(s.cost_total || 0, s.realization)} color="#8b5cf6" />
                        <KpiCard label="Налог" value={formatNumber(s.tax_total || 0)} sub={`НДС ${formatNumber(s.tax_nds || 0)} + УСН ${formatNumber(s.tax_usn || 0)}`} color="#ef4444" />
                        <KpiCard label="Чистая прибыль" value={formatNumber(s.profit || 0)} sub={pct(s.profit || 0, s.realization)} color={s.profit >= 0 ? '#22c55e' : '#ff6b6b'} />
                        <KpiCard label="% выкупа" value={s.buyout_pct?.toFixed(2) + '%'} sub="" />
                    </div>
                    )}

                    {/* Итог по выбранным категориям */}
                    {selTotals && (
                        <div className="glass-card" style={{ padding: '16px 20px', marginBottom: 16, border: '1px solid #bae6fd', background: 'linear-gradient(90deg, rgba(14,165,233,0.06), rgba(14,165,233,0.02))' }}>
                            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '4px 20px' }}>
                                <span style={{ fontSize: 13, fontWeight: 600, color: '#0369a1' }}>✅ Выбрано категорий: {selectedCats.length} · строк: {selTotals.count}</span>
                                <span style={{ marginLeft: 'auto', fontSize: 13, opacity: 0.7 }}>К оплате: <b>{formatNumber(selTotals.pay)} ₽</b></span>
                                <span style={{ fontSize: 13, opacity: 0.7 }}>Реклама: <b>{formatNumber(selTotals.adv)} ₽</b></span>
                                <span style={{ fontSize: 15, color: '#0ea5e9' }}>Чистая выплата: <b style={{ fontSize: 20 }}>{formatNumber(selTotals.net)} ₽</b></span>
                            </div>
                        </div>
                    )}

                    {/* Top-10 — hide for ABC view */}
                    {groupBy !== 'abc' && <TopProductsWidget articles={rawArticles} />}

                    {/* No cost warning */}
                    {(() => { const n = articles.filter((a: any) => !a.cost_price || a.cost_price === 0).length; return n > 0 ? (
                        <div style={{ padding: '10px 16px', marginBottom: 12, background: 'rgba(245,158,11,0.15)', border: '1px solid rgba(245,158,11,0.4)', borderRadius: 8, color: '#f59e0b', fontSize: 13 }}>
                            ⚠️ Товары без себестоимости — {n} шт
                        </div>
                    ) : null; })()}

                    {/* Articles Table */}
                    {groupBy === 'abc' && abcGroups.length > 0 ? (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                            {/* ABC Summary row */}
                            <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                                <div style={{ overflow: 'auto' }}>
                                    {/* TODO: migrate to TanStackDataTable */}
                                    <table className="data-table" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                                        <thead>
                                            <tr>
                                                {bdrColumns.map(col => (
                                                    <th key={col.key} style={thStyle(col)} onClick={() => toggleSort(col.key)}>{col.label}{sortIcon(col.key)}</th>
                                                ))}
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {/* Total summary row */}
                                            {(() => { const r = s; return (
                                            <tr style={{ fontWeight: 700, background: '#eef2ff', color: '#111827' }}>
                                                <td style={{ position: 'sticky', left: 0, background: '#e0e7ff', zIndex: 11, borderRight: '1px solid #c7d2fe' }}>Итого:</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.to_pay)}</td>
                                                <td style={{ textAlign: 'right', color: '#0ea5e9' }}>{formatNumber(r.net_payout)}</td>
                                                <td>-</td><td>-</td><td>-</td>
                                                <td style={{ textAlign: 'right' }}>--</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.other_deduction || 0)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.avg_retail_price)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.avg_sale_price)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.realization)}</td>
                                                <td style={{ textAlign: 'right' }}>{r.turnover_days || '--'}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.sales_amount)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.ppvz_for_pay)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.returns_amount)}</td>
                                                <td style={{ textAlign: 'right', color: '#8b5cf6' }}>{formatNumber(r.cost_total || 0)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.penalties)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.orders_count || 0)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.orders_sum || 0)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.commission)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.total_wb_reward)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.compensation)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.avg_logistics)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.cap_cost || 0)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.cap_retail || 0)}</td>
                                                <td style={{ textAlign: 'right' }}>{r.gmroi || '--'}</td>
                                                <td style={{ textAlign: 'right' }}>{r.gmroi_year || '--'}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.ret_qty)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.sale_qty)}</td>
                                                <td style={{ textAlign: 'right' }}>{r.buyout_pct?.toFixed(2)}%</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.avg_profit_per_item || 0)}</td>
                                                <td style={{ textAlign: 'right', color: '#ef4444' }}>{formatNumber(r.tax_total || 0)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.tax_base || r.sales_amount || 0)}</td>
                                                <td style={{ textAlign: 'right', color: r.profit >= 0 ? '#22c55e' : '#ff6b6b', fontWeight: 700 }}>{formatNumber(r.profit || 0)}</td>
                                                <td style={{ textAlign: 'right' }}>{r.roi?.toFixed(2) || '--'}%</td>
                                                <td style={{ textAlign: 'right' }}>100%</td>
                                                <td style={{ textAlign: 'right' }}>{r.margin_pct?.toFixed(2) || '--'}%</td>
                                                <td style={{ textAlign: 'right', color: '#f59e0b' }}>{formatNumber(r.adv_sum || 0)}</td>
                                                <td style={{ textAlign: 'right' }}>{r.drr?.toFixed(2) || '--'}%</td>
                                                <td style={{ textAlign: 'right' }}>{r.drr_orders?.toFixed(2) || '--'}%</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.acceptance || 0)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.logistics)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(r.storage)}</td>
                                                <td>-</td><td>-</td>
                                            </tr>
                                            ); })()}
                                        </tbody>
                                    </table>
                                </div>
                            </div>

                            {/* ABC Groups */}
                            {abcGroups.map(g => {
                                const isExpanded = expandedAbc[g.label] || false;
                                const gs = g.summary;
                                return (
                                    <div key={g.label} className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                                        {/* Group header */}
                                        <div
                                            onClick={() => setExpandedAbc(prev => ({ ...prev, [g.label]: !prev[g.label] }))}
                                            style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 20px', cursor: 'pointer', background: g.bg, borderBottom: isExpanded ? '1px solid #e5e7eb' : 'none', transition: 'all 0.2s ease' }}
                                        >
                                            <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32, borderRadius: 8, background: g.color, color: '#fff', fontWeight: 800, fontSize: 16 }}>{g.label}</span>
                                            <div>
                                                <div style={{ fontWeight: 700, fontSize: 14, color: '#111827' }}>
                                                    Категория {g.label} — {g.count} {g.count === 1 ? 'артикул' : g.count < 5 ? 'артикула' : 'артикулов'}
                                                </div>
                                                <div style={{ fontSize: 12, opacity: 0.7, marginTop: 2 }}>
                                                    Выручка: {formatNumber(gs.realization || 0)} | Прибыль: <span style={{ color: (gs.profit || 0) >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>{formatNumber(gs.profit || 0)}</span> | Реклама: {formatNumber(gs.adv_sum || 0)} | Маржа: {gs.margin_pct?.toFixed(1) || 0}% | Кап. розн.: {formatNumber(gs.cap_retail || 0)} | Кап. с/с: {formatNumber(gs.cap_cost || 0)}
                                                </div>
                                            </div>
                                            <div style={{ marginLeft: 'auto', fontSize: 14, opacity: 0.5 }}>{isExpanded ? '▲' : '▼'}</div>
                                        </div>
                                        {/* Expanded table */}
                                        {isExpanded && (
                                            <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 400px)' }}>
                                                {/* TODO: migrate to TanStackDataTable */}
                                                <table className="data-table" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                                                    <thead>
                                                        <tr>
                                                            {bdrColumns.map(col => (
                                                                <th key={col.key} style={thStyle(col)} onClick={() => toggleSort(col.key)}>{col.label}{sortIcon(col.key)}</th>
                                                            ))}
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {/* Group summary row */}
                                                        <tr style={{ fontWeight: 700, background: g.bg, color: '#111827' }}>
                                                            <td style={{ position: 'sticky', left: 0, background: g.bg, zIndex: 11, borderRight: '1px solid #e5e7eb' }}>
                                                                <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 4, background: g.color, color: '#fff', fontWeight: 700, fontSize: 11, marginRight: 6 }}>{g.label}</span>
                                                                Итого
                                                            </td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.to_pay || 0)}</td>
                                                            <td style={{ textAlign: 'right', color: '#0ea5e9' }}>{formatNumber(gs.net_payout || 0)}</td>
                                                            <td>-</td><td>-</td><td>-</td>
                                                            <td style={{ textAlign: 'right' }}>--</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.other_deduction || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.avg_retail_price || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.avg_sale_price || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.realization || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{gs.turnover_days?.toFixed(0) || '--'}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.sales_amount || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.ppvz_for_pay || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.returns_amount || 0)}</td>
                                                            <td style={{ textAlign: 'right', color: '#8b5cf6' }}>{formatNumber(gs.cost_total || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.penalties || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.orders_count || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.orders_sum || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.commission || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.total_wb_reward || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.compensation || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.avg_logistics || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.cap_cost || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.cap_retail || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{gs.gmroi?.toFixed(0) || '--'}</td>
                                                            <td style={{ textAlign: 'right' }}>{gs.gmroi_year?.toFixed(0) || '--'}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.ret_qty || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.sale_qty || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{gs.buyout_pct?.toFixed(2) || '--'}%</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.avg_profit_per_item || 0)}</td>
                                                            <td style={{ textAlign: 'right', color: '#ef4444' }}>{formatNumber(gs.tax_total || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.tax_base || 0)}</td>
                                                            <td style={{ textAlign: 'right', color: (gs.profit || 0) >= 0 ? '#22c55e' : '#ff6b6b', fontWeight: 700 }}>{formatNumber(gs.profit || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{gs.roi?.toFixed(2) || '--'}%</td>
                                                            <td style={{ textAlign: 'right' }}>{gs.revenue_share?.toFixed(2) || '--'}%</td>
                                                            <td style={{ textAlign: 'right' }}>{gs.margin_pct?.toFixed(2) || '--'}%</td>
                                                            <td style={{ textAlign: 'right', color: '#f59e0b' }}>{formatNumber(gs.adv_sum || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{gs.drr?.toFixed(2) || '--'}%</td>
                                                            <td style={{ textAlign: 'right' }}>{gs.drr_orders?.toFixed(2) || '--'}%</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.acceptance || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.logistics || 0)}</td>
                                                            <td style={{ textAlign: 'right' }}>{formatNumber(gs.storage || 0)}</td>
                                                            <td>-</td><td>-</td>
                                                        </tr>
                                                        {/* Individual articles */}
                                                        {g.articles.map((a: any, i: number) => {
                                                            const rowBg = i % 2 === 0 ? '#ffffff' : '#f9fafb';
                                                            return (
                                                            <tr key={a.sa_name || i} style={{ background: rowBg, color: '#111827' }}>
                                                                <td style={{ position: 'sticky', left: 0, background: rowBg, zIndex: 11, fontWeight: 500, borderRight: '1px solid #e5e7eb' }}>{a.sa_name || '--'}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.to_pay)}</td>
                                                                <td style={{ textAlign: 'right', color: '#0ea5e9' }}>{formatNumber(a.net_payout)}</td>
                                                                <td>{a.brand || '--'}</td>
                                                                <td>{a.subject || '--'}</td>
                                                                <td>{a.nm_id || '--'}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.cost_price || 0)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.other_deduction || 0)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_retail_price)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_sale_price)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.realization)}</td>
                                                                <td style={{ textAlign: 'right' }}>{a.turnover_days || '--'}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.sales_amount)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.ppvz_for_pay)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.returns_amount)}</td>
                                                                <td style={{ textAlign: 'right', color: '#8b5cf6' }}>{formatNumber(a.cost_total || 0)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.penalties)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.orders_count || 0)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.orders_sum || 0)}</td>
                                                                <td style={{ textAlign: 'right', color: a.commission < 0 ? '#ff6b6b' : undefined }}>{formatNumber(a.commission)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.total_wb_reward)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.compensation)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_logistics)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.cap_cost || 0)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.cap_retail || 0)}</td>
                                                                <td style={{ textAlign: 'right' }}>{a.gmroi || '--'}</td>
                                                                <td style={{ textAlign: 'right' }}>{a.gmroi_year || '--'}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.ret_qty)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.sale_qty)}</td>
                                                                <td style={{ textAlign: 'right' }}>{a.buyout_pct?.toFixed(2) || '--'}%</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_profit_per_item || 0)}</td>
                                                                <td style={{ textAlign: 'right', color: '#ef4444' }}>{formatNumber(a.tax_total || 0)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.tax_base || 0)}</td>
                                                                <td style={{ textAlign: 'right', color: a.profit >= 0 ? '#22c55e' : '#ff6b6b', fontWeight: 600 }}>{formatNumber(a.profit || 0)}</td>
                                                                <td style={{ textAlign: 'right' }}>{a.roi?.toFixed(2) || '--'}%</td>
                                                                <td style={{ textAlign: 'right' }}>{a.revenue_share?.toFixed(2) || '--'}%</td>
                                                                <td style={{ textAlign: 'right' }}>{a.margin_pct?.toFixed(2) || '--'}%</td>
                                                                <td style={{ textAlign: 'right', color: '#f59e0b' }}>{formatNumber(a.adv_sum || 0)}</td>
                                                                <td style={{ textAlign: 'right' }}>{a.drr?.toFixed(2) || '--'}%</td>
                                                                <td style={{ textAlign: 'right' }}>{a.drr_orders?.toFixed(2) || '--'}%</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.acceptance || 0)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.logistics)}</td>
                                                                <td style={{ textAlign: 'right' }}>{formatNumber(a.storage)}</td>
                                                                <td style={{ textAlign: 'center' }}><span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 4, background: g.color, color: '#fff', fontWeight: 700, fontSize: 11 }}>{a.abc_profit}</span></td>
                                                                <td style={{ textAlign: 'center' }}><span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 4, background: g.color, color: '#fff', fontWeight: 700, fontSize: 11 }}>{a.abc_revenue}</span></td>
                                                            </tr>
                                                            );
                                                        })}
                                                    </tbody>
                                                </table>
                                            </div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    ) : (
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                        <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 280px)' }}>
                            {/* TODO: migrate to TanStackDataTable */}
                            <table className="data-table" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                                <thead>
                                    <tr>
                                        {bdrColumns.map(col => (
                                            <th key={col.key} style={thStyle(col)} onClick={() => toggleSort(col.key)}>{col.label}{sortIcon(col.key)}</th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {/* Summary row */}
                                    {(() => { const r = s; return (
                                    <tr style={{ fontWeight: 700, background: '#eef2ff', color: '#111827' }}>
                                        <td style={{ position: 'sticky', left: 0, background: '#e0e7ff', zIndex: 11, borderRight: '1px solid #c7d2fe' }}>{selTotals ? 'Итого (весь период):' : 'Итого:'}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.to_pay)}</td>
                                        <td style={{ textAlign: 'right', color: '#0ea5e9' }}>{formatNumber(r.net_payout)}</td>
                                        {groupBy !== 'brand' && groupBy !== 'tag' && groupBy !== 'imt' && <td>-</td>}
                                        {groupBy !== 'subject' && groupBy !== 'tag' && groupBy !== 'imt' && <td>-</td>}
                                        {groupBy === 'article' && <td>-</td>}
                                        <td style={{ textAlign: 'right' }}>—</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.other_deduction || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.avg_retail_price)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.avg_sale_price)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.realization)}</td>
                                        <td style={{ textAlign: 'right' }}>{r.turnover_days || '—'}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.sales_amount)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.ppvz_for_pay)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.returns_amount)}</td>
                                        <td style={{ textAlign: 'right', color: '#8b5cf6' }}>{formatNumber(r.cost_total || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.penalties)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.orders_count || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.orders_sum || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.commission)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.total_wb_reward)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.compensation)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.avg_logistics)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.cap_cost || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.cap_retail || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{r.gmroi || '—'}</td>
                                        <td style={{ textAlign: 'right' }}>{r.gmroi_year || '—'}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.ret_qty)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.sale_qty)}</td>
                                        <td style={{ textAlign: 'right' }}>{r.buyout_pct?.toFixed(2)}%</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.avg_profit_per_item || 0)}</td>
                                        <td style={{ textAlign: 'right', color: '#ef4444' }}>{formatNumber(r.tax_total || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.tax_base || r.sales_amount || 0)}</td>
                                        <td style={{ textAlign: 'right', color: r.profit >= 0 ? '#22c55e' : '#ff6b6b', fontWeight: 700 }}>{formatNumber(r.profit || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{r.roi?.toFixed(2) || '—'}%</td>
                                        <td style={{ textAlign: 'right' }}>100%</td>
                                        <td style={{ textAlign: 'right' }}>{r.margin_pct?.toFixed(2) || '—'}%</td>
                                        <td style={{ textAlign: 'right', color: '#f59e0b' }}>{formatNumber(r.adv_sum || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{r.drr?.toFixed(2) || '—'}%</td>
                                        <td style={{ textAlign: 'right' }}>{r.drr_orders?.toFixed(2) || '—'}%</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.acceptance || 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.logistics)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(r.storage)}</td>
                                        <td>-</td><td>-</td>
                                    </tr>
                                    ); })()}
                                    {shownArticles.map((a: any, i: number) => {
                                        const rowBg = i % 2 === 0 ? '#ffffff' : '#f9fafb';
                                        return (
                                        <tr key={a.sa_name || i} style={{ background: rowBg, color: '#111827' }}>
                                            <td style={{ position: 'sticky', left: 0, background: rowBg, zIndex: 11, fontWeight: 500, borderRight: '1px solid #e5e7eb' }}>{a.sa_name || '—'}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.to_pay)}</td>
                                            <td style={{ textAlign: 'right', color: '#0ea5e9' }}>{formatNumber(a.net_payout)}</td>
                                            {groupBy !== 'brand' && groupBy !== 'tag' && groupBy !== 'imt' && <td>{a.brand || '—'}</td>}
                                            {groupBy !== 'subject' && groupBy !== 'tag' && groupBy !== 'imt' && <td>{a.subject || '—'}</td>}
                                            {groupBy === 'article' && <td>{a.nm_id || '—'}</td>}
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.cost_price || 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.other_deduction || 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_retail_price)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_sale_price)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.realization)}</td>
                                            <td style={{ textAlign: 'right' }}>{a.turnover_days || '—'}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.sales_amount)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.ppvz_for_pay)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.returns_amount)}</td>
                                            <td style={{ textAlign: 'right', color: '#8b5cf6' }}>{formatNumber(a.cost_total || 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.penalties)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.orders_count || 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.orders_sum || 0)}</td>
                                            <td style={{ textAlign: 'right', color: a.commission < 0 ? '#ff6b6b' : undefined }}>{formatNumber(a.commission)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.total_wb_reward)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.compensation)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_logistics)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.cap_cost || 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.cap_retail || 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{a.gmroi || '—'}</td>
                                            <td style={{ textAlign: 'right' }}>{a.gmroi_year || '—'}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.ret_qty)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.sale_qty)}</td>
                                            <td style={{ textAlign: 'right' }}>{a.buyout_pct?.toFixed(2) || '—'}%</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.avg_profit_per_item || 0)}</td>
                                            <td style={{ textAlign: 'right', color: '#ef4444' }}>{formatNumber(a.tax_total || 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.tax_base || 0)}</td>
                                            <td style={{ textAlign: 'right', color: a.profit >= 0 ? '#22c55e' : '#ff6b6b', fontWeight: 600 }}>{formatNumber(a.profit || 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{a.roi?.toFixed(2) || '—'}%</td>
                                            <td style={{ textAlign: 'right' }}>{a.revenue_share?.toFixed(2) || '—'}%</td>
                                            <td style={{ textAlign: 'right' }}>{a.margin_pct?.toFixed(2) || '—'}%</td>
                                            <td style={{ textAlign: 'right', color: '#f59e0b' }}>{formatNumber(a.adv_sum || 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{a.drr?.toFixed(2) || '—'}%</td>
                                            <td style={{ textAlign: 'right' }}>{a.drr_orders?.toFixed(2) || '—'}%</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.acceptance || 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.logistics)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(a.storage)}</td>
                                            <td style={{ textAlign: 'center' }}><span className={`badge-${a.abc_profit === 'A' ? 'green' : a.abc_profit === 'B' ? 'yellow' : 'red'}`}>{a.abc_profit}</span></td>
                                            <td style={{ textAlign: 'center' }}><span className={`badge-${a.abc_revenue === 'A' ? 'green' : a.abc_revenue === 'B' ? 'yellow' : 'red'}`}>{a.abc_revenue}</span></td>
                                        </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                            {shownArticles.length === 0 && <div className="empty-state" style={{ padding: 20 }}>{selectedCats.length ? 'Нет строк по выбранным категориям' : 'Нет данных за выбранный период'}</div>}
                        </div>
                    </div>
                    )}

                    {/* Tax Summary */}
                    {s.tax_total > 0 && (
                        <div className="glass-card" style={{ padding: 16, marginTop: 12 }}>
                            <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 14 }}>📋 Налоговая нагрузка</div>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8, fontSize: 13 }}>
                                <div>Доходы (Продажи): <b>{formatNumber(s.sales_amount)} ₽</b></div>
                                {s.tax_nds > 0 && <div>Сумма НДС: <b style={{ color: '#ef4444' }}>{formatNumber(s.tax_nds)} ₽</b></div>}
                                <div>Сумма УСН: <b style={{ color: '#ef4444' }}>{formatNumber(s.tax_usn)} ₽</b></div>
                                <div>Итого налог: <b style={{ color: '#ef4444' }}>{formatNumber(s.tax_total)} ₽</b></div>
                                {s.expenses_total > 0 && <div>Расходы (для базы): <b>{formatNumber(s.expenses_total)} ₽</b></div>}
                            </div>
                        </div>
                    )}

                    <div style={{ marginTop: 8, opacity: 0.5, fontSize: 12 }}>
                        Строк в БД: {data?.total_rows || 0} · Показано: {shownArticles.length}{selectedCats.length ? ` из ${articles.length}` : ''}
                    </div>
                </>
            )}

            {!data && !loading && !error && (
                <div className="glass-card" style={{ padding: 40, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>📈</div>
                    <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>БДР — Бюджет Доходов и Расходов</div>
                    <div style={{ opacity: 0.7 }}>Выберите период и нажмите «Загрузить» для получения данных</div>
                    {!isAllSynced && (
                        <div style={{ marginTop: 12, opacity: 0.6, fontSize: 13 }}>💡 Данные загрузятся автоматически при первом запросе</div>
                    )}
                </div>
            )}
        </div>
    );
}
