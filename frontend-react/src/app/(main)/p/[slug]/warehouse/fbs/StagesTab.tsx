'use client';
/**
 * Вкладка «Этапы» — сколько времени задание FBS проводит на каждом шаге пути
 * и у какого склада шаг длиннее.
 *
 * Экран построен вокруг двух разных вопросов, и смешивать их нельзя:
 *
 *   • **Очередь (сверху)** — что висит ПРЯМО СЕЙЧАС и как давно. Она про
 *     управляемое: пока задание в «Собрано — ждёт отгрузки», это наша зона.
 *     Период на неё намеренно не влияет (так же, как «зависло в пути» на
 *     вкладке «Заказы»), поэтому фильтр периода стоит НИЖЕ, у исторических
 *     блоков — иначе он читался бы как фильтр очереди.
 *
 *   • **Этапы и динамика (ниже)** — сколько ПРОШЛЫЕ задания провели на шаге.
 *     Этап засчитывается в сутки, когда он ЗАВЕРШИЛСЯ (так подписано на
 *     графике): группируй мы по дате заказа, свежие сутки состояли бы из
 *     недоехавших заданий и «Ожидание на ПВЗ» за вчера выглядело бы
 *     неправдоподобно быстрым.
 *
 * 🔴 Точность вех разная, и экран обязан её показывать. `exact` — реальные даты
 * WB и наших операций (считаются по всей истории), `approx` — журнал переходов,
 * который начинается с момента наполнения и прошлое НЕ восстанавливает. Поэтому
 * этап с `count = 0` подписывается «истории пока нет», а не показывает прочерк:
 * пустая ячейка читалась бы как «проходит мгновенно».
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
import type {
    FbsStageAnalytics,
    FbsStageBucket,
    FbsStageRow,
    FbsWarehouse,
} from '@/types/api';
import { parseUtcMs } from './fbsShared';
import { isoDaysAgo, todayIso } from './OrdersStats';

const MSK_TZ = 'Europe/Moscow';
const bucketTickFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: MSK_TZ, day: '2-digit', month: '2-digit' });
const bucketFullFmt = new Intl.DateTimeFormat('ru-RU', {
    timeZone: MSK_TZ, day: 'numeric', month: 'long', year: 'numeric',
});
const sinceFmt = new Intl.DateTimeFormat('ru-RU', {
    timeZone: MSK_TZ, day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit',
});

/** Бэкенд отдаёт дату без времени — фиксируем полночь UTC перед `Date`. */
function toTs(day: string): number {
    return new Date(/[Z+]|T/.test(day) ? day : `${day}T00:00:00Z`).getTime();
}

const PRESETS: { key: string; label: string; days: number }[] = [
    { key: '7', label: '7 дней', days: 7 },
    { key: '30', label: '30 дней', days: 30 },
    { key: '90', label: '90 дней', days: 90 },
];

const BUCKETS: { key: FbsStageBucket | ''; label: string }[] = [
    { key: '', label: 'Авто' },
    { key: 'day', label: 'По дням' },
    { key: 'week', label: 'По неделям' },
    { key: 'month', label: 'По месяцам' },
];

/**
 * Длительность словами. Часы до двух суток и дни дальше: «38 ч» человек
 * соотносит с рабочим днём, а «5.2 дн» — с неделей. Голые часы на обоих концах
 * шкалы одинаково нечитаемы («127 ч» требует деления в уме).
 */
function fmtDuration(hours: number | null | undefined): string {
    if (hours === null || hours === undefined) return '—';
    if (hours < 1) return `${formatNumber(Math.round(hours * 60), 0)} мин`;
    if (hours < 48) return `${formatNumber(hours, 1)} ч`;
    return `${formatNumber(hours / 24, 1)} дн`;
}

/** Порог тревоги по возрасту очереди: сутки — тот же канон, что «зависло в пути». */
const QUEUE_ALERT_HOURS = 24;

/** Ярлыки зон — кто отвечает за длительность этапа. */
const ZONE_LABEL: Record<string, string> = {
    us: 'Наша зона',
    wb: 'Логистика WB',
    total: 'Весь путь',
};

/**
 * Цвет зоны — ОДИН источник для полос этапов, шапок таблицы и легенды.
 * Разделение ответственности — главное, что должен считывать экран: до скана QR
 * вопросы к нам и фулфилменту, после — к логистике WB. Разные цвета делают это
 * видимым без чтения подписей.
 */
const ZONE_COLOR: Record<string, string> = {
    us: 'var(--color-accent)',
    wb: 'var(--color-warning)',
    total: 'var(--color-text-dim)',
};

/** Полоска-маркер зоны в шапке колонки — тот же цвет, что у полосы этапа. */
function ZoneHeader({ title, zone, strong }: { title: string; zone: string; strong?: boolean }) {
    return (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
            <div style={{
                width: '100%', height: strong ? 4 : 3, borderRadius: 2,
                background: ZONE_COLOR[zone] ?? 'var(--color-border)',
            }} />
            <span style={strong ? { fontWeight: 700 } : undefined}>{title}</span>
        </div>
    );
}

/** Заголовок зоны с цветной меткой — одна и та же метка у полос и у таблиц. */
function ZoneTitle({ zone, title, note }: { zone: string; title: string; note: string }) {
    return (
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
            <span style={{
                width: 10, height: 10, borderRadius: 3, alignSelf: 'center',
                background: ZONE_COLOR[zone],
            }} />
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{title}</h3>
            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{note}</span>
        </div>
    );
}

interface Props {
    warehouses: FbsWarehouse[];
    refreshTick: number;
    /** Запись в WB отключена режимом контура — догон истории тоже гасим. */
    writeEnabled: boolean;
    writeHint: string;
    onToast: (message: string) => void;
}

export default function StagesTab({ warehouses, refreshTick, writeEnabled, writeHint, onToast }: Props) {
    const [syncing, setSyncing] = useState(false);
    const [data, setData] = useState<FbsStageAnalytics | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const [preset, setPreset] = useState('30');
    const [dateFrom, setDateFrom] = useState(() => isoDaysAgo(30));
    const [dateTo, setDateTo] = useState(() => todayIso());
    const [wbWarehouseId, setWbWarehouseId] = useState<number | ''>('');
    const [bucket, setBucket] = useState<FbsStageBucket | ''>('');
    const [chartStage, setChartStage] = useState('to_handover');

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
            const res = await api.getFbsStageAnalytics({
                dateFrom,
                dateTo,
                wbWarehouseId: wbWarehouseId === '' ? undefined : wbWarehouseId,
                bucket: bucket === '' ? undefined : bucket,
            });
            if (signal?.aborted) return;
            setData(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки аналитики этапов');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [dateFrom, dateTo, wbWarehouseId, bucket]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load, refreshTick]);

    /**
     * Ручной догон истории. 🔴 Потолок 300 — контракт ручки
     * (`POST /orders/history/sync`, `limit ≤ 300`): она исполняется в
     * API-контейнере рядом с uvicorn, и длинный прогон там выедает память.
     * Значение больше даёт 422 ещё до тела ручки.
     */
    const runHistorySync = useCallback(async () => {
        setSyncing(true);
        try {
            const res = await api.syncFbsOrderHistory(300);
            onToast(res.message || 'История догнана');
            // `load` В ЗАВИСИМОСТЯХ обязателен: без него колбэк навсегда держит
            // загрузчик первого рендера и после синка перечитывает данные за
            // СТАРЫЙ период — на экране фильтр 90 дней, цифры за 30.
            await load();
        } catch (e: unknown) {
            onToast(e instanceof Error ? e.message : 'Не удалось догнать историю');
        } finally {
            setSyncing(false);
        }
    }, [onToast, load]);


    const flow = useMemo(() => (data?.stages ?? []).filter(s => !s.summary), [data]);
    const summary = useMemo(() => (data?.stages ?? []).filter(s => s.summary), [data]);

    /** Этапы, у которых есть хоть один замер — только их осмысленно рисовать. */
    const measurable = useMemo(() => (data?.stages ?? []).filter(s => s.count > 0), [data]);

    // Выбранный этап мог остаться от прошлого периода и оказаться пустым —
    // тогда график молча показывал бы «нет данных» при живых соседях.
    useEffect(() => {
        if (!measurable.length) return;
        if (!measurable.some(s => s.key === chartStage)) setChartStage(measurable[0].key);
    }, [measurable, chartStage]);

    const chartData = useMemo(() => {
        if (!data) return [];
        return data.dynamics
            .filter(d => d.stage === chartStage)
            .map(d => ({
                ts: toTs(d.period_start),
                median: d.median_hours ?? 0,
                p90: d.p90_hours ?? 0,
                count: d.count,
            }));
    }, [data, chartStage]);

    /** Разрез по складам — «широкая» таблица: строка = склад, колонка = этап. */
    const warehouseRows = useMemo(() => {
        if (!data) return [];
        const byWarehouse = new Map<string, Record<string, unknown>>();
        for (const row of data.by_warehouse) {
            const id = String(row.wb_warehouse_id ?? 'none');
            let entry = byWarehouse.get(id);
            if (!entry) {
                entry = { warehouse_name: row.warehouse_name, wb_warehouse_id: row.wb_warehouse_id };
                byWarehouse.set(id, entry);
            }
            entry[row.stage] = row.median_hours;
            entry[`${row.stage}__count`] = row.count;
        }
        return Array.from(byWarehouse.values());
    }, [data]);

    /**
     * Колонки таблицы складов ОДНОЙ зоны: цепочка этапов + итог зоны.
     *
     * 🔴 Зоны разведены в две отдельные таблицы намеренно. Одной широкой
     * таблицей (девять столбцов, заголовки в три строки) невозможно ответить на
     * её же вопрос «кто медленный — мы или WB»: глаз не отделяет нашу
     * ответственность от чужой. Две таблицы отвечают на это самой вёрсткой.
     */
    const buildZoneColumns = useCallback((zone: 'us' | 'wb'): Column[] => {
        const all = measurable.length ? measurable : (data?.stages ?? []);
        // «Заказ → отметка ФФ о передаче» в таблицу не идёт: она меряет клик
        // фулфилмента, а не отгрузку, и рядом с итогом зоны читается как
        // противоречие. Её место — в полосах этапов с развёрнутой подписью.
        const rows = all.filter(s => s.zone === zone && s.key !== 'to_handover');
        // Сначала цепочка в хронологии, итог зоны — последним столбцом.
        const ordered = [...rows].sort((a, b) => Number(a.summary) - Number(b.summary));
        return [
            { key: 'warehouse_name', label: 'Склад продавца', align: 'left' },
            ...ordered.map<Column>(stage => ({
                key: stage.key,
                label: stage.short || stage.title,
                align: 'right',
                headerWrap: true,
                // Имя несёт обе границы («Уехал → принял СЦ»), поэтому колонке
                // нужна ширина: иначе стрелка переносится и этап читается как
                // два разных. Полное название и объяснение — в подсказке.
                width: '130px',
                headerTitle: `${stage.title}. ${stage.hint}`,
                sortingFn: 'basic',
                renderHeader: () => (
                    <ZoneHeader
                        title={stage.short || stage.title}
                        zone={stage.zone}
                        strong={stage.summary}
                    />
                ),
                render: (value: unknown, row: Record<string, unknown>) => {
                    const count = Number(row[`${stage.key}__count`] ?? 0);
                    if (!count) return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
                    return (
                        <span
                            title={`Замеров: ${formatNumber(count, 0)}`}
                            style={stage.summary ? { fontWeight: 600 } : undefined}
                        >
                            {fmtDuration(value as number | null)}
                        </span>
                    );
                },
                exportValue: (row: Record<string, unknown>) =>
                    row[stage.key] === null || row[stage.key] === undefined ? '' : Number(row[stage.key]),
            })),
        ];
    }, [measurable, data]);

    const usColumns = useMemo(() => buildZoneColumns('us'), [buildZoneColumns]);
    const wbColumns = useMemo(() => buildZoneColumns('wb'), [buildZoneColumns]);

    const journalSinceMs = parseUtcMs(data?.journal.since);
    const journalSince = journalSinceMs === null ? null : sinceFmt.format(new Date(journalSinceMs));

    /** Доля заданий с выгруженной историей кабинета — подпись «на чём посчитано». */
    /** Хоть один замер опёрся на журнал — тогда его происхождение стоит назвать. */
    const usedJournal = (data?.stages ?? []).some(s => s.approx_count > 0);

    const coveragePct = !data?.history.orders_total || !data.history.orders_covered
        ? null
        : Math.round((data.history.orders_covered / data.history.orders_total) * 100);

    if (loading && !data) {
        return <div className="glass-card" style={{ padding: 24 }}>Загрузка аналитики этапов…</div>;
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


    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {error && (
                <div className="glass-card" style={{ padding: 12, color: 'var(--color-warning)' }}>
                    {error} — показаны данные прошлой загрузки.
                </div>
            )}

            {/* ── Очередь: что висит прямо сейчас. Период сюда не применяется ── */}
            <div className="glass-card">
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 }}>
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Где задания сейчас</h3>
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                        текущая очередь целиком, период не применяется
                    </span>
                </div>
                {data.queue.length === 0 && (
                    <div style={{ padding: 16, color: 'var(--color-text-muted)', fontSize: 13 }}>
                        Очередь пуста — незавершённых заданий нет.
                    </div>
                )}
                <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                    gap: 12,
                    marginTop: 16,
                }}>
                    {data.queue.map(phase => {
                        const hot = (phase.max_age_hours ?? 0) >= QUEUE_ALERT_HOURS && phase.count > 0;
                        return (
                            <div
                                key={phase.phase}
                                style={{
                                    padding: 14,
                                    borderRadius: 12,
                                    border: '1px solid var(--color-border)',
                                    background: 'var(--color-bg-card)',
                                }}
                            >
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', minHeight: 32 }}>
                                    {phase.title}
                                </div>
                                <div style={{ fontSize: 26, fontWeight: 700, marginTop: 4 }}>
                                    {formatNumber(phase.count, 0)}
                                </div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 6 }}>
                                    медиана {fmtDuration(phase.median_age_hours)}
                                </div>
                                <div style={{
                                    fontSize: 12,
                                    marginTop: 2,
                                    color: hot ? 'var(--color-warning)' : 'var(--color-text-dim)',
                                }}>
                                    дольше всех {fmtDuration(phase.max_age_hours)}
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>

            {/* ── Фильтры исторических блоков ── */}
            <div className="glass-card" style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'flex-end' }}>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Период завершения этапа</label>
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
                    <input
                        className="form-input"
                        type="date"
                        style={{ width: 150 }}
                        value={dateFrom}
                        onChange={e => { setDateFrom(e.target.value); setPreset(''); }}
                    />
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>По</label>
                    <input
                        className="form-input"
                        type="date"
                        style={{ width: 150 }}
                        value={dateTo}
                        onChange={e => { setDateTo(e.target.value); setPreset(''); }}
                    />
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Склад WB</label>
                    <select
                        className="form-input"
                        style={{ width: 200 }}
                        value={wbWarehouseId}
                        onChange={e => setWbWarehouseId(e.target.value === '' ? '' : Number(e.target.value))}
                    >
                        <option value="">Все склады</option>
                        {warehouses.map(w => (
                            <option key={w.id} value={w.wb_warehouse_id}>
                                {w.name || `#${w.wb_warehouse_id}`}
                            </option>
                        ))}
                    </select>
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Шаг динамики</label>
                    <select
                        className="form-input"
                        style={{ width: 150 }}
                        value={bucket}
                        onChange={e => setBucket(e.target.value as FbsStageBucket | '')}
                    >
                        {BUCKETS.map(b => <option key={b.key} value={b.key}>{b.label}</option>)}
                    </select>
                </div>
                {loading && (
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)', paddingBottom: 12 }}>
                        обновляем…
                    </span>
                )}
            </div>

            {/* ── Этапы пути ── */}
            <div className="glass-card">
                <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Длительность этапов</h3>
                <p style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: '6px 0 0' }}>
                    Этап засчитан в тот день, когда он завершился. Моменты берутся из истории
                    кабинета WB — точное время каждого плеча;{' '}
                    {coveragePct === null
                        ? 'история ещё не выгружена, пока считаем по журналу переходов (± прогон синка).'
                        : `история выгружена для ${formatNumber(data.history.orders_covered, 0)} из ${formatNumber(data.history.orders_total, 0)} заданий (${coveragePct}%), строк ${formatNumber(data.history.rows, 0)}.`}
                    {' '}«≈ N» у этапа — сколько замеров опёрлись на журнал вместо истории.
                    {usedJournal && journalSince && ` Журнал переходов ведётся с ${journalSince}.`}
                </p>
                {data.history.orders_covered < data.history.orders_total && (
                    <button
                        className="btn btn-secondary btn-sm"
                        style={{ marginTop: 10 }}
                        onClick={runHistorySync}
                        disabled={syncing || !writeEnabled}
                        title={writeEnabled ? 'Забрать историю пачкой из кабинета WB' : writeHint}
                    >
                        {syncing ? 'Догоняем историю…' : 'Догнать историю из кабинета'}
                    </button>
                )}

                {(['us', 'wb'] as const).map(zone => {
                    const rows = flow.filter(s => s.zone === zone);
                    if (!rows.length) return null;
                    return (
                        <div key={zone} style={{ marginTop: 18 }}>
                            <div style={{
                                display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8,
                            }}>
                                <span style={{
                                    width: 10, height: 10, borderRadius: 3,
                                    background: ZONE_COLOR[zone],
                                }} />
                                <span style={{ fontSize: 13, fontWeight: 600 }}>
                                    {zone === 'us' ? 'Наша зона и фулфилмент' : 'Логистика Wildberries'}
                                </span>
                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    {zone === 'us'
                                        ? 'до скана QR — управляем мы'
                                        : 'после скана QR — влияем только выбором склада отгрузки'}
                                </span>
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                                {rows.map(stage => <StageBar key={stage.key} stage={stage} peers={flow} />)}
                            </div>
                        </div>
                    );
                })}

                <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--color-border)' }}>
                    <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
                        Сводно
                        <span style={{ fontWeight: 400, color: 'var(--color-text-muted)', marginLeft: 8 }}>
                            перекрывают этапы выше — складывать с ними нельзя. Наша скорость — это
                            «Заказ → груз уехал»: он закрывается сканом QR, то есть фактом отгрузки,
                            а не отметкой фулфилмента.
                        </span>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {summary.map(stage => <StageBar key={stage.key} stage={stage} peers={summary} />)}
                    </div>
                </div>
            </div>

            {/* ── Динамика ── */}
            <div className="glass-card">
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginBottom: 12 }}>
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Динамика</h3>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginLeft: 'auto' }}>
                        {(measurable.length ? measurable : data.stages).map(stage => (
                            <button
                                key={stage.key}
                                className={`btn btn-sm ${chartStage === stage.key ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setChartStage(stage.key)}
                                title={stage.hint}
                            >
                                {stage.title}
                            </button>
                        ))}
                    </div>
                </div>

                {chartData.length === 0 ? (
                    <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                        {coveragePct === null
                            ? 'История кабинета ещё не выгружена — по этому этапу считать пока нечего.'
                            : 'За выбранный период этап не завершался ни у одного задания.'}
                    </div>
                ) : (
                    <ResponsiveContainer width="100%" height={280}>
                        <ComposedChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                            <XAxis
                                dataKey="ts"
                                type="number"
                                scale="time"
                                domain={['dataMin', 'dataMax']}
                                tickFormatter={ts => bucketTickFmt.format(new Date(ts))}
                                tick={{ fontSize: 12 }}
                            />
                            <YAxis
                                yAxisId="hours"
                                tick={{ fontSize: 12 }}
                                tickFormatter={v => formatNumber(v, 0)}
                            />
                            <YAxis
                                yAxisId="count"
                                orientation="right"
                                tick={{ fontSize: 12 }}
                                tickFormatter={v => formatNumber(v, 0)}
                            />
                            <RechartsTooltip
                                labelFormatter={ts => bucketFullFmt.format(new Date(Number(ts)))}
                                formatter={(value: number, name: string) =>
                                    name === 'Заданий'
                                        ? [formatNumber(value, 0), name]
                                        : [fmtDuration(value), name]
                                }
                            />
                            <RechartsLegend wrapperStyle={{ fontSize: 12 }} />
                            <Bar
                                yAxisId="count"
                                dataKey="count"
                                name="Заданий"
                                fill="var(--color-border)"
                                radius={[4, 4, 0, 0]}
                                maxBarSize={48}
                            />
                            <Line
                                yAxisId="hours"
                                type="monotone"
                                dataKey="median"
                                name="Медиана"
                                stroke="var(--color-accent)"
                                strokeWidth={2}
                                // Один бакет линией не рисуется — показываем точкой.
                                dot={chartData.length === 1}
                            />
                            <Line
                                yAxisId="hours"
                                type="monotone"
                                dataKey="p90"
                                name="Хвост p90"
                                stroke="var(--color-warning)"
                                strokeWidth={2}
                                strokeDasharray="4 3"
                                dot={chartData.length === 1}
                            />
                        </ComposedChart>
                    </ResponsiveContainer>
                )}
            </div>

            {/* ── Разрез по складам: ДВЕ таблицы, по одной на зону ответственности ── */}
            <div className="glass-card">
                <ZoneTitle
                    zone="us"
                    title="Наша зона и фулфилмент — по складам"
                    note="от заказа до скана QR: всё, чем управляем мы"
                />
                <p style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: '6px 0 12px' }}>
                    Медиана длительности этапа. Последний столбец — итог зоны, он перекрывает
                    предыдущие, складывать в строке нельзя. Прочерк — за период этап не завершался;
                    наведите на цифру, чтобы увидеть число замеров. В Excel — часы.
                </p>
                <TanStackDataTable
                    columns={usColumns}
                    data={warehouseRows}
                    exportName={`FBS_наша_зона_по_складам_${data.date_from}_${data.date_to}`}
                    enableSorting
                    emptyText="За период нет ни одного завершённого этапа"
                />
            </div>

            <div className="glass-card">
                <ZoneTitle
                    zone="wb"
                    title="Логистика Wildberries — по складам"
                    note="от скана QR до готовности к вручению: влияем только выбором склада отгрузки"
                />
                <p style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: '6px 0 12px' }}>
                    🔴 Сравнивать склады здесь напрямую нельзя: у них разная география доставки,
                    и разница вдвое чаще означает «возит дальше», а не «работает хуже» — разрез
                    по направлениям на вкладке «География». Выборка меньше, чем у нашей зоны:
                    до скана доезжают почти все задания, до ПВЗ пока единицы.
                </p>
                <TanStackDataTable
                    columns={wbColumns}
                    data={warehouseRows}
                    exportName={`FBS_зона_WB_по_складам_${data.date_from}_${data.date_to}`}
                    enableSorting
                    emptyText="За период ни один заказ не дошёл до готовности"
                />
            </div>
        </div>
    );
}

/**
 * Строка этапа: длительность, полоса относительно самого долгого соседа и
 * честная подпись о происхождении данных.
 *
 * Полоса нормируется по МЕДИАНЕ самого долгого этапа группы, а не по p90:
 * единственный застрявший заказ иначе сжимал бы все прочие полосы в ноль.
 */
function StageBar({ stage, peers }: { stage: FbsStageRow; peers: FbsStageRow[] }) {
    const scale = Math.max(...peers.map(s => s.median_hours ?? 0), 1);
    const width = stage.median_hours ? Math.max(2, (stage.median_hours / scale) * 100) : 0;
    const empty = stage.count === 0;

    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ width: 210, flexShrink: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 500 }} title={stage.hint}>{stage.title}</div>
                <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                    {ZONE_LABEL[stage.zone] ?? stage.zone}
                    {/* Показываем ФАКТ, а не объявленную точность: этап питается
                        историей кабинета, и «≈» уместно ровно настолько,
                        насколько замеры опёрлись на журнал. */}
                    {stage.approx_count > 0 && ` · ≈ ${formatNumber(stage.approx_count, 0)}`}
                </div>
            </div>

            <div style={{ flex: 1, minWidth: 80 }}>
                <div style={{
                    height: 10,
                    borderRadius: 8,
                    background: 'var(--color-border)',
                    overflow: 'hidden',
                }}>
                    <div style={{
                        width: `${width}%`,
                        height: '100%',
                        borderRadius: 8,
                        background: ZONE_COLOR[stage.zone] ?? 'var(--color-accent)',
                        transition: 'width 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
                    }} />
                </div>
            </div>

            <div style={{ width: 96, textAlign: 'right', fontSize: 14, fontWeight: 600 }}>
                {empty ? <span style={{ color: 'var(--color-text-dim)' }}>—</span> : fmtDuration(stage.median_hours)}
            </div>

            <div style={{ width: 190, fontSize: 12, color: 'var(--color-text-muted)' }}>
                {empty ? (
                    'нет завершённых'
                ) : (
                    <>p90 {fmtDuration(stage.p90_hours)} · {formatNumber(stage.count, 0)} шт</>
                )}
                {/* Выброшенные замеры показываем и при ПУСТОЙ выборке: иначе этап,
                    у которого отбраковано всё, выглядел бы как «данных не было»
                    вместо «данные были, но недостоверны» — ровно то молчание,
                    против которого заведено поле `dropped`. */}
                {stage.dropped > 0 && (
                    <span
                        style={{ color: 'var(--color-warning)' }}
                        title="Замеры, выброшенные как недостоверные: обратный порядок вех или застывший статус WB"
                    >
                        {empty ? '' : ' ·'} отброшено {formatNumber(stage.dropped, 0)}
                    </span>
                )}
            </div>
        </div>
    );
}
