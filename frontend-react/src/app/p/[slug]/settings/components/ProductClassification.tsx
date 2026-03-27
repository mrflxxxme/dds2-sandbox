'use client';

import { useState, useEffect, useCallback } from 'react';
import { api } from '@/lib/api';
import type { ProductTag, FunnelProduct } from '@/types/api';
import { formatNumber, exportToExcel } from '@/lib/utils';

const STATUS_OPTIONS = [
    { value: '', label: '—' },
    { value: 'active', label: 'Активный' },
    { value: 'new', label: 'Новинка' },
    { value: 'clearance', label: 'Слив' },
];

const STATUS_LABELS: Record<string, string> = {
    active: 'Активный',
    new: 'Новинка',
    clearance: 'Слив',
};

const STATUS_COLORS: Record<string, string> = {
    active: 'var(--color-success)',
    new: 'var(--color-info)',
    clearance: 'var(--color-danger)',
};

const DEFAULT_COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#06B6D4', '#F97316'];

export function ProductClassification() {
    const [tags, setTags] = useState<ProductTag[]>([]);
    const [products, setProducts] = useState<FunnelProduct[]>([]);
    const [tagMapping, setTagMapping] = useState<Record<string, number[]>>({});
    const [statuses, setStatuses] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [search, setSearch] = useState('');
    const [brandFilter, setBrandFilter] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [tagFilter, setTagFilter] = useState('');
    const [imtFilter, setImtFilter] = useState('');

    // Tag form
    const [showTagForm, setShowTagForm] = useState(false);
    const [tagName, setTagName] = useState('');
    const [tagColor, setTagColor] = useState('#3B82F6');
    const [editingTagId, setEditingTagId] = useState<number | null>(null);
    const [msg, setMsg] = useState('');

    // Bulk paste
    const [showBulkPaste, setShowBulkPaste] = useState(false);
    const [pasteText, setPasteText] = useState('');
    const [pasteResolved, setPasteResolved] = useState<{ found: FunnelProduct[]; notFound: string[] }>({ found: [], notFound: [] });
    const [bulkSaving, setBulkSaving] = useState(false);

    const loadData = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [tagsRes, productsRes, mappingRes, statusesRes] = await Promise.all([
                api.getProductTags(),
                api.getFunnelProducts(),
                api.getProductTagMapping(),
                api.getProductStatuses(),
            ]);
            setTags(tagsRes);
            setProducts(productsRes.products);
            setTagMapping(mappingRes);
            setStatuses(statusesRes);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, []);

    useEffect(() => { loadData(); }, [loadData]);

    // --- Tag CRUD ---
    const saveTag = async () => {
        if (!tagName.trim()) return;
        try {
            await api.upsertProductTag({
                ...(editingTagId ? { id: editingTagId } : {}),
                name: tagName.trim(),
                color: tagColor,
            });
            setTagName('');
            setTagColor('#3B82F6');
            setEditingTagId(null);
            setShowTagForm(false);
            setMsg('');
            await loadData();
        } catch (e: unknown) {
            setMsg(e instanceof Error ? e.message : 'Ошибка');
        }
    };

    const deleteTag = async (id: number) => {
        if (!confirm('Удалить ярлык? Он будет снят со всех товаров.')) return;
        await api.deleteProductTag(id);
        await loadData();
    };

    const startEditTag = (tag: ProductTag) => {
        setEditingTagId(tag.id);
        setTagName(tag.name);
        setTagColor(tag.color);
        setShowTagForm(true);
    };

    // --- Status ---
    const handleStatusChange = async (nmId: number, status: string) => {
        try {
            if (status === '') return;
            await api.setProductStatus({ nm_id: nmId, status });
            setStatuses(prev => ({ ...prev, [String(nmId)]: status }));
        } catch (e: unknown) {
            setMsg(e instanceof Error ? e.message : 'Ошибка');
        }
    };

    // --- Bulk actions (checkbox-based) ---
    const selectAll = () => {
        if (selected.size === filteredProducts.length) {
            setSelected(new Set());
        } else {
            setSelected(new Set(filteredProducts.map(p => p.nm_id)));
        }
    };

    const bulkAssignTag = async (tagId: number) => {
        if (selected.size === 0) return;
        await api.updateProductTagMapping({
            nm_ids: Array.from(selected),
            add_tags: [tagId],
            remove_tags: [],
        });
        await loadData();
        setMsg('');
    };

    const bulkRemoveTag = async (tagId: number) => {
        if (selected.size === 0) return;
        await api.updateProductTagMapping({
            nm_ids: Array.from(selected),
            add_tags: [],
            remove_tags: [tagId],
        });
        await loadData();
    };

    const bulkSetStatus = async (status: string) => {
        if (selected.size === 0 || !status) return;
        await api.bulkSetProductStatus({
            nm_ids: Array.from(selected),
            status,
        });
        const updated = { ...statuses };
        selected.forEach(nmId => { updated[String(nmId)] = status; });
        setStatuses(updated);
        setMsg('');
    };

    // --- Bulk paste resolve ---
    const resolvePaste = useCallback((text: string) => {
        if (!text.trim()) {
            setPasteResolved({ found: [], notFound: [] });
            return;
        }
        // Split by newlines, tabs, commas, semicolons, spaces
        const codes = text
            .split(/[\n\r\t,;]+/)
            .map(s => s.trim())
            .filter(Boolean);

        const found: FunnelProduct[] = [];
        const notFound: string[] = [];
        const seenNm = new Set<number>();

        // Build lookup maps
        const byNmId = new Map<number, FunnelProduct>();
        const byVendor = new Map<string, FunnelProduct>();
        products.forEach(p => {
            byNmId.set(p.nm_id, p);
            byVendor.set(p.vendor_code.toLowerCase(), p);
        });

        for (const code of codes) {
            // Try nm_id (numeric)
            const asNum = parseInt(code, 10);
            let product: FunnelProduct | undefined;
            if (!isNaN(asNum) && byNmId.has(asNum)) {
                product = byNmId.get(asNum);
            }
            // Try vendor_code
            if (!product) {
                product = byVendor.get(code.toLowerCase());
            }
            if (product && !seenNm.has(product.nm_id)) {
                seenNm.add(product.nm_id);
                found.push(product);
            } else if (!product) {
                notFound.push(code);
            }
        }
        setPasteResolved({ found, notFound });
    }, [products]);

    const handlePasteChange = (text: string) => {
        setPasteText(text);
        resolvePaste(text);
    };

    const applyPasteTag = async (tagId: number) => {
        if (pasteResolved.found.length === 0) return;
        setBulkSaving(true);
        try {
            await api.updateProductTagMapping({
                nm_ids: pasteResolved.found.map(p => p.nm_id),
                add_tags: [tagId],
                remove_tags: [],
            });
            await loadData();
            setPasteText('');
            setPasteResolved({ found: [], notFound: [] });
            setMsg('');
        } catch (e: unknown) {
            setMsg(e instanceof Error ? e.message : 'Ошибка');
        }
        setBulkSaving(false);
    };

    const applyPasteStatus = async (status: string) => {
        if (pasteResolved.found.length === 0 || !status) return;
        setBulkSaving(true);
        try {
            await api.bulkSetProductStatus({
                nm_ids: pasteResolved.found.map(p => p.nm_id),
                status,
            });
            const updated = { ...statuses };
            pasteResolved.found.forEach(p => { updated[String(p.nm_id)] = status; });
            setStatuses(updated);
            setPasteText('');
            setPasteResolved({ found: [], notFound: [] });
            setMsg('');
        } catch (e: unknown) {
            setMsg(e instanceof Error ? e.message : 'Ошибка');
        }
        setBulkSaving(false);
    };

    // --- Sync nomenclature (imt_id) ---
    const [syncing, setSyncing] = useState(false);
    const handleSyncNomenclature = async () => {
        setSyncing(true);
        try {
            await api.syncNomenclature();
            await loadData();
        } catch (e: unknown) {
            setMsg(e instanceof Error ? e.message : 'Ошибка синхронизации');
        }
        setSyncing(false);
    };

    // --- Excel export ---
    const handleExport = () => {
        const rows = filteredProducts.map(p => {
            const nmTags = (tagMapping[String(p.nm_id)] || [])
                .map(tid => tags.find(t => t.id === tid)?.name)
                .filter(Boolean)
                .join(', ');
            const status = statuses[String(p.nm_id)] || '';
            return {
                'Артикул': p.vendor_code,
                'nm_id': p.nm_id,
                'Бренд': p.brand,
                'Статус': status ? STATUS_LABELS[status] || status : '',
                'Ярлыки': nmTags,
                'Склейка (imt_id)': p.imt_id || '',
            };
        });
        exportToExcel(rows, 'product_classification');
    };

    // --- Filter ---
    const brands = [...new Set(products.map(p => p.brand))].sort();
    const imtIds = [...new Set(products.map(p => p.imt_id).filter(Boolean))].sort((a, b) => (a ?? 0) - (b ?? 0));

    const filteredProducts = products.filter(p => {
        if (search) {
            const s = search.toLowerCase();
            if (!p.vendor_code.toLowerCase().includes(s) && !String(p.nm_id).includes(s)) return false;
        }
        if (brandFilter && p.brand !== brandFilter) return false;
        if (statusFilter && statuses[String(p.nm_id)] !== statusFilter) return false;
        if (tagFilter) {
            const nmTags = tagMapping[String(p.nm_id)] || [];
            if (!nmTags.includes(Number(tagFilter))) return false;
        }
        if (imtFilter && String(p.imt_id) !== imtFilter) return false;
        return true;
    });

    if (loading) {
        return <div className="glass-card" style={{ padding: 40, textAlign: 'center' }}>Загрузка товаров...</div>;
    }

    if (error) {
        return (
            <div className="glass-card" style={{ padding: 20 }}>
                <p style={{ color: 'var(--color-danger)' }}>{error}</p>
                <button className="btn btn-primary btn-sm" onClick={loadData} style={{ marginTop: 8 }}>Повторить</button>
            </div>
        );
    }

    return (
        <div style={{ display: 'flex', gap: 16 }}>
            {/* Left panel — Tags & Statuses */}
            <div style={{ width: 280, flexShrink: 0 }}>
                {/* Tags section */}
                <div className="glass-card" style={{ padding: 16, marginBottom: 12 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>Ярлыки</h3>
                        <button className="btn btn-primary btn-sm" onClick={() => { setShowTagForm(true); setEditingTagId(null); setTagName(''); setTagColor(DEFAULT_COLORS[tags.length % DEFAULT_COLORS.length]); }}>
                            + Добавить
                        </button>
                    </div>

                    {showTagForm && (
                        <div style={{ marginBottom: 12, padding: 10, background: 'var(--color-bg-input)', borderRadius: 8 }}>
                            <input className="form-input" placeholder="Название" value={tagName} onChange={e => setTagName(e.target.value)}
                                style={{ marginBottom: 8, width: '100%' }} />
                            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
                                {DEFAULT_COLORS.map(c => (
                                    <button key={c} onClick={() => setTagColor(c)}
                                        style={{ width: 24, height: 24, borderRadius: '50%', background: c, border: tagColor === c ? '2px solid white' : '2px solid transparent', cursor: 'pointer' }} />
                                ))}
                            </div>
                            <div style={{ display: 'flex', gap: 8 }}>
                                <button className="btn btn-primary btn-sm" onClick={saveTag}>{editingTagId ? 'Сохранить' : 'Создать'}</button>
                                <button className="btn btn-secondary btn-sm" onClick={() => setShowTagForm(false)}>Отмена</button>
                            </div>
                            {msg && <p style={{ color: 'var(--color-danger)', fontSize: 12, marginTop: 4 }}>{msg}</p>}
                        </div>
                    )}

                    {tags.length === 0 ? (
                        <p style={{ color: 'var(--color-text-dim)', fontSize: 13 }}>Нет ярлыков</p>
                    ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                            {tags.map(tag => (
                                <div key={tag.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px', borderRadius: 6, background: 'var(--color-bg-input)' }}>
                                    <span style={{ width: 12, height: 12, borderRadius: '50%', background: tag.color, flexShrink: 0 }} />
                                    <span style={{ flex: 1, fontSize: 13 }}>{tag.name}</span>
                                    <button onClick={() => startEditTag(tag)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 12, color: 'var(--color-text-dim)' }} title="Редактировать">✏️</button>
                                    <button onClick={() => deleteTag(tag.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 12, color: 'var(--color-text-dim)' }} title="Удалить">🗑</button>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {/* Statuses section */}
                <div className="glass-card" style={{ padding: 16, marginBottom: 12 }}>
                    <h3 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 600 }}>Статусы</h3>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        {STATUS_OPTIONS.filter(s => s.value).map(s => (
                            <div key={s.value} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px' }}>
                                <span style={{ width: 10, height: 10, borderRadius: '50%', background: STATUS_COLORS[s.value] }} />
                                <span style={{ fontSize: 13 }}>{s.label}</span>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Bulk actions (checkbox) */}
                {selected.size > 0 && (
                    <div className="glass-card" style={{ padding: 16 }}>
                        <h3 style={{ margin: '0 0 8px', fontSize: 14, fontWeight: 600 }}>
                            Выбрано: {selected.size}
                        </h3>
                        <div style={{ marginBottom: 8 }}>
                            <label className="form-label" style={{ fontSize: 12 }}>Назначить ярлык:</label>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                {tags.map(t => (
                                    <button key={t.id} className="btn btn-sm" onClick={() => bulkAssignTag(t.id)}
                                        style={{ background: t.color, color: '#fff', fontSize: 11, padding: '2px 8px' }}>
                                        + {t.name}
                                    </button>
                                ))}
                            </div>
                        </div>
                        <div style={{ marginBottom: 8 }}>
                            <label className="form-label" style={{ fontSize: 12 }}>Убрать ярлык:</label>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                {tags.map(t => (
                                    <button key={t.id} className="btn btn-sm btn-secondary" onClick={() => bulkRemoveTag(t.id)}
                                        style={{ fontSize: 11, padding: '2px 8px' }}>
                                        - {t.name}
                                    </button>
                                ))}
                            </div>
                        </div>
                        <div>
                            <label className="form-label" style={{ fontSize: 12 }}>Сменить статус:</label>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                {STATUS_OPTIONS.filter(s => s.value).map(s => (
                                    <button key={s.value} className="btn btn-sm btn-secondary" onClick={() => bulkSetStatus(s.value)}
                                        style={{ fontSize: 11, padding: '2px 8px' }}>
                                        {s.label}
                                    </button>
                                ))}
                            </div>
                        </div>
                    </div>
                )}
            </div>

            {/* Right panel — Product table */}
            <div style={{ flex: 1, minWidth: 0 }}>
                <div className="glass-card" style={{ padding: 16 }}>
                    {/* Filters + Export */}
                    <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                        <input className="form-input" placeholder="Поиск по артикулу / nm_id"
                            value={search} onChange={e => setSearch(e.target.value)}
                            style={{ width: 200 }} />
                        <select className="form-input" value={brandFilter} onChange={e => setBrandFilter(e.target.value)} style={{ width: 160 }}>
                            <option value="">Все бренды</option>
                            {brands.map(b => <option key={b} value={b}>{b}</option>)}
                        </select>
                        <select className="form-input" value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={{ width: 140 }}>
                            <option value="">Все статусы</option>
                            {STATUS_OPTIONS.filter(s => s.value).map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                        </select>
                        <select className="form-input" value={tagFilter} onChange={e => setTagFilter(e.target.value)} style={{ width: 140 }}>
                            <option value="">Все ярлыки</option>
                            {tags.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                        </select>
                        <select className="form-input" value={imtFilter} onChange={e => setImtFilter(e.target.value)} style={{ width: 140 }}>
                            <option value="">Все склейки</option>
                            {imtIds.map(id => <option key={id} value={String(id)}>#{id}</option>)}
                        </select>
                        <button className="btn btn-secondary btn-sm" onClick={loadData}>Обновить</button>
                        <button className="btn btn-secondary btn-sm" onClick={handleExport}>Excel</button>
                        <button className="btn btn-secondary btn-sm" onClick={() => setShowBulkPaste(!showBulkPaste)}
                            style={showBulkPaste ? { background: 'var(--color-primary)', color: '#fff' } : {}}>
                            Массовая привязка
                        </button>
                        <button className="btn btn-secondary btn-sm" onClick={handleSyncNomenclature} disabled={syncing}>
                            {syncing ? 'Синхронизация...' : 'Обновить склейки'}
                        </button>
                    </div>

                    {/* Bulk paste panel */}
                    {showBulkPaste && (
                        <div style={{ marginBottom: 16, padding: 16, background: 'var(--color-bg-input)', borderRadius: 8 }}>
                            <div style={{ display: 'flex', gap: 16 }}>
                                <div style={{ flex: 1 }}>
                                    <label className="form-label" style={{ fontSize: 12, marginBottom: 4 }}>
                                        Вставьте артикулы или nm_id (по одному на строку, или через запятую/Tab из Excel):
                                    </label>
                                    <textarea
                                        className="form-input"
                                        value={pasteText}
                                        onChange={e => handlePasteChange(e.target.value)}
                                        placeholder={'Пример:\nx99_2680v4\nhdd_500\n701808093\n...'}
                                        style={{ width: '100%', height: 120, resize: 'vertical', fontFamily: 'monospace', fontSize: 12 }}
                                    />
                                    {pasteResolved.found.length > 0 && (
                                        <p style={{ fontSize: 12, color: 'var(--color-success)', marginTop: 4 }}>
                                            Найдено: {pasteResolved.found.length} товаров
                                        </p>
                                    )}
                                    {pasteResolved.notFound.length > 0 && (
                                        <p style={{ fontSize: 12, color: 'var(--color-danger)', marginTop: 4 }}>
                                            Не найдено ({pasteResolved.notFound.length}): {pasteResolved.notFound.slice(0, 5).join(', ')}
                                            {pasteResolved.notFound.length > 5 && ` ...и ещё ${pasteResolved.notFound.length - 5}`}
                                        </p>
                                    )}
                                </div>
                                <div style={{ width: 200, flexShrink: 0 }}>
                                    {pasteResolved.found.length > 0 && (
                                        <>
                                            <label className="form-label" style={{ fontSize: 12, marginBottom: 4 }}>Назначить ярлык:</label>
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 12 }}>
                                                {tags.map(t => (
                                                    <button key={t.id} className="btn btn-sm" onClick={() => applyPasteTag(t.id)}
                                                        disabled={bulkSaving}
                                                        style={{ background: t.color, color: '#fff', fontSize: 12 }}>
                                                        + {t.name}
                                                    </button>
                                                ))}
                                                {tags.length === 0 && <p style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>Сначала создайте ярлык</p>}
                                            </div>
                                            <label className="form-label" style={{ fontSize: 12, marginBottom: 4 }}>Установить статус:</label>
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                                {STATUS_OPTIONS.filter(s => s.value).map(s => (
                                                    <button key={s.value} className="btn btn-sm btn-secondary" onClick={() => applyPasteStatus(s.value)}
                                                        disabled={bulkSaving}
                                                        style={{ fontSize: 12 }}>
                                                        {s.label}
                                                    </button>
                                                ))}
                                            </div>
                                        </>
                                    )}
                                    {pasteResolved.found.length === 0 && pasteText.trim() && (
                                        <p style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 20 }}>
                                            Ни один товар не найден
                                        </p>
                                    )}
                                </div>
                            </div>
                        </div>
                    )}

                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 8 }}>
                        Показано: {formatNumber(filteredProducts.length, 0)} из {formatNumber(products.length, 0)}
                    </div>

                    {/* Table */}
                    <div style={{ overflowX: 'auto', maxHeight: 600 }}>
                        <table className="data-table" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
                            <thead>
                                <tr>
                                    <th style={{ width: 36 }}>
                                        <input type="checkbox" checked={selected.size === filteredProducts.length && filteredProducts.length > 0}
                                            onChange={selectAll} />
                                    </th>
                                    <th>Артикул</th>
                                    <th>nm_id</th>
                                    <th>Бренд</th>
                                    <th>Статус</th>
                                    <th>Ярлыки</th>
                                    <th>Склейка</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filteredProducts.length === 0 ? (
                                    <tr><td colSpan={7} style={{ textAlign: 'center', padding: 20, color: 'var(--color-text-dim)' }}>
                                        {products.length === 0 ? 'Нет товаров. Синхронизируйте данные из WB.' : 'Нет товаров по фильтрам'}
                                    </td></tr>
                                ) : filteredProducts.slice(0, 500).map(p => {
                                    const nmTags = tagMapping[String(p.nm_id)] || [];
                                    const status = statuses[String(p.nm_id)] || '';
                                    return (
                                        <tr key={p.nm_id}>
                                            <td>
                                                <input type="checkbox" checked={selected.has(p.nm_id)}
                                                    onChange={() => {
                                                        const next = new Set(selected);
                                                        if (next.has(p.nm_id)) next.delete(p.nm_id);
                                                        else next.add(p.nm_id);
                                                        setSelected(next);
                                                    }} />
                                            </td>
                                            <td style={{ fontWeight: 500 }}>{p.vendor_code}</td>
                                            <td style={{ color: 'var(--color-text-dim)' }}>{p.nm_id}</td>
                                            <td>{p.brand}</td>
                                            <td>
                                                <select value={status} onChange={e => handleStatusChange(p.nm_id, e.target.value)}
                                                    style={{
                                                        background: 'var(--color-bg-input)',
                                                        border: '1px solid var(--color-border)',
                                                        borderRadius: 4,
                                                        padding: '2px 4px',
                                                        fontSize: 12,
                                                        color: status ? STATUS_COLORS[status] : 'var(--color-text-dim)',
                                                        fontWeight: status ? 600 : 400,
                                                    }}>
                                                    {STATUS_OPTIONS.map(s => (
                                                        <option key={s.value} value={s.value}>{s.label}</option>
                                                    ))}
                                                </select>
                                            </td>
                                            <td>
                                                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                                                    {nmTags.length === 0 ? (
                                                        <span style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>—</span>
                                                    ) : nmTags.map(tagId => {
                                                        const tag = tags.find(t => t.id === tagId);
                                                        if (!tag) return null;
                                                        return (
                                                            <span key={tagId} style={{
                                                                display: 'inline-block',
                                                                padding: '1px 8px',
                                                                borderRadius: 12,
                                                                fontSize: 11,
                                                                fontWeight: 500,
                                                                background: tag.color + '22',
                                                                color: tag.color,
                                                                border: `1px solid ${tag.color}44`,
                                                            }}>
                                                                {tag.name}
                                                            </span>
                                                        );
                                                    })}
                                                </div>
                                            </td>
                                            <td style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>
                                                {p.imt_id ? `#${p.imt_id}` : '—'}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                    {filteredProducts.length > 500 && (
                        <p style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 8 }}>
                            Показаны первые 500 товаров. Используйте фильтры для поиска.
                        </p>
                    )}
                </div>
            </div>
        </div>
    );
}
