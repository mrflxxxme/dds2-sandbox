'use client';
/**
 * Строка и карточка ПЕРЕЕЗДА в общем рабочем списке «Листа логиста».
 *
 * Канон юзера 31.07.2026: «пусть это также интегрируется как и остальные
 * заявки, только помечается (тегом) перемещение». Поэтому переезд едет в тех
 * же группах, с тем же чекбоксом и той же плавающей панелью, что и заявки на
 * сборку, — отличается ровно бейджем «Перемещение».
 *
 * Почему отдельный файл, а не ветка в page.tsx: разметка заявки завязана на
 * AssemblyRequest целиком (совместные поставки, WB-пропуск, Газелька, прогноз
 * ₽/паллета). Подсовывать туда переезд под видом заявки — единственный способ
 * получить на экране «Газельку» у переезда между нашими складами. Здесь та же
 * сетка колонок и те же классы, но поля читаются из StockTransfer.
 *
 * Соответствие колонок таблицы заявок:
 *   Заявка → № переезда, Забор → склад-источник, «Сдача WB» → склад-получатель,
 *   Дата сдачи → дата доставки, Поставка/Бренд → прочерк (у переезда их нет),
 *   Прогноз ₽ → ФАКТ стоимости забора (прогнозной модели для переездов нет).
 */
import Link from 'next/link';
import { formatDate, formatNumber } from '@/lib/utils';
import {
    TRANSFER_STATUS_MAP,
    canAssignTransferVehicle,
    transferDaysStuck,
    canSendTransfer,
    canUnassignTransferVehicle,
    toMoney,
    transferDriverName,
    transferSkuCount,
    transferTotalWeight,
    transferUnits,
    transferVehicleAssigned,
    unitCountText,
    unitShort,
} from '@/lib/transfer';
import type { StockTransfer } from '@/types/api';

export interface TransferWorkProps {
    transfer: StockTransfer;
    slug: string;
    /** Имена концов маршрута: из выдачи, а справочник — фолбэк. */
    fromName: string;
    toName: string;
    checked: boolean;
    /** Права редактора: у viewer'а ни чекбокса, ни кнопок. */
    canEdit: boolean;
    busy: boolean;
    onToggle: (id: number) => void;
    onAssign: (id: number) => void;
    onUnassign: (id: number) => void;
    onSend: (id: number) => void;
    /** Порог «висит» в днях — общий со списком заявок (STUCK_THRESHOLD_DAYS). */
    stuckThresholdDays: number;
}

/**
 * «Висит N дн» — собран, но не уехал. Полный аналог сигнала у заявок: логист
 * видит застрявшее одинаково, независимо от типа документа.
 */
function StuckBadge({ transfer, thresholdDays, compact }: {
    transfer: StockTransfer;
    thresholdDays: number;
    compact?: boolean;
}) {
    const days = transferDaysStuck(transfer);
    if (days === null || days < thresholdDays) return null;
    const very = days >= thresholdDays * 2;
    return (
        <span
            className={`badge ${very ? 'badge-danger' : 'badge-warning'}`}
            style={{ fontSize: compact ? 10 : 11 }}
            title="Переезд собран, но всё ещё не уехал — как аномалия «висит» у заявок"
        >
            ⏱ висит {formatNumber(days, 0)} дн
        </span>
    );
}

/** Бейдж-тег: по нему переезд отличается от заявки в общем списке. */
export function TransferTag({ compact }: { compact?: boolean }) {
    return (
        <span
            className="badge badge-secondary"
            style={{ fontSize: compact ? 10 : 11, marginLeft: compact ? 6 : 0 }}
            title="Переезд между нашими складами: не отгрузка на WB, а перемещение стока"
        >
            📦 Перемещение
        </span>
    );
}

/** Выбирать под машину можно только то, что её ещё ждёт либо уже везёт. */
export function isTransferSelectable(t: StockTransfer): boolean {
    return canAssignTransferVehicle(t.status);
}

/** Кнопки действий — общие для таблицы и карточек, чтобы не разъезжались. */
function TransferActions({ transfer, busy, canEdit, onAssign, onUnassign, onSend }: {
    transfer: StockTransfer;
    busy: boolean;
    canEdit: boolean;
    onAssign: (id: number) => void;
    onUnassign: (id: number) => void;
    onSend: (id: number) => void;
}) {
    if (!canEdit) return null;
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {canAssignTransferVehicle(transfer.status) && (
                <button
                    className="btn btn-primary btn-sm"
                    onClick={() => onAssign(transfer.id)}
                    disabled={busy}
                >
                    {transferVehicleAssigned(transfer) ? 'Изменить машину' : 'Назначить машину'}
                </button>
            )}
            {(canUnassignTransferVehicle(transfer.status) || canSendTransfer(transfer.status)) && (
                <div style={{ display: 'flex', gap: 4 }}>
                    {canUnassignTransferVehicle(transfer.status) && (
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => onUnassign(transfer.id)}
                            disabled={busy}
                            title="Снять машину — переезд вернётся в «Готово»"
                        >
                            Отменить
                        </button>
                    )}
                    {canSendTransfer(transfer.status) && (
                        <button
                            className="btn btn-primary btn-sm"
                            onClick={() => onSend(transfer.id)}
                            disabled={busy}
                            title="Списать товар со склада-источника и повесить транзитом на получателя"
                        >
                            Отправить
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

/** Строка табличного вида. Колонок ровно 13 — как в шапке таблицы заявок. */
export function TransferWorkTableRow(props: TransferWorkProps) {
    const { transfer, slug, fromName, toName, checked, canEdit, onToggle, stuckThresholdDays } = props;
    const st = TRANSFER_STATUS_MAP[transfer.status] ?? { label: transfer.status, className: 'badge-secondary' };
    const selectable = canEdit && isTransferSelectable(transfer);
    const weight = transferTotalWeight(transfer);
    const cost = toMoney(transfer.pickup_cost);
    const stuck = transferDaysStuck(transfer);
    const isStuck = stuck !== null && stuck >= stuckThresholdDays;
    const veryStuck = stuck !== null && stuck >= stuckThresholdDays * 2;

    return (
        <tr
            style={{
                cursor: selectable ? 'pointer' : undefined,
                background: checked
                    ? 'rgba(59, 130, 246, 0.06)'
                    : veryStuck ? 'rgba(239,68,68,0.06)' : isStuck ? 'rgba(245,158,11,0.06)' : undefined,
            }}
            onClick={() => selectable && onToggle(transfer.id)}
        >
            <td onClick={e => e.stopPropagation()} style={{ textAlign: 'center' }}>
                {selectable && (
                    <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => onToggle(transfer.id)}
                        style={{ width: 16, height: 16, accentColor: 'var(--color-primary)', cursor: 'pointer' }}
                    />
                )}
            </td>
            <td onClick={e => e.stopPropagation()}>
                <Link
                    href={`/p/${slug}/warehouse/transfers/${transfer.id}`}
                    style={{ fontWeight: 600, textDecoration: 'none', color: 'var(--color-primary)' }}
                >
                    {transfer.number}
                </Link>
                <TransferTag compact />
                {transfer.is_defect && (
                    <span className="badge badge-warning" style={{ fontSize: 10, marginLeft: 6 }} title={transfer.defect_reason || 'Переезд брака'}>
                        Брак
                    </span>
                )}
                {transfer.actual_ready_date && (
                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }} title="Когда переезд отметили собранным">
                        готов: {formatDate(transfer.actual_ready_date)}
                    </div>
                )}
            </td>
            {/* Бренда у переезда нет: он везёт сток целиком, а не отгрузку бренда. */}
            <td style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>—</td>
            <td style={{ color: 'var(--color-text-muted)' }}>{fromName}</td>
            <td>{toName}</td>
            <td style={{ fontSize: 12 }}>{transfer.delivery_date ? formatDate(transfer.delivery_date) : '—'}</td>
            {/* Поставки WB у переезда не бывает. */}
            <td style={{ color: 'var(--color-text-muted)' }}>—</td>
            <td style={{ textAlign: 'right' }}>
                {transfer.pallets_count == null
                    ? '—'
                    : `${formatNumber(transfer.pallets_count, 0)} ${unitShort(transfer.shipped_as_boxes)}`}
            </td>
            <td style={{ textAlign: 'right' }}>{weight === null ? '—' : `${formatNumber(weight, 0)} кг`}</td>
            <td style={{ textAlign: 'right' }}>{formatNumber(transferSkuCount(transfer), 0)}</td>
            <td style={{ textAlign: 'right' }}>
                {cost === null
                    ? <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                    : <span title="Стоимость забора переезда — это ФАКТ из назначенной машины, а не прогноз">
                        {formatNumber(cost, 0)}
                    </span>}
            </td>
            <td>
                <span className={`badge ${st.className}`}>{st.label}</span>
                {isStuck && (
                    <div style={{ marginTop: 2 }}>
                        <StuckBadge transfer={transfer} thresholdDays={stuckThresholdDays} compact />
                    </div>
                )}
            </td>
            <td onClick={e => e.stopPropagation()} style={{ whiteSpace: 'nowrap' }}>
                <TransferActions {...props} />
            </td>
        </tr>
    );
}

/** Карточка — та же сетка, что у карточки заявки (glass-card, minmax 220px). */
export function TransferWorkCard(props: TransferWorkProps) {
    const { transfer, slug, fromName, toName, checked, canEdit, onToggle, stuckThresholdDays } = props;
    const st = TRANSFER_STATUS_MAP[transfer.status] ?? { label: transfer.status, className: 'badge-secondary' };
    const selectable = canEdit && isTransferSelectable(transfer);
    const weight = transferTotalWeight(transfer);
    const cost = toMoney(transfer.pickup_cost);
    const driver = transferDriverName(transfer);
    const stuckDays = transferDaysStuck(transfer);
    const isStuck = stuckDays !== null && stuckDays >= stuckThresholdDays;

    return (
        <div
            className="glass-card"
            style={{
                padding: 16,
                border: checked
                    ? '2px solid var(--color-primary)'
                    : isStuck ? `2px solid var(--color-${stuckDays! >= stuckThresholdDays * 2 ? 'danger' : 'warning'})` : undefined,
                // Левая полоса — как у совместной поставки: мгновенно видно,
                // что строка в списке другого рода.
                borderLeft: '3px solid var(--color-text-muted)',
                cursor: selectable ? 'pointer' : undefined,
            }}
            onClick={selectable ? () => onToggle(transfer.id) : undefined}
        >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    {selectable && (
                        <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => onToggle(transfer.id)}
                            onClick={e => e.stopPropagation()}
                            style={{ width: 18, height: 18, accentColor: 'var(--color-primary)', cursor: 'pointer' }}
                        />
                    )}
                    <Link
                        href={`/p/${slug}/warehouse/transfers/${transfer.id}`}
                        style={{ fontWeight: 600, textDecoration: 'none', color: 'var(--color-text)', whiteSpace: 'nowrap' }}
                        onClick={e => e.stopPropagation()}
                    >
                        {transfer.number}
                    </Link>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    <TransferTag />
                    {transfer.is_defect && (
                        <span className="badge badge-warning" title={transfer.defect_reason || 'Переезд брака'}>Брак</span>
                    )}
                    <span className={`badge ${st.className}`}>{st.label}</span>
                </div>
            </div>

            {isStuck && (
                <div style={{ marginBottom: 8 }}>
                    <StuckBadge transfer={transfer} thresholdDays={stuckThresholdDays} />
                </div>
            )}

            <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                <div style={{ fontSize: 11, fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 2 }}>
                    {fromName}
                </div>
                <div style={{ fontWeight: 500, color: 'var(--color-text)' }}>→ {toName}</div>
                <div>
                    {transfer.pallets_count == null
                        ? 'Единиц: —'
                        : unitCountText(transfer.pallets_count, transfer.shipped_as_boxes)}
                    {' · Вес: '}
                    {weight === null ? '—' : `${formatNumber(weight, 0)} кг`}
                </div>
                <div>Позиций: {formatNumber(transferSkuCount(transfer), 0)} · {formatNumber(transferUnits(transfer), 0)} шт</div>
                {transfer.actual_ready_date && <div>Собран: {formatDate(transfer.actual_ready_date)}</div>}
                {transfer.pickup_date && <div>Забор: {formatDate(transfer.pickup_date)}</div>}
                {transfer.delivery_date && <div>Доставка: {formatDate(transfer.delivery_date)}</div>}
                {cost !== null && <div>Забор: {formatNumber(cost, 0)} ₽</div>}
                {transferVehicleAssigned(transfer) && (
                    <div title={[transfer.vehicle_brand, driver, transfer.driver_phone].filter(Boolean).join(' · ') || undefined}>
                        🚚 {transfer.vehicle_info || 'машина назначена'}
                    </div>
                )}
            </div>

            <div onClick={e => e.stopPropagation()}>
                <TransferActions {...props} />
            </div>
        </div>
    );
}
