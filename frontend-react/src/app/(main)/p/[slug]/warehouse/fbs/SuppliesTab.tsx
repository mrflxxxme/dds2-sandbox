'use client';
/**
 * Вкладка «Поставки» — ГЛАВНЫЙ экран сборки FBS.
 *
 * В кабинете WB единица работы — поставка, а не отдельное задание, и клик по
 * ней УВОДИТ НА ЭКРАН ПОСТАВКИ: шапка (склад, габарит, заказы, QR), панель
 * действий (лист подбора, стикеры, передача) и список заданий. Здесь так же:
 * список поставок → экран поставки → «← к поставкам». Плоский список заданий
 * остаётся на вкладке «Заказы» как источник, из которого поставки набираются.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { FbsSupply, FbsSupplyStatus, FbsWarehouse } from '@/types/api';
import {
    SUPPLY_STATUS_LABEL,
    SUPPLY_STATUSES,
    SupplyStatusBadge,
    cargoLabel,
    crossBorderLabel,
    downloadBase64,
    supplyOrdersCount,
    supplyOrdersMirrorHint,
    supplyOrdersMirrorTitle,
    supplyStatusOf,
} from './fbsShared';
import { MiniKpi } from './stockColumns';
import SupplyDetailScreen from './SupplyDetailScreen';

interface Props {
    warehouses: FbsWarehouse[];
    /**
     * Режим контура разрешает запись в WB. Создание, передача и удаление
     * поставки меняют кабинет → гасятся; синк и QR — чтение, доступны всегда.
     */
    writeEnabled: boolean;
    /** Текст подсказки на выключенной кнопке: safe-режим или «режим не загружен». */
    writeHint: string;
    onShowOrders: (supplyId: string) => void;
    onToast: (msg: string) => void;
    /** Счётчик тиков автообновления страницы; 0 — тиков ещё не было. */
    refreshTick: number;
}

/** Пусто — все состояния; иначе одно из четырёх (query-параметр `status`). */
type StatusFilter = '' | FbsSupplyStatus;

export default function SuppliesTab({
    warehouses, writeEnabled, writeHint, onShowOrders, onToast, refreshTick,
}: Props) {
    const [items, setItems] = useState<FbsSupply[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actionError, setActionError] = useState('');
    const [statusFilter, setStatusFilter] = useState<StatusFilter>('');
    const [whFilter, setWhFilter] = useState<number | ''>('');
    const [syncing, setSyncing] = useState(false);
    const [busyId, setBusyId] = useState<string | null>(null);
    const [creating, setCreating] = useState(false);
    /** Открытая поставка = экран поставки вместо списка (как в кабинете WB). */
    const [openSupply, setOpenSupply] = useState<FbsSupply | null>(null);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getFbsSupplies({
                status: statusFilter || undefined,
                limit: 100,
            });
            if (signal?.aborted) return;
            setItems(res);
            // экран поставки живёт на объекте из списка — после перезагрузки
            // его надо подменить свежим, иначе шапка врёт статусом и счётчиком
            setOpenSupply(prev => (
                prev ? (res.find(s => s.wb_supply_id === prev.wb_supply_id) ?? prev) : prev
            ));
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки поставок');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [statusFilter]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    // Автообновление: загрузчик берём через ref, иначе эффект срабатывал бы на
    // каждую смену фильтра и дублировал обычную загрузку.
    const reloadRef = useRef<(signal?: AbortSignal) => void>(() => {});
    useEffect(() => { reloadRef.current = load; });
    useEffect(() => {
        if (refreshTick === 0) return; // первичную загрузку делает эффект выше
        const controller = new AbortController();
        reloadRef.current(controller.signal);
        return () => controller.abort();
    }, [refreshTick]);

    /** Склад фильтруем на клиенте: списка на 100 строк хватает, лишней ручки нет. */
    const rows = useMemo(
        () => (whFilter === '' ? items : items.filter(s => s.wb_warehouse_id === whFilter)),
        [items, whFilter],
    );

    /**
     * Счётчики по тем же четырём состояниям, что и фильтр: «Переданных» одной
     * цифрой скрывало, что часть закрытых поставок физически ещё лежит у нас
     * (QR не отсканирован). Задания считаем по данным WB — наше зеркало до
     * бэкфилла истории почти везде пустое.
     */
    const stats = useMemo(() => {
        const byStatus: Record<FbsSupplyStatus, number> = {
            active: 0, to_ship: 0, in_delivery: 0, rejected: 0,
        };
        let activeOrders = 0;
        for (const s of rows) {
            const st = supplyStatusOf(s);
            byStatus[st] += 1;
            if (st === 'active') activeOrders += supplyOrdersCount(s);
        }
        return { byStatus, activeOrders };
    }, [rows]);

    const handleSync = async () => {
        setSyncing(true);
        setActionError('');
        try {
            const res = await api.syncFbsSupplies();
            onToast(
                typeof res?.affected === 'number'
                    ? `Поставок обновлено: ${formatNumber(res.affected, 0)}`
                    : 'Поставки синхронизированы',
            );
            await load();
        } catch (e: unknown) {
            setActionError(e instanceof Error ? e.message : 'Ошибка синхронизации поставок');
        } finally {
            setSyncing(false);
        }
    };

    const handleDeliver = async (s: FbsSupply) => {
        if (!confirm(
            `Передать поставку ${s.wb_supply_id} в доставку? После передачи дополнить её заданиями нельзя.`,
        )) return;
        setBusyId(s.wb_supply_id);
        setActionError('');
        try {
            await api.deliverFbsSupply(s.wb_supply_id);
            onToast('Поставка передана в доставку');
            // Экран поставки правим СРАЗУ, не дожидаясь списка: под фильтром
            // «Активные» (и за пределами первых 100 строк) переданной поставки
            // в ответе `load()` уже нет, и `?? prev` вернул бы СТАРЫЙ объект —
            // шапка держала бы бейдж «Активная», QR не появлялся, состав не
            // перечитывался (его зависимость — `supply.done`), а кнопка
            // передачи предлагала повтор, на который WB отвечает ошибкой.
            // Свежий объект из `load()` перекроет этот, если поставка в выдаче.
            // Состояние после передачи — «Отгрузите поставку»: закрыта, но QR
            // на пункте приёма ещё никто не сканировал (`scan_dt` пуст).
            setOpenSupply(prev => (
                prev?.wb_supply_id === s.wb_supply_id
                    ? {
                        ...prev,
                        done: true,
                        status: 'to_ship',
                        closed_at: prev.closed_at ?? new Date().toISOString(),
                    }
                    : prev
            ));
            await load();
        } catch (e: unknown) {
            setActionError(e instanceof Error ? e.message : 'Ошибка передачи поставки');
        } finally {
            setBusyId(null);
        }
    };

    const handleDelete = async (s: FbsSupply) => {
        if (!confirm(`Удалить поставку ${s.wb_supply_id}? Удалить можно только активную и пустую.`)) return;
        setBusyId(s.wb_supply_id);
        setActionError('');
        try {
            await api.deleteFbsSupply(s.wb_supply_id);
            onToast('Поставка удалена');
            // удалённую поставку показывать нечем — возвращаем к списку
            setOpenSupply(prev => (prev?.wb_supply_id === s.wb_supply_id ? null : prev));
            await load();
        } catch (e: unknown) {
            setActionError(e instanceof Error ? e.message : 'Ошибка удаления поставки');
        } finally {
            setBusyId(null);
        }
    };

    const handleBarcode = async (s: FbsSupply) => {
        setBusyId(s.wb_supply_id);
        setActionError('');
        try {
            const res = await api.getFbsSupplyBarcode(s.wb_supply_id, 'png');
            if (!res?.file) {
                setActionError('WB не вернул QR — он доступен только после передачи поставки');
                return;
            }
            downloadBase64(res.file, 'image/png', `${res.barcode || s.wb_supply_id}.png`);
            onToast('QR поставки скачан');
        } catch (e: unknown) {
            setActionError(e instanceof Error ? e.message : 'Ошибка получения QR поставки');
        } finally {
            setBusyId(null);
        }
    };

    const cols: Column[] = [
        {
            key: '_qr', label: 'QR', sortable: false, width: '92px', align: 'center',
            headerTitle: 'QR-лист поставки: WB отдаёт его только после передачи в доставку',
            render: (_v: unknown, row: FbsSupply) => row.done ? (
                <button className="btn btn-secondary btn-sm" disabled={busyId === row.wb_supply_id}
                    onClick={e => { e.stopPropagation(); handleBarcode(row); }}>
                    📄 QR
                </button>
            ) : (
                <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}
                    title="QR появится после передачи поставки в доставку">
                    после передачи
                </span>
            ),
            exportValue: (row: FbsSupply) => row.qr_barcode || '',
        },
        {
            key: 'wb_supply_id', label: 'Поставка',
            render: (v: string, row: FbsSupply) => (
                <div>
                    <div style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</div>
                    {row.name && <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{row.name}</div>}
                </div>
            ),
        },
        {
            key: 'orders_count', label: 'Заданий', align: 'right',
            headerTitle: 'Заданий по данным WB. Если наше зеркало отстаёт, рядом — сколько из них уже у нас',
            render: (_v: number, row: FbsSupply) => {
                // Расхождение с зеркалом — ненавязчиво: цифра остаётся WB-шной,
                // рядом мелкая подпись «в зеркале 0 из 11» с полной подсказкой.
                const mirrorHint = supplyOrdersMirrorHint(row);
                return (
                    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                        <button
                            className="btn btn-sm"
                            title="Открыть задания этой поставки на вкладке «Заказы»"
                            onClick={e => { e.stopPropagation(); onShowOrders(row.wb_supply_id); }}
                        >
                            {formatNumber(supplyOrdersCount(row), 0)}
                        </button>
                        {mirrorHint && (
                            <span
                                style={{ fontSize: 11, color: 'var(--color-text-dim)', whiteSpace: 'nowrap' }}
                                title={supplyOrdersMirrorTitle(row)}
                            >
                                {mirrorHint}
                            </span>
                        )}
                    </div>
                );
            },
            // Сортировка — по ПОКАЗАННОЙ цифре (данные WB), а не по ключу
            // `orders_count`: иначе колонка сортируется не тем, что видно.
            getValue: (row: FbsSupply) => supplyOrdersCount(row),
            sortingFn: 'basic',
            exportValue: (row: FbsSupply) => supplyOrdersCount(row),
        },
        {
            key: 'wb_warehouse_id', label: 'Склад WB',
            render: (v: number | null) => {
                if (v == null) return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
                const wh = warehouses.find(w => w.wb_warehouse_id === v);
                return wh?.name || `#${v}`;
            },
            exportValue: (row: FbsSupply) => {
                const wh = warehouses.find(w => w.wb_warehouse_id === row.wb_warehouse_id);
                return wh?.name || (row.wb_warehouse_id != null ? `#${row.wb_warehouse_id}` : '');
            },
        },
        {
            key: 'destination_office_name', label: 'Склад поставки',
            headerTitle: 'Пункт приёма WB, куда везём поставку (destinationOfficeId)',
            render: (v: string | null, row: FbsSupply) => v
                ? <span style={{ fontSize: 13 }}>{v}</span>
                : (
                    <span style={{ color: 'var(--color-text-dim)' }}>
                        {row.destination_office_id != null ? `#${row.destination_office_id}` : '—'}
                    </span>
                ),
            exportValue: (row: FbsSupply) => row.destination_office_name
                || (row.destination_office_id != null ? `#${row.destination_office_id}` : ''),
        },
        {
            key: 'status', label: 'Статус',
            headerTitle: 'Состояние в терминах кабинета WB: закрытая поставка бывает и «Отгрузите поставку», '
                + 'и «В доставке», и «Отклонена»',
            render: (_v: string, row: FbsSupply) => <SupplyStatusBadge supply={row} />,
            // Сортируем и выгружаем ярлыком, а не машинным кодом статуса.
            getValue: (row: FbsSupply) => SUPPLY_STATUS_LABEL[supplyStatusOf(row)].label,
            exportValue: (row: FbsSupply) => SUPPLY_STATUS_LABEL[supplyStatusOf(row)].label,
        },
        {
            key: 'cargo_type', label: 'Груз',
            headerTitle: 'Габарит фиксирует первое добавленное задание — смешивать WB не разрешает',
            render: (v: number | null, row: FbsSupply) => (
                <span style={{ fontSize: 13 }}>
                    {cargoLabel(v)}
                    {row.cross_border_type ? (
                        <span style={{ color: 'var(--color-text-muted)' }}>
                            {' · '}{crossBorderLabel(row.cross_border_type)}
                        </span>
                    ) : null}
                </span>
            ),
        },
        {
            key: 'is_b2b', label: 'B2B', align: 'center',
            render: (v: boolean) => v ? '✓' : <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        {
            key: 'created_at_wb', label: 'Создана',
            render: (v: string | null) => <span style={{ fontSize: 13 }}>{v ? formatDateTime(v) : '—'}</span>,
        },
        {
            key: 'closed_at', label: 'Закрыта',
            headerTitle: 'Поставка закрывается сама при приёмке первого товара',
            render: (v: string | null) => <span style={{ fontSize: 13 }}>{v ? formatDateTime(v) : '—'}</span>,
        },
        {
            key: '_actions', label: '', sortable: false,
            render: (_v: unknown, row: FbsSupply) => (
                <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                    {!row.done && row.orders_count > 0 && (
                        <button className="btn btn-primary btn-sm"
                            disabled={!writeEnabled || busyId === row.wb_supply_id}
                            title={writeEnabled ? undefined : writeHint}
                            onClick={e => { e.stopPropagation(); handleDeliver(row); }}>
                            Передать
                        </button>
                    )}
                    {!row.done && row.orders_count === 0 && (
                        <button className="btn btn-danger btn-sm"
                            disabled={!writeEnabled || busyId === row.wb_supply_id}
                            title={writeEnabled ? undefined : writeHint}
                            onClick={e => { e.stopPropagation(); handleDelete(row); }}>
                            Удалить
                        </button>
                    )}
                    <button className="btn btn-secondary btn-sm"
                        title="Открыть экран поставки: лист подбора, стикеры, задания"
                        onClick={e => { e.stopPropagation(); setOpenSupply(row); }}>
                        Открыть →
                    </button>
                </div>
            ),
            exportValue: () => '',
        },
    ];

    // Открытая поставка занимает всю вкладку: список, фильтры и KPI прячутся,
    // вернуться — кнопкой «← к поставкам». Это и есть переход «в поставку»
    // из кабинета WB, которого не давал разворот строки.
    if (openSupply) {
        return (
            <>
                <SupplyDetailScreen
                    supply={openSupply}
                    warehouses={warehouses}
                    writeEnabled={writeEnabled}
                    writeHint={writeHint}
                    busy={busyId === openSupply.wb_supply_id}
                    onBack={() => setOpenSupply(null)}
                    onDeliver={handleDeliver}
                    onDelete={handleDelete}
                    onBarcode={handleBarcode}
                    onToast={onToast}
                />
                {actionError && (
                    <div className="glass-card" style={{ padding: 16, marginTop: 12, color: 'var(--color-danger)' }}>
                        {actionError}
                    </div>
                )}
            </>
        );
    }

    return (
        <>
            <div className="glass-card" style={{
                padding: 16, marginBottom: 16, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end',
            }}>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Статус</label>
                    <select className="form-input" style={{ width: 200 }} value={statusFilter}
                        onChange={e => setStatusFilter(e.target.value as StatusFilter)}>
                        <option value="">Все</option>
                        {SUPPLY_STATUSES.map(st => (
                            <option key={st} value={st}>{SUPPLY_STATUS_LABEL[st].label}</option>
                        ))}
                    </select>
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Склад WB</label>
                    <select className="form-input" style={{ width: 200 }} value={whFilter}
                        onChange={e => setWhFilter(e.target.value ? Number(e.target.value) : '')}>
                        <option value="">Все склады</option>
                        {warehouses.map(w => (
                            <option key={w.id} value={w.wb_warehouse_id}>{w.name || `#${w.wb_warehouse_id}`}</option>
                        ))}
                    </select>
                </div>
                <div style={{ flex: 1 }} />
                <button className="btn btn-secondary btn-sm" onClick={handleSync} disabled={syncing}>
                    {syncing ? 'Синхронизация...' : '🔄 Синхронизировать'}
                </button>
                <button
                    className="btn btn-primary btn-sm"
                    onClick={() => setCreating(true)}
                    disabled={!writeEnabled}
                    title={writeEnabled ? undefined : writeHint}
                >
                    + Создать поставку
                </button>
            </div>

            {actionError && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 12, color: 'var(--color-danger)' }}>
                    {actionError}
                </div>
            )}

            {loading && items.length === 0 ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            ) : error ? (
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>
            ) : rows.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    {statusFilter || whFilter !== '' ? (
                        <>
                            📦 Под выбранными фильтрами поставок нет
                            {statusFilter && ` — состояние «${SUPPLY_STATUS_LABEL[statusFilter].label}»`}.
                            {' '}Снимите фильтр или нажмите «Синхронизировать».
                        </>
                    ) : (
                        <>
                            📦 Поставок нет — выделите задания на вкладке «Заказы» и нажмите «В поставку»:
                            система сама разложит их по складам и габаритам.
                        </>
                    )}
                </div>
            ) : (
                <>
                    <div style={{
                        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
                        gap: 12, marginBottom: 12,
                    }}>
                        <MiniKpi label="Активных поставок" value={stats.byStatus.active} />
                        <MiniKpi label="Заданий в активных" value={stats.activeOrders} />
                        <MiniKpi label={SUPPLY_STATUS_LABEL.to_ship.label} value={stats.byStatus.to_ship} />
                        <MiniKpi label={SUPPLY_STATUS_LABEL.in_delivery.label} value={stats.byStatus.in_delivery} />
                        {stats.byStatus.rejected > 0 && (
                            <MiniKpi
                                label={SUPPLY_STATUS_LABEL.rejected.label}
                                value={stats.byStatus.rejected}
                                danger
                            />
                        )}
                    </div>

                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                        Кликните по строке — откроется экран поставки: шапка, лист подбора, стикеры
                        на всю поставку, задания и передача в доставку.
                    </div>

                    <TanStackDataTable
                        columns={cols}
                        data={rows}
                        exportName="FBS_поставки"
                        enableSorting
                        enablePagination={rows.length > 50}
                        getRowId={(row: FbsSupply) => row.wb_supply_id}
                        onRowClick={(row: FbsSupply) => setOpenSupply(row)}
                    />
                </>
            )}

            {creating && (
                <CreateSupplyModal
                    onClose={() => setCreating(false)}
                    onCreated={async () => { setCreating(false); onToast('Поставка создана'); await load(); }}
                />
            )}
        </>
    );
}

// ─── Создание поставки ──────────────────────────────────────────────────────

function CreateSupplyModal({ onClose, onCreated }: {
    onClose: () => void;
    onCreated: () => Promise<void> | void;
}) {
    const [name, setName] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    const handleSave = async () => {
        if (!name.trim()) {
            setError('Название обязательно');
            return;
        }
        setBusy(true);
        setError('');
        try {
            await api.createFbsSupply({ name: name.trim() });
            await onCreated();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка создания поставки');
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 460 }}>
                <h2 className="modal-title">Новая поставка FBS</h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16 }}>
                    Пустая поставка не имеет габаритной группы — её задаёт первое добавленное задание.
                    Обычно поставку заводить руками не нужно: кнопка «В поставку» на вкладке «Заказы»
                    сама создаст столько поставок, сколько требуют склады и габариты.
                </p>
                <div className="form-group">
                    <label className="form-label">Название *</label>
                    <input className="form-input" value={name} maxLength={128}
                        onChange={e => setName(e.target.value)} placeholder="Например: FBS 24.07" />
                </div>
                {error && <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>{error}</div>}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={busy}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleSave} disabled={busy}>
                        {busy ? 'Создание...' : 'Создать'}
                    </button>
                </div>
            </div>
        </div>
    );
}
