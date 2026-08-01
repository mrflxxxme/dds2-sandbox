'use client';
/**
 * Деталка перемещения — переезд между нашими складами как полноценная поездка.
 *
 * Что важно помнить про контракт:
 *  • Статус — ЗЕРКАЛО заявки на сборку: PENDING → IN_PROGRESS → READY →
 *    VEHICLE_ASSIGNED → SHIPPED → DELIVERED, плюс RETURNED → CLOSED и CANCELLED.
 *    Сток двигают ровно два перехода: «Отправить» списывает с источника,
 *    «Принять» приходует на получателе («Вернуть на склад» — обратно источнику).
 *  • Кнопка есть ТОЛЬКО там, где переход разрешён: мёртвая кнопка, отвечающая
 *    400, хуже отсутствующей. Гейты — в lib/transfer.ts (canSendTransfer и др.),
 *    одни на все экраны переездов.
 *  • Править переезд можно до отгрузки (PENDING / IN_PROGRESS / READY): состав
 *    живой, пока сток не списан. Правка живёт на ОТДЕЛЬНОЙ странице
 *    (`transfers/[id]/edit`) — ровно как у заявки на сборку; модалки правки
 *    больше нет, второго пути редактирования быть не должно. Машину назначают
 *    из READY, снятие вернёт туда же.
 *  • Имена концов маршрута приходят в самой схеме; справочник складов грузим
 *    ради контрагента склада забора (и как фолбэк имени). ФФ-связки приходят
 *    в самой схеме (`ff_links`) — раньше карточка ради них тянула ВСЕ заявки
 *    обоих складов. Все эти блоки обязаны переживать отсутствие данных.
 *  • Оплата забора (OUT-xx) в схему перемещения ещё не приехала — блок
 *    показывает то, что есть (стоимость забора), и честно говорит про остальное.
 *  • «Создать поставку у Натали» — тот же контур, что на карточке машины, но
 *    состав берётся из строк ЭТОГО переезда: своей приёмки перемещение не
 *    создаёт, приход у ФФ заводит именно созданная поставка (PVB-…). Кнопка
 *    живёт только когда получатель — склад migfull-портала и переезд ещё не
 *    принят (до DELIVERED, см. canPushTransferToFf); бэкенд повторно проверяет
 *    и то, и другое. Созданная PVB связана с переездом и потому появляется в
 *    блоке «Фулфилмент» — это ДОПОЛНЕНИЕ к связке уже существующих заявок, а
 *    не её дубль: там связывают документ, который у ФФ уже есть, здесь —
 *    заводят новый.
 */
import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatDateTime, formatNumber } from '@/lib/utils';
import { Toast, TransferFfLinkModal } from '@/components';
import MigfullInboundModal from '../../[id]/MigfullInboundModal';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import { usePermissions } from '@/lib/hooks/usePermissions';
import type {
    Nomenclature,
    StockTransfer,
    StockTransferItem,
    TransferFfLink,
    TransferFfSide,
    Warehouse,
} from '@/types/api';
import {
    TRANSFER_STATUS_MAP,
    canAssignTransferVehicle,
    canCloseTransfer,
    canCompleteTransfer,
    canEditTransfer,
    canMarkTransferReady,
    canPushTransferToFf,
    canReturnTransfer,
    canSendTransfer,
    ffLinkLabel,
    ffLinkStage,
    splitTransferFfLinks,
    toMoney,
    transferDriverName,
    transferReceiveProgress,
    transferSkuCount,
    transferUnits,
    transferVehicleAssigned,
    unitCountLabel,
    unitCountText,
    unitWeightLabel,
} from '@/lib/transfer';

export default function TransferDetailPage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
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

    /** Сторона, для которой открыт пикер заявок ФФ (null — закрыт). */
    const [ffLinkSide, setFfLinkSide] = useState<TransferFfSide | null>(null);
    /** id заявки ФФ, которую сейчас отвязываем — блокирует только её кнопку. */
    const [unlinkingId, setUnlinkingId] = useState<number | null>(null);

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

    // Возврат со страницы правки (`?saved=1`): тост показывает карточка, потому
    // что правка живёт на отдельном экране и после сохранения уходит сюда.
    // Параметр сразу снимаем с URL — иначе тост возвращался бы на каждый F5.
    useEffect(() => {
        if (searchParams.get('saved') !== '1') return;
        setToast({ message: 'Перемещение обновлено', type: 'success' });
        router.replace(`/p/${slug}/warehouse/transfers/${id}`, { scroll: false });
    }, [searchParams, router, slug, id]);

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

    // ФФ-связки приезжают в самой схеме переезда (`ff_links`) — отдельных
    // запросов больше нет: карточка тянула ВСЕ заявки обоих складов маршрута
    // ради нескольких строк. Отдельный триггер перечитывания связок тоже не
    // нужен: после создания поставки у Натали хватает reloadTransfer() — новая
    // PVB приезжает в `ff_links` вместе с самим переездом.
    const ffLinks = useMemo(() => splitTransferFfLinks(transfer?.ff_links), [transfer?.ff_links]);

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

    /** Перечитать карточку после связки/отвязки ФФ — ff_links живут в схеме. */
    const reloadTransfer = useCallback(async () => {
        try {
            setTransfer(await api.getTransfer(id));
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Не удалось обновить связки', type: 'error' });
        }
    }, [id]);

    const handleUnlinkFf = async (link: TransferFfLink) => {
        if (!confirm(`Отвязать заявку ФФ ${ffLinkLabel(link)} от перемещения? Сама заявка у ФФ останется — исчезнет только связь.`)) return;
        setUnlinkingId(link.id);
        try {
            await api.unlinkFulfillmentRequest(link.warehouse_id, link.id);
            await reloadTransfer();
            setToast({ message: 'Заявка ФФ отвязана', type: 'success' });
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка отвязки', type: 'error' });
        } finally {
            setUnlinkingId(null);
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
    const toWhName = transfer.to_warehouse_name || warehouseById.get(transfer.to_warehouse_id)?.name || null;
    // Гейты действий — из общего словаря (lib/transfer.ts), а не сравнением
    // статуса строкой здесь: иначе экраны переездов разъедутся между собой.
    // Права редактора — отдельный множитель: у viewer'а нет ни одной кнопки.
    const editor = canEdit();
    const canDraftEdit = editor && canEditTransfer(transfer.status);
    const receiveProgress = transferReceiveProgress(transfer);
    // Поставку у Натали заводим, пока переезд не принят (после DELIVERED товар
    // уже оприходован — бэк вернёт блокировку). Ранние ступени тоже годятся:
    // поставку у ФФ обычно создают заранее, до фактической отправки машины.
    const canPushToNatali = editor
        && migfullWhId != null
        && transfer.to_warehouse_id === migfullWhId
        && canPushTransferToFf(transfer.status)
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
                        {/* Отдельного бейджа «машина назначена» больше нет — это
                            самостоятельная ступень статуса. Но у уехавшего
                            переезда статус её уже не покажет, а госномер логисту
                            нужен и там: напоминаем машиной, а не статусом. */}
                        {vehicleAssigned && transfer.status !== 'VEHICLE_ASSIGNED' && transfer.vehicle_info && (
                            <span className="badge badge-info" title="Машина этого переезда">
                                🚚 {transfer.vehicle_info}
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
                    {/* Прогресс приёмки — главный вопрос по уехавшему переезду:
                        «доехало ли всё». Есть только у SHIPPED (см. хелпер). */}
                    {receiveProgress && (
                        <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <span
                                className={`badge ${receiveProgress.received >= receiveProgress.total ? 'badge-success' : 'badge-warning'}`}
                                title="Сколько единиц склад-получатель уже зачислил себе"
                            >
                                принято {formatNumber(receiveProgress.received, 0)} из {formatNumber(receiveProgress.total, 0)}
                            </span>
                            {receiveProgress.received > 0 && receiveProgress.received < receiveProgress.total && (
                                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                    остаток в пути: {formatNumber(receiveProgress.total - receiveProgress.received, 0)} шт
                                </span>
                            )}
                        </div>
                    )}
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <Link href={`/p/${slug}/warehouse/assembly?tab=transfers`}>
                        <button className="btn btn-secondary">← К списку</button>
                    </Link>
                    {canDraftEdit && (
                        <Link href={`/p/${slug}/warehouse/transfers/${id}/edit`}>
                            <button
                                className="btn btn-secondary"
                                disabled={actionLoading}
                                title="Маршрут, комментарий, брак, транспортная единица и состав — пока переезд не уехал"
                            >
                                Редактировать
                            </button>
                        </Link>
                    )}
                    {editor && canMarkTransferReady(transfer.status) && (
                        <button
                            className="btn btn-primary"
                            onClick={() => runAction(
                                () => api.markTransferReady(transfer.id),
                                transfer.status === 'RETURNED'
                                    ? 'Переезд снова готов к отправке'
                                    : 'Переезд отмечен собранным',
                            )}
                            disabled={actionLoading}
                            title={transfer.status === 'RETURNED'
                                ? 'Переотправить вернувшийся переезд: вернёт его в «Готово», откуда назначается машина. Сток не двигает — он уже возвращён на склад-источник'
                                : 'Отметить, что переезд собран и готов к назначению машины. Сток не двигает'}
                        >
                            {transfer.status === 'RETURNED' ? 'Переотправить' : 'Готов'}
                        </button>
                    )}
                    {editor && canSendTransfer(transfer.status) && (
                        <button
                            className="btn btn-primary"
                            onClick={() => runAction(() => api.sendTransfer(transfer.id), 'Перемещение отправлено — товар в пути')}
                            disabled={actionLoading}
                            title="Списать товар со склада-источника и повесить транзитом на получателя"
                        >
                            Отправить
                        </button>
                    )}
                    {editor && canCompleteTransfer(transfer.status) && (
                        <button
                            className="btn btn-success"
                            onClick={() => runAction(() => api.completeTransfer(transfer.id), 'Перемещение принято')}
                            disabled={actionLoading}
                            title="Оприходовать товар на складе-получателе"
                        >
                            Принять
                        </button>
                    )}
                    {editor && canReturnTransfer(transfer.status) && (
                        <button
                            className="btn btn-secondary"
                            style={{ color: 'var(--color-danger)' }}
                            disabled={actionLoading}
                            title="Получатель не принял: товар вернётся на склад-источник"
                            onClick={() => {
                                // Возврат ДВИГАЕТ сток обратно на источник —
                                // спрашиваем прямо, а не «вы уверены?».
                                if (!confirm(
                                    `Вернуть ${transfer.number} на склад-источник (${fromWhName || `склад ${transfer.from_warehouse_id}`})?\n\n`
                                    + 'Товар вернётся на склад-источник: транзит на получателе снимется, '
                                    + 'а если переезд уже был принят — единицы спишутся с получателя обратно.'
                                )) return;
                                runAction(() => api.returnTransfer(transfer.id), 'Переезд вернулся на склад-источник');
                            }}
                        >
                            Вернуть на склад
                        </button>
                    )}
                    {editor && canCloseTransfer(transfer.status) && (
                        <button
                            className="btn btn-secondary"
                            onClick={() => runAction(() => api.closeTransfer(transfer.id), 'Переезд закрыт')}
                            disabled={actionLoading}
                            title="Закрыть переезд: дальше по нему действий не будет. Сток не двигает"
                        >
                            Закрыть
                        </button>
                    )}
                    {/* Не переход статуса, а действие у ФФ — поэтому стоит ПОСЛЕ
                        цепочки переходов, отдельным хвостом. Результат кнопки
                        (новая PVB) приезжает в блок «Фулфилмент» ниже, в
                        подсекцию приёмки у склада-получателя. */}
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
                    {/* Вехи цепочки. Их НЕ вывести из статуса — он хранит только
                        текущее состояние, а «когда собрали» нужно и после отгрузки. */}
                    {transfer.actual_ready_date && (
                        <InfoField label="Собран" value={formatDate(transfer.actual_ready_date)} />
                    )}
                    {transfer.vehicle_assigned_at && (
                        <InfoField label="Машина назначена" value={formatDateTime(transfer.vehicle_assigned_at)} />
                    )}
                    {transfer.shipped_at && (
                        <InfoField label="Отгружен" value={formatDateTime(transfer.shipped_at)} />
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
                    {/* Машиной карточка НЕ управляет: ни назначения, ни снятия,
                        ни замены реквизитов. Единственная точка — Лист логиста
                        (канон юзера 01.08.2026): он ведёт машины пачкой, видит
                        заявки и переезды в одном списке и назначает одну машину
                        на несколько документов. Кнопка здесь плодила бы второй
                        путь и растаскивала процесс по двум экранам.
                        Ниже — только ЧТЕНИЕ: что за груз и, если машина уже
                        назначена, чья она. Прятать и это значило бы, что карточка
                        врёт о состоянии переезда. */}
                    <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Машина и логистика</h2>
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
                        {canAssignTransferVehicle(transfer.status)
                            ? ' Машину назначает логист — в Листе логиста, там переезды идут одним списком с заявками.'
                            : canEditTransfer(transfer.status)
                                ? ' Машину назначают после того, как переезд собран, — нажмите «Готов».'
                                : ' Переезд уже уехал — назначение машины закрыто.'}
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
            {/* Две стороны маршрута — РАЗНЫЕ склады и разные документы ФФ:
                у источника сборка (отгрузка), у получателя приёмка. На одну
                сторону может приходиться несколько заявок (у Натали короба и
                штучные — отдельные документы), поэтому всюду списки. */}
            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <h2 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 16px' }}>Фулфилмент</h2>
                <FfSideSection
                    title="Отгрузка у склада-источника"
                    warehouseName={fromWhName}
                    links={ffLinks.source}
                    slug={slug}
                    // У переезда, сделанного из заявки, пустота объясняется не
                    // «ещё не связали», а дефолтом конвертации: зеркала ФФ
                    // остаются историей заявки, если галку не включили.
                    emptyText={transfer.converted_from_assembly_id != null
                        ? 'Заявок ФФ на отгрузку не связано: зеркала ФФ переносятся на переезд только если при переделке заявки включить «перенести связку ФФ» — по умолчанию они остаются историей заявки. Нажмите «Связать» — покажем свободные сборки этого склада.'
                        : 'Заявок ФФ на отгрузку не связано. Нажмите «Связать» — покажем свободные сборки этого склада.'}
                    canEdit={canEdit()}
                    unlinkingId={unlinkingId}
                    onLink={() => setFfLinkSide('source')}
                    onUnlink={handleUnlinkFf}
                />
                <div style={{ height: 1, background: 'var(--color-border)', margin: '16px 0' }} />
                <FfSideSection
                    title="Приёмка у склада-получателя"
                    warehouseName={toWhName}
                    links={ffLinks.dest}
                    slug={slug}
                    emptyText="Заявок ФФ на приёмку не связано. У транзитных складов-получателей ФФ-интеграции обычно нет — тогда и связывать нечего."
                    canEdit={canEdit()}
                    unlinkingId={unlinkingId}
                    onLink={() => setFfLinkSide('dest')}
                    onUnlink={handleUnlinkFf}
                />
            </div>

            {/* ─── Состав ─────────────────────────────────────────────── */}
            <div style={{ marginBottom: 16 }}>
                {/* Почему кнопки «Редактировать» нет. Молчаливое исчезновение
                    кнопки читается как баг прав доступа, а не как «поезд ушёл». */}
                {editor && !canEditTransfer(transfer.status) && (
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 13, marginBottom: 8 }}>
                        {transfer.status === 'CANCELLED'
                            ? 'Переезд отменён — правка закрыта.'
                            : 'После отправки сток уже списан со склада-источника — правка закрыта.'}
                    </div>
                )}
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


            {ffLinkSide && (
                <TransferFfLinkModal
                    transferId={transfer.id}
                    transferNumber={transfer.number}
                    side={ffLinkSide}
                    warehouseId={ffLinkSide === 'source' ? transfer.from_warehouse_id : transfer.to_warehouse_id}
                    warehouseName={ffLinkSide === 'source' ? fromWhName : toWhName}
                    onClose={() => setFfLinkSide(null)}
                    onLinked={reloadTransfer}
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
                        // PVB уже связана с переездом (stock_transfer_id), а связки
                        // живут в самой схеме переезда — перечитываем карточку,
                        // чтобы поставка появилась в блоке «Фулфилмент» без F5.
                        reloadTransfer();
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

/**
 * Одна сторона блока «Фулфилмент»: заголовок со складом, список связанных
 * заявок ФФ и кнопка «Связать». Пустое состояние у каждой стороны СВОЁ — на
 * стороне получателя отсутствие заявок это норма (у транзитных складов
 * ФФ-интеграции нет), и общий текст на весь блок читался бы как ошибка.
 */
function FfSideSection({
    title,
    warehouseName,
    links,
    slug,
    emptyText,
    canEdit,
    unlinkingId,
    onLink,
    onUnlink,
}: {
    title: string;
    warehouseName: string | null;
    links: TransferFfLink[];
    slug: string;
    emptyText: string;
    canEdit: boolean;
    unlinkingId: number | null;
    onLink: () => void;
    onUnlink: (link: TransferFfLink) => void;
}) {
    return (
        <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8, flexWrap: 'wrap' }}>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{title}</div>
                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                    {warehouseName || '—'}
                </span>
                <span style={{ flex: 1 }} />
                {canEdit && (
                    <button className="btn btn-secondary btn-sm" onClick={onLink}>Связать</button>
                )}
            </div>
            {links.length === 0 ? (
                <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>{emptyText}</div>
            ) : (
                <div className="ff-link-list" style={{ maxHeight: 'none' }}>
                    {links.map(link => (
                        <div key={link.id} className="ff-link-row">
                            <div className="ff-link-row-main">
                                <div className="ff-link-row-head">
                                    <Link
                                        href={`/p/${slug}/warehouse/${link.warehouse_id}/ff-request/${link.id}`}
                                        className="ff-link-row-number"
                                        style={{ color: 'var(--color-accent)' }}
                                        title="Открыть заявку ФФ"
                                    >
                                        {ffLinkLabel(link)} →
                                    </Link>
                                    <span className="badge badge-info" style={{ fontSize: 11, padding: '2px 8px' }}>
                                        {ffLinkStage(link)}
                                    </span>
                                </div>
                                <span className="ff-link-row-meta">
                                    {link.external_created_at ? `${formatDate(link.external_created_at)} · ` : ''}
                                    {link.total_qty == null ? 'количество неизвестно' : `${formatNumber(link.total_qty, 0)} шт`}
                                </span>
                            </div>
                            {canEdit && (
                                <button
                                    className="btn btn-secondary btn-sm"
                                    style={{ color: 'var(--color-danger)' }}
                                    disabled={unlinkingId === link.id}
                                    onClick={() => onUnlink(link)}
                                    title="Убрать связь с перемещением; сама заявка у ФФ останется"
                                >
                                    {unlinkingId === link.id ? '...' : 'Отвязать'}
                                </button>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
