'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { FunnelDayRow } from '@/types/api';

/**
 * История артикула/кампании за период — ОДИН график, 5 линий (цена с СПП,
 * переходы, бюджет, ДРР, продажи). Каждая линия нормируется в свой [min…max]
 * (растянута на всю высоту — не липнет к верху), ломаные линии + точки, без
 * заливок (чтобы не мутить). Цена с СПП — тусклая (почти не меняется).
 */

export const HISTORY_LINES = [
    { key: 'price_spp', label: 'Цена с СПП ₽', color: '#8b5cf6', dim: true },
    { key: 'open_card', label: 'Переходы', color: '#f59e0b', dim: false },
    { key: 'adv_sum', label: 'Бюджет ₽', color: '#ef4444', dim: false },
    { key: 'drr', label: 'ДРР %', color: '#10b981', dim: false },
    { key: 'orders_sum_rub', label: 'Продажи ₽', color: '#3b82f6', dim: false },
] as const;

type LineKey = typeof HISTORY_LINES[number]['key'];
export interface HistoryPoint { date: string; price_spp: number; open_card: number; adv_sum: number; drr: number; orders_sum_rub: number }

/** Дневные строки воронки → точки графика (цена с СПП при известной ставке СПП). */
export function toHistoryPoints(rows: FunnelDayRow[]): HistoryPoint[] {
    return rows
        .map(r => {
            const price = Number(r.avg_price) || 0;
            const spp = Number(r.spp_rate) || 0;
            return {
                date: r.date,
                price_spp: spp > 0 ? Math.round(price * (1 - spp / 100) * 100) / 100 : price,
                open_card: Number(r.open_card) || 0,
                adv_sum: Number(r.adv_sum) || 0,
                drr: Number(r.drr) || 0,
                orders_sum_rub: Number(r.orders_sum_rub) || 0,
            };
        })
        .sort((a, b) => a.date.localeCompare(b.date));
}

const fmtVal = (n: number) => n.toLocaleString('ru-RU', { maximumFractionDigits: 2 });

// Один компактный график. Широкий viewBox → при width:100% высота небольшая.
const W = 1000, H = 250, PAD_L = 14, PAD_R = 14, PAD_T = 14, PAD_B = 40;

/** Ломаная линия (прямые сегменты) по массиву точек. */
function linePath(pts: { x: number; y: number }[]): string {
    if (pts.length === 0) return '';
    return pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
}

/** График артикула: детализация воронки по nm_id за выбранный период. */
export default function ProductHistoryChart({ nmId, dateFrom, dateTo, title }: { nmId: number; dateFrom: string; dateTo: string; title?: string }) {
    const [points, setPoints] = useState<HistoryPoint[] | null>(null);
    const [error, setError] = useState('');

    useEffect(() => {
        let cancelled = false;
        setPoints(null); setError('');
        const to = dateTo || new Date().toISOString().slice(0, 10);
        const from = dateFrom || new Date(new Date(to + 'T00:00:00').getTime() - 29 * 86400_000).toISOString().slice(0, 10);
        api.getFunnelData({ date_from: from, date_to: to, vendor_code: String(nmId) })
            .then(res => { if (!cancelled) setPoints(toHistoryPoints((res.data || []) as FunnelDayRow[])); })
            .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Ошибка загрузки'); });
        return () => { cancelled = true; };
    }, [nmId, dateFrom, dateTo]);

    return <HistoryChartBody points={points} error={error} title={title} />;
}

/** График кампании: расход самой кампании + метрики её товаров (бэкенд-агрегат). */
export function CampaignHistoryChart({ campaignId, dateFrom, dateTo, title }: { campaignId: number; dateFrom: string; dateTo: string; title?: string }) {
    const [points, setPoints] = useState<HistoryPoint[] | null>(null);
    const [error, setError] = useState('');

    useEffect(() => {
        let cancelled = false;
        setPoints(null); setError('');
        api.getCampaignHistory(campaignId, dateFrom || undefined, dateTo || undefined)
            .then(rows => { if (!cancelled) setPoints(rows); })
            .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Ошибка загрузки'); });
        return () => { cancelled = true; };
    }, [campaignId, dateFrom, dateTo]);

    return <HistoryChartBody points={points} error={error} title={title} note="Бюджет — расход этой кампании; переходы/продажи/цена — по её товарам." />;
}

function HistoryChartBody({ points, error, note, title }: { points: HistoryPoint[] | null; error: string; note?: string; title?: string }) {
    const [hover, setHover] = useState<number | null>(null);
    const [hidden, setHidden] = useState<Set<LineKey>>(new Set());
    const svgRef = useRef<SVGSVGElement>(null);

    const chart = useMemo(() => {
        if (!points || points.length === 0) return null;
        const innerW = W - PAD_L - PAD_R;
        const innerH = H - PAD_T - PAD_B;
        const baseY = PAD_T + innerH;
        const xStep = points.length > 1 ? innerW / (points.length - 1) : innerW;
        const xs = points.map((_, i) => PAD_L + i * xStep);
        const coords: Record<string, { x: number; y: number }[]> = {};
        const path: Record<string, string> = {};
        const ranges: Record<string, { min: number; max: number }> = {};
        for (const l of HISTORY_LINES) {
            const vals = points.map(p => p[l.key]);
            const dataMax = Math.max(...vals);
            const dataMin = Math.min(...vals);
            ranges[l.key] = { min: dataMin, max: dataMax };
            const flat = dataMax === dataMin;
            const range = flat ? 1 : dataMax - dataMin;
            // Автомасштаб в свой [min…max] → линия использует всю высоту (нет пустоты снизу)
            const cs = points.map((p, i) => ({
                x: xs[i],
                y: flat ? PAD_T + innerH / 2 : baseY - ((p[l.key] - dataMin) / range) * innerH,
            }));
            coords[l.key] = cs;
            path[l.key] = linePath(cs);
        }
        return { xs, xStep, coords, path, ranges };
    }, [points]);

    if (error) return <div style={{ padding: 16, fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {error}</div>;
    if (!points) return <div style={{ padding: 24, fontSize: 13, color: 'var(--color-text-dim)' }}>Загрузка истории…</div>;
    if (points.length === 0 || !chart) return <div style={{ padding: 24, fontSize: 13, color: 'var(--color-text-dim)' }}>Нет истории за выбранный период</div>;

    const handleMove = (e: React.MouseEvent<SVGSVGElement>) => {
        const rect = svgRef.current?.getBoundingClientRect();
        if (!rect) return;
        const x = ((e.clientX - rect.left) / rect.width) * W;
        const idx = Math.round((x - PAD_L) / chart.xStep);
        setHover(Math.min(points.length - 1, Math.max(0, idx)));
    };
    const toggleLine = (key: LineKey) => setHidden(prev => {
        const n = new Set(prev);
        n.has(key) ? n.delete(key) : n.add(key);
        return n;
    });

    const hp = hover != null ? points[hover] : null;
    const tooltipLeftPct = hover != null ? (chart.xs[hover] / W) * 100 : 0;
    const labelEvery = Math.ceil(points.length / 14);
    const showDots = points.length <= 45;  // на очень длинном периоде точки на каждом дне — каша
    const innerH = H - PAD_T - PAD_B;

    return (
        <div style={{ padding: '10px 14px 12px' }}>
            {title && <div style={{ fontSize: 14, fontWeight: 600, color: '#111827', marginBottom: 4 }}>{title}</div>}
            {/* Легенда: цвет + название + диапазон [мин…макс], клик — скрыть линию */}
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 6, alignItems: 'baseline' }}>
                {HISTORY_LINES.map(l => {
                    const r = chart.ranges[l.key];
                    const off = hidden.has(l.key);
                    return (
                        <button key={l.key} onClick={() => toggleLine(l.key)}
                            style={{ display: 'flex', alignItems: 'baseline', gap: 5, border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: off ? '#c4c4c4' : (l.dim ? '#9ca3af' : '#374151'), opacity: off ? 0.5 : 1, padding: 0 }}>
                            <span style={{ width: 12, height: 3, background: l.color, borderRadius: 2, display: 'inline-block', transform: 'translateY(-2px)', opacity: l.dim ? 0.5 : 1 }} />
                            {l.label}
                            <span style={{ fontSize: 11, color: '#b0b0b0' }}>{fmtVal(r.min)}…{fmtVal(r.max)}</span>
                        </button>
                    );
                })}
                {note && <span style={{ fontSize: 11, color: '#b0b0b0', marginLeft: 'auto' }}>{note}</span>}
            </div>

            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet"
                    style={{ width: '100%', maxHeight: 300, height: 'auto', display: 'block' }}
                    onMouseMove={handleMove} onMouseLeave={() => setHover(null)}>
                    {/* Лёгкая сетка (визуальный ориентир, без чисел — шкалы разные) */}
                    {[0, 1, 2, 3, 4].map(i => (
                        <line key={i} x1={PAD_L} x2={W - PAD_R} y1={PAD_T + (innerH * i) / 4} y2={PAD_T + (innerH * i) / 4} stroke="#eef0f2" strokeWidth={1} />
                    ))}
                    {/* Вертикальные направляющие по подписанным датам */}
                    {points.map((p, i) => (
                        (i % labelEvery === 0 || i === points.length - 1) && (
                            <line key={`v${i}`} x1={chart.xs[i]} x2={chart.xs[i]} y1={PAD_T} y2={PAD_T + innerH} stroke="#f4f5f7" strokeWidth={1} />
                        )
                    ))}
                    {/* Нижняя ось */}
                    <line x1={PAD_L} x2={W - PAD_R} y1={PAD_T + innerH} y2={PAD_T + innerH} stroke="#dde1e6" strokeWidth={1} />

                    {/* Линии (тусклые — тоньше и полупрозрачные, поверх — яркие) */}
                    {HISTORY_LINES.filter(l => !hidden.has(l.key)).sort((a, b) => Number(b.dim) - Number(a.dim)).map(l => (
                        <path key={l.key} d={chart.path[l.key]} fill="none" stroke={l.color}
                            strokeWidth={l.dim ? 1.3 : 2} strokeOpacity={l.dim ? 0.4 : 1}
                            strokeDasharray={l.dim ? '4 3' : undefined} strokeLinejoin="round" strokeLinecap="round" />
                    ))}

                    {/* Точки на значениях (кроме тусклой; на длинном периоде — только при наведении) */}
                    {showDots && HISTORY_LINES.filter(l => !hidden.has(l.key) && !l.dim).map(l => (
                        chart.coords[l.key].map((c, i) => (
                            <circle key={`${l.key}-${i}`} cx={c.x} cy={c.y} r={2.2} fill={l.color} />
                        ))
                    ))}

                    {/* Курсор + точки дня */}
                    {hover != null && (
                        <g>
                            <line x1={chart.xs[hover]} x2={chart.xs[hover]} y1={PAD_T} y2={PAD_T + innerH} stroke="#9ca3af" strokeWidth={1} strokeDasharray="4 3" />
                            {HISTORY_LINES.filter(l => !hidden.has(l.key)).map(l => (
                                <circle key={l.key} cx={chart.coords[l.key][hover].x} cy={chart.coords[l.key][hover].y} r={3.2} fill="#fff" stroke={l.color} strokeWidth={2} opacity={l.dim ? 0.6 : 1} />
                            ))}
                        </g>
                    )}

                    {/* Ось дат внизу (DD.MM, повёрнуто) */}
                    {points.map((p, i) => (
                        (i % labelEvery === 0 || i === points.length - 1 || hover === i) && (
                            <text key={p.date} x={chart.xs[i]} y={H - PAD_B + 14}
                                fontSize={10} fill={hover === i ? '#111827' : '#9ca3af'} fontWeight={hover === i ? 600 : 400}
                                textAnchor="end" transform={`rotate(-55 ${chart.xs[i]} ${H - PAD_B + 14})`}>
                                {p.date.slice(8, 10)}.{p.date.slice(5, 7)}
                            </text>
                        )
                    ))}
                </svg>

                {/* Тултип — значения дня по всем метрикам */}
                {hp && (
                    <div style={{
                        position: 'absolute', top: 4,
                        left: `min(max(${tooltipLeftPct}%, 90px), calc(100% - 120px))`,
                        transform: 'translateX(-50%)',
                        background: 'rgba(17,24,39,0.92)', color: '#fff', borderRadius: 8,
                        padding: '8px 10px', fontSize: 12, pointerEvents: 'none', whiteSpace: 'nowrap', zIndex: 5,
                    }}>
                        <div style={{ fontWeight: 600, marginBottom: 4 }}>{hp.date}</div>
                        {HISTORY_LINES.map(l => (
                            <div key={l.key} style={{ display: 'flex', alignItems: 'center', gap: 6, opacity: hidden.has(l.key) ? 0.45 : 1 }}>
                                <span style={{ width: 8, height: 8, borderRadius: 4, background: l.color, display: 'inline-block' }} />
                                <span style={{ color: '#d1d5db' }}>{l.label}:</span>
                                <span style={{ fontWeight: 600 }}>{fmtVal(hp[l.key])}</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
