'use client';

/**
 * Вкладка «Динамика расхождения» страницы «Анализ сборки».
 *
 * Дневная история расхождения остатков (наш склад vs ФФ-зеркало, пишет джоба снапшота):
 * сколько штук/SKU у ФФ больше и у нас больше день за днём + журнал изменений по SKU
 * (появился/сошёлся/вырос/уменьшился/сменил знак между соседними срезами).
 * Параллель вкладке «Динамика черновика» — тот же паттерн загрузки и графика.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    CartesianGrid,
    ComposedChart,
    Legend as RechartsLegend,
    Line,
    ResponsiveContainer,
    Tooltip as RechartsTooltip,
    XAxis,
    YAxis,
} from 'recharts';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { StockMismatchChangeRow, StockMismatchHistoryResponse } from '@/types/api';

// ─── Config ─────────────────────────────────────────────────────────────────

type PeriodKey = '7' | '30' | '90';

const PERIODS: { key: PeriodKey; label: string }[] = [
    { key: '7', label: '7 дн' },
    { key: '30', label: '30 дн' },
    { key: '90', label: '90 дн' },
];

const ALL_CATEGORIES = '__all__';
const MSK_TZ = 'Europe/Moscow';

const dayTickFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: MSK_TZ, day: '2-digit', month: '2-digit' });
const dayFullFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: MSK_TZ, weekday: 'short', day: 'numeric', month: 'long' });

/** Бэкенд отдаёт дату без времени («2026-07-20») — фиксируем полночь UTC перед Date. */
function toDate(snapshotDate: string): Date {
    return new Date(/[Z+]|T/.test(snapshotDate) ? snapshotDate : `${snapshotDate}T00:00:00Z`);
}

/** Палитра линий обзорного графика (мультисклад) — циклится, если складов больше. */
const PALETTE = [
    'var(--color-accent)',
    'var(--color-warning)',
    'var(--color-danger)',
    'var(--color-success)',
    'var(--color-text-dim)',
];

const EVENT_META: Record<string, { icon: string; label: string; cls: string }> = {
    appeared: { icon: '🆕', label: 'Появился', cls: 'badge-warning' },
    resolved: { icon: '✅', label: 'Сошёлся', cls: 'badge-success' },
    grew:     { icon: '▲', label: 'Вырос', cls: 'badge-danger' },
    shrank:   { icon: '▼', label: 'Уменьшился', cls: 'badge-success' },
    flipped:  { icon: '↔', label: 'Сменил знак', cls: 'badge-secondary' },
};
const EVENT_ORDER = ['appeared', 'grew', 'shrank', 'resolved', 'flipped'];

function eventMeta(event: string): { icon: string; label: string; cls: string } {
    return EVENT_META[event] ?? { icon: '•', label: event, cls: 'badge-secondary' };
}

// ─── UI atoms ───────────────────────────────────────────────────────────────

function SectionTitle({ children }: { children: React.ReactNode }) {
    return (
        <div
            style={{
                fontSize: 12,
                fontWeight: 600,
                color: 'var(--color-text-muted)',
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                marginBottom: 12,
            }}
        >
            {children}
        </div>
    );
}

function EventBadge({ event }: { event: string }) {
    const m = eventMeta(event);
    return (
        <span className={`badge ${m.cls}`} style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
            {m.icon} {m.label}
        </span>
    );
}

// ─── Tab ────────────────────────────────────────────────────────────────────

export default function StockMismatchDynamicsTab({ slug: _slug }: { slug: string }) {
    const [history, setHistory] = useState<StockMismatchHistoryResponse | null>(null);
    const [changes, setChanges] = useState<StockMismatchChangeRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [period, setPeriod] = useState<PeriodKey>('30');
    const [warehouseId, setWarehouseId] = useState<number | ''>('');
    const [category, setCategory] = useState<string>(ALL_CATEGORIES);
    const [snapshotting, setSnapshotting] = useState(false);
    const [eventFilter, setEventFilter] = useState<Set<string>>(new Set());

    // Счётчик запроса — быстрый клик по периоду/фильтрам не даёт устаревшему
    // ответу перезаписать актуальный выбор (паттерн DraftDynamicsTab).
    const loadSeqRef = useRef(0);
    const load = useCallback(async (days: PeriodKey, wh: number | '', cat: string) => {
        const seq = ++loadSeqRef.current;
        setLoading(true);
        setError('');
        try {
            const whId = wh === '' ? undefined : wh;
            const catParam = cat === ALL_CATEGORIES ? undefined : cat;
            const [hist, chg] = await Promise.all([
                api.getStockMismatchHistory(Number(days), whId, catParam),
                api.getStockMismatchChanges(Number(days), whId, catParam),
            ]);
            if (seq !== loadSeqRef.current) return;
            setHistory(hist);
            setChanges(chg.changes || []);
        } catch (e) {
            if (seq !== loadSeqRef.current) return;
            setError(e instanceof Error ? e.message : 'Не удалось загрузить историю расхождения');
        } finally {
            if (seq === loadSeqRef.current) setLoading(false);
        }
    }, []);

    useEffect(() => { load(period, warehouseId, category); }, [load, period, warehouseId, category]);

    // «Снять срез сейчас» — не ждать джобу после ручной сверки остатков.
    const handleSnapshot = useCallback(async () => {
        if (snapshotting) return;
        setSnapshotting(true);
        try {
            await api.snapshotStockMismatchHistory();
            await load(period, warehouseId, category);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось снять срез');
        } finally {
            setSnapshotting(false);
        }
    }, [snapshotting, load, period, warehouseId, category]);

    const [exporting, setExporting] = useState(false);
    const handleExport = useCallback(async () => {
        if (exporting) return;
        setExporting(true);
        try {
            const whId = warehouseId === '' ? undefined : warehouseId;
            const catParam = category === ALL_CATEGORIES ? undefined : category;
            await api.downloadStockMismatchChangesExcel(Number(period), whId, catParam);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось скачать Excel');
        } finally {
            setExporting(false);
        }
    }, [exporting, period, warehouseId, category]);

    const toggleEvent = useCallback((ev: string) => {
        setEventFilter(prev => {
            const next = new Set(prev);
            if (next.has(ev)) next.delete(ev);
            else next.add(ev);
            return next;
        });
    }, []);

    const isOverview = warehouseId === '';

    // Обзорный режим: серия net_diff на каждый склад, встречающийся в точках.
    const overviewSeries = useMemo(() => {
        if (!isOverview || !history) return [];
        const whMap = new Map(history.warehouses.map(w => [w.warehouse_id, w]));
        const ids = new Set<number>();
        for (const p of history.points) ids.add(p.warehouse_id);
        return Array.from(ids).map((id, idx) => ({
            id,
            key: `wh_${id}`,
            name: whMap.get(id)?.warehouse_name || `Склад #${id}`,
            color: PALETTE[idx % PALETTE.length],
        }));
    }, [isOverview, history]);

    const overviewChartData = useMemo(() => {
        if (!isOverview || !history) return [];
        const byTs = new Map<number, Record<string, number> & { ts: number }>();
        for (const p of history.points) {
            const ts = toDate(p.snapshot_date).getTime();
            const row = byTs.get(ts) || ({ ts } as Record<string, number> & { ts: number });
            row[`wh_${p.warehouse_id}`] = p.net_diff;
            byTs.set(ts, row);
        }
        return Array.from(byTs.values()).sort((a, b) => a.ts - b.ts);
    }, [isOverview, history]);

    // Режим одного склада: две линии — «у ФФ больше» / «у нас больше».
    const whChartData = useMemo(() => {
        if (isOverview || !history) return [];
        return [...history.points]
            .map(p => ({ ts: toDate(p.snapshot_date).getTime(), ff: p.surplus_ff_qty, our: p.surplus_our_qty }))
            .sort((a, b) => a.ts - b.ts);
    }, [isOverview, history]);

    // Журнал изменений: свежие дни сверху; фильтр по типу события (пусто = все).
    const filteredChanges = useMemo(() => {
        const rows = eventFilter.size === 0 ? changes : changes.filter(c => eventFilter.has(c.event));
        return [...rows].sort((a, b) => (a.snapshot_date < b.snapshot_date ? 1 : a.snapshot_date > b.snapshot_date ? -1 : 0));
    }, [changes, eventFilter]);

    const eventCounts = useMemo(() => {
        const m = new Map<string, number>();
        for (const c of changes) m.set(c.event, (m.get(c.event) || 0) + 1);
        return m;
    }, [changes]);

    const isEmpty = !loading && !error && history !== null && history.points.length === 0;
    const hasData = !loading && !error && history !== null && history.points.length > 0;

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* Фильтры */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <select
                    className="form-select"
                    style={{ width: 'auto', minWidth: 160 }}
                    value={warehouseId}
                    onChange={e => setWarehouseId(e.target.value ? Number(e.target.value) : '')}
                    disabled={loading}
                >
                    <option value="">Все склады</option>
                    {(history?.warehouses ?? []).map(w => (
                        <option key={w.warehouse_id} value={w.warehouse_id}>
                            {w.warehouse_name || `Склад #${w.warehouse_id}`}
                        </option>
                    ))}
                </select>
                <select
                    className="form-select"
                    style={{ width: 'auto', minWidth: 160 }}
                    value={category}
                    onChange={e => setCategory(e.target.value)}
                    disabled={loading}
                >
                    <option value={ALL_CATEGORIES}>Все категории</option>
                    {(history?.categories ?? []).map(c => <option key={c} value={c}>{c}</option>)}
                </select>
                <div style={{ display: 'flex', gap: 4 }}>
                    {PERIODS.map(p => (
                        <button
                            key={p.key}
                            type="button"
                            className={`btn btn-sm ${period === p.key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setPeriod(p.key)}
                        >
                            {p.label}
                        </button>
                    ))}
                </div>
                <button type="button" className="btn btn-sm btn-secondary" onClick={handleSnapshot} disabled={snapshotting || loading}>
                    {snapshotting ? 'Срез…' : '⏱ Снять срез сейчас'}
                </button>
                <button
                    type="button"
                    className="btn btn-sm btn-secondary"
                    onClick={handleExport}
                    disabled={exporting || loading || changes.length === 0}
                    style={{ marginLeft: 'auto' }}
                >
                    {exporting ? 'Готовим…' : '⬇ Excel'}
                </button>
            </div>

            {/* Loading */}
            {loading && (
                <div
                    className="glass-card"
                    style={{
                        height: 320,
                        background: 'linear-gradient(90deg, rgba(0,0,0,0.04) 0%, rgba(0,0,0,0.07) 50%, rgba(0,0,0,0.04) 100%)',
                        backgroundSize: '200% 100%',
                        animation: 'shimmer 1.4s linear infinite',
                    }}
                />
            )}

            {/* Error */}
            {!loading && error && (
                <div className="glass-card" style={{ padding: 24, textAlign: 'center' }}>
                    <div style={{ fontSize: 14, color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>
                    <button type="button" className="btn btn-sm btn-secondary" onClick={() => load(period, warehouseId, category)}>
                        Повторить
                    </button>
                </div>
            )}

            {/* Empty: снапшоты копятся вперёд, без бэкфилла — это не ошибка */}
            {isEmpty && (
                <div className="glass-card" style={{ textAlign: 'center', padding: '48px 16px' }}>
                    <div style={{ fontSize: 36, marginBottom: 12 }}>⏱</div>
                    <div style={{ fontSize: 15, fontWeight: 500, marginBottom: 6 }}>История ещё копится</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 13, maxWidth: 460, margin: '0 auto 16px' }}>
                        Срезы расхождения остатков пишутся раз в день. Первую точку можно снять прямо сейчас.
                    </div>
                    <button type="button" className="btn btn-sm btn-primary" onClick={handleSnapshot} disabled={snapshotting}>
                        {snapshotting ? 'Срез…' : '⏱ Снять срез сейчас'}
                    </button>
                </div>
            )}

            {/* График */}
            {hasData && (
                <div className="glass-card" style={{ padding: 20 }}>
                    <SectionTitle>Δ расхождения по дням (MSK)</SectionTitle>
                    <ResponsiveContainer width="100%" height={280}>
                        <ComposedChart
                            data={isOverview ? overviewChartData : whChartData}
                            margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
                        >
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                            <XAxis
                                dataKey="ts"
                                type="number"
                                scale="time"
                                domain={['dataMin', 'dataMax']}
                                tickFormatter={(ts: number) => dayTickFmt.format(new Date(ts))}
                                tick={{ fontSize: 11 }}
                                minTickGap={40}
                            />
                            <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => formatNumber(v, 0)} />
                            <RechartsTooltip
                                labelFormatter={(ts) => dayFullFmt.format(new Date(Number(ts)))}
                                formatter={(value: number | string, name: string) => [formatNumber(Number(value), 0), name]}
                            />
                            <RechartsLegend wrapperStyle={{ fontSize: 12 }} />
                            {isOverview
                                ? overviewSeries.map(s => (
                                    <Line
                                        key={s.key}
                                        type="stepAfter"
                                        dataKey={s.key}
                                        name={s.name}
                                        stroke={s.color}
                                        strokeWidth={2}
                                        dot={false}
                                        connectNulls
                                    />
                                ))
                                : (
                                    <>
                                        <Line type="stepAfter" dataKey="ff" name="у ФФ больше" stroke="var(--color-accent)" strokeWidth={2} dot={false} />
                                        <Line type="stepAfter" dataKey="our" name="у нас больше" stroke="var(--color-danger)" strokeWidth={2} dot={false} />
                                    </>
                                )}
                        </ComposedChart>
                    </ResponsiveContainer>
                </div>
            )}

            {/* Журнал изменений */}
            {hasData && (
                <div className="glass-card" style={{ padding: 20 }}>
                    <SectionTitle>Журнал изменений по SKU</SectionTitle>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
                        {EVENT_ORDER.filter(ev => eventCounts.has(ev)).map(ev => {
                            const m = eventMeta(ev);
                            const active = eventFilter.has(ev);
                            return (
                                <button
                                    key={ev}
                                    type="button"
                                    className={`btn btn-sm ${active ? 'btn-primary' : 'btn-secondary'}`}
                                    onClick={() => toggleEvent(ev)}
                                >
                                    {m.icon} {m.label} · {formatNumber(eventCounts.get(ev) || 0, 0)}
                                </button>
                            );
                        })}
                        {eventFilter.size > 0 && (
                            <button type="button" className="btn btn-sm btn-secondary" onClick={() => setEventFilter(new Set())}>
                                Сбросить фильтр
                            </button>
                        )}
                    </div>

                    {changes.length === 0 ? (
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)', padding: '12px 0' }}>
                            Пока накоплено меньше двух срезов — сравнивать не с чем.
                        </div>
                    ) : filteredChanges.length === 0 ? (
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)', padding: '12px 0' }}>
                            По выбранным типам событий изменений нет.
                        </div>
                    ) : (
                        <div style={{ overflowX: 'auto' }}>
                            <table className="data-table" style={{ width: '100%', fontSize: 13 }}>
                                <thead>
                                    <tr>
                                        <th style={{ textAlign: 'left' }}>Дата</th>
                                        {isOverview && <th style={{ textAlign: 'left' }}>Склад</th>}
                                        <th style={{ textAlign: 'left' }}>Артикул</th>
                                        <th style={{ textAlign: 'left' }}>Категория</th>
                                        <th style={{ textAlign: 'left' }}>Событие</th>
                                        <th style={{ textAlign: 'right' }}>Было → стало</th>
                                        <th style={{ textAlign: 'right' }}>Δ</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {filteredChanges.map((row, idx) => (
                                        <tr key={`${row.warehouse_id}-${row.barcode}-${row.snapshot_date}-${idx}`}>
                                            <td>{dayTickFmt.format(toDate(row.snapshot_date))}</td>
                                            {isOverview && (
                                                <td style={{ color: 'var(--color-text-muted)' }}>
                                                    {row.warehouse_name || `#${row.warehouse_id}`}
                                                </td>
                                            )}
                                            <td>
                                                <div style={{ fontWeight: 500 }}>{row.article_seller || row.name || '—'}</div>
                                                <div style={{ fontSize: 11, color: 'var(--color-text-dim)', fontFamily: 'monospace' }}>
                                                    {row.barcode}
                                                </div>
                                            </td>
                                            <td style={{ color: 'var(--color-text-muted)' }}>{row.category || '—'}</td>
                                            <td><EventBadge event={row.event} /></td>
                                            <td style={{ textAlign: 'right', color: 'var(--color-text-muted)' }}>
                                                {formatNumber(row.prev_diff, 0)} → {formatNumber(row.cur_diff, 0)}
                                            </td>
                                            <td style={{ textAlign: 'right' }}>
                                                <span
                                                    style={{
                                                        fontWeight: 700,
                                                        color: eventMeta(row.event).cls === 'badge-danger'
                                                            ? 'var(--color-danger)'
                                                            : eventMeta(row.event).cls === 'badge-success'
                                                                ? 'var(--color-success)'
                                                                : eventMeta(row.event).cls === 'badge-warning'
                                                                    ? 'var(--color-warning)'
                                                                    : 'var(--color-text)',
                                                    }}
                                                >
                                                    {row.delta > 0 ? '+' : ''}{formatNumber(row.delta, 0)}
                                                </span>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}

            {/* styled-jsx скоупит keyframes per-компонент (паттерн соседних вкладок). */}
            <style jsx>{`
                @keyframes shimmer {
                    0% {
                        background-position: 200% 0;
                    }
                    100% {
                        background-position: -200% 0;
                    }
                }
            `}</style>
        </div>
    );
}
