'use client';
/**
 * WB FBS — продажа со своего склада: маппинг складов продавца WB на наши склады,
 * трансляция остатков с расшифровкой вычетов, сборочные задания и поставки.
 *
 * Page-ключ прав — `fbs` (backend/rbac.py, секция warehouse).
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import PageGuard from '@/components/PageGuard';
import Toast from '@/components/Toast';
import type { FbsModeInfo, FbsWarehouse } from '@/types/api';
import { AutoRefreshBadge, FbsModeBanner, FbsModeErrorBanner, writeDisabledHint } from './fbsShared';
import { useAutoRefresh } from './useAutoRefresh';
import WarehousesTab from './WarehousesTab';
import StockTab from './StockTab';
import OrdersTab from './OrdersTab';
import SuppliesTab from './SuppliesTab';

type Tab = 'supplies' | 'orders' | 'stock' | 'warehouses';

/**
 * Порядок вкладок повторяет кабинет WB: единица работы — ПОСТАВКА, поэтому
 * она первая и открывается по умолчанию. «Заказы» — источник, из которого
 * поставки набираются; остальное — настройка трансляции остатков.
 */
const TABS: { key: Tab; label: string }[] = [
    { key: 'supplies', label: 'Поставки' },
    { key: 'orders', label: 'Заказы' },
    { key: 'stock', label: 'Остатки' },
    { key: 'warehouses', label: 'Склады' },
];

export default function FbsPage() {
    return (
        <PageGuard page="fbs">
            <FbsContent />
        </PageGuard>
    );
}

function FbsContent() {
    const [tab, setTab] = useState<Tab>('supplies');
    const [warehouses, setWarehouses] = useState<FbsWarehouse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [toast, setToast] = useState('');
    const [mode, setMode] = useState<FbsModeInfo | null>(null);
    const [modeError, setModeError] = useState('');
    const [modeLoading, setModeLoading] = useState(true);
    /** Фильтр «задания этой поставки» — переход со вкладки «Поставки». */
    const [orderSupplyFilter, setOrderSupplyFilter] = useState('');

    // Автообновление: тик раз в несколько минут, вкладки подписываются на него
    // и перезагружают свои данные. Неактивные вкладки размонтированы (рендер
    // условный), поэтому лишних запросов тик не порождает.
    const auto = useAutoRefresh();

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getFbsWarehouses();
            if (signal?.aborted) return;
            setWarehouses(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки складов FBS');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    // Тик автообновления: справочник складов перечитываем вместе с активной
    // вкладкой (тумблеры и режимы меняются и из фона — джоб складов, сверка).
    useEffect(() => {
        if (auto.tick === 0) return; // первичную загрузку делает эффект выше
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [auto.tick, load]);

    // Режим контура: пока не загрузился — считаем запись ЗАПРЕЩЁННОЙ.
    // Оптимистичный дефолт «можно писать» дал бы окно, в котором кнопка
    // передачи остатков активна на safe-контуре и упирается в 409.
    // Провал этого запроса — НЕ «молча ничего»: без ретрая любой транзиент
    // (окно деплоя backend/nginx отдаёт 502/504) навсегда гасил бы восемь кнопок
    // записи на боевом контуре, а подсказка врала бы про режим safe.
    const loadMode = useCallback(async (signal?: AbortSignal) => {
        setModeLoading(true);
        setModeError('');
        try {
            const res = await api.getFbsMode();
            if (signal?.aborted) return;
            setMode(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setMode(null);
            setModeError(e instanceof Error ? e.message : 'Ошибка загрузки режима контура');
        } finally {
            if (!signal?.aborted) setModeLoading(false);
        }
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        loadMode(controller.signal);
        return () => controller.abort();
    }, [loadMode]);

    const writeEnabled = mode?.write_enabled ?? false;
    const writeHint = writeDisabledHint(mode);
    const activeCount = warehouses.filter(w => w.is_active).length;

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">FBS Wildberries</h1>
                    <p className="page-subtitle">
                        Продажа со своего склада: поставка — единица работы, внутри неё задания;
                        отдельно — трансляция остатков в WB
                    </p>
                </div>
                <div style={{
                    display: 'flex', flexDirection: 'column', alignItems: 'flex-end',
                    gap: 4, alignSelf: 'center',
                }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Складов WB: {warehouses.length} · активных: {activeCount}
                    </div>
                    <AutoRefreshBadge lastAt={auto.lastAt} enabled={auto.enabled} onToggle={auto.setEnabled} />
                </div>
            </div>

            <FbsModeBanner info={mode} />
            {modeError && (
                <FbsModeErrorBanner
                    message={modeError}
                    retrying={modeLoading}
                    onRetry={() => { loadMode(); }}
                />
            )}

            {/* Вкладки */}
            <div style={{ display: 'flex', gap: 0, marginBottom: 16, borderBottom: '2px solid var(--color-border)' }}>
                {TABS.map(t => (
                    <button
                        key={t.key}
                        onClick={() => setTab(t.key)}
                        style={{
                            padding: '8px 20px',
                            background: 'none',
                            border: 'none',
                            borderBottom: tab === t.key ? '2px solid var(--color-accent)' : '2px solid transparent',
                            marginBottom: -2,
                            fontWeight: tab === t.key ? 600 : 400,
                            color: tab === t.key ? 'var(--color-text)' : 'var(--color-text-muted)',
                            cursor: 'pointer',
                            fontSize: 14,
                        }}
                    >
                        {t.label}
                    </button>
                ))}
            </div>

            {tab === 'warehouses' && (
                <WarehousesTab
                    warehouses={warehouses}
                    loading={loading}
                    error={error}
                    writeEnabled={writeEnabled}
                    writeHint={writeHint}
                    onReload={load}
                    onToast={setToast}
                />
            )}

            {tab === 'stock' && (
                <StockTab
                    warehouses={warehouses}
                    warehousesLoading={loading}
                    writeEnabled={writeEnabled}
                    writeHint={writeHint}
                    onToast={setToast}
                    onGoToWarehouses={() => setTab('warehouses')}
                    onReloadWarehouses={() => load()}
                    refreshTick={auto.tick}
                />
            )}

            {tab === 'orders' && (
                <OrdersTab
                    warehouses={warehouses}
                    supplyFilter={orderSupplyFilter}
                    writeEnabled={writeEnabled}
                    writeHint={writeHint}
                    onSupplyFilterChange={setOrderSupplyFilter}
                    onToast={setToast}
                    refreshTick={auto.tick}
                />
            )}

            {tab === 'supplies' && (
                <SuppliesTab
                    warehouses={warehouses}
                    writeEnabled={writeEnabled}
                    writeHint={writeHint}
                    onShowOrders={(supplyId) => { setOrderSupplyFilter(supplyId); setTab('orders'); }}
                    onToast={setToast}
                    refreshTick={auto.tick}
                />
            )}

            {toast && <Toast message={toast} onClose={() => setToast('')} />}
        </div>
    );
}
