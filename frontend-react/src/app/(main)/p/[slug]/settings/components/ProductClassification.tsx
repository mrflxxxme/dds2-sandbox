'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/lib/api';
import type { ProductTag, ProductSubcategory, DetectedSize, FunnelProduct } from '@/types/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import { BarcodeBulkAssign } from './BarcodeBulkAssign';

// Размер из артикула (зеркало backend article_parser.parse_size): NNN[xх*]NNN
const SIZE_RE = /(\d{2,4})\s*[xх*]\s*(\d{2,4})/i;
const parseSize = (article: string): string => {
    const m = SIZE_RE.exec(article || '');
    return m ? `${m[1]}x${m[2]}` : '';
};

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
    new: 'var(--color-accent)',
    clearance: 'var(--color-danger)',
};

const DEFAULT_COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#06B6D4', '#F97316'];

type Mode = 'products' | 'refs';

/* ─── Small presentational helpers ─────────────────────────────── */

function ColorPicker({ value, onChange }: { value: string; onChange: (c: string) => void }) {
    return (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {DEFAULT_COLORS.map(c => (
                <button key={c} type="button" onClick={() => onChange(c)}
                    style={{ width: 24, height: 24, borderRadius: '50%', background: c, border: value === c ? '2px solid var(--color-text)' : '2px solid transparent', cursor: 'pointer', padding: 0 }} />
            ))}
        </div>
    );
}

function Chip({ label, onClear }: { label: string; onClear: () => void }) {
    return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 6px 3px 10px', borderRadius: 16, fontSize: 12, background: 'rgba(0,113,227,0.08)', color: 'var(--color-accent)', border: '1px solid rgba(0,113,227,0.2)' }}>
            {label}
            <button type="button" onClick={onClear} title="Убрать фильтр"
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 16, height: 16, borderRadius: '50%', border: 'none', background: 'rgba(0,113,227,0.15)', color: 'var(--color-accent)', cursor: 'pointer', fontSize: 11, lineHeight: 1, padding: 0 }}>×</button>
        </span>
    );
}

/* ─── Main component ───────────────────────────────────────────── */

export function ProductClassification() {
    const [mode, setMode] = useState<Mode>('products');

    const [tags, setTags] = useState<ProductTag[]>([]);
    const [products, setProducts] = useState<FunnelProduct[]>([]);
    const [tagMapping, setTagMapping] = useState<Record<string, number[]>>({});
    const [statuses, setStatuses] = useState<Record<string, string>>({});
    const [imtAliases, setImtAliases] = useState<Record<string, string>>({});
    const [editingImtId, setEditingImtId] = useState<number | null>(null);
    const [editingImtNmId, setEditingImtNmId] = useState<number | null>(null); // which row shows the input
    const [editingImtName, setEditingImtName] = useState('');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [selected, setSelected] = useState<Set<number>>(new Set());

    // Filters
    const [search, setSearch] = useState('');
    const [brandFilter, setBrandFilter] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [tagFilter, setTagFilter] = useState('');
    const [imtFilter, setImtFilter] = useState('');
    const [sizeFilter, setSizeFilter] = useState('');
    const [subcatFilter, setSubcatFilter] = useState('');
    const [categoryFilter, setCategoryFilter] = useState('');
    const [showFilters, setShowFilters] = useState(false);

    // Sizes (overrides + aliases)
    const [sizes, setSizes] = useState<DetectedSize[]>([]);
    const [sizeOverrides, setSizeOverrides] = useState<Record<string, string>>({});
    const [sizeAliases, setSizeAliases] = useState<Record<string, string>>({});
    const [editingSizeRaw, setEditingSizeRaw] = useState<string | null>(null); // raw_size в режиме переименования
    const [editingSizeName, setEditingSizeName] = useState('');
    const [editingSizeNm, setEditingSizeNm] = useState<number | null>(null); // per-row перенос размера
    const [editingSizeVal, setEditingSizeVal] = useState('');

    // Categories (override → предмет WB → «Без категории»)
    const [categoryOverrides, setCategoryOverrides] = useState<Record<string, string>>({});
    const [editingCatNm, setEditingCatNm] = useState<number | null>(null);
    const [editingCatVal, setEditingCatVal] = useState('');
    const [bulkCategoryValue, setBulkCategoryValue] = useState('');

    // Sub-categories (винтаж/обычные — одна на товар)
    const [subcategories, setSubcategories] = useState<ProductSubcategory[]>([]);
    const [subcatMapping, setSubcatMapping] = useState<Record<string, number>>({});
    const [showSubForm, setShowSubForm] = useState(false);
    const [subName, setSubName] = useState('');
    const [subColor, setSubColor] = useState('#8B5CF6');
    const [editingSubId, setEditingSubId] = useState<number | null>(null);

    // Tag form
    const [showTagForm, setShowTagForm] = useState(false);
    const [tagName, setTagName] = useState('');
    const [tagColor, setTagColor] = useState('#3B82F6');
    const [editingTagId, setEditingTagId] = useState<number | null>(null);
    const [msg, setMsg] = useState('');

    // Bulk size value (selection bar)
    const [bulkSizeValue, setBulkSizeValue] = useState('');

    // Bulk paste (modal)
    const [showPasteModal, setShowPasteModal] = useState(false);
    const [pasteTab, setPasteTab] = useState<'barcode' | 'article'>('barcode');
    const [pasteText, setPasteText] = useState('');
    const [pasteResolved, setPasteResolved] = useState<{ found: FunnelProduct[]; notFound: string[] }>({ found: [], notFound: [] });
    const [bulkSaving, setBulkSaving] = useState(false);

    const [syncing, setSyncing] = useState(false);

    const loadData = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [tagsRes, productsRes, mappingRes, statusesRes, aliasesRes, sizesRes, sizeOvRes, sizeAlRes, subsRes, subMapRes, catOvRes] = await Promise.all([
                api.getProductTags(),
                api.getFunnelProducts(),
                api.getProductTagMapping(),
                api.getProductStatuses(),
                api.getImtAliases(),
                api.getSizes(),
                api.getSizeOverrides(),
                api.getSizeAliases(),
                api.getSubcategories(),
                api.getSubcategoryMapping(),
                api.getCategoryOverrides(),
            ]);
            setTags(tagsRes);
            setProducts(productsRes.products);
            setTagMapping(mappingRes);
            setStatuses(statusesRes);
            setImtAliases(aliasesRes);
            setSizes(sizesRes);
            setSizeOverrides(sizeOvRes);
            setSizeAliases(sizeAlRes);
            setSubcategories(subsRes);
            setSubcatMapping(subMapRes);
            setCategoryOverrides(catOvRes);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, []);

    useEffect(() => { loadData(); }, [loadData]);

    // --- Effective size of a product: override → parse → «Без размера», затем алиас ---
    const effSize = useCallback((p: FunnelProduct): string => {
        const raw = sizeOverrides[String(p.nm_id)] || parseSize(p.vendor_code) || 'Без размера';
        return sizeAliases[raw] || raw;
    }, [sizeOverrides, sizeAliases]);

    // --- Effective category: override → предмет WB (subject) → «Без категории» ---
    const effCategory = useCallback((p: FunnelProduct): string => {
        return categoryOverrides[String(p.nm_id)] || p.subject || 'Без категории';
    }, [categoryOverrides]);

    // --- Derived ---
    const brands = useMemo(() => [...new Set(products.map(p => p.brand))].sort(), [products]);
    const categories = useMemo(() => [...new Set(products.map(effCategory))].sort(), [products, effCategory]);
    const imtIds = useMemo(() => [...new Set(products.map(p => p.imt_id).filter(Boolean))].sort((a, b) => (a ?? 0) - (b ?? 0)), [products]);

    const filteredProducts = useMemo(() => products.filter(p => {
        if (search) {
            const s = search.toLowerCase();
            if (!p.vendor_code.toLowerCase().includes(s) && !String(p.nm_id).includes(s)) return false;
        }
        if (brandFilter && p.brand !== brandFilter) return false;
        if (categoryFilter && effCategory(p) !== categoryFilter) return false;
        if (statusFilter && statuses[String(p.nm_id)] !== statusFilter) return false;
        if (tagFilter) {
            const nmTags = tagMapping[String(p.nm_id)] || [];
            if (!nmTags.includes(Number(tagFilter))) return false;
        }
        if (imtFilter && String(p.imt_id) !== imtFilter) return false;
        if (sizeFilter && effSize(p) !== sizeFilter) return false;
        if (subcatFilter && String(subcatMapping[String(p.nm_id)] ?? '') !== subcatFilter) return false;
        return true;
    }), [products, search, brandFilter, categoryFilter, statusFilter, tagFilter, imtFilter, sizeFilter, subcatFilter, statuses, tagMapping, subcatMapping, effSize, effCategory]);

    // Таблица показывает первые 500 → выбор/«выбрать все» работают РОВНО по видимым строкам (не по скрытым)
    const visibleProducts = useMemo(() => filteredProducts.slice(0, 500), [filteredProducts]);
    const allVisibleSelected = visibleProducts.length > 0 && visibleProducts.every(p => selected.has(p.nm_id));

    const activeFilters = [
        search && { key: 'search', label: `Поиск: ${search}`, clear: () => setSearch('') },
        brandFilter && { key: 'brand', label: `Бренд: ${brandFilter}`, clear: () => setBrandFilter('') },
        categoryFilter && { key: 'category', label: `Категория: ${categoryFilter}`, clear: () => setCategoryFilter('') },
        statusFilter && { key: 'status', label: `Статус: ${STATUS_LABELS[statusFilter] || statusFilter}`, clear: () => setStatusFilter('') },
        tagFilter && { key: 'tag', label: `Ярлык: ${tags.find(t => String(t.id) === tagFilter)?.name || tagFilter}`, clear: () => setTagFilter('') },
        imtFilter && { key: 'imt', label: `Склейка: #${imtFilter}`, clear: () => setImtFilter('') },
        sizeFilter && { key: 'size', label: `Размер: ${sizeFilter}`, clear: () => setSizeFilter('') },
        subcatFilter && { key: 'subcat', label: `Под-категория: ${subcategories.find(s => String(s.id) === subcatFilter)?.name || subcatFilter}`, clear: () => setSubcatFilter('') },
    ].filter(Boolean) as { key: string; label: string; clear: () => void }[];

    const clearAllFilters = () => {
        setSearch(''); setBrandFilter(''); setCategoryFilter(''); setStatusFilter(''); setTagFilter(''); setImtFilter(''); setSizeFilter(''); setSubcatFilter('');
    };

    // --- Tag CRUD ---
    const saveTag = async () => {
        if (!tagName.trim()) return;
        try {
            await api.upsertProductTag({ ...(editingTagId ? { id: editingTagId } : {}), name: tagName.trim(), color: tagColor });
            setTagName(''); setTagColor('#3B82F6'); setEditingTagId(null); setShowTagForm(false); setMsg('');
            await loadData();
        } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
    };
    const deleteTag = async (id: number) => {
        if (!confirm('Удалить ярлык? Он будет снят со всех товаров.')) return;
        try { await api.deleteProductTag(id); await loadData(); }
        catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
    };
    const startEditTag = (tag: ProductTag) => {
        setEditingTagId(tag.id); setTagName(tag.name); setTagColor(tag.color); setShowTagForm(true);
    };

    // --- Status ---
    const handleStatusChange = async (nmId: number, status: string) => {
        try {
            if (status === '') return;
            await api.setProductStatus({ nm_id: nmId, status });
            setStatuses(prev => ({ ...prev, [String(nmId)]: status }));
        } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
    };

    // --- Selection / bulk (checkbox-based) ---
    const selectAll = () => {
        setSelected(allVisibleSelected ? new Set() : new Set(visibleProducts.map(p => p.nm_id)));
    };
    const toggleOne = (nmId: number) => {
        const next = new Set(selected);
        if (next.has(nmId)) next.delete(nmId); else next.add(nmId);
        setSelected(next);
    };

    // Единый раннер bulk-операций: try/catch (фидбэк об ошибке) + сброс выделения после успеха (нет stale-selection)
    const runBulk = async (fn: () => Promise<void>) => {
        if (selected.size === 0) return;
        try {
            await fn();
            setSelected(new Set());
            setMsg('');
        } catch (e: unknown) {
            setMsg(e instanceof Error ? e.message : 'Ошибка');
        }
    };
    const bulkAssignTag = (tagId: number) => runBulk(async () => {
        await api.updateProductTagMapping({ nm_ids: Array.from(selected), add_tags: [tagId], remove_tags: [] });
        await loadData();
    });
    const bulkRemoveTag = (tagId: number) => runBulk(async () => {
        await api.updateProductTagMapping({ nm_ids: Array.from(selected), add_tags: [], remove_tags: [tagId] });
        await loadData();
    });
    const bulkSetStatus = (status: string) => runBulk(async () => {
        await api.bulkSetProductStatus({ nm_ids: Array.from(selected), status });
        const updated = { ...statuses };
        selected.forEach(nmId => { updated[String(nmId)] = status; });
        setStatuses(updated);
    });
    const bulkSetSize = (value: string) => runBulk(async () => {
        await api.bulkSetSizeOverride(Array.from(selected), value);
        await loadData();
    });
    const bulkSetCategory = (value: string) => runBulk(async () => {
        await api.bulkSetCategoryOverride(Array.from(selected), value);
        await loadData();
    });
    const bulkSetSubcat = (subId: number | null) => runBulk(async () => {
        await api.bulkSetSubcategory(Array.from(selected), subId);
        await loadData();
    });

    // --- Bulk paste resolve ---
    const resolvePaste = useCallback((text: string) => {
        if (!text.trim()) { setPasteResolved({ found: [], notFound: [] }); return; }
        const codes = text.split(/[\n\r\t,;]+/).map(s => s.trim()).filter(Boolean);
        const found: FunnelProduct[] = [];
        const notFound: string[] = [];
        const seenNm = new Set<number>();
        const byNmId = new Map<number, FunnelProduct>();
        const byVendor = new Map<string, FunnelProduct>();
        products.forEach(p => { byNmId.set(p.nm_id, p); byVendor.set(p.vendor_code.toLowerCase(), p); });
        for (const code of codes) {
            const asNum = parseInt(code, 10);
            let product: FunnelProduct | undefined;
            if (!isNaN(asNum) && byNmId.has(asNum)) product = byNmId.get(asNum);
            if (!product) product = byVendor.get(code.toLowerCase());
            if (product && !seenNm.has(product.nm_id)) { seenNm.add(product.nm_id); found.push(product); }
            else if (!product) notFound.push(code);
        }
        setPasteResolved({ found, notFound });
    }, [products]);

    const handlePasteChange = (text: string) => { setPasteText(text); resolvePaste(text); };
    const closePasteModal = () => { setShowPasteModal(false); setPasteText(''); setPasteResolved({ found: [], notFound: [] }); };

    const applyPasteTag = async (tagId: number) => {
        if (pasteResolved.found.length === 0) return;
        setBulkSaving(true);
        try {
            await api.updateProductTagMapping({ nm_ids: pasteResolved.found.map(p => p.nm_id), add_tags: [tagId], remove_tags: [] });
            await loadData(); closePasteModal(); setMsg('');
        } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
        setBulkSaving(false);
    };
    const applyPasteStatus = async (status: string) => {
        if (pasteResolved.found.length === 0 || !status) return;
        setBulkSaving(true);
        try {
            await api.bulkSetProductStatus({ nm_ids: pasteResolved.found.map(p => p.nm_id), status });
            const updated = { ...statuses };
            pasteResolved.found.forEach(p => { updated[String(p.nm_id)] = status; });
            setStatuses(updated); closePasteModal(); setMsg('');
        } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
        setBulkSaving(false);
    };

    // --- IMT alias editing ---
    const startEditImtAlias = (imtId: number, nmId: number) => {
        setEditingImtId(imtId); setEditingImtNmId(nmId); setEditingImtName(imtAliases[String(imtId)] || '');
    };
    const saveImtAlias = async () => {
        if (editingImtId === null) return;
        const name = editingImtName.trim();
        if (!name) { setEditingImtId(null); setEditingImtNmId(null); return; }
        try {
            await api.setImtAlias({ imt_id: editingImtId, name });
            setImtAliases(prev => ({ ...prev, [String(editingImtId)]: name }));
        } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
        setEditingImtId(null); setEditingImtNmId(null);
    };

    // --- Size rename (alias) ---
    const startEditSize = (rawSize: string) => { setEditingSizeRaw(rawSize); setEditingSizeName(sizeAliases[rawSize] || ''); };
    const saveSizeRename = async () => {
        if (editingSizeRaw === null) return;
        try { await api.setSizeAlias(editingSizeRaw, editingSizeName.trim()); await loadData(); }
        catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
        setEditingSizeRaw(null); setEditingSizeName('');
    };

    // --- Per-row size override (перенос) ---
    const startEditProductSize = (p: FunnelProduct) => {
        setEditingSizeNm(p.nm_id);
        setEditingSizeVal(sizeOverrides[String(p.nm_id)] || parseSize(p.vendor_code) || '');
    };
    const saveProductSize = async () => {
        if (editingSizeNm === null) return;
        try { await api.bulkSetSizeOverride([editingSizeNm], editingSizeVal.trim()); await loadData(); }
        catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
        setEditingSizeNm(null); setEditingSizeVal('');
    };

    // --- Per-row category override (перенос в категорию) ---
    const startEditProductCategory = (p: FunnelProduct) => {
        setEditingCatNm(p.nm_id);
        setEditingCatVal(categoryOverrides[String(p.nm_id)] || p.subject || '');
    };
    const saveProductCategory = async () => {
        if (editingCatNm === null) return;
        try { await api.bulkSetCategoryOverride([editingCatNm], editingCatVal.trim()); await loadData(); }
        catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
        setEditingCatNm(null); setEditingCatVal('');
    };

    // --- Sub-category CRUD + assignment ---
    const saveSub = async () => {
        if (!subName.trim()) return;
        try {
            await api.upsertSubcategory({ ...(editingSubId ? { id: editingSubId } : {}), name: subName.trim(), color: subColor });
            setSubName(''); setSubColor('#8B5CF6'); setEditingSubId(null); setShowSubForm(false); setMsg('');
            await loadData();
        } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
    };
    const deleteSub = async (id: number) => {
        if (!confirm('Удалить под-категорию? Она будет снята со всех товаров.')) return;
        try { await api.deleteSubcategory(id); await loadData(); }
        catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
    };
    const startEditSub = (s: ProductSubcategory) => { setEditingSubId(s.id); setSubName(s.name); setSubColor(s.color); setShowSubForm(true); };
    const setProductSubcat = async (nmId: number, subId: number | null) => {
        await api.bulkSetSubcategory([nmId], subId);
        setSubcatMapping(prev => {
            const next = { ...prev };
            if (subId === null) delete next[String(nmId)]; else next[String(nmId)] = subId;
            return next;
        });
    };

    // --- Sync nomenclature (imt_id) ---
    const handleSyncNomenclature = async () => {
        if (syncing) return;
        setSyncing(true);
        try {
            await api.syncNomenclature();
            await loadData();
            setMsg('Склейки обновлены');
        } catch (e: unknown) {
            const errMsg = e instanceof Error ? e.message : 'Ошибка синхронизации';
            setMsg(errMsg.includes('429') || errMsg.includes('много') ? 'Подождите минуту и попробуйте снова' : errMsg);
        } finally {
            setSyncing(false);
        }
    };

    // --- Excel export ---
    const handleExport = () => {
        const rows = filteredProducts.map(p => {
            const nmTags = (tagMapping[String(p.nm_id)] || []).map(tid => tags.find(t => t.id === tid)?.name).filter(Boolean).join(', ');
            const status = statuses[String(p.nm_id)] || '';
            const subId = subcatMapping[String(p.nm_id)];
            const subNm = subId != null ? (subcategories.find(s => s.id === subId)?.name || '') : '';
            return {
                'Артикул': p.vendor_code,
                'nm_id': p.nm_id,
                'Бренд': p.brand,
                'Категория': effCategory(p),
                'Размер': effSize(p),
                'Под-категория': subNm,
                'Статус': status ? STATUS_LABELS[status] || status : '',
                'Ярлыки': nmTags,
                'Склейка (imt_id)': p.imt_id || '',
                'Название склейки': p.imt_id ? (imtAliases[String(p.imt_id)] || '') : '',
            };
        });
        exportToExcel(rows, 'product_classification');
    };

    /* ─── Render ───────────────────────────────────────────────── */

    if (loading) {
        return <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка товаров…</div>;
    }
    if (error) {
        return (
            <div className="glass-card" style={{ padding: 20 }}>
                <p style={{ color: 'var(--color-danger)' }}>{error}</p>
                <button className="btn btn-primary btn-sm" onClick={loadData} style={{ marginTop: 8 }}>Повторить</button>
            </div>
        );
    }

    const selectStyle: React.CSSProperties = { background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 8px', fontSize: 13, color: 'var(--color-text)' };

    return (
        <div className="animate-in">
            {/* ─── Sub-nav: mode segmented control ─── */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', gap: 6 }}>
                    <button type="button" className={`sc-status-pill ${mode === 'products' ? 'sc-status-pill-active' : ''}`} onClick={() => { setMode('products'); setSelected(new Set()); }}>
                        📦 Товары <span className="sc-status-pill-count">{formatNumber(products.length, 0)}</span>
                    </button>
                    <button type="button" className={`sc-status-pill ${mode === 'refs' ? 'sc-status-pill-active' : ''}`} onClick={() => { setMode('refs'); setSelected(new Set()); }}>
                        🏷️ Справочники
                    </button>
                </div>
                {mode === 'products' && (
                    <div style={{ display: 'flex', gap: 6, fontSize: 12, color: 'var(--color-text-muted)' }}>
                        <span>{formatNumber(categories.length, 0)} категорий</span>·
                        <span>{formatNumber(tags.length, 0)} ярлыков</span>·
                        <span>{formatNumber(subcategories.length, 0)} под-категорий</span>·
                        <span>{formatNumber(sizes.length, 0)} размеров</span>
                    </div>
                )}
            </div>

            {/* ─── Global message banner ─── */}
            {msg && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '8px 14px', borderRadius: 10, marginBottom: 12, fontSize: 13, background: 'rgba(0,113,227,0.08)', color: 'var(--color-accent)' }}>
                    <span>{msg}</span>
                    <button type="button" onClick={() => setMsg('')} style={{ border: 'none', background: 'none', color: 'inherit', cursor: 'pointer', fontSize: 16, lineHeight: 1 }}>×</button>
                </div>
            )}

            {/* ═══════════════ MODE: PRODUCTS ═══════════════ */}
            {mode === 'products' && (
                <>
                    <datalist id="cat-suggestions">
                        {categories.map(c => <option key={c} value={c} />)}
                    </datalist>
                    {/* Toolbar */}
                    <div className="glass-card" style={{ padding: 14, marginBottom: 12 }}>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                            <input className="form-input" placeholder="🔍 Поиск по артикулу / nm_id" value={search} onChange={e => setSearch(e.target.value)} style={{ width: 260 }} />
                            <button type="button" className={`btn btn-sm ${showFilters || activeFilters.length ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setShowFilters(v => !v)}>
                                ⚙ Фильтры{activeFilters.length > 0 ? ` · ${activeFilters.length}` : ''}
                            </button>
                            <div style={{ flex: 1 }} />
                            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setShowPasteModal(true)}>📋 Массовая привязка</button>
                            <button type="button" className="btn btn-secondary btn-sm" onClick={handleExport}>⬇ Excel</button>
                            <button type="button" className="btn btn-secondary btn-sm" onClick={handleSyncNomenclature} disabled={syncing}>{syncing ? 'Синхронизация…' : '⟳ Склейки'}</button>
                        </div>

                        {/* Collapsible filter panel */}
                        {showFilters && (
                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--color-border)' }}>
                                <select className="form-input" value={brandFilter} onChange={e => setBrandFilter(e.target.value)} style={{ width: 170 }}>
                                    <option value="">Все бренды</option>
                                    {brands.map(b => <option key={b} value={b}>{b}</option>)}
                                </select>
                                <select className="form-input" value={categoryFilter} onChange={e => setCategoryFilter(e.target.value)} style={{ width: 170 }}>
                                    <option value="">Все категории</option>
                                    {categories.map(c => <option key={c} value={c}>{c}</option>)}
                                </select>
                                <select className="form-input" value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={{ width: 150 }}>
                                    <option value="">Все статусы</option>
                                    {STATUS_OPTIONS.filter(s => s.value).map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                                </select>
                                <select className="form-input" value={tagFilter} onChange={e => setTagFilter(e.target.value)} style={{ width: 150 }}>
                                    <option value="">Все ярлыки</option>
                                    {tags.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                                </select>
                                <select className="form-input" value={imtFilter} onChange={e => setImtFilter(e.target.value)} style={{ width: 150 }}>
                                    <option value="">Все склейки</option>
                                    {imtIds.map(id => <option key={id} value={String(id)}>#{id}</option>)}
                                </select>
                                <select className="form-input" value={sizeFilter} onChange={e => setSizeFilter(e.target.value)} style={{ width: 150 }}>
                                    <option value="">Все размеры</option>
                                    {sizes.map(s => <option key={s.raw_size} value={s.display_name}>{s.display_name}</option>)}
                                </select>
                                <select className="form-input" value={subcatFilter} onChange={e => setSubcatFilter(e.target.value)} style={{ width: 160 }}>
                                    <option value="">Все под-категории</option>
                                    {subcategories.map(s => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
                                </select>
                            </div>
                        )}

                        {/* Active filter chips + count */}
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
                            {activeFilters.map(f => <Chip key={f.key} label={f.label} onClear={f.clear} />)}
                            {activeFilters.length > 0 && (
                                <button type="button" onClick={clearAllFilters} style={{ border: 'none', background: 'none', color: 'var(--color-text-muted)', cursor: 'pointer', fontSize: 12, textDecoration: 'underline' }}>сбросить всё</button>
                            )}
                            <div style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--color-text-dim)' }}>
                                Показано {formatNumber(filteredProducts.length, 0)} из {formatNumber(products.length, 0)}
                            </div>
                        </div>
                    </div>

                    {/* Selection bar (contextual) */}
                    {selected.size > 0 && (
                        <div className="glass-card" style={{ padding: '10px 14px', marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', borderColor: 'var(--color-accent)' }}>
                            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-accent)' }}>✓ Выбрано {formatNumber(selected.size, 0)}</span>

                            {tags.length > 0 && (
                                <select value="" onChange={e => { if (e.target.value) bulkAssignTag(Number(e.target.value)); }} style={selectStyle} title="Назначить ярлык">
                                    <option value="">＋ Ярлык…</option>
                                    {tags.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                                </select>
                            )}
                            {tags.length > 0 && (
                                <select value="" onChange={e => { if (e.target.value) bulkRemoveTag(Number(e.target.value)); }} style={selectStyle} title="Снять ярлык">
                                    <option value="">－ Ярлык…</option>
                                    {tags.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                                </select>
                            )}
                            <select value="" onChange={e => { if (e.target.value) bulkSetStatus(e.target.value); }} style={selectStyle} title="Сменить статус">
                                <option value="">Статус…</option>
                                {STATUS_OPTIONS.filter(s => s.value).map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                            </select>
                            <select value="" onChange={e => { const v = e.target.value; if (v) bulkSetSubcat(v === '__none__' ? null : Number(v)); }} style={selectStyle} title="Под-категория">
                                <option value="">Под-категория…</option>
                                {subcategories.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                                <option value="__none__">— снять —</option>
                            </select>
                            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                                <input className="form-input" placeholder="Размер, напр. 200x300" value={bulkSizeValue} onChange={e => setBulkSizeValue(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter') { bulkSetSize(bulkSizeValue.trim()); setBulkSizeValue(''); } }}
                                    style={{ width: 160, fontSize: 13 }} />
                                <button type="button" className="btn btn-secondary btn-sm" onClick={() => { bulkSetSize(bulkSizeValue.trim()); setBulkSizeValue(''); }}>OK</button>
                            </div>
                            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                                <input className="form-input" list="cat-suggestions" placeholder="Категория…" value={bulkCategoryValue} onChange={e => setBulkCategoryValue(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter') { bulkSetCategory(bulkCategoryValue.trim()); setBulkCategoryValue(''); } }}
                                    style={{ width: 160, fontSize: 13 }} />
                                <button type="button" className="btn btn-secondary btn-sm" onClick={() => { bulkSetCategory(bulkCategoryValue.trim()); setBulkCategoryValue(''); }}>OK</button>
                            </div>

                            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setSelected(new Set())} style={{ marginLeft: 'auto' }}>Снять выделение</button>
                        </div>
                    )}

                    {/* Table */}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                        <div style={{ overflowX: 'auto', maxHeight: 620 }}>
                            <table className="data-table" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
                                <thead>
                                    <tr>
                                        <th style={{ width: 36 }}>
                                            <input type="checkbox" checked={allVisibleSelected} onChange={selectAll} title="Выбрать все видимые" />
                                        </th>
                                        <th>Артикул</th>
                                        <th>nm_id</th>
                                        <th>Бренд</th>
                                        <th>Категория</th>
                                        <th>Размер</th>
                                        <th>Под-категория</th>
                                        <th>Статус</th>
                                        <th>Ярлыки</th>
                                        <th>Склейка</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {filteredProducts.length === 0 ? (
                                        <tr><td colSpan={10} style={{ textAlign: 'center', padding: 28, color: 'var(--color-text-dim)' }}>
                                            {products.length === 0 ? 'Нет товаров. Синхронизируйте данные из WB.' : 'Нет товаров по фильтрам'}
                                        </td></tr>
                                    ) : visibleProducts.map(p => {
                                        const nmTags = tagMapping[String(p.nm_id)] || [];
                                        const status = statuses[String(p.nm_id)] || '';
                                        return (
                                            <tr key={p.nm_id} style={selected.has(p.nm_id) ? { background: 'rgba(0,113,227,0.05)' } : undefined}>
                                                <td>
                                                    <input type="checkbox" checked={selected.has(p.nm_id)} onChange={() => toggleOne(p.nm_id)} />
                                                </td>
                                                <td style={{ fontWeight: 500 }}>{p.vendor_code}</td>
                                                <td style={{ color: 'var(--color-text-dim)' }}>{p.nm_id}</td>
                                                <td>{p.brand}</td>
                                                <td style={{ cursor: 'pointer' }}
                                                    onClick={() => { if (editingCatNm !== p.nm_id) startEditProductCategory(p); }}
                                                    title="Клик — перенести в другую категорию">
                                                    {editingCatNm === p.nm_id ? (
                                                        <input className="form-input" list="cat-suggestions" value={editingCatVal} autoFocus
                                                            onChange={e => setEditingCatVal(e.target.value)}
                                                            onBlur={saveProductCategory}
                                                            onKeyDown={e => { if (e.key === 'Enter') saveProductCategory(); if (e.key === 'Escape') setEditingCatNm(null); }}
                                                            onClick={e => e.stopPropagation()}
                                                            style={{ width: 120, fontSize: 12, padding: '2px 4px' }} />
                                                    ) : (
                                                        <span style={{ color: categoryOverrides[String(p.nm_id)] ? 'var(--color-accent)' : 'var(--color-text)', fontSize: 12 }}>
                                                            {effCategory(p)}
                                                        </span>
                                                    )}
                                                </td>
                                                <td style={{ cursor: 'pointer' }}
                                                    onClick={() => { if (editingSizeNm !== p.nm_id) startEditProductSize(p); }}
                                                    title="Клик — перенести в другой размер">
                                                    {editingSizeNm === p.nm_id ? (
                                                        <input className="form-input" value={editingSizeVal} autoFocus
                                                            onChange={e => setEditingSizeVal(e.target.value)}
                                                            onBlur={saveProductSize}
                                                            onKeyDown={e => { if (e.key === 'Enter') saveProductSize(); if (e.key === 'Escape') setEditingSizeNm(null); }}
                                                            onClick={e => e.stopPropagation()}
                                                            style={{ width: 90, fontSize: 12, padding: '2px 4px' }} />
                                                    ) : (
                                                        <span style={{ color: sizeOverrides[String(p.nm_id)] ? 'var(--color-accent)' : 'var(--color-text)', fontSize: 12 }}>
                                                            {effSize(p)}
                                                        </span>
                                                    )}
                                                </td>
                                                <td>
                                                    <select value={subcatMapping[String(p.nm_id)] ?? ''}
                                                        onChange={e => setProductSubcat(p.nm_id, e.target.value ? Number(e.target.value) : null)}
                                                        style={{ background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '2px 4px', fontSize: 12 }}>
                                                        <option value="">—</option>
                                                        {subcategories.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                                                    </select>
                                                </td>
                                                <td>
                                                    <select value={status} onChange={e => handleStatusChange(p.nm_id, e.target.value)}
                                                        style={{ background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '2px 4px', fontSize: 12, color: status ? STATUS_COLORS[status] : 'var(--color-text-dim)', fontWeight: status ? 600 : 400 }}>
                                                        {STATUS_OPTIONS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
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
                                                                <span key={tagId} style={{ display: 'inline-block', padding: '1px 8px', borderRadius: 12, fontSize: 11, fontWeight: 500, background: tag.color + '22', color: tag.color, border: `1px solid ${tag.color}44` }}>
                                                                    {tag.name}
                                                                </span>
                                                            );
                                                        })}
                                                    </div>
                                                </td>
                                                <td style={{ fontSize: 12, cursor: p.imt_id ? 'pointer' : 'default' }}
                                                    onClick={() => { if (p.imt_id && editingImtNmId !== p.nm_id) startEditImtAlias(p.imt_id, p.nm_id); }}>
                                                    {p.imt_id ? (
                                                        editingImtNmId === p.nm_id ? (
                                                            <input className="form-input" value={editingImtName}
                                                                onChange={e => setEditingImtName(e.target.value)}
                                                                onBlur={saveImtAlias}
                                                                onKeyDown={e => { if (e.key === 'Enter') saveImtAlias(); if (e.key === 'Escape') { setEditingImtId(null); setEditingImtNmId(null); } }}
                                                                onClick={e => e.stopPropagation()}
                                                                autoFocus
                                                                style={{ width: 130, fontSize: 12, padding: '2px 4px' }}
                                                                placeholder={`#${p.imt_id}`} />
                                                        ) : (
                                                            <span style={{ color: imtAliases[String(p.imt_id)] ? 'var(--color-text)' : 'var(--color-text-dim)' }} title="Клик — задать название склейки">
                                                                {imtAliases[String(p.imt_id)] || `#${p.imt_id}`}
                                                            </span>
                                                        )
                                                    ) : <span style={{ color: 'var(--color-text-dim)' }}>—</span>}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                        {filteredProducts.length > 500 && (
                            <p style={{ fontSize: 12, color: 'var(--color-text-dim)', padding: '8px 14px', margin: 0 }}>
                                Показаны первые 500 товаров. Используйте фильтры для поиска.
                            </p>
                        )}
                    </div>
                </>
            )}

            {/* ═══════════════ MODE: REFS (taxonomy) ═══════════════ */}
            {mode === 'refs' && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16, alignItems: 'start' }}>
                    {/* Ярлыки */}
                    <div className="glass-card" style={{ padding: 16 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                            <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>🏷️ Ярлыки</h3>
                            <button type="button" className="btn btn-primary btn-sm" onClick={() => { setShowTagForm(true); setEditingTagId(null); setTagName(''); setTagColor(DEFAULT_COLORS[tags.length % DEFAULT_COLORS.length]); }}>
                                + Добавить
                            </button>
                        </div>
                        <p style={{ margin: '0 0 12px', fontSize: 12, color: 'var(--color-text-dim)' }}>Произвольные метки, можно несколько на товар.</p>

                        {showTagForm && (
                            <div style={{ marginBottom: 12, padding: 12, background: 'var(--color-bg-input)', borderRadius: 10 }}>
                                <input className="form-input" placeholder="Название" value={tagName} onChange={e => setTagName(e.target.value)} style={{ marginBottom: 8, width: '100%' }} />
                                <div style={{ marginBottom: 8 }}><ColorPicker value={tagColor} onChange={setTagColor} /></div>
                                <div style={{ display: 'flex', gap: 8 }}>
                                    <button type="button" className="btn btn-primary btn-sm" onClick={saveTag}>{editingTagId ? 'Сохранить' : 'Создать'}</button>
                                    <button type="button" className="btn btn-secondary btn-sm" onClick={() => setShowTagForm(false)}>Отмена</button>
                                </div>
                            </div>
                        )}

                        {tags.length === 0 ? (
                            <p style={{ color: 'var(--color-text-dim)', fontSize: 13 }}>Нет ярлыков</p>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                {tags.map(tag => (
                                    <div key={tag.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderRadius: 8, background: 'var(--color-bg-input)' }}>
                                        <span style={{ width: 12, height: 12, borderRadius: '50%', background: tag.color, flexShrink: 0 }} />
                                        <span style={{ flex: 1, fontSize: 13 }}>{tag.name}</span>
                                        <button type="button" onClick={() => startEditTag(tag)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 12 }} title="Редактировать">✏️</button>
                                        <button type="button" onClick={() => deleteTag(tag.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 12 }} title="Удалить">🗑</button>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Под-категории */}
                    <div className="glass-card" style={{ padding: 16 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                            <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>📂 Под-категории</h3>
                            <button type="button" className="btn btn-primary btn-sm" onClick={() => { setShowSubForm(true); setEditingSubId(null); setSubName(''); setSubColor(DEFAULT_COLORS[(subcategories.length + 4) % DEFAULT_COLORS.length]); }}>
                                + Добавить
                            </button>
                        </div>
                        <p style={{ margin: '0 0 12px', fontSize: 12, color: 'var(--color-text-dim)' }}>Винтаж / обычные и т.п. — одна на товар.</p>

                        {showSubForm && (
                            <div style={{ marginBottom: 12, padding: 12, background: 'var(--color-bg-input)', borderRadius: 10 }}>
                                <input className="form-input" placeholder="Название (винтаж, обычные…)" value={subName} onChange={e => setSubName(e.target.value)} style={{ marginBottom: 8, width: '100%' }} />
                                <div style={{ marginBottom: 8 }}><ColorPicker value={subColor} onChange={setSubColor} /></div>
                                <div style={{ display: 'flex', gap: 8 }}>
                                    <button type="button" className="btn btn-primary btn-sm" onClick={saveSub}>{editingSubId ? 'Сохранить' : 'Создать'}</button>
                                    <button type="button" className="btn btn-secondary btn-sm" onClick={() => setShowSubForm(false)}>Отмена</button>
                                </div>
                            </div>
                        )}

                        {subcategories.length === 0 ? (
                            <p style={{ color: 'var(--color-text-dim)', fontSize: 13 }}>Нет под-категорий</p>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                {subcategories.map(s => (
                                    <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderRadius: 8, background: 'var(--color-bg-input)' }}>
                                        <span style={{ width: 12, height: 12, borderRadius: '50%', background: s.color, flexShrink: 0 }} />
                                        <span style={{ flex: 1, fontSize: 13 }}>{s.name}</span>
                                        <button type="button" onClick={() => startEditSub(s)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 12 }} title="Редактировать">✏️</button>
                                        <button type="button" onClick={() => deleteSub(s.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 12 }} title="Удалить">🗑</button>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Размеры */}
                    <div className="glass-card" style={{ padding: 16 }}>
                        <h3 style={{ margin: '0 0 4px', fontSize: 15, fontWeight: 600 }}>📏 Размеры</h3>
                        <p style={{ margin: '0 0 12px', fontSize: 12, color: 'var(--color-text-dim)' }}>Клик ✏️ — переименовать (одинаковое имя у разных = объединение).</p>
                        {sizes.length === 0 ? (
                            <p style={{ color: 'var(--color-text-dim)', fontSize: 13 }}>Нет размеров</p>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 2, maxHeight: 360, overflowY: 'auto' }}>
                                {sizes.map(s => (
                                    <div key={s.raw_size} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 8px', borderRadius: 8 }}>
                                        {editingSizeRaw === s.raw_size ? (
                                            <input className="form-input" value={editingSizeName} autoFocus
                                                onChange={e => setEditingSizeName(e.target.value)}
                                                onBlur={saveSizeRename}
                                                onKeyDown={e => { if (e.key === 'Enter') saveSizeRename(); if (e.key === 'Escape') setEditingSizeRaw(null); }}
                                                placeholder={s.raw_size}
                                                style={{ flex: 1, fontSize: 12, padding: '2px 4px' }} />
                                        ) : (
                                            <>
                                                <span style={{ flex: 1, fontSize: 13 }}>{s.display_name}{s.display_name !== s.raw_size && <span style={{ color: 'var(--color-text-dim)', fontSize: 11 }}> ({s.raw_size})</span>}</span>
                                                <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{formatNumber(s.count, 0)}</span>
                                                <button type="button" onClick={() => startEditSize(s.raw_size)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 12 }} title="Переименовать">✏️</button>
                                            </>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Статусы (легенда) */}
                    <div className="glass-card" style={{ padding: 16 }}>
                        <h3 style={{ margin: '0 0 4px', fontSize: 15, fontWeight: 600 }}>🚦 Статусы</h3>
                        <p style={{ margin: '0 0 12px', fontSize: 12, color: 'var(--color-text-dim)' }}>Фиксированный набор. Назначается в таблице товаров.</p>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                            {STATUS_OPTIONS.filter(s => s.value).map(s => (
                                <div key={s.value} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px' }}>
                                    <span style={{ width: 10, height: 10, borderRadius: '50%', background: STATUS_COLORS[s.value] }} />
                                    <span style={{ fontSize: 13 }}>{s.label}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            )}

            {/* ═══════════════ Bulk paste modal ═══════════════ */}
            {showPasteModal && (
                <div className="modal-overlay" onClick={closePasteModal}>
                    <div className="modal-card modal-card-wide modal-card-solid" onClick={e => e.stopPropagation()}>
                        <h2 className="modal-title">📋 Массовая привязка</h2>
                        <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
                            <button type="button" className={`sc-status-pill ${pasteTab === 'barcode' ? 'sc-status-pill-active' : ''}`} onClick={() => setPasteTab('barcode')}>По баркоду</button>
                            <button type="button" className={`sc-status-pill ${pasteTab === 'article' ? 'sc-status-pill-active' : ''}`} onClick={() => setPasteTab('article')}>По артикулу / nm_id</button>
                        </div>
                        {pasteTab === 'barcode' && (
                            <BarcodeBulkAssign products={products} sizes={sizes} categories={categories} subcategories={subcategories} onReload={loadData} setMsg={setMsg} />
                        )}
                        {pasteTab === 'article' && (
                        <div style={{ display: 'flex', gap: 16 }}>
                            <div style={{ flex: 1 }}>
                                <label className="form-label" style={{ fontSize: 12, marginBottom: 4 }}>
                                    Вставьте артикулы или nm_id (по одному на строку, либо через запятую/Tab из Excel):
                                </label>
                                <textarea className="form-input" value={pasteText} onChange={e => handlePasteChange(e.target.value)}
                                    placeholder={'Пример:\nx99_2680v4\nhdd_500\n701808093\n…'}
                                    style={{ width: '100%', height: 180, resize: 'vertical', fontFamily: 'monospace', fontSize: 12 }} />
                                {pasteResolved.found.length > 0 && (
                                    <p style={{ fontSize: 12, color: 'var(--color-success)', marginTop: 6 }}>Найдено: {pasteResolved.found.length} товаров</p>
                                )}
                                {pasteResolved.notFound.length > 0 && (
                                    <p style={{ fontSize: 12, color: 'var(--color-danger)', marginTop: 4 }}>
                                        Не найдено ({pasteResolved.notFound.length}): {pasteResolved.notFound.slice(0, 5).join(', ')}
                                        {pasteResolved.notFound.length > 5 && ` …и ещё ${pasteResolved.notFound.length - 5}`}
                                    </p>
                                )}
                            </div>
                            <div style={{ width: 200, flexShrink: 0 }}>
                                {pasteResolved.found.length > 0 ? (
                                    <>
                                        <label className="form-label" style={{ fontSize: 12, marginBottom: 4 }}>Назначить ярлык:</label>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 12 }}>
                                            {tags.map(t => (
                                                <button key={t.id} type="button" className="btn btn-sm" onClick={() => applyPasteTag(t.id)} disabled={bulkSaving}
                                                    style={{ background: t.color, color: '#fff', fontSize: 12 }}>+ {t.name}</button>
                                            ))}
                                            {tags.length === 0 && <p style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>Сначала создайте ярлык в «Справочниках»</p>}
                                        </div>
                                        <label className="form-label" style={{ fontSize: 12, marginBottom: 4 }}>Установить статус:</label>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                            {STATUS_OPTIONS.filter(s => s.value).map(s => (
                                                <button key={s.value} type="button" className="btn btn-sm btn-secondary" onClick={() => applyPasteStatus(s.value)} disabled={bulkSaving} style={{ fontSize: 12 }}>{s.label}</button>
                                            ))}
                                        </div>
                                    </>
                                ) : pasteText.trim() ? (
                                    <p style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 20 }}>Ни один товар не найден</p>
                                ) : (
                                    <p style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 20 }}>Вставьте список слева — справа появятся действия.</p>
                                )}
                            </div>
                        </div>
                        )}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                            <button type="button" className="btn btn-secondary btn-sm" onClick={closePasteModal}>Закрыть</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
