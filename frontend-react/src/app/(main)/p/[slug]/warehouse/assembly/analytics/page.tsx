'use client';

/**
 * Страница «Анализ сборки» — поток заявок на сборку по этапам.
 *
 * Структура:
 *  1. KPI: средний цикл, средняя сборка, в работе, аномалии.
 *  2. Пайплайн этапов: Сборка → Готов → Машина → Отгружено → Принято ВБ
 *     (средняя/медианная длительность каждого этапа, подсветка превышений порога).
 *  3. Нестандартные переходы (отмены и «откаты назад») — свёрнутый блок.
 *  4. Аномалии — зависшие заявки, сгруппированные по типу проблемы.
 *  5. Разрез по складам.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { exportToExcel, formatDate, formatNumber, pluralRu } from '@/lib/utils';
import type {
    AssemblyAnomalyKind,
    AssemblyAnomalyRow,
    AssemblyFlowAnalyticsResponse,
    AssemblyFlowThresholds,
    AssemblyStatus,
    AssemblyTransitionStat,
    Warehouse,
} from '@/types/api';

// ─── Config ─────────────────────────────────────────────────────────────────

const DEFAULT_THRESHOLDS: AssemblyFlowThresholds = {
    assembly_days: 3,
    ship_days: 2,
    delivery_days: 7,
};

type PeriodKey = '30' | '90' | 'all';

const PERIODS: { key: PeriodKey; label: string }[] = [
    { key: '30', label: '30 дн' },
    { key: '90', label: '90 дн' },
    { key: 'all', label: 'Всё время' },
];

const STATUS_BADGE: Record<string, { label: string; cls: string }> = {
    PENDING:          { label: 'В сборке',         cls: 'badge-info' },
    IN_PROGRESS:      { label: 'В сборке',         cls: 'badge-info' },
    READY:            { label: 'Готово',           cls: 'badge-success' },
    VEHICLE_ASSIGNED: { label: 'Машина назначена', cls: 'badge-info' },
    SHIPPED:          { label: 'Отгружена',        cls: 'badge-success' },
    DELIVERED:        { label: 'Принята WB',       cls: 'badge-success' },
    CLOSED:           { label: 'Закрыт',           cls: 'badge-warning' },
    CANCELLED:        { label: 'Отменена',         cls: 'badge-secondary' },
};

/** Лента этапов: какой статус измеряем и каким порогом подсвечиваем. */
const STAGE_FLOW: {
    stage: AssemblyStatus;
    title: string;
    sub: string;
    thresholdKey: keyof AssemblyFlowThresholds;
}[] = [
    { stage: 'IN_PROGRESS',      title: 'Сборка',           sub: 'до готовности',       thresholdKey: 'assembly_days' },
    { stage: 'READY',            title: 'Готов',            sub: 'ожидание машины',     thresholdKey: 'ship_days' },
    { stage: 'VEHICLE_ASSIGNED', title: 'Машина назначена', sub: 'до отгрузки',         thresholdKey: 'ship_days' },
    { stage: 'SHIPPED',          title: 'Отгружено',        sub: 'в пути до приёмки ВБ', thresholdKey: 'delivery_days' },
];

/** Переходы «назад» — сигналы проблем в процессе. */
const BACKWARD_TRANSITIONS: [string, string][] = [
    ['READY', 'IN_PROGRESS'],
    ['VEHICLE_ASSIGNED', 'READY'],
    ['SHIPPED', 'READY'],
];

const ANOMALY_GROUPS: {
    kind: AssemblyAnomalyKind;
    icon: string;
    title: string;
    desc: (t: AssemblyFlowThresholds) => string;
    color: string;
    thresholdKey: keyof AssemblyFlowThresholds | null;
}[] = [
    {
        kind: 'wb_accepted_not_shipped',
        icon: '🔴',
        title: 'Забыли отгрузить — ВБ уже принял поставку',
        desc: () => 'Поставка принята на стороне WB, а заявка в системе не отгружена. Остатки не списаны — проверьте и отгрузите вручную.',
        color: 'var(--color-danger)',
        thresholdKey: null,
    },
    {
        kind: 'stuck_shipment',
        icon: '🟠',
        title: 'Готово, но не отгружается',
        desc: (t) => `Заявка готова дольше ${formatNumber(t.ship_days, 0)} дн — машина не назначена или не отгружена.`,
        color: 'var(--color-warning)',
        thresholdKey: 'ship_days',
    },
    {
        kind: 'stuck_assembly',
        icon: '🟡',
        title: 'Долго в сборке',
        desc: (t) => `В сборке дольше ${formatNumber(t.assembly_days, 0)} дн.`,
        color: 'var(--color-warning)',
        thresholdKey: 'assembly_days',
    },
    {
        kind: 'shipped_not_accepted',
        icon: '🔵',
        title: 'Отгружено, ВБ не принимает',
        desc: (t) => `Отгружена дольше ${formatNumber(t.delivery_days, 0)} дн назад, приёмка WB не подтверждена.`,
        color: 'var(--color-accent)',
        thresholdKey: 'delivery_days',
    },
];

// ─── Helpers ────────────────────────────────────────────────────────────────

function isoDaysAgo(days: number): string {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d.toISOString().slice(0, 10);
}

function fmtDays(v: number | null | undefined): string {
    if (v === null || v === undefined) return '—';
    return `${formatNumber(v, 1)} дн`;
}

function statusLabel(status: string | null): string {
    if (status === null) return 'Создание';
    return STATUS_BADGE[status]?.label ?? status;
}

function isNonStandardTransition(t: AssemblyTransitionStat): boolean {
    if (t.to_status === 'CANCELLED') return true;
    return BACKWARD_TRANSITIONS.some(([from, to]) => t.from_status === from && t.to_status === to);
}

// ─── UI atoms ───────────────────────────────────────────────────────────────

function KpiCardLite({
    label,
    value,
    sub,
    valueColor,
    onClick,
}: {
    label: string;
    value: string;
    sub?: string;
    valueColor?: string;
    onClick?: () => void;
}) {
    return (
        <div
            className="glass-card"
            style={{ padding: 20, cursor: onClick ? 'pointer' : undefined }}
            onClick={onClick}
            title={onClick ? 'Перейти к аномалиям' : undefined}
        >
            <div
                style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: 'var(--color-text-muted)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    marginBottom: 8,
                }}
            >
                {label}
            </div>
            <div
                style={{
                    fontSize: 32,
                    fontWeight: 700,
                    letterSpacing: '-0.03em',
                    color: valueColor || 'var(--color-text)',
                    lineHeight: 1.1,
                }}
            >
                {value}
            </div>
            {sub && (
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>
                    {sub}
                </div>
            )}
        </div>
    );
}

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

// ─── Page ───────────────────────────────────────────────────────────────────

export default function AssemblyFlowAnalyticsPage() {
    const params = useParams();
    const slug = params.slug as string;

    // Data
    const [data, setData] = useState<AssemblyFlowAnalyticsResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [reloadTick, setReloadTick] = useState(0);

    // Filters
    const [period, setPeriod] = useState<PeriodKey>('90');
    const [warehouseId, setWarehouseId] = useState<number | ''>('');
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);

    // Thresholds: applied (триггерит перезапрос) + draft (строки в инпутах)
    const [thresholds, setThresholds] = useState<AssemblyFlowThresholds>(DEFAULT_THRESHOLDS);
    const [draftThresholds, setDraftThresholds] = useState({
        assembly_days: String(DEFAULT_THRESHOLDS.assembly_days),
        ship_days: String(DEFAULT_THRESHOLDS.ship_days),
        delivery_days: String(DEFAULT_THRESHOLDS.delivery_days),
    });
    const [thresholdsOpen, setThresholdsOpen] = useState(false);
    const thresholdsRef = useRef<HTMLDivElement>(null);

    const anomaliesRef = useRef<HTMLDivElement>(null);

    // ─── Load warehouses (один раз) ───────────────────────────────────────
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

    // ─── Load analytics ───────────────────────────────────────────────────
    const load = useCallback(async (signal: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const resp = await api.getAssemblyFlowAnalytics({
                date_from: period === 'all' ? undefined : isoDaysAgo(period === '30' ? 30 : 90),
                warehouse_ids: warehouseId ? String(warehouseId) : undefined,
                assembly_threshold_days: thresholds.assembly_days,
                ship_threshold_days: thresholds.ship_days,
                delivery_threshold_days: thresholds.delivery_days,
            });
            if (signal.aborted) return;
            setData(resp);
        } catch (e: unknown) {
            if (signal.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (!signal.aborted) setLoading(false);
        }
    }, [period, warehouseId, thresholds]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load, reloadTick]);

    const commitThresholds = useCallback(() => {
        const parse = (s: string, fallback: number) => {
            const n = Number(s);
            return Number.isFinite(n) && n >= 1 ? Math.round(n) : fallback;
        };
        const next: AssemblyFlowThresholds = {
            assembly_days: parse(draftThresholds.assembly_days, DEFAULT_THRESHOLDS.assembly_days),
            ship_days: parse(draftThresholds.ship_days, DEFAULT_THRESHOLDS.ship_days),
            delivery_days: parse(draftThresholds.delivery_days, DEFAULT_THRESHOLDS.delivery_days),
        };
        setDraftThresholds({
            assembly_days: String(next.assembly_days),
            ship_days: String(next.ship_days),
            delivery_days: String(next.delivery_days),
        });
        setThresholds(prev =>
            prev.assembly_days === next.assembly_days
            && prev.ship_days === next.ship_days
            && prev.delivery_days === next.delivery_days
                ? prev
                : next,
        );
    }, [draftThresholds]);

    // ─── Thresholds popover: клик вне → применить введённое и закрыть ────
    // commitThresholds в deps: хендлер пересоздаётся на каждый ввод и всегда
    // видит свежие draft-значения — клик-вне применяет, а не теряет их.
    useEffect(() => {
        if (!thresholdsOpen) return;
        const handler = (e: MouseEvent) => {
            if (thresholdsRef.current && !thresholdsRef.current.contains(e.target as Node)) {
                commitThresholds();
                setThresholdsOpen(false);
            }
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [thresholdsOpen, commitThresholds]);

    // ─── Derived ──────────────────────────────────────────────────────────

    const stageByKey = useMemo(() => {
        const m = new Map<string, { avg_days: number | null; median_days: number | null; count: number }>();
        for (const s of data?.stages ?? []) m.set(s.stage, s);
        return m;
    }, [data]);

    const nonStandardTransitions = useMemo(
        () => (data?.transitions ?? []).filter(isNonStandardTransition).sort((a, b) => b.count - a.count),
        [data],
    );
    const [transitionsOpen, setTransitionsOpen] = useState(false);

    const anomaliesByKind = useMemo(() => {
        const m = new Map<AssemblyAnomalyKind, AssemblyAnomalyRow[]>();
        for (const row of data?.anomalies ?? []) {
            const list = m.get(row.kind) ?? [];
            list.push(row);
            m.set(row.kind, list);
        }
        for (const list of m.values()) list.sort((a, b) => b.days_stuck - a.days_stuck);
        return m;
    }, [data]);

    const sortedByWarehouse = useMemo(
        () => [...(data?.by_warehouse ?? [])].sort(
            (a, b) => b.anomaly_count - a.anomaly_count || b.active_count - a.active_count,
        ),
        [data],
    );

    const hasData = useMemo(() => {
        if (!data) return false;
        return data.summary.active_count > 0
            || data.summary.completed_in_period > 0
            || data.anomalies.length > 0
            || data.stages.some(s => s.count > 0)
            || data.by_warehouse.length > 0;
    }, [data]);

    const appliedThresholds = data?.thresholds ?? thresholds;
    const periodSub = period === 'all' ? 'за всё время' : `за ${period} дн`;

    const scrollToAnomalies = useCallback(() => {
        anomaliesRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, []);

    // Выгрузка ВСЕХ аномалий одним файлом (в порядке групп, внутри — по «висит дн»)
    const handleExportAnomalies = useCallback(() => {
        const titleByKind = new Map(ANOMALY_GROUPS.map(g => [g.kind, g.title]));
        const rows = ANOMALY_GROUPS.flatMap(group => anomaliesByKind.get(group.kind) ?? []);
        if (rows.length === 0) return;
        exportToExcel(
            rows.map(row => ({
                category: titleByKind.get(row.kind) ?? row.kind,
                number: row.number,
                status: STATUS_BADGE[row.status]?.label ?? row.status,
                warehouse: row.warehouse_name || '',
                wb_warehouse: row.wb_warehouse_name || '',
                qty: row.total_qty,
                days_stuck: row.days_stuck,
                since: row.since ? formatDate(row.since) : '',
                wb_fbo_status: row.wb_fbo_status || '',
            })),
            'assembly_flow_anomalies',
            [
                { key: 'category', label: 'Категория' },
                { key: 'number', label: '№' },
                { key: 'status', label: 'Статус' },
                { key: 'warehouse', label: 'Склад' },
                { key: 'wb_warehouse', label: 'Целевой склад ВБ' },
                { key: 'qty', label: 'Шт' },
                { key: 'days_stuck', label: 'Висит дн' },
                { key: 'since', label: 'С какого числа' },
                { key: 'wb_fbo_status', label: 'Статус WB-поставки' },
            ],
        );
    }, [anomaliesByKind]);

    // ─── Render ───────────────────────────────────────────────────────────

    return (
        <div className="animate-in">
            {/* Header */}
            <div className="page-header" style={{ alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
                <div>
                    <h1 className="page-title">Анализ сборки</h1>
                    <p className="page-subtitle">
                        Скорость прохождения заявок по этапам и зависшие заявки
                    </p>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    {/* Период */}
                    <div style={{ display: 'flex', gap: 4 }}>
                        {PERIODS.map(p => (
                            <button
                                key={p.key}
                                type="button"
                                className={`btn btn-sm ${period === p.key ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setPeriod(p.key)}
                            >
                                {p.label}
                            </button>
                        ))}
                    </div>
                    {/* Склад */}
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
                    {/* Пороги */}
                    <div ref={thresholdsRef} style={{ position: 'relative' }}>
                        <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            onClick={() => setThresholdsOpen(o => !o)}
                            title="Пороги аномалий: сборка / отгрузка / приёмка, дней"
                        >
                            ⚙ Пороги · {thresholds.assembly_days}/{thresholds.ship_days}/{thresholds.delivery_days}
                        </button>
                        {thresholdsOpen && (
                            <div
                                className="glass-card"
                                style={{
                                    position: 'absolute',
                                    right: 0,
                                    top: 'calc(100% + 8px)',
                                    zIndex: 30,
                                    padding: 16,
                                    width: 240,
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: 10,
                                }}
                            >
                                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                                    Пороги аномалий, дней
                                </div>
                                {([
                                    ['assembly_days', 'Сборка'],
                                    ['ship_days', 'Отгрузка'],
                                    ['delivery_days', 'Приёмка ВБ'],
                                ] as const).map(([key, label]) => (
                                    <label
                                        key={key}
                                        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, fontSize: 13 }}
                                    >
                                        <span>{label}</span>
                                        <input
                                            type="number"
                                            min={1}
                                            className="form-input"
                                            style={{ width: 72, textAlign: 'right' }}
                                            value={draftThresholds[key]}
                                            onChange={e => setDraftThresholds(prev => ({ ...prev, [key]: e.target.value }))}
                                            onBlur={commitThresholds}
                                            onKeyDown={e => { if (e.key === 'Enter') commitThresholds(); }}
                                        />
                                    </label>
                                ))}
                                <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                                    Превышение порога помечает заявку как аномалию и подсвечивает этап.
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {/* Error */}
            {error && !loading && (
                <div className="glass-card" style={{ padding: 20, color: 'var(--color-danger)', marginBottom: 16 }}>
                    <div style={{ fontWeight: 600, marginBottom: 8 }}>Не удалось загрузить аналитику</div>
                    <div style={{ fontSize: 13, marginBottom: 12 }}>{error}</div>
                    <button type="button" className="btn btn-danger btn-sm" onClick={() => setReloadTick(t => t + 1)}>
                        Повторить
                    </button>
                </div>
            )}

            {/* Loading skeleton */}
            {loading && !error && (
                <>
                    <div className="stats-grid" style={{ marginBottom: 16 }}>
                        <SkeletonCard />
                        <SkeletonCard />
                        <SkeletonCard />
                        <SkeletonCard />
                    </div>
                    <SkeletonCard height={160} />
                    <div style={{ height: 16 }} />
                    <SkeletonCard height={240} />
                </>
            )}

            {/* Empty */}
            {!loading && !error && data && !hasData && (
                <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>📦</div>
                    <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Нет данных за выбранный период</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                        Попробуйте расширить период или убрать фильтр склада
                    </div>
                </div>
            )}

            {/* Data */}
            {!loading && !error && data && hasData && (
                <>
                    {/* KPI */}
                    <div className="stats-grid" style={{ marginBottom: 16 }}>
                        <KpiCardLite
                            label="Средний цикл"
                            value={fmtDays(data.summary.avg_cycle_days)}
                            sub={`создание → отгрузка · ${formatNumber(data.summary.completed_in_period, 0)} завершено ${periodSub}`}
                        />
                        <KpiCardLite
                            label="Средняя сборка"
                            value={fmtDays(data.summary.avg_assembly_days)}
                            sub="создание → готово"
                            valueColor={
                                data.summary.avg_assembly_days !== null
                                && data.summary.avg_assembly_days > appliedThresholds.assembly_days
                                    ? 'var(--color-warning)'
                                    : undefined
                            }
                        />
                        <KpiCardLite
                            label="В работе сейчас"
                            value={formatNumber(data.summary.active_count, 0)}
                            sub="заявок в потоке: сборка → отгрузка"
                        />
                        <KpiCardLite
                            label="Аномалий"
                            value={formatNumber(data.summary.anomaly_count, 0)}
                            sub={data.summary.anomaly_count > 0 ? 'требуют внимания — к списку ↓' : 'всё в порядке'}
                            valueColor={data.summary.anomaly_count > 0 ? 'var(--color-danger)' : 'var(--color-success)'}
                            onClick={data.summary.anomaly_count > 0 ? scrollToAnomalies : undefined}
                        />
                    </div>

                    {/* Пайплайн этапов */}
                    <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                        <SectionTitle>Этапы потока · средняя длительность {periodSub}</SectionTitle>
                        <div style={{ display: 'flex', alignItems: 'stretch', gap: 8, overflowX: 'auto', paddingBottom: 4 }}>
                            {STAGE_FLOW.map((cfg, idx) => {
                                const stat = stageByKey.get(cfg.stage);
                                const limit = appliedThresholds[cfg.thresholdKey];
                                const exceeded = stat != null && stat.avg_days != null && stat.avg_days > limit;
                                return (
                                    <div key={cfg.stage} style={{ display: 'flex', alignItems: 'stretch', gap: 8, flex: '1 0 170px', minWidth: 0 }}>
                                        {idx > 0 && (
                                            <div style={{ alignSelf: 'center', color: 'var(--color-text-dim)', fontSize: 18, flexShrink: 0 }}>
                                                →
                                            </div>
                                        )}
                                        <div
                                            style={{
                                                flex: 1,
                                                minWidth: 160,
                                                padding: '14px 16px',
                                                borderRadius: 12,
                                                border: `1px solid ${exceeded ? 'var(--color-warning)' : 'var(--color-border)'}`,
                                                background: exceeded
                                                    ? 'color-mix(in srgb, var(--color-warning) 7%, transparent)'
                                                    : 'transparent',
                                            }}
                                            title={exceeded ? `Средняя превышает порог ${formatNumber(limit, 0)} дн` : undefined}
                                        >
                                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2 }}>{cfg.title}</div>
                                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 8 }}>{cfg.sub}</div>
                                            <div
                                                style={{
                                                    fontSize: 24,
                                                    fontWeight: 700,
                                                    letterSpacing: '-0.02em',
                                                    lineHeight: 1.1,
                                                    color: exceeded ? 'var(--color-warning)' : 'var(--color-text)',
                                                }}
                                            >
                                                {fmtDays(stat?.avg_days)}
                                            </div>
                                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                                медиана {fmtDays(stat?.median_days)}
                                            </div>
                                            <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 2 }}>
                                                {stat && stat.count > 0
                                                    ? `измерено на ${formatNumber(stat.count, 0)} ${pluralRu(stat.count, ['заявке', 'заявках', 'заявках'])}`
                                                    : 'нет измерений'}
                                            </div>
                                        </div>
                                    </div>
                                );
                            })}
                            {/* Терминальный узел */}
                            <div style={{ display: 'flex', alignItems: 'stretch', gap: 8, flex: '0 0 auto' }}>
                                <div style={{ alignSelf: 'center', color: 'var(--color-text-dim)', fontSize: 18, flexShrink: 0 }}>→</div>
                                <div
                                    style={{
                                        minWidth: 130,
                                        padding: '14px 16px',
                                        borderRadius: 12,
                                        border: '1px solid color-mix(in srgb, var(--color-success) 40%, transparent)',
                                        background: 'color-mix(in srgb, var(--color-success) 7%, transparent)',
                                        display: 'flex',
                                        flexDirection: 'column',
                                        justifyContent: 'center',
                                    }}
                                >
                                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-success)', marginBottom: 4 }}>
                                        ✓ Принято ВБ
                                    </div>
                                    <div style={{ fontSize: 20, fontWeight: 700, lineHeight: 1.1 }}>
                                        {formatNumber(data.summary.completed_in_period, 0)}
                                    </div>
                                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 2 }}>{periodSub}</div>
                                </div>
                            </div>
                        </div>

                        {/* Нестандартные переходы */}
                        {nonStandardTransitions.length > 0 && (
                            <div style={{ marginTop: 14, borderTop: '1px solid var(--color-border)', paddingTop: 10 }}>
                                <button
                                    type="button"
                                    onClick={() => setTransitionsOpen(o => !o)}
                                    style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: 8,
                                        background: 'transparent',
                                        border: 'none',
                                        cursor: 'pointer',
                                        padding: 0,
                                        fontSize: 13,
                                        color: 'var(--color-text)',
                                    }}
                                >
                                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                        {transitionsOpen ? '▾' : '▸'}
                                    </span>
                                    <span style={{ fontWeight: 600 }}>Нестандартные переходы</span>
                                    <span className="badge badge-warning" style={{ fontSize: 11 }}>
                                        {formatNumber(nonStandardTransitions.reduce((s, t) => s + t.count, 0), 0)}
                                    </span>
                                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                        отмены и возвраты на предыдущий этап — сигналы проблем
                                    </span>
                                </button>
                                {transitionsOpen && (
                                    <div style={{ overflowX: 'auto', marginTop: 10 }}>
                                        <table className="data-table" style={{ fontSize: 13 }}>
                                            <thead>
                                                <tr>
                                                    <th>Переход</th>
                                                    <th style={{ textAlign: 'right' }}>Сколько раз</th>
                                                    <th style={{ textAlign: 'right' }}>Среднее время до перехода</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {nonStandardTransitions.map((t, idx) => (
                                                    <tr key={`${t.from_status ?? 'null'}-${t.to_status}-${idx}`}>
                                                        <td>
                                                            <span style={{ fontWeight: 500 }}>{statusLabel(t.from_status)}</span>
                                                            <span style={{ color: 'var(--color-text-dim)', margin: '0 6px' }}>→</span>
                                                            <span
                                                                style={{
                                                                    fontWeight: 500,
                                                                    color: t.to_status === 'CANCELLED' ? 'var(--color-danger)' : 'var(--color-warning)',
                                                                }}
                                                            >
                                                                {statusLabel(t.to_status)}
                                                            </span>
                                                        </td>
                                                        <td style={{ textAlign: 'right' }}>{formatNumber(t.count, 0)}</td>
                                                        <td style={{ textAlign: 'right' }}>{fmtDays(t.avg_days)}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Аномалии */}
                    <div ref={anomaliesRef} style={{ scrollMarginTop: 16, marginBottom: 16 }}>
                        {data.anomalies.length === 0 ? (
                            <div
                                className="glass-card"
                                style={{
                                    padding: 32,
                                    textAlign: 'center',
                                    border: '1px solid color-mix(in srgb, var(--color-success) 35%, transparent)',
                                }}
                            >
                                <div style={{ fontSize: 36, marginBottom: 8 }}>🎉</div>
                                <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-success)' }}>
                                    Все заявки идут по плану
                                </div>
                                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                    Ни одна заявка не превысила пороги {appliedThresholds.assembly_days}/{appliedThresholds.ship_days}/{appliedThresholds.delivery_days} дн
                                </div>
                            </div>
                        ) : (
                            <>
                                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 12 }}>
                                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                                        Аномалии
                                    </div>
                                    <button
                                        type="button"
                                        className="btn btn-secondary btn-sm"
                                        onClick={handleExportAnomalies}
                                        title="Выгрузить все аномалии одним Excel-файлом"
                                    >
                                        ⬇ Excel
                                    </button>
                                </div>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                                {ANOMALY_GROUPS.map(group => {
                                    const rows = anomaliesByKind.get(group.kind);
                                    if (!rows || rows.length === 0) return null;
                                    const limit = group.thresholdKey ? appliedThresholds[group.thresholdKey] : 0;
                                    return (
                                        <div
                                            key={group.kind}
                                            className="glass-card"
                                            style={{ padding: 0, overflow: 'hidden', borderLeft: `3px solid ${group.color}` }}
                                        >
                                            <div style={{ padding: '14px 20px 12px' }}>
                                                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                                    <span>{group.icon}</span>
                                                    <span style={{ fontSize: 14, fontWeight: 600 }}>{group.title}</span>
                                                    <span
                                                        className="badge"
                                                        style={{
                                                            background: `color-mix(in srgb, ${group.color} 14%, transparent)`,
                                                            color: group.color,
                                                            fontSize: 12,
                                                            fontWeight: 700,
                                                        }}
                                                    >
                                                        {formatNumber(rows.length, 0)}
                                                    </span>
                                                </div>
                                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                                    {group.desc(appliedThresholds)}
                                                </div>
                                            </div>
                                            <div style={{ overflowX: 'auto' }}>
                                                <table className="data-table" style={{ fontSize: 13 }}>
                                                    <thead>
                                                        <tr>
                                                            <th>№ заявки</th>
                                                            <th>Статус</th>
                                                            <th>Склад</th>
                                                            <th>Целевой склад ВБ</th>
                                                            <th style={{ textAlign: 'right' }}>Шт</th>
                                                            <th style={{ textAlign: 'right' }}>Висит</th>
                                                            <th>С какого числа</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {rows.map(row => {
                                                            const badge = STATUS_BADGE[row.status] ?? { label: row.status, cls: 'badge-secondary' };
                                                            const critical = group.kind === 'wb_accepted_not_shipped'
                                                                || (limit > 0 && row.days_stuck >= limit * 2);
                                                            return (
                                                                <tr key={row.id}>
                                                                    <td>
                                                                        <Link
                                                                            href={`/p/${slug}/warehouse/assembly/${row.id}`}
                                                                            style={{ color: 'var(--color-accent)', fontWeight: 600, textDecoration: 'none' }}
                                                                        >
                                                                            {row.number}
                                                                        </Link>
                                                                    </td>
                                                                    <td>
                                                                        <span className={`badge ${badge.cls}`} style={{ fontSize: 11 }}>
                                                                            {badge.label}
                                                                        </span>
                                                                    </td>
                                                                    <td style={{ color: 'var(--color-text-muted)' }}>
                                                                        {row.warehouse_name || '—'}
                                                                    </td>
                                                                    <td>
                                                                        {row.wb_warehouse_name || '—'}
                                                                        {group.kind === 'wb_accepted_not_shipped' && row.wb_fbo_status && (
                                                                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                                                                WB: {row.wb_fbo_status}
                                                                            </div>
                                                                        )}
                                                                    </td>
                                                                    <td style={{ textAlign: 'right' }}>{formatNumber(row.total_qty, 0)}</td>
                                                                    <td style={{ textAlign: 'right' }}>
                                                                        <span
                                                                            style={{
                                                                                fontWeight: 700,
                                                                                color: critical ? 'var(--color-danger)' : 'var(--color-text)',
                                                                            }}
                                                                        >
                                                                            {formatNumber(row.days_stuck, 1)} дн
                                                                        </span>
                                                                    </td>
                                                                    <td style={{ color: 'var(--color-text-muted)' }}>
                                                                        {row.since ? formatDate(row.since) : '—'}
                                                                    </td>
                                                                </tr>
                                                            );
                                                        })}
                                                    </tbody>
                                                </table>
                                            </div>
                                        </div>
                                    );
                                })}
                                </div>
                            </>
                        )}
                    </div>

                    {/* Разрез по складам */}
                    {sortedByWarehouse.length > 0 && (
                        <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                            <div style={{ padding: '16px 20px 0' }}>
                                <SectionTitle>Разрез по складам</SectionTitle>
                            </div>
                            <div style={{ overflowX: 'auto' }}>
                                <table className="data-table" style={{ fontSize: 13 }}>
                                    <thead>
                                        <tr>
                                            <th>Склад</th>
                                            <th style={{ textAlign: 'right' }}>В работе</th>
                                            <th style={{ textAlign: 'right' }}>Средний цикл</th>
                                            <th style={{ textAlign: 'right' }}>Аномалии</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {sortedByWarehouse.map(w => (
                                            <tr key={w.warehouse_id}>
                                                <td style={{ fontWeight: 500 }}>
                                                    {w.warehouse_name || `Склад ${w.warehouse_id}`}
                                                </td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(w.active_count, 0)}</td>
                                                <td style={{ textAlign: 'right' }}>{fmtDays(w.avg_cycle_days)}</td>
                                                <td style={{ textAlign: 'right' }}>
                                                    {w.anomaly_count > 0 ? (
                                                        <span className="badge badge-danger" style={{ fontSize: 11 }}>
                                                            {formatNumber(w.anomaly_count, 0)}
                                                        </span>
                                                    ) : (
                                                        <span style={{ color: 'var(--color-text-dim)' }}>0</span>
                                                    )}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}
                </>
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
