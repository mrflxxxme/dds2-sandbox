'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import type { ReviewBreakdownGroup, ReviewBreakdownResponse, ReviewBreakdownRow } from '@/types/api';

const GROUPS: { key: ReviewBreakdownGroup; label: string }[] = [
    { key: 'day', label: 'По дням' },
    { key: 'week', label: 'По неделям' },
    { key: 'month', label: 'По месяцам' },
    { key: 'subject', label: 'По предмету' },
    { key: 'brand', label: 'По бренду' },
    { key: 'nm_id', label: 'По артикулу' },
];
const TIME_GROUPS: ReviewBreakdownGroup[] = ['day', 'week', 'month'];
const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

function avgColor(v: number | null): string {
    if (v == null) return 'var(--color-text-dim)';
    if (v >= 4.5) return '#34c759';
    if (v >= 4.0) return '#7dd957';
    if (v >= 3.0) return '#ff9f0a';
    return '#ff3b30';
}

/** Читаемая подпись группы. */
function fmtLabel(groupBy: ReviewBreakdownGroup, row: ReviewBreakdownRow): string {
    if (groupBy === 'month') {
        const [y, m] = row.key.split('-');
        return `${MONTHS[Number(m) - 1] ?? m}'${y.slice(2)}`;
    }
    if (groupBy === 'week') return `нед. ${formatDate(row.key)}`;
    if (groupBy === 'day') return formatDate(row.key);
    return row.label;
}

function pct(n: number, total: number): number {
    return total ? (n / total) * 100 : 0;
}

/** Ячейка «оценка N»: абсолют + доля. Красный фон, если доля хуже средней (для 1–2★). */
function StarCell({ n, total, worse }: { n: number; total: number; worse: boolean }) {
    const p = pct(n, total);
    return (
        <td style={{ textAlign: 'right', padding: '8px 12px', whiteSpace: 'nowrap' }}>
            <div style={{ fontWeight: 600 }}>{formatNumber(n, 0)}</div>
            <div
                style={{
                    fontSize: 12, marginTop: 2,
                    color: worse ? 'var(--color-danger)' : 'var(--color-text-dim)',
                    background: worse ? 'rgba(255,59,48,0.12)' : 'transparent',
                    borderRadius: 6, display: 'inline-block', padding: worse ? '0 5px' : 0,
                }}
            >
                {formatNumber(p, 1)}%
            </div>
        </td>
    );
}

export default function ReviewsBreakdownTab() {
    const [groupBy, setGroupBy] = useState<ReviewBreakdownGroup>('month');
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');
    const [subject, setSubject] = useState('');
    const [brand, setBrand] = useState('');
    const [nmId, setNmId] = useState('');
    const [data, setData] = useState<ReviewBreakdownResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const nm = nmId.trim() ? Number(nmId.trim()) : undefined;
            setData(await api.getReviewsBreakdown({
                groupBy,
                dateFrom: dateFrom || undefined,
                dateTo: dateTo || undefined,
                subject: subject || undefined,
                brand: brand || undefined,
                nmId: nm && !Number.isNaN(nm) ? nm : undefined,
            }));
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить таблицу');
            setData(null);
        } finally {
            setLoading(false);
        }
    }, [groupBy, dateFrom, dateTo, subject, brand, nmId]);

    useEffect(() => { load(); }, [load]);

    const rows = data?.rows ?? [];
    const totals = data?.totals;
    const isTime = TIME_GROUPS.includes(groupBy);
    const maxTotal = useMemo(() => rows.reduce((m, r) => Math.max(m, r.total), 0), [rows]);

    // Доли 1★/2★ по итогу — порог «хуже среднего» для подсветки
    const totShare1 = totals ? pct(totals.r1, totals.total) : 0;
    const totShare2 = totals ? pct(totals.r2, totals.total) : 0;

    // Дельта рейтинга к предыдущему периоду (для time-группировок; строки идут новые→старые)
    const deltas = useMemo(() => {
        if (!isTime) return {};
        const d: Record<string, number | null> = {};
        for (let i = 0; i < rows.length; i++) {
            const cur = rows[i].avg_rating;
            const prev = rows[i + 1]?.avg_rating ?? null;
            d[rows[i].key] = cur != null && prev != null ? cur - prev : null;
        }
        return d;
    }, [rows, isTime]);

    const exportXlsx = useCallback(() => {
        if (!data) return;
        const flat = [data.totals, ...data.rows].map(r => ({
            'Группа': r.key === '__total__' ? 'ИТОГО' : fmtLabel(groupBy, r),
            'Всего': r.total,
            'Рейтинг': r.avg_rating ?? '',
            '5★': r.r5, '5★ %': `${formatNumber(pct(r.r5, r.total), 1)}%`,
            '4★': r.r4, '4★ %': `${formatNumber(pct(r.r4, r.total), 1)}%`,
            '3★': r.r3, '3★ %': `${formatNumber(pct(r.r3, r.total), 1)}%`,
            '2★': r.r2, '2★ %': `${formatNumber(pct(r.r2, r.total), 1)}%`,
            '1★': r.r1, '1★ %': `${formatNumber(pct(r.r1, r.total), 1)}%`,
        }));
        exportToExcel(flat, `reviews_breakdown_${groupBy}`);
    }, [data, groupBy]);

    const inputStyle = { minWidth: 150 } as const;

    return (
        <div>
            {/* Фильтры */}
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--color-text-dim)' }}>
                    Период с
                    <input type="date" className="form-input" value={dateFrom} onChange={e => setDateFrom(e.target.value)} disabled={loading} />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--color-text-dim)' }}>
                    по
                    <input type="date" className="form-input" value={dateTo} onChange={e => setDateTo(e.target.value)} disabled={loading} />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--color-text-dim)' }}>
                    Предмет
                    <select className="form-input" style={inputStyle} value={subject} onChange={e => setSubject(e.target.value)} disabled={loading}>
                        <option value="">Все</option>
                        {(data?.subjects ?? []).map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--color-text-dim)' }}>
                    Бренд
                    <select className="form-input" style={inputStyle} value={brand} onChange={e => setBrand(e.target.value)} disabled={loading}>
                        <option value="">Все</option>
                        {(data?.brands ?? []).map(b => <option key={b} value={b}>{b}</option>)}
                    </select>
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--color-text-dim)' }}>
                    Артикул (nmID)
                    <input type="number" className="form-input" style={{ width: 130 }} value={nmId} onChange={e => setNmId(e.target.value)} placeholder="Все" disabled={loading} />
                </label>
                {(dateFrom || dateTo || subject || brand || nmId) && (
                    <button className="btn btn-secondary btn-sm" onClick={() => { setDateFrom(''); setDateTo(''); setSubject(''); setBrand(''); setNmId(''); }}>Сбросить</button>
                )}
            </div>

            {/* Группировка */}
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Группировка:</span>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {GROUPS.map(g => (
                        <button
                            key={g.key}
                            className={`btn btn-sm ${groupBy === g.key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setGroupBy(g.key)}
                            disabled={loading}
                        >
                            {g.label}
                        </button>
                    ))}
                </div>
                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={exportXlsx} disabled={loading || !data}>📥 Excel</button>
            </div>

            {error && (
                <div className="glass-card" style={{ marginBottom: 20, color: 'var(--color-danger)' }}>
                    {error} <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={load}>Повторить</button>
                </div>
            )}

            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>Загрузка…</div>
            )}

            {!loading && !error && data && !data.has_key && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>🔑</div>
                    <h3 style={{ margin: '0 0 8px' }}>WB-ключ не настроен</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>Добавьте API-ключ Wildberries со scope «Вопросы и отзывы» в «Настройка проекта» → Интеграции.</p>
                </div>
            )}

            {!loading && !error && data && data.has_key && rows.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>💬</div>
                    <h3 style={{ margin: '0 0 8px' }}>Нет данных</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>По выбранным фильтрам отзывов не найдено.</p>
                </div>
            )}

            {!loading && !error && data && data.has_key && rows.length > 0 && totals && (
                <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                    <div style={{ padding: '16px 20px 8px' }}>
                        <h3 style={{ margin: 0, fontSize: 16 }}>Детальная таблица отзывов</h3>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Абсолютные и относительные значения{data.truncated ? ' · показаны первые 500 групп' : ''}</div>
                    </div>
                    <div style={{ overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                            <thead>
                                <tr style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-text-dim)', fontSize: 12, textTransform: 'uppercase' }}>
                                    <th style={{ textAlign: 'left', padding: '8px 20px' }}>{GROUPS.find(g => g.key === groupBy)?.label.replace('По ', '') ?? ''}</th>
                                    <th style={{ textAlign: 'right', padding: '8px 12px' }}>Всего</th>
                                    <th style={{ textAlign: 'right', padding: '8px 12px' }}>Рейтинг</th>
                                    <th style={{ textAlign: 'right', padding: '8px 12px' }}>5★</th>
                                    <th style={{ textAlign: 'right', padding: '8px 12px' }}>4★</th>
                                    <th style={{ textAlign: 'right', padding: '8px 12px' }}>3★</th>
                                    <th style={{ textAlign: 'right', padding: '8px 12px' }}>2★</th>
                                    <th style={{ textAlign: 'right', padding: '8px 12px' }}>1★</th>
                                </tr>
                            </thead>
                            <tbody>
                                {/* ИТОГО */}
                                <tr style={{ background: 'rgba(0,113,227,0.06)', fontWeight: 600, borderBottom: '1px solid var(--color-border)' }}>
                                    <td style={{ padding: '10px 20px' }}>ИТОГО</td>
                                    <td style={{ textAlign: 'right', padding: '10px 12px' }}>{formatNumber(totals.total, 0)}</td>
                                    <td style={{ textAlign: 'right', padding: '10px 12px', color: avgColor(totals.avg_rating) }}>{totals.avg_rating != null ? `${formatNumber(totals.avg_rating, 2)}★` : '—'}</td>
                                    <StarCell n={totals.r5} total={totals.total} worse={false} />
                                    <StarCell n={totals.r4} total={totals.total} worse={false} />
                                    <StarCell n={totals.r3} total={totals.total} worse={false} />
                                    <StarCell n={totals.r2} total={totals.total} worse={false} />
                                    <StarCell n={totals.r1} total={totals.total} worse={false} />
                                </tr>
                                {rows.map(r => {
                                    const delta = isTime ? deltas[r.key] : undefined;
                                    return (
                                        <tr key={r.key} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                            <td style={{ padding: '8px 20px', position: 'relative' }}>
                                                <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${pct(r.total, maxTotal)}%`, background: 'rgba(94,92,230,0.08)' }} />
                                                <span style={{ position: 'relative' }}>{fmtLabel(groupBy, r)}</span>
                                            </td>
                                            <td style={{ textAlign: 'right', padding: '8px 12px' }}>{formatNumber(r.total, 0)}</td>
                                            <td style={{ textAlign: 'right', padding: '8px 12px', whiteSpace: 'nowrap' }}>
                                                <span style={{ fontWeight: 600, color: avgColor(r.avg_rating) }}>{r.avg_rating != null ? `${formatNumber(r.avg_rating, 2)}★` : '—'}</span>
                                                {delta != null && Math.abs(delta) >= 0.005 && (
                                                    <span style={{ marginLeft: 6, fontSize: 11, color: delta > 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>
                                                        {delta > 0 ? '↑' : '↓'}{formatNumber(Math.abs(delta), 2)}
                                                    </span>
                                                )}
                                            </td>
                                            <StarCell n={r.r5} total={r.total} worse={false} />
                                            <StarCell n={r.r4} total={r.total} worse={false} />
                                            <StarCell n={r.r3} total={r.total} worse={false} />
                                            <StarCell n={r.r2} total={r.total} worse={pct(r.r2, r.total) > totShare2 && r.r2 > 0} />
                                            <StarCell n={r.r1} total={r.total} worse={pct(r.r1, r.total) > totShare1 && r.r1 > 0} />
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
}
