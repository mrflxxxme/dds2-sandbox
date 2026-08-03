'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber } from '@/lib/utils';
import PageHeader from '@/components/PageHeader';
import PageGuard from '@/components/PageGuard';
import type { AbTestResults, AbTestVariantStats } from '@/types/api';

const REFRESH_MS = 60_000; // идущий тест обновляем раз в минуту

/** Фото варианта: авторизованный fetch → objectURL (тег <img> не умеет слать JWT). */
function VariantPhoto({ testId, variantId, dimmed }: { testId: number; variantId: number; dimmed: boolean }) {
    const [url, setUrl] = useState<string | null>(null);
    useEffect(() => {
        let alive = true;
        let obj: string | null = null;
        api.getAbTestPhotoBlob(testId, variantId)
            .then((blob) => {
                if (!alive) return;
                obj = URL.createObjectURL(blob);
                setUrl(obj);
            })
            .catch(() => setUrl(null));
        return () => {
            alive = false;
            if (obj) URL.revokeObjectURL(obj);
        };
    }, [testId, variantId]);
    return (
        <div
            style={{
                width: 120, height: 160, borderRadius: 12, overflow: 'hidden',
                background: 'var(--color-border)', opacity: dimmed ? 0.4 : 1, margin: '0 auto',
            }}
        >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {url && <img src={url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />}
        </div>
    );
}

function VariantBadges({ v }: { v: AbTestVariantStats }) {
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'center', minHeight: 24 }}>
            {v.is_active && <span className="badge badge-info">Сейчас на карточке</span>}
            {v.is_winner && <span className="badge badge-success">🏆 Победитель</span>}
            {v.excluded && <span className="badge badge-secondary">Исключено</span>}
        </div>
    );
}

export default function AbTestDetailPage() {
    const params = useParams<{ slug: string; testId: string }>();
    const router = useRouter();
    const testId = Number(params.testId);

    const [data, setData] = useState<AbTestResults | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);
    const fileRef = useRef<HTMLInputElement>(null);

    const load = useCallback(async (silent = false) => {
        if (!silent) setLoading(true);
        setError(null);
        try {
            setData(await api.getAbTest(testId));
        } catch (e) {
            if (!silent) setError(e instanceof Error ? e.message : 'Не удалось загрузить тест');
        } finally {
            if (!silent) setLoading(false);
        }
    }, [testId]);

    useEffect(() => {
        void load();
    }, [load]);

    useEffect(() => {
        if (data?.test.status !== 'running') return;
        const t = setInterval(() => void load(true), REFRESH_MS);
        return () => clearInterval(t);
    }, [data?.test.status, load]);

    const act = useCallback(async (fn: () => Promise<unknown>) => {
        setBusy(true);
        setActionError(null);
        try {
            await fn();
            await load(true);
        } catch (e) {
            setActionError(e instanceof Error ? e.message : 'Действие не выполнено');
        } finally {
            setBusy(false);
        }
    }, [load]);

    const uploadFiles = useCallback(async (files: FileList | null) => {
        if (!files?.length) return;
        setBusy(true);
        setActionError(null);
        try {
            for (const f of Array.from(files)) {
                await api.uploadAbTestPhoto(testId, f);
            }
            await load(true);
        } catch (e) {
            setActionError(e instanceof Error ? e.message : 'Фото не загрузилось');
        } finally {
            setBusy(false);
            if (fileRef.current) fileRef.current.value = '';
        }
    }, [testId, load]);

    if (loading) {
        return (
            <PageGuard page="ab-tests">
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка…</div>
            </PageGuard>
        );
    }
    if (error || !data) {
        return (
            <PageGuard page="ab-tests">
                <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                    {error ?? 'Тест не найден'}{' '}
                    <button className="btn btn-sm btn-secondary" onClick={() => void load()}>Повторить</button>
                </div>
            </PageGuard>
        );
    }

    const { test, variants, rounds } = data;
    const isDraft = test.status === 'draft';
    const uploaded = variants.filter((v) => !v.is_control);
    const variantById = new Map(variants.map((v) => [v.id, v]));

    const pct = (num: number, den: number, digits = 1): string | null =>
        den > 0 ? `${formatNumber((num / den) * 100, digits)}%` : null;
    const withPct = (count: number, share: string | null) => (
        <>
            {formatNumber(count, 0)}
            {share != null && <span style={{ color: 'var(--color-text-dim)', fontSize: 12 }}> · {share}</span>}
        </>
    );
    const metricRows: { label: string; render: (v: AbTestVariantStats) => ReactNode; hint?: string }[] = [
        { label: 'Показы', render: (v) => formatNumber(v.views, 0) },
        { label: 'Клики', render: (v) => formatNumber(v.clicks, 0) },
        { label: 'CTR', render: (v) => (v.views ? `${formatNumber(v.ctr, 2)}%` : '—') },
        {
            label: 'Откл. от лучшего',
            render: (v) => (v.ctr_gap == null ? '—' : v.ctr_gap === 0 ? 'Лучший' : `−${formatNumber(v.ctr_gap, 2)} п.п.`),
        },
        { label: 'Побед в раундах', render: (v) => formatNumber(v.round_wins, 0) },
        { label: 'Кругов', render: (v) => formatNumber(v.rounds, 0) },
        {
            label: 'Корзины (реклама)',
            render: (v) => withPct(v.atbs, pct(v.atbs, v.clicks)),
            hint: 'Процент — конверсия из кликов в корзину',
        },
        {
            label: 'Заказы (реклама)',
            render: (v) => withPct(v.orders, pct(v.orders, v.atbs)),
            hint: 'Процент — конверсия из корзины в заказ',
        },
        {
            label: '≈ Переходы (все)',
            render: (v) => formatNumber(v.organic_open, 0),
            hint: 'Вся воронка товара за время кругов этого фото — конверсии между кругами размазываются, сравнивать на большой выборке',
        },
        {
            label: '≈ Корзины (все)',
            render: (v) => withPct(v.organic_cart, pct(v.organic_cart, v.organic_open)),
            hint: 'Процент — конверсия из переходов в корзину',
        },
        {
            label: '≈ Заказы (все)',
            render: (v) => withPct(v.organic_orders, pct(v.organic_orders, v.organic_cart)),
            hint: 'Процент — конверсия из корзины в заказ',
        },
        { label: 'Прогресс к цели', render: (v) => `${formatNumber(v.progress_pct, 0)}%` },
    ];

    return (
        <PageGuard page="ab-tests">
            <PageHeader
                title={`🧪 ${test.title || test.name}`}
                subtitle={`Артикул ${test.nm_id} · кампания ${test.campaign_id} · круг ${test.round_minutes} мин (досрочно при ${formatNumber(test.views_per_round, 0)} показах) · цель ${formatNumber(test.target_views, 0)} на фото`}
            />

            <div className="glass-card" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <button className="btn btn-sm btn-secondary" onClick={() => router.push(`/p/${params.slug}/ab-tests`)}>
                    ← Все тесты
                </button>
                <span className={`badge ${
                    test.status === 'running' ? 'badge-info'
                    : test.status === 'finished' ? 'badge-success'
                    : test.status === 'paused' ? 'badge-warning'
                    : test.status === 'error' ? 'badge-danger' : 'badge-secondary'
                }`}>
                    {{ draft: 'Черновик', running: 'Идёт', paused: 'Пауза', finished: 'Завершён', error: 'Ошибка' }[test.status]}
                </span>
                {test.started_at && (
                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        старт {formatDateTime(test.started_at)}{test.finished_at ? ` · финиш ${formatDateTime(test.finished_at)}` : ''}
                    </span>
                )}
                <span style={{ flex: 1 }} />
                {isDraft && (
                    <button
                        className="btn btn-primary"
                        disabled={busy || uploaded.length === 0}
                        title={uploaded.length === 0 ? 'Сначала загрузите хотя бы одно фото' : undefined}
                        onClick={() => void act(() => api.startAbTest(test.id))}
                    >
                        ▶ Запустить тест
                    </button>
                )}
                {(test.status === 'running' || test.status === 'paused') && (
                    <button className="btn btn-danger" disabled={busy} onClick={() => void act(() => api.stopAbTest(test.id))}>
                        ⏹ Завершить досрочно
                    </button>
                )}
                {test.status === 'paused' && (
                    <button className="btn btn-primary" disabled={busy} onClick={() => void act(() => api.resumeAbTest(test.id))}>
                        ▶ Продолжить
                    </button>
                )}
                {test.status === 'finished' && test.winner_variant_id && (
                    <button
                        className="btn btn-success"
                        disabled={busy || !!test.winner_applied_at}
                        onClick={() => void act(() => api.applyAbWinner(test.id))}
                    >
                        {test.winner_applied_at ? '✓ Победитель применён' : '🏆 Применить победителя'}
                    </button>
                )}
            </div>

            {test.status === 'paused' && test.pause_reason && (
                <div className="glass-card" style={{ marginBottom: 16, borderLeft: '3px solid var(--color-warning)', color: 'var(--color-text-muted)', fontSize: 13 }}>
                    ⚠️ {test.pause_reason}
                </div>
            )}
            {actionError && (
                <div className="glass-card" style={{ marginBottom: 16, color: 'var(--color-danger)', fontSize: 13 }}>{actionError}</div>
            )}

            {isDraft && (
                <div className="glass-card" style={{ marginBottom: 16 }}>
                    <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>
                        Варианты фото ({uploaded.length}/10, не считая текущего)
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                        JPEG/PNG/WebP, минимум 700×900, до 10 МБ. Текущее главное фото участвует автоматически как контроль.
                        Без текста и коллажей — WB может понизить такую карточку в поиске.
                    </div>
                    <input
                        ref={fileRef}
                        type="file"
                        accept="image/jpeg,image/png,image/webp"
                        multiple
                        style={{ display: 'none' }}
                        onChange={(e) => void uploadFiles(e.target.files)}
                    />
                    <button className="btn btn-secondary" disabled={busy || uploaded.length >= 10} onClick={() => fileRef.current?.click()}>
                        {busy ? 'Загрузка…' : '+ Добавить фото'}
                    </button>
                </div>
            )}

            {variants.length > 0 && (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto', marginBottom: 16 }}>
                    <table style={{ borderCollapse: 'collapse', fontSize: 14, minWidth: '100%' }}>
                        <thead>
                            <tr>
                                <th style={{ minWidth: 160, padding: 16 }} />
                                {variants.map((v) => (
                                    <th key={v.id} style={{ padding: 16, textAlign: 'center', verticalAlign: 'top', minWidth: 150 }}>
                                        <div style={{ position: 'relative', display: 'inline-block' }}>
                                            <VariantPhoto testId={test.id} variantId={v.id} dimmed={v.excluded} />
                                            {!isDraft && !v.excluded && !v.is_control && test.status === 'running' && (
                                                <button
                                                    className="btn btn-sm btn-danger"
                                                    title="Исключить из теста (данные останутся)"
                                                    style={{ position: 'absolute', top: -8, right: -8, borderRadius: 24, padding: '2px 9px' }}
                                                    disabled={busy}
                                                    onClick={() => void act(() => api.excludeAbVariant(test.id, v.id))}
                                                >
                                                    −
                                                </button>
                                            )}
                                            {!isDraft && v.excluded && test.status === 'running' && (
                                                <button
                                                    className="btn btn-sm btn-success"
                                                    title="Вернуть в тест"
                                                    style={{ position: 'absolute', top: -8, right: -8, borderRadius: 24, padding: '2px 8px' }}
                                                    disabled={busy}
                                                    onClick={() => void act(() => api.includeAbVariant(test.id, v.id))}
                                                >
                                                    +
                                                </button>
                                            )}
                                            {isDraft && !v.is_control && (
                                                <button
                                                    className="btn btn-sm btn-danger"
                                                    title="Удалить фото"
                                                    style={{ position: 'absolute', top: -8, right: -8, borderRadius: 24, padding: '2px 9px' }}
                                                    disabled={busy}
                                                    onClick={() => void act(() => api.deleteAbTestPhoto(test.id, v.id))}
                                                >
                                                    ×
                                                </button>
                                            )}
                                        </div>
                                        <div style={{ marginTop: 8, fontWeight: 600, color: v.excluded ? 'var(--color-text-dim)' : 'var(--color-text)' }}>
                                            {v.is_control ? 'Текущее фото' : `Вариант ${v.position}`}
                                        </div>
                                        <VariantBadges v={v} />
                                        {!v.enough_data && !isDraft && v.views > 0 && (
                                            <div style={{ fontSize: 11, color: 'var(--color-warning)', marginTop: 4 }}>мало данных</div>
                                        )}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        {!isDraft && (
                            <tbody>
                                {metricRows.map((row) => (
                                    <tr key={row.label} style={{ borderTop: '1px solid var(--color-border)' }}>
                                        <td style={{ padding: '10px 16px', color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }} title={row.hint}>
                                            {row.label}{row.hint ? ' ⓘ' : ''}
                                        </td>
                                        {variants.map((v) => (
                                            <td
                                                key={v.id}
                                                style={{
                                                    padding: '10px 16px', textAlign: 'center',
                                                    color: v.excluded ? 'var(--color-text-dim)' : 'var(--color-text)',
                                                    fontWeight: row.label === 'CTR' ? 600 : 400,
                                                }}
                                            >
                                                {row.render(v)}
                                            </td>
                                        ))}
                                    </tr>
                                ))}
                            </tbody>
                        )}
                    </table>
                </div>
            )}

            {!isDraft && rounds.length > 0 && (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                    <div style={{ padding: '16px 16px 0', fontSize: 15, fontWeight: 600 }}>История кругов</div>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            <tr style={{ color: 'var(--color-text-muted)', fontSize: 12 }}>
                                <th style={{ textAlign: 'left', padding: '10px 16px' }}>№</th>
                                <th style={{ textAlign: 'left', padding: '10px 16px' }}>Фото</th>
                                <th style={{ textAlign: 'left', padding: '10px 16px' }}>Начало</th>
                                <th style={{ textAlign: 'left', padding: '10px 16px' }}>Конец</th>
                                <th style={{ textAlign: 'right', padding: '10px 16px' }}>Показы</th>
                                <th style={{ textAlign: 'right', padding: '10px 16px' }}>Клики</th>
                                <th style={{ textAlign: 'right', padding: '10px 16px' }}>CTR</th>
                                <th style={{ textAlign: 'right', padding: '10px 16px' }}>Корзины</th>
                                <th style={{ textAlign: 'right', padding: '10px 16px' }}>Заказы</th>
                                <th style={{ textAlign: 'left', padding: '10px 16px' }}>Пометки</th>
                            </tr>
                        </thead>
                        <tbody>
                            {[...rounds].reverse().map((r) => {
                                const v = variantById.get(r.variant_id);
                                return (
                                    <tr key={r.round_no} style={{ borderTop: '1px solid var(--color-border)' }}>
                                        <td style={{ padding: '8px 16px' }}>{r.round_no}</td>
                                        <td style={{ padding: '8px 16px' }}>
                                            {v ? (v.is_control ? 'Текущее' : `Вариант ${v.position}`) : r.variant_id}
                                        </td>
                                        <td style={{ padding: '8px 16px', whiteSpace: 'nowrap' }}>{formatDateTime(r.started_at)}</td>
                                        <td style={{ padding: '8px 16px', whiteSpace: 'nowrap' }}>
                                            {r.ended_at ? formatDateTime(r.ended_at) : <span className="badge badge-info">идёт</span>}
                                        </td>
                                        <td style={{ padding: '8px 16px', textAlign: 'right' }}>{formatNumber(r.views, 0)}</td>
                                        <td style={{ padding: '8px 16px', textAlign: 'right' }}>{formatNumber(r.clicks, 0)}</td>
                                        <td style={{ padding: '8px 16px', textAlign: 'right' }}>{r.views ? `${formatNumber(r.ctr, 2)}%` : '—'}</td>
                                        <td style={{ padding: '8px 16px', textAlign: 'right' }}>{formatNumber(r.atbs, 0)}</td>
                                        <td style={{ padding: '8px 16px', textAlign: 'right' }}>{formatNumber(r.orders, 0)}</td>
                                        <td style={{ padding: '8px 16px', fontSize: 12, color: 'var(--color-warning)' }}>
                                            {r.flags.campaign_paused ? 'кампания стояла ' : ''}
                                            {r.flags.apply_errors ? (
                                                <span title={r.flags.last_apply_error ? String(r.flags.last_apply_error) : undefined}>
                                                    {r.ended_at
                                                        ? `смена фото с ${String(r.flags.apply_errors)} попытки`
                                                        : `смена фото не проходит (попыток: ${String(r.flags.apply_errors)})`}
                                                </span>
                                            ) : ''}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </PageGuard>
    );
}
