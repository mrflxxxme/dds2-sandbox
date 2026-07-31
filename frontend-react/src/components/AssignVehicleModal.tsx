'use client';
import { useState } from 'react';
import { initialUnitMode, unitCountLabel, unitModeToFlag, unitWeightLabel } from '@/lib/transfer';
import type { UnitMode } from '@/lib/transfer';

/**
 * Модалка «Назначить машину» — общая форма для заявки на сборку и перемещения.
 *
 * Состав полей 1:1 с WB-пропуском (имя/фамилия/госномер/марка/телефон), чтобы
 * занос пропуска не требовал ручного перевода полей. У заявки на сборку такая
 * же форма вшита в страницу (assembly/[id]/page.tsx) — её намеренно НЕ трогаем,
 * этот компонент используют новые экраны переезда.
 *
 * Режим «логистику оказывает склад забора»: перевозчик берётся из контрагента
 * склада-источника, поля подрядчика прячутся и уходят в null.
 */

export interface AssignVehicleValues {
    vehicle_info: string;
    vehicle_brand: string;
    driver_first_name: string;
    driver_last_name: string;
    driver_phone: string;
    logistics_by_warehouse: boolean;
    carrier_inn: string | null;
    carrier_name: string | null;
    pickup_date: string;
    pickup_time_slot: string;
    pickup_cost: number;
    delivery_date: string;
    /**
     * Транспортная единица. Пустое поле уходит как null — бэкенд null игнорирует
     * и уже заданное значение НЕ затирает (иначе повторное назначение машины
     * молча стирало бы паллеты, перенесённые из заявки).
     * Если логист блок не трогал вовсе — null уходит и во флаге: иначе дефолт
     * «паллеты» перевёл бы коробочный переезд в паллетный (важно для bulk).
     */
    pallets_count: number | null;
    pallet_weight_kg: number | null;
    shipped_as_boxes: boolean | null;
}

export interface AssignVehicleInitial {
    vehicle_info?: string | null;
    vehicle_brand?: string | null;
    driver_first_name?: string | null;
    driver_last_name?: string | null;
    driver_phone?: string | null;
    logistics_by_warehouse?: boolean | null;
    pickup_date?: string | null;
    pickup_time_slot?: string | null;
    pickup_cost?: number | null;
    delivery_date?: string | null;
    pallets_count?: number | null;
    pallet_weight_kg?: number | null;
    shipped_as_boxes?: boolean | null;
}

interface Props {
    title?: string;
    initial?: AssignVehicleInitial;
    /** Имя склада забора — в подписи чекбокса «логистику оказывает склад». */
    pickupWarehouseName?: string | null;
    /**
     * Контрагент склада забора. Пусто → чекбокс «логистику оказывает склад»
     * недоступен: перевозчика взять неоткуда.
     */
    pickupWarehouseCounterpartyId?: number | null;
    /** Подпись даты доставки: у заявки «Дата сдачи на WB», у переезда — «Дата доставки». */
    deliveryDateLabel?: string;
    /**
     * Показывать блок транспортной единицы (паллеты/короба + количество и вес).
     * У переезда единица задаётся здесь; при переиспользовании модалки заявкой
     * её нужно выключить — там единица живёт в самой заявке.
     */
    showTransportUnit?: boolean;
    /** Подпись кнопки подтверждения: у bulk — «Назначить (3)», как в модалке заявок. */
    submitLabel?: string;
    submitting?: boolean;
    /** Ошибка сервера — показываем текстом как есть, форма остаётся открытой. */
    error?: string;
    onSubmit: (values: AssignVehicleValues) => void;
    onClose: () => void;
}

const TIME_SLOTS = ['08:00-12:00', '12:00-16:00', '16:00-20:00', '20:00-00:00'];

export default function AssignVehicleModal({
    title = 'Назначить машину',
    initial,
    pickupWarehouseName,
    pickupWarehouseCounterpartyId,
    deliveryDateLabel = 'Дата доставки',
    showTransportUnit = true,
    submitLabel = 'Назначить',
    submitting = false,
    error,
    onSubmit,
    onClose,
}: Props) {
    const [vehicleInfo, setVehicleInfo] = useState(initial?.vehicle_info ?? '');
    const [vehicleBrand, setVehicleBrand] = useState(initial?.vehicle_brand ?? '');
    const [driverFirstName, setDriverFirstName] = useState(initial?.driver_first_name ?? '');
    const [driverLastName, setDriverLastName] = useState(initial?.driver_last_name ?? '');
    const [driverPhone, setDriverPhone] = useState(initial?.driver_phone ?? '');
    const [carrierInn, setCarrierInn] = useState('');
    const [carrierName, setCarrierName] = useState('');
    const [logisticsByWarehouse, setLogisticsByWarehouse] = useState(
        !!initial?.logistics_by_warehouse && !!pickupWarehouseCounterpartyId,
    );
    const [pickupDate, setPickupDate] = useState(initial?.pickup_date ?? '');
    const [pickupTimeSlot, setPickupTimeSlot] = useState(initial?.pickup_time_slot ?? '');
    const [pickupCost, setPickupCost] = useState<number | ''>(initial?.pickup_cost ?? '');
    const [deliveryDate, setDeliveryDate] = useState(initial?.delivery_date ?? '');
    // Транспортная единица: режим меняет только подписи и смысл двух полей ниже.
    // Неизвестная исходная единица (bulk) → режим «не менять», и он ЖЁСТКО
    // отвязан от полей количества/веса: ввод числа не должен решать за логиста,
    // паллеты у него или короба (иначе одна цифра переворачивала бы единицу
    // всем выбранным переездам разом).
    const [unitMode, setUnitMode] = useState<UnitMode>(() => initialUnitMode(initial?.shipped_as_boxes));
    const [palletsCount, setPalletsCount] = useState<number | ''>(initial?.pallets_count ?? '');
    const [palletWeight, setPalletWeight] = useState<number | ''>(initial?.pallet_weight_kg ?? '');
    // «Не менять» предлагаем только когда исходная единица неизвестна: у одного
    // переезда она всегда известна, и лишний вариант там был бы шумом.
    const canKeepUnit = initial?.shipped_as_boxes === null || initial?.shipped_as_boxes === undefined;
    // Подписи полей: в режиме «не менять» единица не выбрана — нейтральные.
    const countLabel = unitMode === 'keep' ? 'Количество единиц' : unitCountLabel(unitMode === 'boxes');
    const weightLabel = unitMode === 'keep' ? 'Вес 1 единицы' : unitWeightLabel(unitMode === 'boxes');

    // Все поля машины/водителя обязательны — состав совпадает с WB-пропуском.
    const valid = !!(vehicleInfo.trim() && vehicleBrand.trim() && driverPhone.trim()
        && driverFirstName.trim() && driverLastName.trim()
        && pickupDate && pickupTimeSlot && pickupCost !== '' && deliveryDate);

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
            // склада — введённые ИНН/название игнорируем.
            carrier_inn: logisticsByWarehouse ? null : (carrierInn.trim() || null),
            carrier_name: logisticsByWarehouse ? null : (carrierName.trim() || null),
            pickup_date: pickupDate,
            pickup_time_slot: pickupTimeSlot,
            pickup_cost: Number(pickupCost),
            delivery_date: deliveryDate,
            // Пустое поле — null («не трогать»), а не 0: бэкенд null игнорирует
            // и не затирает уже заданное. Флаг единицы — только из явного выбора
            // логиста (режим «не менять» → null), НИКОГДА не выводится из того,
            // что он что-то ввёл в соседнее числовое поле.
            pallets_count: palletsCount === '' ? null : Number(palletsCount),
            pallet_weight_kg: palletWeight === '' ? null : Number(palletWeight),
            shipped_as_boxes: unitModeToFlag(unitMode),
        });
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
                <h2 className="modal-title">{title}</h2>
                {error && (
                    <div style={{
                        marginBottom: 12, padding: '8px 12px', borderRadius: 8,
                        background: 'rgba(239, 68, 68, 0.12)', color: 'var(--color-danger)', fontSize: 13,
                    }}>
                        {error}
                    </div>
                )}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div className="form-group">
                        <label className="form-label">Имя водителя *</label>
                        <input className="form-input" value={driverFirstName} onChange={e => setDriverFirstName(e.target.value)} placeholder="Дмитрий" autoFocus />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Фамилия водителя *</label>
                        <input className="form-input" value={driverLastName} onChange={e => setDriverLastName(e.target.value)} placeholder="Крапива" />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Госномер *</label>
                        <input className="form-input" value={vehicleInfo} onChange={e => setVehicleInfo(e.target.value)} placeholder="В874УА37" />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Марка машины *</label>
                        <input className="form-input" value={vehicleBrand} onChange={e => setVehicleBrand(e.target.value)} placeholder="ГАЗ-330, КАМАЗ..." />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Телефон водителя *</label>
                        <input className="form-input" value={driverPhone} onChange={e => setDriverPhone(e.target.value)} placeholder="+7 999 123-45-67" />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Дата забора *</label>
                        <input className="form-input" type="date" value={pickupDate} onChange={e => setPickupDate(e.target.value)} />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Интервал *</label>
                        <select className="form-input" value={pickupTimeSlot} onChange={e => setPickupTimeSlot(e.target.value)}>
                            <option value="">Выберите...</option>
                            {TIME_SLOTS.map(s => (
                                <option key={s} value={s}>{s.replace('-', ' — ')}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group">
                        <label className="form-label">Стоимость забора, ₽ *</label>
                        <input
                            className="form-input"
                            type="number"
                            min={0}
                            value={pickupCost}
                            onChange={e => setPickupCost(e.target.value ? Number(e.target.value) : '')}
                            placeholder="15000"
                        />
                    </div>
                    <div className="form-group">
                        <label className="form-label">{deliveryDateLabel} *</label>
                        <input className="form-input" type="date" value={deliveryDate} onChange={e => setDeliveryDate(e.target.value)} />
                    </div>
                    {showTransportUnit && (
                        <>
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
                        </>
                    )}
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
                                У склада не задан контрагент — укажите его в справочнике складов, чтобы включить.
                            </div>
                        )}
                    </div>
                    {!logisticsByWarehouse && (
                        <>
                            <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    Подрядчик (опционально) — расходы из выписки с этим ИНН попадут в «Перевозчик».
                                </div>
                            </div>
                            <div className="form-group">
                                <label className="form-label">ИНН подрядчика</label>
                                <input
                                    className="form-input"
                                    value={carrierInn}
                                    onChange={e => setCarrierInn(e.target.value)}
                                    placeholder="10 или 12 цифр"
                                    maxLength={12}
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Название подрядчика</label>
                                <input
                                    className="form-input"
                                    value={carrierName}
                                    onChange={e => setCarrierName(e.target.value)}
                                    placeholder="ООО «ТК» / ИП Иванов"
                                />
                            </div>
                        </>
                    )}
                </div>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={submitting}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleSubmit} disabled={submitting || !valid}>
                        {submitting ? 'Назначение...' : submitLabel}
                    </button>
                </div>
            </div>
        </div>
    );
}
