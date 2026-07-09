'use client';
import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatDateTime, formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import { FfMismatchBlock } from '@/components/FfMismatchModal';
import MigfullModal from './MigfullModal';
import PalletLayoutTab from './PalletLayoutTab';
import WbSupplyPanel from './WbSupplyPanel';
import type { Column } from '@/components/DataTable';
import type { AssemblyAttempt, AssemblyHistoryEntry, AssemblyRequest, AssemblyStatus, BoxMultiplicityRow, FfCreateFormResponse, FfPushAssemblyResult, FulfillmentStatus, MigfullPortalConfig, RefreshFromFboResponse, Warehouse, WbFboSupply, WbSupplyState, WbSupplySyncStatus } from '@/types/api';

// Статус заноса заявки в кабинет WB (для вкладки «Поставка WB»).
const WB_SYNC_MAP: Record<WbSupplySyncStatus, { label: string; className: string }> = {
    NONE:     { label: 'Не заведена',                    className: 'badge-secondary' },
    DRAFT:    { label: 'Черновик',                        className: 'badge-info' },
    PREORDER: { label: 'Преордер — нужна бронь даты',     className: 'badge-warning' },
    BOOKED:   { label: 'Дата забронирована',             className: 'badge-info' },
    BOXED:    { label: 'Короба занесены',                className: 'badge-info' },
    PASSED:   { label: 'Пропуск занесён',                className: 'badge-success' },
    ERROR:    { label: 'Ошибка',                          className: 'badge-danger' },
};

// Статусы, в которых заявку имеет смысл отправлять на ФФ (до отгрузки нашей стороной).
const FF_PUSH_STATUSES: ReadonlySet<AssemblyStatus> = new Set<AssemblyStatus>(['PENDING', 'IN_PROGRESS', 'READY', 'VEHICLE_ASSIGNED']);

// ─── Status config ──────────────────────────────────────────────────────────

const STATUS_MAP: Record<AssemblyStatus, { label: string; className: string }> = {
    // PENDING — legacy: новые заявки создаются сразу в IN_PROGRESS.
    PENDING:          { label: 'В сборке',          className: 'badge-info' },
    PRE_DISTRIBUTED:  { label: 'Распределено',      className: 'badge-secondary' },
    IN_PROGRESS:      { label: 'В сборке',          className: 'badge-info' },
    READY:            { label: 'Готово',             className: 'badge-success' },
    VEHICLE_ASSIGNED: { label: 'Машина назначена',   className: 'badge-info' },
    SHIPPED:          { label: 'Отгружена',          className: 'badge-success' },
    DELIVERED:        { label: 'Принята WB',         className: 'badge-success' },
    RETURNED:         { label: 'Возврат на склад',   className: 'badge-warning' },
    CLOSED:           { label: 'Закрыт',             className: 'badge-warning' },
    CANCELLED:        { label: 'Отменена',           className: 'badge-secondary' },
};

// Исход попытки отгрузки (для бейджа в таймлайне попыток).
const ATTEMPT_OUTCOME: Record<AssemblyAttempt['outcome'], { label: string; className: string }> = {
    accepted:   { label: 'Принято WB',  className: 'badge-success' },
    rejected:   { label: 'Не принято',  className: 'badge-danger' },
    in_transit: { label: 'В пути',      className: 'badge-info' },
};

// ─── Component ──────────────────────────────────────────────────────────────

export default function AssemblyDetailPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const id = Number(params.id);

    const [assembly, setAssembly] = useState<AssemblyRequest | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actionLoading, setActionLoading] = useState(false);
    // Вкладки деталки: «Заявка» (состав/логистика) | «Раскладка по паллетам».
    const [tab, setTab] = useState<'request' | 'pallets' | 'wb'>('request');

    // Modals
    const [showVehicleModal, setShowVehicleModal] = useState(false);
    const [vehicleInfo, setVehicleInfo] = useState('');
    const [vehicleBrand, setVehicleBrand] = useState('');
    const [driverPhone, setDriverPhone] = useState('');
    const [carrierInn, setCarrierInn] = useState('');
    const [carrierName, setCarrierName] = useState('');
    const [pickupDate, setPickupDate] = useState('');
    const [pickupTimeSlot, setPickupTimeSlot] = useState('');
    const [pickupCost, setPickupCost] = useState<number | ''>('');
    const [deliveryDate, setDeliveryDate] = useState('');
    const [showShipModal, setShowShipModal] = useState(false);
    const [showCancelModal, setShowCancelModal] = useState(false);
    const [showReturnModal, setShowReturnModal] = useState(false);
    // Возврат: склад возврата (по умолчанию — склад-источник) + список ФФ-складов.
    const [returnWarehouseId, setReturnWarehouseId] = useState<number | ''>('');
    const [ffWarehouses, setFfWarehouses] = useState<Warehouse[]>([]);

    // ФФ push (создать заявку на ФФ из нашей сборки)
    const [ffStatus, setFfStatus] = useState<FulfillmentStatus | null>(null);
    const [showPushModal, setShowPushModal] = useState(false);
    const [pushForm, setPushForm] = useState<FfCreateFormResponse | null>(null);
    const [pushFormLoading, setPushFormLoading] = useState(false);
    const [pushWarehouseId, setPushWarehouseId] = useState<number | ''>('');
    const [pushCollectionDate, setPushCollectionDate] = useState('');
    const [pushUnloadingDate, setPushUnloadingDate] = useState('');
    const [pushDeliveryType, setPushDeliveryType] = useState<'straight' | 'cross_dock'>('straight');
    const [pushComment, setPushComment] = useState('');
    const [pushResult, setPushResult] = useState<FfPushAssemblyResult | null>(null);

    // Migfull-портал (ФФ «Натали») — создать заявку на отгрузку из сборки.
    const [migfullConfig, setMigfullConfig] = useState<MigfullPortalConfig | null>(null);
    const [showMigfullModal, setShowMigfullModal] = useState(false);

    // Refresh from FBO result
    const [refreshResult, setRefreshResult] = useState<RefreshFromFboResponse | null>(null);

    // Согласование предложенной ФФ правки состава (approve = применить, reject = отклонить).
    const [ffReviewLoading, setFfReviewLoading] = useState<'approve' | 'reject' | null>(null);

    // History
    const [history, setHistory] = useState<AssemblyHistoryEntry[]>([]);

    // Цепочка попыток отгрузки (отгрузил → не приняли → вернул → переотгрузил).
    const [attempts, setAttempts] = useState<AssemblyAttempt[]>([]);

    // Сырые кратности коробов (per-nm + per-warehouse) — резолвим ниже с приоритетом склада заявки.
    const [bmRows, setBmRows] = useState<BoxMultiplicityRow[]>([]);

    // Состояние заноса в кабинет WB — для вкладки «Поставка WB» (опционально).
    const [wbState, setWbState] = useState<WbSupplyState | null>(null);

    // FBO supply editing
    const [editingFbo, setEditingFbo] = useState(false);
    const [fboSupplies, setFboSupplies] = useState<WbFboSupply[]>([]);
    const [fboSearchInput, setFboSearchInput] = useState('');
    const [fboDropdownOpen, setFboDropdownOpen] = useState(false);
    const [loadingFboList, setLoadingFboList] = useState(false);
    const fboDropdownRef = useRef<HTMLDivElement>(null);

    // ─── Load ─────────────────────────────────────────────────────────────

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const data = await api.getAssemblyRequest(id);
            setAssembly(data);
            api.getAssemblyHistory(id).then(setHistory).catch(() => {});
            api.getAssemblyAttempts(id).then(setAttempts).catch(() => {});
            // Занос в кабинет WB — опционально: ошибку глотаем, карточка покажет дефолт.
            api.wbSupplyGetState(id).then(setWbState).catch(() => {});
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [id]);

    useEffect(() => { load(); }, [load]);

    // Статус ФФ-подключения склада — нужен для кнопки «Создать заявку на ФФ».
    const warehouseId = assembly?.warehouse_id;
    useEffect(() => {
        if (!warehouseId) return;
        let cancelled = false;
        api.getFulfillmentStatus(warehouseId)
            .then(s => { if (!cancelled) setFfStatus(s); })
            .catch(() => { if (!cancelled) setFfStatus(null); });
        return () => { cancelled = true; };
    }, [warehouseId]);

    useEffect(() => {
        let cancelled = false;
        api.getBoxMultiplicity()
            .then(resp => { if (!cancelled) setBmRows(resp.items); })
            .catch(() => { /* best-effort */ });
        return () => { cancelled = true; };
    }, []);

    // Кратность короба per-баркод. Приоритет: действующая кратность склада ЗАЯВКИ
    // (товар физически едет его коробами, machine-first) → глобальный override →
    // минимальная по складам (фолбэк). Min-фолбэк без учёта склада заявки давал
    // псевдо-россыпь: заявка на хамзу (короб 18, machine) мерялась ручной
    // кратностью натали (13) → целый короб рисовался как «1 кор + 5 шт».
    const ppbByBarcode = useMemo(() => {
        const m = new Map<string, number | null>();
        for (const r of bmRows) {
            let ppb: number | null = null;
            const own = warehouseId != null ? r.per_warehouse.find(p => p.warehouse_id === warehouseId) : undefined;
            if (own?.box_qty && own.box_qty > 0 && own.use_box_multiplicity) {
                ppb = own.box_qty;
            } else if (r.box_qty_override && r.box_qty_override > 0 && r.use_box_multiplicity) {
                ppb = r.box_qty_override;
            } else {
                let best = 0;
                for (const p of r.per_warehouse) {
                    if (p.box_qty && p.box_qty > 0 && p.use_box_multiplicity && (best === 0 || p.box_qty < best)) best = p.box_qty;
                }
                ppb = best > 0 ? best : null;
            }
            if (r.barcode) m.set(r.barcode, ppb);
        }
        return m;
    }, [bmRows, warehouseId]);

    // Размер коробки по баркоду (из машины): свой ФФ → любой доступный.
    const boxSizeByBarcode = useMemo(() => {
        const m = new Map<string, string | null>();
        for (const r of bmRows) {
            const own = warehouseId != null ? r.per_warehouse.find(p => p.warehouse_id === warehouseId) : undefined;
            const bs = own?.box_size || r.per_warehouse.find(p => p.box_size)?.box_size || null;
            if (r.barcode) m.set(r.barcode, bs);
        }
        return m;
    }, [bmRows, warehouseId]);

    // Конфиг migfull-портала («Натали») — грузим один раз; кнопку показываем,
    // только если интеграция настроена и её склад совпадает со складом сборки.
    useEffect(() => {
        let cancelled = false;
        api.migfullPortalConfig()
            .then(c => { if (!cancelled) setMigfullConfig(c); })
            .catch(() => { if (!cancelled) setMigfullConfig(null); });
        return () => { cancelled = true; };
    }, []);

    // Close FBO dropdown on outside click
    useEffect(() => {
        const handler = (e: MouseEvent) => {
            if (fboDropdownRef.current && !fboDropdownRef.current.contains(e.target as Node)) {
                setFboDropdownOpen(false);
            }
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    // Load FBO supplies when editing
    const loadFboSupplies = useCallback(async () => {
        setLoadingFboList(true);
        try {
            const resp = await api.getFboSupplies({
                status: 'ACTIVE,ON_DELIVERY,IN_PROGRESS,ACCEPTED',
                search: fboSearchInput || undefined,
                limit: 100,
                exclude_with_assembly: true,
                // Совместная поставка: не прячем поставку, занятую сборкой ДРУГОГО склада
                // — её можно привязать к сборке этого склада-источника (wms + wms2).
                exclude_assembly_warehouse_id: assembly?.warehouse_id,
            });
            setFboSupplies(resp.items);
        } catch {
            setFboSupplies([]);
        }
        setLoadingFboList(false);
    }, [fboSearchInput, assembly?.warehouse_id]);

    useEffect(() => {
        if (!editingFbo) return;
        const timer = setTimeout(() => { loadFboSupplies(); }, 300);
        return () => clearTimeout(timer);
    }, [editingFbo, loadFboSupplies]);

    const handleFboSave = async (fboId: number | null) => {
        if (!assembly) return;
        const oldFboId = assembly.wb_fbo_supply_id;
        const oldName = assembly.wb_supply_name;
        try {
            setAssembly({ ...assembly, wb_fbo_supply_id: fboId, wb_supply_name: fboId ? (fboSupplies.find(s => s.id === fboId)?.wb_supply_id || String(fboId)) : undefined });
            await api.updateAssemblyRequest(id, { wb_fbo_supply_id: fboId });
            setEditingFbo(false);
            setFboSearchInput('');
            // Reload to get fresh data
            await load();
        } catch (e: unknown) {
            setAssembly({ ...assembly, wb_fbo_supply_id: oldFboId, wb_supply_name: oldName });
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        }
    };

    // ─── Actions ──────────────────────────────────────────────────────────

    const doAction = async (action: () => Promise<AssemblyRequest>) => {
        setActionLoading(true);
        setError('');
        try {
            const updated = await action();
            setAssembly(updated);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const handleStart = () => doAction(() => api.startAssembly(id));
    const handleReady = () => doAction(() => api.markAssemblyReady(id));

    const vehicleFormValid = vehicleInfo.trim() && vehicleBrand.trim() && driverPhone.trim()
        && pickupDate && pickupTimeSlot && pickupCost !== '' && deliveryDate;

    const handleAssignVehicle = async () => {
        if (!vehicleFormValid) return;
        setActionLoading(true);
        setError('');
        try {
            const updated = await api.assignVehicle(id, {
                vehicle_info: vehicleInfo.trim(),
                vehicle_brand: vehicleBrand.trim(),
                driver_phone: driverPhone.trim(),
                pickup_date: pickupDate,
                pickup_time_slot: pickupTimeSlot,
                pickup_cost: Number(pickupCost),
                delivery_date: deliveryDate,
                carrier_inn: carrierInn.trim() || null,
                carrier_name: carrierName.trim() || null,
            });
            setAssembly(updated);
            setShowVehicleModal(false);
            setVehicleInfo('');
            setCarrierInn('');
            setCarrierName('');
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const handleShip = async () => {
        setActionLoading(true);
        setError('');
        try {
            const updated = await api.shipAssembly(id);
            setAssembly(updated);
            setShowShipModal(false);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const handleCancel = async () => {
        setActionLoading(true);
        setError('');
        try {
            const updated = await api.cancelAssembly(id);
            setAssembly(updated);
            setShowCancelModal(false);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const openReturnModal = () => {
        if (!assembly) return;
        setReturnWarehouseId(assembly.warehouse_id);
        setShowReturnModal(true);
        // Список ФФ-складов для выбора склада возврата (можно вернуть на другой).
        api.getWarehouses()
            .then(whs => setFfWarehouses(whs.filter(w => w.warehouse_type === 'FULFILLMENT')))
            .catch(() => {});
    };

    const handleReturn = async () => {
        if (!assembly) return;
        setActionLoading(true);
        setError('');
        try {
            const updated = await api.returnAssembly(id, {
                return_warehouse_id: returnWarehouseId === '' ? null : returnWarehouseId,
            });
            setAssembly(updated);
            setShowReturnModal(false);
            api.getAssemblyAttempts(id).then(setAttempts).catch(() => {});
            api.getAssemblyHistory(id).then(setHistory).catch(() => {});
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const handleReopen = async () => {
        setActionLoading(true);
        setError('');
        try {
            const updated = await api.reopenAssembly(id);
            setAssembly(updated);
            api.getAssemblyHistory(id).then(setHistory).catch(() => {});
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const handleClose = async () => {
        setActionLoading(true);
        setError('');
        try {
            const updated = await api.closeAssembly(id);
            setAssembly(updated);
            api.getAssemblyHistory(id).then(setHistory).catch(() => {});
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    // ─── ФФ push (создать заявку на ФФ из нашей сборки) ────────────────────
    // wmscelicom («Целиком») — самовывоз: склад WB и даты не задаются (берутся из
    // WB-привязки на стороне ФФ), диалог упрощён до подтверждения + комментария.
    const isWmsPush = ffStatus?.provider === 'wmscelicom';
    const canPushToFf = !!assembly && !assembly.ff_request_id
        && ffStatus?.connected === true && (ffStatus.provider === 'skladbot' || ffStatus.provider === 'wmscelicom')
        && FF_PUSH_STATUSES.has(assembly.status);

    const openPushModal = async () => {
        if (!assembly) return;
        const today = new Date().toISOString().slice(0, 10);
        setPushForm(null);
        setPushWarehouseId('');
        setPushCollectionDate(assembly.estimated_ready_date || today);
        setPushUnloadingDate(assembly.estimated_ready_date || today);
        setPushDeliveryType('straight');
        setPushComment('');
        setPushResult(null);
        setError('');
        setShowPushModal(true);
        if (isWmsPush) return;  // «Целиком» — без form-data (склад/даты не нужны)
        // Склад МП у skladbot — select по id из живого form-data, не свободный текст
        setPushFormLoading(true);
        try {
            const form = await api.getFfCreateForm(assembly.warehouse_id, assembly.id);
            setPushForm(form);
            setPushWarehouseId(form.suggested_warehouse_id ?? '');
            setPushCollectionDate(form.collection_date || assembly.estimated_ready_date || today);
            setPushUnloadingDate(form.unloading_date || assembly.estimated_ready_date || today);
            if (form.delivery_type === 'straight' || form.delivery_type === 'cross_dock') setPushDeliveryType(form.delivery_type);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить склады МП');
        }
        setPushFormLoading(false);
    };

    // «Целиком» не требует склада/дат; skladbot — обязательны
    const pushFormValid = isWmsPush || (pushWarehouseId !== '' && !!pushCollectionDate && !!pushUnloadingDate);

    const handlePush = async () => {
        if (!assembly || !pushFormValid) return;
        setActionLoading(true);
        setError('');
        try {
            const payload = isWmsPush
                ? { comment: pushComment.trim() || null }
                : {
                    marketplace_warehouse_id: pushWarehouseId === '' ? undefined : pushWarehouseId,
                    marketplace_id: pushForm?.marketplace_id,
                    collection_date: pushCollectionDate,
                    unloading_date: pushUnloadingDate,
                    delivery_type: pushDeliveryType,
                    comment: pushComment.trim() || null,
                };
            const res = await api.createFfRequestFromAssembly(assembly.warehouse_id, assembly.id, payload);
            setPushResult(res);
            await load();  // подтянуть ff_request_* в шапку
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка создания заявки на ФФ');
        }
        setActionLoading(false);
    };

    const handleRefreshFromFbo = async () => {
        setActionLoading(true);
        setError('');
        setRefreshResult(null);
        try {
            const result = await api.refreshFromFbo(id);
            setRefreshResult(result);
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка обновления из FBO');
        }
        setActionLoading(false);
    };

    // Решение по предложенной ФФ правке состава: применить (approve — может вернуть 400
    // при дефиците остатка) или отклонить (reject). На успехе перечитываем заявку, чтобы
    // блок согласования исчез и обновился состав.
    const handleFfReview = async (action: 'approve' | 'reject') => {
        if (!assembly) return;
        if (action === 'approve' && !confirm('Применить состав ФФ к заявке?')) return;
        setFfReviewLoading(action);
        setError('');
        try {
            const updated = await api.assemblyFfReview(id, action);
            setAssembly(updated);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка согласования');
        }
        setFfReviewLoading(null);
    };

    // Мета-поля (склад WB, плановая дата, палеты, вес, комментарий) редактируемы
    // в любом не-CANCELLED статусе, включая SHIPPED/DELIVERED/CLOSED — backend это
    // допускает (не двигает остатки). Структурные поля (позиции, склад-источник,
    // FBO) остаются под canEditFbo / страницей редактирования.
    const canEditFields = assembly && assembly.status !== 'CANCELLED';
    const canEditAlways = assembly && assembly.status !== 'CANCELLED';
    const canEditFbo = assembly && ['PENDING', 'IN_PROGRESS', 'READY', 'VEHICLE_ASSIGNED'].includes(assembly.status);

    const handleFieldSave = async (field: string, value: number | string) => {
        if (!assembly) return;
        const oldAssembly = { ...assembly };
        try {
            const update: Record<string, unknown> = {};
            if (field === 'pallets_count') {
                const newPallets = Number(value);
                const weight = assembly.pallet_weight_kg || 0;
                setAssembly({ ...assembly, pallets_count: newPallets, total_weight_kg: newPallets * weight });
                update.pallets_count = newPallets;
                update.pallet_weight_kg = weight;
            } else if (field === 'pickup_cost') {
                const cost = Number(value);
                setAssembly({ ...assembly, pickup_cost: cost });
                update.pickup_cost = cost;
            } else if (field === 'vehicle_info') {
                setAssembly({ ...assembly, vehicle_info: String(value) });
                update.vehicle_info = String(value);
            } else if (field === 'vehicle_brand') {
                setAssembly({ ...assembly, vehicle_brand: String(value) });
                update.vehicle_brand = String(value);
            } else if (field === 'driver_phone') {
                setAssembly({ ...assembly, driver_phone: String(value) });
                update.driver_phone = String(value);
            } else if (field === 'carrier_inn') {
                setAssembly({ ...assembly, carrier_inn: String(value) });
                update.carrier_inn = String(value);
                update.carrier_name = assembly.carrier_name ?? null;
            } else if (field === 'carrier_name') {
                setAssembly({ ...assembly, carrier_name: String(value) });
                // Must also send carrier_inn so backend knows to upsert (not unlink)
                update.carrier_inn = assembly.carrier_inn ?? '';
                update.carrier_name = String(value);
            } else {
                setAssembly({ ...assembly, [field]: value });
                update[field] = value || undefined;
            }
            await api.updateAssemblyRequest(id, update);
        } catch (e: unknown) {
            setAssembly(oldAssembly);
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        }
    };

    // ─── Render ───────────────────────────────────────────────────────────

    if (loading) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            </div>
        );
    }

    if (error && !assembly) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-danger)' }}>
                    {error}
                    <div style={{ marginTop: 16 }}>
                        <Link href={`/p/${slug}/warehouse/assembly`}>
                            <button className="btn btn-secondary">Назад к списку</button>
                        </Link>
                    </div>
                </div>
            </div>
        );
    }

    if (!assembly) return null;

    const status = STATUS_MAP[assembly.status] || { label: assembly.status, className: '' };

    // ─── Action buttons by status ─────────────────────────────────────────

    const renderActions = () => {
        const buttons: React.ReactNode[] = [];

        switch (assembly.status) {
            case 'PENDING':
                buttons.push(
                    <button key="start" className="btn btn-primary" onClick={handleStart} disabled={actionLoading}>
                        Начать сборку
                    </button>,
                    <Link key="edit" href={`/p/${slug}/warehouse/assembly/${assembly.id}/edit`}>
                        <button className="btn btn-secondary">Редактировать</button>
                    </Link>,
                    <button key="fbo" className="btn btn-secondary" onClick={handleRefreshFromFbo} disabled={actionLoading}>
                        Из FBO
                    </button>,
                    <button key="cancel" className="btn btn-secondary" onClick={() => setShowCancelModal(true)} disabled={actionLoading}
                        style={{ color: 'var(--color-danger)' }}>
                        Отменить
                    </button>,
                );
                break;
            case 'IN_PROGRESS':
                buttons.push(
                    <button key="ready" className="btn btn-primary" onClick={handleReady} disabled={actionLoading}>
                        Сборка готова
                    </button>,
                    <Link key="edit" href={`/p/${slug}/warehouse/assembly/${assembly.id}/edit`}>
                        <button className="btn btn-secondary">Редактировать</button>
                    </Link>,
                    <button key="fbo" className="btn btn-secondary" onClick={handleRefreshFromFbo} disabled={actionLoading}>
                        Из FBO
                    </button>,
                    <button key="cancel" className="btn btn-secondary" onClick={() => setShowCancelModal(true)} disabled={actionLoading}
                        style={{ color: 'var(--color-danger)' }}>
                        Отменить
                    </button>,
                );
                break;
            case 'READY':
                buttons.push(
                    <button key="vehicle" className="btn btn-primary" onClick={() => setShowVehicleModal(true)} disabled={actionLoading}>
                        Назначить машину
                    </button>,
                    <Link key="edit" href={`/p/${slug}/warehouse/assembly/${assembly.id}/edit`}>
                        <button className="btn btn-secondary">Редактировать</button>
                    </Link>,
                    <button key="fbo" className="btn btn-secondary" onClick={handleRefreshFromFbo} disabled={actionLoading}>
                        Из FBO
                    </button>,
                    <button key="cancel" className="btn btn-secondary" onClick={() => setShowCancelModal(true)} disabled={actionLoading}
                        style={{ color: 'var(--color-danger)' }}>
                        Отменить
                    </button>,
                );
                break;
            case 'VEHICLE_ASSIGNED':
                buttons.push(
                    <button key="ship" className="btn btn-primary" onClick={() => setShowShipModal(true)} disabled={actionLoading}>
                        Отгрузить
                    </button>,
                    <Link key="edit" href={`/p/${slug}/warehouse/assembly/${assembly.id}/edit`}>
                        <button className="btn btn-secondary">Редактировать</button>
                    </Link>,
                    <button key="fbo" className="btn btn-secondary" onClick={handleRefreshFromFbo} disabled={actionLoading}>
                        Из FBO
                    </button>,
                    <button key="cancel" className="btn btn-secondary" onClick={() => setShowCancelModal(true)} disabled={actionLoading}
                        style={{ color: 'var(--color-danger)' }}>
                        Отменить
                    </button>,
                );
                break;
            case 'SHIPPED':
            case 'DELIVERED':
                buttons.push(
                    <button key="return" className="btn btn-primary" onClick={openReturnModal} disabled={actionLoading}>
                        Вернуть на склад
                    </button>,
                    <Link key="edit" href={`/p/${slug}/warehouse/assembly/${assembly.id}/edit`}>
                        <button className="btn btn-secondary">Редактировать</button>
                    </Link>,
                );
                break;
            case 'RETURNED':
                buttons.push(
                    <button key="reopen" className="btn btn-primary" onClick={handleReopen} disabled={actionLoading}
                        title="Переотгрузить: вернуть в «Готово», привязать новую FBW-поставку и назначить водителя">
                        Переотгрузить
                    </button>,
                    <button key="close" className="btn btn-secondary" onClick={handleClose} disabled={actionLoading}>
                        Закрыть заявку
                    </button>,
                    <Link key="edit" href={`/p/${slug}/warehouse/assembly/${assembly.id}/edit`}>
                        <button className="btn btn-secondary">Редактировать</button>
                    </Link>,
                );
                break;
            case 'CLOSED':
                buttons.push(
                    <Link key="edit" href={`/p/${slug}/warehouse/assembly/${assembly.id}/edit`}>
                        <button className="btn btn-secondary">Редактировать</button>
                    </Link>,
                );
                break;
            case 'CANCELLED':
                break;
        }

        if (canPushToFf) {
            buttons.push(
                <button key="push-ff" className="btn btn-secondary" onClick={openPushModal} disabled={actionLoading}
                    title="Создать заявку «Доставка на склад МП» у фулфилмента из состава этой сборки">
                    📦 Создать заявку на ФФ
                </button>,
            );
        }

        // Migfull-портал («Натали»): кнопка только если интеграция настроена, её склад
        // совпадает со складом сборки И у сборки ещё НЕТ заявки в ФФ «Натали». Создание
        // необратимо (нет delete/cancel) → не предлагаем создать дубль; существующие
        // заявки ФФ показаны блоком ниже. Учитываем и наш send, и read-синк/ручной матч.
        const nataliWhId = migfullConfig?.configured ? migfullConfig.warehouse_id : null;
        const hasNataliRequest = nataliWhId != null && (
            (assembly.ff_links?.some((lk) => lk.ff_warehouse_id === nataliWhId) ?? false)
            || assembly.ff_warehouse_id === nataliWhId
        );
        if (nataliWhId != null && nataliWhId === assembly.warehouse_id && !hasNataliRequest) {
            buttons.push(
                <button key="migfull" className="btn btn-secondary" onClick={() => setShowMigfullModal(true)} disabled={actionLoading}
                    title="Создать заявку на отгрузку в ФФ «Натали» (migfull-портал) из состава этой сборки">
                    📦 Создать заявку в ФФ Натали
                </button>,
            );
        }

        return buttons;
    };

    return (
        <div className="animate-in">
            {/* Header */}
            <div className="page-header">
                <div>
                    <Link href={`/p/${slug}/warehouse/assembly`} style={{ color: 'var(--color-text-muted)', textDecoration: 'none', fontSize: 14 }}>
                        &larr; Заявки на сборку
                    </Link>
                    <h1 className="page-title" style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                        {assembly.number}
                        <span className={`badge ${status.className}`} style={assembly.status === 'SHIPPED' ? { opacity: 0.6 } : undefined}>
                            {status.label}
                        </span>
                        {assembly.ff_review_pending && (
                            <span
                                className="badge badge-warning"
                                style={{ fontSize: 12 }}
                                title={
                                    assembly.ff_proposed_by || assembly.ff_proposed_at
                                        ? `Изменил ${assembly.ff_proposed_by || 'ФФ'}${assembly.ff_proposed_at ? `, ${formatDate(assembly.ff_proposed_at)}` : ''}`
                                        : 'ФФ предложил правку состава — требуется согласование'
                                }
                            >
                                ⏳ Ожидает согласования ФФ
                            </span>
                        )}
                    </h1>
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {renderActions()}
                </div>
            </div>

            {/* Вкладки: «Заявка» | «Раскладка по паллетам» */}
            <div className="glass-card" style={{ padding: 8, marginBottom: 16, display: 'flex', gap: 8 }}>
                <button
                    className={`btn btn-sm ${tab === 'request' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setTab('request')}
                >
                    Заявка
                </button>
                <button
                    className={`btn btn-sm ${tab === 'pallets' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setTab('pallets')}
                >
                    Раскладка по паллетам
                </button>
                <button
                    className={`btn btn-sm ${tab === 'wb' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setTab('wb')}
                >
                    {wbState && wbState.sync_status !== 'NONE'
                        ? `Поставка WB · ${WB_SYNC_MAP[wbState.sync_status].label}`
                        : 'Поставка WB'}
                </button>
            </div>

            {/* Расхождение: локальное число паллет ≠ паллеты в WB-пропуске (только в «Готово»). */}
            {assembly.status === 'READY'
                && wbState?.pass_pallets != null
                && wbState.pass_pallets !== assembly.pallets_count && (
                <div
                    className="glass-card"
                    style={{
                        padding: '12px 16px', marginBottom: 16,
                        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                        borderLeft: '3px solid var(--color-warning)',
                    }}
                >
                    <span style={{ fontSize: 18 }}>⚠️</span>
                    <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text)' }}>
                        Паллеты не совпадают с WB: локально {formatNumber(assembly.pallets_count, 0)}, в WB-пропуске {formatNumber(wbState.pass_pallets, 0)}
                    </span>
                </div>
            )}

            {tab === 'pallets' && (
                <PalletLayoutTab assembly={assembly} ppbByBarcode={ppbByBarcode} slug={slug} onSaved={load} />
            )}

            {tab === 'wb' && assembly && (
                <WbSupplyPanel
                    assemblyId={assembly.id}
                    items={assembly.items ?? []}
                    defaultPackageType={assembly.package_type}
                />
            )}

            {tab === 'request' && (<>

            {/* Error */}
            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)' }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={() => setError('')}>
                        Закрыть
                    </button>
                </div>
            )}

            {/* Refresh from FBO result */}
            {refreshResult && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                    <div style={{ fontWeight: 500, marginBottom: 8 }}>Результат обновления из FBO:</div>
                    <div style={{ display: 'flex', gap: 16, fontSize: 14 }}>
                        <span>Добавлено: {refreshResult.added}</span>
                        <span>Удалено: {refreshResult.removed}</span>
                        <span>Изменено: {refreshResult.changed}</span>
                    </div>
                    {assembly?.joint_supply && (
                        <div style={{ marginTop: 8, fontSize: 13, color: 'var(--color-text-muted)' }}>
                            Совместная поставка: данные WB обновлены, но позиции не перестраиваются (общий состав поставки
                            делят несколько сборок). Расхождение считается по сумме всех сборок поставки.
                        </div>
                    )}
                    {refreshResult.skipped && refreshResult.skipped.length > 0 && (
                        <div style={{ marginTop: 8, fontSize: 13, color: 'var(--color-warning)' }}>
                            Пропущено (нет в номенклатуре): {refreshResult.skipped.join(', ')}
                        </div>
                    )}
                    <button className="btn btn-secondary btn-sm" style={{ marginTop: 8 }} onClick={() => setRefreshResult(null)}>
                        Скрыть
                    </button>
                </div>
            )}

            {/* Info card */}
            <div className="glass-card" style={{ padding: 24, marginBottom: 16 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 16 }}>
                    <InfoField label="Склад" value={assembly.warehouse_name || '\u2014'} />
                    {/* FBO supply — editable in PENDING/IN_PROGRESS */}
                    {canEditFbo && editingFbo ? (
                        <div ref={fboDropdownRef} style={{ position: 'relative' }}>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>FBO поставка</div>
                            <input
                                className="form-input"
                                type="text"
                                placeholder="Поиск поставки..."
                                value={fboDropdownOpen ? fboSearchInput : ''}
                                onChange={e => {
                                    setFboSearchInput(e.target.value);
                                    if (!fboDropdownOpen) setFboDropdownOpen(true);
                                }}
                                onFocus={() => setFboDropdownOpen(true)}
                                autoFocus
                                style={{ fontSize: 13 }}
                            />
                            {fboDropdownOpen && (
                                <div style={{
                                    position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
                                    background: 'var(--color-bg)', border: '1px solid var(--color-border)',
                                    borderRadius: 8, maxHeight: 200, overflowY: 'auto',
                                    boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
                                }}>
                                    {/* Option to clear FBO */}
                                    <div
                                        onClick={() => { handleFboSave(null); setFboDropdownOpen(false); }}
                                        style={{ padding: '8px 12px', cursor: 'pointer', fontSize: 13, color: 'var(--color-text-muted)', fontStyle: 'italic' }}
                                        onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
                                        onMouseLeave={e => (e.currentTarget.style.background = '')}
                                    >
                                        Без поставки
                                    </div>
                                    {loadingFboList ? (
                                        <div style={{ padding: 12, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                                    ) : fboSupplies.length === 0 ? (
                                        <div style={{ padding: 12, textAlign: 'center', color: 'var(--color-text-muted)' }}>Поставки не найдены</div>
                                    ) : (
                                        fboSupplies.map(s => (
                                            <div
                                                key={s.id}
                                                onClick={() => { handleFboSave(s.id); setFboDropdownOpen(false); }}
                                                style={{ padding: '8px 12px', cursor: 'pointer', fontSize: 13 }}
                                                onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
                                                onMouseLeave={e => (e.currentTarget.style.background = '')}
                                            >
                                                <strong>{s.wb_supply_id}</strong> — {s.warehouse_name || 'Без склада'} ({s.total_qty} шт.)
                                            </div>
                                        ))
                                    )}
                                </div>
                            )}
                            <div style={{ marginTop: 4 }}>
                                <button className="btn btn-secondary btn-sm" onClick={() => { setEditingFbo(false); setFboSearchInput(''); }}>
                                    Отмена
                                </button>
                            </div>
                        </div>
                    ) : (
                        <div
                            onClick={canEditFbo ? () => { setEditingFbo(true); } : undefined}
                            style={{ cursor: canEditFbo ? 'pointer' : undefined }}
                            title={canEditFbo ? 'Нажмите для изменения' : undefined}
                            onMouseEnter={canEditFbo ? (e) => { e.currentTarget.style.background = 'rgba(59,130,246,0.08)'; } : undefined}
                            onMouseLeave={canEditFbo ? (e) => { e.currentTarget.style.background = ''; } : undefined}
                        >
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>FBO поставка</div>
                            <div style={{ fontSize: 14, fontWeight: 500, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                                {assembly.wb_fbo_supply_id ? (
                                    assembly.wb_supply_name || String(assembly.wb_fbo_supply_id)
                                ) : wbState?.preorder_id ? (
                                    <a
                                        href={`https://seller.wildberries.ru/supplies-management/all-supplies/supply-detail?preorderId=${wbState.preorder_id}&supplyId=${wbState.supply_id ?? ''}`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        onClick={(e) => e.stopPropagation()}
                                        style={{ color: 'var(--color-accent)' }}
                                    >
                                        {wbState.supply_id ? `FBW-${wbState.supply_id}` : `Преордер ${wbState.preorder_id}`}
                                    </a>
                                ) : 'Без поставки'}
                                {canEditFbo && (
                                    <span style={{ color: 'var(--color-primary, #3b82f6)', fontSize: 13, opacity: 0.6 }}>&#x270E;</span>
                                )}
                            </div>
                        </div>
                    )}
                    {assembly.wb_fbo_supply_id ? (
                        <InfoField label="Склад WB" value={assembly.wb_warehouse_name || '\u2014'} />
                    ) : (
                        <EditableInfoField
                            label="Склад WB"
                            value={assembly.wb_warehouse_name_manual || ''}
                            displayValue={assembly.wb_warehouse_name_manual || assembly.wb_warehouse_name || '\u2014'}
                            type="text"
                            editable={!!canEditFields}
                            onSave={async (v) => {
                                if (!assembly) return;
                                const old = assembly.wb_warehouse_name_manual;
                                try {
                                    setAssembly({ ...assembly, wb_warehouse_name_manual: v || null });
                                    await api.updateAssemblyRequest(id, { wb_warehouse_name_manual: v || null });
                                } catch (e: unknown) {
                                    setAssembly({ ...assembly, wb_warehouse_name_manual: old });
                                    setError(e instanceof Error ? e.message : 'Ошибка сохранения');
                                }
                            }}
                        />
                    )}
                    <InfoField label="Создана" value={formatDateTime(assembly.created_at)} />
                    <EditableInfoField
                        label="Дата готовности (план)"
                        value={assembly.estimated_ready_date?.slice(0, 10) || ''}
                        displayValue={formatDate(assembly.estimated_ready_date)}
                        type="date"
                        editable={!!canEditFields}
                        onSave={(v) => handleFieldSave('estimated_ready_date', v)}
                    />
                    <InfoField label="Дата готовности (факт)" value={formatDate(assembly.actual_ready_date)} />
                    <EditableInfoField
                        label="Палеты"
                        value={String(assembly.pallets_count || '')}
                        displayValue={String(assembly.pallets_count)}
                        type="number"
                        editable={!!canEditAlways}
                        onSave={(v) => handleFieldSave('pallets_count', Number(v))}
                    />
                    <InfoField label="Вес 1 палеты" value={assembly.pallet_weight_kg ? formatNumber(assembly.pallet_weight_kg, 1) + ' кг' : '\u2014'} />
                    <InfoField label="Общий вес" value={assembly.total_weight_kg ? formatNumber(assembly.total_weight_kg, 1) + ' кг' : '\u2014'} />
                    <InfoField
                        label="Вес товаров (расчёт)"
                        value={assembly.goods_weight_kg != null && assembly.goods_weight_kg !== ''
                            ? formatNumber(Number(assembly.goods_weight_kg), 1) + ' кг'
                            : '—'}
                    />
                    {assembly.weight_missing_barcodes && assembly.weight_missing_barcodes.length > 0 && (
                        <div style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', padding: '10px 12px', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: 8, fontSize: 13 }}>
                            <span style={{ color: 'var(--color-warning)', fontWeight: 500 }}>
                                ⚠️ Нет веса у {formatNumber(assembly.weight_missing_barcodes.length, 0)} арт.
                            </span>
                            <span style={{ color: 'var(--color-text-muted)' }}>— расчётный вес неполный.</span>
                            <Link href={`/p/${slug}/settings?tab=duties`} style={{ color: 'var(--color-accent)' }}>
                                Заполнить вес в настройках
                            </Link>
                        </div>
                    )}
                    {(assembly.vehicle_info || canEditAlways) && (
                        <EditableInfoField
                            label="Машина"
                            value={assembly.vehicle_info || ''}
                            displayValue={assembly.vehicle_info || '\u2014'}
                            type="text"
                            editable={!!canEditAlways}
                            onSave={(v) => handleFieldSave('vehicle_info', v)}
                        />
                    )}
                    {(assembly.vehicle_brand || canEditAlways) && (
                        <EditableInfoField
                            label="Марка"
                            value={assembly.vehicle_brand || ''}
                            displayValue={assembly.vehicle_brand || '\u2014'}
                            type="text"
                            editable={!!canEditAlways}
                            onSave={(v) => handleFieldSave('vehicle_brand', v)}
                        />
                    )}
                    {(assembly.driver_phone || canEditAlways) && (
                        <EditableInfoField
                            label="Телефон"
                            value={assembly.driver_phone || ''}
                            displayValue={assembly.driver_phone || '\u2014'}
                            type="text"
                            editable={!!canEditAlways}
                            onSave={(v) => handleFieldSave('driver_phone', v)}
                        />
                    )}
                    {(assembly.carrier_inn || canEditAlways) && (
                        <EditableInfoField
                            label="ИНН подрядчика"
                            value={assembly.carrier_inn || ''}
                            displayValue={
                                assembly.counterparty_id ? (
                                    <Link
                                        href={`/p/${slug}/refs/counterparty/${assembly.counterparty_id}`}
                                        style={{ color: 'var(--color-accent)' }}
                                    >
                                        {assembly.carrier_inn} →
                                    </Link>
                                ) : (assembly.carrier_inn || '\u2014')
                            }
                            type="text"
                            editable={!!canEditAlways}
                            onSave={(v) => handleFieldSave('carrier_inn', v)}
                        />
                    )}
                    {(assembly.carrier_name || canEditAlways) && (
                        <EditableInfoField
                            label="Подрядчик"
                            value={assembly.carrier_name || ''}
                            displayValue={assembly.carrier_name || '\u2014'}
                            type="text"
                            editable={!!canEditAlways}
                            onSave={(v) => handleFieldSave('carrier_name', v)}
                        />
                    )}
                    {assembly.pickup_date && (
                        <InfoField label="Забор" value={`${formatDate(assembly.pickup_date)}${assembly.pickup_time_slot ? ', ' + assembly.pickup_time_slot : ''}`} />
                    )}
                    <EditableInfoField
                        label="Стоимость"
                        value={String(assembly.pickup_cost ?? '')}
                        displayValue={assembly.pickup_cost != null ? formatNumber(assembly.pickup_cost) + ' \u20BD' : '\u2014'}
                        type="number"
                        editable={!!canEditAlways}
                        onSave={(v) => handleFieldSave('pickup_cost', Number(v))}
                    />
                    {assembly.delivery_date && (
                        <InfoField label="Сдача на WB" value={formatDate(assembly.delivery_date)} />
                    )}
                    {assembly.vehicle_assigned_at && (
                        <InfoField label="Машина назначена" value={formatDateTime(assembly.vehicle_assigned_at)} />
                    )}
                    {assembly.shipped_at && (
                        <InfoField label="Отгружена" value={formatDateTime(assembly.shipped_at)} />
                    )}
    {(() => {
                        // migfull/«Натали»: на одну сборку может быть 2+ ФФ-заявки → показываем все.
                        const ffLinks = assembly.ff_links?.length
                            ? assembly.ff_links
                            : (assembly.ff_request_id
                                ? [{ ff_request_id: assembly.ff_request_id, ff_request_number: assembly.ff_request_number, ff_stage_title: assembly.ff_stage_title, ff_warehouse_id: assembly.ff_warehouse_id }]
                                : []);
                        if (!ffLinks.length) return null;
                        return (
                            <InfoField
                                label={ffLinks.length > 1 ? `Заявки ФФ (${formatNumber(ffLinks.length, 0)})` : 'Заявка ФФ'}
                                value={
                                    <span style={{ display: 'inline-flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
                                        {ffLinks.map(lk => (
                                            <Link
                                                key={lk.ff_request_id}
                                                href={`/p/${slug}/warehouse/${lk.ff_warehouse_id ?? assembly.warehouse_id}/ff-request/${lk.ff_request_id}`}
                                                title="Открыть ФФ-заявку и сверку состава"
                                                style={{ color: 'var(--color-accent)' }}
                                            >
                                                {lk.ff_request_number}{lk.ff_stage_title ? ` (${lk.ff_stage_title})` : ''} →
                                            </Link>
                                        ))}
                                        {assembly.ff_mismatch === true && (
                                            <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }} title="Подробности — в блоке «Расхождение наполнения» ниже">
                                                ⚠ расхождение
                                            </span>
                                        )}
                                    </span>
                                }
                            />
                        );
                    })()}
                    {assembly.joint_supply && (
                        <InfoField
                            label="Совместная поставка"
                            value={
                                <span style={{ display: 'inline-flex', flexWrap: 'wrap', alignItems: 'center', gap: 10 }}>
                                    <span className="badge badge-info" style={{ fontSize: 11, padding: '2px 8px' }} title="Эта WB-поставка собирается несколькими ФФ — по одной сборке на источник">
                                        Совместная
                                    </span>
                                    {(assembly.joint_siblings || []).map(s => (
                                        <Link
                                            key={s.assembly_id}
                                            href={`/p/${slug}/warehouse/assembly/${s.assembly_id}`}
                                            title={`Сборка того же Совместного номера · ${s.warehouse_name || `Склад ${s.warehouse_id}`}`}
                                            style={{ color: 'var(--color-accent)' }}
                                        >
                                            {s.number}{s.warehouse_name ? ` (${s.warehouse_name})` : ''} →
                                        </Link>
                                    ))}
                                </span>
                            }
                        />
                    )}
                    {assembly.comment && (
                        <div style={{ gridColumn: '1 / -1' }}>
                            <InfoField label="Комментарий" value={assembly.comment} />
                        </div>
                    )}
                </div>
            </div>

            {/* Цепочка попыток отгрузки (отгрузил → не приняли → вернул → переотгрузил) */}
            {attempts.length > 0 && (
                <div className="glass-card" style={{ padding: 24, marginTop: 16 }}>
                    <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>
                        Попытки отгрузки ({formatNumber(attempts.length, 0)})
                    </h2>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
                        {attempts.map((a, idx) => {
                            const oc = ATTEMPT_OUTCOME[a.outcome];
                            const isLast = idx === attempts.length - 1;
                            const dotColor = a.outcome === 'rejected' ? 'var(--color-danger)'
                                : a.outcome === 'accepted' ? 'var(--color-success)' : 'var(--color-accent)';
                            return (
                                <div key={a.shipment_id} style={{ display: 'flex', gap: 12, position: 'relative' }}>
                                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 20 }}>
                                        <div style={{ width: 10, height: 10, borderRadius: '50%', background: dotColor, flexShrink: 0, marginTop: 6 }} />
                                        {!isLast && <div style={{ width: 2, flex: 1, background: 'var(--color-border)' }} />}
                                    </div>
                                    <div style={{ paddingBottom: isLast ? 0 : 16, flex: 1 }}>
                                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                                            <span style={{ fontWeight: 600, fontSize: 14 }}>Попытка {formatNumber(a.attempt_no, 0)}</span>
                                            <span className={`badge ${oc.className}`} style={{ fontSize: 11 }}>{oc.label}</span>
                                            {a.shipped_at && (
                                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{formatDateTime(a.shipped_at)}</span>
                                            )}
                                        </div>
                                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 12 }}>
                                            {a.wb_warehouse_name && <span>Склад WB: <strong style={{ color: 'var(--color-text)' }}>{a.wb_warehouse_name}</strong></span>}
                                            {a.wb_supply_id && <span>FBW: <span style={{ fontFamily: 'monospace' }}>{a.wb_supply_id}</span></span>}
                                            {(a.vehicle_info || a.vehicle_brand) && <span>Машина: {[a.vehicle_info, a.vehicle_brand].filter(Boolean).join(', ')}</span>}
                                            {a.driver_phone && <span>Тел.: {a.driver_phone}</span>}
                                            {a.carrier_name && <span>Подрядчик: {a.carrier_name}</span>}
                                            {a.pickup_cost != null && <span>Стоимость: <strong style={{ color: 'var(--color-text)' }}>{formatNumber(a.pickup_cost)} ₽</strong></span>}
                                            {a.pallets_count != null && <span>Палет: {formatNumber(a.pallets_count, 0)}</span>}
                                        </div>
                                        {a.outcome === 'rejected' && a.returned_to_warehouse_name && (
                                            <div style={{ fontSize: 13, color: 'var(--color-warning)', marginTop: 4 }}>
                                                ↩ Возвращено на склад: {a.returned_to_warehouse_name}
                                                {a.returned_at ? ` (${formatDate(a.returned_at)})` : ''}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* ФФ предлагает изменить состав — согласование (применить/отклонить). Отдельный
                блок, не путать с «Расхождением наполнения» ниже (то — справочное сравнение). */}
            {assembly.ff_review_pending && (() => {
                // Объединяем наш текущий состав (barcode→qty) с предложением ФФ (barcode→qty);
                // строки = union баркодов, сортировка по |разница| убыв.
                const ours = new Map<string, { qty: number; name: string }>();
                for (const it of assembly.items || []) {
                    ours.set(it.barcode, { qty: it.quantity, name: it.product_name || '' });
                }
                const proposed = new Map<string, { qty: number; name: string; article: string }>();
                for (const it of assembly.ff_proposed_items || []) {
                    proposed.set(it.barcode, { qty: it.quantity, name: it.product_name || '', article: it.article || '' });
                }
                const barcodes = Array.from(new Set([...ours.keys(), ...proposed.keys()]));
                const rows = barcodes.map(bc => {
                    const o = ours.get(bc);
                    const p = proposed.get(bc);
                    const ourQty = o?.qty ?? 0;
                    const ffQty = p?.qty ?? 0;
                    return {
                        barcode: bc,
                        name: o?.name || p?.name || p?.article || '',
                        ourQty,
                        ffQty,
                        diff: ffQty - ourQty,
                    };
                }).sort((a, b) => Math.abs(b.diff) - Math.abs(a.diff));
                const ourTotal = rows.reduce((s, r) => s + r.ourQty, 0);
                const ffTotal = rows.reduce((s, r) => s + r.ffQty, 0);
                const totalDiff = ffTotal - ourTotal;
                const diffColor = (d: number) => (d > 0 ? 'var(--color-warning)' : 'var(--color-danger)');
                const signed = (n: number) => `${n > 0 ? '+' : ''}${formatNumber(n, 0)}`;
                return (
                    <div className="glass-card animate-in" style={{ marginTop: 16, borderLeft: '3px solid var(--color-warning)' }}>
                        <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }}>⏳</span>
                            ФФ предлагает изменить состав
                        </h3>
                        <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                            {(assembly.ff_proposed_by || assembly.ff_proposed_at) && (
                                <>Изменил <b style={{ color: 'var(--color-text)' }}>{assembly.ff_proposed_by || 'ФФ'}</b>
                                {assembly.ff_proposed_at ? <>, {formatDate(assembly.ff_proposed_at)}</> : ''}. </>
                            )}
                            Наш итог: <b style={{ color: 'var(--color-text)' }}>{formatNumber(ourTotal, 0)}</b> шт ·
                            {' '}ФФ: <b style={{ color: 'var(--color-text)' }}>{formatNumber(ffTotal, 0)}</b> шт
                            {' '}(разница <b style={{ color: diffColor(totalDiff) }}>{signed(totalDiff)}</b>)
                        </p>
                        <div style={{ overflowX: 'auto' }}>
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                                <thead>
                                    <tr style={{ textAlign: 'left', color: 'var(--color-text-muted)', borderBottom: '1px solid var(--color-border)' }}>
                                        <th style={{ padding: '8px 12px' }}>Товар</th>
                                        <th style={{ padding: '8px 12px' }}>ШК</th>
                                        <th style={{ padding: '8px 12px', textAlign: 'right' }}>Наш</th>
                                        <th style={{ padding: '8px 12px', textAlign: 'right' }}>Предлагает ФФ</th>
                                        <th style={{ padding: '8px 12px', textAlign: 'right' }}>Разница</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {rows.map(r => (
                                        <tr key={r.barcode} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                            <td style={{ padding: '8px 12px' }}>{r.name || '—'}</td>
                                            <td style={{ padding: '8px 12px', fontFamily: 'monospace' }}>{r.barcode}</td>
                                            <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatNumber(r.ourQty, 0)}</td>
                                            <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatNumber(r.ffQty, 0)}</td>
                                            <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600, color: r.diff === 0 ? 'var(--color-text-muted)' : diffColor(r.diff) }}>
                                                {r.diff === 0 ? '0' : signed(r.diff)}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                                <tfoot>
                                    <tr style={{ borderTop: '2px solid var(--color-border)', fontWeight: 600 }}>
                                        <td style={{ padding: '8px 12px' }}>Итого</td>
                                        <td style={{ padding: '8px 12px' }} />
                                        <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatNumber(ourTotal, 0)}</td>
                                        <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatNumber(ffTotal, 0)}</td>
                                        <td style={{ padding: '8px 12px', textAlign: 'right', color: totalDiff === 0 ? 'var(--color-text-muted)' : diffColor(totalDiff) }}>
                                            {totalDiff === 0 ? '0' : signed(totalDiff)}
                                        </td>
                                    </tr>
                                </tfoot>
                            </table>
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16, flexWrap: 'wrap' }}>
                            <button
                                className="btn btn-secondary"
                                onClick={() => handleFfReview('reject')}
                                disabled={ffReviewLoading !== null}
                                style={{ color: 'var(--color-danger)' }}
                            >
                                {ffReviewLoading === 'reject' ? 'Отклонение...' : 'Отказать'}
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={() => handleFfReview('approve')}
                                disabled={ffReviewLoading !== null}
                            >
                                {ffReviewLoading === 'approve' ? 'Применение...' : 'Согласовать'}
                            </button>
                        </div>
                    </div>
                );
            })()}

            {/* Расхождение наполнения с ФФ — отдельным блоком (без клика) */}
            {assembly.ff_mismatch === true && <FfMismatchBlock assemblyId={assembly.id} />}

            {/* Items table */}
            {(() => {
                // Доп. поля: _ppb (кратность по баркоду) и boxes (⌈шт/K⌉ — для Excel).
                const itemsData = (assembly.items || []).map(it => {
                    const k = ppbByBarcode.get(it.barcode) || 0;
                    return { ...it, _ppb: k, _box_size: boxSizeByBarcode.get(it.barcode) || null, boxes: k > 0 ? Math.ceil(it.quantity / k) : null };
                });
                const missingWeightSet = new Set(assembly.weight_missing_barcodes || []);
                const itemCols: Column[] = [
                    { key: 'barcode', label: 'ШК', render: (v: string) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span> },
                    {
                        key: '_wt', label: 'Вес', sortable: false,
                        render: (_v: unknown, row: { barcode: string }) => missingWeightSet.has(row.barcode) ? (
                            <span
                                title="Нет веса — заполните в справочнике «Вес по баркодам»"
                                style={{ fontSize: 10, padding: '1px 6px', borderRadius: 24, background: 'rgba(245,158,11,0.15)', color: 'var(--color-warning)', border: '1px solid rgba(245,158,11,0.3)', whiteSpace: 'nowrap' }}
                            >
                                нет веса
                            </span>
                        ) : <span style={{ color: 'var(--color-success)' }}>✓</span>,
                    },
                    { key: 'product_name', label: 'Товар', render: (v: string) => <span style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block' }}>{v || '\u2014'}</span> },
                    { key: 'article', label: 'Артикул', render: (v: string) => <span style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block', fontFamily: 'monospace', fontSize: 12 }}>{v || '—'}</span> },
                    {
                        key: 'boxes', label: 'Коробок', align: 'right',
                        render: (_v: unknown, row: { quantity: number; _ppb?: number }) => {
                            const k = row._ppb || 0;
                            if (k <= 0) return <span style={{ color: 'var(--color-text-muted)' }}>—</span>;
                            const full = Math.floor(row.quantity / k);
                            const rem = row.quantity % k;
                            return (
                                <span style={{ whiteSpace: 'nowrap' }} title={`${full} полн. коробов по ${k} шт${rem > 0 ? ` + ${rem} шт россыпью (неполный короб)` : ''}`}>
                                    <span style={{ fontWeight: 500 }}>{formatNumber(full, 0)} кор</span>
                                    {rem > 0 && <span style={{ color: 'var(--color-warning)', fontWeight: 600 }}>{` + ${formatNumber(rem, 0)} шт`}</span>}
                                </span>
                            );
                        },
                    },
                    {
                        key: '_box_size', label: 'Размер коробки', align: 'right',
                        render: (v: string | null) => (
                            <span style={{ fontFamily: 'monospace', fontSize: 12, whiteSpace: 'nowrap', color: v ? 'var(--color-text)' : 'var(--color-text-muted)' }}
                                title={v ? undefined : 'Размер коробки не задан (нет данных из машины) — можно указать во вкладке «Кратность»'}>
                                {v || '—'}
                            </span>
                        ),
                    },
                    { key: 'quantity', label: 'В поставке', align: 'right', render: (v: number) => <span style={{ fontWeight: 500 }}>{v}</span> },
                    { key: 'stock_quantity', label: 'На складе', align: 'right', render: (v: number, row: any) => <span style={{ fontWeight: 500, color: v < row.quantity ? 'var(--color-danger)' : 'var(--color-success)' }}>{v}</span> },
                ];
                return (
                    <TanStackDataTable
                        columns={itemCols}
                        data={itemsData}
                        title="Позиции"
                        exportName={`Сборка_${assembly.number}`}
                        enableSorting
                        enablePagination={false}
                        emptyText="Нет позиций"
                    />
                );
            })()}

            {/* History timeline */}
            {history.length > 0 && (
                <div className="glass-card" style={{ padding: 24, marginTop: 16 }}>
                    <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>
                        История
                    </h2>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
                        {history.map((entry, idx) => {
                            const statusInfo = STATUS_MAP[entry.new_status as AssemblyStatus] || { label: entry.new_status, className: '' };
                            const isLast = idx === history.length - 1;
                            return (
                                <div key={entry.id} style={{ display: 'flex', gap: 12, position: 'relative' }}>
                                    {/* Timeline line */}
                                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 20 }}>
                                        <div style={{
                                            width: 10, height: 10, borderRadius: '50%',
                                            background: isLast ? 'var(--color-primary)' : 'var(--color-border)',
                                            flexShrink: 0, marginTop: 6,
                                        }} />
                                        {!isLast && (
                                            <div style={{ width: 2, flex: 1, background: 'var(--color-border)' }} />
                                        )}
                                    </div>
                                    {/* Content */}
                                    <div style={{ paddingBottom: isLast ? 0 : 16, flex: 1 }}>
                                        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                                            <span className={`badge ${statusInfo.className}`} style={{ fontSize: 11 }}>
                                                {statusInfo.label}
                                            </span>
                                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                {formatDateTime(entry.changed_at)}
                                            </span>
                                            {entry.changed_by && (
                                                <span style={{ fontSize: 11, color: 'var(--color-text-muted)', fontStyle: 'italic' }}>
                                                    ({entry.changed_by})
                                                </span>
                                            )}
                                        </div>
                                        {entry.comment && (
                                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                                {entry.comment}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            </>)}

            {/* ─── Modals ──────────────────────────────────────────────────── */}

            {/* Vehicle modal */}
            {showVehicleModal && (
                <div className="modal-overlay" onClick={() => setShowVehicleModal(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
                        <h2 className="modal-title">Назначить машину</h2>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                            <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                                <label className="form-label">Описание машины *</label>
                                <input className="form-input" value={vehicleInfo} onChange={e => setVehicleInfo(e.target.value)} placeholder="Номер, водитель, ТК..." autoFocus />
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
                                    <option value="08:00-12:00">08:00 — 12:00</option>
                                    <option value="12:00-16:00">12:00 — 16:00</option>
                                    <option value="16:00-20:00">16:00 — 20:00</option>
                                    <option value="20:00-00:00">20:00 — 00:00</option>
                                </select>
                            </div>
                            <div className="form-group">
                                <label className="form-label">Стоимость забора, \u20BD *</label>
                                <input className="form-input" type="number" min={0} value={pickupCost} onChange={e => setPickupCost(e.target.value ? Number(e.target.value) : '')} placeholder="15000" />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Дата сдачи на WB *</label>
                                <input className="form-input" type="date" value={deliveryDate} onChange={e => setDeliveryDate(e.target.value)} />
                            </div>
                            <div className="form-group" style={{ gridColumn: '1 / -1', borderTop: '1px solid var(--color-border)', paddingTop: 12, marginTop: 4 }}>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>
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
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={() => setShowVehicleModal(false)}>Отмена</button>
                            <button className="btn btn-primary" onClick={handleAssignVehicle} disabled={actionLoading || !vehicleFormValid}>
                                {actionLoading ? 'Назначение...' : 'Назначить'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Push to FF modal */}
            {showPushModal && assembly && (
                <div className="modal-overlay" onClick={() => { setShowPushModal(false); setPushResult(null); }}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
                        <h2 className="modal-title">Создать заявку на ФФ</h2>
                        {!pushResult ? (
                            <>
                                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16 }}>
                                    {isWmsPush ? (
                                        <>
                                            Создаст заявку на отгрузку у фулфилмента «Целиком» ({ffStatus?.customer_name || 'wmscelicom'}) из
                                            {' '}{assembly.items.length} позиц. ({formatNumber(assembly.items.reduce((s, it) => s + it.quantity, 0), 0)} шт)
                                            и сразу переведёт её в сборку. Склад WB определяется привязкой к поставке на стороне ФФ.
                                            Это <b>реальный заказ</b> у ФФ.
                                        </>
                                    ) : (
                                        <>
                                            Создаст заявку «Доставка на склад МП» у фулфилмента ({ffStatus?.customer_name || 'skladbot'}) из
                                            {' '}{assembly.items.length} позиц. ({formatNumber(assembly.items.reduce((s, it) => s + it.quantity, 0), 0)} шт).
                                            Это <b>реальный заказ</b> у ФФ.
                                        </>
                                    )}
                                </div>
                                <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
                                    {!isWmsPush && (
                                        <>
                                            <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                                                <label className="form-label">Склад МП ({pushForm?.marketplace_name || 'WB'}) *</label>
                                                <select
                                                    className="form-input"
                                                    value={pushWarehouseId}
                                                    onChange={e => setPushWarehouseId(e.target.value ? Number(e.target.value) : '')}
                                                    disabled={pushFormLoading || !pushForm}
                                                    autoFocus
                                                >
                                                    <option value="">{pushFormLoading ? 'Загрузка складов…' : '— выберите склад МП —'}</option>
                                                    {pushForm?.warehouses.map(w => (
                                                        <option key={w.id} value={w.id}>{w.name}</option>
                                                    ))}
                                                </select>
                                                {pushForm?.suggested_warehouse_hint && pushWarehouseId === '' && !pushFormLoading && (
                                                    <div style={{ fontSize: 12, color: 'var(--color-warning)', marginTop: 4 }}>
                                                        Склад заявки «{pushForm.suggested_warehouse_hint}» не найден в списке ФФ — выберите вручную.
                                                    </div>
                                                )}
                                            </div>
                                            <div className="form-group">
                                                <label className="form-label">Тип поставки *</label>
                                                <select className="form-input" value={pushDeliveryType} onChange={e => setPushDeliveryType(e.target.value as 'straight' | 'cross_dock')}>
                                                    {(pushForm?.delivery_types?.length
                                                        ? pushForm.delivery_types
                                                        : [{ value: 'straight', name: 'Прямая' }, { value: 'cross_dock', name: 'Транзит (кросс-док)' }]
                                                    ).map(d => (
                                                        <option key={d.value} value={d.value}>{d.name}</option>
                                                    ))}
                                                </select>
                                            </div>
                                            <div className="form-group" />
                                            <div className="form-group">
                                                <label className="form-label">Дата забора *</label>
                                                <div style={{ display: 'flex', gap: 8, alignItems: 'stretch' }}>
                                                    <input className="form-input" type="date" style={{ flex: 1, minWidth: 0 }} value={pushCollectionDate} onChange={e => setPushCollectionDate(e.target.value)} />
                                                    <button
                                                        type="button"
                                                        className="btn btn-secondary"
                                                        style={{ flexShrink: 0, whiteSpace: 'nowrap' }}
                                                        title="Сегодня + 3 дня"
                                                        onClick={() => { const d = new Date(); d.setDate(d.getDate() + 3); setPushCollectionDate(d.toISOString().slice(0, 10)); }}
                                                    >
                                                        +3 дня
                                                    </button>
                                                </div>
                                            </div>
                                            <div className="form-group">
                                                <label className="form-label">Дата выгрузки *</label>
                                                <input className="form-input" type="date" value={pushUnloadingDate} onChange={e => setPushUnloadingDate(e.target.value)} />
                                            </div>
                                        </>
                                    )}
                                    <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                                        <label className="form-label">Комментарий</label>
                                        <input className="form-input" value={pushComment} onChange={e => setPushComment(e.target.value)} placeholder={`Заявка на сборку ${assembly.number} (DDS)`} />
                                    </div>
                                </div>
                                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                                    <button className="btn btn-secondary" onClick={() => setShowPushModal(false)}>Отмена</button>
                                    <button className="btn btn-primary" onClick={handlePush} disabled={actionLoading || !pushFormValid}>
                                        {actionLoading ? 'Создание...' : 'Создать заявку у ФФ'}
                                    </button>
                                </div>
                            </>
                        ) : (
                            <>
                                <div style={{ color: 'var(--color-success)', fontWeight: 600, marginBottom: 8 }}>
                                    ✓ Заявка создана: {pushResult.ff_number || pushResult.external_id}
                                </div>
                                <div style={{ fontSize: 14, marginBottom: 8 }}>
                                    Отправлено позиций: {pushResult.items_sent} ({formatNumber(pushResult.total_qty, 0)} шт).
                                </div>
                                {pushResult.skipped_barcodes.length > 0 && (
                                    <div style={{ fontSize: 13, color: 'var(--color-warning)', marginBottom: 8 }}>
                                        Не отправлено (нет остатка/карточки у ФФ): {pushResult.skipped_barcodes.length} ШК —
                                        {' '}{pushResult.skipped_barcodes.slice(0, 10).join(', ')}{pushResult.skipped_barcodes.length > 10 ? '…' : ''}
                                    </div>
                                )}
                                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                                    <Link href={`/p/${slug}/warehouse/${assembly.warehouse_id}/ff-request/${pushResult.request.id}`}>
                                        <button className="btn btn-secondary">Открыть ФФ-заявку</button>
                                    </Link>
                                    <button className="btn btn-primary" onClick={() => { setShowPushModal(false); setPushResult(null); }}>Готово</button>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            )}

            {/* Migfull-портал («Натали») — заявка на отгрузку из сборки */}
            {showMigfullModal && assembly && (
                <MigfullModal
                    assemblyId={assembly.id}
                    assemblyNumber={assembly.number}
                    onClose={() => setShowMigfullModal(false)}
                    onSuccess={() => { load(); }}
                />
            )}

            {/* Ship confirmation modal */}
            {showShipModal && (
                <div className="modal-overlay" onClick={() => setShowShipModal(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 480 }}>
                        <h2 className="modal-title">Подтверждение отгрузки</h2>
                        <div style={{ marginBottom: 16, fontSize: 14 }}>
                            <p>Вы уверены, что хотите отгрузить заявку <strong>{assembly.number}</strong>?</p>
                            {assembly.vehicle_info && (
                                <p style={{ color: 'var(--color-text-muted)' }}>Машина: {assembly.vehicle_info}</p>
                            )}
                            <p style={{ color: 'var(--color-text-muted)' }}>
                                Позиций: {assembly.items?.length || 0}, палет: {assembly.pallets_count}
                            </p>
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={() => setShowShipModal(false)}>
                                Отмена
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={handleShip}
                                disabled={actionLoading}
                            >
                                {actionLoading ? 'Отгрузка...' : 'Отгрузить'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Cancel confirmation modal */}
            {showCancelModal && (
                <div className="modal-overlay" onClick={() => setShowCancelModal(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 440 }}>
                        <h2 className="modal-title">Отменить заявку</h2>
                        <div style={{ marginBottom: 16, fontSize: 14 }}>
                            <p>Вы уверены, что хотите отменить заявку <strong>{assembly.number}</strong>?</p>
                            <p style={{ color: 'var(--color-danger)' }}>Это действие нельзя отменить.</p>
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={() => setShowCancelModal(false)}>
                                Нет, оставить
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={handleCancel}
                                disabled={actionLoading}
                                style={{ background: 'var(--color-danger)' }}
                            >
                                {actionLoading ? 'Отмена...' : 'Да, отменить'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Return-to-warehouse confirmation modal */}
            {showReturnModal && (
                <div className="modal-overlay" onClick={() => setShowReturnModal(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 460 }}>
                        <h2 className="modal-title">Вернуть на склад</h2>
                        <div style={{ marginBottom: 16, fontSize: 14 }}>
                            <p>WB не принял поставку по заявке <strong>{assembly.number}</strong>, товар возвращается на склад.</p>
                            <p>Будет создана приёмка-возврат на годный сток, заявка перейдёт в статус <strong>«Возврат на склад»</strong> — после этого её можно <strong>переотгрузить</strong> (новый водитель + новая FBW-поставка) или <strong>закрыть</strong>. Стоимость этой перевозки сохранится в аналитике.</p>
                        </div>
                        <div style={{ marginBottom: 16 }}>
                            <label style={{ fontSize: 12, color: 'var(--color-text-muted)', display: 'block', marginBottom: 4 }}>
                                Склад возврата
                            </label>
                            <select
                                value={returnWarehouseId}
                                onChange={e => setReturnWarehouseId(e.target.value === '' ? '' : Number(e.target.value))}
                                style={{ width: '100%' }}
                            >
                                {/* По умолчанию — склад-источник; можно вернуть на другой ФФ-склад. */}
                                {ffWarehouses.length === 0 && assembly.warehouse_name && (
                                    <option value={assembly.warehouse_id}>{assembly.warehouse_name} (источник)</option>
                                )}
                                {ffWarehouses.map(w => (
                                    <option key={w.id} value={w.id}>
                                        {w.name}{w.id === assembly.warehouse_id ? ' (источник)' : ''}
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={() => setShowReturnModal(false)}>
                                Отмена
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={handleReturn}
                                disabled={actionLoading}
                            >
                                {actionLoading ? 'Возврат...' : 'Вернуть на склад'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

// ─── Helper ──────────────────────────────────────────────────────────────────

function InfoField({ label, value }: { label: string; value: React.ReactNode }) {
    return (
        <div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 14, fontWeight: 500 }}>{value}</div>
        </div>
    );
}

function EditableInfoField({
    label, value, displayValue, type, editable, onSave,
}: {
    label: string; value: string; displayValue: React.ReactNode;
    type: 'date' | 'number' | 'text'; editable: boolean;
    onSave: (val: string) => void;
}) {
    const [editing, setEditing] = useState(false);
    const [inputVal, setInputVal] = useState(value);
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (editing && inputRef.current) {
            inputRef.current.focus();
            if (type === 'number') inputRef.current.select();
        }
    }, [editing, type]);

    const handleSave = () => {
        setEditing(false);
        if (inputVal !== value) onSave(inputVal);
    };

    return (
        <div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>{label}</div>
            {editing && editable ? (
                <input
                    ref={inputRef}
                    type={type}
                    min={type === 'number' ? 0 : undefined}
                    value={inputVal}
                    onChange={(e) => setInputVal(e.target.value)}
                    onBlur={handleSave}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter') inputRef.current?.blur();
                        if (e.key === 'Escape') { setInputVal(value); setEditing(false); }
                    }}
                    className="form-input"
                    style={{ width: type === 'number' ? 80 : 160, padding: '4px 8px', fontSize: 14, fontWeight: 500 }}
                />
            ) : (
                <div
                    onClick={editable ? () => { setInputVal(value); setEditing(true); } : undefined}
                    style={{
                        fontSize: 14,
                        fontWeight: 500,
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 6,
                        cursor: editable ? 'pointer' : undefined,
                        padding: editable ? '2px 8px 2px 0' : undefined,
                        borderRadius: editable ? 6 : undefined,
                        transition: 'background 0.15s',
                    }}
                    title={editable ? 'Нажмите для редактирования' : undefined}
                    onMouseEnter={editable ? (e) => { e.currentTarget.style.background = 'rgba(59,130,246,0.08)'; } : undefined}
                    onMouseLeave={editable ? (e) => { e.currentTarget.style.background = ''; } : undefined}
                >
                    {displayValue || '\u2014'}
                    {editable && (
                        <span style={{ color: 'var(--color-primary, #3b82f6)', fontSize: 13, opacity: 0.6 }}>✎</span>
                    )}
                </div>
            )}
        </div>
    );
}
