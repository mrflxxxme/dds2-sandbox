'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { formatNumber, formatDate } from '@/lib/utils';
import {
    ffListAcceptances,
    ffStartAcceptance,
    ffArchiveAcceptance,
    ffMe,
} from '@/lib/api/ff';
import type { FfAcceptanceRow, FfAcceptanceStatus, FfMe } from '@/types/ff';
import { ProjectBadge, AcceptanceStatusBadge, ReturnBadge, InWorkBadge } from '../../../_components/badges';
import { useTableSort, SortableTh, FfTabs, type SortValue } from '../../../_components/table';

type TabKey = 'active' | 'archive';

const STATUS_OPTIONS: { value: FfAcceptanceStatus | ''; label: string }[] = [
    { value: '', label: 'Все статусы' },
    { value: 'EXPECTED', label: 'Ожидается' },
    { value: 'ACCEPTED', label: 'Принята' },
];

export default function FfAcceptancesPage() {
    const router = useRouter();
    const [rows, setRows] = useState<FfAcceptanceRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [busyId, setBusyId] = useState<number | null>(null);

    // Filters
    const [tab, setTab] = useState<TabKey>('active');
    const [search, setSearch] = useState('');
    const [projectSlug, setProjectSlug] = useState('');
    const [statusFilter, setStatusFilter] = useState<FfAcceptanceStatus | ''>('');
    const [warehouseId, setWarehouseId] = useState<number | ''>('');

    const [me, setMe] = useState<FfMe | null>(null);

    const load = useCallback(
        (
            opts: { archived: boolean; project_slug?: string; status?: FfAcceptanceStatus | ''; warehouse_id?: number | '' },
            signal?: AbortSignal,
        ) => {
            setLoading(true);
            setError('');
            ffListAcceptances({
                limit: 200,
                archived: opts.archived,
                project_slug: opts.project_slug || undefined,
                status: opts.status || undefined,
                warehouse_id: opts.warehouse_id === '' ? undefined : opts.warehouse_id,
            })
                .then((res) => {
                    if (signal?.aborted) return;
                    setRows(res.items);
                })
                .catch((err: unknown) => {
                    if (signal?.aborted) return;
                    setError(err instanceof Error ? err.message : 'Не удалось загрузить приёмки');
                })
                .finally(() => {
                    if (signal?.aborted) return;
                    setLoading(false);
                });
        },
        [],
    );

    useEffect(() => {
        const controller = new AbortController();
        load(
            { archived: tab === 'archive', project_slug: projectSlug, status: statusFilter, warehouse_id: warehouseId },
            controller.signal,
        );
        return () => controller.abort();
    }, [load, tab, projectSlug, statusFilter, warehouseId]);

    useEffect(() => {
        const controller = new AbortController();
        ffMe()
            .then((res) => {
                if (controller.signal.aborted) return;
                setMe(res);
            })
            .catch(() => {
                /* options stay empty — filters degrade gracefully */
            });
        return () => controller.abort();
    }, []);

    const reload = useCallback(() => {
        load({ archived: tab === 'archive', project_slug: projectSlug, status: statusFilter, warehouse_id: warehouseId });
    }, [load, tab, projectSlug, statusFilter, warehouseId]);

    const metrics = useMemo(() => {
        const expected = rows.filter((r) => r.status === 'EXPECTED').length;
        const inWork = rows.filter((r) => r.status === 'EXPECTED' && r.in_work).length;
        const accepted = rows.filter((r) => r.status === 'ACCEPTED').length;
        return { expected, inWork, accepted };
    }, [rows]);

    // Client-side text search over number / warehouse.
    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        if (!q) return rows;
        return rows.filter((r) => `${r.number} ${r.warehouse_name}`.toLowerCase().includes(q));
    }, [rows, search]);

    const getValue = useCallback((row: FfAcceptanceRow, key: string): SortValue => {
        switch (key) {
            case 'number':
                return row.number;
            case 'project':
                return row.project_name;
            case 'kind':
                return row.kind;
            case 'warehouse':
                return row.warehouse_name;
            case 'date':
                return row.actual_date ?? row.planned_date;
            case 'created':
                return row.created_at;
            case 'items':
                return row.items.length;
            case 'status':
                return row.status;
            default:
                return null;
        }
    }, []);

    const { sorted, sortKey, sortDir, toggleSort } = useTableSort(filtered, getValue, {
        key: 'number',
        dir: 'asc',
    });

    const handleStart = async (row: FfAcceptanceRow) => {
        setBusyId(row.id);
        setError('');
        try {
            const updated = await ffStartAcceptance(row.id);
            setRows((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Ошибка');
        } finally {
            setBusyId(null);
        }
    };

    const handleArchive = async (row: FfAcceptanceRow) => {
        setBusyId(row.id);
        setError('');
        try {
            await ffArchiveAcceptance(row.id, !row.is_archived);
            setRows((prev) => prev.filter((r) => r.id !== row.id));
            reload();
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Ошибка архивации');
        } finally {
            setBusyId(null);
        }
    };

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Приёмки</h1>
                    <p className="page-subtitle">Входящие поставки и возвраты WB</p>
                </div>
            </div>

            <FfTabs
                items={[
                    { key: 'active', label: 'Активные' },
                    { key: 'archive', label: 'Архив' },
                ]}
                active={tab}
                onChange={(k) => setTab(k as TabKey)}
            />

            <div className="ff-toolbar">
                <div className="ff-toolbar-field" style={{ flex: 1, minWidth: 200 }}>
                    <label htmlFor="acc-search">Поиск</label>
                    <input
                        id="acc-search"
                        type="text"
                        placeholder="№, склад"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                    />
                </div>
                <div className="ff-toolbar-field">
                    <label htmlFor="acc-project">Проект</label>
                    <select id="acc-project" value={projectSlug} onChange={(e) => setProjectSlug(e.target.value)}>
                        <option value="">Все проекты</option>
                        {me?.projects.map((p) => (
                            <option key={p.slug} value={p.slug}>
                                {p.name}
                            </option>
                        ))}
                    </select>
                </div>
                <div className="ff-toolbar-field">
                    <label htmlFor="acc-status">Статус</label>
                    <select
                        id="acc-status"
                        value={statusFilter}
                        onChange={(e) => setStatusFilter(e.target.value as FfAcceptanceStatus | '')}
                    >
                        {STATUS_OPTIONS.map((o) => (
                            <option key={o.value || 'all'} value={o.value}>
                                {o.label}
                            </option>
                        ))}
                    </select>
                </div>
                <div className="ff-toolbar-field">
                    <label htmlFor="acc-warehouse">Склад</label>
                    <select
                        id="acc-warehouse"
                        value={warehouseId === '' ? '' : String(warehouseId)}
                        onChange={(e) => setWarehouseId(e.target.value === '' ? '' : Number(e.target.value))}
                    >
                        <option value="">Все склады</option>
                        {me?.warehouses.map((w) => (
                            <option key={`${w.project_slug}-${w.warehouse_id}`} value={w.warehouse_id}>
                                {w.warehouse_name}
                            </option>
                        ))}
                    </select>
                </div>
            </div>

            {!loading && !error && rows.length > 0 && (
                <div className="stats-grid">
                    <div className="stat-card">
                        <div className="stat-card-label">Ожидают</div>
                        <div className="stat-card-value">{formatNumber(metrics.expected, 0)}</div>
                    </div>
                    <div className="stat-card">
                        <div className="stat-card-label">В работе</div>
                        <div className="stat-card-value" style={{ color: 'var(--color-accent)' }}>{formatNumber(metrics.inWork, 0)}</div>
                    </div>
                    <div className="stat-card">
                        <div className="stat-card-label">Принято</div>
                        <div className="stat-card-value" style={{ color: 'var(--color-success)' }}>{formatNumber(metrics.accepted, 0)}</div>
                    </div>
                </div>
            )}

            {loading && (
                <div className="ff-feed">
                    <div className="ff-skeleton" />
                    <div className="ff-skeleton" />
                    <div className="ff-skeleton" />
                </div>
            )}

            {!loading && error && (
                <div className="glass-card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, color: 'var(--color-danger)' }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={reload}>
                        Повторить
                    </button>
                </div>
            )}

            {!loading && !error && rows.length === 0 && (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">
                            {tab === 'archive' ? 'В архиве пусто' : 'Нет задач — всё принято'}
                        </div>
                    </div>
                </div>
            )}

            {!loading && !error && rows.length > 0 && sorted.length === 0 && (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">Ничего не найдено по фильтрам</div>
                    </div>
                </div>
            )}

            {!loading && !error && sorted.length > 0 && (
                <div className="glass-card" style={{ overflow: 'auto', padding: 0 }}>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <SortableTh label="№" sortKey="number" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="Проект" sortKey="project" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="Тип" sortKey="kind" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="Склад" sortKey="warehouse" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="Дата" sortKey="date" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="Создана" sortKey="created" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="Позиции" sortKey="items" activeKey={sortKey} dir={sortDir} onSort={toggleSort} align="right" />
                                <SortableTh label="Статус" sortKey="status" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <th>Действие</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sorted.map((row) => {
                                const canStart = row.status === 'EXPECTED' && !row.in_work;
                                return (
                                    <tr
                                        key={row.id}
                                        className="ff-row-link"
                                        onClick={() => router.push(`/ff/acceptances/${row.id}`)}
                                    >
                                        <td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>
                                            <Link
                                                href={`/ff/acceptances/${row.id}`}
                                                onClick={(e) => e.stopPropagation()}
                                                style={{ color: 'var(--color-accent)' }}
                                            >
                                                № {row.number}
                                            </Link>
                                        </td>
                                        <td><ProjectBadge name={row.project_name} /></td>
                                        <td>
                                            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                                                {row.kind === 'return' ? <ReturnBadge /> : <span className="badge badge-secondary">Поставка</span>}
                                                {row.status === 'EXPECTED' && row.in_work && <InWorkBadge />}
                                            </div>
                                        </td>
                                        <td>{row.warehouse_name}</td>
                                        <td style={{ whiteSpace: 'nowrap' }}>
                                            {row.actual_date ? (
                                                <span style={{ color: 'var(--color-success)' }}>{formatDate(row.actual_date)}</span>
                                            ) : row.planned_date ? (
                                                <span style={{ color: 'var(--color-text-muted)' }}>{formatDate(row.planned_date)}</span>
                                            ) : '—'}
                                        </td>
                                        <td style={{ whiteSpace: 'nowrap', color: 'var(--color-text-muted)', fontSize: 13 }}>
                                            {formatDate(row.created_at)}
                                        </td>
                                        <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatNumber(row.items.length, 0)}</td>
                                        <td><AcceptanceStatusBadge status={row.status} /></td>
                                        <td style={{ whiteSpace: 'nowrap' }} onClick={(e) => e.stopPropagation()}>
                                            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                                                {canStart && (
                                                    <button
                                                        className="btn btn-primary btn-sm"
                                                        disabled={busyId === row.id}
                                                        onClick={() => handleStart(row)}
                                                    >
                                                        {busyId === row.id ? '…' : 'Взять в работу'}
                                                    </button>
                                                )}
                                                {row.status === 'EXPECTED' && row.in_work && (
                                                    <Link
                                                        href={`/ff/acceptances/${row.id}`}
                                                        className="btn btn-success btn-sm"
                                                        onClick={(e) => e.stopPropagation()}
                                                    >
                                                        Принять
                                                    </Link>
                                                )}
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    disabled={busyId === row.id}
                                                    onClick={() => handleArchive(row)}
                                                    title={row.is_archived ? 'Вернуть из архива' : 'Убрать в архив'}
                                                >
                                                    {row.is_archived ? 'Из архива' : 'В архив'}
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
