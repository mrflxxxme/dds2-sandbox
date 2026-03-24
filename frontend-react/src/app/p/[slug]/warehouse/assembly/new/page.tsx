'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { Warehouse, WarehouseStockRow, WbFboSupply, WbFboSupplyItem } from '@/types/api';

interface FormItem {
    barcode: string;
    quantity: number;
    product_name?: string;
}

export default function AssemblyNewPage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const slug = params.slug as string;

    const preselectedFboId = searchParams.get('fbo_supply_id');

    // Form state
    const [warehouseId, setWarehouseId] = useState<number | ''>('');
    const [fboSupplyId, setFboSupplyId] = useState<number | ''>('');
    const [estimatedReadyDate, setEstimatedReadyDate] = useState('');
    const [palletsCount, setPalletsCount] = useState<number>(1);
    const [palletWeightKg, setPalletWeightKg] = useState<number>(0);
    const [comment, setComment] = useState('');
    const [formItems, setFormItems] = useState<FormItem[]>([]);

    // Reference data
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [fboSupplies, setFboSupplies] = useState<WbFboSupply[]>([]);
    const [fboSearchInput, setFboSearchInput] = useState('');

    // State
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [loadingFboItems, setLoadingFboItems] = useState(false);
    const [fboDropdownOpen, setFboDropdownOpen] = useState(false);
    const fboDropdownRef = useRef<HTMLDivElement>(null);

    // Stock data by barcode
    const [stockMap, setStockMap] = useState<Map<string, number>>(new Map());

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

    // ─── Load reference data ──────────────────────────────────────────────

    useEffect(() => {
        api.getWarehouses()
            .then(whs => setWarehouses(whs.filter(w => w.warehouse_type === 'FULFILLMENT')))
            .catch(() => {});
    }, []);

    const loadFboSupplies = useCallback(async () => {
        setLoading(true);
        try {
            const resp = await api.getFboSupplies({
                status: 'ACTIVE,ON_DELIVERY,IN_PROGRESS',
                search: fboSearchInput || undefined,
                limit: 100,
                exclude_with_assembly: true,
            });
            setFboSupplies(resp.items);
        } catch {
            setFboSupplies([]);
        }
        setLoading(false);
    }, [fboSearchInput]);

    // Debounced search for FBO supplies
    useEffect(() => {
        const timer = setTimeout(() => { loadFboSupplies(); }, 300);
        return () => clearTimeout(timer);
    }, [loadFboSupplies]);

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

    // ─── Pre-select FBO supply from URL ───────────────────────────────────

    useEffect(() => {
        if (preselectedFboId && fboSupplies.length > 0) {
            const id = Number(preselectedFboId);
            const found = fboSupplies.find(s => s.id === id);
            if (found) {
                setFboSupplyId(id);
            } else {
                // Supply might not be in ACTIVE list, try loading it anyway
                setFboSupplyId(id);
            }
        }
    }, [preselectedFboId, fboSupplies]);

    // ─── Load items when FBO supply changes ──────────────────────────────

    useEffect(() => {
        if (!fboSupplyId) {
            setFormItems([]);
            return;
        }
        const loadItems = async () => {
            setLoadingFboItems(true);
            try {
                const items: WbFboSupplyItem[] = await api.getFboSupplyItems(Number(fboSupplyId));
                // Group by barcode and sum quantities
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
                setFormItems(Array.from(grouped.values()));
            } catch {
                setFormItems([]);
            }
            setLoadingFboItems(false);
        };
        loadItems();
    }, [fboSupplyId]);

    // ─── Computed ─────────────────────────────────────────────────────────

    const selectedWarehouse = warehouses.find(w => w.id === warehouseId) || null;
    const totalWeight = palletsCount * palletWeightKg;

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

    // ─── Submit ───────────────────────────────────────────────────────────

    const handleSubmit = async () => {
        if (!warehouseId || !fboSupplyId || formItems.length === 0) return;

        setSubmitting(true);
        setError('');
        try {
            const result = await api.createAssemblyRequest({
                warehouse_id: Number(warehouseId),
                wb_fbo_supply_id: Number(fboSupplyId),
                estimated_ready_date: estimatedReadyDate || undefined,
                pallets_count: palletsCount,
                pallet_weight_kg: palletWeightKg,
                comment: comment || undefined,
                items: formItems.map(i => ({ barcode: i.barcode, quantity: i.quantity })),
            });
            router.push(`/p/${slug}/warehouse/assembly/${result.id}`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка создания заявки');
        }
        setSubmitting(false);
    };

    // ─── Render ───────────────────────────────────────────────────────────

    return (
        <div className="animate-in">
            {/* Header */}
            <div className="page-header">
                <div>
                    <Link href={`/p/${slug}/warehouse/assembly`} style={{ color: 'var(--color-text-muted)', textDecoration: 'none', fontSize: 14 }}>
                        &larr; Заявки на сборку
                    </Link>
                    <h1 className="page-title">Новая заявка на сборку</h1>
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
                    {/* Warehouse */}
                    <div className="form-group">
                        <label className="form-label">Склад</label>
                        <select
                            className="form-input"
                            value={warehouseId}
                            onChange={e => setWarehouseId(e.target.value ? Number(e.target.value) : '')}
                        >
                            <option value="">Выберите склад...</option>
                            {warehouses.map(w => (
                                <option key={w.id} value={w.id}>{w.name}</option>
                            ))}
                        </select>
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
                                if (!e.target.value) {
                                    setFboSupplyId('');
                                }
                            }}
                            onFocus={() => setFboDropdownOpen(true)}
                        />
                        {fboSupplyId && !fboDropdownOpen && (
                            <button
                                type="button"
                                onClick={() => { setFboSupplyId(''); setFboSearchInput(''); }}
                                style={{
                                    position: 'absolute', right: 8, top: 32,
                                    background: 'none', border: 'none', cursor: 'pointer',
                                    color: 'var(--color-text-muted)', fontSize: 16,
                                }}
                            >
                                &times;
                            </button>
                        )}
                        {fboDropdownOpen && (
                            <div style={{
                                position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
                                background: 'var(--color-bg)', border: '1px solid var(--color-border)',
                                borderRadius: 8, maxHeight: 240, overflowY: 'auto',
                                boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
                            }}>
                                {loading ? (
                                    <div style={{ padding: 12, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                                ) : fboSupplies.length === 0 ? (
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
            <div className="glass-card" style={{ padding: 24 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>
                        Позиции ({formItems.length})
                    </h2>
                    <button className="btn btn-secondary btn-sm" onClick={addItem}>
                        + Добавить позицию
                    </button>
                </div>

                {loadingFboItems ? (
                    <div style={{ textAlign: 'center', padding: 24 }}>Загрузка позиций из FBO...</div>
                ) : formItems.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-muted)' }}>
                        {fboSupplyId
                            ? 'Нет позиций в выбранной поставке'
                            : 'Выберите поставку FBO для автозаполнения или добавьте позиции вручную'}
                    </div>
                ) : (
                    <table className="data-table" style={{ fontSize: 13 }}>
                        <thead>
                            <tr>
                                <th style={{ width: 40 }}>#</th>
                                <th>Товар</th>
                                <th style={{ width: 200 }}>ШК</th>
                                <th style={{ width: 100, textAlign: 'right' }}>В поставке</th>
                                <th style={{ width: 100, textAlign: 'right' }}>На складе</th>
                                <th style={{ width: 40 }}></th>
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
                                            <input
                                                className="form-input"
                                                value={item.barcode}
                                                onChange={e => updateItem(idx, 'barcode', e.target.value)}
                                                placeholder="Штрихкод"
                                                style={{ fontSize: 13, fontFamily: 'monospace' }}
                                            />
                                        </td>
                                        <td>
                                            <input
                                                className="form-input"
                                                type="number"
                                                min={1}
                                                value={item.quantity}
                                                onChange={e => updateItem(idx, 'quantity', Number(e.target.value) || 0)}
                                                style={{ fontSize: 13, textAlign: 'right' }}
                                            />
                                        </td>
                                        <td style={{ textAlign: 'right', fontWeight: 500, color: deficit ? 'var(--color-danger)' : warehouseId ? 'var(--color-success)' : 'var(--color-text-muted)' }}>
                                            {warehouseId ? stockQty : '\u2014'}
                                        </td>
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
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}

                {/* Submit */}
                <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', marginTop: 24 }}>
                    <Link href={`/p/${slug}/warehouse/assembly`}>
                        <button className="btn btn-secondary">Отмена</button>
                    </Link>
                    <button
                        className="btn btn-primary"
                        onClick={handleSubmit}
                        disabled={submitting || !warehouseId || !fboSupplyId || formItems.length === 0}
                    >
                        {submitting ? 'Создание...' : 'Создать заявку'}
                    </button>
                </div>
            </div>
        </div>
    );
}
