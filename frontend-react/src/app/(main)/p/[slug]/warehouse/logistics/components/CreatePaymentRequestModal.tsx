'use client';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate, formatDateTime, formatNumber } from '@/lib/utils';
import type {
    PaymentRequestDetail,
    PaymentRequestStatus,
    PaymentRequestCategory,
    PaymentCategory,
    ShippableShipmentRow,
    InvoiceParseResult,
} from '@/types/api';

const STATUS_LABEL: Record<PaymentRequestStatus, string> = {
    DRAFT: 'Черновик',
    PENDING_REVIEW: 'На проверке',
    APPROVED: 'Согласовано',
    DRAFT_CREATED: 'Платёжка создана',
    PAID: 'Оплачено',
    REJECTED: 'Отклонено',
    CANCELLED: 'Отменено',
};

const STATUS_CLASS: Record<PaymentRequestStatus, string> = {
    DRAFT: 'badge-secondary',
    PENDING_REVIEW: 'badge-warning',
    APPROVED: 'badge-info',
    DRAFT_CREATED: 'badge-info',
    PAID: 'badge-success',
    REJECTED: 'badge-danger',
    CANCELLED: 'badge-secondary',
};

// Фолбэк-лейблы/набор системных кодов — пока справочник не загрузился с API.
const CATEGORY_LABEL: Record<string, string> = {
    LOGISTICS: 'Логистика',
    PHOTO_CONTENT: 'Фотоконтент',
    CUSTOMS: 'Таможенное оформление',
    FULFILLMENT: 'Фулфилмент',
    DESIGN: 'Дизайн',
    HOUSEHOLD: 'Хозрасходы',
    OTHER: 'Другое',
};
const CATEGORY_OPTIONS_FALLBACK: PaymentRequestCategory[] = ['PHOTO_CONTENT', 'DESIGN', 'FULFILLMENT', 'CUSTOMS', 'LOGISTICS', 'HOUSEHOLD', 'OTHER'];

interface Props {
    /** Pre-selected outbound_shipment_id — when opened from a SHIPPED row */
    initialShipmentId?: number;
    /** Несколько заборов на одну оплату (один счёт за N отгрузок одного перевозчика) */
    initialShipmentIds?: number[];
    /** Open an existing request to view (read-only) or edit (DRAFT) */
    editRequestId?: number;
    /** Уже распознанный счёт (загружен на листе логиста) — добить пустые реквизиты без повторного парса. */
    prefillParse?: InvoiceParseResult | null;
    /** Файл счёта для авто-прикрепления как «Счёт» на шаге документов. */
    prefillFile?: File | null;
    onClose: () => void;
    onSuccess?: (detail: PaymentRequestDetail) => void;
}

type Mode = 'COUNTERPARTY' | 'MANUAL';
type Step = 'form' | 'docs' | 'done';

export default function CreatePaymentRequestModal({ initialShipmentId, initialShipmentIds, editRequestId, prefillParse, prefillFile, onClose, onSuccess }: Props) {
    const isEdit = editRequestId != null;
    const isMultiProp = (initialShipmentIds?.length ?? 0) > 0;
    const [mode, setMode] = useState<Mode>(initialShipmentId || isMultiProp ? 'COUNTERPARTY' : 'MANUAL');
    const [step, setStep] = useState<Step>('form');
    const [editLoading, setEditLoading] = useState(isEdit);
    const [readOnly, setReadOnly] = useState(false);

    // ─── Shippable picker ────────────────────────────────────────────────
    const [shippable, setShippable] = useState<ShippableShipmentRow[]>([]);
    const [shippableLoading, setShippableLoading] = useState(false);
    const [shipSearch, setShipSearch] = useState('');
    const [selectedShipment, setSelectedShipment] = useState<ShippableShipmentRow | null>(null);
    // Мульти-режим: несколько заборов на одну оплату.
    const [multiShipments, setMultiShipments] = useState<ShippableShipmentRow[]>([]);
    // Picker hidden when opened from a specific row — заявка привязана к той отгрузке.
    const [showPicker, setShowPicker] = useState(!initialShipmentId && !isMultiProp);

    // ─── Manual form ─────────────────────────────────────────────────────
    const [payeeInn, setPayeeInn] = useState('');
    const [payeeName, setPayeeName] = useState('');
    const [payeeAccount, setPayeeAccount] = useState('');
    const [payeeBik, setPayeeBik] = useState('');
    const [payeeBankName, setPayeeBankName] = useState('');
    const [payeeCorrAccount, setPayeeCorrAccount] = useState('');
    const [payeeKpp, setPayeeKpp] = useState('');
    const [amount, setAmount] = useState('');
    const [pickupDate, setPickupDate] = useState('');
    const [purpose, setPurpose] = useState('');

    // ─── Назначение оплаты + проект (свободная заявка, не из листа логиста) ───────
    const isShipmentContext = initialShipmentId != null || isMultiProp;
    const [category, setCategory] = useState<PaymentRequestCategory>(isShipmentContext ? 'LOGISTICS' : 'OTHER');
    // projectSel: id проекта | 'none' (без проекта) | '' (текущий — до загрузки списка).
    const [projectSel, setProjectSel] = useState<string>('');
    const [projects, setProjects] = useState<Array<{ id: number; name: string; slug: string }>>([]);
    // Бренд-атрибуция (свободная заявка/правка) — список как в План-Факте; '' = «Все бренды».
    const [brands, setBrands] = useState<string[]>([]);
    const [brandSel, setBrandSel] = useState<string>('');
    const params = useParams();
    const slug = typeof params?.slug === 'string' ? params.slug : Array.isArray(params?.slug) ? params.slug[0] : '';

    // ─── Справочник «Назначение оплаты» (динамический список для выпадающего меню) ──
    const [categories, setCategories] = useState<PaymentCategory[]>([]);
    useEffect(() => {
        let aborted = false;
        (async () => {
            try { const cs = await api.listPaymentCategories(); if (!aborted) setCategories(cs); }
            catch { /* справочник недоступен — останутся фолбэк-категории */ }
        })();
        return () => { aborted = true; };
    }, []);
    // Опции селекта; текущее значение гарантированно присутствует (удалённая кастомная при правке).
    const categoryOptions = useMemo(() => {
        const base = categories.length
            ? categories.map(c => ({ code: c.code, label: c.label }))
            : CATEGORY_OPTIONS_FALLBACK.map(c => ({ code: c, label: CATEGORY_LABEL[c] }));
        if (category && !base.some(o => o.code === category)) {
            base.unshift({ code: category, label: CATEGORY_LABEL[category] ?? category });
        }
        return base;
    }, [categories, category]);
    const catLabelOf = (code: string | null | undefined): string =>
        (code ? (categories.find(c => c.code === code)?.label ?? CATEGORY_LABEL[code] ?? code) : '');

    // Бренды проекта для атрибуции (источник — План-Факт/wb-brands). Свободная заявка/правка.
    useEffect(() => {
        if (isShipmentContext) return;
        let aborted = false;
        (async () => {
            try {
                const b = await api.getWbBrands();
                if (!aborted) setBrands(b.filter(n => n !== 'Неопознанный Товар'));
            } catch { /* бренды недоступны — селект скрыт */ }
        })();
        return () => { aborted = true; };
    }, [isShipmentContext]);
    // Сохранённый бренд гарантированно среди опций (при правке — даже если он исчез из продаж).
    const brandOptions = useMemo(() => {
        const list = brands.slice();
        if (brandSel && !list.includes(brandSel)) list.unshift(brandSel);
        return list;
    }, [brands, brandSel]);

    // Список проектов для выбора (только для свободной заявки). Дефолт projectSel='' = текущий проект.
    useEffect(() => {
        if (isShipmentContext || isEdit) return;
        let aborted = false;
        (async () => {
            try {
                const ps = await api.getProjects();
                if (aborted) return;
                setProjects(ps.map(p => ({ id: p.id, name: p.name, slug: p.slug })));
            } catch { /* список проектов недоступен — останутся «текущий» и «без проекта» */ }
        })();
        return () => { aborted = true; };
    }, [isShipmentContext, isEdit]);

    // ─── Created request ──────────────────────────────────────────────────
    const [created, setCreated] = useState<PaymentRequestDetail | null>(null);
    const [invoiceFile, setInvoiceFile] = useState<File | null>(null);
    const [actFile, setActFile] = useState<File | null>(null);
    const invoiceRef = useRef<HTMLInputElement>(null);
    const actRef = useRef<HTMLInputElement>(null);

    // ─── Распознавание счёта (опциональный помощник: PDF/Word → авто-заполнение реквизитов) ───
    const [parsing, setParsing] = useState(false);
    const [parseInfo, setParseInfo] = useState<string>('');
    const invoiceParseRef = useRef<HTMLInputElement>(null);

    // ─── Status & errors ─────────────────────────────────────────────────
    const [creating, setCreating] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [error, setError] = useState('');
    const [missingReqs, setMissingReqs] = useState<string[]>([]);
    const [liveStatus, setLiveStatus] = useState<PaymentRequestStatus | null>(null);
    const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // ─── Load shippable list ─────────────────────────────────────────────
    const autoPickedRef = useRef(false);
    const loadShippable = useCallback(async (q: string) => {
        setShippableLoading(true);
        try {
            // When opened from a row (single or multi), don't exclude already-requested ones — the target must be findable.
            const lockedToRows = initialShipmentId != null || isMultiProp;
            const rows = await api.listShippable(q || undefined, lockedToRows ? undefined : false);
            setShippable(rows);
            // Auto-pick pre-selected shipment(s) on first load only; if not found, reveal the picker.
            if (!autoPickedRef.current && initialShipmentId) {
                autoPickedRef.current = true;
                const found = rows.find(r => r.outbound_shipment_id === initialShipmentId);
                if (found) setSelectedShipment(found);
                else setShowPicker(true);
            } else if (!autoPickedRef.current && isMultiProp) {
                autoPickedRef.current = true;
                const idSet = new Set(initialShipmentIds);
                const picked = rows.filter(r => idSet.has(r.outbound_shipment_id));
                if (picked.length > 0) {
                    setMultiShipments(picked);
                    setSelectedShipment(picked[0]);  // prefill requisites from the (shared) carrier
                } else {
                    setShowPicker(true);  // ничего не нашли — дать выбрать вручную
                }
            }
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки отгрузок');
        }
        setShippableLoading(false);
    }, [initialShipmentId, isMultiProp, initialShipmentIds]);

    useEffect(() => {
        if (mode === 'COUNTERPARTY') loadShippable('');
    }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        if (mode !== 'COUNTERPARTY') return;
        const t = setTimeout(() => loadShippable(shipSearch), 300);
        return () => clearTimeout(t);
    }, [shipSearch]); // eslint-disable-line react-hooks/exhaustive-deps

    // ─── Prefill requisites + amount when a shipment is picked ───────────
    // Carrier data may be partially empty (банковские реквизиты вносятся вручную
    // и запоминаются на контрагенте для следующего раза).
    useEffect(() => {
        if (mode !== 'COUNTERPARTY' || !selectedShipment) return;
        const s = selectedShipment;
        setAmount(s.pickup_cost != null ? String(s.pickup_cost) : '');
        setPayeeInn(s.carrier_inn || '');
        setPayeeName(s.carrier_name || '');
        setPayeeAccount(s.carrier_bank_account || '');
        setPayeeBik(s.carrier_bik || '');
        setPayeeBankName(s.carrier_bank_name || '');
        setPayeeCorrAccount(s.carrier_corr_account || '');
        setPayeeKpp(s.carrier_kpp || '');
    }, [selectedShipment, mode]);

    // ─── Multi-режим: сумма заявки = Σ стоимостей заборов (один счёт за все) ──────
    // Объявлен ПОСЛЕ prefill-эффекта выше — выполняется следом и перетирает amount,
    // который тот выставил по первому забору, на итоговую сумму всех.
    useEffect(() => {
        if (mode !== 'COUNTERPARTY' || multiShipments.length === 0) return;
        const total = multiShipments.reduce(
            (acc, s) => acc + (s.pickup_cost != null ? Number(s.pickup_cost) : 0), 0,
        );
        setAmount(total.toFixed(2));  // .toFixed — чтобы на провод не уходил float-артефакт (0.1+0.2)
    }, [multiShipments, mode]);

    // ─── Poll status after submit ─────────────────────────────────────────
    const startPolling = useCallback((id: number) => {
        const tick = async () => {
            try {
                const s = await api.getPaymentRequestStatus(id);
                setLiveStatus(s.status);
                if (s.status === 'PENDING_REVIEW' || s.status === 'DRAFT_CREATED' || s.status === 'PAID') {
                    // terminal for our flow: stop polling
                    return;
                }
                pollRef.current = setTimeout(tick, 5000);
            } catch {
                // ignore poll errors
            }
        };
        pollRef.current = setTimeout(tick, 5000);
    }, []);

    useEffect(() => () => { if (pollRef.current) clearTimeout(pollRef.current); }, []);

    // ─── Load existing request for view/edit ──────────────────────────────
    useEffect(() => {
        if (editRequestId == null) return;
        (async () => {
            setEditLoading(true);
            try {
                const d = await api.getPaymentRequest(editRequestId);
                setCreated(d);
                setMode('MANUAL');
                setPayeeInn(d.payee_inn || '');
                setPayeeName(d.payee_name || '');
                setPayeeAccount(d.payee_account || '');
                setPayeeBik(d.payee_bik || '');
                setPayeeBankName(d.payee_bank_name || '');
                setPayeeCorrAccount(d.payee_corr_account || '');
                setPayeeKpp(d.payee_kpp || '');
                setAmount(d.amount != null ? String(d.amount) : '');
                setPickupDate(d.pickup_date || '');
                setPurpose(d.purpose || '');
                setCategory(d.category ?? 'OTHER');  // даём поправить ошибочно выбранное «Назначение»
                setBrandSel(d.brand ?? '');           // бренд можно поправить при правке заявки
                setLiveStatus(d.status);
                setReadOnly(d.status !== 'DRAFT' && d.status !== 'PENDING_REVIEW');  // редактировать можно до создания платёжки в банке
                setStep('form');
            } catch (e: unknown) {
                setError(e instanceof Error ? e.message : 'Ошибка загрузки заявки');
            }
            setEditLoading(false);
        })();
    }, [editRequestId]);

    const reloadCreated = useCallback(async (id: number) => {
        try { setCreated(await api.getPaymentRequest(id)); } catch { /* ignore */ }
    }, []);

    // ─── Авто-подстановка корр. счёта + банка по БИК (банк требует к/с; вводить вручную лень) ──
    const handleBikBlur = useCallback(async () => {
        const bik = payeeBik.trim();
        if (!/^\d{9}$/.test(bik) || payeeCorrAccount.trim()) return;  // не перетираем введённое
        try {
            const bank = await api.getBankByBic(bik);
            setPayeeCorrAccount(prev => prev.trim() ? prev : bank.corr_account);
            setPayeeBankName(prev => prev.trim() ? prev : bank.name);
        } catch { /* банка нет в справочнике — к/с вводится вручную */ }
    }, [payeeBik, payeeCorrAccount]);

    // ─── Применить распознанный счёт к форме (общее для ручной загрузки и предзаполнения с листа) ──
    // В потоке логиста (заявка из отгрузки) имя/ИНН/реквизиты перевозчика уже подставлены —
    // распознанное ДОБИВАЕТ только пустые поля (fillEmptyOnly), чтобы не затереть данные контрагента.
    const applyParsed = useCallback((r: InvoiceParseResult, fillEmptyOnly: boolean) => {
        const fill = (val: string | null | undefined, setter: (v: (p: string) => string) => void) => {
            if (!val) return;
            setter(prev => (fillEmptyOnly && prev.trim()) ? prev : val);
        };
        fill(r.payee_name, setPayeeName);
        fill(r.payee_inn, setPayeeInn);
        fill(r.payee_account, setPayeeAccount);
        fill(r.payee_bik, setPayeeBik);
        fill(r.payee_bank_name, setPayeeBankName);
        fill(r.payee_corr_account, setPayeeCorrAccount);
        fill(r.payee_kpp, setPayeeKpp);
        fill(r.amount != null ? String(r.amount) : null, setAmount);
        fill(r.purpose, setPurpose);
        const FIELD_RU: Record<string, string> = { payee_inn: 'ИНН', payee_bik: 'БИК', payee_account: 'р/с', payee_kpp: 'КПП', payee_name: 'получатель', amount: 'сумма', purpose: 'назначение', payee_corr_account: 'корр.счёт', payee_bank_name: 'банк' };
        const found = r.fields_found.map(f => FIELD_RU[f] ?? f).join(', ');
        setParseInfo(found ? `Распознано: ${found}.` + (r.warnings.length ? ' ⚠ ' + r.warnings.join('; ') : '') : (r.warnings.join('; ') || 'Не удалось распознать — заполните вручную'));
    }, []);

    // ─── Загрузить счёт → распознать реквизиты (бэкенд парсит PDF/Word/фото, фронт только подставляет) ──
    const handleParseInvoice = async (file: File) => {
        setParsing(true); setError(''); setParseInfo('');
        try {
            const r = await api.parseInvoice(file);
            applyParsed(r, isShipmentContext);
            setInvoiceFile(file);  // тот же файл прикрепится как Счёт на шаге 2 — не грузить дважды
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка распознавания счёта');
        }
        setParsing(false);
    };

    // ─── Предзаполнение уже распознанным счётом (загружен на листе логиста) — после prefill из забора. ──
    const prefillDoneRef = useRef(false);
    useEffect(() => {
        if (!prefillParse || prefillDoneRef.current) return;
        // дождаться prefill реквизитов из выбранного забора, иначе пустые поля затрутся carrier-эффектом
        if (initialShipmentId != null && !selectedShipment) return;
        if (isMultiProp && multiShipments.length === 0) return;
        prefillDoneRef.current = true;
        applyParsed(prefillParse, true);
        if (prefillFile) setInvoiceFile(prefillFile);
    }, [prefillParse, prefillFile, selectedShipment, multiShipments, initialShipmentId, isMultiProp, applyParsed]);

    // ─── Client-side requisites validation (понятные сообщения вместо сырого 422) ──
    const validateRequisites = (): string | null => {
        if (!payeeName.trim()) return 'Укажите наименование получателя';
        if (!/^\d{10,12}$/.test(payeeInn.trim())) return 'ИНН должен содержать 10 или 12 цифр';
        if (!/^\d{20}$/.test(payeeAccount.trim())) return 'Расчётный счёт должен содержать ровно 20 цифр';
        if (!/^\d{9}$/.test(payeeBik.trim())) return 'БИК должен содержать ровно 9 цифр';
        if (payeeCorrAccount.trim() && !/^\d{20}$/.test(payeeCorrAccount.trim())) return 'Корр. счёт должен содержать 20 цифр';
        if (payeeKpp.trim() && !/^\d{9}$/.test(payeeKpp.trim())) return 'КПП должен содержать 9 цифр';
        if (!amount || Number(amount) <= 0) return 'Укажите сумму больше 0';
        return null;
    };

    // ─── Step 1: create the request ───────────────────────────────────────
    const handleCreate = async () => {
        setError('');
        if (mode === 'COUNTERPARTY' && !selectedShipment) { setError('Выберите отгрузку'); return; }
        const vErr = validateRequisites();
        if (vErr) { setError(vErr); return; }
        // Назначение + проект — только для свободной заявки (не из листа логиста, не при правке).
        const extra: { category?: PaymentRequestCategory; project_id?: number | null; brand?: string } = {};
        if (!isShipmentContext && !isEdit) {
            extra.category = category;
            if (category !== 'LOGISTICS') {
                if (projectSel === 'none') extra.project_id = null;
                else if (projectSel && !Number.isNaN(Number(projectSel))) extra.project_id = Number(projectSel);
            }
            if (brandSel) extra.brand = brandSel;  // '' = «Все бренды» → не отправляем (остаётся NULL)
        }
        setCreating(true);
        try {
            let detail: PaymentRequestDetail;
            if (isEdit && created) {
                detail = await api.updatePaymentRequest(created.id, {
                    category,  // даём исправить ошибочно выбранное «Назначение» (бэкенд PATCH это принимает)
                    brand: brandSel || null,  // '' → сбросить в «Все бренды»; иначе тег бренда
                    payee_inn: payeeInn || undefined,
                    payee_name: payeeName || undefined,
                    payee_account: payeeAccount || undefined,
                    payee_bik: payeeBik || undefined,
                    payee_bank_name: payeeBankName || undefined,
                    payee_corr_account: payeeCorrAccount || undefined,
                    payee_kpp: payeeKpp || undefined,
                    amount: amount || undefined,
                    pickup_date: pickupDate || undefined,
                    purpose: purpose || undefined,
                });
            } else if (mode === 'COUNTERPARTY') {
                // Requisites entered/edited in the form (override + remembered on the carrier)
                const requisites = {
                    payee_inn: payeeInn || undefined,
                    payee_name: payeeName || undefined,
                    payee_account: payeeAccount || undefined,
                    payee_bik: payeeBik || undefined,
                    payee_bank_name: payeeBankName || undefined,
                    payee_corr_account: payeeCorrAccount || undefined,
                    payee_kpp: payeeKpp || undefined,
                    amount: amount || undefined,
                    pickup_date: pickupDate || undefined,
                    purpose: purpose || undefined,
                } as const;
                if (multiShipments.length > 0) {
                    // Одна заявка/счёт за N заборов одного перевозчика (назначение строит бэкенд).
                    detail = await api.createPaymentRequest({
                        source: 'COUNTERPARTY',
                        outbound_shipment_ids: multiShipments.map(s => s.outbound_shipment_id),
                        counterparty_id: multiShipments[0].counterparty_id ?? undefined,
                        ...requisites,
                    });
                } else {
                    if (!selectedShipment) { setError('Выберите отгрузку'); setCreating(false); return; }
                    detail = await api.createPaymentRequest({
                        source: 'COUNTERPARTY',
                        outbound_shipment_id: selectedShipment.outbound_shipment_id,
                        counterparty_id: selectedShipment.counterparty_id ?? undefined,
                        ...requisites,
                    });
                }
            } else {
                detail = await api.createPaymentRequest({
                    source: 'MANUAL',
                    ...extra,
                    payee_inn: payeeInn || undefined,
                    payee_name: payeeName || undefined,
                    payee_account: payeeAccount || undefined,
                    payee_bik: payeeBik || undefined,
                    payee_bank_name: payeeBankName || undefined,
                    payee_corr_account: payeeCorrAccount || undefined,
                    payee_kpp: payeeKpp || undefined,
                    amount: amount || undefined,
                    pickup_date: pickupDate || undefined,
                    purpose: purpose || undefined,
                });
            }
            setCreated(detail);
            setStep('docs');
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : '';
            // 422 с перечнем незаполненных реквизитов → показываем списком.
            try {
                const parsed = JSON.parse(msg);
                if (Array.isArray(parsed)) { setMissingReqs(parsed); setCreating(false); return; }
                if (parsed?.missing && Array.isArray(parsed.missing)) { setMissingReqs(parsed.missing); setCreating(false); return; }
            } catch { /* not JSON */ }
            setError(msg || 'Ошибка создания');
        }
        setCreating(false);
    };

    // ─── Step 2: upload docs (опционально) + финализация ─────────────────
    // Заявка создаётся сразу «На проверке» — отдельной передачи в оплату нет.
    const handleUploadDocs = async () => {
        if (!created) return;
        setError('');
        // Для не-логистических заявок счёт обязателен.
        const requireInvoice = !isShipmentContext && category !== 'LOGISTICS';
        const hasInvoice = !!invoiceFile || created.documents.some(d => d.doc_type === 'INVOICE');
        if (requireInvoice && !hasInvoice) { setError('Приложите файл счёта'); return; }
        setUploading(true);
        try {
            if (invoiceFile) await api.uploadPaymentRequestDocument(created.id, invoiceFile, 'INVOICE');
            if (actFile) await api.uploadPaymentRequestDocument(created.id, actFile, 'ACT');
            const detail = await api.getPaymentRequest(created.id);
            setCreated(detail);
            setLiveStatus(detail.status);
            setStep('done');
            startPolling(created.id);
            onSuccess?.(detail);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки документов');
        }
        setUploading(false);
    };

    const displayStatus = liveStatus ?? created?.status;

    // Read-only детали существующей не-черновой заявки.
    const roRow = (l: string, v: string | null | undefined) => v ? (
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '4px 0', borderBottom: '1px solid var(--color-border)', fontSize: 13 }}>
            <span style={{ color: 'var(--color-text-muted)' }}>{l}</span>
            <span style={{ textAlign: 'right', wordBreak: 'break-all' }}>{v}</span>
        </div>
    ) : null;

    const renderReadOnly = () => {
        if (!created) return null;
        return (
            <div>
                <div style={{ marginBottom: 12 }}>
                    <span className={`badge ${STATUS_CLASS[created.status]}`}>{STATUS_LABEL[created.status]}</span>
                </div>
                {roRow('Получатель', created.payee_name)}
                {roRow('ИНН', created.payee_inn)}
                {roRow('КПП', created.payee_kpp)}
                {roRow('Расчётный счёт', created.payee_account)}
                {roRow('БИК', created.payee_bik)}
                {roRow('Банк', created.payee_bank_name)}
                {roRow('Сумма', created.amount ? `${formatNumber(Number(created.amount), 2)} ${created.currency}` : null)}
                {roRow('Дата забора', created.pickup_date ? formatDate(created.pickup_date) : null)}
                {roRow('Категория', created.category ? catLabelOf(created.category) : null)}
                {roRow('Бренд', created.brand)}
                {roRow('Назначение', created.purpose)}
                {created.bank_doc_id && roRow('ID платёжки', created.bank_doc_id)}
                {created.documents.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                        <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 13 }}>Документы</div>
                        {created.documents.map(d => (
                            <div key={d.id} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', fontSize: 13 }}>
                                <span>{d.doc_type === 'INVOICE' ? 'Счёт' : 'Акт'} · {d.original_filename ?? 'файл'}</span>
                                <a href={api.paymentRequestDocumentDownloadUrl(created.id, d.id)} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--color-accent)' }}>Скачать</a>
                            </div>
                        ))}
                    </div>
                )}
                {created.events.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                        <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 13 }}>История</div>
                        {created.events.map(ev => (
                            <div key={ev.id} style={{ display: 'flex', gap: 8, fontSize: 12, marginBottom: 2, alignItems: 'center' }}>
                                <span style={{ color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>{formatDateTime(ev.changed_at)}</span>
                                <span className={`badge ${STATUS_CLASS[ev.new_status]}`} style={{ fontSize: 10 }}>{STATUS_LABEL[ev.new_status]}</span>
                                {ev.comment && <span style={{ color: 'var(--color-text-muted)' }}>{ev.comment}</span>}
                            </div>
                        ))}
                    </div>
                )}
            </div>
        );
    };

    // Shared requisites grid — used by both «Выбрать отгрузку» (pre-filled) and «Ввести вручную».
    const requisitesGrid = (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
            {/* Распознавание из файла/фото счёта — доступно во всех потоках создания (в т.ч. у логиста);
                в потоке отгрузки добивает только пустые реквизиты перевозчика (см. handleParseInvoice). */}
            {!isEdit && (
                <div style={{ gridColumn: '1 / -1' }}>
                    <button type="button" className="btn btn-secondary btn-sm" onClick={() => invoiceParseRef.current?.click()} disabled={parsing}>
                        {parsing ? 'Распознаю...' : '📄 Загрузить счёт или фото → распознать реквизиты'}
                    </button>
                    <input ref={invoiceParseRef} type="file" accept=".pdf,.doc,.docx,.jpg,.jpeg,.png,.webp,.heic" style={{ display: 'none' }}
                        onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) handleParseInvoice(f); }} />
                    {parseInfo && <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 6 }}>{parseInfo}</div>}
                </div>
            )}
            <div className="form-group" style={{ margin: 0 }}>
                <label className="form-label">Наименование получателя *</label>
                <input className="form-input" value={payeeName} onChange={e => setPayeeName(e.target.value)} placeholder="ООО «Транспорт»" />
            </div>
            <div className="form-group" style={{ margin: 0 }}>
                <label className="form-label">ИНН *</label>
                <input className="form-input" value={payeeInn} onChange={e => setPayeeInn(e.target.value)} placeholder="10 или 12 цифр" maxLength={12} />
            </div>
            <div className="form-group" style={{ margin: 0 }}>
                <label className="form-label">Расчётный счёт *</label>
                <input className="form-input" value={payeeAccount} onChange={e => setPayeeAccount(e.target.value)} placeholder="20 цифр (40702810...)" maxLength={20} />
            </div>
            <div className="form-group" style={{ margin: 0 }}>
                <label className="form-label">БИК *</label>
                <input className="form-input" value={payeeBik} onChange={e => setPayeeBik(e.target.value)} onBlur={handleBikBlur} placeholder="9 цифр" maxLength={9} />
            </div>
            <div className="form-group" style={{ margin: 0, gridColumn: '1 / -1' }}>
                <label className="form-label">Наименование банка</label>
                <input className="form-input" value={payeeBankName} onChange={e => setPayeeBankName(e.target.value)} placeholder="Сбербанк / ВТБ..." />
            </div>
            <div className="form-group" style={{ margin: 0 }}>
                <label className="form-label">Корр. счёт</label>
                <input className="form-input" value={payeeCorrAccount} onChange={e => setPayeeCorrAccount(e.target.value)} placeholder="30101810..." maxLength={20} />
            </div>
            <div className="form-group" style={{ margin: 0 }}>
                <label className="form-label">КПП</label>
                <input className="form-input" value={payeeKpp} onChange={e => setPayeeKpp(e.target.value)} placeholder="9 цифр" maxLength={9} />
            </div>
            <div className="form-group" style={{ margin: 0 }}>
                <label className="form-label">Сумма (₽) *</label>
                <input className="form-input" type="number" min={0} value={amount} onChange={e => setAmount(e.target.value)} placeholder="0.00" />
            </div>
            {/* «Дата забора» — поле логистики; для фото/дизайна/таможни и пр. оно не нужно. */}
            {(isShipmentContext || category === 'LOGISTICS') && (
                <div className="form-group" style={{ margin: 0 }}>
                    <label className="form-label">Дата забора</label>
                    <input className="form-input" type="date" value={pickupDate} onChange={e => setPickupDate(e.target.value)} />
                </div>
            )}
            <div className="form-group" style={{ margin: 0, gridColumn: '1 / -1' }}>
                <label className="form-label">Назначение платежа</label>
                <input className="form-input" value={purpose} onChange={e => setPurpose(e.target.value)} placeholder="Транспортные услуги по договору..." />
            </div>
        </div>
    );

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div
                className="modal-card"
                onClick={e => e.stopPropagation()}
                style={{ maxWidth: 680, width: '95vw', maxHeight: '90vh', overflow: 'auto', background: '#fff' }}
            >
                <h2 className="modal-title">
                    {isEdit ? `Заявка ${created?.number ?? ''}` : 'Заявка на оплату'}
                </h2>

                {editLoading && (
                    <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка заявки...</div>
                )}
                {isEdit && !created && !editLoading && error && (
                    <div style={{ padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.1)', color: 'var(--color-danger)', fontSize: 13 }}>{error}</div>
                )}
                {readOnly && created && !editLoading && (
                    <>
                        {renderReadOnly()}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={onClose}>Закрыть</button>
                        </div>
                    </>
                )}

                {!editLoading && !readOnly && (!isEdit || created) && (<>
                {/* ─── Step indicator ─── */}
                <div style={{ display: 'flex', gap: 0, marginBottom: 20 }}>
                    {(['form', 'docs', 'done'] as Step[]).map((s, i) => (
                        <div
                            key={s}
                            style={{
                                flex: 1,
                                textAlign: 'center',
                                padding: '6px 0',
                                fontSize: 13,
                                fontWeight: step === s ? 600 : 400,
                                background: step === s ? 'var(--color-accent)' : 'var(--color-bg)',
                                color: step === s ? '#fff' : 'var(--color-text-muted)',
                                borderRadius: i === 0 ? '8px 0 0 8px' : i === 2 ? '0 8px 8px 0' : 0,
                            }}
                        >
                            {i + 1}. {s === 'form' ? 'Реквизиты' : s === 'docs' ? 'Документы' : 'Готово'}
                        </div>
                    ))}
                </div>

                {/* ─── Error banner ─── */}
                {error && (
                    <div style={{ padding: '10px 14px', marginBottom: 12, borderRadius: 8, background: 'var(--color-danger-bg, rgba(239,68,68,0.1))', color: 'var(--color-danger)', fontSize: 13 }}>
                        {error}
                    </div>
                )}
                {missingReqs.length > 0 && (
                    <div style={{ padding: '10px 14px', marginBottom: 12, borderRadius: 8, background: 'rgba(239,68,68,0.08)', fontSize: 13, color: 'var(--color-danger)' }}>
                        <div style={{ fontWeight: 600, marginBottom: 4 }}>Не заполнено для передачи в оплату:</div>
                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                            {missingReqs.map((r, i) => <li key={i}>{r}</li>)}
                        </ul>
                    </div>
                )}

                {/* ══════════ STEP 1: FORM ══════════ */}
                {step === 'form' && (
                    <>
                        {/* Назначение оплаты + проект — для свободной заявки и при правке (проект меняем
                            только при создании: бэкенд PATCH категорию принимает, project_id — нет). */}
                        {!isShipmentContext && (
                        <div style={{ display: 'grid', gridTemplateColumns: category !== 'LOGISTICS' && !isEdit ? '1fr 1fr' : '1fr', gap: 12, marginBottom: 16 }}>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">Назначение оплаты *</label>
                                <select
                                    className="form-input"
                                    value={category}
                                    onChange={e => {
                                        const v = e.target.value as PaymentRequestCategory;
                                        setCategory(v);
                                        // Не-логистика не привязывается к отгрузке → только ручной ввод.
                                        if (v !== 'LOGISTICS') { setMode('MANUAL'); setSelectedShipment(null); setMultiShipments([]); }
                                    }}
                                >
                                    {categoryOptions.map(o => <option key={o.code} value={o.code}>{o.label}</option>)}
                                </select>
                            </div>
                            {category !== 'LOGISTICS' && !isEdit && (
                                <div className="form-group" style={{ margin: 0 }}>
                                    <label className="form-label">Проект</label>
                                    <select className="form-input" value={projectSel} onChange={e => setProjectSel(e.target.value)}>
                                        <option value="">— Текущий проект —</option>
                                        {projects.filter(p => p.slug !== slug).map(p => <option key={p.id} value={String(p.id)}>{p.name}</option>)}
                                        <option value="none">— Без проекта (общая) —</option>
                                    </select>
                                </div>
                            )}
                        </div>
                        )}

                        {/* Бренд — атрибуция расхода (как в План-Факте); «Все бренды» = общий по проекту */}
                        {!isShipmentContext && brandOptions.length > 0 && (
                            <div className="form-group" style={{ margin: '0 0 16px' }}>
                                <label className="form-label">Бренд</label>
                                <select className="form-input" value={brandSel} onChange={e => setBrandSel(e.target.value)}>
                                    <option value="">Все бренды</option>
                                    {brandOptions.map(b => <option key={b} value={b}>{b}</option>)}
                                </select>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                    «Все бренды» — расход общий по проекту.
                                </div>
                            </div>
                        )}

                        {/* Mode toggle — только для логистики (привязка к отгрузке); скрыт при правке/мульти */}
                        {!isEdit && !isMultiProp && category === 'LOGISTICS' && (
                        <div style={{ display: 'flex', gap: 0, marginBottom: 20 }}>
                            <button
                                className={`btn btn-sm ${mode === 'COUNTERPARTY' ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => { setMode('COUNTERPARTY'); setSelectedShipment(null); setShowPicker(true); }}
                                style={{ borderRadius: '8px 0 0 8px' }}
                            >
                                Выбрать отгрузку
                            </button>
                            <button
                                className={`btn btn-sm ${mode === 'MANUAL' ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setMode('MANUAL')}
                                style={{ borderRadius: '0 8px 8px 0' }}
                            >
                                Ввести вручную
                            </button>
                        </div>
                        )}

                        {/* ─── COUNTERPARTY mode ─── */}
                        {mode === 'COUNTERPARTY' && (
                            <div style={{ marginBottom: 16 }}>
                                {showPicker && (<>
                                <div className="form-group">
                                    <label className="form-label">Поиск по отгрузкам (SHIPPED)</label>
                                    <input
                                        className="form-input"
                                        placeholder="Номер заявки, склад, подрядчик..."
                                        value={shipSearch}
                                        onChange={e => setShipSearch(e.target.value)}
                                    />
                                </div>
                                {shippableLoading ? (
                                    <div style={{ padding: 12, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>Загрузка...</div>
                                ) : (
                                    <div style={{ maxHeight: 220, overflowY: 'auto', border: '1px solid var(--color-border)', borderRadius: 8 }}>
                                        {shippable.length === 0 ? (
                                            <div style={{ padding: 16, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                                                Нет отгрузок для выбора
                                            </div>
                                        ) : (
                                            shippable.map(row => {
                                                const isSelected = selectedShipment?.outbound_shipment_id === row.outbound_shipment_id;
                                                return (
                                                    <div
                                                        key={row.outbound_shipment_id}
                                                        onClick={() => !row.already_requested && setSelectedShipment(row)}
                                                        style={{
                                                            padding: '10px 14px',
                                                            borderBottom: '1px solid var(--color-border)',
                                                            cursor: row.already_requested ? 'not-allowed' : 'pointer',
                                                            background: isSelected ? 'rgba(0,122,255,0.08)' : row.already_requested ? 'var(--color-bg)' : undefined,
                                                            opacity: row.already_requested ? 0.5 : 1,
                                                            display: 'flex',
                                                            justifyContent: 'space-between',
                                                            alignItems: 'center',
                                                        }}
                                                    >
                                                        <div>
                                                            <div style={{ fontWeight: 600, fontSize: 13 }}>{row.number}</div>
                                                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                                {row.destination || '—'}
                                                                {row.shipped_date && ` · ${formatDate(row.shipped_date)}`}
                                                                {row.carrier_name && ` · ${row.carrier_name}`}
                                                            </div>
                                                        </div>
                                                        <div style={{ textAlign: 'right', flexShrink: 0, marginLeft: 12 }}>
                                                            {row.pickup_cost != null && (
                                                                <div style={{ fontWeight: 600, fontSize: 13 }}>
                                                                    {formatNumber(Number(row.pickup_cost), 2)} ₽
                                                                </div>
                                                            )}
                                                            {row.already_requested && row.request_status && (
                                                                <span className={`badge ${STATUS_CLASS[row.request_status]}`} style={{ fontSize: 11 }} title="Заявка уже есть — открыть можно во вкладке «Оплаты»">
                                                                    {STATUS_LABEL[row.request_status]}
                                                                </span>
                                                            )}
                                                            {isSelected && !row.already_requested && (
                                                                <span style={{ color: 'var(--color-accent)', fontSize: 18 }}>✓</span>
                                                            )}
                                                        </div>
                                                    </div>
                                                );
                                            })
                                        )}
                                    </div>
                                )}
                                </>)}

                                {!showPicker && shippableLoading && (
                                    <div style={{ padding: 12, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                                        Загрузка отгрузки...
                                    </div>
                                )}

                                {/* Привязанная отгрузка — крупная шапка + редактируемые реквизиты */}
                                {selectedShipment && multiShipments.length === 0 && (
                                    <div style={{ marginTop: showPicker ? 12 : 0 }}>
                                        <div style={{ padding: '10px 14px', marginBottom: 12, borderRadius: 8, background: 'rgba(0,122,255,0.06)', border: '1px solid var(--color-border)' }}>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                                                <div style={{ fontSize: 13 }}>
                                                    Отгрузка <strong>{selectedShipment.number}</strong>
                                                    {selectedShipment.destination && ` · ${selectedShipment.destination}`}
                                                    {selectedShipment.shipped_date && ` · ${formatDate(selectedShipment.shipped_date)}`}
                                                    {selectedShipment.carrier_name && ` · ${selectedShipment.carrier_name}`}
                                                    {selectedShipment.pickup_cost != null && (
                                                        <strong> · {formatNumber(Number(selectedShipment.pickup_cost), 2)} ₽</strong>
                                                    )}
                                                </div>
                                                {!showPicker && (
                                                    <button
                                                        className="btn btn-sm btn-secondary"
                                                        style={{ flexShrink: 0 }}
                                                        onClick={() => setShowPicker(true)}
                                                    >
                                                        Другая отгрузка
                                                    </button>
                                                )}
                                            </div>
                                            {!selectedShipment.carrier_bank_account && (
                                                <div style={{ marginTop: 6, fontSize: 12, color: 'var(--color-warning)' }}>
                                                    Банковские реквизиты перевозчика не сохранены — заполните р/с и БИК ниже (запомним на будущее).
                                                </div>
                                            )}
                                        </div>
                                        {requisitesGrid}
                                    </div>
                                )}

                                {/* Мульти-оплата: несколько заборов одного перевозчика → один счёт */}
                                {multiShipments.length > 0 && (
                                    <div>
                                        <div style={{ padding: '10px 14px', marginBottom: 12, borderRadius: 8, background: 'rgba(0,122,255,0.06)', border: '1px solid var(--color-border)' }}>
                                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
                                                Одна оплата за {multiShipments.length} {pluralZabor(multiShipments.length)}
                                                {multiShipments[0].carrier_name && ` · ${multiShipments[0].carrier_name}`}
                                            </div>
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                                {multiShipments.map(s => (
                                                    <div key={s.outbound_shipment_id} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 12 }}>
                                                        <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                            <strong>{s.number}</strong>
                                                            {s.destination && ` · ${s.destination}`}
                                                            {s.pickup_date && ` · ${formatDate(s.pickup_date)}`}
                                                        </span>
                                                        <span style={{ flexShrink: 0, fontWeight: 600 }}>
                                                            {s.pickup_cost != null ? `${formatNumber(Number(s.pickup_cost), 2)} ₽` : '—'}
                                                        </span>
                                                    </div>
                                                ))}
                                            </div>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--color-border)', fontSize: 13, fontWeight: 700 }}>
                                                <span>Итого</span>
                                                <span>{formatNumber(multiShipments.reduce((a, s) => a + (s.pickup_cost != null ? Number(s.pickup_cost) : 0), 0), 2)} ₽</span>
                                            </div>
                                            {!multiShipments[0].carrier_bank_account && (
                                                <div style={{ marginTop: 6, fontSize: 12, color: 'var(--color-warning)' }}>
                                                    Банковские реквизиты перевозчика не сохранены — заполните р/с и БИК ниже (запомним на будущее).
                                                </div>
                                            )}
                                        </div>
                                        {requisitesGrid}
                                    </div>
                                )}

                                {/* Locked, но привязанная отгрузка не найдена среди доступных */}
                                {!selectedShipment && !showPicker && !shippableLoading && (
                                    <div style={{ padding: 12, fontSize: 13, color: 'var(--color-text-muted)' }}>
                                        Отгрузка не найдена среди доступных.{' '}
                                        <button className="btn btn-sm btn-secondary" onClick={() => setShowPicker(true)}>Выбрать вручную</button>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* ─── MANUAL mode ─── */}
                        {mode === 'MANUAL' && <div>{requisitesGrid}</div>}

                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                            <button
                                className="btn btn-primary"
                                onClick={handleCreate}
                                disabled={creating || (mode === 'COUNTERPARTY' && !selectedShipment)}
                            >
                                {creating ? (isEdit ? 'Сохранение...' : 'Создание...') : (isEdit ? 'Сохранить — документы' : 'Далее — документы')}
                            </button>
                        </div>
                    </>
                )}

                {/* ══════════ STEP 2: DOCUMENTS ══════════ */}
                {step === 'docs' && created && (
                    <>
                        <div style={{ padding: '12px 14px', background: 'var(--color-bg)', borderRadius: 8, marginBottom: 16, fontSize: 13 }}>
                            {isEdit ? 'Заявка' : 'Создана заявка'} <strong>{created.number}</strong>
                            {created.payee_name && ` · ${created.payee_name}`}
                            {created.amount && ` · ${formatNumber(Number(created.amount), 2)} ₽`}
                        </div>

                        {created.documents.length > 0 && (
                            <div style={{ marginBottom: 12 }}>
                                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: 'var(--color-text-muted)' }}>Загруженные документы</div>
                                {created.documents.map(d => (
                                    <div key={d.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 10px', background: 'var(--color-bg)', borderRadius: 8, marginBottom: 4 }}>
                                        <span style={{ fontSize: 12 }}>
                                            <span className="badge badge-secondary" style={{ fontSize: 11, marginRight: 6 }}>{d.doc_type === 'INVOICE' ? 'Счёт' : 'Акт'}</span>
                                            {d.original_filename ?? 'файл'}
                                        </span>
                                        <button
                                            title="Удалить"
                                            onClick={async () => { await api.deletePaymentRequestDocument(created.id, d.id); await reloadCreated(created.id); }}
                                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-danger)', fontSize: 14 }}
                                        >
                                            ✕
                                        </button>
                                    </div>
                                ))}
                            </div>
                        )}

                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
                            <div
                                onClick={() => invoiceRef.current?.click()}
                                style={{
                                    border: `2px dashed ${invoiceFile ? 'var(--color-success)' : 'var(--color-border)'}`,
                                    borderRadius: 12,
                                    padding: 20,
                                    textAlign: 'center',
                                    cursor: 'pointer',
                                    transition: 'border-color 0.2s',
                                }}
                            >
                                <div style={{ fontSize: 24, marginBottom: 6 }}>📄</div>
                                <div style={{ fontSize: 13, fontWeight: 600 }}>Счёт (INVOICE)</div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                    {invoiceFile ? invoiceFile.name : 'PDF, Word или фото'}
                                </div>
                                <input
                                    ref={invoiceRef}
                                    type="file"
                                    accept=".pdf,.doc,.docx,.jpg,.jpeg,.png,.webp,.heic"
                                    style={{ display: 'none' }}
                                    onChange={e => setInvoiceFile(e.target.files?.[0] ?? null)}
                                />
                            </div>

                            <div
                                onClick={() => actRef.current?.click()}
                                style={{
                                    border: `2px dashed ${actFile ? 'var(--color-success)' : 'var(--color-border)'}`,
                                    borderRadius: 12,
                                    padding: 20,
                                    textAlign: 'center',
                                    cursor: 'pointer',
                                    transition: 'border-color 0.2s',
                                }}
                            >
                                <div style={{ fontSize: 24, marginBottom: 6 }}>📋</div>
                                <div style={{ fontSize: 13, fontWeight: 600 }}>Акт (ACT)</div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                    {actFile ? actFile.name : 'PDF, Word или фото'}
                                </div>
                                <input
                                    ref={actRef}
                                    type="file"
                                    accept=".pdf,.doc,.docx,.jpg,.jpeg,.png,.webp,.heic"
                                    style={{ display: 'none' }}
                                    onChange={e => setActFile(e.target.files?.[0] ?? null)}
                                />
                            </div>
                        </div>

                        {missingReqs.length > 0 && (
                            <div style={{ padding: '10px 14px', marginBottom: 12, borderRadius: 8, background: 'rgba(239,68,68,0.08)', fontSize: 13, color: 'var(--color-danger)' }}>
                                <div style={{ fontWeight: 600, marginBottom: 4 }}>Не заполнено:</div>
                                <ul style={{ margin: 0, paddingLeft: 18 }}>
                                    {missingReqs.map((r, i) => <li key={i}>{r}</li>)}
                                </ul>
                            </div>
                        )}

                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={onClose}>Закрыть</button>
                            <button
                                className="btn btn-primary"
                                onClick={handleUploadDocs}
                                disabled={uploading}
                            >
                                {uploading ? 'Сохранение...' : 'Сохранить и закрыть'}
                            </button>
                        </div>
                    </>
                )}

                {/* ══════════ STEP 3: DONE ══════════ */}
                {step === 'done' && created && (
                    <div style={{ textAlign: 'center', padding: '24px 0' }}>
                        <div style={{ fontSize: 48, marginBottom: 12 }}>✅</div>
                        <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Заявка передана в оплату</div>
                        <div style={{ fontSize: 14, color: 'var(--color-text-muted)', marginBottom: 16 }}>
                            {created.number}
                            {created.payee_name && ` · ${created.payee_name}`}
                        </div>
                        {displayStatus && (
                            <span className={`badge ${STATUS_CLASS[displayStatus]}`} style={{ fontSize: 14 }}>
                                {STATUS_LABEL[displayStatus]}
                            </span>
                        )}
                        {displayStatus === 'PENDING_REVIEW' && (
                            <div style={{ marginTop: 12, fontSize: 13, color: 'var(--color-text-muted)' }}>
                                Ожидаем подтверждения оператора...
                            </div>
                        )}
                        <div style={{ marginTop: 24 }}>
                            <button className="btn btn-primary" onClick={onClose}>Закрыть</button>
                        </div>
                    </div>
                )}
                </>)}
            </div>
        </div>
    );
}

// Русское склонение «забор» для счётчика: 1 забор, 2 забора, 5 заборов.
function pluralZabor(n: number): string {
    const mod10 = n % 10, mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return 'забор';
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return 'забора';
    return 'заборов';
}
