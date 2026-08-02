'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import type { StockWatchItem, StockWatchListResponse, StockWatchStatus } from '@/types/api';

const PAGE = 100; // watches за подгрузку

const STATUS_LABEL: Record<StockWatchStatus, string> = {
    watching: '⏳ Следим',
    drafted: '📦 Черновик готов',
    dismissed: '✖ Снято',
};
const STATUS_BADGE: Record<StockWatchStatus, string> = {
    watching: 'badge-warning',
    drafted: 'badge-success',
    dismissed: 'badge-secondary',
};

function WatchCard({ watch, onDismissed, onGoToDrafts }: {
    watch: StockWatchItem;
    onDismissed: () => void;
    onGoToDrafts?: () => void;
}) {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState('');

    const dismiss = async () => {
        if (!window.confirm('Снять вопрос с наблюдения? Проверка остатков по нему прекратится.')) return;
        setBusy(true);
        setErr('');
        try {
            await api.dismissStockWatch(watch.id);
            onDismissed();
        } catch (e) {
            setErr(e instanceof Error ? e.message : 'Не удалось снять с наблюдения');
            setBusy(false);
        }
    };

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 12 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
                <span className={`badge ${STATUS_BADGE[watch.status]}`}>{STATUS_LABEL[watch.status]}</span>
                <span style={{ color: 'var(--color-text-dim)' }}>
                    {watch.product_name || `nmID ${watch.nm_id}`}
                    {watch.product_name ? ` · nmID ${watch.nm_id}` : ''}
                </span>
                {watch.last_qty != null && (
                    <span className={`badge ${watch.last_qty > 0 ? 'badge-success' : 'badge-secondary'}`}>
                        остаток: {formatNumber(watch.last_qty, 0)} шт
                    </span>
                )}
                <span style={{ marginLeft: 'auto', color: 'var(--color-text-dim)' }}>
                    {watch.status === 'watching'
                        ? (watch.created_at ? `Следим с ${formatDate(watch.created_at)}` : '')
                        : (watch.resolved_at ? formatDate(watch.resolved_at) : '')}
                </span>
            </div>

            {watch.question_text && (
                <p style={{ margin: '8px 0 0', fontSize: 13, color: 'var(--color-text-dim)', fontStyle: 'italic', whiteSpace: 'pre-wrap' }}>
                    Вопрос: {watch.question_text}
                </p>
            )}

            {err && <div style={{ marginTop: 8, fontSize: 13, color: 'var(--color-danger)' }}>{err}</div>}

            <div style={{ display: 'flex', gap: 6, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                {watch.status === 'watching' && (
                    <button className="btn btn-danger btn-sm" onClick={dismiss} disabled={busy}>
                        {busy ? 'Снятие…' : '✖ Снять с наблюдения'}
                    </button>
                )}
                {watch.status === 'drafted' && (
                    onGoToDrafts ? (
                        <button className="btn btn-primary btn-sm" onClick={onGoToDrafts}>
                            → К черновику
                        </button>
                    ) : (
                        <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>
                            Черновик ответа ждёт одобрения во вкладке «💬 Вопросы-автоответы».
                        </span>
                    )
                )}
            </div>
        </div>
    );
}

const STATUS_FILTERS: { key: StockWatchStatus | ''; label: string }[] = [
    { key: 'watching', label: '⏳ Под наблюдением' },
    { key: 'drafted', label: '📦 Черновики готовы' },
    { key: 'dismissed', label: '✖ Снято' },
    { key: '', label: 'Все' },
];

export default function StockWatchTab({ onGoToDrafts }: { onGoToDrafts?: () => void }) {
    const [meta, setMeta] = useState<StockWatchListResponse | null>(null);
    const [items, setItems] = useState<StockWatchItem[]>([]);
    const [status, setStatus] = useState<StockWatchStatus | ''>('watching');
    const [loading, setLoading] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [acting, setActing] = useState<'tick' | 'scan' | null>(null);
    const [msg, setMsg] = useState('');
    const [error, setError] = useState('');

    const load = useCallback(async (currentStatus: StockWatchStatus | '') => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getStockWatches({ status: currentStatus || undefined, take: PAGE, skip: 0 });
            setMeta(res);
            setItems(res.items);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить поступления');
            setMeta(null);
            setItems([]);
        } finally {
            setLoading(false);
        }
    }, []);

    const loadMore = useCallback(async () => {
        setLoadingMore(true);
        try {
            const res = await api.getStockWatches({ status: status || undefined, take: PAGE, skip: items.length });
            setMeta(res);
            setItems(prev => [...prev, ...res.items]);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить ещё');
        } finally {
            setLoadingMore(false);
        }
    }, [status, items.length]);

    useEffect(() => {
        load(status);
    }, [status, load]);

    const runTick = async () => {
        setActing('tick');
        setMsg('');
        setError('');
        try {
            const res = await api.runStockWatchTick();
            setMsg(
                `✓ Проверка остатков: проверено ${formatNumber(res.checked, 0)}, `
                + `черновиков создано ${formatNumber(res.drafted, 0)}, `
                + `ждут поступления ${formatNumber(res.waiting, 0)}`
                + (res.errors > 0 ? `, ошибок сети: ${formatNumber(res.errors, 0)} (повторим при следующей проверке)` : ''),
            );
            await load(status);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось запустить проверку остатков');
        } finally {
            setActing(null);
        }
    };

    const runScan = async () => {
        setActing('scan');
        setMsg('');
        setError('');
        try {
            const res = await api.scanStockWatches();
            setMsg(
                `✓ Перескан вопросов: найдено о наличии ${formatNumber(res.scanned, 0)}, `
                + `поставлено на наблюдение ${formatNumber(res.created, 0)}, `
                + `снято ${formatNumber(res.dismissed, 0)}`,
            );
            await load(status);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось пересканировать вопросы');
        } finally {
            setActing(null);
        }
    };

    const counts = meta?.counts ?? {};
    const total = meta?.total ?? 0;
    const hasMore = items.length < total;
    const busy = acting !== null;

    return (
        <div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                {STATUS_FILTERS.map(f => (
                    <button
                        key={f.key}
                        className={`btn btn-sm ${status === f.key ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setStatus(f.key)}
                    >
                        {f.label}
                        {f.key && counts[f.key] != null ? `: ${formatNumber(counts[f.key], 0)}` : ''}
                    </button>
                ))}
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <button className="btn btn-primary btn-sm" onClick={runTick} disabled={busy || loading}>
                        {acting === 'tick' ? 'Проверка…' : '🔄 Проверить остатки сейчас'}
                    </button>
                    <button className="btn btn-secondary btn-sm" onClick={runScan} disabled={busy || loading}>
                        {acting === 'scan' ? 'Сканирование…' : '🔍 Пересканировать вопросы'}
                    </button>
                </div>
            </div>

            {msg && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 16, color: 'var(--color-success)', display: 'flex', alignItems: 'center', gap: 8 }}>
                    {msg}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setMsg('')}>✕</button>
                </div>
            )}

            {error && (
                <div className="glass-card" style={{ marginBottom: 16, color: 'var(--color-danger)' }}>
                    {error}{' '}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={() => load(status)}>Повторить</button>
                </div>
            )}

            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>
                    Загрузка поступлений…
                </div>
            )}

            {!loading && !error && items.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>📦</div>
                    <h3 style={{ margin: '0 0 8px' }}>Поступлений нет</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        {status
                            ? 'С таким статусом ничего нет.'
                            : 'Когда покупатель спрашивает «когда появится в наличии?», вопрос ставится на наблюдение. Нажмите «🔍 Пересканировать вопросы», чтобы найти такие вопросы.'}
                    </p>
                </div>
            )}

            {!loading && !error && items.length > 0 && (
                <div>
                    {items.map(w => (
                        <WatchCard
                            key={w.id}
                            watch={w}
                            onDismissed={() => load(status)}
                            onGoToDrafts={onGoToDrafts}
                        />
                    ))}
                    {hasMore && (
                        <div style={{ textAlign: 'center', marginTop: 8 }}>
                            <button className="btn btn-secondary" onClick={loadMore} disabled={loadingMore}>
                                {loadingMore ? 'Загрузка…' : `Показать ещё (${formatNumber(total - items.length, 0)})`}
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
