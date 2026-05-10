'use client';
import { useEffect, useState, useCallback, useMemo } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { BoxMultiplicityRow, BoxMultiplicityBulkItem, BoxMultiplicityPerWarehouseRow } from '@/types/api';
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

    // Per-RF expansion state: nm_id → expanded?
    const [expandedNm, setExpandedNm] = useState<Set<number>>(new Set());
    // Per-RF inline edit: "{nm_id}:{warehouse_id}" → string value
    const [perRfEdit, setPerRfEdit] = useState<Record<string, string>>({});

    // Filters
    const [brandFilter, setBrandFilter] = useState('');
    const [subjectFilter, setSubjectFilter] = useState('');
    const [stockFilter, setStockFilter] = useState<StockFilter>('all');
    const [search, setSearch] = useState('');

    // Bulk-paste inline editor (как в приёмке) — 5+ редактируемых строк.
    type PasteRow = { barcode: string; ppb: string; use: string };
    const emptyPasteRow = (): PasteRow => ({ barcode: '', ppb: '', use: '' });
    const [pasteOpen, setPasteOpen] = useState(false);
    const [pasteRows, setPasteRows] = useState<PasteRow[]>(() => Array.from({ length: 5 }, emptyPasteRow));
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

    // ─── Bulk paste (inline editor, не модалка) ───────────────────────────
    // Конвертируем текущие inline-строки в TSV → парсер → распознанные items.
    const pasteText = useMemo(
        () => pasteRows
            .filter(r => r.barcode.trim() || r.ppb.trim() || r.use.trim())
            .map(r => `${r.barcode}\t${r.ppb}\t${r.use}`)
            .join('\n'),
        [pasteRows],
    );
    const parsedPaste = useMemo(() => parseBoxMultiplicityPaste(pasteText), [pasteText]);

    const openPaste = () => {
        setPasteOpen(true);
        setPasteRows(Array.from({ length: 5 }, emptyPasteRow));
        setPasteResult(null);
    };

    const closePaste = () => {
        setPasteOpen(false);
        setPasteRows(Array.from({ length: 5 }, emptyPasteRow));
        setPasteResult(null);
    };

    const updatePasteRow = (idx: number, field: keyof PasteRow, value: string) => {
        setPasteRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: value };
            // Авто-добавляем буферные строки если последняя строка тронута.
            const last = next[next.length - 1];
            if (last.barcode.trim() || last.ppb.trim() || last.use.trim()) {
                next.push(emptyPasteRow());
            }
            return next;
        });
    };

    // Excel-paste: TSV в первую пустую строку → распарсить и заполнить начиная отсюда.
    const handleRowsPaste = (e: React.ClipboardEvent, startIdx: number) => {
        const text = e.clipboardData.getData('text/plain');
        if (!text.includes('\t') && !text.includes('\n')) return;
        e.preventDefault();
        const lines = text.replace(/\r\n/g, '\n').split('\n').filter(l => l.length > 0);
        if (lines.length === 0) return;
        const parsed: PasteRow[] = lines.map(l => {
            const cols = l.split('\t');
            return {
                barcode: (cols[0] ?? '').trim(),
                ppb: (cols[1] ?? '').trim(),
                use: (cols[2] ?? '').trim(),
            };
        });
        setPasteRows(prev => {
            const next = [...prev];
            // Заменяем строки начиная с startIdx
            for (let i = 0; i < parsed.length; i++) {
                next[startIdx + i] = parsed[i];
            }
            // Гарантируем 2 буферные пустые строки в конце
            while (next.length < startIdx + parsed.length + 2 ||
                   (next[next.length - 1].barcode || next[next.length - 1].ppb || next[next.length - 1].use)) {
                next.push(emptyPasteRow());
                if (next.length > startIdx + parsed.length + 5) break;  // safety
            }
            return next;
        });
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
            if (resp.updated.length > 0) {
                const updMap = new Map(resp.updated.map(r => [r.nm_id, r]));
                setRows(prev => prev.map(r => updMap.get(r.nm_id) || r));
            }
            setPasteResult({
                matched: resp.matched_count,
                updated: resp.updated.length,
                notFound: resp.not_found,
            });
            setPasteRows(Array.from({ length: 5 }, emptyPasteRow));
        } catch (e: any) {
            alert(e?.message || 'Ошибка применения');
        } finally {
            setPasteApplying(false);
        }
    };

    // ─── Per-RF handlers ───────────────────────────────────────────────────
    const toggleExpand = (nmId: number) => {
        setExpandedNm(prev => {
            const next = new Set(prev);
            if (next.has(nmId)) next.delete(nmId);
            else next.add(nmId);
            return next;
        });
    };

    const savePerRfPpb = async (row: BoxMultiplicityRow, wh: BoxMultiplicityPerWarehouseRow) => {
        const key = `${row.nm_id}:${wh.warehouse_id}`;
        const raw = perRfEdit[key] ?? '';
        const trimmed = raw.trim();
        const value = trimmed === '' ? null : parseInt(trimmed, 10);
        if (value !== null && (Number.isNaN(value) || value < 1)) {
            alert('Кратность должна быть положительным числом или пустой');
            return;
        }
        try {
            const updated = await api.patchPerWarehouseBoxMultiplicity(
                row.barcode, wh.warehouse_id, { box_qty: value },
            );
            setRows(prev => prev.map(r => r.nm_id === row.nm_id ? updated : r));
            setPerRfEdit(prev => {
                const next = { ...prev };
                delete next[key];
                return next;
            });
        } catch (e: any) {
            alert(e?.message || 'Ошибка сохранения');
        }
    };

    const togglePerRfUse = async (row: BoxMultiplicityRow, wh: BoxMultiplicityPerWarehouseRow, next: boolean) => {
        // Optimistic
        setRows(prev => prev.map(r => {
            if (r.nm_id !== row.nm_id) return r;
            return {
                ...r,
                per_warehouse: r.per_warehouse.map(p =>
                    p.warehouse_id === wh.warehouse_id ? { ...p, use_box_multiplicity: next } : p,
                ),
            };
        }));
        try {
            const updated = await api.patchPerWarehouseBoxMultiplicity(
                row.barcode, wh.warehouse_id, { use_box_multiplicity: next },
            );
            setRows(prev => prev.map(r => r.nm_id === row.nm_id ? updated : r));
        } catch (e: any) {
            // rollback
            setRows(prev => prev.map(r => {
                if (r.nm_id !== row.nm_id) return r;
                return {
                    ...r,
                    per_warehouse: r.per_warehouse.map(p =>
                        p.warehouse_id === wh.warehouse_id ? { ...p, use_box_multiplicity: !next } : p,
                    ),
                };
            }));
            alert(e?.message || 'Ошибка сохранения');
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
            key: 'per_warehouse',
            label: 'По ФФ-складам',
            width: '320px',
            getValue: (r: BoxMultiplicityRow) =>
                r.per_warehouse.filter(p => p.box_qty !== null).length,
            render: (_v: unknown, r: BoxMultiplicityRow) => {
                const expanded = expandedNm.has(r.nm_id);
                const overridesCount = r.per_warehouse.filter(p => p.box_qty !== null).length;
                const usedOff = r.per_warehouse.filter(p => !p.use_box_multiplicity).length;

                if (!expanded) {
                    return (
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => toggleExpand(r.nm_id)}
                            style={{ width: '100%', textAlign: 'left', fontSize: 12 }}
                        >
                            ▸ {r.per_warehouse.length} ФФ
                            {overridesCount > 0 && (
                                <span style={{ color: 'var(--color-accent)', marginLeft: 6 }}>
                                    · override: {overridesCount}
                                </span>
                            )}
                            {usedOff > 0 && (
                                <span style={{ color: 'var(--color-warning)', marginLeft: 6 }}>
                                    · откл: {usedOff}
                                </span>
                            )}
                        </button>
                    );
                }
                return (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => toggleExpand(r.nm_id)}
                            style={{ fontSize: 12, marginBottom: 4 }}
                        >▾ Свернуть</button>
                        {r.per_warehouse.map(wh => {
                            const editKey = `${r.nm_id}:${wh.warehouse_id}`;
                            const editVal = perRfEdit[editKey];
                            const showEdit = editVal !== undefined;
                            return (
                                <div
                                    key={wh.warehouse_id}
                                    style={{
                                        display: 'grid', gridTemplateColumns: '1fr auto auto auto',
                                        alignItems: 'center', gap: 6, fontSize: 12,
                                        padding: '4px 6px', borderRadius: 6,
                                        background: 'var(--color-bg)',
                                    }}
                                >
                                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {wh.warehouse_name}
                                        {wh.rf_stock > 0 && (
                                            <span style={{ color: 'var(--color-success)', marginLeft: 4 }}>
                                                ({formatNumber(wh.rf_stock)})
                                            </span>
                                        )}
                                    </span>
                                    {showEdit ? (
                                        <input
                                            type="text"
                                            inputMode="numeric"
                                            value={editVal}
                                            onChange={e => setPerRfEdit(p => ({ ...p, [editKey]: e.target.value }))}
                                            onKeyDown={e => {
                                                if (e.key === 'Enter') savePerRfPpb(r, wh);
                                                else if (e.key === 'Escape') {
                                                    setPerRfEdit(p => {
                                                        const next = { ...p };
                                                        delete next[editKey];
                                                        return next;
                                                    });
                                                }
                                            }}
                                            placeholder="—"
                                            autoFocus
                                            style={{
                                                width: 60, padding: '2px 6px', fontSize: 12,
                                                border: '1px solid var(--color-border)', borderRadius: 4,
                                                textAlign: 'right',
                                            }}
                                        />
                                    ) : (
                                        <span
                                            onClick={() => setPerRfEdit(p => ({
                                                ...p, [editKey]: wh.box_qty !== null ? String(wh.box_qty) : '',
                                            }))}
                                            style={{
                                                cursor: 'pointer', textAlign: 'right',
                                                color: wh.box_qty !== null ? 'var(--color-accent)' : 'var(--color-text-dim)',
                                                fontWeight: wh.box_qty !== null ? 600 : 400,
                                                minWidth: 50, padding: '2px 4px',
                                            }}
                                            title={wh.box_qty !== null ? 'Изменить' : 'Задать'}
                                        >
                                            {wh.box_qty !== null ? `${wh.box_qty} шт` : '—'}
                                        </span>
                                    )}
                                    {showEdit ? (
                                        <button
                                            className="btn btn-success btn-sm"
                                            onClick={() => savePerRfPpb(r, wh)}
                                            style={{ padding: '2px 6px' }}
                                        >✓</button>
                                    ) : (
                                        <span style={{ width: 24 }} />
                                    )}
                                    <input
                                        type="checkbox"
                                        checked={wh.use_box_multiplicity}
                                        onChange={e => togglePerRfUse(r, wh, e.target.checked)}
                                        title={wh.use_box_multiplicity
                                            ? 'Учитывается при сборке с этого ФФ'
                                            : 'Игнорируется — раздаём без округления'}
                                        style={{ width: 14, height: 14, cursor: 'pointer' }}
                                    />
                                </div>
                            );
                        })}
                    </div>
                );
            },
            exportValue: (r: BoxMultiplicityRow) => r.per_warehouse
                .filter(p => p.box_qty !== null)
                .map(p => `${p.warehouse_name}:${p.box_qty}${p.use_box_multiplicity ? '' : '*'}`)
                .join(' | '),
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

            {/* ─── Bulk paste inline editor ───────────────────────────────── */}
            {pasteOpen && (
                <div className="glass-card" style={{ padding: 20, marginTop: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
                        <div>
                            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>📋 Массовое редактирование</h3>
                            <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                вставьте из Excel: <strong>Баркод, Кратность, Учитывать</strong>
                                {' · '}кратность: число или <code>-</code>/<code>0</code> = очистить, пусто = не менять
                                {' · '}учитывать: <code>да/нет</code>/<code>+/-</code>/<code>1/0</code>, пусто = не менять
                            </p>
                        </div>
                        <button className="btn btn-secondary btn-sm" onClick={closePaste} disabled={pasteApplying}>✕ Свернуть</button>
                    </div>

                    {pasteResult && (
                        <div style={{
                            padding: '10px 14px', marginBottom: 12,
                            borderRadius: 8, background: 'rgba(52, 199, 89, 0.08)',
                            borderLeft: '3px solid var(--color-success)', fontSize: 13,
                        }}>
                            ✅ Обновлено <strong>{pasteResult.updated}</strong> SKU
                            {' · '}найдено по barcode <strong>{pasteResult.matched}</strong>
                            {pasteResult.notFound.length > 0 && (
                                <details style={{ marginTop: 6, fontSize: 12 }}>
                                    <summary style={{ cursor: 'pointer', color: 'var(--color-warning)' }}>
                                        Не найдено barcode ({pasteResult.notFound.length})
                                    </summary>
                                    <div style={{ fontFamily: 'monospace', marginTop: 4, maxHeight: 80, overflowY: 'auto' }}>
                                        {pasteResult.notFound.join(', ')}
                                    </div>
                                </details>
                            )}
                            <button
                                className="btn btn-secondary btn-sm"
                                style={{ marginTop: 8 }}
                                onClick={() => setPasteResult(null)}
                            >Ещё одна вставка</button>
                        </div>
                    )}

                    <div style={{ overflowX: 'auto' }}>
                        <table style={{ width: '100%', fontSize: 13 }}>
                            <thead>
                                <tr style={{ color: 'var(--color-text-muted)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.05 }}>
                                    <th style={{ textAlign: 'left', padding: '6px 8px', width: 40 }}>#</th>
                                    <th style={{ textAlign: 'left', padding: '6px 8px' }}>Баркод</th>
                                    <th style={{ textAlign: 'left', padding: '6px 8px', width: 140 }}>Кратность</th>
                                    <th style={{ textAlign: 'left', padding: '6px 8px', width: 140 }}>Учитывать</th>
                                    <th style={{ textAlign: 'center', padding: '6px 8px', width: 60 }}>—</th>
                                </tr>
                            </thead>
                            <tbody>
                                {pasteRows.map((row, i) => {
                                    const filled = row.barcode.trim() || row.ppb.trim() || row.use.trim();
                                    return (
                                        <tr key={i} style={{ borderTop: '1px solid var(--color-border)' }}>
                                            <td style={{ padding: '6px 8px', color: 'var(--color-text-muted)' }}>{i + 1}</td>
                                            <td style={{ padding: '6px 8px' }}>
                                                <input
                                                    type="text"
                                                    value={row.barcode}
                                                    onChange={e => updatePasteRow(i, 'barcode', e.target.value)}
                                                    onPaste={e => handleRowsPaste(e, i)}
                                                    placeholder="Баркод"
                                                    style={{
                                                        width: '100%', padding: '6px 10px', fontSize: 13,
                                                        border: '1px solid var(--color-border)', borderRadius: 8,
                                                        fontFamily: 'monospace',
                                                    }}
                                                />
                                            </td>
                                            <td style={{ padding: '6px 8px' }}>
                                                <input
                                                    type="text"
                                                    inputMode="numeric"
                                                    value={row.ppb}
                                                    onChange={e => updatePasteRow(i, 'ppb', e.target.value)}
                                                    onPaste={e => handleRowsPaste(e, i)}
                                                    placeholder="0"
                                                    style={{
                                                        width: '100%', padding: '6px 10px', fontSize: 13,
                                                        border: '1px solid var(--color-border)', borderRadius: 8,
                                                        textAlign: 'right',
                                                    }}
                                                />
                                            </td>
                                            <td style={{ padding: '6px 8px' }}>
                                                <input
                                                    type="text"
                                                    value={row.use}
                                                    onChange={e => updatePasteRow(i, 'use', e.target.value)}
                                                    onPaste={e => handleRowsPaste(e, i)}
                                                    placeholder="да / нет"
                                                    style={{
                                                        width: '100%', padding: '6px 10px', fontSize: 13,
                                                        border: '1px solid var(--color-border)', borderRadius: 8,
                                                    }}
                                                />
                                            </td>
                                            <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                                                {filled && pasteRows.length > 1 && (
                                                    <button
                                                        className="btn btn-secondary btn-sm"
                                                        onClick={() => setPasteRows(prev => prev.filter((_, j) => j !== i))}
                                                        title="Удалить строку"
                                                        style={{ padding: '2px 8px' }}
                                                    >✕</button>
                                                )}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>

                    {/* Errors summary */}
                    {parsedPaste.errors.length > 0 && (
                        <div style={{
                            marginTop: 8, padding: '8px 12px', borderRadius: 8,
                            background: 'rgba(255, 59, 48, 0.06)', borderLeft: '3px solid var(--color-danger)',
                            fontSize: 12, color: 'var(--color-danger)',
                        }}>
                            <strong>Ошибки в строках:</strong>
                            <ul style={{ margin: '4px 0 0 18px', padding: 0 }}>
                                {parsedPaste.errors.slice(0, 5).map((err, i) => (
                                    <li key={i}>строка {err.line}: {err.reason}</li>
                                ))}
                                {parsedPaste.errors.length > 5 && <li>…и ещё {parsedPaste.errors.length - 5}</li>}
                            </ul>
                        </div>
                    )}

                    <div style={{ display: 'flex', gap: 8, marginTop: 16, justifyContent: 'flex-end', alignItems: 'center' }}>
                        <span style={{ marginRight: 'auto', fontSize: 12, color: 'var(--color-text-muted)' }}>
                            Распознано: <strong style={{ color: 'var(--color-success)' }}>{parsedPaste.rows.length}</strong>
                            {parsedPaste.errors.length > 0 && (
                                <> · ошибок: <strong style={{ color: 'var(--color-danger)' }}>{parsedPaste.errors.length}</strong></>
                            )}
                        </span>
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
                </div>
            )}
        </div>
    );
}
