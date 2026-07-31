'use client';
/**
 * Вкладка «Перемещения» страницы сборки — переезды между нашими складами.
 *
 * Переезд = полноценная поездка: у него такая же машина/логистика, как у заявки
 * на сборку, поэтому список показывает и машину, и стоимость забора. Ступени
 * статуса «машина назначена» НЕТ (черновик → в пути → принято): назначенная
 * машина показывается бейджем прямо на черновике (см. transferVehicleAssigned).
 *
 * Фильтрация — клиентская: эндпоинт списка умеет только in_transit и
 * warehouse_id (источник ИЛИ получатель), а набор мал (серверный кап 500).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import { Toast, AssignVehicleModal } from '@/components';
import type { AssignVehicleValues } from '@/components';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import { usePermissions } from '@/lib/hooks/usePermissions';
import type { StockTransfer, StockTransferStatus, TransferLogisticsSummary, Warehouse } from '@/types/api';
import {
    TRANSFER_REPORT_DEFAULT_DAYS,
    TRANSFER_STATUS_MAP,
    toMoney,
    transferReportDefaultRange,
    transferDriverName,
    transferSkuCount,
    transferUnits,
    transferVehicleAssigned,
    unitCountText,
    unitShort,
} from '@/lib/transfer';

const STATUS_OPTIONS: { value: '' | StockTransferStatus; label: string }[] = [
    { value: '', label: 'Все статусы' },
    { value: 'DRAFT', label: 'Черновик' },
    { value: 'IN_TRANSIT', label: 'В пути' },
    { value: 'COMPLETED', label: 'Принято' },
];

interface Props {
    slug: string;
}

export default function TransfersTab({ slug }: Props) {
    const router = useRouter();
    const { canEdit } = usePermissions();

    const [items, setItems] = useState<StockTransfer[]>([]);
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    // Фильтры
    const [search, setSearch] = useState('');
    const [fromId, setFromId] = useState<number | ''>('');
    const [toId, setToId] = useState<number | ''>('');
    const [statusFilter, setStatusFilter] = useState<'' | StockTransferStatus>('');
    const [noVehicleOnly, setNoVehicleOnly] = useState(false);

    // Модалка выбора склада-источника перед созданием переезда: форма создания
    // живёт на складе (/warehouse/{id}/transfer/new) и без него не открывается.
    const [showCreate, setShowCreate] = useState(false);
    const [createFromId, setCreateFromId] = useState<number | ''>('');

    // Назначение машины прямо из списка (кнопка в колонке «Машина»).
    const [vehicleFor, setVehicleFor] = useState<StockTransfer | null>(null);
    const [assigning, setAssigning] = useState(false);
    const [assignError, setAssignError] = useState('');

    // Мини-сводка стоимости логистики переездов (тот же отчёт, что на «Листе
    // логиста»): необязательная — ошибку глушим, список от неё не зависит.
    const [costSummary, setCostSummary] = useState<TransferLogisticsSummary | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const rows = await api.getTransfers(false);
            setItems(rows);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    useEffect(() => {
        // Тот же период, что и на вкладке «Переезды» (общий источник —
        // transferReportDefaultRange). Без дат отчёт считает за ВСЁ время, и
        // суммы в сводке не сошлись бы с экраном, куда ведёт «Подробнее».
        api.getTransferLogisticsReport(transferReportDefaultRange())
            .then(r => setCostSummary(r.summary))
            .catch(() => {});
    }, []);

    // Справочник складов — для имён маршрута и контрагента склада забора.
    // Не фильтруем по типу: переезд бывает и на транзитный, и на внешний склад.
    useEffect(() => {
        api.getWarehouses().then(setWarehouses).catch(() => {});
    }, []);

    const warehouseById = useMemo(() => {
        const map = new Map<number, Warehouse>();
        warehouses.forEach(w => map.set(w.id, w));
        return map;
    }, [warehouses]);

    const whName = useCallback(
        (id: number) => warehouseById.get(id)?.name ?? `Склад ${id}`,
        [warehouseById],
    );
    // Имена концов маршрута отдаёт сам список; справочник — только фолбэк
    // (и источник контрагента склада забора для модалки машины).
    const routeFrom = useCallback((t: StockTransfer) => t.from_warehouse_name || whName(t.from_warehouse_id), [whName]);
    const routeTo = useCallback((t: StockTransfer) => t.to_warehouse_name || whName(t.to_warehouse_id), [whName]);

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        return items.filter(t => {
            if (q && !t.number.toLowerCase().includes(q)) return false;
            if (fromId !== '' && t.from_warehouse_id !== fromId) return false;
            if (toId !== '' && t.to_warehouse_id !== toId) return false;
            if (statusFilter && t.status !== statusFilter) return false;
            if (noVehicleOnly && transferVehicleAssigned(t)) return false;
            return true;
        });
    }, [items, search, fromId, toId, statusFilter, noVehicleOnly]);

    const hasActiveFilters = search !== '' || fromId !== '' || toId !== '' || statusFilter !== '' || noVehicleOnly;

    const resetFilters = () => {
        setSearch('');
        setFromId('');
        setToId('');
        setStatusFilter('');
        setNoVehicleOnly(false);
    };

    const openDetail = (id: number) => router.push(`/p/${slug}/warehouse/transfers/${id}`);

    const handleAssign = async (values: AssignVehicleValues) => {
        if (!vehicleFor) return;
        setAssigning(true);
        setAssignError('');
        try {
            const updated = await api.assignTransferVehicle(vehicleFor.id, values);
            setItems(prev => prev.map(t => (t.id === updated.id ? updated : t)));
            setVehicleFor(null);
            setToast({ message: `Машина назначена на ${updated.number}`, type: 'success' });
        } catch (e: unknown) {
            setAssignError(e instanceof Error ? e.message : 'Ошибка назначения машины');
        } finally {
            setAssigning(false);
        }
    };

    const columns: Column[] = [
        {
            key: 'number', label: 'Номер', width: '110px',
            render: (_v, row: StockTransfer) => (
                <span style={{ fontWeight: 600 }}>{row.number}</span>
            ),
        },
        {
            key: 'route', label: 'Откуда → Куда',
            getValue: (row: StockTransfer) => `${routeFrom(row)} → ${routeTo(row)}`,
            render: (_v, row: StockTransfer) => (
                <span>
                    {routeFrom(row)}
                    <span style={{ color: 'var(--color-text-muted)' }}> → </span>
                    {routeTo(row)}
                </span>
            ),
            exportValue: (row: StockTransfer) => `${routeFrom(row)} → ${routeTo(row)}`,
        },
        {
            key: 'items', label: 'Состав', align: 'right', width: '150px',
            getValue: (row: StockTransfer) => transferUnits(row),
            render: (_v, row: StockTransfer) => (
                <span>
                    {formatNumber(transferSkuCount(row), 0)} SKU
                    <span style={{ color: 'var(--color-text-muted)' }}> / </span>
                    {formatNumber(transferUnits(row), 0)} шт
                </span>
            ),
            exportValue: (row: StockTransfer) => `${transferSkuCount(row)} SKU / ${transferUnits(row)} шт`,
        },
        {
            key: 'pallets_count', label: 'Кол-во ед.', align: 'right', width: '120px',
            headerTitle: 'Транспортная единица переезда: паллеты или короба (как у заявки на сборку)',
            getValue: (row: StockTransfer) => row.pallets_count ?? -1,
            render: (_v, row: StockTransfer) => (
                row.pallets_count == null
                    ? <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                    : <span>{unitCountText(row.pallets_count, row.shipped_as_boxes)}</span>
            ),
            exportValue: (row: StockTransfer) => (
                row.pallets_count == null ? '' : `${row.pallets_count} ${unitShort(row.shipped_as_boxes)}`
            ),
        },
        {
            key: 'status', label: 'Статус', width: '150px',
            render: (_v, row: StockTransfer) => {
                const st = TRANSFER_STATUS_MAP[row.status] ?? { label: row.status, className: 'badge-secondary' };
                return (
                    <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                        <span className={`badge ${st.className}`}>{st.label}</span>
                        {row.is_defect && <span className="badge badge-warning" title={row.defect_reason || 'Брак'}>Брак</span>}
                    </span>
                );
            },
            exportValue: (row: StockTransfer) => TRANSFER_STATUS_MAP[row.status]?.label ?? row.status,
        },
        {
            key: 'vehicle', label: 'Машина', width: '190px',
            getValue: (row: StockTransfer) => row.vehicle_info || '',
            render: (_v, row: StockTransfer) => {
                if (transferVehicleAssigned(row)) {
                    const driver = transferDriverName(row);
                    return (
                        <span title={[row.vehicle_brand, driver, row.driver_phone].filter(Boolean).join(' · ') || undefined}>
                            <span className="badge badge-info">🚚 {row.vehicle_info || 'назначена'}</span>
                        </span>
                    );
                }
                // Назначать машину бэкенд разрешает только в черновике; в пути и
                // принятое — уже история, кнопку не показываем.
                if (row.status !== 'DRAFT') return <span style={{ color: 'var(--color-text-muted)' }}>—</span>;
                if (!canEdit()) return <span style={{ color: 'var(--color-text-muted)' }}>—</span>;
                return (
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={e => { e.stopPropagation(); setAssignError(''); setVehicleFor(row); }}
                    >
                        Назначить
                    </button>
                );
            },
            exportValue: (row: StockTransfer) => row.vehicle_info || '',
        },
        {
            key: 'pickup_cost', label: 'Стоимость забора', align: 'right', width: '150px',
            getValue: (row: StockTransfer) => toMoney(row.pickup_cost) ?? 0,
            render: (_v, row: StockTransfer) => {
                const cost = toMoney(row.pickup_cost);
                return cost === null
                    ? <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                    : <span>{formatNumber(cost, 0)} ₽</span>;
            },
            exportValue: (row: StockTransfer) => toMoney(row.pickup_cost) ?? '',
        },
        {
            key: 'created_at', label: 'Дата', width: '130px',
            getValue: (row: StockTransfer) => row.pickup_date || row.created_at || '',
            render: (_v, row: StockTransfer) => (
                <span title={row.pickup_date ? 'Дата забора' : 'Дата создания'}>
                    {formatDate(row.pickup_date || row.created_at)}
                </span>
            ),
            exportValue: (row: StockTransfer) => row.pickup_date || row.created_at || '',
        },
    ];

    return (
        <div>
            {toast && (
                <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} duration={toast.type === 'error' ? 4000 : 2500} />
            )}

            {/* Шапка вкладки */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                    Переезды между нашими складами: {formatNumber(filtered.length, 0)}
                    {filtered.length !== items.length && ` из ${formatNumber(items.length, 0)}`}
                </div>
                <span style={{ flex: 1 }} />
                {canEdit() && (
                    <button className="btn btn-primary" onClick={() => { setCreateFromId(fromId); setShowCreate(true); }}>
                        Создать перемещение
                    </button>
                )}
            </div>

            {/* Мини-сводка стоимости переездов → «Лист логиста», вкладка «Переезды» */}
            {costSummary && costSummary.transfers_count > 0 && (
                <div
                    className="glass-card"
                    style={{
                        padding: '10px 16px', marginBottom: 16,
                        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 13,
                        borderLeft: (toMoney(costSummary.unpaid_cost) ?? 0) > 0 ? '3px solid var(--color-warning)' : undefined,
                    }}
                >
                    <span style={{ fontSize: 15 }}>💰</span>
                    <span>
                        Переездов за {formatNumber(TRANSFER_REPORT_DEFAULT_DAYS, 0)} дней: <b>{formatNumber(costSummary.transfers_count, 0)}</b>
                        {' · '}
                        Стоимость: <b>{formatNumber(toMoney(costSummary.total_cost) ?? 0, 0)} ₽</b>
                        {' · '}
                        Не оплачено: <b style={{ color: (toMoney(costSummary.unpaid_cost) ?? 0) > 0 ? 'var(--color-warning)' : undefined }}>
                            {formatNumber(toMoney(costSummary.unpaid_cost) ?? 0, 0)} ₽
                        </b>
                    </span>
                    <span style={{ flex: 1 }} />
                    <Link
                        href={`/p/${slug}/warehouse/logistics?tab=transfers`}
                        style={{ color: 'var(--color-accent)' }}
                        title="Отчёт по стоимости логистики переездов на «Листе логиста»"
                    >
                        Подробнее →
                    </Link>
                </div>
            )}

            {/* Фильтры */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap' }}>
                    <div className="form-group" style={{ flex: 1, minWidth: 200 }}>
                        <input
                            className="form-input"
                            placeholder="Поиск по номеру перемещения (TR-31)…"
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                        />
                    </div>
                    <div className="form-group">
                        <select
                            className="form-input"
                            value={fromId}
                            onChange={e => setFromId(e.target.value ? Number(e.target.value) : '')}
                        >
                            <option value="">Откуда: все склады</option>
                            {warehouses.map(w => (
                                <option key={w.id} value={w.id}>{w.name}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group">
                        <select
                            className="form-input"
                            value={toId}
                            onChange={e => setToId(e.target.value ? Number(e.target.value) : '')}
                        >
                            <option value="">Куда: все склады</option>
                            {warehouses.map(w => (
                                <option key={w.id} value={w.id}>{w.name}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group">
                        <select
                            className="form-input"
                            value={statusFilter}
                            onChange={e => setStatusFilter(e.target.value as '' | StockTransferStatus)}
                        >
                            {STATUS_OPTIONS.map(o => (
                                <option key={o.value} value={o.value}>{o.label}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group">
                        <select
                            className="form-input"
                            value={noVehicleOnly ? 'none' : ''}
                            onChange={e => setNoVehicleOnly(e.target.value === 'none')}
                            title="Переезды, на которые ещё не назначена машина"
                        >
                            <option value="">Машина: все</option>
                            <option value="none">Без машины</option>
                        </select>
                    </div>
                    {hasActiveFilters && (
                        <div className="form-group">
                            <button className="btn btn-secondary" onClick={resetFilters}>✕ Сбросить</button>
                        </div>
                    )}
                </div>
            </div>

            {/* Состояния взаимоисключающие: при ошибке НЕ показываем ещё и
                «Перемещений нет» — пустой список из-за сбоя загрузки читался бы
                как «переездов действительно нет». */}
            {error ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-danger)' }}>
                    {error}
                    <div style={{ marginTop: 12 }}>
                        <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
                    </div>
                </div>
            ) : loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            ) : filtered.length === 0 ? (
                <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>🚚</div>
                    <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>
                        {items.length > 0 ? 'Ничего не найдено' : 'Перемещений нет'}
                    </div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                        {items.length > 0
                            ? 'Под текущие фильтры не подходит ни одно перемещение — сбросьте фильтры'
                            : 'Перемещений нет — создайте переезд или переделайте заявку в перемещение'}
                    </div>
                </div>
            ) : (
                <TanStackDataTable
                    columns={columns}
                    data={filtered}
                    onRowClick={(row: StockTransfer) => openDetail(row.id)}
                    pageSize={50}
                    exportName="stock_transfers"
                />
            )}

            {/* Выбор склада-источника → форма создания переезда */}
            {showCreate && (
                <div className="modal-overlay" onClick={() => setShowCreate(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 420 }}>
                        <h2 className="modal-title">Создать перемещение</h2>
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                            Состав переезда набирается по остаткам склада-источника — выберите, откуда едем.
                        </div>
                        <div className="form-group">
                            <label className="form-label">Склад-источник *</label>
                            <select
                                className="form-input"
                                value={createFromId}
                                onChange={e => setCreateFromId(e.target.value ? Number(e.target.value) : '')}
                                autoFocus
                            >
                                <option value="">— выберите склад —</option>
                                {warehouses.map(w => (
                                    <option key={w.id} value={w.id}>{w.name}</option>
                                ))}
                            </select>
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={() => setShowCreate(false)}>Отмена</button>
                            <button
                                className="btn btn-primary"
                                disabled={createFromId === ''}
                                onClick={() => router.push(`/p/${slug}/warehouse/${createFromId}/transfer/new`)}
                            >
                                Продолжить
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Назначение машины из списка */}
            {vehicleFor && (
                <AssignVehicleModal
                    title={`Назначить машину · ${vehicleFor.number}`}
                    initial={{
                        vehicle_info: vehicleFor.vehicle_info,
                        vehicle_brand: vehicleFor.vehicle_brand,
                        driver_first_name: vehicleFor.driver_first_name,
                        driver_last_name: vehicleFor.driver_last_name,
                        driver_phone: vehicleFor.driver_phone,
                        logistics_by_warehouse: vehicleFor.logistics_by_warehouse,
                        pickup_date: vehicleFor.pickup_date,
                        pickup_time_slot: vehicleFor.pickup_time_slot,
                        pickup_cost: toMoney(vehicleFor.pickup_cost),
                        delivery_date: vehicleFor.delivery_date,
                        pallets_count: vehicleFor.pallets_count,
                        pallet_weight_kg: toMoney(vehicleFor.pallet_weight_kg),
                        shipped_as_boxes: vehicleFor.shipped_as_boxes,
                    }}
                    pickupWarehouseName={routeFrom(vehicleFor)}
                    pickupWarehouseCounterpartyId={warehouseById.get(vehicleFor.from_warehouse_id)?.counterparty_id ?? null}
                    deliveryDateLabel="Дата доставки"
                    submitting={assigning}
                    error={assignError}
                    onSubmit={handleAssign}
                    onClose={() => { setVehicleFor(null); setAssignError(''); }}
                />
            )}
        </div>
    );
}
