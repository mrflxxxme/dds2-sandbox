'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatNumber, formatDate, formatDateTime } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type {
    Warehouse, InboundReceipt, OutboundShipment,
    WarehouseStockRow, StockMovement, StockTransfer, DeliveryTimesResponse,
    DefectMarkOperation, VehicleStatus,
    FulfillmentStatus, FulfillmentProviderId, FfStocksResponse, FfStockRow, FfRequestRow, FfRequestKind,
} from '@/types/api';
import type { Column } from '@/components/DataTable';
import { FF_LINKED_STATUS_LABELS, FfLinkModal, ffStageBadge } from './ff-shared';

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

type WarehouseTab = 'all' | 'receipts' | 'shipments' | 'transfers' | 'stock' | 'defects' | 'delivery' | 'requisites' | 'ff-stocks' | 'ff-assembly' | 'ff-inbound';
const WAREHOUSE_TABS: WarehouseTab[] = ['all', 'receipts', 'shipments', 'transfers', 'stock', 'defects', 'delivery', 'requisites', 'ff-stocks', 'ff-assembly', 'ff-inbound'];

export default function WarehouseDetailPage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const slug = params.slug as string;
    const warehouseId = Number(params.id);
    const [tab, setTab] = useState<WarehouseTab>('receipts');

    // Активная вкладка из ?tab= (back-ссылки подстраниц).
    // useSearchParams пуст на первом рендере до гидратации — реагируем только на непустое значение, без редиректов.
    useEffect(() => {
        const raw = searchParams.get('tab') ?? '';
        if (raw && (WAREHOUSE_TABS as string[]).includes(raw)) setTab(raw as WarehouseTab);
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

    // Если интеграцию отключили, а открыта ФФ-вкладка — возвращаемся на «Реквизиты».
    // Пока статус не загружен (ffStatus === null) — не сбрасываем: иначе ?tab=ff-* проигрывает гонку загрузке статуса.
    useEffect(() => {
        if (ffStatus && !ffStatus.connected && (tab === 'ff-stocks' || tab === 'ff-assembly' || tab === 'ff-inbound')) {
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
        { key: 'transfers' as const, label: 'Перемещения', count: transferCount },
        { key: 'stock' as const, label: 'Остатки и статистика' },
        { key: 'defects' as const, label: 'Брак', count: defectCount },
        { key: 'delivery' as const, label: 'Время доставки' },
        { key: 'all' as const, label: 'История движений' },
        ...(ffConnected ? [
            { key: 'ff-stocks' as const, label: 'ФФ остатки' },
            { key: 'ff-assembly' as const, label: 'ФФ сборка' },
            { key: 'ff-inbound' as const, label: 'ФФ приёмки' },
        ] : []),
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
            <ExpectedVehicles warehouseId={warehouseId} slug={slug} />

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
            {tab === 'transfers' && (
                <TransfersTab
                    warehouseId={warehouseId}
                    onCountChange={setTransferCount}
                />
            )}
            {tab === 'stock' && <StockTab warehouseId={warehouseId} />}
            {tab === 'defects' && <DefectsTab warehouseId={warehouseId} onCountChange={setDefectCount} />}
            {tab === 'delivery' && <DeliveryTab warehouseId={warehouseId} />}
            {tab === 'ff-stocks' && ffConnected && <FfStocksTab warehouseId={warehouseId} />}
            {tab === 'ff-assembly' && ffConnected && <FfRequestsTab warehouseId={warehouseId} slug={slug} kind="assembly" />}
            {tab === 'ff-inbound' && ffConnected && <FfRequestsTab warehouseId={warehouseId} slug={slug} kind="inbound" />}
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
        </div>
    );
}

/* ─── Fulfillment integration (skladbot / wmscelicom) — блок в «Реквизитах» ── */

const FF_EXPIRY_WARNING_MS = 30 * 24 * 60 * 60 * 1000; // 30 дней

const FF_PROVIDER_LABELS: Record<string, string> = {
    skladbot: 'skladbot.ru',
    wmscelicom: 'Целиком (WMS Celicom)',
};

function FulfillmentSection({ warehouseId, status, onChanged }: {
    warehouseId: number;
    status: FulfillmentStatus | null;
    onChanged: () => void;
}) {
    const [provider, setProvider] = useState<FulfillmentProviderId>('skladbot');
    const [token, setToken] = useState('');
    const [baseUrl, setBaseUrl] = useState('');
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
        setBusy(true);
        setError('');
        setSyncMsg('');
        try {
            await api.connectFulfillment(warehouseId, {
                provider,
                token: token.trim(),
                base_url: provider === 'wmscelicom' ? baseUrl.trim() : null,
            });
            setToken('');
            setBaseUrl('');
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
                            ? 'Подключите личный кабинет skladbot.ru по seller-токену — появятся вкладки «ФФ остатки», «ФФ сборка» и «ФФ приёмки» с данными фулфилмента.'
                            : 'Подключите инстанс «Целиком» (WMS Celicom) по API-токену и адресу инстанса — появятся вкладки «ФФ остатки», «ФФ сборка» (отгрузки FBO) и «ФФ приёмки».'}
                    </p>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', maxWidth: 860, flexWrap: 'wrap' }}>
                        <div className="form-group" style={{ width: 220, marginBottom: 0 }}>
                            <label className="form-label">Провайдер</label>
                            <select
                                className="form-input"
                                value={provider}
                                onChange={e => setProvider(e.target.value as FulfillmentProviderId)}
                            >
                                <option value="skladbot">skladbot.ru</option>
                                <option value="wmscelicom">Целиком (WMS Celicom)</option>
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
                        <div className="form-group" style={{ flex: 1, minWidth: 220, marginBottom: 0 }}>
                            <label className="form-label">Токен</label>
                            <input
                                className="form-input"
                                type="password"
                                value={token}
                                onChange={e => setToken(e.target.value)}
                                placeholder={provider === 'skladbot' ? 'Seller-токен skladbot.ru' : 'API-токен Целиком'}
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

function ExpectedVehicles({ warehouseId, slug }: { warehouseId: number; slug: string }) {
    const router = useRouter();
    const [vehicles, setVehicles] = useState<any[]>([]);

    const loadVehicles = useCallback(() => {
        api.getExpectedVehicles(warehouseId).then(setVehicles).catch(() => {});
    }, [warehouseId]);

    useEffect(() => { loadVehicles(); }, [loadVehicles]);

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
                        </div>
                    );
                })}
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

/* ─── Tab: ФФ остатки ───────────────────────────────────────────────────── */

type FfStockQuickFilter = 'diff' | 'unmatched' | null;

function FfStocksTab({ warehouseId }: { warehouseId: number }) {
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
        { key: 'ff_good', label: 'ФФ годный', align: 'right', format: 'number' },
        { key: 'ff_reserve', label: 'ФФ резерв', align: 'right', format: 'number' },
        {
            key: 'ff_defect', label: 'ФФ брак', align: 'right',
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
        { label: 'Брак ФФ', value: totals.ff_defect, color: totals.ff_defect > 0 ? 'var(--color-warning)' : undefined },
        { label: 'У нас', value: totals.our_quantity },
        { label: 'Расхождение', value: totals.diff, color: totals.diff > 0 ? 'var(--color-success)' : totals.diff < 0 ? 'var(--color-danger)' : undefined, filter: 'diff' },
        { label: 'Несматчено', value: totals.unmatched, color: totals.unmatched > 0 ? 'var(--color-warning)' : undefined, filter: 'unmatched' },
    ] : [];

    return (
        <>
            {totals && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12, marginBottom: 16 }}>
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
                <select className="input" style={{ maxWidth: 200, fontSize: 13 }} value={subjectFilter} onChange={e => setSubjectFilter(e.target.value)}>
                    <option value="">Предмет: Все</option>
                    {(data?.subjects ?? []).map(s => <option key={s} value={s}>{s}</option>)}
                </select>
                <select className="input" style={{ maxWidth: 200, fontSize: 13 }} value={brandFilter} onChange={e => setBrandFilter(e.target.value)}>
                    <option value="">Бренд: Все</option>
                    {(data?.brands ?? []).map(b => <option key={b} value={b}>{b}</option>)}
                </select>
                <input
                    className="input"
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

/* ─── Tabs: ФФ сборка / ФФ приёмки ──────────────────────────────────────── */

function FfRequestsTab({ warehouseId, slug, kind }: { warehouseId: number; slug: string; kind: FfRequestKind }) {
    const [rows, setRows] = useState<FfRequestRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actingId, setActingId] = useState<number | null>(null);

    // Модал «Связать» (общий пикер — ff-shared)
    const [linkFor, setLinkFor] = useState<FfRequestRow | null>(null);

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        api.getFulfillmentRequests(warehouseId, kind)
            .then(r => { if (!controller.signal.aborted) setRows(r); })
            .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [warehouseId, kind]);

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

    const linkedStatusLabel = (s: string | null) => (s ? (FF_LINKED_STATUS_LABELS[s] || s) : '');

    const cols: Column[] = [
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
        { key: 'type_name', label: 'Тип', render: (v: string | null) => v || '—' },
        {
            key: 'stage_title', label: 'Стадия',
            render: (v: string | null, row: FfRequestRow) => (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span>{v || row.status || '—'}</span>
                    {ffStageBadge(row)}
                </span>
            ),
            exportValue: (row: FfRequestRow) => row.stage_title || row.status || '',
        },
        {
            key: 'linked_number', label: 'Связь',
            render: (_: unknown, row: FfRequestRow) => {
                const acting = actingId === row.id;
                if (row.linked_number) {
                    return (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                            <span style={{ fontWeight: 600 }}>{row.linked_number}</span>
                            {row.linked_status && (
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{linkedStatusLabel(row.linked_status)}</span>
                            )}
                            <button className="btn btn-sm btn-secondary" onClick={() => handleUnlink(row)} disabled={acting}>
                                {acting ? '...' : 'Отвязать'}
                            </button>
                        </span>
                    );
                }
                return (
                    <button className="btn btn-sm btn-secondary" onClick={() => setLinkFor(row)} disabled={acting}>
                        Связать
                    </button>
                );
            },
            exportValue: (row: FfRequestRow) => row.linked_number
                ? `${row.linked_number}${row.linked_status ? ` (${linkedStatusLabel(row.linked_status)})` : ''}`
                : '',
        },
    ];

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error && rows.length === 0) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>;

    return (
        <>
            {error && <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>}

            <TanStackDataTable
                columns={cols}
                data={rows}
                emptyText={kind === 'assembly'
                    ? 'Нет заявок на сборку — выполните синхронизацию во вкладке «Реквизиты»'
                    : 'Нет заявок на приёмку — выполните синхронизацию во вкладке «Реквизиты»'}
                emptyIcon={kind === 'assembly' ? '🧰' : '📥'}
                exportName={kind === 'assembly' ? 'ff_assembly_requests' : 'ff_inbound_requests'}
            />

            {linkFor && (
                <FfLinkModal
                    warehouseId={warehouseId}
                    kind={kind}
                    request={linkFor}
                    onClose={() => setLinkFor(null)}
                    onLinked={updated => {
                        setRows(prev => prev.map(r => r.id === updated.id ? updated : r));
                        setLinkFor(null);
                    }}
                />
            )}
        </>
    );
}
