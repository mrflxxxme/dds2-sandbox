'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import PageHeader from '@/components/PageHeader';
import KpiCard from '@/components/KpiCard';
import type { Review, ReviewsListResponse } from '@/types/api';

type Tab = 'unanswered' | 'answered';

function Stars({ rating }: { rating: number }) {
    const r = Math.max(0, Math.min(5, rating));
    return (
        <span style={{ color: 'var(--color-warning)', letterSpacing: 1 }} title={`${r} / 5`}>
            {'★'.repeat(r)}
            <span style={{ color: 'var(--color-border)' }}>{'★'.repeat(5 - r)}</span>
        </span>
    );
}

function ReviewCard({ review }: { review: Review }) {
    return (
        <div className="glass-card" style={{ padding: 20, marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
                <div style={{ minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        {review.rating > 0 && <Stars rating={review.rating} />}
                        {review.user_name && (
                            <span style={{ fontWeight: 600, fontSize: 14 }}>{review.user_name}</span>
                        )}
                        {review.is_answered ? (
                            <span className="badge badge-success">Отвечен</span>
                        ) : (
                            <span className="badge badge-warning">Без ответа</span>
                        )}
                    </div>
                    <div style={{ color: 'var(--color-text-dim)', fontSize: 13, marginTop: 4 }}>
                        {[review.brand, review.product_name].filter(Boolean).join(' · ') || '—'}
                        {review.article ? ` · ${review.article}` : ''}
                        {review.nm_id ? ` · nmID ${review.nm_id}` : ''}
                    </div>
                </div>
                <div style={{ color: 'var(--color-text-dim)', fontSize: 13, whiteSpace: 'nowrap' }}>
                    {review.created_date ? formatDate(review.created_date) : ''}
                </div>
            </div>

            {review.text && (
                <p style={{ marginTop: 12, marginBottom: 0, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
                    {review.text}
                </p>
            )}

            {(review.pros || review.cons) && (
                <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 4, fontSize: 14 }}>
                    {review.pros && (
                        <div><span style={{ color: 'var(--color-success)', fontWeight: 600 }}>+ </span>{review.pros}</div>
                    )}
                    {review.cons && (
                        <div><span style={{ color: 'var(--color-danger)', fontWeight: 600 }}>− </span>{review.cons}</div>
                    )}
                </div>
            )}
        </div>
    );
}

export default function ReviewsPage() {
    const [data, setData] = useState<ReviewsListResponse | null>(null);
    const [tab, setTab] = useState<Tab>('unanswered');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async (currentTab: Tab) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getReviews({ isAnswered: currentTab === 'answered', take: 200 });
            setData(res);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить отзывы');
            setData(null);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        load(tab);
    }, [tab, load]);

    const items = data?.items ?? [];

    return (
        <div className="animate-in">
            <PageHeader
                title="Отзывы"
                subtitle="Отзывы покупателей Wildberries"
                icon="⭐"
                actions={
                    <button className="btn btn-secondary btn-sm" onClick={() => load(tab)} disabled={loading}>
                        {loading ? 'Обновление…' : '↻ Обновить'}
                    </button>
                }
            />

            {/* Табы фильтра */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
                <button
                    className={`btn btn-sm ${tab === 'unanswered' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setTab('unanswered')}
                >
                    Без ответа
                </button>
                <button
                    className={`btn btn-sm ${tab === 'answered' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setTab('answered')}
                >
                    Отвеченные
                </button>
            </div>

            {/* KPI */}
            {data && data.has_key && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 16, marginBottom: 20 }}>
                    <KpiCard label="Средняя оценка" value={data.average_rating != null ? formatNumber(data.average_rating, 2) : '—'} />
                    <KpiCard label="Без ответа" value={formatNumber(data.count_unanswered, 0)} />
                    <KpiCard label="В архиве" value={formatNumber(data.count_archive, 0)} />
                    <KpiCard label="Показано" value={formatNumber(items.length, 0)} />
                </div>
            )}

            {/* error */}
            {error && (
                <div className="glass-card" style={{ marginBottom: 20, color: 'var(--color-danger)' }}>
                    {error}{' '}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={() => load(tab)}>
                        Повторить
                    </button>
                </div>
            )}

            {/* no WB key */}
            {!loading && !error && data && !data.has_key && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>🔑</div>
                    <h3 style={{ margin: '0 0 8px' }}>WB-ключ не настроен</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        Чтобы видеть отзывы покупателей, добавьте API-ключ Wildberries со scope
                        «Вопросы и отзывы» в разделе «Настройка проекта» → Интеграции.
                    </p>
                </div>
            )}

            {/* loading */}
            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>
                    Загрузка отзывов…
                </div>
            )}

            {/* empty */}
            {!loading && !error && data && data.has_key && items.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>💬</div>
                    <h3 style={{ margin: '0 0 8px' }}>Отзывов нет</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        {tab === 'unanswered' ? 'Все отзывы обработаны — без ответа ничего нет.' : 'Отвеченных отзывов пока нет.'}
                    </p>
                </div>
            )}

            {/* data */}
            {!loading && !error && data && data.has_key && items.length > 0 && (
                <div>
                    {items.map((r) => (
                        <ReviewCard key={r.id} review={r} />
                    ))}
                </div>
            )}
        </div>
    );
}
