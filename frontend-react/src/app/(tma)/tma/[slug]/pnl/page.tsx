'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { haptic } from '@/lib/telegram';

/**
 * TMA P&L (ОПИУ) page — month/quarter/year period switcher + OPIU data.
 *
 * FIX #5: setTimeout cleanup via useRef + max retry count for polling.
 */

type Period = 'month' | 'quarter' | 'year';

interface PnlSection {
    label: string;
    rows: Array<{ label: string; value: number }>;
    total?: { label: string; value: number };
}

interface OpiuResponse {
    rows: Array<{
        article: string;
        [key: string]: unknown;
    }>;
    totals: Record<string, number>;
    status?: string;
}

const MAX_RETRIES = 10;
const RETRY_DELAY_MS = 3000;

function getDateRange(period: Period): { dateFrom: string; dateTo: string } {
    const now = new Date();
    const year = now.getFullYear();
    const month = now.getMonth(); // 0-indexed

    let dateFrom: Date;
    let dateTo: Date;

    switch (period) {
        case 'month':
            dateFrom = new Date(year, month, 1);
            dateTo = new Date(year, month + 1, 0);
            break;
        case 'quarter': {
            const qStart = Math.floor(month / 3) * 3;
            dateFrom = new Date(year, qStart, 1);
            dateTo = new Date(year, qStart + 3, 0);
            break;
        }
        case 'year':
            dateFrom = new Date(year, 0, 1);
            dateTo = new Date(year, 11, 31);
            break;
    }

    const fmt = (d: Date) => d.toISOString().slice(0, 10);
    return { dateFrom: fmt(dateFrom), dateTo: fmt(dateTo) };
}

export default function TmaPnlPage() {
    const [period, setPeriod] = useState<Period>('month');
    const [sections, setSections] = useState<PnlSection[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // FIX #5: useRef for timeout cleanup
    const timeoutRef = useRef<NodeJS.Timeout>();
    const retryCountRef = useRef(0);

    const loadData = useCallback(async () => {
        setLoading(true);
        setError('');
        retryCountRef.current = 0;

        const { dateFrom, dateTo } = getDateRange(period);

        try {
            const data = await api.request<OpiuResponse>(
                'GET',
                `/api/v1/reports/opiu?date_from=${dateFrom}&date_to=${dateTo}`
            );

            // If status is "computing", poll until ready (with max retries)
            if (data.status === 'computing') {
                if (retryCountRef.current < MAX_RETRIES) {
                    retryCountRef.current++;
                    timeoutRef.current = setTimeout(() => {
                        loadData();
                    }, RETRY_DELAY_MS);
                } else {
                    setError('Расчёт занимает слишком много времени. Попробуйте позже.');
                    setLoading(false);
                }
                return;
            }

            // Parse OPIU totals into display sections
            const t = data.totals || {};
            const parsed: PnlSection[] = [];

            // Revenue section
            parsed.push({
                label: 'Выручка',
                rows: [
                    { label: 'Продажи (WB)', value: t.revenue_wb || 0 },
                    { label: 'Прочие продажи', value: t.revenue_other || 0 },
                ],
                total: { label: 'Итого выручка', value: t.revenue_total || 0 },
            });

            // Cost of goods
            parsed.push({
                label: 'Себестоимость',
                rows: [
                    { label: 'Себестоимость товара', value: t.cogs || 0 },
                    { label: 'Логистика', value: t.logistics || 0 },
                    { label: 'Комиссия WB', value: t.wb_commission || 0 },
                ],
                total: { label: 'Валовая прибыль', value: t.gross_profit || 0 },
            });

            // Operating expenses
            parsed.push({
                label: 'Операционные расходы',
                rows: [
                    { label: 'Реклама', value: t.ads || t.ad_deduction || 0 },
                    { label: 'Хранение', value: t.storage || 0 },
                    { label: 'Штрафы', value: t.penalties || 0 },
                    { label: 'Прочие удержания', value: t.other_deduction || 0 },
                ].filter(r => r.value !== 0),
                total: { label: 'Операционная прибыль', value: t.operating_profit || 0 },
            });

            // Net profit
            if (t.net_profit !== undefined) {
                parsed.push({
                    label: 'Итог',
                    rows: [
                        { label: 'Налоги', value: t.tax || 0 },
                    ].filter(r => r.value !== 0),
                    total: { label: 'Чистая прибыль', value: t.net_profit || 0 },
                });
            }

            setSections(parsed);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [period]);

    // FIX #5: Cleanup timeout on period change or unmount
    useEffect(() => {
        loadData();
        return () => {
            if (timeoutRef.current) {
                clearTimeout(timeoutRef.current);
            }
        };
    }, [loadData]);

    const handlePeriodChange = useCallback((p: Period) => {
        haptic('selection');
        setPeriod(p);
    }, []);

    return (
        <div className="tma-page">
            <div className="tma-page-header">
                <div className="tma-page-title">P&L (ОПИУ)</div>
            </div>

            {/* Period Switcher */}
            <div className="tma-period-switch">
                {([
                    { key: 'month' as Period, label: 'Месяц' },
                    { key: 'quarter' as Period, label: 'Квартал' },
                    { key: 'year' as Period, label: 'Год' },
                ]).map(({ key, label }) => (
                    <button
                        key={key}
                        className={`tma-period-btn${period === key ? ' active' : ''}`}
                        onClick={() => handlePeriodChange(key)}
                    >
                        {label}
                    </button>
                ))}
            </div>

            {loading ? (
                <div className="tma-loading">
                    <div className="tma-spinner" />
                    <div className="tma-loading-text">Загрузка...</div>
                </div>
            ) : error ? (
                <div className="tma-empty">
                    <div className="tma-empty-icon">⚠️</div>
                    <div className="tma-empty-text">{error}</div>
                    <button
                        className="tma-btn tma-btn-primary"
                        style={{ marginTop: 16 }}
                        onClick={() => {
                            haptic('light');
                            loadData();
                        }}
                    >
                        Повторить
                    </button>
                </div>
            ) : sections.length === 0 ? (
                <div className="tma-empty">
                    <div className="tma-empty-icon">📭</div>
                    <div className="tma-empty-text">Нет данных за выбранный период</div>
                </div>
            ) : (
                sections.map((section, si) => (
                    <div key={si} className="tma-card">
                        <div className="tma-pnl-section-header">{section.label}</div>
                        {section.rows.map((row, ri) => (
                            <div key={ri} className="tma-pnl-row">
                                <span className="tma-pnl-label">{row.label}</span>
                                <span className={`tma-pnl-value ${row.value >= 0 ? 'tma-stat-positive' : 'tma-stat-negative'}`}>
                                    {formatNumber(row.value, 0)} ₽
                                </span>
                            </div>
                        ))}
                        {section.total && (
                            <div className="tma-pnl-row tma-pnl-total">
                                <span className="tma-pnl-label">{section.total.label}</span>
                                <span className={`tma-pnl-value ${section.total.value >= 0 ? 'tma-stat-positive' : 'tma-stat-negative'}`}>
                                    {formatNumber(section.total.value, 0)} ₽
                                </span>
                            </div>
                        )}
                    </div>
                ))
            )}
        </div>
    );
}
