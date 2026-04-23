'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate, formatDateTime, formatNumber } from '@/lib/utils';
import KpiCard from '@/components/KpiCard';
import TabLayout from '@/components/TabLayout';
import TanStackDataTable from '@/components/TanStackDataTable';
import Toast from '@/components/Toast';
import type { Column } from '@/components/DataTable';
import type {
    Warehouse,
    WbGoodsReturn,
    WbGoodsReturnPvzGroup,
    WbGoodsReturnSummary,
    WbGoodsReturnUiState,
} from '@/types/api';

// ─── Configuration ──────────────────────────────────────────────────────

type TabKey = 'pvz' | 'in_transit' | 'history';

const TABS = [
    { key: 'pvz', label: 'На ПВЗ' },
    { key: 'in_transit', label: 'В пути на склад' },
    { key: 'history', label: 'История' },
];

const UI_STATE_LABEL: Record<WbGoodsReturnUiState, string> = {
    ready_for_pickup: 'Готово к выдаче',
    in_transit_to_pvz: 'Едет на ПВЗ',
    pickup_planned: 'Запланирована приёмка',
    picked_up_pending_receipt: 'Забрали, ждёт приёмки',
    received: 'Принят',
    expired: 'Просрочен',
    picked_without_receipt: 'Без оформления',
    archived: 'В архиве',
};

const UI_STATE_BADGE: Record<WbGoodsReturnUiState, string> = {
    ready_for_pickup: 'badge-success',
    in_transit_to_pvz: 'badge-info',
    pickup_planned: 'badge-warning',
    picked_up_pending_receipt: 'badge-warning',
    received: 'badge-success',
    expired: 'badge-danger',
    picked_without_receipt: 'badge-danger',
    archived: 'badge-secondary',
};

const ACTIONABLE_STATES: ReadonlyArray<WbGoodsReturnUiState> = [
    'ready_for_pickup',
    'picked_without_receipt',
];

function isActionable(state: WbGoodsReturnUiState): boolean {
    return ACTIONABLE_STATES.includes(state);
}

function daysBetween(dt: string | null | undefined): number | null {
    if (!dt) return null;
    const now = new Date();
    const target = new Date(dt);
    if (Number.isNaN(target.getTime())) return null;
    const diff = target.getTime() - now.getTime();
    return Math.ceil(diff / (1000 * 60 * 60 * 24));
}

function formatProduct(item: WbGoodsReturn): string {
    const parts: string[] = [];
    if (item.brand) parts.push(item.brand);
    if (item.subject_name) parts.push(item.subject_name);
    if (parts.length === 0 && item.nm_id) parts.push(`nm ${item.nm_id}`);
    return parts.join(' · ') || '—';
}

function formatArticle(item: WbGoodsReturn): string {
    // артикул продавца (из Nomenclature) приоритетнее WB-артикула
    if (item.article_seller) return item.article_seller;
    if (item.nm_id) return String(item.nm_id);
    return '—';
}

// ─── Component ──────────────────────────────────────────────────────────

export default function WbReturnsPage() {
    const params = useParams();
    const slug = params.slug as string;

    const [tab, setTab] = useState<TabKey>('pvz');

    // Summary + groups (tab pvz)
    const [summary, setSummary] = useState<WbGoodsReturnSummary | null>(null);
    const [pvzGroups, setPvzGroups] = useState<WbGoodsReturnPvzGroup[]>([]);
    const [expandedGroupId, setExpandedGroupId] = useState<number | 'unknown' | null>(null);

    // Items list (tab in_transit / history)
    const [items, setItems] = useState<WbGoodsReturn[]>([]);

    // Common state
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' | 'info' } | null>(null);

    // Selection for receipt creation
    const [selectedSrids, setSelectedSrids] = useState<Set<string>>(new Set());

    // Modal state
    const [showReceiptModal, setShowReceiptModal] = useState(false);
    const [modalWarehouseId, setModalWarehouseId] = useState<number | null>(null);
    const [modalComment, setModalComment] = useState('');
    const [creatingReceipt, setCreatingReceipt] = useState(false);

    // Archive state
    const [archiving, setArchiving] = useState(false);

    // Sync state
    const [syncing, setSyncing] = useState(false);

    // Search (simple client-side search for history tab)
    const [search, setSearch] = useState('');

    // Pagination для in_transit / history
    const PAGE_SIZE = 200;
    const [page, setPage] = useState(0);
    const [total, setTotal] = useState(0);

    // ─── Load warehouses once ─────────────────────────────────────────
    useEffect(() => {
        api.getWarehouses()
            .then(ws => setWarehouses(ws.filter(w => w.is_active)))
            .catch(() => { /* soft fail — modal shows empty list */ });
    }, []);

    // ─── Load data for current tab ────────────────────────────────────
    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            if (tab === 'pvz') {
                const [sum, groups] = await Promise.all([
                    api.getWbReturnsSummary(),
                    api.getWbReturnsPvzGroups(),
                ]);
                setSummary(sum);
                setPvzGroups(groups);
            } else if (tab === 'in_transit') {
                const resp = await api.getWbReturns({
                    tab: 'in_transit',
                    search: search || undefined,
                    limit: PAGE_SIZE,
                    offset: page * PAGE_SIZE,
                });
                setItems(resp.items);
                setTotal(resp.total);
            } else {
                const resp = await api.getWbReturns({
                    tab: 'history',
                    search: search || undefined,
                    limit: PAGE_SIZE,
                    offset: page * PAGE_SIZE,
                });
                setItems(resp.items);
                setTotal(resp.total);
            }
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [tab, search, page]);

    useEffect(() => {
        load();
    }, [load]);

    // Clear selection + pagination when switching tabs or search
    useEffect(() => {
        setSelectedSrids(new Set());
        setExpandedGroupId(null);
        setPage(0);
    }, [tab, search]);

    // ─── Selection handlers ───────────────────────────────────────────
    const toggleSrid = (srid: string) => {
        setSelectedSrids(prev => {
            const next = new Set(prev);
            if (next.has(srid)) next.delete(srid);
            else next.add(srid);
            return next;
        });
    };

    const clearSelection = () => setSelectedSrids(new Set());

    // ─── Receipt modal ────────────────────────────────────────────────
    const openReceiptModal = () => {
        if (selectedSrids.size === 0) return;
        // Preselect first active warehouse if available
        setModalWarehouseId(warehouses[0]?.id ?? null);
        setModalComment('');
        setShowReceiptModal(true);
    };

    const closeReceiptModal = () => {
        if (creatingReceipt) return;
        setShowReceiptModal(false);
    };

    const handleCreateReceipt = async () => {
        if (!modalWarehouseId) {
            setToast({ message: 'Выберите склад назначения', type: 'error' });
            return;
        }
        if (selectedSrids.size === 0) return;
        setCreatingReceipt(true);
        try {
            const result = await api.createReceiptFromReturns({
                warehouse_id: modalWarehouseId,
                srids: Array.from(selectedSrids),
                comment: modalComment.trim() || undefined,
            });
            const skipped = result.skipped_srids?.length ?? 0;
            const msg = skipped > 0
                ? `Приёмка ${result.receipt_number}: ${result.items_count} товаров, ${skipped} без штрихкода (без позиций)`
                : `Приёмка ${result.receipt_number} создана (${result.items_count} товаров)`;
            setToast({
                message: msg,
                type: skipped > 0 ? 'info' : 'success',
            });
            setShowReceiptModal(false);
            setSelectedSrids(new Set());
            await load();
        } catch (e: unknown) {
            setToast({
                message: e instanceof Error ? e.message : 'Ошибка создания приёмки',
                type: 'error',
            });
        } finally {
            setCreatingReceipt(false);
        }
    };

    // ─── Archive ──────────────────────────────────────────────────────
    const archiveSrids = async (srids: string[]) => {
        if (srids.length === 0) return;
        const noun = srids.length === 1 ? 'позицию' : 'позиций';
        if (!confirm(`Отправить в архив ${srids.length} ${noun}? Они будут скрыты из «На ПВЗ» и появятся в «Истории».`)) return;
        setArchiving(true);
        try {
            const res = await api.archiveWbReturns({ srids });
            const skipped = res.skipped_srids?.length ?? 0;
            const msg = skipped > 0
                ? `Архивировано ${res.archived_count}, пропущено ${skipped} (не соответствуют «забрали без оформления»)`
                : `Архивировано ${res.archived_count}`;
            setToast({ message: msg, type: skipped > 0 ? 'info' : 'success' });
            setSelectedSrids(new Set());
            await load();
        } catch (e: unknown) {
            setToast({
                message: e instanceof Error ? e.message : 'Ошибка архивации',
                type: 'error',
            });
        } finally {
            setArchiving(false);
        }
    };

    // ─── Sync ─────────────────────────────────────────────────────────
    const handleSync = async () => {
        setSyncing(true);
        try {
            const result = await api.syncWbReturns({});
            setToast({
                message: `Синхронизация завершена: получено ${result.rows_fetched}, обновлено ${result.rows_upserted}`,
                type: 'success',
            });
            await load();
        } catch (e: unknown) {
            setToast({
                message: e instanceof Error ? e.message : 'Ошибка синхронизации',
                type: 'error',
            });
        } finally {
            setSyncing(false);
        }
    };

    // ─── Derived values ───────────────────────────────────────────────
    const totalInTransitKpi = useMemo(() => {
        if (!summary) return 0;
        return summary.pickup_planned + summary.picked_up_pending_receipt;
    }, [summary]);

    const hasSoonExpires = !!(summary && summary.soon_expires > 0);
    const hasPickedWithoutReceipt = !!(summary && summary.picked_without_receipt > 0);

    // ─── Render ───────────────────────────────────────────────────────
    return (
        <div className="animate-in">
            {/* Page header */}
            <div className="page-header">
                <div>
                    <h1 className="page-title">Возвраты на ПВЗ</h1>
                    <p className="page-subtitle">
                        Покупательские возвраты, ожидающие получения с пунктов выдачи
                    </p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={handleSync}
                        disabled={syncing}
                    >
                        {syncing ? 'Синхронизация...' : 'Синхронизировать с WB'}
                    </button>
                </div>
            </div>

            {/* KPI row */}
            {summary && (
                <div className="stats-grid" style={{ marginBottom: 20 }}>
                    <KpiCard
                        label="Готово к выдаче"
                        value={summary.ready_for_pickup}
                        icon="📦"
                        color={hasSoonExpires ? 'var(--color-warning)' : 'var(--color-accent)'}
                        sub={hasSoonExpires ? `${summary.soon_expires} с дедлайном` : undefined}
                    />
                    <KpiCard
                        label="Скоро истечёт"
                        value={summary.soon_expires}
                        icon="⏰"
                        color="var(--color-danger)"
                        sub="до 3 дней"
                    />
                    <KpiCard
                        label="В пути на склад"
                        value={totalInTransitKpi}
                        icon="🚚"
                        color="var(--color-accent)"
                        sub={`${summary.pickup_planned} запланировано · ${summary.picked_up_pending_receipt} ждёт приёмки`}
                    />
                    <KpiCard
                        label="Забрали без оформления"
                        value={summary.picked_without_receipt}
                        icon="⚠️"
                        color={hasPickedWithoutReceipt ? 'var(--color-danger)' : 'var(--color-text-muted)'}
                        sub={hasPickedWithoutReceipt ? 'требуется оформить' : 'всё ок'}
                    />
                </div>
            )}

            {/* Tabs */}
            <TabLayout
                tabs={TABS}
                active={tab}
                onChange={key => setTab(key as TabKey)}
            />

            {/* Search for in_transit / history */}
            {(tab === 'in_transit' || tab === 'history') && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 16 }}>
                    <input
                        className="form-input"
                        placeholder="Поиск по SRID, штрихкоду, бренду, артикулу..."
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                    />
                </div>
            )}

            {/* Loading / Error */}
            {loading && (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>
                    Загрузка...
                </div>
            )}
            {!loading && error && (
                <div className="glass-card" style={{ padding: 16, color: 'var(--color-danger)' }}>
                    {error}
                </div>
            )}

            {/* Content by tab */}
            {!loading && !error && tab === 'pvz' && (
                <PvzTab
                    groups={pvzGroups}
                    selectedSrids={selectedSrids}
                    onToggleSrid={toggleSrid}
                    expandedId={expandedGroupId}
                    onExpand={setExpandedGroupId}
                    onArchive={(srid) => archiveSrids([srid])}
                    archiving={archiving}
                />
            )}

            {!loading && !error && tab === 'in_transit' && (
                <InTransitTab items={items} slug={slug} />
            )}

            {!loading && !error && tab === 'history' && (
                <HistoryTab items={items} />
            )}

            {/* Pagination for in_transit / history */}
            {!loading && !error && (tab === 'in_transit' || tab === 'history') && total > PAGE_SIZE && (
                <div
                    className="glass-card"
                    style={{
                        padding: 12,
                        marginTop: 16,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: 12,
                    }}
                >
                    <span style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                        {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} из {formatNumber(total)}
                    </span>
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => setPage(p => Math.max(0, p - 1))}
                            disabled={page === 0}
                        >
                            ← Назад
                        </button>
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => setPage(p => p + 1)}
                            disabled={(page + 1) * PAGE_SIZE >= total}
                        >
                            Вперёд →
                        </button>
                    </div>
                </div>
            )}

            {/* Sticky selection bar */}
            {tab === 'pvz' && selectedSrids.size > 0 && (
                <div
                    className="glass-card"
                    style={{
                        position: 'fixed',
                        bottom: 24,
                        left: '50%',
                        transform: 'translateX(-50%)',
                        padding: '12px 20px',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 16,
                        zIndex: 100,
                        boxShadow: '0 12px 32px rgba(0,0,0,0.12)',
                    }}
                >
                    <span style={{ fontWeight: 600 }}>Выбрано: {selectedSrids.size}</span>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={clearSelection}
                        disabled={archiving || creatingReceipt}
                    >
                        Очистить
                    </button>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => archiveSrids(Array.from(selectedSrids))}
                        disabled={archiving || creatingReceipt}
                        title="Применимо только к «Забрали без оформления» — остальные будут пропущены"
                    >
                        {archiving ? 'Архивация...' : 'В архив'}
                    </button>
                    <button
                        className="btn btn-primary btn-sm"
                        onClick={openReceiptModal}
                        disabled={archiving || creatingReceipt}
                    >
                        Оформить приёмку
                    </button>
                </div>
            )}

            {/* Receipt creation modal */}
            {showReceiptModal && (
                <div className="modal-overlay" onClick={closeReceiptModal}>
                    <div
                        className="modal-card"
                        onClick={e => e.stopPropagation()}
                        style={{
                            maxWidth: 520,
                            background: 'var(--color-bg)',
                            backdropFilter: 'none',
                            WebkitBackdropFilter: 'none',
                        }}
                    >
                        <h2 className="modal-title">
                            Оформить приёмку ({selectedSrids.size} товаров)
                        </h2>
                        <p style={{ color: 'var(--color-text-muted)', fontSize: 14, marginBottom: 16 }}>
                            Выбранные товары будут оформлены как приёмка со статусом «Ожидается».
                            Приёмка автоматически будет помечена как брак.
                        </p>
                        <div className="form-group">
                            <label className="form-label">Склад назначения *</label>
                            <select
                                className="form-input"
                                value={modalWarehouseId ?? ''}
                                onChange={e => setModalWarehouseId(
                                    e.target.value ? parseInt(e.target.value, 10) : null,
                                )}
                                disabled={creatingReceipt}
                            >
                                <option value="">— выберите склад —</option>
                                {warehouses.map(w => (
                                    <option key={w.id} value={w.id}>{w.name}</option>
                                ))}
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label">Комментарий</label>
                            <textarea
                                className="form-input"
                                rows={3}
                                value={modalComment}
                                onChange={e => setModalComment(e.target.value)}
                                placeholder="Необязательно"
                                disabled={creatingReceipt}
                            />
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button
                                className="btn btn-secondary"
                                onClick={closeReceiptModal}
                                disabled={creatingReceipt}
                            >
                                Отмена
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={handleCreateReceipt}
                                disabled={creatingReceipt || !modalWarehouseId}
                            >
                                {creatingReceipt ? 'Создание...' : 'Создать приёмку'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {toast && (
                <Toast
                    message={toast.message}
                    type={toast.type}
                    onClose={() => setToast(null)}
                />
            )}
        </div>
    );
}

// ─── Tab "PVZ" — grouped by pickup point ────────────────────────────────

interface PvzTabProps {
    groups: WbGoodsReturnPvzGroup[];
    selectedSrids: Set<string>;
    onToggleSrid: (srid: string) => void;
    expandedId: number | 'unknown' | null;
    onExpand: (id: number | 'unknown' | null) => void;
    onArchive: (srid: string) => void;
    archiving: boolean;
}

function PvzTab({ groups, selectedSrids, onToggleSrid, expandedId, onExpand, onArchive, archiving }: PvzTabProps) {
    if (groups.length === 0) {
        return (
            <div className="empty-state glass-card" style={{ padding: 64, textAlign: 'center' }}>
                <div style={{ fontSize: 48, marginBottom: 16 }}>📦</div>
                <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>
                    Возвратов на ПВЗ нет
                </div>
                <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                    Данные подтягиваются из Wildberries. Нажмите «Синхронизировать с WB», чтобы обновить.
                </div>
            </div>
        );
    }

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {groups.map(group => {
                const groupKey: number | 'unknown' = group.dst_office_id ?? 'unknown';
                const isOpen = expandedId === groupKey;

                const daysToExpire = daysBetween(group.nearest_expired_dt);
                const urgent = daysToExpire != null && daysToExpire <= 3;

                return (
                    <div key={String(groupKey)} className="glass-card" style={{ padding: 16 }}>
                        <div
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                gap: 12,
                                cursor: 'pointer',
                            }}
                            onClick={() => onExpand(isOpen ? null : groupKey)}
                        >
                            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, minWidth: 0 }}>
                                <span style={{ fontSize: 20 }}>📍</span>
                                <div style={{ minWidth: 0, flex: 1 }}>
                                    <div style={{ fontWeight: 600, fontSize: 15 }}>
                                        {group.dst_office_address || 'Адрес не указан'}
                                    </div>
                                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                        {group.dst_office_id != null ? `ID: ${group.dst_office_id} · ` : ''}
                                        {group.total} шт · готовы: {group.ready_count} · в пути: {group.in_transit_count}
                                        {group.picked_without_receipt_count > 0 && (
                                            <> · <span style={{ color: 'var(--color-danger)', fontWeight: 600 }}>
                                                забрали без оформления: {group.picked_without_receipt_count}
                                            </span></>
                                        )}
                                    </div>
                                </div>
                                {group.picked_without_receipt_count > 0 && (
                                    <span className="badge badge-danger">
                                        ⚠ {group.picked_without_receipt_count} без приёмки
                                    </span>
                                )}
                                {urgent && group.nearest_expired_dt && (
                                    <span className="badge badge-danger">
                                        Дедлайн: {formatDate(group.nearest_expired_dt)}
                                    </span>
                                )}
                            </div>
                            <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                {isOpen ? '▼' : '▶'}
                            </span>
                        </div>

                        {isOpen && (
                            <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 20 }}>
                                <PvzSection
                                    title="Готовые к выдаче"
                                    icon="📦"
                                    accentColor="var(--color-success)"
                                    emptyHint="— нет"
                                    items={group.items.filter(i => i.ui_state === 'ready_for_pickup')}
                                    columns={['checkbox', 'product', 'article', 'size', 'srid', 'ready_from', 'expires']}
                                    selectedSrids={selectedSrids}
                                    onToggleSrid={onToggleSrid}
                                    onArchive={onArchive}
                                    archiving={archiving}
                                />
                                <PvzSection
                                    title="В пути на ПВЗ"
                                    icon="🚛"
                                    accentColor="var(--color-accent)"
                                    emptyHint="— нет"
                                    items={group.items.filter(i => i.ui_state === 'in_transit_to_pvz')}
                                    columns={['product', 'article', 'size', 'srid', 'expires']}
                                    selectedSrids={selectedSrids}
                                    onToggleSrid={onToggleSrid}
                                    onArchive={onArchive}
                                    archiving={archiving}
                                />
                                <PvzSection
                                    title="Забрали без оформления"
                                    icon="⚠️"
                                    accentColor="var(--color-danger)"
                                    emptyHint="— нет"
                                    items={group.items.filter(i => i.ui_state === 'picked_without_receipt')}
                                    columns={['checkbox', 'product', 'article', 'size', 'srid', 'picked_at', 'archive_btn']}
                                    selectedSrids={selectedSrids}
                                    onToggleSrid={onToggleSrid}
                                    onArchive={onArchive}
                                    archiving={archiving}
                                />
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
}

// ─── PvzSection — мини-таблица под конкретный ui_state внутри группы ПВЗ ──

type PvzColumn =
    | 'checkbox'
    | 'product'
    | 'article'
    | 'size'
    | 'srid'
    | 'ready_from'
    | 'picked_at'
    | 'expires'
    | 'archive_btn';

interface PvzSectionProps {
    title: string;
    icon: string;
    accentColor: string;
    emptyHint: string;
    items: WbGoodsReturn[];
    columns: PvzColumn[];
    selectedSrids: Set<string>;
    onToggleSrid: (srid: string) => void;
    onArchive: (srid: string) => void;
    archiving: boolean;
}

function PvzSection({
    title, icon, accentColor, emptyHint,
    items, columns, selectedSrids, onToggleSrid, onArchive, archiving,
}: PvzSectionProps) {
    const hasCheckbox = columns.includes('checkbox');
    const selectableSrids = items.filter(i => isActionable(i.ui_state)).map(i => i.srid);
    const selectedInSection = selectableSrids.filter(s => selectedSrids.has(s)).length;
    const allSectionSelected = selectableSrids.length > 0 && selectedInSection === selectableSrids.length;

    const toggleAll = (checked: boolean) => {
        for (const srid of selectableSrids) {
            const isSel = selectedSrids.has(srid);
            if (checked && !isSel) onToggleSrid(srid);
            else if (!checked && isSel) onToggleSrid(srid);
        }
    };

    return (
        <div>
            <div
                style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    padding: '6px 12px',
                    borderLeft: `3px solid ${accentColor}`,
                    background: 'rgba(0,0,0,0.02)',
                    borderRadius: 6,
                    marginBottom: 8,
                }}
            >
                <span style={{ fontSize: 16 }}>{icon}</span>
                <span style={{ fontWeight: 600, fontSize: 14 }}>
                    {title} <span style={{ color: accentColor, fontWeight: 700 }}>{items.length}</span>
                </span>
            </div>

            {items.length === 0 ? (
                <div style={{
                    padding: '8px 16px',
                    color: 'var(--color-text-muted)',
                    fontSize: 13,
                    fontStyle: 'italic',
                }}>
                    {emptyHint}
                </div>
            ) : (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table" style={{ width: '100%' }}>
                        <thead>
                            <tr>
                                {columns.map(col => {
                                    switch (col) {
                                        case 'checkbox':
                                            return (
                                                <th key={col} style={{ width: 40 }}>
                                                    <input
                                                        type="checkbox"
                                                        checked={allSectionSelected}
                                                        disabled={selectableSrids.length === 0}
                                                        onChange={e => toggleAll(e.target.checked)}
                                                        onClick={e => e.stopPropagation()}
                                                    />
                                                </th>
                                            );
                                        case 'product': return <th key={col}>Товар</th>;
                                        case 'article': return <th key={col}>Артикул</th>;
                                        case 'size': return <th key={col}>Размер</th>;
                                        case 'srid': return <th key={col}>SRID</th>;
                                        case 'ready_from': return <th key={col}>Готов с</th>;
                                        case 'picked_at': return <th key={col}>Забрали с ПВЗ</th>;
                                        case 'expires': return <th key={col}>Истекает</th>;
                                        case 'archive_btn': return <th key={col} style={{ width: 100 }}></th>;
                                    }
                                })}
                            </tr>
                        </thead>
                        <tbody>
                            {items.map(item => {
                                const checked = selectedSrids.has(item.srid);
                                const actionable = isActionable(item.ui_state);
                                return (
                                    <tr key={item.id}>
                                        {columns.map(col => {
                                            switch (col) {
                                                case 'checkbox':
                                                    return (
                                                        <td key={col} style={{ textAlign: 'center' }}>
                                                            <input
                                                                type="checkbox"
                                                                checked={checked}
                                                                disabled={!actionable}
                                                                onChange={() => onToggleSrid(item.srid)}
                                                                onClick={e => e.stopPropagation()}
                                                            />
                                                        </td>
                                                    );
                                                case 'product':
                                                    return <td key={col}>{formatProduct(item)}</td>;
                                                case 'article':
                                                    return (
                                                        <td key={col} style={{ fontFamily: 'monospace', fontSize: 12 }}>
                                                            {formatArticle(item)}
                                                        </td>
                                                    );
                                                case 'size':
                                                    return (
                                                        <td key={col} style={{ color: 'var(--color-text-muted)' }}>
                                                            {item.tech_size || '—'}
                                                        </td>
                                                    );
                                                case 'srid':
                                                    return (
                                                        <td key={col} style={{
                                                            fontFamily: 'monospace',
                                                            fontSize: 12,
                                                            color: 'var(--color-text-muted)',
                                                        }}>
                                                            {item.srid}
                                                        </td>
                                                    );
                                                case 'ready_from':
                                                    return <td key={col}>{formatDate(item.ready_to_return_dt)}</td>;
                                                case 'picked_at':
                                                    return <td key={col}>{formatDate(item.completed_dt)}</td>;
                                                case 'expires':
                                                    return <td key={col}>{formatDate(item.expired_dt)}</td>;
                                                case 'archive_btn':
                                                    return (
                                                        <td key={col} style={{ textAlign: 'right' }}>
                                                            <button
                                                                className="btn btn-secondary btn-sm"
                                                                onClick={e => {
                                                                    e.stopPropagation();
                                                                    onArchive(item.srid);
                                                                }}
                                                                disabled={archiving}
                                                                title="Отправить в архив (списать без приёмки)"
                                                            >
                                                                В архив
                                                            </button>
                                                        </td>
                                                    );
                                            }
                                        })}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

// ─── Tab "In Transit" — received returns waiting for warehouse ──────────

interface InTransitTabProps {
    items: WbGoodsReturn[];
    slug: string;
}

interface InTransitGroup {
    receipt_id: number;
    receipt_number: string;
    warehouse_id: number | null;
    warehouse_name: string | null;
    items_count: number;
    picked_up_count: number;
    ui_state: WbGoodsReturnUiState;
    items: WbGoodsReturn[];
}

function groupByReceipt(items: WbGoodsReturn[]): InTransitGroup[] {
    const map = new Map<number, InTransitGroup>();
    for (const r of items) {
        if (!r.inbound_receipt_id) continue;
        const existing = map.get(r.inbound_receipt_id);
        if (existing) {
            existing.items.push(r);
            existing.items_count += 1;
            if (r.completed_dt) existing.picked_up_count += 1;
            // If any item already picked up — promote ui_state
            if (r.ui_state === 'picked_up_pending_receipt') {
                existing.ui_state = 'picked_up_pending_receipt';
            }
        } else {
            map.set(r.inbound_receipt_id, {
                receipt_id: r.inbound_receipt_id,
                receipt_number: r.inbound_receipt_number ?? `#${r.inbound_receipt_id}`,
                warehouse_id: r.inbound_warehouse_id,
                warehouse_name: r.inbound_warehouse_name,
                items_count: 1,
                picked_up_count: r.completed_dt ? 1 : 0,
                ui_state: r.ui_state,
                items: [r],
            });
        }
    }
    return Array.from(map.values());
}

function InTransitTab({ items, slug }: InTransitTabProps) {
    const [expanded, setExpanded] = useState<Set<number>>(new Set());
    const groups = useMemo(() => groupByReceipt(items), [items]);

    const toggleExpand = (receiptId: number) => {
        setExpanded(prev => {
            const next = new Set(prev);
            if (next.has(receiptId)) next.delete(receiptId);
            else next.add(receiptId);
            return next;
        });
    };

    if (groups.length === 0) {
        return (
            <div className="empty-state glass-card" style={{ padding: 64, textAlign: 'center' }}>
                <div style={{ fontSize: 48, marginBottom: 16 }}>🚚</div>
                <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>
                    Товаров в пути нет
                </div>
                <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                    Сюда попадают возвраты, по которым уже создана приёмка, но склад ещё не получил товар.
                </div>
            </div>
        );
    }

    return (
        <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="data-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                    <tr>
                        <th style={{ width: 40 }}></th>
                        <th>Приёмка</th>
                        <th>Склад</th>
                        <th style={{ textAlign: 'right' }}>Товаров</th>
                        <th style={{ textAlign: 'right' }}>Забрано с ПВЗ</th>
                        <th>Статус</th>
                    </tr>
                </thead>
                <tbody>
                    {groups.map(g => (
                        <React.Fragment key={g.receipt_id}>
                            <tr
                                onClick={() => toggleExpand(g.receipt_id)}
                                style={{ cursor: 'pointer' }}
                            >
                                <td style={{ textAlign: 'center', color: 'var(--color-text-muted)' }}>
                                    {expanded.has(g.receipt_id) ? '▼' : '▶'}
                                </td>
                                <td>
                                    {g.warehouse_id != null ? (
                                        <Link href={`/p/${slug}/warehouse/${g.warehouse_id}`} onClick={e => e.stopPropagation()}>
                                            <span className="badge badge-warning">{g.receipt_number}</span>
                                        </Link>
                                    ) : (
                                        <span className="badge badge-warning">{g.receipt_number}</span>
                                    )}
                                </td>
                                <td>{g.warehouse_name || '—'}</td>
                                <td style={{ textAlign: 'right', fontWeight: 600 }}>{formatNumber(g.items_count)}</td>
                                <td style={{ textAlign: 'right' }}>
                                    {formatNumber(g.picked_up_count)} / {formatNumber(g.items_count)}
                                </td>
                                <td>
                                    <span className={`badge ${UI_STATE_BADGE[g.ui_state]}`}>
                                        {UI_STATE_LABEL[g.ui_state]}
                                    </span>
                                </td>
                            </tr>
                            {expanded.has(g.receipt_id) && g.items.map(item => (
                                <tr key={item.id} style={{ background: 'var(--color-bg-hover)' }}>
                                    <td></td>
                                    <td colSpan={2} style={{ paddingLeft: 32, fontSize: 13 }}>
                                        {formatProduct(item)}
                                        <span
                                            style={{ color: 'var(--color-text-muted)', marginLeft: 8, fontFamily: 'monospace', fontSize: 11 }}
                                            title="Артикул продавца (или WB nmID)"
                                        >
                                            арт. {formatArticle(item)}
                                        </span>
                                        <span style={{ color: 'var(--color-text-muted)', marginLeft: 8, fontFamily: 'monospace', fontSize: 11 }}>
                                            {item.srid}
                                        </span>
                                    </td>
                                    <td style={{ textAlign: 'right', fontSize: 13, color: 'var(--color-text-muted)' }}>
                                        {formatDate(item.order_dt)}
                                    </td>
                                    <td style={{ textAlign: 'right', fontSize: 13, color: 'var(--color-text-muted)' }}>
                                        {formatDateTime(item.completed_dt) || '—'}
                                    </td>
                                    <td></td>
                                </tr>
                            ))}
                        </React.Fragment>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

// ─── Tab "History" — completed / expired / without receipt ──────────────

function HistoryTab({ items }: { items: WbGoodsReturn[] }) {
    const cols: Column[] = useMemo(() => [
        {
            key: 'product',
            label: 'Товар',
            render: (_: unknown, row: WbGoodsReturn) => formatProduct(row),
            exportValue: (row: WbGoodsReturn) => formatProduct(row),
        },
        {
            key: 'article',
            label: 'Артикул',
            render: (_: unknown, row: WbGoodsReturn) => (
                <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{formatArticle(row)}</span>
            ),
            exportValue: (row: WbGoodsReturn) => formatArticle(row),
        },
        {
            key: 'srid',
            label: 'SRID',
            render: (v: string) => (
                <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span>
            ),
        },
        {
            key: 'inbound_receipt_number',
            label: 'Приёмка',
            render: (v: string | null) => v || '—',
        },
        {
            key: 'ui_state',
            label: 'Результат',
            render: (v: WbGoodsReturnUiState) => (
                <span className={`badge ${UI_STATE_BADGE[v]}`}>{UI_STATE_LABEL[v]}</span>
            ),
            exportValue: (row: WbGoodsReturn) => UI_STATE_LABEL[row.ui_state],
        },
        {
            key: 'completed_dt',
            label: 'Забрали с ПВЗ',
            render: (v: string | null) => formatDate(v),
            exportValue: (row: WbGoodsReturn) => row.completed_dt ?? '',
        },
        {
            key: 'archived_at',
            label: 'В архив',
            render: (v: string | null) => formatDate(v),
            exportValue: (row: WbGoodsReturn) => row.archived_at ?? '',
        },
        {
            key: 'expired_dt',
            label: 'Истекает',
            format: 'date' as const,
        },
        {
            key: 'reason',
            label: 'Причина',
            render: (v: string | null) => v || '—',
        },
    ], []);

    if (items.length === 0) {
        return (
            <div className="empty-state glass-card" style={{ padding: 64, textAlign: 'center' }}>
                <div style={{ fontSize: 48, marginBottom: 16 }}>📜</div>
                <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>
                    История пуста
                </div>
                <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                    Здесь отображаются принятые и архивированные возвраты. Отправьте «без оформления» в архив кнопкой на вкладке «На ПВЗ» — они появятся здесь.
                </div>
            </div>
        );
    }

    return (
        <TanStackDataTable
            columns={cols}
            data={items}
            exportName="wb_returns_history"
            enableSorting
        />
    );
}
