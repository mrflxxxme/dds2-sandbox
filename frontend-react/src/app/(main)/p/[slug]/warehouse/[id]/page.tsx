'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatNumber, formatDate, formatDateTime } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type {
    Warehouse, InboundReceipt, OutboundShipment, AssemblyRequest,
    WarehouseStockRow, StockMovement, StockTransfer, DeliveryTimesResponse,
    DefectMarkOperation, VehicleStatus,
    FulfillmentStatus, FulfillmentProviderId, FfStocksResponse, FfStockRow, FfBoxPack, FfNomenclatureOption, FfRequestRow, FfRequestKind, FfStatusEvent, FfSyncRun, FfUnlinkedAssembly,
    FfBulkCreateResult, FfBulkCreateAssemblyResult, FfRepackCandidate, FfRepackCandidatesOut,
    ExpectedVehicleRow,
} from '@/types/api';
import type { Column } from '@/components/DataTable';
import Toast from '@/components/Toast';
import { FF_LINKED_STATUS_LABELS, FfLinkModal, ffSkippedNotice, ffStageBadge, ffStatusBadge, ffStatusLabel, ffEventBadge, ffEventSummary } from './ff-shared';
import FfBillingTab from './FfBillingTab';
import MigfullInboundModal from './MigfullInboundModal';
import { FfMismatchModal } from '@/components/FfMismatchModal';
import { whNamesMatch } from '@/lib/utils/ffLinkCandidates';

/* ─── Transfers helpers (общие для страницы и вкладки) ───────────────────── */

const TRANSFER_STATUS_LABELS: Record<string, string> = {
    DRAFT: 'Черновик',
    IN_TRANSIT: 'В пути',
    COMPLETED: 'Завершено',
};

// Требуют действия: входящие «в пути» (принять) + исходящие черновики (отправить/удалить)
function countActionableTransfers(transfers: StockTransfer[], warehouseId: number): number {
    return transfers.filter(t =>
        (t.status === 'IN_TRANSIT' && t.to_warehouse_id === warehouseId) ||
        (t.status === 'DRAFT' && t.from_warehouse_id === warehouseId)
    ).length;
}

/* ─── Main page ────────────────────────────────────────────────────────────── */

type WarehouseTab = 'all' | 'receipts' | 'shipments' | 'assemblies' | 'transfers' | 'stock' | 'defects' | 'delivery' | 'requisites' | 'fulfillment' | 'ffbilling';
const WAREHOUSE_TABS: WarehouseTab[] = ['all', 'receipts', 'shipments', 'assemblies', 'transfers', 'stock', 'defects', 'delivery', 'requisites', 'fulfillment', 'ffbilling'];

// Под-вкладки раздела «Фулфилмент» — вложенная навигация внутри одной вкладки.
type FfSubTab = 'stocks' | 'boxes' | 'assembly' | 'inbound' | 'return' | 'history' | 'sync';
// boxes (сопоставление короб→россыпь) — только для migfull («Натали»); фильтруется по провайдеру.
const FF_SUB_TABS: { key: FfSubTab; label: string; migfullOnly?: boolean }[] = [
    { key: 'stocks', label: 'Остатки' },
    { key: 'boxes', label: 'Сопоставление', migfullOnly: true },
    { key: 'assembly', label: 'Сборка' },
    { key: 'inbound', label: 'Приёмки' },
    { key: 'return', label: 'Возвраты' },
    { key: 'history', label: 'История' },
    { key: 'sync', label: 'Синхронизация' },
];
// Старые deep-ссылки ?tab=ff-* → раздел «Фулфилмент» + нужная под-вкладка (back-compat).
const FF_TAB_ALIASES: Record<string, FfSubTab> = {
    'ff-stocks': 'stocks', 'ff-boxes': 'boxes', 'ff-assembly': 'assembly', 'ff-inbound': 'inbound',
    'ff-return': 'return', 'ff-history': 'history', 'ff-sync': 'sync',
};

export default function WarehouseDetailPage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const slug = params.slug as string;
    const warehouseId = Number(params.id);
    const [tab, setTab] = useState<WarehouseTab>('receipts');
    const [ffSub, setFfSub] = useState<FfSubTab>('stocks');

    // Активная вкладка из ?tab= (back-ссылки подстраниц).
    // useSearchParams пуст на первом рендере до гидратации — реагируем только на непустое значение, без редиректов.
    // Старые ?tab=ff-* открывают раздел «Фулфилмент» на нужной под-вкладке.
    useEffect(() => {
        const raw = searchParams.get('tab') ?? '';
        if (!raw) return;
        if (raw in FF_TAB_ALIASES) {
            setTab('fulfillment');
            setFfSub(FF_TAB_ALIASES[raw]);
        } else if ((WAREHOUSE_TABS as string[]).includes(raw)) {
            setTab(raw as WarehouseTab);
        }
    }, [searchParams]);
    const [warehouse, setWarehouse] = useState<Warehouse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [ffStatus, setFfStatus] = useState<FulfillmentStatus | null>(null);

    // Counts for tab badges
    const [receiptCount, setReceiptCount] = useState(0);
    const [shipmentCount, setShipmentCount] = useState(0);
    const [transferCount, setTransferCount] = useState(0);
    const [defectCount, setDefectCount] = useState(0);
    const [assemblyCount, setAssemblyCount] = useState(0);

    // No modals — all create/detail views are separate pages

    // Бейдж «Перемещения» считается на уровне страницы — иначе он «0» до открытия вкладки
    const refreshTransferCount = useCallback(async () => {
        try {
            const transfers = await api.getTransfers(false, warehouseId);
            setTransferCount(countActionableTransfers(transfers, warehouseId));
        } catch { /* не валим страницу из-за бейджа */ }
    }, [warehouseId]);

    // Статус ФФ-интеграции — определяет видимость вкладок «ФФ …»
    const refreshFfStatus = useCallback(async () => {
        try {
            const s = await api.getFulfillmentStatus(warehouseId);
            setFfStatus(s);
        } catch { /* интеграция недоступна — вкладки просто не показываем */ }
    }, [warehouseId]);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const whs = await api.getWarehouses();
            const wh = whs.find(w => w.id === warehouseId);
            setWarehouse(wh || null);
            refreshTransferCount();
            refreshFfStatus();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, [warehouseId, refreshTransferCount, refreshFfStatus]);

    useEffect(() => { load(); }, [load]);

    const ffConnected = ffStatus?.connected === true;
    // Сигнал «связали заявки из карточки машины» → вкладки ФФ перезагружаются:
    // модалка живёт в блоке машин и иначе список заявок не узнаёт о связке.
    const [ffLinkTick, setFfLinkTick] = useState(0);

    // Если интеграцию отключили, а открыта ФФ-вкладка — возвращаемся на «Реквизиты».
    // Пока статус не загружен (ffStatus === null) — не сбрасываем: иначе ?tab=ff-* проигрывает гонку загрузке статуса.
    useEffect(() => {
        if (ffStatus && !ffStatus.connected && tab === 'fulfillment') {
            setTab('requisites');
        }
    }, [ffStatus, tab]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;
    if (!warehouse) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Склад не найден</div>;

    const isFulfillment = warehouse.warehouse_type === 'FULFILLMENT';

    const tabs = [
        { key: 'receipts' as const, label: 'Приёмки', count: receiptCount },
        ...(isFulfillment ? [{ key: 'shipments' as const, label: 'Отгрузки', count: shipmentCount }] : []),
        ...(isFulfillment ? [{ key: 'assemblies' as const, label: 'Заявки на отправку', count: assemblyCount }] : []),
        { key: 'transfers' as const, label: 'Перемещения', count: transferCount },
        { key: 'stock' as const, label: 'Остатки и статистика' },
        { key: 'defects' as const, label: 'Брак', count: defectCount },
        { key: 'delivery' as const, label: 'Время доставки' },
        { key: 'all' as const, label: 'История движений' },
        ...(ffConnected ? [
            { key: 'fulfillment' as const, label: 'Фулфилмент' },
        ] : []),
        ...(isFulfillment ? [{ key: 'ffbilling' as const, label: 'Тарифы ФФ' }] : []),
        { key: 'requisites' as const, label: 'Реквизиты' },
    ];

    return (
        <div className="animate-in">
            {/* Header with action buttons */}
            <div className="page-header">
                <div>
                    <h1 className="page-title">{warehouse.name}</h1>
                    <p className="page-subtitle">
                        {isFulfillment ? 'Фулфилмент' : 'Внешний склад'}
                        {warehouse.country ? ` — ${warehouse.country}` : ''}
                    </p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-primary" onClick={() => router.push(`/p/${slug}/warehouse/${warehouseId}/receipt/new`)}>
                        + Приёмка
                    </button>
                    <button className="btn btn-secondary" onClick={() => router.push(`/p/${slug}/warehouse/${warehouseId}/transfer/new`)}>
                        + Перемещение
                    </button>
                </div>
            </div>

            {/* Expected vehicles */}
            <ExpectedVehicles warehouseId={warehouseId} slug={slug} ffConnected={ffConnected} onFfLinked={() => setFfLinkTick(t => t + 1)} />

            {/* Tabs with counts */}
            <div style={{ display: 'flex', gap: 4, marginBottom: 20, borderBottom: '1px solid var(--color-border)', paddingBottom: 0 }}>
                {tabs.map(t => (
                    <button
                        key={t.key}
                        onClick={() => setTab(t.key)}
                        style={{
                            padding: '10px 16px',
                            fontSize: 14,
                            fontWeight: tab === t.key ? 600 : 400,
                            color: tab === t.key ? 'var(--color-primary)' : 'var(--color-text-muted)',
                            background: 'none',
                            border: 'none',
                            borderBottom: tab === t.key ? '2px solid var(--color-primary)' : '2px solid transparent',
                            cursor: 'pointer',
                            marginBottom: -1,
                        }}
                    >
                        {t.label}
                        {'count' in t && t.count !== undefined && (
                            <span style={{
                                marginLeft: 6,
                                fontSize: 11,
                                background: 'rgba(0,0,0,0.1)',
                                color: 'var(--color-text)',
                                borderRadius: 10,
                                padding: '2px 7px',
                                fontWeight: 600,
                            }}>
                                {t.count}
                            </span>
                        )}
                    </button>
                ))}
            </div>

            {tab === 'all' && <AllTab warehouseId={warehouseId} />}
            {tab === 'receipts' && (
                <ReceiptsTab
                    warehouseId={warehouseId}
                    onCountChange={setReceiptCount}
                    onTransfersChanged={refreshTransferCount}
                />
            )}
            {tab === 'shipments' && (
                <ShipmentsTab
                    warehouseId={warehouseId}
                    warehouseType={warehouse.warehouse_type}
                    onCountChange={setShipmentCount}
                />
            )}
            {tab === 'assemblies' && (
                <AssembliesTab
                    warehouseId={warehouseId}
                    slug={slug}
                    onCountChange={setAssemblyCount}
                />
            )}
            {tab === 'transfers' && (
                <TransfersTab
                    warehouseId={warehouseId}
                    onCountChange={setTransferCount}
                />
            )}
            {tab === 'stock' && <StockTab warehouseId={warehouseId} />}
            {tab === 'defects' && <DefectsTab warehouseId={warehouseId} onCountChange={setDefectCount} />}
            {tab === 'delivery' && <DeliveryTab warehouseId={warehouseId} />}
            {tab === 'fulfillment' && ffConnected && (
                <FulfillmentTabs warehouseId={warehouseId} slug={slug} sub={ffSub} onSubChange={setFfSub} provider={ffStatus?.provider ?? null} externalTick={ffLinkTick} />
            )}
            {tab === 'ffbilling' && isFulfillment && <FfBillingTab warehouseId={warehouseId} />}
            {tab === 'requisites' && (
                <>
                    <RequisitesTab warehouse={warehouse} onChanged={load} />
                    <FulfillmentSection warehouseId={warehouseId} status={ffStatus} onChanged={refreshFfStatus} />
                </>
            )}
        </div>
    );
}

/* ─── Requisites Tab (ИНН + название юрлица) ─────────────────────────── */

function RequisitesTab({ warehouse, onChanged }: { warehouse: Warehouse; onChanged: () => void }) {
    const [inn, setInn] = useState(warehouse.counterparty_inn ?? '');
    const [name, setName] = useState(warehouse.counterparty_name ?? '');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [message, setMessage] = useState('');

    // Дополнительные контрагенты ФФ (поверх основного)
    const extras = warehouse.extra_counterparties ?? [];
    const [extraInn, setExtraInn] = useState('');
    const [extraName, setExtraName] = useState('');
    const [extraBusy, setExtraBusy] = useState(false);
    const [extraError, setExtraError] = useState('');

    const handleAddExtra = async () => {
        const cleanInn = extraInn.trim();
        if (!/^\d{10,12}$/.test(cleanInn)) {
            setExtraError('ИНН должен быть 10 или 12 цифр');
            return;
        }
        setExtraBusy(true);
        setExtraError('');
        try {
            await api.addWarehouseExtraCounterparty(warehouse.id, { inn: cleanInn, name: extraName.trim() || null });
            setExtraInn('');
            setExtraName('');
            onChanged();
        } catch (e: unknown) {
            setExtraError(e instanceof Error ? e.message : 'Ошибка добавления');
        } finally {
            setExtraBusy(false);
        }
    };

    const handleRemoveExtra = async (cpId: number) => {
        if (!confirm('Отвязать доп. контрагента от склада?')) return;
        setExtraBusy(true);
        setExtraError('');
        try {
            await api.removeWarehouseExtraCounterparty(warehouse.id, cpId);
            onChanged();
        } catch (e: unknown) {
            setExtraError(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setExtraBusy(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        setError('');
        setMessage('');
        try {
            const cleanInn = inn.trim();
            if (cleanInn && !/^\d{10,12}$/.test(cleanInn)) {
                setError('ИНН должен быть 10 или 12 цифр');
                setSaving(false);
                return;
            }
            await api.setWarehouseCounterparty(warehouse.id, {
                inn: cleanInn || null,
                name: name.trim() || null,
            });
            setMessage(cleanInn ? 'Реквизиты сохранены. Контрагент привязан как «Фулфилмент».' : 'Реквизиты очищены.');
            onChanged();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    const handleUnlink = async () => {
        if (!confirm('Отвязать контрагента от склада?')) return;
        setSaving(true);
        setError('');
        try {
            await api.setWarehouseCounterparty(warehouse.id, { inn: null, name: null });
            setInn('');
            setName('');
            setMessage('Контрагент отвязан от склада.');
            onChanged();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="glass-card" style={{ padding: 24 }}>
            <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0, marginBottom: 8 }}>Реквизиты компании</h3>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 20 }}>
                ИНН и название юр. лица, которое обслуживает этот склад. Используется для авто-категоризации
                расходов из выписок — транзакции с совпадающим ИНН попадут в категорию «Фулфилмент».
            </p>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 16, maxWidth: 700 }}>
                <div className="form-group">
                    <label className="form-label">ИНН</label>
                    <input
                        className="form-input"
                        value={inn}
                        onChange={e => setInn(e.target.value)}
                        placeholder="10 или 12 цифр"
                        maxLength={12}
                    />
                </div>
                <div className="form-group">
                    <label className="form-label">Название компании</label>
                    <input
                        className="form-input"
                        value={name}
                        onChange={e => setName(e.target.value)}
                        placeholder="ООО «Ромашка» / ИП Иванов"
                    />
                </div>
            </div>

            {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 12 }}>{error}</div>}
            {message && <div style={{ color: 'var(--color-success)', fontSize: 13, marginTop: 12 }}>{message}</div>}

            <div style={{ marginTop: 20, display: 'flex', gap: 8 }}>
                <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving}>
                    {saving ? 'Сохранение...' : 'Сохранить'}
                </button>
                {warehouse.counterparty_id && (
                    <button className="btn btn-danger btn-sm" onClick={handleUnlink} disabled={saving}>
                        Отвязать контрагента
                    </button>
                )}
            </div>

            {warehouse.counterparty_id && (
                <div style={{
                    marginTop: 20, padding: 12, background: 'var(--color-bg)',
                    borderRadius: 8, fontSize: 13, color: 'var(--color-text-muted)',
                }}>
                    Привязан контрагент #{warehouse.counterparty_id}
                    {warehouse.counterparty_inn ? ` · ИНН ${warehouse.counterparty_inn}` : ''}
                    {warehouse.counterparty_name ? ` · ${warehouse.counterparty_name}` : ''}
                </div>
            )}

            {/* ─── Дополнительные контрагенты ФФ ─────────────────────────── */}
            <div style={{ marginTop: 32, borderTop: '1px solid var(--color-border)', paddingTop: 24 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0, marginBottom: 8 }}>
                    Дополнительные контрагенты ФФ
                </h3>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 20 }}>
                    Другие юр. лица, которые тоже относятся к этому складу. Их ИНН так же попадут в
                    категорию «Фулфилмент» при импорте выписок.
                </p>

                {extras.length === 0 ? (
                    <div style={{ fontSize: 13, color: 'var(--color-text-dim)', marginBottom: 16 }}>
                        Пока нет дополнительных контрагентов.
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16, maxWidth: 700 }}>
                        {extras.map(cp => (
                            <div
                                key={cp.id}
                                style={{
                                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                    gap: 12, padding: 12, background: 'var(--color-bg)', borderRadius: 8, fontSize: 13,
                                }}
                            >
                                <span style={{ color: 'var(--color-text)' }}>
                                    #{cp.id}
                                    {cp.inn ? ` · ИНН ${cp.inn}` : ''}
                                    {cp.name ? ` · ${cp.name}` : ''}
                                </span>
                                <button
                                    className="btn btn-danger btn-sm"
                                    onClick={() => handleRemoveExtra(cp.id)}
                                    disabled={extraBusy}
                                >
                                    Отвязать
                                </button>
                            </div>
                        ))}
                    </div>
                )}

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 16, maxWidth: 700 }}>
                    <div className="form-group">
                        <label className="form-label">ИНН</label>
                        <input
                            className="form-input"
                            value={extraInn}
                            onChange={e => setExtraInn(e.target.value)}
                            placeholder="10 или 12 цифр"
                            maxLength={12}
                        />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Название компании</label>
                        <input
                            className="form-input"
                            value={extraName}
                            onChange={e => setExtraName(e.target.value)}
                            placeholder="ИП Мащенок Никита Сергеевич"
                        />
                    </div>
                </div>

                {extraError && (
                    <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 12 }}>{extraError}</div>
                )}

                <div style={{ marginTop: 20 }}>
                    <button className="btn btn-secondary btn-sm" onClick={handleAddExtra} disabled={extraBusy}>
                        {extraBusy ? 'Сохранение...' : 'Добавить контрагента'}
                    </button>
                </div>
            </div>
        </div>
    );
}

/* ─── Fulfillment integration (skladbot / wmscelicom / migfull) — блок в «Реквизитах» ── */

const FF_EXPIRY_WARNING_MS = 30 * 24 * 60 * 60 * 1000; // 30 дней

const FF_PROVIDER_LABELS: Record<string, string> = {
    skladbot: 'skladbot.ru',
    wmscelicom: 'Целиком (WMS Celicom)',
    migfull: 'Натали (migfull.app)',
    mprocket: 'Нитропак (mprocket)',
};

function FulfillmentSection({ warehouseId, status, onChanged }: {
    warehouseId: number;
    status: FulfillmentStatus | null;
    onChanged: () => void;
}) {
    const [provider, setProvider] = useState<FulfillmentProviderId>('skladbot');
    const [token, setToken] = useState('');
    const [baseUrl, setBaseUrl] = useState('');
    const [tenantGuid, setTenantGuid] = useState('');
    const [customerId, setCustomerId] = useState('');  // skladbot: id кабинета (для FF-operator токена)
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [syncMsg, setSyncMsg] = useState('');

    const connected = status?.connected === true;

    const handleConnect = async () => {
        if (!token.trim()) {
            setError('Введите токен');
            return;
        }
        if (provider === 'wmscelicom' && !baseUrl.trim()) {
            setError('Укажите адрес инстанса (например client.wmscelicom.ru)');
            return;
        }
        if (provider === 'migfull' && !tenantGuid.trim()) {
            setError('Укажите GUID кабинета migfull.app');
            return;
        }
        setBusy(true);
        setError('');
        setSyncMsg('');
        try {
            await api.connectFulfillment(warehouseId, {
                provider,
                token: token.trim(),
                base_url: provider === 'wmscelicom' ? baseUrl.trim() : null,
                tenant_guid: provider === 'migfull' ? tenantGuid.trim() : null,
                customer_id: provider === 'skladbot' && customerId.trim() ? Number(customerId.trim()) : null,
            });
            setToken('');
            setBaseUrl('');
            setTenantGuid('');
            setCustomerId('');
            onChanged();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка подключения');
        } finally {
            setBusy(false);
        }
    };

    const handleDisconnect = async () => {
        if (!confirm('Отключить фулфилмент-интеграцию? Токен будет удалён.')) return;
        setBusy(true);
        setError('');
        setSyncMsg('');
        try {
            await api.disconnectFulfillment(warehouseId);
            onChanged();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка отключения');
        } finally {
            setBusy(false);
        }
    };

    const handleSync = async () => {
        setBusy(true);
        setError('');
        setSyncMsg('');
        try {
            const r = await api.syncFulfillment(warehouseId);
            const unmatched = r.unmatched_barcodes > 0
                ? `, несматченных ШК: ${formatNumber(r.unmatched_barcodes, 0)}`
                : '';
            setSyncMsg(`Синхронизировано: ${formatNumber(r.stocks_synced, 0)} остатков / ${formatNumber(r.requests_synced, 0)} заявок${unmatched}`);
            onChanged();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка синхронизации');
        } finally {
            setBusy(false);
        }
    };

    // token_expires_at — naive UTC ISO без зоны; добавляем 'Z', чтобы Date.parse не счёл локальным
    const expiresMs = status?.token_expires_at
        ? Date.parse(status.token_expires_at.endsWith('Z') ? status.token_expires_at : status.token_expires_at + 'Z')
        : null;
    const expired = expiresMs !== null && expiresMs <= Date.now();
    const expiringSoon = !expired && expiresMs !== null && expiresMs - Date.now() < FF_EXPIRY_WARNING_MS;

    return (
        <div className="glass-card" style={{ padding: 24, marginTop: 16 }}>
            <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0, marginBottom: 8 }}>
                Фулфилмент-интеграция
                {connected && status?.provider ? ` (${FF_PROVIDER_LABELS[status.provider] || status.provider})` : ''}
            </h3>

            {!connected ? (
                <>
                    <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 20 }}>
                        {provider === 'skladbot'
                            ? 'Подключите личный кабинет skladbot.ru по seller-токену — появится вкладка «Фулфилмент» с остатками, сборкой, приёмками и журналом синхронизаций.'
                            : provider === 'migfull'
                                ? 'Read-only API склада «Натали» (migfull.app): остатки, приёмки и отгрузки.'
                                : 'Подключите инстанс «Целиком» (WMS Celicom) по API-токену и адресу инстанса — появится вкладка «Фулфилмент» с остатками, сборкой (отгрузки FBO) и приёмками.'}
                    </p>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', maxWidth: 860, flexWrap: 'wrap' }}>
                        <div className="form-group" style={{ width: 220, marginBottom: 0 }}>
                            <label className="form-label">Провайдер</label>
                            <select
                                className="form-input"
                                value={provider}
                                onChange={e => { setProvider(e.target.value as FulfillmentProviderId); setError(''); }}
                            >
                                <option value="skladbot">skladbot.ru</option>
                                <option value="wmscelicom">Целиком (WMS Celicom)</option>
                                <option value="migfull">Натали (migfull.app)</option>
                            </select>
                        </div>
                        {provider === 'wmscelicom' && (
                            <div className="form-group" style={{ width: 260, marginBottom: 0 }}>
                                <label className="form-label">Адрес инстанса</label>
                                <input
                                    className="form-input"
                                    value={baseUrl}
                                    onChange={e => setBaseUrl(e.target.value)}
                                    placeholder="client.wmscelicom.ru"
                                    autoComplete="off"
                                />
                            </div>
                        )}
                        {provider === 'migfull' && (
                            <div className="form-group" style={{ width: 260, marginBottom: 0 }}>
                                <label className="form-label">GUID кабинета</label>
                                <input
                                    className="form-input"
                                    value={tenantGuid}
                                    onChange={e => setTenantGuid(e.target.value)}
                                    placeholder="123e4567-e89b-…"
                                    autoComplete="off"
                                />
                            </div>
                        )}
                        {provider === 'skladbot' && (
                            <div className="form-group" style={{ width: 200, marginBottom: 0 }}>
                                <label className="form-label" title="Для FF-operator токена (видит несколько кабинетов) укажите id вашего кабинета. Для селлер-токена можно оставить пустым.">ID кабинета</label>
                                <input
                                    className="form-input"
                                    type="number"
                                    value={customerId}
                                    onChange={e => setCustomerId(e.target.value)}
                                    placeholder="напр. 6282"
                                    autoComplete="off"
                                />
                            </div>
                        )}
                        <div className="form-group" style={{ flex: 1, minWidth: 220, marginBottom: 0 }}>
                            <label className="form-label">Токен</label>
                            <input
                                className="form-input"
                                type="password"
                                value={token}
                                onChange={e => setToken(e.target.value)}
                                placeholder={provider === 'skladbot' ? 'Seller-токен skladbot.ru' : provider === 'migfull' ? 'Bearer-токен migfull.app' : 'API-токен Целиком'}
                                autoComplete="off"
                            />
                        </div>
                        <button className="btn btn-primary" onClick={handleConnect} disabled={busy}>
                            {busy ? 'Подключение...' : 'Подключить'}
                        </button>
                    </div>
                </>
            ) : (
                <>
                    <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '8px 16px', fontSize: 13, maxWidth: 700, marginBottom: 16 }}>
                        <span style={{ color: 'var(--color-text-muted)' }}>Кабинет</span>
                        <span style={{ fontWeight: 600 }}>
                            {status?.customer_name || status?.api_base_url || '—'}
                            {status?.customer_id ? ` (#${status.customer_id})` : ''}
                        </span>
                        <span style={{ color: 'var(--color-text-muted)' }}>Токен</span>
                        <span>{status?.key_preview || '—'}</span>
                        {(status?.token_expires_at || status?.provider === 'skladbot') && (
                            <>
                                <span style={{ color: 'var(--color-text-muted)' }}>Действует до</span>
                                <span
                                    style={{
                                        color: expired ? 'var(--color-danger)' : expiringSoon ? 'var(--color-warning)' : undefined,
                                        fontWeight: expired || expiringSoon ? 600 : 400,
                                    }}
                                >
                                    {status?.token_expires_at ? formatDate(status.token_expires_at) : '—'}
                                    {expired ? ' — истёк, переподключите' : expiringSoon ? ' — скоро истечёт' : ''}
                                </span>
                            </>
                        )}
                        <span style={{ color: 'var(--color-text-muted)' }}>Последняя синхронизация</span>
                        <span>{status?.last_sync_at ? formatDateTime(status.last_sync_at) : 'ещё не выполнялась'}</span>
                    </div>
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-primary btn-sm" onClick={handleSync} disabled={busy}>
                            {busy ? 'Синхронизация...' : 'Синхронизировать сейчас'}
                        </button>
                        <button className="btn btn-danger btn-sm" onClick={handleDisconnect} disabled={busy}>
                            Отключить
                        </button>
                    </div>
                </>
            )}

            {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 12 }}>{error}</div>}
            {syncMsg && <div style={{ color: 'var(--color-success)', fontSize: 13, marginTop: 12 }}>{syncMsg}</div>}
        </div>
    );
}

/* ─── Expected Vehicles (Ожидаемые поставки) ──────────────────────────── */

const STATUS_LABELS_VEHICLE: Record<string, string> = {
    SHIPPED: 'Отгружен', CUSTOMS: 'Таможня', DISPATCHED: 'Отправлена',
};
const STATUS_COLORS_VEHICLE: Record<string, string> = {
    SHIPPED: '#3b82f6', CUSTOMS: '#f59e0b', DISPATCHED: '#8b5cf6',
};

const NEXT_VEHICLE_ACTION: Record<string, { status: string; label: string; color: string }> = {
    SHIPPED: { status: 'CUSTOMS', label: 'На таможню', color: '#f59e0b' },
    CUSTOMS: { status: 'DISPATCHED', label: 'Отправлена', color: '#8b5cf6' },
    // DISPATCHED: приёмка через InboundReceipt (таб "Приёмки")
};

function ExpectedVehicles({ warehouseId, slug, ffConnected, onFfLinked }: { warehouseId: number; slug: string; ffConnected: boolean; onFfLinked?: () => void }) {
    const router = useRouter();
    const [vehicles, setVehicles] = useState<ExpectedVehicleRow[]>([]);
    // Модалка «Связать заявки ФФ» — машина, к чьей приёмке привязываем
    const [linkFor, setLinkFor] = useState<ExpectedVehicleRow | null>(null);
    // Модалка «Создать поставку у Натали» — машина, из чьей приёмки создаём
    const [natPushFor, setNatPushFor] = useState<ExpectedVehicleRow | null>(null);
    // Склад migfull-портала (кнопка «Создать поставку у Натали» — только на нём)
    const [migfullWhId, setMigfullWhId] = useState<number | null>(null);
    const [toast, setToast] = useState('');

    const loadVehicles = useCallback(() => {
        api.getExpectedVehicles(warehouseId).then(setVehicles).catch(() => {});
    }, [warehouseId]);

    useEffect(() => { loadVehicles(); }, [loadVehicles]);

    useEffect(() => {
        let cancelled = false;
        api.migfullPortalConfig()
            .then(c => { if (!cancelled) setMigfullWhId(c.configured ? c.warehouse_id : null); })
            .catch(() => { /* портал не подключён — кнопки просто нет */ });
        return () => { cancelled = true; };
    }, []);

    const handleAction = async (e: React.MouseEvent, orderNo: string, nextStatus: string) => {
        e.stopPropagation();
        try {
            await api.updateVehicleStatus(orderNo, { status: nextStatus as VehicleStatus });
            loadVehicles();
        } catch (err: unknown) {
            alert(err instanceof Error ? err.message : 'Ошибка');
        }
    };

    if (vehicles.length === 0) return null;

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                <span>🚛</span> Ожидаемые поставки ({vehicles.length})
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
                {vehicles.map(v => {
                    const action = NEXT_VEHICLE_ACTION[v.status];
                    return (
                        <div
                            key={v.order_no}
                            onClick={() => router.push(`/p/${slug}/supply-chain/vehicles/${encodeURIComponent(v.order_no)}`)}
                            style={{
                                padding: '12px 14px', borderRadius: 12,
                                border: '1px solid var(--color-border)',
                                cursor: 'pointer', transition: 'all 0.15s',
                            }}
                            onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--color-primary)'; e.currentTarget.style.transform = 'translateY(-1px)'; }}
                            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--color-border)'; e.currentTarget.style.transform = ''; }}
                        >
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                                <span style={{ fontWeight: 600, fontSize: 13 }}>{v.order_no}</span>
                                <span style={{
                                    padding: '2px 8px', borderRadius: 10, fontSize: 10, fontWeight: 600,
                                    color: '#fff', background: STATUS_COLORS_VEHICLE[v.status] || '#6b7280',
                                }}>
                                    {STATUS_LABELS_VEHICLE[v.status] || v.status}
                                </span>
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <div style={{ display: 'flex', gap: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    <span>{v.items_count} поз. / {formatNumber(v.total_qty, 0)} шт</span>
                                    {v.estimated_arrival_date && (
                                        <span style={{ color: 'var(--color-text)' }}>📅 {formatDate(v.estimated_arrival_date)}</span>
                                    )}
                                </div>
                                {action ? (
                                    <button
                                        onClick={e => handleAction(e, v.order_no, action.status)}
                                        style={{
                                            padding: '3px 10px', borderRadius: 8, fontSize: 11, fontWeight: 600,
                                            border: `1px solid ${action.color}`, background: action.color,
                                            color: '#fff', cursor: 'pointer', whiteSpace: 'nowrap',
                                        }}
                                    >
                                        {action.label}
                                    </button>
                                ) : v.status === 'DISPATCHED' ? (
                                    <span style={{ fontSize: 11, color: 'var(--color-success)', fontWeight: 600 }}>→ Приёмки</span>
                                ) : null}
                            </div>
                            {v.receipt_id != null && (ffConnected || migfullWhId === warehouseId) && (
                                <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                    {ffConnected && (
                                        <button
                                            className="btn btn-secondary btn-sm"
                                            style={{ fontSize: 11, padding: '3px 10px' }}
                                            onClick={e => { e.stopPropagation(); setLinkFor(v); }}
                                            title="Связать несвязанные заявки ФФ (kind=приёмка) с нашей приёмкой этой машины"
                                        >
                                            Связать заявки ФФ
                                        </button>
                                    )}
                                    {migfullWhId === warehouseId && (
                                        <button
                                            className="btn btn-secondary btn-sm"
                                            style={{ fontSize: 11, padding: '3px 10px' }}
                                            onClick={e => { e.stopPropagation(); setNatPushFor(v); }}
                                            title="Создать поставку (приёмку) в WMS Натали из состава приёмки этой машины"
                                        >
                                            Создать поставку у Натали
                                        </button>
                                    )}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>

            {linkFor && linkFor.receipt_id != null && (
                <FfVehicleLinkModal
                    warehouseId={warehouseId}
                    vehicle={linkFor}
                    onClose={() => setLinkFor(null)}
                    onLinked={linkedNumbers => {
                        setLinkFor(null);
                        setToast(`Связано с приёмкой машины ${linkFor.order_no}: ${linkedNumbers.join(', ')}`);
                        loadVehicles();
                        onFfLinked?.();  // вкладки ФФ перезагружают списки
                    }}
                />
            )}
            {natPushFor && natPushFor.receipt_id != null && (
                <MigfullInboundModal
                    receiptId={natPushFor.receipt_id}
                    vehicleOrderNo={natPushFor.order_no}
                    onClose={() => setNatPushFor(null)}
                    onSuccess={res => {
                        setToast(`Поставка у Натали создана: ${res.shipment_number || res.shipment_guid || '—'}`);
                        loadVehicles();
                        onFfLinked?.();  // вкладки ФФ перезагружают списки (PVB прилетит синком уже связанной)
                    }}
                />
            )}
            {toast && <Toast message={toast} onClose={() => setToast('')} />}
        </div>
    );
}

/* ─── Модалка «Связать заявки ФФ» с приёмкой машины (мульти-выбор) ────────── */

function FfVehicleLinkModal({ warehouseId, vehicle, onClose, onLinked }: {
    warehouseId: number;
    /** машина (receipt_id — наша приёмка, цель связки) */
    vehicle: ExpectedVehicleRow;
    onClose: () => void;
    /** успешно связали все выбранные: номера заявок (для тоста родителя) */
    onLinked: (linkedNumbers: string[]) => void;
}) {
    const [rows, setRows] = useState<FfRequestRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
    const [acting, setActing] = useState(false);
    // Поиск по номеру/стадии — заявок бывают десятки, глазами не найти.
    const [search, setSearch] = useState('');

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        api.getFulfillmentRequests(warehouseId, 'inbound')
            .then(r => {
                if (controller.signal.aborted) return;
                // Только свободные приёмки ФФ: без нашей приёмки/перемещения и не пара «вскрытия коробов»
                setRows(r.filter(x =>
                    !x.archived
                    && x.inbound_receipt_id == null
                    && x.stock_transfer_id == null
                    && x.repack_return_id == null
                ));
            })
            .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка загрузки заявок ФФ'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [warehouseId]);

    const toggle = (id: number) => {
        setSelected(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });
    };

    const handleLink = async () => {
        if (vehicle.receipt_id == null || selected.size === 0) return;
        setActing(true);
        setError('');
        // Последовательно, ошибки — по-заявочно (мульти-связка N→1 разрешена бэком для migfull)
        const errs: Record<number, string> = {};
        const linked: string[] = [];
        const okIds = new Set<number>();
        for (const row of rows.filter(r => selected.has(r.id))) {
            try {
                await api.linkFulfillmentRequest(warehouseId, row.id, { inbound_receipt_id: vehicle.receipt_id });
                linked.push(row.number || row.external_id);
                okIds.add(row.id);
            } catch (e: unknown) {
                errs[row.id] = e instanceof Error ? e.message : 'Ошибка связывания';
            }
        }
        setActing(false);
        if (Object.keys(errs).length === 0) {
            onLinked(linked);
            return;
        }
        // Частичный успех: связанные убираем из списка, ошибки показываем в строках
        setRowErrors(errs);
        if (okIds.size > 0) {
            setRows(prev => prev.filter(r => !okIds.has(r.id)));
            setSelected(prev => new Set([...prev].filter(id => !okIds.has(id))));
        }
        setError(linked.length > 0
            ? `Связано: ${linked.join(', ')}. Остальные — с ошибками (см. строки)`
            : 'Связать не удалось — ошибки указаны в строках');
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-wide modal-card-solid" onClick={e => e.stopPropagation()}>
                <h2 className="modal-title" style={{ marginBottom: 8 }}>
                    Связать заявки ФФ — {vehicle.order_no}
                </h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    Выбранные заявки ФФ (приёмки) будут связаны с нашей приёмкой этой машины.
                    Можно выбрать несколько — например, штуки и короба одной поставки.
                </p>

                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

                {!loading && rows.length > 0 && (
                    <input
                        className="form-input"
                        style={{ marginBottom: 12, fontSize: 13 }}
                        placeholder="Поиск по номеру или стадии…"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        autoFocus
                    />
                )}

                {loading ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                ) : rows.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                        Несвязанных заявок ФФ (приёмок) нет
                    </div>
                ) : rows.filter(r => !search
                    || (r.number || '').toLowerCase().includes(search.toLowerCase())
                    || (r.stage_title || r.status || '').toLowerCase().includes(search.toLowerCase())
                ).length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                        Ничего не найдено по «{search}»
                    </div>
                ) : (
                    <div className="ff-link-list">
                        {rows.filter(r => !search
                            || (r.number || '').toLowerCase().includes(search.toLowerCase())
                            || (r.stage_title || r.status || '').toLowerCase().includes(search.toLowerCase())
                        ).map(row => (
                            <label key={row.id} className="ff-link-row" style={{ cursor: 'pointer' }}>
                                <input
                                    type="checkbox"
                                    checked={selected.has(row.id)}
                                    onChange={() => toggle(row.id)}
                                    disabled={acting}
                                />
                                {/* flex:1 — .ff-link-row даёт space-between, прижимаем контент влево */}
                                <div className="ff-link-row-main" style={{ flex: 1 }}>
                                    <div className="ff-link-row-head">
                                        <span className="ff-link-row-number">{row.number || row.external_id}</span>
                                        {(row.stage_title || row.status) && (
                                            <span className="badge badge-secondary" style={{ fontSize: 11, padding: '2px 8px' }}>
                                                {row.stage_title || row.status}
                                            </span>
                                        )}
                                        <span className="ff-link-row-meta">
                                            {row.external_created_at ? `${formatDate(row.external_created_at)} · ` : ''}
                                            {formatNumber(row.total_qty_units ?? row.total_qty ?? 0, 0)} шт
                                            {row.total_boxes != null && ` · 📦 ${formatNumber(row.total_boxes, 0)} кор.`}
                                        </span>
                                        {/* Мгновенный маркер вида заявки — пара «штучная + коробовая»
                                            одной машины различима без чтения цифр. */}
                                        <span
                                            className={`badge ${row.total_boxes != null ? 'badge-info' : 'badge-secondary'}`}
                                            style={{ fontSize: 11, padding: '2px 8px' }}
                                        >
                                            {row.total_boxes != null ? 'коробами' : 'штучная'}
                                        </span>
                                    </div>
                                    {rowErrors[row.id] && (
                                        <div style={{ color: 'var(--color-danger)', fontSize: 12, marginTop: 4 }}>{rowErrors[row.id]}</div>
                                    )}
                                </div>
                            </label>
                        ))}
                    </div>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={acting}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleLink} disabled={acting || selected.size === 0 || rows.length === 0}>
                        {acting ? 'Связывание...' : `Связать выбранные (${formatNumber(selected.size, 0)})`}
                    </button>
                </div>
            </div>
        </div>
    );
}

/* ─── Tab: Все (движения) ───────────────────────────────────────────────── */

function AllTab({ warehouseId }: { warehouseId: number }) {
    const [movements, setMovements] = useState<StockMovement[]>([]);
    const [loading, setLoading] = useState(true);
    const [deletingId, setDeletingId] = useState<number | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const r = await api.getStockMovements(warehouseId);
            setMovements(r);
        } catch { /* ignore */ }
        setLoading(false);
    }, [warehouseId]);

    useEffect(() => { load(); }, [load]);

    const DEFECT_TYPES = new Set(['DEFECT_MARK', 'DEFECT_RECEIVE', 'DEFECT_WRITEOFF', 'DEFECT_RECOVER']);

    const handleDelete = async (m: StockMovement) => {
        if (!DEFECT_TYPES.has(m.movement_type)) return;
        if (!confirm('Удалить движение?')) return;
        setDeletingId(m.id);
        try {
            await api.deleteDefectMovement(warehouseId, m.id);
            await load();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка удаления');
        } finally {
            setDeletingId(null);
        }
    };

    const movementTypeLabel: Record<string, string> = {
        INBOUND: 'Приёмка',
        INBOUND_EDIT: 'Корректировка приёмки',
        INBOUND_CANCEL: 'Отмена приёмки',
        OUTBOUND: 'Отгрузка',
        OUTBOUND_CANCEL: 'Отмена отгрузки',
        TRANSFER_IN: 'Перемещение (вход)',
        TRANSFER_OUT: 'Перемещение (выход)',
        ADJUSTMENT: 'Корректировка',
        DEFECT_MARK: 'Отметка брака',
        DEFECT_RECEIVE: 'Приёмка брака',
        DEFECT_WRITEOFF: 'Списание брака',
        DEFECT_RECOVER: 'Восстановление',
        DEFECT_TRANSFER_OUT: 'Брак: перемещение (выход)',
        DEFECT_TRANSFER_IN: 'Брак: перемещение (вход)',
    };

    const cols: Column[] = [
        { key: 'created_at', label: 'Дата', format: 'date' },
        {
            key: 'movement_type', label: 'Тип',
            render: (v: string) => movementTypeLabel[v] || v,
        },
        { key: 'barcode', label: 'ШК' },
        {
            key: 'quantity', label: 'Кол-во', align: 'right',
            render: (v: number) => (
                <span style={{ color: v > 0 ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                    {v > 0 ? '+' : ''}{v}
                </span>
            ),
        },
        {
            key: 'defect_delta', label: 'Брак', align: 'right',
            render: (v: number) => {
                if (!v) return <span style={{ color: 'var(--color-text-muted)' }}>{'\u2014'}</span>;
                return (
                    <span style={{ color: v > 0 ? 'var(--color-warning)' : 'var(--color-success)', fontWeight: 600 }}>
                        {v > 0 ? '+' : ''}{v}
                    </span>
                );
            },
        },
        { key: 'reference_type', label: 'Документ' },
        { key: 'comment', label: 'Комментарий' },
        {
            key: 'id', label: '', align: 'center',
            render: (_v: number, row: StockMovement) => {
                if (!DEFECT_TYPES.has(row.movement_type)) return null;
                const isDeleting = deletingId === row.id;
                return (
                    <button
                        className="btn btn-sm btn-danger"
                        onClick={() => handleDelete(row)}
                        disabled={isDeleting}
                        title="Удалить движение"
                        style={{ padding: '2px 8px', fontSize: 14, lineHeight: 1 }}
                    >
                        {isDeleting ? '...' : '×'}
                    </button>
                );
            },
        },
    ];

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    return (
        <TanStackDataTable
            columns={cols}
            data={movements}
            emptyText="Нет движений"
            emptyIcon="📋"
            exportName="movements"
        />
    );
}

/* ─── Tab: Приёмки ──────────────────────────────────────────────────────── */

type UnifiedDoc = {
    docType: 'receipt' | 'mark';
    id: number;
    number: string;
    status: string;
    is_defect: boolean;
    is_mark: boolean;
    positions: number;
    total_qty: number;
    actual_qty: number | null;
    planned_date: string | null;
    reason: string;
    created_at: string | null;
    receipt?: InboundReceipt;
    mark?: DefectMarkOperation;
};

function ReceiptsTab({ warehouseId, onCountChange, onTransfersChanged }: {
    warehouseId: number;
    onCountChange: (n: number) => void;
    onTransfersChanged?: () => void;
}) {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const [docs, setDocs] = useState<UnifiedDoc[]>([]);
    const [incomingDefects, setIncomingDefects] = useState<StockTransfer[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [accepting, setAccepting] = useState<number | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [r, transfers, marks] = await Promise.all([
                api.getReceipts(warehouseId),
                api.getTransfers(true),
                api.getDefectMarkOperations(warehouseId),
            ]);
            const receiptDocs: UnifiedDoc[] = r.map((x: InboundReceipt) => {
                const expected = x.items.reduce((s, it: any) => s + (it.expected_qty || 0), 0);
                const actual = x.items.reduce((s, it: any) => s + (it.actual_qty || 0), 0);
                return {
                    docType: 'receipt',
                    id: x.id,
                    number: x.number,
                    status: x.status,
                    is_defect: !!x.is_defect,
                    is_mark: false,
                    positions: x.items.length,
                    total_qty: expected,
                    actual_qty: x.status === 'ACCEPTED' ? actual : null,
                    planned_date: x.planned_date || null,
                    reason: (x.is_defect ? x.defect_reason : x.comment) || '—',
                    created_at: x.created_at || null,
                    receipt: x,
                };
            });
            const markDocs: UnifiedDoc[] = marks.map((m: DefectMarkOperation) => {
                const total = m.items.reduce((s, it) => s + it.quantity, 0);
                return {
                    docType: 'mark',
                    id: m.id,
                    number: m.number,
                    status: m.status,
                    is_defect: true,
                    is_mark: true,
                    positions: m.items.length,
                    total_qty: total,
                    actual_qty: m.status === 'ACCEPTED' ? total : null,
                    planned_date: null,
                    reason: m.reason || '—',
                    created_at: m.created_at || null,
                    mark: m,
                };
            });
            const unified = [...receiptDocs, ...markDocs].sort((a, b) => {
                const ta = a.created_at ? Date.parse(a.created_at) : 0;
                const tb = b.created_at ? Date.parse(b.created_at) : 0;
                return tb - ta;
            });
            setDocs(unified);
            const incoming = transfers.filter((t: StockTransfer) =>
                t.is_defect && t.to_warehouse_id === warehouseId && t.status === 'IN_TRANSIT'
            );
            setIncomingDefects(incoming);
            onCountChange(unified.length + incoming.length);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, [warehouseId, onCountChange]);

    const handleAcceptDefect = async (transferId: number) => {
        setAccepting(transferId);
        try {
            await api.completeTransfer(transferId);
            await load();
            onTransfersChanged?.();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setAccepting(null);
    };

    useEffect(() => { load(); }, [load]);

    const statusBadge = (s: string) => {
        const map: Record<string, { label: string; bg: string; color: string }> = {
            DRAFT: { label: 'Черновик', bg: 'rgba(0,0,0,0.06)', color: 'var(--color-text-muted)' },
            EXPECTED: { label: 'Ожидается', bg: 'rgba(245,158,11,0.1)', color: '#b45309' },
            ACCEPTED: { label: 'Принята', bg: 'rgba(34,197,94,0.1)', color: '#16a34a' },
            CANCELLED: { label: 'Отменена', bg: 'rgba(239,68,68,0.1)', color: '#dc2626' },
        };
        const { label, bg, color } = map[s] || { label: s, bg: 'transparent', color: 'inherit' };
        return <span style={{ color, background: bg, padding: '2px 8px', borderRadius: 12, fontSize: 12, fontWeight: 600 }}>{label}</span>;
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    const cols: Column[] = [
        {
            key: 'number', label: '№',
            render: (v: string, row: UnifiedDoc) => (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontWeight: 600 }}>{v}</span>
                    {row.is_mark && <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }}>Пометка брака</span>}
                    {row.is_defect && !row.is_mark && <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }}>Брак</span>}
                </span>
            ),
        },
        { key: 'status', label: 'Статус', render: (v: string) => statusBadge(v) },
        {
            key: 'positions', label: 'Позиции',
            render: (_: unknown, row: UnifiedDoc) => (
                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                    {row.positions} поз., {formatNumber(row.total_qty, 0)} {row.docType === 'mark' ? 'шт.' : 'ожид.'}
                    {row.docType === 'receipt' && row.actual_qty !== null && (
                        <span style={{ color: row.actual_qty < row.total_qty ? '#b45309' : 'var(--color-success)', fontWeight: 600 }}> / {formatNumber(row.actual_qty, 0)} факт</span>
                    )}
                </span>
            ),
        },
        {
            key: 'planned_date', label: 'Плановая дата',
            render: (_: unknown, row: UnifiedDoc) => row.planned_date ? formatDate(row.planned_date) : '—',
        },
        {
            key: 'reason', label: 'Комментарий / причина',
            render: (v: string) => v || '—',
        },
        { key: 'created_at', label: 'Создана', format: 'date' },
    ];

    return (
        <>
            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}

            {/* Incoming defect transfers */}
            {incomingDefects.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Входящие перемещения брака</div>
                    {incomingDefects.map(t => (
                        <div key={t.id} className="glass-card" style={{
                            padding: 16, marginBottom: 8,
                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                            border: '1px solid var(--color-warning)',
                        }}>
                            <div>
                                <div style={{ fontWeight: 600 }}>{t.number}</div>
                                <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                    {t.items?.length || 0} поз., {formatNumber(t.items?.reduce((s, i) => s + i.quantity, 0) || 0)} шт.
                                    {t.defect_reason ? ` — ${t.defect_reason}` : ''}
                                </div>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span className="badge badge-warning">Брак в пути</span>
                                <button
                                    className="btn btn-success btn-sm"
                                    onClick={() => handleAcceptDefect(t.id)}
                                    disabled={accepting === t.id}
                                >
                                    {accepting === t.id ? 'Принятие...' : 'Принять'}
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            <TanStackDataTable
                columns={cols}
                data={docs}
                emptyText="Нет документов"
                emptyIcon="📥"
                onRowClick={(row: UnifiedDoc) => {
                    if (row.docType === 'receipt') {
                        router.push(`/p/${slug}/warehouse/${warehouseId}/receipt/${row.id}`);
                    } else if (row.docType === 'mark') {
                        router.push(`/p/${slug}/warehouse/${warehouseId}/mark-operation/${row.id}`);
                    }
                }}
            />
        </>
    );
}

/* ─── Tab: Отгрузки ─────────────────────────────────────────────────────── */

function ShipmentsTab({ warehouseId, warehouseType, onCountChange }: {
    warehouseId: number;
    warehouseType: string;
    onCountChange: (n: number) => void;
}) {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const [shipments, setShipments] = useState<OutboundShipment[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const r = await api.getShipments(warehouseId);
            setShipments(r);
            onCountChange(r.length);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, [warehouseId, onCountChange]);

    useEffect(() => { load(); }, [load]);

    const statusBadge = (s: string) => {
        const map: Record<string, { label: string; bg: string; color: string }> = {
            DRAFT: { label: 'Черновик', bg: 'rgba(0,0,0,0.06)', color: 'var(--color-text-muted)' },
            SHIPPED: { label: 'Отгружена', bg: 'rgba(245,158,11,0.1)', color: '#b45309' },
            DELIVERED: { label: 'Доставлена', bg: 'rgba(34,197,94,0.1)', color: '#16a34a' },
            CANCELLED: { label: 'Отменена', bg: 'rgba(239,68,68,0.1)', color: '#dc2626' },
        };
        const { label, bg, color } = map[s] || { label: s, bg: 'transparent', color: 'inherit' };
        return <span style={{ color, background: bg, padding: '2px 8px', borderRadius: 12, fontSize: 12, fontWeight: 600 }}>{label}</span>;
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (warehouseType !== 'FULFILLMENT') {
        return <div className="glass-card" style={{ padding: 32, textAlign: 'center', opacity: 0.6 }}>Отгрузки доступны только для Фулфилмент</div>;
    }

    const cols: Column[] = [
        { key: 'number', label: '№' },
        { key: 'status', label: 'Статус', render: (v: string) => statusBadge(v) },
        {
            key: 'items', label: 'Позиции',
            render: (_: unknown, row: OutboundShipment) => {
                const qty = row.items.reduce((s: number, it: { quantity: number }) => s + it.quantity, 0);
                return <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>{row.items.length} поз., {formatNumber(qty)} шт.</span>;
            },
        },
        { key: 'destination', label: 'Назначение' },
        { key: 'shipped_date', label: 'Дата отгрузки', format: 'date' },
        { key: 'created_at', label: 'Создана', format: 'date' },
    ];

    return (
        <>
            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}

            <TanStackDataTable columns={cols} data={shipments} emptyText="Нет отгрузок" emptyIcon="📤" onRowClick={(row) => router.push(`/p/${slug}/warehouse/${warehouseId}/shipment/${row.id}`)} />
        </>
    );
}

/* ─── Tab: Заявки на отправку (история связей склада-источника) ──────────── */

function AssembliesTab({ warehouseId, slug, onCountChange }: {
    warehouseId: number;
    slug: string;
    onCountChange: (n: number) => void;
}) {
    const router = useRouter();
    const [rows, setRows] = useState<AssemblyRequest[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            // Все заявки склада-источника (вкл. архивные) — история связей склада с
            // отправками на WB и привязками к ФФ-заявкам. Эндпоинт уже обогащает ff-связью.
            const r = await api.getAssemblyRequests({ warehouse_id: warehouseId, view: 'all' });
            setRows(r.items);
            onCountChange(r.total);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, [warehouseId, onCountChange]);

    useEffect(() => { load(); }, [load]);

    const statusBadge = (s: string) => (
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)' }}>
            {FF_LINKED_STATUS_LABELS[s] || s}
        </span>
    );

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    const cols: Column[] = [
        { key: 'number', label: '№' },
        { key: 'status', label: 'Статус', render: (v: string) => statusBadge(v) },
        {
            key: 'effective_wb_warehouse', label: 'Склад WB',
            render: (_: unknown, row: AssemblyRequest) => row.effective_wb_warehouse || row.wb_warehouse_name || '—',
        },
        { key: 'wb_supply_name', label: 'Поставка WB', render: (v: string | undefined) => v || '—' },
        {
            key: 'ff_request_number', label: '№ ФФ-заявки',
            render: (v: string | null | undefined) => v || '—',
        },
        { key: 'actual_ready_date', label: 'Готова', format: 'date' },
        { key: 'shipped_at', label: 'Отгружена', format: 'date' },
        { key: 'created_at', label: 'Создана', format: 'date' },
    ];

    return (
        <>
            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}
            <TanStackDataTable
                columns={cols}
                data={rows}
                emptyText="Нет заявок на отправку с этого склада"
                emptyIcon="🚚"
                exportName="warehouse_assemblies"
                onRowClick={(row) => router.push(`/p/${slug}/warehouse/assembly/${row.id}`)}
            />
        </>
    );
}

/* ─── Tab: Перемещения ──────────────────────────────────────────────────── */

function TransfersTab({ warehouseId, onCountChange }: {
    warehouseId: number;
    onCountChange: (n: number) => void;
}) {
    const [transfers, setTransfers] = useState<StockTransfer[]>([]);
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actingId, setActingId] = useState<number | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [mine, whs] = await Promise.all([
                api.getTransfers(false, warehouseId),
                api.getWarehouses(),
            ]);
            setTransfers(mine);
            setWarehouses(whs);
            onCountChange(countActionableTransfers(mine, warehouseId));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, [warehouseId, onCountChange]);

    useEffect(() => { load(); }, [load]);

    const whName = (id: number) => warehouses.find(w => w.id === id)?.name || `#${id}`;

    const handleSend = async (t: StockTransfer) => {
        if (!confirm(`Отправить ${t.number}? Товар спишется со склада-источника.`)) return;
        setActingId(t.id);
        try {
            await api.sendTransfer(t.id);
            await load();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка отправки');
        } finally {
            setActingId(null);
        }
    };

    const handleAccept = async (t: StockTransfer) => {
        if (!confirm(`Принять ${t.number}? Товар зачислится на этот склад.`)) return;
        setActingId(t.id);
        try {
            await api.completeTransfer(t.id);
            await load();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка приёмки');
        } finally {
            setActingId(null);
        }
    };

    const handleCancel = async (t: StockTransfer) => {
        if (!confirm(`Удалить черновик ${t.number}?`)) return;
        setActingId(t.id);
        try {
            await api.cancelTransfer(t.id);
            await load();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка удаления');
        } finally {
            setActingId(null);
        }
    };

    const statusBadge = (s: string) => {
        const styleMap: Record<string, { bg: string; color: string }> = {
            DRAFT: { bg: 'rgba(0,0,0,0.06)', color: 'var(--color-text-muted)' },
            IN_TRANSIT: { bg: 'rgba(245,158,11,0.1)', color: '#b45309' },
            COMPLETED: { bg: 'rgba(34,197,94,0.1)', color: '#16a34a' },
        };
        const label = TRANSFER_STATUS_LABELS[s] || s;
        const { bg, color } = styleMap[s] || { bg: 'transparent', color: 'inherit' };
        return <span style={{ color, background: bg, padding: '2px 8px', borderRadius: 12, fontSize: 12, fontWeight: 600 }}>{label}</span>;
    };

    const directionText = (row: StockTransfer) => row.from_warehouse_id === warehouseId
        ? `Исходящее → ${whName(row.to_warehouse_id)}`
        : `Входящее ← ${whName(row.from_warehouse_id)}`;
    const itemsText = (row: StockTransfer) => {
        const qty = row.items.reduce((s: number, it: { quantity: number }) => s + it.quantity, 0);
        return `${row.items.length} поз., ${formatNumber(qty)} шт.`;
    };

    const cols: Column[] = [
        { key: 'number', label: '№' },
        {
            key: 'from_warehouse_id', label: 'Направление',
            render: (_: unknown, row: StockTransfer) => <span>{directionText(row)}</span>,
            exportValue: (row: StockTransfer) => directionText(row),
        },
        {
            key: 'status', label: 'Статус',
            render: (v: string) => statusBadge(v),
            exportValue: (row: StockTransfer) => TRANSFER_STATUS_LABELS[row.status] || row.status,
        },
        {
            key: 'is_defect', label: 'Тип',
            render: (v: boolean, row: StockTransfer) => v
                ? <span className="badge badge-danger" title={row.defect_reason || ''}>Брак</span>
                : <span style={{ color: 'var(--color-text-muted)' }}>Годный</span>,
            exportValue: (row: StockTransfer) => row.is_defect ? 'Брак' : 'Годный',
        },
        {
            key: 'items', label: 'Позиции',
            render: (_: unknown, row: StockTransfer) => (
                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>{itemsText(row)}</span>
            ),
            exportValue: (row: StockTransfer) => itemsText(row),
        },
        { key: 'comment', label: 'Комментарий' },
        { key: 'created_at', label: 'Создано', format: 'date' },
        {
            key: 'id', label: '', align: 'center',
            exportValue: () => '',
            render: (_v: number, row: StockTransfer) => {
                const acting = actingId === row.id;
                if (row.status === 'DRAFT' && row.from_warehouse_id === warehouseId) {
                    return (
                        <span style={{ display: 'inline-flex', gap: 6 }}>
                            <button className="btn btn-sm btn-primary" onClick={() => handleSend(row)} disabled={acting}>
                                {acting ? '...' : 'Отправить'}
                            </button>
                            <button className="btn btn-sm btn-danger" onClick={() => handleCancel(row)} disabled={acting} title="Удалить черновик">
                                ×
                            </button>
                        </span>
                    );
                }
                if (row.status === 'IN_TRANSIT' && row.to_warehouse_id === warehouseId) {
                    return (
                        <button className="btn btn-sm btn-success" onClick={() => handleAccept(row)} disabled={acting}>
                            {acting ? '...' : 'Принять'}
                        </button>
                    );
                }
                return null;
            },
        },
    ];

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    return (
        <>
            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}

            <TanStackDataTable
                columns={cols}
                data={transfers}
                emptyText="Нет перемещений"
                emptyIcon="🔄"
                exportName="transfers"
            />
        </>
    );
}

/* ─── Tab: Остатки и статистика ─────────────────────────────────────────── */

function StockTab({ warehouseId }: { warehouseId: number }) {
    const [stock, setStock] = useState<WarehouseStockRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const [showAdj, setShowAdj] = useState(false);
    const [adjBarcode, setAdjBarcode] = useState('');
    const [adjDelta, setAdjDelta] = useState('');
    const [adjReason, setAdjReason] = useState('');
    const [adjSaving, setAdjSaving] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try { const r = await api.getWarehouseStock(warehouseId); setStock(r); }
        catch (e: unknown) { setError(e instanceof Error ? e.message : 'Ошибка'); }
        setLoading(false);
    }, [warehouseId]);

    useEffect(() => { load(); }, [load]);

    const handleAdjustment = async () => {
        if (!adjBarcode.trim() || !adjDelta || !adjReason.trim()) return;
        setAdjSaving(true); setError('');
        try {
            await api.createAdjustment(warehouseId, { barcode: adjBarcode.trim(), delta: parseInt(adjDelta), reason: adjReason.trim() });
            setShowAdj(false); setAdjBarcode(''); setAdjDelta(''); setAdjReason(''); await load();
        } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Ошибка'); }
        setAdjSaving(false);
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    const totalQty = stock.reduce((s, r) => s + r.quantity, 0);
    const totalDefect = stock.reduce((s, r) => s + (r.defect_quantity || 0), 0);
    const totalCost = stock.reduce((s, r) => s + (r.cost_price || 0) * r.quantity, 0);
    const totalReserved = stock.reduce((s, r) => s + (r.reserved || 0), 0);
    const totalAvailable = stock.reduce((s, r) => s + (r.available || 0), 0);

    const cols: Column[] = [
        { key: 'barcode', label: 'ШК' },
        { key: 'quantity', label: 'Кол-во', align: 'right', format: 'number' },
        {
            key: 'defect_quantity', label: 'Брак', align: 'right',
            render: (v: number) => (
                <span style={{ color: v > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)', fontWeight: v > 0 ? 600 : 400 }}>
                    {formatNumber(v, 0)}
                </span>
            ),
        },
        { key: 'reserved', label: 'Зарезерв.', align: 'right', format: 'number' },
        { key: 'available', label: 'Доступно', align: 'right', format: 'number' },
        { key: 'in_transit', label: 'В пути', align: 'right', format: 'number' },
        { key: 'cost_price', label: 'Себестоимость', align: 'right', render: (v: number | null) => v ? formatNumber(v) + ' \u20BD' : '—' },
        { key: 'updated_at', label: 'Обновлено', format: 'date' },
    ];

    return (
        <>
            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 12 }}>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Позиций</div>
                    <div style={{ fontSize: 24, fontWeight: 700 }}>{stock.length}</div>
                </div>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Всего штук</div>
                    <div style={{ fontSize: 24, fontWeight: 700 }}>{formatNumber(totalQty)}</div>
                </div>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Зарезервировано</div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: totalReserved > 0 ? 'var(--color-warning)' : undefined }}>{formatNumber(totalReserved)}</div>
                </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 20 }}>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Доступно</div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-success)' }}>{formatNumber(totalAvailable)}</div>
                </div>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Брак</div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: totalDefect > 0 ? 'var(--color-warning)' : undefined }}>{formatNumber(totalDefect)}</div>
                </div>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Себестоимость</div>
                    <div style={{ fontSize: 24, fontWeight: 700 }}>{formatNumber(totalCost)} {'\u20BD'}</div>
                </div>
            </div>

            <div style={{ marginBottom: 12 }}>
                <button className="btn btn-secondary btn-sm" onClick={() => setShowAdj(true)}>Корректировка</button>
            </div>

            <TanStackDataTable columns={cols} data={stock} emptyText="Нет остатков" emptyIcon="📦" exportName="warehouse_stock" />

            {showAdj && (
                <div className="modal-overlay" onClick={() => setShowAdj(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 400 }}>
                        <h2 className="modal-title">Корректировка остатков</h2>
                        <div className="form-group">
                            <label className="form-label">Баркод *</label>
                            <input className="form-input" value={adjBarcode} onChange={e => setAdjBarcode(e.target.value)} autoFocus />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Дельта (+ излишек, - недостача) *</label>
                            <input className="form-input" type="number" value={adjDelta} onChange={e => setAdjDelta(e.target.value)} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Причина *</label>
                            <textarea className="form-input" value={adjReason} onChange={e => setAdjReason(e.target.value)} rows={2} />
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={() => setShowAdj(false)}>Отмена</button>
                            <button className="btn btn-primary" onClick={handleAdjustment} disabled={adjSaving}>
                                {adjSaving ? 'Сохранение...' : 'Применить'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
}

/* ─── Tab: Брак (Defects) ──────────────────────────────────────────────── */

function DefectsTab({ warehouseId, onCountChange }: {
    warehouseId: number;
    onCountChange: (n: number) => void;
}) {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;

    const [stock, setStock] = useState<Record<string, unknown>[]>([]);
    const [outgoingTransfers, setOutgoingTransfers] = useState<StockTransfer[]>([]);
    const [defectShipments, setDefectShipments] = useState<OutboundShipment[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [defects, nomData, transfers, shipments] = await Promise.all([
                api.getDefectStock(warehouseId),
                api.getNomenclature(),
                api.getTransfers(true),
                api.getDefectShipments(warehouseId),
            ]);
            const nomByBarcode = new Map(nomData.map(n => [n.barcode, n]));
            const enriched = defects.map((r: WarehouseStockRow) => {
                const n = nomByBarcode.get(r.barcode);
                return { ...r, article_seller: n?.article_seller || '', subject: n?.subject || '' };
            });
            setStock(enriched);
            const outgoing = transfers.filter((t: StockTransfer) =>
                t.is_defect && t.from_warehouse_id === warehouseId && t.status === 'IN_TRANSIT'
            );
            setOutgoingTransfers(outgoing);
            setDefectShipments(shipments);
            onCountChange(enriched.length);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, [warehouseId, onCountChange]);

    useEffect(() => { load(); }, [load]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    const totalDefectItems = stock.length;
    const totalDefectQty = stock.reduce((s, r) => s + (Number(r.defect_quantity) || 0), 0);
    const defectUrl = (action: string) => `/p/${slug}/warehouse/${warehouseId}/defect/${action}`;

    const docStatusBadge = (s: string) => {
        const map: Record<string, { label: string; bg: string; color: string }> = {
            ACCEPTED: { label: 'Принята', bg: 'rgba(34,197,94,0.1)', color: '#16a34a' },
            CANCELLED: { label: 'Отменена', bg: 'rgba(239,68,68,0.1)', color: '#dc2626' },
            SHIPPED: { label: 'Списана', bg: 'rgba(34,197,94,0.1)', color: '#16a34a' },
            DELIVERED: { label: 'Списана', bg: 'rgba(34,197,94,0.1)', color: '#16a34a' },
            DRAFT: { label: 'Черновик', bg: 'rgba(0,0,0,0.06)', color: 'var(--color-text-muted)' },
            EXPECTED: { label: 'Ожидается', bg: 'rgba(245,158,11,0.1)', color: '#b45309' },
        };
        const { label, bg, color } = map[s] || { label: s, bg: 'transparent', color: 'inherit' };
        return <span style={{ color, background: bg, padding: '2px 8px', borderRadius: 12, fontSize: 12, fontWeight: 600 }}>{label}</span>;
    };


    const cols: Column[] = [
        { key: 'article_seller', label: 'Артикул' },
        { key: 'barcode', label: 'ШК' },
        {
            key: 'defect_quantity', label: 'Кол-во брака', align: 'right',
            render: (v: number) => (
                <span style={{ color: v > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)', fontWeight: 600 }}>
                    {formatNumber(v, 0)}
                </span>
            ),
        },
        {
            key: 'defect_in_transit', label: 'Брак в пути', align: 'right',
            render: (v: number) => (
                <span style={{ color: v > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)', fontWeight: v > 0 ? 600 : 400 }}>
                    {formatNumber(v, 0)}
                </span>
            ),
        },
        { key: 'updated_at', label: 'Обновлено', format: 'date' },
    ];

    return (
        <>
            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12, marginBottom: 20 }}>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Позиций с браком</div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: totalDefectItems > 0 ? 'var(--color-warning)' : undefined }}>{totalDefectItems}</div>
                </div>
                <div className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Всего бракованных штук</div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: totalDefectQty > 0 ? 'var(--color-warning)' : undefined }}>{formatNumber(totalDefectQty)}</div>
                </div>
            </div>

            <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
                <button className="btn btn-secondary btn-sm" onClick={() => router.push(defectUrl('mark'))}>Отметить брак</button>
                <button className="btn btn-secondary btn-sm" onClick={() => router.push(defectUrl('receive'))}>Принять брак</button>
                <button className="btn btn-secondary btn-sm" onClick={() => router.push(defectUrl('writeoff'))}>Списать</button>
                <button className="btn btn-secondary btn-sm" onClick={() => router.push(defectUrl('recover'))}>Восстановить</button>
            </div>

            {/* Defect writeoff shipments (списания брака — документы) */}
            {defectShipments.length > 0 && (
                <div style={{ marginBottom: 20 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
                        Списания брака <span style={{ color: 'var(--color-text-muted)', fontWeight: 400 }}>({defectShipments.length})</span>
                    </div>
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                        <table className="data-table" style={{ marginBottom: 0 }}>
                            <thead>
                                <tr>
                                    <th>№</th>
                                    <th>Статус</th>
                                    <th>Позиции</th>
                                    <th>Причина</th>
                                    <th style={{ textAlign: 'right' }}>Создана</th>
                                </tr>
                            </thead>
                            <tbody>
                                {defectShipments.map(s => {
                                    const qty = (s.items || []).reduce((a, i) => a + (i.quantity || 0), 0);
                                    return (
                                        <tr
                                            key={s.id}
                                            onClick={() => router.push(`/p/${slug}/warehouse/${warehouseId}/shipment/${s.id}`)}
                                            style={{ cursor: 'pointer' }}
                                        >
                                            <td style={{ fontWeight: 600 }}>{s.number}</td>
                                            <td>{docStatusBadge(s.status)}</td>
                                            <td>{(s.items || []).length} поз., {formatNumber(qty)} шт.</td>
                                            <td style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>{s.defect_reason || '—'}</td>
                                            <td style={{ textAlign: 'right', color: 'var(--color-text-muted)', fontSize: 13 }}>
                                                {formatDate(s.shipped_date || s.created_at)}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {/* Outgoing defect transfers (sent from this warehouse) */}
            {outgoingTransfers.length > 0 && (
                <div style={{ marginBottom: 20 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Исходящие перемещения брака</div>
                    {outgoingTransfers.map(t => (
                        <div key={t.id} className="glass-card" style={{
                            padding: 16, marginBottom: 8,
                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                        }}>
                            <div>
                                <div style={{ fontWeight: 600 }}>{t.number}</div>
                                <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                    {t.items?.length || 0} поз., {formatNumber(t.items?.reduce((s, i) => s + i.quantity, 0) || 0)} шт.
                                    {t.defect_reason ? ` — ${t.defect_reason}` : ''}
                                </div>
                            </div>
                            <span className="badge badge-warning">Ожидает приёмки</span>
                        </div>
                    ))}
                </div>
            )}

            <TanStackDataTable columns={cols} data={stock} emptyText="Нет бракованных товаров" emptyIcon="📋" exportName="defect_stock" />
        </>
    );
}

/* ─── Tab: Время доставки ──────────────────────────────────────────────── */

function DeliveryTab({ warehouseId }: { warehouseId: number }) {
    const [data, setData] = useState<DeliveryTimesResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');

    // Editable state
    const [assemblyDays, setAssemblyDays] = useState(0);
    const [wbAcceptanceDays, setWbAcceptanceDays] = useState(2);
    const [deliveryMap, setDeliveryMap] = useState<Record<string, number>>({});

    // Inline editing for assembly_days / wb_acceptance_days
    const [editingAssembly, setEditingAssembly] = useState(false);
    const [editingAcceptance, setEditingAcceptance] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const r = await api.getDeliveryTimes(warehouseId);
            setData(r);
            setAssemblyDays(r.assembly_days);
            setWbAcceptanceDays(r.wb_acceptance_days);
            const map: Record<string, number> = {};
            r.wb_warehouses.forEach(w => { map[w.wb_warehouse_name] = w.delivery_days; });
            setDeliveryMap(map);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, [warehouseId]);

    useEffect(() => { load(); }, [load]);

    const handleSave = async () => {
        setSaving(true);
        setError('');
        setSuccess('');
        try {
            const items = Object.entries(deliveryMap).map(([name, days]) => ({
                wb_warehouse_name: name,
                delivery_days: days,
            }));
            const r = await api.updateDeliveryTimes(warehouseId, {
                assembly_days: assemblyDays,
                wb_acceptance_days: wbAcceptanceDays,
                items,
            });
            setData(r);
            setSuccess('Сохранено');
            setTimeout(() => setSuccess(''), 3000);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setSaving(false);
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    if (!data || data.wb_warehouses.length === 0) {
        return (
            <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>
                <div style={{ fontSize: 40, marginBottom: 12 }}>📦</div>
                <div style={{ fontSize: 15, color: 'var(--color-text-muted)' }}>
                    Сначала синхронизируйте остатки WB, чтобы увидеть список складов
                </div>
            </div>
        );
    }

    const totalDays = (wbName: string) => assemblyDays + (deliveryMap[wbName] ?? 3) + wbAcceptanceDays;

    return (
        <div className="glass-card" style={{ padding: 24 }}>
            <p style={{ fontSize: 14, color: 'var(--color-text-muted)', marginBottom: 20 }}>
                Укажите сколько дней занимает доставка с этого склада до каждого склада WB
                (без учёта сборки)
            </p>

            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}
            {success && <div style={{ color: 'var(--color-success)', marginBottom: 12 }}>{success}</div>}

            {/* Editable cards */}
            <div style={{ display: 'flex', gap: 12, marginBottom: 24 }}>
                <div
                    style={{
                        padding: '10px 16px',
                        border: '1px solid var(--color-border)',
                        borderRadius: 10,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        cursor: 'pointer',
                    }}
                    onClick={() => setEditingAssembly(true)}
                >
                    <span style={{ fontSize: 14 }}>Время сборки:</span>
                    {editingAssembly ? (
                        <input
                            type="number"
                            min={0}
                            value={assemblyDays}
                            onChange={e => setAssemblyDays(Math.max(0, parseInt(e.target.value) || 0))}
                            onBlur={() => setEditingAssembly(false)}
                            onKeyDown={e => e.key === 'Enter' && setEditingAssembly(false)}
                            autoFocus
                            style={{ width: 50, padding: '2px 6px', fontSize: 14, border: '1px solid var(--color-primary)', borderRadius: 6, textAlign: 'center' }}
                        />
                    ) : (
                        <span style={{ fontWeight: 600 }}>{assemblyDays} дн.</span>
                    )}
                    {!editingAssembly && <span style={{ fontSize: 14, opacity: 0.5 }}>✏️</span>}
                </div>

                <div
                    style={{
                        padding: '10px 16px',
                        border: '1px solid var(--color-border)',
                        borderRadius: 10,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        cursor: 'pointer',
                    }}
                    onClick={() => setEditingAcceptance(true)}
                >
                    <span style={{ fontSize: 14 }}>Приёмка WB:</span>
                    {editingAcceptance ? (
                        <input
                            type="number"
                            min={0}
                            value={wbAcceptanceDays}
                            onChange={e => setWbAcceptanceDays(Math.max(0, parseInt(e.target.value) || 0))}
                            onBlur={() => setEditingAcceptance(false)}
                            onKeyDown={e => e.key === 'Enter' && setEditingAcceptance(false)}
                            autoFocus
                            style={{ width: 50, padding: '2px 6px', fontSize: 14, border: '1px solid var(--color-primary)', borderRadius: 6, textAlign: 'center' }}
                        />
                    ) : (
                        <span style={{ fontWeight: 600 }}>{wbAcceptanceDays} дн.</span>
                    )}
                    {!editingAcceptance && <span style={{ fontSize: 14, opacity: 0.5 }}>✏️</span>}
                </div>
            </div>

            {/* Table */}
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                <thead>
                    <tr style={{ borderBottom: '2px solid var(--color-border)' }}>
                        <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, textTransform: 'uppercase', fontSize: 11, color: 'var(--color-text-muted)', letterSpacing: '0.5px' }}>Склад WB</th>
                        <th style={{ textAlign: 'center', padding: '8px 12px', fontWeight: 600, textTransform: 'uppercase', fontSize: 11, color: 'var(--color-text-muted)', letterSpacing: '0.5px' }}>Дней доставки</th>
                        <th style={{ textAlign: 'center', padding: '8px 12px', fontWeight: 600, textTransform: 'uppercase', fontSize: 11, color: 'var(--color-text-muted)', letterSpacing: '0.5px' }}>Итого до WB</th>
                    </tr>
                </thead>
                <tbody>
                    {data.wb_warehouses.map((wh, idx) => {
                        const days = deliveryMap[wh.wb_warehouse_name] ?? 3;
                        const total = totalDays(wh.wb_warehouse_name);
                        const isFirst = idx === 0;
                        return (
                            <tr key={wh.wb_warehouse_name} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                <td style={{ padding: '10px 12px' }}>{wh.wb_warehouse_name}</td>
                                <td style={{ padding: '10px 12px', textAlign: 'center' }}>
                                    <input
                                        type="number"
                                        min={0}
                                        value={days}
                                        onChange={e => {
                                            const v = Math.max(0, parseInt(e.target.value) || 0);
                                            setDeliveryMap(prev => ({ ...prev, [wh.wb_warehouse_name]: v }));
                                        }}
                                        style={{
                                            width: 60,
                                            padding: '4px 8px',
                                            fontSize: 14,
                                            border: '1px solid var(--color-border)',
                                            borderRadius: 6,
                                            textAlign: 'center',
                                        }}
                                    />
                                </td>
                                <td style={{ padding: '10px 12px', textAlign: 'center', fontWeight: 500 }}>
                                    {total} дн{isFirst ? ` (${assemblyDays}+${days}+${wbAcceptanceDays})` : ''}
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>

            {/* Save button */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 12, marginTop: 20 }}>
                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>По умолчанию: 3 дня, если не задано</span>
                <button
                    className="btn btn-primary"
                    onClick={handleSave}
                    disabled={saving}
                >
                    {saving ? 'Сохранение...' : 'Сохранить'}
                </button>
            </div>
        </div>
    );
}

/* ─── Раздел «Фулфилмент»: одна вкладка с вложенными под-вкладками ───────── */

function FulfillmentTabs({
    warehouseId, slug, sub, onSubChange, provider, externalTick = 0,
}: {
    warehouseId: number;
    slug: string;
    sub: FfSubTab;
    onSubChange: (s: FfSubTab) => void;
    provider: string | null;
    /** Внешний сигнал перезагрузки (связка из карточки машины). */
    externalTick?: number;
}) {
    // Вкладка «Сопоставление» (короба) — только для migfull; на других провайдерах прячем.
    const tabs = FF_SUB_TABS.filter(t => !t.migfullOnly || provider === 'migfull');
    const activeSub = tabs.some(t => t.key === sub) ? sub : 'stocks';
    return (
        <>
            <div style={{ display: 'flex', gap: 4, marginBottom: 16, flexWrap: 'wrap' }}>
                {tabs.map(t => (
                    <button
                        key={t.key}
                        onClick={() => onSubChange(t.key)}
                        className={`btn btn-sm ${activeSub === t.key ? 'btn-primary' : 'btn-secondary'}`}
                    >
                        {t.label}
                    </button>
                ))}
            </div>
            {activeSub === 'stocks' && <FfStocksTab warehouseId={warehouseId} provider={provider} />}
            {activeSub === 'boxes' && <FfBoxPacksTab warehouseId={warehouseId} />}
            {activeSub === 'assembly' && <FfRequestsTab warehouseId={warehouseId} slug={slug} kind="assembly" externalTick={externalTick} />}
            {activeSub === 'inbound' && <FfRequestsTab warehouseId={warehouseId} slug={slug} kind="inbound" externalTick={externalTick} />}
            {activeSub === 'return' && <FfRequestsTab warehouseId={warehouseId} slug={slug} kind="return" externalTick={externalTick} />}
            {activeSub === 'history' && <FfHistoryTab warehouseId={warehouseId} slug={slug} />}
            {activeSub === 'sync' && <FfSyncTab warehouseId={warehouseId} />}
        </>
    );
}

/* ─── Tab: ФФ остатки ───────────────────────────────────────────────────── */

type FfStockQuickFilter = 'diff' | 'unmatched' | null;

function FfStocksTab({ warehouseId, provider }: { warehouseId: number; provider: string | null }) {
    const [data, setData] = useState<FfStocksResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [subjectFilter, setSubjectFilter] = useState('');
    const [brandFilter, setBrandFilter] = useState('');
    const [search, setSearch] = useState('');
    const [quickFilter, setQuickFilter] = useState<FfStockQuickFilter>(null);

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        api.getFulfillmentStocks(warehouseId)
            .then(r => { if (!controller.signal.aborted) setData(r); })
            .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [warehouseId]);

    const rows = useMemo(() => {
        let result = data?.rows ?? [];
        if (subjectFilter) result = result.filter(r => r.subject === subjectFilter);
        if (brandFilter) result = result.filter(r => r.brand === brandFilter);
        const q = search.trim().toLowerCase();
        if (q) {
            result = result.filter(r =>
                r.barcode.toLowerCase().includes(q)
                || (r.article_seller ?? '').toLowerCase().includes(q)
                || (r.vendor_code ?? '').toLowerCase().includes(q)
            );
        }
        if (quickFilter === 'diff') result = result.filter(r => r.diff !== 0);
        if (quickFilter === 'unmatched') result = result.filter(r => r.nomenclature_id === null);
        return result;
    }, [data, subjectFilter, brandFilter, search, quickFilter]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;

    const totals = data?.totals;
    const hasFilters = Boolean(subjectFilter || brandFilter || search.trim() || quickFilter);
    const isMigfull = provider === 'migfull';  // только у «Натали» резерв раскладывается на части
    // FBS-вычет из ff_good: провайдеры остаток под FBS не снимают, снимаем мы.
    // Колонка и KPI появляются, только когда вычет вообще есть.
    const hasFbs = (totals?.ff_fbs ?? 0) > 0;

    const diffCell = (v: number) => {
        if (!v) return <span style={{ color: 'var(--color-text-muted)' }}>0</span>;
        return (
            <span style={{ color: v > 0 ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                {v > 0 ? '+' : ''}{formatNumber(v, 0)}
            </span>
        );
    };

    const cols: Column[] = [
        { key: 'barcode', label: 'ШК' },
        {
            key: 'article_seller', label: 'Наш артикул',
            render: (_: unknown, row: FfStockRow) => (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span>{row.article_seller ?? row.vendor_code ?? '—'}</span>
                    {row.nomenclature_id === null && (
                        <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }}>нет в номенклатуре</span>
                    )}
                </span>
            ),
            exportValue: (row: FfStockRow) => row.article_seller ?? row.vendor_code ?? '',
        },
        { key: 'subject', label: 'Предмет', render: (v: string | null) => v || '—' },
        { key: 'brand', label: 'Бренд', render: (v: string | null) => v || '—' },
        {
            key: 'ff_good', label: 'ФФ годный', align: 'right',
            render: (v: number, row: FfStockRow) => (
                <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                    <span>{formatNumber(v, 0)}</span>
                    {row.ff_box_units > 0 && (
                        <span
                            style={{ fontSize: 11, color: 'var(--color-text-muted)' }}
                            title={`${formatNumber(row.ff_box_count, 0)} коробов сведено в россыпь`}
                        >
                            в коробах: {formatNumber(row.ff_box_units, 0)}
                        </span>
                    )}
                </span>
            ),
            exportValue: (row: FfStockRow) => row.ff_good,
        },
        // «ФФ годный» приходит уже ЗА ВЫЧЕТОМ этой колонки — она расшифровка,
        // симметрично «в коробах», и живёт только пока у склада есть вычет.
        ...(hasFbs ? ([{
            key: 'ff_fbs', label: 'Отгружено FBS', align: 'right', headerWrap: true,
            headerTitle: 'Списано у нас по FBS-продажам; провайдер выбытие ещё не отразил '
                + '— вычтено из остатка ФФ',
            render: (v: number) => (
                <span style={{ color: v > 0 ? 'var(--color-accent)' : 'var(--color-text-muted)' }}>
                    {formatNumber(v, 0)}
                </span>
            ),
        }] as Column[]) : []),
        { key: 'ff_reserve', label: 'ФФ резерв', align: 'right', format: 'number' },
        // migfull: резерв (stock_locked) = «Собрано» (под активные отгрузки) + «В приёмке»
        // (свежий приход, залоченный при оприходовании) + «Брак» (остаток). Без этого
        // разбиения заявки-в-работе и приход ложно падали бы в брак.
        ...(isMigfull ? ([
            { key: 'ff_reserve_ready', label: 'Собрано', align: 'right', format: 'number' },
            { key: 'ff_inbound_locked', label: 'В приёмке', align: 'right', format: 'number' },
        ] as Column[]) : []),
        {
            // У migfull поля брака в API НЕТ, и эта цифра — остаток от вычитания
            // (заблокировано − собрано − в приёмке), а не измерение. Живая сверка
            // кабинета натали 27.07.2026: половина заблокированного не объясняется
            // ничем из того, что отдаёт их API, а на разобранном SKU это оказался
            // товар НА СБОРКЕ. Называть такое браком — врать, поэтому у migfull
            // колонка честно зовётся «Прочая блокировка».
            key: 'ff_defect', label: isMigfull ? 'Блокировка проч.' : 'ФФ брак',
            align: 'right', headerWrap: true,
            headerTitle: isMigfull
                ? 'Заблокировано у ФФ БЕЗ объяснения: резерв минус собранное под активные '
                  + 'отгрузки минус приёмки. Это НЕ брак — поля брака у Натали в API нет. '
                  + 'Сюда попадает в том числе товар на сборке по недавно закрытым отгрузкам. '
                  + 'Реальный брак по такому складу — только в колонке «У нас брак».'
                : 'Брак по данным провайдера (отдельное поле API)',
            render: (v: number) => (
                <span style={{ color: v > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)', fontWeight: v > 0 ? 600 : 400 }}>
                    {formatNumber(v, 0)}
                </span>
            ),
        },
        { key: 'our_quantity', label: 'У нас', align: 'right', format: 'number' },
        {
            key: 'our_defect', label: 'У нас брак', align: 'right',
            render: (v: number) => (
                <span style={{ color: v > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)', fontWeight: v > 0 ? 600 : 400 }}>
                    {formatNumber(v, 0)}
                </span>
            ),
        },
        { key: 'diff', label: 'Расхождение', align: 'right', render: (v: number) => diffCell(v) },
    ];

    const summary: { label: string; value: number; color?: string; filter?: FfStockQuickFilter }[] = totals ? [
        { label: 'Годный ФФ', value: totals.ff_good },
        { label: 'Резерв', value: totals.ff_reserve },
        ...(isMigfull ? [
            { label: 'Собрано', value: totals.ff_reserve_ready, color: 'var(--color-accent)' },
            ...(totals.ff_inbound_locked > 0 ? [{ label: 'В приёмке', value: totals.ff_inbound_locked, color: 'var(--color-accent)' }] : []),
        ] : []),
        {
            label: isMigfull ? 'Блокировка проч.' : 'Брак ФФ',
            value: totals.ff_defect,
            color: totals.ff_defect > 0 ? 'var(--color-warning)' : undefined,
        },
        ...(totals.ff_box_units > 0 ? [{ label: 'В коробах', value: totals.ff_box_units, color: 'var(--color-accent)' }] : []),
        ...(totals.ff_fbs > 0 ? [{ label: 'Отгружено FBS', value: totals.ff_fbs, color: 'var(--color-accent)' }] : []),
        { label: 'У нас', value: totals.our_quantity },
        { label: 'Расхождение', value: totals.diff, color: totals.diff > 0 ? 'var(--color-success)' : totals.diff < 0 ? 'var(--color-danger)' : undefined, filter: 'diff' },
        { label: 'Несматчено', value: totals.unmatched, color: totals.unmatched > 0 ? 'var(--color-warning)' : undefined, filter: 'unmatched' },
    ] : [];

    return (
        <>
            {totals && (
                /* auto-fit, а не repeat(N, 1fr): карточек бывает до десятка
                   (Собрано/В приёмке/В коробах/Отгружено FBS — условные), и
                   жёсткая сетка ужимала бы их в нечитаемые столбики. */
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 16 }}>
                    {summary.map(s => {
                        const filter = s.filter ?? null;
                        const active = filter !== null && quickFilter === filter;
                        return (
                            <div
                                key={s.label}
                                className="glass-card"
                                onClick={filter ? () => setQuickFilter(prev => prev === filter ? null : filter) : undefined}
                                title={filter ? (active ? 'Сбросить фильтр' : 'Показать товары с этим признаком') : undefined}
                                style={{
                                    padding: 16,
                                    textAlign: 'center',
                                    cursor: filter ? 'pointer' : undefined,
                                    border: active ? '1px solid var(--color-accent)' : '1px solid transparent',
                                }}
                            >
                                <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>{s.label}</div>
                                <div style={{ fontSize: 24, fontWeight: 700, color: s.color }}>
                                    {s.label === 'Расхождение' && s.value > 0 ? '+' : ''}{formatNumber(s.value, 0)}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}

            <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                <select className="form-input" style={{ maxWidth: 200, fontSize: 13 }} value={subjectFilter} onChange={e => setSubjectFilter(e.target.value)}>
                    <option value="">Предмет: Все</option>
                    {(data?.subjects ?? []).map(s => <option key={s} value={s}>{s}</option>)}
                </select>
                <select className="form-input" style={{ maxWidth: 200, fontSize: 13 }} value={brandFilter} onChange={e => setBrandFilter(e.target.value)}>
                    <option value="">Бренд: Все</option>
                    {(data?.brands ?? []).map(b => <option key={b} value={b}>{b}</option>)}
                </select>
                <input
                    className="form-input"
                    style={{ maxWidth: 240, fontSize: 13 }}
                    placeholder="🔍 Баркод / артикул"
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                />
                {quickFilter && (
                    <span className="badge badge-info" style={{ fontSize: 12 }}>
                        {quickFilter === 'diff' ? 'Только с расхождением' : 'Только несматченные'}
                    </span>
                )}
                {hasFilters && (
                    <button
                        className="btn btn-sm btn-secondary"
                        onClick={() => { setSubjectFilter(''); setBrandFilter(''); setSearch(''); setQuickFilter(null); }}
                    >
                        Сбросить
                    </button>
                )}
                <span style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--color-text-muted)' }}>
                    {hasFilters ? `Показано: ${formatNumber(rows.length, 0)} из ${formatNumber(data?.rows.length ?? 0, 0)} · ` : ''}
                    Синхронизировано: {data?.synced_at ? formatDateTime(data.synced_at) : 'ещё не выполнялась'}
                </span>
            </div>

            <TanStackDataTable
                columns={cols}
                data={rows}
                emptyText={hasFilters
                    ? 'Ничего не найдено по заданным фильтрам'
                    : 'Нет данных — выполните синхронизацию во вкладке «Реквизиты»'}
                emptyIcon="📦"
                exportName="ff_stocks"
            />
        </>
    );
}

/* ─── Tab: ФФ сопоставление (короб → россыпь) ───────────────────────────── */

function FfBoxPacksTab({ warehouseId }: { warehouseId: number }) {
    const [data, setData] = useState<FfBoxPack[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [search, setSearch] = useState('');
    const [onlyUnmapped, setOnlyUnmapped] = useState(false);
    const [editing, setEditing] = useState<FfBoxPack | null>(null);
    const [notice, setNotice] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setData(await api.getFulfillmentBoxPacks(warehouseId));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setLoading(false);
        }
    }, [warehouseId]);

    useEffect(() => { void load(); }, [load]);

    const rows = useMemo(() => {
        let result = data ?? [];
        const q = search.trim().toLowerCase();
        if (q) {
            result = result.filter(r =>
                r.box_barcode.toLowerCase().includes(q)
                || (r.base_barcode ?? '').toLowerCase().includes(q)
                || (r.article_seller ?? '').toLowerCase().includes(q)
                || (r.name ?? '').toLowerCase().includes(q)
            );
        }
        if (onlyUnmapped) result = result.filter(r => r.source === 'unmapped');
        return result;
    }, [data, search, onlyUnmapped]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;

    const all = data ?? [];
    const manual = all.filter(r => r.source === 'manual').length;
    const unmapped = all.filter(r => r.source === 'unmapped').length;
    const totalUnits = all.reduce((s, r) => s + r.units_qty, 0);

    const sourceBadge = (s: FfBoxPack['source']) => {
        if (s === 'manual') return <span className="badge badge-info" style={{ fontSize: 11, padding: '2px 8px' }}>вручную</span>;
        if (s === 'unmapped') return <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }}>не сопоставлен</span>;
        return <span className="badge badge-secondary" style={{ fontSize: 11, padding: '2px 8px' }}>авто</span>;
    };

    const cols: Column[] = [
        { key: 'box_barcode', label: 'ШК короба' },
        { key: 'source', label: 'Тип', render: (v: FfBoxPack['source']) => sourceBadge(v), exportValue: (r: FfBoxPack) => r.source },
        {
            key: 'article_seller', label: 'Наш товар',
            render: (_: unknown, row: FfBoxPack) => row.article_seller ?? '—',
            exportValue: (row: FfBoxPack) => row.article_seller ?? '',
        },
        { key: 'subject', label: 'Предмет', render: (v: string | null) => v || '—' },
        {
            key: 'units_per_box', label: 'В коробе, шт', align: 'right',
            render: (v: number, r: FfBoxPack) => (r.source === 'unmapped' ? '—' : formatNumber(v, 0)),
            exportValue: (r: FfBoxPack) => (r.source === 'unmapped' ? '' : r.units_per_box),
        },
        { key: 'box_qty', label: 'Коробов', align: 'right', format: 'number' },
        {
            key: 'units_qty', label: '= штук', align: 'right',
            render: (v: number, r: FfBoxPack) => (r.source === 'unmapped' ? '—' : <strong>{formatNumber(v, 0)}</strong>),
            exportValue: (r: FfBoxPack) => (r.source === 'unmapped' ? '' : r.units_qty),
        },
        { key: 'base_barcode', label: 'ШК россыпи', render: (v: string | null) => v || '—' },
        {
            key: 'actions', label: '', sortable: false,
            render: (_: unknown, row: FfBoxPack) => (
                <button className="btn btn-sm btn-secondary" onClick={() => setEditing(row)}>
                    {row.source === 'unmapped' ? 'Указать' : 'Изменить'}
                </button>
            ),
            exportValue: () => '',
        },
    ];

    const summary = [
        { label: 'Коробных SKU', value: all.length },
        { label: 'Вручную', value: manual, color: manual > 0 ? 'var(--color-accent)' : undefined },
        { label: 'Не сопоставлено', value: unmapped, color: unmapped > 0 ? 'var(--color-warning)' : undefined },
        { label: 'Всего в штуках', value: totalUnits },
    ];

    return (
        <>
            <div className="glass-card" style={{ padding: 16, marginBottom: 16, fontSize: 13, color: 'var(--color-text-muted)' }}>
                Сопоставление выводится автоматически при синхронизации: ШК короба (ITF14) → ШК россыпи (EAN13) по GTIN-14, кол-во в коробе — из названия «короб N шт.». Если короб не сопоставился — нажмите «Указать» и выберите наш товар. Остатки коробов сводятся к россыпи на вкладке «Остатки».
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
                {summary.map(s => (
                    <div key={s.label} className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>{s.label}</div>
                        <div style={{ fontSize: 24, fontWeight: 700, color: s.color }}>{formatNumber(s.value, 0)}</div>
                    </div>
                ))}
            </div>
            {notice && (
                <div className="badge badge-success" style={{ marginBottom: 12, fontSize: 13, padding: '6px 12px' }}>{notice}</div>
            )}
            <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                <input
                    className="form-input"
                    style={{ maxWidth: 280, fontSize: 13 }}
                    placeholder="🔍 ШК короба / россыпи / артикул"
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                />
                <button
                    className={`btn btn-sm ${onlyUnmapped ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setOnlyUnmapped(v => !v)}
                >
                    Только не сопоставленные
                </button>
                <span style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--color-text-muted)' }}>
                    Показано: {formatNumber(rows.length, 0)} из {formatNumber(all.length, 0)}
                </span>
            </div>
            <TanStackDataTable
                columns={cols}
                data={rows}
                emptyText="Короба не обнаружены — склад учитывает товар только россыпью (или ещё не было синхронизации)"
                emptyIcon="📦"
                exportName="ff_box_packs"
            />
            {editing && (
                <FfBoxOverrideModal
                    warehouseId={warehouseId}
                    pack={editing}
                    onClose={() => setEditing(null)}
                    onSaved={(msg) => { setEditing(null); setNotice(msg); void load(); }}
                />
            )}
        </>
    );
}

/* ─── Модалка: ручное сопоставление короба ──────────────────────────────── */

function FfBoxOverrideModal({ warehouseId, pack, onClose, onSaved }: {
    warehouseId: number;
    pack: FfBoxPack;
    onClose: () => void;
    onSaved: (notice: string) => void;
}) {
    const [query, setQuery] = useState('');
    const [options, setOptions] = useState<FfNomenclatureOption[]>([]);
    const [searching, setSearching] = useState(false);
    const [selected, setSelected] = useState<FfNomenclatureOption | null>(null);
    const [units, setUnits] = useState<string>(pack.units_per_box > 1 ? String(pack.units_per_box) : '');
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        const q = query.trim();
        if (!q) { setOptions([]); return; }
        const controller = new AbortController();
        setSearching(true);
        api.searchFulfillmentNomenclature(warehouseId, q)
            .then(r => { if (!controller.signal.aborted) setOptions(r); })
            .catch(() => { if (!controller.signal.aborted) setOptions([]); })
            .finally(() => { if (!controller.signal.aborted) setSearching(false); });
        return () => controller.abort();
    }, [warehouseId, query]);

    const unitsNum = parseInt(units, 10);
    const canSave = selected !== null && Number.isFinite(unitsNum) && unitsNum >= 1 && !saving;

    const handleSave = async () => {
        if (!selected || !Number.isFinite(unitsNum) || unitsNum < 1) return;
        setSaving(true);
        setError('');
        try {
            await api.setFulfillmentBoxOverride(warehouseId, pack.box_barcode, {
                nomenclature_id: selected.id,
                units_per_box: unitsNum,
            });
            onSaved(`Короб ${pack.box_barcode} → ${selected.article_seller ?? selected.barcode}, ${unitsNum} шт/короб`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
            setSaving(false);
        }
    };

    const handleReset = async () => {
        setSaving(true);
        setError('');
        try {
            await api.deleteFulfillmentBoxOverride(warehouseId, pack.box_barcode);
            onSaved(`Короб ${pack.box_barcode} — ручная привязка снята (вернулся к авто)`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка сброса');
            setSaving(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-solid" onClick={e => e.stopPropagation()}>
                <h2 className="modal-title" style={{ marginBottom: 4 }}>Что лежит в коробе?</h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    ШК короба: <strong>{pack.box_barcode}</strong>
                    {pack.name ? ` · ${pack.name}` : ''} · остаток {formatNumber(pack.box_qty, 0)} кор.
                </p>

                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)' }}>Наш товар (россыпь)</label>
                {selected ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '6px 0 12px' }}>
                        <span className="badge badge-info" style={{ fontSize: 13, padding: '4px 10px' }}>
                            {selected.article_seller ?? '—'} · {selected.barcode}
                        </span>
                        <button className="btn btn-sm btn-secondary" onClick={() => { setSelected(null); }}>Изменить</button>
                    </div>
                ) : (
                    <>
                        <input
                            className="form-input"
                            style={{ width: '100%', fontSize: 13, margin: '6px 0 8px' }}
                            placeholder="🔍 Поиск по артикулу или ШК россыпи"
                            value={query}
                            onChange={e => setQuery(e.target.value)}
                            autoFocus
                        />
                        <div style={{ maxHeight: 240, overflowY: 'auto', marginBottom: 12 }}>
                            {searching && <div style={{ padding: 12, color: 'var(--color-text-muted)', fontSize: 13 }}>Поиск...</div>}
                            {!searching && query.trim() && options.length === 0 && (
                                <div style={{ padding: 12, color: 'var(--color-text-muted)', fontSize: 13 }}>Ничего не найдено (товар должен быть в номенклатуре с ШК)</div>
                            )}
                            {options.map(o => (
                                <div
                                    key={o.id}
                                    onClick={() => setSelected(o)}
                                    style={{ padding: '8px 10px', borderRadius: 8, cursor: 'pointer', display: 'flex', justifyContent: 'space-between', gap: 8 }}
                                    className="hover-row"
                                >
                                    <span style={{ fontWeight: 500 }}>{o.article_seller ?? '—'}</span>
                                    <span style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>{o.subject || ''} · {o.barcode}</span>
                                </div>
                            ))}
                        </div>
                    </>
                )}

                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)' }}>Штук в коробе</label>
                <input
                    className="form-input"
                    type="number"
                    min={1}
                    style={{ width: 140, fontSize: 13, margin: '6px 0 16px', display: 'block' }}
                    placeholder="напр. 20"
                    value={units}
                    onChange={e => setUnits(e.target.value)}
                />

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    {pack.source === 'manual' && (
                        <button className="btn btn-sm btn-danger" onClick={handleReset} disabled={saving} style={{ marginRight: 'auto' }}>
                            Сбросить привязку
                        </button>
                    )}
                    <button className="btn btn-sm btn-secondary" onClick={onClose} disabled={saving}>Отмена</button>
                    <button className="btn btn-sm btn-primary" onClick={handleSave} disabled={!canSave}>Сохранить</button>
                </div>
            </div>
        </div>
    );
}

/* ─── Tab: ФФ история (журнал смены статусов синком) ────────────────────── */

const FF_EVENT_KIND_LABELS: Record<string, string> = {
    assembly: 'Сборка',
    inbound: 'Приёмка',
    other: 'Прочее',
};
type FfHistoryKindFilter = 'all' | 'assembly' | 'inbound';

function FfHistoryTab({ warehouseId, slug }: { warehouseId: number; slug: string }) {
    const [events, setEvents] = useState<FfStatusEvent[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [kindFilter, setKindFilter] = useState<FfHistoryKindFilter>('all');
    const [search, setSearch] = useState('');

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        api.getFfStatusHistory(warehouseId)
            .then(r => { if (!controller.signal.aborted) setEvents(r); })
            .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [warehouseId]);

    const rows = useMemo(() => {
        let result = events;
        if (kindFilter !== 'all') result = result.filter(e => e.kind === kindFilter);
        const q = search.trim().toLowerCase();
        if (q) {
            result = result.filter(e =>
                (e.number ?? '').toLowerCase().includes(q)
                || e.external_id.toLowerCase().includes(q)
                || ffEventSummary(e).toLowerCase().includes(q)
            );
        }
        return result;
    }, [events, kindFilter, search]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;

    const hasFilters = kindFilter !== 'all' || Boolean(search.trim());

    const cols: Column[] = [
        {
            key: 'changed_at', label: 'Когда',
            render: (v: string) => formatDateTime(v),
            exportValue: (row: FfStatusEvent) => row.changed_at,
        },
        {
            key: 'number', label: 'Заявка',
            render: (_: unknown, row: FfStatusEvent) => (
                <Link
                    href={`/p/${slug}/warehouse/${warehouseId}/ff-request/${row.fulfillment_request_id}`}
                    title="Открыть заявку"
                    style={{ fontWeight: 600, color: 'var(--color-accent)', textDecoration: 'none' }}
                >
                    {row.number || row.external_id}
                </Link>
            ),
            exportValue: (row: FfStatusEvent) => row.number || row.external_id,
        },
        {
            key: 'kind', label: 'Тип',
            render: (v: string) => FF_EVENT_KIND_LABELS[v] || v,
        },
        {
            key: 'dest_warehouse', label: 'Склад сдачи',
            render: (v: string | null) => v || '—',
            exportValue: (row: FfStatusEvent) => row.dest_warehouse || '',
        },
        {
            key: 'total_qty', label: 'Кол-во', align: 'right',
            render: (v: number | null) => (v == null ? '—' : formatNumber(v, 0)),
            exportValue: (row: FfStatusEvent) => row.total_qty ?? '',
        },
        {
            key: 'linked_number', label: 'Наша заявка',
            render: (v: string | null) => v
                ? <span style={{ fontWeight: 600 }}>{v}</span>
                : <span style={{ color: 'var(--color-text-muted)' }}>—</span>,
            exportValue: (row: FfStatusEvent) => row.linked_number || '',
        },
        {
            key: 'event', label: 'Что изменилось',
            render: (_: unknown, row: FfStatusEvent) => (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    {ffEventBadge(row)}
                    <span>{ffEventSummary(row)}</span>
                </span>
            ),
            exportValue: (row: FfStatusEvent) => ffEventSummary(row),
        },
    ];

    const filterBtn = (key: FfHistoryKindFilter, label: string) => (
        <button
            className={`btn btn-sm ${kindFilter === key ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setKindFilter(key)}
        >
            {label}
        </button>
    );

    return (
        <>
            <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                <div style={{ display: 'flex', gap: 4 }}>
                    {filterBtn('all', 'Все')}
                    {filterBtn('assembly', 'Сборка')}
                    {filterBtn('inbound', 'Приёмки')}
                </div>
                <input
                    className="form-input"
                    style={{ maxWidth: 280, fontSize: 13 }}
                    placeholder="🔍 Номер заявки / стадия"
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                />
                {hasFilters && (
                    <button className="btn btn-sm btn-secondary" onClick={() => { setKindFilter('all'); setSearch(''); }}>
                        Сбросить
                    </button>
                )}
                <span style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--color-text-muted)' }}>
                    {hasFilters ? `Показано: ${formatNumber(rows.length, 0)} из ${formatNumber(events.length, 0)}` : `Всего событий: ${formatNumber(events.length, 0)}`}
                </span>
            </div>

            <TanStackDataTable
                columns={cols}
                data={rows}
                emptyText={hasFilters
                    ? 'Нет событий по заданным фильтрам'
                    : 'История пуста — статусы заявок ещё не менялись. Журнал заполняется при синхронизации.'}
                emptyIcon="🕓"
                exportName="ff_status_history"
            />
        </>
    );
}

function FfSyncTab({ warehouseId }: { warehouseId: number }) {
    const [runs, setRuns] = useState<FfSyncRun[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        api.getFfSyncRuns(warehouseId)
            .then(r => { if (!controller.signal.aborted) setRuns(r); })
            .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [warehouseId]);

    const lastOk = useMemo(() => runs.find(r => r.status === 'OK') ?? null, [runs]);

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;

    const statusBadge = (status: string) => {
        if (status === 'OK') return <span className="badge badge-success">Успешно</span>;
        if (status === 'ERROR') return <span className="badge badge-danger">Ошибка</span>;
        return <span className="badge badge-info">Выполняется</span>;
    };

    const cols: Column[] = [
        {
            key: 'started_at', label: 'Когда',
            render: (v: string) => formatDateTime(v),
            exportValue: (row: FfSyncRun) => row.started_at,
        },
        {
            key: 'status', label: 'Статус',
            render: (v: string) => statusBadge(v),
        },
        {
            key: 'stocks_synced', label: 'Остатков',
            render: (v: number) => formatNumber(v, 0),
        },
        {
            key: 'requests_synced', label: 'Заявок',
            render: (v: number) => formatNumber(v, 0),
        },
        {
            key: 'duration_seconds', label: 'Длительность',
            render: (v: number | null) => v === null ? '—' : `${formatNumber(v, 0)} с`,
        },
        {
            key: 'error_msg', label: 'Ошибка',
            render: (v: string | null) => v ?? '—',
        },
    ];

    return (
        <>
            <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                    Последняя успешная синхронизация: {lastOk ? formatDateTime(lastOk.started_at) : '—'}
                </span>
                <span style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--color-text-muted)' }}>
                    Всего прогонов: {formatNumber(runs.length, 0)}
                </span>
            </div>

            <TanStackDataTable
                columns={cols}
                data={runs}
                emptyText="Синхронизаций ещё не было — журнал заполнится при первом синке."
                emptyIcon="♻️"
                exportName="ff_sync_runs"
            />
        </>
    );
}

/* ─── Tabs: ФФ сборка / ФФ приёмки ──────────────────────────────────────── */

/* Бейджи «вскрытие коробов» (migfull, «Натали»): вскрытие оформляется парой документов
   «Возврат» (короба) + «Поступление» (россыпь) — сток не двигается, это переупаковка. */
/**
 * Строка в паре «вскрытия»: у ПОСТУПЛЕНИЯ заполнен repack_return_id, у
 * ВОЗВРАТА — только зеркальный repack_pair_number (само поле пары живёт на
 * стороне поступления). Проверка одного repack_return_id теряла бейдж и
 * оставляла кнопку «Связать вскрытие» на уже связанном возврате.
 */
function ffRepackPaired(row: FfRequestRow): boolean {
    return row.repack_return_id != null || !!row.repack_pair_number;
}

/**
 * Код прогресса приёмки для фильтра: accepting — принимается, done — принято
 * всё, over — сверх заявки, idle — не начато. null — прогресс неприменим
 * (закрыта / нет данных факта).
 */
function ffProgressCode(row: FfRequestRow): 'accepting' | 'done' | 'over' | 'idle' | null {
    if (row.is_completed || row.accepted_qty == null) return null;
    const acc = row.accepted_qty;
    if (acc === 0) return 'idle';
    const planned = row.total_qty_units ?? row.total_qty;
    if (planned == null) return 'accepting';
    if (acc > planned) return 'over';
    return acc === planned ? 'done' : 'accepting';
}

function FfRequestsTab({ warehouseId, slug, kind, externalTick = 0 }: { warehouseId: number; slug: string; kind: FfRequestKind; externalTick?: number }) {
    const [rows, setRows] = useState<FfRequestRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actingId, setActingId] = useState<number | null>(null);

    // Вид «Активные | Архив» (local_archived)
    const [showArchived, setShowArchived] = useState(false);
    // Фильтры по статусам (клиентские, из загруженных строк)
    const [stageFilter, setStageFilter] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    // Срез по типу операции: вскрытие коробов / обычные / возврат без пары.
    const [opFilter, setOpFilter] = useState('');
    // Срез по живому прогрессу приёмки (см. ffProgressCode).
    const [progressFilter, setProgressFilter] = useState('');
    // Toast успеха + несмахиваемое предупреждение (пропущенные ШК при создании заявки)
    const [toast, setToast] = useState('');
    const [notice, setNotice] = useState('');

    // Модал «Связать» (общий пикер — ff-shared)
    const [linkFor, setLinkFor] = useState<FfRequestRow | null>(null);
    // Модал «Связать вскрытие» — возврат, для которого подбираем поступление-пару
    const [repackFor, setRepackFor] = useState<FfRequestRow | null>(null);

    // Массовый выбор строк (для архива/возврата) — id видимых заявок
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [bulkActing, setBulkActing] = useState(false);

    // Тик-счётчики для ручного перезапуска загрузки заявок и блока «без связи» (после реверс-линка)
    const [reloadTick, setReloadTick] = useState(0);
    const [unlinkedReloadTick, setUnlinkedReloadTick] = useState(0);
    // id сборки для модалки «расхождение наполнения»
    const [mismatchForAssembly, setMismatchForAssembly] = useState<number | null>(null);

    // Смена вида/фильтра/перезагрузка — сбросить выбор (id устаревают)
    useEffect(() => { setSelected(new Set()); }, [warehouseId, kind, showArchived, stageFilter, statusFilter, opFilter, progressFilter, reloadTick]);

    // Реверс-линк связал ФФ-заявку с нашей сборкой → обновляем обе таблицы
    const handleReverseLinked = useCallback((ffNumber: string, assemblyNumber: string) => {
        setReloadTick(t => t + 1);
        setUnlinkedReloadTick(t => t + 1);
        setToast(`Заявка ФФ ${ffNumber} связана со сборкой № ${assemblyNumber}`);
    }, []);

    // Массово создали заявки ФФ из блока «без связи» → обновляем обе таблицы
    const handleBulkCreated = useCallback((createdCount: number) => {
        setReloadTick(t => t + 1);
        setUnlinkedReloadTick(t => t + 1);
        if (createdCount > 0) setToast(`Создано заявок на ФФ: ${formatNumber(createdCount, 0)}`);
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        api.getFulfillmentRequests(warehouseId, kind, showArchived)
            .then(r => { if (!controller.signal.aborted) setRows(r); })
            .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [warehouseId, kind, showArchived, reloadTick, externalTick]);

    const handleUnlink = async (row: FfRequestRow) => {
        if (!confirm(`Отвязать заявку ${row.number || row.external_id} от документа ${row.linked_number}?`)) return;
        setActingId(row.id);
        setError('');
        try {
            const updated = await api.unlinkFulfillmentRequest(warehouseId, row.id);
            setRows(prev => prev.map(r => r.id === updated.id ? updated : r));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка отвязки');
        } finally {
            setActingId(null);
        }
    };

    // Разорвать пару «вскрытие коробов». DELETE зовётся с id ВОЗВРАТА:
    // на строке поступления пара-возврат лежит в repack_return_id.
    const handleRepackUnlink = async (row: FfRequestRow) => {
        const returnId = row.kind === 'return' ? row.id : row.repack_return_id;
        if (returnId == null) return;
        if (!confirm(`Разорвать пару «вскрытие коробов» у заявки ${row.number || row.external_id}?`)) return;
        setActingId(row.id);
        setError('');
        try {
            await api.unlinkFfRepackPair(warehouseId, returnId);
            setToast('Пара «вскрытие коробов» разорвана');
            setReloadTick(t => t + 1);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка отвязки пары');
        } finally {
            setActingId(null);
        }
    };

    // В архив / вернуть из архива — строка покидает текущий вид
    const handleArchiveToggle = async (row: FfRequestRow) => {
        setActingId(row.id);
        setError('');
        try {
            const updated = row.local_archived
                ? await api.unarchiveFulfillmentRequest(warehouseId, row.id)
                : await api.archiveFulfillmentRequest(warehouseId, row.id);
            setRows(prev => prev.filter(r => r.id !== updated.id));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : (row.local_archived ? 'Ошибка возврата из архива' : 'Ошибка архивирования'));
        } finally {
            setActingId(null);
        }
    };

    // Создать заявку на сборку из состава ФФ-заявки и сразу связать их (kind=assembly)
    const handleCreateAssembly = async (row: FfRequestRow) => {
        if (!confirm(`Создать заявку на сборку из ФФ-заявки ${row.number || row.external_id}?`)) return;
        setActingId(row.id);
        setError('');
        setNotice('');
        try {
            const result = await api.createAssemblyFromFf(warehouseId, row.id);
            setRows(prev => prev.map(r => r.id === result.request.id ? result.request : r));
            setToast(`Создана заявка на сборку № ${result.assembly_number}`);
            if (result.skipped_barcodes.length > 0) {
                setNotice(ffSkippedNotice(result.assembly_number, result.skipped_barcodes));
            }
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка создания заявки на сборку');
        } finally {
            setActingId(null);
        }
    };

    const linkedStatusLabel = (s: string | null) => (s ? (FF_LINKED_STATUS_LABELS[s] || s) : '');

    const toggleOne = (id: number, checked: boolean) => {
        setSelected(prev => {
            const next = new Set(prev);
            if (checked) next.add(id); else next.delete(id);
            return next;
        });
    };

    // Колонка «Кол-во (шт)» (пересчёт коробов) видна, когда у заявок есть это число (migfull)
    const hasUnits = rows.some(r => r.total_qty_units != null);

    // Сколько заявок ФФ привязано к одной нашей сборке (migfull/«Натали» — N:1).
    // Считаем из загруженных строк: если >1 — на строках показываем бейдж, что они
    // относятся к одной нашей сборке ASM (иначе строки выглядят несвязанными).
    const linkedCount = useMemo(() => {
        const m = new Map<number, number>();
        for (const r of rows) {
            if (r.assembly_request_id != null) m.set(r.assembly_request_id, (m.get(r.assembly_request_id) ?? 0) + 1);
        }
        return m;
    }, [rows]);

    const cols: Column[] = [
        {
            key: '_select', label: '', sortable: false, align: 'center',
            exportValue: () => '',
            render: (_: unknown, row: FfRequestRow) => (
                <input
                    type="checkbox"
                    checked={selected.has(row.id)}
                    onChange={e => toggleOne(row.id, e.target.checked)}
                    aria-label={`Выбрать заявку ${row.number || row.external_id}`}
                />
            ),
        },
        {
            key: 'number', label: 'Номер',
            render: (v: string | null, row: FfRequestRow) => (
                <Link
                    href={`/p/${slug}/warehouse/${warehouseId}/ff-request/${row.id}`}
                    title="Открыть состав заявки"
                    style={{ fontWeight: 600, color: 'var(--color-accent)', textDecoration: 'none' }}
                >
                    {v || row.external_id}
                </Link>
            ),
            exportValue: (row: FfRequestRow) => row.number || row.external_id,
        },
        {
            key: 'external_created_at', label: 'Создана',
            render: (v: string | null) => (v ? formatDate(v) : '—'),
        },
        // Тип показывает ОПЕРАЦИЮ, а не константу провайдера: «Приёмка» у всех
        // строк не говорила ничего, а бейджи типов в «Стадии» налезали друг на
        // друга. Вскрытие/перемещение различимы прямо здесь.
        {
            key: 'type_name', label: 'Тип',
            render: (v: string | null, row: FfRequestRow) => {
                if (ffRepackPaired(row)) {
                    return (
                        <span
                            style={{ fontWeight: 600, color: 'var(--color-accent)' }}
                            title={`Вскрытие коробов — пара ${row.repack_pair_number || '…'}: внутренняя переупаковка ФФ, сток не двигается`}
                        >
                            Вскрытие коробов
                        </span>
                    );
                }
                if (kind === 'inbound' && row.stock_transfer_id != null) {
                    return (
                        <span
                            style={{ fontWeight: 600 }}
                            title={`Приёмка внутреннего перемещения с нашего склада${row.linked_number ? ` (${row.linked_number})` : ''} — это не закупка`}
                        >
                            Перемещение
                        </span>
                    );
                }
                return v || '—';
            },
            exportValue: (row: FfRequestRow) => ffRepackPaired(row)
                ? 'Вскрытие коробов'
                : (kind === 'inbound' && row.stock_transfer_id != null ? 'Перемещение' : row.type_name || ''),
        },
        // Только для сборки: склад отгрузки МП (из деталки skladbot)
        ...(kind === 'assembly' ? [
            {
                key: 'dest_warehouse', label: 'Склад отгрузки',
                render: (v: string | null) => v || '—',
                exportValue: (row: FfRequestRow) => row.dest_warehouse || '',
            } as Column,
        ] : []),
        // Заявленное кол-во — во всех под-вкладках (сборка / приёмки / возвраты).
        // Возврат с коробами: «штук россыпи · N кор.» — сырое кол-во строк
        // смешивает короба и штуки (603 «кол-во» = 600 коробов + 3 шт) и
        // читалось как штуки. Живой прогресс приёмки живёт в «Стадии».
        {
            key: 'total_qty', label: 'Кол-во, шт', align: 'right',
            // Единый канон: главное число — ВСЕГДА штуки (пересчёт коробов, когда
            // есть карта кратности), «· N кор.» — подпись. Сырые кол-ва смешивали
            // короба со штуками («заявлен 1» = 1 короб) — сравнивать нельзя было.
            // Три визуальных состояния — «коробами или штуками» видно сразу:
            //   «X шт»            — состав подтверждённо штучный;
            //   «X шт · 📦 N кор.» — в составе короба (пересчитаны в штуки);
            //   «X ?»             — единицы не определены (нет карты кратности) —
            //                       сырое число строк, может смешивать короба и штуки.
            render: (v: number | null, row: FfRequestRow) => {
                const units = kind === 'assembly' ? null : row.total_qty_units;
                if (kind !== 'assembly' && units != null) {
                    return (
                        <span style={{ whiteSpace: 'nowrap' }}>
                            <span>{formatNumber(units, 0)}</span>
                            <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}> шт</span>
                            {row.total_boxes != null && (
                                <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                    {' '}· 📦 {formatNumber(row.total_boxes, 0)} кор.
                                </span>
                            )}
                        </span>
                    );
                }
                if (v == null) return '—';
                // Без суффикса «шт» = единицы не подтверждены (сырое число строк) —
                // тихий сигнал вместо оранжевого «?» на каждой свежей приёмке.
                return (
                    <span
                        style={{ whiteSpace: 'nowrap' }}
                        title={kind !== 'assembly'
                            ? 'Единицы не подтверждены: у части SKU нет карты кратности — число по строкам документа'
                            : undefined}
                    >
                        {formatNumber(v, 0)}
                    </span>
                );
            },
            exportValue: (row: FfRequestRow) =>
                (kind === 'assembly' ? row.total_qty : row.total_qty_units ?? row.total_qty) ?? '',
        } as Column,
        // Кол-во в штуках россыпи (пересчёт коробов) — только сборка и только когда есть (Натали/migfull)
        ...(kind === 'assembly' && hasUnits ? [{
            key: 'total_qty_units', label: 'Кол-во (шт)', align: 'right',
            render: (v: number | null) => (v == null ? '—' : formatNumber(v, 0)),
            exportValue: (row: FfRequestRow) => row.total_qty_units ?? '',
        } as Column] : []),
        {
            key: 'stage_title', label: 'Стадия',
            render: (v: string | null, row: FfRequestRow) => (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <span>{v || row.status || '—'}</span>
                    {ffStageBadge(row)}
                    {/* Живой факт приёмки Натали (received-строки, тянет синк) —
                        В ШТУКАХ, как и заявленное. Три состояния: частично (info),
                        всё (success), сверх заявки (warning). «Принято 0» не
                        показываем — шум на каждой активной строке. */}
                    {kind === 'inbound' && !row.is_completed && (row.accepted_qty ?? 0) > 0 && (() => {
                        const acc = row.accepted_qty as number;
                        const planned = row.total_qty_units ?? row.total_qty;
                        const over = planned != null && acc > planned;
                        const full = planned != null && acc === planned;
                        const badge = over ? 'badge-warning' : full ? 'badge-success' : 'badge-info';
                        const label = planned == null
                            ? `принято ${formatNumber(acc, 0)}`
                            : over
                                ? `принято ${formatNumber(acc, 0)} из ${formatNumber(planned, 0)} — сверх заявки`
                                : full
                                    ? `принято всё · ${formatNumber(acc, 0)}`
                                    : `принято ${formatNumber(acc, 0)} из ${formatNumber(planned, 0)}`;
                        return (
                            <span
                                className={`badge ${badge}`}
                                style={{ fontSize: 11, padding: '2px 8px' }}
                                title="Принято фактически (в штуках) — живой прогресс приёмки ФФ, обновляется синхронизацией"
                            >
                                {label}
                            </span>
                        );
                    })()}
                    {/* Тип операции живёт в колонке «Тип», отвязка — в «Связи».
                        Здесь только предупреждение возврата без пары. */}
                    {kind === 'return' && !ffRepackPaired(row) && row.repack_unpaired && (
                        <span
                            className="badge badge-warning"
                            style={{ fontSize: 11, padding: '2px 8px' }}
                            title="Поступления-пары нет — возможно, реальный возврат товара со склада ФФ. Проверьте и оформите приход вручную, если товар едет к вам"
                        >
                            Без пары
                        </span>
                    )}
                </span>
            ),
            exportValue: (row: FfRequestRow) => row.stage_title || row.status || '',
        },
        {
            key: 'ff_status', label: 'Статус ФФ',
            render: (_: unknown, row: FfRequestRow) => ffStatusBadge(row),
            exportValue: (row: FfRequestRow) => ffStatusLabel(row),
        },
        {
            key: 'linked_number', label: 'Связь',
            render: (_: unknown, row: FfRequestRow) => {
                const acting = actingId === row.id;
                // Пара «вскрытия» — тоже связь: номер парного документа + отвязка.
                if (ffRepackPaired(row)) {
                    return (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <span
                                style={{ fontWeight: 600 }}
                                title={kind === 'return' ? 'Поступление-пара вскрытия' : 'Возврат-пара вскрытия'}
                            >
                                {row.repack_pair_number || '—'}
                            </span>
                            <button className="btn btn-sm btn-secondary" onClick={() => handleRepackUnlink(row)} disabled={acting}>
                                {acting ? '...' : 'Отвязать'}
                            </button>
                        </span>
                    );
                }
                if (row.linked_number) {
                    const siblings = row.assembly_request_id != null ? (linkedCount.get(row.assembly_request_id) ?? 0) : 0;
                    return (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            {row.assembly_request_id != null ? (
                                <Link
                                    href={`/p/${slug}/warehouse/assembly/${row.assembly_request_id}`}
                                    title="Открыть нашу сборку"
                                    style={{ fontWeight: 600, color: 'var(--color-accent)', textDecoration: 'none' }}
                                >
                                    {row.linked_number}
                                </Link>
                            ) : (
                                <span style={{ fontWeight: 600 }}>{row.linked_number}</span>
                            )}
                            {siblings > 1 && (
                                <span
                                    className="badge badge-info"
                                    style={{ fontSize: 11, padding: '2px 8px' }}
                                    title={`Сборка ${row.linked_number}: на неё в ФФ «Натали» заведено ${formatNumber(siblings, 0)} заявок — все относятся к одной нашей сборке`}
                                >
                                    заявок на сборку: {formatNumber(siblings, 0)}
                                </span>
                            )}
                            {row.linked_status && (
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{linkedStatusLabel(row.linked_status)}</span>
                            )}
                            {row.linked_mismatch === true && (
                                row.assembly_request_id != null ? (
                                    <button
                                        type="button"
                                        className="badge badge-warning"
                                        style={{ fontSize: 11, padding: '2px 8px', cursor: 'pointer', border: 'none' }}
                                        title="Показать расхождения по позициям"
                                        onClick={() => setMismatchForAssembly(row.assembly_request_id)}
                                    >
                                        ⚠ расхождение
                                    </button>
                                ) : (
                                    <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }} title="Состав нашего документа расходится с заявкой ФФ по наполнению">
                                        ⚠ расхождение
                                    </span>
                                )
                            )}
                            <button className="btn btn-sm btn-secondary" onClick={() => handleUnlink(row)} disabled={acting}>
                                {acting ? '...' : 'Отвязать'}
                            </button>
                        </span>
                    );
                }
                return (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        {/* Возврат с нашим документом не связывается (бэк отвергнет) —
                            для него ручная связка пары «вскрытие коробов» */}
                        {kind !== 'return' && (
                            <button className="btn btn-sm btn-secondary" onClick={() => setLinkFor(row)} disabled={acting}>
                                Связать
                            </button>
                        )}
                        {kind === 'return' && !ffRepackPaired(row) && (
                            <button
                                className="btn btn-sm btn-secondary"
                                title="Подобрать поступление-пару «вскрытие коробов» (ФФ вскрыл короба под FBS)"
                                onClick={() => setRepackFor(row)}
                                disabled={acting}
                            >
                                Связать вскрытие
                            </button>
                        )}
                        {kind === 'assembly' && row.assembly_request_id == null && (
                            <button
                                className="btn btn-sm btn-primary"
                                title="Создать заявку на сборку из состава этой ФФ-заявки"
                                onClick={() => handleCreateAssembly(row)}
                                disabled={acting}
                            >
                                {acting ? '...' : 'Создать заявку'}
                            </button>
                        )}
                    </span>
                );
            },
            exportValue: (row: FfRequestRow) => row.linked_number
                ? `${row.linked_number}${row.linked_status ? ` (${linkedStatusLabel(row.linked_status)})` : ''}`
                : '',
        },
        {
            key: 'local_archived', label: 'Архив',
            render: (_: unknown, row: FfRequestRow) => {
                const acting = actingId === row.id;
                return (
                    <button
                        className="btn btn-sm btn-secondary"
                        title={row.local_archived ? 'Вернуть из архива в активные' : 'Убрать в архив (локальная пометка)'}
                        onClick={() => handleArchiveToggle(row)}
                        disabled={acting}
                    >
                        {acting ? '...' : (row.local_archived ? 'Вернуть' : 'В архив')}
                    </button>
                );
            },
            exportValue: (row: FfRequestRow) => (row.local_archived ? 'архив' : ''),
        },
    ];

    // Распознаваемые значения для фильтров: стадия провайдера + статус ФФ (бейдж)
    const stageOptions = useMemo(
        () => Array.from(new Set(rows.map(r => r.stage_title || r.status || '').filter(Boolean))).sort(),
        [rows],
    );
    const statusOptions = useMemo(
        () => Array.from(new Set(rows.map(r => ffStatusLabel(r)).filter(Boolean))).sort(),
        [rows],
    );
    const filteredRows = useMemo(
        () => rows.filter(r =>
            (!stageFilter || (r.stage_title || r.status || '') === stageFilter)
            && (!statusFilter || ffStatusLabel(r) === statusFilter)
            && (!opFilter
                || (opFilter === 'repack' && ffRepackPaired(r))
                || (opFilter === 'transfer' && r.stock_transfer_id != null)
                || (opFilter === 'plain'
                    && !ffRepackPaired(r) && !r.repack_unpaired && r.stock_transfer_id == null)
                || (opFilter === 'unpaired' && !!r.repack_unpaired))
            && (!progressFilter || ffProgressCode(r) === progressFilter),
        ),
        [rows, stageFilter, statusFilter, opFilter, progressFilter],
    );

    // Чипы срезов со счётчиками — только осмысленные для вкладки и с данными.
    const opChips = useMemo(() => {
        if (kind === 'assembly') return [] as Array<[string, string, number]>;
        const paired = rows.filter(r => ffRepackPaired(r)).length;
        const unpaired = rows.filter(r => !!r.repack_unpaired).length;
        const transfers = rows.filter(r => r.stock_transfer_id != null && !ffRepackPaired(r)).length;
        const plain = rows.length - paired - unpaired - transfers;
        const chips: Array<[string, string, number]> = [['', 'Все', rows.length]];
        if (paired) chips.push(['repack', 'Вскрытие коробов', paired]);
        if (transfers) chips.push(['transfer', 'Перемещения', transfers]);
        if (plain && (paired || unpaired || transfers)) chips.push(['plain', 'Обычные', plain]);
        if (kind === 'return' && unpaired) chips.push(['unpaired', 'Без пары', unpaired]);
        return chips.length > 1 ? chips : [];
    }, [rows, kind]);
    const progressChips = useMemo(() => {
        if (kind !== 'inbound') return [] as Array<[string, string, number]>;
        const by = { accepting: 0, done: 0, over: 0, idle: 0 } as Record<string, number>;
        for (const r of rows) {
            const c = ffProgressCode(r);
            if (c) by[c] += 1;
        }
        const chips: Array<[string, string, number]> = [];
        if (by.accepting) chips.push(['accepting', 'Принимается', by.accepting]);
        if (by.done) chips.push(['done', 'Принято всё', by.done]);
        if (by.over) chips.push(['over', 'Сверх заявки', by.over]);
        if (by.idle) chips.push(['idle', 'Не начато', by.idle]);
        return chips;
    }, [rows, kind]);

    // Массовый выбор/архив видимых заявок
    const selectedCount = useMemo(() => filteredRows.filter(r => selected.has(r.id)).length, [filteredRows, selected]);
    const allVisibleSelected = filteredRows.length > 0 && selectedCount === filteredRows.length;
    const toggleAll = (checked: boolean) => setSelected(checked ? new Set(filteredRows.map(r => r.id)) : new Set());
    const handleBulkArchive = async () => {
        const ids = filteredRows.filter(r => selected.has(r.id)).map(r => r.id);
        if (ids.length === 0) return;
        setBulkActing(true);
        setError('');
        try {
            await api.bulkArchiveFulfillmentRequests(warehouseId, { ff_request_ids: ids, archived: !showArchived });
            const idSet = new Set(ids);
            setRows(prev => prev.filter(r => !idSet.has(r.id)));  // строки покидают текущий вид
            setSelected(new Set());
            setToast(showArchived
                ? `Возвращено из архива: ${formatNumber(ids.length, 0)}`
                : `В архив: ${formatNumber(ids.length, 0)}`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка массового архивирования');
        } finally {
            setBulkActing(false);
        }
    };

    const bulkBar = (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                <input type="checkbox" checked={allVisibleSelected} onChange={e => toggleAll(e.target.checked)} />
                Выбрать все
            </label>
            {selectedCount > 0 && (
                <>
                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Выбрано: {formatNumber(selectedCount, 0)}</span>
                    <button className="btn btn-sm btn-primary" onClick={handleBulkArchive} disabled={bulkActing}>
                        {bulkActing
                            ? '...'
                            : (showArchived
                                ? `Вернуть из архива (${formatNumber(selectedCount, 0)})`
                                : `В архив (${formatNumber(selectedCount, 0)})`)}
                    </button>
                    <button className="btn btn-sm btn-secondary" onClick={() => setSelected(new Set())} disabled={bulkActing}>Сбросить</button>
                </>
            )}
        </div>
    );

    // Переключатель вида — виден и во время загрузки/ошибки; смена вида сбрасывает фильтры
    const switchView = (archived: boolean) => { setShowArchived(archived); setStageFilter(''); setStatusFilter(''); };
    const viewToggle = (
        <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
            <button
                className={`btn btn-sm ${!showArchived ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => switchView(false)}
            >
                Активные
            </button>
            <button
                className={`btn btn-sm ${showArchived ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => switchView(true)}
            >
                Архив
            </button>
        </div>
    );

    const statusFilters = (stageOptions.length > 1 || statusOptions.length > 1 || opChips.length > 0 || progressChips.length > 0) ? (
        <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            <select className="form-input" style={{ maxWidth: 240, fontSize: 13 }} value={stageFilter} onChange={e => setStageFilter(e.target.value)}>
                <option value="">Стадия: все</option>
                {stageOptions.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <select className="form-input" style={{ maxWidth: 240, fontSize: 13 }} value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
                <option value="">Статус ФФ: все</option>
                {statusOptions.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            {/* Срезы — селектами в один ряд со «Стадией»/«Статусом ФФ»: чипы
                разрастались в два ряда кнопок и читались как шум. */}
            {opChips.length > 0 && (
                <select className="form-input" style={{ maxWidth: 220, fontSize: 13 }} value={opFilter} onChange={e => setOpFilter(e.target.value)}>
                    {opChips.map(([code, label, n]) => (
                        <option key={`op-${code}`} value={code}>
                            {code === '' ? 'Тип: все' : `${label} · ${formatNumber(n, 0)}`}
                        </option>
                    ))}
                </select>
            )}
            {progressChips.length > 0 && (
                <select className="form-input" style={{ maxWidth: 220, fontSize: 13 }} value={progressFilter} onChange={e => setProgressFilter(e.target.value)}>
                    <option value="">Приёмка: вся</option>
                    {progressChips.map(([code, label, n]) => (
                        <option key={`pr-${code}`} value={code}>{label} · {formatNumber(n, 0)}</option>
                    ))}
                </select>
            )}
            {(stageFilter || statusFilter || opFilter || progressFilter) && (
                <button className="btn btn-sm btn-secondary" onClick={() => { setStageFilter(''); setStatusFilter(''); setOpFilter(''); setProgressFilter(''); }}>Сбросить</button>
            )}
            <span style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--color-text-muted)' }}>
                Показано: {formatNumber(filteredRows.length, 0)} из {formatNumber(rows.length, 0)}
            </span>
        </div>
    ) : null;

    if (loading) return <>{viewToggle}<div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div></>;
    if (error && rows.length === 0) return <>{viewToggle}<div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div></>;

    return (
        <>
            {viewToggle}

            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}
            {notice && (
                <div style={{ color: 'var(--color-warning)', marginBottom: 12, display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
                    <span>{notice}</span>
                    <button className="btn btn-sm btn-secondary" onClick={() => setNotice('')}>✕</button>
                </div>
            )}

            {statusFilters}

            {filteredRows.length > 0 && bulkBar}

            <TanStackDataTable
                columns={cols}
                data={filteredRows}
                emptyText={(stageFilter || statusFilter || opFilter || progressFilter)
                    ? 'Нет заявок под выбранные фильтры'
                    : (showArchived
                        ? 'Архив пуст'
                        : (kind === 'assembly'
                            ? 'Нет заявок на сборку — выполните синхронизацию во вкладке «Реквизиты»'
                            : (kind === 'return'
                                ? 'Нет возвратов — выполните синхронизацию во вкладке «Реквизиты»'
                                : 'Нет заявок на приёмку — выполните синхронизацию во вкладке «Реквизиты»')))}
                emptyIcon={showArchived ? '🗄️' : (kind === 'assembly' ? '🧰' : (kind === 'return' ? '↩️' : '📥'))}
                exportName={kind === 'assembly' ? 'ff_assembly_requests' : (kind === 'return' ? 'ff_return_requests' : 'ff_inbound_requests')}
            />

            {toast && <Toast message={toast} onClose={() => setToast('')} />}

            {mismatchForAssembly != null && (
                <FfMismatchModal assemblyId={mismatchForAssembly} onClose={() => setMismatchForAssembly(null)} />
            )}

            {linkFor && (
                <FfLinkModal
                    warehouseId={warehouseId}
                    kind={kind}
                    request={linkFor}
                    onClose={() => setLinkFor(null)}
                    onLinked={updated => {
                        setRows(prev => prev.map(r => r.id === updated.id ? updated : r));
                        // Связали ФФ-заявку с нашей сборкой → эта сборка покидает блок «без связи»
                        if (kind === 'assembly') setUnlinkedReloadTick(t => t + 1);
                        setLinkFor(null);
                    }}
                />
            )}

            {repackFor && (
                <FfRepackLinkModal
                    warehouseId={warehouseId}
                    request={repackFor}
                    onClose={() => setRepackFor(null)}
                    onLinked={pairNumber => {
                        setToast(`Возврат ${repackFor.number || repackFor.external_id} связан с поступлением ${pairNumber}`);
                        setRepackFor(null);
                        setReloadTick(t => t + 1);
                    }}
                />
            )}

            {/* Реверс: наши заявки на сборку без связи с ФФ (только во вкладке «Сборка») */}
            {kind === 'assembly' && (
                <FfUnlinkedAssembliesBlock
                    warehouseId={warehouseId}
                    slug={slug}
                    reloadTick={unlinkedReloadTick}
                    onReverseLinked={handleReverseLinked}
                    onBulkCreated={handleBulkCreated}
                />
            )}
        </>
    );
}

/* ─── Блок: наши заявки на сборку без связи с ФФ + реверс-линк-модал ─────── */

function FfUnlinkedAssembliesBlock({ warehouseId, slug, reloadTick, onReverseLinked, onBulkCreated }: {
    warehouseId: number;
    slug: string;
    /** меняется → перезагрузить список (после связывания из любой из двух таблиц) */
    reloadTick: number;
    /** реверс-линк выполнен: (ffNumber, assemblyNumber) — родитель обновляет обе таблицы и тост */
    onReverseLinked: (ffNumber: string, assemblyNumber: string) => void;
    /** массово создали заявки ФФ: createdCount — родитель обновляет обе таблицы */
    onBulkCreated: (createdCount: number) => void;
}) {
    const [rows, setRows] = useState<FfUnlinkedAssembly[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Реверс-линк-модал: выбранная наша заявка сборки, для которой ищем ФФ-заявку
    const [linkForAssembly, setLinkForAssembly] = useState<FfUnlinkedAssembly | null>(null);
    // Массовый выбор сборок + модал массового создания заявок ФФ
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [bulkOpen, setBulkOpen] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        api.getFfUnlinkedAssemblies(warehouseId)
            .then(r => { if (!controller.signal.aborted) setRows(r); })
            .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [warehouseId, reloadTick]);

    // Перезагрузка списка — сбросить выбор (id устаревают)
    useEffect(() => { setSelected(new Set()); }, [warehouseId, reloadTick]);

    const statusLabel = (s: string) => FF_LINKED_STATUS_LABELS[s] || s;

    const toggleOne = (id: number, checked: boolean) => {
        setSelected(prev => {
            const next = new Set(prev);
            if (checked) next.add(id); else next.delete(id);
            return next;
        });
    };
    const allSelected = rows.length > 0 && rows.every(r => selected.has(r.id));
    const toggleAll = (checked: boolean) => setSelected(checked ? new Set(rows.map(r => r.id)) : new Set());
    const selectedAssemblies = rows.filter(r => selected.has(r.id));

    const cols: Column[] = [
        {
            key: '_select', label: '', sortable: false, align: 'center',
            exportValue: () => '',
            render: (_: unknown, row: FfUnlinkedAssembly) => (
                <input
                    type="checkbox"
                    checked={selected.has(row.id)}
                    onChange={e => toggleOne(row.id, e.target.checked)}
                    aria-label={`Выбрать сборку ${row.number}`}
                />
            ),
        },
        {
            key: 'number', label: '№',
            render: (v: string, row: FfUnlinkedAssembly) => (
                <Link
                    href={`/p/${slug}/warehouse/assembly/${row.id}`}
                    title="Открыть заявку на сборку"
                    style={{ fontWeight: 600, color: 'var(--color-accent)', textDecoration: 'none' }}
                >
                    {v}
                </Link>
            ),
            exportValue: (row: FfUnlinkedAssembly) => row.number,
        },
        {
            key: 'status', label: 'Статус',
            render: (v: string) => (
                <span className="badge badge-secondary" style={{ fontSize: 11, padding: '2px 8px' }}>{statusLabel(v)}</span>
            ),
            exportValue: (row: FfUnlinkedAssembly) => statusLabel(row.status),
        },
        { key: 'brands', label: 'Бренд', render: (v: string | null) => v || '—' },
        { key: 'dest_warehouse', label: 'Склад сдачи', render: (v: string | null) => v || '—' },
        {
            key: 'total_qty', label: 'Товары', align: 'right',
            render: (v: number) => formatNumber(v, 0),
        },
        {
            key: 'estimated_ready_date', label: 'Дата готовности',
            render: (v: string | null) => (v ? formatDate(v) : '—'),
        },
        {
            key: 'created_at', label: 'Создана',
            render: (v: string) => formatDateTime(v),
            exportValue: (row: FfUnlinkedAssembly) => row.created_at,
        },
        {
            key: 'id', label: '', align: 'center',
            exportValue: () => '',
            render: (_: unknown, row: FfUnlinkedAssembly) => (
                <button className="btn btn-sm btn-primary" onClick={() => setLinkForAssembly(row)}>
                    Связать с ФФ
                </button>
            ),
        },
    ];

    return (
        <div style={{ marginTop: 32 }}>
            <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0, marginBottom: 4 }}>
                Наши заявки на сборку без связи с ФФ
            </h3>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                Активные сборки этого склада, которым ещё не сопоставлена заявка фулфилмента.
            </p>

            {loading ? (
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', padding: '8px 0' }}>Загрузка...</div>
            ) : error ? (
                <div style={{ fontSize: 13, color: 'var(--color-danger)', padding: '8px 0' }}>{error}</div>
            ) : rows.length === 0 ? (
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', padding: '8px 0' }}>
                    Все активные сборки этого склада уже связаны с ФФ
                </div>
            ) : (
                <>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                            <input type="checkbox" checked={allSelected} onChange={e => toggleAll(e.target.checked)} />
                            Выбрать все
                        </label>
                        {selectedAssemblies.length > 0 && (
                            <>
                                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Выбрано: {formatNumber(selectedAssemblies.length, 0)}</span>
                                <button className="btn btn-sm btn-primary" onClick={() => setBulkOpen(true)}>
                                    📦 Создать заявки на ФФ ({formatNumber(selectedAssemblies.length, 0)})
                                </button>
                                <button className="btn btn-sm btn-secondary" onClick={() => setSelected(new Set())}>Сбросить</button>
                            </>
                        )}
                    </div>
                    <TanStackDataTable
                        columns={cols}
                        data={rows}
                        emptyText="Все активные сборки этого склада уже связаны с ФФ"
                        emptyIcon="🔗"
                        exportName="ff_unlinked_assemblies"
                    />
                </>
            )}

            {linkForAssembly && (
                <FfReverseLinkModal
                    warehouseId={warehouseId}
                    assembly={linkForAssembly}
                    onClose={() => setLinkForAssembly(null)}
                    onLinked={ffNumber => {
                        onReverseLinked(ffNumber, linkForAssembly.number);
                        setLinkForAssembly(null);
                    }}
                />
            )}

            {bulkOpen && (
                <FfBulkCreateModal
                    warehouseId={warehouseId}
                    assemblies={selectedAssemblies}
                    onClose={() => setBulkOpen(false)}
                    onDone={createdCount => {
                        setBulkOpen(false);
                        setSelected(new Set());
                        onBulkCreated(createdCount);
                    }}
                />
            )}
        </div>
    );
}

/* ─── Модал: массовое создание заявок ФФ из выбранных сборок (push) ──────── */

function FfBulkCreateModal({ warehouseId, assemblies, onClose, onDone }: {
    warehouseId: number;
    assemblies: FfUnlinkedAssembly[];
    onClose: () => void;
    /** закрытие после создания: createdCount → родитель обновляет таблицы */
    onDone: (createdCount: number) => void;
}) {
    const plus3 = () => {
        const d = new Date();
        d.setDate(d.getDate() + 3);
        return d.toISOString().slice(0, 10);
    };
    const [deliveryType, setDeliveryType] = useState<'straight' | 'cross_dock'>('straight');
    const [collectionDate, setCollectionDate] = useState(plus3());
    const [comment, setComment] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState('');
    const [result, setResult] = useState<FfBulkCreateResult | null>(null);

    const totalQty = assemblies.reduce((s, a) => s + a.total_qty, 0);

    const submit = async () => {
        if (!collectionDate) return;
        setSubmitting(true);
        setError('');
        try {
            const res = await api.bulkCreateFfRequests(warehouseId, {
                assembly_request_ids: assemblies.map(a => a.id),
                collection_date: collectionDate,
                delivery_type: deliveryType,
                comment: comment.trim() || null,
            });
            setResult(res);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка массового создания заявок');
        }
        setSubmitting(false);
    };

    const STATUS_META: Record<FfBulkCreateAssemblyResult['status'], { label: string; cls: string }> = {
        created: { label: 'Создана', cls: 'badge-success' },
        deficit: { label: 'Нехватка остатков', cls: 'badge-warning' },
        no_warehouse: { label: 'Склад не подобран', cls: 'badge-warning' },
        already_linked: { label: 'Уже связана', cls: 'badge-secondary' },
        empty: { label: 'Нет позиций', cls: 'badge-secondary' },
        error: { label: 'Ошибка', cls: 'badge-danger' },
    };

    const lineDetail = (r: FfBulkCreateAssemblyResult): string => {
        if (r.status === 'created') return r.ff_number || r.external_id || '';
        if (r.status === 'deficit') {
            const head = r.deficit
                .slice(0, 3)
                .map(d => `${d.barcode}: ${formatNumber(d.needed, 0)}/${formatNumber(d.available, 0)}`)
                .join(', ');
            const rest = r.deficit.length - 3;
            return `дефицит (нужно/есть) — ${head}${rest > 0 ? ` и ещё ${formatNumber(rest, 0)}` : ''}`;
        }
        return r.message || '';
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-solid" onClick={e => e.stopPropagation()} style={{ width: 'min(480px, 92vw)' }}>
                <h2 className="modal-title">Создать заявки на ФФ ({formatNumber(assemblies.length, 0)})</h2>
                {!result ? (
                    <>
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 20, lineHeight: 1.5 }}>
                            Создаст <b>реальные заказы</b> «Доставка на склад МП» у фулфилмента для {formatNumber(assemblies.length, 0)} сборок ({formatNumber(totalQty, 0)} шт).
                            {' '}Склад МП и дата выгрузки подбираются <b>по каждой</b> сборке (склад WB / поставка FBW).
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                            <div className="form-group">
                                <label className="form-label">Тип поставки *</label>
                                <select className="form-input" value={deliveryType} onChange={e => setDeliveryType(e.target.value as 'straight' | 'cross_dock')}>
                                    <option value="straight">Прямая</option>
                                    <option value="cross_dock">Транзит (кросс-док)</option>
                                </select>
                            </div>
                            <div className="form-group">
                                <label className="form-label">Дата забора *</label>
                                <div style={{ display: 'flex', gap: 8, alignItems: 'stretch' }}>
                                    <input className="form-input" type="date" style={{ flex: 1, minWidth: 0 }} value={collectionDate} onChange={e => setCollectionDate(e.target.value)} />
                                    <button type="button" className="btn btn-secondary" style={{ flexShrink: 0, whiteSpace: 'nowrap' }} title="Сегодня + 3 дня" onClick={() => setCollectionDate(plus3())}>+3 дня</button>
                                </div>
                            </div>
                            <div className="form-group">
                                <label className="form-label">Комментарий</label>
                                <input className="form-input" value={comment} onChange={e => setComment(e.target.value)} placeholder="Заявка на сборку … (DDS)" />
                            </div>
                        </div>
                        {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 12 }}>{error}</div>}
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={onClose} disabled={submitting}>Отмена</button>
                            <button className="btn btn-primary" onClick={submit} disabled={submitting || !collectionDate}>
                                {submitting ? 'Создание…' : `Создать заявки (${formatNumber(assemblies.length, 0)})`}
                            </button>
                        </div>
                    </>
                ) : (
                    <>
                        <div style={{ fontSize: 14, marginBottom: 12 }}>
                            Создано: <b style={{ color: 'var(--color-success)' }}>{formatNumber(result.created_count, 0)}</b>
                            {result.failed_count > 0 && <> · не создано: <b style={{ color: 'var(--color-warning)' }}>{formatNumber(result.failed_count, 0)}</b></>}
                        </div>
                        <div style={{ maxHeight: 320, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
                            {result.results.map(r => {
                                const meta = STATUS_META[r.status];
                                return (
                                    <div key={r.assembly_request_id} style={{ display: 'flex', alignItems: 'baseline', gap: 8, fontSize: 13, flexWrap: 'wrap' }}>
                                        <span style={{ fontWeight: 600, minWidth: 90 }}>{r.assembly_number}</span>
                                        <span className={`badge ${meta.cls}`} style={{ fontSize: 11, padding: '2px 8px' }}>{meta.label}</span>
                                        <span style={{ color: 'var(--color-text-muted)' }}>{lineDetail(r)}</span>
                                    </div>
                                );
                            })}
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-primary" onClick={() => onDone(result.created_count)}>Готово</button>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}

/* ─── Реверс-линк-модал: выбрать несвязанную ФФ-заявку для нашей сборки ──── */

function FfReverseLinkModal({ warehouseId, assembly, onClose, onLinked }: {
    warehouseId: number;
    /** наша заявка сборки, для которой ищем ФФ-заявку */
    assembly: FfUnlinkedAssembly;
    onClose: () => void;
    /** успешно связали: номер выбранной ФФ-заявки (для тоста родителя) */
    onLinked: (ffNumber: string) => void;
}) {
    const [candidates, setCandidates] = useState<FfRequestRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [acting, setActing] = useState(false);
    const [search, setSearch] = useState('');
    // По умолчанию — только заявки ФФ с тем же складом сдачи, что у сборки
    const [showAllWh, setShowAllWh] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        api.getFulfillmentRequests(warehouseId, 'assembly')
            .then(r => {
                if (controller.signal.aborted) return;
                // Только несвязанные заявки ФФ (linked_number пуст)
                setCandidates(r.filter(row => !row.linked_number));
            })
            .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка загрузки заявок ФФ'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [warehouseId]);

    const otherWhCount = useMemo(
        () => candidates.filter(c => !whNamesMatch(assembly.dest_warehouse, c.dest_warehouse)).length,
        [candidates, assembly.dest_warehouse],
    );
    const filtered = useMemo(() => {
        const byWh = showAllWh ? candidates : candidates.filter(c => whNamesMatch(assembly.dest_warehouse, c.dest_warehouse));
        const q = search.trim().toLowerCase();
        if (!q) return byWh;
        return byWh.filter(c =>
            (c.number ?? '').toLowerCase().includes(q)
            || c.external_id.toLowerCase().includes(q)
            || (c.stage_title ?? '').toLowerCase().includes(q)
            || (c.dest_warehouse ?? '').toLowerCase().includes(q)
        );
    }, [candidates, search, showAllWh, assembly.dest_warehouse]);

    const handleLink = async (ff: FfRequestRow) => {
        setActing(true);
        setError('');
        try {
            await api.linkFulfillmentRequest(warehouseId, ff.id, { assembly_request_id: assembly.id });
            onLinked(ff.number || ff.external_id);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка связывания');
            setActing(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-wide modal-card-solid" onClick={e => e.stopPropagation()}>
                <h2 className="modal-title" style={{ marginBottom: 8 }}>
                    Связать сборку {assembly.number}
                    <span style={{ fontWeight: 500, color: 'var(--color-text-muted)' }}> · {formatNumber(assembly.total_qty, 0)} шт</span>
                </h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    Выберите несвязанную заявку фулфилмента этого склада
                    {assembly.dest_warehouse
                        ? <> со складом сдачи <b style={{ color: 'var(--color-text)' }}>{assembly.dest_warehouse}</b>:</>
                        : ':'}
                </p>

                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

                {loading ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                ) : candidates.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                        Нет несвязанных заявок ФФ у этого склада
                    </div>
                ) : (
                    <>
                        {otherWhCount > 0 && (
                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, marginBottom: 12, cursor: 'pointer' }}>
                                <input type="checkbox" checked={showAllWh} onChange={e => setShowAllWh(e.target.checked)} />
                                Показать все склады (ещё {formatNumber(otherWhCount, 0)})
                            </label>
                        )}
                        <input
                            type="text"
                            className="form-input ff-link-search"
                            placeholder="Поиск: номер, стадия, склад отгрузки"
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                        />
                        {filtered.length === 0 ? (
                            <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                                {!showAllWh && !search.trim() && otherWhCount > 0 ? (
                                    <>
                                        Нет заявок ФФ с этим складом сдачи.{' '}
                                        <button className="btn btn-sm btn-secondary" onClick={() => setShowAllWh(true)} style={{ marginTop: 8 }}>
                                            Показать все склады ({formatNumber(otherWhCount, 0)})
                                        </button>
                                    </>
                                ) : 'Ничего не найдено по запросу'}
                            </div>
                        ) : (
                            <div className="ff-link-list">
                                {filtered.map(c => (
                                    <div key={c.id} className="ff-link-row">
                                        <div className="ff-link-row-main">
                                            <div className="ff-link-row-head">
                                                <span className="ff-link-row-number">{c.number || c.external_id}</span>
                                                {(c.stage_title || c.status) && (
                                                    <span className="badge badge-secondary" style={{ fontSize: 11, padding: '2px 8px' }}>
                                                        {c.stage_title || c.status}
                                                    </span>
                                                )}
                                                <span className="ff-link-row-meta">
                                                    {c.external_created_at ? `${formatDate(c.external_created_at)} · ` : ''}
                                                    {c.total_qty != null ? `${formatNumber(c.total_qty, 0)} шт` : '—'}
                                                    {c.dest_warehouse ? ` · ${c.dest_warehouse}` : ''}
                                                </span>
                                            </div>
                                        </div>
                                        <button className="btn btn-sm btn-primary" onClick={() => handleLink(c)} disabled={acting}>
                                            {acting ? '...' : 'Выбрать'}
                                        </button>
                                    </div>
                                ))}
                            </div>
                        )}
                    </>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                </div>
            </div>
        </div>
    );
}

/* ─── Модал «Связать вскрытие»: поступление-пара для возврата (migfull) ──── */

function FfRepackLinkModal({ warehouseId, request, onClose, onLinked }: {
    warehouseId: number;
    /** возврат (kind=return) без пары, для которого подбираем поступление */
    request: FfRequestRow;
    onClose: () => void;
    /** успешно связали: номер выбранного поступления (для тоста родителя) */
    onLinked: (pairNumber: string) => void;
}) {
    const [data, setData] = useState<FfRepackCandidatesOut | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [acting, setActing] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        api.getFfRepackCandidates(warehouseId, request.id)
            .then(r => { if (!controller.signal.aborted) setData(r); })
            .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка загрузки кандидатов'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [warehouseId, request.id]);

    const handleLink = async (c: FfRepackCandidate) => {
        setActing(true);
        setError('');
        try {
            await api.linkFfRepackPair(warehouseId, request.id, c.id);
            onLinked(c.number || String(c.id));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка связывания пары');
            setActing(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-wide modal-card-solid" onClick={e => e.stopPropagation()}>
                <h2 className="modal-title" style={{ marginBottom: 8 }}>
                    Вскрытие коробов — {data?.return_number || request.number || request.external_id}
                </h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    Возврат: {data == null
                        ? '…'
                        : (data.return_units != null
                            ? <b style={{ color: 'var(--color-text)' }}>{formatNumber(data.return_units, 0)} шт россыпи</b>
                            : 'состав не разрешён')}
                    {' '}· выберите поступление-пару — вместе они означают переупаковку, сток не двигается.
                </p>

                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 12 }}>{error}</div>}

                {loading ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                ) : (data?.candidates.length ?? 0) === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                        Кандидатов не найдено (окно ±14 дней)
                    </div>
                ) : (
                    <div className="ff-link-list">
                        {data!.candidates.map(c => (
                            <div key={c.id} className="ff-link-row">
                                <div className="ff-link-row-main">
                                    <div className="ff-link-row-head">
                                        <span className="ff-link-row-number">{c.number || '—'}</span>
                                        {c.status && (
                                            <span className="badge badge-secondary" style={{ fontSize: 11, padding: '2px 8px' }}>{c.status}</span>
                                        )}
                                        {c.exact ? (
                                            <span
                                                className="badge badge-success"
                                                style={{ fontSize: 11, padding: '2px 8px' }}
                                                title="Состав совпал точно — такой кандидат авто-матчер пометил бы сам"
                                            >
                                                точное
                                            </span>
                                        ) : (
                                            <span className="badge badge-info" style={{ fontSize: 11, padding: '2px 8px' }} title="Пересечение состава с возвратом, % от большей стороны">
                                                совпадение {formatNumber(c.overlap_pct, 0)}%
                                            </span>
                                        )}
                                        <span className="ff-link-row-meta">
                                            {c.external_created_at ? `${formatDate(c.external_created_at)} · ` : ''}
                                            {formatNumber(c.units_sum, 0)} шт
                                        </span>
                                    </div>
                                </div>
                                <button className="btn btn-sm btn-primary" onClick={() => handleLink(c)} disabled={acting}>
                                    {acting ? '...' : 'Связать'}
                                </button>
                            </div>
                        ))}
                    </div>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                </div>
            </div>
        </div>
    );
}
