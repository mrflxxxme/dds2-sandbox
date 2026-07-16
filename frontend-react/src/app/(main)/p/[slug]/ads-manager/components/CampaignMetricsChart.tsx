'use client';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import type { CampaignMetricRow } from '@/types/api';
import { fmt, fmtPct } from './adsShared';
import Tooltip from './Tooltip';

/**
 * Комбо-график посуточных метрик кампании: одна «объёмная» метрика — фоновыми
 * столбцами, остальные выбранные — линиями с точками поверх (не перекрывают друг
 * друга, в отличие от прежнего наложения столбцов).
 * Метрики несопоставимы по величине, поэтому каждая нормируется на свой пик
 * (шкала — доля от пика, 0…100%); абсолютный максимум показан в легенде.
 * Период задаёт страница кампании — здесь только отрисовка переданных строк.
 */

export type MetricKey = Extract<
    keyof CampaignMetricRow,
    'views' | 'clicks' | 'ctr' | 'spend' | 'cpc' | 'cpl' | 'cpo' | 'add_to_cart' | 'orders' | 'orders_sum' | 'customer_price' | 'spp' | 'drr'
>;

/** Метрики по умолчанию при первом заходе в кампанию. */
export const DEFAULT_CHART_METRICS: MetricKey[] = ['clicks', 'spend', 'spp'];

// «Переходы» (open_card) намеренно нет: по смыслу это те же клики.
// bar: метрика «объёмная» (может быть фоновыми столбцами). Проценты и стоимости — всегда линией.
export const CHART_METRICS: { key: MetricKey; label: string; color: string; pct?: boolean; bar?: boolean }[] = [
    { key: 'views', label: 'Показы', color: '#8b5cf6', bar: true },
    { key: 'clicks', label: 'Клики', color: '#3b82f6', bar: true },
    { key: 'ctr', label: 'CTR %', color: '#6366f1', pct: true },
    { key: 'spend', label: 'Затраты ₽', color: '#ef4444', bar: true },
    { key: 'cpc', label: 'CPC ₽', color: '#f97316' },
    { key: 'cpl', label: 'CPL ₽', color: '#f59e0b' },
    { key: 'cpo', label: 'CPO ₽', color: '#a16207' },
    { key: 'add_to_cart', label: 'Корзины', color: '#14b8a6', bar: true },
    { key: 'orders', label: 'Заказы шт.', color: '#10b981', bar: true },
    { key: 'orders_sum', label: 'Заказали на сумму', color: '#0ea5e9', bar: true },
    { key: 'customer_price', label: 'Цена Клиенту ₽', color: '#0d9488' },  // цена с СПП — линией
    { key: 'spp', label: 'СПП %', color: '#7c3aed', pct: true },           // средний СПП за день
    { key: 'drr', label: 'ДРР %', color: '#be185d', pct: true },
];

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

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

// Единая типографика графика: Roboto (наследуется от body, в SVG задаём явно) + аккуратная шкала кеглей.
const FONT = 'var(--font-sans)';
const FS = { tick: 10, legend: 12, hint: 11, tip: 12, empty: 13 };  // ось/подписи · легенда · подсказка · тултип · пусто

// PAD_B меньше, чем было: подписи дат больше не наклонены и занимают одну строку
const W = 1000, PAD_L = 14, PAD_R = 14, PAD_T = 14, PAD_B = 26;
const H_MIN = 160;

/** Выбор метрик — контролируемый: живёт на странице кампании, чтобы переживать
 *  перемонтирование графика при смене товара/периода (метрики перезагружаются). */
export default function CampaignMetricsChart({ rows, selected, onToggle, launchDate }: {
    rows: CampaignMetricRow[];
    selected: Set<MetricKey>;
    onToggle: (k: MetricKey) => void;
    /** Дата запуска кампании (ISO, WB createTime) — красная вертикальная линия на графике. */
    launchDate?: string | null;
}) {
    const [hover, setHover] = useState<number | null>(null);
    const [mouseX, setMouseX] = useState(0);
    const wrapRef = useRef<HTMLDivElement>(null);
    // График заполняет ПАНЕЛЬ (а не окно): меряем свою высоту, чтобы влезать без прокрутки/клипа
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
    }, []);

    // Высота viewBox подобрана под реальную ширину, чтобы 1 юнит = 1 пиксель:
    // иначе meet вписал бы график с полями, а «none» растянул бы подписи.
    // Потолок по соотношению сторон: в свёрнутом хедере панель становится очень высокой,
    // и без ограничения линии вытягиваются в нечитаемые вертикальные пики. Держим высоту
    // не выше ~46% ширины графика — комфортное для линий соотношение (~2.2:1).
    const MAX_AR = 0.46;
    const svgPxHeight = Math.round(Math.max(H_MIN, Math.min(availH - 84, wrapW * MAX_AR)));
    const H = Math.round((W * svgPxHeight) / Math.max(1, wrapW));

    // Дни по возрастанию — бэкенд может отдавать в любом порядке
    const points = useMemo(() => [...rows].sort((a, b) => a.date.localeCompare(b.date)), [rows]);

    // Общая геометрия столбцов (период один для всех метрик)
    const geo = useMemo(() => {
        if (points.length === 0) return null;
        const innerW = W - PAD_L - PAD_R;
        const innerH = H - PAD_T - PAD_B;
        const baseY = PAD_T + innerH;
        const bw = innerW / points.length;
        const barW = Math.max(1, Math.min(46, bw * 0.62));
        const cxs = points.map((_, i) => PAD_L + i * bw + bw / 2);
        return { innerW, innerH, baseY, bw, barW, cxs };
    }, [points, H]);

    // Столбцы каждой метрики от нуля, своя шкала 0…max
    const bars = useMemo(() => {
        const out = {} as Record<MetricKey, { max: number; empty: boolean; rects: { x: number; y: number; h: number }[] }>;
        if (!geo) return out;
        for (const m of CHART_METRICS) {
            const peak = Math.max(0, ...points.map(p => Math.max(0, Number(p[m.key]) || 0)));
            const max = peak || 1;  // защита от деления на ноль
            out[m.key] = {
                max,
                empty: peak === 0,
                rects: points.map((p, i) => {
                    const v = Math.max(0, Number(p[m.key]) || 0);
                    const h = (v / max) * geo.innerH;
                    return { x: geo.cxs[i] - geo.barW / 2, y: geo.baseY - h, h };
                }),
            };
        }
        return out;
    }, [points, geo]);

    // x-координата линии запуска кампании. Ось категориальная (бар на строку, не на
    // календарный день), поэтому «прилипаем» к первому дню >= даты запуска — устойчиво к
    // пропущенным дням. Не рисуем, если запуск раньше окна (весь график уже после запуска)
    // или позже последнего дня (данных после запуска нет).
    const launchX = useMemo(() => {
        if (!launchDate || !geo || points.length === 0) return null;
        const day = launchDate.slice(0, 10);  // createTime — datetime, сравниваем по дате
        if (day < points[0].date) return null;                 // запуск раньше видимого окна
        const idx = points.findIndex(p => p.date >= day);
        if (idx < 0) return null;                              // запуск позже последнего дня
        return geo.cxs[idx] - geo.bw / 2;                     // левый край бара дня запуска (как у границ месяцев)
    }, [launchDate, points, geo]);

    if (points.length === 0 || !geo) {
        return <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>За выбранный период нет данных по кампании</div>;
    }

    const ordered = CHART_METRICS.filter(m => selected.has(m.key));
    // Комбо: одна объёмная метрика — фоновыми столбцами (предпочтительно с данными),
    // остальные — линиями. Если объёмных нет — рисуем только линии.
    const barMetric = ordered.find(m => m.bar && !bars[m.key].empty) ?? ordered.find(m => m.bar) ?? null;
    const lineMetrics = ordered.filter(m => m.key !== barMetric?.key);
    // Объёмная метрика рисуется «лоллипопом»: тонкий штырёк от базовой линии + точка-набалдашник
    // на значении. Без линий — штырёк плотнее, чтобы не выглядел бледным.
    const stickOp = lineMetrics.length > 0 ? 0.4 : 0.6;
    const dotR = Math.min(5, Math.max(2.5, geo.barW / 6));
    const hp = hover != null ? points[hover] : null;
    const labelEvery = Math.ceil(points.length / 14);
    const onCellMove = (i: number, e: React.MouseEvent) => {
        setHover(i);
        const r = wrapRef.current?.getBoundingClientRect();
        if (r) setMouseX(e.clientX - r.left);
    };
    const val = (p: CampaignMetricRow, m: typeof CHART_METRICS[number]) => (m.pct ? fmtPct(Number(p[m.key])) : fmt(Number(p[m.key])));

    return (
        <div ref={fitRef} style={{ padding: '12px 14px', display: 'flex', gap: 12, alignItems: 'flex-start', height: '100%', minHeight: 0, boxSizing: 'border-box', overflow: 'hidden', fontFamily: FONT }}>
            {/* Слева — мультивыбор метрик */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 172, flexShrink: 0, maxHeight: '100%', overflowY: 'auto' }}>
                {CHART_METRICS.map(m => {
                    const active = selected.has(m.key);
                    return (
                        <button key={m.key} onClick={() => onToggle(m.key)}
                            style={{
                                display: 'flex', alignItems: 'center', gap: 8, textAlign: 'left', border: 'none', cursor: 'pointer',
                                borderRadius: 8, padding: '4px 10px', fontSize: FS.legend, fontWeight: active ? 600 : 500,
                                background: active ? hexA(m.color, 0.12) : 'transparent', color: active ? m.color : '#6b7280',
                            }}>
                            <span style={{
                                width: 14, height: 14, borderRadius: 4, flexShrink: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                border: `1.5px solid ${active ? m.color : '#cbd5e1'}`, background: active ? m.color : '#fff',
                                color: '#fff', fontSize: 10, lineHeight: 1,
                            }}>{active ? '✓' : ''}</span>
                            {m.label}
                        </button>
                    );
                })}
            </div>

            {/* Справа — наложение столбцов (multiply: пересечения смешиваются) */}
            <div ref={wrapRef} style={{ flex: 1, minWidth: 0, position: 'relative' }}>
                {ordered.length === 0 ? (
                    <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: FS.empty }}>Выберите слева одну или несколько метрик.</div>
                ) : (
                    <>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', marginBottom: 6 }}>
                            {ordered.map(m => {
                                const isBar = m.key === barMetric?.key;
                                return (
                                // Диапазон «0…N» не показываем: пик каждой метрики виден в подсказке.
                                // Маркер повторяет отрисовку: столбец — квадрат-плашка, линия — черта с точкой.
                                <Tooltip key={m.key} text={bars[m.key].empty ? 'Нет данных за период' : `${isBar ? 'Точки' : 'Линия'} · пик: ${fmtCompact(bars[m.key].max)}`}>
                                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: FS.legend, color: '#374151' }}>
                                        {isBar
                                            ? <span style={{ width: 9, height: 9, borderRadius: '50%', background: m.color, display: 'inline-block' }} />
                                            : <span style={{ width: 14, height: 2, background: m.color, borderRadius: 2, display: 'inline-block' }} />}
                                        {m.label}
                                        {bars[m.key].empty && <span style={{ color: '#b0b0b0' }}>нет данных</span>}
                                    </span>
                                </Tooltip>
                                );
                            })}
                            {launchX != null && (
                                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: FS.hint, color: '#ef4444', marginLeft: 'auto' }}>
                                    <span style={{ width: 12, height: 2, background: '#ef4444', display: 'inline-block' }} />запуск кампании
                                </span>
                            )}
                            <span style={{ fontSize: FS.hint, color: '#b0b0b0', marginLeft: launchX != null ? 12 : 'auto' }}>шкала — доля от пика каждой метрики</span>
                        </div>

                        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" style={{ width: '100%', height: svgPxHeight, display: 'block', fontFamily: FONT }}
                            onMouseLeave={() => setHover(null)}>
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

                            {/* Объёмная метрика — «лоллипоп»: тонкий штырёк + точка-набалдашник (вместо массивных столбцов) */}
                            {barMetric && !bars[barMetric.key].empty && (
                                <g>
                                    {bars[barMetric.key].rects.map((b, i) => {
                                        const cx = geo.cxs[i];
                                        const dim = hover == null || hover === i ? 1 : 0.4;
                                        return (
                                            <g key={i} opacity={dim}>
                                                <line x1={cx} x2={cx} y1={geo.baseY} y2={b.y} stroke={barMetric.color}
                                                    strokeWidth={2.5} strokeOpacity={stickOp} strokeLinecap="round" />
                                                <circle cx={cx} cy={b.y} r={dotR} fill={barMetric.color} stroke="#fff" strokeWidth={1} />
                                            </g>
                                        );
                                    })}
                                </g>
                            )}

                            {/* Остальные метрики — линиями поверх (не перекрывают друг друга) */}
                            {lineMetrics.map(m => {
                                if (bars[m.key].empty) return null;
                                const d = bars[m.key].rects.map((b, i) => `${i === 0 ? 'M' : 'L'}${geo.cxs[i].toFixed(1)} ${b.y.toFixed(1)}`).join(' ');
                                const last = bars[m.key].rects[bars[m.key].rects.length - 1];
                                return (
                                    <g key={m.key} pointerEvents="none">
                                        <path d={d} fill="none" stroke={m.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
                                            opacity={hover == null ? 1 : 0.85} />
                                        {/* Точка на конце линии — прямая привязка к серии */}
                                        <circle cx={geo.cxs[geo.cxs.length - 1]} cy={last.y} r={3} fill={m.color} stroke="#fff" strokeWidth={1.5} />
                                    </g>
                                );
                            })}

                            {hover != null && (
                                <g pointerEvents="none">
                                    <line x1={geo.cxs[hover]} x2={geo.cxs[hover]} y1={PAD_T} y2={geo.baseY} stroke="#9ca3af" strokeWidth={1} strokeDasharray="3 3" />
                                    {ordered.map(m => (
                                        bars[m.key].empty ? null : (
                                            <circle key={m.key} cx={geo.cxs[hover]} cy={bars[m.key].rects[hover].y} r={3} fill={m.color} stroke="#fff" strokeWidth={1} />
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

                            {/* Запуск кампании — сплошная красная линия */}
                            {launchX != null && (
                                <g pointerEvents="none">
                                    <line x1={launchX} x2={launchX} y1={PAD_T} y2={geo.baseY} stroke="#ef4444" strokeWidth={1.5} />
                                    <text x={launchX + 3} y={PAD_T + 20} fontSize={FS.tick} fill="#ef4444" fontWeight={600}>запуск</text>
                                </g>
                            )}

                            {/* Подписи дат — горизонтально (без поворота головы) */}
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
                        {ordered.map(m => (
                            <div key={m.key} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                <span style={{ width: 8, height: 8, borderRadius: 4, background: m.color, display: 'inline-block' }} />
                                <span style={{ color: '#d1d5db' }}>{m.label}:</span>
                                <span style={{ fontWeight: 600 }}>{val(hp, m)}</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
