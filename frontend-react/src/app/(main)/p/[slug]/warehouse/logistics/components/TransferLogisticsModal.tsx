'use client';
import { useState } from 'react';
import { formatNumber, formatDate } from '@/lib/utils';
import {
    initialUnitMode,
    toMoney,
    unitCountLabel,
    unitModeToFlag,
    unitWeightLabel,
} from '@/lib/transfer';
import type { UnitMode } from '@/lib/transfer';
import type { StockTransfer, TransferAssignVehiclePayload } from '@/types/api';

/**
 * «Указать перевозчика и стоимость» для УЖЕ УЕХАВШЕГО переезда.
 *
 * Зачем отдельная форма, а не общая модалка назначения машины: та требует
 * полный комплект реквизитов водителя (госномер + ФИО + телефон), потому что
 * её состав совпадает с WB-пропуском. Здесь пропуска нет и быть не может —
 * машина уехала недели назад, и заставлять логиста выдумывать ФИО водителя
 * ради того, чтобы завести сумму в «Оплаты», значит закрыть сценарий совсем
 * (TR-1…TR-31 уехали до появления машины на перемещении).
 *
 * Обязательное здесь ровно то, без чего переезд не станет документом оплаты:
 * стоимость, дата забора и признак перевозчика. Признак — зеркало серверного
 * `_require_vehicle_identity`: госномер ЛИБО подрядчик ЛИБО «везёт склад
 * забора»; пустое тело поставило бы машину-призрак и породило забор без
 * перевозчика и без суммы.
 */

const TIME_SLOTS = ['08:00-12:00', '12:00-16:00', '16:00-20:00', '20:00-00:00'];

interface Props {
    /** Кого оживляем: одна строка либо пачка (bulk-эндпоинт принимает те же поля). */
    transfers: StockTransfer[];
    /** Склад забора — подпись чекбокса «логистику оказывает склад». */
    pickupWarehouseName: string | null;
    /** Контрагент склада забора. Пусто → чекбокс недоступен: перевозчика взять неоткуда. */
    pickupWarehouseCounterpartyId: number | null;
    submitting: boolean;
    /** Ошибка сервера — показываем как есть, форма остаётся открытой. */
    error?: string;
    onSubmit: (payload: TransferAssignVehiclePayload) => void;
    onClose: () => void;
}

/** Первое непустое значение поля среди выбранных — префилл для пачки. */
function firstValue<T>(transfers: StockTransfer[], pick: (t: StockTransfer) => T | null | undefined): T | null {
    for (const t of transfers) {
        const v = pick(t);
        if (v !== null && v !== undefined && v !== '') return v;
    }
    return null;
}

export default function TransferLogisticsModal({
    transfers,
    pickupWarehouseName,
    pickupWarehouseCounterpartyId,
    submitting,
    error,
    onSubmit,
    onClose,
}: Props) {
    const single = transfers.length === 1 ? transfers[0] : null;
    // Дата забора по умолчанию — день фактической отгрузки: у старого переезда
    // это единственная известная дата, и пустой она уходить не должна (бэкенд
    // ждёт date, а пустая строка — 422).
    const shippedDay = firstValue(transfers, t => t.shipped_at?.slice(0, 10));
    const [carrierInn, setCarrierInn] = useState('');
    const [carrierName, setCarrierName] = useState('');
    const [logisticsByWarehouse, setLogisticsByWarehouse] = useState(false);
    const [pickupDate, setPickupDate] = useState(
        firstValue(transfers, t => t.pickup_date) ?? shippedDay ?? '',
    );
    const [pickupTimeSlot, setPickupTimeSlot] = useState(firstValue(transfers, t => t.pickup_time_slot) ?? '');
    const [pickupCost, setPickupCost] = useState<number | ''>(
        single ? (toMoney(single.pickup_cost) ?? '') : '',
    );
    const [deliveryDate, setDeliveryDate] = useState(
        firstValue(transfers, t => t.delivery_date) ?? shippedDay ?? '',
    );
    const [vehicleInfo, setVehicleInfo] = useState(firstValue(transfers, t => t.vehicle_info) ?? '');
    const [vehicleBrand, setVehicleBrand] = useState(firstValue(transfers, t => t.vehicle_brand) ?? '');
    const [driverFirstName, setDriverFirstName] = useState(firstValue(transfers, t => t.driver_first_name) ?? '');
    const [driverLastName, setDriverLastName] = useState(firstValue(transfers, t => t.driver_last_name) ?? '');
    const [driverPhone, setDriverPhone] = useState(firstValue(transfers, t => t.driver_phone) ?? '');
    // Единица известна только у одиночного переезда: в пачке она у каждого своя,
    // и двухпозиционный тумблер молча перевернул бы коробочные в паллетные.
    const [unitMode, setUnitMode] = useState<UnitMode>(() => initialUnitMode(single ? single.shipped_as_boxes : null));
    const [palletsCount, setPalletsCount] = useState<number | ''>(single?.pallets_count ?? '');
    const [palletWeight, setPalletWeight] = useState<number | ''>(
        single ? (toMoney(single.pallet_weight_kg) ?? '') : '',
    );
    const canKeepUnit = !single;

    const countLabel = unitMode === 'keep' ? 'Количество единиц' : unitCountLabel(unitMode === 'boxes');
    const weightLabel = unitMode === 'keep' ? 'Вес 1 единицы' : unitWeightLabel(unitMode === 'boxes');

    const hasCarrier = !!(vehicleInfo.trim() || carrierInn.trim() || carrierName.trim() || logisticsByWarehouse);
    const valid = pickupCost !== '' && !!pickupDate && hasCarrier && transfers.length > 0;

    const handleSubmit = () => {
        if (!valid || submitting) return;
        onSubmit({
            vehicle_info: vehicleInfo.trim(),
            vehicle_brand: vehicleBrand.trim(),
            driver_first_name: driverFirstName.trim(),
            driver_last_name: driverLastName.trim(),
            driver_phone: driverPhone.trim(),
            logistics_by_warehouse: logisticsByWarehouse,
            // В режиме «логистика от склада» подрядчик берётся из контрагента
            // склада — введённые ИНН/название игнорируем, как на бэкенде.
            carrier_inn: logisticsByWarehouse ? null : (carrierInn.trim() || null),
            carrier_name: logisticsByWarehouse ? null : (carrierName.trim() || null),
            pickup_date: pickupDate,
            pickup_time_slot: pickupTimeSlot,
            pickup_cost: Number(pickupCost),
            // Дата доставки пустой не уходит: бэкенд ждёт date, «» — 422.
            delivery_date: deliveryDate || pickupDate,
            // Пустое поле — null («не трогать»): бэкенд null игнорирует и уже
            // заданную транспортную единицу не затирает.
            pallets_count: palletsCount === '' ? null : Number(palletsCount),
            pallet_weight_kg: palletWeight === '' ? null : Number(palletWeight),
            shipped_as_boxes: unitModeToFlag(unitMode),
        });
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            {/* modal-card-solid — непрозрачный фон темы. Обычная `modal-card`
                стеклянная (blur + полупрозрачность), и поверх плотной сетки
                карточек «Уехали без стоимости» сквозь неё просвечивали чужие
                номера и кнопки: форму было physически трудно читать. Ширина
                тоже больше: полей здесь двенадцать, в 560px они жались. */}
            <div
                className="modal-card modal-card-wide modal-card-solid"
                onClick={e => e.stopPropagation()}
                style={{ maxHeight: '90vh', overflow: 'auto' }}
            >
                <h2 className="modal-title">
                    Перевозчик и стоимость{transfers.length > 1 ? ` (${transfers.length})` : ''}
                </h2>
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    Статус переезда не изменится и сток не сдвинется — заполнится только логистика.
                    Именно она заводит переезд в «Оплаты» и в отчёт «Переезды».
                </div>
                {error && (
                    <div style={{
                        marginBottom: 12, padding: '8px 12px', borderRadius: 8,
                        background: 'rgba(239, 68, 68, 0.12)', color: 'var(--color-danger)', fontSize: 13,
                    }}>
                        {error}
                    </div>
                )}
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    {transfers.map(t => (
                        <div key={t.id}>
                            {t.number}
                            {t.shipped_at ? ` · уехал ${formatDate(t.shipped_at)}` : ''}
                            {t.pallets_count != null ? ` · ${formatNumber(t.pallets_count, 0)} ед.` : ''}
                        </div>
                    ))}
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div className="form-group">
                        <label className="form-label">Стоимость забора, ₽ *</label>
                        <input
                            className="form-input"
                            type="number"
                            min={0}
                            value={pickupCost}
                            onChange={e => setPickupCost(e.target.value ? Number(e.target.value) : '')}
                            placeholder="15000"
                            autoFocus
                        />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Дата забора *</label>
                        <input className="form-input" type="date" value={pickupDate} onChange={e => setPickupDate(e.target.value)} />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Интервал</label>
                        <select className="form-input" value={pickupTimeSlot} onChange={e => setPickupTimeSlot(e.target.value)}>
                            <option value="">Не важно</option>
                            {TIME_SLOTS.map(s => (
                                <option key={s} value={s}>{s.replace('-', ' — ')}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group">
                        <label className="form-label">Дата доставки</label>
                        <input className="form-input" type="date" value={deliveryDate} onChange={e => setDeliveryDate(e.target.value)} />
                    </div>

                    <div className="form-group" style={{ gridColumn: '1 / -1', borderTop: '1px solid var(--color-border)', paddingTop: 12, marginTop: 4 }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: pickupWarehouseCounterpartyId ? 'pointer' : 'not-allowed' }}>
                            <input
                                type="checkbox"
                                checked={logisticsByWarehouse}
                                disabled={!pickupWarehouseCounterpartyId}
                                onChange={e => setLogisticsByWarehouse(e.target.checked)}
                            />
                            <span style={{ fontSize: 13 }}>
                                Логистику оказывает склад забора{pickupWarehouseName ? ` (${pickupWarehouseName})` : ''}
                            </span>
                        </label>
                        {!pickupWarehouseCounterpartyId && (
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 6 }}>
                                {transfers.length > 1
                                    ? 'Доступно, когда все выбранные переезды едут с одного склада и у него задан контрагент.'
                                    : 'У склада не задан контрагент — укажите его в справочнике складов, чтобы включить.'}
                            </div>
                        )}
                    </div>

                    {!logisticsByWarehouse && (
                        <>
                            <div className="form-group">
                                <label className="form-label">ИНН перевозчика</label>
                                <input
                                    className="form-input"
                                    value={carrierInn}
                                    onChange={e => setCarrierInn(e.target.value)}
                                    placeholder="10 или 12 цифр"
                                    maxLength={12}
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Название перевозчика</label>
                                <input
                                    className="form-input"
                                    value={carrierName}
                                    onChange={e => setCarrierName(e.target.value)}
                                    placeholder="ООО «ТК» / ИП Иванов"
                                />
                            </div>
                        </>
                    )}

                    <div className="form-group" style={{ gridColumn: '1 / -1', borderTop: '1px solid var(--color-border)', paddingTop: 12, marginTop: 4 }}>
                        <label className="form-label">Машина и водитель — если известны</label>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                            Для истории. Пропуск на WB по уехавшему переезду уже не нужен, поэтому поля необязательные.
                        </div>
                    </div>
                    <div className="form-group">
                        <label className="form-label">Госномер</label>
                        <input className="form-input" value={vehicleInfo} onChange={e => setVehicleInfo(e.target.value)} placeholder="В874УА37" />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Марка машины</label>
                        <input className="form-input" value={vehicleBrand} onChange={e => setVehicleBrand(e.target.value)} placeholder="ГАЗ-330, КАМАЗ..." />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Имя водителя</label>
                        <input className="form-input" value={driverFirstName} onChange={e => setDriverFirstName(e.target.value)} placeholder="Дмитрий" />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Фамилия водителя</label>
                        <input className="form-input" value={driverLastName} onChange={e => setDriverLastName(e.target.value)} placeholder="Крапива" />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Телефон водителя</label>
                        <input className="form-input" value={driverPhone} onChange={e => setDriverPhone(e.target.value)} placeholder="+7 999 123-45-67" />
                    </div>

                    <div className="form-group" style={{ gridColumn: '1 / -1', borderTop: '1px solid var(--color-border)', paddingTop: 12, marginTop: 4 }}>
                        <label className="form-label">Транспортная единица</label>
                        <div style={{ display: 'flex', gap: 0 }}>
                            {canKeepUnit && (
                                <button
                                    type="button"
                                    className={`btn btn-sm ${unitMode === 'keep' ? 'btn-primary' : 'btn-secondary'}`}
                                    onClick={() => setUnitMode('keep')}
                                    style={{ borderRadius: '8px 0 0 8px' }}
                                    title="Оставить каждому переезду его единицу — у выбранных она может быть разной"
                                >
                                    Не менять
                                </button>
                            )}
                            <button
                                type="button"
                                className={`btn btn-sm ${unitMode === 'pallets' ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setUnitMode('pallets')}
                                style={{ borderRadius: canKeepUnit ? 0 : '8px 0 0 8px' }}
                            >
                                Паллеты
                            </button>
                            <button
                                type="button"
                                className={`btn btn-sm ${unitMode === 'boxes' ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setUnitMode('boxes')}
                                style={{ borderRadius: '0 8px 8px 0' }}
                            >
                                Короба
                            </button>
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 6 }}>
                            {unitMode === 'keep'
                                ? 'Единица у каждого переезда останется своя — переключайте, только если хотите задать её всем выбранным.'
                                : 'Пустые количество и вес ничего не затирают — уже заданные значения останутся как есть.'}
                        </div>
                    </div>
                    <div className="form-group">
                        <label className="form-label">{countLabel}</label>
                        <input
                            className="form-input"
                            type="number"
                            min={0}
                            value={palletsCount}
                            onChange={e => setPalletsCount(e.target.value ? Number(e.target.value) : '')}
                            placeholder={unitMode === 'boxes' ? '12' : '5'}
                        />
                    </div>
                    <div className="form-group">
                        <label className="form-label">{weightLabel}, кг</label>
                        <input
                            className="form-input"
                            type="number"
                            min={0}
                            step="0.1"
                            value={palletWeight}
                            onChange={e => setPalletWeight(e.target.value ? Number(e.target.value) : '')}
                            placeholder={unitMode === 'boxes' ? '18' : '300'}
                        />
                    </div>
                </div>

                {!hasCarrier && (
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 12 }}>
                        Укажите госномер, перевозчика или отметьте «логистику оказывает склад забора» —
                        иначе забор получится без перевозчика и повиснет мусором в «Оплатах».
                    </div>
                )}

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={submitting}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleSubmit} disabled={submitting || !valid}>
                        {submitting ? 'Сохранение...' : `Сохранить (${transfers.length})`}
                    </button>
                </div>
            </div>
        </div>
    );
}
