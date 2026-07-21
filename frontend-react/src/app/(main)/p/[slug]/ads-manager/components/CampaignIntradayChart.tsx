'use client';
import React, { useState } from 'react';
import type { CampaignIntradayMetrics, CampaignIntradayPoint } from '@/types/api';
import { fmt, thStyle, tdStyle, tdLeft } from './adsShared';

/** Внутридневной график «место принятия решения» (стиль mkeeper): по интервалам снимков
 *  — показы (фиолетовые бары) + клики (зелёные, своя шкала), расход, CTR с порогом
 *  «мин показов» и тренд. Данные копятся вперёд из официальной статистики WB (нативно
 *  почасовку WB не отдаёт), поэтому первые дни/часы график наполняется постепенно. */
export default function CampaignIntradayChart({ resp, onSetInterval }: {
    resp: CampaignIntradayMetrics;
    onSetInterval?: (minutes: number) => void | Promise<void>;
}) {
    const [minViews, setMinViews] = useState(50);
    const [hover, setHover] = useState<number | null>(null);
    const points = resp.points;

    // CTR интервала считаем только при показах >= порога — иначе шум (1 клик / 3 показа = 33%).
    const ctrOf = (p: CampaignIntradayPoint): number | null =>
        p.views >= minViews && p.views > 0 ? (p.clicks / p.views) * 100 : null;

    // Тренд CTR: средний CTR первой половины «зачётных» интервалов vs второй.
    const eligible = points.map(ctrOf).filter((c): c is number => c != null);
    let trend: 'up' | 'down' | 'flat' = 'flat';
    if (eligible.length >= 2) {
        const mid = Math.floor(eligible.length / 2);
        const a = avg(eligible.slice(0, mid));
        const b = avg(eligible.slice(mid));
        trend = b > a * 1.05 ? 'up' : b < a * 0.95 ? 'down' : 'flat';
    }

    const maxViews = Math.max(1, ...points.map(p => p.views));
    const maxClicks = Math.max(1, ...points.map(p => p.clicks));
    // Подписи времени прореживаем, чтобы не слипались (цель ~8 меток).
    const labelStep = Math.max(1, Math.ceil(points.length / 8));

    if (resp.snapshots === 0) {
        return (
            <div style={{ padding: 20 }}>
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
                    <IntervalSelect value={resp.interval_min} onChange={onSetInterval} />
                </div>
                <div style={{ textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>
                    За этот день снимков ещё нет. Внутридневные показы/клики копятся вперёд из официальной
                    статистики WB — готовую почасовку WB не отдаёт. Интервалы появляются по мере
                    обновления данных WB (примерно раз в 30–60 мин), первые — через пару снимков.
                </div>
            </div>
        );
    }

    // Диапазон интервала «начало–конец»: начало = время прошлого снимка, у первого — от полуночи.
    const rangeOf = (i: number) => `${i > 0 ? points[i - 1].time : '00:00'}–${points[i].time}`;
    // Наведённый бар (иначе — последний): его цифры крупно в заголовке, как «место решения».
    const focus = hover != null && points[hover] ? hover : points.length - 1;
    const fp = points[focus];
    const fCtr = fp ? ctrOf(fp) : null;
    // Значения над барами: при плотной сетке подпись слиплась бы над каждым столбцом, поэтому
    // показываем прорежённо (каждый labelStep-й) — плюс всегда над столбцом под курсором.
    const showValueAt = (i: number) => i === focus || points.length <= 16 || i % labelStep === 0;

    return (
        <div style={{ padding: 16 }}>
            {/* Шапка: интервал в фокусе (наведённый/последний) + диапазон времени + тренд + порог */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
                <div style={{ fontSize: 14, color: '#374151', display: 'flex', alignItems: 'center', gap: 8 }}>
                    {fp ? (
                        <span><b>{rangeOf(focus)}</b> · Показы: <b>{fmt(fp.views)}</b> · Клики: <b>{fmt(fp.clicks)}</b> · CTR: <b>{fCtr != null ? fCtr.toFixed(1) : '—'}</b> · Расход: <b>{fmt(fp.spend)} ₽</b></span>
                    ) : <span>Нет интервалов</span>}
                    <TrendArrow trend={trend} />
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                    <IntervalSelect value={resp.interval_min} onChange={onSetInterval} />
                    <label style={{ fontSize: 12, color: '#6b7280', display: 'flex', alignItems: 'center', gap: 6 }}>
                        Мин показов для CTR
                        <input
                            type="number" min={0} value={minViews}
                            onChange={e => setMinViews(Math.max(0, Number(e.target.value) || 0))}
                            style={{ width: 64, padding: '3px 6px', border: '1px solid #e5e7eb', borderRadius: 8, fontSize: 12, textAlign: 'right' }}
                        />
                    </label>
                </div>
            </div>

            {/* График: по интервалу два бара рядом — показы (фиолетовый) + клики (зелёный, своя шкала).
                Над кончиком каждого бара — его значение за интервал. Наведение подсвечивает столбец. */}
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 220, borderBottom: '1px solid #e5e7eb', paddingTop: 18 }}>
                {points.map((p, i) => (
                    <div key={i}
                        onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                        style={{ position: 'relative', flex: 1, height: '100%', cursor: 'default',
                            background: i === focus ? 'rgba(124,109,242,0.08)' : undefined, borderRadius: '4px 4px 0 0' }}>
                        {/* бар показов — над ним число показов (главная метрика) */}
                        <div style={{ position: 'absolute', bottom: 0, left: '6%', width: '42%', height: `${(p.views / maxViews) * 100}%`, minHeight: p.views > 0 ? 2 : 0, background: '#7c6df2', borderRadius: '2px 2px 0 0' }}>
                            {p.views > 0 && showValueAt(i) && <BarValue v={p.views} color="#5b4fc4" big={i === focus} />}
                        </div>
                        {/* бар кликов — число только под курсором, чтобы над узкими барами не было каши */}
                        <div style={{ position: 'absolute', bottom: 0, right: '6%', width: '42%', height: `${(p.clicks / maxClicks) * 100}%`, minHeight: p.clicks > 0 ? 2 : 0, background: '#86c99a', borderRadius: '2px 2px 0 0' }}>
                            {p.clicks > 0 && i === focus && <BarValue v={p.clicks} color="#3f8a58" big />}
                        </div>
                    </div>
                ))}
            </div>
            {/* Подписи времени (прорежены) */}
            <div style={{ display: 'flex', gap: 2, marginBottom: 10 }}>
                {points.map((p, i) => (
                    <div key={i} style={{ flex: 1, textAlign: 'center', fontSize: 9, color: i === focus ? '#6d5fd0' : '#9ca3af', fontWeight: i === focus ? 700 : 400 }}>{i % labelStep === 0 || i === focus ? p.time : ''}</div>
                ))}
            </div>

            {/* Легенда */}
            <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', fontSize: 12, color: '#6b7280', marginBottom: 14 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><i style={{ width: 10, height: 10, background: '#7c6df2', borderRadius: 2, display: 'inline-block' }} /> Показы</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><i style={{ width: 10, height: 10, background: '#86c99a', borderRadius: 2, display: 'inline-block' }} /> Клики</span>
                <span style={{ marginLeft: 'auto', color: '#9ca3af' }}>наведите на столбец — показы и клики за интервал</span>
            </div>

            {/* Таблица по интервалам: диапазон времени + сколько показов/кликов пришло ЗА интервал */}
            <table className="data-table" style={{ borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                <thead><tr>
                    <th style={{ ...thStyle, textAlign: 'left' }}>Интервал (МСК)</th>
                    <th style={thStyle}>Показы</th>
                    <th style={thStyle}>Клики</th>
                    <th style={thStyle}>CTR</th>
                    <th style={thStyle}>Расход ₽</th>
                </tr></thead>
                <tbody>
                    {points.map((p, i) => {
                        const c = ctrOf(p);
                        return (
                            <tr key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                                style={{ background: i === focus ? '#f5f3ff' : undefined }}>
                                <td style={tdLeft}>{rangeOf(i)}</td>
                                <td style={tdStyle}>{fmt(p.views)}</td>
                                <td style={tdStyle}>{fmt(p.clicks)}</td>
                                <td style={{ ...tdStyle, color: c == null ? '#c0c4cc' : undefined }}>{c != null ? c.toFixed(1) : '—'}</td>
                                <td style={tdStyle}>{fmt(p.spend)}</td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}

/** Число над кончиком бара — горизонтальное и контрастное. Под курсором крупнее. */
function BarValue({ v, color, big }: { v: number; color: string; big?: boolean }) {
    return (
        <span style={{
            position: 'absolute', bottom: '100%', left: '50%', transform: 'translateX(-50%)',
            whiteSpace: 'nowrap', fontSize: big ? 12 : 10, fontWeight: 700, color, lineHeight: 1, paddingBottom: 3,
        }}>{fmt(v)}</span>
    );
}

function avg(a: number[]): number {
    return a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0;
}

// Только 30/60: официальная статистика WB обновляется не чаще ~раза в 30 мин.
const INTERVAL_OPTIONS = [30, 60];

/** Селектор частоты снимков (проект-глобально). Меняет, как часто job снимает стату. */
function IntervalSelect({ value, onChange }: { value?: number; onChange?: (m: number) => void | Promise<void> }) {
    const [saving, setSaving] = useState(false);
    const cur = value ?? 30;
    if (!onChange) return null;
    return (
        <label title="Как часто снимается внутридневная статистика — для всех кампаний проекта"
            style={{ fontSize: 12, color: '#6b7280', display: 'flex', alignItems: 'center', gap: 6 }}>
            Снимки каждые
            <select
                value={cur} disabled={saving}
                onChange={async e => {
                    const m = Number(e.target.value);
                    if (m === cur) return;
                    setSaving(true);
                    try { await onChange(m); } finally { setSaving(false); }
                }}
                style={{ padding: '3px 6px', border: '1px solid #e5e7eb', borderRadius: 8, fontSize: 12, background: '#fff', cursor: saving ? 'wait' : 'pointer' }}
            >
                {INTERVAL_OPTIONS.map(m => <option key={m} value={m}>{m} мин</option>)}
            </select>
        </label>
    );
}

/** Красная трендовая стрелка CTR (как у конкурента). */
function TrendArrow({ trend }: { trend: 'up' | 'down' | 'flat' }) {
    if (trend === 'flat') return null;
    const up = trend === 'up';
    return (
        <span title={up ? 'CTR растёт' : 'CTR падает'} style={{ color: '#ef4444', fontWeight: 700, fontSize: 14, lineHeight: 1 }}>
            {up ? '↗' : '↘'}
        </span>
    );
}
