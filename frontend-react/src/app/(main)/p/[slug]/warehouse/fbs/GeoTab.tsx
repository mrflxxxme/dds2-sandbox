'use client';
/**
 * Вкладка «География» — куда едут заказы FBS, как быстро и что съедает время.
 *
 * Вкладка «Этапы» отвечает «сколько длится шаг». Здесь три соседних вопроса,
 * которые без географии не разрешимы: КУДА едет (округ покупателя), КАКИМ
 * маршрутом (узлы СЦ) и ЧТО тормозит (перевалки, удержание на узлах).
 *
 * 🔴 Главное, что экран обязан доносить: сравнивать склады по скорости
 * доставки НАПРЯМУЮ нельзя — у них разная география, и разница вдвое может
 * означать «возит дальше», а не «работает хуже». Поэтому матрица
 * «склад × округ» стоит первой: только внутри одного столбца сравнение честное.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
    Bar,
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
import type { Column } from '@/components/DataTable';
import type { FbsGeoAnalytics, FbsWarehouse } from '@/types/api';
import { isoDaysAgo, todayIso } from './OrdersStats';

/** Псевдо-направление «итог по складу» — приходит с бэкенда как обычная клетка. */
const ALL_DIRECTIONS = 'Все направления';
/** Псевдо-округ несшитых: в матрице ему не место, он живёт в «Направлениях». */
const UNMATCHED = 'Не сшито со статистикой';

const PRESETS: { key: string; label: string; days: number }[] = [
    { key: '30', label: '30 дней', days: 30 },
    { key: '90', label: '90 дней', days: 90 },
    { key: '180', label: 'Полгода', days: 180 },
];

/** Дни словами: до суток — часы, дальше — дни. Голые доли суток нечитаемы. */
function fmtDays(days: number | null | undefined): string {
    if (days === null || days === undefined) return '—';
    if (days < 1) return `${formatNumber(days * 24, 1)} ч`;
    return `${formatNumber(days, 1)} дн`;
}

function fmtHours(hours: number | null | undefined): string {
    if (hours === null || hours === undefined) return '—';
    if (hours < 48) return `${formatNumber(hours, 1)} ч`;
    return `${formatNumber(hours / 24, 1)} дн`;
}

function pct(part: number, total: number): string {
    return total > 0 ? `${formatNumber((part / total) * 100, 0)}%` : '—';
}

interface Props {
    warehouses: FbsWarehouse[];
    refreshTick: number;
}

export default function GeoTab({ warehouses, refreshTick }: Props) {
    const [data, setData] = useState<FbsGeoAnalytics | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [preset, setPreset] = useState('90');
    const [dateFrom, setDateFrom] = useState(() => isoDaysAgo(90));
    const [dateTo, setDateTo] = useState(() => todayIso());
    const [wbWarehouseId, setWbWarehouseId] = useState<number | ''>('');

    const applyPreset = useCallback((key: string) => {
        const found = PRESETS.find(p => p.key === key);
        if (!found) return;
        setPreset(key);
        setDateFrom(isoDaysAgo(found.days));
        setDateTo(todayIso());
    }, []);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getFbsGeoAnalytics({
                dateFrom,
                dateTo,
                wbWarehouseId: wbWarehouseId === '' ? undefined : wbWarehouseId,
            });
            if (signal?.aborted) return;
            setData(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки географии');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [dateFrom, dateTo, wbWarehouseId]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load, refreshTick]);

    /** Матрица в «широкую» форму: строка — склад, колонка — округ. */
    const matrixRows = useMemo(() => {
        if (!data) return [];
        const byWarehouse = new Map<string, Record<string, unknown>>();
        for (const cell of data.matrix) {
            const key = String(cell.wb_warehouse_id ?? 'none');
            let row = byWarehouse.get(key);
            if (!row) {
                row = { warehouse_name: cell.warehouse_name };
                byWarehouse.set(key, row);
            }
            row[cell.label] = cell.median_days;
            row[`${cell.label}__n`] = cell.orders;
        }
        return Array.from(byWarehouse.values());
    }, [data]);

    /**
     * Столбцы матрицы: сначала итог по складу, затем округа по убыванию объёма.
     *
     * 🔴 Итог обязателен и стоит первым: клетки по округам считаются только на
     * сшитых с зеркалом статистики заданиях, и склад, у которого сшитых путей
     * нет вовсе, иначе исчезал из таблицы целиком (так пропадал «Газпром
     * Москва»). Порядок округов берётся из `directions` — по объёму.
     */
    const matrixColumns = useMemo<Column[]>(() => {
        if (!data) return [];
        const okrugs = [
            ALL_DIRECTIONS,
            ...data.directions.map(d => d.label).filter(l => l !== UNMATCHED),
        ];
        return [
            { key: 'warehouse_name', label: 'Склад отгрузки', align: 'left' },
            ...okrugs.map<Column>(okrug => ({
                key: okrug,
                label: okrug === ALL_DIRECTIONS ? 'Все направления' : okrug.replace(' федеральный округ', ' ФО'),
                align: 'right',
                headerWrap: true,
                sortingFn: 'basic',
                render: (value: unknown, row: Record<string, unknown>) => {
                    const n = Number(row[`${okrug}__n`] ?? 0);
                    if (!n) return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
                    return <span title={`Заказов: ${formatNumber(n, 0)}`}>{fmtDays(value as number)}</span>;
                },
                exportValue: (row: Record<string, unknown>) =>
                    row[okrug] == null ? '' : Number(row[okrug]),
            })),
        ];
    }, [data]);

    const directionColumns = useMemo<Column[]>(() => [
        { key: 'label', label: 'Округ покупателя', align: 'left' },
        { key: 'orders', label: 'Замеров', align: 'right', format: 'number' },
        {
            key: 'median_days', label: 'Медиана', align: 'right', sortingFn: 'basic',
            render: (v: unknown) => fmtDays(v as number),
        },
        {
            key: 'p90_days', label: 'Хвост p90', align: 'right', sortingFn: 'basic',
            render: (v: unknown) => fmtDays(v as number),
        },
        {
            key: 'avg_hops', label: 'Перевалок', align: 'right', sortingFn: 'basic',
            render: (v: unknown) => (v == null ? '—' : formatNumber(v as number, 1)),
        },
        {
            key: 'sla_in_time', label: 'В срок WB', align: 'right', headerWrap: true,
            headerTitle: 'Доля заказов, готовых к вручению не позже обещанной WB даты',
            render: (_v: unknown, row: Record<string, unknown>) => {
                const total = Number(row.sla_total ?? 0);
                return total ? `${pct(Number(row.sla_in_time ?? 0), total)} из ${formatNumber(total, 0)}` : '—';
            },
        },
        {
            key: 'refused', label: 'Отказы', align: 'right', headerWrap: true,
            headerTitle: 'Только по созревшей когорте — свежие заказы ещё в пути',
            render: (_v: unknown, row: Record<string, unknown>) => {
                const matured = Number(row.matured ?? 0);
                return matured
                    ? `${pct(Number(row.refused ?? 0), matured)} из ${formatNumber(matured, 0)}`
                    : '—';
            },
        },
    ], []);

    const destinationColumns = useMemo<Column[]>(() => [
        { key: 'label', label: 'Город назначения', align: 'left' },
        { key: 'orders', label: 'Заказов', align: 'right', format: 'number' },
        {
            key: 'median_days', label: 'Медиана', align: 'right', sortingFn: 'basic',
            render: (v: unknown) => fmtDays(v as number),
        },
        {
            key: 'p90_days', label: 'Хвост p90', align: 'right', sortingFn: 'basic',
            render: (v: unknown) => fmtDays(v as number),
        },
    ], []);

    const nodeColumns = useMemo<Column[]>(() => [
        { key: 'label', label: 'Узел маршрута', align: 'left' },
        { key: 'passes', label: 'Проходов', align: 'right', format: 'number' },
        {
            key: 'median_hours', label: 'Держит груз', align: 'right', sortingFn: 'basic',
            headerTitle: 'Медиана от прибытия до отправки. Прочерк — в истории одно событие, удержание неизвестно',
            render: (v: unknown, row: Record<string, unknown>) =>
                Number(row.measured ?? 0) ? fmtHours(v as number) : <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        {
            key: 'p90_hours', label: 'Хвост p90', align: 'right', sortingFn: 'basic',
            render: (v: unknown, row: Record<string, unknown>) =>
                Number(row.measured ?? 0) ? fmtHours(v as number) : '—',
        },
    ], []);

    const hopsChart = useMemo(
        () => (data?.hops ?? []).map(h => ({
            hops: h.hops, days: h.median_days ?? 0, orders: h.orders,
        })),
        [data],
    );

    if (loading && !data) {
        return <div className="glass-card" style={{ padding: 24 }}>Загрузка географии…</div>;
    }
    if (error && !data) {
        return (
            <div className="glass-card" style={{ padding: 24 }}>
                <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>
                <button className="btn btn-secondary btn-sm" onClick={() => load()}>Повторить</button>
            </div>
        );
    }
    if (!data) return null;

    const matched = data.coverage.orders_matched;
    const total = data.coverage.orders_total;

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {error && (
                <div className="glass-card" style={{ padding: 12, color: 'var(--color-warning)' }}>
                    {error} — показаны данные прошлой загрузки.
                </div>
            )}

            {/* ── Фильтры ── */}
            <div className="glass-card" style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'flex-end' }}>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Период завершения пути</label>
                    <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                        {PRESETS.map(p => (
                            <button
                                key={p.key}
                                className={`btn btn-sm ${preset === p.key ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => applyPreset(p.key)}
                            >
                                {p.label}
                            </button>
                        ))}
                    </div>
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>С</label>
                    <input className="form-input" type="date" style={{ width: 150 }}
                        value={dateFrom} onChange={e => { setDateFrom(e.target.value); setPreset(''); }} />
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>По</label>
                    <input className="form-input" type="date" style={{ width: 150 }}
                        value={dateTo} onChange={e => { setDateTo(e.target.value); setPreset(''); }} />
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Склад WB</label>
                    <select className="form-input" style={{ width: 200 }} value={wbWarehouseId}
                        onChange={e => setWbWarehouseId(e.target.value === '' ? '' : Number(e.target.value))}>
                        <option value="">Все склады</option>
                        {warehouses.map(w => (
                            <option key={w.id} value={w.wb_warehouse_id}>{w.name || `#${w.wb_warehouse_id}`}</option>
                        ))}
                    </select>
                </div>
                {loading && (
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)', paddingBottom: 12 }}>обновляем…</span>
                )}
            </div>

            {/* ── Матрица: главный операционный ответ ── */}
            <div className="glass-card">
                <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Откуда куда возить</h3>
                <p style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: '6px 0 12px' }}>
                    Медиана пути «первая сортировка → готов к вручению». 🔴 Сравнивать склады между собой
                    можно ТОЛЬКО внутри одного столбца: у складов разная география, и разница вдвое чаще
                    означает «возит дальше», а не «работает хуже». Наведите на значение — число заказов.
                </p>
                <TanStackDataTable
                    columns={matrixColumns}
                    data={matrixRows}
                    exportName={`FBS_география_склад_округ_${data.date_from}_${data.date_to}`}
                    enableSorting
                    emptyText="За период ни один путь не завершился"
                />
            </div>

            {/* ── Направления ── */}
            <div className="glass-card">
                <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Направления</h3>
                <p style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: '6px 0 12px' }}>
                    Округ покупателя определяется сшивкой с отчётом статистики WB — сшито{' '}
                    {formatNumber(matched, 0)} из {formatNumber(total, 0)} заданий ({pct(matched, total)}).
                    Несшитое — это в основном отменённые заказы: в отчёт продаж WB они не попадают
                    вовсе. Они собраны отдельной строкой и в матрицу выше не идут; вопрос «куда едет»
                    без пробелов закрывает таблица «Куда едет».
                    Отказы считаются только по заказам старше {data.maturity_days} дней: свежие ещё в пути,
                    и доля по ним была бы неправдой.
                </p>
                <TanStackDataTable
                    columns={directionColumns}
                    data={data.directions}
                    exportName={`FBS_направления_${data.date_from}_${data.date_to}`}
                    enableSorting
                    emptyText="Нет завершённых путей за период"
                />
            </div>

            {/* ── Города назначения: полное покрытие, без дыр сшивки ── */}
            <div className="glass-card">
                <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Куда едет</h3>
                <p style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: '6px 0 12px' }}>
                    Последняя точка маршрута перед выдачей — известна для ВСЕХ заказов с
                    завершённым путём, поэтому дыр сшивки здесь нет. 🔴 Это узел сети WB
                    (сортировочный центр или ПВЗ), а не адрес покупателя: «Внуково Почта России» —
                    точка сети, а не место, где живёт клиент.
                </p>
                <TanStackDataTable
                    columns={destinationColumns}
                    data={data.destinations}
                    exportName={`FBS_города_назначения_${data.date_from}_${data.date_to}`}
                    enableSorting
                    enablePagination={data.destinations.length > 25}
                    pageSize={25}
                    emptyText="За период ни один путь не завершился"
                />
            </div>

            {/* ── Перевалки ── */}
            <div className="glass-card">
                <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Перевалки</h3>
                <p style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: '6px 0 12px' }}>
                    Сколько сортировочных центров прошёл заказ и сколько это стоило времени.
                    Единственный рычаг раздела, на который можно повлиять: маршрут задаётся складом отгрузки.
                </p>
                {hopsChart.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                        Нет данных за период
                    </div>
                ) : (
                    <ResponsiveContainer width="100%" height={240}>
                        <ComposedChart data={hopsChart} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                            <XAxis dataKey="hops" tick={{ fontSize: 12 }}
                                label={{ value: 'узлов в маршруте', position: 'insideBottom', offset: -4, fontSize: 11 }} />
                            <YAxis yAxisId="days" tick={{ fontSize: 12 }} />
                            <YAxis yAxisId="orders" orientation="right" tick={{ fontSize: 12 }} />
                            <RechartsTooltip
                                formatter={(value: number, name: string) =>
                                    name === 'Заказов' ? [formatNumber(value, 0), name] : [fmtDays(value), name]}
                                labelFormatter={(v) => `${v} узлов маршрута`}
                            />
                            <RechartsLegend wrapperStyle={{ fontSize: 12 }} />
                            <Bar yAxisId="orders" dataKey="orders" name="Заказов"
                                fill="var(--color-border)" radius={[4, 4, 0, 0]} maxBarSize={48} />
                            <Line yAxisId="days" type="monotone" dataKey="days" name="Медиана пути"
                                stroke="var(--color-accent)" strokeWidth={2} />
                        </ComposedChart>
                    </ResponsiveContainer>
                )}
            </div>

            {/* ── Узлы ── */}
            <div className="glass-card">
                <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Узлы маршрута</h3>
                <p style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: '6px 0 12px' }}>
                    Сколько заказов прошло через сортировочный центр и сколько он держит груз —
                    от прибытия до отправки. Прочерк означает «неизвестно»: в истории по этому узлу
                    одно событие, а не «прошёл мгновенно».
                </p>
                <TanStackDataTable
                    columns={nodeColumns}
                    data={data.nodes}
                    exportName={`FBS_узлы_маршрута_${data.date_from}_${data.date_to}`}
                    enableSorting
                    emptyText="Нет данных за период"
                />
            </div>
        </div>
    );
}
