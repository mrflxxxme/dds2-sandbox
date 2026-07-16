'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import KpiCard from '@/components/KpiCard';
import type { Review, ReviewsListResponse } from '@/types/api';

type SubTab = 'unanswered' | 'answered';

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

const PAGE = 100; // отзывов за подгрузку

export default function ReviewsListTab() {
    const [meta, setMeta] = useState<ReviewsListResponse | null>(null);
    const [items, setItems] = useState<Review[]>([]);
    const [sub, setSub] = useState<SubTab>('unanswered');
    const [loading, setLoading] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [error, setError] = useState('');

    // Загрузка первой страницы среза (сброс накопленного списка).
    const load = useCallback(async (currentTab: SubTab) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getReviews({ isAnswered: currentTab === 'answered', take: PAGE, skip: 0 });
            setMeta(res);
            setItems(res.items);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить отзывы');
            setMeta(null);
            setItems([]);
        } finally {
            setLoading(false);
        }
    }, []);

    // Догрузка следующей страницы (append).
    const loadMore = useCallback(async () => {
        setLoadingMore(true);
        setError('');
        try {
            const res = await api.getReviews({ isAnswered: sub === 'answered', take: PAGE, skip: items.length });
            setMeta(res);
            setItems(prev => [...prev, ...res.items]);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить ещё');
        } finally {
            setLoadingMore(false);
        }
    }, [sub, items.length]);

    useEffect(() => {
        load(sub);
    }, [sub, load]);

    const refresh = useCallback(async () => {
        setSyncing(true);
        setError('');
        try {
            await api.syncReviews();
            await load(sub);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось обновить отзывы');
        } finally {
            setSyncing(false);
        }
    }, [sub, load]);

    const data = meta;
    const total = meta?.total ?? 0;
    const hasMore = items.length < total;
    const busy = loading || syncing;

    return (
        <div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 20, alignItems: 'center', flexWrap: 'wrap' }}>
                <button
                    className={`btn btn-sm ${sub === 'unanswered' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setSub('unanswered')}
                >
                    Без ответа
                </button>
                <button
                    className={`btn btn-sm ${sub === 'answered' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setSub('answered')}
                >
                    Отвеченные
                </button>
                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={refresh} disabled={busy}>
                    {syncing ? 'Обновление…' : '↻ Обновить из WB'}
                </button>
            </div>

            {data && data.has_key && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 16, marginBottom: 20 }}>
                    <KpiCard label="Средняя оценка" value={data.average_rating != null ? formatNumber(Number(data.average_rating), 2) : '—'} />
                    <KpiCard label="Без ответа" value={formatNumber(data.count_unanswered, 0)} />
                    <KpiCard label={sub === 'answered' ? 'Отвеченных всего' : 'Без ответа всего'} value={formatNumber(total, 0)} />
                    <KpiCard label="Показано" value={`${formatNumber(items.length, 0)} из ${formatNumber(total, 0)}`} />
                </div>
            )}

            {error && (
                <div className="glass-card" style={{ marginBottom: 20, color: 'var(--color-danger)' }}>
                    {error}{' '}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={() => load(sub)}>
                        Повторить
                    </button>
                </div>
            )}

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

            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>
                    Загрузка отзывов…
                </div>
            )}

            {!loading && !error && data && data.has_key && items.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>💬</div>
                    <h3 style={{ margin: '0 0 8px' }}>Отзывов нет</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        {sub === 'unanswered' ? 'Без ответа ничего нет. Нажмите «Обновить из WB», если ждёте новые.' : 'Отвеченных отзывов пока нет.'}
                    </p>
                </div>
            )}

            {!loading && !error && data && data.has_key && items.length > 0 && (
                <div>
                    {items.map((r) => (
                        <ReviewCard key={r.id} review={r} />
                    ))}
                    {hasMore && (
                        <div style={{ textAlign: 'center', marginTop: 8 }}>
                            <button className="btn btn-secondary" onClick={loadMore} disabled={loadingMore || busy}>
                                {loadingMore ? 'Загрузка…' : `Показать ещё (${formatNumber(total - items.length, 0)})`}
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
