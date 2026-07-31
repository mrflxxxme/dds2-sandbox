'use client';
/**
 * Вкладка «Перемещения» страницы сборки — переезды между нашими складами.
 *
 * Переезд = полноценная поездка: у него такая же машина/логистика, как у заявки
 * на сборку, поэтому список показывает и машину, и стоимость забора. Статус —
 * ЗЕРКАЛО заявки (PENDING → … → DELIVERED), включая отдельную ступень
 * «Машина назначена»; словарь и гейты — общие, из lib/transfer.ts.
 *
 * Фильтрация — клиентская: эндпоинт списка умеет только in_transit и
 * warehouse_id (источник ИЛИ получатель), а набор мал (серверный кап 500).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import { Toast } from '@/components';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import { usePermissions } from '@/lib/hooks/usePermissions';
import type { StockTransfer, StockTransferStatus, TransferLogisticsSummary, Warehouse } from '@/types/api';
import {
    TRANSFER_REPORT_DEFAULT_DAYS,
    TRANSFER_STATUS_MAP,
    ffLinkLabel,
    toMoney,
    transferReceiveProgress,
    transferReportDefaultRange,
    transferDriverName,
    transferSkuCount,
    transferTotalWeight,
    transferUnits,
    transferVehicleAssigned,
    unitCountText,
    unitShort,
} from '@/lib/transfer';

// Опции фильтра выводим ИЗ словаря: добавится ступень на бэкенде — она
// появится и здесь, а не потеряется в руками собранном списке (ровно так
// фильтр и разъехался бы с бейджами в таблице).
const STATUS_OPTIONS: { value: '' | StockTransferStatus; label: string }[] = [
    { value: '', label: 'Все статусы' },
    ...(Object.entries(TRANSFER_STATUS_MAP) as [StockTransferStatus, { label: string }][])
        .map(([value, { label }]) => ({ value, label })),
];

/** Номера связанных заявок ФФ одной строкой — обе стороны маршрута подряд. */
function ffNumbers(t: StockTransfer): string[] {
    return (t.ff_links ?? []).map(ffLinkLabel).filter(n => n && n !== '—');
}

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

    // Набор колонок повторяет вкладку «Заявки FBO» (assembly/page.tsx, cols):
    // № → Статус → маршрут/склад → Товары → Палеты → Общий вес → даты. Там, где
    // у заявки WB-специфика (номер поставки WB, тип приёмки), у переезда её нет
    // и прочерк был бы шумом — вместо неё стоят машина и стоимость забора,
    // которые для переезда играют ту же роль «чем и почём везём».
    const columns: Column[] = [
        {
            key: 'number', label: '№', width: '150px',
            // Теги — рядом с номером, как у заявки (совместная / предзаявка / ФФ).
            render: (_v, row: StockTransfer) => (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 600 }}>{row.number}</span>
                    {row.is_defect && (
                        <span className="badge badge-warning" style={{ fontSize: 11 }} title={row.defect_reason || 'Переезд брака'}>
                            Брак
                        </span>
                    )}
                </span>
            ),
            exportValue: (row: StockTransfer) => (row.is_defect ? `${row.number} (брак)` : row.number),
        },
        {
            key: 'status', label: 'Статус', width: '150px',
            getValue: (row: StockTransfer) => TRANSFER_STATUS_MAP[row.status]?.label ?? row.status,
            render: (_v, row: StockTransfer) => {
                const st = TRANSFER_STATUS_MAP[row.status] ?? { label: row.status, className: 'badge-secondary' };
                return <span className={`badge ${st.className}`}>{st.label}</span>;
            },
            exportValue: (row: StockTransfer) => TRANSFER_STATUS_MAP[row.status]?.label ?? row.status,
        },
        {
            key: 'ff', label: 'Заявка ФФ',
            // Связки приходят прямо в списке (батч на бэкенде, N+1 нет). Обе
            // стороны маршрута в одной ячейке: у источника сборка, у получателя
            // приёмка — логисту важен сам факт «документ у ФФ заведён».
            headerTitle: 'Связанные заявки фулфилмента: отгрузка у источника и приёмка у получателя',
            getValue: (row: StockTransfer) => ffNumbers(row).join(', '),
            render: (_v, row: StockTransfer) => {
                const nums = ffNumbers(row);
                return nums.length === 0
                    ? <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                    : <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{nums.join(', ')}</span>;
            },
            exportValue: (row: StockTransfer) => ffNumbers(row).join(', '),
        },
        {
            key: 'route', label: 'Маршрут',
            headerTitle: 'Откуда → Куда. Аналог пары «Склад» + «WB-склад» у заявки FBO',
            getValue: (row: StockTransfer) => `${routeFrom(row)} → ${routeTo(row)}`,
            render: (_v, row: StockTransfer) => (
                <span style={{ fontSize: 13 }}>
                    {routeFrom(row)}
                    <span style={{ display: 'block', fontSize: 12, color: 'var(--color-text-muted)' }}>
                        → {routeTo(row)}
                    </span>
                </span>
            ),
            exportValue: (row: StockTransfer) => `${routeFrom(row)} → ${routeTo(row)}`,
        },
        {
            key: 'items_qty', label: 'Товары', align: 'right', width: '150px',
            headerTitle: 'Штук всего и сколько это позиций (SKU)',
            getValue: (row: StockTransfer) => transferUnits(row),
            render: (_v, row: StockTransfer) => {
                // Прогресс приёмки есть только у уехавшего переезда и только в
                // списке (в карточке бэкенд его не считает) — см. transferReceiveProgress.
                const progress = transferReceiveProgress(row);
                return (
                    <span>
                        {formatNumber(transferUnits(row), 0)} шт
                        <span style={{ display: 'block', fontSize: 12, color: 'var(--color-text-muted)' }}>
                            {formatNumber(transferSkuCount(row), 0)} SKU
                        </span>
                        {progress && (
                            <span
                                style={{
                                    display: 'block', fontSize: 12,
                                    color: progress.received > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)',
                                }}
                                title="Сколько единиц получатель уже зачислил себе"
                            >
                                принято {formatNumber(progress.received, 0)} из {formatNumber(progress.total, 0)}
                            </span>
                        )}
                    </span>
                );
            },
            exportValue: (row: StockTransfer) => transferUnits(row),
        },
        {
            key: 'pallets_count', label: 'Палеты', align: 'right', width: '120px',
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
            key: 'total_weight_kg', label: 'Общий вес', align: 'right', width: '120px',
            // Отдельного поля общего веса у переезда нет: считаем сами, как
            // карточка (вес ОДНОЙ единицы × количество). Нет одного из двух — прочерк,
            // а не ноль: «0 кг» читался бы как «взвесили и получилось ноль».
            headerTitle: 'Вес одной единицы × количество единиц',
            getValue: (row: StockTransfer) => transferTotalWeight(row) ?? -1,
            render: (_v, row: StockTransfer) => {
                const w = transferTotalWeight(row);
                return w === null
                    ? <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                    : <span>{formatNumber(w, 0)} кг</span>;
            },
            exportValue: (row: StockTransfer) => transferTotalWeight(row) ?? '',
        },
        {
            key: 'vehicle', label: 'Машина', width: '190px',
            // Только ПОКАЗ. Назначение машины живёт на «Листе логиста» — так же,
            // как у заявок на сборку: логист сажает на одну машину пачку
            // документов сразу, а не по одному из карточки списка.
            getValue: (row: StockTransfer) => row.vehicle_info || '',
            render: (_v, row: StockTransfer) => {
                if (!transferVehicleAssigned(row)) return <span style={{ color: 'var(--color-text-muted)' }}>—</span>;
                const driver = transferDriverName(row);
                return (
                    <span title={[row.vehicle_brand, driver, row.driver_phone].filter(Boolean).join(' · ') || undefined}>
                        <span className="badge badge-info">🚚 {row.vehicle_info || 'назначена'}</span>
                    </span>
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
            key: 'pickup_date', label: 'Дата забора', width: '130px',
            getValue: (row: StockTransfer) => row.pickup_date || '',
            render: (_v, row: StockTransfer) => (
                row.pickup_date
                    ? <span>{formatDate(row.pickup_date)}</span>
                    : <span style={{ color: 'var(--color-text-muted)' }}>—</span>
            ),
            exportValue: (row: StockTransfer) => (row.pickup_date ? formatDate(row.pickup_date) : ''),
        },
        {
            key: 'delivery_date', label: 'Дата доставки', width: '130px',
            getValue: (row: StockTransfer) => row.delivery_date || '',
            render: (_v, row: StockTransfer) => (
                row.delivery_date
                    ? <span>{formatDate(row.delivery_date)}</span>
                    : <span style={{ color: 'var(--color-text-muted)' }}>—</span>
            ),
            exportValue: (row: StockTransfer) => (row.delivery_date ? formatDate(row.delivery_date) : ''),
        },
        {
            // Вехи цепочки — те же колонки, что у заявки FBO («Дата готовности»).
            // Из статуса не выводятся: он знает только текущее состояние, а
            // «когда собрали» нужно и после отгрузки.
            key: 'actual_ready_date', label: 'Готовность', width: '130px',
            headerTitle: 'Когда переезд отметили собранным (кнопкой «Готов» или синком ФФ)',
            getValue: (row: StockTransfer) => row.actual_ready_date || '',
            render: (_v, row: StockTransfer) => (
                row.actual_ready_date
                    ? <span>{formatDate(row.actual_ready_date)}</span>
                    : <span style={{ color: 'var(--color-text-muted)' }}>—</span>
            ),
            exportValue: (row: StockTransfer) => (row.actual_ready_date ? formatDate(row.actual_ready_date) : ''),
        },
        {
            key: 'shipped_at', label: 'Отгружен', width: '130px',
            headerTitle: 'Когда переезд уехал со склада-источника. При возврате обнуляется — следующая попытка проставит свой',
            getValue: (row: StockTransfer) => row.shipped_at || '',
            render: (_v, row: StockTransfer) => (
                row.shipped_at
                    ? <span>{formatDate(row.shipped_at)}</span>
                    : <span style={{ color: 'var(--color-text-muted)' }}>—</span>
            ),
            exportValue: (row: StockTransfer) => (row.shipped_at ? formatDate(row.shipped_at) : ''),
        },
        {
            key: 'created_at', label: 'Создано', width: '130px',
            getValue: (row: StockTransfer) => row.created_at || '',
            render: (_v, row: StockTransfer) => formatDate(row.created_at),
            exportValue: (row: StockTransfer) => (row.created_at ? formatDate(row.created_at) : ''),
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

        </div>
    );
}
