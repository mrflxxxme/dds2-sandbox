'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatDateTime } from '@/lib/utils';
import { exportToExcel } from '@/lib/utils';
import type { AcceptanceLimitsResponse, AcceptanceLimitEntry, AcceptanceBoxType } from '@/types/api';

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

export default function AcceptanceLimitsPage() {
    const [data, setData] = useState<AcceptanceLimitsResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState('');
    const [boxFilter, setBoxFilter] = useState<BoxFilter>('all');
    const [search, setSearch] = useState('');

    const load = useCallback(async (force = false) => {
        if (force) setRefreshing(true); else setLoading(true);
        setError('');
        try {
            const res = await api.getWbAcceptanceLimits(undefined, force);
            setData(res);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
        setRefreshing(false);
    }, []);

    useEffect(() => { load(false); }, [load]);

    const rows = useMemo<AcceptanceLimitEntry[]>(() => {
        if (!data) return [];
        const q = search.trim().toLowerCase();
        return data.warehouses.filter(w =>
            (boxFilter === 'all' || w.box_type === boxFilter) &&
            (!q || w.canonical_name.toLowerCase().includes(q) || w.warehouse_name.toLowerCase().includes(q))
        );
    }, [data, boxFilter, search]);

    const handleExport = () => {
        if (!data) return;
        const flat: Record<string, string | number>[] = [];
        for (const w of rows) {
            for (const d of w.days) {
                flat.push({
                    Склад: w.canonical_name,
                    Тип: BOX_LABEL[w.box_type as AcceptanceBoxType] ?? w.box_type,
                    Дата: d.date,
                    Коэффициент: d.coefficient,
                    Статус: d.is_closed ? 'Закрыто' : d.is_free ? 'Бесплатно' : 'Платно',
                });
            }
        }
        exportToExcel(flat, 'acceptance_limits');
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
                    <h1 className="page-title">Лимиты приёмки</h1>
                    <p className="page-subtitle">
                        Коэффициенты приёмки WB на ближайшие дни по складам и типам упаковки
                        {data?.fetched_at && (
                            <span style={{ color: 'var(--color-text-muted)' }}> · обновлено {formatDateTime(data.fetched_at)}</span>
                        )}
                    </p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={handleExport} disabled={rows.length === 0}>📥 Excel</button>
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
                    placeholder="Поиск склада..."
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    style={{ maxWidth: 260 }}
                />
                <div style={{ display: 'flex', gap: 14, marginLeft: 'auto', fontSize: 13, color: 'var(--color-text-muted)' }}>
                    <span><span style={{ color: 'var(--color-success)' }}>●</span> бесплатно</span>
                    <span><span style={{ color: 'var(--color-warning)' }}>●</span> платно (×N)</span>
                    <span><span style={{ color: 'var(--color-danger)' }}>●</span> закрыто</span>
                </div>
            </div>

            {/* Сетка */}
            {rows.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>📅</div>
                    <p style={{ marginBottom: 4 }}>Нет данных по лимитам приёмки.</p>
                    <p style={{ fontSize: 13 }}>Проверьте, что WB-ключ имеет доступ «Поставки», и нажмите «Обновить».</p>
                </div>
            ) : (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                    <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>
                        <thead>
                            <tr>
                                <th style={{ position: 'sticky', left: 0, background: 'var(--color-bg-card)', textAlign: 'left', padding: '10px 12px', zIndex: 1, minWidth: 200 }}>Склад</th>
                                <th style={{ padding: '10px 8px', textAlign: 'center', minWidth: 60 }}>Тип</th>
                                {dates.map(d => (
                                    <th key={d} style={{ padding: '10px 6px', textAlign: 'center', minWidth: 44, color: 'var(--color-text-muted)', fontWeight: 600 }} title={formatDate(d)}>
                                        {shortDate(d)}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((w, i) => {
                                const byDate = new Map(w.days.map(d => [d.date, d]));
                                return (
                                    <tr key={`${w.warehouse_id}-${w.box_type}-${i}`} style={{ borderTop: '1px solid var(--color-border)' }}>
                                        <td style={{ position: 'sticky', left: 0, background: 'var(--color-bg-card)', padding: '8px 12px', fontWeight: 500 }} title={w.warehouse_name}>
                                            {w.canonical_name}
                                        </td>
                                        <td style={{ padding: '8px', textAlign: 'center' }}>
                                            <span className="badge badge-secondary">{BOX_LABEL[w.box_type as AcceptanceBoxType] ?? w.box_type}</span>
                                        </td>
                                        {dates.map(date => {
                                            const day = byDate.get(date);
                                            if (!day) {
                                                return <td key={date} style={{ textAlign: 'center', color: 'var(--color-text-dim)' }}>—</td>;
                                            }
                                            const paid = !day.is_free && !day.is_closed;
                                            const color = day.is_closed ? 'var(--color-danger)' : day.is_free ? 'var(--color-success)' : 'var(--color-warning)';
                                            const label = day.is_closed ? '✕' : day.is_free ? '✓' : `×${day.coefficient}`;
                                            const status = day.is_closed ? 'Закрыто' : day.is_free ? 'Бесплатно' : `Платно ×${day.coefficient}`;
                                            const tip = `${formatDate(date)} · ${status}`
                                                + (day.storage_coef != null ? ` · хранение ×${day.storage_coef}` : '')
                                                + (day.delivery_coef != null ? ` · логистика ×${day.delivery_coef}` : '');
                                            return (
                                                <td key={date} style={{ textAlign: 'center', padding: '6px 4px' }} title={tip}>
                                                    <span style={{ color, fontWeight: paid ? 700 : 600 }}>{label}</span>
                                                </td>
                                            );
                                        })}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
