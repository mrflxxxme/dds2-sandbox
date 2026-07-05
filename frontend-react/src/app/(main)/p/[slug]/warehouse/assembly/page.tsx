'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatNumber, pluralRu } from '@/lib/utils';
import { Toast } from '@/components';
import TanStackDataTable from '@/components/TanStackDataTable';
import { FfMismatchModal } from '@/components/FfMismatchModal';
import type { Column } from '@/components/DataTable';
import { usePermissions } from '@/lib/hooks/usePermissions';
import type { AssemblyBulkStatus, AssemblyDraft, AssemblyRequest, AssemblyStatus, CreatedAssemblyGroup, FfLinkInfo, Warehouse } from '@/types/api';
import { findDuplicateLanes } from '@/lib/utils/assemblyDraftMerge';

// ─── Status config ──────────────────────────────────────────────────────────

// Все привязанные ФФ-заявки сборки: ff_links (migfull/«Натали» — 2+) либо первая
// привязка из плоских полей (обратная совместимость).
function ffLinksOf(row: AssemblyRequest): FfLinkInfo[] {
    if (row.ff_links?.length) return row.ff_links;
    if (row.ff_request_id) {
        return [{
            ff_request_id: row.ff_request_id,
            ff_request_number: row.ff_request_number,
            ff_stage_title: row.ff_stage_title,
            ff_warehouse_id: row.ff_warehouse_id,
        }];
    }
    return [];
}

// Подпись-тултип бейджа «Совместная»: другие сборки той же WB-поставки.
function jointTitle(row: AssemblyRequest): string {
    const sibs = row.joint_siblings || [];
    if (!sibs.length) return 'Совместная WB-поставка (несколько сборок с разных ФФ)';
    const parts = sibs.map(s => `${s.warehouse_name || `Склад ${s.warehouse_id}`} (${s.number})`);
    return `Совместная WB-поставка · ещё: ${parts.join(', ')}`;
}

const STATUS_MAP: Record<AssemblyStatus, { label: string; className: string }> = {
    // PENDING — legacy: больше не используется при создании, но может встретиться в истории.
    PENDING:          { label: 'В сборке',          className: 'badge-info' },
    IN_PROGRESS:      { label: 'В сборке',          className: 'badge-info' },
    READY:            { label: 'Готово',             className: 'badge-success' },
    VEHICLE_ASSIGNED: { label: 'Машина назначена',   className: 'badge-info' },
    SHIPPED:          { label: 'Отгружена',          className: 'badge-success' },
    DELIVERED:        { label: 'Принята WB',         className: 'badge-success' },
    RETURNED:         { label: 'Возврат на склад',   className: 'badge-warning' },
    CLOSED:           { label: 'Закрыт',             className: 'badge-warning' },
    CANCELLED:        { label: 'Отменена',           className: 'badge-secondary' },
};

const EDITABLE_STATUSES: AssemblyStatus[] = ['IN_PROGRESS', 'READY'];

// Заявки, которые ещё НЕ отгружены на WB → можно массово удалить (товар физически
// не списан, висит только резерв). Отгруженные (SHIPPED/DELIVERED/RETURNED/CLOSED)
// удалять нельзя — сначала отмена с откатом стока.
const BULK_DELETABLE_STATUSES = new Set<AssemblyStatus>(['PENDING', 'IN_PROGRESS', 'READY', 'VEHICLE_ASSIGNED', 'CANCELLED']);
const isBulkDeletable = (status: string): boolean => BULK_DELETABLE_STATUSES.has(status as AssemblyStatus);

const STATUS_OPTIONS_FILTER: { value: string; label: string }[] = [
    { value: '', label: 'Все статусы' },
    { value: 'IN_PROGRESS', label: 'В сборке' },
    { value: 'READY', label: 'Готово' },
    { value: 'VEHICLE_ASSIGNED', label: 'Машина назначена' },
    { value: 'SHIPPED', label: 'Отгружена' },
    { value: 'DELIVERED', label: 'Принята WB' },
    { value: 'RETURNED', label: 'Возврат на склад' },
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
    RETURNED:         'bg-amber-100 text-amber-700 border-amber-200',
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
    estimated,
    emptyLabel,
    step,
    onSave,
}: {
    value: number;
    suffix?: string;
    editable: boolean;
    highlight?: boolean;
    estimated?: boolean;  // значение показано как РАСЧЁТНОЕ (примерное) → префикс «≈», приглушённый цвет
    emptyLabel?: string;  // подпись при пустом значении вместо «—» (напр. «нет веса»)
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

    const numTxt = suffix ? `${formatNumber(value, 1)} ${suffix}` : String(value);
    const displayVal = value > 0 ? (estimated ? `≈ ${numTxt}` : numTxt) : (highlight ? 'Указать...' : emptyLabel || '\u2014');

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
                ${estimated || (emptyLabel && value <= 0) ? 'text-slate-400' : highlight ? 'bg-red-50 text-red-600' : 'text-slate-700'}
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

// ─── Weight Cell (авто-вес) ──────────────────────────────────────────────────
// «Общий вес» проставляется АВТОМАТИЧЕСКИ: если ручной вес не задан, показываем
// расчётный вес отгрузки (нетто + тара коробов) как «≈ N кг» (приглушённо). Без
// кнопок «Указать»/«применить». Клик — ручное переопределение.
//  · частичный расчёт (у части арт. нет веса) → компактный значок «⚠» с тултипом;
//  · нет веса ни у одного товара → «нет веса» (заполнить справочник в настройках).

function WeightCell({
    row,
    editable,
    onSave,
}: {
    row: AssemblyRequest;
    editable: boolean;
    onSave: (val: number) => void;
}) {
    const estimated = !!row.weight_is_estimated;
    const missing = row.weight_missing_barcodes?.length ?? 0;
    const total = Number(row.total_weight_kg) || 0;
    // Нет ни ручного, ни расчётного веса — у товаров заявки нет веса нигде.
    const noWeightData = row.status === 'IN_PROGRESS' && total <= 0 && !estimated;

    return (
        <div style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'flex-end', gap: 4, whiteSpace: 'nowrap' }}>
            <EditableCell
                value={total}
                suffix="кг"
                editable={editable}
                estimated={estimated}
                emptyLabel={noWeightData ? 'нет веса' : undefined}
                step={0.1}
                onSave={onSave}
            />
            {estimated && missing > 0 && (
                <span
                    title={`Нет веса у ${formatNumber(missing, 0)} арт. — расчёт неполный, дозаполните «Вес по баркодам» в настройках`}
                    style={{ fontSize: 14, lineHeight: 1, color: 'var(--color-warning, #d97706)', cursor: 'help' }}
                >
                    ⚠
                </span>
            )}
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
    // id сборки, для которой открыта модалка «расхождение наполнения»
    const [mismatchForId, setMismatchForId] = useState<number | null>(null);

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
    // Вид списка: по умолчанию «Активные» — скрываем уже принятые ВБ / закрытые /
    // отменённые сборки (чище список). «Архив» — только они; «Все» — без фильтра.
    const [view, setView] = useState<'active' | 'archived' | 'all'>('active');
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');
    const [searchInput, setSearchInput] = useState('');
    const [search, setSearch] = useState('');
    const [brandFilter, setBrandFilter] = useState('');
    const [ffLinkFilter, setFfLinkFilter] = useState<'' | 'none' | 'linked'>('');
    const [jointOnly, setJointOnly] = useState(false);
    // Монотонный счётчик запросов списка — отбрасываем устаревшие ответы (см. load)
    const loadSeq = useRef(0);
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
        // Гонка: debounce-поиск + смена фильтров могут пускать перекрывающиеся
        // запросы; считаем только последний, иначе устаревший ответ перетрёт свежий.
        const seq = ++loadSeq.current;
        setLoading(true);
        setError('');
        try {
            const resp = await api.getAssemblyRequests({
                warehouse_id: warehouseId || undefined,
                status: statusFilter || undefined,
                view,
                search: search || undefined,
                date_from: dateFrom || undefined,
                date_to: dateTo || undefined,
                brand: brandFilter || undefined,
                ff_link: ffLinkFilter || undefined,
                joint_only: jointOnly || undefined,
                limit: LOAD_LIMIT,
            });
            if (seq !== loadSeq.current) return;
            setItems(resp.items);
            setTotal(resp.total);
        } catch (e: unknown) {
            if (seq !== loadSeq.current) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (seq === loadSeq.current) setLoading(false);
        }
    }, [warehouseId, statusFilter, view, search, dateFrom, dateTo, brandFilter, ffLinkFilter, jointOnly]);

    useEffect(() => { load(); }, [load]);

    // Автопоиск: применяем ввод через 350 мс после остановки набора — как
    // выпадающие фильтры (мгновенно), без обязательного Enter. Enter всё ещё
    // запускает поиск сразу (см. handleSearchKeyDown).
    useEffect(() => {
        if (searchInput === search) return;
        const t = setTimeout(() => setSearch(searchInput), 350);
        return () => clearTimeout(t);
    }, [searchInput, search]);

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

    // ─── Массовое удаление (заявки из черновика, ещё не на WB) ───────────────
    const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
    const [bulkDeleting, setBulkDeleting] = useState(false);
    const deletableCount = useMemo(() => items.filter(i => isBulkDeletable(i.status)).length, [items]);

    const toggleSelect = useCallback((id: number) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });
    }, []);
    const selectAllDeletable = useCallback(() => {
        setSelectedIds(new Set(items.filter(i => isBulkDeletable(i.status)).map(i => i.id)));
    }, [items]);

    const handleBulkDelete = useCallback(async () => {
        const ids = [...selectedIds];
        if (ids.length === 0) return;
        if (!confirm(`Удалить ${formatNumber(ids.length, 0)} заявок? Это действие нельзя отменить.`)) return;
        setBulkDeleting(true);
        try {
            const res = await api.deleteAssemblyBulk(ids);
            const skippedIds = new Set(res.skipped.map(s => s.id));
            const removed = ids.filter(id => !skippedIds.has(id));
            setItems(prev => prev.filter(i => !removed.includes(i.id)));
            setTotal(t => Math.max(0, t - res.deleted));
            setSelectedIds(new Set());
            const note = res.skipped.length > 0 ? ` · пропущено ${formatNumber(res.skipped.length, 0)} (уже на WB)` : '';
            setToast({
                message: `Удалено заявок: ${formatNumber(res.deleted, 0)}${note}`,
                type: res.deleted > 0 ? 'success' : 'error',
            });
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка массового удаления', type: 'error' });
        } finally {
            setBulkDeleting(false);
        }
    }, [selectedIds]);

    // ─── Массовая смена статуса (ОДИН запрос — поштучные смены съедали общий
    // write-лимит и падали 429 «Слишком много запросов» на середине пачки) ───
    const [bulkStatusing, setBulkStatusing] = useState<AssemblyBulkStatus | null>(null);
    const handleBulkStatus = useCallback(async (status: AssemblyBulkStatus) => {
        if (selectedIds.size === 0 || bulkStatusing) return;
        // Бэкенд невалидные переходы отсеет и сам, но с технической причиной
        // («Cannot transition…») — заведомо непереводимые статусы фильтруем заранее
        // и говорим по-русски. READY достижим из сборки, IN_PROGRESS — ещё и из PENDING/READY.
        const eligible = new Set<AssemblyStatus>(status === 'READY' ? ['IN_PROGRESS', 'READY'] : ['PENDING', 'IN_PROGRESS', 'READY']);
        const selItems = items.filter(i => selectedIds.has(i.id));
        const ids = selItems.filter(i => eligible.has(i.status)).map(i => i.id);
        const ineligible = selItems.length - ids.length;
        const label = status === 'READY' ? 'Готово' : 'В сборке';
        if (ids.length === 0) {
            setToast({ message: `Среди выбранных нет заявок, которые можно перевести в «${label}»`, type: 'error' });
            return;
        }
        const inelNote = ineligible > 0 ? ` (${formatNumber(ineligible, 0)} из выбранных пропущу — статус не позволяет)` : '';
        if (!confirm(`Перевести ${formatNumber(ids.length, 0)} заявок в «${label}»?${inelNote}`)) return;
        setBulkStatusing(status);
        try {
            const res = await api.setAssemblyStatusBulk(ids, status);
            const byId = new Map(res.updated.map(r => [r.id, r]));
            setItems(prev => prev.map(i => byId.get(i.id) ?? i));
            setSelectedIds(new Set());
            const note = res.skipped.length > 0
                ? ` · пропущено ${formatNumber(res.skipped.length, 0)}: ${res.skipped.slice(0, 3).map(s => `${s.number || s.id} — ${s.reason}`).join('; ')}${res.skipped.length > 3 ? ' …' : ''}`
                : '';
            const inelToast = ineligible > 0 ? ` · ${formatNumber(ineligible, 0)} не в подходящем статусе` : '';
            setToast({
                message: `Переведено в «${label}»: ${formatNumber(res.updated.length, 0)}${note}${inelToast}`,
                type: res.updated.length > 0 ? 'success' : 'error',
            });
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка массовой смены статуса', type: 'error' });
        } finally {
            setBulkStatusing(null);
        }
    }, [selectedIds, items, bulkStatusing]);

    // ─── Columns (TanStackDataTable: сортировка + Excel) ────────────────────
    // Inline-редактирование (палеты/вес/дата/статус) и кнопки-действия живут
    // внутри render-ячеек; getValue даёт сортировку по вычисляемым колонкам,
    // exportValue — корректную выгрузку JSX-ячеек.
    const itemsQty = (row: AssemblyRequest) => row.items ? row.items.reduce((s, i) => s + (i.quantity || 0), 0) : 0;
    const cols: Column[] = [
        {
            key: '_select', label: '',
            getValue: () => '',
            exportValue: () => '',
            // Вся ячейка — большая кликабельная зона переключения, клик НЕ открывает
            // заявку (stopPropagation), чтобы не «проваливаться» при выборе галочек.
            render: (_v, row: AssemblyRequest) => (
                isBulkDeletable(row.status) ? (
                    <div
                        onClick={(e) => { e.stopPropagation(); toggleSelect(row.id); }}
                        title="Выбрать для удаления"
                        style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '10px 8px', margin: '-7px -6px', cursor: 'pointer' }}
                    >
                        <input
                            type="checkbox"
                            checked={selectedIds.has(row.id)}
                            readOnly
                            tabIndex={-1}
                            style={{ pointerEvents: 'none', accentColor: '#3b82f6', width: 18, height: 18 }}
                        />
                    </div>
                ) : null
            ),
        },
        {
            key: 'number', label: '№',
            render: (_v, row: AssemblyRequest) => (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 500 }}>{row.number}</span>
                    {row.is_pre_distribution && (
                        <span
                            className="badge badge-info"
                            style={{ fontSize: 11 }}
                            title="Заявка создана предраспределением машины в пути (до приёмки)"
                        >
                            🚚 Предраспределение{row.source_vehicle_order_no ? ` машины ${row.source_vehicle_order_no}` : ''}
                        </span>
                    )}
                    {row.is_prebooking && (
                        <span
                            className="badge badge-warning"
                            style={{ fontSize: 11 }}
                            title="Предзаявка на моно: целые моно-паллеты на склад без лимита приёмки (⌛) — сдаются бронью"
                        >
                            🅿️ Предзаявка
                        </span>
                    )}
                    {row.ff_review_pending && (
                        <span className="badge badge-warning" style={{ fontSize: 11 }} title="ФФ предложил правку состава — требуется согласование">
                            ⏳ ФФ
                        </span>
                    )}
                    {row.joint_supply && (
                        <span className="badge badge-info" style={{ fontSize: 11 }} title={jointTitle(row)}>
                            Совместная
                        </span>
                    )}
                </span>
            ),
            exportValue: (row: AssemblyRequest) => row.joint_supply ? `${row.number} (совместная)` : row.number,
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
            getValue: (row: AssemblyRequest) => ffLinksOf(row).map(l => l.ff_request_number || `#${l.ff_request_id}`).join(', '),
            // migfull/«Натали»: на одну сборку может быть несколько ФФ-заявок → показываем все
            render: (_v, row: AssemblyRequest) => {
                const links = ffLinksOf(row);
                if (!links.length) return '—';
                return (
                    <span style={{ display: 'inline-flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
                        {links.map(l => (
                            <Link
                                key={l.ff_request_id}
                                href={`/p/${slug}/warehouse/${l.ff_warehouse_id ?? row.warehouse_id}/ff-request/${l.ff_request_id}`}
                                onClick={(e) => e.stopPropagation()}
                                style={{ color: 'var(--color-accent)', fontWeight: 500, fontSize: 13 }}
                            >
                                {l.ff_request_number || `#${l.ff_request_id}`}{l.ff_stage_title ? ` (${l.ff_stage_title})` : ''}
                            </Link>
                        ))}
                        {row.ff_mismatch === true && (
                            <button
                                type="button"
                                className="badge badge-warning"
                                style={{ fontSize: 11, padding: '2px 8px', cursor: 'pointer', border: 'none' }}
                                title="Показать расхождения по позициям"
                                onClick={(e) => { e.stopPropagation(); setMismatchForId(row.id); }}
                            >
                                ⚠ расхождение
                            </button>
                        )}
                    </span>
                );
            },
            exportValue: (row: AssemblyRequest) => ffLinksOf(row).map(l => l.ff_request_number || `#${l.ff_request_id}`).join(', '),
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
                <WeightCell
                    row={row}
                    editable={EDITABLE_STATUSES.includes(row.status)}
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

            {mismatchForId != null && (
                <FfMismatchModal assemblyId={mismatchForId} onClose={() => setMismatchForId(null)} />
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
                            placeholder="Поиск по номеру заявки или поставке WB…"
                            value={searchInput}
                            onChange={e => setSearchInput(e.target.value)}
                            onKeyDown={handleSearchKeyDown}
                        />
                    </div>
                    <div className="form-group">
                        <select
                            className="form-input"
                            value={view}
                            onChange={e => { setView(e.target.value as 'active' | 'archived' | 'all'); }}
                            title="Принятые ВБ, закрытые и отменённые сборки уходят в архив"
                        >
                            <option value="active">Активные</option>
                            <option value="archived">Архив</option>
                            <option value="all">Все</option>
                        </select>
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
                        <select
                            className="form-input"
                            value={jointOnly ? 'joint' : ''}
                            onChange={e => { setJointOnly(e.target.value === 'joint'); }}
                        >
                            <option value="">Поставка: все</option>
                            <option value="joint">Только совместные</option>
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
                    onRowClick={(row: AssemblyRequest) => {
                        // В режиме выбора (есть отмеченные) клик по строке переключает
                        // её галочку и НЕ открывает заявку — чтобы выбор не слетал.
                        if (selectedIds.size > 0) {
                            if (isBulkDeletable(row.status)) toggleSelect(row.id);
                            return;
                        }
                        router.push(`/p/${slug}/warehouse/assembly/${row.id}`);
                    }}
                    rowClassName={(row: AssemblyRequest) =>
                        selectedIds.has(row.id) ? 'assembly-row-selected'
                            : (highlightActive && justCreatedIds.has(row.id) ? 'assembly-row-just-created' : '')
                    }
                    pageSize={CLIENT_PAGE_SIZE}
                    exportName="assembly_requests"
                    actions={
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            {selectedIds.size > 0 ? (
                                <>
                                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Выбрано: {formatNumber(selectedIds.size, 0)}</span>
                                    <button className="btn btn-secondary btn-sm" onClick={() => handleBulkStatus('IN_PROGRESS')} disabled={bulkDeleting || bulkStatusing != null}
                                        title="Перевести все выбранные в «В сборке» одним запросом">
                                        {bulkStatusing === 'IN_PROGRESS' ? 'Перевожу…' : '→ В сборке'}
                                    </button>
                                    <button className="btn btn-success btn-sm" onClick={() => handleBulkStatus('READY')} disabled={bulkDeleting || bulkStatusing != null}
                                        title="Перевести все выбранные в «Готово» одним запросом (нужны поставка WB, палеты и вес)">
                                        {bulkStatusing === 'READY' ? 'Перевожу…' : '→ Готово'}
                                    </button>
                                    <button className="btn btn-danger btn-sm" onClick={handleBulkDelete} disabled={bulkDeleting || bulkStatusing != null}>
                                        {bulkDeleting ? 'Удаление…' : `🗑 Удалить выбранные (${formatNumber(selectedIds.size, 0)})`}
                                    </button>
                                    <button className="btn btn-secondary btn-sm" onClick={() => setSelectedIds(new Set())}>Снять</button>
                                </>
                            ) : (
                                deletableCount > 0 ? (
                                    <button className="btn btn-secondary btn-sm" onClick={selectAllDeletable} title="Выбрать все заявки, которые ещё не на WB">
                                        ☑ Выбрать все удаляемые ({formatNumber(deletableCount, 0)})
                                    </button>
                                ) : null
                            )}
                        </div>
                    }
                />
            )}
        </div>
    );
}
