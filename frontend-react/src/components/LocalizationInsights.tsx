'use client';

import { Fragment, useMemo, useState } from 'react';
import type { LocalizationSkuRow } from '@/types/api';
import { formatNumber } from '@/lib/utils';
import {
    DISTRICT_COLORS,
    DISTRICT_LABELS,
    DISTRICT_ORDER,
    IDEAL_KRP,
    IDEAL_KTR,
    getKrp,
    getKtr,
} from '@/lib/constants/localization';

type TabKey = 'simulator' | 'groups';

const TABS: ReadonlyArray<{ key: TabKey; label: string; hint: string }> = [
    {
        key: 'simulator',
        label: 'Pareto-симулятор',
        hint: 'если развести top-N% — каким будет ИЛ/ИРП и какие именно артикулы',
    },
    { key: 'groups', label: 'Сводка', hint: 'средневзвешенный КТР/КРП по группам' },
];

const PRESET_PCTS: ReadonlyArray<number> = [5, 10, 20, 30, 50];

type GroupBy = 'brand' | 'subject';

interface DistChunk {
    /** Ключ ФО (DISTRICT_ORDER) */
    key: string;
    label: string;
    nonLocal: number;
}

interface SelectedRow {
    nm_id: number;
    vendor_code: string;
    title: string;
    brand: string;
    subject: string;
    total: number;
    non_local: number;
    loc_pct: number;
    ktr: number;
    krp: number;
    contribution: number;
    potentialSave: number;
    /** Топ-3 ФО с максимальным non_local (без abroad/unknown). */
    distribution: DistChunk[];
}

interface GroupStat {
    /** Ключ группы (бренд или предмет) */
    key: string;
    skus: number;
    orders: number;
    locPct: number;
    ktr: number;
    krp: number;
    contribution: number;
    potentialSave: number;
}

/**
 * potential_save = orders × (ktr - 0.5) — потенциал ужать contribution до
 * идеального КТР. Чем больше — тем выгоднее «развести» SKU по складам.
 *
 * Если КТР уже 0.5 (≥95% локализации) — potential_save ≤ 0, SKU не нуждается
 * в развозке.
 */
function potentialSave(row: Pick<LocalizationSkuRow, 'total' | 'ktr'>): number {
    return Math.max(0, Number(row.total) * (Number(row.ktr) - IDEAL_KTR));
}

/**
 * Топ-3 ФО (без abroad/unknown) по `non_local` для одного артикула —
 * куда и сколько штук довезти. Если у артикула вообще нет нелокальных
 * заказов — пустой массив.
 */
function distributionFor(row: LocalizationSkuRow): DistChunk[] {
    if (!row.districts || row.districts.length === 0) return [];
    const chunks: DistChunk[] = [];
    for (const d of row.districts) {
        if (d.district === 'abroad' || d.district === 'unknown') continue;
        if (d.non_local <= 0) continue;
        chunks.push({
            key: d.district,
            label: DISTRICT_LABELS[d.district] ?? d.district,
            nonLocal: Number(d.non_local) || 0,
        });
    }
    chunks.sort((a, b) => b.nonLocal - a.nonLocal);
    return chunks.slice(0, 3);
}

/**
 * Рекомендация (одно слово+число) — берём первый ФО из distributionFor.
 * Используется в маленьких карточках/тестах.
 */
function suggestDistrict(row: LocalizationSkuRow): string {
    const dist = distributionFor(row);
    if (!dist.length) return '—';
    const top = dist[0];
    return `${top.label} • ${top.nonLocal} нелок.`;
}

interface InsightsProps {
    /** Уже отфильтрованные строки (учитывают search/status/brand/subject). */
    rows: LocalizationSkuRow[];
    /** Полная серверная сводка по проекту (без фильтров) — для baseline в симуляторе. */
    baselineLoc?: number;
    baselineIrp?: number;
}

/**
 * Insights-блок над таблицей. 2 вкладки:
 * - Pareto-симулятор: ползунок «топ-N%», KPI до/после, таблица выбранных
 *   SKU + рекомендация «куда довезти».
 * - Сводка по брендам/категориям: groupBy toggle, средневзвешенные КТР/КРП.
 *
 * Все расчёты — клиентские, на основе rows. Реагирует на фильтры страницы.
 */
export default function LocalizationInsights({
    rows,
    baselineLoc,
    baselineIrp,
}: InsightsProps) {
    const [tab, setTab] = useState<TabKey>('simulator');
    const [pct, setPct] = useState<number>(20);
    const [groupBy, setGroupBy] = useState<GroupBy>('brand');

    /** Список с potentialSave + distribution. */
    const enriched = useMemo(() => {
        return rows.map(r => ({
            row: r,
            potentialSave: potentialSave(r),
            distribution: distributionFor(r),
        }));
    }, [rows]);

    /** Симулятор: какие именно SKU «развозим» + KPI до/после. */
    const simulation = useMemo(() => {
        if (!enriched.length) {
            return null;
        }
        // Кандидаты на развоз — только те у кого potentialSave > 0
        // (КТР > 0.5). Идеальные SKU не входят в «развоз», но входят в
        // средневзвешенный пересчёт.
        const candidates = enriched
            .filter(r => r.potentialSave > 0)
            .sort((a, b) => b.potentialSave - a.potentialSave);

        if (!candidates.length) {
            return {
                n: 0,
                totalSkus: enriched.length,
                candidatesCount: 0,
                targetOrders: 0,
                totalOrders: enriched.reduce((s, e) => s + Number(e.row.total) || s, 0),
                targetSavedOrders: 0,
                baseLoc: 0,
                baseIrp: 0,
                baseLocPctOverall: 0,
                newLoc: 0,
                newIrp: 0,
                deltaLoc: 0,
                deltaIrp: 0,
                savedContribution: 0,
                selected: [] as SelectedRow[],
            };
        }
        const n = Math.max(1, Math.min(candidates.length, Math.round((candidates.length * pct) / 100)));
        const targetSet = new Set(candidates.slice(0, n).map(r => r.row.nm_id));

        let totalOrders = 0;
        let baseWKtr = 0;
        let baseWKrp = 0;
        let baseWLoc = 0;
        let newWKtr = 0;
        let newWKrp = 0;
        let baseContribution = 0;
        let newContribution = 0;
        let targetOrders = 0;
        let targetSavedOrders = 0;

        for (const e of enriched) {
            const r = e.row;
            const orders = Number(r.total) || 0;
            totalOrders += orders;
            baseWKtr += orders * Number(r.ktr);
            baseWKrp += orders * Number(r.krp);
            baseWLoc += orders * Number(r.loc_pct);
            baseContribution += Number(r.contribution);
            if (targetSet.has(r.nm_id)) {
                newWKtr += orders * IDEAL_KTR;
                newWKrp += orders * IDEAL_KRP;
                newContribution += orders * IDEAL_KTR;
                targetOrders += orders;
                targetSavedOrders += Number(r.non_local) || 0;
            } else {
                newWKtr += orders * Number(r.ktr);
                newWKrp += orders * Number(r.krp);
                newContribution += Number(r.contribution);
            }
        }

        const denom = totalOrders > 0 ? totalOrders : 1;
        const baseLoc = baseWKtr / denom;
        const baseIrp = baseWKrp / denom;
        const baseLocPctOverall = baseWLoc / denom;
        const newLoc = newWKtr / denom;
        const newIrp = newWKrp / denom;

        const selected: SelectedRow[] = candidates.slice(0, n).map(e => ({
            nm_id: e.row.nm_id,
            vendor_code: e.row.vendor_code,
            title: e.row.title,
            brand: e.row.brand || '—',
            subject: e.row.subject || '—',
            total: Number(e.row.total) || 0,
            non_local: Number(e.row.non_local) || 0,
            loc_pct: Number(e.row.loc_pct) || 0,
            ktr: Number(e.row.ktr) || 0,
            krp: Number(e.row.krp) || 0,
            contribution: Number(e.row.contribution) || 0,
            potentialSave: e.potentialSave,
            distribution: e.distribution,
        }));

        return {
            n,
            totalSkus: enriched.length,
            candidatesCount: candidates.length,
            targetOrders,
            totalOrders,
            targetSavedOrders,
            baseLoc,
            baseIrp,
            baseLocPctOverall,
            newLoc,
            newIrp,
            deltaLoc: baseLoc - newLoc,
            deltaIrp: baseIrp - newIrp,
            savedContribution: baseContribution - newContribution,
            selected,
        };
    }, [enriched, pct]);

    /** Группы (бренд или категория): средневзвешенные КТР/КРП по orders. */
    const groupStats: GroupStat[] = useMemo(() => {
        const acc = new Map<
            string,
            {
                skus: number;
                orders: number;
                wKtr: number;
                wKrp: number;
                wLoc: number;
                contribution: number;
                potentialSave: number;
            }
        >();
        for (const e of enriched) {
            const r = e.row;
            const rawKey = groupBy === 'brand' ? r.brand : r.subject;
            const key = rawKey || '—';
            const orders = Number(r.total) || 0;
            const cur = acc.get(key) ?? {
                skus: 0,
                orders: 0,
                wKtr: 0,
                wKrp: 0,
                wLoc: 0,
                contribution: 0,
                potentialSave: 0,
            };
            cur.skus += 1;
            cur.orders += orders;
            cur.wKtr += orders * Number(r.ktr);
            cur.wKrp += orders * Number(r.krp);
            cur.wLoc += orders * Number(r.loc_pct);
            cur.contribution += Number(r.contribution);
            cur.potentialSave += e.potentialSave;
            acc.set(key, cur);
        }
        return Array.from(acc.entries())
            .map(([key, v]) => {
                const denom = v.orders > 0 ? v.orders : 1;
                return {
                    key,
                    skus: v.skus,
                    orders: v.orders,
                    locPct: v.wLoc / denom,
                    ktr: v.wKtr / denom,
                    krp: v.wKrp / denom,
                    contribution: v.contribution,
                    potentialSave: v.potentialSave,
                };
            })
            .sort((a, b) => b.potentialSave - a.potentialSave);
    }, [enriched, groupBy]);

    const empty = !rows.length;
    // Suppress unused-import warning for KTR/KRP helpers; они re-exported для тестов.
    void getKtr;
    void getKrp;
    void DISTRICT_ORDER;

    return (
        <div className="glass-card loc-insights-card">
            <style>{`
                .loc-insights-card { padding: 16px 20px 20px; margin-bottom: 16px; }
                .loc-insights-tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 14px; border-bottom: 1px solid var(--color-border); padding-bottom: 12px; }
                .loc-insights-tab { padding: 6px 14px; border-radius: 8px; border: 1px solid transparent; background: transparent; color: var(--color-text-muted); font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.15s; }
                .loc-insights-tab:hover { color: var(--color-accent); background: rgba(0, 113, 227, 0.06); }
                .loc-insights-tab.active { background: var(--color-accent); color: white; border-color: var(--color-accent); }
                .loc-insights-hint { font-size: 11px; color: var(--color-text-muted); margin-top: -8px; margin-bottom: 12px; }

                .loc-insights-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12px; }
                .loc-insights-table th { text-align: left; padding: 8px 10px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-muted); border-bottom: 1px solid var(--color-border); white-space: nowrap; user-select: none; background: #f5f5f7; }
                .loc-insights-table th.sortable { cursor: pointer; }
                .loc-insights-table th.sortable:hover { color: var(--color-accent); }
                .loc-insights-table th.num, .loc-insights-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
                .loc-insights-table td { padding: 8px 10px; border-bottom: 1px solid var(--color-border); white-space: nowrap; }
                .loc-insights-table tbody tr:hover td { background: rgba(0, 113, 227, 0.04); }
                .loc-insights-table tbody tr:last-child td { border-bottom: none; }

                .loc-sim-grid { display: grid; grid-template-columns: 280px 1fr; gap: 24px; align-items: start; margin-bottom: 16px; }
                @media (max-width: 900px) { .loc-sim-grid { grid-template-columns: 1fr; } }
                .loc-sim-presets { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
                .loc-sim-preset { padding: 6px 12px; border-radius: 8px; border: 1px solid var(--color-border); background: transparent; color: var(--color-text-muted); font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.15s; }
                .loc-sim-preset:hover { background: rgba(0, 113, 227, 0.05); color: var(--color-accent); }
                .loc-sim-preset.active { background: var(--color-accent); color: white; border-color: var(--color-accent); }
                .loc-sim-input { width: 100%; padding: 8px 12px; border-radius: 8px; border: 1px solid var(--color-border); background: var(--color-bg-card); color: inherit; font-size: 13px; margin-bottom: 12px; }
                .loc-sim-summary { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--color-text-muted); }
                .loc-sim-summary b { color: var(--color-text); }

                .loc-sim-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
                .loc-sim-cell { padding: 14px 16px; border-radius: 12px; background: rgba(0, 113, 227, 0.04); border: 1px solid rgba(0, 113, 227, 0.12); }
                .loc-sim-cell.success { background: rgba(52, 199, 89, 0.08); border-color: rgba(52, 199, 89, 0.18); }
                .loc-sim-cell-label { font-size: 11px; font-weight: 700; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
                .loc-sim-cell .row { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
                .loc-sim-cell .now { font-size: 14px; color: var(--color-text-muted); }
                .loc-sim-cell .next { font-size: 22px; font-weight: 700; letter-spacing: -0.02em; }
                .loc-sim-cell.success .next { color: var(--color-success); }
                .loc-sim-cell .delta { font-size: 11px; color: var(--color-success); margin-top: 4px; }

                .loc-dist-chips { display: inline-flex; gap: 4px; flex-wrap: wrap; }
                .loc-dist-chip { display: inline-block; padding: 1px 8px; border-radius: 12px; background: rgba(0, 113, 227, 0.08); color: var(--color-accent); font-weight: 600; font-size: 11px; }
                .loc-region-pill { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; vertical-align: middle; }
                .loc-region-row { cursor: pointer; user-select: none; }
                .loc-region-row:hover td { background: rgba(0, 113, 227, 0.06) !important; }
                .loc-region-row[aria-expanded="true"] td { background: rgba(0, 113, 227, 0.04); }
                .loc-region-caret { display: inline-block; width: 14px; color: var(--color-text-muted); font-size: 9px; margin-right: 2px; }
                .loc-region-detail td { padding: 0 !important; background: rgba(0, 113, 227, 0.02); border-bottom: 1px solid var(--color-border); }
                .loc-region-inner { width: 100%; margin: 0; }
                .loc-region-inner th, .loc-region-inner td { background: transparent !important; font-size: 11px; padding: 6px 10px !important; }
                .loc-region-inner th { color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.04em; font-size: 10px; }

                .loc-group-toggle { display: inline-flex; gap: 4px; padding: 3px; background: rgba(0, 0, 0, 0.04); border-radius: 8px; margin-bottom: 12px; }
                .loc-group-toggle button { padding: 6px 14px; border-radius: 6px; border: none; background: transparent; color: var(--color-text-muted); font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.15s; }
                .loc-group-toggle button.active { background: var(--color-bg-card); color: var(--color-text); box-shadow: var(--shadow-glass); }

                .loc-section-mini { font-size: 12px; font-weight: 700; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin: 16px 0 8px; }
                .loc-empty-mini { font-size: 12px; color: var(--color-text-muted); padding: 16px 0; }
            `}</style>

            <div className="loc-insights-tabs" role="tablist">
                {TABS.map(t => (
                    <button
                        key={t.key}
                        role="tab"
                        aria-selected={tab === t.key}
                        className={`loc-insights-tab ${tab === t.key ? 'active' : ''}`}
                        onClick={() => setTab(t.key)}
                    >
                        {t.label}
                    </button>
                ))}
            </div>

            {empty && (
                <div className="loc-empty-mini">
                    Нет артикулов в выборке. Снимите фильтры или расширьте период.
                </div>
            )}

            {!empty && tab === 'simulator' && simulation && (
                <SimulatorTab
                    pct={pct}
                    onPct={setPct}
                    sim={simulation}
                    baselineLoc={baselineLoc}
                    baselineIrp={baselineIrp}
                    hint={TABS[0].hint}
                />
            )}

            {!empty && tab === 'groups' && (
                <GroupsTab
                    rows={groupStats}
                    groupBy={groupBy}
                    onGroupBy={setGroupBy}
                    hint={TABS[1].hint}
                />
            )}
        </div>
    );
}

// ─── Tab: Pareto-симулятор + таблица выбранных ────────────────────────────────

interface SimResult {
    n: number;
    totalSkus: number;
    candidatesCount: number;
    targetOrders: number;
    totalOrders: number;
    targetSavedOrders: number;
    baseLoc: number;
    baseIrp: number;
    baseLocPctOverall: number;
    newLoc: number;
    newIrp: number;
    deltaLoc: number;
    deltaIrp: number;
    savedContribution: number;
    selected: SelectedRow[];
}

type SelectedSortKey =
    | 'vendor_code'
    | 'brand'
    | 'total'
    | 'non_local'
    | 'loc_pct'
    | 'ktr'
    | 'potentialSave';

interface RegionRollup {
    /** Ключ ФО */
    key: string;
    label: string;
    /** Σ нелок-заказов в этот ФО среди выбранных артикулов */
    nonLocal: number;
    /** Уникальных артикулов с нелок-заказами в этот ФО */
    skus: number;
    /** Доля от общего нелок-объёма выборки, % */
    sharePct: number;
}

type RegionSortKey = 'label' | 'nonLocal' | 'skus' | 'sharePct';

/**
 * Сводка «куда сколько отправлять» по выбранным артикулам.
 *
 * Группируем все non_local-заказы выбранных артикулов по ФО (без abroad/unknown),
 * суммируем штуки, считаем число уникальных SKU и долю от общего нелок-объёма.
 *
 * Используется как «план логистики» — одной строкой на регион видно, сколько
 * штук туда довезти и от скольких артикулов.
 */
function rollupByRegion(selected: SelectedRow[]): RegionRollup[] {
    if (!selected.length) return [];
    const acc = new Map<string, { label: string; nonLocal: number; skus: number }>();
    for (const r of selected) {
        for (const d of r.distribution) {
            const cur = acc.get(d.key) ?? { label: d.label, nonLocal: 0, skus: 0 };
            cur.nonLocal += d.nonLocal;
            cur.skus += 1;
            acc.set(d.key, cur);
        }
    }
    const total = Array.from(acc.values()).reduce((s, v) => s + v.nonLocal, 0);
    return Array.from(acc.entries())
        .map(([key, v]) => ({
            key,
            label: v.label,
            nonLocal: v.nonLocal,
            skus: v.skus,
            sharePct: total > 0 ? (v.nonLocal / total) * 100 : 0,
        }))
        .sort((a, b) => b.nonLocal - a.nonLocal);
}

function SimulatorTab({
    pct,
    onPct,
    sim,
    baselineLoc,
    baselineIrp,
    hint,
}: {
    pct: number;
    onPct: (v: number) => void;
    sim: SimResult;
    baselineLoc?: number;
    baselineIrp?: number;
    hint: string;
}) {
    const [sortKey, setSortKey] = useState<SelectedSortKey>('potentialSave');
    const [sortDesc, setSortDesc] = useState(true);
    const [rSortKey, setRSortKey] = useState<RegionSortKey>('nonLocal');
    const [rSortDesc, setRSortDesc] = useState(true);
    const [expandedRegion, setExpandedRegion] = useState<string | null>(null);

    const regionRollup = useMemo(() => rollupByRegion(sim.selected), [sim.selected]);
    const regionRollupSorted = useMemo(() => {
        const copy = [...regionRollup];
        copy.sort((a, b) => {
            const av = a[rSortKey];
            const bv = b[rSortKey];
            if (typeof av === 'number' && typeof bv === 'number') {
                return rSortDesc ? bv - av : av - bv;
            }
            return rSortDesc
                ? String(bv).localeCompare(String(av))
                : String(av).localeCompare(String(bv));
        });
        return copy;
    }, [regionRollup, rSortKey, rSortDesc]);
    const regionTotal = useMemo(
        () => regionRollup.reduce((s, r) => s + r.nonLocal, 0),
        [regionRollup],
    );
    const handleRSort = (k: RegionSortKey) => {
        if (rSortKey === k) setRSortDesc(!rSortDesc);
        else {
            setRSortKey(k);
            setRSortDesc(true);
        }
    };
    const rArrow = (k: RegionSortKey) => (rSortKey === k ? (rSortDesc ? ' ↓' : ' ↑') : '');

    const sortedSelected = useMemo(() => {
        const copy = [...sim.selected];
        copy.sort((a, b) => {
            const av = a[sortKey];
            const bv = b[sortKey];
            if (typeof av === 'number' && typeof bv === 'number') {
                return sortDesc ? bv - av : av - bv;
            }
            return sortDesc
                ? String(bv).localeCompare(String(av))
                : String(av).localeCompare(String(bv));
        });
        return copy;
    }, [sim.selected, sortKey, sortDesc]);

    const handleSort = (k: SelectedSortKey) => {
        if (sortKey === k) setSortDesc(!sortDesc);
        else {
            setSortKey(k);
            setSortDesc(true);
        }
    };
    const arrow = (k: SelectedSortKey) => (sortKey === k ? (sortDesc ? ' ↓' : ' ↑') : '');

    const targetShare = sim.totalOrders > 0 ? (sim.targetOrders / sim.totalOrders) * 100 : 0;

    return (
        <>
            <div className="loc-insights-hint">
                {hint}. «Идеально развести» = довести `% лок` до ≥95% (КТР {formatNumber(IDEAL_KTR, 2)}, КРП 0%).
            </div>
            <div className="loc-sim-grid">
                <div>
                    <div className="loc-sim-presets" role="tablist">
                        {PRESET_PCTS.map(p => (
                            <button
                                key={p}
                                className={`loc-sim-preset ${p === pct ? 'active' : ''}`}
                                onClick={() => onPct(p)}
                            >
                                {p}%
                            </button>
                        ))}
                        <button
                            className={`loc-sim-preset ${pct === 100 ? 'active' : ''}`}
                            onClick={() => onPct(100)}
                        >
                            Все
                        </button>
                    </div>
                    <input
                        type="range"
                        min={1}
                        max={100}
                        step={1}
                        value={pct}
                        onChange={e => onPct(Number(e.target.value))}
                        className="loc-sim-input"
                        aria-label="Доля артикулов на развоз, %"
                    />
                    <div className="loc-sim-summary">
                        <div>
                            Развозим: <b>{formatNumber(sim.n, 0)}</b> из{' '}
                            <b>{formatNumber(sim.candidatesCount, 0)}</b> кандидатов (всего в выборке{' '}
                            <b>{formatNumber(sim.totalSkus, 0)}</b> арт., из них{' '}
                            <b>{formatNumber(sim.totalSkus - sim.candidatesCount, 0)}</b> уже идеальные).
                        </div>
                        <div>
                            Покрывает <b>{formatNumber(targetShare, 1)}%</b> заказов (
                            {formatNumber(sim.targetOrders, 0)} из {formatNumber(sim.totalOrders, 0)}).
                        </div>
                        <div>
                            «На исправление» нелокальных заказов:{' '}
                            <b>{formatNumber(sim.targetSavedOrders, 0)}</b> шт.
                        </div>
                    </div>
                </div>
                <div className="loc-sim-cards">
                    <div className="loc-sim-cell success">
                        <div className="loc-sim-cell-label">ИЛ</div>
                        <div className="row">
                            <span className="now">{formatNumber(sim.baseLoc, 2)} →</span>
                            <span className="next">{formatNumber(sim.newLoc, 2)}</span>
                        </div>
                        <div className="delta">
                            -{formatNumber(sim.deltaLoc, 3)}{' '}
                            {baselineLoc != null && Math.abs(baselineLoc - sim.baseLoc) > 0.01 && (
                                <span style={{ color: 'var(--color-text-muted)' }}>
                                    {' '}(baseline {formatNumber(baselineLoc, 2)})
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="loc-sim-cell success">
                        <div className="loc-sim-cell-label">ИРП</div>
                        <div className="row">
                            <span className="now">{formatNumber(sim.baseIrp, 2)}% →</span>
                            <span className="next">{formatNumber(sim.newIrp, 2)}%</span>
                        </div>
                        <div className="delta">
                            -{formatNumber(sim.deltaIrp, 3)}%{' '}
                            {baselineIrp != null && Math.abs(baselineIrp - sim.baseIrp) > 0.01 && (
                                <span style={{ color: 'var(--color-text-muted)' }}>
                                    {' '}(baseline {formatNumber(baselineIrp, 2)}%)
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="loc-sim-cell">
                        <div className="loc-sim-cell-label">Сэкономленный вклад</div>
                        <div className="row">
                            <span className="next">{formatNumber(sim.savedContribution, 1)}</span>
                        </div>
                        <div className="delta" style={{ color: 'var(--color-text-muted)' }}>
                            шт.×КТР, исчезнет из суммы при идеальном развозе
                        </div>
                    </div>
                </div>
            </div>

            {regionRollup.length > 0 && (
                <>
                    <div className="loc-section-mini">
                        Куда сколько отправить (сводно по {formatNumber(regionRollup.length, 0)} ФО)
                    </div>
                    <div style={{ overflowX: 'auto' }}>
                        <table
                            className="loc-insights-table"
                            data-testid="insights-region-rollup"
                        >
                            <thead>
                                <tr>
                                    <th className="sortable" onClick={() => handleRSort('label')}>
                                        Федеральный округ{rArrow('label')}
                                    </th>
                                    <th
                                        className="sortable num"
                                        onClick={() => handleRSort('nonLocal')}
                                        title="Σ нелок-заказов из выбранных артикулов в этот ФО"
                                    >
                                        Нужно отправить (шт.){rArrow('nonLocal')}
                                    </th>
                                    <th
                                        className="sortable num"
                                        onClick={() => handleRSort('skus')}
                                        title="Уникальных артикулов с нелок-заказами в этот ФО"
                                    >
                                        Артикулов{rArrow('skus')}
                                    </th>
                                    <th
                                        className="sortable num"
                                        onClick={() => handleRSort('sharePct')}
                                    >
                                        Доля{rArrow('sharePct')}
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                {regionRollupSorted.map(r => {
                                    const isOpen = expandedRegion === r.key;
                                    const skusInRegion = sim.selected
                                        .map(s => {
                                            const d = s.distribution.find(x => x.key === r.key);
                                            if (!d || d.nonLocal <= 0) return null;
                                            return { row: s, regionNonLocal: d.nonLocal };
                                        })
                                        .filter((x): x is { row: SelectedRow; regionNonLocal: number } => x !== null)
                                        .sort((a, b) => b.regionNonLocal - a.regionNonLocal);
                                    return (
                                        <Fragment key={r.key}>
                                            <tr
                                                className="loc-region-row"
                                                onClick={() =>
                                                    setExpandedRegion(isOpen ? null : r.key)
                                                }
                                                aria-expanded={isOpen}
                                                data-testid={`region-row-${r.key}`}
                                            >
                                                <td>
                                                    <span
                                                        className="loc-region-caret"
                                                        aria-hidden="true"
                                                    >
                                                        {isOpen ? '▼' : '▶'}
                                                    </span>
                                                    <span
                                                        className="loc-region-pill"
                                                        style={{
                                                            background:
                                                                DISTRICT_COLORS[r.key] ?? '#475569',
                                                        }}
                                                    />
                                                    {r.label}
                                                </td>
                                                <td
                                                    className="num"
                                                    style={{
                                                        fontWeight: 700,
                                                        color: 'var(--color-danger)',
                                                    }}
                                                >
                                                    {formatNumber(r.nonLocal, 0)}
                                                </td>
                                                <td className="num">{formatNumber(r.skus, 0)}</td>
                                                <td className="num">
                                                    {formatNumber(r.sharePct, 1)}%
                                                </td>
                                            </tr>
                                            {isOpen && (
                                                <tr
                                                    className="loc-region-detail"
                                                    data-testid={`region-detail-${r.key}`}
                                                >
                                                    <td colSpan={4} style={{ padding: 0 }}>
                                                        <table className="loc-insights-table loc-region-inner">
                                                            <thead>
                                                                <tr>
                                                                    <th>Артикул</th>
                                                                    <th>Название</th>
                                                                    <th>Бренд</th>
                                                                    <th className="num">
                                                                        Заказов всего
                                                                    </th>
                                                                    <th className="num">
                                                                        В этот ФО (нелок.)
                                                                    </th>
                                                                    <th className="num">КТР</th>
                                                                    <th className="num">% лок.</th>
                                                                </tr>
                                                            </thead>
                                                            <tbody>
                                                                {skusInRegion.map(s => (
                                                                    <tr key={s.row.nm_id}>
                                                                        <td>{s.row.vendor_code}</td>
                                                                        <td
                                                                            style={{
                                                                                maxWidth: 240,
                                                                                overflow: 'hidden',
                                                                                textOverflow:
                                                                                    'ellipsis',
                                                                            }}
                                                                            title={s.row.title}
                                                                        >
                                                                            {s.row.title}
                                                                        </td>
                                                                        <td
                                                                            style={{
                                                                                color:
                                                                                    'var(--color-text-muted)',
                                                                            }}
                                                                        >
                                                                            {s.row.brand}
                                                                        </td>
                                                                        <td className="num">
                                                                            {formatNumber(
                                                                                s.row.total,
                                                                                0,
                                                                            )}
                                                                        </td>
                                                                        <td
                                                                            className="num"
                                                                            style={{
                                                                                fontWeight: 700,
                                                                                color:
                                                                                    'var(--color-danger)',
                                                                            }}
                                                                        >
                                                                            {formatNumber(
                                                                                s.regionNonLocal,
                                                                                0,
                                                                            )}
                                                                        </td>
                                                                        <td
                                                                            className="num"
                                                                            style={{
                                                                                fontWeight: 600,
                                                                            }}
                                                                        >
                                                                            {formatNumber(
                                                                                s.row.ktr,
                                                                                2,
                                                                            )}
                                                                        </td>
                                                                        <td className="num">
                                                                            {formatNumber(
                                                                                s.row.loc_pct,
                                                                                1,
                                                                            )}
                                                                            %
                                                                        </td>
                                                                    </tr>
                                                                ))}
                                                            </tbody>
                                                        </table>
                                                    </td>
                                                </tr>
                                            )}
                                        </Fragment>
                                    );
                                })}
                                <tr style={{ fontWeight: 700 }}>
                                    <td>ИТОГО</td>
                                    <td className="num" style={{ color: 'var(--color-danger)' }}>
                                        {formatNumber(regionTotal, 0)}
                                    </td>
                                    <td className="num">—</td>
                                    <td className="num">100,0%</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </>
            )}

            <div className="loc-section-mini">
                Что именно развозить ({formatNumber(sim.n, 0)} арт., отсортировано по потенциалу)
            </div>
            {sim.selected.length === 0 ? (
                <div className="loc-empty-mini">
                    Все артикулы уже в идеальном КТР (0.50). Развозить нечего.
                </div>
            ) : (
                <div style={{ overflowX: 'auto' }}>
                    <table className="loc-insights-table" data-testid="insights-selected">
                        <thead>
                            <tr>
                                <th className="sortable" onClick={() => handleSort('vendor_code')}>
                                    Артикул{arrow('vendor_code')}
                                </th>
                                <th>Название</th>
                                <th className="sortable" onClick={() => handleSort('brand')}>
                                    Бренд{arrow('brand')}
                                </th>
                                <th className="sortable num" onClick={() => handleSort('total')}>
                                    Заказов{arrow('total')}
                                </th>
                                <th className="sortable num" onClick={() => handleSort('non_local')}>
                                    Нелок.{arrow('non_local')}
                                </th>
                                <th className="sortable num" onClick={() => handleSort('loc_pct')}>
                                    % лок.{arrow('loc_pct')}
                                </th>
                                <th className="sortable num" onClick={() => handleSort('ktr')}>
                                    КТР{arrow('ktr')}
                                </th>
                                <th className="num">→ КТР</th>
                                <th
                                    className="sortable num"
                                    onClick={() => handleSort('potentialSave')}
                                    title="orders × (КТР - 0.50)"
                                >
                                    Потенциал ↓{arrow('potentialSave')}
                                </th>
                                <th>Куда довезти (топ-3 ФО, нелок. заказы)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sortedSelected.map(r => (
                                <tr key={r.nm_id}>
                                    <td>{r.vendor_code}</td>
                                    <td
                                        style={{
                                            maxWidth: 220,
                                            overflow: 'hidden',
                                            textOverflow: 'ellipsis',
                                        }}
                                        title={r.title}
                                    >
                                        {r.title}
                                    </td>
                                    <td style={{ color: 'var(--color-text-muted)' }}>{r.brand || '—'}</td>
                                    <td className="num">{formatNumber(r.total, 0)}</td>
                                    <td className="num" style={{ color: 'var(--color-danger)' }}>
                                        {formatNumber(r.non_local, 0)}
                                    </td>
                                    <td className="num">{formatNumber(r.loc_pct, 1)}%</td>
                                    <td className="num" style={{ fontWeight: 600 }}>
                                        {formatNumber(r.ktr, 2)}
                                    </td>
                                    <td className="num" style={{ color: 'var(--color-success)', fontWeight: 600 }}>
                                        {formatNumber(IDEAL_KTR, 2)}
                                    </td>
                                    <td className="num" style={{ fontWeight: 700 }}>
                                        -{formatNumber(r.potentialSave, 1)}
                                    </td>
                                    <td>
                                        {r.distribution.length === 0 ? (
                                            <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                                        ) : (
                                            <span className="loc-dist-chips">
                                                {r.distribution.map(d => (
                                                    <span key={d.key} className="loc-dist-chip">
                                                        {d.label}: {formatNumber(d.nonLocal, 0)}
                                                    </span>
                                                ))}
                                            </span>
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </>
    );
}

// ─── Tab: Сводка по группам (бренды / категории) ─────────────────────────────

type GroupSortKey =
    | 'key'
    | 'skus'
    | 'orders'
    | 'locPct'
    | 'ktr'
    | 'krp'
    | 'contribution'
    | 'potentialSave';

function GroupsTab({
    rows,
    groupBy,
    onGroupBy,
    hint,
}: {
    rows: GroupStat[];
    groupBy: GroupBy;
    onGroupBy: (g: GroupBy) => void;
    hint: string;
}) {
    const [sortKey, setSortKey] = useState<GroupSortKey>('potentialSave');
    const [sortDesc, setSortDesc] = useState(true);

    const sorted = useMemo(() => {
        const copy = [...rows];
        copy.sort((a, b) => {
            const av = a[sortKey];
            const bv = b[sortKey];
            if (typeof av === 'number' && typeof bv === 'number') {
                return sortDesc ? bv - av : av - bv;
            }
            return sortDesc
                ? String(bv).localeCompare(String(av))
                : String(av).localeCompare(String(bv));
        });
        return copy;
    }, [rows, sortKey, sortDesc]);

    const handleSort = (k: GroupSortKey) => {
        if (sortKey === k) setSortDesc(!sortDesc);
        else {
            setSortKey(k);
            setSortDesc(true);
        }
    };
    const arrow = (k: GroupSortKey) => (sortKey === k ? (sortDesc ? ' ↓' : ' ↑') : '');

    const colLabel = groupBy === 'brand' ? 'Бренд' : 'Категория';

    if (!rows.length) {
        return <div className="loc-empty-mini">Нет данных для группировки.</div>;
    }

    return (
        <>
            <div className="loc-insights-hint">{hint}.</div>
            <div className="loc-group-toggle" role="tablist" aria-label="Группировка">
                <button
                    role="tab"
                    aria-selected={groupBy === 'brand'}
                    className={groupBy === 'brand' ? 'active' : ''}
                    onClick={() => onGroupBy('brand')}
                >
                    По брендам
                </button>
                <button
                    role="tab"
                    aria-selected={groupBy === 'subject'}
                    className={groupBy === 'subject' ? 'active' : ''}
                    onClick={() => onGroupBy('subject')}
                >
                    По категориям
                </button>
            </div>
            <div style={{ overflowX: 'auto' }}>
                <table
                    className="loc-insights-table"
                    data-testid="insights-groups"
                    data-groupby={groupBy}
                >
                    <thead>
                        <tr>
                            <th className="sortable" onClick={() => handleSort('key')}>
                                {colLabel}
                                {arrow('key')}
                            </th>
                            <th className="sortable num" onClick={() => handleSort('skus')}>
                                Артикулов{arrow('skus')}
                            </th>
                            <th className="sortable num" onClick={() => handleSort('orders')}>
                                Заказов{arrow('orders')}
                            </th>
                            <th className="sortable num" onClick={() => handleSort('locPct')}>
                                % лок.{arrow('locPct')}
                            </th>
                            <th className="sortable num" onClick={() => handleSort('ktr')}>
                                КТР{arrow('ktr')}
                            </th>
                            <th className="sortable num" onClick={() => handleSort('krp')}>
                                КРП %{arrow('krp')}
                            </th>
                            <th className="sortable num" onClick={() => handleSort('contribution')}>
                                Вклад{arrow('contribution')}
                            </th>
                            <th
                                className="sortable num"
                                onClick={() => handleSort('potentialSave')}
                                title="Сумма orders × (КТР - 0.50) по группе"
                            >
                                Потенциал ↓ КТР{arrow('potentialSave')}
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        {sorted.map(b => (
                            <tr key={b.key}>
                                <td>{b.key}</td>
                                <td className="num">{formatNumber(b.skus, 0)}</td>
                                <td className="num">{formatNumber(b.orders, 0)}</td>
                                <td className="num">{formatNumber(b.locPct, 1)}%</td>
                                <td className="num" style={{ fontWeight: 600 }}>
                                    {formatNumber(b.ktr, 2)}
                                </td>
                                <td className="num">{formatNumber(b.krp, 2)}%</td>
                                <td className="num">{formatNumber(b.contribution, 1)}</td>
                                <td className="num" style={{ color: 'var(--color-danger)', fontWeight: 700 }}>
                                    -{formatNumber(b.potentialSave, 1)}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    );
}

// Re-exported helpers for unit tests
export { potentialSave, suggestDistrict, distributionFor, rollupByRegion, getKtr, getKrp };
