'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, Cell } from 'recharts';
import { api } from '@/lib/api';
import { formatDate, formatNumber, exportToExcel } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import KpiCard from '@/components/KpiCard';
import type { Column } from '@/components/DataTable';
import type { AssemblyRequest, AssemblyStatus, LogisticsAnalyticsResponse, LogisticsRouteStat, LogisticsShipmentRow, LogisticsAnomalyType, CostForecastResponse, CostForecastWarehouse, GazelkaConfig, StockTransfer, TransferAssignVehiclePayload, Warehouse } from '@/types/api';
import {
    TRANSFER_STATUS_MAP,
    canSetTransferLogistics,
    gazelkaSourceWarehouseIds,
    isInternalTransfer,
    initialUnitMode,
    toMoney,
    transferDaysStuck,
    transferSkuCount,
    transferStatusLabel,
    transferTotalWeight,
    transferUnits,
    unitCountLabel,
    unitModeToFlag,
    unitShort,
    unitWeightLabel,
} from '@/lib/transfer';
import type { UnitMode } from '@/lib/transfer';
import LogisticsCostByPallets from './components/LogisticsCostByPallets';
import LogisticsCostPerUnit from './components/LogisticsCostPerUnit';
import LogisticsCarriers from './components/LogisticsCarriers';
import LogisticsAnomalies from './components/LogisticsAnomalies';
import CreatePaymentRequestModal from './components/CreatePaymentRequestModal';
import ShipmentPaymentsTab from './components/ShipmentPaymentsTab';
import GazelkaModal from './components/GazelkaModal';
import GazelkaOrdersTab from './components/GazelkaOrdersTab';
import TransferLogisticsTab from './components/TransferLogisticsTab';
import TransferLogisticsModal from './components/TransferLogisticsModal';
import { TransferWorkCard, TransferWorkTableRow, isTransferSelectable } from './components/TransferWorkRow';

// Сегменты аналитики истории отправок.
type AnalyticsView = 'overview' | 'pallets' | 'per_unit' | 'carriers' | 'anomalies' | 'matrix';

const ANOMALY_BADGE: Record<LogisticsAnomalyType, { label: string; className: string }> = {
    no_cost: { label: 'нет цены', className: 'badge-secondary' },
    overpriced: { label: 'завышена', className: 'badge-danger' },
    underpriced: { label: 'занижена', className: 'badge-warning' },
};

// ─── Cost forecast helpers ───────────────────────────────────────────────────

// Корзина размера отгрузки — зеркалит backend `_bucket_for_pallets`.
function bucketForPallets(p: number): string {
    if (p <= 1) return '1';
    if (p <= 3) return '2-3';
    if (p <= 5) return '4-5';
    if (p <= 10) return '6-10';
    return '11+';
}

interface ForecastResult { total: number; cpp: number; low: number; high: number; basis: string; sample: number; }

/** Прогноз стоимости перевозки заявки: склад+размер → склад → глобально. */
function forecastFor(model: CostForecastResponse | null, warehouse: string | null | undefined, pallets: number | null | undefined): ForecastResult | null {
    if (!model || !warehouse || !pallets || pallets <= 0) return null;
    const bucket = bucketForPallets(pallets);
    const wh: CostForecastWarehouse | undefined = model.warehouses.find(w => w.dest_warehouse === warehouse);
    // Decimal-поля приходят из API строками — коэрсим в number для formatNumber.
    let cpp: number | null = null, low = 0, high = 0, basis = '', sample = 0;
    if (wh) {
        const b = wh.buckets.find(x => x.bucket === bucket);
        if (b && b.sample_size >= 2) { cpp = Number(b.cpp); low = Number(b.low); high = Number(b.high); basis = 'склад+размер'; sample = b.sample_size; }
        else if (wh.sample_size >= 2) { cpp = Number(wh.cpp); low = Number(wh.low); high = Number(wh.high); basis = 'склад'; sample = wh.sample_size; }
    }
    if (cpp == null) {
        if (model.sample_size >= 2) { cpp = Number(model.global_cpp); low = Number(model.global_low); high = Number(model.global_high); basis = 'в среднем'; sample = model.sample_size; }
        else return null;
    }
    return { total: cpp * pallets, cpp, low: low * pallets, high: high * pallets, basis, sample };
}

// ─── «Висит N дн»: заявка готова, но не отгружается (как аномалия stuck_shipment) ──
const STUCK_THRESHOLD_DAYS = 2;   // порог из аналитики сборки (stuck_shipment = 2 дн)

/** Сколько дней заявка «висит» готовой/с машиной, но не отгружена. null — не применимо.
 *  ПРИБЛИЖЕНИЕ аномалии stuck_shipment: считаем от actual_ready_date (полночь),
 *  тогда как аналитика берёт точное время перехода в READY из истории (на списке его нет).
 *  Возможно расхождение <1 сут с экраном «Анализ сборки» — допустимо (оба — сигналы, порог общий). */
function daysStuck(item: AssemblyRequest): number | null {
    if (item.status !== 'READY' && item.status !== 'VEHICLE_ASSIGNED') return null;
    if (!item.actual_ready_date) return null;
    const ready = new Date(`${item.actual_ready_date}T00:00:00`);
    if (Number.isNaN(ready.getTime())) return null;
    const days = Math.floor((Date.now() - ready.getTime()) / 86_400_000);
    return days >= 0 ? days : null;
}

/** Номера привязанных заявок ФФ (несколько для migfull/«Натали»). */
function ffRequestNumbers(item: AssemblyRequest): string[] {
    const links = item.ff_links;
    if (links && links.length > 0) {
        const nums = links.map(l => l.ff_request_number).filter((n): n is string => !!n);
        if (nums.length) return nums;
    }
    return item.ff_request_number ? [item.ff_request_number] : [];
}

// ─── Joint (совместная) FBO-поставка: коллапс N сборок в одну карточку ─────────
// Подпись единицы («пал» / «кор») — общий unitShort из lib/transfer: своя копия
// жила здесь до переезда и успела разъехаться по типу аргумента.

/** Строка разбивки забора совместной поставки: один склад-источник. */
interface JointRow {
    number: string;                        // номер сборки этого склада-источника (ASM-…)
    warehouse_id: number;
    warehouse_name: string | null;
    pallets_count: number | null;
    pallet_weight_kg: number | null;       // вес паллеты (коэрсим из строки)
    ff_request_number: string | null;
    status: AssemblyStatus | string;
}

/** Строки разбивки совместной поставки: сам anchor + его siblings, по складу-источнику.
 *  Anchor берёт собственные поля; siblings — поля JointSibling. Сортировка по warehouse_id. */
function jointRows(item: AssemblyRequest): JointRow[] {
    const rows: JointRow[] = [{
        number: item.number,
        warehouse_id: item.warehouse_id,
        warehouse_name: item.warehouse_name ?? null,
        pallets_count: item.pallets_count,
        pallet_weight_kg: item.pallet_weight_kg != null ? Number(item.pallet_weight_kg) : null,
        ff_request_number: ffRequestNumbers(item)[0] ?? null,
        status: item.status,
    }];
    for (const s of item.joint_siblings ?? []) {
        rows.push({
            number: s.number,
            warehouse_id: s.warehouse_id,
            warehouse_name: s.warehouse_name ?? null,
            pallets_count: s.pallets_count ?? null,
            pallet_weight_kg: s.pallet_weight_kg != null ? Number(s.pallet_weight_kg) : null,
            ff_request_number: s.ff_request_number ?? null,
            status: s.status,
        });
    }
    return rows.sort((a, b) => a.warehouse_id - b.warehouse_id);
}

/** Суммарные паллеты совместной поставки: joint_total_pallets либо сумма строк разбивки. */
function jointTotalPallets(item: AssemblyRequest): number {
    if (item.joint_total_pallets != null) return item.joint_total_pallets;
    return jointRows(item).reduce((s, r) => s + (r.pallets_count ?? 0), 0);
}

/** Суммарный вес (кг) совместной поставки: joint_total_weight_kg либо сумма (паллеты × вес паллеты). */
function jointTotalWeight(item: AssemblyRequest): number {
    if (item.joint_total_weight_kg != null) return Number(item.joint_total_weight_kg);
    return jointRows(item).reduce((s, r) => s + (r.pallets_count ?? 0) * (r.pallet_weight_kg ?? 0), 0);
}

/** Склады-источники совместной поставки, ещё не готовые (PENDING/IN_PROGRESS) — для гейта кнопки. */
function jointPendingWarehouses(item: AssemblyRequest): string[] {
    return jointRows(item)
        .filter(r => r.status === 'PENDING' || r.status === 'IN_PROGRESS')
        .map(r => r.warehouse_name || `склад #${r.warehouse_id}`);
}

interface VehiclePrefill {
    info: string;       // госномер (vehicle_info)
    brand: string;
    phone: string;
    firstName: string;
    lastName: string;
}

/** Префилл модалки «Назначить машину» из уже известных данных выбранных заявок.
 *  Приоритет: назначенная машина заявки → данные WB-пропуска (могли завести раньше
 *  в панели «Поставка WB» или подтянуть из кабинета). Берём первую заявку, у которой
 *  что-то есть: машина у всех выбранных заявок одна (bulk-назначение). */
function vehiclePrefill(items: AssemblyRequest[]): VehiclePrefill {
    const empty: VehiclePrefill = { info: '', brand: '', phone: '', firstName: '', lastName: '' };
    for (const it of items) {
        if (it.vehicle_info || it.vehicle_brand || it.driver_phone) {
            return {
                info: it.vehicle_info || '',
                brand: it.vehicle_brand || '',
                phone: it.driver_phone || '',
                firstName: it.driver_first_name || '',
                lastName: it.driver_last_name || '',
            };
        }
    }
    for (const it of items) {
        const wb = it.wb_supply;
        if (!wb) continue;
        if (wb.pass_car_number || wb.pass_car_model || wb.pass_driver_phone
            || wb.pass_driver_first || wb.pass_driver_last) {
            return {
                info: wb.pass_car_number || '',
                brand: wb.pass_car_model || '',
                phone: wb.pass_driver_phone || '',
                firstName: wb.pass_driver_first || '',
                lastName: wb.pass_driver_last || '',
            };
        }
    }
    return empty;
}

/** Коллапс совместных поставок: из плоского списка оставляем по одной заявке-«якорю»
 *  на каждую WB FBO-поставку (минимальный id среди сборок этой поставки в загруженном
 *  списке), остальные joint-сёстры выкидываем, чтобы не рисовать их отдельными карточками.
 *  Не-совместные заявки не трогаем. */
function collapseJoint(items: AssemblyRequest[]): AssemblyRequest[] {
    // Минимальный id по wb_fbo_supply_id среди ЗАГРУЖЕННЫХ совместных заявок.
    const minIdBySupply = new Map<number, number>();
    for (const i of items) {
        if (!i.joint_supply || i.wb_fbo_supply_id == null) continue;
        const cur = minIdBySupply.get(i.wb_fbo_supply_id);
        if (cur == null || i.id < cur) minIdBySupply.set(i.wb_fbo_supply_id, i.id);
    }
    return items.filter(i => {
        if (!i.joint_supply || i.wb_fbo_supply_id == null) return true;
        return minIdBySupply.get(i.wb_fbo_supply_id) === i.id;
    });
}

// ─── Status config ──────────────────────────────────────────────────────────

const STATUS_MAP: Record<AssemblyStatus, { label: string; className: string }> = {
    PENDING:          { label: 'Ожидает сборку',    className: 'badge-warning' },
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

const WB_STATUS_MAP: Record<string, { label: string; className: string }> = {
    ACTIVE:       { label: 'Запланирована',      className: 'badge-warning' },
    ON_DELIVERY:  { label: 'Отгрузка разрешена', className: 'badge-info' },
    IN_PROGRESS:  { label: 'Идёт приёмка',       className: 'badge-info' },
    ACCEPTED:     { label: 'Принята',            className: 'badge-success' },
    CANCELLED:    { label: 'Отклонена',          className: 'badge-secondary' },
};

type GroupBy = 'wb_warehouse' | 'warehouse';

// ─── Component ──────────────────────────────────────────────────────────────

export default function LogisticsPage() {
    const params = useParams();
    const slug = params.slug as string;

    const [items, setItems] = useState<AssemblyRequest[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Tab
    // transfers — переезды между нашими складами: отдельный отчёт, в общие
    // метрики логистики (маршруты на WB) они не ложатся принципиально.
    const [activeTab, setActiveTab] = useState<'active' | 'history' | 'payments' | 'gazelka' | 'transfers'>('active');

    // Справочник складов — один на страницу: оба блока переездов (рабочая
    // секция и вкладка «Переезды») получают его пропом, иначе один и тот же
    // список качался бы заново при каждом переключении вкладок.
    const [warehousesDir, setWarehousesDir] = useState<Warehouse[]>([]);
    useEffect(() => {
        api.getWarehouses().then(setWarehousesDir).catch(() => {});
    }, []);

    // Диплинк на вкладку: ?tab=transfers — из сводки «Перемещения» на странице
    // сборки. Читаем из location, а не useSearchParams: страница клиентская и
    // тянуть за собой Suspense-границу ради одного параметра незачем.
    useEffect(() => {
        try {
            const t = new URLSearchParams(window.location.search).get('tab');
            if (t === 'transfers' || t === 'payments' || t === 'gazelka' || t === 'history') setActiveTab(t);
        } catch { /* SSR — эффект не выполнится, дефолт остаётся */ }
    }, []);
    // History tab — построчные отправки (источник для сортируемой таблицы)
    const [shipmentRows, setShipmentRows] = useState<LogisticsShipmentRow[]>([]);
    const [shipmentsTotal, setShipmentsTotal] = useState(0);
    const [shipmentsTruncated, setShipmentsTruncated] = useState(false);
    const [shipmentsLoading, setShipmentsLoading] = useState(false);
    // Растущий набор брендов для дропдауна (не теряем опции при фильтрации)
    const [brandOptions, setBrandOptions] = useState<string[]>([]);

    // Analytics
    const [analyticsData, setAnalyticsData] = useState<LogisticsAnalyticsResponse | null>(null);
    const [analyticsLoading, setAnalyticsLoading] = useState(false);
    const [analyticsView, setAnalyticsView] = useState<AnalyticsView>('overview');

    // Analytics filters
    const [analyticsDateFrom, setAnalyticsDateFrom] = useState(() => {
        const d = new Date(); d.setDate(d.getDate() - 30);
        return d.toISOString().slice(0, 10);
    });
    const [analyticsDateTo, setAnalyticsDateTo] = useState(() => new Date().toISOString().slice(0, 10));
    const [analyticsBrand, setAnalyticsBrand] = useState('');
    const [analyticsDestFilter, setAnalyticsDestFilter] = useState('');
    const [analyticsCarrier, setAnalyticsCarrier] = useState<number | ''>('');

    // Filters
    const [groupBy, setGroupBy] = useState<GroupBy>('wb_warehouse');
    const [showSoonReady, setShowSoonReady] = useState(false);
    const [showInProgress, setShowInProgress] = useState(false);
    const [warehouseFilter, setWarehouseFilter] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [activeSearch, setActiveSearch] = useState('');
    // Прогнозная модель ₽/паллета (для неназначенных заявок).
    const [forecast, setForecast] = useState<CostForecastResponse | null>(null);

    // View mode
    const [viewMode, setViewMode] = useState<'cards' | 'table'>(() => {
        if (typeof window !== 'undefined') {
            return (localStorage.getItem('logistics_view') as 'cards' | 'table') || 'cards';
        }
        return 'cards';
    });
    const switchViewMode = (mode: 'cards' | 'table') => {
        setViewMode(mode);
        localStorage.setItem('logistics_view', mode);
    };

    // Checkboxes for bulk selection
    const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set());

    // ─── Перемещения в ОБЩЕМ рабочем списке ────────────────────────────────
    // Канон юзера 31.07.2026: переезд едет в том же списке, что и заявки, и
    // отличается тегом «Перемещение». Своё состояние — потому что это ДРУГАЯ
    // сущность с другими эндпоинтами; общее у них — список, чекбоксы и машина.
    const [transfers, setTransfers] = useState<StockTransfer[]>([]);
    const [checkedTransferIds, setCheckedTransferIds] = useState<Set<number>>(new Set());
    /** Тип строки в списке: заявки, перемещения или всё вперемешку. */
    const [typeFilter, setTypeFilter] = useState<'all' | 'assembly' | 'transfer'>('all');

    // ─── Уехали без стоимости: третий срез переездов ───────────────────────
    // Отчёт «Переезды» и вкладка «Оплаты» строятся на ЗАБОРАХ переезда, а забор
    // создаётся только при оформленной логистике. Все 28 старых переездов
    // (TR-1…TR-31) уехали без неё → в отчёте пусто, и юзер справедливо спросил,
    // «где все старые переезды». Этот срез — их рабочий список: заполнил
    // стоимость → переезд появился и в оплатах, и в отчёте.
    const [noCostTransfers, setNoCostTransfers] = useState<StockTransfer[]>([]);
    const [noCostOpen, setNoCostOpen] = useState(false);
    const [checkedNoCostIds, setCheckedNoCostIds] = useState<Set<number>>(new Set());
    /** Переезды, которым сейчас дописываем логистику (модалка). Пусто — закрыта. */
    const [logisticsTargets, setLogisticsTargets] = useState<StockTransfer[]>([]);
    const [logisticsError, setLogisticsError] = useState('');
    /** Зелёная плашка результата: у ошибки своя, красная. */
    const [notice, setNotice] = useState('');

    // Gazelka
    const [gazelkaConfig, setGazelkaConfig] = useState<GazelkaConfig | null>(null);
    const [gazelkaAssemblyId, setGazelkaAssemblyId] = useState<number | null>(null);
    const [gazelkaAssemblyNumber, setGazelkaAssemblyNumber] = useState<string>('');
    /** Что отдаём Газельке: заявку или ПЕРЕЕЗД — у них разные draft/send. */
    const [gazelkaKind, setGazelkaKind] = useState<'assembly' | 'transfer'>('assembly');
    const [showGazelkaModal, setShowGazelkaModal] = useState(false);

    // Payment request modal
    const [paymentShipmentId, setPaymentShipmentId] = useState<number | undefined>(undefined);
    const [showPaymentModal, setShowPaymentModal] = useState(false);

    // Vehicle modal
    const [showVehicleModal, setShowVehicleModal] = useState(false);
    const [vehicleInfo, setVehicleInfo] = useState('');
    const [vehicleBrand, setVehicleBrand] = useState('');
    const [driverPhone, setDriverPhone] = useState('');
    const [driverFirstName, setDriverFirstName] = useState('');
    const [driverLastName, setDriverLastName] = useState('');
    const [carrierInn, setCarrierInn] = useState('');
    const [carrierName, setCarrierName] = useState('');
    const [selectedIds, setSelectedIds] = useState<number[]>([]);
    // Переезды, попавшие в ту же модалку. Реквизиты у них ОБЩИЕ на пачку
    // (контракт bulk-эндпоинта переездов), поэтому не пер-строчная карта, как
    // у заявок, а один набор полей.
    const [selectedTransferIds, setSelectedTransferIds] = useState<number[]>([]);
    const [transferParams, setTransferParams] = useState({
        pickup_date: '', pickup_time_slot: '', pickup_cost: '' as number | '', delivery_date: '',
    });
    // Транспортная единица переездов в той же модалке. Раньше сюда жёстко
    // уходили три null — размер груза из Листа логиста было не поправить вообще,
    // хотя логист узнаёт «сколько палет» ровно в момент посадки на машину.
    // Режим трёхпозиционный: «не менять» обязателен для пачки с РАЗНОЙ единицей
    // (иначе одно нажатие перевернуло бы коробочные переезды в паллетные).
    const [transferUnitMode, setTransferUnitMode] = useState<UnitMode>('keep');
    const [transferPallets, setTransferPallets] = useState<number | ''>('');
    const [transferPalletWeight, setTransferPalletWeight] = useState<number | ''>('');
    const [actionLoading, setActionLoading] = useState(false);

    // Per-request params in modal: { [requestId]: { pickup_date, pickup_time_slot, pickup_cost, delivery_date } }
    interface PerRequestParams {
        pickup_date: string;
        pickup_time_slot: string;
        pickup_cost: number | '';
        delivery_date: string;
    }
    const [perRequestParams, setPerRequestParams] = useState<Record<number, PerRequestParams>>({});

    /** Показывать кнопку «Отправить в Газельку» только если:
     *  - Газелька настроена
     *  - склад заявки совпадает со складом Газельки
     *  - статус READY или VEHICLE_ASSIGNED
     *  - заявка ещё НЕ отправлена (via_gazelka=false): активная связь SENT/MATCHED →
     *    повторная отправка плодит дубль в Газельке. Провалившаяся отправка (FAILED)
     *    даёт via_gazelka=false → кнопка остаётся для легитимного ре-сенда. */
    const isGazelkaEligible = (item: AssemblyRequest): boolean => {
        if (!gazelkaConfig?.configured) return false;
        if (item.via_gazelka) return false;
        if (item.warehouse_name !== gazelkaConfig.warehouse_name) return false;
        return item.status === 'READY' || item.status === 'VEHICLE_ASSIGNED';
    };

    const openGazelkaModal = (item: AssemblyRequest) => {
        setGazelkaKind('assembly');
        setGazelkaAssemblyId(item.id);
        setGazelkaAssemblyNumber(item.number);
        setShowGazelkaModal(true);
    };

    /** Та же модалка для ПЕРЕЕЗДА — отличие только в kind (другие draft/send). */
    const openGazelkaTransferModal = (t: StockTransfer) => {
        setGazelkaKind('transfer');
        setGazelkaAssemblyId(t.id);
        setGazelkaAssemblyNumber(t.number);
        setShowGazelkaModal(true);
    };

    /**
     * Склады, с которых Газелька возит. Конфиг уже загружен для заявок — второй
     * раз его не спрашиваем; гейт переезда идёт по складу-ИСТОЧНИКУ.
     */
    const gazelkaWarehouseIds = useMemo(() => gazelkaSourceWarehouseIds(gazelkaConfig), [gazelkaConfig]);

    // ─── Load data ────────────────────────────────────────────────────────

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            // Load READY and VEHICLE_ASSIGNED
            const readyResp = await api.getAssemblyRequests({ status: 'READY', limit: 500 });
            const vehicleResp = await api.getAssemblyRequests({ status: 'VEHICLE_ASSIGNED', limit: 500 });

            let allItems = [...readyResp.items, ...vehicleResp.items];

            // Optionally load IN_PROGRESS (all assemblies currently being worked on)
            if (showInProgress) {
                const inProgressResp = await api.getAssemblyRequests({ status: 'IN_PROGRESS', limit: 500 });
                allItems = [...allItems, ...inProgressResp.items];
            }

            // Optionally load "soon ready" (PENDING/IN_PROGRESS with estimated_ready_date <= today+2)
            if (showSoonReady) {
                const today = new Date();
                const twoDaysLater = new Date(today);
                twoDaysLater.setDate(twoDaysLater.getDate() + 2);
                const dateTo = twoDaysLater.toISOString().split('T')[0];

                const pendingResp = await api.getAssemblyRequests({ status: 'PENDING', date_to: dateTo, limit: 500 });
                const inProgressResp = await api.getAssemblyRequests({ status: 'IN_PROGRESS', date_to: dateTo, limit: 500 });

                // Filter by estimated_ready_date
                const soonItems = [...pendingResp.items, ...inProgressResp.items].filter(item => {
                    if (!item.estimated_ready_date) return false;
                    return new Date(item.estimated_ready_date) <= twoDaysLater;
                });

                allItems = [...allItems, ...soonItems];
            }

            // Deduplicate by id (in case showInProgress + showSoonReady overlap)
            const seen = new Set<number>();
            allItems = allItems.filter(item => {
                if (seen.has(item.id)) return false;
                seen.add(item.id);
                return true;
            });

            setItems(allItems);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [showSoonReady, showInProgress]);

    useEffect(() => { load(); }, [load]);

    /**
     * Переезды рабочего списка: READY (ждут машину) + VEHICLE_ASSIGNED (машина
     * уже назначена). Два запроса, а не один без фильтра: `status` на бэкенде
     * одно­значный, а тянуть ВСЕ переезды (кап 500) значит вымыть свежие READY
     * давно принятыми. VEHICLE_ASSIGNED обязателен: без него строка исчезала бы
     * из списка ровно в момент назначения, унося «Изменить/Снять машину».
     */
    const loadTransfers = useCallback(async () => {
        try {
            const [ready, assigned] = await Promise.all([
                api.getTransfers(false, undefined, { status: 'READY' }),
                api.getTransfers(false, undefined, { status: 'VEHICLE_ASSIGNED' }),
            ]);
            const rows = [...ready, ...assigned].sort((a, b) => b.id - a.id);
            setTransfers(rows);
            // Выбор чистим: строка могла уехать из среза (отправлена другим окном).
            setCheckedTransferIds(prev => new Set([...prev].filter(id => rows.some(r => r.id === id))));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки перемещений');
        }
    }, []);

    useEffect(() => { loadTransfers(); }, [loadTransfers]);

    /**
     * Уехавшие переезды БЕЗ стоимости забора — одним запросом (`status_in`),
     * а не двумя: рядом с двумя срезами рабочего списка третий round-trip на
     * каждый статус заметен при каждом открытии страницы.
     *
     * RETURNED/CLOSED сюда не берём намеренно: везли их тоже, но рабочий
     * список логиста — про то, что ещё едет и ждёт денег; терминальные разборы
     * живут в отчёте «Переезды».
     */
    const loadNoCostTransfers = useCallback(async () => {
        try {
            const rows = await api.getTransfers(false, undefined, {
                statuses: ['SHIPPED', 'DELIVERED'],
                hasPickupCost: false,
            });
            // Внутренние перекладки (склад сам в себя) сюда не попадают: платить
            // за них не за что, и в списке «дай денег этому рейсу» они были бы
            // вечными висяками, которые никто никогда не закроет.
            const sorted = [...rows]
                .filter(t => !isInternalTransfer(t))
                .sort((a, b) => b.id - a.id);
            setNoCostTransfers(sorted);
            // Строка могла уйти из среза (стоимость завели в другом окне) —
            // выбор чистим, иначе массовая кнопка считает призраков.
            setCheckedNoCostIds(prev => new Set([...prev].filter(id => sorted.some(r => r.id === id))));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки уехавших перемещений');
        }
    }, []);

    useEffect(() => { loadNoCostTransfers(); }, [loadNoCostTransfers]);

    // Прогнозная модель грузится один раз (не зависит от фильтров).
    useEffect(() => { api.getCostForecast().then(setForecast).catch(() => {}); }, []);

    // Конфиг Газельки — один раз при маунте.
    useEffect(() => {
        let aborted = false;
        api.getGazelkaConfig().then(cfg => {
            if (!aborted) setGazelkaConfig(cfg);
        }).catch(() => {});
        return () => { aborted = true; };
    }, []);

    // ─── Load shipments (построчная история отправок за период) ──────────────
    // Грузим ВЕСЬ набор за период — клиентская сортировка идёт по всему периоду,
    // а не по одной странице (фикс бага сортировки).

    const loadShipments = useCallback(async () => {
        setShipmentsLoading(true);
        try {
            const resp = await api.getShipmentsList({
                date_from: analyticsDateFrom || undefined,
                date_to: analyticsDateTo || undefined,
                brands: analyticsBrand || undefined,
                carrier_id: analyticsCarrier || undefined,
                dest_warehouse: analyticsDestFilter || undefined,
            });
            setShipmentRows(resp.items);
            setShipmentsTotal(resp.total);
            setShipmentsTruncated(resp.truncated);
            // Растим список брендов (union) — чтобы фильтр не терял опции.
            setBrandOptions(prev => {
                const next = new Set(prev);
                for (const r of resp.items) {
                    if (r.brands) r.brands.split(',').forEach(b => { const t = b.trim(); if (t) next.add(t); });
                }
                return Array.from(next).sort((a, b) => a.localeCompare(b, 'ru'));
            });
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки отправок');
        }
        setShipmentsLoading(false);
    }, [analyticsDateFrom, analyticsDateTo, analyticsBrand, analyticsCarrier, analyticsDestFilter]);

    useEffect(() => { if (activeTab === 'history') loadShipments(); }, [activeTab, loadShipments]);

    // ─── Load analytics ────────────────────────────────────────────────────
    // Аналитика — обзор по периоду/бренду (подрядчик/склад — drill-фильтры таблицы).

    const loadAnalytics = useCallback(async () => {
        setAnalyticsLoading(true);
        try {
            const resp = await api.getShipmentAnalytics({
                date_from: analyticsDateFrom || undefined,
                date_to: analyticsDateTo || undefined,
                brands: analyticsBrand || undefined,
            });
            setAnalyticsData(resp);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки аналитики');
        }
        setAnalyticsLoading(false);
    }, [analyticsDateFrom, analyticsDateTo, analyticsBrand]);

    useEffect(() => { if (activeTab === 'history') loadAnalytics(); }, [activeTab, loadAnalytics]);

    // ─── Filtered items ─────────────────────────────────────────────────

    const searchNorm = activeSearch.trim().toLowerCase();
    const filteredItems = items.filter(i => {
        if (warehouseFilter) {
            const val = groupBy === 'wb_warehouse' ? i.effective_wb_warehouse : i.warehouse_name;
            if (val !== warehouseFilter) return false;
        }
        if (statusFilter && i.status !== statusFilter) return false;
        if (searchNorm) {
            // Поиск по нашей заявке (№) и по ФБО-поставке (WB id / имя).
            const hay = `${i.number || ''} ${i.wb_supply_id_wb || ''} ${i.wb_supply_name || ''}`.toLowerCase();
            if (!hay.includes(searchNorm)) return false;
        }
        return true;
    });

    // ─── Collapse joint supplies ──────────────────────────────────────────
    // Совместная поставка (N сборок на одну WB FBO-поставку) схлопывается в одну
    // карточку-«якорь» (мин. id среди сборок поставки); остальные сёстры выкидываем.

    const displayItems = collapseJoint(filteredItems);

    // ─── Перемещения: те же фильтры, что и у заявок ───────────────────────
    // Имена концов маршрута отдаёт сам список; справочник складов — фолбэк.
    const transferWhName = (id: number, name?: string | null) =>
        name || warehousesDir.find(w => w.id === id)?.name || `Склад ${id}`;
    const transferFromName = (t: StockTransfer) => transferWhName(t.from_warehouse_id, t.from_warehouse_name);
    const transferToName = (t: StockTransfer) => transferWhName(t.to_warehouse_id, t.to_warehouse_name);
    /** Ключ группировки переезда: аналог WB-склада — склад-ПОЛУЧАТЕЛЬ. */
    const transferGroupKey = (t: StockTransfer) =>
        groupBy === 'wb_warehouse' ? transferToName(t) : transferFromName(t);

    /** Подгруппа переезда — второй конец маршрута (зеркало level2Key заявки). */
    const transferSubGroupKey = (t: StockTransfer) =>
        groupBy === 'wb_warehouse' ? transferFromName(t) : transferToName(t);

    // ─── Опции фильтров: по ОБОИМ типам документов ─────────────────────────
    // Дропдаун строился только по заявкам: склад, где сегодня есть ТОЛЬКО
    // переезды, в «Все склады» не появлялся вовсе, а при «Только перемещения»
    // оба дропдауна оказывались пустыми — фильтровать было нечем.
    const warehouseOptions = (() => {
        const key = groupBy === 'wb_warehouse'
            ? (i: AssemblyRequest) => i.effective_wb_warehouse || ''
            : (i: AssemblyRequest) => i.warehouse_name || '';
        const names = new Set<string>();
        if (typeFilter !== 'transfer') for (const i of items) { const v = key(i); if (v) names.add(v); }
        if (typeFilter !== 'assembly') for (const t of transfers) { const v = transferGroupKey(t); if (v) names.add(v); }
        return Array.from(names).sort((a, b) => a.localeCompare(b, 'ru'));
    })();

    // Коды статусов у заявки и переезда общие (READY / VEHICLE_ASSIGNED), поэтому
    // один дропдаун фильтрует оба списка без перевода — но опции обязаны прийти
    // из обоих, иначе статус, который есть только у переездов, недоступен.
    const statusOptions: string[] = (() => {
        const set = new Set<string>();
        if (typeFilter !== 'transfer') for (const i of items) set.add(i.status);
        if (typeFilter !== 'assembly') for (const t of transfers) set.add(t.status);
        return Array.from(set);
    })();

    const filteredTransfers = typeFilter === 'assembly' ? [] : transfers.filter(t => {
        if (warehouseFilter && transferGroupKey(t) !== warehouseFilter) return false;
        // Коды статусов у переезда и заявки общие (READY / VEHICLE_ASSIGNED),
        // поэтому один дропдаун фильтрует и то, и другое без перевода.
        if (statusFilter && t.status !== statusFilter) return false;
        if (searchNorm && !t.number.toLowerCase().includes(searchNorm)) return false;
        return true;
    });

    // ─── Grouping ─────────────────────────────────────────────────────────

    const grouped = groupItems(typeFilter === 'transfer' ? [] : displayItems, groupBy);
    // Переезды подмешиваем в ТЕ ЖЕ группы. Группы, которых у заявок нет
    // (переезд на склад, куда сборок сейчас нет), добавляем — иначе строка
    // молча исчезла бы из списка, хотя фильтры её пропустили.
    const groupedAll: (Group & { transfers: StockTransfer[] })[] = (() => {
        const out = grouped.map(g => ({ ...g, transfers: [] as StockTransfer[] }));
        const byKey = new Map(out.map(g => [g.key, g]));
        for (const t of filteredTransfers) {
            const key = transferGroupKey(t);
            let g = byKey.get(key);
            if (!g) {
                g = { key, label: key, items: [], subGroups: [], transfers: [] };
                byKey.set(key, g);
                out.push(g);
            }
            g.transfers.push(t);
        }
        return out;
    })();

    // ─── Summary ──────────────────────────────────────────────────────────
    // Для совместного якоря считаем по всей поставке (joint-итоги), а не по одной сборке.

    const totalRequests = displayItems.length;
    // 🔴 Палеты и короба НЕ складываются: это разные единицы, и общий счётчик
    // «Палет» врал ровно так же, как врала колонка «Палеты» на коробочных
    // документах (58dbf8d4). Совместную поставку считаем паллетной — она такой
    // и есть, joint-итоги подписаны «пал» во всей разбивке.
    const totalPallets = displayItems.reduce(
        (s, i) => s + (i.joint_supply ? jointTotalPallets(i) : (i.shipped_as_boxes ? 0 : i.pallets_count)), 0);
    const totalBoxes = displayItems.reduce(
        (s, i) => s + (!i.joint_supply && i.shipped_as_boxes ? i.pallets_count : 0), 0);
    const totalWeight = displayItems.reduce((s, i) => s + (i.joint_supply ? jointTotalWeight(i) : (Number(i.total_weight_kg) || 0)), 0);

    // ─── Checked items summary ──────────────────────────────────────────

    // Итоги списка включают переезды: логисту важен весь объём, который он
    // сегодня сажает на машины, а не только WB-часть.
    const totalTransfers = filteredTransfers.length;
    /** Единицы переездов по типу: паллетные и коробочные считаются раздельно. */
    const transferUnitsOf = (rows: StockTransfer[], boxes: boolean) =>
        rows.reduce((s, t) => s + (!!t.shipped_as_boxes === boxes ? (t.pallets_count || 0) : 0), 0);
    // Имена с суффиксом Total — не косметика: `transferPallets` уже занято
    // состоянием ПОЛЯ ФОРМЫ назначения машины (number | ''), и одноимённая
    // сводная переменная затеняла бы его в KPI, складывая строку с числом.
    const transferPalletsTotal = transferUnitsOf(filteredTransfers, false);
    const transferBoxesTotal = transferUnitsOf(filteredTransfers, true);
    const transferWeight = filteredTransfers.reduce((s, t) => s + (transferTotalWeight(t) || 0), 0);

    const checkedTransfers = filteredTransfers.filter(t => checkedTransferIds.has(t.id));
    // Страховка от рассинхрона выбор↔данные: переезд могли отметить в READY, а
    // затем отправить из другого окна — в назначение машины он уже не годится.
    // Газельку исключаем по той же причине, что и у заявок: машину назначает агрегатор.
    const assignableCheckedTransferIds = checkedTransfers
        .filter(t => isTransferSelectable(t) && !t.via_gazelka).map(t => t.id);
    const checkedTransferPallets = transferUnitsOf(checkedTransfers, false);
    const checkedTransferBoxes = transferUnitsOf(checkedTransfers, true);
    const checkedTransferWeight = checkedTransfers.reduce((s, t) => s + (transferTotalWeight(t) || 0), 0);

    const toggleCheckedTransfer = (id: number) => {
        setCheckedTransferIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    const checkedItems = displayItems.filter(i => checkedIds.has(i.id));
    // Страховка от рассинхрона checkedIds↔данные: заявку могли отметить в READY, затем
    // отправить в Газельку (via_gazelka стал true после load) — из назначения машины её исключаем.
    const assignableCheckedIds = checkedItems.filter(i => !i.via_gazelka).map(i => i.id);
    const checkedPallets = checkedItems.reduce(
        (s, i) => s + (i.joint_supply ? jointTotalPallets(i) : (i.shipped_as_boxes ? 0 : i.pallets_count)), 0);
    const checkedBoxes = checkedItems.reduce(
        (s, i) => s + (!i.joint_supply && i.shipped_as_boxes ? i.pallets_count : 0), 0);
    const checkedWeight = checkedItems.reduce((s, i) => s + (i.joint_supply ? jointTotalWeight(i) : (Number(i.total_weight_kg) || 0)), 0);

    const toggleChecked = (id: number) => {
        setCheckedIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    // ─── Actions ──────────────────────────────────────────────────────────

    const openVehicleModal = (ids: number[], transferIds: number[] = []) => {
        setSelectedIds(ids);
        setSelectedTransferIds(transferIds);
        // Префилл переездов: если у выбранных машина уже есть — подставляем её
        // реквизиты, чтобы «Изменить машину» не начиналась с пустой формы.
        const preT = transfers.find(t => transferIds.includes(t.id) && t.vehicle_info);
        setTransferParams({
            pickup_date: preT?.pickup_date || '',
            pickup_time_slot: preT?.pickup_time_slot || '',
            pickup_cost: toMoney(preT?.pickup_cost) ?? '',
            delivery_date: preT?.delivery_date || '',
        });
        // Транспортная единица: у ОДНОГО переезда она известна — подставляем её
        // и текущие количество/вес. У пачки единица бывает разной, поэтому
        // «не менять» и пустые поля: любое другое значение уехало бы на все.
        const onlyTransfer = transferIds.length === 1
            ? transfers.find(t => t.id === transferIds[0]) ?? null
            : null;
        setTransferUnitMode(initialUnitMode(onlyTransfer ? onlyTransfer.shipped_as_boxes : null));
        setTransferPallets(onlyTransfer?.pallets_count ?? '');
        setTransferPalletWeight(onlyTransfer ? (toMoney(onlyTransfer.pallet_weight_kg) ?? '') : '');
        // Префилл: если пропуск уже заведён (вручную в панели «Поставка WB» или
        // подтянут из кабинета) — подставляем его данные в ячейки, чтобы логист
        // не перевводил номер/марку/телефон.
        const pre = vehiclePrefill(items.filter(i => ids.includes(i.id)));
        // Выбраны только переезды — префилл берём с их машины: иначе форма
        // пустая там, где данные уже есть.
        const preFromTransfer = ids.length === 0 && preT;
        setVehicleInfo(preFromTransfer ? (preT.vehicle_info || '') : pre.info);
        setVehicleBrand(preFromTransfer ? (preT.vehicle_brand || '') : pre.brand);
        setDriverPhone(preFromTransfer ? (preT.driver_phone || '') : pre.phone);
        setDriverFirstName(preFromTransfer ? (preT.driver_first_name || '') : pre.firstName);
        setDriverLastName(preFromTransfer ? (preT.driver_last_name || '') : pre.lastName);
        const params: Record<number, PerRequestParams> = {};
        for (const id of ids) {
            params[id] = { pickup_date: '', pickup_time_slot: '', pickup_cost: '', delivery_date: '' };
        }
        setPerRequestParams(params);
        setShowVehicleModal(true);
    };

    // Все поля водителя/машины обязательны — их состав совпадает с WB-пропуском,
    // который WB не примет без единого пропущенного поля.
    // Параметры ПЕРЕЕЗДА обязательными не делаем: бэкенд принимает назначение
    // без даты и стоимости (переезд между своими складами возят и без забора),
    // а выдумывать более строгую валидацию, чем контракт, — прятать рабочий
    // сценарий. Заявочная часть проверяется как раньше, построчно.
    const vehicleFormValid = !!(vehicleInfo.trim() && vehicleBrand.trim() && driverPhone.trim()
        && driverFirstName.trim() && driverLastName.trim()
        && (selectedIds.length > 0 || selectedTransferIds.length > 0)
        && selectedIds.every(id => {
            const p = perRequestParams[id];
            return p && p.pickup_date && p.pickup_time_slot && p.pickup_cost !== '' && p.delivery_date;
        }));

    const updateParam = (id: number, field: keyof PerRequestParams, value: string | number) => {
        setPerRequestParams(prev => ({
            ...prev,
            [id]: { ...prev[id], [field]: value },
        }));
    };

    /**
     * Назначение машины на СМЕШАННЫЙ выбор: заявки + перемещения.
     *
     * Бэкенд про смесь не знает и знать не должен — разводим на клиенте и
     * зовём два существующих bulk-эндпоинта. Каждый вызов ловим отдельно:
     * частичный отказ («заявки прошли, переезды нет») обязан быть виден, иначе
     * логист уверен, что посадил на машину всё, а половина осталась ждать.
     */
    const handleAssignVehicle = async () => {
        if (!vehicleFormValid) return;
        setActionLoading(true);
        setError('');
        const done: string[] = [];
        const failed: string[] = [];
        try {
            const bulkItems = selectedIds.map(id => {
                const p = perRequestParams[id];
                return {
                    request_id: id,
                    pickup_date: p.pickup_date,
                    pickup_time_slot: p.pickup_time_slot,
                    pickup_cost: Number(p.pickup_cost),
                    delivery_date: p.delivery_date,
                };
            });
            const carrierPayload = {
                carrier_inn: carrierInn.trim() || null,
                carrier_name: carrierName.trim() || null,
            };
            const driverPayload = {
                vehicle_info: vehicleInfo.trim(),
                vehicle_brand: vehicleBrand.trim(),
                driver_phone: driverPhone.trim(),
                driver_first_name: driverFirstName.trim(),
                driver_last_name: driverLastName.trim(),
            };
            if (selectedIds.length > 0) {
                try {
                    if (selectedIds.length === 1) {
                        await api.assignVehicle(selectedIds[0], {
                            ...driverPayload,
                            ...carrierPayload,
                            ...bulkItems[0],
                        });
                    } else {
                        await api.assignVehicleBulk({
                            ...driverPayload,
                            ...carrierPayload,
                            items: bulkItems,
                        });
                    }
                    done.push(`заявок: ${selectedIds.length}`);
                } catch (e: unknown) {
                    failed.push(`заявки (${selectedIds.length}) — ${e instanceof Error ? e.message : 'ошибка'}`);
                }
            }
            if (selectedTransferIds.length > 0) {
                // Реквизиты переездов общие на пачку, пер-строчных полей у них
                // нет. Транспортная единица теперь приходит из формы: пустое
                // поле — null («не трогать»), режим «не менять» — null во флаге
                // (иначе дефолт «паллеты» перевернул бы коробочный переезд).
                const transferPayload = {
                    ...driverPayload,
                    ...carrierPayload,
                    logistics_by_warehouse: false,
                    // Даты и стоимость подпись формы разрешает не заполнять —
                    // значит шлём null, а не «». Пустая строка в `date | None`
                    // даёт 422 (Pydantic её не коэрсит), и вся пачка отваливалась
                    // с сырым текстом валидатора; ноль вместо пустой стоимости
                    // означал бы «везли бесплатно».
                    pickup_date: transferParams.pickup_date || null,
                    pickup_time_slot: transferParams.pickup_time_slot,
                    pickup_cost: transferParams.pickup_cost === '' ? null : Number(transferParams.pickup_cost),
                    delivery_date: transferParams.delivery_date || null,
                    pallets_count: transferPallets === '' ? null : Number(transferPallets),
                    pallet_weight_kg: transferPalletWeight === '' ? null : Number(transferPalletWeight),
                    shipped_as_boxes: unitModeToFlag(transferUnitMode),
                };
                try {
                    if (selectedTransferIds.length === 1) {
                        await api.assignTransferVehicle(selectedTransferIds[0], transferPayload);
                    } else {
                        await api.assignTransferVehicleBulk(selectedTransferIds, transferPayload);
                    }
                    done.push(`перемещений: ${selectedTransferIds.length}`);
                } catch (e: unknown) {
                    failed.push(`перемещения (${selectedTransferIds.length}) — ${e instanceof Error ? e.message : 'ошибка'}`);
                }
            }
            if (failed.length === 0) {
                setShowVehicleModal(false);
                setCheckedIds(new Set());
                setCheckedTransferIds(new Set());
                setCarrierInn('');
                setCarrierName('');
            } else {
                // Часть прошла — говорим ЧТО именно, и оставляем модалку
                // открытой, чтобы можно было повторить только неудавшееся.
                setError(done.length > 0
                    ? `Назначено — ${done.join(', ')}. Не удалось: ${failed.join('; ')}`
                    : `Не удалось назначить: ${failed.join('; ')}`);
            }
            await Promise.all([load(), loadTransfers()]);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    // ─── Действия по перемещению (строка общего списка) ────────────────────

    const runTransferAction = async (fn: () => Promise<unknown>, errPrefix: string) => {
        setActionLoading(true);
        setError('');
        try {
            await fn();
            await loadTransfers();
        } catch (e: unknown) {
            setError(`${errPrefix}: ${e instanceof Error ? e.message : 'ошибка'}`);
        }
        setActionLoading(false);
    };

    const handleUnassignTransfer = (id: number) => {
        const t = transfers.find(x => x.id === id);
        if (!confirm(`Снять машину с ${t?.number || `#${id}`}? Переезд вернётся в «Готово», транспортная единица груза останется как есть.`)) return;
        runTransferAction(() => api.unassignTransferVehicle(id), 'Снятие машины');
    };

    const handleSendTransfer = (id: number) => {
        const t = transfers.find(x => x.id === id);
        if (!confirm(`Отправить ${t?.number || `#${id}`}? Товар спишется со склада-источника и повиснет транзитом на получателе.`)) return;
        runTransferAction(() => api.sendTransfer(id), 'Отправка перемещения');
    };

    // ─── Оживление уехавших переездов (перевозчик и стоимость задним числом) ──

    const toggleCheckedNoCost = (id: number) => {
        setCheckedNoCostIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    const openLogisticsModal = (ids: number[]) => {
        const rows = noCostTransfers.filter(t => ids.includes(t.id) && canSetTransferLogistics(t.status));
        if (rows.length === 0) return;
        setLogisticsError('');
        setNotice('');
        setLogisticsTargets(rows);
    };

    /**
     * Склад забора выбранных переездов — только когда он ОДИН: чекбокс
     * «логистику оказывает склад забора» берёт перевозчика из контрагента
     * склада, и на пачке с разными источниками обещать этого нельзя.
     */
    const logisticsPickupWarehouse = (() => {
        const ids = new Set(logisticsTargets.map(t => t.from_warehouse_id));
        if (ids.size !== 1) return null;
        return warehousesDir.find(w => w.id === [...ids][0]) ?? null;
    })();

    const handleSetTransferLogistics = async (payload: TransferAssignVehiclePayload) => {
        const rows = logisticsTargets;
        if (rows.length === 0) return;
        setActionLoading(true);
        setLogisticsError('');
        try {
            if (rows.length === 1) await api.setTransferLogistics(rows[0].id, payload);
            else await api.setTransferLogisticsBulk(rows.map(t => t.id), payload);
            setLogisticsTargets([]);
            setCheckedNoCostIds(new Set());
            // Говорим прямо, ЧТО изменилось: иначе строка просто исчезает из
            // списка, и непонятно, доехали деньги до оплат или потерялись.
            setNotice(`${rows.map(t => t.number).join(', ')} — забор создан: переезд теперь виден во вкладках «Оплаты» и «Переезды».`);
            await loadNoCostTransfers();
        } catch (e: unknown) {
            setLogisticsError(e instanceof Error ? e.message : 'Ошибка');
        }
        setActionLoading(false);
    };

    const handleShip = async (id: number) => {
        setActionLoading(true);
        setError('');
        try {
            await api.shipAssembly(id);
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка отгрузки');
        }
        setActionLoading(false);
    };

    // Отгрузка совместной поставки разом: бэк отгружает все назначенные сборки поставки.
    const handleShipJoint = async (anchorId: number) => {
        setActionLoading(true);
        setError('');
        try {
            await api.shipJoint(anchorId);
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка отгрузки');
        }
        setActionLoading(false);
    };

    const handleUnassignVehicle = async (id: number) => {
        if (!confirm('Отменить назначение машины? Заявка вернётся в статус "Готово".')) return;
        setActionLoading(true);
        setError('');
        try {
            await api.unassignVehicle(id);
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка отмены');
        }
        setActionLoading(false);
    };

    // ─── Export ───────────────────────────────────────────────────────────

    /**
     * Выгрузка рабочего списка. Переезды выгружаются РЯДОМ с заявками, одной
     * таблицей и с колонкой типа документа: на экране они уже один список, и
     * Excel без них означал бы, что половина работы логиста не выгружается.
     * Колонки общие, потому что смысл совпадает: «куда» у заявки — склад сдачи
     * WB, у переезда — склад-получатель.
     */
    const handleExport = () => {
        const assemblyRows = items.map(i => ({
            'Тип': 'Заявка',
            '№': i.number,
            'Заявка ФФ': ffRequestNumbers(i).join(', '),
            'Статус': STATUS_MAP[i.status]?.label || i.status,
            'Висит дн': daysStuck(i) ?? '',
            'Склад забора': i.warehouse_name || '',
            'Куда (склад WB / получатель)': i.effective_wb_warehouse || '',
            'Поставка FBO': i.wb_supply_name || '',
            'Кол-во ед.': i.pallets_count,
            'Единица': i.shipped_as_boxes ? 'короба' : 'паллеты',
            'Общий вес': i.total_weight_kg || 0,
            'Стоимость забора': '' as number | '',
            'Машина': i.vehicle_info || '',
            'Дата готовности': i.estimated_ready_date || '',
        }));
        const transferRows = transfers.map(t => ({
            'Тип': 'Перемещение',
            '№': t.number,
            // Связки ФФ приходят только в деталке переезда — в списке их нет,
            // и выдумывать пустой join по отсутствующему полю незачем.
            'Заявка ФФ': '',
            'Статус': transferStatusLabel(t.status),
            'Висит дн': transferDaysStuck(t) ?? '',
            'Склад забора': transferFromName(t),
            'Куда (склад WB / получатель)': transferToName(t),
            'Поставка FBO': '',
            'Кол-во ед.': t.pallets_count ?? '',
            'Единица': t.shipped_as_boxes ? 'короба' : 'паллеты',
            'Общий вес': transferTotalWeight(t) ?? '',
            // У переезда это ФАКТ, а не прогноз: стоимость приходит из машины.
            'Стоимость забора': toMoney(t.pickup_cost) ?? '',
            'Машина': t.vehicle_info || '',
            'Дата готовности': t.actual_ready_date || '',
        }));
        exportToExcel([...assemblyRows, ...transferRows], 'logistics_sheet');
    };

    // ─── Render ───────────────────────────────────────────────────────────

    const isSoonReady = (item: AssemblyRequest) =>
        item.status === 'PENDING' || item.status === 'IN_PROGRESS';

    // Дата сдачи на ВБ из FBO-поставки: факт приоритетнее плана.
    const renderWbDeliveryDate = (item: AssemblyRequest): React.ReactNode => {
        if (item.wb_fbo_actual_date) {
            return <span style={{ color: 'var(--color-success)' }}>{formatDate(item.wb_fbo_actual_date)} <span style={{ fontSize: 10, opacity: 0.7 }}>факт</span></span>;
        }
        if (item.wb_fbo_planned_date) {
            return <span style={{ color: 'var(--color-text-muted)' }}>{formatDate(item.wb_fbo_planned_date)} <span style={{ fontSize: 10, opacity: 0.7 }}>план</span></span>;
        }
        return <span style={{ color: 'var(--color-text-muted)' }}>{'—'}</span>;
    };

    // Прогноз стоимости для ещё не назначенной заявки (READY) — по складу сдачи и паллетам.
    const renderForecastCard = (item: AssemblyRequest): React.ReactNode => {
        if (item.status !== 'READY') return null;
        const f = forecastFor(forecast, item.effective_wb_warehouse, item.pallets_count);
        if (!f) return null;
        return (
            <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--color-border)' }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-accent)' }}>
                    📊 Прогноз: ~{formatNumber(f.total, 0)} ₽
                    <span style={{ fontWeight: 400, color: 'var(--color-text-muted)' }}> · ~{formatNumber(f.cpp, 0)} ₽/пал</span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }} title={`Диапазон по истории · уровень: ${f.basis} · отгрузок: ${f.sample}`}>
                    {formatNumber(f.low, 0)}–{formatNumber(f.high, 0)} ₽ · {f.basis}
                </div>
            </div>
        );
    };

    // Разбивка забора совместной поставки: по строке на склад-источник + ИТОГО.
    const renderJointBreakdown = (item: AssemblyRequest): React.ReactNode => {
        const rows = jointRows(item);
        return (
            <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--color-border)' }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 4 }}>
                    Забор по складам
                </div>
                {/* Компактная таблица под узкую карточку: фикс-лейаут + узкие колонки,
                    статус — точкой. (.data-table с padding 12×16 и auto-layout вылезала за край.) */}
                <table style={{ width: '100%', tableLayout: 'fixed', borderCollapse: 'collapse', fontSize: 11 }}>
                    <colgroup>
                        <col />
                        <col style={{ width: 30 }} />
                        <col style={{ width: 54 }} />
                        <col style={{ width: 46 }} />
                        <col style={{ width: 18 }} />
                    </colgroup>
                    <thead>
                        <tr style={{ color: 'var(--color-text-muted)' }}>
                            <th style={{ padding: '2px 4px', textAlign: 'left', fontWeight: 500 }}>Сборка / склад</th>
                            <th style={{ padding: '2px 4px', textAlign: 'right', fontWeight: 500 }}>Пал</th>
                            <th style={{ padding: '2px 4px', textAlign: 'right', fontWeight: 500 }}>Вес</th>
                            <th style={{ padding: '2px 4px', textAlign: 'right', fontWeight: 500 }}>ФФ №</th>
                            <th style={{ padding: '2px 2px' }}></th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map(r => {
                            const weight = r.pallets_count != null && r.pallet_weight_kg != null
                                ? r.pallets_count * r.pallet_weight_kg : null;
                            const ready = r.status !== 'PENDING' && r.status !== 'IN_PROGRESS';
                            return (
                                <tr key={r.warehouse_id} style={{ borderTop: '1px solid var(--color-border)' }}>
                                    <td style={{ padding: '3px 4px', verticalAlign: 'top' }}>
                                        <div style={{ fontFamily: 'monospace' }}>{r.number}</div>
                                        <div style={{ color: 'var(--color-text-muted)', fontSize: 10, wordBreak: 'break-word' }}>{r.warehouse_name || `#${r.warehouse_id}`}</div>
                                    </td>
                                    <td style={{ padding: '3px 4px', textAlign: 'right', verticalAlign: 'top' }}>{r.pallets_count != null ? formatNumber(r.pallets_count, 0) : '—'}</td>
                                    <td style={{ padding: '3px 4px', textAlign: 'right', verticalAlign: 'top' }}>{weight != null ? formatNumber(weight, 0) + ' кг' : '—'}</td>
                                    <td style={{ padding: '3px 4px', textAlign: 'right', verticalAlign: 'top', fontFamily: 'monospace' }}>{r.ff_request_number || '—'}</td>
                                    <td style={{ padding: '3px 2px', textAlign: 'center', verticalAlign: 'top' }}>
                                        <span
                                            title={STATUS_MAP[r.status as AssemblyStatus]?.label || String(r.status)}
                                            style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: ready ? 'var(--color-success)' : 'var(--color-warning)' }}
                                        />
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                    <tfoot>
                        <tr style={{ fontWeight: 600, borderTop: '1px solid var(--color-border)' }}>
                            <td style={{ padding: '3px 4px' }}>Итого</td>
                            <td style={{ padding: '3px 4px', textAlign: 'right' }}>{formatNumber(jointTotalPallets(item), 0)}</td>
                            <td style={{ padding: '3px 4px', textAlign: 'right' }}>{formatNumber(jointTotalWeight(item), 0)} кг</td>
                            <td colSpan={2}></td>
                        </tr>
                    </tfoot>
                </table>
                <div style={{ display: 'flex', gap: 10, fontSize: 10, color: 'var(--color-text-muted)', marginTop: 4 }}>
                    <span><span style={{ display: 'inline-block', width: 7, height: 7, borderRadius: '50%', background: 'var(--color-warning)', verticalAlign: 'middle', marginRight: 3 }} />в сборке</span>
                    <span><span style={{ display: 'inline-block', width: 7, height: 7, borderRadius: '50%', background: 'var(--color-success)', verticalAlign: 'middle', marginRight: 3 }} />готова</span>
                </div>
            </div>
        );
    };

    return (
        <div className="animate-in">
            {/* Header */}
            <div className="page-header">
                <div>
                    <h1 className="page-title">Лист логиста</h1>
                </div>
                <button className="btn btn-secondary" onClick={handleExport}>
                    Excel
                </button>
            </div>

            {/* Tab switcher + view toggle */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 0 }}>
                    <button
                        className={`btn ${activeTab === 'active' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setActiveTab('active')}
                        style={{ borderRadius: '8px 0 0 8px' }}
                    >
                        Активные
                    </button>
                    <button
                        className={`btn ${activeTab === 'history' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setActiveTab('history')}
                        style={{ borderRadius: 0 }}
                    >
                        История отправок
                    </button>
                    <button
                        className={`btn ${activeTab === 'payments' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setActiveTab('payments')}
                        style={{ borderRadius: 0 }}
                    >
                        💳 Оплаты
                    </button>
                    <button
                        className={`btn ${activeTab === 'gazelka' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setActiveTab('gazelka')}
                        style={{ borderRadius: 0 }}
                    >
                        🚚 Газелька
                    </button>
                    <button
                        className={`btn ${activeTab === 'transfers' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setActiveTab('transfers')}
                        style={{ borderRadius: '0 8px 8px 0' }}
                        title="Стоимость логистики переездов между нашими складами — отдельно от маршрутов на WB"
                    >
                        📦 Переезды
                    </button>
                </div>
                {activeTab === 'active' && (
                    <div style={{ display: 'flex', gap: 0 }}>
                        <button
                            className={`btn btn-sm ${viewMode === 'cards' ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => switchViewMode('cards')}
                            style={{ borderRadius: '8px 0 0 8px' }}
                        >
                            Карточки
                        </button>
                        <button
                            className={`btn btn-sm ${viewMode === 'table' ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => switchViewMode('table')}
                            style={{ borderRadius: '0 8px 8px 0' }}
                        >
                            Таблица
                        </button>
                    </div>
                )}
            </div>

            {/* Error */}
            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)', whiteSpace: 'pre-line' }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={() => setError('')}>
                        Закрыть
                    </button>
                </div>
            )}

            {/* Результат действия. Отдельно от ошибки: «получилось» в красной
                плашке читается как отказ. */}
            {notice && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-success)', whiteSpace: 'pre-line' }}>
                    {notice}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={() => setNotice('')}>
                        Закрыть
                    </button>
                </div>
            )}

            {activeTab === 'gazelka' ? (
                <GazelkaOrdersTab />
            ) : activeTab === 'transfers' ? (
                <TransferLogisticsTab warehouses={warehousesDir} />
            ) : activeTab === 'payments' ? (
                <ShipmentPaymentsTab />
            ) : activeTab === 'active' ? (
                <>
                    {/* Filters */}
                    <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                        <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                            <div className="form-group" style={{ margin: 0 }}>
                                <select
                                    className="form-input"
                                    value={groupBy}
                                    onChange={e => { setGroupBy(e.target.value as GroupBy); setWarehouseFilter(''); }}
                                >
                                    <option value="wb_warehouse">По складу сдачи WB</option>
                                    <option value="warehouse">По складу забора</option>
                                </select>
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <select
                                    className="form-input"
                                    value={warehouseFilter}
                                    onChange={e => setWarehouseFilter(e.target.value)}
                                >
                                    <option value="">Все склады</option>
                                    {warehouseOptions.map(w => (
                                        <option key={w} value={w}>{w}</option>
                                    ))}
                                </select>
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <select
                                    className="form-input"
                                    value={statusFilter}
                                    onChange={e => setStatusFilter(e.target.value)}
                                >
                                    <option value="">Все статусы</option>
                                    {statusOptions.map(s => (
                                        // Подпись берём заявочную, а чего у заявки нет
                                        // (PENDING переезда) — из словаря переезда.
                                        <option key={s} value={s}>
                                            {STATUS_MAP[s as AssemblyStatus]?.label || transferStatusLabel(s)}
                                        </option>
                                    ))}
                                </select>
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                {/* Тип строки: список общий, поэтому фильтр по типу
                                    обязателен — иначе «покажи только переезды»
                                    делается глазами. */}
                                <select
                                    className="form-input"
                                    value={typeFilter}
                                    onChange={e => setTypeFilter(e.target.value as 'all' | 'assembly' | 'transfer')}
                                    title="Заявки на сборку и перемещения между нашими складами идут одним списком"
                                >
                                    <option value="all">Заявки и перемещения</option>
                                    <option value="assembly">Только заявки</option>
                                    <option value="transfer">Только перемещения</option>
                                </select>
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <input
                                    className="form-input"
                                    type="search"
                                    value={activeSearch}
                                    onChange={e => setActiveSearch(e.target.value)}
                                    placeholder="Поиск: № заявки, переезда или поставка ФБО"
                                    style={{ minWidth: 240 }}
                                />
                            </div>
                            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 14 }}>
                                <input
                                    type="checkbox"
                                    checked={showInProgress}
                                    onChange={e => setShowInProgress(e.target.checked)}
                                />
                                Показывать в работе
                            </label>
                            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 14 }}>
                                <input
                                    type="checkbox"
                                    checked={showSoonReady}
                                    onChange={e => setShowSoonReady(e.target.checked)}
                                />
                                Показывать скоро готовые
                            </label>
                        </div>
                    </div>

                    {/* Summary */}
                    {/* Итоги считаем по ОБОИМ типам: логист сажает на машины и то,
                        и другое, и «сколько всего палет сегодня» — общий вопрос. */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 16 }}>
                        <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                            <div style={{ fontSize: 24, fontWeight: 600 }}>{totalRequests}</div>
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Заявок</div>
                        </div>
                        <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                            <div style={{ fontSize: 24, fontWeight: 600 }}>{totalTransfers}</div>
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Перемещений</div>
                        </div>
                        <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                            <div style={{ fontSize: 24, fontWeight: 600 }}>{formatNumber(totalPallets + transferPalletsTotal, 0)}</div>
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Палет</div>
                            {/* Короба отдельной строкой: складывать их с палетами
                                в одно число значит врать про объём машины. */}
                            {(totalBoxes + transferBoxesTotal) > 0 && (
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }} title="Коробочные документы считаются отдельно — это другая единица">
                                    + {formatNumber(totalBoxes + transferBoxesTotal, 0)} коробов
                                </div>
                            )}
                        </div>
                        <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                            <div style={{ fontSize: 24, fontWeight: 600 }}>
                                {(totalWeight + transferWeight) > 0 ? formatNumber(totalWeight + transferWeight, 0) : '0'}
                            </div>
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Общий вес (кг)</div>
                        </div>
                    </div>

                    {/* ─── Уехали без стоимости ──────────────────────────────
                        Рабочий список «переезд уехал, а денег на нём нет». Живёт
                        ЗДЕСЬ, а не в отчёте «Переезды», потому что отчёт строится
                        на заборах — а забора у этих переездов как раз и нет, и
                        завести его больше неоткуда. Свёрнут по умолчанию: это
                        разовая уборка долгов, а не ежедневная работа логиста. */}
                    {noCostTransfers.length > 0 && (
                        <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                            <div
                                onClick={() => setNoCostOpen(v => !v)}
                                style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', flexWrap: 'wrap' }}
                            >
                                <span style={{ fontSize: 15, fontWeight: 600 }}>
                                    {noCostOpen ? '▾' : '▸'} Уехали без стоимости
                                </span>
                                <span className="badge badge-warning">
                                    {formatNumber(noCostTransfers.length, 0)}
                                </span>
                                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                    не попали ни в «Оплаты», ни в отчёт «Переезды» — укажите перевозчика и стоимость
                                </span>
                            </div>
                            {noCostOpen && (
                                <div style={{ marginTop: 12 }}>
                                    {checkedNoCostIds.size > 0 && (
                                        <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                                            <button
                                                className="btn btn-primary btn-sm"
                                                onClick={() => openLogisticsModal([...checkedNoCostIds])}
                                                disabled={actionLoading}
                                            >
                                                Указать на {formatNumber(checkedNoCostIds.size, 0)} перемещ.
                                            </button>
                                            <button className="btn btn-secondary btn-sm" onClick={() => setCheckedNoCostIds(new Set())}>
                                                Снять выбор
                                            </button>
                                        </div>
                                    )}
                                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
                                        {noCostTransfers.map(t => {
                                            const st = TRANSFER_STATUS_MAP[t.status] ?? { label: t.status, className: 'badge-secondary' };
                                            const checked = checkedNoCostIds.has(t.id);
                                            return (
                                                <div
                                                    key={t.id}
                                                    className="glass-card"
                                                    style={{
                                                        padding: 16,
                                                        border: checked ? '2px solid var(--color-accent)' : undefined,
                                                        cursor: 'pointer',
                                                    }}
                                                    onClick={() => toggleCheckedNoCost(t.id)}
                                                >
                                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                                                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                                            <input
                                                                type="checkbox"
                                                                checked={checked}
                                                                onChange={() => toggleCheckedNoCost(t.id)}
                                                                onClick={e => e.stopPropagation()}
                                                                style={{ width: 18, height: 18, accentColor: 'var(--color-accent)', cursor: 'pointer' }}
                                                            />
                                                            <Link
                                                                href={`/p/${slug}/warehouse/transfers/${t.id}`}
                                                                style={{ fontWeight: 600, textDecoration: 'none', color: 'var(--color-text)' }}
                                                                onClick={e => e.stopPropagation()}
                                                            >
                                                                {t.number}
                                                            </Link>
                                                        </div>
                                                        <span className={`badge ${st.className}`}>{st.label}</span>
                                                    </div>
                                                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                                        <div style={{ fontSize: 11, fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                                                            Откуда → Куда
                                                        </div>
                                                        <div style={{ fontWeight: 600, color: 'var(--color-text)', marginBottom: 4 }}>
                                                            {transferFromName(t)} <span style={{ color: 'var(--color-text-muted)' }}>→</span> {transferToName(t)}
                                                        </div>
                                                        <div>Позиций: {formatNumber(transferSkuCount(t), 0)} · {formatNumber(transferUnits(t), 0)} шт</div>
                                                        {t.shipped_at && <div>Уехал: {formatDate(t.shipped_at)}</div>}
                                                    </div>
                                                    <button
                                                        className="btn btn-secondary btn-sm"
                                                        style={{ marginTop: 8 }}
                                                        onClick={e => { e.stopPropagation(); openLogisticsModal([t.id]); }}
                                                        disabled={actionLoading}
                                                    >
                                                        Указать перевозчика и стоимость
                                                    </button>
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>
                            )}
                        </div>
                    )}

                    {/* Content */}
                    {loading ? (
                        <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
                    ) : groupedAll.length === 0 ? (
                        <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                            <div style={{ fontSize: 48, marginBottom: 16 }}>🚛</div>
                            <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Нет работы для логиста</div>
                            <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                                Здесь появятся заявки и перемещения в статусе &laquo;Готово&raquo; и &laquo;Машина назначена&raquo;
                            </div>
                        </div>
                    ) : viewMode === 'table' ? (
                        /* ─── Table view ─── */
                        /* TODO: migrate to TanStackDataTable — has grouped rows with React.Fragment, checkboxes, inline buttons */
                        <div className="glass-card" style={{ overflow: 'auto' }}>
                            <table className="data-table" style={{ fontSize: 13 }}>
                                <thead>
                                    <tr>
                                        <th style={{ width: 32 }}></th>
                                        <th>Заявка</th>
                                        <th>Бренд</th>
                                        <th>Забор</th>
                                        <th>Сдача WB</th>
                                        <th>Дата сдачи</th>
                                        <th>Поставка</th>
                                        <th style={{ textAlign: 'right' }}>Палет</th>
                                        <th style={{ textAlign: 'right' }}>Вес</th>
                                        <th style={{ textAlign: 'right' }}>Позиции</th>
                                        {/* Заголовок честный: у заявки здесь прогноз по
                                            истории, у перемещения — ФАКТ стоимости забора. */}
                                        <th
                                            style={{ textAlign: 'right' }}
                                            title="Заявка — прогноз ₽ по истории отправок; перемещение — фактическая стоимость забора"
                                        >
                                            Прогноз / факт ₽
                                        </th>
                                        <th>Статус</th>
                                        <th></th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {groupedAll.map(group => (
                                        <React.Fragment key={group.key}>
                                            <tr>
                                                <td colSpan={13} style={{ background: 'var(--color-bg)', fontWeight: 600, fontSize: 13, padding: '8px 12px' }}>
                                                    {group.label || 'Без склада'}
                                                    <span style={{ fontWeight: 400, color: 'var(--color-text-muted)', marginLeft: 8 }}>
                                                        {group.items.length} заявок{group.transfers.length > 0 ? ` + ${group.transfers.length} перемещений` : ''}, {group.items.reduce((s, i) => s + (i.joint_supply ? jointTotalPallets(i) : i.pallets_count), 0) + group.transfers.reduce((s, t) => s + (t.pallets_count || 0), 0)} палет
                                                    </span>
                                                </td>
                                            </tr>
                                            {group.items.map(item => {
                                                const statusCfg = STATUS_MAP[item.status] || { label: item.status, className: '' };
                                                const isJoint = !!item.joint_supply;
                                                const jointReady = item.joint_ready !== false;
                                                // Газелька ведёт логистику сама — из ручного назначения (чек/bulk) исключаем.
                                                const canCheck = item.status === 'READY' && (!isJoint || jointReady) && !item.via_gazelka;
                                                const isChecked = checkedIds.has(item.id);
                                                const stuck = daysStuck(item);
                                                const isStuck = stuck != null && stuck >= STUCK_THRESHOLD_DAYS;
                                                const veryStuck = stuck != null && stuck >= STUCK_THRESHOLD_DAYS * 2;
                                                const ffNums = ffRequestNumbers(item);
                                                const jointPending = isJoint ? jointPendingWarehouses(item) : [];
                                                return (
                                                    <tr
                                                        key={item.id}
                                                        style={{
                                                            cursor: 'pointer',
                                                            background: isChecked ? 'rgba(59, 130, 246, 0.06)' : veryStuck ? 'rgba(239,68,68,0.06)' : isStuck ? 'rgba(245,158,11,0.06)' : undefined,
                                                        }}
                                                        onClick={() => canCheck && toggleChecked(item.id)}
                                                    >
                                                        <td onClick={e => e.stopPropagation()} style={{ textAlign: 'center' }}>
                                                            {canCheck && (
                                                                <input
                                                                    type="checkbox"
                                                                    checked={isChecked}
                                                                    onChange={() => toggleChecked(item.id)}
                                                                    style={{ width: 16, height: 16, accentColor: 'var(--color-accent)', cursor: 'pointer' }}
                                                                />
                                                            )}
                                                        </td>
                                                        <td onClick={e => e.stopPropagation()}>
                                                            <Link
                                                                href={`/p/${slug}/warehouse/assembly/${item.id}`}
                                                                style={{ fontWeight: 600, textDecoration: 'none', color: 'var(--color-accent)' }}
                                                            >
                                                                {item.number}
                                                            </Link>
                                                            {isJoint && (
                                                                <span className="badge badge-info" style={{ fontSize: 10, marginLeft: 6 }} title={`Совместная FBO-поставка · ${jointRows(item).map(r => r.warehouse_name || `#${r.warehouse_id}`).join(' + ')}`}>
                                                                    Совместная
                                                                </span>
                                                            )}
                                                            {item.via_gazelka && (
                                                                <span className="badge badge-info" style={{ fontSize: 10, marginLeft: 6 }} title="Отправлено в Газельку — машину назначает агрегатор, вручную нельзя">🚚 Газелька</span>
                                                            )}
                                                            {!isJoint && ffNums.length > 0 && (
                                                                <div style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--color-text-muted)' }} title="Номер(а) заявки ФФ">
                                                                    ФФ: {ffNums.join(', ')}
                                                                </div>
                                                            )}
                                                        </td>
                                                        <td style={{ fontSize: 12 }}>{item.brands || '\u2014'}</td>
                                                        <td style={{ color: 'var(--color-text-muted)' }}>{isJoint ? jointRows(item).map(r => r.warehouse_name || `#${r.warehouse_id}`).join(' + ') : (item.warehouse_name || '\u2014')}</td>
                                                        <td>{item.effective_wb_warehouse || '—'}</td>
                                                        <td style={{ fontSize: 12 }}>{renderWbDeliveryDate(item)}</td>
                                                        <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{item.wb_supply_id_wb ||'\u2014'}</td>
                                                        <td style={{ textAlign: 'right' }}>{isJoint ? formatNumber(jointTotalPallets(item), 0) + ' пал' : `${formatNumber(item.pallets_count, 0)} ${unitShort(item.shipped_as_boxes)}`}</td>
                                                        <td style={{ textAlign: 'right' }}>{isJoint ? formatNumber(jointTotalWeight(item), 0) + ' кг' : (item.total_weight_kg ? formatNumber(item.total_weight_kg, 0) + ' кг' : '\u2014')}</td>
                                                        <td style={{ textAlign: 'right' }}>{item.items?.length || 0}</td>
                                                        <td style={{ textAlign: 'right' }}>{(() => {
                                                            const f = item.status === 'READY' ? forecastFor(forecast, item.effective_wb_warehouse, item.pallets_count) : null;
                                                            return f
                                                                ? <span style={{ color: 'var(--color-accent)', fontWeight: 600 }} title={`~${formatNumber(f.cpp, 0)} ₽/пал · ${formatNumber(f.low, 0)}–${formatNumber(f.high, 0)} ₽ · ${f.basis}`}>~{formatNumber(f.total, 0)}</span>
                                                                : <span style={{ color: 'var(--color-text-muted)' }}>{'—'}</span>;
                                                        })()}</td>
                                                        <td>
                                                            <span className={`badge ${statusCfg.className}`}>{statusCfg.label}</span>
                                                            {isStuck && (
                                                                <div style={{ marginTop: 2 }}>
                                                                    <span className={`badge ${veryStuck ? 'badge-danger' : 'badge-warning'}`} style={{ fontSize: 10 }}>
                                                                        ⏱ висит {formatNumber(stuck as number, 0)} дн
                                                                    </span>
                                                                </div>
                                                            )}
                                                        </td>
                                                        <td onClick={e => e.stopPropagation()} style={{ whiteSpace: 'nowrap' }}>
                                                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                                                {item.status === 'READY' && (
                                                                    item.via_gazelka ? (
                                                                        <div>
                                                                            <button className="btn btn-primary btn-sm" disabled>Назначить машину</button>
                                                                            <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginTop: 2, whiteSpace: 'normal', maxWidth: 160 }}>
                                                                                Логистику ведёт Газелька
                                                                            </div>
                                                                        </div>
                                                                    ) : isJoint && !jointReady ? (
                                                                        <div>
                                                                            <button className="btn btn-primary btn-sm" disabled>Назначить машину</button>
                                                                            <div style={{ fontSize: 10, color: 'var(--color-warning)', marginTop: 2, whiteSpace: 'normal', maxWidth: 160 }}>
                                                                                Ждёт: {jointPending.join(', ') || 'другие склады'}
                                                                            </div>
                                                                        </div>
                                                                    ) : (
                                                                        <button
                                                                            className="btn btn-primary btn-sm"
                                                                            onClick={() => openVehicleModal([item.id])}
                                                                            disabled={actionLoading}
                                                                        >
                                                                            Назначить машину
                                                                        </button>
                                                                    )
                                                                )}
                                                                {item.status === 'VEHICLE_ASSIGNED' && (
                                                                    <div style={{ display: 'flex', gap: 4 }}>
                                                                        <button
                                                                            className="btn btn-secondary btn-sm"
                                                                            onClick={() => handleUnassignVehicle(item.id)}
                                                                            disabled={actionLoading}
                                                                        >
                                                                            Отменить
                                                                        </button>
                                                                        <button
                                                                            className="btn btn-primary btn-sm"
                                                                            onClick={() => isJoint ? handleShipJoint(item.id) : handleShip(item.id)}
                                                                            disabled={actionLoading}
                                                                        >
                                                                            {isJoint ? 'Отгрузить поставку' : 'Отгрузить'}
                                                                        </button>
                                                                    </div>
                                                                )}
                                                                {isGazelkaEligible(item) && (
                                                                    <button
                                                                        className="btn btn-secondary btn-sm"
                                                                        onClick={() => openGazelkaModal(item)}
                                                                    >
                                                                        Газелька
                                                                    </button>
                                                                )}
                                                            </div>
                                                        </td>
                                                    </tr>
                                                );
                                            })}
                                            {/* Перемещения — те же группы, тот же чекбокс,
                                                отличие ровно в теге «Перемещение». */}
                                            {group.transfers.map(t => (
                                                <TransferWorkTableRow
                                                    key={`t${t.id}`}
                                                    transfer={t}
                                                    slug={slug}
                                                    fromName={transferFromName(t)}
                                                    toName={transferToName(t)}
                                                    checked={checkedTransferIds.has(t.id)}
                                                    canEdit
                                                    busy={actionLoading}
                                                    onToggle={toggleCheckedTransfer}
                                                    onAssign={id => openVehicleModal([], [id])}
                                                    onUnassign={handleUnassignTransfer}
                                                    onSend={handleSendTransfer}
                                                    onGazelka={openGazelkaTransferModal}
                                                    gazelkaWarehouseIds={gazelkaWarehouseIds}
                                                    stuckThresholdDays={STUCK_THRESHOLD_DAYS}
                                                />
                                            ))}
                                        </React.Fragment>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    ) : (
                        /* ─── Cards view ─── */
                        <div>
                            {groupedAll.map(group => (
                                <div key={group.key} style={{ marginBottom: 24 }}>
                                    <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12, padding: '0 4px' }}>
                                        {group.label || 'Без склада'}
                                        <span style={{ fontWeight: 400, color: 'var(--color-text-muted)', marginLeft: 8, fontSize: 14 }}>
                                            ({group.items.length} заявок{group.transfers.length > 0 ? ` + ${group.transfers.length} перемещений` : ''}, {group.items.reduce((s, i) => s + (i.joint_supply ? jointTotalPallets(i) : i.pallets_count), 0) + group.transfers.reduce((s, t) => s + (t.pallets_count || 0), 0)} палет)
                                        </span>
                                    </h2>

                                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
                                        {group.subGroups.flatMap(sub => sub.items).map(item => {
                                            const soon = isSoonReady(item);
                                            const statusCfg = STATUS_MAP[item.status] || { label: item.status, className: '' };
                                            const isChecked = checkedIds.has(item.id);
                                            const isJoint = !!item.joint_supply;
                                            const jointReady = item.joint_ready !== false; // дефолт: не блокируем, если флаг не пришёл
                                            // Совместный якорь можно чекать (для bulk) только когда вся поставка готова; Газелька ведёт логистику сама.
                                            const canCheck = item.status === 'READY' && (!isJoint || jointReady) && !item.via_gazelka;
                                            const stuck = daysStuck(item);
                                            const isStuck = stuck != null && stuck >= STUCK_THRESHOLD_DAYS;
                                            const veryStuck = stuck != null && stuck >= STUCK_THRESHOLD_DAYS * 2;
                                            const ffNums = ffRequestNumbers(item);
                                            const jointPending = isJoint ? jointPendingWarehouses(item) : [];
                                            const borderColor = isChecked
                                                ? 'var(--color-accent)'
                                                : veryStuck ? 'var(--color-danger)'
                                                : isStuck ? 'var(--color-warning)'
                                                : undefined;

                                            return (
                                                <div
                                                    key={item.id}
                                                    className="glass-card"
                                                    style={{
                                                        padding: 16,
                                                        // Совместную не затемняем даже «в сборке» — внутри читаемая разбивка.
                                                        opacity: (soon && !isJoint) ? 0.5 : 1,
                                                        border: borderColor ? `2px solid ${borderColor}` : undefined,
                                                        borderLeft: isJoint ? '3px solid var(--color-accent)' : undefined,
                                                        cursor: canCheck ? 'pointer' : undefined,
                                                    }}
                                                    onClick={canCheck ? () => toggleChecked(item.id) : undefined}
                                                >
                                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                                                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                                            {canCheck && (
                                                                <input
                                                                    type="checkbox"
                                                                    checked={isChecked}
                                                                    onChange={() => toggleChecked(item.id)}
                                                                    onClick={e => e.stopPropagation()}
                                                                    style={{ width: 18, height: 18, accentColor: 'var(--color-accent)', cursor: 'pointer' }}
                                                                />
                                                            )}
                                                            <Link
                                                                href={`/p/${slug}/warehouse/assembly/${item.id}`}
                                                                style={{ fontWeight: 600, textDecoration: 'none', color: 'var(--color-text)', whiteSpace: 'nowrap' }}
                                                                onClick={e => e.stopPropagation()}
                                                            >
                                                                {item.number}
                                                            </Link>
                                                            {isJoint && (item.joint_siblings?.length ?? 0) > 0 && (
                                                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }} title="Связанные сборки той же совместной поставки">
                                                                    + {(item.joint_siblings || []).map(s => s.number).join(', ')}
                                                                </span>
                                                            )}
                                                        </div>
                                                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                                                            {isJoint && (
                                                                <span className="badge badge-info" title="Совместная FBO-поставка: несколько сборок (по складу-источнику) в одну WB-поставку">
                                                                    Совместная
                                                                </span>
                                                            )}
                                                            {item.via_gazelka && (
                                                                <span className="badge badge-info" title="Отправлено в Газельку — машину назначает агрегатор, вручную нельзя">🚚 Газелька</span>
                                                            )}
                                                            <span className={`badge ${statusCfg.className}`}>
                                                                {soon && item.estimated_ready_date
                                                                    ? formatDate(item.estimated_ready_date)
                                                                    : statusCfg.label}
                                                            </span>
                                                        </div>
                                                    </div>

                                                    {isStuck && (
                                                        <div style={{ marginBottom: 8 }}>
                                                            <span
                                                                className={`badge ${veryStuck ? 'badge-danger' : 'badge-warning'}`}
                                                                style={{ fontSize: 11 }}
                                                                title="Готово, но не отгружается — как в аномалиях «Анализа сборки»"
                                                            >
                                                                ⏱ висит {formatNumber(stuck as number, 0)} дн
                                                            </span>
                                                        </div>
                                                    )}

                                                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                                                        {item.warehouse_name && (
                                                            <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 2 }}>{item.warehouse_name}</div>
                                                        )}
                                                        {item.brands && (
                                                            <div style={{ fontWeight: 500, color: 'var(--color-text)' }}>{item.brands}</div>
                                                        )}
                                                        {isJoint ? (
                                                            <div>Палет: {formatNumber(jointTotalPallets(item), 0)} &middot; Вес: {formatNumber(jointTotalWeight(item), 0)} кг <span style={{ fontSize: 11, opacity: 0.7 }}>(вся поставка)</span></div>
                                                        ) : (
                                                        <div>{item.shipped_as_boxes ? 'Короба' : 'Палет'}: {formatNumber(item.pallets_count, 0)} &middot; Вес: {item.total_weight_kg ? formatNumber(item.total_weight_kg, 0) + ' кг' : '\u2014'}</div>
                                                        )}
                                                        <div>Позиций: {item.items?.length || 0}</div>
                                                        {(item.wb_supply_id_wb || item.wb_supply_name) && (
                                                            <div style={{ fontFamily: 'monospace', fontSize: 12 }}>Поставка: {item.wb_supply_id_wb || item.wb_supply_name}</div>
                                                        )}
                                                        {!isJoint && ffNums.length > 0 && (
                                                            <div style={{ fontFamily: 'monospace', fontSize: 12 }} title="Номер(а) привязанной заявки ФФ">
                                                                Заявка ФФ: {ffNums.join(', ')}
                                                            </div>
                                                        )}
                                                        {item.effective_wb_warehouse && (
                                                            <div>WB: {item.effective_wb_warehouse}</div>
                                                        )}
                                                        {(item.wb_fbo_actual_date || item.wb_fbo_planned_date) && (
                                                            <div>Сдача ВБ: {renderWbDeliveryDate(item)}</div>
                                                        )}
                                                    </div>

                                                    {isJoint ? renderJointBreakdown(item) : renderForecastCard(item)}

                                                    {(!soon || isJoint) && (
                                                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
                                                            <div style={{ display: 'flex', gap: 8 }}>
                                                                {item.status === 'READY' && item.via_gazelka ? (
                                                                    <div style={{ width: '100%' }}>
                                                                        <button className="btn btn-primary btn-sm" disabled style={{ width: '100%' }}>
                                                                            Назначить машину
                                                                        </button>
                                                                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                                                            Логистику ведёт Газелька
                                                                        </div>
                                                                    </div>
                                                                ) : isJoint && !jointReady ? (
                                                                    // Совместная не готова — пометка + неактивная кнопка ВСЕГДА (даже если якорь «в сборке»).
                                                                    <div style={{ width: '100%' }}>
                                                                        <button className="btn btn-primary btn-sm" disabled style={{ width: '100%' }}>
                                                                            Назначить машину
                                                                        </button>
                                                                        <div style={{ fontSize: 11, color: 'var(--color-warning)', marginTop: 4 }}>
                                                                            Ждёт готовности: {jointPending.join(', ') || 'другие склады поставки'}
                                                                        </div>
                                                                    </div>
                                                                ) : item.status === 'READY' ? (
                                                                    <button
                                                                        className="btn btn-primary btn-sm"
                                                                        onClick={() => openVehicleModal([item.id])}
                                                                        disabled={actionLoading}
                                                                    >
                                                                        Назначить машину{isJoint ? ' (совместная)' : ''}
                                                                    </button>
                                                                ) : null}
                                                                {item.status === 'VEHICLE_ASSIGNED' && (
                                                                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: '100%' }}>
                                                                        {(item.vehicle_info || item.driver_phone) && (
                                                                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', lineHeight: 1.4 }}>
                                                                                {item.vehicle_info}
                                                                                {item.driver_phone && <div>{item.driver_phone}</div>}
                                                                            </div>
                                                                        )}
                                                                        <div style={{ display: 'flex', gap: 8 }}>
                                                                            <button
                                                                                className="btn btn-secondary btn-sm"
                                                                                onClick={(e) => { e.stopPropagation(); handleUnassignVehicle(item.id); }}
                                                                                disabled={actionLoading}
                                                                            >
                                                                                Отменить
                                                                            </button>
                                                                            <button
                                                                                className="btn btn-primary btn-sm"
                                                                                onClick={(e) => { e.stopPropagation(); isJoint ? handleShipJoint(item.id) : handleShip(item.id); }}
                                                                                disabled={actionLoading}
                                                                            >
                                                                                {isJoint ? 'Отгрузить поставку' : 'Отгрузить'}
                                                                            </button>
                                                                        </div>
                                                                    </div>
                                                                )}
                                                            </div>
                                                            {isGazelkaEligible(item) && (
                                                                <button
                                                                    className="btn btn-secondary btn-sm"
                                                                    onClick={(e) => { e.stopPropagation(); openGazelkaModal(item); }}
                                                                >
                                                                    Отправить в Газельку
                                                                </button>
                                                            )}
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        })}
                                        {/* Перемещения — карточки в ТОЙ ЖЕ сетке, что и
                                            заявки: один список, тег «Перемещение». */}
                                        {group.transfers.map(t => (
                                            <TransferWorkCard
                                                key={`t${t.id}`}
                                                transfer={t}
                                                slug={slug}
                                                fromName={transferFromName(t)}
                                                toName={transferToName(t)}
                                                checked={checkedTransferIds.has(t.id)}
                                                canEdit
                                                busy={actionLoading}
                                                onToggle={toggleCheckedTransfer}
                                                onAssign={id => openVehicleModal([], [id])}
                                                onUnassign={handleUnassignTransfer}
                                                onSend={handleSendTransfer}
                                                onGazelka={openGazelkaTransferModal}
                                                gazelkaWarehouseIds={gazelkaWarehouseIds}
                                                stuckThresholdDays={STUCK_THRESHOLD_DAYS}
                                            />
                                        ))}
                                    </div>

                                    {/* Bulk assign buttons per sub-group */}
                                    {group.subGroups.map(sub => {
                                        // Совместный якорь попадает в bulk только если вся поставка готова (joint_ready).
                                        const readyIds = sub.items
                                            .filter(i => i.status === 'READY' && (!i.joint_supply || i.joint_ready !== false) && !i.via_gazelka)
                                            .map(i => i.id);
                                        if (readyIds.length <= 1) return null;
                                        return (
                                            <div key={sub.key} style={{ marginTop: 8, padding: '0 4px' }}>
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    onClick={() => openVehicleModal(readyIds)}
                                                    disabled={actionLoading}
                                                >
                                                    Назначить машину для всех {sub.label ? `"${sub.label}"` : ''} ({readyIds.length})
                                                </button>
                                            </div>
                                        );
                                    })}
                                </div>
                            ))}
                        </div>
                    )}

                </>
            ) : (
                /* History tab */
                <>
                    {/* Analytics filters */}
                    <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                        <div style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap' }}>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label" style={{ fontSize: 12 }}>Период от</label>
                                <input className="form-input" type="date" value={analyticsDateFrom} onChange={e => setAnalyticsDateFrom(e.target.value)} style={{ fontSize: 13 }} />
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label" style={{ fontSize: 12 }}>до</label>
                                <input className="form-input" type="date" value={analyticsDateTo} onChange={e => setAnalyticsDateTo(e.target.value)} style={{ fontSize: 13 }} />
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label" style={{ fontSize: 12 }}>Бренд</label>
                                <select className="form-input" value={analyticsBrand} onChange={e => setAnalyticsBrand(e.target.value)} style={{ fontSize: 13, minWidth: 140 }}>
                                    <option value="">Все бренды</option>
                                    {brandOptions.map(b => (
                                        <option key={b} value={b}>{b}</option>
                                    ))}
                                </select>
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label" style={{ fontSize: 12 }}>Подрядчик</label>
                                <select className="form-input" value={analyticsCarrier} onChange={e => setAnalyticsCarrier(e.target.value ? Number(e.target.value) : '')} style={{ fontSize: 13, minWidth: 180 }}>
                                    <option value="">Все подрядчики</option>
                                    {analyticsData?.by_carrier.filter(c => c.counterparty_id != null).map(c => (
                                        <option key={c.counterparty_id} value={c.counterparty_id as number}>{c.carrier_name}</option>
                                    ))}
                                </select>
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label" style={{ fontSize: 12 }}>Склад сдачи</label>
                                <select className="form-input" value={analyticsDestFilter} onChange={e => setAnalyticsDestFilter(e.target.value)} style={{ fontSize: 13, minWidth: 160 }}>
                                    <option value="">Все склады</option>
                                    {analyticsData?.by_destination.map(d => (
                                        <option key={d.dest_warehouse} value={d.dest_warehouse}>{d.dest_warehouse}</option>
                                    ))}
                                </select>
                            </div>
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 8 }}>
                            Период и бренд — для всей аналитики; подрядчик и склад дополнительно фильтруют таблицу отправок.
                        </div>
                    </div>

                    {/* Analytics section */}
                    {analyticsLoading ? (
                        <div className="glass-card" style={{ padding: 32, textAlign: 'center', marginBottom: 16 }}>Загрузка аналитики...</div>
                    ) : analyticsData && (
                        <>
                            {/* KPI Cards — расширенная сводка периода */}
                            <div className="stats-grid" style={{ marginBottom: 16 }}>
                                <KpiCard label="Всего отправок" value={formatNumber(analyticsData.summary.total_shipments, 0)} />
                                <KpiCard label="Всего заявок" value={formatNumber(analyticsData.summary.total_requests, 0)} />
                                <KpiCard label="Всего палет" value={formatNumber(analyticsData.summary.total_pallets, 0)} />
                                <KpiCard label="Товаров, шт" value={formatNumber(analyticsData.summary.total_items, 0)} />
                                <KpiCard label="Складов сдачи" value={formatNumber(analyticsData.summary.total_destinations, 0)} />
                                <KpiCard label="Подрядчиков" value={formatNumber(analyticsData.summary.total_carriers, 0)} />
                                <KpiCard label="Общая стоимость" value={formatNumber(analyticsData.summary.total_cost, 0)} sub="₽" />
                                <KpiCard label="Средняя ₽/палета" value={formatNumber(analyticsData.summary.avg_cost_per_pallet, 0)} sub="₽" />
                            </div>

                            {/* View segmented control */}
                            <div style={{ display: 'flex', gap: 0, marginBottom: 16, flexWrap: 'wrap' }}>
                                {([
                                    ['overview', 'Обзор'],
                                    ['pallets', 'Стоимость и паллеты'],
                                    ['per_unit', 'Стоимость ₽/шт'],
                                    ['carriers', 'Подрядчики'],
                                    ['anomalies', `Аномалии${analyticsData.anomalies.length ? ` (${analyticsData.anomalies.length})` : ''}`],
                                    ['matrix', 'Матрица маршрутов'],
                                ] as [AnalyticsView, string][]).map(([v, label], i, arr) => (
                                    <button
                                        key={v}
                                        className={`btn btn-sm ${analyticsView === v ? 'btn-primary' : 'btn-secondary'}`}
                                        onClick={() => setAnalyticsView(v)}
                                        style={{ borderRadius: i === 0 ? '8px 0 0 8px' : i === arr.length - 1 ? '0 8px 8px 0' : 0 }}
                                    >
                                        {label}
                                    </button>
                                ))}
                            </div>

                            {analyticsView === 'overview' && (
                                analyticsData.by_destination.length > 0 ? (
                                    <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                                        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Средняя ₽/палета по складам сдачи</div>
                                        <ResponsiveContainer width="100%" height={320}>
                                            <BarChart data={analyticsData.by_destination} margin={{ top: 5, right: 20, bottom: 5, left: 20 }}>
                                                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                                                <XAxis dataKey="dest_warehouse" tick={{ fontSize: 11 }} angle={-30} textAnchor="end" height={90} interval={0} />
                                                <YAxis tick={{ fontSize: 12 }} />
                                                <RechartsTooltip
                                                    formatter={(value: number) => [formatNumber(value, 0) + ' ₽', 'Средняя ₽/палета']}
                                                    contentStyle={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', borderRadius: 8, fontSize: 13 }}
                                                />
                                                <Bar dataKey="avg_cost" radius={[4, 4, 0, 0]}>
                                                    {analyticsData.by_destination.map((entry, idx) => (
                                                        <Cell key={idx} fill={entry.avg_cost > 4000 ? '#ff7e79' : '#007aff'} />
                                                    ))}
                                                </Bar>
                                            </BarChart>
                                        </ResponsiveContainer>
                                    </div>
                                ) : (
                                    <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)', marginBottom: 16 }}>Нет данных за период</div>
                                )
                            )}

                            {analyticsView === 'pallets' && (
                                <LogisticsCostByPallets
                                    buckets={analyticsData.pallet_buckets}
                                    points={analyticsData.cost_points}
                                    byDestination={analyticsData.by_destination}
                                    destCells={analyticsData.dest_pallet_cells}
                                />
                            )}

                            {analyticsView === 'per_unit' && (
                                <LogisticsCostPerUnit
                                    dateFrom={analyticsDateFrom}
                                    dateTo={analyticsDateTo}
                                    brand={analyticsBrand}
                                />
                            )}

                            {analyticsView === 'carriers' && (
                                <LogisticsCarriers
                                    byCarrier={analyticsData.by_carrier}
                                    byDestination={analyticsData.by_destination}
                                />
                            )}

                            {analyticsView === 'anomalies' && (
                                <LogisticsAnomalies anomalies={analyticsData.anomalies} slug={slug} />
                            )}

                            {analyticsView === 'matrix' && (
                                analyticsData.by_route.length > 0
                                    ? <HeatmapMatrix routes={analyticsData.by_route} />
                                    : <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)', marginBottom: 16 }}>Нет маршрутов за период</div>
                            )}
                        </>
                    )}

                    {/* Shipments table — построчная история отправок (фикс сортировки: весь период) */}
                    {shipmentsTruncated && (
                        <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-warning)' }}>
                            Показаны первые {formatNumber(shipmentRows.length, 0)} из {formatNumber(shipmentsTotal, 0)} отправок — сузьте период для полного списка.
                        </div>
                    )}
                    {shipmentsLoading ? (
                        <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
                    ) : shipmentRows.length === 0 ? (
                        <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                            <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Нет отправок за период</div>
                            <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                                Измените период или фильтры — здесь все отгрузки заявок за выбранный диапазон
                            </div>
                        </div>
                    ) : (
                        <TanStackDataTable
                            columns={[
                                { key: 'status', label: 'Статус', sortable: true, render: (_v: string, row: LogisticsShipmentRow) => { const cfg = STATUS_MAP[row.status as AssemblyStatus] || { label: row.status || '—', className: '' }; return <span className={`badge ${cfg.className}`}>{cfg.label}</span>; }},
                                { key: 'assembly_number', label: '№', sortable: true, render: (v: string, row: LogisticsShipmentRow) => (
                                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}>
                                        <span style={{ fontWeight: 500 }}>{v || '—'}</span>
                                        {row.via_gazelka && <span className="badge badge-info" style={{ fontSize: 10 }} title="Отправлено через Газельку (интеграция)">🚚 Газелька</span>}
                                    </span>
                                ) },
                                { key: 'shipment_id', label: '', sortable: false, render: (_v: number, row: LogisticsShipmentRow) => row.status === 'SHIPPED' ? (
                                    <button
                                        className="btn btn-secondary btn-sm"
                                        style={{ whiteSpace: 'nowrap', fontSize: 11 }}
                                        onClick={e => { e.stopPropagation(); setPaymentShipmentId(row.shipment_id); setShowPaymentModal(true); }}
                                    >
                                        💳 Оплата
                                    </button>
                                ) : null },
                                { key: 'brands', label: 'Бренд', sortable: true, render: (v: string) => <span style={{ fontSize: 12 }}>{v || '—'}</span> },
                                { key: 'carrier_name', label: 'Подрядчик', sortable: true, render: (v: string | null, row: LogisticsShipmentRow) => v ? <span title={row.carrier_inn || ''} style={{ fontSize: 12 }}>{v}</span> : <span style={{ color: 'var(--color-text-muted)' }}>{'—'}</span> },
                                { key: 'wb_supply_id', label: 'Поставка WB', sortable: true, render: (v: string) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v || '—'}</span> },
                                { key: 'wb_fbo_status', label: 'Статус WB', sortable: true, render: (v: string) => { if (!v) return '—'; const cfg = WB_STATUS_MAP[v]; return cfg ? <span className={`badge ${cfg.className}`}>{cfg.label}</span> : '—'; }},
                                { key: 'src_warehouse', label: 'Склад забора', sortable: true, render: (v: string) => <span style={{ color: 'var(--color-text-muted)' }}>{v || '—'}</span> },
                                { key: 'dest_warehouse', label: 'Склад сдачи', sortable: true, render: (v: string) => <span style={{ color: 'var(--color-text-muted)' }}>{v || '—'}</span> },
                                { key: 'shipped_date', label: 'Дата отгрузки', sortable: true, render: (v: string) => v ? formatDate(v) : '—' },
                                { key: 'wb_fbo_actual_date', label: 'Дата сдачи', sortable: true, render: (v: string) => v ? formatDate(v) : '—' },
                                { key: 'wb_fbo_planned_date', label: 'План сдачи', sortable: true, render: (v: string) => v ? formatDate(v) : '—' },
                                { key: 'pallets_count', label: 'Кол-во ед.', headerTitle: 'Число единиц поставки (паллет или коробов)', align: 'right', sortable: true, render: (v: number | null, row: LogisticsShipmentRow) => v != null ? `${formatNumber(v, 0)} ${unitShort(row.shipped_as_boxes)}` : '—' },
                                { key: 'pickup_cost', label: 'Стоимость', align: 'right', sortable: true, render: (v: number | null) => v != null ? formatNumber(v, 0) : '—' },
                                { key: 'cost_per_pallet', label: '₽/палета', align: 'right', sortable: true, render: (v: number | null, row: LogisticsShipmentRow) => {
                                    if (v == null) {
                                        return row.anomaly_type === 'no_cost'
                                            ? <span className="badge badge-secondary">нет цены</span>
                                            : '—';
                                    }
                                    const anom = row.anomaly_type ? ANOMALY_BADGE[row.anomaly_type] : null;
                                    const color = row.anomaly_type === 'overpriced' ? 'var(--color-danger)' : row.anomaly_type === 'underpriced' ? 'var(--color-warning)' : undefined;
                                    return <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center', justifyContent: 'flex-end' }}>
                                        <span style={{ color, fontWeight: anom ? 600 : undefined }}>{formatNumber(v, 0)}</span>
                                        {anom && <span className={`badge ${anom.className}`} style={{ fontSize: 10 }}>{anom.label}</span>}
                                    </span>;
                                }},
                                { key: 'total_weight_kg', label: 'Вес', align: 'right', sortable: true, render: (v: number | null) => v ? formatNumber(v, 1) + ' кг' : '—' },
                            ]}
                            data={shipmentRows}
                            title={`Всего отправок: ${shipmentsTotal}`}
                            exportName="logistics_shipments"
                            enableSorting
                            enablePagination
                            pageSize={50}
                            onRowClick={(row: LogisticsShipmentRow) => { if (row.assembly_request_id) window.location.href = `/p/${slug}/warehouse/assembly/${row.assembly_request_id}`; }}
                        />
                    )}
                </>
            )}

            {/* Floating action bar */}
            {(checkedIds.size > 0 || checkedTransfers.length > 0) && (
                <div style={{
                    position: 'fixed',
                    bottom: 0,
                    left: 0,
                    right: 0,
                    background: 'rgba(30, 30, 30, 0.95)',
                    backdropFilter: 'blur(8px)',
                    WebkitBackdropFilter: 'blur(8px)',
                    color: '#fff',
                    padding: '12px 24px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    zIndex: 1000,
                    borderTop: '1px solid rgba(255,255,255,0.1)',
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14 }}>
                        <span style={{ color: 'var(--color-success)' }}>&#10003;</span>
                        Выбрано: {checkedItems.length} заявок
                        {checkedTransfers.length > 0 && <> &middot; {checkedTransfers.length} перемещений</>}
                        &middot; {checkedPallets + checkedTransferPallets} палет
                        &middot; {(checkedWeight + checkedTransferWeight) > 0 ? formatNumber(checkedWeight + checkedTransferWeight, 0) : 0} кг
                    </div>
                    <button
                        className="btn btn-primary"
                        onClick={() => openVehicleModal(assignableCheckedIds, assignableCheckedTransferIds)}
                        disabled={actionLoading || (assignableCheckedIds.length === 0 && assignableCheckedTransferIds.length === 0)}
                    >
                        Назначить машину ({assignableCheckedIds.length + assignableCheckedTransferIds.length})
                    </button>
                </div>
            )}

            {/* Payment request modal */}
            {showPaymentModal && (
                <CreatePaymentRequestModal
                    initialShipmentId={paymentShipmentId}
                    onClose={() => { setShowPaymentModal(false); setPaymentShipmentId(undefined); }}
                />
            )}

            {/* Vehicle modal */}
            {showVehicleModal && (
                <div className="modal-overlay" onClick={() => setShowVehicleModal(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 960, width: '95vw', maxHeight: '90vh', overflow: 'auto', background: '#fff' }}>
                        <h2 className="modal-title">Назначить машину</h2>

                        {/* Block 1: данные водителя и машины — состав 1:1 с WB-пропуском
                            (Имя / Фамилия / Телефон / Марка / Госномер), чтобы занос
                            пропуска не требовал ручного перевода полей. */}
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">Имя водителя *</label>
                                <input className="form-input" value={driverFirstName} onChange={e => setDriverFirstName(e.target.value)} placeholder="Дмитрий" autoFocus />
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">Фамилия водителя *</label>
                                <input className="form-input" value={driverLastName} onChange={e => setDriverLastName(e.target.value)} placeholder="Крапива" />
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">Телефон водителя *</label>
                                <input className="form-input" value={driverPhone} onChange={e => setDriverPhone(e.target.value)} placeholder="+7 999 123-45-67" />
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">Марка машины *</label>
                                <input className="form-input" value={vehicleBrand} onChange={e => setVehicleBrand(e.target.value)} placeholder="ГАЗ-330, КАМАЗ..." />
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">Госномер *</label>
                                <input className="form-input" value={vehicleInfo} onChange={e => setVehicleInfo(e.target.value)} placeholder="В874УА37" />
                            </div>
                        </div>

                        {/* Block 1b: Carrier (optional) */}
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 12, marginBottom: 16, padding: 12, background: 'var(--color-bg)', borderRadius: 8 }}>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">ИНН подрядчика</label>
                                <input
                                    className="form-input"
                                    value={carrierInn}
                                    onChange={e => setCarrierInn(e.target.value)}
                                    placeholder="10 или 12 цифр"
                                    maxLength={12}
                                />
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">Название подрядчика</label>
                                <input
                                    className="form-input"
                                    value={carrierName}
                                    onChange={e => setCarrierName(e.target.value)}
                                    placeholder="ООО «ТК» / ИП Иванов — свяжется с контрагентом автоматически"
                                />
                            </div>
                        </div>

                        {/* Block 2: Per-request params (только заявки: у них
                            дата/слот/стоимость задаются ПОСТРОЧНО) */}
                        {selectedIds.length > 0 && (
                        <div style={{ borderTop: '1px solid var(--color-border)', paddingTop: 12 }}>
                            <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 8 }}>Параметры по заявкам ({selectedIds.length})</div>
                            <div style={{ maxHeight: 300, overflow: 'auto' }}>
                                {/* TODO: migrate to TanStackDataTable — has inline form inputs per row */}
                                <table className="data-table" style={{ fontSize: 13, marginBottom: 0 }}>
                                    <thead>
                                        <tr>
                                            <th>Заявка</th>
                                            <th>Палеты</th>
                                            <th>Склад WB</th>
                                            <th>Дата забора *</th>
                                            <th>Интервал *</th>
                                            <th>Стоимость, ₽ *</th>
                                            <th>Дата сдачи *</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {selectedIds.map(id => {
                                            const item = items.find(i => i.id === id);
                                            const p = perRequestParams[id] || { pickup_date: '', pickup_time_slot: '', pickup_cost: '', delivery_date: '' };
                                            return (
                                                <tr key={id}>
                                                    <td style={{ fontWeight: 500, whiteSpace: 'nowrap' }}>{item?.number || `#${id}`}</td>
                                                    <td style={{ textAlign: 'center' }}>{item?.pallets_count || '\u2014'}</td>
                                                    <td style={{ fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>{item?.effective_wb_warehouse || '\u2014'}</td>
                                                    <td><input className="form-input" type="date" value={p.pickup_date} onChange={e => updateParam(id, 'pickup_date', e.target.value)} style={{ minWidth: 130, padding: '4px 6px', fontSize: 13 }} /></td>
                                                    <td>
                                                        <select className="form-input" value={p.pickup_time_slot} onChange={e => updateParam(id, 'pickup_time_slot', e.target.value)} style={{ minWidth: 110, padding: '4px 6px', fontSize: 13 }}>
                                                            <option value="">...</option>
                                                            <option value="08:00-12:00">08-12</option>
                                                            <option value="12:00-16:00">12-16</option>
                                                            <option value="16:00-20:00">16-20</option>
                                                            <option value="20:00-00:00">20-00</option>
                                                        </select>
                                                    </td>
                                                    <td><input className="form-input" type="number" min={0} value={p.pickup_cost} onChange={e => updateParam(id, 'pickup_cost', e.target.value ? Number(e.target.value) : '')} placeholder="0" style={{ minWidth: 80, padding: '4px 6px', fontSize: 13 }} /></td>
                                                    <td><input className="form-input" type="date" value={p.delivery_date} onChange={e => updateParam(id, 'delivery_date', e.target.value)} style={{ minWidth: 130, padding: '4px 6px', fontSize: 13 }} /></td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                        )}

                        {/* Block 3: перемещения. Реквизиты ОБЩИЕ на всю пачку —
                            таков контракт bulk-эндпоинта переездов, пер-строчных
                            дат и стоимостей у них нет. Поля необязательные:
                            переезд между своими складами возят и без забора. */}
                        {selectedTransferIds.length > 0 && (
                            <div style={{ borderTop: '1px solid var(--color-border)', paddingTop: 12, marginTop: 12 }}>
                                <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 4 }}>
                                    Параметры по перемещениям ({selectedTransferIds.length})
                                </div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                                    Уйдут ОБЩИМИ на все выбранные перемещения. Можно оставить пустыми —
                                    тогда назначится только машина и водитель.
                                </div>
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
                                    <div className="form-group" style={{ margin: 0 }}>
                                        <label className="form-label">Дата забора</label>
                                        <input
                                            className="form-input" type="date" value={transferParams.pickup_date}
                                            onChange={e => setTransferParams(prev => ({ ...prev, pickup_date: e.target.value }))}
                                        />
                                    </div>
                                    <div className="form-group" style={{ margin: 0 }}>
                                        <label className="form-label">Интервал</label>
                                        <select
                                            className="form-input" value={transferParams.pickup_time_slot}
                                            onChange={e => setTransferParams(prev => ({ ...prev, pickup_time_slot: e.target.value }))}
                                        >
                                            <option value="">...</option>
                                            <option value="08:00-12:00">08-12</option>
                                            <option value="12:00-16:00">12-16</option>
                                            <option value="16:00-20:00">16-20</option>
                                            <option value="20:00-00:00">20-00</option>
                                        </select>
                                    </div>
                                    <div className="form-group" style={{ margin: 0 }}>
                                        <label className="form-label">Стоимость, ₽</label>
                                        <input
                                            className="form-input" type="number" min={0} placeholder="0"
                                            value={transferParams.pickup_cost}
                                            onChange={e => setTransferParams(prev => ({ ...prev, pickup_cost: e.target.value ? Number(e.target.value) : '' }))}
                                        />
                                    </div>
                                    <div className="form-group" style={{ margin: 0 }}>
                                        <label className="form-label">Дата доставки</label>
                                        <input
                                            className="form-input" type="date" value={transferParams.delivery_date}
                                            onChange={e => setTransferParams(prev => ({ ...prev, delivery_date: e.target.value }))}
                                        />
                                    </div>
                                </div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 8 }}>
                                    {selectedTransferIds
                                        .map(id => transfers.find(t => t.id === id))
                                        .filter((t): t is StockTransfer => !!t)
                                        .map(t => `${t.number} (${transferFromName(t)} → ${transferToName(t)})`)
                                        .join(', ')}
                                </div>
                            </div>
                        )}

                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={() => setShowVehicleModal(false)}>Отмена</button>
                            <button className="btn btn-primary" onClick={handleAssignVehicle} disabled={actionLoading || !vehicleFormValid}>
                                {actionLoading ? 'Назначение...' : `Назначить (${selectedIds.length + selectedTransferIds.length})`}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* ─── Gazelka modal ─── */}
            {showGazelkaModal && gazelkaAssemblyId !== null && (
                <GazelkaModal
                    // 🔴 kind обязателен: без него модалка открывалась бы в режиме
                    // СБОРКИ, а id несёт переезд — draft ушёл бы на чужой документ,
                    // а send создал бы РЕАЛЬНЫЙ заказ у перевозчика по чужому
                    // маршруту. Отката у Газельки нет (нет API, только form-POST).
                    kind={gazelkaKind}
                    assemblyId={gazelkaAssemblyId}
                    assemblyNumber={gazelkaAssemblyNumber}
                    onClose={() => setShowGazelkaModal(false)}
                    onSuccess={() => {
                        setShowGazelkaModal(false);
                        load();
                    }}
                />
            )}

            {/* ─── Перевозчик и стоимость уехавшему переезду ─── */}
            {logisticsTargets.length > 0 && (
                <TransferLogisticsModal
                    transfers={logisticsTargets}
                    pickupWarehouseName={logisticsPickupWarehouse?.name ?? null}
                    pickupWarehouseCounterpartyId={logisticsPickupWarehouse?.counterparty_id ?? null}
                    submitting={actionLoading}
                    error={logisticsError}
                    onSubmit={handleSetTransferLogistics}
                    onClose={() => { setLogisticsTargets([]); setLogisticsError(''); }}
                />
            )}
        </div>
    );
}

// ─── Heatmap Matrix Component ───────────────────────────────────────────────

function HeatmapMatrix({ routes }: { routes: LogisticsRouteStat[] }) {
    const srcWarehouses = useMemo(() => [...new Set(routes.map(r => r.src_warehouse))].sort(), [routes]);
    const destWarehouses = useMemo(() => [...new Set(routes.map(r => r.dest_warehouse))].sort(), [routes]);

    const routeMap = useMemo(() => {
        const map: Record<string, number> = {};
        for (const r of routes) {
            map[`${r.src_warehouse}|${r.dest_warehouse}`] = r.avg_cost;
        }
        return map;
    }, [routes]);

    const allCosts = routes.map(r => r.avg_cost);
    const minCost = Math.min(...allCosts);
    const maxCost = Math.max(...allCosts);

    const interpolateColor = (value: number): string => {
        if (maxCost === minCost) return '#d1fae5';
        const t = (value - minCost) / (maxCost - minCost);
        // green (#d1fae5) to red (#fee2e2)
        const r = Math.round(209 + (254 - 209) * t);
        const g = Math.round(250 + (226 - 250) * t);
        const b = Math.round(229 + (226 - 229) * t);
        return `rgb(${r}, ${g}, ${b})`;
    };

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 16, overflow: 'auto' }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Матрица маршрутов (стоимость/палета)</div>
            <div style={{
                display: 'grid',
                gridTemplateColumns: `120px repeat(${destWarehouses.length}, 1fr)`,
                gap: 4,
                minWidth: destWarehouses.length * 100 + 120,
            }}>
                {/* Header row */}
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', padding: 8 }}></div>
                {destWarehouses.map(dw => (
                    <div key={dw} style={{ fontSize: 11, fontWeight: 600, textAlign: 'center', padding: 8, color: 'var(--color-text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {dw}
                    </div>
                ))}

                {/* Data rows */}
                {srcWarehouses.map(sw => (
                    <React.Fragment key={sw}>
                        <div style={{ fontSize: 12, fontWeight: 500, padding: 8, display: 'flex', alignItems: 'center', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {sw}
                        </div>
                        {destWarehouses.map(dw => {
                            const cost = routeMap[`${sw}|${dw}`];
                            if (cost === undefined) {
                                return (
                                    <div key={dw} style={{ padding: 8, textAlign: 'center', fontSize: 12, color: 'var(--color-text-muted)', borderRadius: 6, background: 'var(--color-bg)' }}>
                                        —
                                    </div>
                                );
                            }
                            return (
                                <div
                                    key={dw}
                                    style={{
                                        padding: 8,
                                        textAlign: 'center',
                                        fontSize: 12,
                                        fontWeight: 500,
                                        borderRadius: 6,
                                        background: interpolateColor(cost),
                                        color: '#1a1a1a',
                                        cursor: 'default',
                                        transition: 'transform 0.15s, box-shadow 0.15s',
                                    }}
                                    onMouseEnter={e => { e.currentTarget.style.transform = 'scale(1.05)'; e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.15)'; }}
                                    onMouseLeave={e => { e.currentTarget.style.transform = 'scale(1)'; e.currentTarget.style.boxShadow = 'none'; }}
                                >
                                    {formatNumber(cost, 0)}
                                </div>
                            );
                        })}
                    </React.Fragment>
                ))}
            </div>
        </div>
    );
}

// ─── Grouping helper ─────────────────────────────────────────────────────────

interface SubGroup {
    key: string;
    label: string;
    items: AssemblyRequest[];
}

interface Group {
    key: string;
    label: string;
    items: AssemblyRequest[];
    subGroups: SubGroup[];
}

function groupItems(items: AssemblyRequest[], groupBy: GroupBy): Group[] {
    const level1Key = groupBy === 'wb_warehouse'
        ? (i: AssemblyRequest) => i.effective_wb_warehouse || ''
        : (i: AssemblyRequest) => i.warehouse_name || '';

    const level2Key = groupBy === 'wb_warehouse'
        ? (i: AssemblyRequest) => i.warehouse_name || ''
        : (i: AssemblyRequest) => i.effective_wb_warehouse || '';

    // Group level 1
    const map1 = new Map<string, AssemblyRequest[]>();
    for (const item of items) {
        const k = level1Key(item);
        const arr = map1.get(k) || [];
        arr.push(item);
        map1.set(k, arr);
    }

    const groups: Group[] = [];
    for (const [key, groupItems] of map1) {
        // Sub-group level 2
        const map2 = new Map<string, AssemblyRequest[]>();
        for (const item of groupItems) {
            const k = level2Key(item);
            const arr = map2.get(k) || [];
            arr.push(item);
            map2.set(k, arr);
        }

        const subGroups: SubGroup[] = [];
        for (const [subKey, subItems] of map2) {
            subGroups.push({ key: subKey, label: subKey, items: subItems });
        }

        groups.push({
            key,
            label: key,
            items: groupItems,
            subGroups,
        });
    }

    return groups;
}
