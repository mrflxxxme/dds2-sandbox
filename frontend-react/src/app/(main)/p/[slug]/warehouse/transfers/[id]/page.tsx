'use client';
/**
 * Деталка перемещения — переезд между нашими складами как полноценная поездка.
 *
 * Что важно помнить про контракт:
 *  • Ступени статуса «машина назначена» НЕТ (DRAFT → IN_TRANSIT → COMPLETED):
 *    назначенная машина — это признак черновика (vehicle_assigned_at), поэтому
 *    бейдж «машина назначена» рисуется отдельно от статуса.
 *  • Назначать машину бэкенд разрешает ТОЛЬКО в DRAFT; в пути/принято — 400.
 *  • Имена концов маршрута приходят в самой схеме; справочник складов грузим
 *    ради контрагента склада забора (и как фолбэк имени). ФФ-связки берём со
 *    стороны ФФ (в заявке ФФ есть stock_transfer_id) — в схеме перемещения их
 *    нет. Все эти блоки обязаны переживать отсутствие данных.
 *  • Оплата забора (OUT-xx) в схему перемещения ещё не приехала — блок
 *    показывает то, что есть (стоимость забора), и честно говорит про остальное.
 *  • «Создать поставку у Натали» — тот же контур, что на карточке машины, но
 *    состав берётся из строк ЭТОГО переезда: своей приёмки перемещение не
 *    создаёт, приход у ФФ заводит именно созданная поставка (PVB-…). Кнопка
 *    живёт только когда получатель — склад migfull-портала и переезд ещё не
 *    принят; бэкенд повторно проверяет и то, и другое.
 */
import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatDateTime, formatNumber } from '@/lib/utils';
import { Toast, AssignVehicleModal } from '@/components';
import type { AssignVehicleValues } from '@/components';
import MigfullInboundModal from '../../[id]/MigfullInboundModal';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import { usePermissions } from '@/lib/hooks/usePermissions';
import type { FfRequestRow, Nomenclature, StockTransfer, StockTransferItem, Warehouse } from '@/types/api';
import {
    TRANSFER_STATUS_MAP,
    toMoney,
    transferDriverName,
    transferSkuCount,
    transferUnits,
    transferVehicleAssigned,
    unitCountLabel,
    unitCountText,
    unitWeightLabel,
} from '@/lib/transfer';

export default function TransferDetailPage() {
    const params = useParams();
    const slug = params.slug as string;
    const id = Number(params.id);
    const { canEdit } = usePermissions();

    const [transfer, setTransfer] = useState<StockTransfer | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actionLoading, setActionLoading] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);
    // Заявки ФФ, привязанные к этому переезду (slot stock_transfer_id).
    const [ffLinks, setFfLinks] = useState<{ row: FfRequestRow; warehouseId: number }[]>([]);
    const [ffLoading, setFfLoading] = useState(false);
    // Явный триггер перечитывания связок: эффект ниже намеренно НЕ зависит от
    // объекта переезда, поэтому после создания поставки у Натали (новая PVB со
    // stock_transfer_id) обновить блок можно только так.
    const [ffReloadKey, setFfReloadKey] = useState(0);

    const [showVehicleModal, setShowVehicleModal] = useState(false);
    const [assigning, setAssigning] = useState(false);
    const [assignError, setAssignError] = useState('');

    // Склад migfull-портала: кнопка «Создать поставку у Натали» только когда
    // получатель переезда — именно он. Портал не подключён → кнопки просто нет.
    const [migfullWhId, setMigfullWhId] = useState<number | null>(null);
    const [showNatPush, setShowNatPush] = useState(false);

    // ─── Load ─────────────────────────────────────────────────────────────

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const t = await api.getTransfer(id);
            setTransfer(t);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => { load(); }, [load]);

    useEffect(() => {
        // Справочники — фоном: без них страница живёт (покажет id вместо имени).
        api.getWarehouses().then(setWarehouses).catch(() => {});
        api.getNomenclature().then(setNomenclature).catch(() => {});
        let cancelled = false;
        api.migfullPortalConfig()
            .then(c => { if (!cancelled) setMigfullWhId(c.configured ? c.warehouse_id : null); })
            .catch(() => { /* портал не подключён — кнопки просто нет */ });
        return () => { cancelled = true; };
    }, []);

    // ФФ-связки: в схеме перемещения их нет, поэтому спрашиваем оба склада
    // маршрута и оставляем заявки с нашим stock_transfer_id. Склад без ФФ
    // отдаёт пустой список — ошибки глушим, блок необязательный.
    // Зависимости — только id переезда и его маршрут, а НЕ весь объект: любое
    // действие («Отправить», «Назначить машину») кладёт новый объект в стейт, и
    // с [transfer] связки перезапрашивались бы по обоим складам после каждого
    // клика, хотя маршрут не менялся.
    const transferId = transfer?.id;
    const fromWhId = transfer?.from_warehouse_id;
    const toWhId = transfer?.to_warehouse_id;
    useEffect(() => {
        if (transferId == null || fromWhId == null || toWhId == null) return;
        let cancelled = false;
        const whIds = Array.from(new Set([fromWhId, toWhId]));
        setFfLoading(true);
        Promise.all(whIds.map(whId =>
            api.getFulfillmentRequests(whId)
                .then(rows => rows.map(row => ({ row, warehouseId: whId })))
                .catch(() => [] as { row: FfRequestRow; warehouseId: number }[]),
        ))
            .then(chunks => {
                if (cancelled) return;
                const flat = chunks.flat().filter(x => x.row.stock_transfer_id === transferId);
                // Один и тот же ФФ-документ мог прийти из обоих складов маршрута.
                const seen = new Set<number>();
                setFfLinks(flat.filter(x => (seen.has(x.row.id) ? false : (seen.add(x.row.id), true))));
            })
            .finally(() => { if (!cancelled) setFfLoading(false); });
        return () => { cancelled = true; };
    }, [transferId, fromWhId, toWhId, ffReloadKey]);

    const warehouseById = useMemo(() => {
        const map = new Map<number, Warehouse>();
        warehouses.forEach(w => map.set(w.id, w));
        return map;
    }, [warehouses]);

    const whName = useCallback(
        (whId: number) => warehouseById.get(whId)?.name ?? `Склад ${whId}`,
        [warehouseById],
    );

    const nomByBarcode = useMemo(() => {
        const map = new Map<string, Nomenclature>();
        nomenclature.forEach(n => { if (n.barcode) map.set(n.barcode, n); });
        return map;
    }, [nomenclature]);

    // ─── Actions ──────────────────────────────────────────────────────────

    const runAction = async (fn: () => Promise<StockTransfer>, okMessage: string) => {
        setActionLoading(true);
        try {
            const updated = await fn();
            setTransfer(updated);
            setToast({ message: okMessage, type: 'success' });
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка', type: 'error' });
        } finally {
            setActionLoading(false);
        }
    };

    const handleAssign = async (values: AssignVehicleValues) => {
        setAssigning(true);
        setAssignError('');
        try {
            const updated = await api.assignTransferVehicle(id, values);
            setTransfer(updated);
            setShowVehicleModal(false);
            setToast({ message: 'Машина назначена', type: 'success' });
        } catch (e: unknown) {
            setAssignError(e instanceof Error ? e.message : 'Ошибка назначения машины');
        } finally {
            setAssigning(false);
        }
    };

    // ─── States: loading / error / empty / data ───────────────────────────

    if (loading) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            </div>
        );
    }

    if (error && !transfer) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-danger)' }}>
                    {error}
                    <div style={{ marginTop: 16, display: 'flex', gap: 8, justifyContent: 'center' }}>
                        <button className="btn btn-secondary" onClick={load}>Повторить</button>
                        <Link href={`/p/${slug}/warehouse/assembly?tab=transfers`}>
                            <button className="btn btn-secondary">Назад к списку</button>
                        </Link>
                    </div>
                </div>
            </div>
        );
    }

    if (!transfer) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>🚚</div>
                    <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Перемещение не найдено</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14, marginBottom: 16 }}>
                        Возможно, черновик удалили — вернитесь к списку перемещений
                    </div>
                    <Link href={`/p/${slug}/warehouse/assembly?tab=transfers`}>
                        <button className="btn btn-secondary">К списку</button>
                    </Link>
                </div>
            </div>
        );
    }

    const status = TRANSFER_STATUS_MAP[transfer.status] ?? { label: transfer.status, className: 'badge-secondary' };
    const vehicleAssigned = transferVehicleAssigned(transfer);
    const driver = transferDriverName(transfer);
    const pickupCost = toMoney(transfer.pickup_cost);
    // pallet_weight_kg — вес ОДНОЙ единицы; общий считаем сами, отдельного поля нет.
    const palletWeight = toMoney(transfer.pallet_weight_kg);
    const totalUnitWeight = palletWeight !== null && transfer.pallets_count != null
        ? palletWeight * transfer.pallets_count
        : null;
    const fromWh = warehouseById.get(transfer.from_warehouse_id);
    // Имя склада забора берём из выдачи, справочник — фолбэк (и источник
    // контрагента для тумблера «логистику оказывает склад»).
    const fromWhName = transfer.from_warehouse_name || fromWh?.name || null;
    const canAssignVehicle = canEdit() && transfer.status === 'DRAFT';
    // Поставку у Натали заводим, пока переезд не принят (после COMPLETED товар уже
    // оприходован — бэк вернёт 400). Черновик тоже годится: поставку у ФФ обычно
    // создают заранее, до фактической отправки машины.
    const canPushToNatali = canEdit()
        && migfullWhId != null
        && transfer.to_warehouse_id === migfullWhId
        && transfer.status !== 'COMPLETED'
        && (transfer.items?.length ?? 0) > 0;

    const itemColumns: Column[] = [
        {
            key: 'barcode', label: 'Баркод', width: '160px',
            render: (_v, row: StockTransferItem) => <span style={{ fontFamily: 'monospace' }}>{row.barcode}</span>,
        },
        {
            key: 'article', label: 'Артикул',
            getValue: (row: StockTransferItem) => nomByBarcode.get(row.barcode)?.article_seller ?? '',
            render: (_v, row: StockTransferItem) => {
                const n = nomByBarcode.get(row.barcode);
                return n?.article_seller || n?.subject || n?.name || '—';
            },
            exportValue: (row: StockTransferItem) => nomByBarcode.get(row.barcode)?.article_seller ?? '',
        },
        {
            key: 'quantity', label: 'Количество, шт', align: 'right', width: '150px',
            getValue: (row: StockTransferItem) => row.quantity,
            render: (_v, row: StockTransferItem) => formatNumber(row.quantity, 0),
            exportValue: (row: StockTransferItem) => row.quantity,
        },
    ];

    return (
        <div className="animate-in">
            {toast && (
                <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} duration={toast.type === 'error' ? 5000 : 2500} />
            )}

            {/* ─── Шапка ──────────────────────────────────────────────── */}
            <div className="page-header">
                <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                        <h1 className="page-title" style={{ margin: 0 }}>{transfer.number}</h1>
                        <span className={`badge ${status.className}`}>{status.label}</span>
                        {vehicleAssigned && transfer.status === 'DRAFT' && (
                            <span className="badge badge-info" title="Машина назначена — отдельного статуса у этой ступени нет">
                                🚚 машина назначена
                            </span>
                        )}
                        {transfer.is_defect && (
                            <span className="badge badge-warning" title={transfer.defect_reason || 'Переезд брака'}>Брак</span>
                        )}
                    </div>
                    <p className="page-subtitle">
                        {transfer.from_warehouse_name || whName(transfer.from_warehouse_id)}
                        {' → '}
                        {transfer.to_warehouse_name || whName(transfer.to_warehouse_id)}
                        {' · '}
                        {formatNumber(transferSkuCount(transfer), 0)} SKU / {formatNumber(transferUnits(transfer), 0)} шт
                    </p>
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <Link href={`/p/${slug}/warehouse/assembly?tab=transfers`}>
                        <button className="btn btn-secondary">← К списку</button>
                    </Link>
                    {canEdit() && transfer.status === 'DRAFT' && (
                        <button
                            className="btn btn-primary"
                            onClick={() => runAction(() => api.sendTransfer(transfer.id), 'Перемещение отправлено — товар в пути')}
                            disabled={actionLoading}
                            title="Списать товар со склада-источника и перевести переезд в «В пути»"
                        >
                            Отправить
                        </button>
                    )}
                    {canEdit() && transfer.status === 'IN_TRANSIT' && (
                        <button
                            className="btn btn-success"
                            onClick={() => runAction(() => api.completeTransfer(transfer.id), 'Перемещение принято')}
                            disabled={actionLoading}
                            title="Оприходовать товар на складе-получателе"
                        >
                            Принять
                        </button>
                    )}
                    {canPushToNatali && (
                        <button
                            className="btn btn-secondary"
                            onClick={() => setShowNatPush(true)}
                            title="Создать поставку (приёмку) в WMS Натали из состава этого перемещения"
                        >
                            Создать поставку у Натали
                        </button>
                    )}
                </div>
            </div>

            {/* Ошибка обновления при уже загруженной карточке */}
            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)' }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={() => setError('')}>Закрыть</button>
                </div>
            )}

            {/* ─── Даты и происхождение ───────────────────────────────── */}
            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 16 }}>
                    <InfoField label="Создано" value={formatDateTime(transfer.created_at)} />
                    <InfoField label="Дата забора" value={formatDate(transfer.pickup_date)} />
                    <InfoField label="Дата доставки" value={formatDate(transfer.delivery_date)} />
                    {transfer.vehicle_assigned_at && (
                        <InfoField label="Машина назначена" value={formatDateTime(transfer.vehicle_assigned_at)} />
                    )}
                    {transfer.converted_from_assembly_id != null && (
                        <InfoField
                            label="Из заявки"
                            value={
                                <Link
                                    href={`/p/${slug}/warehouse/assembly/${transfer.converted_from_assembly_id}`}
                                    style={{ color: 'var(--color-accent)' }}
                                    title="Заявка на сборку, которую переделали в этот переезд"
                                >
                                    заявка #{transfer.converted_from_assembly_id} →
                                </Link>
                            }
                        />
                    )}
                    {transfer.comment && <InfoField label="Комментарий" value={transfer.comment} />}
                    {transfer.is_defect && transfer.defect_reason && (
                        <InfoField label="Причина брака" value={transfer.defect_reason} />
                    )}
                </div>
            </div>

            {/* ─── Машина и логистика ─────────────────────────────────── */}
            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                    <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Машина и логистика</h2>
                    <span style={{ flex: 1 }} />
                    {canAssignVehicle && (
                        <button className="btn btn-primary btn-sm" onClick={() => { setAssignError(''); setShowVehicleModal(true); }}>
                            {vehicleAssigned ? 'Изменить машину' : 'Назначить машину'}
                        </button>
                    )}
                    {/* Паритет с секцией Листа логиста: снять машину можно и
                        оттуда, и с карточки — иначе логист, зашедший в переезд,
                        вынужден возвращаться на другой экран ради одной кнопки. */}
                    {canAssignVehicle && vehicleAssigned && (
                        <button
                            className="btn btn-secondary btn-sm"
                            style={{ color: 'var(--color-danger)' }}
                            disabled={actionLoading}
                            title="Транспортную единицу груза не трогает"
                            onClick={() => {
                                if (!confirm(`Снять машину с ${transfer.number}? Транспортная единица груза останется как есть.`)) return;
                                runAction(() => api.unassignTransferVehicle(transfer.id), 'Машина снята');
                            }}
                        >
                            Снять машину
                        </button>
                    )}
                </div>
                {/* Транспортная единица живёт отдельно от машины: паллеты
                    переезжают из заявки при конвертации, ещё до назначения. */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 16, marginBottom: vehicleAssigned ? 16 : 12 }}>
                    <InfoField
                        label={unitCountLabel(transfer.shipped_as_boxes)}
                        value={transfer.pallets_count == null
                            ? '—'
                            : unitCountText(transfer.pallets_count, transfer.shipped_as_boxes)}
                    />
                    <InfoField
                        label={unitWeightLabel(transfer.shipped_as_boxes)}
                        value={palletWeight === null ? '—' : `${formatNumber(palletWeight, 1)} кг`}
                    />
                    {totalUnitWeight !== null && (
                        <InfoField
                            label="Общий вес"
                            value={`${formatNumber(totalUnitWeight, 1)} кг`}
                        />
                    )}
                </div>
                {!vehicleAssigned ? (
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                        Машина не назначена.
                        {transfer.status === 'DRAFT'
                            ? ' Назначьте машину — госномер, водитель, перевозчик, дата и слот забора.'
                            : ' Переезд уже в пути — назначение машины закрыто (правки только в черновике).'}
                    </div>
                ) : (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 16 }}>
                        <InfoField label="Госномер" value={transfer.vehicle_info || '—'} />
                        <InfoField label="Марка" value={transfer.vehicle_brand || '—'} />
                        <InfoField label="Водитель" value={driver || '—'} />
                        <InfoField label="Телефон" value={transfer.driver_phone || '—'} />
                        <InfoField
                            label="Перевозчик"
                            value={
                                transfer.logistics_by_warehouse
                                    ? (
                                        <span title="Перевозчик — контрагент склада забора">
                                            Логистика склада забора{fromWhName ? ` (${fromWhName})` : ''}
                                        </span>
                                    )
                                    // Имя приходит с бэкенда; пока его нет —
                                    // честный id, а не выдуманное название.
                                    : (transfer.counterparty_name
                                        || (transfer.counterparty_id != null
                                            ? `Контрагент #${transfer.counterparty_id}`
                                            : '—'))
                            }
                        />
                        <InfoField label="Слот забора" value={transfer.pickup_time_slot || '—'} />
                        <InfoField
                            label="Стоимость забора"
                            value={pickupCost === null ? '—' : `${formatNumber(pickupCost, 0)} ₽`}
                        />
                    </div>
                )}
            </div>

            {/* ─── Оплата ─────────────────────────────────────────────── */}
            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <h2 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 16px' }}>Оплата</h2>
                {/* Схема перемещения отдаёт только стоимость забора: статуса
                    заявки на оплату и номера расхода в ней НЕТ. Обещать «появится
                    после отправки» нельзя — забор давно создан, просто карточка
                    его не видит. Поэтому говорим ровно то, что знаем, и уводим
                    туда, где оплата действительно ведётся. */}
                {pickupCost !== null ? (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 16 }}>
                        <InfoField label="Стоимость забора" value={`${formatNumber(pickupCost, 0)} ₽`} />
                        <InfoField
                            label="Оплата забора"
                            value={
                                <Link
                                    href={`/p/${slug}/warehouse/logistics?tab=payments`}
                                    style={{ color: 'var(--color-accent)' }}
                                    title="Заявки на оплату заборов ведутся на «Листе логиста»"
                                >
                                    Лист логиста → Оплаты
                                </Link>
                            }
                        />
                    </div>
                ) : (
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                        Стоимость забора не задана — она указывается при назначении машины.
                    </div>
                )}
            </div>

            {/* ─── Фулфилмент ─────────────────────────────────────────── */}
            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <h2 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 16px' }}>Фулфилмент</h2>
                {ffLoading ? (
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>Загрузка связок…</div>
                ) : ffLinks.length === 0 ? (
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                        Связанных заявок ФФ нет. Зеркала ФФ переносятся на перемещение только если при
                        переделке заявки включить «перенести связку ФФ» — по умолчанию они остаются историей заявки.
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
                        {ffLinks.map(({ row, warehouseId }) => (
                            <Link
                                key={row.id}
                                href={`/p/${slug}/warehouse/${warehouseId}/ff-request/${row.id}`}
                                style={{ color: 'var(--color-accent)' }}
                                title={`${whName(warehouseId)}${row.stage_title ? ` · ${row.stage_title}` : ''}`}
                            >
                                {row.number || `Заявка #${row.id}`}
                                {row.stage_title ? ` (${row.stage_title})` : ''} →
                            </Link>
                        ))}
                    </div>
                )}
            </div>

            {/* ─── Состав ─────────────────────────────────────────────── */}
            <div style={{ marginBottom: 16 }}>
                {(transfer.items?.length ?? 0) === 0 ? (
                    <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                        <div style={{ fontSize: 40, marginBottom: 12 }}>📦</div>
                        <div style={{ fontSize: 15, fontWeight: 500, marginBottom: 6 }}>Состав пуст</div>
                        <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                            В перемещении нет позиций — отправлять нечего
                        </div>
                    </div>
                ) : (
                    <TanStackDataTable
                        columns={itemColumns}
                        data={transfer.items ?? []}
                        pageSize={50}
                        exportName={`transfer_${transfer.number}`}
                    />
                )}
            </div>

            {showVehicleModal && (
                <AssignVehicleModal
                    title={`Назначить машину · ${transfer.number}`}
                    initial={{
                        vehicle_info: transfer.vehicle_info,
                        vehicle_brand: transfer.vehicle_brand,
                        driver_first_name: transfer.driver_first_name,
                        driver_last_name: transfer.driver_last_name,
                        driver_phone: transfer.driver_phone,
                        logistics_by_warehouse: transfer.logistics_by_warehouse,
                        pickup_date: transfer.pickup_date,
                        pickup_time_slot: transfer.pickup_time_slot,
                        pickup_cost: pickupCost,
                        delivery_date: transfer.delivery_date,
                        pallets_count: transfer.pallets_count,
                        pallet_weight_kg: palletWeight,
                        shipped_as_boxes: transfer.shipped_as_boxes,
                    }}
                    pickupWarehouseName={fromWhName}
                    pickupWarehouseCounterpartyId={fromWh?.counterparty_id ?? null}
                    deliveryDateLabel="Дата доставки"
                    submitting={assigning}
                    error={assignError}
                    onSubmit={handleAssign}
                    onClose={() => { setShowVehicleModal(false); setAssignError(''); }}
                />
            )}

            {showNatPush && (
                <MigfullInboundModal
                    source={{ kind: 'transfer', id: transfer.id }}
                    sourceLabel={`Перемещение ${transfer.number}`}
                    onClose={() => setShowNatPush(false)}
                    onSuccess={res => {
                        setShowNatPush(false);
                        setToast({
                            message: `Поставка у Натали создана: ${res.shipment_number || res.shipment_guid || '—'}`,
                            type: 'success',
                        });
                        // PVB уже связана с переездом (stock_transfer_id) — перечитываем
                        // блок ФФ-связок, чтобы она появилась без перезагрузки страницы.
                        setFfReloadKey(k => k + 1);
                    }}
                />
            )}
        </div>
    );
}

function InfoField({ label, value }: { label: string; value: React.ReactNode }) {
    return (
        <div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 14, fontWeight: 500 }}>{value}</div>
        </div>
    );
}
