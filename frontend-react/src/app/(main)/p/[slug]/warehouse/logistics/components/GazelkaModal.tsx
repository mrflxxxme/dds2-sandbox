'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import {
    dateChoices,
    findPlan,
    formatAllowedDays,
    formatDateOption,
    validDelivery,
    warehousesFor,
} from '@/lib/gazelkaSchedule';
import { formatNumber } from '@/lib/utils';
import type {
    GazelkaDraft,
    GazelkaEditDraft,
    GazelkaFormOptions,
    GazelkaPrefill,
    GazelkaSendRequest,
    GazelkaSelectOption,
} from '@/types/api';

interface Props {
    assemblyId: number;
    assemblyNumber: string;
    onClose: () => void;
    onSuccess: () => void;
    /** Если задан — режим редактирования существующей заявки Газельки. */
    editPlanId?: number;
    /** Заголовок для режима редактирования (напр. «Заявка 12345 · WB-...»). */
    editTitle?: string;
}

function SelectField({
    label,
    value,
    onChange,
    options,
    required,
    placeholder,
}: {
    label: string;
    value: string;
    onChange: (v: string) => void;
    options: GazelkaSelectOption[];
    required?: boolean;
    placeholder?: string;
}) {
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)' }}>
                {label}{required && <span style={{ color: 'var(--color-danger)', marginLeft: 2 }}>*</span>}
            </label>
            <select
                value={value}
                onChange={e => onChange(e.target.value)}
                style={{
                    padding: '7px 10px',
                    borderRadius: 8,
                    border: '1px solid var(--color-border)',
                    background: 'var(--color-bg-card)',
                    color: 'var(--color-text)',
                    fontSize: 14,
                }}
            >
                {placeholder && <option value="">{placeholder}</option>}
                {options.map(o => (
                    <option key={o.place_id ?? o.value} value={o.value}>{o.label}</option>
                ))}
            </select>
        </div>
    );
}

function TextField({
    label,
    value,
    onChange,
    type = 'text',
    required,
    placeholder,
}: {
    label: string;
    value: string;
    onChange: (v: string) => void;
    type?: string;
    required?: boolean;
    placeholder?: string;
}) {
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)' }}>
                {label}{required && <span style={{ color: 'var(--color-danger)', marginLeft: 2 }}>*</span>}
            </label>
            <input
                type={type}
                value={value}
                onChange={e => onChange(e.target.value)}
                placeholder={placeholder}
                style={{
                    padding: '7px 10px',
                    borderRadius: 8,
                    border: '1px solid var(--color-border)',
                    background: 'var(--color-bg-card)',
                    color: 'var(--color-text)',
                    fontSize: 14,
                }}
            />
        </div>
    );
}

function NumberField({
    label,
    value,
    onChange,
    required,
    min,
}: {
    label: string;
    value: number | '';
    onChange: (v: number | '') => void;
    required?: boolean;
    min?: number;
}) {
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)' }}>
                {label}{required && <span style={{ color: 'var(--color-danger)', marginLeft: 2 }}>*</span>}
            </label>
            <input
                type="number"
                value={value === '' ? '' : value}
                min={min}
                onChange={e => {
                    const v = e.target.value;
                    onChange(v === '' ? '' : Number(v));
                }}
                style={{
                    padding: '7px 10px',
                    borderRadius: 8,
                    border: '1px solid var(--color-border)',
                    background: 'var(--color-bg-card)',
                    color: 'var(--color-text)',
                    fontSize: 14,
                }}
            />
        </div>
    );
}

function initForm(options: GazelkaFormOptions, prefill: GazelkaPrefill) {
    // Дефолты берём из выбранных порталом значений: у него первая опция ≠ выбранная
    // (price_id идёт Симферополь…Иваново, выбрано Иваново), а от price_id зависит график.
    const entity = options.default_entity_id ?? options.entities[0]?.value ?? '';
    return {
        entity_id: entity,
        payer_id: entity,
        price_id: options.default_price_id ?? options.price_lists[0]?.value ?? '',
        is_marketplace: 'yes' as string,
        marketplace_id: prefill.marketplace_id ?? options.marketplaces[0]?.value ?? '',
        supply_id: prefill.supply_id ?? '',
        // Без матча склад НЕ угадываем: первый в списке может принадлежать другому маркетплейсу
        delivery_address: prefill.delivery_address ?? '',
        delivery_address_x2: prefill.delivery_address_x2 ?? '',
        departure_date: prefill.departure_date ?? '',
        delivery_date: prefill.delivery_date ?? '',
        delivery_time: '',
        daily_delivery_timeslot: prefill.daily_delivery_timeslot ?? options.timeslots[0]?.value ?? '',
        delivery_contact: prefill.delivery_contact ?? '',
        customer_phone: prefill.customer_phone ?? '',
        monomix: options.supply_types[0]?.value ?? '',
        pallets: prefill.pallets,
        boxes: prefill.boxes,
        weight: prefill.weight !== null ? String(Number(prefill.weight)) : '',
        weight2: '',
        volume: '',
        length: 60 as number | '',
        height: 40 as number | '',
        width: 40 as number | '',
        palleting: false,
        notes: prefill.notes ?? '',
    };
}

/** Инициализация формы из GazelkaSendRequest (режим редактирования). */
function initFormFromValues(values: GazelkaSendRequest): ReturnType<typeof initForm> {
    return {
        entity_id: values.entity_id,
        payer_id: values.payer_id,
        price_id: values.price_id,
        is_marketplace: values.is_marketplace,
        marketplace_id: values.marketplace_id ?? '',
        supply_id: values.supply_id ?? '',
        delivery_address: values.delivery_address ?? '',
        delivery_address_x2: values.delivery_address_x2 ?? '',
        departure_date: values.departure_date ?? '',
        delivery_date: values.delivery_date ?? '',
        delivery_time: values.delivery_time ?? '',
        daily_delivery_timeslot: values.daily_delivery_timeslot ?? '',
        delivery_contact: values.delivery_contact ?? '',
        customer_phone: values.customer_phone ?? '',
        monomix: values.monomix ?? '',
        pallets: values.pallets,
        boxes: values.boxes,
        weight: values.weight ?? '',
        weight2: values.weight2 ?? '',
        volume: values.volume ?? '',
        length: values.length !== undefined ? values.length : (60 as number | ''),
        height: values.height !== undefined ? values.height : (40 as number | ''),
        width: values.width !== undefined ? values.width : (40 as number | ''),
        palleting: values.palleting,
        notes: values.notes ?? '',
    };
}

type FormState = ReturnType<typeof initForm>;

export default function GazelkaModal({ assemblyId, assemblyNumber, onClose, onSuccess, editPlanId, editTitle }: Props) {
    const isEditMode = editPlanId !== undefined;

    // В режиме отправки — GazelkaDraft; в режиме редактирования — GazelkaEditDraft.
    const [draft, setDraft] = useState<GazelkaDraft | null>(null);
    const [editDraft, setEditDraft] = useState<GazelkaEditDraft | null>(null);
    const [loadingDraft, setLoadingDraft] = useState(true);
    const [draftError, setDraftError] = useState('');

    const [form, setForm] = useState<FormState | null>(null);

    const [submitting, setSubmitting] = useState(false);
    const [submitError, setSubmitError] = useState('');
    const [submitSuccess, setSubmitSuccess] = useState('');
    const [validationError, setValidationError] = useState('');

    // Единый путь загрузки draft (и для маунта, и для кнопки «Повторить»).
    // Возвращает cleanup: useEffect отдаёт его напрямую. Сигнал не идёт в fetch
    // (api без AbortSignal) — guard `aborted` гасит stale-setState в StrictMode.
    const loadDraft = useCallback(() => {
        setLoadingDraft(true);
        setDraftError('');
        const controller = new AbortController();
        if (isEditMode && editPlanId !== undefined) {
            api.getGazelkaEditDraft(editPlanId).then(d => {
                if (controller.signal.aborted) return;
                setEditDraft(d);
                setForm(initFormFromValues(d.values));
            }).catch((e: unknown) => {
                if (controller.signal.aborted) return;
                setDraftError(e instanceof Error ? e.message : 'Ошибка загрузки');
            }).finally(() => {
                if (!controller.signal.aborted) setLoadingDraft(false);
            });
        } else {
            api.getGazelkaDraft(assemblyId).then(d => {
                if (controller.signal.aborted) return;
                setDraft(d);
                setForm(initForm(d.options, d.prefill));
            }).catch((e: unknown) => {
                if (controller.signal.aborted) return;
                setDraftError(e instanceof Error ? e.message : 'Ошибка загрузки');
            }).finally(() => {
                if (!controller.signal.aborted) setLoadingDraft(false);
            });
        }
        return () => controller.abort();
    }, [assemblyId, isEditMode, editPlanId]);

    useEffect(() => loadDraft(), [loadDraft]);

    // Актуальные options для рендера формы
    const formOptions: GazelkaFormOptions | null = isEditMode
        ? (editDraft?.options ?? null)
        : (draft?.options ?? null);

    // График выбранного направления. Для не-маркетплейсной доставки его нет — даты свободные.
    const isMarketplace = form?.is_marketplace === 'yes';
    const plan = formOptions && form && isMarketplace
        ? findPlan(formOptions, form.price_id, form.delivery_address, form.marketplace_id)
        : null;
    const warehouses = formOptions && form && isMarketplace
        ? warehousesFor(formOptions, form.price_id, form.marketplace_id)
        : (formOptions?.delivery_warehouses ?? []);
    // Даты, реально допустимые для этого направления. Протухший prefill (или дата уже
    // созданной заявки) в списки не попадает и потому НЕ считается выбранным.
    const dates = formOptions && form && plan ? dateChoices(formOptions, plan, form.departure_date) : null;
    const departureValue = dates ? dates.departure : (form?.departure_date ?? '');
    const deliveryValue = dates ? validDelivery(dates, form?.delivery_date ?? '') : (form?.delivery_date ?? '');

    const set = <K extends keyof FormState>(key: K, value: FormState[K]) => {
        setForm(prev => prev ? { ...prev, [key]: value } : prev);
        setValidationError('');
        setSubmitError('');
    };

    /** Смена маркетплейса/города обнуляет склад: у каждого направления свой график. */
    const setScopeField = (key: 'price_id' | 'marketplace_id', value: string) => {
        setForm(prev => prev ? { ...prev, [key]: value, delivery_address: '', departure_date: '', delivery_date: '' } : prev);
        setValidationError('');
        setSubmitError('');
    };

    /** Смена склада или даты отправки делает прежнюю дату доставки невалидной. */
    const setDeliveryAddress = (value: string) => {
        setForm(prev => prev ? { ...prev, delivery_address: value, departure_date: '', delivery_date: '' } : prev);
        setValidationError('');
        setSubmitError('');
    };

    const setDepartureDate = (value: string) => {
        setForm(prev => prev ? { ...prev, departure_date: value, delivery_date: '' } : prev);
        setValidationError('');
        setSubmitError('');
    };

    const handleSubmit = async () => {
        if (!form) return;
        if (!isEditMode && !draft) return;
        if (isEditMode && !editDraft) return;
        if (!form.entity_id) { setValidationError('Выберите юрлицо'); return; }
        if (!form.payer_id) { setValidationError('Выберите плательщика'); return; }
        if (!form.price_id) { setValidationError('Выберите прайс-лист (город-источник)'); return; }
        if (isMarketplace) {
            if (!form.delivery_address) { setValidationError('Выберите склад назначения'); return; }
            if (!plan) { setValidationError('Это направление не обслуживается из выбранного города'); return; }
            if (!departureValue) { setValidationError('Выберите дату отправки'); return; }
            if (!deliveryValue) { setValidationError('Выберите дату доставки'); return; }
        }

        const body: GazelkaSendRequest = {
            entity_id: form.entity_id,
            payer_id: form.payer_id,
            price_id: form.price_id,
            is_marketplace: form.is_marketplace,
            marketplace_id: form.marketplace_id || null,
            supply_id: form.supply_id || null,
            delivery_address: form.delivery_address || null,
            delivery_address_x2: form.delivery_address_x2 || null,
            // Только нормализованные даты: залипшее в стейте протухшее значение не уедет
            departure_date: departureValue || null,
            delivery_date: deliveryValue || null,
            delivery_time: form.delivery_time || null,
            daily_delivery_timeslot: form.daily_delivery_timeslot || null,
            delivery_contact: form.delivery_contact || null,
            customer_phone: form.customer_phone || null,
            monomix: form.monomix || null,
            pallets: form.pallets,
            boxes: form.boxes,
            weight: form.weight || null,
            weight2: form.weight2 || null,
            volume: form.volume || null,
            length: form.length === '' ? 60 : form.length,
            height: form.height === '' ? 40 : form.height,
            width: form.width === '' ? 40 : form.width,
            palleting: form.palleting,
            notes: form.notes || null,
            // force_resend только в режиме отправки новой заявки
            ...(!isEditMode && { force_resend: draft!.already_sent }),
        };

        setSubmitting(true);
        setSubmitError('');
        setSubmitSuccess('');
        try {
            let result;
            if (isEditMode && editPlanId !== undefined) {
                result = await api.saveGazelkaEdit(editPlanId, body);
            } else {
                result = await api.sendToGazelka(assemblyId, body);
            }
            if (result.ok) {
                setSubmitSuccess(
                    isEditMode
                        ? (result.ref ? `Заявка сохранена. Ref: ${result.ref}` : 'Заявка успешно сохранена')
                        : (result.ref ? `Заявка отправлена. Ref: ${result.ref}` : 'Заявка успешно отправлена в Газельку')
                );
                onSuccess();
            } else {
                setSubmitError(result.message || (isEditMode ? 'Ошибка сохранения' : 'Ошибка отправки'));
            }
        } catch (e: unknown) {
            setSubmitError(e instanceof Error ? e.message : (isEditMode ? 'Ошибка сохранения' : 'Ошибка отправки'));
        } finally {
            setSubmitting(false);
        }
    };

    // ─── Render ───────────────────────────────────────────────────────────────

    return (
        <div
            className="modal-overlay"
            style={{ padding: '24px 16px' }}
            onClick={e => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div
                className="modal-card modal-card-wide modal-card-solid"
                style={{
                    width: 720, maxWidth: '94vw',
                    maxHeight: 'calc(100vh - 48px)',
                    padding: 0,
                    display: 'flex', flexDirection: 'column',
                    overflow: 'hidden',
                }}
                onClick={e => e.stopPropagation()}
            >
                {/* Header — липкая шапка */}
                <div style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    padding: '18px 24px', borderBottom: '1px solid var(--color-border)', flexShrink: 0,
                }}>
                    <div>
                        <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>
                            {isEditMode ? 'Редактировать заявку' : 'Отправить в Газельку'}
                        </h2>
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 2 }}>
                            {isEditMode
                                ? (editTitle || `Заявка ${editPlanId}`)
                                : `Заявка ${assemblyNumber}`
                            }
                        </div>
                    </div>
                    <button
                        onClick={onClose}
                        style={{
                            background: 'none', border: 'none', fontSize: 24,
                            cursor: 'pointer', color: 'var(--color-text-muted)',
                            lineHeight: 1, padding: '0 4px',
                        }}
                        aria-label="Закрыть"
                    >
                        ×
                    </button>
                </div>

                {/* Body — прокручиваемая середина */}
                <div style={{ padding: '20px 24px', overflowY: 'auto', flex: 1 }}>

                {/* Loading */}
                {loadingDraft && (
                    <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--color-text-muted)' }}>
                        Загрузка данных...
                    </div>
                )}

                {/* Error loading draft */}
                {!loadingDraft && draftError && (
                    <div style={{ padding: '16px', borderRadius: 8, background: 'rgba(239,68,68,0.1)', color: 'var(--color-danger)', marginBottom: 16 }}>
                        {draftError}
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={loadDraft}
                            style={{ marginLeft: 12 }}
                        >
                            Повторить
                        </button>
                    </div>
                )}

                {/* Not eligible (только в режиме отправки) */}
                {!loadingDraft && !draftError && !isEditMode && draft && !draft.eligible && (
                    <div style={{ padding: '16px', borderRadius: 8, background: 'rgba(245,158,11,0.1)', color: 'var(--color-warning)' }}>
                        Эта заявка недоступна для отправки в Газельку.
                    </div>
                )}

                {/* Already sent warning (только в режиме отправки) */}
                {!loadingDraft && !draftError && !isEditMode && draft?.eligible && draft.already_sent && (
                    <div style={{ padding: '12px 16px', borderRadius: 8, background: 'rgba(245,158,11,0.1)', color: 'var(--color-warning)', marginBottom: 16, fontSize: 14 }}>
                        Заявка уже была отправлена{draft.sent_ref ? ` (ref: ${draft.sent_ref})` : ''}. Повторная отправка создаст новую заявку в Газельке.
                    </div>
                )}

                {/* Success */}
                {submitSuccess && (
                    <div style={{ padding: '12px 16px', borderRadius: 8, background: 'rgba(34,197,94,0.1)', color: 'var(--color-success)', marginBottom: 16, fontSize: 14 }}>
                        {submitSuccess}
                    </div>
                )}

                {/* Form */}
                {!loadingDraft && !draftError && (isEditMode ? !!editDraft : draft?.eligible) && form && formOptions && (
                    <>
                        {/* ── Юрлицо и плательщик ── */}
                        <div style={{ marginBottom: 20 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                                Контрагенты
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                                <SelectField
                                    label="Юрлицо"
                                    value={form.entity_id}
                                    onChange={v => set('entity_id', v)}
                                    options={formOptions.entities}
                                    required
                                    placeholder="— выберите —"
                                />
                                <SelectField
                                    label="Плательщик"
                                    value={form.payer_id}
                                    onChange={v => set('payer_id', v)}
                                    options={formOptions.entities}
                                    required
                                    placeholder="— выберите —"
                                />
                                <SelectField
                                    label="Прайс-лист (город-источник)"
                                    value={form.price_id}
                                    onChange={v => setScopeField('price_id', v)}
                                    options={formOptions.price_lists}
                                    required
                                    placeholder="— выберите —"
                                />
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                    <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)' }}>Маркетплейс</label>
                                    <select
                                        value={form.is_marketplace}
                                        onChange={e => set('is_marketplace', e.target.value)}
                                        style={{
                                            padding: '7px 10px', borderRadius: 8,
                                            border: '1px solid var(--color-border)',
                                            background: 'var(--color-bg-card)', color: 'var(--color-text)', fontSize: 14,
                                        }}
                                    >
                                        <option value="yes">Да</option>
                                        <option value="no">Нет</option>
                                        <option value="">Не указано</option>
                                    </select>
                                </div>
                            </div>
                            {formOptions.marketplaces.length > 0 && (
                                <div style={{ marginTop: 12 }}>
                                    <SelectField
                                        label="ID маркетплейса"
                                        value={form.marketplace_id}
                                        onChange={v => setScopeField('marketplace_id', v)}
                                        options={formOptions.marketplaces}
                                        placeholder="— выберите —"
                                    />
                                </div>
                            )}
                        </div>

                        {/* ── Тип поставки ── */}
                        {formOptions.supply_types.length > 0 && (
                            <div style={{ marginBottom: 20 }}>
                                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                                    Тип поставки
                                </div>
                                <SelectField
                                    label="Моно/Микс"
                                    value={form.monomix}
                                    onChange={v => set('monomix', v)}
                                    options={formOptions.supply_types}
                                    placeholder="— выберите —"
                                />
                            </div>
                        )}

                        {/* ── Адрес и доставка ── */}
                        <div style={{ marginBottom: 20 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                                Адрес и доставка
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                                {warehouses.length > 0 ? (
                                    <SelectField
                                        label="Склад назначения"
                                        value={form.delivery_address}
                                        onChange={setDeliveryAddress}
                                        options={warehouses}
                                        placeholder="— выберите —"
                                    />
                                ) : (
                                    <TextField
                                        label="Адрес доставки"
                                        value={form.delivery_address}
                                        onChange={setDeliveryAddress}
                                        placeholder="Адрес"
                                    />
                                )}
                                <TextField
                                    label="Адрес доп."
                                    value={form.delivery_address_x2}
                                    onChange={v => set('delivery_address_x2', v)}
                                    placeholder="Доп. адрес (опционально)"
                                />
                                {/* С графиком — только допустимые дни (портал 500-ит на «серый» день
                                    календаря). Без графика доставка идёт свободными датами. */}
                                {dates ? (
                                    <>
                                        <SelectField
                                            label="Дата отправки"
                                            value={departureValue}
                                            onChange={setDepartureDate}
                                            options={dates.departures.map(d => ({ value: d, label: formatDateOption(d) }))}
                                            placeholder="— выберите —"
                                        />
                                        <SelectField
                                            label="Дата доставки"
                                            value={deliveryValue}
                                            onChange={v => set('delivery_date', v)}
                                            options={dates.deliveries.map(d => ({ value: d, label: formatDateOption(d) }))}
                                            placeholder={departureValue ? '— выберите —' : 'сначала дата отправки'}
                                        />
                                    </>
                                ) : (
                                    <>
                                        <TextField
                                            label="Дата отправки"
                                            value={form.departure_date}
                                            onChange={setDepartureDate}
                                            type="date"
                                        />
                                        <TextField
                                            label="Дата доставки"
                                            value={form.delivery_date}
                                            onChange={v => set('delivery_date', v)}
                                            type="date"
                                        />
                                    </>
                                )}
                                <TextField
                                    label="Время доставки"
                                    value={form.delivery_time}
                                    onChange={v => set('delivery_time', v)}
                                    placeholder="напр. 09:00"
                                />
                                {formOptions.timeslots.length > 0 ? (
                                    <SelectField
                                        label="Таймслот"
                                        value={form.daily_delivery_timeslot}
                                        onChange={v => set('daily_delivery_timeslot', v)}
                                        options={formOptions.timeslots}
                                        placeholder="— выберите —"
                                    />
                                ) : (
                                    <TextField
                                        label="Таймслот"
                                        value={form.daily_delivery_timeslot}
                                        onChange={v => set('daily_delivery_timeslot', v)}
                                        placeholder="Таймслот"
                                    />
                                )}
                            </div>
                            {plan && (
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 8 }}>
                                    График склада: погрузка {formatAllowedDays(plan.loading_days)} · доставка{' '}
                                    {formatAllowedDays(plan.delivery_days)} · в пути {formatNumber(plan.eta_days, 0)} дн.
                                </div>
                            )}
                            {isMarketplace && !plan && form.delivery_address && (
                                <div style={{ fontSize: 12, color: 'var(--color-danger)', marginTop: 8 }}>
                                    Это направление не обслуживается из выбранного города — выберите другой склад или прайс-лист.
                                </div>
                            )}
                        </div>

                        {/* ── Контакты ── */}
                        <div style={{ marginBottom: 20 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                                Контакты
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                                <TextField
                                    label="Контактное лицо"
                                    value={form.delivery_contact}
                                    onChange={v => set('delivery_contact', v)}
                                    placeholder="ФИО"
                                />
                                <TextField
                                    label="Телефон"
                                    value={form.customer_phone}
                                    onChange={v => set('customer_phone', v)}
                                    placeholder="+7..."
                                />
                            </div>
                        </div>

                        {/* ── Груз ── */}
                        <div style={{ marginBottom: 20 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                                Параметры груза
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
                                <NumberField
                                    label="Паллет"
                                    value={form.pallets}
                                    onChange={v => set('pallets', v === '' ? 0 : v as number)}
                                    min={0}
                                />
                                <NumberField
                                    label="Коробов"
                                    value={form.boxes}
                                    onChange={v => set('boxes', v === '' ? 0 : v as number)}
                                    min={0}
                                />
                                <TextField
                                    label={`Вес (кг)${form.weight ? ` · ${formatNumber(Number(form.weight), 1)} кг` : ''}`}
                                    value={form.weight}
                                    onChange={v => set('weight', v)}
                                    type="number"
                                    placeholder="кг"
                                />
                                <TextField
                                    label="Вес 2 (кг)"
                                    value={form.weight2}
                                    onChange={v => set('weight2', v)}
                                    type="number"
                                    placeholder="кг"
                                />
                                <TextField
                                    label="Объём (м³)"
                                    value={form.volume}
                                    onChange={v => set('volume', v)}
                                    type="number"
                                    placeholder="м³"
                                />
                                <TextField
                                    label="Номер поставки WB"
                                    value={form.supply_id}
                                    onChange={v => set('supply_id', v)}
                                    placeholder="WB-..."
                                />
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginTop: 12 }}>
                                <NumberField
                                    label="Длина (см)"
                                    value={form.length}
                                    onChange={v => set('length', v)}
                                    min={0}
                                />
                                <NumberField
                                    label="Высота (см)"
                                    value={form.height}
                                    onChange={v => set('height', v)}
                                    min={0}
                                />
                                <NumberField
                                    label="Ширина (см)"
                                    value={form.width}
                                    onChange={v => set('width', v)}
                                    min={0}
                                />
                            </div>
                            <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
                                <input
                                    type="checkbox"
                                    id="gazelka-palleting"
                                    checked={form.palleting}
                                    onChange={e => set('palleting', e.target.checked)}
                                    style={{ width: 16, height: 16, accentColor: 'var(--color-accent)', cursor: 'pointer' }}
                                />
                                <label htmlFor="gazelka-palleting" style={{ fontSize: 14, cursor: 'pointer' }}>
                                    Паллетирование
                                </label>
                            </div>
                        </div>

                        {/* ── Примечания ── */}
                        <div style={{ marginBottom: 20 }}>
                            <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)', display: 'block', marginBottom: 4 }}>
                                Примечания
                            </label>
                            <textarea
                                value={form.notes}
                                onChange={e => set('notes', e.target.value)}
                                rows={3}
                                placeholder="Дополнительные комментарии..."
                                style={{
                                    width: '100%', padding: '8px 10px',
                                    borderRadius: 8, border: '1px solid var(--color-border)',
                                    background: 'var(--color-bg-card)', color: 'var(--color-text)',
                                    fontSize: 14, resize: 'vertical', boxSizing: 'border-box',
                                }}
                            />
                        </div>

                    </>
                )}
                </div>{/* /body */}

                {/* Footer — липкий, кнопки всегда видны */}
                {!loadingDraft && !draftError && (isEditMode ? !!editDraft : draft?.eligible) && form && (
                    <div style={{
                        padding: '14px 24px', borderTop: '1px solid var(--color-border)',
                        flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 10,
                    }}>
                        {(validationError || submitError) && (
                            <div style={{ padding: '8px 12px', borderRadius: 8, background: 'rgba(239,68,68,0.1)', color: 'var(--color-danger)', fontSize: 13 }}>
                                {validationError || submitError}
                            </div>
                        )}
                        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={onClose} disabled={submitting}>
                                Отмена
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={handleSubmit}
                                disabled={submitting}
                            >
                                {submitting
                                    ? (isEditMode ? 'Сохранение...' : 'Отправка...')
                                    : (isEditMode ? 'Сохранить' : 'Отправить в Газельку')
                                }
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
