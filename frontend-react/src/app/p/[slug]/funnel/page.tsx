'use client';
import React, { useEffect, useState, useCallback, useRef } from 'react';
import { api } from '@/lib/api';
import { usePermissions } from '@/lib/hooks/usePermissions';
import { MultiLineChart } from './components/MultiLineChart';
import { DayAnalysisTab } from './components/DayAnalysisTab';
import { AdsTab } from './components/AdsTab';
import type { FunnelDayRow, FunnelSkuRow, FunnelGroupRow, FunnelAbcRow, FunnelSummary, FunnelFilters } from '@/types/api';

const fmt = (n: number) => n?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '0';
const fmtPct = (n: number) => (n || 0).toFixed(2) + '%';

/* ─── Main page ──────────────────────────────────────────────── */

export default function FunnelPage() {
    const { canEdit } = usePermissions();
    const [tab, setTab] = useState<'funnel' | 'day-analysis' | 'ads'>('funnel');
    const [data, setData] = useState<FunnelDayRow[]>([]);
    const [detailed, setDetailed] = useState(false);
    const [summary, setSummary] = useState<FunnelSummary|null>(null);
    const [filters, setFilters] = useState<FunnelFilters>({ brands: [], subjects: [], vendor_codes: [], min_date: null, max_date: null });
    const [loading, setLoading] = useState(false);
    const headerRow1Ref = useRef<HTMLTableRowElement>(null);
    const [row1H, setRow1H] = useState(32);
    const [initDone, setInitDone] = useState(false);
    const [lastSyncAt, setLastSyncAt] = useState<string | null>(null);
    const [missingDays, setMissingDays] = useState<number | null>(null);
    const [hasBdr, setHasBdr] = useState(false);

    // Group by mode
    const [groupBy, setGroupBy] = useState<'day' | 'sku' | 'brand' | 'subject' | 'tag' | 'imt' | 'abc'>('day');
    const [skuData, setSkuData] = useState<FunnelSkuRow[]>([]);
    const [groupData, setGroupData] = useState<FunnelGroupRow[]>([]);
    const [abcData, setAbcData] = useState<FunnelAbcRow[]>([]);
    const [expandedAbc, setExpandedAbc] = useState<Set<string>>(new Set());

    // Filters
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');
    const [brand, setBrand] = useState('');
    const [subject, setSubject] = useState('');
    const [search, setSearch] = useState('');

    // Which charts to display (multiple selection)
    const [chartFields, setChartFields] = useState<{ field: string; label: string; color: string }[]>([
        { field: 'orders_sum_rub', label: 'Сумма заказов ₽', color: '#8b5cf6' }
    ]);

    // Measure first header row height dynamically
    useEffect(() => {
        const el = headerRow1Ref.current;
        if (!el) return;
        const measure = () => setRow1H(el.offsetHeight);
        measure();
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, [data]);

    const loadFilters = useCallback(async () => {
        try {
            const f = await api.getFunnelFilters();
            setFilters(f);
            return f;
        } catch { return null; }
    }, []);

    const loadData = useCallback(async (df?: string, dt?: string, gb?: 'day' | 'sku' | 'brand' | 'subject' | 'tag' | 'imt' | 'abc') => {
        const from = df || dateFrom;
        const to = dt || dateTo;
        const mode = gb || groupBy;
        if (!from || !to) return [];
        setLoading(true);
        try {
            const [res, sum] = await Promise.all([
                api.getFunnelData({ date_from: from, date_to: to, brand, vendor_code: search, subject, group_by: mode }),
                api.getFunnelSummary(from, to, brand, subject),
            ]);
            if (mode === 'abc') {
                setAbcData((res.data || []) as any[]);
                setData([]);
                setSkuData([]);
                setGroupData([]);
            } else if (mode === 'sku') {
                setSkuData((res.data || []) as FunnelSkuRow[]);
                setData([]);
                setGroupData([]);
                setAbcData([]);
            } else if (mode === 'brand' || mode === 'subject' || mode === 'tag' || mode === 'imt') {
                setGroupData((res.data || []) as FunnelGroupRow[]);
                setData([]);
                setSkuData([]);
                setAbcData([]);
            } else {
                setData((res.data || []) as FunnelDayRow[]);
                setSkuData([]);
                setGroupData([]);
                setAbcData([]);
            }
            setDetailed(res.detailed || false);
            setSummary(sum);
            setHasBdr(res.has_bdr || false);
            return res.data || [];
        } catch (e: unknown) {
            console.error(e);
            return [];
        } finally {
            setLoading(false);
        }
    }, [dateFrom, dateTo, brand, subject, search, groupBy]);

    const loadSyncStatus = useCallback(async () => {
        try {
            const s = await api.getSyncStatus();
            if (s.last_syncs?.length > 0) {
                const last = s.last_syncs[0];
                setLastSyncAt(last.finished_at || last.started_at || null);
            }
            if (s.missing_days != null) {
                setMissingDays(s.missing_days);
            }
        } catch { }
    }, []);

    // Init: load filters → set dates from DB range → load data
    useEffect(() => {
        (async () => {
            const f = await loadFilters();
            await loadSyncStatus();
            if (f?.min_date && f?.max_date) {
                // Default to last 30 days for Ads tab, full range for others
                const today = new Date();
                const thirtyDaysAgo = new Date(today);
                thirtyDaysAgo.setDate(today.getDate() - 30);
                const defaultFrom = thirtyDaysAgo.toISOString().slice(0, 10);
                const defaultTo = f.max_date;
                setDateFrom(defaultFrom);
                setDateTo(defaultTo);
                await loadData(defaultFrom, defaultTo);
            }
            setInitDone(true);
        })();
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => { if (initDone && dateFrom && dateTo) loadData(); }, [dateFrom, dateTo, brand, subject, search]);


    // Summary card definitions
    interface SummaryCard {
        label: string;
        field: string;
        color: string;
        suffix?: string;
    }

    const summaryCards: SummaryCard[] = [
        { label: 'Переходы', field: 'open_card', color: '#f59e0b' },
        { label: 'Корзины', field: 'add_to_cart', color: '#3b82f6' },
        { label: 'Заказы', field: 'orders_count', color: '#10b981' },
        { label: 'Сумма заказов ₽', field: 'orders_sum_rub', color: '#8b5cf6' },
        { label: 'Расходы рекл. ₽', field: 'adv_sum', color: '#ef4444' },
        { label: 'ДРР %', field: 'drr', color: '#f97316', suffix: '%' },
        { label: 'Просмотры', field: 'adv_views', color: '#6366f1' },
        { label: 'Клики', field: 'adv_clicks', color: '#ec4899' },
    ];

    const formatSyncDate = (iso: string | null) => {
        if (!iso) return '—';
        try {
            const d = new Date(iso);
            return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        } catch { return iso; }
    };

    return (
        <div>
            <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 16 }}>📊 Воронка продаж</h1>

            {/* Tabs */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                <button className={`tab-btn ${tab === 'funnel' ? 'active' : ''}`} onClick={() => setTab('funnel')}>Воронка</button>
                <button className={`tab-btn ${tab === 'day-analysis' ? 'active' : ''}`} onClick={() => setTab('day-analysis')}>🔍 Анализ дня</button>
                <button className={`tab-btn ${tab === 'ads' ? 'active' : ''}`} onClick={() => {
                    setTab('ads');
                    // Default to last 30 days for ads tab
                    const today = new Date();
                    const d30 = new Date(today);
                    d30.setDate(today.getDate() - 30);
                    const from30 = d30.toISOString().slice(0, 10);
                    const toNow = filters.max_date || today.toISOString().slice(0, 10);
                    if (dateFrom < from30) {
                        setDateFrom(from30);
                        setDateTo(toNow);
                    }
                }}>📢 Реклама</button>
            </div>

            {/* Shared filters — visible on funnel + ads tabs */}
            {(tab === 'funnel' || tab === 'ads') && (
                <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                    <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
                        min={filters.min_date || undefined} max={filters.max_date || undefined}
                        style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13 }} />
                    <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                    <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
                        min={filters.min_date || undefined} max={filters.max_date || undefined}
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
                </div>
            )}

            {tab === 'funnel' && (
                <>
                    {/* Sync status + Tax */}
                    <div className="glass-card" style={{ marginBottom: 16, padding: '12px 16px' }}>
                        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                            <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>
                                🔄 Последняя синхронизация: <strong style={{ color: 'var(--color-text)' }}>{formatSyncDate(lastSyncAt)}</strong>
                            </span>
                            <span style={{ fontSize: 12, color: 'rgba(16,185,129,0.8)' }}>● авто</span>
                            {missingDays != null && missingDays > 0 && (
                                <span style={{ fontSize: 12, color: '#f59e0b', background: 'rgba(245,158,11,0.1)', padding: '2px 8px', borderRadius: 4 }}>
                                    ⏳ Осталось обновить: {missingDays} {missingDays === 1 ? 'день' : missingDays < 5 ? 'дня' : 'дней'}
                                </span>
                            )}
                            {missingDays === 0 && (
                                <span style={{ fontSize: 12, color: '#10b981' }}>✅ Все дни синхронизированы</span>
                            )}
                            {hasBdr && (
                                <span title="Прибыль рассчитана по данным БДР за 7 дней — учитывает реальную комиссию WB, логистику, штрафы, хранение"
                                    style={{ fontSize: 12, color: '#3b82f6', background: 'rgba(59,130,246,0.1)', padding: '2px 8px', borderRadius: 4, cursor: 'help' }}>
                                    ℹ️ Прибыль по БДР
                                </span>
                            )}
                            {!hasBdr && (
                                <span title="Загрузите финансовый отчёт WB для точного расчёта прибыли"
                                    style={{ fontSize: 12, color: '#f59e0b', background: 'rgba(245,158,11,0.1)', padding: '2px 8px', borderRadius: 4, cursor: 'help' }}>
                                    ⚠️ Нет данных БДР — прибыль по тарифам
                                </span>
                            )}
                        </div>
                    </div>

                    {/* Summary header — clickable cards to switch chart */}
                    {summary && (
                        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${summaryCards.length}, 1fr)`, gap: 6, marginBottom: 12 }}>
                            {summaryCards.map((s: SummaryCard) => {
                                const isActive = chartFields.some(c => c.field === s.field);
                                const toggleChart = () => {
                                    setChartFields(prev => {
                                        const exists = prev.find(c => c.field === s.field);
                                        if (exists) {
                                            const next = prev.filter(c => c.field !== s.field);
                                            return next.length > 0 ? next : prev; // keep at least one
                                        }
                                        return [...prev, { field: s.field, label: s.label, color: s.color }];
                                    });
                                };
                                return (
                                    <div key={s.label} className="glass-card"
                                        onClick={toggleChart}
                                        style={{
                                            padding: '10px 14px', textAlign: 'center',
                                            cursor: 'pointer', transition: 'transform 0.15s, box-shadow 0.15s',
                                            border: isActive ? `1px solid ${s.color}60` : '1px solid transparent',
                                            boxShadow: isActive ? `0 2px 12px ${s.color}20` : 'none',
                                        }}
                                        onMouseEnter={e => { (e.currentTarget as HTMLElement).style.transform = 'translateY(-2px)'; }}
                                        onMouseLeave={e => { (e.currentTarget as HTMLElement).style.transform = 'none'; }}>
                                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>
                                            {s.label} {isActive ? '📈' : ''}
                                        </div>
                                        <div style={{ fontSize: 18, fontWeight: 700, color: s.color }}>
                                            {s.suffix ? fmtPct(summary[s.field]).replace('%', '') + s.suffix : fmt(summary[s.field])}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}

                    {/* Search filter (funnel only) */}
                    <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                        <input placeholder="🔍 Артикул..." value={search} onChange={e => setSearch(e.target.value)}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)', fontSize: 13, width: 160 }} />
                        {detailed && (
                            <span style={{ fontSize: 12, color: '#f59e0b', marginLeft: 8 }}>📋 Детализация по артикулам</span>
                        )}
                    </div>

                    {/* Group by toggle */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                        <h3 style={{ fontSize: 16, fontWeight: 600, color: '#111827', margin: 0 }}>
                            {groupBy === 'day' ? 'Сводка по дням' : groupBy === 'sku' ? 'Сводка по товарам' : groupBy === 'brand' ? 'Сводка по брендам' : groupBy === 'abc' ? 'ABC анализ' : 'Сводка по категориям'}
                        </h3>
                        <div style={{ display: 'flex', gap: 0, border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}>
                            {(['day', 'sku', 'brand', 'subject', 'tag', 'imt', 'abc'] as const).map((mode, idx) => {
                                const labels = { day: 'По дням', sku: 'По артикулам', brand: 'По брендам', subject: 'По категориям', tag: 'По ярлыкам', imt: 'По склейкам', abc: 'ABC анализ' };
                                return (
                                    <button
                                        key={mode}
                                        onClick={() => { setGroupBy(mode); loadData(undefined, undefined, mode); }}
                                        style={{
                                            padding: '6px 16px', fontSize: 13, fontWeight: 500, cursor: 'pointer', border: 'none',
                                            borderLeft: idx > 0 ? '1px solid #e5e7eb' : 'none',
                                            background: groupBy === mode ? '#3b82f6' : '#fff',
                                            color: groupBy === mode ? '#fff' : '#374151',
                                        }}
                                    >{labels[mode]}</button>
                                );
                            })}
                        </div>
                    </div>

                    {/* Inline chart — above table (only in day mode) */}
                    {groupBy === 'day' && data.length > 0 && chartFields.length > 0 && (
                        <MultiLineChart data={data} lines={chartFields} />
                    )}

                    {/* SKU Table */}
                    {groupBy === 'sku' && (
                        <div className="glass-card" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                            <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 320px)' }}>
                                {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> : (
                                    <table className="data-table" style={{ minWidth: 1600, borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#ffffff' }}>
                                    <thead>
                                        <tr ref={headerRow1Ref}>
                                            <th rowSpan={2} style={{ position: 'sticky', left: 0, top: 0, background: '#ffffff', color: '#374151', zIndex: 22, verticalAlign: 'bottom', borderBottom: '2px solid #e5e7eb', minWidth: 200, borderRight: '1px solid #e5e7eb', padding: '8px 12px', boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.08)' }}>ТОВАР</th>
                                            <th colSpan={5} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb' }}>ВОРОНКА ПРОДАЖ</th>
                                            <th colSpan={7} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>ВНУТРЕННЯЯ РЕКЛАМА</th>
                                            <th colSpan={8} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>ФИНАНСЫ</th>
                                            <th colSpan={2} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>КОНВЕРСИЯ</th>
                                        </tr>
                                        <tr>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Переходы</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Корзины</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Заказы</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Сумма ₽</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Выручка ₽</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>Расходы ₽</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Просмотры</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Клики</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>CTR</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>CPC</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>CPM</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>ДРР</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>СПП %</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Выкуп %</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Налог ₽</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Расх. WB %</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Комиссия ₽</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Прибыль ₽</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Маржа</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Ср. цена</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>В корзину</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>В заказ</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {skuData.length === 0 && (
                                            <tr><td colSpan={30} style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-dim)' }}>
                                                Нет данных за выбранный период
                                            </td></tr>
                                        )}
                                        {skuData.map((r, i) => {
                                            const rowBg = i % 2 === 0 ? '#ffffff' : '#f9fafb';
                                            return (
                                                <tr key={r.nm_id} style={{ background: rowBg, color: '#111827' }}>
                                                    <td style={{ position: 'sticky', left: 0, background: rowBg, zIndex: 2, padding: '8px 12px', borderRight: '1px solid #e5e7eb', borderBottom: '1px solid #f3f4f6', boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.05)', minWidth: 200 }}>
                                                        <div style={{ fontWeight: 600, fontSize: 13 }}>{r.brand || '\u2014'}</div>
                                                        <div style={{ fontSize: 11, color: '#6b7280' }}>{r.vendor_code} <span style={{ color: '#9ca3af' }}>#WB-{r.nm_id}</span></div>
                                                        {r.subject && <div style={{ fontSize: 10, color: '#9ca3af' }}>{r.subject}</div>}
                                                    </td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.open_card)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.add_to_cart)}</td>
                                                    <td style={{ textAlign: 'right', fontWeight: 600, borderBottom: '1px solid #f3f4f6' }}>{fmt(r.orders_count)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.orders_sum_rub)}</td>
                                                    <td style={{ textAlign: 'right', fontWeight: 500, borderBottom: '1px solid #f3f4f6', color: r.revenue > 0 ? '#111827' : '#ef4444' }}>{fmt(r.revenue)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', borderLeft: '1px solid #f3f4f6', color: r.adv_sum > 0 ? '#f97316' : '#9ca3af' }}>{fmt(r.adv_sum)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.adv_views)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.adv_clicks)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.ctr > 5 ? '#10b981' : r.ctr > 2 ? '#374151' : '#f59e0b' }}>{fmtPct(r.ctr)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.cpc)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.cpm)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.drr > 30 ? '#ef4444' : r.drr > 15 ? '#f59e0b' : r.drr > 0 ? '#10b981' : '#9ca3af', fontWeight: r.drr > 30 ? 600 : 400 }}>{fmtPct(r.drr)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', borderLeft: '1px solid #f3f4f6', color: (r.spp_rate || 0) > 40 ? '#ef4444' : (r.spp_rate || 0) > 20 ? '#f59e0b' : '#10b981', fontSize: 12 }}>{r.spp_rate ? fmtPct(r.spp_rate) : '\u2014'}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', fontSize: 12 }}>{r.buyout_percent ? fmtPct(r.buyout_percent) : '\u2014'}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: '#6b7280' }}>{fmt(r.tax)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.commission_rate > 0 ? '#6366f1' : '#9ca3af', fontSize: 12 }}>{r.commission_rate > 0 ? fmtPct(r.commission_rate) : '\u2014'}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.commission > 0 ? '#6366f1' : '#9ca3af', fontWeight: 500 }}>{r.commission > 0 ? fmt(r.commission) : '\u2014'}</td>
                                                    <td style={{ textAlign: 'right', fontWeight: 700, borderBottom: '1px solid #f3f4f6', color: r.profit > 0 ? '#10b981' : '#ef4444', background: r.profit > 0 ? '#f0fdf4' : r.profit < 0 ? '#fef2f2' : undefined }}>{fmt(r.profit)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.margin > 20 ? '#10b981' : r.margin > 0 ? '#65a30d' : '#ef4444', fontWeight: r.margin > 20 ? 600 : 400 }}>{fmtPct(r.margin)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.avg_price)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', borderLeft: '1px solid #f3f4f6' }}>{fmtPct(r.add_to_cart_pct)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmtPct(r.cart_to_order_pct)}</td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                    </table>
                                )}
                            </div>
                            <div style={{ padding: '16px 20px', borderTop: '1px solid #e5e7eb', fontSize: 12, color: 'var(--color-text-dim)', background: '#f9fafb' }}>
                                Всего товаров: {skuData.length} (топ-500 по сумме заказов)
                            </div>
                        </div>
                    )}

                    {/* Brand / Subject Group Table */}
                    {(groupBy === 'brand' || groupBy === 'subject' || groupBy === 'tag' || groupBy === 'imt') && (
                        <div className="glass-card" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                            <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 320px)' }}>
                                {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> : (
                                    <table className="data-table" style={{ minWidth: 1600, borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#ffffff' }}>
                                    <thead>
                                        <tr ref={headerRow1Ref}>
                                            <th rowSpan={2} style={{ position: 'sticky', left: 0, top: 0, background: '#ffffff', color: '#374151', zIndex: 22, verticalAlign: 'bottom', borderBottom: '2px solid #e5e7eb', minWidth: 200, borderRight: '1px solid #e5e7eb', padding: '8px 12px', boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.08)' }}>{groupBy === 'brand' ? 'БРЕНД' : groupBy === 'tag' ? 'ЯРЛЫК' : groupBy === 'imt' ? 'СКЛЕЙКА' : 'КАТЕГОРИЯ'}</th>
                                            <th colSpan={5} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb' }}>ВОРОНКА ПРОДАЖ</th>
                                            <th colSpan={7} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>ВНУТРЕННЯЯ РЕКЛАМА</th>
                                            <th colSpan={8} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>ФИНАНСЫ</th>
                                            <th colSpan={2} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>КОНВЕРСИЯ</th>
                                        </tr>
                                        <tr>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Переходы</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Корзины</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Заказы</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Сумма ₽</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Выручка ₽</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>Расходы ₽</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Просмотры</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Клики</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>CTR</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>CPC</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>CPM</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>ДРР</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>СПП %</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Выкуп %</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Налог ₽</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Расх. WB %</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Комиссия ₽</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Прибыль ₽</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Маржа</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Ср. цена</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>В корзину</th>
                                            <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>В заказ</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {groupData.length === 0 && (
                                            <tr><td colSpan={30} style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-dim)' }}>
                                                Нет данных за выбранный период
                                            </td></tr>
                                        )}
                                        {groupData.map((r, i) => {
                                            const grpLabel = groupBy === 'brand' ? (r.brand || '\u2014') : groupBy === 'tag' ? (r.tag || '\u2014') : groupBy === 'imt' ? (r.imt_group || '\u2014') : (r.subject || '\u2014');
                                            const rowBg = i % 2 === 0 ? '#ffffff' : '#f9fafb';
                                            return (
                                                <tr key={grpLabel} style={{ background: rowBg, color: '#111827' }}>
                                                    <td style={{ position: 'sticky', left: 0, background: rowBg, zIndex: 2, padding: '8px 12px', borderRight: '1px solid #e5e7eb', borderBottom: '1px solid #f3f4f6', boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.05)', minWidth: 200 }}>
                                                        <div style={{ fontWeight: 600, fontSize: 13 }}>{grpLabel}</div>
                                                    </td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.open_card)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.add_to_cart)}</td>
                                                    <td style={{ textAlign: 'right', fontWeight: 600, borderBottom: '1px solid #f3f4f6' }}>{fmt(r.orders_count)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.orders_sum_rub)}</td>
                                                    <td style={{ textAlign: 'right', fontWeight: 500, borderBottom: '1px solid #f3f4f6', color: r.revenue > 0 ? '#111827' : '#ef4444' }}>{fmt(r.revenue)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', borderLeft: '1px solid #f3f4f6', color: r.adv_sum > 0 ? '#f97316' : '#9ca3af' }}>{fmt(r.adv_sum)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.adv_views)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.adv_clicks)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.ctr > 5 ? '#10b981' : r.ctr > 2 ? '#374151' : '#f59e0b' }}>{fmtPct(r.ctr)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.cpc)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.cpm)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.drr > 30 ? '#ef4444' : r.drr > 15 ? '#f59e0b' : r.drr > 0 ? '#10b981' : '#9ca3af', fontWeight: r.drr > 30 ? 600 : 400 }}>{fmtPct(r.drr)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', borderLeft: '1px solid #f3f4f6', color: (r.spp_rate || 0) > 40 ? '#ef4444' : (r.spp_rate || 0) > 20 ? '#f59e0b' : '#10b981', fontSize: 12 }}>{r.spp_rate ? fmtPct(r.spp_rate) : '\u2014'}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', fontSize: 12 }}>{r.buyout_percent ? fmtPct(r.buyout_percent) : '\u2014'}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: '#6b7280' }}>{fmt(r.tax)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.commission_rate > 0 ? '#6366f1' : '#9ca3af', fontSize: 12 }}>{r.commission_rate > 0 ? fmtPct(r.commission_rate) : '\u2014'}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.commission > 0 ? '#6366f1' : '#9ca3af', fontWeight: 500 }}>{r.commission > 0 ? fmt(r.commission) : '\u2014'}</td>
                                                    <td style={{ textAlign: 'right', fontWeight: 700, borderBottom: '1px solid #f3f4f6', color: r.profit > 0 ? '#10b981' : '#ef4444', background: r.profit > 0 ? '#f0fdf4' : r.profit < 0 ? '#fef2f2' : undefined }}>{fmt(r.profit)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.margin > 20 ? '#10b981' : r.margin > 0 ? '#65a30d' : '#ef4444', fontWeight: r.margin > 20 ? 600 : 400 }}>{fmtPct(r.margin)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.avg_price)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', borderLeft: '1px solid #f3f4f6' }}>{fmtPct(r.add_to_cart_pct)}</td>
                                                    <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmtPct(r.cart_to_order_pct)}</td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                    </table>
                                )}
                            </div>
                            <div style={{ padding: '16px 20px', borderTop: '1px solid #e5e7eb', fontSize: 12, color: 'var(--color-text-dim)', background: '#f9fafb' }}>
                                Всего {groupBy === 'brand' ? 'брендов' : groupBy === 'tag' ? 'ярлыков' : groupBy === 'imt' ? 'склеек' : 'категорий'}: {groupData.length}
                            </div>
                        </div>
                    )}

                    {/* ABC Analysis — 3 grouped rows A/B/C with expand to show SKU items */}
                    {groupBy === 'abc' && (() => {
                        const ABC_COLORS: Record<string, string> = { A: '#22c55e', B: '#f59e0b', C: '#ef4444' };
                        const ABC_LABELS: Record<string, string> = { A: 'Категория A (80% выручки)', B: 'Категория B (15% выручки)', C: 'Категория C (5% выручки)' };
                        const abcBadge = (val: string) => (
                            <span style={{ display: 'inline-block', width: 32, height: 24, lineHeight: '24px', textAlign: 'center', borderRadius: 6, fontWeight: 700, fontSize: 14, color: '#fff', background: ABC_COLORS[val] || '#9ca3af' }}>{val}</span>
                        );
                        // eslint-disable-next-line @typescript-eslint/no-explicit-any
                        const d = abcData as any[];
                        const totalRev = d.reduce((s: number, r: any) => s + (r.revenue || 0), 0);
                        const groups = (['A', 'B', 'C'] as const).map(cat => {
                            // eslint-disable-next-line @typescript-eslint/no-explicit-any
                            const items = d.filter((r: any) => r.abc_revenue === cat);
                            const sum = (key: string) => items.reduce((s: number, r: any) => s + (r[key] || 0), 0);
                            const revenue = sum('revenue'); const profit = sum('profit');
                            const ordersCount = sum('orders_count'); const ordersSum = sum('orders_sum_rub');
                            const adv = sum('adv_sum'); const views = sum('adv_views'); const clicks = sum('adv_clicks');
                            const openCard = sum('open_card'); const addToCart = sum('add_to_cart');
                            const tax = sum('tax'); const commission = sum('commission'); const costTotal = sum('cost_total');
                            const margin = revenue ? (profit / revenue * 100) : 0;
                            const drr = ordersSum ? (adv / ordersSum * 100) : 0;
                            const pctRev = totalRev ? (revenue / totalRev * 100) : 0;
                            const ctr = views ? (clicks / views * 100) : 0;
                            const cpc = clicks ? (adv / clicks) : 0;
                            const cpm = views ? (adv / views * 1000) : 0;
                            const cartPct = openCard ? (addToCart / openCard * 100) : 0;
                            const orderPct = addToCart ? (ordersCount / addToCart * 100) : 0;
                            return { cat, items, revenue, profit, ordersCount, ordersSum, adv, views, clicks, openCard, addToCart, tax, commission, costTotal, margin, drr, pctRev, ctr, cpc, cpm, cartPct, orderPct };
                        });
                        const thS = { position: 'sticky' as const, top: 0, background: '#fff', zIndex: 20, borderBottom: '2px solid #e5e7eb', padding: '8px 10px', fontSize: 11, textAlign: 'right' as const, color: '#374151' };
                        return (
                        <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                            <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 320px)' }}>
                                {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> : (
                                <table className="data-table" style={{ minWidth: 1800, borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                                    <thead>
                                        <tr>
                                            <th style={{ ...thS, width: 30, textAlign: 'center' }}></th>
                                            <th style={{ ...thS, textAlign: 'left', minWidth: 200 }}>КАТЕГОРИЯ</th>
                                            <th style={thS}>ТОВАРОВ</th>
                                            <th style={thS}>ДОЛЯ ВЫР.</th>
                                            <th style={thS}>ПЕРЕХОДЫ</th>
                                            <th style={thS}>КОРЗИНЫ</th>
                                            <th style={thS}>ЗАКАЗЫ</th>
                                            <th style={thS}>СУММА ₽</th>
                                            <th style={thS}>ВЫРУЧКА ₽</th>
                                            <th style={thS}>РАСХОДЫ РЕКЛ.</th>
                                            <th style={thS}>ПРОСМОТРЫ</th>
                                            <th style={thS}>КЛИКИ</th>
                                            <th style={thS}>CTR</th>
                                            <th style={thS}>CPC</th>
                                            <th style={thS}>CPM</th>
                                            <th style={thS}>ДРР</th>
                                            <th style={thS}>НАЛОГ ₽</th>
                                            <th style={thS}>КОМИССИЯ ₽</th>
                                            <th style={thS}>ПРИБЫЛЬ ₽</th>
                                            <th style={thS}>МАРЖА</th>
                                            <th style={thS}>В КОРЗИНУ</th>
                                            <th style={thS}>В ЗАКАЗ</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {d.length === 0 && <tr><td colSpan={22} style={{ textAlign: 'center', padding: 40, color: '#9ca3af' }}>Нет данных</td></tr>}
                                        {groups.map(g => {
                                            const isExp = expandedAbc.has(g.cat);
                                            const tdS = { padding: '10px 10px', borderBottom: '1px solid #e5e7eb', textAlign: 'right' as const };
                                            return (
                                                <React.Fragment key={g.cat}>
                                                    <tr style={{ cursor: 'pointer', background: ABC_COLORS[g.cat] + '08', fontWeight: 600 }}
                                                        onClick={() => setExpandedAbc(prev => { const n = new Set(prev); n.has(g.cat) ? n.delete(g.cat) : n.add(g.cat); return n; })}>
                                                        <td style={{ ...tdS, textAlign: 'center' }}>{isExp ? '▼' : '▶'}</td>
                                                        <td style={{ ...tdS, textAlign: 'left' }}><span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{abcBadge(g.cat)} {ABC_LABELS[g.cat]}</span></td>
                                                        <td style={tdS}>{g.items.length}</td>
                                                        <td style={tdS}>{fmtPct(g.pctRev)}</td>
                                                        <td style={tdS}>{fmt(g.openCard)}</td>
                                                        <td style={tdS}>{fmt(g.addToCart)}</td>
                                                        <td style={tdS}>{fmt(g.ordersCount)}</td>
                                                        <td style={tdS}>{fmt(g.ordersSum)}</td>
                                                        <td style={tdS}>{fmt(g.revenue)}</td>
                                                        <td style={{ ...tdS, color: '#f97316' }}>{fmt(g.adv)}</td>
                                                        <td style={tdS}>{fmt(g.views)}</td>
                                                        <td style={tdS}>{fmt(g.clicks)}</td>
                                                        <td style={tdS}>{fmtPct(g.ctr)}</td>
                                                        <td style={tdS}>{fmt(g.cpc)}</td>
                                                        <td style={tdS}>{fmt(g.cpm)}</td>
                                                        <td style={{ ...tdS, color: g.drr > 30 ? '#ef4444' : g.drr > 15 ? '#f59e0b' : '#10b981' }}>{fmtPct(g.drr)}</td>
                                                        <td style={tdS}>{fmt(g.tax)}</td>
                                                        <td style={tdS}>{fmt(g.commission)}</td>
                                                        <td style={{ ...tdS, color: g.profit >= 0 ? '#10b981' : '#ef4444', fontWeight: 700 }}>{fmt(g.profit)}</td>
                                                        <td style={{ ...tdS, color: g.margin > 20 ? '#10b981' : g.margin > 0 ? '#65a30d' : '#ef4444' }}>{fmtPct(g.margin)}</td>
                                                        <td style={tdS}>{fmtPct(g.cartPct)}</td>
                                                        <td style={tdS}>{fmtPct(g.orderPct)}</td>
                                                    </tr>
                                                    {isExp && g.items.map((r: any, i: number) => (
                                                        <tr key={r.nm_id || i} style={{ background: i % 2 === 0 ? '#fafafa' : '#fff', fontSize: 12 }}>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}></td>
                                                            <td style={{ ...tdS, textAlign: 'left', borderBottom: '1px solid #f3f4f6' }}>
                                                                <div style={{ fontWeight: 500 }}>{r.vendor_code || r.nm_id}</div>
                                                                <div style={{ fontSize: 11, color: '#9ca3af' }}>{r.subject} · {r.brand}</div>
                                                            </td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6', color: '#9ca3af' }}>—</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}></td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmt(r.open_card)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmt(r.add_to_cart)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmt(r.orders_count)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmt(r.orders_sum_rub)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmt(r.revenue)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6', color: '#f97316' }}>{fmt(r.adv_sum)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmt(r.adv_views)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmt(r.adv_clicks)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmtPct(r.ctr)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmt(r.cpc)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmt(r.cpm)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6', color: r.drr > 30 ? '#ef4444' : r.drr > 15 ? '#f59e0b' : '#10b981' }}>{fmtPct(r.drr)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmt(r.tax)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmt(r.commission)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6', color: r.profit >= 0 ? '#10b981' : '#ef4444' }}>{fmt(r.profit)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmtPct(r.margin)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmtPct(r.add_to_cart_pct)}</td>
                                                            <td style={{ ...tdS, borderBottom: '1px solid #f3f4f6' }}>{fmtPct(r.cart_to_order_pct)}</td>
                                                        </tr>
                                                    ))}
                                                </React.Fragment>
                                            );
                                        })}
                                    </tbody>
                                </table>
                                )}
                            </div>
                            <div style={{ padding: '12px 20px', borderTop: '1px solid #e5e7eb', fontSize: 12, color: '#9ca3af', background: '#f9fafb' }}>
                                Всего товаров: {d.length} | A — {groups[0]?.items.length || 0} шт, B — {groups[1]?.items.length || 0} шт, C — {groups[2]?.items.length || 0} шт
                            </div>
                        </div>
                        );
                    })()}

                    {/* Day Table with sticky header — both rows pinned */}
                    {groupBy === 'day' && <div className="glass-card" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                        <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 280px)' }}>
                            {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> : (
                                <table className="data-table" style={{ minWidth: detailed ? 2000 : 1400, borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#ffffff' }}>
                                <thead>
                                    <tr ref={headerRow1Ref}>
                                        <th rowSpan={2} style={{ position: 'sticky', left: 0, top: 0, background: '#ffffff', color: '#374151', backdropFilter: 'none', zIndex: 22, verticalAlign: 'bottom', borderBottom: '2px solid #e5e7eb', minWidth: 100, borderRight: '1px solid #e5e7eb', padding: '8px 12px', boxShadow: !detailed ? 'inset -6px 0 6px -6px rgba(0,0,0,0.08)' : 'none' }}>ДАТА</th>
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', left: 100, top: 0, background: '#ffffff', color: '#374151', backdropFilter: 'none', zIndex: 22, verticalAlign: 'bottom', minWidth: 130, borderRight: '1px solid #e5e7eb', padding: '8px 12px', boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.08)' }}>Артикул</th>}
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', zIndex: 20, verticalAlign: 'bottom', borderBottom: '2px solid #e5e7eb' }}>nmId</th>}
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', zIndex: 20, verticalAlign: 'bottom', borderBottom: '2px solid #e5e7eb' }}>Предмет</th>}
                                        {detailed && <th rowSpan={2} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', zIndex: 20, verticalAlign: 'bottom', borderBottom: '2px solid #e5e7eb' }}>Бренд</th>}
                                        <th colSpan={5} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb' }}>ВОРОНКА</th>
                                        <th colSpan={7} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>ВНУТРЕННЯЯ РЕКЛАМА</th>
                                        <th colSpan={detailed ? 9 : 8} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>ФИНАНСЫ</th>
                                        <th colSpan={2} style={{ position: 'sticky', top: 0, background: '#f9fafb', color: '#374151', textAlign: 'center', zIndex: 20, borderBottom: '2px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>КОНВЕРСИЯ</th>
                                    </tr>
                                    <tr>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Переходы</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Корзины</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Заказы</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Сумма ₽</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Выручка ₽</th>

                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>Расходы ₽</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Просмотры</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Клики</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>CTR</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>CPC</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>CPM</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>ДРР</th>

                                        {detailed && <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>Себест. ₽</th>}
                                        <th title="СПП — скидка Wildberries за счёт WB, не влияет на выплату, но снижает налог" style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: !detailed ? '1px solid #e5e7eb' : 'none', cursor: 'help' }}>СПП %</th>
                                        <th title="Процент выкупа — сколько заказов фактически выкупается" style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', cursor: 'help' }}>Выкуп %</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Налог ₽</th>
                                        <th title="Расходы WB — комиссия + логистика + штрафы + хранение" style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', cursor: 'help' }}>Расх. WB %</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Комиссия ₽</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Прибыль ₽</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Маржа</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>Ср. цена</th>

                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb', borderLeft: '1px solid #e5e7eb' }}>В корзину</th>
                                        <th style={{ position: 'sticky', top: row1H, background: '#ffffff', color: '#4b5563', zIndex: 19, fontSize: 11, borderBottom: '1px solid #e5e7eb' }}>В заказ</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.length === 0 && (
                                        <tr><td colSpan={30} style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-dim)' }}>
                                            Данные загружаются автоматически. Ожидайте синхронизации.
                                        </td></tr>
                                    )}
                                    {data.map((r, i) => {
                                        const rowBg = i % 2 === 0 ? '#ffffff' : '#f9fafb';
                                        return (
                                            <tr key={i} style={{ background: rowBg, color: '#111827' }}>
                                                <td style={{ position: 'sticky', left: 0, background: rowBg, color: '#111827', zIndex: 2, whiteSpace: 'nowrap', fontSize: 13, fontWeight: 500, minWidth: 100, padding: '8px 12px', borderRight: '1px solid #e5e7eb', borderBottom: '1px solid #f3f4f6', boxShadow: !detailed ? 'inset -6px 0 6px -6px rgba(0,0,0,0.05)' : 'none' }}>{r.date}</td>
                                                {detailed && <td style={{ position: 'sticky', left: 100, background: rowBg, color: '#111827', zIndex: 2, fontSize: 12, minWidth: 130, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 160, padding: '8px 12px', borderRight: '1px solid #e5e7eb', borderBottom: '1px solid #f3f4f6', boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.05)' }}>{r.vendor_code}</td>}
                                                {detailed && <td style={{ fontSize: 12, borderBottom: '1px solid #f3f4f6' }}><a href={`https://www.wildberries.ru/catalog/${r.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{r.nm_id}</a></td>}
                                                {detailed && <td style={{ fontSize: 12, borderBottom: '1px solid #f3f4f6' }}>{r.subject}</td>}
                                                {detailed && <td style={{ fontSize: 12, borderBottom: '1px solid #f3f4f6' }}>{r.brand}</td>}
                                                {/* Воронка */}
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', background: r.open_card > 300000 ? '#fffbeb' : undefined }}>{fmt(r.open_card)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', background: r.add_to_cart > 15000 ? '#eff6ff' : undefined }}>{fmt(r.add_to_cart)}</td>
                                                <td style={{ textAlign: 'right', fontWeight: 600, borderBottom: '1px solid #f3f4f6', background: r.orders_count > 2500 ? '#f0fdf4' : undefined }}>{fmt(r.orders_count)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', background: r.orders_sum_rub > 5000000 ? '#faf5ff' : undefined }}>{fmt(r.orders_sum_rub)}</td>
                                                <td style={{ textAlign: 'right', fontWeight: 500, borderBottom: '1px solid #f3f4f6', color: r.revenue > 0 ? '#111827' : '#ef4444' }}>{fmt(r.revenue)}</td>
                                                {/* Реклама */}
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', borderLeft: '1px solid #f3f4f6', color: r.adv_sum > 400000 ? '#ef4444' : r.adv_sum > 100000 ? '#f59e0b' : r.adv_sum > 0 ? '#f97316' : '#9ca3af', background: r.adv_sum > 400000 ? '#fef2f2' : undefined }}>{fmt(r.adv_sum)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.adv_views)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.adv_clicks)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.ctr > 5 ? '#10b981' : r.ctr > 2 ? '#374151' : '#f59e0b' }}>{fmtPct(r.ctr)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.cpc)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.cpm)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.drr > 30 ? '#ef4444' : r.drr > 15 ? '#f59e0b' : r.drr > 0 ? '#10b981' : '#9ca3af', fontWeight: r.drr > 30 ? 600 : 400, background: r.drr > 30 ? '#fef2f2' : undefined }}>{fmtPct(r.drr)}</td>
                                                {/* Финансы */}
                                                {detailed && <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', borderLeft: '1px solid #f3f4f6' }}>{r.cost_price ? fmt(r.cost_total) : <span style={{ color: '#f59e0b', fontSize: 11 }}>—</span>}</td>}
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', borderLeft: !detailed ? '1px solid #f3f4f6' : 'none', color: (r.spp_rate || 0) > 40 ? '#ef4444' : (r.spp_rate || 0) > 20 ? '#f59e0b' : '#10b981', fontSize: 12 }}>{r.spp_rate ? fmtPct(r.spp_rate) : '—'}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: '#374151', fontSize: 12 }}>{r.buyout_percent ? fmtPct(r.buyout_percent) : '—'}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: '#6b7280' }}>{fmt(r.tax)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.commission_rate > 0 ? '#6366f1' : '#9ca3af', fontSize: 12 }}>{r.commission_rate > 0 ? fmtPct(r.commission_rate) : '—'}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.commission > 0 ? '#6366f1' : '#9ca3af', fontWeight: 500 }}>{r.commission > 0 ? fmt(r.commission) : '—'}</td>
                                                <td style={{ textAlign: 'right', fontWeight: 700, borderBottom: '1px solid #f3f4f6', color: r.profit > 0 ? '#10b981' : '#ef4444', background: r.profit > 0 ? '#f0fdf4' : r.profit < 0 ? '#fef2f2' : undefined }}>{fmt(r.profit)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.margin > 20 ? '#10b981' : r.margin > 0 ? '#65a30d' : '#ef4444', fontWeight: r.margin > 20 ? 600 : 400 }}>{fmtPct(r.margin)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6' }}>{fmt(r.avg_price)}</td>
                                                {/* Конверсия */}
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', borderLeft: '1px solid #f3f4f6', color: r.add_to_cart_pct > 8 ? '#10b981' : r.add_to_cart_pct > 4 ? '#374151' : '#f59e0b' }}>{fmtPct(r.add_to_cart_pct)}</td>
                                                <td style={{ textAlign: 'right', borderBottom: '1px solid #f3f4f6', color: r.cart_to_order_pct > 15 ? '#10b981' : r.cart_to_order_pct > 8 ? '#374151' : '#f59e0b' }}>{fmtPct(r.cart_to_order_pct)}</td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                                </table>
                            )}
                        </div>
                        <div style={{ padding: '16px 20px', borderTop: '1px solid #e5e7eb', fontSize: 12, color: 'var(--color-text-dim)', background: '#f9fafb' }}>
                            Всего строк: {data.length} {!detailed && '(агрегация по дням)'}
                        </div>
                    </div>}
                </>
            )
            }

            {/* ─── Day Analysis tab ─── */}
            {tab === 'day-analysis' && (
                <DayAnalysisTab brand={brand} subject={subject} filters={filters} />
            )}

            {/* ─── Ads tab ─── */}
            {tab === 'ads' && (
                <AdsTab dateFrom={dateFrom} dateTo={dateTo} brand={brand} subject={subject} />
            )}

        </div >
    );
}
