'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { NewcomerReview, NewcomersResponse } from '@/types/api';

const DAYS_OPTIONS = [
    { key: 14, label: '2 недели' },
    { key: 30, label: 'Месяц' },
    { key: 60, label: '2 месяца' },
    { key: 90, label: 'Квартал' },
];
const MAX_RATING = 4.6;

/** Цвет средней оценки по значению (красный → жёлтый → зелёный). */
function avgColor(v: number | null): string {
    if (v == null) return 'var(--color-text-dim)';
    if (v >= 4.0) return '#7dd957';
    if (v >= 3.0) return '#ff9f0a';
    return '#ff3b30';
}

export default function ReviewsNewcomersTab() {
    const [data, setData] = useState<NewcomersResponse | null>(null);
    const [days, setDays] = useState(30);
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
