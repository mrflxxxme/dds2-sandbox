'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, formatDateTime, exportToExcel } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import type { RawSourceRows, RawColumn } from '@/types/api';

const PER_PAGE = 50;
const iso = (d: Date) => d.toISOString().slice(0, 10);

/** Значение ячейки — по типу колонки из ORM-модели. */
function cellText(value: unknown, type: RawColumn['type']): string {
    if (value == null || value === '') return '—';
    switch (type) {
        // Идентификаторы — без разделителей разрядов: «37158056», не «37 158 056»
        case 'id': return String(value);
        case 'date': return formatDate(String(value));
        case 'datetime': return formatDateTime(String(value));
        case 'number': return typeof value === 'number' ? formatNumber(value, Number.isInteger(value) ? 0 : 2) : String(value);
        case 'bool': return value ? 'да' : 'нет';
        case 'json': return Array.isArray(value) ? value.join(', ') : JSON.stringify(value);
        default: return String(value);
    }
}

export default function RawSourcePage() {
    const routeParams = useParams();
    const slug = typeof routeParams?.slug === 'string' ? routeParams.slug : Array.isArray(routeParams?.slug) ? routeParams.slug[0] : '';
    const key = typeof routeParams?.key === 'string' ? routeParams.key : Array.isArray(routeParams?.key) ? routeParams.key[0] : '';

    const [data, setData] = useState<RawSourceRows | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [page, setPage] = useState(1);
    const [sort, setSort] = useState<{ by: string; dir: 'asc' | 'desc' } | null>(null);
    const [from, setFrom] = useState('');
    const [to, setTo] = useState('');

    // signal — защита от гонки устаревших ответов (быстрая смена фильтра/периода во время
    // загрузки: медленный старый ответ перетирал свежий) и от двойного маунта StrictMode.
    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true); setError('');
        try {
            const res = await api.getRawSourceRows(key, {
                limit: PER_PAGE, offset: (page - 1) * PER_PAGE,
                date_from: from || undefined, date_to: to || undefined,
                sort_by: sort?.by, sort_dir: sort?.dir,
            });
            if (signal?.aborted) return;
            setData(res);
        } catch (e) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [key, page, from, to, sort]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    // Смена фильтра/сортировки возвращает на первую страницу (иначе можно застрять за концом
    // выборки). Сбрасываем прямо в обработчиках: отдельный useEffect дал бы лишний запрос.
    const toggleSort = (col: string) => {
        setPage(1);
        setSort(s => (s?.by === col ? { by: col, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { by: col, dir: 'desc' }));
    };
    const setRange = (f: string, t: string) => { setPage(1); setFrom(f); setTo(t); };

    const pageCount = data ? Math.max(1, Math.ceil(data.total / PER_PAGE)) : 1;
    const sortBy = sort?.by ?? data?.date_field;

    const onExport = () => {
        if (!data) return;
        exportToExcel(
            data.rows.map(r => Object.fromEntries(data.columns.map(c => [c.label, cellText(r[c.key], c.type)]))),
            `${data.table}_стр${page}`,
        );
    };

    const rangeLabel = useMemo(() => {
        if (!data || data.total === 0) return 'нет строк';
        const a = (page - 1) * PER_PAGE + 1;
        const b = Math.min(page * PER_PAGE, data.total);
        return `${formatNumber(a, 0)}–${formatNumber(b, 0)} из ${formatNumber(data.total, 0)}`;
    }, [data, page]);

    return (
        <PageGuard page="raw-data">
            <div className="animate-in">
                <div style={{ marginBottom: 12 }}>
                    <Link href={`/p/${slug}/raw-data`} style={{ fontSize: 12, color: 'var(--color-accent)', textDecoration: 'none' }}>← Все источники</Link>
                    <h1 style={{ fontSize: 26, fontWeight: 700, margin: '6px 0 0' }}>{data?.title || 'Источник'}</h1>
                    {data && (
                        <>
                            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', margin: '6px 0 0', maxWidth: 760 }}>{data.description}</p>
                            <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 4 }}><code>{data.table}</code> · {data.source}</div>
                        </>
                    )}
                </div>

                <div className="glass-card static" style={{ padding: '10px 14px', marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 12, color: '#6b7280' }}>Период по «{data?.columns.find(c => c.key === data?.date_field)?.label ?? 'дате'}»</span>
                    <input type="date" value={from} onChange={e => setRange(e.target.value, to)} aria-label="Дата с"
                        style={{ fontSize: 12, padding: '4px 7px', borderRadius: 8, border: '1px solid #d1d5db', background: '#fff' }} />
                    <span style={{ color: '#9ca3af' }}>—</span>
                    <input type="date" value={to} onChange={e => setRange(from, e.target.value)} aria-label="Дата по"
                        style={{ fontSize: 12, padding: '4px 7px', borderRadius: 8, border: '1px solid #d1d5db', background: '#fff' }} />
                    {(from || to) && (
                        <button className="btn btn-secondary btn-sm" style={{ fontSize: 11.5 }} onClick={() => setRange('', '')}>Сбросить</button>
                    )}
                    <button className="btn btn-secondary btn-sm" style={{ fontSize: 11.5, marginLeft: 'auto' }}
                        onClick={onExport} disabled={!data || data.rows.length === 0}
                        title="Выгрузить показанную страницу в Excel">Excel</button>
                    <button className="btn btn-secondary btn-sm" style={{ fontSize: 11.5 }}
                        onClick={() => setRange(iso(new Date(Date.now() - 6 * 86400_000)), iso(new Date()))}>7 дней</button>
                </div>

                {loading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка…</div>}

                {!loading && error && (
                    <div className="glass-card" style={{ padding: '16px 20px', border: '1px solid var(--color-danger)', background: '#fef2f2' }}>
                        <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>{error}</span>
                    </div>
                )}

                {!loading && !error && data && data.rows.length === 0 && (
                    <div className="glass-card static" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>
                        За выбранный период строк нет.
                    </div>
                )}

                {!loading && !error && data && data.rows.length > 0 && (
                    <>
                        <div className="glass-card static" style={{ padding: 0, overflow: 'hidden' }}>
                            <div style={{ overflowX: 'auto' }}>
                                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                    <thead>
                                        <tr>
                                            {data.columns.map(c => (
                                                <th key={c.key} onClick={() => toggleSort(c.key)}
                                                    style={{
                                                        position: 'sticky', top: 0, zIndex: 2, cursor: 'pointer',
                                                        background: '#374151', color: '#e5e7eb', fontSize: 9.5, fontWeight: 600,
                                                        letterSpacing: 0.3, textTransform: 'uppercase', whiteSpace: 'nowrap',
                                                        textAlign: c.type === 'number' ? 'right' : 'left', padding: '7px 8px',
                                                    }}>
                                                    {c.label}{sortBy === c.key ? (sort?.dir === 'asc' ? ' ↑' : ' ↓') : ''}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.rows.map((r, i) => (
                                            <tr key={i} style={{ borderTop: '1px solid #f1f2f4' }}>
                                                {data.columns.map(c => (
                                                    <td key={c.key} style={{
                                                        padding: '3px 8px', fontSize: 11, height: 26, whiteSpace: 'nowrap',
                                                        color: '#111827', textAlign: c.type === 'number' ? 'right' : 'left',
                                                    }}>
                                                        {cellText(r[c.key], c.type)}
                                                    </td>
                                                ))}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10 }}>
                            <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }} disabled={page <= 1} onClick={() => setPage(p => p - 1)}>←</button>
                            <span style={{ fontSize: 12, color: '#6b7280' }}>Стр. {page} из {formatNumber(pageCount, 0)} · {rangeLabel}</span>
                            <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }} disabled={page >= pageCount} onClick={() => setPage(p => p + 1)}>→</button>
                        </div>
                    </>
                )}
            </div>
        </PageGuard>
    );
}
