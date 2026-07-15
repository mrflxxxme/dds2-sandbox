'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import PageHeader from '@/components/PageHeader';
import PageGuard from '@/components/PageGuard';
import type { AbTestListItem, AbTestStatus } from '@/types/api';

const STATUS_LABEL: Record<AbTestStatus, string> = {
    draft: 'Черновик',
    running: 'Идёт',
    paused: 'Пауза',
    finished: 'Завершён',
    error: 'Ошибка',
};

const STATUS_BADGE: Record<AbTestStatus, string> = {
    draft: 'badge-secondary',
    running: 'badge-info',
    paused: 'badge-warning',
    finished: 'badge-success',
    error: 'badge-danger',
};

function StatusCell({ t }: { t: AbTestListItem }) {
    if (t.status === 'running') {
        return (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 80, height: 6, borderRadius: 8, background: 'var(--color-border)', overflow: 'hidden' }}>
                    <span style={{ display: 'block', width: `${t.progress_pct}%`, height: '100%', background: 'var(--color-accent)' }} />
                </span>
                <span style={{ fontSize: 13, color: 'var(--color-muted)' }}>{formatNumber(t.progress_pct, 0)}%</span>
            </span>
        );
    }
    return (
        <span className={`badge ${STATUS_BADGE[t.status]}`} title={t.pause_reason ?? undefined}>
            {STATUS_LABEL[t.status]}{t.status === 'paused' && t.pause_reason ? ' ⚠️' : ''}
        </span>
    );
}

export default function AbTestsPage() {
    const params = useParams<{ slug: string }>();
    const router = useRouter();
    const [tests, setTests] = useState<AbTestListItem[]>([]);
    const [statusFilter, setStatusFilter] = useState<AbTestStatus | 'all'>('all');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.listAbTests();
            setTests(res.tests);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить тесты');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    const visible = statusFilter === 'all' ? tests : tests.filter((t) => t.status === statusFilter);
    const counts = (s: AbTestStatus) => tests.filter((t) => t.status === s).length;

    return (
        <PageGuard page="ab-tests">
            <PageHeader title="🧪 АБ-тесты фото" subtitle="Тестирование главного фото карточки: ротация вариантов по кругам показов, победитель — по CTR" />

            <div className="glass-card" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                {(['all', 'running', 'paused', 'draft', 'finished'] as const).map((s) => (
                    <button
                        key={s}
                        className={`btn btn-sm ${statusFilter === s ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setStatusFilter(s)}
                    >
                        {s === 'all' ? `Все ${tests.length}` : `${STATUS_LABEL[s]} ${counts(s)}`}
                    </button>
                ))}
                <span style={{ flex: 1 }} />
                <button className="btn btn-primary" onClick={() => router.push(`/p/${params.slug}/ab-tests/create`)}>
                    + Новый тест
                </button>
            </div>

            {loading && <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-muted)' }}>Загрузка…</div>}
            {error && !loading && (
                <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                    {error} <button className="btn btn-sm btn-secondary" onClick={() => void load()}>Повторить</button>
                </div>
            )}
            {!loading && !error && visible.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-muted)', padding: 48 }}>
                    {tests.length === 0
                        ? 'Тестов ещё нет. Создайте первый: выберите артикул, кампанию и загрузите варианты фото.'
                        : 'Нет тестов с таким статусом.'}
                </div>
            )}

            {!loading && !error && visible.length > 0 && (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-muted)', fontSize: 12 }}>
                                <th style={{ textAlign: 'left', padding: '12px 16px' }}>Создан</th>
                                <th style={{ textAlign: 'left', padding: '12px 16px' }}>Товар</th>
                                <th style={{ textAlign: 'left', padding: '12px 16px' }}>Артикул</th>
                                <th style={{ textAlign: 'left', padding: '12px 16px' }}>Кампания</th>
                                <th style={{ textAlign: 'right', padding: '12px 16px' }}>Фото</th>
                                <th style={{ textAlign: 'left', padding: '12px 16px' }}>Статус</th>
                            </tr>
                        </thead>
                        <tbody>
                            {visible.map((t) => (
                                <tr
                                    key={t.id}
                                    onClick={() => router.push(`/p/${params.slug}/ab-tests/${t.id}`)}
                                    style={{ borderBottom: '1px solid var(--color-border)', cursor: 'pointer' }}
                                >
                                    <td style={{ padding: '12px 16px', whiteSpace: 'nowrap' }}>{formatDate(t.created_at)}</td>
                                    <td style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10 }}>
                                        {/* eslint-disable-next-line @next/next/no-img-element */}
                                        <img
                                            src={`${process.env.NEXT_PUBLIC_API_URL}/api/v1/media/product-image/${t.nm_id}`}
                                            alt=""
                                            style={{ width: 32, height: 42, objectFit: 'cover', borderRadius: 8 }}
                                        />
                                        <span>
                                            {t.title || t.name}
                                            <span style={{ display: 'block', fontSize: 12, color: 'var(--color-dim)' }}>{t.vendor_code}</span>
                                        </span>
                                    </td>
                                    <td style={{ padding: '12px 16px' }}>{t.nm_id}</td>
                                    <td style={{ padding: '12px 16px' }}>{t.campaign_id}</td>
                                    <td style={{ padding: '12px 16px', textAlign: 'right' }}>{formatNumber(t.variants_count, 0)}</td>
                                    <td style={{ padding: '12px 16px' }}><StatusCell t={t} /></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </PageGuard>
    );
}
