'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import { Toast } from '@/components';
import { usePermissions } from '@/lib/hooks/usePermissions';
import type { AssemblyDraft, AssemblyRequest, AssemblyStatus, Warehouse } from '@/types/api';
import { consolidatingLanes, findDuplicateLanes } from '@/lib/utils/assemblyDraftMerge';

// ─── Status config ──────────────────────────────────────────────────────────

const STATUS_MAP: Record<AssemblyStatus, { label: string; className: string }> = {
    // PENDING — legacy: больше не используется при создании, но может встретиться в истории.
    PENDING:          { label: 'В сборке',          className: 'badge-info' },
    IN_PROGRESS:      { label: 'В сборке',          className: 'badge-info' },
    READY:            { label: 'Готово',             className: 'badge-success' },
    VEHICLE_ASSIGNED: { label: 'Машина назначена',   className: 'badge-info' },
    SHIPPED:          { label: 'Отгружена',          className: 'badge-success' },
    DELIVERED:        { label: 'Принята WB',         className: 'badge-success' },
    CANCELLED:        { label: 'Отменена',           className: 'badge-secondary' },
};

const EDITABLE_STATUSES: AssemblyStatus[] = ['IN_PROGRESS', 'READY'];

const STATUS_OPTIONS_FILTER: { value: string; label: string }[] = [
    { value: '', label: 'Все статусы' },
    { value: 'IN_PROGRESS', label: 'В сборке' },
    { value: 'READY', label: 'Готово' },
    { value: 'VEHICLE_ASSIGNED', label: 'Машина назначена' },
    { value: 'SHIPPED', label: 'Отгружена' },
    { value: 'DELIVERED', label: 'Принята WB' },
    { value: 'CANCELLED', label: 'Отменена' },
];

// ─── Status colors for Tailwind badge ────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
    PENDING:          'bg-blue-100 text-blue-700 border-blue-200 focus:ring-blue-400',
    IN_PROGRESS:      'bg-blue-100 text-blue-700 border-blue-200 focus:ring-blue-400',
    READY:            'bg-green-100 text-green-700 border-green-200 focus:ring-green-400',
    VEHICLE_ASSIGNED: 'bg-sky-100 text-sky-700 border-sky-200',
    SHIPPED:          'bg-emerald-100/60 text-emerald-600 border-emerald-200',
    DELIVERED:        'bg-emerald-100 text-emerald-700 border-emerald-200',
    CANCELLED:        'bg-slate-100 text-slate-500 border-slate-200',
};

// ─── Inline Status Badge ────────────────────────────────────────────────────

function StatusBadge({
    item,
    onStatusChange,
    onShip,
    onDelete,
}: {
    item: AssemblyRequest;
    onStatusChange: (id: number, newStatus: AssemblyStatus) => void;
    onShip?: (item: AssemblyRequest) => void;
    onDelete?: (item: AssemblyRequest) => void;
}) {
    const status = STATUS_MAP[item.status] || { label: item.status, className: '' };
    const canEdit = EDITABLE_STATUSES.includes(item.status);
    const colorClass = STATUS_COLORS[item.status] || 'bg-slate-100 text-slate-700 border-slate-200';
    const canDelete = item.status === 'CANCELLED' || item.status === 'PENDING';

    if (!canEdit) {
        return (
            <div className="inline-flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                <span className={`inline-block text-xs font-semibold rounded-full py-1.5 px-3 border ${colorClass}`}>
                    {status.label}
                </span>
                {item.status === 'VEHICLE_ASSIGNED' && onShip && (
                    <button
                        className="text-xs font-semibold rounded-full py-1.5 px-3 border bg-emerald-100 text-emerald-700 border-emerald-200 hover:bg-emerald-200 transition-colors"
                        onClick={(e) => { e.stopPropagation(); onShip(item); }}
                    >
                        Отгрузить
                    </button>
                )}
                {canDelete && onDelete && (
                    <button
                        className="text-xs rounded-full py-1.5 px-2.5 border bg-red-50 text-red-600 border-red-200 hover:bg-red-100 transition-colors"
                        title="Удалить заявку"
                        onClick={(e) => { e.stopPropagation(); onDelete(item); }}
                    >
                        🗑️
                    </button>
                )}
            </div>
        );
    }

    return (
        <div className="relative inline-block" onClick={(e) => e.stopPropagation()}>
            <select
                value={item.status}
                onChange={(e) => {
                    e.stopPropagation();
                    onStatusChange(item.id, e.target.value as AssemblyStatus);
                }}
                className={`
                    appearance-none cursor-pointer outline-none border transition-all text-xs font-semibold rounded-full
                    py-1.5 pl-3 pr-8 shadow-sm focus:ring-2 focus:ring-offset-1
                    ${colorClass}
                `}
            >
                <option value="IN_PROGRESS">В сборке</option>
                <option value="READY">Готово</option>
            </select>
            <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2">
                <svg className="w-3.5 h-3.5 opacity-60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M19 9l-7 7-7-7" />
                </svg>
            </div>
        </div>
    );
}

// ─── Inline Editable Cell ───────────────────────────────────────────────────

function EditableCell({
    value,
    suffix,
    editable,
    highlight,
    step,
    onSave,
}: {
    value: number;
    suffix?: string;
    editable: boolean;
    highlight?: boolean;
    step?: number;
    onSave: (val: number) => void;
}) {
    const [editing, setEditing] = useState(false);
    const [inputVal, setInputVal] = useState('');
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (editing && inputRef.current) {
            inputRef.current.focus();
            inputRef.current.select();
        }
    }, [editing]);

    const handleSave = () => {
        setEditing(false);
        const num = parseFloat(inputVal);
        if (!isNaN(num) && num !== value) {
            onSave(num);
        } else {
            setInputVal(String(value || ''));
        }
    };

    if (editing && editable) {
        return (
            <input
                ref={inputRef}
                type="number"
                min={0}
                step={step || 1}
                value={inputVal}
                onChange={(e) => setInputVal(e.target.value)}
                onBlur={handleSave}
                onKeyDown={(e) => {
                    if (e.key === 'Enter') inputRef.current?.blur();
                    if (e.key === 'Escape') { setInputVal(String(value || '')); setEditing(false); }
                }}
                onClick={(e) => e.stopPropagation()}
                className="w-20 text-right bg-white border-2 border-blue-500 rounded-md px-2 py-1 outline-none text-sm font-medium shadow-[0_0_0_3px_rgba(59,130,246,0.1)] transition-all"
                style={{ minWidth: 60 }}
            />
        );
    }

    const displayVal = value > 0 ? (suffix ? `${formatNumber(value, 1)} ${suffix}` : String(value)) : (highlight ? 'Указать...' : '\u2014');

    return (
        <div
            onClick={(e) => {
                if (!editable) return;
                e.stopPropagation();
                setInputVal(String(value || ''));
                setEditing(true);
            }}
            className={`
                group inline-flex items-center justify-end px-2 py-1 rounded-md transition-colors
                border border-transparent
                ${editable ? 'cursor-pointer hover:bg-slate-50 hover:border-slate-200' : ''}
                ${highlight ? 'bg-red-50 text-red-600' : 'text-slate-700'}
            `}
            title={editable ? 'Нажмите для редактирования' : undefined}
        >
            {editable && (
                <svg className="w-3 h-3 mr-1.5 opacity-0 group-hover:opacity-50 transition-opacity text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                </svg>
            )}
            <span className="font-medium text-sm">{displayVal}</span>
        </div>
    );
}

// ─── Inline Editable Date Cell ──────────────────────────────────────────────

function EditableDateCell({
    value,
    editable,
    onSave,
}: {
    value: string | null | undefined;
    editable: boolean;
    onSave: (val: string) => void;
}) {
    const [editing, setEditing] = useState(false);
    const [inputVal, setInputVal] = useState('');
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (editing && inputRef.current) {
            inputRef.current.focus();
        }
    }, [editing]);

    const handleSave = () => {
        setEditing(false);
        if (inputVal !== (value?.slice(0, 10) || '')) {
            onSave(inputVal);
        }
    };

    if (editing && editable) {
        return (
            <input
                ref={inputRef}
                type="date"
                value={inputVal}
                onChange={(e) => setInputVal(e.target.value)}
                onBlur={handleSave}
                onKeyDown={(e) => {
                    if (e.key === 'Enter') inputRef.current?.blur();
                    if (e.key === 'Escape') { setInputVal(value?.slice(0, 10) || ''); setEditing(false); }
                }}
                onClick={(e) => e.stopPropagation()}
                className="bg-white border-2 border-blue-500 rounded-md px-2 py-1 outline-none text-sm font-medium shadow-[0_0_0_3px_rgba(59,130,246,0.1)] transition-all"
                style={{ minWidth: 130 }}
            />
        );
    }

    const displayVal = value ? formatDate(value) : '\u2014';

    return (
        <div
            onClick={(e) => {
                if (!editable) return;
                e.stopPropagation();
                setInputVal(value?.slice(0, 10) || '');
                setEditing(true);
            }}
            className={`
                group inline-flex items-center px-2 py-1 rounded-md transition-colors
                border border-transparent
                ${editable ? 'cursor-pointer hover:bg-slate-50 hover:border-slate-200' : ''}
                text-slate-700
            `}
            title={editable ? 'Нажмите для редактирования' : undefined}
        >
            {editable && (
                <svg className="w-3 h-3 mr-1.5 opacity-0 group-hover:opacity-50 transition-opacity text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                </svg>
            )}
            <span className="font-medium text-sm">{displayVal}</span>
        </div>
    );
}

// ─── Component ──────────────────────────────────────────────────────────────

export default function AssemblyListPage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const slug = params.slug as string;
    const { canEdit } = usePermissions();

    // Data
    const [items, setItems] = useState<AssemblyRequest[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    // Drafts
    const [drafts, setDrafts] = useState<AssemblyDraft[]>([]);
    const [selectedDraftIds, setSelectedDraftIds] = useState<Set<number>>(new Set());
    const [merging, setMerging] = useState(false);
    const [showMergeConfirm, setShowMergeConfirm] = useState(false);

    // Highlight just-created request ids (from query string)
    const justCreatedIds = useMemo(() => {
        const raw = searchParams.get('just_created');
        if (!raw) return new Set<number>();
        const ids = raw.split(',').map(s => Number(s.trim())).filter(n => !isNaN(n) && n > 0);
        return new Set(ids);
    }, [searchParams]);

    const [highlightActive, setHighlightActive] = useState(false);
    useEffect(() => {
        if (justCreatedIds.size === 0) return;
        setHighlightActive(true);
        const t = setTimeout(() => setHighlightActive(false), 3000);
        return () => clearTimeout(t);
    }, [justCreatedIds]);

    const loadDrafts = useCallback(async () => {
        try {
            const list = await api.listAssemblyDrafts();
            setDrafts(list);
        } catch {
            setDrafts([]);
        }
    }, []);

    useEffect(() => { loadDrafts(); }, [loadDrafts]);

    // Refresh drafts when returning to the tab (e.g. after committing on /distribute)
    useEffect(() => {
        const handler = () => { loadDrafts(); };
        window.addEventListener('focus', handler);
        return () => window.removeEventListener('focus', handler);
    }, [loadDrafts]);

    const handleDeleteDraft = useCallback(async (draftId: number) => {
        if (!confirm('Удалить черновик?')) return;
        try {
            await api.deleteAssemblyDraft(draftId);
            setDrafts(prev => prev.filter(d => d.id !== draftId));
            setToast({ message: 'Черновик удалён', type: 'success' });
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : 'Ошибка удаления';
            // 404 → черновик уже удалён в БД (commit / параллельная вкладка); идемпотентно убираем из UI
            if (/not found|404/i.test(msg)) {
                setDrafts(prev => prev.filter(d => d.id !== draftId));
                setToast({ message: 'Черновик уже был удалён', type: 'success' });
            } else {
                setToast({ message: msg, type: 'error' });
            }
        }
    }, []);

    // Filters
    const [warehouseId, setWarehouseId] = useState<number | ''>('');
    const [statusFilter, setStatusFilter] = useState('');
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');
    const [searchInput, setSearchInput] = useState('');
    const [search, setSearch] = useState('');
    const [brandFilter, setBrandFilter] = useState('');
    const [page, setPage] = useState(0);
    const PAGE_SIZE = 50;

    // Warehouse options (FULFILLMENT only)
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [brandOptions, setBrandOptions] = useState<string[]>([]);

    const warehouseNameById = useCallback(
        (id: number) => warehouses.find(w => w.id === id)?.name ?? `Склад ${id}`,
        [warehouses],
    );

    // Дубли направлений: пары (ff→wb), которые встречаются в ≥2 открытых черновиках
    const duplicateLanes = useMemo(
        () => drafts.length >= 2 ? findDuplicateLanes(drafts, warehouseNameById) : [],
        [drafts, warehouseNameById],
    );

    const handleMergeDrafts = useCallback(async (ids: number[]) => {
        setMerging(true);
        try {
            const merged = await api.mergeAssemblyDrafts(ids);
            setShowMergeConfirm(false);
            setSelectedDraftIds(new Set());
            const qs = new URLSearchParams({ draft: String(merged.id), pkg: 'BOX', type: 'all' });
            router.push(`/p/${slug}/warehouse/assembly/distribute/preview?${qs.toString()}`);
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка объединения черновиков', type: 'error' });
            setShowMergeConfirm(false);
        } finally {
            setMerging(false);
        }
    }, [router, slug]);

    useEffect(() => {
        api.getWarehouses()
            .then(whs => setWarehouses(whs.filter(w => w.warehouse_type === 'FULFILLMENT')))
            .catch(() => {});
        api.getWbBrands()
            .then(brands => setBrandOptions(brands.sort()))
            .catch(() => {});
    }, []);

    // ─── Load data ────────────────────────────────────────────────────────

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const resp = await api.getAssemblyRequests({
                warehouse_id: warehouseId || undefined,
                status: statusFilter || undefined,
                search: search || undefined,
                date_from: dateFrom || undefined,
                date_to: dateTo || undefined,
                brand: brandFilter || undefined,
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
            });
            setItems(resp.items);
            setTotal(resp.total);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [warehouseId, statusFilter, search, dateFrom, dateTo, brandFilter, page]);

    useEffect(() => { load(); }, [load]);

    const handleSearchKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
            setSearch(searchInput);
            setPage(0);
        }
    };

    const totalPages = Math.ceil(total / PAGE_SIZE);

    // ─── Inline actions ─────────────────────────────────────────────────

    const updateItemLocal = (id: number, patch: Partial<AssemblyRequest>) => {
        setItems(prev => prev.map(it => it.id === id ? { ...it, ...patch } : it));
    };

    const handleStatusChange = async (id: number, newStatus: AssemblyStatus) => {
        const item = items.find(it => it.id === id);
        if (!item || item.status === newStatus) return;

        // Client-side validation for READY
        if (newStatus === 'READY') {
            if (!item.pallets_count || item.pallets_count <= 0 || !item.total_weight_kg || item.total_weight_kg <= 0) {
                setToast({ message: 'Укажите количество палет и вес перед переводом в «Готово»', type: 'error' });
                return;
            }
        }

        const oldStatus = item.status;
        updateItemLocal(id, { status: newStatus });

        try {
            if (newStatus === 'IN_PROGRESS') {
                await api.startAssembly(id);
            } else if (newStatus === 'READY') {
                await api.markAssemblyReady(id);
            }
            setToast({ message: `Статус изменён → ${STATUS_MAP[newStatus]?.label || newStatus}`, type: 'success' });
        } catch (e: unknown) {
            updateItemLocal(id, { status: oldStatus });
            setToast({ message: e instanceof Error ? e.message : 'Ошибка смены статуса', type: 'error' });
        }
    };

    const handlePalletsChange = async (item: AssemblyRequest, newPallets: number) => {
        const oldPallets = item.pallets_count;
        const oldWeight = item.total_weight_kg || 0;

        // Recalculate pallet_weight_kg: keep total weight, adjust per-pallet
        const newPalletWeight = newPallets > 0 && oldWeight > 0
            ? oldWeight / newPallets
            : Number(item.pallet_weight_kg) || 0;

        const newTotal = newPallets * newPalletWeight;
        updateItemLocal(item.id, { pallets_count: newPallets, total_weight_kg: newTotal });

        try {
            await api.updateAssemblyRequest(item.id, {
                pallets_count: newPallets,
                pallet_weight_kg: Math.round(newPalletWeight * 100) / 100,
            });
            setToast({ message: 'Палеты обновлены', type: 'success' });
        } catch (e: unknown) {
            updateItemLocal(item.id, { pallets_count: oldPallets, total_weight_kg: oldWeight });
            setToast({ message: e instanceof Error ? e.message : 'Ошибка сохранения', type: 'error' });
        }
    };

    const handleWeightChange = async (item: AssemblyRequest, newTotalWeight: number) => {
        const oldWeight = item.total_weight_kg || 0;
        const pallets = item.pallets_count || 1;
        const newPalletWeight = pallets > 0 ? newTotalWeight / pallets : 0;

        updateItemLocal(item.id, { total_weight_kg: newTotalWeight, pallet_weight_kg: newPalletWeight });

        try {
            await api.updateAssemblyRequest(item.id, {
                pallets_count: pallets,
                pallet_weight_kg: Math.round(newPalletWeight * 100) / 100,
            });
            setToast({ message: 'Вес обновлён', type: 'success' });
        } catch (e: unknown) {
            updateItemLocal(item.id, { total_weight_kg: oldWeight });
            setToast({ message: e instanceof Error ? e.message : 'Ошибка сохранения', type: 'error' });
        }
    };

    const handleDateChange = async (item: AssemblyRequest, newDate: string) => {
        const oldDate = item.estimated_ready_date;
        updateItemLocal(item.id, { estimated_ready_date: newDate || undefined });

        try {
            await api.updateAssemblyRequest(item.id, {
                estimated_ready_date: newDate || undefined,
            });
            setToast({ message: 'Дата готовности обновлена', type: 'success' });
        } catch (e: unknown) {
            updateItemLocal(item.id, { estimated_ready_date: oldDate });
            setToast({ message: e instanceof Error ? e.message : 'Ошибка сохранения', type: 'error' });
        }
    };

    const handleShipFromList = async (item: AssemblyRequest) => {
        if (!confirm(`Отгрузить заявку ${item.number}? Остатки будут списаны со склада.`)) return;

        const oldStatus = item.status;
        updateItemLocal(item.id, { status: 'SHIPPED' as AssemblyStatus });

        try {
            await api.shipAssembly(item.id);
            setToast({ message: `Заявка ${item.number} отгружена`, type: 'success' });
        } catch (e: unknown) {
            updateItemLocal(item.id, { status: oldStatus });
            setToast({ message: e instanceof Error ? e.message : 'Ошибка отгрузки', type: 'error' });
        }
    };

    const handleDeleteAssembly = async (item: AssemblyRequest) => {
        if (!confirm(`Удалить заявку ${item.number}? Это действие нельзя отменить.`)) return;
        try {
            await api.deleteAssembly(item.id);
            setItems(prev => prev.filter(i => i.id !== item.id));
            setToast({ message: `Заявка ${item.number} удалена`, type: 'success' });
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка удаления', type: 'error' });
        }
    };

    // ─── Render ───────────────────────────────────────────────────────────

    return (
        <div className="animate-in">
            {/* Toast */}
            {toast && (
                <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} duration={toast.type === 'error' ? 4000 : 2500} />
            )}

            {/* Merge confirm modal */}
            {showMergeConfirm && (
                <div style={{
                    position: 'fixed', inset: 0, zIndex: 1000,
                    background: 'rgba(0,0,0,0.5)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    padding: 16,
                }}>
                    <div className="glass-card" style={{ maxWidth: 520, width: '100%' }}>
                        {/* Modal header */}
                        <div style={{ marginBottom: 16 }}>
                            <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 8 }}>
                                Объединить {selectedDraftIds.size} черновика в один?
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                                {drafts
                                    .filter(d => selectedDraftIds.has(d.id))
                                    .map(d => (
                                        <div key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                                            <span style={{ color: 'var(--color-accent)', fontWeight: 600, fontSize: 11 }}>#{d.id}</span>
                                            <span style={{ color: 'var(--color-text)' }}>{d.name || `Черновик #${d.id}`}</span>
                                            <span style={{ color: 'var(--color-text-muted)', fontSize: 12 }}>
                                                · {d.distribution.rows.length} поз.
                                            </span>
                                        </div>
                                    ))}
                            </div>
                        </div>

                        {(() => {
                            const lanes = consolidatingLanes(selectedDraftIds, duplicateLanes);
                            const selectedIds = [...selectedDraftIds];
                            const totalPieces = lanes.reduce((s, l) => s + l.pieces, 0);
                            return lanes.length > 0 ? (
                                <>
                                    {/* Summary chips */}
                                    <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
                                        <span className="badge badge-info">
                                            {lanes.length} {lanes.length === 1 ? 'маршрут' : lanes.length < 5 ? 'маршрута' : 'маршрутов'} объединится
                                        </span>
                                        <span className="badge badge-secondary">
                                            ~{formatNumber(totalPieces)} шт. итого
                                        </span>
                                    </div>

                                    {/* Lane list */}
                                    <div style={{
                                        maxHeight: 280,
                                        overflowY: 'auto',
                                        display: 'flex',
                                        flexDirection: 'column',
                                        gap: 6,
                                        marginBottom: 12,
                                        paddingRight: 2,
                                    }}>
                                        {lanes.map(lane => (
                                            <div key={`${lane.ffId}-${lane.wbName}`} style={{
                                                padding: '10px 12px',
                                                background: 'var(--color-bg)',
                                                borderRadius: 10,
                                                fontSize: 13,
                                            }}>
                                                {/* Route + total */}
                                                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 5 }}>
                                                    <span style={{ fontWeight: 600 }}>{lane.ffName}</span>
                                                    <span style={{ color: 'var(--color-text-muted)' }}>→</span>
                                                    <span style={{ fontWeight: 600 }}>{lane.wbName}</span>
                                                    <span style={{ marginLeft: 'auto', fontWeight: 600 }}>
                                                        ~{formatNumber(lane.pieces)} шт.
                                                    </span>
                                                </div>
                                                {/* Per-draft breakdown */}
                                                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                                                    {selectedIds
                                                        .filter(id => lane.piecesPerDraft[id] != null)
                                                        .map(id => (
                                                            <span key={id} style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                                                #{id}: ~{formatNumber(lane.piecesPerDraft[id])} шт.
                                                            </span>
                                                        ))}
                                                </div>
                                            </div>
                                        ))}
                                    </div>

                                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 16 }}>
                                        Несовпадающие маршруты останутся отдельными заявками при передаче на ФФ.
                                    </div>
                                </>
                            ) : (
                                <div style={{ marginBottom: 16, fontSize: 13, color: 'var(--color-text-muted)' }}>
                                    Черновики будут объединены. Все маршруты останутся отдельными заявками при передаче на ФФ.
                                </div>
                            );
                        })()}

                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button
                                className="btn btn-secondary btn-sm"
                                onClick={() => setShowMergeConfirm(false)}
                                disabled={merging}
                            >
                                Отмена
                            </button>
                            <button
                                className="btn btn-primary btn-sm"
                                onClick={() => handleMergeDrafts([...selectedDraftIds])}
                                disabled={merging}
                            >
                                {merging ? 'Объединяю…' : 'Объединить и открыть →'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Header */}
            <div className="page-header">
                <div>
                    <h1 className="page-title">Заявки на сборку</h1>
                    <p className="page-subtitle">Всего: {total}</p>
                </div>
                {canEdit() && (
                    <Link href={`/p/${slug}/warehouse/assembly/new`}>
                        <button className="btn btn-primary">Создать заявку</button>
                    </Link>
                )}
            </div>

            {/* Duplicate-lane warning banner */}
            {duplicateLanes.length > 0 && (
                <div className="glass-card" style={{ padding: '12px 16px', marginBottom: 12, borderLeft: '3px solid var(--color-warning)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                        <span>⚠️</span>
                        <span style={{ fontWeight: 600, color: 'var(--color-text)' }}>Обнаружены дублирующиеся маршруты</span>
                    </div>
                    <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {duplicateLanes.map(lane => (
                            <span key={`${lane.ffId}-${lane.wbName}`} className="badge badge-warning" style={{ fontSize: 11 }}>
                                {lane.ffName} → {lane.wbName} · {formatNumber(lane.pieces)} шт. · #{lane.draftIds.join(', #')}
                            </span>
                        ))}
                    </div>
                    <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-muted)' }}>
                        Выберите черновики ниже и объедините — они будут переданы на ФФ одной отправкой.
                    </div>
                </div>
            )}

            {/* Drafts */}
            {drafts.length > 0 && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text)' }}>
                            Незавершённые черновики ({drafts.length})
                        </div>
                        {selectedDraftIds.size >= 2 && (
                            <button
                                className="btn btn-primary btn-sm"
                                onClick={() => setShowMergeConfirm(true)}
                                disabled={merging}
                            >
                                Объединить {selectedDraftIds.size} черновика →
                            </button>
                        )}
                    </div>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {drafts.map(draft => {
                            const isSelected = selectedDraftIds.has(draft.id);
                            return (
                                <div
                                    key={draft.id}
                                    style={{
                                        display: 'flex', alignItems: 'center', gap: 8,
                                        padding: '8px 12px',
                                        border: `1px solid ${isSelected ? 'var(--color-accent)' : 'var(--color-border)'}`,
                                        borderRadius: 12,
                                        background: isSelected ? 'color-mix(in srgb, var(--color-accent) 8%, var(--color-bg))' : 'var(--color-bg)',
                                        fontSize: 12,
                                        transition: 'border-color 0.2s, background 0.2s',
                                    }}
                                >
                                    <input
                                        type="checkbox"
                                        checked={isSelected}
                                        onChange={e => {
                                            const checked = e.target.checked;
                                            setSelectedDraftIds(prev => {
                                                const next = new Set(prev);
                                                if (checked) next.add(draft.id); else next.delete(draft.id);
                                                return next;
                                            });
                                        }}
                                        style={{ accentColor: 'var(--color-accent)', cursor: 'pointer' }}
                                    />
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                                        <span style={{ fontWeight: 600 }}>{draft.name || `Черновик #${draft.id}`}</span>
                                        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                            {draft.distribution.rows.length} поз. · обновлён {formatDate(draft.updated_at)}
                                        </span>
                                    </div>
                                    <button
                                        className="btn btn-secondary btn-sm"
                                        onClick={() => router.push(`/p/${slug}/warehouse/assembly/distribute?draft=${draft.id}`)}
                                    >
                                        Открыть
                                    </button>
                                    <button
                                        className="btn btn-danger btn-sm"
                                        onClick={() => handleDeleteDraft(draft.id)}
                                        title="Удалить черновик"
                                    >
                                        ×
                                    </button>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* Filters */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap' }}>
                    <div className="form-group" style={{ flex: 1, minWidth: 200 }}>
                        <input
                            className="form-input"
                            placeholder="Поиск по номеру или поставке WB... (Enter)"
                            value={searchInput}
                            onChange={e => setSearchInput(e.target.value)}
                            onKeyDown={handleSearchKeyDown}
                        />
                    </div>
                    <div className="form-group">
                        <select
                            className="form-input"
                            value={warehouseId}
                            onChange={e => { setWarehouseId(e.target.value ? Number(e.target.value) : ''); setPage(0); }}
                        >
                            <option value="">Все склады</option>
                            {warehouses.map(w => (
                                <option key={w.id} value={w.id}>{w.name}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group">
                        <select
                            className="form-input"
                            value={statusFilter}
                            onChange={e => { setStatusFilter(e.target.value); setPage(0); }}
                        >
                            {STATUS_OPTIONS_FILTER.map(o => (
                                <option key={o.value} value={o.value}>{o.label}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group">
                        <select
                            className="form-input"
                            value={brandFilter}
                            onChange={e => { setBrandFilter(e.target.value); setPage(0); }}
                        >
                            <option value="">Все бренды</option>
                            {brandOptions.map(b => (
                                <option key={b} value={b}>{b}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group">
                        <input
                            className="form-input"
                            type="date"
                            value={dateFrom}
                            onChange={e => { setDateFrom(e.target.value); setPage(0); }}
                            placeholder="Дата от"
                        />
                    </div>
                    <div className="form-group">
                        <input
                            className="form-input"
                            type="date"
                            value={dateTo}
                            onChange={e => { setDateTo(e.target.value); setPage(0); }}
                            placeholder="Дата до"
                        />
                    </div>
                </div>
            </div>

            {/* Error */}
            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)' }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={() => setError('')}>
                        Закрыть
                    </button>
                </div>
            )}

            {/* Table */}
            {loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            ) : items.length === 0 ? (
                <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>📋</div>
                    <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Нет заявок на сборку</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                        Создайте заявку из поставки FBO или нажмите &laquo;Создать заявку&raquo;
                    </div>
                </div>
            ) : (
                <div className="glass-card" style={{ overflow: 'auto' }}>
                    {/* TODO: migrate to TanStackDataTable — has inline editing (EditableCell, EditableDateCell, StatusBadge select) */}
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>№</th>
                                <th>Статус</th>
                                <th>Бренд</th>
                                <th>Склад</th>
                                <th>FBO поставка</th>
                                <th style={{ textAlign: 'right' }}>Товары</th>
                                <th style={{ textAlign: 'right' }}>Палеты</th>
                                <th style={{ textAlign: 'right' }}>Общий вес</th>
                                <th>Дата готовности</th>
                                <th>Создана</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map(item => {
                                const canEdit = EDITABLE_STATUSES.includes(item.status);
                                const needsHighlight = item.status === 'IN_PROGRESS';
                                const isJustCreated = highlightActive && justCreatedIds.has(item.id);
                                return (
                                    <tr
                                        key={item.id}
                                        style={{
                                            cursor: 'pointer',
                                            background: isJustCreated ? 'rgba(52,199,89,0.15)' : undefined,
                                            transition: 'background 0.5s ease',
                                        }}
                                        onClick={() => router.push(`/p/${slug}/warehouse/assembly/${item.id}`)}
                                    >
                                        <td style={{ fontWeight: 500 }}>{item.number}</td>
                                        <td>
                                            <StatusBadge item={item} onStatusChange={handleStatusChange} onShip={handleShipFromList} onDelete={handleDeleteAssembly} />
                                        </td>
                                        <td style={{ fontSize: 13 }}>
                                            {item.brands || '\u2014'}
                                        </td>
                                        <td style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                                            {item.warehouse_name || '\u2014'}
                                        </td>
                                        <td style={{ fontSize: 13 }}>
                                            {item.wb_supply_name || '\u2014'}
                                            {item.wb_warehouse_name && (
                                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                    {item.wb_warehouse_name}
                                                </div>
                                            )}
                                        </td>
                                        <td style={{ textAlign: 'right', fontSize: 13 }}>
                                            {item.items ? item.items.reduce((sum, i) => sum + (i.quantity || 0), 0) : '\u2014'}
                                        </td>
                                        <td style={{ textAlign: 'right' }}>
                                            <EditableCell
                                                value={item.pallets_count}
                                                editable={canEdit}
                                                highlight={needsHighlight && (!item.pallets_count || item.pallets_count <= 0)}
                                                onSave={(val) => handlePalletsChange(item, val)}
                                            />
                                        </td>
                                        <td style={{ textAlign: 'right' }}>
                                            <EditableCell
                                                value={item.total_weight_kg || 0}
                                                suffix="кг"
                                                editable={canEdit}
                                                highlight={needsHighlight && (!item.total_weight_kg || item.total_weight_kg <= 0)}
                                                step={0.1}
                                                onSave={(val) => handleWeightChange(item, val)}
                                            />
                                        </td>
                                        <td>
                                            <EditableDateCell
                                                value={item.estimated_ready_date}
                                                editable={canEdit}
                                                onSave={(val) => handleDateChange(item, val)}
                                            />
                                        </td>
                                        <td>{formatDate(item.created_at)}</td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>

                    {/* Pagination */}
                    {totalPages > 1 && (
                        <div style={{
                            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                            padding: '12px 16px', borderTop: '1px solid var(--color-border)',
                        }}>
                            <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                Показано {page * PAGE_SIZE + 1}&ndash;{Math.min((page + 1) * PAGE_SIZE, total)} из {total}
                            </span>
                            <div style={{ display: 'flex', gap: 4 }}>
                                <button
                                    className="btn btn-secondary btn-sm"
                                    disabled={page === 0}
                                    onClick={() => setPage(p => p - 1)}
                                >
                                    &larr;
                                </button>
                                <button
                                    className="btn btn-secondary btn-sm"
                                    disabled={page >= totalPages - 1}
                                    onClick={() => setPage(p => p + 1)}
                                >
                                    &rarr;
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
