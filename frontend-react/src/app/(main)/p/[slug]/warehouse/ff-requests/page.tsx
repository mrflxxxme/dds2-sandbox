'use client';

/**
 * Страница «Заявки ФФ» — сводный мэтчинг заявок фулфилментов с нашими
 * заявками на сборку по всем складам с подключённой интеграцией.
 *
 * Данные: GET /warehouse/fulfillment/overview (api.getFfOverview).
 * Линковка — существующие link/unlinkFulfillmentRequest; ручной выбор —
 * переиспользованный FfLinkModal из вкладок склада (ff-shared).
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import PageHeader from '@/components/PageHeader';
import TanStackDataTable from '@/components/TanStackDataTable';
import { FfMismatchModal } from '@/components/FfMismatchModal';
import type { Column } from '@/components/DataTable';
import type {
    FfIntegratedWarehouse,
    FfMatchSuggestion,
    FfOverviewRequestRow,
    FfRequestKind,
} from '@/types/api';
import { FF_LINKED_STATUS_LABELS, FfLinkModal } from '../[id]/ff-shared';

/* ─── helpers ─────────────────────────────────────────────────────────────── */

/** «синк N мин назад» от last_sync_at; null → «не синхронизировано» */
function syncAgoLabel(iso: string | null): string {
    if (!iso) return 'не синхронизировано';
    const ms = Date.now() - new Date(iso).getTime();
    if (!Number.isFinite(ms) || ms < 0) return `синк ${formatDate(iso)}`;
    const min = Math.floor(ms / 60000);
    if (min < 1) return 'синк только что';
    if (min < 60) return `синк ${min} мин назад`;
    const h = Math.floor(min / 60);
    if (h < 24) return `синк ${h} ч назад`;
    const d = Math.floor(h / 24);
    return `синк ${d} дн назад`;
}

/** Бейдж статуса ФФ-заявки: завершена / просрочена / архив / текущая стадия */
function ffStatusBadge(row: FfOverviewRequestRow) {
    const base: React.CSSProperties = { fontSize: 11, padding: '2px 8px' };
    if (row.is_completed) return <span className="badge badge-success" style={base}>Завершена</span>;
    if (row.expired) return <span className="badge badge-danger" style={base}>Просрочена</span>;
    if (row.archived) return <span className="badge badge-secondary" style={base}>Архив</span>;
    const stage = row.stage_title || row.status;
    if (!stage) return <span style={{ color: 'var(--color-text-muted)' }}>—</span>;
    return <span className="badge badge-info" style={base}>{stage}</span>;
}

const linkedStatusLabel = (s: string | null) => (s ? (FF_LINKED_STATUS_LABELS[s] || s) : '');

/* ─── page ───────────────────────────────────────────────────────────────── */

export default function FfRequestsOverviewPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;

    // Фильтры
    const [kind, setKind] = useState<FfRequestKind>('assembly');
    const [onlyUnlinked, setOnlyUnlinked] = useState(false);
    const [selectedWh, setSelectedWh] = useState<number | null>(null);

    // Данные
    const [warehouses, setWarehouses] = useState<FfIntegratedWarehouse[]>([]);
    const [requests, setRequests] = useState<FfOverviewRequestRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [loaded, setLoaded] = useState(false);
    const [error, setError] = useState('');

    // Действия
    const [syncingId, setSyncingId] = useState<number | null>(null);
    const [actingId, setActingId] = useState<number | null>(null);
    const [linkFor, setLinkFor] = useState<FfOverviewRequestRow | null>(null);
    // id сборки для модалки «расхождение наполнения»
    const [mismatchForAssembly, setMismatchForAssembly] = useState<number | null>(null);

    // Перезагрузка: silent — без скелетона (после link/unlink/sync)
    const [reloadKey, setReloadKey] = useState(0);
    const silentRef = useRef(false);
    const reload = useCallback((silent = true) => {
        silentRef.current = silent;
        setReloadKey(k => k + 1);
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        const silent = silentRef.current;
        silentRef.current = false;
        if (!silent) { setLoading(true); setError(''); }
        api.getFfOverview({
            kind,
            warehouse_id: selectedWh ?? undefined,
            only_unlinked: onlyUnlinked || undefined,
        })
            .then(r => {
                if (controller.signal.aborted) return;
                setRequests(r.requests);
                setWarehouses(prev => {
                    // При фильтре по складу бэкенд может вернуть только выбранный —
                    // обновляем счётчики, не схлопывая остальные карточки.
                    if (selectedWh == null || prev.length === 0) return r.warehouses;
                    const byId = new Map(r.warehouses.map(w => [w.warehouse_id, w]));
                    return prev.map(w => byId.get(w.warehouse_id) ?? w);
                });
                setLoaded(true);
            })
            .catch((e: unknown) => {
                if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка загрузки');
            })
            .finally(() => {
                if (!controller.signal.aborted) setLoading(false);
            });
        return () => controller.abort();
    }, [kind, onlyUnlinked, selectedWh, reloadKey]);

    /* ─── handlers ─── */

    const handleSync = useCallback(async (warehouseId: number) => {
        setSyncingId(warehouseId);
        setError('');
        try {
            await api.syncFulfillment(warehouseId);
            reload();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка синхронизации');
        } finally {
            setSyncingId(null);
        }
    }, [reload]);

    // Optimistic-обновление строки после link/unlink + тихий рефетч счётчиков
    const applyUpdated = useCallback((updated: Partial<FfOverviewRequestRow> & { id: number }) => {
        setRequests(prev => prev.map(r => (r.id === updated.id ? { ...r, ...updated, suggestions: [] } : r)));
        reload();
    }, [reload]);

    const handleSuggestionLink = useCallback(async (row: FfOverviewRequestRow, s: FfMatchSuggestion) => {
        if (!confirm(`Связать заявку ФФ ${row.number || row.external_id} с заявкой № ${s.number}?\n${s.reason}`)) return;
        setActingId(row.id);
        setError('');
        try {
            const updated = await api.linkFulfillmentRequest(row.warehouse_id, row.id, { assembly_request_id: s.assembly_request_id });
            applyUpdated(updated);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка связывания');
        } finally {
            setActingId(null);
        }
    }, [applyUpdated]);

    const handleUnlink = useCallback(async (row: FfOverviewRequestRow) => {
        if (!confirm(`Отвязать заявку ФФ ${row.number || row.external_id} от документа ${row.linked_number}?`)) return;
        setActingId(row.id);
        setError('');
        try {
            const updated = await api.unlinkFulfillmentRequest(row.warehouse_id, row.id);
            applyUpdated(updated);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка отвязки');
        } finally {
            setActingId(null);
        }
    }, [applyUpdated]);

    /* ─── derived ─── */

    const providerLabelByWh = new Map(warehouses.map(w => [w.warehouse_id, w.provider_label]));

    /* ─── columns ─── */

    const cols: Column[] = [
        {
            key: 'number', label: '№ ФФ',
            render: (v: string | null, row: FfOverviewRequestRow) => (
                <span onClick={e => e.stopPropagation()}>
                    <Link
                        href={`/p/${slug}/warehouse/${row.warehouse_id}/ff-request/${row.id}`}
                        title="Открыть состав заявки ФФ"
                        style={{ fontWeight: 600, color: 'var(--color-accent)', textDecoration: 'none' }}
                    >
                        {v || row.external_id}
                    </Link>
                    {v && (
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{row.external_id}</div>
                    )}
                </span>
            ),
            exportValue: (row: FfOverviewRequestRow) => row.number || row.external_id,
        },
        {
            key: 'warehouse_name', label: 'Склад',
            render: (v: string, row: FfOverviewRequestRow) => (
                <span>
                    <span>{v}</span>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                        {providerLabelByWh.get(row.warehouse_id) || row.provider}
                    </div>
                </span>
            ),
            exportValue: (row: FfOverviewRequestRow) => row.warehouse_name,
        },
        {
            key: 'stage_title', label: 'Статус ФФ',
            render: (_: unknown, row: FfOverviewRequestRow) => ffStatusBadge(row),
            exportValue: (row: FfOverviewRequestRow) => row.is_completed
                ? 'Завершена'
                : (row.expired ? 'Просрочена' : (row.stage_title || row.status || '')),
        },
        {
            key: 'external_created_at', label: 'Дата ФФ',
            render: (v: string | null) => (v ? formatDate(v) : '—'),
        },
        {
            key: 'linked_number', label: 'Наша заявка',
            render: (_: unknown, row: FfOverviewRequestRow) => {
                const acting = actingId === row.id;

                // Связана → бейдж-ссылка на наш документ + статус + отвязать
                if (row.linked_number) {
                    const href = row.assembly_request_id
                        ? `/p/${slug}/warehouse/assembly/${row.assembly_request_id}`
                        : (row.inbound_receipt_id
                            ? `/p/${slug}/warehouse/${row.warehouse_id}/receipt/${row.inbound_receipt_id}`
                            : null);
                    return (
                        <span onClick={e => e.stopPropagation()} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            {href ? (
                                <Link href={href} className="badge badge-info" title="Открыть наш документ" style={{ textDecoration: 'none' }}>
                                    № {row.linked_number}
                                </Link>
                            ) : (
                                <span className="badge badge-info">№ {row.linked_number}</span>
                            )}
                            {row.linked_status && (
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{linkedStatusLabel(row.linked_status)}</span>
                            )}
                            {row.linked_mismatch === true && (
                                row.assembly_request_id != null ? (
                                    <button
                                        type="button"
                                        className="badge badge-warning"
                                        style={{ fontSize: 11, padding: '2px 8px', cursor: 'pointer', border: 'none' }}
                                        title="Показать расхождения по позициям"
                                        onClick={() => setMismatchForAssembly(row.assembly_request_id)}
                                    >
                                        ⚠ расхождение
                                    </button>
                                ) : (
                                    <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }} title="Состав нашего документа расходится с заявкой ФФ по наполнению">
                                        ⚠ расхождение
                                    </span>
                                )
                            )}
                            <button
                                className="btn btn-sm btn-secondary"
                                title="Отвязать от нашего документа"
                                onClick={() => handleUnlink(row)}
                                disabled={acting}
                            >
                                {acting ? '...' : '✕ отвязать'}
                            </button>
                        </span>
                    );
                }

                // Не связана, есть кандидаты авто-мэтчинга → chips + ручной выбор
                if (row.suggestions.length > 0) {
                    return (
                        <span onClick={e => e.stopPropagation()} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                            {row.suggestions.map(s => (
                                <button
                                    key={s.assembly_request_id}
                                    className="btn btn-sm btn-secondary"
                                    title={`${s.reason} · ${linkedStatusLabel(s.status)} · ${formatDate(s.created_at)} · ${formatNumber(s.total_qty, 0)} шт`}
                                    onClick={() => handleSuggestionLink(row, s)}
                                    disabled={acting}
                                    style={{ borderColor: 'var(--color-accent)', color: 'var(--color-accent)' }}
                                >
                                    № {s.number} · {s.score}%
                                </button>
                            ))}
                            <button className="btn btn-sm btn-secondary" onClick={() => setLinkFor(row)} disabled={acting}>
                                выбрать…
                            </button>
                        </span>
                    );
                }

                // Не связана, кандидатов нет → ручной выбор
                return (
                    <span onClick={e => e.stopPropagation()}>
                        <button className="btn btn-sm btn-secondary" onClick={() => setLinkFor(row)} disabled={acting}>
                            связать…
                        </button>
                    </span>
                );
            },
            exportValue: (row: FfOverviewRequestRow) => row.linked_number
                ? `${row.linked_number}${row.linked_status ? ` (${linkedStatusLabel(row.linked_status)})` : ''}`
                : (row.suggestions.length > 0
                    ? `кандидаты: ${row.suggestions.map(s => `${s.number} (${s.score}%)`).join(', ')}`
                    : ''),
        },
    ];

    /* ─── render ─── */

    const header = (
        <PageHeader
            title="Заявки ФФ"
            subtitle="Заявки фулфилментов и связь с заявками на сборку"
            icon="🔗"
        />
    );

    if (loading && !loaded) {
        return (
            <div className="animate-in">
                {header}
                <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
                    {[0, 1].map(i => (
                        <div key={i} className="glass-card" style={{ width: 260, height: 110, opacity: 0.5 }} />
                    ))}
                </div>
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Загрузка...
                </div>
            </div>
        );
    }

    if (error && !loaded) {
        return (
            <div className="animate-in">
                {header}
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>
            </div>
        );
    }

    // Нет ни одного склада с интеграцией
    if (loaded && warehouses.length === 0) {
        return (
            <div className="animate-in">
                {header}
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 32, marginBottom: 12 }}>🔌</div>
                    <div style={{ fontWeight: 600, marginBottom: 8 }}>Нет складов с интеграцией фулфилмента</div>
                    <div style={{ color: 'var(--color-text-muted)', marginBottom: 16 }}>
                        Подключите фулфилмент в карточке склада — вкладка «Реквизиты»
                    </div>
                    <Link href={`/p/${slug}/warehouse`} className="btn btn-primary">К складам</Link>
                </div>
            </div>
        );
    }

    const totalUnlinked = warehouses.reduce((acc, w) => acc + w.requests_unlinked, 0);
    const emptyAllLinked = onlyUnlinked && requests.length === 0;

    return (
        <div className="animate-in">
            {header}

            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}

            {/* ─── Карточки складов-интеграций ─── */}
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                {warehouses.map(w => {
                    const active = selectedWh === w.warehouse_id;
                    const syncing = syncingId === w.warehouse_id;
                    return (
                        <div
                            key={w.warehouse_id}
                            className="glass-card"
                            onClick={() => setSelectedWh(prev => (prev === w.warehouse_id ? null : w.warehouse_id))}
                            title={active ? 'Снять фильтр по складу' : 'Показать только заявки этого склада'}
                            style={{
                                padding: 16,
                                minWidth: 240,
                                cursor: 'pointer',
                                border: active ? '1px solid var(--color-accent)' : '1px solid transparent',
                                boxShadow: active ? '0 0 0 1px var(--color-accent)' : undefined,
                            }}
                        >
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 6 }}>
                                <span style={{ fontWeight: 600 }}>{w.warehouse_name}</span>
                                <button
                                    className="btn btn-sm btn-secondary"
                                    title="Синхронизировать с фулфилментом"
                                    onClick={e => { e.stopPropagation(); handleSync(w.warehouse_id); }}
                                    disabled={syncing}
                                >
                                    {syncing ? '…' : '⟳'}
                                </button>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                                <span className="badge badge-secondary" style={{ fontSize: 11, padding: '2px 8px' }}>{w.provider_label}</span>
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{syncAgoLabel(w.last_sync_at)}</span>
                            </div>
                            <div style={{ fontSize: 13 }}>
                                <span>Заявок: <b>{formatNumber(w.requests_total, 0)}</b></span>
                                <span style={{ margin: '0 6px', color: 'var(--color-text-dim)' }}>·</span>
                                <span style={{ color: w.requests_unlinked > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)' }}>
                                    несвязанных: <b>{formatNumber(w.requests_unlinked, 0)}</b>
                                </span>
                            </div>
                        </div>
                    );
                })}
            </div>

            {/* ─── Панель фильтров ─── */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 6 }}>
                    <button
                        className={`btn btn-sm ${kind === 'assembly' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setKind('assembly')}
                    >
                        Сборка
                    </button>
                    <button
                        className={`btn btn-sm ${kind === 'inbound' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setKind('inbound')}
                    >
                        Приёмки
                    </button>
                </div>
                <button
                    className={`btn btn-sm ${onlyUnlinked ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setOnlyUnlinked(v => !v)}
                    title="Показать только заявки без связи с нашим документом"
                >
                    {/* requests_unlinked считает только kind=assembly — на «Приёмках» счётчик не показываем */}
                    Только несвязанные{kind === 'assembly' && totalUnlinked > 0 ? ` (${formatNumber(totalUnlinked, 0)})` : ''}
                </button>
                {selectedWh != null && (
                    <button className="btn btn-sm btn-secondary" onClick={() => setSelectedWh(null)}>
                        ✕ склад: {warehouses.find(w => w.warehouse_id === selectedWh)?.warehouse_name || selectedWh}
                    </button>
                )}
                {loading && <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Обновление…</span>}
            </div>

            {/* ─── Таблица заявок ─── */}
            {emptyAllLinked ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 32, marginBottom: 12 }}>🎉</div>
                    <div style={{ fontWeight: 600 }}>Все заявки связаны</div>
                    <div style={{ color: 'var(--color-text-muted)', marginTop: 6 }}>
                        Несвязанных заявок {kind === 'assembly' ? 'на сборку' : 'на приёмку'} нет
                    </div>
                </div>
            ) : (
                <TanStackDataTable
                    columns={cols}
                    data={requests}
                    onRowClick={(row: FfOverviewRequestRow) =>
                        router.push(`/p/${slug}/warehouse/${row.warehouse_id}/ff-request/${row.id}`)}
                    emptyText="Нет заявок по выбранным фильтрам"
                    emptyIcon={kind === 'assembly' ? '🧰' : '📥'}
                    exportName="ff_requests_overview"
                />
            )}

            {/* ─── Модал ручного выбора (переиспользуем пикер вкладок склада) ─── */}
            {linkFor && (
                <FfLinkModal
                    warehouseId={linkFor.warehouse_id}
                    kind={kind}
                    request={linkFor}
                    onClose={() => setLinkFor(null)}
                    onLinked={updated => {
                        applyUpdated(updated);
                        setLinkFor(null);
                    }}
                />
            )}

            {mismatchForAssembly != null && (
                <FfMismatchModal assemblyId={mismatchForAssembly} onClose={() => setMismatchForAssembly(null)} />
            )}
        </div>
    );
}
