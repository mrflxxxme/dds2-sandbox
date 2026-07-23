'use client';

/**
 * Раздел «Стоимость ₽/шт» на «Листе логиста».
 *
 * Стоимость логистики (забора, `pickup_cost`) в пересчёте НА ШТУКУ и НА КОРОБ,
 * в разрезе категории и бренда, плюс динамика за период (по дате отгрузки).
 * Стоимость перевозки — на всю отгрузку; разносим по позициям пропорционально
 * штукам (₽/шт) и коробам (₽/короб). Данные — из заборов и позиций отгрузок.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
    CartesianGrid,
    ComposedChart,
    Legend as RechartsLegend,
    Line,
    ResponsiveContainer,
    Tooltip as RechartsTooltip,
    XAxis,
    YAxis,
} from 'recharts';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import KpiCard from '@/components/KpiCard';
import type { Column } from '@/components/DataTable';
import type { LogisticsCostPerUnitResponse } from '@/types/api';

type GroupBy = 'day' | 'week' | 'month';

const GROUPS: { key: GroupBy; label: string }[] = [
    { key: 'day', label: 'День' },
    { key: 'week', label: 'Неделя' },
    { key: 'month', label: 'Месяц' },
];

// Decimal-поля бэка приходят строкой — коэрсим перед формат/арифметикой (см. learnings).
const num = (v: number | string | null | undefined): number => (v == null ? 0 : Number(v));
const rub = (v: number | string | null | undefined, digits = 2): string =>
    v == null ? '—' : formatNumber(num(v), digits) + ' ₽';

export default function LogisticsCostPerUnit({ dateFrom, dateTo, brand }: {
    dateFrom: string;
    dateTo: string;
    brand: string;
}) {
    const [groupBy, setGroupBy] = useState<GroupBy>('month');
    const [category, setCategory] = useState('');
    const [data, setData] = useState<LogisticsCostPerUnitResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const loadSeq = useRef(0);

    const load = useCallback(async () => {
        const seq = ++loadSeq.current;
        setLoading(true);
        setError('');
        try {
            const r = await api.getLogisticsCostPerUnit({
                date_from: dateFrom || undefined,
                date_to: dateTo || undefined,
                brands: brand || undefined,
                categories: category || undefined,
                group_by: groupBy,
            });
            if (seq !== loadSeq.current) return; // устаревший ответ (быстрые клики по фильтрам)
            setData(r);
        } catch (e: unknown) {
            if (seq !== loadSeq.current) return;
            setError(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            if (seq === loadSeq.current) setLoading(false);
        }
    }, [dateFrom, dateTo, brand, category, groupBy]);

    useEffect(() => { load(); }, [load]);

    const chartData = (data?.dynamics ?? []).map(p => ({
        period: p.period,
        cost_per_unit: num(p.cost_per_unit),
        cost_per_box: num(p.cost_per_box),
    }));

    const cols: Column[] = [
        { key: 'name', label: 'Название' },
        { key: 'units', label: 'Штук', render: (v: number) => formatNumber(v, 0) },
        { key: 'boxes', label: 'Коробов', render: (v: number) => formatNumber(v, 0) },
        { key: 'cost_per_unit', label: '₽/шт', render: (v: number | null) => rub(v) },
        { key: 'cost_per_box', label: '₽/короб', render: (v: number | null) => rub(v) },
        { key: 'total_cost', label: 'Стоимость', render: (v: number) => rub(v, 0) },
    ];

    const s = data?.summary;
    const isEmpty = !s || s.shipments === 0;

    return (
        <div className="animate-in">
            {/* Controls: период группировки + категория */}
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 16 }}>
                <div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 6 }}>Динамика по</div>
                    <div style={{ display: 'flex', gap: 0 }}>
                        {GROUPS.map((g, i) => (
                            <button
                                key={g.key}
                                className={`btn btn-sm ${groupBy === g.key ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setGroupBy(g.key)}
                                style={{ borderRadius: i === 0 ? '8px 0 0 8px' : i === GROUPS.length - 1 ? '0 8px 8px 0' : 0 }}
                            >
                                {g.label}
                            </button>
                        ))}
                    </div>
                </div>
                <div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 6 }}>Категория</div>
                    <select className="form-input" value={category} onChange={e => setCategory(e.target.value)} style={{ minWidth: 200 }}>
                        <option value="">Все категории</option>
                        {(data?.categories_available ?? []).map(c => (
                            <option key={c} value={c}>{c}</option>
                        ))}
                    </select>
                </div>
            </div>

            {loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            ) : error ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-danger)' }}>{error}</div>
            ) : isEmpty ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Нет отгрузок со стоимостью за период
                </div>
            ) : (
                <>
                    {data!.truncated && (
                        <div style={{ fontSize: 12, color: 'var(--color-warning)', marginBottom: 12 }}>
                            Период шире лимита — показана часть отгрузок. Сузьте диапазон дат для полной картины.
                        </div>
                    )}

                    {/* KPI */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 16 }}>
                        <KpiCard label="₽ на штуку" value={rub(s!.cost_per_unit)} />
                        <KpiCard label="₽ на короб" value={rub(s!.cost_per_box)} />
                        <KpiCard label="Общая стоимость" value={formatNumber(num(s!.total_cost), 0)} sub="₽" />
                        <KpiCard label="Штук" value={formatNumber(s!.total_units, 0)} />
                        <KpiCard label="Коробов" value={formatNumber(s!.total_boxes, 0)} />
                        <KpiCard label="Отправок" value={formatNumber(s!.shipments, 0)} />
                    </div>

                    {/* Динамика за период */}
                    <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
                            Динамика стоимости за период{brand ? ` — ${brand}` : ''}{category ? ` / ${category}` : ''}
                        </div>
                        {chartData.length > 0 ? (
                            <ResponsiveContainer width="100%" height={300}>
                                <ComposedChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                                    <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                                    <XAxis dataKey="period" tick={{ fontSize: 11 }} angle={-25} textAnchor="end" height={60} interval={0} />
                                    <YAxis tick={{ fontSize: 12 }} />
                                    <RechartsTooltip
                                        formatter={(value: number, n: string) => [formatNumber(value, 2) + ' ₽', n === 'cost_per_unit' ? '₽/шт' : '₽/короб']}
                                        contentStyle={{ background: 'var(--color-bg-primary)', border: '1px solid var(--color-border)', borderRadius: 8, fontSize: 13 }}
                                    />
                                    <RechartsLegend formatter={(v: string) => (v === 'cost_per_unit' ? '₽/шт' : '₽/короб')} />
                                    <Line type="monotone" dataKey="cost_per_unit" name="cost_per_unit" stroke="var(--color-accent)" strokeWidth={2} dot={{ r: 3 }} />
                                    <Line type="monotone" dataKey="cost_per_box" name="cost_per_box" stroke="var(--color-warning)" strokeWidth={2} dot={{ r: 3 }} />
                                </ComposedChart>
                            </ResponsiveContainer>
                        ) : (
                            <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Нет точек за период</div>
                        )}
                    </div>

                    {/* Таблицы по категории и бренду */}
                    <div style={{ marginBottom: 16 }}>
                        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>По категориям</div>
                        <TanStackDataTable columns={cols} data={data!.by_category} emptyText="Нет данных" emptyIcon="📦" exportName="logistics_cost_by_category" />
                    </div>
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>По брендам</div>
                        <TanStackDataTable columns={cols} data={data!.by_brand} emptyText="Нет данных" emptyIcon="🏷️" exportName="logistics_cost_by_brand" />
                    </div>
                </>
            )}
        </div>
    );
}
