'use client';

import { haptic } from '@/lib/telegram';
import type { AssemblyListResponse } from './types';
import { STATUS_LABELS, STATUS_COLORS, compactNumber, formatShortDate, totalItems } from './helpers';

export default function HistoryTab({ data, loading, error, onRetry }: {
    data: AssemblyListResponse | null;
    loading: boolean;
    error: string;
    onRetry: () => void;
}) {
    if (loading) return <div className="tma-loading"><div className="tma-spinner" /><div className="tma-loading-text">Загрузка...</div></div>;
    if (error) return (
        <div className="tma-empty">
            <div className="tma-empty-icon">⚠️</div>
            <div className="tma-empty-text">{error}</div>
            <button className="tma-btn tma-btn-primary" style={{ marginTop: 16 }} onClick={() => { haptic('light'); onRetry(); }}>Повторить</button>
        </div>
    );
    if (!data || data.items.length === 0) return (
        <div className="tma-empty"><div className="tma-empty-icon">📭</div><div className="tma-empty-text">Нет отправок</div></div>
    );

    return (
        <div className="tma-card">
            <div className="tma-card-title">Отправки ({data.total})</div>
            {data.items.map((req) => (
                <div key={req.id} className="tma-wh-row">
                    <div className="tma-wh-row-left">
                        <div className="tma-wh-row-name">
                            {req.number}
                            <span className="tma-status-badge" style={{ background: STATUS_COLORS[req.status] || '#8e8e93' }}>
                                {STATUS_LABELS[req.status] || req.status}
                            </span>
                        </div>
                        <div className="tma-wh-row-sub">
                            {req.wb_supply_id_wb || req.wb_warehouse_name}
                            {req.brands ? ` \u00b7 ${req.brands}` : ''}
                        </div>
                        <div className="tma-wh-row-sub">
                            {totalItems(req)} шт
                            {req.pallets_count ? ` \u00b7 ${req.pallets_count} палл.` : ''}
                            {req.vehicle_info ? ` \u00b7 ${req.vehicle_info}` : ''}
                        </div>
                    </div>
                    <div className="tma-wh-row-right">
                        <div className="tma-wh-row-qty">{formatShortDate(req.shipped_at || req.delivery_date)}</div>
                    </div>
                </div>
            ))}
        </div>
    );
}
