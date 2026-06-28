'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { formatNumber, formatDate } from '@/lib/utils';
import {
    ffListAssemblies,
    ffStartAssembly,
    ffReadyAssembly,
    ffShipAssembly,
    ffArchiveAssembly,
    ffMe,
} from '@/lib/api/ff';
import type { FfAssemblyRow, FfAssemblyStatus, FfMe } from '@/types/ff';
import { ProjectBadge, AssemblyStatusBadge, StuckBadge, assemblyStuckDays } from '../../../_components/badges';
import { useTableSort, SortableTh, FfTabs, type SortValue } from '../../../_components/table';

type TabKey = 'active' | 'archive';

const STATUS_OPTIONS: { value: FfAssemblyStatus | ''; label: string }[] = [
    { value: '', label: 'Все статусы' },
    { value: 'PENDING', label: 'Ожидает сборку' },
    { value: 'IN_PROGRESS', label: 'В сборке' },
    { value: 'READY', label: 'Готово' },
    { value: 'VEHICLE_ASSIGNED', label: 'Машина назначена' },
    { value: 'SHIPPED', label: 'Отгружена' },
    { value: 'DELIVERED', label: 'Принята WB' },
    { value: 'RETURNED', label: 'Возврат на склад' },
    { value: 'CLOSED', label: 'Закрыта' },
    { value: 'CANCELLED', label: 'Отменена' },
];

export default function FfAssembliesPage() {
    const router = useRouter();
    const [rows, setRows] = useState<FfAssemblyRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [busyId, setBusyId] = useState<number | null>(null);

    // Filters
    const [tab, setTab] = useState<TabKey>('active');
    const [search, setSearch] = useState('');
    const [projectSlug, setProjectSlug] = useState('');
    const [statusFilter, setStatusFilter] = useState<FfAssemblyStatus | ''>('');
    const [warehouseId, setWarehouseId] = useState<number | ''>('');
    const [onlyStuck, setOnlyStuck] = useState(false);

    // ffMe-driven select options (resilient: empty on error).
    const [me, setMe] = useState<FfMe | null>(null);

    // Inline «Готово» (ready) modal — quick capability kept on the list.
    const [readyRow, setReadyRow] = useState<FfAssemblyRow | null>(null);
    const [pallets, setPallets] = useState('');
    const [weight, setWeight] = useState('');
    const [readyError, setReadyError] = useState('');

    const load = useCallback(
        (
            opts: { archived: boolean; project_slug?: string; status?: FfAssemblyStatus | ''; warehouse_id?: number | '' },
            signal?: AbortSignal,
        ) => {
            setLoading(true);
            setError('');
            ffListAssemblies({
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
                    setError(err instanceof Error ? err.message : 'Не удалось загрузить заявки');
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
        const toAssemble = rows.filter((r) => r.status === 'PENDING' || r.status === 'IN_PROGRESS').length;
        const ready = rows.filter((r) => r.status === 'READY' || r.status === 'VEHICLE_ASSIGNED').length;
        const shipped = rows.filter((r) => r.status === 'SHIPPED' || r.status === 'DELIVERED').length;
        return { toAssemble, ready, shipped };
    }, [rows]);

    // Client-side text search over number / warehouse / wb_warehouse, plus the
    // «только зависшие» toggle (stuck ≥ 2 days). Applied before sorting.
    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        return rows.filter((r) => {
            if (q) {
                const hay = `${r.number} ${r.warehouse_name} ${r.wb_warehouse_name ?? ''}`.toLowerCase();
                if (!hay.includes(q)) return false;
            }
            if (onlyStuck) {
                const days = assemblyStuckDays(r.status, r.created_at, r.actual_ready_date);
                if (days == null || days < 2) return false;
            }
            return true;
        });
    }, [rows, search, onlyStuck]);

    const getValue = useCallback((row: FfAssemblyRow, key: string): SortValue => {
        switch (key) {
            case 'number':
                return row.number;
            case 'project':
                return row.project_name;
            case 'wb_warehouse':
                return row.wb_warehouse_name ?? row.warehouse_name;
            case 'fbo':
                return row.fbo_supply_number;
            case 'brand':
                return row.brands;
            case 'package':
                return row.package_type;
            case 'items':
                return row.items.length;
            case 'pallets':
                return row.pallets_count;
            case 'created':
                return row.created_at;
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

    const replaceRow = (updated: FfAssemblyRow) =>
        setRows((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));

    const handleStart = async (row: FfAssemblyRow) => {
        setBusyId(row.id);
        setError('');
        try {
            replaceRow(await ffStartAssembly(row.id));
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Ошибка');
        } finally {
            setBusyId(null);
        }
    };

    const handleShip = async (row: FfAssemblyRow) => {
        setBusyId(row.id);
        setError('');
        try {
            replaceRow(await ffShipAssembly(row.id));
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Ошибка отгрузки');
        } finally {
            setBusyId(null);
        }
    };

    const handleArchive = async (row: FfAssemblyRow) => {
        setBusyId(row.id);
        setError('');
        try {
            await ffArchiveAssembly(row.id, !row.is_archived);
            // The row leaves the current view (active/archive) — drop it locally, then reload.
            setRows((prev) => prev.filter((r) => r.id !== row.id));
            reload();
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Ошибка архивации');
        } finally {
            setBusyId(null);
        }
    };

    const openReady = (row: FfAssemblyRow) => {
        setReadyError('');
        setPallets(row.pallets_count != null ? String(row.pallets_count) : '');
        setWeight(row.pallet_weight_kg != null ? String(Number(row.pallet_weight_kg)) : '');
        setReadyRow(row);
    };

    const submitReady = async () => {
        if (!readyRow) return;
        const pc = Number(pallets);
        const pw = Number(weight);
        if (!(pc > 0) || !(pw > 0)) {
            setReadyError('Укажите число паллет и вес паллеты больше нуля');
            return;
        }
        setReadyError('');
        setBusyId(readyRow.id);
        try {
            const updated = await ffReadyAssembly(readyRow.id, {
                pallets_count: pc,
                pallet_weight_kg: pw,
            });
            replaceRow(updated);
            setReadyRow(null);
        } catch (err: unknown) {
            setReadyError(err instanceof Error ? err.message : 'Ошибка');
        } finally {
            setBusyId(null);
        }
    };

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Заявки на сборку</h1>
                    <p className="page-subtitle">Сборка и отгрузка поставок</p>
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
                    <label htmlFor="asm-search">Поиск</label>
                    <input
                        id="asm-search"
                        type="text"
                        placeholder="№, склад, WB-склад"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                    />
                </div>
                <div className="ff-toolbar-field">
                    <label htmlFor="asm-project">Проект</label>
                    <select id="asm-project" value={projectSlug} onChange={(e) => setProjectSlug(e.target.value)}>
                        <option value="">Все проекты</option>
                        {me?.projects.map((p) => (
                            <option key={p.slug} value={p.slug}>
                                {p.name}
                            </option>
                        ))}
                    </select>
                </div>
                <div className="ff-toolbar-field">
                    <label htmlFor="asm-status">Статус</label>
                    <select
                        id="asm-status"
                        value={statusFilter}
                        onChange={(e) => setStatusFilter(e.target.value as FfAssemblyStatus | '')}
                    >
                        {STATUS_OPTIONS.map((o) => (
                            <option key={o.value || 'all'} value={o.value}>
                                {o.label}
                            </option>
                        ))}
                    </select>
                </div>
                <div className="ff-toolbar-field">
                    <label htmlFor="asm-warehouse">Склад</label>
                    <select
                        id="asm-warehouse"
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
                <div className="ff-toolbar-field">
                    <label htmlFor="asm-stuck">Зависшие</label>
                    <label
                        htmlFor="asm-stuck"
                        style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', height: 38 }}
                    >
                        <input
                            id="asm-stuck"
                            type="checkbox"
                            checked={onlyStuck}
                            onChange={(e) => setOnlyStuck(e.target.checked)}
                        />
                        <span style={{ fontSize: 14 }}>Только зависшие</span>
                    </label>
                </div>
            </div>

            {!loading && !error && rows.length > 0 && (
                <div className="stats-grid">
                    <div className="stat-card">
                        <div className="stat-card-label">В сборке</div>
                        <div className="stat-card-value" style={{ color: 'var(--color-accent)' }}>{formatNumber(metrics.toAssemble, 0)}</div>
                    </div>
                    <div className="stat-card">
                        <div className="stat-card-label">Готово к отгрузке</div>
                        <div className="stat-card-value">{formatNumber(metrics.ready, 0)}</div>
                    </div>
                    <div className="stat-card">
                        <div className="stat-card-label">Отгружено</div>
                        <div className="stat-card-value" style={{ color: 'var(--color-success)' }}>{formatNumber(metrics.shipped, 0)}</div>
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
                            {tab === 'archive' ? 'В архиве пусто' : 'Нет задач — всё собрано'}
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
                                <SortableTh label="WB-склад" sortKey="wb_warehouse" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="ФБО поставка" sortKey="fbo" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="Тип" sortKey="package" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="Бренд" sortKey="brand" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="Позиции" sortKey="items" activeKey={sortKey} dir={sortDir} onSort={toggleSort} align="right" />
                                <SortableTh label="Паллеты" sortKey="pallets" activeKey={sortKey} dir={sortDir} onSort={toggleSort} align="right" />
                                <SortableTh label="Создана" sortKey="created" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <SortableTh label="Статус" sortKey="status" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                                <th>Действие</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sorted.map((row) => (
                                <tr
                                    key={row.id}
                                    className="ff-row-link"
                                    onClick={() => router.push(`/ff/assemblies/${row.id}`)}
                                >
                                    <td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>
                                        <Link
                                            href={`/ff/assemblies/${row.id}`}
                                            onClick={(e) => e.stopPropagation()}
                                            style={{ color: 'var(--color-accent)' }}
                                        >
                                            № {row.number}
                                        </Link>
                                    </td>
                                    <td><ProjectBadge name={row.project_name} /></td>
                                    <td>
                                        <div>{row.warehouse_name}</div>
                                        {row.wb_warehouse_name && (
                                            <div style={{ color: 'var(--color-text-muted)', fontSize: 12 }}>→ {row.wb_warehouse_name}</div>
                                        )}
                                    </td>
                                    <td style={{ whiteSpace: 'nowrap' }}>
                                        {row.fbo_supply_number ? (
                                            <span style={{ fontFamily: 'monospace', fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
                                                {row.fbo_supply_number}
                                            </span>
                                        ) : (
                                            <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                                        )}
                                    </td>
                                    <td style={{ color: 'var(--color-text-muted)' }}>{row.package_type || '—'}</td>
                                    <td>{row.brands || '—'}</td>
                                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatNumber(row.items.length, 0)}</td>
                                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                                        {row.pallets_count != null ? formatNumber(row.pallets_count, 0) : '—'}
                                        {row.pallets_count != null && row.pallet_weight_kg != null && (
                                            <div style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>{formatNumber(Number(row.pallet_weight_kg), 0)} кг/пал</div>
                                        )}
                                    </td>
                                    <td style={{ whiteSpace: 'nowrap', color: 'var(--color-text-muted)', fontSize: 13 }}>
                                        {formatDate(row.created_at)}
                                    </td>
                                    <td>
                                        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                                            <AssemblyStatusBadge status={row.status} />
                                            <StuckBadge days={assemblyStuckDays(row.status, row.created_at, row.actual_ready_date)} />
                                        </div>
                                    </td>
                                    <td style={{ whiteSpace: 'nowrap' }} onClick={(e) => e.stopPropagation()}>
                                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                                            {row.status === 'PENDING' && (
                                                <button
                                                    className="btn btn-primary btn-sm"
                                                    disabled={busyId === row.id}
                                                    onClick={() => handleStart(row)}
                                                >
                                                    {busyId === row.id ? '…' : 'Взять в работу'}
                                                </button>
                                            )}
                                            {row.status === 'IN_PROGRESS' && (
                                                <button className="btn btn-primary btn-sm" onClick={() => openReady(row)}>
                                                    Готово
                                                </button>
                                            )}
                                            {row.status === 'READY' && (
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    disabled
                                                    title="Ждём назначения машины логистикой"
                                                >
                                                    Ждём машину
                                                </button>
                                            )}
                                            {row.status === 'VEHICLE_ASSIGNED' && (
                                                <button
                                                    className="btn btn-success btn-sm"
                                                    disabled={busyId === row.id || !row.vehicle_assigned}
                                                    title={row.vehicle_assigned ? undefined : 'Ждём назначения машины логистикой'}
                                                    onClick={() => handleShip(row)}
                                                >
                                                    {busyId === row.id ? '…' : 'Отгрузил'}
                                                </button>
                                            )}
                                            {row.status !== 'DELIVERED' && row.status !== 'CANCELLED' && (
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    disabled={busyId === row.id}
                                                    onClick={() => handleArchive(row)}
                                                    title={row.is_archived ? 'Вернуть из архива' : 'Убрать в архив'}
                                                >
                                                    {row.is_archived ? 'Из архива' : 'В архив'}
                                                </button>
                                            )}
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* ─── Готовность modal (quick ready kept on the list) ─── */}
            {readyRow && (
                <div className="modal-overlay" onClick={() => setReadyRow(null)}>
                    <div className="modal-card" onClick={(e) => e.stopPropagation()}>
                        <h2 className="modal-title">Готовность № {readyRow.number}</h2>
                        <div className="form-group" style={{ marginBottom: 16 }}>
                            <label className="form-label">Паллет, шт</label>
                            <input
                                className="form-input"
                                type="number"
                                min={1}
                                inputMode="numeric"
                                value={pallets}
                                onChange={(e) => setPallets(e.target.value)}
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Вес паллеты, кг</label>
                            <input
                                className="form-input"
                                type="number"
                                min={0}
                                step="0.1"
                                inputMode="decimal"
                                value={weight}
                                onChange={(e) => setWeight(e.target.value)}
                            />
                        </div>
                        {readyError && (
                            <div className="glass-card" style={{ marginTop: 16, color: 'var(--color-danger)' }}>{readyError}</div>
                        )}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 24 }}>
                            <button
                                className="btn btn-secondary"
                                disabled={busyId === readyRow.id}
                                onClick={() => setReadyRow(null)}
                            >
                                Отмена
                            </button>
                            <button
                                className="btn btn-primary"
                                disabled={busyId === readyRow.id}
                                onClick={submitReady}
                            >
                                {busyId === readyRow.id ? 'Сохранение…' : 'Готово'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
