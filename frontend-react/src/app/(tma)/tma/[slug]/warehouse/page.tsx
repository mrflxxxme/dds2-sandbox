'use client';

import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { haptic } from '@/lib/telegram';
import type {
    Tab, WarehouseData, StockAnalyticsData,
    AssemblyListResponse, StockNeedData,
} from './types';
import { formatSyncTime } from './helpers';
import StockTab from './StockTab';
import AssemblyTab from './AssemblyTab';
import LogisticsTab from './LogisticsTab';
import HistoryTab from './HistoryTab';

/**
 * TMA Warehouse — 4 tabs with lazy loading.
 * Stock loads on mount; Assembly/Logistics/History load on first tab switch.
 */

const TABS: { key: Tab; label: string; icon: string }[] = [
    { key: 'stock', label: 'Остатки', icon: '📊' },
    { key: 'assembly', label: 'Сборка', icon: '📦' },
    { key: 'logistics', label: 'Логист', icon: '🚛' },
    { key: 'history', label: 'Отправки', icon: '✈️' },
];

export default function TmaWarehousePage() {
    const [tab, setTab] = useState<Tab>('stock');

    // Stock
    const [whData, setWhData] = useState<WarehouseData | null>(null);
    const [analytics, setAnalytics] = useState<StockAnalyticsData | null>(null);
    const [stockLoading, setStockLoading] = useState(true);
    const [stockError, setStockError] = useState('');

    // Assembly
    const [asmData, setAsmData] = useState<AssemblyListResponse | null>(null);
    const [asmLoading, setAsmLoading] = useState(false);
    const [asmError, setAsmError] = useState('');
    const [asmLoaded, setAsmLoaded] = useState(false);

    // Logistics
    const [needData, setNeedData] = useState<StockNeedData | null>(null);
    const [needLoading, setNeedLoading] = useState(false);
    const [needError, setNeedError] = useState('');
    const [needLoaded, setNeedLoaded] = useState(false);

    // History
    const [histData, setHistData] = useState<AssemblyListResponse | null>(null);
    const [histLoading, setHistLoading] = useState(false);
    const [histError, setHistError] = useState('');
    const [histLoaded, setHistLoaded] = useState(false);

    // ─── Loaders ──────────────────────────────────────────────────────────────

    const loadStock = useCallback(async () => {
        setStockLoading(true);
        setStockError('');
        try {
            const [wh, an] = await Promise.all([
                api.request<WarehouseData>('GET', '/api/v1/reports/stock_warehouses'),
                api.request<StockAnalyticsData>('GET', '/api/v1/reports/stock_analytics?mode=wb_rf_transit'),
            ]);
            setWhData(wh);
            setAnalytics(an);
        } catch (err) {
            setStockError(err instanceof Error ? err.message : 'Ошибка загрузки');
        } finally {
            setStockLoading(false);
        }
    }, []);

    const loadAssembly = useCallback(async () => {
        setAsmLoading(true);
        setAsmError('');
        try {
            const d = await api.request<AssemblyListResponse>(
                'GET', '/api/v1/warehouse/assembly?status=PENDING,IN_PROGRESS,READY,VEHICLE_ASSIGNED&limit=50'
            );
            setAsmData(d);
            setAsmLoaded(true);
        } catch (err) {
            setAsmError(err instanceof Error ? err.message : 'Ошибка загрузки');
        } finally {
            setAsmLoading(false);
        }
    }, []);

    const loadNeed = useCallback(async () => {
        setNeedLoading(true);
        setNeedError('');
        try {
            const d = await api.request<StockNeedData>('GET', '/api/v1/reports/stock_need');
            setNeedData(d);
            setNeedLoaded(true);
        } catch (err) {
            setNeedError(err instanceof Error ? err.message : 'Ошибка загрузки');
        } finally {
            setNeedLoading(false);
        }
    }, []);

    const loadHistory = useCallback(async () => {
        setHistLoading(true);
        setHistError('');
        try {
            const d = await api.request<AssemblyListResponse>(
                'GET', '/api/v1/warehouse/assembly?status=SHIPPED,DELIVERED,CANCELLED&limit=30'
            );
            setHistData(d);
            setHistLoaded(true);
        } catch (err) {
            setHistError(err instanceof Error ? err.message : 'Ошибка загрузки');
        } finally {
            setHistLoading(false);
        }
    }, []);

    // Load stock on mount
    useEffect(() => { loadStock(); }, [loadStock]);

    // Lazy-load on tab switch
    useEffect(() => {
        if (tab === 'assembly' && !asmLoaded) loadAssembly();
        if (tab === 'logistics' && !needLoaded) loadNeed();
        if (tab === 'history' && !histLoaded) loadHistory();
    }, [tab, asmLoaded, needLoaded, histLoaded, loadAssembly, loadNeed, loadHistory]);

    const handleTab = useCallback((t: Tab) => { haptic('selection'); setTab(t); }, []);

    // Refresh assembly after status change
    const handleAsmUpdate = useCallback(() => {
        loadAssembly();
    }, [loadAssembly]);

    return (
        <div className="tma-page">
            <div className="tma-page-header">
                <div className="tma-page-title">Склад</div>
                {whData && <div className="tma-page-subtitle">Синхр. {formatSyncTime(whData.last_synced_at)}</div>}
            </div>

            {/* Tab bar */}
            <div className="tma-tab-bar">
                {TABS.map(({ key, label, icon }) => (
                    <button
                        key={key}
                        className={`tma-tab-item${tab === key ? ' active' : ''}`}
                        onClick={() => handleTab(key)}
                    >
                        <span className="tma-tab-icon">{icon}</span>
                        <span className="tma-tab-label">{label}</span>
                    </button>
                ))}
            </div>

            {/* Content */}
            {tab === 'stock' && (
                stockLoading
                    ? <div className="tma-loading"><div className="tma-spinner" /><div className="tma-loading-text">Загрузка...</div></div>
                    : stockError
                        ? <div className="tma-empty"><div className="tma-empty-icon">⚠️</div><div className="tma-empty-text">{stockError}</div>
                            <button className="tma-btn tma-btn-primary" style={{ marginTop: 16 }} onClick={() => { haptic('light'); loadStock(); }}>Повторить</button></div>
                        : !whData
                            ? <div className="tma-empty"><div className="tma-empty-icon">📭</div><div className="tma-empty-text">Нет данных</div></div>
                            : <StockTab whData={whData} analytics={analytics} />
            )}

            {tab === 'assembly' && (
                <AssemblyTab
                    data={asmData} loading={asmLoading} error={asmError}
                    onRetry={loadAssembly} onDataUpdate={handleAsmUpdate}
                />
            )}

            {tab === 'logistics' && (
                <LogisticsTab data={needData} loading={needLoading} error={needError} onRetry={loadNeed} />
            )}

            {tab === 'history' && (
                <HistoryTab data={histData} loading={histLoading} error={histError} onRetry={loadHistory} />
            )}
        </div>
    );
}
