'use client';

/**
 * Вкладка «Динамика черновика» страницы «Анализ сборки».
 *
 * Почасовая история наполнения черновиков по категориям (пишет джоба :40 MSK):
 * сколько штук/коробов/паллет «Сборка» предлагала отправить час за часом.
 * Отвечает на вопрос «менеджеры разбирают черновик в заявки или он висит»:
 * штуки падают → разбирают; плато днями → висит.
 *
 * График: штуки (левая ось) + паллеты (правая). Ниже — срез последнего часа
 * по категориям и дни с почасовой детализацией (Δ к предыдущему часу).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
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
import type { DraftCategoryHistoryPoint } from '@/types/api';

// ─── Config ─────────────────────────────────────────────────────────────────

type PeriodKey = '7' | '14' | '30';

const PERIODS: { key: PeriodKey; label: string }[] = [
    { key: '7', label: '7 дн' },
    { key: '14', label: '14 дн' },
    { key: '30', label: '30 дн' },
];

const ALL_CATEGORIES = '__all__';
const MSK_TZ = 'Europe/Moscow';

const dayKeyFmt = new Intl.DateTimeFormat('en-CA', { timeZone: MSK_TZ, year: 'numeric', month: '2-digit', day: '2-digit' });
const dayLabelFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: MSK_TZ, weekday: 'short', day: 'numeric', month: 'long' });
const hourFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: MSK_TZ, hour: '2-digit', minute: '2-digit' });
const tickFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: MSK_TZ, day: '2-digit', month: '2-digit', hour: '2-digit' });

/** Бэкенд отдаёт naive-UTC ISO (без «Z») — дошиваем зону перед Date. */
function toDate(takenAt: string): Date {
    return new Date(/[Z+]/.test(takenAt.slice(-6)) ? takenAt : `${takenAt}Z`);
}

/** Почасовой агрегат выбранных категорий. */
interface HourAgg {
    ts: number;
    positions: number;
    unitsRows: number;
    unitsPrebook: number;
    boxes: number;
    pallets: number;
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

/** Δ штук за отрезок: минус = разобрали (хорошо), плюс = добавилось, 0 = без движения. */
function DeltaBadge({ delta }: { delta: number }) {
    if (delta === 0) return <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>без движения</span>;
    const down = delta < 0;
    return (
        <span style={{ fontSize: 12, fontWeight: 600, color: down ? 'var(--color-success)' : 'var(--color-warning)' }}>
            {down ? '▼ разобрали' : '▲ добавилось'} {formatNumber(Math.abs(delta), 0)} шт
        </span>
    );
}

// ─── Tab ────────────────────────────────────────────────────────────────────

export default function DraftDynamicsTab({ slug }: { slug: string }) {
    const [points, setPoints] = useState<DraftCategoryHistoryPoint[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [period, setPeriod] = useState<PeriodKey>('14');
    const [category, setCategory] = useState<string>(ALL_CATEGORIES);
    const [snapshotting, setSnapshotting] = useState(false);

    // Счётчик запроса: быстрый клик 7→14→30 не даёт устаревшему ответу
    // перезаписать выбранный период (ревью LOW).
    const loadSeqRef = useRef(0);
    const load = useCallback(async (days: PeriodKey) => {
        const seq = ++loadSeqRef.current;
        setLoading(true);
        setError('');
        try {
            const res = await api.getDraftCategoryHistory(Number(days));
            if (seq !== loadSeqRef.current) return;
            setPoints(res.points || []);
        } catch (e) {
            if (seq !== loadSeqRef.current) return;
            setError(e instanceof Error ? e.message : 'Не удалось загрузить историю');
        } finally {
            if (seq === loadSeqRef.current) setLoading(false);
        }
    }, []);

    useEffect(() => { load(period); }, [load, period]);

    // «Снять срез сейчас» — не ждать почасовую джобу после правок черновика.
    const handleSnapshot = useCallback(async () => {
        if (snapshotting) return;
        setSnapshotting(true);
        try {
            await api.snapshotDraftCategoryHistory();
            await load(period);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось снять срез');
        } finally {
            setSnapshotting(false);
        }
    }, [snapshotting, load, period]);

    // Категории по убыванию последних штук — селект и порядок таблицы среза.
    const categories = useMemo(() => {
        const totals = new Map<string, number>();
        for (const p of points || []) totals.set(p.category, (totals.get(p.category) || 0) + p.units_rows + p.units_prebook);
        return Array.from(totals.entries()).sort((a, b) => b[1] - a[1]).map(([c]) => c);
    }, [points]);

    const filtered = useMemo(
        () => (points || []).filter(p => category === ALL_CATEGORIES || p.category === category),
        [points, category],
    );

    // Час → агрегат выбранных категорий (категории делят SKU без пересечений —
    // суммы позиций/штук честные). Отсортировано по времени.
    const hours = useMemo<HourAgg[]>(() => {
        const byTs = new Map<number, HourAgg>();
        for (const p of filtered) {
            const ts = toDate(p.taken_at).getTime();
            const agg = byTs.get(ts) || { ts, positions: 0, unitsRows: 0, unitsPrebook: 0, boxes: 0, pallets: 0 };
            agg.positions += p.positions;
            agg.unitsRows += p.units_rows;
            agg.unitsPrebook += p.units_prebook;
            agg.boxes += p.boxes;
            agg.pallets += p.pallets;
            byTs.set(ts, agg);
        }
        return Array.from(byTs.values()).sort((a, b) => a.ts - b.ts);
    }, [filtered]);

    const chartData = useMemo(
        () => hours.map(h => ({ ts: h.ts, units: h.unitsRows + h.unitsPrebook, pallets: Math.round(h.pallets * 10) / 10 })),
        [hours],
    );

    // Δ штук к предыдущему часу per ts — O(n) вместо findIndex в рендере (ревью LOW).
    const deltaByTs = useMemo(() => {
        const m = new Map<number, number | null>();
        hours.forEach((h, i) => {
            m.set(h.ts, i === 0 ? null
                : (h.unitsRows + h.unitsPrebook) - (hours[i - 1].unitsRows + hours[i - 1].unitsPrebook));
        });
        return m;
    }, [hours]);

    // Дни (MSK), свежие сверху; внутри дня часы свежие сверху + Δ к предыдущему часу.
    const days = useMemo(() => {
        const map = new Map<string, HourAgg[]>();
        for (const h of hours) {
            const key = dayKeyFmt.format(new Date(h.ts));
            (map.get(key) || map.set(key, []).get(key)!).push(h);
        }
        return Array.from(map.entries())
            .sort((a, b) => (a[0] < b[0] ? 1 : -1))
            .map(([key, list]) => {
                const first = list[0];
                const last = list[list.length - 1];
                return {
                    key,
                    label: dayLabelFmt.format(new Date(first.ts)),
                    hoursDesc: [...list].reverse(),
                    firstUnits: first.unitsRows + first.unitsPrebook,
                    lastUnits: last.unitsRows + last.unitsPrebook,
                    lastPallets: last.pallets,
                };
            });
    }, [hours]);

    // Срез последнего часа по категориям (для выбранной — только она).
    const latest = useMemo(() => {
        if (!filtered.length) return null;
        const lastTs = Math.max(...filtered.map(p => toDate(p.taken_at).getTime()));
        const rows = filtered
            .filter(p => toDate(p.taken_at).getTime() === lastTs)
            .sort((a, b) => (b.units_rows + b.units_prebook) - (a.units_rows + a.units_prebook));
        return { ts: lastTs, rows };
    }, [filtered]);

    const isEmpty = !loading && !error && points !== null && points.length === 0;
    const hasData = !loading && !error && hours.length > 0;
    // Точки есть, но фильтр категории за период дал пусто — своя ветка (ревью LOW).
    const emptyFiltered = !loading && !error && points !== null && points.length > 0 && hours.length === 0;

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* Фильтры */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <select
                    className="form-select"
                    style={{ width: 'auto', minWidth: 180 }}
                    value={category}
                    onChange={e => setCategory(e.target.value)}
                    disabled={loading}
                >
                    <option value={ALL_CATEGORIES}>Все категории</option>
                    {categories.map(c => <option key={c} value={c}>{c}</option>)}
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
                <Link href={`/p/${slug}/warehouse/assembly/distribute`} className="btn btn-sm btn-secondary" style={{ marginLeft: 'auto' }}>
                    Открыть Сборку →
                </Link>
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
                    <button type="button" className="btn btn-sm btn-secondary" onClick={() => load(period)}>Повторить</button>
                </div>
            )}

            {/* Empty: снапшоты копятся вперёд, без бэкфилла — это не ошибка */}
            {isEmpty && (
                <div className="glass-card" style={{ textAlign: 'center', padding: '48px 16px' }}>
                    <div style={{ fontSize: 36, marginBottom: 12 }}>⏱</div>
                    <div style={{ fontSize: 15, fontWeight: 500, marginBottom: 6 }}>История ещё копится</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 13, maxWidth: 460, margin: '0 auto 16px' }}>
                        Срезы черновиков пишутся раз в час. Первую точку можно снять прямо сейчас.
                    </div>
                    <button type="button" className="btn btn-sm btn-primary" onClick={handleSnapshot} disabled={snapshotting}>
                        {snapshotting ? 'Срез…' : '⏱ Снять срез сейчас'}
                    </button>
                </div>
            )}

            {/* Пусто после фильтра категории */}
            {emptyFiltered && (
                <div className="glass-card" style={{ textAlign: 'center', padding: '32px 16px' }}>
                    <div style={{ fontSize: 14, color: 'var(--color-text-muted)' }}>
                        По категории «{category === ALL_CATEGORIES ? 'Все' : category}» за выбранный период срезов нет.
                    </div>
                </div>
            )}

            {/* График: штуки + паллеты по часам */}
            {hasData && (
                <div className="glass-card" style={{ padding: 20 }}>
                    <SectionTitle>Штуки и паллеты в черновике · по часам (MSK)</SectionTitle>
                    <ResponsiveContainer width="100%" height={280}>
                        <ComposedChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                            <XAxis
                                dataKey="ts"
                                type="number"
                                scale="time"
                                domain={['dataMin', 'dataMax']}
                                tickFormatter={(ts: number) => tickFmt.format(new Date(ts))}
                                tick={{ fontSize: 11 }}
                                minTickGap={48}
                            />
                            <YAxis yAxisId="units" tick={{ fontSize: 11 }} tickFormatter={(v: number) => formatNumber(v, 0)} />
                            <YAxis yAxisId="pallets" orientation="right" tick={{ fontSize: 11 }} />
                            <RechartsTooltip
                                labelFormatter={(ts) => tickFmt.format(new Date(Number(ts)))}
                                formatter={(value: number | string, name: string) =>
                                    [name === 'Паллеты' ? formatNumber(Number(value), 1) : formatNumber(Number(value), 0), name]}
                            />
                            <RechartsLegend wrapperStyle={{ fontSize: 12 }} />
                            <Line yAxisId="units" type="stepAfter" dataKey="units" name="Штуки" stroke="var(--color-accent)" strokeWidth={2} dot={false} />
                            <Line yAxisId="pallets" type="stepAfter" dataKey="pallets" name="Паллеты" stroke="var(--color-warning)" strokeWidth={2} dot={false} />
                        </ComposedChart>
                    </ResponsiveContainer>
                </div>
            )}

            {/* Срез последнего часа по категориям */}
            {hasData && latest && (
                <div className="glass-card" style={{ padding: 20 }}>
                    <SectionTitle>Последний срез · {tickFmt.format(new Date(latest.ts))} MSK</SectionTitle>
                    <div style={{ overflowX: 'auto' }}>
                        <table className="data-table" style={{ width: '100%', fontSize: 13 }}>
                            <thead>
                                <tr>
                                    <th style={{ textAlign: 'left' }}>Категория</th>
                                    <th style={{ textAlign: 'right' }}>Позиций</th>
                                    <th style={{ textAlign: 'right' }}>Шт (строки)</th>
                                    <th style={{ textAlign: 'right' }}>Шт (предбронь)</th>
                                    <th style={{ textAlign: 'right' }}>Коробов</th>
                                    <th style={{ textAlign: 'right' }}>Паллет</th>
                                </tr>
                            </thead>
                            <tbody>
                                {latest.rows.map(p => (
                                    <tr key={p.category}>
                                        <td>{p.category}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(p.positions, 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(p.units_rows, 0)}</td>
                                        <td style={{ textAlign: 'right', color: 'var(--color-text-muted)' }}>{formatNumber(p.units_prebook, 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(p.boxes, 0)}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(p.pallets, 1)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {/* Дни: заголовок с Δ за день + почасовая детализация */}
            {hasData && days.map(day => (
                <details key={day.key} className="glass-card" style={{ padding: '14px 20px' }}>
                    <summary style={{ cursor: 'pointer', display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', listStyle: 'none' }}>
                        <span style={{ fontSize: 14, fontWeight: 600 }}>{day.label}</span>
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                            {formatNumber(day.firstUnits, 0)} → {formatNumber(day.lastUnits, 0)} шт
                        </span>
                        <DeltaBadge delta={day.lastUnits - day.firstUnits} />
                        <span style={{ fontSize: 12, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>
                            {formatNumber(day.lastPallets, 1)} паллет · {day.hoursDesc.length} срезов
                        </span>
                    </summary>
                    <div style={{ overflowX: 'auto', marginTop: 12 }}>
                        <table className="data-table" style={{ width: '100%', fontSize: 13 }}>
                            <thead>
                                <tr>
                                    <th style={{ textAlign: 'left' }}>Час (MSK)</th>
                                    <th style={{ textAlign: 'right' }}>Позиций</th>
                                    <th style={{ textAlign: 'right' }}>Шт (строки)</th>
                                    <th style={{ textAlign: 'right' }}>Шт (предбронь)</th>
                                    <th style={{ textAlign: 'right' }}>Коробов</th>
                                    <th style={{ textAlign: 'right' }}>Паллет</th>
                                    <th style={{ textAlign: 'right' }}>Δ шт</th>
                                </tr>
                            </thead>
                            <tbody>
                                {day.hoursDesc.map(h => {
                                    // Δ к предыдущему ЧАСУ (для первого часа дня — к последнему
                                    // часу предыдущего дня); null = самая первая точка истории.
                                    const delta = deltaByTs.get(h.ts) ?? null;
                                    return (
                                        <tr key={h.ts}>
                                            <td>{hourFmt.format(new Date(h.ts))}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(h.positions, 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(h.unitsRows, 0)}</td>
                                            <td style={{ textAlign: 'right', color: 'var(--color-text-muted)' }}>{formatNumber(h.unitsPrebook, 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(h.boxes, 0)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(h.pallets, 1)}</td>
                                            <td style={{ textAlign: 'right' }}>
                                                {delta === null
                                                    ? <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                                                    : <span style={{ color: delta < 0 ? 'var(--color-success)' : delta > 0 ? 'var(--color-warning)' : 'var(--color-text-dim)' }}>
                                                        {delta > 0 ? '+' : ''}{formatNumber(delta, 0)}
                                                    </span>}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </details>
            ))}

            {/* styled-jsx скоупит keyframes per-компонент — без локальной копии
                скелет был бы статичным (паттерн соседних вкладок). */}
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
