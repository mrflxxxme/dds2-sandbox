'use client';
import { useEffect, useState, useCallback, useMemo } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { BoxMultiplicityRow, BoxMultiplicityBulkItem } from '@/types/api';
import { parseBoxMultiplicityPaste } from '@/lib/utils/boxMultiplicityPaste';

type StockFilter = 'all' | 'rf' | 'in_assembly' | 'in_transit' | 'no_wb' | 'no_stock';

export default function BoxMultiplicityPage() {
    useParams() as { slug: string };  // route guard — slug used by API client
    const [rows, setRows] = useState<BoxMultiplicityRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [editingNm, setEditingNm] = useState<number | null>(null);
    const [editValue, setEditValue] = useState('');
    const [saving, setSaving] = useState(false);

    // Filters
    const [brandFilter, setBrandFilter] = useState('');
    const [subjectFilter, setSubjectFilter] = useState('');
    const [stockFilter, setStockFilter] = useState<StockFilter>('all');
    const [search, setSearch] = useState('');

    // Bulk-paste modal
    const [pasteOpen, setPasteOpen] = useState(false);
    const [pasteText, setPasteText] = useState('');
    const [pasteApplying, setPasteApplying] = useState(false);
    const [pasteResult, setPasteResult] = useState<{ matched: number; updated: number; notFound: string[] } | null>(null);

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

    // ─── Bulk paste ────────────────────────────────────────────────────────
    const parsedPaste = useMemo(() => parseBoxMultiplicityPaste(pasteText), [pasteText]);

    const openPaste = () => {
        setPasteOpen(true);
        setPasteText('');
        setPasteResult(null);
    };

    const closePaste = () => {
        setPasteOpen(false);
        setPasteText('');
        setPasteResult(null);
    };

    const applyPaste = async () => {
        if (parsedPaste.rows.length === 0) return;
        setPasteApplying(true);
        try {
            const items: BoxMultiplicityBulkItem[] = parsedPaste.rows.map(r => {
                const item: BoxMultiplicityBulkItem = { barcode: r.barcode };
                if (r.box_qty_override !== undefined) item.box_qty_override = r.box_qty_override;
                if (r.use_box_multiplicity !== undefined) item.use_box_multiplicity = r.use_box_multiplicity;
                return item;
            });
            const resp = await api.bulkBoxMultiplicity(items);
            // Merge updated rows back into state
            if (resp.updated.length > 0) {
                const updMap = new Map(resp.updated.map(r => [r.nm_id, r]));
                setRows(prev => prev.map(r => updMap.get(r.nm_id) || r));
            }
            setPasteResult({
                matched: resp.matched_count,
                updated: resp.updated.length,
                notFound: resp.not_found,
            });
            setPasteText('');
        } catch (e: any) {
            alert(e?.message || 'Ошибка применения');
        } finally {
            setPasteApplying(false);
        }
    };

    const toggleUse = async (nmId: number, next: boolean) => {
        // Optimistic flip — откат при ошибке
        setRows(prev => prev.map(r => r.nm_id === nmId ? { ...r, use_box_multiplicity: next } : r));
        try {
            const updated = await api.patchBoxMultiplicity(nmId, { use_box_multiplicity: next });
            setRows(prev => prev.map(r => r.nm_id === nmId ? updated : r));
        } catch (e: any) {
            setRows(prev => prev.map(r => r.nm_id === nmId ? { ...r, use_box_multiplicity: !next } : r));
            alert(e?.message || 'Ошибка сохранения');
        }
    };

    // ─── Filter options (учитывают друг друга — если выбран предмет, бренды
    // сужаются до тех что встречаются в этом предмете и наоборот) ──────────
    const brandOptions = useMemo(() => {
        const set = new Set<string>();
        for (const r of rows) {
            if (subjectFilter && r.subject !== subjectFilter) continue;
            if (r.brand) set.add(r.brand);
        }
        return Array.from(set).sort();
    }, [rows, subjectFilter]);

    const subjectOptions = useMemo(() => {
        const set = new Set<string>();
        for (const r of rows) {
            if (brandFilter && r.brand !== brandFilter) continue;
            if (r.subject) set.add(r.subject);
        }
        return Array.from(set).sort();
    }, [rows, brandFilter]);

    // ─── Filtered rows ─────────────────────────────────────────────────────
    const filteredRows = useMemo(() => {
        const q = search.trim().toLowerCase();
        return rows.filter(r => {
            if (brandFilter && r.brand !== brandFilter) return false;
            if (subjectFilter && r.subject !== subjectFilter) return false;
            if (q) {
                const haystack = `${r.vendor_code || ''} ${r.barcode} ${r.nm_id}`.toLowerCase();
                if (!haystack.includes(q)) return false;
            }
            switch (stockFilter) {
                case 'rf': return r.rf_stock > 0;
                case 'in_assembly': return r.in_assembly > 0;
                case 'in_transit': return r.in_transit > 0;
                case 'no_wb': return r.wb_stock === 0;
                case 'no_stock': return r.rf_stock === 0 && r.wb_stock === 0 && r.in_assembly === 0 && r.in_transit === 0;
                default: return true;
            }
        });
    }, [rows, brandFilter, subjectFilter, stockFilter, search]);

    // KPI и chip-каунты — по scope бренд+предмет (без чипов остатков и без search),
    // чтобы при выборе предмета шапка показывала числа в рамках этого предмета.
    const scopeRows = useMemo(() => rows.filter(r => {
        if (brandFilter && r.brand !== brandFilter) return false;
        if (subjectFilter && r.subject !== subjectFilter) return false;
        return true;
    }), [rows, brandFilter, subjectFilter]);

    const stats = useMemo(() => {
        const total = scopeRows.length;
        const withEffective = scopeRows.filter(r => r.effective_box_qty !== null).length;
        const withManual = scopeRows.filter(r => r.box_qty_override !== null).length;
        const fromVehicle = scopeRows.filter(r => r.box_qty_override === null && r.box_qty_from_vehicle !== null).length;
        const empty = total - withEffective;
        const active = scopeRows.filter(r => r.use_box_multiplicity && r.effective_box_qty !== null).length;
        const filterCounts = {
            rf: scopeRows.filter(r => r.rf_stock > 0).length,
            in_assembly: scopeRows.filter(r => r.in_assembly > 0).length,
            in_transit: scopeRows.filter(r => r.in_transit > 0).length,
            no_wb: scopeRows.filter(r => r.wb_stock === 0).length,
            no_stock: scopeRows.filter(r => r.rf_stock === 0 && r.wb_stock === 0 && r.in_assembly === 0 && r.in_transit === 0).length,
        };
        return { total, withEffective, withManual, fromVehicle, empty, active, filterCounts };
    }, [scopeRows]);

    const columns: Column[] = [
        {
            key: 'vendor_code',
            label: 'Артикул',
            width: '240px',
            render: (_v: unknown, r: BoxMultiplicityRow) => (
                <span style={{ wordBreak: 'break-word', whiteSpace: 'normal', display: 'inline-block', maxWidth: 230 }}>
                    {r.vendor_code || <span style={{ color: 'var(--color-text-dim)' }}>—</span>}
                </span>
            ),
        },
        {
            key: 'barcode',
            label: 'Barcode',
            width: '140px',
            render: (_v: unknown, r: BoxMultiplicityRow) => (
                <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.barcode}</span>
            ),
        },
        {
            key: 'nm_id',
            label: 'nm_id',
            align: 'right',
            width: '110px',
            // НЕ format: 'number' — иначе ru-RU локаль рендерит ID с пробелами/запятой
            render: (_v: unknown, r: BoxMultiplicityRow) => (
                <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.nm_id}</span>
            ),
            exportValue: (r: BoxMultiplicityRow) => r.nm_id,
        },
        { key: 'brand', label: 'Бренд', width: '120px' },
        { key: 'subject', label: 'Предмет', width: '140px' },
        {
            key: 'rf_stock',
            label: 'Остатки',
            align: 'right',
            width: '180px',
            getValue: (r: BoxMultiplicityRow) => r.rf_stock + r.wb_stock + r.in_assembly + r.in_transit,
            render: (_v: unknown, r: BoxMultiplicityRow) => {
                const parts: Array<{ label: string; value: number; color: string }> = [];
                if (r.rf_stock > 0) parts.push({ label: 'ФФ', value: r.rf_stock, color: 'var(--color-success)' });
                if (r.in_assembly > 0) parts.push({ label: 'сборка', value: r.in_assembly, color: 'var(--color-warning)' });
                if (r.in_transit > 0) parts.push({ label: 'путь', value: r.in_transit, color: 'var(--color-accent)' });
                if (r.wb_stock > 0) parts.push({ label: 'WB', value: r.wb_stock, color: 'var(--color-text)' });
                if (parts.length === 0) {
                    return <span style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>—</span>;
                }
                return (
                    <div style={{ fontSize: 12, lineHeight: 1.4 }}>
                        {parts.map(p => (
                            <div key={p.label} style={{ color: p.color }}>
                                {p.label}: <strong>{formatNumber(p.value)}</strong>
                            </div>
                        ))}
                    </div>
                );
            },
            exportValue: (r: BoxMultiplicityRow) =>
                `ФФ:${r.rf_stock} | сборка:${r.in_assembly} | путь:${r.in_transit} | WB:${r.wb_stock}`,
        },
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
            key: 'use_box_multiplicity',
            label: 'Учитывать',
            align: 'center',
            width: '110px',
            getValue: (r: BoxMultiplicityRow) => (r.use_box_multiplicity ? 1 : 0),
            render: (_v: unknown, r: BoxMultiplicityRow) => {
                const disabled = r.effective_box_qty === null;
                return (
                    <label
                        title={disabled
                            ? 'Сначала задай кратность (вручную или примем машину)'
                            : (r.use_box_multiplicity
                                ? 'Кратность учитывается при создании сборки'
                                : 'Кратность игнорируется — раздаём без округления')}
                        style={{
                            display: 'inline-flex', alignItems: 'center', gap: 6, cursor: disabled ? 'not-allowed' : 'pointer',
                            opacity: disabled ? 0.4 : 1,
                        }}
                    >
                        <input
                            type="checkbox"
                            checked={r.use_box_multiplicity}
                            disabled={disabled}
                            onChange={e => toggleUse(r.nm_id, e.target.checked)}
                            style={{ width: 16, height: 16, cursor: disabled ? 'not-allowed' : 'pointer' }}
                        />
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                            {r.use_box_multiplicity ? 'да' : 'нет'}
                        </span>
                    </label>
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
                if (!r.use_box_multiplicity) {
                    return (
                        <div style={{ textAlign: 'right' }}>
                            <div style={{ color: 'var(--color-text-dim)', textDecoration: 'line-through' }}>
                                {formatNumber(r.effective_box_qty)} шт
                            </div>
                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>отключено</div>
                        </div>
                    );
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
            <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
                <div>
                    <h1 className="page-title">📦 Кратность коробок</h1>
                    <p className="page-subtitle">
                        Кратность из последней принятой машины + ручной override.
                        Используется в распределении при создании сборки.
                    </p>
                </div>
                <button className="btn btn-primary btn-sm" onClick={openPaste}>
                    📋 Вставить из буфера
                </button>
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
                    <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--color-success)' }}>
                        {formatNumber(stats.active)}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Учитывается</div>
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
                <>
                    {/* Filter row 1: brand + subject + search */}
                    <div className="glass-card" style={{ padding: 12, marginBottom: 12, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                        <select
                            value={brandFilter}
                            onChange={e => setBrandFilter(e.target.value)}
                            style={{ padding: '6px 10px', fontSize: 13, border: '1px solid var(--color-border)', borderRadius: 8, minWidth: 160 }}
                        >
                            <option value="">Все бренды ({brandOptions.length})</option>
                            {brandOptions.map(b => <option key={b} value={b}>{b}</option>)}
                        </select>
                        <select
                            value={subjectFilter}
                            onChange={e => setSubjectFilter(e.target.value)}
                            style={{ padding: '6px 10px', fontSize: 13, border: '1px solid var(--color-border)', borderRadius: 8, minWidth: 160 }}
                        >
                            <option value="">Все предметы ({subjectOptions.length})</option>
                            {subjectOptions.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                        <input
                            type="text"
                            placeholder="Поиск по артикулу / barcode / nm_id"
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                            style={{ padding: '6px 10px', fontSize: 13, border: '1px solid var(--color-border)', borderRadius: 8, flex: 1, minWidth: 200 }}
                        />
                        {(brandFilter || subjectFilter || search || stockFilter !== 'all') && (
                            <button
                                className="btn btn-secondary btn-sm"
                                onClick={() => { setBrandFilter(''); setSubjectFilter(''); setSearch(''); setStockFilter('all'); }}
                            >✕ Сброс</button>
                        )}
                    </div>

                    {/* Filter row 2: stock chips */}
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
                        {([
                            { key: 'all', label: 'Все', count: rows.length },
                            { key: 'rf', label: 'Есть на ФФ', count: stats.filterCounts.rf },
                            { key: 'in_assembly', label: 'В сборке', count: stats.filterCounts.in_assembly },
                            { key: 'in_transit', label: 'В пути', count: stats.filterCounts.in_transit },
                            { key: 'no_wb', label: 'Нет на WB', count: stats.filterCounts.no_wb },
                            { key: 'no_stock', label: 'Нет нигде', count: stats.filterCounts.no_stock },
                        ] as Array<{ key: StockFilter; label: string; count: number }>).map(c => (
                            <button
                                key={c.key}
                                className={`btn btn-sm ${stockFilter === c.key ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setStockFilter(c.key)}
                            >
                                {c.label} ({formatNumber(c.count)})
                            </button>
                        ))}
                    </div>

                    {filteredRows.length === 0 ? (
                        <div className="empty-state">
                            <div className="empty-state-icon">🔍</div>
                            <div>Под текущие фильтры ничего не подходит</div>
                        </div>
                    ) : (
                        <TanStackDataTable
                            columns={columns}
                            data={filteredRows}
                            exportName="box_multiplicity"
                            emptyText="Нет данных"
                            pageSize={100}
                        />
                    )}
                </>
            )}

            {/* ─── Bulk paste modal ─────────────────────────────────────── */}
            {pasteOpen && (
                <div
                    onClick={closePaste}
                    style={{
                        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        zIndex: 1000, padding: 24,
                    }}
                >
                    <div
                        onClick={e => e.stopPropagation()}
                        className="glass-card"
                        style={{ maxWidth: 720, width: '100%', maxHeight: '90vh', overflowY: 'auto', padding: 24 }}
                    >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
                            <div>
                                <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>📋 Массовое редактирование</h2>
                                <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--color-text-muted)' }}>
                                    Вставь TAB-разделённые данные (как из Excel).
                                </p>
                            </div>
                            <button className="btn btn-secondary btn-sm" onClick={closePaste}>✕</button>
                        </div>

                        <div style={{
                            background: 'var(--color-bg)', border: '1px solid var(--color-border)',
                            borderRadius: 8, padding: 12, marginBottom: 12, fontSize: 12,
                            color: 'var(--color-text-muted)', lineHeight: 1.6,
                        }}>
                            <div><strong style={{ color: 'var(--color-text)' }}>Формат колонок:</strong> barcode &lt;TAB&gt; кратность &lt;TAB&gt; учитывать</div>
                            <div>· Кратность: число <code>1–10000</code>, либо <code>-</code>/<code>null</code>/<code>0</code> = очистить, либо пусто = не менять</div>
                            <div>· Учитывать: <code>да/нет</code>, <code>1/0</code>, <code>+/-</code>, <code>true/false</code>, либо пусто = не менять</div>
                            <div>· SKU матчатся по barcode внутри проекта</div>
                        </div>

                        {pasteResult ? (
                            <div className="glass-card" style={{ padding: 16, marginBottom: 12, borderLeft: '3px solid var(--color-success)' }}>
                                <div style={{ fontSize: 14, marginBottom: 8 }}>
                                    ✅ Готово — обновлено <strong>{pasteResult.updated}</strong> SKU,
                                    {' '}найдено по barcode <strong>{pasteResult.matched}</strong>
                                </div>
                                {pasteResult.notFound.length > 0 && (
                                    <details style={{ fontSize: 12 }}>
                                        <summary style={{ cursor: 'pointer', color: 'var(--color-warning)' }}>
                                            Не найдено barcode ({pasteResult.notFound.length})
                                        </summary>
                                        <div style={{ fontFamily: 'monospace', marginTop: 6, maxHeight: 120, overflowY: 'auto' }}>
                                            {pasteResult.notFound.join(', ')}
                                        </div>
                                    </details>
                                )}
                                <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
                                    <button className="btn btn-secondary btn-sm" onClick={() => setPasteResult(null)}>
                                        Ещё одна вставка
                                    </button>
                                    <button className="btn btn-primary btn-sm" onClick={closePaste}>Закрыть</button>
                                </div>
                            </div>
                        ) : (
                            <>
                                <textarea
                                    value={pasteText}
                                    onChange={e => setPasteText(e.target.value)}
                                    placeholder={'2043740032052\t12\tда\n2043788808268\t6\tнет\n2044778634607\t-\t'}
                                    rows={10}
                                    style={{
                                        width: '100%', padding: 10, fontFamily: 'monospace', fontSize: 13,
                                        border: '1px solid var(--color-border)', borderRadius: 8,
                                        resize: 'vertical', boxSizing: 'border-box',
                                    }}
                                    autoFocus
                                />

                                {/* Preview */}
                                {pasteText && (
                                    <div style={{ marginTop: 12, fontSize: 13 }}>
                                        <div style={{ marginBottom: 6 }}>
                                            <span style={{ color: 'var(--color-success)', fontWeight: 600 }}>
                                                ✓ Распознано: {parsedPaste.rows.length}
                                            </span>
                                            {parsedPaste.errors.length > 0 && (
                                                <span style={{ color: 'var(--color-danger)', marginLeft: 12, fontWeight: 600 }}>
                                                    ✗ Ошибки: {parsedPaste.errors.length}
                                                </span>
                                            )}
                                        </div>

                                        {parsedPaste.errors.length > 0 && (
                                            <details style={{ marginBottom: 8 }}>
                                                <summary style={{ cursor: 'pointer', color: 'var(--color-danger)', fontSize: 12 }}>
                                                    Подробности ошибок
                                                </summary>
                                                <div style={{ fontSize: 12, fontFamily: 'monospace', marginTop: 6, maxHeight: 120, overflowY: 'auto' }}>
                                                    {parsedPaste.errors.map((err, i) => (
                                                        <div key={i} style={{ color: 'var(--color-danger)', marginBottom: 2 }}>
                                                            строка {err.line}: {err.reason}
                                                        </div>
                                                    ))}
                                                </div>
                                            </details>
                                        )}

                                        {parsedPaste.rows.length > 0 && (
                                            <details>
                                                <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                    Превью ({Math.min(parsedPaste.rows.length, 20)})
                                                </summary>
                                                <table style={{ width: '100%', fontSize: 12, fontFamily: 'monospace', marginTop: 6 }}>
                                                    <thead>
                                                        <tr style={{ color: 'var(--color-text-muted)' }}>
                                                            <th style={{ textAlign: 'left' }}>barcode</th>
                                                            <th style={{ textAlign: 'right' }}>кратность</th>
                                                            <th style={{ textAlign: 'center' }}>учитывать</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {parsedPaste.rows.slice(0, 20).map((r, i) => (
                                                            <tr key={i}>
                                                                <td>{r.barcode}</td>
                                                                <td style={{ textAlign: 'right' }}>
                                                                    {r.box_qty_override === null
                                                                        ? <span style={{ color: 'var(--color-warning)' }}>очистить</span>
                                                                        : r.box_qty_override !== undefined
                                                                            ? r.box_qty_override
                                                                            : <span style={{ color: 'var(--color-text-dim)' }}>—</span>}
                                                                </td>
                                                                <td style={{ textAlign: 'center' }}>
                                                                    {r.use_box_multiplicity === undefined
                                                                        ? <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                                                                        : r.use_box_multiplicity ? 'да' : 'нет'}
                                                                </td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </details>
                                        )}
                                    </div>
                                )}

                                <div style={{ display: 'flex', gap: 8, marginTop: 16, justifyContent: 'flex-end' }}>
                                    <button className="btn btn-secondary btn-sm" onClick={closePaste} disabled={pasteApplying}>
                                        Отмена
                                    </button>
                                    <button
                                        className="btn btn-primary btn-sm"
                                        onClick={applyPaste}
                                        disabled={pasteApplying || parsedPaste.rows.length === 0}
                                    >
                                        {pasteApplying ? 'Применяю…' : `Применить (${parsedPaste.rows.length})`}
                                    </button>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
