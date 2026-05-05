'use client';

import { useState, useEffect, useMemo, useCallback } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import type { LocalizationSummary, LocalizationSkuRow } from '@/types/api';

type StatusKey = LocalizationSkuRow['status'];
type SortKey = keyof Pick<
    LocalizationSkuRow,
    'vendor_code' | 'title' | 'subject' | 'local' | 'non_local' | 'total' | 'loc_pct' | 'ktr' | 'krp' | 'contribution'
> | 'status';

const STATUS_META: Record<StatusKey, { label: string; cls: string; range: string }> = {
    excellent: { label: 'Отличная', cls: 'badge-success', range: 'КТР ≤ 0.90' },
    neutral: { label: 'Нейтральная', cls: 'badge-secondary', range: 'КТР 1.00–1.05' },
    weak: { label: 'Слабая', cls: 'badge-warning', range: 'КТР 1.10–1.30' },
    critical: { label: 'Критическая', cls: 'badge-danger', range: 'КТР ≥ 1.40' },
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

function statusColor(status: StatusKey): string {
    switch (status) {
        case 'excellent': return 'var(--color-success)';
        case 'neutral': return 'var(--color-text-muted)';
        case 'weak': return 'var(--color-warning)';
        case 'critical': return 'var(--color-danger)';
    }
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
            filteredRows.map(r => ({
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
            })),
            'localization_index',
        );
    };

    const sortArrow = (col: SortKey) => sortKey === col ? (sortDesc ? ' ↓' : ' ↑') : '';

    return (
        <div className="page-container">
            <style>{`
                .loc-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 1rem; margin-bottom: 1.5rem; }
                .loc-header h1 { font-size: 1.75rem; font-weight: 700; margin: 0; letter-spacing: -0.03em; }
                .loc-controls { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-bottom: 1.5rem; }
                .loc-controls input, .loc-controls select { padding: 0.5rem 0.75rem; border-radius: 8px; border: 1px solid var(--color-border); background: var(--color-bg-card); color: inherit; font-size: 0.85rem; }
                .loc-presets { display: flex; gap: 4px; }
                .loc-preset { padding: 0.4rem 0.75rem; border-radius: 8px; border: 1px solid var(--color-border); background: transparent; color: var(--color-text-muted); font-size: 0.8rem; cursor: pointer; transition: all 0.2s; }
                .loc-preset:hover { background: rgba(0, 113, 227, 0.05); color: var(--color-accent); }
                .loc-preset.active { background: var(--color-accent); color: white; border-color: var(--color-accent); }
                .loc-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 1.5rem; }
                .loc-kpi { padding: 20px; }
                .loc-kpi-label { font-size: 12px; font-weight: 600; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
                .loc-kpi-value { font-size: 32px; font-weight: 700; letter-spacing: -0.03em; line-height: 1; }
                .loc-kpi-sub { font-size: 12px; color: var(--color-text-muted); margin-top: 6px; }
                .loc-legend { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 1.5rem; padding: 16px; }
                .loc-legend-item { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; }
                .loc-legend-dot { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }
                .loc-table { width: 100%; border-collapse: collapse; font-size: 13px; }
                .loc-table th { text-align: left; padding: 0.6rem 0.75rem; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-muted); border-bottom: 1px solid var(--color-border); cursor: pointer; user-select: none; white-space: nowrap; }
                .loc-table th:hover { color: var(--color-accent); }
                .loc-table th.num, .loc-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
                .loc-table td { padding: 0.55rem 0.75rem; border-bottom: 1px solid var(--color-border); }
                .loc-table tbody tr:hover td { background: rgba(0, 113, 227, 0.04); }
                .loc-title { max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
                .loc-ktr { font-weight: 600; }
                .loc-search-bar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }
                .loc-search-bar input { flex: 1; min-width: 240px; }
                .skel-row { height: 32px; background: linear-gradient(90deg, rgba(0,0,0,0.04), rgba(0,0,0,0.08), rgba(0,0,0,0.04)); background-size: 200% 100%; border-radius: 6px; margin-bottom: 6px; animation: skel 1.4s ease-in-out infinite; }
                @keyframes skel { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
                .loc-error { padding: 16px; color: var(--color-danger); }
                .loc-error button { margin-left: 12px; }
                .loc-section-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; color: var(--color-text); }
                .loc-pill-row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 1.5rem; }
                .loc-pill { padding: 10px 16px; border-radius: 24px; font-size: 13px; font-weight: 600; }
                .loc-pill.green { background: rgba(52, 199, 89, 0.12); color: var(--color-success); }
                .loc-pill.red { background: rgba(255, 59, 48, 0.12); color: var(--color-danger); }
            `}</style>

            <div className="loc-header">
                <h1>Индекс локализации</h1>
                <button className="btn btn-secondary btn-sm" onClick={handleExport} disabled={!filteredRows.length}>
                    📥 Excel
                </button>
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
                        За период нет данных. Запустите синхронизацию воронки.
                    </div>
                </div>
            )}

            {summary && summary.total_orders > 0 && (
                <>
                    <div className="loc-kpis">
                        <div className="glass-card loc-kpi">
                            <div className="loc-kpi-label">Индекс локализации (ИЛ)</div>
                            <div className="loc-kpi-value" style={{ color: 'var(--color-accent)' }}>
                                {formatNumber(summary.localization_index, 2)}
                            </div>
                            <div className="loc-kpi-sub">средневзвеш. КТР</div>
                        </div>
                        <div className="glass-card loc-kpi">
                            <div className="loc-kpi-label">Индекс распределения продаж (ИРП)</div>
                            <div className="loc-kpi-value" style={{ color: 'var(--color-warning)' }}>
                                {formatNumber(summary.irp_percent, 2)}%
                            </div>
                            <div className="loc-kpi-sub">средневзвеш. КРП</div>
                        </div>
                        <div className="glass-card loc-kpi">
                            <div className="loc-kpi-label">Локальных заказов WB (РФ)</div>
                            <div className="loc-kpi-value" style={{ color: 'var(--color-success)' }}>
                                {formatNumber(summary.local_orders, 0)}
                            </div>
                            <div className="loc-kpi-sub">{formatNumber(summary.loc_pct_overall, 1)}% от общего</div>
                        </div>
                        <div className="glass-card loc-kpi">
                            <div className="loc-kpi-label">Нелокальных</div>
                            <div className="loc-kpi-value" style={{ color: 'var(--color-danger)' }}>
                                {formatNumber(summary.non_local_orders, 0)}
                            </div>
                            <div className="loc-kpi-sub">{formatNumber(100 - summary.loc_pct_overall, 1)}% от общего</div>
                        </div>
                        <div className="glass-card loc-kpi">
                            <div className="loc-kpi-label">Всего FBW</div>
                            <div className="loc-kpi-value">
                                {formatNumber(summary.total_orders, 0)}
                            </div>
                            <div className="loc-kpi-sub">за период</div>
                        </div>
                        <div className="glass-card loc-kpi">
                            <div className="loc-kpi-label">Артикулов в расчёте</div>
                            <div className="loc-kpi-value">
                                {formatNumber(summary.articles_count, 0)}
                            </div>
                            <div className="loc-kpi-sub">SKU с продажами</div>
                        </div>
                    </div>

                    <div className="loc-pill-row">
                        <div className="loc-pill green">
                            КРП = 0: {formatNumber(summary.articles_local_count, 0)} артикулов
                        </div>
                        <div className="loc-pill red">
                            КРП &gt; 0: {formatNumber(summary.articles_critical_count, 0)} артикулов
                        </div>
                    </div>

                    <div className="glass-card loc-legend">
                        {(['excellent', 'neutral', 'weak', 'critical'] as StatusKey[]).map(k => (
                            <div key={k} className="loc-legend-item">
                                <span className="loc-legend-dot" style={{ background: statusColor(k) }} />
                                <strong>{STATUS_META[k].label}</strong>
                                <span style={{ color: 'var(--color-text-muted)' }}>{STATUS_META[k].range}</span>
                            </div>
                        ))}
                    </div>

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

                        <div style={{ overflowX: 'auto' }}>
                            <table className="loc-table">
                                <thead>
                                    <tr>
                                        <th onClick={() => handleSort('vendor_code')}>Артикул{sortArrow('vendor_code')}</th>
                                        <th onClick={() => handleSort('title')}>Название{sortArrow('title')}</th>
                                        <th onClick={() => handleSort('subject')}>Предмет{sortArrow('subject')}</th>
                                        <th className="num" onClick={() => handleSort('local')}>ВБ Лок (РФ){sortArrow('local')}</th>
                                        <th className="num" onClick={() => handleSort('non_local')}>ВБ Нелок (РФ){sortArrow('non_local')}</th>
                                        <th className="num" onClick={() => handleSort('total')}>Всего WB (РФ){sortArrow('total')}</th>
                                        <th className="num" onClick={() => handleSort('loc_pct')}>% лок{sortArrow('loc_pct')}</th>
                                        <th className="num" onClick={() => handleSort('ktr')}>КТР{sortArrow('ktr')}</th>
                                        <th className="num" onClick={() => handleSort('krp')}>КРП %{sortArrow('krp')}</th>
                                        <th className="num" onClick={() => handleSort('contribution')}>Вклад шт×КТР{sortArrow('contribution')}</th>
                                        <th onClick={() => handleSort('status')}>Статус{sortArrow('status')}</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {filteredRows.length === 0 ? (
                                        <tr>
                                            <td colSpan={11} style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-muted)' }}>
                                                Нет артикулов под фильтр
                                            </td>
                                        </tr>
                                    ) : filteredRows.slice(0, 1000).map(r => {
                                        const meta = STATUS_META[r.status];
                                        return (
                                            <tr key={r.nm_id}>
                                                <td>{r.vendor_code}</td>
                                                <td className="loc-title" title={r.title}>{r.title}</td>
                                                <td style={{ color: 'var(--color-text-muted)' }}>{r.subject}</td>
                                                <td className="num">{formatNumber(r.local, 0)}</td>
                                                <td className="num">{formatNumber(r.non_local, 0)}</td>
                                                <td className="num">{formatNumber(r.total, 0)}</td>
                                                <td className="num">{formatNumber(r.loc_pct, 1)}%</td>
                                                <td className="num loc-ktr" style={{ color: statusColor(r.status) }}>
                                                    {formatNumber(r.ktr, 2)}
                                                </td>
                                                <td className="num">{formatNumber(r.krp, 2)}%</td>
                                                <td className="num">{formatNumber(r.contribution, 1)}</td>
                                                <td>
                                                    <span className={`badge ${meta.cls}`}>{meta.label}</span>
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
        </div>
    );
}
