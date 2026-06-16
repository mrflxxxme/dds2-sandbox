'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatNumber, pluralRu } from '@/lib/utils';
import { Toast } from '@/components';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import { usePermissions } from '@/lib/hooks/usePermissions';
import type { AssemblyDraft, AssemblyRequest, AssemblyStatus, CreatedAssemblyGroup, Warehouse } from '@/types/api';
import { findDuplicateLanes } from '@/lib/utils/assemblyDraftMerge';

// ─── Status config ──────────────────────────────────────────────────────────

const STATUS_MAP: Record<AssemblyStatus, { label: string; className: string }> = {
    // PENDING — legacy: больше не используется при создании, но может встретиться в истории.
    PENDING:          { label: 'В сборке',          className: 'badge-info' },
    IN_PROGRESS:      { label: 'В сборке',          className: 'badge-info' },
    READY:            { label: 'Готово',             className: 'badge-success' },
    VEHICLE_ASSIGNED: { label: 'Машина назначена',   className: 'badge-info' },
    SHIPPED:          { label: 'Отгружена',          className: 'badge-success' },
    DELIVERED:        { label: 'Принята WB',         className: 'badge-success' },
    CLOSED:           { label: 'Закрыт',             className: 'badge-warning' },
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
    { value: 'CLOSED', label: 'Закрыт' },
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
    CLOSED:           'bg-amber-100 text-amber-700 border-amber-200',
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

    // Drafts / созданные партии — только каунты для полоски-сводки «Распределение»
    // (сами блоки живут на /warehouse/assembly/distribution)
    const [drafts, setDrafts] = useState<AssemblyDraft[]>([]);
    const [createdGroups, setCreatedGroups] = useState<CreatedAssemblyGroup[]>([]);

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
        try {
            setCreatedGroups(await api.getCreatedAssemblyGroups());
        } catch {
            setCreatedGroups([]);
        }
    }, []);

    useEffect(() => { loadDrafts(); }, [loadDrafts]);

    // Refresh drafts when returning to the tab (e.g. after committing on /distribute)
    useEffect(() => {
        const handler = () => { loadDrafts(); };
        window.addEventListener('focus', handler);
        return () => window.removeEventListener('focus', handler);
    }, [loadDrafts]);

    // Filters
    const [warehouseId, setWarehouseId] = useState<number | ''>('');
    const [statusFilter, setStatusFilter] = useState('');
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');
    const [searchInput, setSearchInput] = useState('');
    const [search, setSearch] = useState('');
    const [brandFilter, setBrandFilter] = useState('');
    const [ffLinkFilter, setFfLinkFilter] = useState<'' | 'none' | 'linked'>('');
    // Пагинация/сортировка/экспорт — клиентские, через TanStackDataTable: грузим весь
    // отфильтрованный набор. Потолок = серверный кап эндпоинта (limit ≤ 500; _build_response
    // делает per-row запросы, потому кап и стоит). Если набор больше — показываем подсказку
    // «показаны первые N» (см. page-subtitle), чтобы не было тихой обрезки сортировки/экспорта.
    const LOAD_LIMIT = 500;
    const CLIENT_PAGE_SIZE = 50;

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
                ff_link: ffLinkFilter || undefined,
                limit: LOAD_LIMIT,
            });
            setItems(resp.items);
            setTotal(resp.total);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [warehouseId, statusFilter, search, dateFrom, dateTo, brandFilter, ffLinkFilter]);

    useEffect(() => { load(); }, [load]);

    const handleSearchKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
            setSearch(searchInput);
        }
    };

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

    // ─── Columns (TanStackDataTable: сортировка + Excel) ────────────────────
    // Inline-редактирование (палеты/вес/дата/статус) и кнопки-действия живут
    // внутри render-ячеек; getValue даёт сортировку по вычисляемым колонкам,
    // exportValue — корректную выгрузку JSX-ячеек.
    const itemsQty = (row: AssemblyRequest) => row.items ? row.items.reduce((s, i) => s + (i.quantity || 0), 0) : 0;
    const cols: Column[] = [
        {
            key: 'number', label: '№',
            render: (_v, row: AssemblyRequest) => <span style={{ fontWeight: 500 }}>{row.number}</span>,
            exportValue: (row: AssemblyRequest) => row.number,
        },
        {
            key: 'status', label: 'Статус',
            getValue: (row: AssemblyRequest) => STATUS_MAP[row.status]?.label || row.status,
            render: (_v, row: AssemblyRequest) => (
                <StatusBadge item={row} onStatusChange={handleStatusChange} onShip={handleShipFromList} onDelete={handleDeleteAssembly} />
            ),
            exportValue: (row: AssemblyRequest) => STATUS_MAP[row.status]?.label || row.status,
        },
        {
            key: 'ff', label: 'Заявка ФФ',
            getValue: (row: AssemblyRequest) => row.ff_request_number || '',
            render: (_v, row: AssemblyRequest) => row.ff_request_id ? (
                <Link
                    href={`/p/${slug}/warehouse/${row.ff_warehouse_id ?? row.warehouse_id}/ff-request/${row.ff_request_id}`}
                    onClick={(e) => e.stopPropagation()}
                    style={{ color: 'var(--color-accent)', fontWeight: 500, fontSize: 13 }}
                >
                    {row.ff_request_number || `#${row.ff_request_id}`}{row.ff_stage_title ? ` (${row.ff_stage_title})` : ''}
                </Link>
            ) : '—',
            exportValue: (row: AssemblyRequest) => row.ff_request_number || '',
        },
        {
            key: 'brands', label: 'Бренд',
            render: (_v, row: AssemblyRequest) => <span style={{ fontSize: 13 }}>{row.brands || '—'}</span>,
            exportValue: (row: AssemblyRequest) => row.brands || '',
        },
        {
            key: 'warehouse_name', label: 'Склад',
            render: (_v, row: AssemblyRequest) => <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>{row.warehouse_name || '—'}</span>,
            exportValue: (row: AssemblyRequest) => row.warehouse_name || '',
        },
        {
            key: 'wb_supply_name', label: 'FBO поставка',
            render: (_v, row: AssemblyRequest) => (
                <span style={{ fontSize: 13 }}>
                    {row.wb_supply_name || '—'}
                    {row.wb_warehouse_name && (
                        <span style={{ display: 'block', fontSize: 12, color: 'var(--color-text-muted)' }}>{row.wb_warehouse_name}</span>
                    )}
                </span>
            ),
            exportValue: (row: AssemblyRequest) => row.wb_supply_name || '',
        },
        {
            key: 'items_qty', label: 'Товары', align: 'right',
            getValue: itemsQty,
            render: (_v, row: AssemblyRequest) => row.items ? formatNumber(itemsQty(row), 0) : '—',
            exportValue: itemsQty,
        },
        {
            key: 'pallets_count', label: 'Палеты', align: 'right',
            render: (_v, row: AssemblyRequest) => (
                <EditableCell
                    value={row.pallets_count}
                    editable={EDITABLE_STATUSES.includes(row.status)}
                    highlight={row.status === 'IN_PROGRESS' && (!row.pallets_count || row.pallets_count <= 0)}
                    onSave={(val) => handlePalletsChange(row, val)}
                />
            ),
            exportValue: (row: AssemblyRequest) => row.pallets_count,
        },
        {
            key: 'total_weight_kg', label: 'Общий вес', align: 'right',
            getValue: (row: AssemblyRequest) => row.total_weight_kg || 0,
            render: (_v, row: AssemblyRequest) => (
                <EditableCell
                    value={row.total_weight_kg || 0}
                    suffix="кг"
                    editable={EDITABLE_STATUSES.includes(row.status)}
                    highlight={row.status === 'IN_PROGRESS' && (!row.total_weight_kg || row.total_weight_kg <= 0)}
                    step={0.1}
                    onSave={(val) => handleWeightChange(row, val)}
                />
            ),
            exportValue: (row: AssemblyRequest) => row.total_weight_kg || 0,
        },
        {
            key: 'estimated_ready_date', label: 'Дата готовности',
            getValue: (row: AssemblyRequest) => row.estimated_ready_date || '',
            render: (_v, row: AssemblyRequest) => (
                <EditableDateCell
                    value={row.estimated_ready_date}
                    editable={EDITABLE_STATUSES.includes(row.status)}
                    onSave={(val) => handleDateChange(row, val)}
                />
            ),
            exportValue: (row: AssemblyRequest) => row.estimated_ready_date ? formatDate(row.estimated_ready_date) : '',
        },
        {
            key: 'created_at', label: 'Создана',
            render: (_v, row: AssemblyRequest) => formatDate(row.created_at),
            exportValue: (row: AssemblyRequest) => row.created_at,
        },
    ];

    // ─── Render ───────────────────────────────────────────────────────────

    return (
        <div className="animate-in">
            {/* Toast */}
            {toast && (
                <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} duration={toast.type === 'error' ? 4000 : 2500} />
            )}

            {/* Header */}
            <div className="page-header">
                <div>
                    <h1 className="page-title">Заявки на сборку</h1>
                    <p className="page-subtitle">
                        Всего: {total}
                        {items.length < total && ` · показаны первые ${formatNumber(items.length, 0)} — уточните фильтры`}
                    </p>
                </div>
                {canEdit() && (
                    <Link href={`/p/${slug}/warehouse/assembly/new`}>
                        <button className="btn btn-primary">Создать заявку</button>
                    </Link>
                )}
            </div>

            {/* Полоска-сводка «Распределение» — блоки переехали на /warehouse/assembly/distribution */}
            {(drafts.length > 0 || createdGroups.length > 0) && (
                <div
                    className="glass-card"
                    style={{
                        padding: '12px 16px', marginBottom: 16,
                        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                        borderLeft: duplicateLanes.length > 0 ? '3px solid var(--color-warning)' : undefined,
                    }}
                >
                    <span style={{ fontSize: 15 }}>🧩</span>
                    <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text)' }}>Распределение:</span>
                    <span style={{ fontSize: 13, color: 'var(--color-text)' }}>
                        {[
                            drafts.length > 0 ? `${drafts.length} ${pluralRu(drafts.length, ['черновик', 'черновика', 'черновиков'])}` : null,
                            createdGroups.length > 0 ? `${createdGroups.length} ${pluralRu(createdGroups.length, ['партия', 'партии', 'партий'])}` : null,
                        ].filter(Boolean).join(' · ')}
                    </span>
                    {duplicateLanes.length > 0 && (
                        <span className="badge badge-warning" style={{ fontSize: 11 }}>
                            ⚠️ {duplicateLanes.length} {pluralRu(duplicateLanes.length, ['дубль', 'дубля', 'дублей'])} маршрутов
                        </span>
                    )}
                    <span style={{ flex: 1 }} />
                    <Link href={`/p/${slug}/warehouse/assembly/distribution`}>
                        <button className="btn btn-secondary btn-sm">Открыть →</button>
                    </Link>
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
                            onChange={e => { setWarehouseId(e.target.value ? Number(e.target.value) : ''); }}
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
                            onChange={e => { setStatusFilter(e.target.value); }}
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
                            onChange={e => { setBrandFilter(e.target.value); }}
                        >
                            <option value="">Все бренды</option>
                            {brandOptions.map(b => (
                                <option key={b} value={b}>{b}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group">
                        <select
                            className="form-input"
                            value={ffLinkFilter}
                            onChange={e => { setFfLinkFilter(e.target.value as '' | 'none' | 'linked'); }}
                        >
                            <option value="">ФФ-связь: все</option>
                            <option value="none">Без связи</option>
                            <option value="linked">Со связью</option>
                        </select>
                    </div>
                    <div className="form-group">
                        <input
                            className="form-input"
                            type="date"
                            value={dateFrom}
                            onChange={e => { setDateFrom(e.target.value); }}
                            placeholder="Дата от"
                        />
                    </div>
                    <div className="form-group">
                        <input
                            className="form-input"
                            type="date"
                            value={dateTo}
                            onChange={e => { setDateTo(e.target.value); }}
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
                <TanStackDataTable
                    columns={cols}
                    data={items}
                    onRowClick={(row: AssemblyRequest) => router.push(`/p/${slug}/warehouse/assembly/${row.id}`)}
                    rowClassName={(row: AssemblyRequest) => highlightActive && justCreatedIds.has(row.id) ? 'assembly-row-just-created' : ''}
                    pageSize={CLIENT_PAGE_SIZE}
                    exportName="assembly_requests"
                />
            )}
        </div>
    );
}
