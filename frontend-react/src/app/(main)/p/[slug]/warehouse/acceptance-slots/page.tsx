'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatDateTime, exportToExcel } from '@/lib/utils';
import type {
    SupplyAcceptanceSlotsResponse,
    SupplyAcceptanceSlotRow,
    AcceptanceBoxType,
} from '@/types/api';

type BoxFilter = 'all' | AcceptanceBoxType;

const BOX_LABEL: Record<AcceptanceBoxType, string> = {
    box: 'Короб',
    mono: 'Моно',
    super: 'Супер',
};

const BOX_TABS: { key: BoxFilter; label: string }[] = [
    { key: 'all', label: 'Все' },
    { key: 'box', label: 'Короб' },
    { key: 'mono', label: 'Моно' },
    { key: 'super', label: 'Супер' },
];

/** Short DD.MM from an ISO date (string-only — no Date parsing, tz-safe). */
function shortDate(iso: string): string {
    return `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
}

/** Ближайший бесплатный день поставки (ISO) или null. */
function nearestFree(row: SupplyAcceptanceSlotRow): string | null {
    return row.days.find(d => d.is_free)?.date ?? null;
}

interface WhGroup {
    wh: string;
    rows: SupplyAcceptanceSlotRow[];
}

export default function AcceptanceSlotsPage() {
    const [data, setData] = useState<SupplyAcceptanceSlotsResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState('');
    const [boxFilter, setBoxFilter] = useState<BoxFilter>('all');
    const [search, setSearch] = useState('');

    const load = useCallback(async (force = false) => {
        if (force) setRefreshing(true); else setLoading(true);
        setError('');
        try {
            const res = await api.getSupplyAcceptanceSlots(force);
            setData(res);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
        setRefreshing(false);
    }, []);

    useEffect(() => { load(false); }, [load]);

    const groups = useMemo<WhGroup[]>(() => {
        if (!data) return [];
        const q = search.trim().toLowerCase();
        const filtered = data.rows.filter(r =>
            (boxFilter === 'all' || r.box_type === boxFilter) &&
            (!q
                || r.assembly_number.toLowerCase().includes(q)
                || (r.wb_supply_id ?? '').toLowerCase().includes(q)
                || r.canonical_name.toLowerCase().includes(q)
                || (r.warehouse_name ?? '').toLowerCase().includes(q))
        );
        const byWh = new Map<string, SupplyAcceptanceSlotRow[]>();
        for (const r of filtered) {
            const k = r.canonical_name || '—';
            const arr = byWh.get(k);
            if (arr) arr.push(r); else byWh.set(k, [r]);
        }
        return Array.from(byWh.entries()).map(([wh, rows]) => ({ wh, rows }));
    }, [data, search, boxFilter]);

    const totalSupplies = useMemo(() => groups.reduce((s, g) => s + g.rows.length, 0), [groups]);

    const handleExport = () => {
        if (!data) return;
        const flat: Record<string, string | number>[] = [];
        for (const g of groups) {
            for (const r of g.rows) {
                for (const d of r.days) {
                    flat.push({
                        Склад: r.canonical_name,
                        Заявка: r.assembly_number,
                        Поставка: r.wb_supply_id ?? '',
                        Тип: BOX_LABEL[r.box_type] ?? r.box_type,
                        'Плановая сдача': r.planned_date ?? '',
                        Дата: d.date,
                        Коэффициент: d.coefficient,
                        Статус: d.is_closed ? 'Закрыто' : d.is_free ? 'Бесплатно' : 'Платно',
                    });
                }
            }
        }
        exportToExcel(flat, 'acceptance_slots');
    };

    if (loading) {
        return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    }
    if (error) {
        return (
            <div className="glass-card" style={{ padding: 32 }}>
                <p style={{ color: 'var(--color-danger)', marginBottom: 16 }}>⚠️ {error}</p>
                <button className="btn btn-secondary" onClick={() => load(true)}>Повторить</button>
            </div>
        );
    }

    const dates = data?.dates ?? [];

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Слоты сдачи поставок</h1>
                    <p className="page-subtitle">
                        Когда можно сдать каждую активную заявку — календарь приёмки её склада WB
                        {' · '}{totalSupplies} поставок · {groups.length} складов
                        {data?.fetched_at && (
                            <span style={{ color: 'var(--color-text-muted)' }}> · обновлено {formatDateTime(data.fetched_at)}</span>
                        )}
                    </p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={handleExport} disabled={totalSupplies === 0}>📥 Excel</button>
                    <button className="btn btn-primary btn-sm" onClick={() => load(true)} disabled={refreshing}>
                        {refreshing ? 'Обновление...' : '🔄 Обновить'}
                    </button>
                </div>
            </div>

            {/* Фильтры */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16, display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', gap: 6 }}>
                    {BOX_TABS.map(t => (
                        <button
                            key={t.key}
                            className={`btn btn-sm ${boxFilter === t.key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setBoxFilter(t.key)}
                        >
                            {t.label}
                        </button>
                    ))}
                </div>
                <input
                    className="form-input"
                    placeholder="Поиск: заявка, поставка, склад..."
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    style={{ maxWidth: 280 }}
                />
                <div style={{ display: 'flex', gap: 14, marginLeft: 'auto', fontSize: 13, color: 'var(--color-text-muted)' }}>
                    <span><span style={{ color: 'var(--color-success)' }}>●</span> бесплатно</span>
                    <span><span style={{ color: 'var(--color-warning)' }}>●</span> платно (×N)</span>
                    <span><span style={{ color: 'var(--color-danger)' }}>●</span> закрыто</span>
                    <span style={{ color: 'var(--color-accent)' }}>▢ плановая сдача</span>
                </div>
            </div>

            {/* Группы по складам */}
            {totalSupplies === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>📅</div>
                    <p style={{ marginBottom: 4 }}>Нет активных заявок для сдачи.</p>
                    <p style={{ fontSize: 13 }}>Появятся заявки в статусе «В работе / Готово / Машина назначена» — здесь будут их слоты приёмки.</p>
                </div>
            ) : (
                groups.map(g => (
                    <div key={g.wh} className="glass-card" style={{ padding: 0, marginBottom: 16, overflowX: 'auto' }}>
                        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--color-border)', display: 'flex', alignItems: 'baseline', gap: 10 }}>
                            <span style={{ fontSize: 16, fontWeight: 600 }}>{g.wh}</span>
                            <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>{g.rows.length} поставок</span>
                        </div>
                        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>
                            <thead>
                                <tr>
                                    <th style={{ position: 'sticky', left: 0, background: 'var(--color-bg-card)', textAlign: 'left', padding: '10px 12px', zIndex: 1, minWidth: 130 }}>Заявка</th>
                                    <th style={{ padding: '10px 8px', textAlign: 'left', minWidth: 90 }}>Поставка</th>
                                    <th style={{ padding: '10px 8px', textAlign: 'center', minWidth: 56 }}>Тип</th>
                                    <th style={{ padding: '10px 8px', textAlign: 'center', minWidth: 96 }}>Ближайший слот</th>
                                    {dates.map(d => (
                                        <th key={d} style={{ padding: '10px 6px', textAlign: 'center', minWidth: 44, color: 'var(--color-text-muted)', fontWeight: 600 }} title={formatDate(d)}>
                                            {shortDate(d)}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {g.rows.map((r, i) => {
                                    const byDate = new Map(r.days.map(d => [d.date, d]));
                                    const free = nearestFree(r);
                                    return (
                                        <tr key={`${r.assembly_request_id}-${i}`} style={{ borderTop: '1px solid var(--color-border)' }}>
                                            <td style={{ position: 'sticky', left: 0, background: 'var(--color-bg-card)', padding: '8px 12px', fontWeight: 500 }} title={r.warehouse_name ?? undefined}>
                                                {r.assembly_number}
                                                {r.planned_date && (
                                                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                                        план {formatDate(r.planned_date)}
                                                    </div>
                                                )}
                                            </td>
                                            <td style={{ padding: '8px', color: 'var(--color-text-muted)' }}>{r.wb_supply_id ?? '—'}</td>
                                            <td style={{ padding: '8px', textAlign: 'center' }}>
                                                <span className="badge badge-secondary">{BOX_LABEL[r.box_type] ?? r.box_type}</span>
                                            </td>
                                            <td style={{ padding: '8px', textAlign: 'center', fontWeight: 600, color: free ? 'var(--color-success)' : 'var(--color-text-dim)' }}>
                                                {free ? shortDate(free) : '—'}
                                            </td>
                                            {!r.matched ? (
                                                <td colSpan={dates.length} style={{ padding: '8px 12px', color: 'var(--color-text-dim)', fontStyle: 'italic' }}>
                                                    Склад не найден в календаре приёмки WB
                                                </td>
                                            ) : (
                                                dates.map(date => {
                                                    const day = byDate.get(date);
                                                    const isPlanned = r.planned_date === date;
                                                    const cellBase: React.CSSProperties = {
                                                        textAlign: 'center',
                                                        padding: '6px 4px',
                                                        ...(isPlanned ? { outline: '2px solid var(--color-accent)', outlineOffset: -2, borderRadius: 6 } : {}),
                                                    };
                                                    if (!day) {
                                                        return <td key={date} style={{ ...cellBase, color: 'var(--color-text-dim)' }}>—</td>;
                                                    }
                                                    const paid = !day.is_free && !day.is_closed;
                                                    const color = day.is_closed ? 'var(--color-danger)' : day.is_free ? 'var(--color-success)' : 'var(--color-warning)';
                                                    const label = day.is_closed ? '✕' : day.is_free ? '✓' : `×${day.coefficient}`;
                                                    const status = day.is_closed ? 'Закрыто' : day.is_free ? 'Бесплатно' : `Платно ×${day.coefficient}`;
                                                    const tip = `${formatDate(date)} · ${status}`
                                                        + (isPlanned ? ' · плановая сдача' : '')
                                                        + (day.storage_coef != null ? ` · хранение ×${day.storage_coef}` : '')
                                                        + (day.delivery_coef != null ? ` · логистика ×${day.delivery_coef}` : '');
                                                    return (
                                                        <td key={date} style={cellBase} title={tip}>
                                                            <span style={{ color, fontWeight: paid ? 700 : 600 }}>{label}</span>
                                                        </td>
                                                    );
                                                })
                                            )}
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                ))
            )}
        </div>
    );
}
