'use client';

import type { WarehouseData, StockAnalyticsData, StockArticle, TrafficLight } from './types';
import { TRAFFIC_ICONS, TRAFFIC_LABELS, compactNumber } from './helpers';

export default function StockTab({ whData, analytics }: {
    whData: WarehouseData;
    analytics: StockAnalyticsData | null;
}) {
    const trafficCounts: Record<TrafficLight, number> = { red: 0, orange: 0, yellow: 0, green: 0 };
    const criticalArticles: StockArticle[] = [];

    if (analytics?.articles) {
        for (const a of analytics.articles) {
            if (a.traffic_light in trafficCounts) trafficCounts[a.traffic_light]++;
            if (a.traffic_light === 'red' || a.traffic_light === 'orange') criticalArticles.push(a);
        }
    }

    criticalArticles.sort((a, b) => {
        if (a.traffic_light !== b.traffic_light) return a.traffic_light === 'red' ? -1 : 1;
        return (a.days_left ?? 0) - (b.days_left ?? 0);
    });

    const ch = whData.change_total;

    return (
        <>
            {/* Summary strip */}
            <div className="tma-stat-grid">
                <div className="tma-stat-cell">
                    <div className="tma-stat-cell-value">{compactNumber(whData.total_qty)}</div>
                    <div className="tma-stat-cell-label">На складах</div>
                </div>
                <div className="tma-stat-cell">
                    <div className={`tma-stat-cell-value ${ch > 0 ? 'tma-stat-positive' : ch < 0 ? 'tma-stat-negative' : ''}`}>
                        {ch > 0 ? '+' : ''}{compactNumber(ch)}
                    </div>
                    <div className="tma-stat-cell-label">За сутки</div>
                </div>
                <div className="tma-stat-cell">
                    <div className="tma-stat-cell-value">{compactNumber(whData.total_in_way_to_client)}</div>
                    <div className="tma-stat-cell-label">К клиентам</div>
                </div>
                <div className="tma-stat-cell">
                    <div className="tma-stat-cell-value">{whData.total_warehouses}</div>
                    <div className="tma-stat-cell-label">Складов</div>
                </div>
            </div>

            {/* Traffic light */}
            {analytics && analytics.articles.length > 0 && (
                <div className="tma-card">
                    <div className="tma-card-title">Запасы</div>
                    <div className="tma-traffic-grid">
                        {(['red', 'orange', 'yellow', 'green'] as TrafficLight[]).map((l) => (
                            <div key={l} className="tma-traffic-item">
                                <span className="tma-traffic-icon">{TRAFFIC_ICONS[l]}</span>
                                <span className="tma-traffic-count">{trafficCounts[l]}</span>
                                <span className="tma-traffic-label">{TRAFFIC_LABELS[l]}</span>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Critical alerts */}
            {criticalArticles.length > 0 && (
                <div className="tma-card">
                    <div className="tma-card-title">Требуют внимания ({criticalArticles.length})</div>
                    {criticalArticles.slice(0, 10).map((a) => (
                        <div key={a.nm_id} className="tma-wh-alert-row">
                            <div className="tma-wh-alert-left">
                                <span className="tma-wh-alert-icon">{TRAFFIC_ICONS[a.traffic_light]}</span>
                                <div className="tma-wh-alert-info">
                                    <div className="tma-wh-alert-name">{a.vendor_code || a.nm_id}</div>
                                    <div className="tma-wh-alert-sub">
                                        {a.subject}{a.brand ? ` \u00b7 ${a.brand}` : ''}
                                    </div>
                                </div>
                            </div>
                            <div className="tma-wh-alert-right">
                                <div className={`tma-wh-alert-days tma-traffic-${a.traffic_light}-text`}>
                                    {a.days_left != null ? `${a.days_left} дн` : '—'}
                                </div>
                                <div className="tma-wh-alert-stock">{a.stocks_wb} шт</div>
                            </div>
                        </div>
                    ))}
                    {criticalArticles.length > 10 && <div className="tma-wh-more">+{criticalArticles.length - 10} ещё</div>}
                </div>
            )}

            {/* Warehouses list */}
            {whData.warehouses.length > 0 && (
                <div className="tma-card">
                    <div className="tma-card-title">Склады WB</div>
                    {whData.warehouses.slice(0, 10).map((wh, i) => (
                        <div key={i} className="tma-wh-row">
                            <div className="tma-wh-row-left">
                                <div className="tma-wh-row-name">{wh.name}</div>
                                <div className="tma-wh-row-sub">{wh.articles_count} артикулов</div>
                            </div>
                            <div className="tma-wh-row-right">
                                <div className="tma-wh-row-qty">{compactNumber(wh.total_qty)} шт</div>
                                {wh.change !== 0 && (
                                    <div className={`tma-wh-row-change ${wh.change > 0 ? 'tma-stat-positive' : 'tma-stat-negative'}`}>
                                        {wh.change > 0 ? '+' : ''}{compactNumber(wh.change)}
                                    </div>
                                )}
                            </div>
                        </div>
                    ))}
                    {whData.warehouses.length > 10 && <div className="tma-wh-more">+{whData.warehouses.length - 10} ещё</div>}
                </div>
            )}
        </>
    );
}
