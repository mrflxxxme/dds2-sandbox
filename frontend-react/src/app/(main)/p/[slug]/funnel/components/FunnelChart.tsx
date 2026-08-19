'use client';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { COLUMNS, COLUMN_BY_KEY, accumulate, fmtInt, fmtRub, type ColumnDef, type ColumnLayout, type Row } from './columns';
import Tooltip from '@/components/Tooltip';

/* ─── График динамики воронки ──────────────────────────────────────────────
 * Механика — как в «Управлении рекламой» (CampaignMetricsChart): слева
 * мультивыбор метрик, справа комбо — одна «объёмная» метрика лоллипопами,
 * остальные линиями поверх. Метрики несопоставимы по величине, поэтому каждая
 * нормируется на свой пик (шкала 0…100 % от пика), абсолютные значения — в
 * подсказке под курсором.
 * Отличие от рекламы: метрики берутся из каталога колонок воронки (тот же
 * источник правды, что у таблицы и Excel), а производные (ДРР, маржа, CR)
 * считаются той же машинкой ИТОГО — сумма процентов по дням была бы неверной.
 * ─────────────────────────────────────────────────────────────────────── */

/** Цвет серии. Ключи каталога, которых нет в карте, получают цвет из палитры по порядку. */
const SERIES_COLOR: Record<string, string> = {
    orders_count: '#10b981', revenue: '#0ea5e9', adv_sum: '#ef4444', profit: '#16a34a',
    margin: '#84cc16', drr: '#be185d', spp_rate: '#7c3aed', price_after_spp: '#0d9488',
    cost_per_unit: '#a16207', adv_views: '#8b5cf6', ctr: '#6366f1', open_card: '#f59e0b',
    add_to_cart: '#14b8a6', add_to_cart_pct: '#06b6d4', cart_to_order_pct: '#f472b6',
    orders_sum_rub: '#3b82f6', avg_price: '#64748b', buyout_percent: '#059669',
    cost_total: '#6366f1', tax: '#94a3b8', cpm: '#fb7185', cpc: '#f97316', cpl: '#f59e0b',
    cpo: '#a16207', adv_clicks: '#ec4899', commission_rate: '#d946ef', commission: '#c026d3',
};
const FALLBACK = ['#0ea5e9', '#8b5cf6', '#14b8a6', '#f97316', '#ec4899', '#84cc16'];

/** Метрики графика = каталог колонок без расширенных (остатки грузятся отдельным запросом). */
export const CHART_SERIES: { key: string; label: string; color: string; bar: boolean; col: ColumnDef }[] =
    COLUMNS.filter(c => !c.extendedOnly).map((c, i) => ({
        key: c.key,
        label: c.label,
        color: SERIES_COLOR[c.key] ?? FALLBACK[i % FALLBACK.length],
        // «Объёмная» метрика (может рисоваться лоллипопами) — та, что складывается по дням.
        // Проценты, средние и стоимости всегда линией: их пик не имеет физического «нуля-объёма».
        bar: c.total === 'sum' && c.unit !== 'CR',
        col: c,
    }));

const SERIES_BY_KEY: Record<string, typeof CHART_SERIES[number]> = Object.fromEntries(CHART_SERIES.map(s => [s.key, s]));

/** Метрики по умолчанию при первом заходе. */
export const DEFAULT_CHART_SERIES = ['revenue', 'orders_count', 'drr'];

/** Список метрик слева повторяет группы колонок таблицы: та же раскладка, порядок и цвета. */
export function seriesSections(layout: ColumnLayout): { label: string; color: string; items: typeof CHART_SERIES }[] {
    const pick = (keys: string[]) => keys.map(k => SERIES_BY_KEY[k]).filter(Boolean);
    const out = layout.groups
        .map(g => ({ label: g.label, color: g.color, items: pick(g.cols) }))
        .filter(s => s.items.length > 0);
    const placed = new Set(out.flatMap(s => s.items.map(i => i.key)));
    // Метрики вне групп (и появившиеся в каталоге позже) — последней секцией, чтобы не пропали
    const rest = CHART_SERIES.filter(s => !placed.has(s.key));
    if (rest.length) out.push({ label: 'Без группы', color: '#94a3b8', items: rest });
    return out;
}

const fmtCompact = (n: number) => {
    const a = Math.abs(n);
    if (a >= 1_000_000) return (n / 1_000_000).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' млн';
    if (a >= 1_000) return (n / 1_000).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' тыс';
    return n.toLocaleString('ru-RU', { maximumFractionDigits: 1 });
};
const hexA = (hex: string, a: number) => {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
};
const fmtDay = (d: string) => `${d.slice(8, 10)}.${d.slice(5, 7)}`;
const isWeekend = (iso: string) => {
    const [y, m, d] = (iso || '').split('-').map(Number);
    if (!y) return false;
    const wd = new Date(y, m - 1, d).getDay();
    return wd === 0 || wd === 6;
};

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

const FONT = 'var(--font-sans)';
const FS = { tick: 10, legend: 12, hint: 11, tip: 12, empty: 13 };

const W = 1000, PAD_L = 14, PAD_R = 14, PAD_T = 14, PAD_B = 26;
const H_MIN = 160;

/** Значения всех метрик по дням: строки за день сворачиваются той же машинкой, что и ИТОГО.
 *  Складывать можно только потоковые метрики; ДРР, маржа, CR и средние выводятся из сумм. */
export function dailyPoints(rows: Row[]): { date: string; v: Record<string, number | null> }[] {
    const byDate = new Map<string, Row[]>();
    for (const r of rows) {
        const d = r.date || '';
        if (!d) continue;
        const bucket = byDate.get(d);
        if (bucket) bucket.push(r); else byDate.set(d, [r]);
    }
    return Array.from(byDate.entries())
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([date, dayRows]) => {
            const totals = accumulate(dayRows);
            const v: Record<string, number | null> = {};
            for (const s of CHART_SERIES) {
                v[s.key] = s.col.total === 'sum'
                    ? dayRows.reduce((acc, r) => acc + (s.col.value(r) ?? 0), 0)
                    : s.col.total(totals);
            }
            return { date, v };
        });
}

export default function FunnelChart({ rows, layout, selected, onToggle }: {
    rows: Row[];
    /** Раскладка колонок таблицы — задаёт группировку и порядок метрик в списке слева. */
    layout: ColumnLayout;
    selected: string[];
    onToggle: (key: string) => void;
}) {
    const [hover, setHover] = useState<number | null>(null);
    const [mouseX, setMouseX] = useState(0);
    const wrapRef = useRef<HTMLDivElement>(null);
    // График заполняет карточку (а не окно): меряем свою высоту, чтобы влезать без прокрутки
    const fitRef = useRef<HTMLDivElement>(null);
    const [wrapW, setWrapW] = useState(1000);
    const [availH, setAvailH] = useState(400);

    useEffect(() => {
        const el = wrapRef.current;
        const root = fitRef.current;
        if (!el) return;
        const measure = () => { setWrapW(el.clientWidth || 1000); if (root) setAvailH(root.clientHeight || 400); };
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        if (root) ro.observe(root);
        measure();
        return () => ro.disconnect();
        // Пока строк нет, график не отрисован и мерить нечего — переподписываемся,
        // когда данные приехали, иначе размеры навсегда остались бы дефолтными.
    }, [rows.length]);

    // Высота viewBox подобрана под реальную ширину: 1 юнит = 1 пиксель.
    // График занимает ВСЮ свободную высоту карточки (минус свои поля и легенду) — потолка
    // по соотношению сторон нет: на широком окне он иначе жался к верхней трети экрана.
    const CHROME = 54;   // padding контейнера (12+12) + строка легенды с отступом
    const svgPxHeight = Math.round(Math.max(H_MIN, availH - CHROME));
    const H = Math.round((W * svgPxHeight) / Math.max(1, wrapW));

    const points = useMemo(() => dailyPoints(rows), [rows]);
    const sections = useMemo(() => seriesSections(layout), [layout]);

    /* Группы метрик сворачиваются, как группы колонок в настройке таблицы. Пока
     * пользователь их не трогал, раскрыты те, где что-то выбрано, — список короткий,
     * а выбранное всегда на виду. Первый клик фиксирует состояние. */
    const [collapsed, setCollapsed] = useState<Set<string> | null>(null);
    const shown = collapsed ?? new Set(
        sections.filter(sec => !sec.items.some(i => selected.includes(i.key))).map(sec => sec.label));
    const toggleGroup = (label: string) => setCollapsed(() => {
        const next = new Set(shown);
        if (next.has(label)) next.delete(label); else next.add(label);
        return next;
    });

    const geo = useMemo(() => {
        if (points.length === 0) return null;
        const innerW = W - PAD_L - PAD_R;
        const innerH = H - PAD_T - PAD_B;
        const baseY = PAD_T + innerH;
        const bw = innerW / points.length;
        const cxs = points.map((_, i) => PAD_L + i * bw + bw / 2);
        return { innerW, innerH, baseY, bw, barW: Math.max(1, Math.min(46, bw * 0.62)), cxs };
    }, [points, H]);

    // Каждая метрика — своя шкала 0…пик; отрицательные значения (убыток) прижимаются к нулю
    const series = useMemo(() => {
        const out: Record<string, { max: number; empty: boolean; ys: number[] }> = {};
        if (!geo) return out;
        for (const s of CHART_SERIES) {
            const vals = points.map(p => Math.max(0, Number(p.v[s.key]) || 0));
            const peak = Math.max(0, ...vals);
            const max = peak || 1;   // защита от деления на ноль
            out[s.key] = { max, empty: peak === 0, ys: vals.map(v => geo.baseY - (v / max) * geo.innerH) };
        }
        return out;
    }, [points, geo]);

    /** Оболочка одна и для пустого периода, и для отрисованного графика: так список
     *  метрик остаётся на месте, а замеры (wrapRef) не теряются между состояниями. */
    const shell: React.CSSProperties = {
        padding: '12px 14px', display: 'flex', gap: 12, alignItems: 'flex-start',
        height: '100%', minHeight: 0, boxSizing: 'border-box', overflow: 'hidden', fontFamily: FONT,
    };

    // Порядок легенды и подсказки — как в списке слева (значит, как в таблице)
    const ordered = sections.flatMap(sec => sec.items).filter(s => selected.includes(s.key));
    const barSeries = ordered.find(s => s.bar && !series[s.key]?.empty) ?? ordered.find(s => s.bar) ?? null;
    const lineSeries = ordered.filter(s => s.key !== barSeries?.key);
    const stickOp = lineSeries.length > 0 ? 0.4 : 0.6;
    const dotR = geo ? Math.min(5, Math.max(2.5, geo.barW / 6)) : 3;
    const hp = hover != null ? points[hover] : null;
    const labelEvery = Math.ceil(points.length / 14);
    const onCellMove = (i: number, e: React.MouseEvent) => {
        setHover(i);
        const r = wrapRef.current?.getBoundingClientRect();
        if (r) setMouseX(e.clientX - r.left);
    };
    /** Показ значения — форматом своей колонки, чтобы цифра совпадала с таблицей. */
    const val = (p: { v: Record<string, number | null> }, s: typeof CHART_SERIES[number]) => {
        const v = p.v[s.key];
        if (v == null) return '—';
        const col = COLUMN_BY_KEY[s.key];
        return col?.format ? col.format(v, {}) : col?.unit === '₽' ? fmtRub(v) : fmtInt(v);
    };

    return (
        <div ref={fitRef} style={shell}>
            {/* Слева — мультивыбор метрик, разложенный по группам колонок таблицы */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 184, flexShrink: 0, maxHeight: '100%', overflowY: 'auto' }}>
                {sections.map(sec => {
                    const on = sec.items.filter(i => selected.includes(i.key)).length;
                    const open = !shown.has(sec.label);
                    return (
                    <React.Fragment key={sec.label}>
                        <div onClick={() => toggleGroup(sec.label)} title={open ? 'Свернуть группу' : 'Развернуть группу'}
                            style={{
                                display: 'flex', alignItems: 'center', gap: 6, padding: '7px 10px 3px', cursor: 'pointer',
                                fontSize: 10, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase', color: '#94a3b8',
                            }}>
                            <span style={{ width: 8, fontSize: 9, color: '#94a3b8', flexShrink: 0 }}>{open ? '▾' : '▸'}</span>
                            <span style={{ width: 6, height: 6, borderRadius: 3, background: sec.color, flexShrink: 0 }} />
                            <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{sec.label}</span>
                            {on > 0 && (
                                <span style={{ fontSize: 10, fontWeight: 700, color: '#fff', background: sec.color, borderRadius: 8, padding: '0 6px', flexShrink: 0 }}>{on}</span>
                            )}
                        </div>
                        {open && sec.items.map(s => {
                            const active = selected.includes(s.key);
                            return (
                                <button key={s.key} onClick={() => onToggle(s.key)} title={s.col.title}
                                    style={{
                                        display: 'flex', alignItems: 'center', gap: 8, textAlign: 'left', border: 'none', cursor: 'pointer',
                                        borderRadius: 8, padding: '4px 10px', fontSize: FS.legend, fontWeight: active ? 600 : 500,
                                        background: active ? hexA(s.color, 0.12) : 'transparent', color: active ? s.color : '#6b7280',
                                    }}>
                                    <span style={{
                                        width: 14, height: 14, borderRadius: 4, flexShrink: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                        border: `1.5px solid ${active ? s.color : '#cbd5e1'}`, background: active ? s.color : '#fff',
                                        color: '#fff', fontSize: 10, lineHeight: 1,
                                    }}>{active ? '✓' : ''}</span>
                                    {s.label}
                                </button>
                            );
                        })}
                    </React.Fragment>
                    );
                })}
            </div>

            <div ref={wrapRef} style={{ flex: 1, minWidth: 0, position: 'relative' }}>
                {!geo ? (
                    <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: FS.empty }}>За выбранный период нет данных</div>
                ) : ordered.length === 0 ? (
                    <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: FS.empty }}>Выберите слева одну или несколько метрик.</div>
                ) : (
                    <>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', marginBottom: 6 }}>
                            {ordered.map(s => {
                                const isBar = s.key === barSeries?.key;
                                return (
                                    <Tooltip key={s.key} text={series[s.key].empty ? 'Нет данных за период' : `${isBar ? 'Точки' : 'Линия'} · пик: ${fmtCompact(series[s.key].max)}`}>
                                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: FS.legend, color: '#374151' }}>
                                            {isBar
                                                ? <span style={{ width: 9, height: 9, borderRadius: '50%', background: s.color, display: 'inline-block' }} />
                                                : <span style={{ width: 14, height: 2, background: s.color, borderRadius: 2, display: 'inline-block' }} />}
                                            {s.label}
                                            {series[s.key].empty && <span style={{ color: '#b0b0b0' }}>нет данных</span>}
                                        </span>
                                    </Tooltip>
                                );
                            })}
                            <span style={{ fontSize: FS.hint, color: '#b0b0b0', marginLeft: 'auto' }}>шкала — доля от пика каждой метрики · серые полосы — выходные</span>
                        </div>

                        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" style={{ width: '100%', height: svgPxHeight, display: 'block', fontFamily: FONT }}
                            onMouseLeave={() => setHover(null)}>
                            {/* Выходные — вертикальные полосы: объясняют недельную пилу */}
                            {points.map((p, i) => (
                                isWeekend(p.date) && (
                                    <rect key={`w${p.date}`} x={PAD_L + i * geo.bw} y={PAD_T} width={geo.bw} height={geo.innerH} fill="#f5f6f8" />
                                )
                            ))}
                            {[0, 1, 2, 3, 4].map(i => {
                                const y = PAD_T + (geo.innerH * i) / 4;
                                return (
                                    <g key={i}>
                                        <line x1={PAD_L} x2={W - PAD_R} y1={y} y2={y} stroke="#eef0f2" strokeWidth={1} />
                                        <text x={W - PAD_R} y={y - 2} fontSize={FS.tick} fill="#b8bcc2" textAnchor="end">{(4 - i) * 25}%</text>
                                    </g>
                                );
                            })}
                            <line x1={PAD_L} x2={W - PAD_R} y1={geo.baseY} y2={geo.baseY} stroke="#dde1e6" strokeWidth={1} />

                            {/* Объёмная метрика — «лоллипоп»: штырёк от базовой линии + точка на значении */}
                            {barSeries && !series[barSeries.key].empty && (
                                <g>
                                    {series[barSeries.key].ys.map((y, i) => {
                                        const cx = geo.cxs[i];
                                        const dim = hover == null || hover === i ? 1 : 0.4;
                                        return (
                                            <g key={i} opacity={dim}>
                                                <line x1={cx} x2={cx} y1={geo.baseY} y2={y} stroke={barSeries.color}
                                                    strokeWidth={2.5} strokeOpacity={stickOp} strokeLinecap="round" />
                                                <circle cx={cx} cy={y} r={dotR} fill={barSeries.color} stroke="#fff" strokeWidth={1} />
                                            </g>
                                        );
                                    })}
                                </g>
                            )}

                            {/* Остальные метрики — линиями поверх */}
                            {lineSeries.map(s => {
                                if (series[s.key].empty) return null;
                                const ys = series[s.key].ys;
                                const d = ys.map((y, i) => `${i === 0 ? 'M' : 'L'}${geo.cxs[i].toFixed(1)} ${y.toFixed(1)}`).join(' ');
                                return (
                                    <g key={s.key} pointerEvents="none">
                                        <path d={d} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
                                            opacity={hover == null ? 1 : 0.85} />
                                        <circle cx={geo.cxs[geo.cxs.length - 1]} cy={ys[ys.length - 1]} r={3} fill={s.color} stroke="#fff" strokeWidth={1.5} />
                                    </g>
                                );
                            })}

                            {hover != null && (
                                <g pointerEvents="none">
                                    <line x1={geo.cxs[hover]} x2={geo.cxs[hover]} y1={PAD_T} y2={geo.baseY} stroke="#9ca3af" strokeWidth={1} strokeDasharray="3 3" />
                                    {ordered.map(s => (
                                        series[s.key].empty ? null : (
                                            <circle key={s.key} cx={geo.cxs[hover]} cy={series[s.key].ys[hover]} r={3} fill={s.color} stroke="#fff" strokeWidth={1} />
                                        )
                                    ))}
                                </g>
                            )}

                            {/* Границы месяцев — пунктир */}
                            {points.map((p, i) => (
                                i > 0 && p.date.slice(5, 7) !== points[i - 1].date.slice(5, 7) && (
                                    <g key={`m${p.date}`} pointerEvents="none">
                                        <line x1={geo.cxs[i] - geo.bw / 2} x2={geo.cxs[i] - geo.bw / 2} y1={PAD_T} y2={geo.baseY}
                                            stroke="#cbd5e1" strokeWidth={1} strokeDasharray="4 4" />
                                        <text x={geo.cxs[i] - geo.bw / 2 + 3} y={PAD_T + 9} fontSize={FS.tick} fill="#9ca3af">{MONTHS[Number(p.date.slice(5, 7)) - 1]}</text>
                                    </g>
                                )
                            ))}

                            {/* Подписи дат — горизонтально */}
                            {points.map((p, i) => (
                                (i % labelEvery === 0 || i === points.length - 1 || hover === i) && (
                                    <text key={p.date} x={geo.cxs[i]} y={H - PAD_B + 14}
                                        fontSize={FS.tick} fill={hover === i ? '#111827' : '#9ca3af'} fontWeight={hover === i ? 600 : 400}
                                        textAnchor="middle">
                                        {fmtDay(p.date)}
                                    </text>
                                )
                            ))}

                            {/* Прозрачные hit-зоны — точное определение дня под курсором */}
                            {points.map((p, i) => (
                                <rect key={`h${p.date}`} x={PAD_L + i * geo.bw} y={PAD_T} width={geo.bw} height={geo.innerH}
                                    fill="transparent" onMouseMove={e => onCellMove(i, e)} />
                            ))}
                        </svg>
                    </>
                )}

                {hp && ordered.length > 0 && (
                    <div style={{
                        position: 'absolute', top: 4, left: `clamp(90px, ${mouseX}px, calc(100% - 130px))`, transform: 'translateX(-50%)',
                        background: 'rgba(17,24,39,0.92)', color: '#fff', borderRadius: 8, fontFamily: FONT,
                        padding: '8px 10px', fontSize: FS.tip, pointerEvents: 'none', whiteSpace: 'nowrap', zIndex: 5,
                    }}>
                        <div style={{ fontWeight: 600, marginBottom: 4 }}>{fmtDay(hp.date)}</div>
                        {ordered.map(s => (
                            <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                <span style={{ width: 8, height: 8, borderRadius: 4, background: s.color, display: 'inline-block' }} />
                                <span style={{ color: '#d1d5db' }}>{s.label}:</span>
                                <span style={{ fontWeight: 600 }}>{val(hp, s)}</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
