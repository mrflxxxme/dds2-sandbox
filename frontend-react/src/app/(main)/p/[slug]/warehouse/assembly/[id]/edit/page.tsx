'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { AssemblyRequest, Warehouse, WarehouseStockRow, WbFboSupply, WbFboSupplyItem } from '@/types/api';

interface FormItem {
    barcode: string;
    quantity: number;
    product_name?: string;
}

export default function AssemblyEditPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const id = Number(params.id);

    // Original assembly data
    const [assembly, setAssembly] = useState<AssemblyRequest | null>(null);

    // Form state
    const [warehouseId, setWarehouseId] = useState<number | ''>('');
    const [fboSupplyId, setFboSupplyId] = useState<number | ''>('');
    const [estimatedReadyDate, setEstimatedReadyDate] = useState('');
    const [palletsCount, setPalletsCount] = useState<number>(1);
    const [palletWeightKg, setPalletWeightKg] = useState<number>(0);
    const [comment, setComment] = useState('');
    const [wbWarehouseName, setWbWarehouseName] = useState('');
    const [formItems, setFormItems] = useState<FormItem[]>([{ barcode: '', quantity: 1 }]);

    // Reference data
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [fboSupplies, setFboSupplies] = useState<WbFboSupply[]>([]);
    const [fboSearchInput, setFboSearchInput] = useState('');
    const [wbWarehouses, setWbWarehouses] = useState<string[]>([]);

    // State
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [fboDropdownOpen, setFboDropdownOpen] = useState(false);
    const fboDropdownRef = useRef<HTMLDivElement>(null);

    // Stock data by barcode
    const [stockMap, setStockMap] = useState<Map<string, number>>(new Map());

    // Paste feedback
    const [pasteResult, setPasteResult] = useState<string | null>(null);

    // Close dropdown on outside click
    useEffect(() => {
        const handler = (e: MouseEvent) => {
            if (fboDropdownRef.current && !fboDropdownRef.current.contains(e.target as Node)) {
                setFboDropdownOpen(false);
            }
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    // ─── Load existing assembly + reference data ─────────────────────────

    useEffect(() => {
        const loadAll = async () => {
            setLoading(true);
            try {
                const [data, whs, wbNames] = await Promise.all([
                    api.getAssemblyRequest(id),
                    api.getWarehouses(),
                    api.getWbWarehouseNames(),
                ]);
                setAssembly(data);
                setWarehouses(whs.filter(w => w.warehouse_type === 'FULFILLMENT'));
                setWbWarehouses(wbNames);

                // Pre-fill form
                setWarehouseId(data.warehouse_id);
                setFboSupplyId(data.wb_fbo_supply_id || '');
                setEstimatedReadyDate(data.estimated_ready_date || '');
                setPalletsCount(data.pallets_count);
                setPalletWeightKg(data.pallet_weight_kg);
                setComment(data.comment || '');
                setWbWarehouseName(data.wb_warehouse_name_manual || '');

                // Pre-fill items
                if (data.items.length > 0) {
                    setFormItems([
                        ...data.items.map(i => ({
                            barcode: i.barcode,
                            quantity: i.quantity,
                            product_name: i.product_name || undefined,
                        })),
                        { barcode: '', quantity: 1 },
                    ]);
                }
            } catch (e: unknown) {
                setError(e instanceof Error ? e.message : 'Ошибка загрузки');
            }
            setLoading(false);
        };
        loadAll();
    }, [id]);

    // Load FBO supplies for dropdown
    const loadFboSupplies = useCallback(async () => {
        try {
            const resp = await api.getFboSupplies({
                status: 'ACTIVE,ON_DELIVERY,IN_PROGRESS,ACCEPTED',
                search: fboSearchInput || undefined,
                limit: 100,
            });
            setFboSupplies(resp.items);
        } catch {
            setFboSupplies([]);
        }
    }, [fboSearchInput, assembly?.wb_fbo_supply_id]);

    useEffect(() => {
        if (!loading) {
            const timer = setTimeout(() => { loadFboSupplies(); }, 300);
            return () => clearTimeout(timer);
        }
    }, [loading, loadFboSupplies]);

    // Load items when FBO supply changes (only for new selections, not initial load)
    const initialFboRef = useRef<number | null>(null);
    useEffect(() => {
        if (!assembly) return;
        // Remember initial FBO supply id
        if (initialFboRef.current === null) {
            initialFboRef.current = assembly.wb_fbo_supply_id || 0;
        }
    }, [assembly]);

    useEffect(() => {
        if (!fboSupplyId || initialFboRef.current === null) return;
        // Skip on initial load — items are already pre-filled from assembly
        if (Number(fboSupplyId) === initialFboRef.current) return;

        const loadItems = async () => {
            try {
                const items: WbFboSupplyItem[] = await api.getFboSupplyItems(Number(fboSupplyId));
                const grouped = new Map<string, FormItem>();
                for (const item of items) {
                    const existing = grouped.get(item.barcode);
                    if (existing) {
                        existing.quantity += item.quantity;
                    } else {
                        grouped.set(item.barcode, {
                            barcode: item.barcode,
                            quantity: item.quantity,
                            product_name: item.product_name || item.article_seller || undefined,
                        });
                    }
                }
                setFormItems([...Array.from(grouped.values()), { barcode: '', quantity: 1 }]);
            } catch { /* ignore */ }
        };
        loadItems();
    }, [fboSupplyId]);

    // Load stock when warehouse changes
    useEffect(() => {
        if (!warehouseId) {
            setStockMap(new Map());
            return;
        }
        api.getWarehouseStock(Number(warehouseId))
            .then((rows: WarehouseStockRow[]) => {
                const map = new Map<string, number>();
                for (const row of rows) {
                    map.set(row.barcode, (map.get(row.barcode) || 0) + row.available);
                }
                setStockMap(map);
            })
            .catch(() => setStockMap(new Map()));
    }, [warehouseId]);

    // ─── Computed ─────────────────────────────────────────────────────────

    const selectedWarehouse = warehouses.find(w => w.id === warehouseId) || null;
    const totalWeight = palletsCount * palletWeightKg;
    const canEditItems = assembly && ['PENDING', 'IN_PROGRESS'].includes(assembly.status);

    // ─── Item management ──────────────────────────────────────────────────

    const addItem = () => {
        setFormItems(prev => [...prev, { barcode: '', quantity: 1 }]);
    };

    const updateItem = (index: number, field: keyof FormItem, value: string | number) => {
        setFormItems(prev => prev.map((item, i) =>
            i === index ? { ...item, [field]: value } : item
        ));
    };

    const removeItem = (index: number) => {
        setFormItems(prev => prev.filter((_, i) => i !== index));
    };

    const handleBulkPaste = (text: string) => {
        const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
        const parsed: FormItem[] = [];
        for (const line of lines) {
            const parts = line.split(/\t+|\s{2,}/);
            if (parts.length >= 2) {
                const barcode = parts[0].trim();
                const qty = parseInt(parts[1].trim(), 10);
                if (barcode && qty > 0) {
                    parsed.push({ barcode, quantity: qty });
                }
            } else if (parts.length === 1) {
                const barcode = parts[0].trim();
                if (barcode) {
                    parsed.push({ barcode, quantity: 1 });
                }
            }
        }
        if (parsed.length > 0) {
            setFormItems(prev => {
                const filled = prev.filter(i => i.barcode.trim());
                return [...filled, ...parsed, { barcode: '', quantity: 1 }];
            });
            setPasteResult(`Добавлено ${parsed.length} позиций`);
            setTimeout(() => setPasteResult(null), 3000);
        }
    };

    // ─── Submit ───────────────────────────────────────────────────────────

    const handleSubmit = async () => {
        const filledItems = formItems.filter(i => i.barcode.trim());

        setSubmitting(true);
        setError('');
        try {
            await api.updateAssemblyRequest(id, {
                wb_fbo_supply_id: fboSupplyId ? Number(fboSupplyId) : null,
                wb_warehouse_name_manual: fboSupplyId ? undefined : (wbWarehouseName || null),
                estimated_ready_date: estimatedReadyDate || null,
                pallets_count: palletsCount,
                pallet_weight_kg: palletWeightKg,
                comment: comment || null,
                ...(canEditItems ? { items: filledItems.map(i => ({ barcode: i.barcode, quantity: i.quantity })) } : {}),
            });
            router.push(`/p/${slug}/warehouse/assembly/${id}`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        }
        setSubmitting(false);
    };

    // ─── Render ───────────────────────────────────────────────────────────

    if (loading) {
        return <div className="animate-in" style={{ textAlign: 'center', padding: 48 }}>Загрузка...</div>;
    }

    if (!assembly) {
        return (
            <div className="animate-in" style={{ textAlign: 'center', padding: 48 }}>
                <p style={{ color: 'var(--color-danger)' }}>{error || 'Заявка не найдена'}</p>
                <Link href={`/p/${slug}/warehouse/assembly`}>
                    <button className="btn btn-secondary" style={{ marginTop: 16 }}>Назад</button>
                </Link>
            </div>
        );
    }

    return (
        <div className="animate-in">
            {/* Header */}
            <div className="page-header">
                <div>
                    <Link href={`/p/${slug}/warehouse/assembly/${id}`} style={{ color: 'var(--color-text-muted)', textDecoration: 'none', fontSize: 14 }}>
                        &larr; {assembly.number}
                    </Link>
                    <h1 className="page-title">Редактирование {assembly.number}</h1>
                </div>
            </div>

            {/* Error */}
            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)', whiteSpace: 'pre-line' }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={() => setError('')}>
                        Закрыть
                    </button>
                </div>
            )}

            {/* Form */}
            <div className="glass-card" style={{ padding: 24, marginBottom: 16 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                    {/* Warehouse — read-only */}
                    <div className="form-group">
                        <label className="form-label">Склад</label>
                        <div style={{ padding: '8px 12px', background: 'var(--color-bg-secondary)', borderRadius: 8 }}>
                            {assembly.warehouse_name || 'Склад #' + assembly.warehouse_id}
                        </div>
                    </div>

                    {/* FBO Supply — searchable dropdown */}
                    <div className="form-group" ref={fboDropdownRef} style={{ position: 'relative' }}>
                        <label className="form-label">Поставка FBO</label>
                        <input
                            className="form-input"
                            type="text"
                            placeholder="Введите номер поставки..."
                            value={fboDropdownOpen ? fboSearchInput : (
                                fboSupplyId
                                    ? (() => {
                                        const s = fboSupplies.find(s => s.id === fboSupplyId);
                                        return s ? `${s.wb_supply_id} — ${s.warehouse_name || 'Без склада'} (${s.total_qty} шт.)` : fboSearchInput;
                                    })()
                                    : fboSearchInput
                            )}
                            onChange={e => {
                                setFboSearchInput(e.target.value);
                                if (!fboDropdownOpen) setFboDropdownOpen(true);
                                if (!e.target.value) setFboSupplyId('');
                            }}
                            onFocus={() => setFboDropdownOpen(true)}
                        />
                        {fboSupplyId && !fboDropdownOpen && (
                            <div style={{ position: 'absolute', right: 8, top: 30, display: 'flex', gap: 4 }}>
                                <button
                                    type="button"
                                    title="Обновить поставку из WB"
                                    onClick={async () => {
                                        try {
                                            setSubmitting(true);
                                            await api.syncFboSupplies();
                                            const items: WbFboSupplyItem[] = await api.getFboSupplyItems(Number(fboSupplyId), true);
                                            const grouped = new Map<string, FormItem>();
                                            for (const item of items) {
                                                const existing = grouped.get(item.barcode);
                                                if (existing) {
                                                    existing.quantity += item.quantity;
                                                } else {
                                                    grouped.set(item.barcode, {
                                                        barcode: item.barcode,
                                                        quantity: item.quantity,
                                                        product_name: item.product_name || item.article_seller || undefined,
                                                    });
                                                }
                                            }
                                            setFormItems([...Array.from(grouped.values()), { barcode: '', quantity: 1 }]);
                                        } catch { /* ignore */ }
                                        setSubmitting(false);
                                    }}
                                    style={{
                                        background: 'none', border: 'none', cursor: 'pointer',
                                        color: 'var(--color-primary)', fontSize: 14,
                                    }}
                                >
                                    &#x1f504;
                                </button>
                                <button
                                    type="button"
                                    onClick={() => { setFboSupplyId(''); setFboSearchInput(''); }}
                                    style={{
                                        background: 'none', border: 'none', cursor: 'pointer',
                                        color: 'var(--color-text-muted)', fontSize: 16,
                                    }}
                                >
                                    &times;
                                </button>
                            </div>
                        )}
                        {fboDropdownOpen && (
                            <div style={{
                                position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
                                background: 'var(--color-bg)', border: '1px solid var(--color-border)',
                                borderRadius: 8, maxHeight: 240, overflowY: 'auto',
                                boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
                            }}>
                                {fboSupplies.length === 0 ? (
                                    <div style={{ padding: 12, textAlign: 'center', color: 'var(--color-text-muted)' }}>Поставки не найдены</div>
                                ) : (
                                    fboSupplies.map(s => (
                                        <div
                                            key={s.id}
                                            onClick={() => {
                                                setFboSupplyId(s.id);
                                                setFboSearchInput('');
                                                setFboDropdownOpen(false);
                                            }}
                                            style={{
                                                padding: '8px 12px', cursor: 'pointer',
                                                background: s.id === fboSupplyId ? 'var(--color-bg-secondary)' : undefined,
                                                fontSize: 13,
                                            }}
                                            onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
                                            onMouseLeave={e => (e.currentTarget.style.background = s.id === fboSupplyId ? 'var(--color-bg-secondary)' : '')}
                                        >
                                            <strong>{s.wb_supply_id}</strong> — {s.warehouse_name || 'Без склада'} ({s.total_qty} шт.)
                                        </div>
                                    ))
                                )}
                            </div>
                        )}
                    </div>

                    {/* Ready date */}
                    <div className="form-group">
                        <label className="form-label">Дата готовности (план)</label>
                        <div style={{ display: 'flex', gap: 8 }}>
                            <input
                                className="form-input"
                                type="date"
                                value={estimatedReadyDate}
                                onChange={e => setEstimatedReadyDate(e.target.value)}
                                style={{ flex: 1 }}
                            />
                            {selectedWarehouse?.assembly_days != null && selectedWarehouse.assembly_days > 0 && (
                                <button
                                    type="button"
                                    className="btn btn-secondary btn-sm"
                                    style={{ whiteSpace: 'nowrap' }}
                                    onClick={() => {
                                        const d = new Date();
                                        d.setDate(d.getDate() + selectedWarehouse.assembly_days!);
                                        setEstimatedReadyDate(d.toISOString().slice(0, 10));
                                    }}
                                >
                                    +{selectedWarehouse.assembly_days} дн.
                                </button>
                            )}
                        </div>
                    </div>

                    {/* Pallets */}
                    <div style={{ display: 'flex', gap: 12 }}>
                        <div className="form-group" style={{ flex: 1 }}>
                            <label className="form-label">Палеты</label>
                            <input
                                className="form-input"
                                type="number"
                                min={0}
                                value={palletsCount}
                                onChange={e => setPalletsCount(Number(e.target.value) || 0)}
                            />
                        </div>
                        <div className="form-group" style={{ flex: 1 }}>
                            <label className="form-label">Вес 1 палеты (кг)</label>
                            <input
                                className="form-input"
                                type="number"
                                min={0}
                                step={0.1}
                                value={palletWeightKg}
                                onChange={e => setPalletWeightKg(Number(e.target.value) || 0)}
                            />
                        </div>
                        <div className="form-group" style={{ flex: 1 }}>
                            <label className="form-label">Общий вес</label>
                            <div style={{ padding: '8px 12px', background: 'var(--color-bg-secondary)', borderRadius: 8, fontWeight: 500 }}>
                                {totalWeight > 0 ? formatNumber(totalWeight, 1) + ' кг' : '\u2014'}
                            </div>
                        </div>
                    </div>

                    {/* WB Warehouse — only when no FBO selected */}
                    {!fboSupplyId && (
                        <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                            <label className="form-label">Склад сдачи WB</label>
                            <input
                                className="form-input"
                                list="wb-warehouse-list"
                                value={wbWarehouseName}
                                onChange={e => setWbWarehouseName(e.target.value)}
                                placeholder="Выберите или введите склад WB..."
                            />
                            <datalist id="wb-warehouse-list">
                                {wbWarehouses.map(name => <option key={name} value={name} />)}
                            </datalist>
                        </div>
                    )}

                    {/* Comment */}
                    <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                        <label className="form-label">Комментарий</label>
                        <textarea
                            className="form-input"
                            rows={2}
                            value={comment}
                            onChange={e => setComment(e.target.value)}
                            placeholder="Примечания к заявке..."
                        />
                    </div>
                </div>
            </div>

            {/* Items */}
            <div className="glass-card" style={{ padding: 24 }} onPaste={e => {
                if (!canEditItems) return;
                const text = e.clipboardData.getData('text/plain');
                if (!text.includes('\t') && !text.includes('\n')) return;
                e.preventDefault();
                handleBulkPaste(text);
            }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>
                        Позиции ({formItems.filter(i => i.barcode).length})
                    </h2>
                    {canEditItems && (
                        <button className="btn btn-secondary btn-sm" onClick={addItem}>
                            + Добавить позицию
                        </button>
                    )}
                </div>

                <table className="data-table" style={{ fontSize: 13 }}>
                    <thead>
                        <tr>
                            <th style={{ width: 40 }}>#</th>
                            <th>Товар</th>
                            <th style={{ width: 200 }}>ШК</th>
                            <th style={{ width: 100, textAlign: 'right' }}>Кол-во</th>
                            <th style={{ width: 100, textAlign: 'right' }}>На складе</th>
                            {canEditItems && <th style={{ width: 40 }}></th>}
                        </tr>
                    </thead>
                    <tbody>
                        {formItems.map((item, idx) => {
                            const stockQty = stockMap.get(item.barcode) || 0;
                            const deficit = item.barcode && stockQty < item.quantity;
                            return (
                                <tr key={idx}>
                                    <td style={{ color: 'var(--color-text-muted)' }}>{idx + 1}</td>
                                    <td style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                        {item.product_name || '\u2014'}
                                    </td>
                                    <td>
                                        {canEditItems ? (
                                            <input
                                                className="form-input"
                                                value={item.barcode}
                                                onChange={e => updateItem(idx, 'barcode', e.target.value)}
                                                placeholder="Штрихкод"
                                                style={{ fontSize: 13, fontFamily: 'monospace' }}
                                            />
                                        ) : (
                                            <span style={{ fontFamily: 'monospace' }}>{item.barcode}</span>
                                        )}
                                    </td>
                                    <td>
                                        {canEditItems ? (
                                            <input
                                                className="form-input"
                                                type="number"
                                                min={1}
                                                value={item.quantity}
                                                onChange={e => updateItem(idx, 'quantity', Number(e.target.value) || 0)}
                                                style={{ fontSize: 13, textAlign: 'right' }}
                                            />
                                        ) : (
                                            <span style={{ textAlign: 'right', display: 'block' }}>{item.quantity}</span>
                                        )}
                                    </td>
                                    <td style={{ textAlign: 'right', fontWeight: 500, color: deficit ? 'var(--color-danger)' : warehouseId ? 'var(--color-success)' : 'var(--color-text-muted)' }}>
                                        {warehouseId ? stockQty : '\u2014'}
                                    </td>
                                    {canEditItems && (
                                        <td>
                                            <button
                                                className="btn btn-secondary btn-sm"
                                                onClick={() => removeItem(idx)}
                                                title="Удалить"
                                                style={{ padding: '4px 8px' }}
                                            >
                                                &times;
                                            </button>
                                        </td>
                                    )}
                                </tr>
                            );
                        })}
                    </tbody>
                </table>

                {canEditItems && (
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 8 }}>
                        {pasteResult
                            ? <span style={{ color: 'var(--color-success)' }}>{pasteResult}</span>
                            : 'Ctrl+V — вставить из Excel (штрихкод ⇥ кол-во)'}
                    </div>
                )}

                {!canEditItems && (
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 8 }}>
                        Позиции нельзя редактировать в статусе {assembly.status}
                    </div>
                )}

                {/* Submit */}
                <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', marginTop: 24 }}>
                    <Link href={`/p/${slug}/warehouse/assembly/${id}`}>
                        <button className="btn btn-secondary">Отмена</button>
                    </Link>
                    <button
                        className="btn btn-primary"
                        onClick={handleSubmit}
                        disabled={submitting}
                    >
                        {submitting ? 'Сохранение...' : 'Сохранить'}
                    </button>
                </div>
            </div>
        </div>
    );
}
