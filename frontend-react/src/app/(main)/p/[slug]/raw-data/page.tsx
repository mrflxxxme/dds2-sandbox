'use client';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import type { RawSource, RawRefreshProgress } from '@/types/api';
import { reconcileProgress, type ProgressMap } from '@/lib/rawDataProgress';

// Пока идёт дозагрузка — опрашиваем прогресс; в покое поллинга нет
const POLL_MS = 3000;
// Периоды для источников, которые умеют грузиться за диапазон
const RANGES: { days: number; label: string }[] = [
    { days: 7, label: '7 дней' },
    { days: 30, label: '30 дней' },
    { days: 90, label: '90 дней' },
    { days: 365, label: 'Год' },
];

const iso = (d: Date) => d.toISOString().slice(0, 10);

/** Дата последнего факта: у части источников это дата+время (срез «сейчас»). */
function lastDateLabel(src: RawSource): string {
    if (!src.last_date) return '—';
    return formatDate(src.last_date);
}

/** «Свежесть»: сколько дней прошло с последнего факта. */
function freshness(src: RawSource): { color: string; text: string } {
    const freshAt = src.fresh_at ?? src.last_date;  // у кампаний last_date = дата создания последней кампании, не синк
    if (!freshAt) return { color: '#9ca3af', text: 'нет данных' };
    const days = Math.floor((Date.now() - new Date(freshAt).getTime()) / 86400_000);
    if (days <= 1) return { color: '#10b981', text: days === 0 ? 'сегодня' : 'вчера' };
    if (days <= 3) return { color: '#f59e0b', text: `${days} дн. назад` };
    return { color: '#ef4444', text: `${days} дн. назад` };
}

function ProgressBadge({ p }: { p: RawRefreshProgress }) {
    const map = {
        running: { bg: '#eff6ff', color: '#1d4ed8', text: 'загружается…' },
        ok: { bg: '#ecfdf5', color: '#065f46', text: 'загружено' },
        error: { bg: '#fef2f2', color: '#b91c1c', text: 'ошибка' },
    } as const;
    const m = map[p.status];
    return (
        <span title={p.error || undefined}
            style={{ fontSize: 11, padding: '2px 8px', borderRadius: 24, background: m.bg, color: m.color, whiteSpace: 'nowrap' }}>
            {m.text}
        </span>
    );
}

function SourceRow({ src, progress, onRefresh, busy, slug }: {
    src: RawSource;
    progress: RawRefreshProgress | null;
    onRefresh: (key: string, days: number | null) => void;
    busy: boolean;
    slug: string;
}) {
    const [days, setDays] = useState(src.key === 'ad_payments' ? 365 : 30);
    const f = freshness(src);
    const running = progress?.status === 'running';

    return (
        <tr style={{ borderTop: '1px solid #eef0f2' }}>
            <td style={{ padding: '10px 12px', verticalAlign: 'top' }}>
                {/* Настоящая ссылка: правый клик и Cmd+клик открывают таблицу в новой вкладке */}
                <Link href={`/p/${slug}/raw-data/${src.key}`}
                    style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-accent)', textDecoration: 'none' }}>
                    {src.title} →
                </Link>
                <div style={{ fontSize: 11, color: '#6b7280', marginTop: 2, maxWidth: 420 }}>{src.description}</div>
                <div style={{ fontSize: 10.5, color: '#9ca3af', marginTop: 3 }}>
                    <code>{src.table}</code> · {src.source}
                </div>
            </td>
            <td style={{ padding: '10px 12px', textAlign: 'right', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{src.rows != null ? formatNumber(src.rows, 0) : '—'}</div>
                <div style={{ fontSize: 10.5, color: '#9ca3af' }}>строк</div>
            </td>
            <td style={{ padding: '10px 12px', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                <div style={{ fontSize: 12, color: '#374151' }}>
                    {src.first_date ? formatDate(src.first_date) : '—'} — {lastDateLabel(src)}
                </div>
                <div style={{ fontSize: 11, color: f.color, marginTop: 2 }}>● {f.text}</div>
            </td>
            <td style={{ padding: '10px 12px', verticalAlign: 'top', fontSize: 11, color: '#6b7280', maxWidth: 180 }}>
                {src.schedule}
            </td>
            <td style={{ padding: '10px 12px', textAlign: 'right', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    {progress && <ProgressBadge p={progress} />}
                    {src.ranged && (
                        <select value={days} onChange={e => setDays(Number(e.target.value))} disabled={running}
                            aria-label="Период дозагрузки"
                            style={{ fontSize: 11, padding: '3px 5px', borderRadius: 8, border: '1px solid #d1d5db', background: '#fff', color: '#374151' }}>
                            {RANGES.map(r => <option key={r.days} value={r.days}>{r.label}</option>)}
                        </select>
                    )}
                    <button className="btn btn-secondary btn-sm" style={{ fontSize: 11.5 }}
                        disabled={!src.refreshable || running || busy}
                        title={src.refresh_hint || 'Дозагрузка недоступна'}
                        onClick={() => onRefresh(src.key, src.ranged ? days : null)}>
                        {running ? '…' : 'Дозагрузить'}
                    </button>
                </div>
                {progress?.status === 'error' && (
                    <div style={{ fontSize: 10.5, color: '#ef4444', marginTop: 4, maxWidth: 240, whiteSpace: 'normal' }}>{progress.error}</div>
                )}
            </td>
        </tr>
    );
}

export default function RawDataPage() {
    const routeParams = useParams();
    const slug = typeof routeParams?.slug === 'string' ? routeParams.slug : Array.isArray(routeParams?.slug) ? routeParams.slug[0] : '';
    const [sources, setSources] = useState<RawSource[]>([]);
    const [progress, setProgress] = useState<ProgressMap>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');          // ошибка ПЕРВИЧНОЙ загрузки списка (блокирует)
    const [actionError, setActionError] = useState(''); // ошибка дозагрузки/фона (баннер, список не прячем)
    const [busy, setBusy] = useState(false);

    const load = useCallback(async () => {
        try {
            const res = await api.getRawSources();
            setSources(res.sources);
            setProgress(prev => reconcileProgress(
                prev,
                Object.fromEntries(res.sources.filter(s => s.progress).map(s => [s.key, s.progress!])),
            ));
            setError('');
        } catch (e) {
            // Фон/ретрай не должен стирать уже показанный список — блокирующую
            // ошибку ставим только когда показывать нечего (первичная загрузка).
            setSources(prev => {
                const msg = e instanceof Error ? e.message : 'Ошибка загрузки';
                if (prev.length === 0) setError(msg); else setActionError(msg);
                return prev;
            });
        } finally { setLoading(false); }
    }, []);

    useEffect(() => { load(); }, [load]);

    const anyRunning = Object.values(progress).some(p => p.status === 'running');

    // Поллим прогресс, только пока что-то грузится.
    useEffect(() => {
        if (!anyRunning) return;
        const t = setInterval(async () => {
            try {
                const res = await api.getRawRefreshProgress();
                setProgress(prev => reconcileProgress(prev, res.progress));
            } catch { /* прогресс не критичен */ }
        }, POLL_MS);
        return () => clearInterval(t);
    }, [anyRunning]);

    // Когда дозагрузки завершились (running → покой) — перечитываем объёмы/свежесть.
    const prevRunningRef = useRef(false);
    useEffect(() => {
        if (prevRunningRef.current && !anyRunning) load();
        prevRunningRef.current = anyRunning;
    }, [anyRunning, load]);

    const onRefresh = async (key: string, days: number | null) => {
        setBusy(true); setActionError('');
        try {
            const body = days ? { date_from: iso(new Date(Date.now() - (days - 1) * 86400_000)), date_to: iso(new Date()) } : undefined;
            const res = await api.refreshRawSource(key, body);
            if (res.status === 'already_running' || res.status === 'started') {
                setProgress(p => ({ ...p, [key]: { status: 'running', started_at: new Date().toISOString(), finished_at: null, error: null } }));
            } else if (res.status === 'unsupported') {
                setActionError(res.error || 'Дозагрузка недоступна для этого источника');
            }
        } catch (e) {
            setActionError(e instanceof Error ? e.message : 'Не удалось запустить дозагрузку');
        } finally { setBusy(false); }
    };

    const grouped = useMemo(() => {
        const m = new Map<string, RawSource[]>();
        sources.forEach(s => m.set(s.group, [...(m.get(s.group) || []), s]));
        return [...m.entries()];
    }, [sources]);

    const totalRows = useMemo(() => sources.reduce((a, s) => a + (s.rows || 0), 0), [sources]);

    return (
        <PageGuard page="raw-data">
            <div className="animate-in">
                <div style={{ marginBottom: 8 }}>
                    <h1 style={{ fontSize: 28, fontWeight: 700, margin: 0 }}>Сырые данные</h1>
                    <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 6, maxWidth: 760 }}>
                        Что сервис забирает из внешних API и хранит у себя. Планировщик обновляет источники сам;
                        кнопка «Дозагрузить» запускает загрузку немедленно, в фоне.
                    </p>
                </div>

                {loading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка…</div>}

                {/* Блокирующая ошибка — только когда показывать нечего (первичная загрузка) */}
                {!loading && error && sources.length === 0 && (
                    <div className="glass-card" style={{ padding: '16px 20px', border: '1px solid var(--color-danger)', background: '#fef2f2' }}>
                        <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>{error}</span>
                    </div>
                )}

                {/* Ошибка дозагрузки/фонового обновления — баннер поверх списка, список не прячем */}
                {actionError && (
                    <div className="glass-card" style={{ padding: '10px 14px', marginBottom: 12, border: '1px solid var(--color-danger)', background: '#fef2f2', display: 'flex', alignItems: 'center', gap: 10, justifyContent: 'space-between' }}>
                        <span style={{ fontSize: 12.5, color: 'var(--color-danger)' }}>{actionError}</span>
                        <button className="btn btn-secondary btn-sm" style={{ fontSize: 11 }} onClick={() => setActionError('')}>✕</button>
                    </div>
                )}

                {!loading && !error && sources.length === 0 && (
                    <div className="glass-card static" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>
                        Источники не настроены.
                    </div>
                )}

                {!loading && sources.length > 0 && (
                    <>
                        <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 12 }}>
                            Всего накоплено: <b style={{ color: '#111827' }}>{formatNumber(totalRows, 0)}</b> строк в {sources.length} источниках
                        </div>

                        {grouped.map(([group, items]) => (
                            <div key={group} className="glass-card static" style={{ padding: 0, marginBottom: 14, overflow: 'hidden' }}>
                                <div style={{ padding: '10px 14px', background: '#374151', color: '#e5e7eb', fontSize: 11, fontWeight: 700, letterSpacing: 0.3 }}>
                                    {group.toUpperCase()}
                                </div>
                                <div style={{ overflowX: 'auto' }}>
                                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                        <tbody>
                                            {items.map(s => (
                                                <SourceRow key={s.key} src={s} progress={progress[s.key] ?? null} onRefresh={onRefresh} busy={busy} slug={slug} />
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        ))}
                    </>
                )}
            </div>
        </PageGuard>
    );
}
