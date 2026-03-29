'use client';

import { haptic } from '@/lib/telegram';
import type { StockNeedData } from './types';
import { compactNumber } from './helpers';

export default function LogisticsTab({ data, loading, error, onRetry }: {
    data: StockNeedData | null;
    loading: boolean;
    error: string;
    onRetry: () => void;
}) {
    if (loading) return <div className="tma-loading"><div className="tma-spinner" /><div className="tma-loading-text">Загрузка...</div></div>;
    if (error) return <ErrorRetry error={error} onRetry={onRetry} />;
    if (!data || data.articles.length === 0) return <Empty text="Нет данных по потребностям" />;

    const s = data.summary;
    const needArticles = data.articles
        .filter(a => a.total_need > 0 || a.deficit > 0)
        .sort((a, b) => b.deficit - a.deficit || b.total_need - a.total_need);

    return (
        <>
            <div className="tma-stat-grid">
                <div className="tma-stat-cell">
                    <div className="tma-stat-cell-value">{compactNumber(s.total_need)}</div>
                    <div className="tma-stat-cell-label">Нужно</div>
                </div>
                <div className="tma-stat-cell">
                    <div className="tma-stat-cell-value tma-stat-positive">{compactNumber(s.total_can_send)}</div>
                    <div className="tma-stat-cell-label">Можно</div>
                </div>
                <div className="tma-stat-cell">
                    <div className={`tma-stat-cell-value ${s.total_deficit > 0 ? 'tma-stat-negative' : ''}`}>
                        {compactNumber(s.total_deficit)}
                    </div>
                    <div className="tma-stat-cell-label">Дефицит</div>
                </div>
                <div className="tma-stat-cell">
                    <div className="tma-stat-cell-value">{s.avg_delivery_days} дн</div>
                    <div className="tma-stat-cell-label">Доставка</div>
                </div>
            </div>

            {needArticles.length > 0 && (
                <div className="tma-card">
                    <div className="tma-card-title">Потребности ({needArticles.length})</div>
                    {needArticles.slice(0, 20).map((a) => (
                        <div key={a.nm_id} className="tma-wh-alert-row">
                            <div className="tma-wh-alert-left">
                                <div className="tma-wh-alert-info">
                                    <div className="tma-wh-alert-name">{a.vendor_code}</div>
                                    <div className="tma-wh-alert-sub">
                                        {a.subject}{a.brand ? ` \u00b7 ${a.brand}` : ''}
                                    </div>
                                    <div className="tma-wh-alert-sub tma-wh-meta">
                                        WB: {a.stocks_wb}
                                        {a.in_assembly > 0 && <> &middot; <span className="tma-text-purple">сборка {a.in_assembly}</span></>}
                                        {a.in_transit > 0 && <> &middot; <span className="tma-stat-positive">в пути {a.in_transit}</span></>}
                                    </div>
                                </div>
                            </div>
                            <div className="tma-wh-alert-right">
                                {a.can_send > 0 ? (
                                    <>
                                        <div className="tma-wh-alert-days tma-stat-positive">{a.can_send}</div>
                                        <div className="tma-wh-alert-stock">отправить</div>
                                    </>
                                ) : a.deficit > 0 ? (
                                    <>
                                        <div className="tma-wh-alert-days tma-stat-negative">{a.deficit}</div>
                                        <div className="tma-wh-alert-stock">дефицит</div>
                                    </>
                                ) : (
                                    <>
                                        <div className="tma-wh-alert-days">{a.total_need}</div>
                                        <div className="tma-wh-alert-stock">нужно</div>
                                    </>
                                )}
                            </div>
                        </div>
                    ))}
                    {needArticles.length > 20 && <div className="tma-wh-more">+{needArticles.length - 20} ещё</div>}
                </div>
            )}
        </>
    );
}

function ErrorRetry({ error, onRetry }: { error: string; onRetry: () => void }) {
    return (
        <div className="tma-empty">
            <div className="tma-empty-icon">⚠️</div>
            <div className="tma-empty-text">{error}</div>
            <button className="tma-btn tma-btn-primary" style={{ marginTop: 16 }} onClick={() => { haptic('light'); onRetry(); }}>Повторить</button>
        </div>
    );
}

function Empty({ text }: { text: string }) {
    return <div className="tma-empty"><div className="tma-empty-icon">📭</div><div className="tma-empty-text">{text}</div></div>;
}
