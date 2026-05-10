'use client';
import { useEffect, useState, useCallback, useMemo } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { BoxMultiplicityRow } from '@/types/api';

export default function BoxMultiplicityPage() {
    useParams() as { slug: string };  // route guard — slug used by API client
    const [rows, setRows] = useState<BoxMultiplicityRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [editingNm, setEditingNm] = useState<number | null>(null);
    const [editValue, setEditValue] = useState('');
    const [saving, setSaving] = useState(false);

    const load = useCallback(async () => {
        try {
            setLoading(true);
            setError(null);
            const resp = await api.getBoxMultiplicity();
            setRows(resp.items);
        } catch (e: any) {
            setError(e?.message || 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const startEdit = (row: BoxMultiplicityRow) => {
        setEditingNm(row.nm_id);
        setEditValue(row.box_qty_override !== null ? String(row.box_qty_override) : '');
    };

    const cancelEdit = () => { setEditingNm(null); setEditValue(''); };

    const saveEdit = async (nmId: number) => {
        const trimmed = editValue.trim();
        const value = trimmed === '' ? null : parseInt(trimmed, 10);
        if (value !== null && (Number.isNaN(value) || value < 1)) {
            alert('Кратность должна быть положительным числом или пустой');
            return;
        }
        try {
            setSaving(true);
            const updated = await api.setBoxMultiplicity(nmId, value);
            setRows(prev => prev.map(r => r.nm_id === nmId ? updated : r));
            cancelEdit();
        } catch (e: any) {
            alert(e?.message || 'Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    const stats = useMemo(() => {
        const total = rows.length;
        const withEffective = rows.filter(r => r.effective_box_qty !== null).length;
        const withManual = rows.filter(r => r.box_qty_override !== null).length;
        const fromVehicle = rows.filter(r => r.box_qty_override === null && r.box_qty_from_vehicle !== null).length;
        const empty = total - withEffective;
        return { total, withEffective, withManual, fromVehicle, empty };
    }, [rows]);

    const columns: Column[] = [
        { key: 'vendor_code', label: 'Артикул', width: '160px' },
        { key: 'nm_id', label: 'nm_id', format: 'number', align: 'right', width: '100px' },
        { key: 'brand', label: 'Бренд', width: '120px' },
        { key: 'subject', label: 'Предмет', width: '140px' },
        {
            key: 'box_qty_from_vehicle',
            label: 'Из машины',
            align: 'right',
            getValue: (r: BoxMultiplicityRow) => r.box_qty_from_vehicle ?? -1,
            render: (_v: unknown, r: BoxMultiplicityRow) => {
                if (r.box_qty_from_vehicle === null) {
                    return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
                }
                return (
                    <div style={{ textAlign: 'right' }}>
                        <div style={{ fontWeight: 500 }}>{formatNumber(r.box_qty_from_vehicle)} шт</div>
                        {r.vehicle_order_no && (
                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                {r.vehicle_order_no}
                                {r.vehicle_received_at && ` · ${formatDate(r.vehicle_received_at)}`}
                            </div>
                        )}
                    </div>
                );
            },
        },
        {
            key: 'box_qty_from_factory',
            label: 'Из заказа',
            align: 'right',
            format: 'number',
            getValue: (r: BoxMultiplicityRow) => r.box_qty_from_factory ?? -1,
            render: (_v: unknown, r: BoxMultiplicityRow) => (
                <span style={{ color: r.box_qty_from_factory === null ? 'var(--color-text-dim)' : undefined }}>
                    {r.box_qty_from_factory === null ? '—' : `${formatNumber(r.box_qty_from_factory)} шт`}
                </span>
            ),
        },
        {
            key: 'box_qty_override',
            label: 'Ручной override',
            align: 'right',
            width: '180px',
            getValue: (r: BoxMultiplicityRow) => r.box_qty_override ?? -1,
            render: (_v: unknown, r: BoxMultiplicityRow) => {
                if (editingNm === r.nm_id) {
                    return (
                        <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end', alignItems: 'center' }}>
                            <input
                                type="text"
                                inputMode="numeric"
                                value={editValue}
                                onChange={e => setEditValue(e.target.value)}
                                placeholder="пусто = очистить"
                                autoFocus
                                style={{
                                    width: 90, padding: '4px 8px', fontSize: 13,
                                    border: '1px solid var(--color-border)', borderRadius: 6,
                                }}
                                onKeyDown={e => {
                                    if (e.key === 'Enter') saveEdit(r.nm_id);
                                    else if (e.key === 'Escape') cancelEdit();
                                }}
                            />
                            <button
                                className="btn btn-success btn-sm"
                                disabled={saving}
                                onClick={() => saveEdit(r.nm_id)}
                                title="Сохранить (Enter)"
                            >✓</button>
                            <button
                                className="btn btn-secondary btn-sm"
                                disabled={saving}
                                onClick={cancelEdit}
                                title="Отмена (Esc)"
                            >✕</button>
                        </div>
                    );
                }
                return (
                    <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', alignItems: 'center' }}>
                        {r.box_qty_override !== null && (
                            <span style={{ fontWeight: 500 }}>{formatNumber(r.box_qty_override)} шт</span>
                        )}
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => startEdit(r)}
                            title={r.box_qty_override !== null ? 'Изменить' : 'Указать вручную'}
                        >
                            {r.box_qty_override !== null ? '✏️' : '➕'}
                        </button>
                    </div>
                );
            },
        },
        {
            key: 'effective_box_qty',
            label: 'Применяется',
            align: 'right',
            getValue: (r: BoxMultiplicityRow) => r.effective_box_qty ?? -1,
            render: (_v: unknown, r: BoxMultiplicityRow) => {
                if (r.effective_box_qty === null) {
                    return <span style={{ color: 'var(--color-warning)', fontSize: 12 }}>не задана</span>;
                }
                const source = r.box_qty_override !== null
                    ? 'ручной'
                    : r.box_qty_from_vehicle !== null
                        ? 'из машины'
                        : 'из заказа';
                return (
                    <div style={{ textAlign: 'right' }}>
                        <div style={{ fontWeight: 600, color: 'var(--color-accent)' }}>
                            {formatNumber(r.effective_box_qty)} шт
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{source}</div>
                    </div>
                );
            },
        },
    ];

    if (loading) {
        return <div className="glass-card" style={{ padding: 24 }}>Загрузка...</div>;
    }
    if (error) {
        return (
            <div className="glass-card" style={{ padding: 24, color: 'var(--color-danger)' }}>
                {error}
            </div>
        );
    }

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">📦 Кратность коробок</h1>
                    <p className="page-subtitle">
                        Кратность из последней принятой машины + ручной override.
                        Используется в распределении при создании сборки.
                    </p>
                </div>
            </div>

            <div className="stats-grid" style={{ marginBottom: 16 }}>
                <div className="glass-card" style={{ padding: '14px 18px', textAlign: 'center' }}>
                    <div style={{ fontSize: 26, fontWeight: 700 }}>{formatNumber(stats.total)}</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Всего SKU</div>
                </div>
                <div className="glass-card" style={{ padding: '14px 18px', textAlign: 'center' }}>
                    <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--color-success)' }}>
                        {formatNumber(stats.withEffective)}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>С кратностью</div>
                </div>
                <div className="glass-card" style={{ padding: '14px 18px', textAlign: 'center' }}>
                    <div style={{ fontSize: 26, fontWeight: 700 }}>{formatNumber(stats.fromVehicle)}</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Из машины</div>
                </div>
                <div className="glass-card" style={{ padding: '14px 18px', textAlign: 'center' }}>
                    <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--color-accent)' }}>
                        {formatNumber(stats.withManual)}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Ручной ввод</div>
                </div>
                <div className="glass-card" style={{ padding: '14px 18px', textAlign: 'center' }}>
                    <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--color-warning)' }}>
                        {formatNumber(stats.empty)}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Не задана</div>
                </div>
            </div>

            {rows.length === 0 ? (
                <div className="empty-state">
                    <div className="empty-state-icon">📦</div>
                    <div>Нет SKU с привязкой к WB</div>
                </div>
            ) : (
                <TanStackDataTable
                    columns={columns}
                    data={rows}
                    exportName="box_multiplicity"
                    emptyText="Нет данных"
                    pageSize={100}
                />
            )}
        </div>
    );
}
