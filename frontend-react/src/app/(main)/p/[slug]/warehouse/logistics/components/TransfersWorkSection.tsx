'use client';
/**
 * Секция «Перемещения» в РАБОЧЕМ списке Листа логиста.
 *
 * Не путать с вкладкой «Переезды» (TransferLogisticsTab): та про деньги и
 * аналитику, эта — про работу. Логист назначает машину не из карточки
 * документа, а отсюда: выделил три переезда на «транзит Питер» — посадил их
 * одной газелью.
 *
 * Показываем только `DRAFT`: отгруженный переезд (`IN_TRANSIT`) машину уже не
 * меняет. Группировка — по складу-ЗАБОРА: машина приезжает за грузом именно
 * туда, и «что ждёт машину на Натали» — вопрос одного взгляда.
 *
 * Bulk-контракт отличается от заявочного: у заявок дата/стоимость забора
 * задаются пер-строчно, у переездов реквизиты ОБЩИЕ на все выбранные
 * (`{ids, payload}`), поэтому модалка одна на всю пачку.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatNumber, pluralRu } from '@/lib/utils';
import { AssignVehicleModal, Toast } from '@/components';
import type { AssignVehicleValues } from '@/components';
import { usePermissions } from '@/lib/hooks/usePermissions';
import type { StockTransfer, Warehouse } from '@/types/api';
import {
    toMoney,
    transferDriverName,
    transferSkuCount,
    transferUnits,
    transferVehicleAssigned,
    unitCountText,
} from '@/lib/transfer';

/** Префилл модалки: машина у выбранных переездов обычно одна — берём первую заполненную. */
function vehiclePrefill(rows: StockTransfer[]): StockTransfer | null {
    return rows.find(r => transferVehicleAssigned(r)) ?? null;
}

/** Справочник складов — пропом от страницы: см. TransferLogisticsTab, качать его
 *  дважды на одном экране незачем (нужен здесь только ради контрагента склада). */
export default function TransfersWorkSection({ warehouses }: { warehouses: Warehouse[] }) {
    const params = useParams();
    const slug = params.slug as string;
    const { canEdit } = usePermissions();

    const [rows, setRows] = useState<StockTransfer[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actionLoading, setActionLoading] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    const [checked, setChecked] = useState<Set<number>>(new Set());
    // Кому назначаем: пачка id (пер-строчная кнопка — пачка из одного).
    const [assignIds, setAssignIds] = useState<number[] | null>(null);
    const [assigning, setAssigning] = useState(false);
    const [assignError, setAssignError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            // Только черновики: посадить на машину можно лишь их.
            const data = await api.getTransfers(false, undefined, { status: 'DRAFT' });
            setRows(data);
            // Выбор чистим: строка могла уехать из среза (отправлена другим окном).
            setChecked(prev => new Set([...prev].filter(id => data.some(r => r.id === id))));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки перемещений');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const warehouseById = useMemo(() => {
        const map = new Map<number, Warehouse>();
        warehouses.forEach(w => map.set(w.id, w));
        return map;
    }, [warehouses]);

    const fromName = useCallback(
        (t: StockTransfer) => t.from_warehouse_name || warehouseById.get(t.from_warehouse_id)?.name || `Склад ${t.from_warehouse_id}`,
        [warehouseById],
    );
    const toName = useCallback(
        (t: StockTransfer) => t.to_warehouse_name || warehouseById.get(t.to_warehouse_id)?.name || `Склад ${t.to_warehouse_id}`,
        [warehouseById],
    );

    /** Группы по складу забора, внутри — свежие сверху (id desc, как отдаёт бэк). */
    const groups = useMemo(() => {
        const byWh = new Map<number, StockTransfer[]>();
        rows.forEach(t => {
            const list = byWh.get(t.from_warehouse_id);
            if (list) list.push(t);
            else byWh.set(t.from_warehouse_id, [t]);
        });
        return [...byWh.entries()]
            .map(([whId, list]) => ({ whId, name: fromName(list[0]), list }))
            .sort((a, b) => a.name.localeCompare(b.name, 'ru'));
    }, [rows, fromName]);

    const toggle = (id: number) => {
        setChecked(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    const toggleGroup = (list: StockTransfer[]) => {
        const ids = list.map(t => t.id);
        const allChecked = ids.every(id => checked.has(id));
        setChecked(prev => {
            const next = new Set(prev);
            ids.forEach(id => (allChecked ? next.delete(id) : next.add(id)));
            return next;
        });
    };

    const assignRows = assignIds ? rows.filter(t => assignIds.includes(t.id)) : [];
    const prefillRow = vehiclePrefill(assignRows);
    const pickupWh = assignRows.length ? warehouseById.get(assignRows[0].from_warehouse_id) : undefined;
    // Все выбранные должны ехать с ОДНОГО склада: перевозчик и слот забора у
    // пачки общие, а машина не заезжает на два склада за один слот.
    const mixedPickup = assignRows.length > 1
        && new Set(assignRows.map(t => t.from_warehouse_id)).size > 1;

    const handleAssign = async (values: AssignVehicleValues) => {
        if (!assignIds?.length) return;
        setAssigning(true);
        setAssignError('');
        try {
            if (assignIds.length === 1) {
                await api.assignTransferVehicle(assignIds[0], values);
            } else {
                await api.assignTransferVehicleBulk(assignIds, values);
            }
            setAssignIds(null);
            setChecked(new Set());
            setToast({
                message: assignIds.length === 1
                    ? 'Машина назначена'
                    : `Машина назначена на ${formatNumber(assignIds.length, 0)} ${pluralRu(assignIds.length, ['переезд', 'переезда', 'переездов'])}`,
                type: 'success',
            });
            await load();
        } catch (e: unknown) {
            // Bulk на бэке атомарный: первый отказ роняет весь вызов — показываем
            // текст как есть, выбор не сбрасываем, чтобы можно было поправить.
            setAssignError(e instanceof Error ? e.message : 'Ошибка назначения машины');
        } finally {
            setAssigning(false);
        }
    };

    const handleUnassign = async (t: StockTransfer) => {
        if (!confirm(`Снять машину с ${t.number}? Транспортная единица груза останется как есть.`)) return;
        setActionLoading(true);
        try {
            const updated = await api.unassignTransferVehicle(t.id);
            setRows(prev => prev.map(r => (r.id === updated.id ? updated : r)));
            setToast({ message: `Машина снята с ${updated.number}`, type: 'success' });
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка снятия машины', type: 'error' });
        } finally {
            setActionLoading(false);
        }
    };

    const waitingCount = rows.filter(t => !transferVehicleAssigned(t)).length;

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
            {toast && (
                <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} duration={toast.type === 'error' ? 5000 : 2500} />
            )}

            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
                <span style={{ fontSize: 15 }}>📦</span>
                <span style={{ fontSize: 14, fontWeight: 600 }}>Перемещения</span>
                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                    переезды между нашими складами (черновики)
                    {waitingCount > 0 && ` · без машины: ${formatNumber(waitingCount, 0)}`}
                </span>
                <span style={{ flex: 1 }} />
                {checked.size > 0 && canEdit() && (
                    <>
                        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                            Выбрано: {formatNumber(checked.size, 0)}
                        </span>
                        <button
                            className="btn btn-primary btn-sm"
                            onClick={() => { setAssignError(''); setAssignIds([...checked]); }}
                            disabled={actionLoading}
                            title="Одна машина на все выбранные переезды"
                        >
                            Назначить машину ({formatNumber(checked.size, 0)})
                        </button>
                        <button className="btn btn-secondary btn-sm" onClick={() => setChecked(new Set())}>Снять выбор</button>
                    </>
                )}
                <button className="btn btn-secondary btn-sm" onClick={load} disabled={loading} title="Обновить список переездов">
                    {loading ? '…' : '↻'}
                </button>
            </div>

            {error ? (
                <div style={{ padding: 12, color: 'var(--color-danger)', fontSize: 13 }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={load}>Повторить</button>
                </div>
            ) : loading ? (
                <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>Загрузка…</div>
            ) : groups.length === 0 ? (
                <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                    Переездов-черновиков нет — все перемещения уже отправлены или их ещё не создавали
                </div>
            ) : (
                groups.map(g => {
                    const waiting = g.list.filter(t => !transferVehicleAssigned(t));
                    const allChecked = g.list.every(t => checked.has(t.id));
                    return (
                        <div key={g.whId} style={{ marginBottom: 16 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
                                {canEdit() && (
                                    <input
                                        type="checkbox"
                                        checked={allChecked}
                                        onChange={() => toggleGroup(g.list)}
                                        style={{ cursor: 'pointer' }}
                                        title="Выбрать все переезды склада"
                                    />
                                )}
                                <span style={{ fontSize: 13, fontWeight: 600 }}>С {g.name}</span>
                                {waiting.length > 0 ? (
                                    <span style={{ fontSize: 12, color: 'var(--color-warning)' }}>
                                        ждут машину: {waiting.map(t => t.number).join(', ')}
                                    </span>
                                ) : (
                                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>все с машиной</span>
                                )}
                            </div>
                            <div style={{ overflowX: 'auto' }}>
                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                                    <tbody>
                                        {g.list.map(t => {
                                            const assigned = transferVehicleAssigned(t);
                                            const cost = toMoney(t.pickup_cost);
                                            return (
                                                <tr key={t.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                                    <td style={{ padding: '8px 6px', width: 28 }}>
                                                        {canEdit() && (
                                                            <input
                                                                type="checkbox"
                                                                checked={checked.has(t.id)}
                                                                onChange={() => toggle(t.id)}
                                                                style={{ cursor: 'pointer' }}
                                                            />
                                                        )}
                                                    </td>
                                                    <td style={{ padding: '8px 6px', whiteSpace: 'nowrap' }}>
                                                        <Link
                                                            href={`/p/${slug}/warehouse/transfers/${t.id}`}
                                                            style={{ color: 'var(--color-accent)', fontWeight: 600 }}
                                                            title="Открыть карточку перемещения"
                                                        >
                                                            {t.number}
                                                        </Link>
                                                    </td>
                                                    <td style={{ padding: '8px 6px', color: 'var(--color-text-muted)' }}>
                                                        {fromName(t)} → {toName(t)}
                                                    </td>
                                                    <td style={{ padding: '8px 6px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                        {t.pallets_count == null ? '—' : unitCountText(t.pallets_count, t.shipped_as_boxes)}
                                                    </td>
                                                    <td style={{ padding: '8px 6px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                        {formatNumber(transferUnits(t), 0)} шт
                                                        <span style={{ color: 'var(--color-text-muted)' }}> / {formatNumber(transferSkuCount(t), 0)} SKU</span>
                                                    </td>
                                                    <td style={{ padding: '8px 6px', whiteSpace: 'nowrap' }}>
                                                        {assigned ? (
                                                            <span
                                                                className="badge badge-info"
                                                                title={[t.vehicle_brand, transferDriverName(t), t.driver_phone, t.pickup_date ? `забор ${formatDate(t.pickup_date)}` : null]
                                                                    .filter(Boolean).join(' · ') || undefined}
                                                            >
                                                                🚚 {t.vehicle_info || 'назначена'}
                                                            </span>
                                                        ) : (
                                                            <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                                                        )}
                                                    </td>
                                                    <td style={{ padding: '8px 6px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                        {cost === null ? '—' : `${formatNumber(cost, 0)} ₽`}
                                                    </td>
                                                    <td style={{ padding: '8px 6px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                        {canEdit() && (
                                                            <div style={{ display: 'inline-flex', gap: 6 }}>
                                                                <button
                                                                    className="btn btn-secondary btn-sm"
                                                                    onClick={() => { setAssignError(''); setAssignIds([t.id]); }}
                                                                    disabled={actionLoading}
                                                                >
                                                                    {assigned ? 'Изменить машину' : 'Назначить машину'}
                                                                </button>
                                                                {assigned && (
                                                                    <button
                                                                        className="btn btn-secondary btn-sm"
                                                                        onClick={() => handleUnassign(t)}
                                                                        disabled={actionLoading}
                                                                        style={{ color: 'var(--color-danger)' }}
                                                                    >
                                                                        Снять машину
                                                                    </button>
                                                                )}
                                                            </div>
                                                        )}
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    );
                })
            )}

            {assignIds && assignIds.length > 0 && (
                <AssignVehicleModal
                    title={assignIds.length === 1
                        ? `Назначить машину · ${assignRows[0]?.number ?? ''}`
                        : `Назначить машину на ${formatNumber(assignIds.length, 0)} ${pluralRu(assignIds.length, ['переезд', 'переезда', 'переездов'])}`}
                    submitLabel={assignIds.length === 1 ? 'Назначить' : `Назначить (${formatNumber(assignIds.length, 0)})`}
                    initial={{
                        vehicle_info: prefillRow?.vehicle_info,
                        vehicle_brand: prefillRow?.vehicle_brand,
                        driver_first_name: prefillRow?.driver_first_name,
                        driver_last_name: prefillRow?.driver_last_name,
                        driver_phone: prefillRow?.driver_phone,
                        logistics_by_warehouse: prefillRow?.logistics_by_warehouse,
                        pickup_date: prefillRow?.pickup_date,
                        pickup_time_slot: prefillRow?.pickup_time_slot,
                        pickup_cost: toMoney(prefillRow?.pickup_cost ?? null),
                        delivery_date: prefillRow?.delivery_date,
                        // Единицу в bulk не префиллим: у выбранных переездов она
                        // разная, и подстановка чужого числа тихо перезаписала бы
                        // остальные. Для одного — показываем его же значения.
                        pallets_count: assignIds.length === 1 ? assignRows[0]?.pallets_count : null,
                        pallet_weight_kg: assignIds.length === 1 ? toMoney(assignRows[0]?.pallet_weight_kg ?? null) : null,
                        shipped_as_boxes: assignIds.length === 1 ? assignRows[0]?.shipped_as_boxes : null,
                    }}
                    pickupWarehouseName={mixedPickup ? null : (pickupWh?.name ?? null)}
                    // Разные склады забора → перевозчика «от склада» взять неоткуда.
                    pickupWarehouseCounterpartyId={mixedPickup ? null : (pickupWh?.counterparty_id ?? null)}
                    deliveryDateLabel="Дата доставки"
                    submitting={assigning}
                    // Предупреждение, а не запрет: бэкенд такую пачку примет, но
                    // реквизиты у неё общие — логист должен видеть, что делает.
                    error={assignError || (mixedPickup
                        ? '⚠ Выбраны переезды с РАЗНЫХ складов забора. Реквизиты (перевозчик, дата и слот) уйдут общие на все — убедитесь, что это осознанно.'
                        : '')}
                    onSubmit={handleAssign}
                    onClose={() => { setAssignIds(null); setAssignError(''); }}
                />
            )}
        </div>
    );
}
