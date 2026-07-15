'use client';
import { Fragment, useEffect, useState, useCallback, useRef } from 'react';
import { api } from '@/lib/api';
import { exportToExcel } from '@/lib/utils';
import type { AdTabProduct, AdTabGroupRow, UnifiedSyncProgress } from '@/types/api';

const fmt = (n: number) => n?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '0';
const fmtPct = (n: number) => (n || 0).toFixed(2) + '%';
// drr === null — расход без заказов (ДРР = ∞): красим красным и показываем «∞»
const drrColor = (drr: number | null) => (drr === null || drr > 30 ? '#ef4444' : drr > 15 ? '#f59e0b' : '#22c55e');
const drrLabel = (drr: number | null) => (drr === null ? '∞' : fmtPct(drr));

const STATUS_MAP: Record<number, { label: string; color: string }> = {
    9: { label: 'Активна', color: '#22c55e' },
    11: { label: 'На паузе', color: '#f59e0b' },
    7: { label: 'Завершена', color: '#94a3b8' },
};

const ABC_COLORS: Record<string, string> = { A: '#22c55e', B: '#f59e0b', C: '#ef4444' };
const abcBadge = (v?: string) => {
    const letter = v || '—';
    const color = ABC_COLORS[letter] || '#94a3b8';
    return (
        <span style={{
            display: 'inline-block', padding: '2px 10px', borderRadius: 12,
            fontSize: 12, fontWeight: 700, background: color + '20', color,
        }}>{letter}</span>
    );
};

const TYPE_MAP: Record<string, string> = {
    cpm: 'CPM',
    cpc: 'CPC',
    unified: 'Авто',
};

/** Build human-readable progress string from UnifiedSyncProgress */
function formatProgress(p: UnifiedSyncProgress): string {
    switch (p.phase) {
        case 'campaigns':
            return 'Загрузка кампаний...';
        case 'budgets':
            return `Бюджеты: ${p.budgets_done || 0} / ${p.budgets_total || '?'}`;
        case 'funnel':
            return `Воронка: ${p.funnel_days_done || 0} / ${p.funnel_days_total || '?'} дней`;
        case 'done':
            return `✅ Готово: ${p.campaigns_total || 0} кампаний, ${p.funnel_days_done || 0} дней`;
        case 'error':
            return p.error || p.detail || 'Ошибка синхронизации';
        default:
            return '';
    }
}

interface AdsTabProps {
    dateFrom: string;
    dateTo: string;
    brand: string;
    subject: string;
}

type AdGroupBy = 'sku' | 'brand' | 'subject' | 'tag' | 'imt' | 'abc';
const GROUP_LABELS: Record<AdGroupBy, string> = {
    sku: 'По артикулам', brand: 'По брендам', subject: 'По категориям',
    tag: 'По ярлыкам', imt: 'По склейкам', abc: 'ABC анализ',
};

export function AdsTab({ dateFrom, dateTo, brand, subject }: AdsTabProps) {
    const [data, setData] = useState<AdTabProduct[]>([]);
    const [groupData, setGroupData] = useState<AdTabGroupRow[]>([]);
    const [groupBy, setGroupBy] = useState<AdGroupBy>('sku');
    const [loading, setLoading] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [progress, setProgress] = useState('');
    const [error, setError] = useState('');
    const [expandedNm, setExpandedNm] = useState<Set<number>>(new Set());
    const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
    const [expandedCamp, setExpandedCamp] = useState<Set<string>>(new Set());
    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const loadData = useCallback(async (gb?: AdGroupBy) => {
        if (!dateFrom || !dateTo) return;
        const mode = gb ?? groupBy;
        setLoading(true);
        setError('');
        try {
            if (mode === 'sku' || mode === 'abc') {
                const res = await api.getAdTab({ date_from: dateFrom, date_to: dateTo, brand, subject });
                setData(res);
                setGroupData([]);
            } else {
                const res = await api.getAdTab({ date_from: dateFrom, date_to: dateTo, brand, subject, group_by: mode });
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                setGroupData(res as any as AdTabGroupRow[]);
                setData([]);
            }
        } catch (e: unknown) {
            setError((e as Error).message || 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [dateFrom, dateTo, brand, subject, groupBy]);

    useEffect(() => { loadData(); }, [loadData]);

    // Cleanup poll on unmount
    useEffect(() => {
        return () => {
            if (pollRef.current) clearInterval(pollRef.current);
        };
    }, []);

    /** Process a progress response — returns true if polling should continue */
    const handleProgressUpdate = useCallback((p: UnifiedSyncProgress): boolean => {
        if (p.phase === 'idle') {
            return false;
        }
        if (p.phase === 'done') {
            setProgress(formatProgress(p));
            setSyncing(false);
            loadData();
            setTimeout(() => setProgress(''), 5000);
            return false;
        }
        if (p.phase === 'error') {
            setProgress('');
            setSyncing(false);
            setError(p.error || p.detail || 'Ошибка синхронизации');
            return false;
        }
        // Active phase
        setProgress(formatProgress(p));
        setSyncing(true);
        return true;
    }, [loadData]);

    /** Start polling unified sync progress */
    const startPoll = useCallback(() => {
        if (pollRef.current) clearInterval(pollRef.current);
        const poll = setInterval(async () => {
            try {
                const p = await api.getUnifiedSyncProgress();
                if (!handleProgressUpdate(p)) {
                    clearInterval(poll);
                    if (pollRef.current === poll) pollRef.current = null;
                }
            } catch { /* ignore poll errors */ }
        }, 5000);
        pollRef.current = poll;
        // Safety timeout: stop after 10 min
        setTimeout(() => {
            clearInterval(poll);
            if (pollRef.current === poll) {
                pollRef.current = null;
                setSyncing(false);
            }
        }, 600000);
    }, [handleProgressUpdate]);

    // On mount: check if unified sync is already running, resume polling
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const p = await api.getUnifiedSyncProgress();
                if (cancelled) return;
                if (p.phase && p.phase !== 'idle' && p.phase !== 'done' && p.phase !== 'error') {
                    setSyncing(true);
                    setProgress(formatProgress(p));
                    startPoll();
                }
            } catch { /* ignore */ }
        })();
        return () => { cancelled = true; };
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    const handleSync = async () => {
        if (!dateFrom || !dateTo) return;
        setSyncing(true);
        setProgress('Запуск...');
        setError('');
        try {
            await api.unifiedSync(dateFrom, dateTo);
            startPoll();
        } catch (e: unknown) {
            setError((e as Error).message || 'Ошибка синхронизации');
            setSyncing(false);
            setProgress('');
        }
    };

    const toggleExpand = (nmId: number) => {
        setExpandedNm(prev => {
            const next = new Set(prev);
            next.has(nmId) ? next.delete(nmId) : next.add(nmId);
            return next;
        });
    };

    const handleExport = () => {
        const rows = data.map(p => ({
            'Артикул': p.vendor_code || '',
            'Товар': p.subject || '',
            'Бренд': p.brand || '',
            'Просмотры': p.adv_views,
            'Клики': p.adv_clicks,
            'Расход ₽': p.adv_sum,
            'CTR %': p.ctr,
            'CPC ₽': p.cpc,
            'CPM ₽': p.cpm,
            'ДРР %': p.drr === null ? '∞ (заказов нет)' : p.drr,
            'Заказы': p.orders_count,
            'Сумма заказов ₽': p.orders_sum_rub,
            'Бюджет ₽': p.campaigns.reduce((s, c) => s + (c.budget || 0), 0),
        }));
        exportToExcel(rows, 'advertising');
    };

    // Summary calculations — from either data source
    const allItems = groupBy === 'sku' || groupBy === 'abc' ? data : groupData;
    const totalSpend = allItems.reduce((s, p) => s + p.adv_sum, 0);
    const totalViews = allItems.reduce((s, p) => s + p.adv_views, 0);
    const totalClicks = allItems.reduce((s, p) => s + p.adv_clicks, 0);
    const totalOrdersSum = allItems.reduce((s, p) => s + p.orders_sum_rub, 0);
    const avgDrr = totalOrdersSum ? (totalSpend / totalOrdersSum * 100) : 0;
    const hasData = (groupBy === 'sku' || groupBy === 'abc') ? data.length > 0 : groupData.length > 0;

    // Period length check
    const periodDays = dateFrom && dateTo
        ? Math.ceil((new Date(dateTo).getTime() - new Date(dateFrom).getTime()) / 86400000)
        : 0;
    const isLongPeriod = periodDays > 30;

    // Shared styles (matching funnel table design)
    const stickyCol: React.CSSProperties = { position: 'sticky', left: 0, zIndex: 2, borderRight: '1px solid #e5e7eb' };
    const thStyle: React.CSSProperties = { padding: '10px 8px', textAlign: 'right', fontSize: 11, fontWeight: 600, color: '#4b5563', borderBottom: '2px solid #e5e7eb', whiteSpace: 'nowrap', background: '#ffffff' };
    const tdStyle: React.CSSProperties = { padding: '8px 8px', borderBottom: '1px solid #f3f4f6', fontSize: 12 };
    const tdNum: React.CSSProperties = { ...tdStyle, textAlign: 'right', fontVariantNumeric: 'tabular-nums' };
    const campTd: React.CSSProperties = { padding: '6px 8px', borderBottom: '1px solid #e8ecf4', fontSize: 11, color: '#6b7280' };
    const campTdNum: React.CSSProperties = { ...campTd, textAlign: 'right', fontVariantNumeric: 'tabular-nums' };

    return (
        <div>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ margin: 0 }}>Реклама</h3>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <button className="btn btn-outline" onClick={handleExport} disabled={!data.length}>
                        📥 Excel
                    </button>
                    <button className="btn btn-primary" onClick={handleSync} disabled={syncing || !dateFrom || !dateTo}>
                        {syncing ? '⏳ Обновление...' : '🔄 Обновить'}
                    </button>
                    {progress && (
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{progress}</span>
                    )}
                </div>
            </div>

            {/* Group tabs */}
            <div style={{ display: 'flex', gap: 0, marginBottom: 16, flexWrap: 'wrap' }}>
                {(['sku', 'brand', 'subject', 'tag', 'imt', 'abc'] as const).map(mode => (
                    <button
                        key={mode}
                        onClick={() => { setGroupBy(mode); setExpandedGroups(new Set()); setExpandedNm(new Set()); setExpandedCamp(new Set()); loadData(mode); }}
                        style={{
                            padding: '8px 16px', fontSize: 13, fontWeight: groupBy === mode ? 600 : 400, cursor: 'pointer',
                            background: groupBy === mode ? 'var(--color-primary, #3b82f6)' : '#f3f4f6',
                            color: groupBy === mode ? '#fff' : '#374151',
                            border: 'none', borderRadius: 8, whiteSpace: 'nowrap',
                        }}
                    >{GROUP_LABELS[mode]}</button>
                ))}
            </div>

            {/* Summary cards */}
            {hasData && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12, marginBottom: 20 }}>
                    {[
                        { label: 'Расход рекл. ₽', value: fmt(totalSpend) },
                        { label: 'Просмотры', value: fmt(totalViews) },
                        { label: 'Клики', value: fmt(totalClicks) },
                        { label: 'ДРР %', value: fmtPct(avgDrr) },
                    ].map(c => (
                        <div key={c.label} className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                            <div style={{ fontSize: 22, fontWeight: 700 }}>{c.value}</div>
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>{c.label}</div>
                        </div>
                    ))}
                </div>
            )}

            {/* Long period warning */}
            {isLongPeriod && !syncing && (
                <div style={{ fontSize: 13, color: '#6366f1', background: 'rgba(99,102,241,0.08)', padding: '10px 16px', borderRadius: 8, marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span>📅 Период {periodDays} дней — загрузка займёт ~{Math.ceil(periodDays / 3)} мин. Рекомендуем до 30 дней.</span>
                    <button
                        className="btn btn-sm"
                        style={{ background: '#6366f1', color: '#fff', marginLeft: 12, whiteSpace: 'nowrap' }}
                        onClick={handleSync}
                    >
                        Загрузить в фоне
                    </button>
                </div>
            )}

            {/* Error */}
            {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}

            {/* Loading */}
            {loading && <div style={{ textAlign: 'center', padding: 40 }}>Загрузка...</div>}

            {/* Empty state */}
            {!loading && !error && !hasData && (
                <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Нет данных по рекламе за выбранный период
                </div>
            )}

            {/* Hint: sync campaigns if no campaigns linked */}
            {!loading && data.length > 0 && (groupBy === 'sku' || groupBy === 'abc') && data.every(p => p.campaigns.length === 0) && (
                <div style={{ fontSize: 13, color: '#f59e0b', background: 'rgba(245,158,11,0.08)', padding: '8px 16px', borderRadius: 8, marginBottom: 12 }}>
                    ⚠️ Кампании не синхронизированы. Нажмите «Обновить» чтобы загрузить названия и бюджеты рекламных кампаний из WB.
                </div>
            )}

            {/* ABC Analysis — grouped by A/B/C categories */}
            {!loading && data.length > 0 && groupBy === 'abc' && (() => {
                const abcGroups = (['A', 'B', 'C'] as const).map(cat => {
                    const items = data.filter(p => p.abc_revenue === cat);
                    const views = items.reduce((s, p) => s + p.adv_views, 0);
                    const clicks = items.reduce((s, p) => s + p.adv_clicks, 0);
                    const advSum = items.reduce((s, p) => s + p.adv_sum, 0);
                    const ordersSum = items.reduce((s, p) => s + p.orders_sum_rub, 0);
                    const activeCamp = items.reduce((s, p) => s + (p.active_campaigns || 0), 0);
                    return {
                        cat, items, views, clicks, advSum, ordersSum, activeCamp,
                        ctr: views ? (clicks / views * 100) : 0,
                        cpc: clicks ? (advSum / clicks) : 0,
                        cpm: views ? (advSum / views * 1000) : 0,
                        drr: ordersSum ? (advSum / ordersSum * 100) : (advSum > 0 ? null : 0),
                    };
                });
                const ABC_COLORS: Record<string, string> = { A: '#22c55e', B: '#f59e0b', C: '#ef4444' };
                const ABC_LABELS: Record<string, string> = { A: 'Категория A (80% выручки)', B: 'Категория B (15% выручки)', C: 'Категория C (5% выручки)' };
                return (
                    <div style={{ overflowX: 'auto', borderRadius: 12, border: '1px solid #e5e7eb' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, minWidth: 1200 }}>
                            <thead>
                                <tr>
                                    <th style={{ ...thStyle, textAlign: 'left', minWidth: 250 }}>КАТЕГОРИЯ</th>
                                    <th style={thStyle}>Товаров</th>
                                    <th style={{ ...thStyle, borderLeft: '1px solid #e5e7eb' }}>Просмотры</th>
                                    <th style={thStyle}>Клики</th>
                                    <th style={thStyle}>Расход ₽</th>
                                    <th style={thStyle}>CTR %</th>
                                    <th style={thStyle}>CPC ₽</th>
                                    <th style={thStyle}>CPM ₽</th>
                                    <th style={thStyle}>ДРР %</th>
                                    <th style={{ ...thStyle, borderLeft: '1px solid #e5e7eb' }}>Акт. РК</th>
                                </tr>
                            </thead>
                            <tbody>
                                {abcGroups.map(g => {
                                    const isExp = expandedGroups.has(g.cat);
                                    return (
                                        <Fragment key={g.cat}>
                                            <tr
                                                style={{ cursor: 'pointer', background: ABC_COLORS[g.cat] + '08', fontWeight: 600 }}
                                                onClick={() => setExpandedGroups(prev => { const n = new Set(prev); n.has(g.cat) ? n.delete(g.cat) : n.add(g.cat); return n; })}
                                            >
                                                <td style={{ padding: '10px 12px', borderBottom: '1px solid #e5e7eb' }}>
                                                    <span style={{ fontSize: 10, color: '#9ca3af', marginRight: 6 }}>{isExp ? '▼' : '▶'}</span>
                                                    <span style={{ display: 'inline-block', width: 28, height: 22, lineHeight: '22px', textAlign: 'center', borderRadius: 6, fontWeight: 700, fontSize: 13, color: '#fff', background: ABC_COLORS[g.cat], marginRight: 8 }}>{g.cat}</span>
                                                    {ABC_LABELS[g.cat]}
                                                </td>
                                                <td style={tdNum}>{g.items.length}</td>
                                                <td style={{ ...tdNum, borderLeft: '1px solid #f3f4f6' }}>{fmt(g.views)}</td>
                                                <td style={tdNum}>{fmt(g.clicks)}</td>
                                                <td style={{ ...tdNum, fontWeight: 600 }}>{fmt(g.advSum)}</td>
                                                <td style={tdNum}>{fmtPct(g.ctr)}</td>
                                                <td style={tdNum}>{fmt(g.cpc)}</td>
                                                <td style={tdNum}>{fmt(g.cpm)}</td>
                                                <td style={{ ...tdNum, color: drrColor(g.drr), fontWeight: 600 }}>{drrLabel(g.drr)}</td>
                                                <td style={{ ...tdNum, borderLeft: '1px solid #f3f4f6', color: g.activeCamp > 0 ? '#22c55e' : '#9ca3af', fontWeight: g.activeCamp > 0 ? 600 : 400 }}>{g.activeCamp}</td>
                                            </tr>
                                            {isExp && g.items.map((p, pi) => {
                                                const cBg = pi % 2 === 0 ? '#fafafa' : '#ffffff';
                                                const isNmExp = expandedNm.has(p.nm_id);
                                                return (
                                                    <Fragment key={`abc-${p.nm_id}`}>
                                                        <tr style={{ background: cBg, fontSize: 12, cursor: p.campaigns.length > 0 ? 'pointer' : undefined }}
                                                            onClick={() => p.campaigns.length > 0 && toggleExpand(p.nm_id)}>
                                                            <td style={{ padding: '6px 12px 6px 48px', borderBottom: '1px solid #f3f4f6' }}>
                                                                {p.campaigns.length > 0 && <span style={{ fontSize: 9, color: '#9ca3af', marginRight: 4 }}>{isNmExp ? '▼' : '▶'}</span>}
                                                                <span style={{ fontWeight: 500 }}>{p.vendor_code || p.nm_id}</span>
                                                                <div style={{ fontSize: 11, color: '#9ca3af' }}>{p.subject} · {p.brand}</div>
                                                            </td>
                                                            <td style={{ ...tdNum, color: '#9ca3af' }}>—</td>
                                                            <td style={{ ...tdNum, borderLeft: '1px solid #f3f4f6' }}>{fmt(p.adv_views)}</td>
                                                            <td style={tdNum}>{fmt(p.adv_clicks)}</td>
                                                            <td style={{ ...tdNum, fontWeight: 500 }}>{fmt(p.adv_sum)}</td>
                                                            <td style={tdNum}>{fmtPct(p.ctr)}</td>
                                                            <td style={tdNum}>{fmt(p.cpc)}</td>
                                                            <td style={tdNum}>{fmt(p.cpm)}</td>
                                                            <td style={{ ...tdNum, color: drrColor(p.drr) }}>{drrLabel(p.drr)}</td>
                                                            <td style={{ ...tdNum, borderLeft: '1px solid #f3f4f6', color: p.active_campaigns > 0 ? '#22c55e' : '#9ca3af' }}>{p.active_campaigns || 0}</td>
                                                        </tr>
                                                        {isNmExp && p.campaigns.map(c => {
                                                            const st = STATUS_MAP[c.status] || { label: String(c.status), color: '#94a3b8' };
                                                            return (
                                                                <tr key={`abcc-${p.nm_id}-${c.campaign_id}`} style={{ background: '#f0f4ff', fontSize: 11 }}>
                                                                    <td style={{ padding: '4px 12px 4px 68px', borderBottom: '1px solid #e8ecf4' }}>
                                                                        <span style={{ marginRight: 4 }}>📢</span>
                                                                        {c.name || c.campaign_id}
                                                                        <span style={{ display: 'inline-block', padding: '1px 6px', borderRadius: 10, fontSize: 10, fontWeight: 600, background: st.color + '20', color: st.color, marginLeft: 6 }}>{st.label}</span>
                                                                    </td>
                                                                    <td style={campTd}></td>
                                                                    <td style={campTdNum}>{c.views ? fmt(c.views) : '—'}</td>
                                                                    <td style={campTdNum}>{c.clicks ? fmt(c.clicks) : '—'}</td>
                                                                    <td style={{ ...campTdNum, fontWeight: c.spend ? 600 : 400 }}>{c.spend ? fmt(c.spend) : '—'}</td>
                                                                    <td style={campTdNum}>{c.ctr ? fmtPct(c.ctr) : '—'}</td>
                                                                    <td style={campTdNum}>{c.cpc ? fmt(c.cpc) : '—'}</td>
                                                                    <td style={campTdNum}>{c.cpm ? fmt(c.cpm) : '—'}</td>
                                                                    <td style={campTd}></td>
                                                                    <td style={campTd}></td>
                                                                </tr>
                                                            );
                                                        })}
                                                    </Fragment>
                                                );
                                            })}
                                        </Fragment>
                                    );
                                })}
                            </tbody>
                        </table>
                        <div style={{ padding: '12px 20px', borderTop: '1px solid #e5e7eb', fontSize: 12, color: '#9ca3af', background: '#f9fafb' }}>
                            Всего товаров: {data.length} | A — {abcGroups[0]?.items.length || 0} шт, B — {abcGroups[1]?.items.length || 0} шт, C — {abcGroups[2]?.items.length || 0} шт
                        </div>
                    </div>
                );
            })()}

            {/* Data table — SKU mode */}
            {!loading && data.length > 0 && groupBy === 'sku' && (
                <div style={{ overflowX: 'auto', borderRadius: 12, border: '1px solid #e5e7eb' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, minWidth: 1200 }}>
                        <thead>
                            <tr>
                                <th colSpan={2} style={{ ...stickyCol, minWidth: 230, background: '#ffffff', zIndex: 22, borderBottom: '2px solid #e5e7eb', borderRight: '1px solid #e5e7eb', padding: '10px 12px', textAlign: 'left', color: '#374151', fontWeight: 600, boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.08)' }}>
                                    АРТИКУЛ
                                </th>
                                <th style={thStyle}>Товар</th>
                                <th style={thStyle}>Бренд</th>
                                <th style={{ ...thStyle, borderLeft: '1px solid #e5e7eb' }}>Просмотры</th>
                                <th style={thStyle}>Клики</th>
                                <th style={thStyle}>Расход ₽</th>
                                <th style={thStyle}>CTR %</th>
                                <th style={thStyle}>CPC ₽</th>
                                <th style={thStyle}>CPM ₽</th>
                                <th style={thStyle}>ДРР %</th>
                                <th style={{ ...thStyle, textAlign: 'center', borderLeft: '1px solid #e5e7eb' }}>ABC выр.</th>
                                <th style={{ ...thStyle, textAlign: 'center' }}>ABC приб.</th>
                                <th style={{ ...thStyle, borderLeft: '1px solid #e5e7eb' }}>Акт. РК</th>
                                <th style={thStyle}>Остатки</th>
                                <th style={thStyle}>Бюджет ₽</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.map((p, idx) => {
                                const isExpanded = expandedNm.has(p.nm_id);
                                const totalBudget = p.campaigns.reduce((s, c) => s + (c.budget || 0), 0);
                                const rowBg = idx % 2 === 0 ? '#ffffff' : '#f9fafb';
                                return (
                                    <Fragment key={p.nm_id}>
                                        <tr
                                            onClick={() => p.campaigns.length > 0 && toggleExpand(p.nm_id)}
                                            style={{ cursor: p.campaigns.length > 0 ? 'pointer' : undefined, background: rowBg }}
                                        >
                                            <td style={{ ...stickyCol, width: 24, background: rowBg, borderRight: 'none', paddingLeft: 8, borderBottom: '1px solid #f3f4f6', boxShadow: 'none' }}>
                                                {p.campaigns.length > 0 && <span style={{ fontSize: 10, color: '#6b7280' }}>{isExpanded ? '▼' : '▶'}</span>}
                                            </td>
                                            <td style={{ ...stickyCol, left: 24, background: rowBg, minWidth: 206, padding: '8px 12px', borderBottom: '1px solid #f3f4f6', fontWeight: 500, fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 240, boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.05)' }}>
                                                {p.vendor_code || p.nm_id}
                                            </td>
                                            <td style={tdStyle}>{p.subject || '—'}</td>
                                            <td style={tdStyle}>{p.brand || '—'}</td>
                                            <td style={{ ...tdNum, borderLeft: '1px solid #f3f4f6' }}>{fmt(p.adv_views)}</td>
                                            <td style={tdNum}>{fmt(p.adv_clicks)}</td>
                                            <td style={{ ...tdNum, fontWeight: 600 }}>{fmt(p.adv_sum)}</td>
                                            <td style={tdNum}>{fmtPct(p.ctr)}</td>
                                            <td style={tdNum}>{fmt(p.cpc)}</td>
                                            <td style={tdNum}>{fmt(p.cpm)}</td>
                                            <td style={{ ...tdNum, color: drrColor(p.drr), fontWeight: 600 }}>
                                                {drrLabel(p.drr)}
                                            </td>
                                            <td style={{ ...tdStyle, textAlign: 'center', borderLeft: '1px solid #f3f4f6' }}>{abcBadge(p.abc_revenue)}</td>
                                            <td style={{ ...tdStyle, textAlign: 'center' }}>{abcBadge(p.abc_profit)}</td>
                                            <td style={{ ...tdNum, borderLeft: '1px solid #f3f4f6', color: p.active_campaigns > 0 ? '#22c55e' : '#9ca3af', fontWeight: p.active_campaigns > 0 ? 600 : 400 }}>{p.active_campaigns || 0}</td>
                                            <td style={tdNum}>{fmt(p.stock_qty || 0)}</td>
                                            <td style={{ ...tdNum, fontWeight: 600 }}>{fmt(totalBudget)}</td>
                                        </tr>
                                        {isExpanded && p.campaigns.map(c => {
                                            const st = STATUS_MAP[c.status] || { label: String(c.status), color: '#94a3b8' };
                                            const events = (c.events || []) as { event_type: string; old_value: string; new_value: string; created_at: string }[];
                                            const campKey = `${p.nm_id}-${c.campaign_id}`;
                                            const isCampExpanded = expandedCamp.has(campKey);
                                            const hasEvents = events.length > 0;
                                            return (
                                                <Fragment key={`camp-${p.nm_id}-${c.campaign_id}`}>
                                                    <tr
                                                        style={{ background: '#f0f4ff', cursor: hasEvents ? 'pointer' : undefined }}
                                                        onClick={hasEvents ? () => setExpandedCamp(prev => {
                                                            const next = new Set(prev);
                                                            next.has(campKey) ? next.delete(campKey) : next.add(campKey);
                                                            return next;
                                                        }) : undefined}
                                                    >
                                                        {/* col 1: expand */}
                                                        <td style={{ ...stickyCol, width: 24, background: '#f0f4ff', borderRight: 'none', paddingLeft: 16, borderBottom: '1px solid #e8ecf4', boxShadow: 'none' }}>
                                                            {hasEvents && <span style={{ fontSize: 9, color: '#94a3b8' }}>{isCampExpanded ? '▼' : '▶'}</span>}
                                                        </td>
                                                        {/* col 2: name (instead of article) */}
                                                        <td style={{ ...stickyCol, left: 24, background: '#f0f4ff', padding: '6px 12px', borderBottom: '1px solid #e8ecf4', fontSize: 12, boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.05)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 240 }}>
                                                            <span style={{ marginRight: 4 }}>📢</span>
                                                            {c.name || c.campaign_id}
                                                        </td>
                                                        {/* col 3: type */}
                                                        <td style={campTd}>{TYPE_MAP[c.campaign_type || ''] || c.campaign_type || '—'}</td>
                                                        {/* col 4: status */}
                                                        <td style={campTd}>
                                                            <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600, background: st.color + '20', color: st.color }}>{st.label}</span>
                                                        </td>
                                                        {/* col 5-11: views, clicks, spend, CTR, CPC, CPM, DRR */}
                                                        <td style={campTdNum}>{c.views ? fmt(c.views) : '—'}</td>
                                                        <td style={campTdNum}>{c.clicks ? fmt(c.clicks) : '—'}</td>
                                                        <td style={{ ...campTdNum, fontWeight: c.spend ? 600 : 400, color: c.spend ? '#374151' : '#9ca3af' }}>{c.spend ? fmt(c.spend) : '—'}</td>
                                                        <td style={campTdNum}>{c.ctr ? fmtPct(c.ctr) : '—'}</td>
                                                        <td style={campTdNum}>{c.cpc ? fmt(c.cpc) : '—'}</td>
                                                        <td style={campTdNum}>{c.cpm ? fmt(c.cpm) : '—'}</td>
                                                        <td style={campTd}></td>
                                                        {/* col 12-15: ABC rev, ABC profit, active campaigns, stock — empty */}
                                                        <td style={campTd}></td>
                                                        <td style={campTd}></td>
                                                        <td style={campTd}></td>
                                                        <td style={campTd}></td>
                                                        {/* col 16: budget */}
                                                        <td style={{ ...campTdNum, fontWeight: 600 }}>{fmt(c.budget)}</td>
                                                    </tr>
                                                    {isCampExpanded && events.map((ev, i) => {
                                                        const dt = ev.created_at ? new Date(ev.created_at).toLocaleString('ru-RU', {
                                                            day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
                                                        }) : '';
                                                        const isBudget = ev.event_type === 'budget_change';
                                                        const isStatus = ev.event_type === 'status_change';
                                                        const statusLabel = (v: string) => {
                                                            const n = Number(v);
                                                            return STATUS_MAP[n]?.label || v;
                                                        };
                                                        return (
                                                            <tr key={`ev-${c.campaign_id}-${i}`} style={{ background: '#f8f9fc' }}>
                                                                <td style={{ ...stickyCol, background: '#f8f9fc', borderRight: 'none', boxShadow: 'none' }}></td>
                                                                <td colSpan={15} style={{ ...stickyCol, left: 24, background: '#f8f9fc', padding: '4px 12px 4px 40px', fontSize: 11, color: '#6b7280', borderBottom: '1px solid #eef0f4', boxShadow: 'none' }}>
                                                                    <span style={{ color: '#9ca3af', marginRight: 8 }}>{dt}</span>
                                                                    {isBudget && (
                                                                        <span>💰 бюджет {fmt(Number(ev.old_value))} → <strong style={{ color: '#374151' }}>{fmt(Number(ev.new_value))}</strong></span>
                                                                    )}
                                                                    {isStatus && (
                                                                        <span>🔄 статус {statusLabel(ev.old_value)} → <strong style={{ color: '#374151' }}>{statusLabel(ev.new_value)}</strong></span>
                                                                    )}
                                                                </td>
                                                            </tr>
                                                        );
                                                    })}
                                                </Fragment>
                                            );
                                        })}
                                    </Fragment>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
            {/* Grouped table — brand/subject/tag/imt mode */}
            {!loading && groupData.length > 0 && groupBy !== 'sku' && groupBy !== 'abc' && (
                <div style={{ overflowX: 'auto', borderRadius: 12, border: '1px solid #e5e7eb' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, minWidth: 1200 }}>
                        <thead>
                            <tr>
                                <th style={{ ...thStyle, textAlign: 'left', minWidth: 230, borderRight: '1px solid #e5e7eb' }}>
                                    {groupBy === 'brand' ? 'БРЕНД' : groupBy === 'subject' ? 'КАТЕГОРИЯ' : groupBy === 'tag' ? 'ЯРЛЫК' : 'СКЛЕЙКА'}
                                </th>
                                <th style={thStyle}>Товаров</th>
                                <th style={{ ...thStyle, borderLeft: '1px solid #e5e7eb' }}>Просмотры</th>
                                <th style={thStyle}>Клики</th>
                                <th style={thStyle}>Расход ₽</th>
                                <th style={thStyle}>CTR %</th>
                                <th style={thStyle}>CPC ₽</th>
                                <th style={thStyle}>CPM ₽</th>
                                <th style={thStyle}>ДРР %</th>
                                <th style={{ ...thStyle, textAlign: 'center', borderLeft: '1px solid #e5e7eb' }}>ABC выр.</th>
                                <th style={{ ...thStyle, textAlign: 'center' }}>ABC приб.</th>
                                <th style={{ ...thStyle, borderLeft: '1px solid #e5e7eb' }}>Акт. РК</th>
                            </tr>
                        </thead>
                        <tbody>
                            {groupData.map((g, gi) => {
                                const isGrpExp = expandedGroups.has(g.group_name);
                                const rowBg = gi % 2 === 0 ? '#ffffff' : '#f9fafb';
                                return (
                                    <Fragment key={g.group_name}>
                                        <tr
                                            style={{ background: rowBg, cursor: 'pointer', fontWeight: 600 }}
                                            onClick={() => setExpandedGroups(prev => { const n = new Set(prev); n.has(g.group_name) ? n.delete(g.group_name) : n.add(g.group_name); return n; })}
                                        >
                                            <td style={{ padding: '8px 12px', borderBottom: '1px solid #f3f4f6', borderRight: '1px solid #e5e7eb', minWidth: 230 }}>
                                                <span style={{ fontSize: 10, color: '#9ca3af', marginRight: 6 }}>{isGrpExp ? '▼' : '▶'}</span>
                                                {g.group_name}
                                                <span style={{ fontSize: 11, color: '#9ca3af', fontWeight: 400, marginLeft: 6 }}>({g.product_count})</span>
                                            </td>
                                            <td style={tdNum}>{g.product_count}</td>
                                            <td style={{ ...tdNum, borderLeft: '1px solid #f3f4f6' }}>{fmt(g.adv_views)}</td>
                                            <td style={tdNum}>{fmt(g.adv_clicks)}</td>
                                            <td style={{ ...tdNum, fontWeight: 600 }}>{fmt(g.adv_sum)}</td>
                                            <td style={tdNum}>{fmtPct(g.ctr)}</td>
                                            <td style={tdNum}>{fmt(g.cpc)}</td>
                                            <td style={tdNum}>{fmt(g.cpm)}</td>
                                            <td style={{ ...tdNum, color: drrColor(g.drr), fontWeight: 600 }}>{drrLabel(g.drr)}</td>
                                            <td style={{ ...tdStyle, textAlign: 'center', borderLeft: '1px solid #f3f4f6' }}>{abcBadge(g.abc_revenue)}</td>
                                            <td style={{ ...tdStyle, textAlign: 'center' }}>{abcBadge(g.abc_profit)}</td>
                                            <td style={{ ...tdNum, borderLeft: '1px solid #f3f4f6', color: g.active_campaigns > 0 ? '#22c55e' : '#9ca3af', fontWeight: g.active_campaigns > 0 ? 600 : 400 }}>{g.active_campaigns || 0}</td>
                                        </tr>
                                        {isGrpExp && g.children.map((p, pi) => {
                                            const isNmExp = expandedNm.has(p.nm_id);
                                            const cBg = pi % 2 === 0 ? '#fafafa' : '#ffffff';
                                            return (
                                                <Fragment key={`child-${p.nm_id}`}>
                                                    <tr
                                                        style={{ background: cBg, fontSize: 12, cursor: p.campaigns.length > 0 ? 'pointer' : undefined }}
                                                        onClick={() => p.campaigns.length > 0 && toggleExpand(p.nm_id)}
                                                    >
                                                        <td style={{ padding: '6px 12px 6px 32px', borderBottom: '1px solid #f3f4f6', borderRight: '1px solid #e5e7eb' }}>
                                                            {p.campaigns.length > 0 && <span style={{ fontSize: 9, color: '#9ca3af', marginRight: 4 }}>{isNmExp ? '▼' : '▶'}</span>}
                                                            <span style={{ fontWeight: 500 }}>{p.vendor_code || p.nm_id}</span>
                                                            <div style={{ fontSize: 11, color: '#9ca3af' }}>{p.subject} · {p.brand}</div>
                                                        </td>
                                                        <td style={{ ...tdNum, color: '#9ca3af' }}>—</td>
                                                        <td style={{ ...tdNum, borderLeft: '1px solid #f3f4f6' }}>{fmt(p.adv_views)}</td>
                                                        <td style={tdNum}>{fmt(p.adv_clicks)}</td>
                                                        <td style={{ ...tdNum, fontWeight: 500 }}>{fmt(p.adv_sum)}</td>
                                                        <td style={tdNum}>{fmtPct(p.ctr)}</td>
                                                        <td style={tdNum}>{fmt(p.cpc)}</td>
                                                        <td style={tdNum}>{fmt(p.cpm)}</td>
                                                        <td style={{ ...tdNum, color: drrColor(p.drr) }}>{drrLabel(p.drr)}</td>
                                                        <td style={{ ...tdStyle, textAlign: 'center', borderLeft: '1px solid #f3f4f6' }}>{abcBadge(p.abc_revenue)}</td>
                                                        <td style={{ ...tdStyle, textAlign: 'center' }}>{abcBadge(p.abc_profit)}</td>
                                                        <td style={{ ...tdNum, borderLeft: '1px solid #f3f4f6', color: p.active_campaigns > 0 ? '#22c55e' : '#9ca3af', fontWeight: p.active_campaigns > 0 ? 600 : 400 }}>{p.active_campaigns || 0}</td>
                                                    </tr>
                                                    {isNmExp && p.campaigns.map(c => {
                                                        const st = STATUS_MAP[c.status] || { label: String(c.status), color: '#94a3b8' };
                                                        return (
                                                            <tr key={`gc-${p.nm_id}-${c.campaign_id}`} style={{ background: '#f0f4ff', fontSize: 11 }}>
                                                                <td style={{ padding: '4px 12px 4px 52px', borderBottom: '1px solid #e8ecf4', borderRight: '1px solid #e5e7eb' }}>
                                                                    <span style={{ marginRight: 4 }}>📢</span>
                                                                    {c.name || c.campaign_id}
                                                                    <span style={{ display: 'inline-block', padding: '1px 6px', borderRadius: 10, fontSize: 10, fontWeight: 600, background: st.color + '20', color: st.color, marginLeft: 6 }}>{st.label}</span>
                                                                </td>
                                                                <td style={campTd}></td>
                                                                <td style={campTdNum}>{c.views ? fmt(c.views) : '—'}</td>
                                                                <td style={campTdNum}>{c.clicks ? fmt(c.clicks) : '—'}</td>
                                                                <td style={{ ...campTdNum, fontWeight: c.spend ? 600 : 400 }}>{c.spend ? fmt(c.spend) : '—'}</td>
                                                                <td style={campTdNum}>{c.ctr ? fmtPct(c.ctr) : '—'}</td>
                                                                <td style={campTdNum}>{c.cpc ? fmt(c.cpc) : '—'}</td>
                                                                <td style={campTdNum}>{c.cpm ? fmt(c.cpm) : '—'}</td>
                                                                <td style={campTd}></td>
                                                                <td style={campTd}></td>
                                                                <td style={campTd}></td>
                                                                <td style={campTd}></td>
                                                            </tr>
                                                        );
                                                    })}
                                                </Fragment>
                                            );
                                        })}
                                    </Fragment>
                                );
                            })}
                        </tbody>
                    </table>
                    <div style={{ padding: '12px 20px', borderTop: '1px solid #e5e7eb', fontSize: 12, color: '#9ca3af', background: '#f9fafb' }}>
                        Всего {groupBy === 'brand' ? 'брендов' : groupBy === 'subject' ? 'категорий' : groupBy === 'tag' ? 'ярлыков' : 'склеек'}: {groupData.length}
                    </div>
                </div>
            )}
        </div>
    );
}
