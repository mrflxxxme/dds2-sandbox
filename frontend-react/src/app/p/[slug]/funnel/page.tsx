'use client';
import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';

const fmt = (n: number) => n?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '0';
const fmtPct = (n: number) => (n || 0).toFixed(2) + '%';

function getLast30Days() {
    const today = new Date();
    const to = new Date(today);
    to.setDate(to.getDate() - 1); // Yesterday
    const from = new Date(to);
    from.setDate(from.getDate() - 29); // 30 days back
    const f = (d: Date) => d.toISOString().slice(0, 10);
    return { from: f(from), to: f(to) };
}

export default function FunnelPage() {
    const defaultDates = getLast30Days();
    const [tab, setTab] = useState<'funnel' | 'costs'>('funnel');
    const [data, setData] = useState<any[]>([]);
    const [detailed, setDetailed] = useState(false);
    const [summary, setSummary] = useState<any>(null);
    const [filters, setFilters] = useState<any>({ brands: [], subjects: [] });
    const [loading, setLoading] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [taxRate, setTaxRate] = useState(6);
    const [initDone, setInitDone] = useState(false);

    // Filters — default to last 30 days
    const [dateFrom, setDateFrom] = useState(defaultDates.from);
    const [dateTo, setDateTo] = useState(defaultDates.to);
    const [brand, setBrand] = useState('');
    const [subject, setSubject] = useState('');
    const [search, setSearch] = useState('');

    // Sync dates — default to last 30 days
    const [syncFrom, setSyncFrom] = useState(defaultDates.from);
    const [syncTo, setSyncTo] = useState(defaultDates.to);

    // Costs tab
    const [costs, setCosts] = useState<any>({ overrides: [], missing: [] });
    const [editCost, setEditCost] = useState<{ nm_id: number; cost_price: string } | null>(null);

    const loadFilters = useCallback(async () => {
        try {
            const f = await api.getFunnelFilters();
            setFilters(f);
        } catch { }
    }, []);

    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const [res, sum, tax] = await Promise.all([
                api.getFunnelData({ date_from: dateFrom, date_to: dateTo, brand, vendor_code: search, subject }),
                api.getFunnelSummary(dateFrom, dateTo),
                api.getFunnelTax(),
            ]);
            setData(res.data || []);
            setDetailed(res.detailed || false);
            setSummary(sum);
            setTaxRate(tax.tax_rate || 6);
            return res.data || [];
        } catch (e: any) {
            console.error(e);
            return [];
        } finally {
            setLoading(false);
        }
    }, [dateFrom, dateTo, brand, subject, search]);

    const loadCosts = useCallback(async () => {
        try {
            const c = await api.getFunnelCosts();
            setCosts(c);
        } catch { }
    }, []);

    // Auto-sync on first load if no data
    useEffect(() => {
        (async () => {
            await loadFilters();
            const rows = await loadData();
            if (rows.length === 0 && !initDone) {
                // Auto-sync last 30 days
                setSyncing(true);
                try {
                    await api.syncFunnel(defaultDates.from, defaultDates.to);
                    await loadData();
                    await loadFilters();
                } catch (e: any) {
                    console.error('Auto-sync failed:', e);
                }
                setSyncing(false);
            }
            setInitDone(true);
        })();
    }, []);

    useEffect(() => { if (initDone && (dateFrom || dateTo)) loadData(); }, [dateFrom, dateTo, brand, subject, search]);
    useEffect(() => { if (tab === 'costs') loadCosts(); }, [tab]);

    const handleSync = async () => {
        if (!syncFrom || !syncTo) return alert('Укажите даты синхронизации');
        setSyncing(true);
        try {
            const res = await api.syncFunnel(syncFrom, syncTo);
            alert(`Синхронизировано: ${res.rows} строк за ${res.days} дней`);
            loadData();
            loadFilters();
        } catch (e: any) {
            alert('Ошибка: ' + (e.message || e));
        }
        setSyncing(false);
    };

    const handleSaveCost = async () => {
        if (!editCost) return;
        try {
            await api.setFunnelCost(editCost.nm_id, parseFloat(editCost.cost_price));
            setEditCost(null);
            loadCosts();
            loadData();
        } catch (e: any) { alert(e.message); }
    };

    const handleSaveTax = async () => {
        try {
            await api.setFunnelTax(taxRate);
            loadData();
        } catch (e: any) { alert(e.message); }
    };

    return (
        <div>
            <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 16 }}>📊 Воронка продаж</h1>

            {/* Tabs */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                <button className={`tab-btn ${tab === 'funnel' ? 'active' : ''}`} onClick={() => setTab('funnel')}>Воронка</button>
                <button className={`tab-btn ${tab === 'costs' ? 'active' : ''}`} onClick={() => setTab('costs')}>Себестоимость</button>
            </div>

            {tab === 'funnel' && (
                <>
                    {/* Sync panel */}
                    <div className="glass-card" style={{ marginBottom: 16, padding: 16 }}>
                        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                            <span style={{ fontWeight: 500, fontSize: 14 }}>🔄 Синхронизация WB:</span>
                            <input type="date" value={syncFrom} onChange={e => setSyncFrom(e.target.value)}
                                style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)' }} />
                            <span>—</span>
                            <input type="date" value={syncTo} onChange={e => setSyncTo(e.target.value)}
                                style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)' }} />
                            <button className="btn-primary" onClick={handleSync} disabled={syncing}
                                style={{ padding: '6px 16px', fontSize: 13 }}>
                                {syncing ? '⏳ Загрузка...' : '🔄 Синхронизировать'}
                            </button>
                            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
                                <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Налог %:</span>
                                <input type="number" value={taxRate} step="0.1"
                                    onChange={e => setTaxRate(parseFloat(e.target.value) || 0)}
                                    style={{ width: 60, background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', textAlign: 'center' }} />
                                <button className="btn-secondary" onClick={handleSaveTax} style={{ padding: '4px 10px', fontSize: 12 }}>Сохранить</button>
                            </div>
                        </div>
                    </div>

                    {/* Summary header */}
                    {summary && (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8, marginBottom: 16 }}>
                            {[
                                { label: 'Переходы', value: fmt(summary.open_card), color: '#f59e0b' },
                                { label: 'Корзины', value: fmt(summary.add_to_cart), color: '#3b82f6' },
                                { label: 'Заказы', value: fmt(summary.orders_count), color: '#10b981' },
                                { label: 'Сумма заказов ₽', value: fmt(summary.orders_sum_rub), color: '#8b5cf6' },
                                { label: 'Расходы рекл. ₽', value: fmt(summary.adv_sum), color: '#ef4444' },
                                { label: 'Просмотры', value: fmt(summary.adv_views), color: '#6366f1' },
                                { label: 'Клики', value: fmt(summary.adv_clicks), color: '#ec4899' },
                            ].map(s => (
                                <div key={s.label} className="glass-card" style={{ padding: '10px 14px', textAlign: 'center' }}>
                                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>{s.label}</div>
                                    <div style={{ fontSize: 18, fontWeight: 700, color: s.color }}>{s.value}</div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Filters */}
                    <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                        <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13 }} />
                        <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                        <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13 }} />
                        <select value={brand} onChange={e => setBrand(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13 }}>
                            <option value="">Все бренды</option>
                            {filters.brands?.map((b: string) => <option key={b} value={b}>{b}</option>)}
                        </select>
                        <select value={subject} onChange={e => setSubject(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13 }}>
                            <option value="">Все категории</option>
                            {filters.subjects?.map((s: string) => <option key={s} value={s}>{s}</option>)}
                        </select>
                        <input placeholder="🔍 Артикул..." value={search} onChange={e => setSearch(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13, width: 160 }} />
                        {detailed && (
                            <span style={{ fontSize: 12, color: '#f59e0b', marginLeft: 8 }}>📋 Детализация по артикулам</span>
                        )}
                    </div>

                    {/* Table */}
                    <div className="glass-card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 380px)' }}>
                        {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> : (
                            <table className="data-table" style={{ minWidth: detailed ? 1800 : 1200 }}>
                                <thead>
                                    <tr>
                                        <th style={{ position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 2 }}>Дата</th>
                                        {detailed && <th>Артикул</th>}
                                        {detailed && <th>nmId</th>}
                                        {detailed && <th>Предмет</th>}
                                        {detailed && <th>Бренд</th>}
                                        <th colSpan={5} style={{ background: 'rgba(245,158,11,0.15)', textAlign: 'center' }}>Воронка</th>
                                        <th colSpan={7} style={{ background: 'rgba(99,102,241,0.15)', textAlign: 'center' }}>Внутренняя реклама</th>
                                        <th colSpan={4} style={{ background: 'rgba(16,185,129,0.15)', textAlign: 'center' }}>Финансы</th>
                                        <th colSpan={2} style={{ background: 'rgba(236,72,153,0.15)', textAlign: 'center' }}>Конверсия</th>
                                    </tr>
                                    <tr>
                                        {/* second header row — repeat Дата for alignment */}
                                        <th style={{ position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 2, fontSize: 0, padding: 0, height: 0, overflow: 'hidden' }}></th>
                                        {detailed && <th style={{ fontSize: 0, padding: 0, height: 0 }}></th>}
                                        {detailed && <th style={{ fontSize: 0, padding: 0, height: 0 }}></th>}
                                        {detailed && <th style={{ fontSize: 0, padding: 0, height: 0 }}></th>}
                                        {detailed && <th style={{ fontSize: 0, padding: 0, height: 0 }}></th>}
                                        {/* Воронка */}
                                        <th style={{ background: 'rgba(245,158,11,0.08)' }}>Переходы</th>
                                        <th style={{ background: 'rgba(245,158,11,0.08)' }}>Корзины</th>
                                        <th style={{ background: 'rgba(245,158,11,0.08)' }}>Заказы</th>
                                        <th style={{ background: 'rgba(245,158,11,0.08)' }}>Сумма ₽</th>
                                        <th style={{ background: 'rgba(245,158,11,0.08)' }}>Выручка ₽</th>
                                        {/* Реклама */}
                                        <th style={{ background: 'rgba(99,102,241,0.08)' }}>Расходы ₽</th>
                                        <th style={{ background: 'rgba(99,102,241,0.08)' }}>Просмотры</th>
                                        <th style={{ background: 'rgba(99,102,241,0.08)' }}>Клики</th>
                                        <th style={{ background: 'rgba(99,102,241,0.08)' }}>CTR</th>
                                        <th style={{ background: 'rgba(99,102,241,0.08)' }}>CPC</th>
                                        <th style={{ background: 'rgba(99,102,241,0.08)' }}>CPM</th>
                                        <th style={{ background: 'rgba(99,102,241,0.08)' }}>ДРР</th>
                                        {/* Финансы */}
                                        {detailed && <th style={{ background: 'rgba(16,185,129,0.08)' }}>Себест. ₽</th>}
                                        <th style={{ background: 'rgba(16,185,129,0.08)' }}>Налог ₽</th>
                                        <th style={{ background: 'rgba(16,185,129,0.08)' }}>Прибыль ₽</th>
                                        <th style={{ background: 'rgba(16,185,129,0.08)' }}>Маржа</th>
                                        <th style={{ background: 'rgba(16,185,129,0.08)' }}>Ср. цена</th>
                                        {/* Конверсия */}
                                        <th style={{ background: 'rgba(236,72,153,0.08)' }}>В корзину</th>
                                        <th style={{ background: 'rgba(236,72,153,0.08)' }}>В заказ</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.length === 0 && (
                                        <tr><td colSpan={30} style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-dim)' }}>
                                            Нет данных. Нажмите «Синхронизировать» чтобы загрузить данные из WB.
                                        </td></tr>
                                    )}
                                    {data.map((r, i) => (
                                        <tr key={i}>
                                            <td style={{ position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 1, whiteSpace: 'nowrap', fontSize: 12 }}>{r.date}</td>
                                            {detailed && <td style={{ fontSize: 12 }}>{r.vendor_code}</td>}
                                            {detailed && <td style={{ fontSize: 12 }}><a href={`https://www.wildberries.ru/catalog/${r.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{r.nm_id}</a></td>}
                                            {detailed && <td style={{ fontSize: 12 }}>{r.subject}</td>}
                                            {detailed && <td style={{ fontSize: 12 }}>{r.brand}</td>}
                                            {/* Воронка */}
                                            <td style={{ textAlign: 'right' }}>{fmt(r.open_card)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.add_to_cart)}</td>
                                            <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(r.orders_count)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.orders_sum_rub)}</td>
                                            <td style={{ textAlign: 'right', fontWeight: 500 }}>{fmt(r.revenue)}</td>
                                            {/* Реклама */}
                                            <td style={{ textAlign: 'right', color: r.adv_sum > 0 ? '#ef4444' : '' }}>{fmt(r.adv_sum)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.adv_views)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.adv_clicks)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmtPct(r.ctr)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.cpc)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.cpm)}</td>
                                            <td style={{ textAlign: 'right', color: r.drr > 30 ? '#ef4444' : r.drr > 15 ? '#f59e0b' : '#10b981' }}>{fmtPct(r.drr)}</td>
                                            {/* Финансы */}
                                            {detailed && <td style={{ textAlign: 'right' }}>{r.cost_price ? fmt(r.cost_total) : <span style={{ color: '#f59e0b', fontSize: 11 }}>—</span>}</td>}
                                            <td style={{ textAlign: 'right' }}>{fmt(r.tax)}</td>
                                            <td style={{ textAlign: 'right', fontWeight: 700, color: r.profit > 0 ? '#10b981' : '#ef4444' }}>{fmt(r.profit)}</td>
                                            <td style={{ textAlign: 'right', color: r.margin > 0 ? '#10b981' : '#ef4444' }}>{fmtPct(r.margin)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmt(r.avg_price)}</td>
                                            {/* Конверсия */}
                                            <td style={{ textAlign: 'right' }}>{fmtPct(r.add_to_cart_pct)}</td>
                                            <td style={{ textAlign: 'right' }}>{fmtPct(r.cart_to_order_pct)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>
                    <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-dim)' }}>
                        Всего строк: {data.length} {!detailed && '(агрегация по дням)'}
                    </div>
                </>
            )}

            {tab === 'costs' && (
                <div>
                    <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>Ручная себестоимость</h2>
                    <p style={{ fontSize: 13, color: 'var(--color-text-dim)', marginBottom: 16 }}>
                        Товары без себестоимости из заказов. Укажите себестоимость за штуку для расчёта прибыли.
                    </p>
                    {costs.missing?.length > 0 && (
                        <div className="glass-card" style={{ marginBottom: 16 }}>
                            <h3 style={{ fontSize: 14, fontWeight: 500, marginBottom: 8, padding: '12px 16px 0' }}>
                                ⚠️ Без себестоимости ({costs.missing.length})
                            </h3>
                            <table className="data-table">
                                <thead><tr><th>nmId</th><th>Артикул</th><th>Предмет</th><th>Бренд</th><th>Себестоимость ₽</th><th></th></tr></thead>
                                <tbody>
                                    {costs.missing.map((m: any) => (
                                        <tr key={m.nm_id}>
                                            <td><a href={`https://www.wildberries.ru/catalog/${m.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{m.nm_id}</a></td>
                                            <td>{m.vendor_code}</td><td>{m.subject}</td><td>{m.brand}</td>
                                            <td>{editCost?.nm_id === m.nm_id ? (
                                                <input type="number" value={editCost.cost_price} autoFocus
                                                    onChange={e => setEditCost({ ...editCost, cost_price: e.target.value })}
                                                    onKeyDown={e => e.key === 'Enter' && handleSaveCost()}
                                                    style={{ width: 100, background: 'var(--color-bg)', border: '1px solid var(--color-accent)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)' }} />
                                            ) : '—'}</td>
                                            <td>{editCost?.nm_id === m.nm_id ? (
                                                <div style={{ display: 'flex', gap: 4 }}>
                                                    <button className="btn-primary" onClick={handleSaveCost} style={{ padding: '2px 8px', fontSize: 12 }}>✓</button>
                                                    <button className="btn-secondary" onClick={() => setEditCost(null)} style={{ padding: '2px 8px', fontSize: 12 }}>✕</button>
                                                </div>
                                            ) : (
                                                <button className="btn-secondary" onClick={() => setEditCost({ nm_id: m.nm_id, cost_price: '' })} style={{ padding: '2px 8px', fontSize: 12 }}>✏️</button>
                                            )}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                    {costs.overrides?.length > 0 && (
                        <div className="glass-card">
                            <h3 style={{ fontSize: 14, fontWeight: 500, marginBottom: 8, padding: '12px 16px 0' }}>
                                ✅ Установленные ({costs.overrides.length})
                            </h3>
                            <table className="data-table">
                                <thead><tr><th>nmId</th><th>Себестоимость ₽</th><th></th></tr></thead>
                                <tbody>
                                    {costs.overrides.map((o: any) => (
                                        <tr key={o.nm_id}>
                                            <td><a href={`https://www.wildberries.ru/catalog/${o.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{o.nm_id}</a></td>
                                            <td>{editCost?.nm_id === o.nm_id ? (
                                                <input type="number" value={editCost.cost_price} autoFocus
                                                    onChange={e => setEditCost({ ...editCost, cost_price: e.target.value })}
                                                    onKeyDown={e => e.key === 'Enter' && handleSaveCost()}
                                                    style={{ width: 100, background: 'var(--color-bg)', border: '1px solid var(--color-accent)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)' }} />
                                            ) : fmt(o.cost_price)}</td>
                                            <td>{editCost?.nm_id === o.nm_id ? (
                                                <div style={{ display: 'flex', gap: 4 }}>
                                                    <button className="btn-primary" onClick={handleSaveCost} style={{ padding: '2px 8px', fontSize: 12 }}>✓</button>
                                                    <button className="btn-secondary" onClick={() => setEditCost(null)} style={{ padding: '2px 8px', fontSize: 12 }}>✕</button>
                                                </div>
                                            ) : (
                                                <button className="btn-secondary" onClick={() => setEditCost({ nm_id: o.nm_id, cost_price: String(o.cost_price) })} style={{ padding: '2px 8px', fontSize: 12 }}>✏️</button>
                                            )}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                    {costs.missing?.length === 0 && costs.overrides?.length === 0 && (
                        <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                            Нет данных. Сначала синхронизируйте воронку на вкладке «Воронка».
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
