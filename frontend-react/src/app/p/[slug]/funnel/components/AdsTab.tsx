'use client';
import { Fragment, useEffect, useState, useCallback, useRef } from 'react';
import { api } from '@/lib/api';
import { exportToExcel } from '@/lib/utils';
import type { AdTabProduct, UnifiedSyncProgress } from '@/types/api';

const fmt = (n: number) => n?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '0';
const fmtPct = (n: number) => (n || 0).toFixed(2) + '%';

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

export function AdsTab({ dateFrom, dateTo, brand, subject }: AdsTabProps) {
    const [data, setData] = useState<AdTabProduct[]>([]);
    const [loading, setLoading] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [progress, setProgress] = useState('');
    const [error, setError] = useState('');
    const [expandedNm, setExpandedNm] = useState<Set<number>>(new Set());
    const [expandedCamp, setExpandedCamp] = useState<Set<string>>(new Set());
    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const loadData = useCallback(async () => {
        if (!dateFrom || !dateTo) return;
        setLoading(true);
        setError('');
        try {
            const res = await api.getAdTab({ date_from: dateFrom, date_to: dateTo, brand, subject });
            setData(res);
        } catch (e: unknown) {
            setError((e as Error).message || 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [dateFrom, dateTo, brand, subject]);

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
            'ДРР %': p.drr,
            'Заказы': p.orders_count,
            'Сумма заказов ₽': p.orders_sum_rub,
            'Бюджет ₽': p.campaigns.reduce((s, c) => s + (c.budget || 0), 0),
        }));
        exportToExcel(rows, 'advertising');
    };

    // Summary calculations
    const totalSpend = data.reduce((s, p) => s + p.adv_sum, 0);
    const totalViews = data.reduce((s, p) => s + p.adv_views, 0);
    const totalClicks = data.reduce((s, p) => s + p.adv_clicks, 0);
    const totalOrdersSum = data.reduce((s, p) => s + p.orders_sum_rub, 0);
    const avgDrr = totalOrdersSum ? (totalSpend / totalOrdersSum * 100) : 0;

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

            {/* Summary cards */}
            {data.length > 0 && (
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
            {!loading && !error && data.length === 0 && (
                <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Нет данных по рекламе за выбранный период
                </div>
            )}

            {/* Hint: sync campaigns if no campaigns linked */}
            {!loading && data.length > 0 && data.every(p => p.campaigns.length === 0) && (
                <div style={{ fontSize: 13, color: '#f59e0b', background: 'rgba(245,158,11,0.08)', padding: '8px 16px', borderRadius: 8, marginBottom: 12 }}>
                    ⚠️ Кампании не синхронизированы. Нажмите «Обновить» чтобы загрузить названия и бюджеты рекламных кампаний из WB.
                </div>
            )}

            {/* Data table */}
            {!loading && data.length > 0 && (
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
                                <th style={{ ...thStyle, borderLeft: '1px solid #e5e7eb' }}>Остатки</th>
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
                                            <td style={{ ...tdNum, color: p.drr > 30 ? '#ef4444' : p.drr > 15 ? '#f59e0b' : '#22c55e', fontWeight: 600 }}>
                                                {fmtPct(p.drr)}
                                            </td>
                                            <td style={{ ...tdStyle, textAlign: 'center', borderLeft: '1px solid #f3f4f6' }}>{abcBadge(p.abc_revenue)}</td>
                                            <td style={{ ...tdStyle, textAlign: 'center' }}>{abcBadge(p.abc_profit)}</td>
                                            <td style={{ ...tdNum, borderLeft: '1px solid #f3f4f6' }}>{fmt(p.stock_qty || 0)}</td>
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
                                                        {/* col 12-14: ABC rev, ABC profit, stock — empty */}
                                                        <td style={campTd}></td>
                                                        <td style={campTd}></td>
                                                        <td style={campTd}></td>
                                                        {/* col 15: budget */}
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
                                                                <td colSpan={14} style={{ ...stickyCol, left: 24, background: '#f8f9fc', padding: '4px 12px 4px 40px', fontSize: 11, color: '#6b7280', borderBottom: '1px solid #eef0f4', boxShadow: 'none' }}>
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
        </div>
    );
}
