'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { FunnelDayRow } from '@/types/api';

/**
 * История артикула/кампании за период — ОДНА гистограмма выбранной метрики по дням.
 * Слева — переключатель метрики (цена с СПП, переходы, бюджет, ДРР, продажи),
 * справа — столбцы этой метрики от нуля. Период задаётся здесь же (по умолчанию
 * 30 дней, минимум 30; пресеты 30/60/90/180 + произвольные даты).
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

// Компактный формат для оси/подписей столбцов
const fmtCompact = (n: number) => {
    const a = Math.abs(n);
    if (a >= 1_000_000) return (n / 1_000_000).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' млн';
    if (a >= 1_000) return (n / 1_000).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' тыс';
    return n.toLocaleString('ru-RU', { maximumFractionDigits: 1 });
};
// Цвет метрики с прозрачностью — для активной кнопки/заливки
const hexA = (hex: string, a: number) => {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
};

const isoD = (d: Date) => d.toISOString().slice(0, 10);
const DAY = 86400_000;
type Period = { from: string; to: string };


/** Период по умолчанию — последние 30 дней (или шире, если переданное окно больше). */
function seedPeriod(dateFrom?: string): Period {
    const to = isoD(new Date());
    const min30From = isoD(new Date(Date.now() - 29 * DAY));
    const from = dateFrom && dateFrom < min30From ? dateFrom : min30From;
    return { from, to };
}

// Один компактный график. Широкий viewBox → при width:100% высота небольшая.
const W = 1000, H = 250, PAD_L = 14, PAD_R = 14, PAD_T = 14, PAD_B = 40;

/** График артикула: детализация воронки по nm_id за выбранный период. */
export default function ProductHistoryChart({ nmId, dateFrom, dateTo, title }: { nmId: number; dateFrom: string; dateTo: string; title?: string }) {
    const [points, setPoints] = useState<HistoryPoint[] | null>(null);
    const [error, setError] = useState('');
    const [period, setPeriod] = useState<Period>(() => seedPeriod(dateFrom));

    useEffect(() => {
        let cancelled = false;
        setPoints(null); setError('');
        api.getFunnelData({ date_from: period.from, date_to: period.to, vendor_code: String(nmId) })
            .then(res => { if (!cancelled) setPoints(toHistoryPoints((res.data || []) as FunnelDayRow[])); })
            .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Ошибка загрузки'); });
        return () => { cancelled = true; };
    }, [nmId, period.from, period.to]);

    return <HistoryChartBody points={points} error={error} period={period} onPeriod={(from, to) => setPeriod({ from, to })} />;
}

/** График кампании: расход самой кампании + метрики её товаров (бэкенд-агрегат). */
export function CampaignHistoryChart({ campaignId, dateFrom, dateTo, title }: { campaignId: number; dateFrom: string; dateTo: string; title?: string }) {
    const [points, setPoints] = useState<HistoryPoint[] | null>(null);
    const [error, setError] = useState('');
    const [period, setPeriod] = useState<Period>(() => seedPeriod(dateFrom));

    useEffect(() => {
        let cancelled = false;
        setPoints(null); setError('');
        api.getCampaignHistory(campaignId, period.from, period.to)
            .then(rows => { if (!cancelled) setPoints(rows); })
            .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Ошибка загрузки'); });
        return () => { cancelled = true; };
    }, [campaignId, period.from, period.to]);

    return <HistoryChartBody points={points} error={error} period={period} onPeriod={(from, to) => setPeriod({ from, to })}
        note="Бюджет — расход этой кампании; переходы/продажи/цена — по её товарам." />;
}

const PRESETS = [30, 60, 90, 180];
const dateInput: React.CSSProperties = { fontSize: 12, padding: '4px 8px', border: '1px solid #e5e7eb', borderRadius: 8, color: '#374151', background: '#fff' };

function HistoryChartBody({ points, error, note, period, onPeriod }: {
    points: HistoryPoint[] | null; error: string; note?: string;
    period: Period; onPeriod: (from: string, to: string) => void;
}) {
    const [hover, setHover] = useState<number | null>(null);
    const [mouseX, setMouseX] = useState(0);
    const [selected, setSelected] = useState<Set<LineKey>>(() => new Set<LineKey>(['open_card']));
    const wrapRef = useRef<HTMLDivElement>(null);

    // Общая геометрия столбцов (одна для всех метрик — период один)
    const geo = useMemo(() => {
        if (!points || points.length === 0) return null;
        const innerW = W - PAD_L - PAD_R;
        const innerH = H - PAD_T - PAD_B;
        const baseY = PAD_T + innerH;
        const bw = innerW / points.length;
        const barW = Math.max(1, Math.min(46, bw * 0.62));
        const cxs = points.map((_, i) => PAD_L + i * bw + bw / 2);
        return { innerW, innerH, baseY, bw, barW, cxs };
    }, [points]);

    // Столбцы каждой метрики от нуля — своя шкала 0…max (метрики несопоставимы по величине)
    const metricBars = useMemo(() => {
        const out = {} as Record<LineKey, { max: number; rects: { x: number; y: number; h: number; cx: number; v: number }[] }>;
        if (!points || !geo) return out;
        for (const l of HISTORY_LINES) {
            const max = Math.max(0, ...points.map(p => Math.max(0, p[l.key]))) || 1;
            out[l.key] = {
                max,
                rects: points.map((p, i) => {
                    const v = Math.max(0, p[l.key]);
                    const h = (v / max) * geo.innerH;
                    return { x: geo.cxs[i] - geo.barW / 2, y: geo.baseY - h, h, cx: geo.cxs[i], v };
                }),
            };
        }
        return out;
    }, [points, geo]);

    // Пресеты периода (относительно сегодня); произвольные даты — через инпуты
    const spanDays = Math.round((new Date(period.to + 'T00:00:00').getTime() - new Date(period.from + 'T00:00:00').getTime()) / DAY) + 1;
    const isToday = period.to === isoD(new Date());
    const setPreset = (days: number) => {
        const to = isoD(new Date());
        onPeriod(isoD(new Date(Date.now() - (days - 1) * DAY)), to);
    };

    const periodControl = (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
            <div style={{ display: 'inline-flex', border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}>
                {PRESETS.map((d, i) => {
                    const active = isToday && spanDays === d;
                    return (
                        <button key={d} onClick={() => setPreset(d)}
                            style={{
                                padding: '5px 12px', fontSize: 12, fontWeight: 500, cursor: 'pointer', border: 'none',
                                borderLeft: i > 0 ? '1px solid #e5e7eb' : 'none',
                                background: active ? '#3b82f6' : '#fff', color: active ? '#fff' : '#374151',
                            }}>
                            {d} дн.
                        </button>
                    );
                })}
            </div>
            <input type="date" value={period.from} max={period.to} onChange={e => onPeriod(e.target.value, period.to)} style={dateInput} />
            <span style={{ color: '#9ca3af', fontSize: 12 }}>—</span>
            <input type="date" value={period.to} min={period.from} onChange={e => onPeriod(period.from, e.target.value)} style={dateInput} />
            <span style={{ fontSize: 12, color: '#9ca3af' }}>{spanDays} дн.</span>
            {note && <span style={{ fontSize: 11, color: '#b0b0b0', marginLeft: 'auto' }}>{note}</span>}
        </div>
    );

    if (error) return <div style={{ padding: '10px 14px 12px' }}>{periodControl}<div style={{ padding: 16, fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {error}</div></div>;
    if (!points) return <div style={{ padding: '10px 14px 12px' }}>{periodControl}<div style={{ padding: 24, fontSize: 13, color: 'var(--color-text-dim)' }}>Загрузка истории…</div></div>;
    if (points.length === 0 || !geo) return <div style={{ padding: '10px 14px 12px' }}>{periodControl}<div style={{ padding: 24, fontSize: 13, color: 'var(--color-text-dim)' }}>Нет истории за выбранный период</div></div>;

    const hp = hover != null ? points[hover] : null;
    const labelEvery = Math.ceil(points.length / 14);
    const ordered = HISTORY_LINES.filter(l => selected.has(l.key));
    const toggleMetric = (k: LineKey) => setSelected(prev => {
        const n = new Set(prev);
        n.has(k) ? n.delete(k) : n.add(k);
        return n;
    });

    // Наведение: прозрачные hit-зоны по столбцам ⇒ индекс определяется точно (без letterbox-сдвига)
    const onCellMove = (i: number, e: React.MouseEvent) => {
        setHover(i);
        const r = wrapRef.current?.getBoundingClientRect();
        if (r) setMouseX(e.clientX - r.left);
    };

    const tooltipEl = hp && ordered.length > 0 && (
        <div style={{
            position: 'absolute', top: 4,
            left: `clamp(90px, ${mouseX}px, calc(100% - 130px))`,
            transform: 'translateX(-50%)',
            background: 'rgba(17,24,39,0.92)', color: '#fff', borderRadius: 8,
            padding: '8px 10px', fontSize: 12, pointerEvents: 'none', whiteSpace: 'nowrap', zIndex: 5,
        }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{hp.date}</div>
            {ordered.map(l => (
                <div key={l.key} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 4, background: l.color, display: 'inline-block' }} />
                    <span style={{ color: '#d1d5db' }}>{l.label}:</span>
                    <span style={{ fontWeight: 600 }}>{fmtVal(hp[l.key])}</span>
                </div>
            ))}
        </div>
    );

    return (
        <div style={{ padding: '10px 14px 12px' }}>
            {periodControl}

            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                {/* Слева — мультивыбор метрик (несколько параллельно) */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 156, flexShrink: 0 }}>
                    {HISTORY_LINES.map(l => {
                        const active = selected.has(l.key);
                        return (
                            <button key={l.key} onClick={() => toggleMetric(l.key)}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: 8, textAlign: 'left', border: 'none', cursor: 'pointer',
                                    borderRadius: 8, padding: '7px 10px', fontSize: 12.5, fontWeight: active ? 600 : 500,
                                    background: active ? hexA(l.color, 0.12) : 'transparent',
                                    color: active ? l.color : '#6b7280',
                                }}>
                                <span style={{
                                    width: 14, height: 14, borderRadius: 4, flexShrink: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                    border: `1.5px solid ${active ? l.color : '#cbd5e1'}`, background: active ? l.color : '#fff',
                                    color: '#fff', fontSize: 10, lineHeight: 1,
                                }}>{active ? '✓' : ''}</span>
                                {l.label}
                            </button>
                        );
                    })}
                </div>

                {/* Справа — один график: наложение метрик столбцами (бленд multiply — пересечения смешиваются) */}
                <div ref={wrapRef} style={{ flex: 1, minWidth: 0, position: 'relative' }}>
                    {ordered.length === 0 ? (
                        <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>
                            Выберите слева одну или несколько метрик.
                        </div>
                    ) : (
                        <>
                            {/* Легенда: цвет + метрика + её абсолютный максимум (шкала нормирована) */}
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', marginBottom: 6 }}>
                                {ordered.map(l => (
                                    <span key={l.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#374151' }}>
                                        <span style={{ width: 9, height: 9, borderRadius: 3, background: l.color, display: 'inline-block' }} />
                                        {l.label}<span style={{ color: '#b0b0b0' }}>0…{fmtCompact(metricBars[l.key].max)}</span>
                                    </span>
                                ))}
                                <span style={{ fontSize: 11, color: '#b0b0b0', marginLeft: 'auto' }}>шкала — доля от пика каждой метрики</span>
                            </div>
                            <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet"
                                style={{ width: '100%', height: 320, display: 'block' }}
                                onMouseLeave={() => setHover(null)}>
                                {/* Сетка + шкала в % (нормировка) */}
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

                                {/* Наложение столбцов — multiply: пересечения дают смешанные (более тёмные) цвета */}
                                {ordered.map(l => (
                                    <g key={l.key} style={{ mixBlendMode: 'multiply' }}>
                                        {metricBars[l.key].rects.map((b, i) => (
                                            <rect key={i} x={b.x} y={b.y} width={geo.barW} height={Math.max(0, b.h)} rx={2}
                                                fill={l.color} fillOpacity={hover == null || hover === i ? 0.55 : 0.3} />
                                        ))}
                                    </g>
                                ))}

                                {/* Курсор + точки метрик на активном дне */}
                                {hover != null && (
                                    <g pointerEvents="none">
                                        <line x1={geo.cxs[hover]} x2={geo.cxs[hover]} y1={PAD_T} y2={geo.baseY} stroke="#9ca3af" strokeWidth={1} strokeDasharray="3 3" />
                                        {ordered.map(l => (
                                            <circle key={l.key} cx={geo.cxs[hover]} cy={metricBars[l.key].rects[hover].y} r={3} fill={l.color} stroke="#fff" strokeWidth={1} />
                                        ))}
                                    </g>
                                )}

                                {/* Ось дат */}
                                {points.map((p, i) => (
                                    (i % labelEvery === 0 || i === points.length - 1 || hover === i) && (
                                        <text key={p.date} x={geo.cxs[i]} y={H - PAD_B + 14}
                                            fontSize={10} fill={hover === i ? '#111827' : '#9ca3af'} fontWeight={hover === i ? 600 : 400}
                                            textAnchor="end" transform={`rotate(-55 ${geo.cxs[i]} ${H - PAD_B + 14})`}>
                                            {p.date.slice(8, 10)}.{p.date.slice(5, 7)}
                                        </text>
                                    )
                                ))}

                                {/* Прозрачные hit-зоны — точное определение дня под курсором */}
                                {points.map((_, i) => (
                                    <rect key={`h${i}`} x={PAD_L + i * geo.bw} y={PAD_T} width={geo.bw} height={geo.innerH}
                                        fill="transparent" onMouseMove={e => onCellMove(i, e)} />
                                ))}
                            </svg>
                        </>
                    )}
                    {tooltipEl}
                </div>
            </div>
        </div>
    );
}
