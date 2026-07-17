'use client';

import { useCallback, useEffect, useState } from 'react';
import {
    LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
    Legend, ResponsiveContainer,
} from 'recharts';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import KpiCard from '@/components/KpiCard';
import type { GroupRating, ProductTag, ReviewsPeriod, ReviewsSummaryResponse } from '@/types/api';

const RATING_COLORS: Record<number, string> = {
    1: '#ff3b30',
    2: '#ff9f0a',
    3: '#ffd60a',
    4: '#7dd957',
    5: '#34c759',
};
const RATING_KEYS: Record<string, string> = { r1: '★1', r2: '★2', r3: '★3', r4: '★4', r5: '★5' };

// Диапазоны выборки (совпадают с backend period). Дефолт — год.
const PERIODS: { key: ReviewsPeriod; label: string }[] = [
    { key: '2w', label: '2 недели' },
    { key: '1m', label: 'Месяц' },
    { key: '3m', label: 'Квартал' },
    { key: '6m', label: 'Полгода' },
    { key: '1y', label: 'Год' },
    { key: 'all', label: 'Всё время' },
];

/** Короткая подпись бакета для оси X: YYYY-MM-DD → DD.MM, YYYY-MM → MM.YY. */
function bucketLabel(bucket: string): string {
    const parts = bucket.split('-');
    if (parts.length === 3) return `${parts[2]}.${parts[1]}`; // день
    if (parts.length === 2) return `${parts[1]}.${parts[0].slice(2)}`; // месяц
    return bucket;
}

function avgStr(v: number | null): string {
    return v != null ? formatNumber(Number(v), 2) : '—';
}

/** Цвет средней оценки по значению (красный → жёлтый → зелёный). */
function avgColor(v: number | null): string {
    if (v == null) return 'var(--color-text-dim)';
    const n = Number(v);
    if (n >= 4.5) return '#34c759';
    if (n >= 4.0) return '#7dd957';
    if (n >= 3.0) return '#ff9f0a';
    return '#ff3b30';
}

/** Карточка сводного рейтинга группы: средняя + распределение 5→1. */
function RatingCard({ g }: { g: GroupRating }) {
    const color = avgColor(g.avg_rating);
    const counts: Record<number, number> = { 1: g.r1, 2: g.r2, 3: g.r3, 4: g.r4, 5: g.r5 };
    return (
        <div className="glass-card" style={{ padding: 16, borderTop: `3px solid ${color}` }}>
            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={g.name}>
                {g.name}
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 2 }}>
                <span style={{ fontSize: 28, fontWeight: 700, color }}>{avgStr(g.avg_rating)}</span>
                <span style={{ color, fontSize: 16 }}>★</span>
            </div>
            <div style={{ color: 'var(--color-text-dim)', fontSize: 12, marginBottom: 12 }}>
                Отзывов: {formatNumber(g.count, 0)}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {[5, 4, 3, 2, 1].map(n => {
                    const cnt = counts[n];
                    const pct = g.count ? (cnt / g.count) * 100 : 0;
                    return (
                        <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                            <span style={{ width: 10, color: 'var(--color-text-dim)' }}>{n}</span>
                            <div style={{ flex: 1, height: 6, background: 'var(--color-border)', borderRadius: 3, overflow: 'hidden' }}>
                                <div style={{ width: `${pct}%`, height: '100%', background: RATING_COLORS[n], borderRadius: 3 }} />
                            </div>
                            <span style={{ width: 54, textAlign: 'right' }}>{formatNumber(cnt, 0)}</span>
                            <span style={{ width: 44, textAlign: 'right', color: 'var(--color-text-dim)' }}>{formatNumber(pct, 1)}%</span>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

type GroupMode = 'category' | 'brand';

export default function ReviewsSummaryTab() {
    const [data, setData] = useState<ReviewsSummaryResponse | null>(null);
    const [tags, setTags] = useState<ProductTag[]>([]);
    const [filterTag, setFilterTag] = useState('');
    const [period, setPeriod] = useState<ReviewsPeriod>('1y');
    const [groupMode, setGroupMode] = useState<GroupMode>('category');
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [error, setError] = useState('');

    // Ярлыки для выпадающего фильтра (как в воронке продаж)
    useEffect(() => {
        api.getProductTags().then(setTags).catch(() => setTags([]));
    }, []);

    const load = useCallback(async (tag: string, p: ReviewsPeriod) => {
        setLoading(true);
        setError('');
        try {
            setData(await api.getReviewsSummary(tag || undefined, p));
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить сводку');
            setData(null);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        load(filterTag, period);
    }, [filterTag, period, load]);

    const refresh = useCallback(async () => {
        setSyncing(true);
        setError('');
        try {
            setData(await api.syncReviews(filterTag || undefined, period));
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось обновить отзывы');
        } finally {
            setSyncing(false);
        }
    }, [filterTag, period]);

    const ratingRows = (data?.monthly_rating ?? []).map(p => ({
        month: p.month,
        avg: p.avg_rating != null ? Number(p.avg_rating) : null,
        count: p.count,
    }));
    const volumeRows = data?.monthly_volume ?? [];
    const groups = groupMode === 'category' ? (data?.by_category ?? []) : (data?.by_brand ?? []);

    return (
        <div>
            {/* Диапазон + фильтр по ярлыку + обновление */}
            <div style={{ display: 'flex', gap: 12, marginBottom: 20, alignItems: 'center', flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {PERIODS.map(p => (
                        <button
                            key={p.key}
                            className={`btn btn-sm ${period === p.key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setPeriod(p.key)}
                            disabled={loading || syncing}
                        >
                            {p.label}
                        </button>
                    ))}
                </div>
                <select
                    className="form-input"
                    style={{ width: 200 }}
                    value={filterTag}
                    onChange={e => setFilterTag(e.target.value)}
                    disabled={loading || syncing}
                >
                    <option value="">Все ярлыки</option>
                    {tags.map(t => <option key={t.id} value={t.name}>{t.name}</option>)}
                </select>
                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={refresh} disabled={loading || syncing}>
                    {syncing ? 'Обновление…' : '↻ Обновить из WB'}
                </button>
            </div>

            {error && (
                <div className="glass-card" style={{ marginBottom: 20, color: 'var(--color-danger)' }}>
                    {error}{' '}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={() => load(filterTag, period)}>Повторить</button>
                </div>
            )}

            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>
                    Загрузка сводки…
                </div>
            )}

            {!loading && !error && data && !data.has_key && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>🔑</div>
                    <h3 style={{ margin: '0 0 8px' }}>WB-ключ не настроен</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        Добавьте API-ключ Wildberries со scope «Вопросы и отзывы» в разделе
                        «Настройка проекта» → Интеграции, затем нажмите «Обновить из WB».
                    </p>
                </div>
            )}

            {!loading && !error && data && data.has_key && data.summary.total === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>💬</div>
                    <h3 style={{ margin: '0 0 8px' }}>Отзывов нет</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        {filterTag
                            ? 'По выбранному ярлыку за этот период отзывов не найдено.'
                            : period !== 'all'
                                ? 'За выбранный период отзывов нет — попробуйте увеличить диапазон или «Обновить из WB».'
                                : 'Нажмите «Обновить из WB», чтобы подтянуть отзывы покупателей.'}
                    </p>
                </div>
            )}

            {!loading && !error && data && data.has_key && data.summary.total > 0 && (
                <>
                    {/* KPI */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 16, marginBottom: 20 }}>
                        <KpiCard label="Средняя оценка" value={avgStr(data.summary.average_rating)} />
                        <KpiCard label="Всего отзывов" value={formatNumber(data.summary.total, 0)} />
                        <KpiCard label="С текстом" value={formatNumber(data.summary.count_with_text, 0)} />
                        <KpiCard label="Без текста" value={formatNumber(data.summary.count_no_text, 0)} />
                        <KpiCard label="Без ответа" value={formatNumber(data.summary.count_unanswered, 0)} />
                        <KpiCard label="Позитивных (4–5)" value={formatNumber(data.summary.count_positive, 0)} />
                        <KpiCard label="Негативных (1–2)" value={formatNumber(data.summary.count_negative, 0)} />
                    </div>

                    {/* Средний рейтинг по месяцам */}
                    <div className="glass-card" style={{ padding: 20, marginBottom: 20 }}>
                        <h3 style={{ margin: '0 0 12px', fontSize: 16 }}>Средний рейтинг по месяцам</h3>
                        <ResponsiveContainer width="100%" height={280}>
                            <LineChart data={ratingRows} margin={{ top: 8, right: 16, bottom: 8, left: -16 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                                <XAxis dataKey="month" tick={{ fontSize: 12 }} tickFormatter={bucketLabel} minTickGap={16} />
                                <YAxis domain={[0, 5]} tick={{ fontSize: 12 }} />
                                <Tooltip
                                    formatter={(v: number) => [formatNumber(v, 2), 'Средняя оценка']}
                                    contentStyle={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 12 }}
                                />
                                <Line type="monotone" dataKey="avg" stroke="#0071e3" strokeWidth={2} dot={{ r: 3 }} connectNulls name="Средняя оценка" />
                            </LineChart>
                        </ResponsiveContainer>
                    </div>

                    {/* Объём отзывов по месяцам с разбивкой по оценкам */}
                    <div className="glass-card" style={{ padding: 20, marginBottom: 20 }}>
                        <h3 style={{ margin: '0 0 12px', fontSize: 16 }}>Объём отзывов по месяцам (по оценкам)</h3>
                        <ResponsiveContainer width="100%" height={280}>
                            <BarChart data={volumeRows} margin={{ top: 8, right: 16, bottom: 8, left: -16 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                                <XAxis dataKey="month" tick={{ fontSize: 12 }} tickFormatter={bucketLabel} minTickGap={16} />
                                <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                                <Tooltip
                                    formatter={(v: number, key: string) => [formatNumber(v, 0), RATING_KEYS[key] ?? key]}
                                    contentStyle={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 12 }}
                                />
                                <Legend formatter={(key: string) => RATING_KEYS[key] ?? key} />
                                {[1, 2, 3, 4, 5].map(n => (
                                    <Bar key={n} dataKey={`r${n}`} stackId="v" fill={RATING_COLORS[n]} name={`r${n}`} />
                                ))}
                            </BarChart>
                        </ResponsiveContainer>
                    </div>

                    {/* Сводный рейтинг — карточки по предмету / бренду */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
                        <h3 style={{ margin: 0, fontSize: 16 }}>Сводный рейтинг</h3>
                        <div style={{ display: 'flex', gap: 4 }}>
                            <button className={`btn btn-sm ${groupMode === 'category' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setGroupMode('category')}>По предмету</button>
                            <button className={`btn btn-sm ${groupMode === 'brand' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setGroupMode('brand')}>По бренду</button>
                        </div>
                    </div>
                    {groups.length === 0 ? (
                        <div className="glass-card" style={{ padding: 20, color: 'var(--color-text-dim)', fontSize: 14 }}>Нет данных</div>
                    ) : (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16 }}>
                            {groups.map(g => <RatingCard key={g.name} g={g} />)}
                        </div>
                    )}
                </>
            )}
        </div>
    );
}
