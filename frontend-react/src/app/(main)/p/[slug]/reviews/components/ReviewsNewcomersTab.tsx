'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { NewcomerGroup, NewcomerReview, NewcomersResponse } from '@/types/api';

const DAYS_OPTIONS = [
    { key: 14, label: '2 недели' },
    { key: 30, label: 'Месяц' },
    { key: 60, label: '2 месяца' },
    { key: 90, label: 'Квартал' },
];
const MAX_RATING = 4.6;
const RATING_COLORS: Record<number, string> = { 1: '#ff3b30', 2: '#ff9f0a', 3: '#ffd60a', 4: '#7dd957', 5: '#34c759' };

/** Цвет средней оценки по значению (красный → жёлтый → зелёный). */
function avgColor(v: number | null): string {
    if (v == null) return 'var(--color-text-dim)';
    if (v >= 4.0) return '#7dd957';
    if (v >= 3.0) return '#ff9f0a';
    return '#ff3b30';
}

type GroupMode = 'category' | 'brand' | 'tag';

/** Карточка разреза: имя, число новинок + отзывов, средняя, распределение 5→1. */
function GroupCard({ g }: { g: NewcomerGroup }) {
    const color = avgColor(g.avg_rating);
    const counts: Record<number, number> = { 1: g.r1, 2: g.r2, 3: g.r3, 4: g.r4, 5: g.r5 };
    return (
        <div className="glass-card" style={{ padding: 16, borderTop: `3px solid ${color}` }}>
            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={g.name}>
                {g.name}
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 2 }}>
                <span style={{ fontSize: 26, fontWeight: 700, color }}>{g.avg_rating != null ? formatNumber(Number(g.avg_rating), 2) : '—'}</span>
                <span style={{ color, fontSize: 15 }}>★</span>
            </div>
            <div style={{ color: 'var(--color-text-dim)', fontSize: 12, marginBottom: 12 }}>
                Новинок: {formatNumber(g.products, 0)} · Отзывов: {formatNumber(g.count, 0)}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {[5, 4, 3, 2, 1].map(n => {
                    const cnt = counts[n];
                    const rated = g.r1 + g.r2 + g.r3 + g.r4 + g.r5;
                    const pct = rated ? (cnt / rated) * 100 : 0;
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

export default function ReviewsNewcomersTab() {
    const [data, setData] = useState<NewcomersResponse | null>(null);
    const [days, setDays] = useState(30);
    const [groupMode, setGroupMode] = useState<GroupMode>('category');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async (d: number) => {
        setLoading(true);
        setError('');
        try {
            setData(await api.getReviewsNewcomers(d, MAX_RATING));
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить новинки');
            setData(null);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(days); }, [days, load]);

    const items = data?.items ?? [];
    const groups: NewcomerGroup[] = groupMode === 'category'
        ? (data?.by_category ?? [])
        : groupMode === 'brand'
            ? (data?.by_brand ?? [])
            : (data?.by_tag ?? []);

    const columns: Column[] = useMemo(() => [
        {
            key: 'name', label: 'Товар',
            render: (v: string, row: NewcomerReview) => (
                <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 260 }} title={v}>{v}</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>nmID {row.nm_id}</div>
                </div>
            ),
        },
        { key: 'brand', label: 'Бренд' },
        { key: 'subject', label: 'Предмет' },
        {
            key: 'first_date', label: 'Старт продаж',
            render: (v: string, row: NewcomerReview) => (
                <span style={{ fontSize: 13 }}>{formatDate(v)}<span style={{ color: 'var(--color-text-dim)' }}> · {formatNumber(row.days_on_sale, 0)} дн</span></span>
            ),
        },
        {
            key: 'avg_rating', label: 'Рейтинг',
            render: (v: number | null) => (
                <span style={{ fontWeight: 700, color: avgColor(v) }}>{v != null ? `${formatNumber(Number(v), 2)} ★` : '—'}</span>
            ),
        },
        { key: 'count', label: 'Отзывов', format: 'number' as const },
        {
            key: 'count_unanswered', label: 'Без ответа',
            render: (v: number) => (
                <span style={v > 0 ? { color: 'var(--color-warning)', fontWeight: 600 } : undefined}>{formatNumber(v, 0)}</span>
            ),
        },
        {
            key: 'r1', label: '★1–2',
            render: (_v: number, row: NewcomerReview) => (
                <span style={{ color: 'var(--color-danger)', fontWeight: 600 }}>{formatNumber(row.r1 + row.r2, 0)}</span>
            ),
        },
    ], []);

    return (
        <div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>На продаже:</span>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {DAYS_OPTIONS.map(o => (
                        <button
                            key={o.key}
                            className={`btn btn-sm ${days === o.key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setDays(o.key)}
                            disabled={loading}
                        >
                            {o.label}
                        </button>
                    ))}
                </div>
                <span style={{ fontSize: 13, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>
                    Рейтинг ниже {formatNumber(MAX_RATING, 1)} ★
                </span>
            </div>

            <div style={{ fontSize: 13, color: 'var(--color-text-dim)', marginBottom: 16 }}>
                Новинки (на продаже меньше выбранного срока) с рейтингом ниже порога — ранний сигнал, что товар собирает плохие отзывы.
                «Старт продаж» — по дате первой продажи, а если она неизвестна — по дате первого отзыва.
            </div>

            {error && (
                <div className="glass-card" style={{ marginBottom: 20, color: 'var(--color-danger)' }}>
                    {error}{' '}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={() => load(days)}>Повторить</button>
                </div>
            )}

            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>
                    Загрузка новинок…
                </div>
            )}

            {!loading && !error && data && !data.has_key && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>🔑</div>
                    <h3 style={{ margin: '0 0 8px' }}>WB-ключ не настроен</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        Добавьте API-ключ Wildberries со scope «Вопросы и отзывы» в разделе
                        «Настройка проекта» → Интеграции, затем обновите отзывы.
                    </p>
                </div>
            )}

            {!loading && !error && data && data.has_key && items.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>✅</div>
                    <h3 style={{ margin: '0 0 8px' }}>Проблемных новинок нет</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        Среди новинок за выбранный срок нет товаров с рейтингом ниже {formatNumber(MAX_RATING, 1)} ★.
                    </p>
                </div>
            )}

            {!loading && !error && data && data.has_key && items.length > 0 && (
                <>
                    <div style={{ marginBottom: 12, fontSize: 14 }}>
                        Найдено <b>{formatNumber(items.length, 0)}</b> проблемных новинок
                    </div>

                    {/* Распределение проблемных новинок по разрезам (над таблицей — сразу видно) */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '0 0 12px', flexWrap: 'wrap' }}>
                        <h3 style={{ margin: 0, fontSize: 16 }}>Распределение</h3>
                        <div style={{ display: 'flex', gap: 4 }}>
                            <button className={`btn btn-sm ${groupMode === 'category' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setGroupMode('category')}>По предмету</button>
                            <button className={`btn btn-sm ${groupMode === 'brand' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setGroupMode('brand')}>По бренду</button>
                            <button className={`btn btn-sm ${groupMode === 'tag' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setGroupMode('tag')}>По ярлыку</button>
                        </div>
                    </div>
                    {groups.length === 0 ? (
                        <div className="glass-card" style={{ padding: 20, color: 'var(--color-text-dim)', fontSize: 14, marginBottom: 24 }}>Нет данных</div>
                    ) : (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16, marginBottom: 24 }}>
                            {groups.map(g => <GroupCard key={g.name} g={g} />)}
                        </div>
                    )}

                    <h3 style={{ margin: '0 0 12px', fontSize: 16 }}>Список новинок</h3>
                    <TanStackDataTable
                        columns={columns}
                        data={items}
                        exportName="problem_newcomers"
                        enableSorting
                        enablePagination={items.length > 50}
                    />
                </>
            )}
        </div>
    );
}
