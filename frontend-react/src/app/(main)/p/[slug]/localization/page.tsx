'use client';

import { useState, useEffect, useMemo, useCallback } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import type {
    LocalizationSummary,
    LocalizationSkuRow,
    DistrictBreakdown,
} from '@/types/api';
import {
    DISTRICT_ORDER,
    DISTRICT_LABELS,
    DISTRICT_COLORS,
} from '@/lib/constants/localization';
import Toast from '@/components/Toast';

type StatusKey = LocalizationSkuRow['status'];
type SortKey =
    | keyof Pick<
        LocalizationSkuRow,
        | 'vendor_code'
        | 'title'
        | 'subject'
        | 'local'
        | 'non_local'
        | 'total'
        | 'loc_pct'
        | 'ktr'
        | 'krp'
        | 'contribution'
    >
    | 'status';

const STATUS_META: Record<StatusKey, { label: string; cls: string; range: string; color: string }> = {
    excellent: { label: 'Отличная', cls: 'badge-success', range: 'КТР ≤ 0.90', color: 'var(--color-success)' },
    neutral: { label: 'Нейтральная', cls: 'badge-warning', range: 'КТР 1.00–1.05', color: 'var(--color-warning)' },
    weak: { label: 'Слабая', cls: 'badge-warning', range: 'КТР 1.10–1.30', color: '#c2410c' },
    critical: { label: 'Критическая', cls: 'badge-danger', range: 'КТР ≥ 1.40', color: 'var(--color-danger)' },
};

const PRESETS: { label: string; days: number }[] = [
    { label: '7 дней', days: 7 },
    { label: '14 дней', days: 14 },
    { label: '30 дней', days: 30 },
    { label: '91 день', days: 91 },
];

function ymd(d: Date): string {
    return d.toISOString().slice(0, 10);
}

/** Найти DistrictBreakdown по ключу в массиве `districts`, fallback — нули. */
function findDistrict(arr: DistrictBreakdown[], key: string): DistrictBreakdown | null {
    return arr.find(d => d.district === key) ?? null;
}

export default function LocalizationPage() {
    const [summary, setSummary] = useState<LocalizationSummary | null>(null);
    const [rows, setRows] = useState<LocalizationSkuRow[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');

    const [statusFilter, setStatusFilter] = useState<StatusKey | ''>('');
    const [brandFilter, setBrandFilter] = useState('');
    const [subjectFilter, setSubjectFilter] = useState('');
    const [search, setSearch] = useState('');

    const [sortKey, setSortKey] = useState<SortKey>('contribution');
    const [sortDesc, setSortDesc] = useState(true);

    const [syncing, setSyncing] = useState(false);
    const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' | 'info' } | null>(null);

    // Default range: 91 days back from today
    useEffect(() => {
        const now = new Date();
        const to = ymd(now);
        const from = ymd(new Date(now.getTime() - 91 * 86400000));
        setDateFrom(from);
        setDateTo(to);
    }, []);

    const loadData = useCallback(async () => {
        if (!dateFrom || !dateTo) return;
        try {
            setLoading(true);
            setError(null);
            const [s, sk] = await Promise.all([
                api.getLocalizationSummary(dateFrom, dateTo),
                api.getLocalizationSkus(dateFrom, dateTo),
            ]);
            setSummary(s);
            setRows(sk);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
            setSummary(null);
            setRows([]);
        } finally {
            setLoading(false);
        }
    }, [dateFrom, dateTo]);

    useEffect(() => { loadData(); }, [loadData]);

    const handleSync = useCallback(async () => {
        if (syncing) return;
        try {
            setSyncing(true);
            setToast({ msg: 'Синхронизация…', type: 'info' });
            const res = await api.syncLocalizationNow();
            setToast({
                msg: `Готово: получено ${formatNumber(res.total_fetched, 0)}, обновлено ${formatNumber(res.updated, 0)}`,
                type: 'success',
            });
            await loadData();
        } catch (e: unknown) {
            setToast({
                msg: e instanceof Error ? `Ошибка: ${e.message}` : 'Ошибка синхронизации',
                type: 'error',
            });
        } finally {
            setSyncing(false);
        }
    }, [syncing, loadData]);

    const applyPreset = (days: number) => {
        const now = new Date();
        setDateTo(ymd(now));
        setDateFrom(ymd(new Date(now.getTime() - days * 86400000)));
    };

    const brands = useMemo(() => {
        const s = new Set<string>();
        rows.forEach(r => { if (r.brand) s.add(r.brand); });
        return Array.from(s).sort();
    }, [rows]);

    const subjects = useMemo(() => {
        const s = new Set<string>();
        rows.forEach(r => { if (r.subject) s.add(r.subject); });
        return Array.from(s).sort();
    }, [rows]);

    /** Какие округа реально присутствуют хотя бы у одного SKU — те и показываем. */
    const visibleDistricts = useMemo(() => {
        const present = new Set<string>();
        rows.forEach(r => (r.districts ?? []).forEach(d => present.add(d.district)));
        return DISTRICT_ORDER.filter(k => present.has(k));
    }, [rows]);

    /** Признак наличия данных по округам (для empty-state в правой зоне). */
    const districtsAvailable = visibleDistricts.length > 0;

    const filteredRows = useMemo(() => {
        let out = rows;
        if (statusFilter) out = out.filter(r => r.status === statusFilter);
        if (brandFilter) out = out.filter(r => r.brand === brandFilter);
        if (subjectFilter) out = out.filter(r => r.subject === subjectFilter);
        if (search) {
            const q = search.toLowerCase();
            out = out.filter(r =>
                r.vendor_code.toLowerCase().includes(q) ||
                r.title.toLowerCase().includes(q),
            );
        }
        const sorted = [...out].sort((a, b) => {
            const av = a[sortKey];
            const bv = b[sortKey];
            if (typeof av === 'number' && typeof bv === 'number') {
                return sortDesc ? bv - av : av - bv;
            }
            const as = String(av);
            const bs = String(bv);
            return sortDesc ? bs.localeCompare(as) : as.localeCompare(bs);
        });
        return sorted;
    }, [rows, statusFilter, brandFilter, subjectFilter, search, sortKey, sortDesc]);

    const handleSort = (col: SortKey) => {
        if (sortKey === col) setSortDesc(!sortDesc);
        else { setSortKey(col); setSortDesc(true); }
    };

    const handleExport = () => {
        if (!filteredRows.length) return;
        exportToExcel(
            filteredRows.map(r => {
                const base: Record<string, string | number> = {
                    'Артикул': r.vendor_code,
                    'Название': r.title,
                    'Предмет': r.subject,
                    'Бренд': r.brand,
                    'ВБ Лок (РФ)': r.local,
                    'ВБ Нелок (РФ)': r.non_local,
                    'Всего WB (РФ)': r.total,
                    '% лок': r.loc_pct,
                    'КТР': r.ktr,
                    'КРП %': r.krp,
                    'Вклад шт×КТР': r.contribution,
                    'Статус': STATUS_META[r.status].label,
                };
                visibleDistricts.forEach(k => {
                    const d = findDistrict(r.districts, k);
                    const lbl = DISTRICT_LABELS[k] ?? k;
                    base[`${lbl} — Лок`] = d?.local ?? 0;
                    base[`${lbl} — Нелок`] = d?.non_local ?? 0;
                    base[`${lbl} — Всего`] = d?.total ?? 0;
                    base[`${lbl} — % лок`] = d?.local_pct ?? 0;
                });
                return base;
            }),
            'localization_index',
        );
    };

    const sortArrow = (col: SortKey) => sortKey === col ? (sortDesc ? ' ↓' : ' ↑') : '';

    /** Доля артикулов c КРП = 0 / > 0 — для блока «АРТИКУЛЫ ПО КРП». */
    const krpStats = useMemo(() => {
        if (!summary) return null;
        const total = summary.articles_count || 1;
        return {
            localCount: summary.articles_local_count,
            criticalCount: summary.articles_critical_count,
            localPct: (summary.articles_local_count / total) * 100,
            criticalPct: (summary.articles_critical_count / total) * 100,
        };
    }, [summary]);

    /** Sticky-итог: суммируем только по filteredRows (учитывая фильтры). */
    const filteredTotals = useMemo(() => {
        if (!filteredRows.length) return null;
        let local = 0, nonLocal = 0, total = 0, contribution = 0;
        const districtAgg: Record<string, { local: number; non_local: number; total: number }> = {};
        filteredRows.forEach(r => {
            local += r.local;
            nonLocal += r.non_local;
            total += r.total;
            contribution += r.contribution;
            (r.districts ?? []).forEach(d => {
                if (!districtAgg[d.district]) districtAgg[d.district] = { local: 0, non_local: 0, total: 0 };
                districtAgg[d.district].local += d.local;
                districtAgg[d.district].non_local += d.non_local;
                districtAgg[d.district].total += d.total;
            });
        });
        return {
            local, nonLocal, total, contribution,
            locPct: total > 0 ? (local / total) * 100 : 0,
            districts: districtAgg,
        };
    }, [filteredRows]);

    return (
        <div className="page-container">
            <style>{`
                .loc-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 1rem; margin-bottom: 1.5rem; }
                .loc-header h1 { font-size: 1.75rem; font-weight: 700; margin: 0; letter-spacing: -0.03em; }
                .loc-header-actions { display: flex; gap: 8px; }

                .loc-controls { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-bottom: 1.5rem; }
                .loc-controls input, .loc-controls select { padding: 0.5rem 0.75rem; border-radius: 8px; border: 1px solid var(--color-border); background: var(--color-bg-card); color: inherit; font-size: 0.85rem; }
                .loc-presets { display: flex; gap: 4px; }
                .loc-preset { padding: 0.4rem 0.75rem; border-radius: 8px; border: 1px solid var(--color-border); background: transparent; color: var(--color-text-muted); font-size: 0.8rem; cursor: pointer; transition: all 0.2s; }
                .loc-preset:hover { background: rgba(0, 113, 227, 0.05); color: var(--color-accent); }
                .loc-preset.active { background: var(--color-accent); color: white; border-color: var(--color-accent); }

                /* Top-блок: левая (KPI стопкой) + правая (легенда + КРП) */
                .loc-top { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 1.5rem; }
                @media (max-width: 1100px) { .loc-top { grid-template-columns: 1fr; } }

                .loc-kpi-stack { display: flex; flex-direction: column; gap: 0; }
                .loc-kpi-row { display: flex; align-items: center; justify-content: space-between; padding: 14px 20px; border-bottom: 1px solid var(--color-border); }
                .loc-kpi-row:last-child { border-bottom: none; }
                .loc-kpi-row-label { font-size: 13px; color: var(--color-text-muted); font-weight: 500; }
                .loc-kpi-row-value { font-size: 22px; font-weight: 700; letter-spacing: -0.02em; }
                .loc-kpi-row-value.h2 { font-size: 28px; }

                .loc-right-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
                @media (max-width: 700px) { .loc-right-grid { grid-template-columns: 1fr; } }

                .loc-box { padding: 16px 18px; }
                .loc-box-title { font-size: 11px; font-weight: 700; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 12px; }
                .loc-legend-list { display: flex; flex-direction: column; gap: 8px; }
                .loc-legend-item { display: flex; align-items: center; gap: 8px; font-size: 12px; }
                .loc-legend-pill { display: inline-flex; align-items: center; padding: 2px 10px; border-radius: 24px; font-size: 11px; font-weight: 600; color: white; flex-shrink: 0; }
                .loc-legend-meta { color: var(--color-text-muted); font-size: 11px; }
                .loc-legend-footer { font-size: 11px; color: var(--color-text-muted); margin-top: 12px; padding-top: 8px; border-top: 1px solid var(--color-border); }

                .loc-krp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
                .loc-krp-cell { padding: 10px 12px; border-radius: 10px; }
                .loc-krp-cell.green { background: rgba(52, 199, 89, 0.1); color: var(--color-success); }
                .loc-krp-cell.red { background: rgba(255, 59, 48, 0.1); color: var(--color-danger); }
                .loc-krp-num { font-size: 20px; font-weight: 700; line-height: 1.1; }
                .loc-krp-label { font-size: 11px; font-weight: 600; opacity: 0.85; margin-top: 2px; }
                .loc-krp-pct { font-size: 11px; opacity: 0.7; margin-top: 2px; }
                .loc-krp-footer { font-size: 11px; color: var(--color-text-muted); margin-top: 12px; padding-top: 8px; border-top: 1px solid var(--color-border); }

                .loc-search-bar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }
                .loc-search-bar input { flex: 1; min-width: 240px; padding: 0.5rem 0.75rem; border-radius: 8px; border: 1px solid var(--color-border); background: var(--color-bg-card); color: inherit; font-size: 0.85rem; }
                .loc-search-bar select { padding: 0.5rem 0.75rem; border-radius: 8px; border: 1px solid var(--color-border); background: var(--color-bg-card); color: inherit; font-size: 0.85rem; }

                /* Таблица */
                .loc-table-wrap { overflow-x: auto; border-radius: 12px; }
                .loc-table { border-collapse: separate; border-spacing: 0; font-size: 12px; min-width: 1200px; }
                .loc-table th { text-align: left; padding: 0.55rem 0.7rem; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-muted); border-bottom: 1px solid var(--color-border); user-select: none; white-space: nowrap; background: #f5f5f7; }
                .loc-table th.sortable { cursor: pointer; }
                .loc-table th.sortable:hover { color: var(--color-accent); }
                .loc-table th.num, .loc-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
                .loc-table th.center, .loc-table td.center { text-align: center; }
                .loc-table td { padding: 0.5rem 0.7rem; border-bottom: 1px solid var(--color-border); white-space: nowrap; }
                .loc-table tbody tr.row-default:hover td { background: rgba(0, 113, 227, 0.04); }
                .loc-table tbody tr.row-krp td { background: rgba(255, 59, 48, 0.04); color: rgb(159, 18, 57); }
                .loc-table tbody tr.row-krp:hover td { background: rgba(255, 59, 48, 0.08); }

                .loc-title-cell { max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
                .loc-ktr-cell { font-weight: 600; }
                .loc-status-pill { display: inline-block; padding: 2px 10px; border-radius: 24px; font-size: 11px; font-weight: 600; color: white; }

                /* Sticky-итог row */
                .loc-table tbody tr.totals-row { position: sticky; top: 0; z-index: 2; }
                .loc-table tbody tr.totals-row td { background: #eef2ff; font-weight: 700; border-bottom: 2px solid var(--color-border); }

                /* District group headers */
                .loc-district-group { color: white; text-align: center; padding: 6px 8px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; font-size: 10px; }
                .loc-district-sub { background: #fafafa; font-size: 10px; text-align: center; padding: 4px 6px; }

                .loc-section-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; color: var(--color-text); }

                .skel-row { height: 32px; background: linear-gradient(90deg, rgba(0,0,0,0.04), rgba(0,0,0,0.08), rgba(0,0,0,0.04)); background-size: 200% 100%; border-radius: 6px; margin-bottom: 6px; animation: skel 1.4s ease-in-out infinite; }
                @keyframes skel { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
                .loc-error { padding: 16px; color: var(--color-danger); }
                .loc-error button { margin-left: 12px; }
            `}</style>

            <div className="loc-header">
                <h1>Индекс локализации</h1>
                <div className="loc-header-actions">
                    <button
                        className="btn btn-primary btn-sm"
                        onClick={handleSync}
                        disabled={syncing}
                        title="Запустить синхронизацию заказов WB и пересчитать индекс"
                    >
                        {syncing ? 'Обновляю…' : 'Обновить данные'}
                    </button>
                    <button className="btn btn-secondary btn-sm" onClick={handleExport} disabled={!filteredRows.length}>
                        Excel
                    </button>
                </div>
            </div>

            <div className="loc-controls">
                <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
                <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} />
                <div className="loc-presets">
                    {PRESETS.map(p => {
                        const now = new Date();
                        const expectedFrom = ymd(new Date(now.getTime() - p.days * 86400000));
                        const isActive = dateTo === ymd(now) && dateFrom === expectedFrom;
                        return (
                            <button
                                key={p.days}
                                className={`loc-preset${isActive ? ' active' : ''}`}
                                onClick={() => applyPreset(p.days)}
                            >{p.label}</button>
                        );
                    })}
                </div>
            </div>

            {error && (
                <div className="glass-card loc-error">
                    Не удалось загрузить: {error}
                    <button className="btn btn-secondary btn-sm" onClick={loadData}>Повторить</button>
                </div>
            )}

            {loading && !summary && (
                <div className="glass-card" style={{ padding: 24 }}>
                    <div className="skel-row" style={{ height: 80, marginBottom: 16 }} />
                    {Array.from({ length: 8 }).map((_, i) => (
                        <div key={i} className="skel-row" />
                    ))}
                </div>
            )}

            {!loading && !error && summary && summary.total_orders === 0 && (
                <div className="empty-state">
                    <div className="empty-state-icon">📊</div>
                    <div className="empty-state-text">
                        Нет данных за выбранный период. Запустите синхронизацию воронки.
                    </div>
                </div>
            )}

            {summary && summary.total_orders > 0 && (
                <>
                    {/* Top block: 2-col grid */}
                    <div className="loc-top">
                        {/* LEFT: KPI stack */}
                        <div className="glass-card loc-kpi-stack">
                            <div className="loc-kpi-row">
                                <div className="loc-kpi-row-label">Индекс Локализации (ИЛ)</div>
                                <div className="loc-kpi-row-value h2" style={{ color: 'var(--color-accent)' }}>
                                    {formatNumber(summary.localization_index, 2)}
                                </div>
                            </div>
                            <div className="loc-kpi-row">
                                <div className="loc-kpi-row-label">Индекс Распределения Продаж (ИРП)</div>
                                <div className="loc-kpi-row-value h2" style={{ color: '#7c3aed' }}>
                                    {formatNumber(summary.irp_percent, 2)}%
                                </div>
                            </div>
                            <div className="loc-kpi-row">
                                <div className="loc-kpi-row-label">Локальных WB (РФ)</div>
                                <div className="loc-kpi-row-value">
                                    {formatNumber(summary.local_orders, 0)}
                                </div>
                            </div>
                            <div className="loc-kpi-row">
                                <div className="loc-kpi-row-label">Нелокальных WB (РФ)</div>
                                <div className="loc-kpi-row-value" style={{ color: 'var(--color-danger)' }}>
                                    {formatNumber(summary.non_local_orders, 0)}
                                </div>
                            </div>
                            <div className="loc-kpi-row">
                                <div className="loc-kpi-row-label">% локализации (всего)</div>
                                <div className="loc-kpi-row-value">
                                    {formatNumber(summary.loc_pct_overall, 1)}%
                                </div>
                            </div>
                            <div className="loc-kpi-row">
                                <div className="loc-kpi-row-label">Артикулов в расчёте</div>
                                <div className="loc-kpi-row-value">
                                    {formatNumber(summary.articles_count, 0)}
                                </div>
                            </div>
                        </div>

                        {/* RIGHT: legend + KRP boxes */}
                        <div className="loc-right-grid">
                            <div className="glass-card loc-box">
                                <div className="loc-box-title">Легенда</div>
                                <div className="loc-legend-list">
                                    {(['excellent', 'neutral', 'weak', 'critical'] as StatusKey[]).map(k => {
                                        const meta = STATUS_META[k];
                                        return (
                                            <div key={k} className="loc-legend-item">
                                                <span className="loc-legend-pill" style={{ background: meta.color }}>
                                                    {meta.range}
                                                </span>
                                                <span style={{ fontWeight: 600 }}>{meta.label}</span>
                                            </div>
                                        );
                                    })}
                                </div>
                                <div className="loc-legend-footer">
                                    КТР: новые значения с 23.03.2026
                                </div>
                            </div>

                            <div className="glass-card loc-box">
                                <div className="loc-box-title">Артикулы по КРП</div>
                                <div className="loc-krp-grid">
                                    <div className="loc-krp-cell green">
                                        <div className="loc-krp-num">{formatNumber(krpStats?.localCount, 0)}</div>
                                        <div className="loc-krp-label">КРП = 0</div>
                                        <div className="loc-krp-pct">{formatNumber(krpStats?.localPct, 1)}% от всех</div>
                                    </div>
                                    <div className="loc-krp-cell red">
                                        <div className="loc-krp-num">{formatNumber(krpStats?.criticalCount, 0)}</div>
                                        <div className="loc-krp-label">КРП &gt; 0</div>
                                        <div className="loc-krp-pct">{formatNumber(krpStats?.criticalPct, 1)}% от всех</div>
                                    </div>
                                </div>
                                <div className="loc-krp-footer">
                                    Всего: {formatNumber(summary.articles_count, 0)} арт. | {formatNumber(krpStats?.localPct, 0)}% локализованы | {formatNumber(krpStats?.criticalPct, 0)}% с надбавкой
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Filters */}
                    <div className="glass-card" style={{ padding: 20 }}>
                        <div className="loc-section-title">
                            Артикулы ({formatNumber(filteredRows.length, 0)} из {formatNumber(rows.length, 0)})
                        </div>
                        <div className="loc-search-bar">
                            <input
                                placeholder="Поиск по артикулу или названию..."
                                value={search}
                                onChange={e => setSearch(e.target.value)}
                            />
                            <select value={statusFilter} onChange={e => setStatusFilter(e.target.value as StatusKey | '')}>
                                <option value="">Все статусы</option>
                                <option value="excellent">Отличные</option>
                                <option value="neutral">Нейтральные</option>
                                <option value="weak">Слабые</option>
                                <option value="critical">Критические</option>
                            </select>
                            <select value={brandFilter} onChange={e => setBrandFilter(e.target.value)}>
                                <option value="">Все бренды</option>
                                {brands.map(b => <option key={b} value={b}>{b}</option>)}
                            </select>
                            <select value={subjectFilter} onChange={e => setSubjectFilter(e.target.value)}>
                                <option value="">Все предметы</option>
                                {subjects.map(s => <option key={s} value={s}>{s}</option>)}
                            </select>
                        </div>

                        {/* Table */}
                        <div className="loc-table-wrap">
                            <table className="loc-table" data-testid="localization-table">
                                <thead>
                                    {/* Group-headers row (only district group spans 4 sub-cols each) */}
                                    {districtsAvailable && (
                                        <tr>
                                            <th colSpan={11} style={{ background: '#f5f5f7' }} />
                                            {visibleDistricts.map(k => (
                                                <th
                                                    key={`grp-${k}`}
                                                    colSpan={4}
                                                    className="loc-district-group"
                                                    style={{ background: DISTRICT_COLORS[k] ?? '#475569' }}
                                                >
                                                    {DISTRICT_LABELS[k] ?? k}
                                                </th>
                                            ))}
                                            <th style={{ background: '#f5f5f7' }} />
                                        </tr>
                                    )}
                                    <tr>
                                        <th className="sortable" onClick={() => handleSort('vendor_code')}>Артикул{sortArrow('vendor_code')}</th>
                                        <th className="sortable" onClick={() => handleSort('title')}>Название{sortArrow('title')}</th>
                                        <th className="sortable" onClick={() => handleSort('subject')}>Предмет{sortArrow('subject')}</th>
                                        <th className="sortable num" onClick={() => handleSort('local')}>ВБ Лок.{sortArrow('local')}</th>
                                        <th className="sortable num" onClick={() => handleSort('non_local')}>ВБ Нелок.{sortArrow('non_local')}</th>
                                        <th className="sortable num" onClick={() => handleSort('total')}>Всего WB (РФ){sortArrow('total')}</th>
                                        <th className="sortable num" onClick={() => handleSort('loc_pct')}>% лок.{sortArrow('loc_pct')}</th>
                                        <th className="sortable num" onClick={() => handleSort('ktr')}>КТР{sortArrow('ktr')}</th>
                                        <th className="sortable num" onClick={() => handleSort('krp')}>КРП, %{sortArrow('krp')}</th>
                                        <th className="sortable num" onClick={() => handleSort('contribution')}>Вклад шт×КТР{sortArrow('contribution')}</th>
                                        <th className="sortable" onClick={() => handleSort('status')}>Статус{sortArrow('status')}</th>
                                        {visibleDistricts.flatMap(k => [
                                            <th key={`${k}-l`} className="loc-district-sub num">Лок.</th>,
                                            <th key={`${k}-n`} className="loc-district-sub num">Нелок.</th>,
                                            <th key={`${k}-t`} className="loc-district-sub num">Всего</th>,
                                            <th key={`${k}-p`} className="loc-district-sub num">% лок.</th>,
                                        ])}
                                        <th className="sortable num" onClick={() => handleSort('contribution')}>Вклад в ИЛ{sortArrow('contribution')}</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {/* Sticky totals row */}
                                    {filteredTotals && (
                                        <tr className="totals-row" data-testid="totals-row">
                                            <td>ИТОГО</td>
                                            <td colSpan={2} style={{ color: 'var(--color-text-muted)' }}>
                                                {formatNumber(filteredRows.length, 0)} арт.
                                            </td>
                                            <td className="num">{formatNumber(filteredTotals.local, 0)}</td>
                                            <td className="num">{formatNumber(filteredTotals.nonLocal, 0)}</td>
                                            <td className="num">{formatNumber(filteredTotals.total, 0)}</td>
                                            <td className="num">{formatNumber(filteredTotals.locPct, 1)}%</td>
                                            <td className="num">{formatNumber(summary.localization_index, 2)}</td>
                                            <td className="num">{formatNumber(summary.irp_percent, 2)}%</td>
                                            <td className="num">{formatNumber(filteredTotals.contribution, 1)}</td>
                                            <td>—</td>
                                            {visibleDistricts.flatMap(k => {
                                                const d = filteredTotals.districts[k];
                                                const total = d?.total ?? 0;
                                                const local = d?.local ?? 0;
                                                const pct = total > 0 ? (local / total) * 100 : 0;
                                                return [
                                                    <td key={`tot-${k}-l`} className="num">{formatNumber(local, 0)}</td>,
                                                    <td key={`tot-${k}-n`} className="num">{formatNumber(d?.non_local ?? 0, 0)}</td>,
                                                    <td key={`tot-${k}-t`} className="num">{formatNumber(total, 0)}</td>,
                                                    <td key={`tot-${k}-p`} className="num">{formatNumber(pct, 1)}%</td>,
                                                ];
                                            })}
                                            <td className="num">{formatNumber(filteredTotals.contribution, 1)}</td>
                                        </tr>
                                    )}

                                    {filteredRows.length === 0 ? (
                                        <tr>
                                            <td
                                                colSpan={12 + visibleDistricts.length * 4}
                                                style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-muted)' }}
                                            >
                                                Нет данных за выбранный период
                                            </td>
                                        </tr>
                                    ) : filteredRows.slice(0, 1000).map(r => {
                                        const meta = STATUS_META[r.status];
                                        const rowCls = r.krp > 0 ? 'row-krp' : 'row-default';
                                        const noDistricts = !r.districts || r.districts.length === 0;
                                        return (
                                            <tr key={r.nm_id} className={rowCls}>
                                                <td>{r.vendor_code}</td>
                                                <td className="loc-title-cell" title={r.title}>{r.title}</td>
                                                <td style={{ color: 'var(--color-text-muted)' }}>{r.subject}</td>
                                                <td className="num">{formatNumber(r.local, 0)}</td>
                                                <td className="num">{formatNumber(r.non_local, 0)}</td>
                                                <td className="num">{formatNumber(r.total, 0)}</td>
                                                <td className="num">{formatNumber(r.loc_pct, 1)}%</td>
                                                <td className="num loc-ktr-cell" style={{ color: meta.color }}>
                                                    {formatNumber(r.ktr, 2)}
                                                </td>
                                                <td className="num">{formatNumber(r.krp, 2)}%</td>
                                                <td className="num">{formatNumber(r.contribution, 1)}</td>
                                                <td>
                                                    <span className="loc-status-pill" style={{ background: meta.color }}>
                                                        {meta.label}
                                                    </span>
                                                </td>
                                                {visibleDistricts.flatMap(k => {
                                                    if (noDistricts) {
                                                        return [
                                                            <td
                                                                key={`${r.nm_id}-${k}-empty`}
                                                                colSpan={4}
                                                                className="num"
                                                                style={{ color: 'var(--color-text-muted)', textAlign: 'center' }}
                                                                title="Sync wb_orders ещё не запускался — нажмите Обновить данные"
                                                            >
                                                                —
                                                            </td>,
                                                        ];
                                                    }
                                                    const d = findDistrict(r.districts, k);
                                                    return [
                                                        <td key={`${r.nm_id}-${k}-l`} className="num">{formatNumber(d?.local ?? 0, 0)}</td>,
                                                        <td key={`${r.nm_id}-${k}-n`} className="num">{formatNumber(d?.non_local ?? 0, 0)}</td>,
                                                        <td key={`${r.nm_id}-${k}-t`} className="num">{formatNumber(d?.total ?? 0, 0)}</td>,
                                                        <td key={`${r.nm_id}-${k}-p`} className="num">{formatNumber(d?.local_pct ?? 0, 1)}%</td>,
                                                    ];
                                                })}
                                                <td className="num" style={{ fontWeight: 600 }}>
                                                    {formatNumber(r.contribution, 1)}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                        {filteredRows.length > 1000 && (
                            <div style={{ marginTop: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>
                                Показаны первые 1000 из {formatNumber(filteredRows.length, 0)}. Используйте фильтры для уточнения.
                            </div>
                        )}
                    </div>
                </>
            )}

            {toast && (
                <Toast
                    message={toast.msg}
                    type={toast.type}
                    onClose={() => setToast(null)}
                    duration={toast.type === 'info' ? 8000 : 4000}
                />
            )}
        </div>
    );
}
