'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { PackageType, Warehouse, WarehouseStockRow, WbFboSupply, WbFboSupplyItem } from '@/types/api';

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
    const prefillFromAnalytics = searchParams.get('prefill') === '1';

    // Form state
    const [warehouseId, setWarehouseId] = useState<number | ''>('');
    const [fboSupplyId, setFboSupplyId] = useState<number | ''>('');
    const [estimatedReadyDate, setEstimatedReadyDate] = useState('');
    const [palletsCount, setPalletsCount] = useState<number>(1);
    const [palletWeightKg, setPalletWeightKg] = useState<number>(0);
    const [comment, setComment] = useState('');
    const [wbWarehouseName, setWbWarehouseName] = useState('');
    const [packageType, setPackageType] = useState<PackageType>('BOX');
    const [formItems, setFormItems] = useState<FormItem[]>([{ barcode: '', quantity: 1 }]);

    // Reference data
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [fboSupplies, setFboSupplies] = useState<WbFboSupply[]>([]);
    const [fboSearchInput, setFboSearchInput] = useState('');
    const [wbWarehouses, setWbWarehouses] = useState<string[]>([]);

    // State
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [loadingFboItems, setLoadingFboItems] = useState(false);
    const [fboDropdownOpen, setFboDropdownOpen] = useState(false);
    const fboDropdownRef = useRef<HTMLDivElement>(null);

    // Stock data by barcode
    const [stockMap, setStockMap] = useState<Map<string, number>>(new Map());

    // Bulk paste result feedback
    const [pasteResult, setPasteResult] = useState<string | null>(null);

    // Warning shown when FBO lazy-load returned no items (WB rate limit / empty supply)
    const [fboItemsWarning, setFboItemsWarning] = useState<string | null>(null);

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
        api.getWbWarehouseNames()
            .then(setWbWarehouses)
            .catch(() => {});
    }, []);

    const loadFboSupplies = useCallback(async () => {
        setLoading(true);
        try {
            const resp = await api.getFboSupplies({
                // ACCEPTED included: users need to link assembly requests
                // retroactively for supplies already accepted by WB.
                status: 'ACTIVE,ON_DELIVERY,IN_PROGRESS,ACCEPTED',
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

    // ─── Prefill from analytics (sessionStorage handoff) ──────────────────

    useEffect(() => {
        if (!prefillFromAnalytics) return;
        const raw = sessionStorage.getItem('pending_assembly');
        if (!raw) return;
        try {
            const parsed = JSON.parse(raw) as {
                warehouse_id: number;
                items: { barcode: string; quantity: number; product_name?: string }[];
            };
            if (parsed.warehouse_id) setWarehouseId(parsed.warehouse_id);
            if (Array.isArray(parsed.items) && parsed.items.length > 0) {
                setFormItems(parsed.items.map(i => ({
                    barcode: i.barcode,
                    quantity: i.quantity,
                    product_name: i.product_name,
                })));
            }
        } catch {
            // ignore malformed payload
        }
        sessionStorage.removeItem('pending_assembly');
    }, [prefillFromAnalytics]);

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
            // Don't clear items when FBO is deselected — user may have added items manually
            setFboItemsWarning(null);
            return;
        }
        const loadItems = async () => {
            setLoadingFboItems(true);
            setFboItemsWarning(null);
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
                if (grouped.size === 0) {
                    setFormItems([{ barcode: '', quantity: 1 }]);
                    setFboItemsWarning('WB API не вернул позиции для этой поставки (возможно, лимит запросов или данные ещё не синхронизированы). Заполните штрихкоды вручную или вставьте из Excel (Ctrl+V).');
                } else {
                    setFormItems(Array.from(grouped.values()));
                }
            } catch (e: unknown) {
                setFormItems([{ barcode: '', quantity: 1 }]);
                const msg = e instanceof Error ? e.message : 'Ошибка загрузки';
                setFboItemsWarning(`Не удалось загрузить позиции из WB: ${msg}. Заполните вручную или вставьте из Excel (Ctrl+V).`);
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

    const handleBulkPaste = (text: string) => {
        const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
        const parsed: FormItem[] = [];
        for (const line of lines) {
            // Support tab or multiple spaces as delimiter
            const parts = line.split(/\t+|\s{2,}/);
            if (parts.length >= 2) {
                const barcode = parts[0].trim();
                const qty = parseInt(parts[1].trim(), 10);
                if (barcode && qty > 0) {
                    parsed.push({ barcode, quantity: qty });
                }
            } else if (parts.length === 1) {
                // Single barcode without quantity — default to 1
                const barcode = parts[0].trim();
                if (barcode) {
                    parsed.push({ barcode, quantity: 1 });
                }
            }
        }
        if (parsed.length > 0) {
            // Replace empty rows with parsed data + one empty row at the end
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
        if (!warehouseId || filledItems.length === 0) return;

        setSubmitting(true);
        setError('');
        try {
            const result = await api.createAssemblyRequest({
                warehouse_id: Number(warehouseId),
                wb_fbo_supply_id: fboSupplyId ? Number(fboSupplyId) : null,
                wb_warehouse_name_manual: fboSupplyId ? undefined : (wbWarehouseName || null),
                estimated_ready_date: estimatedReadyDate || undefined,
                pallets_count: palletsCount,
                pallet_weight_kg: palletWeightKg,
                package_type: packageType,
                comment: comment || undefined,
                items: filledItems.map(i => ({ barcode: i.barcode, quantity: i.quantity })),
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

                    {/* Тип поставки (упаковка WB: короб / монопаллета / суперсейф) */}
                    <div className="form-group">
                        <label className="form-label">Тип поставки</label>
                        <select
                            className="form-input"
                            value={packageType}
                            onChange={e => setPackageType(e.target.value as PackageType)}
                        >
                            <option value="BOX">Короб</option>
                            <option value="MONOPALLET">Монопаллета</option>
                            <option value="SUPERSAFE">Суперсейф</option>
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
                            <div style={{ position: 'absolute', right: 8, top: 30, display: 'flex', gap: 4 }}>
                                <button
                                    type="button"
                                    title="Обновить данные из WB"
                                    onClick={async () => {
                                        try {
                                            setLoadingFboItems(true);
                                            await api.syncFboSupplies();
                                            // Reload items
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
                                            setFormItems(Array.from(grouped.values()));
                                            // Update total_qty in fboSupplies list from actual items
                                            const actualQty = Array.from(grouped.values()).reduce((s, i) => s + i.quantity, 0);
                                            setFboSupplies(prev => prev.map(s =>
                                                s.id === Number(fboSupplyId) ? { ...s, total_qty: actualQty } : s
                                            ));
                                        } catch { /* ignore */ }
                                        setLoadingFboItems(false);
                                    }}
                                    style={{
                                        background: 'none', border: 'none', cursor: 'pointer',
                                        color: 'var(--color-primary)', fontSize: 14,
                                    }}
                                >
                                    🔄
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
                                value={palletWeightKg || ''}
                                onChange={e => setPalletWeightKg(Number(e.target.value) || 0)}
                                disabled
                                title="Вес считается автоматически из справочника «Вес по баркодам»"
                            />
                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                Авто из справочника «Вес по баркодам»
                            </div>
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
                            <label className="form-label">Склад сдачи WB <span style={{ color: 'var(--color-text-muted)', fontWeight: 400 }}>(если поставка ещё не создана)</span></label>
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
                // Direct paste on table container (like in receipt page)
                const text = e.clipboardData.getData('text/plain');
                if (!text.includes('\t') && !text.includes('\n')) return;
                e.preventDefault();
                handleBulkPaste(text);
            }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>
                        Позиции ({formItems.filter(i => i.barcode).length})
                    </h2>
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-secondary btn-sm" onClick={addItem}>
                            + Добавить позицию
                        </button>
                    </div>
                </div>

                {fboItemsWarning && !loadingFboItems && (
                    <div style={{ padding: 12, marginBottom: 12, background: 'rgba(255, 159, 10, 0.1)', border: '1px solid var(--color-warning)', borderRadius: 8, fontSize: 13, color: 'var(--color-text)' }}>
                        <span style={{ color: 'var(--color-warning)', fontWeight: 600 }}>⚠ </span>
                        {fboItemsWarning}
                    </div>
                )}

                {loadingFboItems ? (
                    <div style={{ textAlign: 'center', padding: 24 }}>Загрузка позиций из FBO...</div>
                ) : (
                    /* TODO: migrate to TanStackDataTable — has inline form inputs (barcode input, quantity input, remove button) */
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

                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 8 }}>
                    {pasteResult
                        ? <span style={{ color: 'var(--color-success)' }}>{pasteResult}</span>
                        : 'Ctrl+V — вставить из Excel (штрихкод ⇥ кол-во)'}
                </div>

                {/* Submit */}
                <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', marginTop: 24 }}>
                    <Link href={`/p/${slug}/warehouse/assembly`}>
                        <button className="btn btn-secondary">Отмена</button>
                    </Link>
                    <button
                        className="btn btn-primary"
                        onClick={handleSubmit}
                        disabled={submitting || !warehouseId || !formItems.some(i => i.barcode.trim())}
                    >
                        {submitting ? 'Создание...' : 'Создать заявку'}
                    </button>
                </div>
            </div>
        </div>
    );
}
