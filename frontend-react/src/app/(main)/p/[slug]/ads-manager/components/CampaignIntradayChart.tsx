'use client';
import React, { useState } from 'react';
import type { CampaignIntradayMetrics, CampaignIntradayPoint } from '@/types/api';
import { fmt, thStyle, tdStyle, tdLeft } from './adsShared';

/** Внутридневной график «место принятия решения» (стиль mkeeper): по интервалам снимков
 *  (~30 мин) — показы (фиолетовые бары) + клики (зелёные, своя шкала), расход, CTR с порогом
 *  «мин показов» и тренд. Данные копятся вперёд из кабинетной сессии (WB нативно почасовку
 *  не отдаёт), поэтому первые дни/часы график наполняется постепенно. */
export default function CampaignIntradayChart({ resp, onSetInterval }: {
    resp: CampaignIntradayMetrics;
    onSetInterval?: (minutes: number) => void | Promise<void>;
}) {
    const [minViews, setMinViews] = useState(50);
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
    const last = points[points.length - 1];
    const lastCtr = last ? ctrOf(last) : null;
    const totalCtr = resp.totals.views ? (resp.totals.clicks / resp.totals.views) * 100 : 0;
    // Подписи времени прореживаем, чтобы не слипались (цель ~8 меток).
    const labelStep = Math.max(1, Math.ceil(points.length / 8));

    if (resp.snapshots === 0) {
        return (
            <div style={{ padding: 20 }}>
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
                    <IntervalSelect value={resp.interval_min} onChange={onSetInterval} />
                </div>
                <div style={{ textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>
                    За этот день снимков ещё нет. Внутридневные показы/клики копятся вперёд из сессии
                    рекламного кабинета — WB готовую почасовку не отдаёт. Первые интервалы появятся через
                    пару снимков. Если данных нет и завтра — проверьте доступ WB в настройках (сессия
                    рекламного кабинета).
                </div>
            </div>
        );
    }

    return (
        <div style={{ padding: 16 }}>
            {/* Шапка: последний интервал + тренд + порог */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
                <div style={{ fontSize: 13, color: '#374151', display: 'flex', alignItems: 'center', gap: 8 }}>
                    {last ? (
                        <span>Интервал <b>{last.time}</b> · Показы: <b>{last.views}</b> · Клики: <b>{last.clicks}</b> · CTR: <b>{lastCtr != null ? lastCtr.toFixed(1) : '—'}</b></span>
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

            {/* График: по интервалу два бара рядом — показы (фиолетовый) и клики (зелёный, своя шкала) */}
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 190, borderBottom: '1px solid #e5e7eb' }}>
                {points.map((p, i) => (
                    <div key={i} title={`${p.time} — показы ${p.views}, клики ${p.clicks}, ${fmt(p.spend)} ₽`}
                        style={{ flex: 1, display: 'flex', alignItems: 'flex-end', justifyContent: 'center', gap: 1, height: '100%' }}>
                        <div style={{ width: '46%', height: `${(p.views / maxViews) * 100}%`, minHeight: p.views > 0 ? 2 : 0, background: '#7c6df2', borderRadius: '2px 2px 0 0' }} />
                        <div style={{ width: '46%', height: `${(p.clicks / maxClicks) * 100}%`, minHeight: p.clicks > 0 ? 2 : 0, background: '#86c99a', borderRadius: '2px 2px 0 0' }} />
                    </div>
                ))}
            </div>
            {/* Подписи времени (прорежены) */}
            <div style={{ display: 'flex', gap: 2, marginBottom: 10 }}>
                {points.map((p, i) => (
                    <div key={i} style={{ flex: 1, textAlign: 'center', fontSize: 9, color: '#9ca3af' }}>{i % labelStep === 0 ? p.time : ''}</div>
                ))}
            </div>

            {/* Легенда + сводка за день */}
            <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', fontSize: 12, color: '#6b7280', marginBottom: 14 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><i style={{ width: 10, height: 10, background: '#7c6df2', borderRadius: 2, display: 'inline-block' }} /> Показы</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><i style={{ width: 10, height: 10, background: '#86c99a', borderRadius: 2, display: 'inline-block' }} /> Клики</span>
                <span style={{ marginLeft: 'auto' }}>
                    За <b>{resp.date}</b>: показы <b>{resp.totals.views}</b> · клики <b>{resp.totals.clicks}</b> · CTR <b>{totalCtr.toFixed(1)}%</b> · потрачено <b>{fmt(resp.totals.spend)} ₽</b>
                </span>
            </div>

            {/* Таблица по интервалам */}
            <table className="data-table" style={{ borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                <thead><tr>
                    <th style={{ ...thStyle, textAlign: 'left' }}>Время (МСК)</th>
                    <th style={thStyle}>Показы</th>
                    <th style={thStyle}>Клики</th>
                    <th style={thStyle}>CTR</th>
                    <th style={thStyle}>Расход ₽</th>
                </tr></thead>
                <tbody>
                    {points.map((p, i) => {
                        const c = ctrOf(p);
                        return (
                            <tr key={i}>
                                <td style={tdLeft}>{p.time}</td>
                                <td style={tdStyle}>{p.views}</td>
                                <td style={tdStyle}>{p.clicks}</td>
                                <td style={{ ...tdStyle, color: c == null ? '#c0c4cc' : undefined }}>{c != null ? c.toFixed(1) : '—'}</td>
                                <td style={tdStyle}>{fmt(p.spend)}</td>
                            </tr>
                        );
                    })}
                    <tr style={{ background: '#f5f3ff' }}>
                        <td style={{ ...tdLeft, fontWeight: 700 }}>Всего</td>
                        <td style={{ ...tdStyle, fontWeight: 700 }}>{resp.totals.views}</td>
                        <td style={{ ...tdStyle, fontWeight: 700 }}>{resp.totals.clicks}</td>
                        <td style={{ ...tdStyle, fontWeight: 700 }}>{totalCtr.toFixed(1)}</td>
                        <td style={{ ...tdStyle, fontWeight: 700 }}>{fmt(resp.totals.spend)}</td>
                    </tr>
                </tbody>
            </table>
        </div>
    );
}

function avg(a: number[]): number {
    return a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0;
}

const INTERVAL_OPTIONS = [10, 20, 30, 60];

/** Селектор частоты снимков (проект-глобально). Меняет, как часто job снимает стату. */
function IntervalSelect({ value, onChange }: { value?: number; onChange?: (m: number) => void | Promise<void> }) {
    const [saving, setSaving] = useState(false);
    const cur = value ?? 10;
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
