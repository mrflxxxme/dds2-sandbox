'use client';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import type { CampaignMetricRow } from '@/types/api';
import { fmt, fmtPct } from './adsShared';
import { useFitViewport } from './useFitViewport';

/**
 * Гистограмма посуточных метрик кампании: наложение нескольких метрик столбцами.
 * Метрики несопоставимы по величине, поэтому каждая нормируется на свой пик
 * (шкала — доля от пика, 0…100%); абсолютный максимум показан в легенде.
 * Период задаёт страница кампании — здесь только отрисовка переданных строк.
 */

export type MetricKey = Extract<
    keyof CampaignMetricRow,
    'views' | 'clicks' | 'ctr' | 'spend' | 'cpc' | 'cpl' | 'cpo' | 'add_to_cart' | 'orders' | 'orders_sum' | 'drr'
>;

/** Метрики по умолчанию при первом заходе в кампанию. */
export const DEFAULT_CHART_METRICS: MetricKey[] = ['clicks', 'spend'];

// «Переходы» (open_card) намеренно нет: по смыслу это те же клики.
export const CHART_METRICS: { key: MetricKey; label: string; color: string; pct?: boolean }[] = [
    { key: 'views', label: 'Показы', color: '#8b5cf6' },
    { key: 'clicks', label: 'Клики', color: '#3b82f6' },
    { key: 'ctr', label: 'CTR %', color: '#6366f1', pct: true },
    { key: 'spend', label: 'Затраты ₽', color: '#ef4444' },
    { key: 'cpc', label: 'CPC ₽', color: '#f97316' },
    { key: 'cpl', label: 'CPL ₽', color: '#f59e0b' },
    { key: 'cpo', label: 'CPO ₽', color: '#a16207' },
    { key: 'add_to_cart', label: 'Корзины', color: '#14b8a6' },
    { key: 'orders', label: 'Заказы шт.', color: '#10b981' },
    { key: 'orders_sum', label: 'Заказали на сумму', color: '#0ea5e9' },
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

// PAD_B меньше, чем было: подписи дат больше не наклонены и занимают одну строку
const W = 1000, PAD_L = 14, PAD_R = 14, PAD_T = 14, PAD_B = 26;
const H_MIN = 240;

/** Выбор метрик — контролируемый: живёт на странице кампании, чтобы переживать
 *  перемонтирование графика при смене товара/периода (метрики перезагружаются). */
export default function CampaignMetricsChart({ rows, selected, onToggle }: {
    rows: CampaignMetricRow[];
    selected: Set<MetricKey>;
    onToggle: (k: MetricKey) => void;
}) {
    const [hover, setHover] = useState<number | null>(null);
    const [mouseX, setMouseX] = useState(0);
    const wrapRef = useRef<HTMLDivElement>(null);
    // График занимает остаток экрана: свернули хедер — стало детальнее
    const { ref: fitRef, maxHeight: fitHeight } = useFitViewport(320, 16);
    const [wrapW, setWrapW] = useState(1000);

    useEffect(() => {
        const el = wrapRef.current;
        if (!el) return;
        const ro = new ResizeObserver(() => setWrapW(el.clientWidth || 1000));
        ro.observe(el);
        setWrapW(el.clientWidth || 1000);
        return () => ro.disconnect();
    }, []);

    // Высота viewBox подобрана под реальную ширину, чтобы 1 юнит = 1 пиксель:
    // иначе meet вписал бы график с полями, а «none» растянул бы подписи.
    const svgPxHeight = Math.max(H_MIN, (fitHeight ?? 420) - 70);
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

    if (points.length === 0 || !geo) {
        return <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>За выбранный период нет данных по кампании</div>;
    }

    const ordered = CHART_METRICS.filter(m => selected.has(m.key));
    const hp = hover != null ? points[hover] : null;
    const labelEvery = Math.ceil(points.length / 14);
    const onCellMove = (i: number, e: React.MouseEvent) => {
        setHover(i);
        const r = wrapRef.current?.getBoundingClientRect();
        if (r) setMouseX(e.clientX - r.left);
    };
    const val = (p: CampaignMetricRow, m: typeof CHART_METRICS[number]) => (m.pct ? fmtPct(Number(p[m.key])) : fmt(Number(p[m.key])));

    return (
        <div ref={fitRef} style={{ padding: '12px 14px', display: 'flex', gap: 12, alignItems: 'flex-start' }}>
            {/* Слева — мультивыбор метрик */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 172, flexShrink: 0 }}>
                {CHART_METRICS.map(m => {
                    const active = selected.has(m.key);
                    return (
                        <button key={m.key} onClick={() => onToggle(m.key)}
                            style={{
                                display: 'flex', alignItems: 'center', gap: 8, textAlign: 'left', border: 'none', cursor: 'pointer',
                                borderRadius: 8, padding: '6px 10px', fontSize: 12.5, fontWeight: active ? 600 : 500,
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
                    <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>Выберите слева одну или несколько метрик.</div>
                ) : (
                    <>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', marginBottom: 6 }}>
                            {ordered.map(m => (
                                // Диапазон «0…N» не показываем: пик каждой метрики виден в подсказке
                                <span key={m.key} title={bars[m.key].empty ? 'Нет данных за период' : `Пик: ${fmtCompact(bars[m.key].max)}`}
                                    style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#374151' }}>
                                    <span style={{ width: 9, height: 9, borderRadius: 3, background: m.color, display: 'inline-block' }} />
                                    {m.label}
                                    {bars[m.key].empty && <span style={{ color: '#b0b0b0' }}>нет данных</span>}
                                </span>
                            ))}
                            <span style={{ fontSize: 11, color: '#b0b0b0', marginLeft: 'auto' }}>шкала — доля от пика каждой метрики</span>
                        </div>

                        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" style={{ width: '100%', height: svgPxHeight, display: 'block' }}
                            onMouseLeave={() => setHover(null)}>
                            {[0, 1, 2, 3, 4].map(i => {
                                const y = PAD_T + (geo.innerH * i) / 4;
                                return (
                                    <g key={i}>
                                        <line x1={PAD_L} x2={W - PAD_R} y1={y} y2={y} stroke="#eef0f2" strokeWidth={1} />
                                        <text x={W - PAD_R} y={y - 2} fontSize={9} fill="#b8bcc2" textAnchor="end">{(4 - i) * 25}%</text>
                                    </g>
                                );
                            })}
                            <line x1={PAD_L} x2={W - PAD_R} y1={geo.baseY} y2={geo.baseY} stroke="#dde1e6" strokeWidth={1} />

                            {ordered.map(m => (
                                <g key={m.key} style={{ mixBlendMode: 'multiply' }}>
                                    {bars[m.key].rects.map((b, i) => (
                                        <rect key={i} x={b.x} y={b.y} width={geo.barW} height={Math.max(0, b.h)} rx={2}
                                            fill={m.color} fillOpacity={hover == null || hover === i ? 0.55 : 0.3} />
                                    ))}
                                </g>
                            ))}

                            {hover != null && (
                                <g pointerEvents="none">
                                    <line x1={geo.cxs[hover]} x2={geo.cxs[hover]} y1={PAD_T} y2={geo.baseY} stroke="#9ca3af" strokeWidth={1} strokeDasharray="3 3" />
                                    {ordered.map(m => (
                                        <circle key={m.key} cx={geo.cxs[hover]} cy={bars[m.key].rects[hover].y} r={3} fill={m.color} stroke="#fff" strokeWidth={1} />
                                    ))}
                                </g>
                            )}

                            {/* Границы месяцев — пунктир */}
                            {points.map((p, i) => (
                                i > 0 && p.date.slice(5, 7) !== points[i - 1].date.slice(5, 7) && (
                                    <g key={`m${p.date}`} pointerEvents="none">
                                        <line x1={geo.cxs[i] - geo.bw / 2} x2={geo.cxs[i] - geo.bw / 2} y1={PAD_T} y2={geo.baseY}
                                            stroke="#cbd5e1" strokeWidth={1} strokeDasharray="4 4" />
                                        <text x={geo.cxs[i] - geo.bw / 2 + 3} y={PAD_T + 9} fontSize={9} fill="#9ca3af">{MONTHS[Number(p.date.slice(5, 7)) - 1]}</text>
                                    </g>
                                )
                            ))}

                            {/* Подписи дат — горизонтально (без поворота головы) */}
                            {points.map((p, i) => (
                                (i % labelEvery === 0 || i === points.length - 1 || hover === i) && (
                                    <text key={p.date} x={geo.cxs[i]} y={H - PAD_B + 14}
                                        fontSize={9} fill={hover === i ? '#111827' : '#9ca3af'} fontWeight={hover === i ? 600 : 400}
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
                        background: 'rgba(17,24,39,0.92)', color: '#fff', borderRadius: 8,
                        padding: '8px 10px', fontSize: 12, pointerEvents: 'none', whiteSpace: 'nowrap', zIndex: 5,
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
