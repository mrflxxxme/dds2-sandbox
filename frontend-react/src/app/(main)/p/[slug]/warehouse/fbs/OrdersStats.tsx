'use client';
/**
 * Статистика заказов FBS за период: выручка, разрезы, график и доля в объёме
 * воронки продаж.
 *
 * Сводка раскрыта по умолчанию — это первое, что хотят видеть на вкладке;
 * свернуть можно кнопкой. Одна агрегирующая ручка на весь блок, поэтому
 * открытое состояние не стоит заметного трафика.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import { exportToExcel, formatDate, formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { FbsOrderStatRow, FbsOrderStats } from '@/types/api';
import { SUPPLIER_STATUS_LABEL, num } from './fbsShared';

const MSK_TZ = 'Europe/Moscow';
const dayTickFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: MSK_TZ, day: '2-digit', month: '2-digit' });
const dayFullFmt = new Intl.DateTimeFormat('ru-RU', {
    timeZone: MSK_TZ, weekday: 'short', day: 'numeric', month: 'long',
});

/** Бэкенд отдаёт дату без времени («2026-07-20») — фиксируем полночь UTC перед Date. */
function toTs(day: string): number {
    return new Date(/[Z+]|T/.test(day) ? day : `${day}T00:00:00Z`).getTime();
}

/** Пресеты периода: то, что реально спрашивают, — без ручного ввода дат. */
const PRESETS: { key: string; label: string; days: number }[] = [
    { key: '7', label: '7 дней', days: 7 },
    { key: '30', label: '30 дней', days: 30 },
    { key: '90', label: '90 дней', days: 90 },
];

/** Сколько строк разреза видно сразу — карточки держат общую высоту. */
const COLLAPSED_ROWS = 5;
/** Потолок раскрытого разреза; всё, что глубже, забирают из Excel. */
const TOP_ROWS = 25;

/** Разрезы одним списком: порядок карточек и источник строк. */
const CUTS: { key: string; title: string; rows: (d: FbsOrderStats) => FbsOrderStatRow[] }[] = [
    { key: 'brand', title: 'По брендам', rows: d => d.by_brand },
    { key: 'subject', title: 'По предметам', rows: d => d.by_subject },
    { key: 'subcategory', title: 'По под-категориям', rows: d => d.by_subcategory },
    {
        key: 'status',
        title: 'По статусам',
        // Машинные коды наружу не показываем — словарь тот же, что в списке заданий.
        rows: d => d.by_status.map(r => ({ ...r, label: SUPPLIER_STATUS_LABEL[r.label] ?? r.label })),
    },
];

/** Все разрезы в один файл — по листу на разрез (кнопка на секции, не на каждой карточке). */
function exportCuts(data: FbsOrderStats) {
    const columns = [
        { key: 'label', label: 'Значение' },
        { key: 'orders_count', label: 'Заказов, шт' },
        { key: 'orders_sum', label: 'Выручка, ₽', exportValue: (r: FbsOrderStatRow) => num(r.orders_sum) },
    ];
    const [first, ...rest] = CUTS;
    exportToExcel(
        first.rows(data),
        `FBS_разрезы_${data.date_from}_${data.date_to}`,
        columns,
        rest.map(cut => ({ sheetName: cut.title, data: cut.rows(data), columns })),
        first.title,
    );
}

/**
 * Календарная дата в МСК — контракт ручки статистики именно московский.
 *
 * `toISOString()` здесь был бы ошибкой: он даёт UTC-дату, и с 00:00 до 03:00 МСК
 * «сегодня» на фронте отставало на сутки — текущий день целиком выпадал из
 * выборки, а пользователь видел вчерашний период под видом сегодняшнего.
 * `en-CA` выбран потому, что его формат — ровно `YYYY-MM-DD`.
 */
const mskDateFmt = new Intl.DateTimeFormat('en-CA', { timeZone: MSK_TZ });

function mskDate(d: Date): string {
    return mskDateFmt.format(d);
}

function isoDaysAgo(days: number): string {
    const d = new Date();
    d.setDate(d.getDate() - (days - 1));
    return mskDate(d);
}

function todayIso(): string {
    return mskDate(new Date());
}

interface Props {
    /** Склад продавца из фильтров вкладки: статистика следует за ним. */
    wbWarehouseId: number | '';
    /** Счётчик тиков автообновления страницы. */
    refreshTick: number;
}

export default function OrdersStats({ wbWarehouseId, refreshTick }: Props) {
    const [open, setOpen] = useState(true);
    const [preset, setPreset] = useState('30');
    const [dateFrom, setDateFrom] = useState(() => isoDaysAgo(30));
    const [dateTo, setDateTo] = useState(todayIso);
    const [data, setData] = useState<FbsOrderStats | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const applyPreset = useCallback((key: string) => {
        setPreset(key);
        const found = PRESETS.find(p => p.key === key);
        if (!found) return;
        setDateFrom(isoDaysAgo(found.days));
        setDateTo(todayIso());
    }, []);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getFbsOrderStats({
                dateFrom,
                dateTo,
                wbWarehouseId: wbWarehouseId === '' ? undefined : wbWarehouseId,
            });
            if (signal?.aborted) return;
            setData(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки статистики');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [dateFrom, dateTo, wbWarehouseId]);

    useEffect(() => {
        if (!open) return;
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [open, load]);

    // Автообновление: загрузчик через ref, иначе эффект срабатывал бы на смену
    // периода и дублировал обычную загрузку.
    const reloadRef = useRef<(signal?: AbortSignal) => void>(() => {});
    useEffect(() => { reloadRef.current = load; });
    // `open` в зависимостях быть НЕ должен: с ним раскрытие блока давало второй
    // запрос сразу вслед за первым (эффект выше уже грузит). Реагируем только на
    // смену тика, а закрытое состояние гасим сравнением с предыдущим значением.
    const lastTickRef = useRef(refreshTick);
    useEffect(() => {
        if (refreshTick === lastTickRef.current) return;
        lastTickRef.current = refreshTick;
        if (!open) return;
        const controller = new AbortController();
        reloadRef.current(controller.signal);
        return () => controller.abort();
    }, [refreshTick, open]);

    const totals = useMemo(() => {
        if (!data) return null;
        const sum = num(data.orders_sum);
        const count = data.orders_count;
        const cancelledSum = num(data.cancelled_sum);
        return {
            count,
            sum,
            avg: count > 0 ? sum / count : 0,
            cancelledCount: data.cancelled_count,
            cancelledSum,
            liveCount: count - data.cancelled_count,
            liveSum: sum - cancelledSum,
        };
    }, [data]);

    /**
     * Доля FBS. Числитель и знаменатель — за ОДИНАКОВЫЕ дни: воронка отстаёт от
     * заданий, и полный период FBS, делённый на неполный период воронки, дал бы
     * кратно завышенную цифру. Отрезок считает бэкенд, здесь только деление.
     */
    const funnel = useMemo(() => {
        const f = data?.funnel;
        if (!f?.has_data) return null;
        const fSum = num(f.orders_sum);
        return {
            count: f.orders_count,
            sum: fSum,
            fbsCount: f.fbs_orders_count,
            fbsSum: num(f.fbs_orders_sum),
            shareCount: f.orders_count > 0 ? (f.fbs_orders_count / f.orders_count) * 100 : 0,
            shareSum: fSum > 0 ? (num(f.fbs_orders_sum) / fSum) * 100 : 0,
            fullPeriod: f.full_period,
            from: f.covered_from ?? null,
            to: f.covered_to ?? null,
        };
    }, [data]);

    /** Точки графика: ось X числовая (время), иначе пропуски дней рисуются равномерно. */
    const chartData = useMemo(
        () => (data?.by_day ?? []).map(d => ({
            ts: toTs(d.day),
            sum: num(d.orders_sum),
            count: d.orders_count,
        })),
        [data],
    );

    if (!open) {
        return (
            <div className="glass-card" style={{ padding: 12, marginBottom: 12 }}>
                <button className="btn btn-sm" onClick={() => setOpen(true)}>
                    📊 Показать статистику за период
                </button>
            </div>
        );
    }

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
            {/* Период */}
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12 }}>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Период</label>
                    <div style={{ display: 'flex', gap: 0 }}>
                        {PRESETS.map((p, i) => (
                            <button
                                key={p.key}
                                className={`btn btn-sm ${preset === p.key ? 'btn-primary' : 'btn-secondary'}`}
                                style={{
                                    borderTopLeftRadius: i === 0 ? undefined : 0,
                                    borderBottomLeftRadius: i === 0 ? undefined : 0,
                                    borderTopRightRadius: i === PRESETS.length - 1 ? undefined : 0,
                                    borderBottomRightRadius: i === PRESETS.length - 1 ? undefined : 0,
                                }}
                                onClick={() => applyPreset(p.key)}
                            >
                                {p.label}
                            </button>
                        ))}
                    </div>
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>С</label>
                    <input
                        className="form-input" type="date" style={{ width: 150 }}
                        value={dateFrom}
                        onChange={e => { setDateFrom(e.target.value); setPreset(''); }}
                    />
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>По</label>
                    <input
                        className="form-input" type="date" style={{ width: 150 }}
                        value={dateTo}
                        onChange={e => { setDateTo(e.target.value); setPreset(''); }}
                    />
                </div>
                <div style={{ flex: 1 }} />
                <button className="btn btn-secondary btn-sm" onClick={() => load()} disabled={loading}>
                    {loading ? 'Обновление...' : '🔄 Пересчитать'}
                </button>
                <button className="btn btn-sm" onClick={() => setOpen(false)}>Свернуть</button>
            </div>

            {error ? (
                <div style={{ padding: 16, color: 'var(--color-danger)' }}>{error}</div>
            ) : loading && !data ? (
                <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Загрузка статистики...
                </div>
            ) : !data || !totals ? null : totals.count === 0 ? (
                <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    📭 За выбранный период заказов FBS не было
                    {wbWarehouseId !== '' ? ' на этом складе продавца' : ''}.
                </div>
            ) : (
                <>
                    <div style={{
                        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
                        gap: 12, marginBottom: 12,
                    }}>
                        <Kpi label="Заказов, шт" value={formatNumber(totals.count, 0)} />
                        <Kpi label="Выручка, ₽" value={formatNumber(totals.sum, 0)} accent />
                        <Kpi label="Средний чек, ₽" value={formatNumber(totals.avg, 0)} />
                        {/* Отмены показываем, только когда они есть: при нуле карточка
                            «Живых» дословно повторяет «Выручку» и читается как сбой. */}
                        {totals.cancelledCount > 0 && (
                            <>
                                <Kpi
                                    label="Отменено, шт"
                                    value={formatNumber(totals.cancelledCount, 0)}
                                    hint={`на ${formatNumber(totals.cancelledSum, 0)} ₽`}
                                    danger
                                />
                                <Kpi
                                    label="Живых, ₽"
                                    value={formatNumber(totals.liveSum, 0)}
                                    hint={`${formatNumber(totals.liveCount, 0)} шт без отменённых`}
                                />
                            </>
                        )}
                        <Kpi
                            label="Доля FBS, %"
                            value={funnel ? formatNumber(funnel.shareSum, 1) : '—'}
                            hint={funnel
                                ? `${formatNumber(funnel.shareCount, 1)} % по штукам`
                                : 'нет данных воронки'}
                            accent={!!funnel}
                        />
                    </div>

                    {/* Сопоставление с воронкой */}
                    <div className="glass-card" style={{
                        padding: 12, marginBottom: 16, fontSize: 13,
                        borderLeft: '4px solid var(--color-accent)',
                    }}>
                        {funnel ? (
                            <>
                                <div style={{ marginBottom: 4 }}>
                                    Доля FBS в заказах проекта:{' '}
                                    <strong style={{ fontSize: 15 }}>
                                        {formatNumber(funnel.shareCount, 1)} %
                                    </strong>{' '}
                                    по штукам ·{' '}
                                    <strong style={{ fontSize: 15 }}>
                                        {formatNumber(funnel.shareSum, 1)} %
                                    </strong>{' '}
                                    по деньгам
                                </div>
                                <div style={{ color: 'var(--color-text-muted)' }}>
                                    {formatNumber(funnel.fbsCount, 0)} шт FBS
                                    из {formatNumber(funnel.count, 0)} шт по воронке
                                    · {formatNumber(funnel.fbsSum, 0)} ₽
                                    из {formatNumber(funnel.sum, 0)} ₽.
                                    {' '}Знаменатель — весь проект (FBO + FBS): воронка WB не делит
                                    продажи по схеме
                                    {wbWarehouseId !== '' ? ', а склад продавца в ней не выделить' : ''}.
                                </div>
                                {!funnel.fullPeriod && funnel.from && funnel.to && (
                                    // Воронку наполняет свой синк, и хвост периода в ней обычно
                                    // пустой. Считать полный период FBS от неполной воронки —
                                    // это кратно завышенная доля, поэтому обе стороны берутся за
                                    // один отрезок, и он обязан быть назван.
                                    <div style={{ color: 'var(--color-warning)', marginTop: 4 }}>
                                        ⚠️ Воронка заполнена только по {formatDate(funnel.to)} — доля
                                        посчитана за {formatDate(funnel.from)} — {formatDate(funnel.to)},
                                        а не за весь выбранный период. KPI выше — за весь период.
                                    </div>
                                )}
                            </>
                        ) : (
                            <span style={{ color: 'var(--color-text-muted)' }}>
                                ⚠️ Данных воронки продаж за этот период нет — долю FBS в общем объёме
                                посчитать не от чего. Дождитесь синка воронки.
                            </span>
                        )}
                    </div>

                    {/* График по дням: деньги столбиками, штуки линией на своей оси —
                        у них разный порядок величин, одна ось расплющила бы штуки в ноль. */}
                    <div style={{ marginBottom: 16 }}>
                        <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 8px' }}>Динамика по дням</h3>
                        <div className="glass-card" style={{ padding: 12 }}>
                            <ResponsiveContainer width="100%" height={260}>
                                <ComposedChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                                    <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                                    <XAxis
                                        dataKey="ts"
                                        type="number"
                                        scale="time"
                                        domain={['dataMin', 'dataMax']}
                                        tickFormatter={(ts: number) => dayTickFmt.format(new Date(ts))}
                                        tick={{ fontSize: 11 }}
                                        minTickGap={30}
                                    />
                                    <YAxis
                                        yAxisId="money"
                                        tick={{ fontSize: 11 }}
                                        tickFormatter={(v: number) => formatNumber(v, 0)}
                                    />
                                    <YAxis
                                        yAxisId="count"
                                        orientation="right"
                                        tick={{ fontSize: 11 }}
                                        tickFormatter={(v: number) => formatNumber(v, 0)}
                                    />
                                    <RechartsTooltip
                                        labelFormatter={(ts) => dayFullFmt.format(new Date(Number(ts)))}
                                        formatter={(value: number | string, name: string) => [
                                            formatNumber(Number(value), 0), name,
                                        ]}
                                    />
                                    <RechartsLegend wrapperStyle={{ fontSize: 12 }} />
                                    <Bar
                                        yAxisId="money"
                                        dataKey="sum"
                                        name="Выручка, ₽"
                                        fill="var(--color-accent)"
                                        radius={[4, 4, 0, 0]}
                                        // Без потолка на коротком периоде столбец растягивается
                                        // на треть графика и читается как заливка, а не как бар.
                                        maxBarSize={48}
                                    />
                                    <Line
                                        yAxisId="count"
                                        type="monotone"
                                        dataKey="count"
                                        name="Заказов, шт"
                                        stroke="var(--color-warning)"
                                        strokeWidth={2}
                                        dot={{ r: 3 }}
                                    />
                                </ComposedChart>
                            </ResponsiveContainer>
                        </div>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '0 0 8px' }}>
                        <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Разрезы</h3>
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                            доля — от выручки периода
                        </span>
                        <div style={{ flex: 1 }} />
                        <button
                            className="btn btn-sm"
                            onClick={() => exportCuts(data)}
                            title="Выгрузить все разрезы в один файл — по листу на разрез"
                        >
                            📊 Excel
                        </button>
                    </div>
                    <div style={{
                        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
                        gap: 12, alignItems: 'start',
                    }}>
                        {CUTS.map(cut => (
                            <CutCard
                                key={cut.key}
                                title={cut.title}
                                rows={cut.rows(data)}
                                total={totals.sum}
                            />
                        ))}
                    </div>

                    <div style={{ marginTop: 16 }}>
                        <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 8px' }}>
                            По дням — цифрами
                        </h3>
                        <TanStackDataTable
                            columns={DAY_COLUMNS}
                            data={data.by_day}
                            exportName="FBS_заказы_по_дням"
                            enableSorting
                            enablePagination={data.by_day.length > 60}
                            pageSize={60}
                            emptyText="Нет данных"
                        />
                    </div>
                </>
            )}
        </div>
    );
}

function Kpi(
    { label, value, hint, accent, danger }:
    { label: string; value: string; hint?: string; accent?: boolean; danger?: boolean },
) {
    return (
        <div className="glass-card" style={{ padding: 12 }}>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{label}</div>
            <div style={{
                fontSize: 22, fontWeight: 700,
                color: danger ? 'var(--color-danger)' : accent ? 'var(--color-accent)' : undefined,
            }}>
                {value}
            </div>
            {hint && <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{hint}</div>}
        </div>
    );
}

/**
 * Один разрез — компактный список с полосой доли вместо таблицы.
 *
 * Табличный компонент здесь был неуместен: ради трёх строк он приносил свою
 * шапку с сортировками, кнопку экспорта и горизонтальный скролл, из-за которого
 * колонка «Доля» уезжала за край карточки. Полоса под строкой сообщает
 * соотношение мгновенно — то, ради чего разрез и смотрят.
 */
function CutCard(
    { title, rows, total }: { title: string; rows: FbsOrderStatRow[]; total: number },
) {
    const [expanded, setExpanded] = useState(false);
    const shown = expanded ? rows.slice(0, TOP_ROWS) : rows.slice(0, COLLAPSED_ROWS);
    // Полоса меряется относительно ЛИДЕРА разреза, а не от всей выручки: иначе
    // в разрезе с одним доминирующим значением все остальные схлопываются в
    // невидимую нить и сравнивать их между собой нечем.
    const max = rows.reduce((acc, r) => Math.max(acc, num(r.orders_sum)), 0);

    return (
        <div className="glass-card" style={{ padding: 16 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 12 }}>
                <h4 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>{title}</h4>
                <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                    {formatNumber(rows.length, 0)}
                </span>
            </div>

            {rows.length === 0 ? (
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Нет данных</div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {shown.map(row => {
                        const sum = num(row.orders_sum);
                        const share = total > 0 ? (sum / total) * 100 : 0;
                        const width = max > 0 ? Math.max(2, (sum / max) * 100) : 0;
                        return (
                            <div key={row.label}>
                                <div style={{
                                    display: 'flex', alignItems: 'baseline', gap: 8,
                                    fontSize: 13, marginBottom: 4,
                                }}>
                                    <span style={{
                                        flex: 1, overflow: 'hidden', textOverflow: 'ellipsis',
                                        whiteSpace: 'nowrap',
                                    }} title={row.label}>
                                        {row.label}
                                    </span>
                                    <span style={{ color: 'var(--color-text-muted)', fontSize: 12 }}>
                                        {formatNumber(row.orders_count, 0)} шт
                                    </span>
                                    <span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
                                        {formatNumber(sum, 0)} ₽
                                    </span>
                                    <span style={{
                                        color: 'var(--color-text-dim)', fontSize: 12, minWidth: 44,
                                        textAlign: 'right', fontVariantNumeric: 'tabular-nums',
                                    }}>
                                        {formatNumber(share, 1)} %
                                    </span>
                                </div>
                                <div style={{
                                    height: 6, borderRadius: 8, background: 'var(--color-border)',
                                    overflow: 'hidden',
                                }}>
                                    <div style={{
                                        width: `${width}%`, height: '100%', borderRadius: 8,
                                        background: 'var(--color-accent)',
                                        transition: 'width 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
                                    }} />
                                </div>
                            </div>
                        );
                    })}
                    {rows.length > COLLAPSED_ROWS && (
                        <button
                            className="btn btn-sm"
                            style={{ alignSelf: 'flex-start' }}
                            onClick={() => setExpanded(v => !v)}
                        >
                            {expanded
                                ? 'Свернуть'
                                : `Ещё ${formatNumber(Math.min(rows.length, TOP_ROWS) - COLLAPSED_ROWS, 0)}`}
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

const DAY_COLUMNS: Column[] = [
    // Сортировка по сырой ISO-строке (она лексикографически совпадает с
    // хронологией), показ — человеческим форматом.
    { key: 'day', label: 'День', width: '40%', render: (v: string) => formatDate(v) },
    { key: 'orders_count', label: 'Заказов, шт', align: 'right', render: (v: number) => formatNumber(v, 0) },
    {
        key: 'orders_sum', label: 'Выручка, ₽', align: 'right',
        // Numeric приезжает СТРОКОЙ: без getValue сортировка сравнивает её
        // лексикографически, и «9 000» оказывается больше «10 000».
        getValue: (row: { orders_sum: number }) => num(row.orders_sum),
        sortingFn: 'basic',
        render: (v: number) => formatNumber(num(v), 0),
        exportValue: (row: { orders_sum: number }) => num(row.orders_sum),
    },
];
