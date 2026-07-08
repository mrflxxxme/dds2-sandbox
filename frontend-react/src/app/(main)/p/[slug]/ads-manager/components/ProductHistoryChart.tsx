'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { FunnelDayRow } from '@/types/api';

/**
 * История артикула/кампании за период: 5 линий (цена с СПП, переходы, бюджет
 * рекламы, ДРР, продажи) плавными кривыми с заливкой и наведением — вертикальный
 * курсор и тултип со значениями дня. Каждая линия нормируется в свой диапазон.
 * Единый график: рендерится ОДИН сверху, по клику на строку меняется nm/кампания.
 */

export const HISTORY_LINES = [
    { key: 'price_spp', label: 'Цена с СПП ₽', color: '#8b5cf6' },
    { key: 'open_card', label: 'Переходы', color: '#f59e0b' },
    { key: 'adv_sum', label: 'Бюджет ₽', color: '#ef4444' },
    { key: 'drr', label: 'ДРР %', color: '#10b981' },
    { key: 'orders_sum_rub', label: 'Продажи ₽', color: '#3b82f6' },
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

// Широкий viewBox → при width:100% график остаётся невысоким (не «огромным»).
const W = 1200, H = 260, PAD_L = 12, PAD_R = 12, PAD_T = 14, PAD_B = 48;

/** Плавная кривая (Catmull-Rom → кубические Безье) по массиву точек. */
function smoothPath(pts: { x: number; y: number }[], tension = 0.16): string {
    if (pts.length === 0) return '';
    if (pts.length === 1) return `M${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`;
    let d = `M${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`;
    for (let i = 0; i < pts.length - 1; i++) {
        const p0 = pts[i - 1] || pts[i];
        const p1 = pts[i];
        const p2 = pts[i + 1];
        const p3 = pts[i + 2] || p2;
        const c1x = p1.x + (p2.x - p0.x) * tension;
        const c1y = p1.y + (p2.y - p0.y) * tension;
        const c2x = p2.x - (p3.x - p1.x) * tension;
        const c2y = p2.y - (p3.y - p1.y) * tension;
        d += ` C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2.x.toFixed(1)},${p2.y.toFixed(1)}`;
    }
    return d;
}

/** График артикула: детализация воронки по nm_id за выбранный период. */
export default function ProductHistoryChart({ nmId, dateFrom, dateTo, title }: { nmId: number; dateFrom: string; dateTo: string; title?: string }) {
    const [points, setPoints] = useState<HistoryPoint[] | null>(null);
    const [error, setError] = useState('');

    useEffect(() => {
        let cancelled = false;
        setPoints(null); setError('');
        const to = dateTo || new Date().toISOString().slice(0, 10);
        // По умолчанию — 30 дней назад, если период не задан
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

    return <HistoryChartBody points={points} error={error} title={title} note="Бюджет — расход именно этой кампании; переходы/продажи/цена — по её товарам (все источники трафика)." />;
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
        const line: Record<string, string> = {};
        const area: Record<string, string> = {};
        const ranges: Record<string, { min: number; max: number }> = {};
        for (const l of HISTORY_LINES) {
            const vals = points.map(p => p[l.key]);
            const dataMax = Math.max(...vals);
            const dataMin = Math.min(...vals);
            const max = Math.max(dataMax, 1e-9);
            const min = Math.min(dataMin, 0);
            const range = max - min || 1;
            ranges[l.key] = { min: dataMin, max: dataMax };
            const cs = points.map((p, i) => ({ x: xs[i], y: PAD_T + innerH - ((p[l.key] - min) / range) * innerH }));
            coords[l.key] = cs;
            line[l.key] = smoothPath(cs);
            area[l.key] = `${smoothPath(cs)} L${cs[cs.length - 1].x.toFixed(1)},${baseY.toFixed(1)} L${cs[0].x.toFixed(1)},${baseY.toFixed(1)} Z`;
        }
        return { xs, xStep, coords, line, area, ranges };
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
    // Каждый n-й день подписываем, чтобы подписи не сливались на длинном периоде
    const labelEvery = Math.ceil(points.length / 16);

    return (
        <div style={{ padding: '10px 14px 12px' }}>
            {title && <div style={{ fontSize: 14, fontWeight: 600, color: '#111827', marginBottom: 6 }}>{title}</div>}
            {/* Легенда: у каждой линии своя шкала → показываем её диапазон [мин…макс] */}
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 4, alignItems: 'baseline' }}>
                {HISTORY_LINES.map(l => {
                    const r = chart.ranges[l.key];
                    return (
                        <button key={l.key} onClick={() => toggleLine(l.key)}
                            style={{ display: 'flex', alignItems: 'baseline', gap: 5, border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: hidden.has(l.key) ? '#9ca3af' : '#374151', opacity: hidden.has(l.key) ? 0.5 : 1, padding: 0 }}>
                            <span style={{ width: 14, height: 3, background: l.color, borderRadius: 2, display: 'inline-block', transform: 'translateY(-2px)' }} />
                            {l.label}
                            <span style={{ fontSize: 11, color: '#9ca3af' }}>{fmtVal(r.min)}…{fmtVal(r.max)}</span>
                        </button>
                    );
                })}
                {note && <span style={{ fontSize: 11, color: '#9ca3af', marginLeft: 'auto' }}>{note}</span>}
            </div>
            <div style={{ fontSize: 10, color: '#b0b0b0', marginBottom: 4 }}>
                У каждой линии свой масштаб (диапазон рядом с названием) — сравнивайте форму и совпадение пиков, точные значения дня — при наведении.
            </div>

            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet"
                    style={{ width: '100%', maxHeight: 320, height: 'auto', display: 'block', background: '#fafafa', borderRadius: 8 }}
                    onMouseMove={handleMove} onMouseLeave={() => setHover(null)}>
                    <defs>
                        {HISTORY_LINES.map(l => (
                            <linearGradient key={l.key} id={`ads-grad-${l.key}`} x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stopColor={l.color} stopOpacity={0.16} />
                                <stop offset="100%" stopColor={l.color} stopOpacity={0} />
                            </linearGradient>
                        ))}
                    </defs>
                    {/* Сетка */}
                    {[0, 1, 2, 3, 4].map(i => (
                        <line key={i} x1={PAD_L} x2={W - PAD_R} y1={PAD_T + ((H - PAD_T - PAD_B) * i) / 4} y2={PAD_T + ((H - PAD_T - PAD_B) * i) / 4} stroke="#e5e7eb" strokeWidth={1} />
                    ))}
                    {/* Заливка под кривыми */}
                    {HISTORY_LINES.filter(l => !hidden.has(l.key)).map(l => (
                        <path key={l.key} d={chart.area[l.key]} fill={`url(#ads-grad-${l.key})`} stroke="none" />
                    ))}
                    {/* Плавные линии */}
                    {HISTORY_LINES.filter(l => !hidden.has(l.key)).map(l => (
                        <path key={l.key} d={chart.line[l.key]} fill="none" stroke={l.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                    ))}
                    {/* Курсор + точки на линиях */}
                    {hover != null && (
                        <g>
                            <line x1={chart.xs[hover]} x2={chart.xs[hover]} y1={PAD_T} y2={H - PAD_B} stroke="#9ca3af" strokeWidth={1} strokeDasharray="4 3" />
                            {HISTORY_LINES.filter(l => !hidden.has(l.key)).map(l => (
                                <circle key={l.key} cx={chart.coords[l.key][hover].x} cy={chart.coords[l.key][hover].y} r={3.5} fill="#fff" stroke={l.color} strokeWidth={2} />
                            ))}
                        </g>
                    )}
                    {/* Подпись даты (DD.MM, повёрнуто чтобы влезло) */}
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

                {/* Тултип */}
                {hp && (
                    <div style={{
                        position: 'absolute', top: 8,
                        left: `min(max(${tooltipLeftPct}%, 90px), calc(100% - 110px))`,
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
