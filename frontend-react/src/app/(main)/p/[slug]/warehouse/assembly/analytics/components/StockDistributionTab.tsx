'use client';

/**
 * Вкладка «Распределение остатков» страницы «Анализ сборки».
 *
 * «Где сейчас товар»: склад ФФ / в сборке / готово / в пути — 100% стэк-бар.
 * Сводка сверху + разрезы «По складам» и «По статусу товара».
 *
 * Свой селект ФФ-склада + переключатель статуса товара (active/new/clearance/none).
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
    Area,
    AreaChart,
    CartesianGrid,
    Legend as RechartsLegend,
    ResponsiveContainer,
    Tooltip as RechartsTooltip,
    XAxis,
    YAxis,
} from 'recharts';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import type {
    StockDistributionBucket,
    StockDistributionDailyStat,
    StockDistributionResponse,
    Warehouse,
} from '@/types/api';

// ─── Config ─────────────────────────────────────────────────────────────────

/** Статус товара для фильтра (пусто = без фильтра). */
const PRODUCT_STATUSES: { key: string; label: string }[] = [
    { key: '', label: 'Все' },
    { key: 'active', label: 'Активный' },
    { key: 'new', label: 'Новинка' },
    { key: 'clearance', label: 'Слив' },
    { key: 'none', label: 'Без статуса' },
];

/** Сегменты стэк-бара: ключи qty/pct + цвет + подпись. */
const SEGMENTS: {
    qtyKey: keyof StockDistributionBucket;
    pctKey: keyof StockDistributionBucket;
    label: string;
    color: string;
}[] = [
    { qtyKey: 'ff_stock', pctKey: 'ff_stock_pct', label: 'На складе', color: 'var(--color-text-dim)' },
    { qtyKey: 'in_assembly', pctKey: 'in_assembly_pct', label: 'В сборке', color: 'var(--color-accent)' },
    { qtyKey: 'ready', pctKey: 'ready_pct', label: 'Готово', color: 'var(--color-success)' },
    { qtyKey: 'in_transit', pctKey: 'in_transit_pct', label: 'В пути', color: 'var(--color-warning)' },
];

/** Серии стек-графика истории (порядок = порядок потока, цвета из SEGMENTS). */
const HISTORY_SERIES: {
    key: keyof Omit<StockDistributionDailyStat, 'date'>;
    label: string;
    color: string;
}[] = [
    { key: 'ff_stock', label: 'На складе', color: 'var(--color-text-dim)' },
    { key: 'in_assembly', label: 'В сборке', color: 'var(--color-accent)' },
    { key: 'ready', label: 'Готово', color: 'var(--color-success)' },
    { key: 'in_transit', label: 'В пути', color: 'var(--color-warning)' },
];

/** Период истории: 30 / 90 дн / Всё (дефолт 90). */
type HistoryPeriodKey = '30' | '90' | 'all';

const HISTORY_PERIODS: { key: HistoryPeriodKey; label: string }[] = [
    { key: '30', label: '30 дн' },
    { key: '90', label: '90 дн' },
    { key: 'all', label: 'Всё' },
];

/** «2026-06-05» → «05.06» для оси X. */
function fmtDayTick(iso: string): string {
    return `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
}

/** ISO YYYY-MM-DD для «сегодня - days». */
function isoDaysAgo(days: number): string {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d.toISOString().slice(0, 10);
}

// ─── UI atoms ───────────────────────────────────────────────────────────────

function SkeletonCard({ height = 96 }: { height?: number }) {
    return (
        <div
            className="glass-card"
            style={{
                height,
                background: 'linear-gradient(90deg, rgba(0,0,0,0.04) 0%, rgba(0,0,0,0.07) 50%, rgba(0,0,0,0.04) 100%)',
                backgroundSize: '200% 100%',
                animation: 'shimmer 1.4s linear infinite',
            }}
        />
    );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
    return (
        <div
            style={{
                fontSize: 12,
                fontWeight: 600,
                color: 'var(--color-text-muted)',
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                marginBottom: 12,
            }}
        >
            {children}
        </div>
    );
}

/** 100% горизонтальный стэк-бар из 4 сегментов. */
function StackedBar({ bucket }: { bucket: StockDistributionBucket }) {
    return (
        <div
            style={{
                display: 'flex',
                width: '100%',
                height: 22,
                borderRadius: 8,
                overflow: 'hidden',
                background: 'var(--color-border)',
            }}
        >
            {SEGMENTS.map(seg => {
                const pct = Number(bucket[seg.pctKey]) || 0;
                if (pct <= 0) return null;
                const qty = Number(bucket[seg.qtyKey]) || 0;
                return (
                    <div
                        key={String(seg.qtyKey)}
                        style={{ width: `${pct}%`, background: seg.color, height: '100%' }}
                        title={`${seg.label}: ${formatNumber(qty, 0)} шт · ${formatNumber(pct, 1)}%`}
                    />
                );
            })}
        </div>
    );
}

/** Легенда: цвет + подпись + % + шт. */
function Legend({ bucket }: { bucket: StockDistributionBucket }) {
    return (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 10 }}>
            {SEGMENTS.map(seg => {
                const qty = Number(bucket[seg.qtyKey]) || 0;
                const pct = Number(bucket[seg.pctKey]) || 0;
                return (
                    <div key={String(seg.qtyKey)} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                        <span style={{ width: 10, height: 10, borderRadius: 3, background: seg.color, flexShrink: 0 }} />
                        <span style={{ color: 'var(--color-text-muted)' }}>{seg.label}</span>
                        <span style={{ fontWeight: 600 }}>{formatNumber(pct, 1)}%</span>
                        <span style={{ color: 'var(--color-text-dim)' }}>· {formatNumber(qty, 0)} шт</span>
                    </div>
                );
            })}
        </div>
    );
}

/** Строка разреза: label группы + стэк-бар + краткие подписи под ним. */
function GroupRow({ label, bucket }: { label: string; bucket: StockDistributionBucket }) {
    return (
        <div style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, marginBottom: 6 }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>{label}</span>
                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                    {formatNumber(bucket.total, 0)} шт
                </span>
            </div>
            <StackedBar bucket={bucket} />
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 6 }}>
                {SEGMENTS.map(seg => {
                    const pct = Number(bucket[seg.pctKey]) || 0;
                    if (pct <= 0) return null;
                    return (
                        <span key={String(seg.qtyKey)} style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                            <span style={{ color: seg.color, fontWeight: 600 }}>●</span> {seg.label} {formatNumber(pct, 1)}%
                        </span>
                    );
                })}
            </div>
        </div>
    );
}

/**
 * Карточка «Динамика по дням»: стек-area по 4 бакетам.
 * Свои состояния loading / error / empty — независимы от снимка.
 * EMPTY здесь — НЕ ошибка: снапшоты копятся вперёд, без бэкфилла.
 */
function HistoryCard({
    history,
    loading,
    error,
    period,
    onPeriodChange,
}: {
    history: StockDistributionDailyStat[] | null;
    loading: boolean;
    error: string;
    period: HistoryPeriodKey;
    onPeriodChange: (p: HistoryPeriodKey) => void;
}) {
    const isEmpty = !loading && !error && history !== null && history.length === 0;
    const hasData = !loading && !error && history !== null && history.length > 0;

    return (
        <div className="glass-card" style={{ padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <SectionTitle>Динамика по дням</SectionTitle>
                <div style={{ display: 'flex', gap: 4 }}>
                    {HISTORY_PERIODS.map(p => (
                        <button
                            key={p.key}
                            type="button"
                            className={`btn btn-sm ${period === p.key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => onPeriodChange(p.key)}
                        >
                            {p.label}
                        </button>
                    ))}
                </div>
            </div>

            {/* Loading: мини-скелетон */}
            {loading && (
                <div
                    style={{
                        height: 280,
                        borderRadius: 12,
                        background: 'linear-gradient(90deg, rgba(0,0,0,0.04) 0%, rgba(0,0,0,0.07) 50%, rgba(0,0,0,0.04) 100%)',
                        backgroundSize: '200% 100%',
                        animation: 'shimmer 1.4s linear infinite',
                    }}
                />
            )}

            {/* Error: тихо строкой */}
            {!loading && error && (
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', padding: '24px 0' }}>
                    Не удалось загрузить историю: {error}
                </div>
            )}

            {/* Empty: история ещё копится (не ошибка) */}
            {isEmpty && (
                <div style={{ textAlign: 'center', padding: '40px 16px' }}>
                    <div style={{ fontSize: 36, marginBottom: 12 }}>📈</div>
                    <div style={{ fontSize: 15, fontWeight: 500, marginBottom: 6 }}>История ещё копится</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 13, maxWidth: 420, margin: '0 auto' }}>
                        Снимки распределения сохраняются ежедневно — динамика появится со временем.
                    </div>
                </div>
            )}

            {/* Data: стек-area */}
            {hasData && history && (
                <ResponsiveContainer width="100%" height={280}>
                    <AreaChart data={history} margin={{ top: 5, right: 8, bottom: 5, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                        <XAxis dataKey="date" tickFormatter={fmtDayTick} tick={{ fontSize: 11 }} minTickGap={24} />
                        <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                        <RechartsTooltip
                            formatter={(value: number, name: string) => [formatNumber(value, 0), name]}
                            labelFormatter={(l: string) => formatDate(l)}
                            contentStyle={{ borderRadius: 12, border: '1px solid var(--color-border)', boxShadow: '0 4px 12px rgba(0,0,0,0.08)', fontSize: 13 }}
                        />
                        <RechartsLegend wrapperStyle={{ fontSize: 12 }} />
                        {HISTORY_SERIES.map(s => (
                            <Area
                                key={s.key}
                                type="monotone"
                                dataKey={s.key}
                                name={s.label}
                                stackId="stock"
                                stroke={s.color}
                                fill={s.color}
                                fillOpacity={0.35}
                            />
                        ))}
                    </AreaChart>
                </ResponsiveContainer>
            )}
        </div>
    );
}

// ─── Tab ────────────────────────────────────────────────────────────────────

export default function StockDistributionTab({ slug: _slug }: { slug: string }) {
    const [data, setData] = useState<StockDistributionResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [reloadTick, setReloadTick] = useState(0);

    const [warehouseId, setWarehouseId] = useState<number | ''>('');
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [productStatus, setProductStatus] = useState('');

    // ─── История по дням (отдельный фетч) ─────────────────────────────────
    const [history, setHistory] = useState<StockDistributionDailyStat[] | null>(null);
    const [historyLoading, setHistoryLoading] = useState(true);
    const [historyError, setHistoryError] = useState('');
    const [historyPeriod, setHistoryPeriod] = useState<HistoryPeriodKey>('90');

    // ─── Load FF-warehouses (один раз) ────────────────────────────────────
    useEffect(() => {
        const controller = new AbortController();
        api.getWarehouses()
            .then(whs => {
                if (controller.signal.aborted) return;
                setWarehouses(whs.filter(w => w.warehouse_type === 'FULFILLMENT'));
            })
            .catch(() => {});
        return () => controller.abort();
    }, []);

    // ─── Load distribution ────────────────────────────────────────────────
    const load = useCallback(async (signal: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const resp = await api.getAssemblyStockDistribution({
                warehouse_ids: warehouseId ? String(warehouseId) : undefined,
                product_status: productStatus || undefined,
            });
            if (signal.aborted) return;
            setData(resp);
        } catch (e: unknown) {
            if (signal.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (!signal.aborted) setLoading(false);
        }
    }, [warehouseId, productStatus]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load, reloadTick]);

    // ─── Load history (период + те же фильтры, что снимок) ────────────────
    useEffect(() => {
        const controller = new AbortController();
        const { signal } = controller;
        setHistoryLoading(true);
        setHistoryError('');
        api.getAssemblyStockDistributionHistory({
            date_from: historyPeriod === 'all' ? undefined : isoDaysAgo(historyPeriod === '30' ? 30 : 90),
            warehouse_ids: warehouseId ? String(warehouseId) : undefined,
            product_status: productStatus || undefined,
        })
            .then(resp => {
                if (signal.aborted) return;
                setHistory(resp.daily);
            })
            .catch((e: unknown) => {
                if (signal.aborted) return;
                setHistoryError(e instanceof Error ? e.message : 'Ошибка загрузки');
            })
            .finally(() => {
                if (!signal.aborted) setHistoryLoading(false);
            });
        return () => controller.abort();
    }, [warehouseId, productStatus, historyPeriod]);

    // ─── Derived ──────────────────────────────────────────────────────────
    const total = data?.total;
    const byWarehouse = useMemo(() => data?.by_warehouse ?? [], [data]);
    const byStatus = useMemo(() => data?.by_status ?? [], [data]);
    const isEmpty = !!total && total.total === 0;

    // ─── Render ───────────────────────────────────────────────────────────

    return (
        <div className="animate-in">
            {/* Фильтры: склад + статус товара */}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 16 }}>
                <select
                    className="form-input"
                    style={{ width: 'auto', minWidth: 150 }}
                    value={warehouseId}
                    onChange={e => setWarehouseId(e.target.value ? Number(e.target.value) : '')}
                >
                    <option value="">Все склады</option>
                    {warehouses.map(w => (
                        <option key={w.id} value={w.id}>{w.name}</option>
                    ))}
                </select>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {PRODUCT_STATUSES.map(s => (
                        <button
                            key={s.key || 'all'}
                            type="button"
                            className={`btn btn-sm ${productStatus === s.key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setProductStatus(s.key)}
                        >
                            {s.label}
                        </button>
                    ))}
                </div>
            </div>

            {/* Error */}
            {error && !loading && (
                <div className="glass-card" style={{ padding: 20, color: 'var(--color-danger)', marginBottom: 16 }}>
                    <div style={{ fontWeight: 600, marginBottom: 8 }}>Не удалось загрузить распределение остатков</div>
                    <div style={{ fontSize: 13, marginBottom: 12 }}>{error}</div>
                    <button type="button" className="btn btn-danger btn-sm" onClick={() => setReloadTick(t => t + 1)}>
                        Повторить
                    </button>
                </div>
            )}

            {/* Loading */}
            {loading && !error && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    <SkeletonCard height={120} />
                    <SkeletonCard height={200} />
                    <SkeletonCard height={200} />
                </div>
            )}

            {/* Empty */}
            {!loading && !error && data && isEmpty && (
                <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>📦</div>
                    <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Нет товара в обороте</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                        Попробуйте убрать фильтр склада или статуса товара
                    </div>
                </div>
            )}

            {/* Data */}
            {!loading && !error && data && total && !isEmpty && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                    {/* Сводка */}
                    <div className="glass-card" style={{ padding: 20 }}>
                        <SectionTitle>
                            Где сейчас товар (всего {formatNumber(total.total, 0)} шт)
                        </SectionTitle>
                        <StackedBar bucket={total} />
                        <Legend bucket={total} />
                    </div>

                    {/* По складам */}
                    {byWarehouse.length > 0 && (
                        <div className="glass-card" style={{ padding: 20 }}>
                            <SectionTitle>По складам</SectionTitle>
                            {byWarehouse.map(g => (
                                <GroupRow key={g.key} label={g.label} bucket={g.bucket} />
                            ))}
                        </div>
                    )}

                    {/* По статусу товара */}
                    {byStatus.length > 0 && (
                        <div className="glass-card" style={{ padding: 20 }}>
                            <SectionTitle>По статусу товара</SectionTitle>
                            {byStatus.map(g => (
                                <GroupRow key={g.key} label={g.label} bucket={g.bucket} />
                            ))}
                        </div>
                    )}

                    {/* Динамика по дням */}
                    <HistoryCard
                        history={history}
                        loading={historyLoading}
                        error={historyError}
                        period={historyPeriod}
                        onPeriodChange={setHistoryPeriod}
                    />
                </div>
            )}

            <style jsx>{`
                @keyframes shimmer {
                    0% {
                        background-position: 200% 0;
                    }
                    100% {
                        background-position: -200% 0;
                    }
                }
            `}</style>
        </div>
    );
}
