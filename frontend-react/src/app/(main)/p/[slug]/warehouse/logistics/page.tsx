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
import type { AssemblyRequest, AssemblyStatus, LogisticsAnalyticsResponse, LogisticsRouteStat, LogisticsShipmentRow, LogisticsAnomalyType, CostForecastResponse, CostForecastWarehouse, GazelkaConfig } from '@/types/api';
import LogisticsCostByPallets from './components/LogisticsCostByPallets';
import LogisticsCarriers from './components/LogisticsCarriers';
import LogisticsAnomalies from './components/LogisticsAnomalies';
import CreatePaymentRequestModal from './components/CreatePaymentRequestModal';
import ShipmentPaymentsTab from './components/ShipmentPaymentsTab';
import GazelkaModal from './components/GazelkaModal';
import GazelkaOrdersTab from './components/GazelkaOrdersTab';

// Сегменты аналитики истории отправок.
type AnalyticsView = 'overview' | 'pallets' | 'carriers' | 'anomalies' | 'matrix';

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
    ACTIVE:       { label: 'Запланирована',  className: 'badge-warning' },
    ON_DELIVERY:  { label: 'В пути',         className: 'badge-info' },
    IN_PROGRESS:  { label: 'Разгрузка',      className: 'badge-info' },
    ACCEPTED:     { label: 'Принята',         className: 'badge-success' },
    CANCELLED:    { label: 'Отменена',        className: 'badge-secondary' },
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
    const [activeTab, setActiveTab] = useState<'active' | 'history' | 'payments' | 'gazelka'>('active');
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

    // Gazelka
    const [gazelkaConfig, setGazelkaConfig] = useState<GazelkaConfig | null>(null);
    const [gazelkaAssemblyId, setGazelkaAssemblyId] = useState<number | null>(null);
    const [gazelkaAssemblyNumber, setGazelkaAssemblyNumber] = useState<string>('');
    const [showGazelkaModal, setShowGazelkaModal] = useState(false);

    // Payment request modal
    const [paymentShipmentId, setPaymentShipmentId] = useState<number | undefined>(undefined);
    const [showPaymentModal, setShowPaymentModal] = useState(false);

    // Vehicle modal
    const [showVehicleModal, setShowVehicleModal] = useState(false);
    const [vehicleInfo, setVehicleInfo] = useState('');
    const [vehicleBrand, setVehicleBrand] = useState('');
    const [driverPhone, setDriverPhone] = useState('');
    const [carrierInn, setCarrierInn] = useState('');
    const [carrierName, setCarrierName] = useState('');
    const [selectedIds, setSelectedIds] = useState<number[]>([]);
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
     *  - статус READY или VEHICLE_ASSIGNED */
    const isGazelkaEligible = (item: AssemblyRequest): boolean => {
        if (!gazelkaConfig?.configured) return false;
        if (item.warehouse_name !== gazelkaConfig.warehouse_name) return false;
        return item.status === 'READY' || item.status === 'VEHICLE_ASSIGNED';
    };

    const openGazelkaModal = (item: AssemblyRequest) => {
        setGazelkaAssemblyId(item.id);
        setGazelkaAssemblyNumber(item.number);
        setShowGazelkaModal(true);
    };

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

    // ─── Warehouse list for filter ──────────────────────────────────────────

    const warehouseOptions = (() => {
        const key = groupBy === 'wb_warehouse'
            ? (i: AssemblyRequest) => i.effective_wb_warehouse || ''
            : (i: AssemblyRequest) => i.warehouse_name || '';
        const names = new Set(items.map(key).filter(Boolean));
        return Array.from(names).sort();
    })();

    // ─── Filtered items ─────────────────────────────────────────────────

    // Статусы, реально присутствующие в загруженных заявках (для дропдауна фильтра).
    const statusOptions = Array.from(new Set(items.map(i => i.status)));

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

    // ─── Grouping ─────────────────────────────────────────────────────────

    const grouped = groupItems(displayItems, groupBy);

    // ─── Summary ──────────────────────────────────────────────────────────
    // Для совместного якоря считаем по всей поставке (joint-итоги), а не по одной сборке.

    const totalRequests = displayItems.length;
    const totalPallets = displayItems.reduce((s, i) => s + (i.joint_supply ? jointTotalPallets(i) : i.pallets_count), 0);
    const totalWeight = displayItems.reduce((s, i) => s + (i.joint_supply ? jointTotalWeight(i) : (Number(i.total_weight_kg) || 0)), 0);

    // ─── Checked items summary ──────────────────────────────────────────

    const checkedItems = displayItems.filter(i => checkedIds.has(i.id));
    // Страховка от рассинхрона checkedIds↔данные: заявку могли отметить в READY, затем
    // отправить в Газельку (via_gazelka стал true после load) — из назначения машины её исключаем.
    const assignableCheckedIds = checkedItems.filter(i => !i.via_gazelka).map(i => i.id);
    const checkedPallets = checkedItems.reduce((s, i) => s + (i.joint_supply ? jointTotalPallets(i) : i.pallets_count), 0);
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

    const openVehicleModal = (ids: number[]) => {
        setSelectedIds(ids);
        setVehicleInfo('');
        setVehicleBrand('');
        setDriverPhone('');
        const params: Record<number, PerRequestParams> = {};
        for (const id of ids) {
            params[id] = { pickup_date: '', pickup_time_slot: '', pickup_cost: '', delivery_date: '' };
        }
        setPerRequestParams(params);
        setShowVehicleModal(true);
    };

    const vehicleFormValid = vehicleInfo.trim() && vehicleBrand.trim() && driverPhone.trim()
        && selectedIds.every(id => {
            const p = perRequestParams[id];
            return p && p.pickup_date && p.pickup_time_slot && p.pickup_cost !== '' && p.delivery_date;
        });

    const updateParam = (id: number, field: keyof PerRequestParams, value: string | number) => {
        setPerRequestParams(prev => ({
            ...prev,
            [id]: { ...prev[id], [field]: value },
        }));
    };

    const handleAssignVehicle = async () => {
        if (!vehicleFormValid || selectedIds.length === 0) return;
        setActionLoading(true);
        setError('');
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
            if (selectedIds.length === 1) {
                await api.assignVehicle(selectedIds[0], {
                    vehicle_info: vehicleInfo.trim(),
                    vehicle_brand: vehicleBrand.trim(),
                    driver_phone: driverPhone.trim(),
                    ...carrierPayload,
                    ...bulkItems[0],
                });
            } else {
                await api.assignVehicleBulk({
                    vehicle_info: vehicleInfo.trim(),
                    vehicle_brand: vehicleBrand.trim(),
                    driver_phone: driverPhone.trim(),
                    ...carrierPayload,
                    items: bulkItems,
                });
            }
            setShowVehicleModal(false);
            setCheckedIds(new Set());
            setCarrierInn('');
            setCarrierName('');
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
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

    const handleExport = () => {
        const data = items.map(i => ({
            '№': i.number,
            'Заявка ФФ': ffRequestNumbers(i).join(', '),
            'Статус': STATUS_MAP[i.status]?.label || i.status,
            'Висит дн': daysStuck(i) ?? '',
            'Склад': i.warehouse_name || '',
            'Склад WB': i.effective_wb_warehouse || '',
            'Поставка FBO': i.wb_supply_name || '',
            'Палеты': i.pallets_count,
            'Общий вес': i.total_weight_kg || 0,
            'Машина': i.vehicle_info || '',
            'Дата готовности': i.estimated_ready_date || '',
        }));
        exportToExcel(data, 'logistics_sheet');
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
                        style={{ borderRadius: '0 8px 8px 0' }}
                    >
                        🚚 Газелька
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

            {activeTab === 'gazelka' ? (
                <GazelkaOrdersTab />
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
                                        <option key={s} value={s}>{STATUS_MAP[s]?.label || s}</option>
                                    ))}
                                </select>
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <input
                                    className="form-input"
                                    type="search"
                                    value={activeSearch}
                                    onChange={e => setActiveSearch(e.target.value)}
                                    placeholder="Поиск: № заявки или поставка ФБО"
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
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 16 }}>
                        <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                            <div style={{ fontSize: 24, fontWeight: 600 }}>{totalRequests}</div>
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Заявок</div>
                        </div>
                        <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                            <div style={{ fontSize: 24, fontWeight: 600 }}>{totalPallets}</div>
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Палет</div>
                        </div>
                        <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                            <div style={{ fontSize: 24, fontWeight: 600 }}>{totalWeight > 0 ? formatNumber(totalWeight, 0) : '0'}</div>
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Общий вес (кг)</div>
                        </div>
                    </div>

                    {/* Content */}
                    {loading ? (
                        <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
                    ) : items.length === 0 ? (
                        <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                            <div style={{ fontSize: 48, marginBottom: 16 }}>🚛</div>
                            <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Нет заявок для логистики</div>
                            <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                                Здесь появятся заявки в статусе &laquo;Готово&raquo; и &laquo;Машина назначена&raquo;
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
                                        <th style={{ textAlign: 'right' }}>Прогноз ₽</th>
                                        <th>Статус</th>
                                        <th></th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {grouped.map(group => (
                                        <React.Fragment key={group.key}>
                                            <tr>
                                                <td colSpan={13} style={{ background: 'var(--color-bg-secondary)', fontWeight: 600, fontSize: 13, padding: '8px 12px' }}>
                                                    {group.label || 'Без склада'}
                                                    <span style={{ fontWeight: 400, color: 'var(--color-text-muted)', marginLeft: 8 }}>
                                                        {group.items.length} заявок, {group.items.reduce((s, i) => s + (i.joint_supply ? jointTotalPallets(i) : i.pallets_count), 0)} палет
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
                                                                    style={{ width: 16, height: 16, accentColor: 'var(--color-primary)', cursor: 'pointer' }}
                                                                />
                                                            )}
                                                        </td>
                                                        <td onClick={e => e.stopPropagation()}>
                                                            <Link
                                                                href={`/p/${slug}/warehouse/assembly/${item.id}`}
                                                                style={{ fontWeight: 600, textDecoration: 'none', color: 'var(--color-primary)' }}
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
                                                        <td style={{ textAlign: 'right' }}>{isJoint ? formatNumber(jointTotalPallets(item), 0) : item.pallets_count}</td>
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
                                        </React.Fragment>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    ) : (
                        /* ─── Cards view ─── */
                        <div>
                            {grouped.map(group => (
                                <div key={group.key} style={{ marginBottom: 24 }}>
                                    <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12, padding: '0 4px' }}>
                                        {group.label || 'Без склада'}
                                        <span style={{ fontWeight: 400, color: 'var(--color-text-muted)', marginLeft: 8, fontSize: 14 }}>
                                            ({group.items.length} заявок, {group.items.reduce((s, i) => s + (i.joint_supply ? jointTotalPallets(i) : i.pallets_count), 0)} палет)
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
                                                ? 'var(--color-primary)'
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
                                                        borderLeft: isJoint ? '3px solid var(--color-primary)' : undefined,
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
                                                                    style={{ width: 18, height: 18, accentColor: 'var(--color-primary)', cursor: 'pointer' }}
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
                                                        <div>Палет: {item.pallets_count} &middot; Вес: {item.total_weight_kg ? formatNumber(item.total_weight_kg, 0) + ' кг' : '\u2014'}</div>
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
                                                    contentStyle={{ background: 'var(--color-bg-primary)', border: '1px solid var(--color-border)', borderRadius: 8, fontSize: 13 }}
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
                                { key: 'pallets_count', label: 'Палеты', align: 'right', sortable: true, render: (v: number | null) => v != null ? formatNumber(v, 0) : '—' },
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
            {checkedIds.size > 0 && (
                <div style={{
                    position: 'fixed',
                    bottom: 0,
                    left: 0,
                    right: 0,
                    background: 'rgba(30, 30, 30, 0.95)',
                    backdropFilter: 'blur(8px)',
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
                        Выбрано: {checkedItems.length} заявки &middot; {checkedPallets} палет &middot; {checkedWeight > 0 ? formatNumber(checkedWeight, 0) : 0} кг
                    </div>
                    <button
                        className="btn btn-primary"
                        onClick={() => openVehicleModal(assignableCheckedIds)}
                        disabled={actionLoading || assignableCheckedIds.length === 0}
                    >
                        Назначить машину
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

                        {/* Block 1: Common vehicle data */}
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">Описание машины *</label>
                                <input className="form-input" value={vehicleInfo} onChange={e => setVehicleInfo(e.target.value)} placeholder="Номер, водитель, ТК..." autoFocus />
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">Марка машины *</label>
                                <input className="form-input" value={vehicleBrand} onChange={e => setVehicleBrand(e.target.value)} placeholder="ГАЗ-330, КАМАЗ..." />
                            </div>
                            <div className="form-group" style={{ margin: 0 }}>
                                <label className="form-label">Телефон водителя *</label>
                                <input className="form-input" value={driverPhone} onChange={e => setDriverPhone(e.target.value)} placeholder="+7 999 123-45-67" />
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

                        {/* Block 2: Per-request params */}
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

                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={() => setShowVehicleModal(false)}>Отмена</button>
                            <button className="btn btn-primary" onClick={handleAssignVehicle} disabled={actionLoading || !vehicleFormValid}>
                                {actionLoading ? 'Назначение...' : `Назначить (${selectedIds.length})`}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* ─── Gazelka modal ─── */}
            {showGazelkaModal && gazelkaAssemblyId !== null && (
                <GazelkaModal
                    assemblyId={gazelkaAssemblyId}
                    assemblyNumber={gazelkaAssemblyNumber}
                    onClose={() => setShowGazelkaModal(false)}
                    onSuccess={() => {
                        setShowGazelkaModal(false);
                        load();
                    }}
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
                                    <div key={dw} style={{ padding: 8, textAlign: 'center', fontSize: 12, color: 'var(--color-text-muted)', borderRadius: 6, background: 'var(--color-bg-secondary)' }}>
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
