'use client';

import { useMemo, useState } from 'react';
import { formatNumber, formatDate } from '@/lib/utils';
import type { WarehouseMeasurement } from '@/types/api';

/** Один замер артикула в хронологии с дельтой объёма к предыдущему замеру. */
interface HistoryPoint {
    dim_id: number;
    measured_at: string | null;
    length: number | null;
    width: number | null;
    height: number | null;
    volume: number | null;
    delta: number | null; // объём − объём предыдущего замера (хронологически)
    photo_urls: string[] | null;
}

/** Артикул с полной историей замеров (замеры отсортированы по возрастанию даты). */
interface HistoryArticle {
    nm_id: number;
    subject_name: string | null;
    brand: string | null;
    count: number;
    first_volume: number | null;
    last_volume: number | null;
    min_volume: number | null;
    max_volume: number | null;
    total_delta: number | null; // последний объём − первый
    points: HistoryPoint[];
}

const num = (v: string | number | null | undefined) => (v == null ? null : Number(v));
const ts = (d: string | null) => (d ? new Date(d).getTime() : 0);
const dims = (l: number | null, w: number | null, h: number | null) =>
    [l, w, h].every((x) => x == null) ? '—' : `${l ?? '—'}×${w ?? '—'}×${h ?? '—'}`;

/** Дельта объёма со знаком и цветом: рост габаритов (↑) — хуже (danger), уменьшение (↓) — success. */
function VolumeDelta({ delta }: { delta: number | null }) {
    if (delta == null) return <span style={{ color: 'var(--color-dim)' }}>—</span>;
    if (Math.abs(delta) < 0.0005)
        return <span style={{ color: 'var(--color-muted)' }}>0</span>;
    const up = delta > 0;
    return (
        <span style={{ color: up ? 'var(--color-danger)' : 'var(--color-success)', fontWeight: 600 }}>
            {up ? '▲' : '▼'} {up ? '+' : '−'}{formatNumber(Math.abs(delta), 3)}
        </span>
    );
}

function PhotoLinks({ urls }: { urls: string[] | null }) {
    if (!urls || urls.length === 0) return <>—</>;
    return (
        <span style={{ display: 'inline-flex', gap: 8 }}>
            {urls.map((u, i) => (
                <a key={i} href={u} target="_blank" rel="noopener noreferrer"
                   className="badge badge-info" style={{ textDecoration: 'none' }}>
                    📷 {i + 1}
                </a>
            ))}
        </span>
    );
}

/** Группировка плоского списка замеров по артикулу с расчётом динамики объёма. */
function buildHistory(items: WarehouseMeasurement[]): HistoryArticle[] {
    const byNm = new Map<number, WarehouseMeasurement[]>();
    for (const m of items) {
        const arr = byNm.get(m.nm_id);
        if (arr) arr.push(m);
        else byNm.set(m.nm_id, [m]);
    }

    const articles: HistoryArticle[] = [];
    for (const [nm_id, raw] of byNm) {
        // Хронологически: от старого замера к новому.
        const sorted = [...raw].sort((a, b) => ts(a.measured_at) - ts(b.measured_at));

        let prevVol: number | null = null;
        const points: HistoryPoint[] = sorted.map((m) => {
            const volume = num(m.volume);
            const delta = volume != null && prevVol != null ? volume - prevVol : null;
            if (volume != null) prevVol = volume;
            return {
                dim_id: m.dim_id,
                measured_at: m.measured_at,
                length: m.length,
                width: m.width,
                height: m.height,
                volume,
                delta,
                photo_urls: m.photo_urls,
            };
        });

        const vols = points.map((p) => p.volume).filter((v): v is number => v != null);
        const first = points.find((p) => p.volume != null)?.volume ?? null;
        const last = [...points].reverse().find((p) => p.volume != null)?.volume ?? null;

        articles.push({
            nm_id,
            subject_name: sorted[sorted.length - 1]?.subject_name ?? null,
            brand: sorted[sorted.length - 1]?.brand ?? null,
            count: sorted.length,
            first_volume: first,
            last_volume: last,
            min_volume: vols.length ? Math.min(...vols) : null,
            max_volume: vols.length ? Math.max(...vols) : null,
            total_delta: first != null && last != null ? last - first : null,
            points,
        });
    }

    // Сначала артикулы с наибольшим числом замеров (там есть что смотреть), затем по свежести.
    articles.sort(
        (a, b) =>
            b.count - a.count ||
            ts(b.points[b.points.length - 1]?.measured_at ?? null) -
                ts(a.points[a.points.length - 1]?.measured_at ?? null),
    );
    return articles;
}

const thStyle: React.CSSProperties = {
    textAlign: 'left', padding: '8px 12px', fontSize: 12, fontWeight: 600,
    color: 'var(--color-muted)', borderBottom: '1px solid var(--color-border)',
};
const tdStyle: React.CSSProperties = {
    padding: '8px 12px', fontSize: 13, borderBottom: '1px solid var(--color-border)',
};

function ArticleRow({ a }: { a: HistoryArticle }) {
    const [open, setOpen] = useState(false);
    return (
        <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
            <button
                onClick={() => setOpen((o) => !o)}
                style={{
                    display: 'flex', alignItems: 'center', gap: 12, width: '100%',
                    padding: '14px 20px', background: 'none', border: 'none', cursor: 'pointer',
                    textAlign: 'left', color: 'var(--color-text)',
                }}
            >
                <span style={{ color: 'var(--color-muted)', width: 14, flexShrink: 0 }}>
                    {open ? '▾' : '▸'}
                </span>
                <span style={{ fontWeight: 600, fontFamily: 'var(--font-mono, monospace)' }}>{a.nm_id}</span>
                {a.brand && <span className="badge badge-secondary">{a.brand}</span>}
                <span style={{ color: 'var(--color-muted)', fontSize: 13 }}>{a.subject_name ?? '—'}</span>

                <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 16 }}>
                    <span className="badge badge-info">{formatNumber(a.count, 0)} замеров</span>
                    <span style={{ fontSize: 13, color: 'var(--color-muted)' }}>
                        {formatNumber(a.first_volume ?? 0, 3)} → <strong style={{ color: 'var(--color-text)' }}>
                            {formatNumber(a.last_volume ?? 0, 3)}
                        </strong> л
                    </span>
                    <VolumeDelta delta={a.total_delta} />
                </span>
            </button>

            {open && (
                <div style={{ overflowX: 'auto', borderTop: '1px solid var(--color-border)' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead>
                            <tr>
                                <th style={thStyle}>Дата замера</th>
                                <th style={thStyle}>№ замера ВБ</th>
                                <th style={thStyle}>Габариты Д×Ш×В, см</th>
                                <th style={{ ...thStyle, textAlign: 'right' }}>Объём, л</th>
                                <th style={{ ...thStyle, textAlign: 'right' }}>Δ к прошлому</th>
                                <th style={{ ...thStyle, textAlign: 'center' }}>Фото</th>
                            </tr>
                        </thead>
                        <tbody>
                            {a.points.map((p) => (
                                <tr key={p.dim_id}>
                                    <td style={tdStyle}>{formatDate(p.measured_at)}</td>
                                    <td style={tdStyle}>{p.dim_id}</td>
                                    <td style={tdStyle}>{dims(p.length, p.width, p.height)}</td>
                                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                                        {p.volume == null ? '—' : formatNumber(p.volume, 3)}
                                    </td>
                                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                                        <VolumeDelta delta={p.delta} />
                                    </td>
                                    <td style={{ ...tdStyle, textAlign: 'center' }}>
                                        <PhotoLinks urls={p.photo_urls} />
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

export default function MeasurementHistory({
    items,
    loading,
}: {
    items: WarehouseMeasurement[];
    loading: boolean;
}) {
    const articles = useMemo(() => buildHistory(items), [items]);

    if (loading) {
        return <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-muted)' }}>Загрузка…</div>;
    }
    if (articles.length === 0) {
        return (
            <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-muted)' }}>
                <div style={{ fontSize: 40, marginBottom: 8 }}>📈</div>
                Нет замеров за период. Нажмите «Синхронизировать».
            </div>
        );
    }

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontSize: 13, color: 'var(--color-muted)', marginBottom: 4 }}>
                {formatNumber(articles.length, 0)} артикулов с замерами — нажмите на артикул, чтобы раскрыть историю
            </div>
            {articles.map((a) => (
                <ArticleRow key={a.nm_id} a={a} />
            ))}
        </div>
    );
}
