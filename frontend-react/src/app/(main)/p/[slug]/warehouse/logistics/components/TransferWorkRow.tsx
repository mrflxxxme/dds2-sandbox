'use client';
/**
 * Строка и карточка ПЕРЕЕЗДА в общем рабочем списке «Листа логиста».
 *
 * Канон юзера 31.07.2026: «пусть это также интегрируется как и остальные
 * заявки, только помечается (тегом) перемещение». Поэтому переезд едет в тех
 * же группах, с тем же чекбоксом и той же плавающей панелью, что и заявки на
 * сборку, — отличается ровно бейджем «Перемещение».
 *
 * 🔴 ГАБАРИТЫ 1:1 с заявкой (канон юзера 01.08.2026: «нужно сделать по
 * размерам как и обычная заявка на сборку»). Карточка переезда была легче и
 * ниже соседней заявочной по трём причинам, и все три сняты здесь:
 *   1. постоянная левая полоса `borderLeft` — у заявки она только у совместной
 *      поставки, то есть означает «особый случай», а не «другой документ»;
 *   2. блок действий колонкой во всю ширину — у заявки это РЯД кнопок
 *      естественной ширины (`display:flex; gap:8; marginTop:8`);
 *   3. отсутствие нижнего блока — у заявки в READY снизу висит прогноз ₽
 *      (page.tsx renderForecastCard). Его место занял `TransferFactCard`: у
 *      переезда там ФАКТ стоимости забора, прогнозной модели для него нет.
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
    canPushTransferToGazelka,
    canSendTransfer,
    canSendTransferNow,
    canUnassignTransferVehicle,
    toMoney,
    transferCostPerUnit,
    transferDaysStuck,
    transferDriverName,
    transferSendBlockReason,
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
    /** «Отправить в Газельку» — открывает общую модалку (kind='transfer'). */
    onGazelka: (transfer: StockTransfer) => void;
    /** Склады-источники, обслуживаемые Газелькой (gazelkaSourceWarehouseIds конфига). */
    gazelkaWarehouseIds: number[];
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

/** Газелька ведёт переезд — тот же бейдж и тот же текст, что у заявки. */
function GazelkaBadge({ compact }: { compact?: boolean }) {
    return (
        <span
            className="badge badge-info"
            style={{ fontSize: compact ? 10 : 11, marginLeft: compact ? 6 : 0 }}
            title="Отправлено в Газельку — машину назначает агрегатор, вручную нельзя"
        >
            🚚 Газелька
        </span>
    );
}

/** Выбирать под машину можно только то, что её ещё ждёт либо уже везёт. */
export function isTransferSelectable(t: StockTransfer): boolean {
    return canAssignTransferVehicle(t.status);
}

/**
 * Задизейбленная кнопка с объяснением ПОД ней — дословная раскладка заявки
 * (page.tsx, ветки «Логистику ведёт Газелька» / «Ждёт готовности»).
 *
 * Прятать такую кнопку нельзя: исчезнувшее действие читается как поломка
 * интерфейса, а не как «сейчас нельзя вот почему».
 */
function BlockedAction({ label, reason, card }: { label: string; reason: string; card: boolean }) {
    return (
        <div style={card ? { width: '100%' } : undefined}>
            <button className="btn btn-primary btn-sm" disabled style={card ? { width: '100%' } : undefined}>
                {label}
            </button>
            <div style={{
                fontSize: card ? 11 : 10,
                color: 'var(--color-text-muted)',
                marginTop: card ? 4 : 2,
                whiteSpace: 'normal',
                maxWidth: card ? undefined : 160,
            }}>
                {reason}
            </div>
        </div>
    );
}

/**
 * Кнопки действий — общие для таблицы и карточек, чтобы не разъезжались.
 *
 * `variant` меняет ровно отступы: в таблице заявка держит колонку с gap 4, на
 * карточке — колонку с gap 8 и `marginTop: 8` поверх ряда кнопок gap 8.
 */
function TransferActions({ transfer, busy, canEdit, variant, gazelkaWarehouseIds, onAssign, onUnassign, onSend, onGazelka }: {
    transfer: StockTransfer;
    busy: boolean;
    canEdit: boolean;
    variant: 'table' | 'card';
    gazelkaWarehouseIds: number[];
    onAssign: (id: number) => void;
    onUnassign: (id: number) => void;
    onSend: (id: number) => void;
    onGazelka: (transfer: StockTransfer) => void;
}) {
    if (!canEdit) return null;
    const card = variant === 'card';
    const gap = card ? 8 : 4;
    // Газелька ведёт логистику сама: ручное назначение и снятие машины у такого
    // переезда только рассинхронят нас с агрегатором — как у заявки.
    const viaGazelka = transfer.via_gazelka === true;
    const sendBlock = transferSendBlockReason(transfer);
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap, marginTop: card ? 8 : undefined }}>
            <div style={{ display: 'flex', gap, flexWrap: card ? 'wrap' : undefined }}>
                {canAssignTransferVehicle(transfer.status) && (
                    viaGazelka ? (
                        <BlockedAction
                            label={transferVehicleAssigned(transfer) ? 'Изменить машину' : 'Назначить машину'}
                            reason="Логистику ведёт Газелька"
                            card={card}
                        />
                    ) : (
                        <button
                            className="btn btn-primary btn-sm"
                            onClick={() => onAssign(transfer.id)}
                            disabled={busy}
                        >
                            {transferVehicleAssigned(transfer) ? 'Изменить машину' : 'Назначить машину'}
                        </button>
                    )
                )}
                {canUnassignTransferVehicle(transfer.status) && (
                    viaGazelka ? (
                        <BlockedAction label="Отменить" reason="Логистику ведёт Газелька" card={card} />
                    ) : (
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => onUnassign(transfer.id)}
                            disabled={busy}
                            title="Снять машину — переезд вернётся в «Готово»"
                        >
                            Отменить
                        </button>
                    )
                )}
                {/* 🔴 Канон юзера 01.08.2026: «удали кнопку отправить, пока машина
                    не назначена». Не дизейбл, не подсказка — кнопки НЕТ вовсе.
                    Порядок действий однозначен: сначала машина, потом отправка
                    (её сделает либо логист этой кнопкой, либо авто-сигнал ФФ).
                    TR-32 показал цену полумеры: кнопка висела на голом READY,
                    нажатие списывало сток, а забор не создавался (гард
                    has_logistics) — переезд уезжал мимо оплат и отчёта логистики,
                    и добрать его задним числом было нечем. */}
                {canSendTransferNow(transfer) && (
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
            {canPushTransferToGazelka(transfer, gazelkaWarehouseIds) && (
                <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => onGazelka(transfer)}
                    disabled={busy}
                    title="Отдать переезд перевозчику-агрегатору: машину назначит он"
                >
                    {card ? 'Отправить в Газельку' : 'Газелька'}
                </button>
            )}
        </div>
    );
}

/**
 * Нижний блок карточки — ФАКТ логистики переезда. Занимает место прогноза
 * ₽/паллета у заявки (page.tsx renderForecastCard) и верстается теми же
 * размерами: без него карточка переезда была заметно ниже соседней заявочной,
 * и два документа одного списка выглядели разного веса.
 *
 * Рисуется ВСЕГДА, в том числе когда стоимости нет: пустое место снова развело
 * бы высоту, а строка «Забор: не задан» ещё и объясняет логисту, чего не
 * хватает, чтобы переезд доехал до «Оплат».
 */
function TransferFactCard({ transfer }: { transfer: StockTransfer }) {
    const cost = toMoney(transfer.pickup_cost);
    const perUnit = transferCostPerUnit(transfer);
    const driver = transferDriverName(transfer);
    const vehicle = [transfer.vehicle_info, transfer.vehicle_brand, driver, transfer.driver_phone]
        .filter(Boolean).join(' · ');
    // Перевозчик читается сверху вниз: машина → «везёт склад» → чего не хватает.
    const detail = vehicle
        || (transfer.logistics_by_warehouse ? 'Логистику оказывает склад забора' : null)
        || (cost === null ? 'Появится после назначения машины' : 'Перевозчик не указан');
    return (
        <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--color-border)' }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: cost === null ? 'var(--color-text-muted)' : 'var(--color-accent)' }}>
                🚚 Забор: {cost === null ? 'не задан' : `${formatNumber(cost, 0)} ₽`}
                {perUnit !== null && (
                    <span style={{ fontWeight: 400, color: 'var(--color-text-muted)' }}>
                        {' '}· {formatNumber(perUnit, 0)} ₽/{unitShort(transfer.shipped_as_boxes)}
                    </span>
                )}
            </div>
            <div
                style={{ fontSize: 11, color: 'var(--color-text-muted)' }}
                title="Стоимость забора переезда — ФАКТ из назначенной машины; прогнозной модели для переездов нет"
            >
                {detail}
            </div>
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
                        style={{ width: 16, height: 16, accentColor: 'var(--color-accent)', cursor: 'pointer' }}
                    />
                )}
            </td>
            <td onClick={e => e.stopPropagation()}>
                <Link
                    href={`/p/${slug}/warehouse/transfers/${transfer.id}`}
                    style={{ fontWeight: 600, textDecoration: 'none', color: 'var(--color-accent)' }}
                >
                    {transfer.number}
                </Link>
                <TransferTag compact />
                {transfer.via_gazelka && <GazelkaBadge compact />}
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
            <td style={{ fontSize: 12 }}>{'—'}</td>
            <td style={{ color: 'var(--color-text-muted)' }}>{fromName}</td>
            <td>{toName}</td>
            <td style={{ fontSize: 12 }}>{transfer.delivery_date ? formatDate(transfer.delivery_date) : '—'}</td>
            {/* Поставки WB у переезда не бывает — колонка держит моноширинный вид заявки. */}
            <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{'—'}</td>
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
                    : <span
                        style={{ fontWeight: 600 }}
                        title="Стоимость забора переезда — это ФАКТ из назначенной машины, а не прогноз"
                    >
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
                <TransferActions {...props} variant="table" />
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
    const stuckDays = transferDaysStuck(transfer);
    const isStuck = stuckDays !== null && stuckDays >= stuckThresholdDays;

    return (
        <div
            className="glass-card"
            style={{
                padding: 16,
                // Рамка — ровно как у заявки: выбор и «висит». Постоянной левой
                // полосы у переезда НЕТ: у заявки она означает совместную
                // поставку (особый случай), а тип документа и так виден по тегу.
                border: checked
                    ? '2px solid var(--color-accent)'
                    : isStuck ? `2px solid var(--color-${stuckDays! >= stuckThresholdDays * 2 ? 'danger' : 'warning'})` : undefined,
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
                            style={{ width: 18, height: 18, accentColor: 'var(--color-accent)', cursor: 'pointer' }}
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
                    {transfer.via_gazelka && <GazelkaBadge />}
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
                {/* 🔴 Маршрут подписан явно. Раньше склад-источник стоял мелким
                    капсом отдельной строкой (у заявки в этом месте склад забора),
                    а получатель — строкой «→ Газпром»: на карточке переезда это
                    читалось как «склад и что-то ещё», и логист не понимал, откуда
                    и куда едет груз. У переезда, в отличие от заявки, ВАЖНЫ ОБА
                    конца — поэтому не капс, а подписанная пара «Откуда → Куда». */}
                <div style={{ marginBottom: 4 }}>
                    <div style={{ fontSize: 11, fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                        Откуда → Куда
                    </div>
                    <div style={{ fontWeight: 600, color: 'var(--color-text)', lineHeight: 1.35 }}>
                        {fromName} <span style={{ color: 'var(--color-text-muted)' }}>→</span> {toName}
                    </div>
                </div>
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
            </div>

            {/* Место прогноза ₽ у заявки — у переезда здесь факт логистики. */}
            <TransferFactCard transfer={transfer} />

            <div onClick={e => e.stopPropagation()}>
                <TransferActions {...props} variant="card" />
            </div>
        </div>
    );
}
