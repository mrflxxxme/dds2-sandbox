'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatNumber, pluralRu, exportToExcel } from '@/lib/utils';
import { Toast } from '@/components';
import TanStackDataTable from '@/components/TanStackDataTable';
import { FfMismatchModal } from '@/components/FfMismatchModal';
import type { Column } from '@/components/DataTable';
import { usePermissions } from '@/lib/hooks/usePermissions';
import type { AssemblyBulkStatus, AssemblyDraft, AssemblyRequest, AssemblyStatus, CreatedAssemblyGroup, FfLinkInfo, SourceVehicleOption, Warehouse, WbSupplySyncStatus } from '@/types/api';
import { findDuplicateLanes } from '@/lib/utils/assemblyDraftMerge';
import { ASSEMBLY_STATUS_MAP } from '@/lib/assembly-status';
import CreatedRequestsModal, { type CreatedRequestRow } from './distribute/components/CreatedRequestsModal';

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

// Подпись типа поставки WB для колонки «Тип поставки».
const PKG_TYPE_LABEL: Record<string, string> = { BOX: '📦 Короб', MONOPALLET: '📐 Моно', SUPERSAFE: '🔒 Сейф' };
// Плоские метки упаковки для Excel-выгрузки «Сборочного листа» (без эмодзи).
const PKG_PLAIN: Record<string, string> = { BOX: 'Короб', MONOPALLET: 'Моно', SUPERSAFE: 'Сейф' };

// Подпись-тултип бейджа «Совместная»: другие сборки той же WB-поставки.
function jointTitle(row: AssemblyRequest): string {
    const sibs = row.joint_siblings || [];
    if (!sibs.length) return 'Совместная WB-поставка (несколько сборок с разных ФФ)';
    const parts = sibs.map(s => `${s.warehouse_name || `Склад ${s.warehouse_id}`} (${s.number})`);
    return `Совместная WB-поставка · ещё: ${parts.join(', ')}`;
}

// Единый словарь статусов — src/lib/assembly-status.ts (был копией на каждой странице раздела)
const STATUS_MAP = ASSEMBLY_STATUS_MAP;

// Занос заявки в кабинет WB (колонка «WB»): фолбэк-подпись, когда живой статус
// кабинета (wb_supply_state) ещё не подтянут.
const WB_SYNC_BRIEF: Record<WbSupplySyncStatus, { label: string; className: string }> = {
    NONE:     { label: 'Не заведена',      className: 'badge-secondary' },
    DRAFT:    { label: 'Черновик',          className: 'badge-info' },
    PREORDER: { label: 'Преордер',          className: 'badge-warning' },
    BOOKED:   { label: 'Дата забронирована', className: 'badge-info' },
    BOXED:    { label: 'Короба занесены',   className: 'badge-info' },
    PASSED:   { label: 'Пропуск занесён',   className: 'badge-success' },
    ERROR:    { label: 'Ошибка',            className: 'badge-danger' },
};

// Строка WB-сводки описывает реальную поставку (а не голый локальный черновик
// пропуска, который заводится при назначении машины). Голый черновик в колонке
// «WB» показываем как «—»: поставку в кабинете ещё не создавали.
function hasWbSupply(wb: AssemblyRequest['wb_supply']): boolean {
    if (!wb) return false;
    return wb.sync_status !== 'NONE' || wb.supply_id != null || wb.preorder_id != null;
}

// Номер занесённой в кабинет поставки ДО связки с WbFboSupply: бронь → supply_id,
// предзаказ → preorder_id. Единый источник для render и Excel-экспорта колонки
// «FBO поставка». Идентификаторы — без formatNumber (разряды в номере не нужны).
function wbCabinetNo(wb: AssemblyRequest['wb_supply']): string | null {
    if (!wb) return null;
    if (wb.supply_id != null) return `Поставка ${wb.supply_id}`;
    if (wb.preorder_id != null) return `Предзаказ ${wb.preorder_id}`;
    return null;
}

// Статусы, в которых расхождение паллет уже имеет смысл: до «Готово» число паллет
// ещё правят на сборке, после отгрузки — это факт, который важно видеть.
const PALLET_MISMATCH_STATUSES: ReadonlySet<AssemblyStatus> = new Set<AssemblyStatus>([
    'READY', 'VEHICLE_ASSIGNED', 'SHIPPED',
]);

// Расхождение локального числа паллет с паллетами WB-пропуска.
function palletsMismatch(row: AssemblyRequest): boolean {
    const wb = row.wb_supply;
    if (!PALLET_MISMATCH_STATUSES.has(row.status)) return false;
    return !!wb && wb.pass_pallets != null && wb.pass_pallets !== row.pallets_count;
}

const EDITABLE_STATUSES: AssemblyStatus[] = ['IN_PROGRESS', 'READY'];

// Заявки, которые ещё НЕ отгружены на WB → можно массово удалить (товар физически
// не списан, висит только резерв). Отгруженные (SHIPPED/DELIVERED/RETURNED/CLOSED)
// удалять нельзя — сначала отмена с откатом стока.
const BULK_DELETABLE_STATUSES = new Set<AssemblyStatus>(['PENDING', 'IN_PROGRESS', 'READY', 'VEHICLE_ASSIGNED', 'CANCELLED']);
const isBulkDeletable = (status: string): boolean => BULK_DELETABLE_STATUSES.has(status as AssemblyStatus);
// Выбор строки в списке (чекбокс) — надмножество удаляемых + PRE_DISTRIBUTED, чтобы
// дубли экрана «Распределить машину» можно было выделить и объединить кнопкой «🔗».
const isRowSelectable = (status: string): boolean => isBulkDeletable(status) || status === 'PRE_DISTRIBUTED';

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
    // '' | pre_dist | prebooking | plain | `veh:<CostOrder.id>` (конкретная машина)
    // | `draft:<draft_id>` (заявки конкретного черновика).
    const [sourceFilter, setSourceFilter] = useState<string>('');
    const [sourceVehicles, setSourceVehicles] = useState<SourceVehicleOption[]>([]);

    // ─── Персист фильтров ───────────────────────────────────────────────
    // Заход в заявку — это router.push на отдельную страницу, обратно возвращает
    // <Link>: список размонтируется и весь useState умирает. Поэтому храним набор
    // фильтров в localStorage (на проект) и восстанавливаем после монтирования.
    // localStorage недоступен на SSR → только внутри useEffect, всё в try/catch.
    const FILTERS_KEY = `assembly_filters_${slug}`;
    const filtersRestored = useRef(false);
    const sourceFromDeeplink = useRef(false);
    const [filtersReady, setFiltersReady] = useState(false);

    useEffect(() => {
        try {
            const raw = localStorage.getItem(FILTERS_KEY);
            const f = (raw ? JSON.parse(raw) : {}) as Record<string, unknown>;
            // Ветка else обязательна: смена проекта (/p/a/... → /p/b/...) не размонтирует
            // страницу, и без явного сброса фильтры проекта A утекли бы в проект B —
            // а save-эффект тут же записал бы их под новым ключом.
            setWarehouseId(typeof f.warehouseId === 'number' ? f.warehouseId : '');
            setStatusFilter(typeof f.statusFilter === 'string' ? f.statusFilter : '');
            setView(f.view === 'archived' || f.view === 'all' ? f.view : 'active');
            setDateFrom(typeof f.dateFrom === 'string' ? f.dateFrom : '');
            setDateTo(typeof f.dateTo === 'string' ? f.dateTo : '');
            const s = typeof f.search === 'string' ? f.search : '';
            setSearch(s);
            setSearchInput(s);
            setBrandFilter(typeof f.brandFilter === 'string' ? f.brandFilter : '');
            setFfLinkFilter(f.ffLinkFilter === 'none' || f.ffLinkFilter === 'linked' ? f.ffLinkFilter : '');
            setJointOnly(f.jointOnly === true);
            setSourceFilter(typeof f.sourceFilter === 'string' ? f.sourceFilter : '');
        } catch { /* SSR / битый JSON — просто стартуем с дефолтов */ }
        // Диплинк прошлого проекта не должен глушить персист нового.
        sourceFromDeeplink.current = false;
        filtersRestored.current = true;
        setFiltersReady(true);
    }, [FILTERS_KEY]);

    // Диплинк на фильтр «Источник» (модалка созданных заявок / внешние ссылки):
    // ?src_draft=<id> → заявки черновика, ?src_vehicle=<id> → заявки машины.
    useEffect(() => {
        const d = Number(searchParams.get('src_draft'));
        const v = Number(searchParams.get('src_vehicle'));
        if (d > 0) setSourceFilter(`draft:${d}`);
        else if (v > 0) setSourceFilter(`veh:${v}`);
        // Диплинк — разовый переход, а не выбор пользователя: не персистим его,
        // иначе следующий заход БЕЗ query покажет заявки того же черновика без
        // видимой причины. Ref снимается, как только юзер сам трогает «Источник».
        if (d > 0 || v > 0) sourceFromDeeplink.current = true;
    }, [searchParams]);
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
        api.getAssemblySourceVehicles()
            .then(setSourceVehicles)
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
                // `veh:<id>` — конкретная машина (точечный source_vehicle_id),
                // `draft:<id>` — заявки конкретного черновика (source_draft_id),
                // иначе — категория source.
                source: sourceFilter && !sourceFilter.startsWith('veh:') && !sourceFilter.startsWith('draft:')
                    ? (sourceFilter as 'pre_dist' | 'prebooking' | 'plain')
                    : undefined,
                source_vehicle_id: sourceFilter.startsWith('veh:') ? Number(sourceFilter.slice(4)) : undefined,
                draft_id: sourceFilter.startsWith('draft:') ? Number(sourceFilter.slice(6)) : undefined,
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
    }, [warehouseId, statusFilter, view, search, dateFrom, dateTo, brandFilter, ffLinkFilter, jointOnly, sourceFilter]);

    // Не грузим до восстановления фильтров — иначе первый запрос уйдёт с дефолтами
    // и список моргнёт «все заявки» → «отфильтрованные».
    useEffect(() => { if (filtersReady) load(); }, [load, filtersReady]);

    // Сохраняем фильтры после каждого изменения (но не до восстановления —
    // иначе дефолты затрут сохранённый набор).
    useEffect(() => {
        if (!filtersRestored.current) return;
        try {
            localStorage.setItem(FILTERS_KEY, JSON.stringify({
                warehouseId, statusFilter, view, dateFrom, dateTo,
                search, brandFilter, ffLinkFilter, jointOnly,
                sourceFilter: sourceFromDeeplink.current ? '' : sourceFilter,
            }));
        } catch { /* приватный режим / переполнение quota — не критично */ }
    }, [FILTERS_KEY, warehouseId, statusFilter, view, dateFrom, dateTo,
        search, brandFilter, ffLinkFilter, jointOnly, sourceFilter]);

    /** Активен ли хоть один фильтр (view='active' — дефолт, не считается) */
    const hasActiveFilters = warehouseId !== '' || statusFilter !== '' || view !== 'active'
        || dateFrom !== '' || dateTo !== '' || search !== '' || brandFilter !== ''
        || ffLinkFilter !== '' || jointOnly || sourceFilter !== '';

    const resetFilters = useCallback(() => {
        setWarehouseId('');
        setStatusFilter('');
        setView('active');
        setDateFrom('');
        setDateTo('');
        setSearchInput('');
        setSearch('');
        setBrandFilter('');
        setFfLinkFilter('');
        setJointOnly(false);
        sourceFromDeeplink.current = false;
        setSourceFilter('');
        // Ключ не чистим: save-эффект тут же перезапишет его дефолтами.
    }, []);

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

    // ─── Bulk-синк WB-состояний (F1): опрашивает кабинет по всем заявкам проекта ──
    const [wbSyncing, setWbSyncing] = useState(false);
    const handleWbSyncAll = useCallback(async () => {
        if (wbSyncing) return;
        setWbSyncing(true);
        try {
            const res = await api.wbSupplySyncAllStates();
            setToast({
                message: `Проверено ${formatNumber(res.checked, 0)}, обновлено ${formatNumber(res.updated, 0)}`,
                type: 'success',
            });
            await load();
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка синхронизации WB', type: 'error' });
        } finally {
            setWbSyncing(false);
        }
    }, [wbSyncing, load]);

    // ─── Массовое удаление (заявки из черновика, ещё не на WB) ───────────────
    const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
    // «🚀 WB / ФФ» по выбранным: та же модалка, что после коммита черновика
    // (фоновый занос WB по одной + заявки ФФ по провайдерам).
    const [createExternal, setCreateExternal] = useState<CreatedRequestRow[] | null>(null);
    const openCreateExternal = useCallback(() => {
        const rows: CreatedRequestRow[] = items.filter(r => selectedIds.has(r.id)).map(r => ({
            id: r.id,
            number: r.number,
            ffId: r.warehouse_id,
            ffName: r.warehouse_name || `Склад ${r.warehouse_id}`,
            wbName: r.effective_wb_warehouse ?? r.wb_warehouse_name_manual ?? r.wb_warehouse_name ?? null,
            pkg: r.package_type ?? 'BOX',
            qty: (r.items ?? []).reduce((s, i) => s + (i.quantity || 0), 0),
            sku: (r.items ?? []).length,
        }));
        if (rows.length > 0) setCreateExternal(rows);
    }, [items, selectedIds]);
    const [bulkDeleting, setBulkDeleting] = useState(false);
    const deletableCount = useMemo(() => items.filter(i => isBulkDeletable(i.status)).length, [items]);

    // Уникальные товары без веса, участвующие в сборках (по всем загруженным заявкам) —
    // для баннера «заполнить вес». Имя резолвим из позиций той же заявки.
    const missingWeightItems = useMemo(() => {
        const byBarcode = new Map<string, string>();
        for (const req of items) {
            const missing = req.weight_missing_barcodes;
            if (!missing || missing.length === 0) continue;
            const nameByBc = new Map<string, string>();
            for (const it of req.items || []) nameByBc.set(it.barcode, it.article || it.product_name || it.barcode);
            for (const bc of missing) {
                if (!byBarcode.has(bc)) byBarcode.set(bc, nameByBc.get(bc) || bc);
            }
        }
        return [...byBarcode.values()];
    }, [items]);

    // Заявки, у которых паллеты заявки ≠ паллетам WB-пропуска (Готово / Машина
    // назначена / Отгружена) — баннер сверху + бейдж «≠ WB» в колонке «Палеты».
    const palletMismatchRows = useMemo(() => items.filter(palletsMismatch), [items]);

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

    // Экспорт «Сборочный лист»: одна строка = один товар в заявке (по всему
    // отфильтрованному набору, а не по выбору галочками). Для машины V-…: видно,
    // что и сколько (коробов + штук) едет на каждый WB-склад. Коробов из бэка
    // (⌈штук/кратность⌉); пусто, если кратность не задана. + строка ИТОГО.
    const exportPickList = useCallback(() => {
        const rows = items.flatMap(r =>
            (r.items ?? []).map(it => ({
                number: r.number,
                wb: r.effective_wb_warehouse || r.wb_warehouse_name || '',
                supply: r.wb_supply_name || wbCabinetNo(r.wb_supply) || '',
                // Голый номер поставки WB: FBW-номер связанной FBO-поставки, иначе
                // кабинетный номер (бронь supply_id → предзаказ preorder_id).
                supply_no: r.wb_supply_id_wb || r.wb_supply?.supply_id || r.wb_supply?.preorder_id || '',
                pkg: PKG_PLAIN[r.package_type || 'BOX'] || r.package_type || '',
                article: it.article || it.product_name || '',
                barcode: it.barcode,
                boxes: it.boxes ?? '',
                qty: it.quantity,
            })),
        );
        if (rows.length === 0) {
            setToast({ message: 'Нет позиций для выгрузки', type: 'error' });
            return;
        }
        const totalBoxes = items.reduce((s, r) => s + (r.items ?? []).reduce((a, it) => a + (it.boxes ?? 0), 0), 0);
        const totalQty = items.reduce((s, r) => s + (r.items ?? []).reduce((a, it) => a + (it.quantity || 0), 0), 0);
        rows.push({ number: 'ИТОГО', wb: '', supply: '', supply_no: '', pkg: '', article: '', barcode: '', boxes: totalBoxes, qty: totalQty });
        exportToExcel(rows, 'assembly_picklist', [
            { key: 'number', label: '№ заявки' },
            { key: 'wb', label: 'WB-склад' },
            { key: 'supply', label: 'FBO поставка' },
            { key: 'supply_no', label: '№ поставки WB' },
            { key: 'pkg', label: 'Тип' },
            { key: 'article', label: 'Артикул' },
            { key: 'barcode', label: 'Баркод' },
            { key: 'boxes', label: 'Коробов' },
            { key: 'qty', label: 'Штук' },
        ]);
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

    // ─── Объединение выбранных сборок (дубли одного направления) ─────────────
    // Бэкенд склеит только однородный набор (склад · направление · упаковка) в статусе
    // «В сборке»; здесь то же условие проверяем заранее, чтобы гейтить кнопку и объяснить.
    const [bulkMerging, setBulkMerging] = useState(false);
    const mergeDirKey = useCallback((r: AssemblyRequest) => {
        const pkg = r.package_type || 'BOX';
        // Пре-дистрибуции разных машин на одно направление не сливаем (зеркалит backend).
        const veh = r.source_vehicle_id != null ? `::veh${r.source_vehicle_id}` : '';
        if (r.wb_fbo_supply_id != null) return `${r.warehouse_id}::${pkg}::sup${r.wb_fbo_supply_id}${veh}`;
        return `${r.warehouse_id}::${pkg}::wh${(r.wb_warehouse_name_manual || r.wb_warehouse_name || '').trim()}${veh}`;
    }, []);
    const mergeEligibility = useMemo<{ ok: boolean; reason: string }>(() => {
        const sel = items.filter(i => selectedIds.has(i.id));
        if (sel.length < 2) return { ok: false, reason: 'Выберите ≥2 заявки одного направления' };
        // «В сборке» (PENDING/IN_PROGRESS) или «Распределено под машину» (PRE_DISTRIBUTED);
        // смешивать PRE_DISTRIBUTED с обычными нельзя (у него нет реального стока).
        const okStatus = (s: AssemblyStatus) => s === 'PENDING' || s === 'IN_PROGRESS' || s === 'PRE_DISTRIBUTED';
        if (sel.some(i => !okStatus(i.status))) {
            return { ok: false, reason: 'Объединять можно только «В сборке» / «Распределено»' };
        }
        const hasPre = sel.some(i => i.status === 'PRE_DISTRIBUTED');
        if (hasPre && sel.some(i => i.status !== 'PRE_DISTRIBUTED')) {
            return { ok: false, reason: 'Нельзя смешивать «Распределено под машину» с «В сборке»' };
        }
        if (new Set(sel.map(mergeDirKey)).size > 1) {
            return { ok: false, reason: 'Выбраны разные склад / направление / упаковка / машина' };
        }
        return { ok: true, reason: '' };
    }, [items, selectedIds, mergeDirKey]);

    const handleBulkMerge = useCallback(async () => {
        const ids = [...selectedIds];
        if (ids.length < 2 || bulkMerging) return;
        if (!confirm(`Объединить ${formatNumber(ids.length, 0)} ${pluralRu(ids.length, ['заявку', 'заявки', 'заявок'])} в одну?`)) return;
        setBulkMerging(true);
        try {
            const survivor = await api.mergeAssemblyRequests(ids);
            setItems(prev => prev
                .filter(i => !ids.includes(i.id) || i.id === survivor.id)
                .map(i => (i.id === survivor.id ? survivor : i)));
            setTotal(t => Math.max(0, t - (ids.length - 1)));
            setSelectedIds(new Set());
            setToast({ message: `Объединено в ${survivor.number}`, type: 'success' });
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка объединения', type: 'error' });
        } finally {
            setBulkMerging(false);
        }
    }, [selectedIds, bulkMerging]);

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
                isRowSelectable(row.status) ? (
                    <div
                        onClick={(e) => { e.stopPropagation(); toggleSelect(row.id); }}
                        title="Выбрать (удаление / объединение)"
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
                    {row.is_pre_distribution ? (
                        <span
                            className="badge badge-info"
                            style={{ fontSize: 11 }}
                            title="Заявка создана предраспределением машины в пути (до приёмки)"
                        >
                            🚚 Предраспределение{row.source_vehicle_order_no ? ` машины ${row.source_vehicle_order_no}` : ''}
                        </span>
                    ) : row.source_vehicle_order_no ? (
                        <span
                            className="badge badge-info"
                            style={{ fontSize: 11 }}
                            title="Обычная заявка, созданная распределением уже принятой машины (остатки списаны как у обычной сборки)"
                        >
                            🚚 Из машины {row.source_vehicle_order_no}
                        </span>
                    ) : null}
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
            render: (_v, row: AssemblyRequest) => {
                // Склад назначения WB известен из заявки (wb_warehouse_name_manual) ещё до
                // связки с FBO-поставкой → показываем его сразу; после связки добавится номер
                // поставки (wb_supply_name). effective_wb_warehouse = supply-склад → manual.
                // До связки у занесённой заявки уже есть номер в кабинете: бронь → supply_id,
                // предзаказ → preorder_id (номер из assembly_wb_supply, а не FBO-синка).
                const wbWh = row.effective_wb_warehouse || row.wb_warehouse_name;
                const cabinetNo = !row.wb_supply_name ? wbCabinetNo(row.wb_supply) : null;
                return (
                    <span style={{ fontSize: 13 }}>
                        {row.wb_supply_name
                            || (cabinetNo && <span style={{ color: 'var(--color-text-muted)' }}>{cabinetNo}</span>)
                            || '—'}
                        {wbWh && (
                            <span style={{ display: 'block', fontSize: 12, color: 'var(--color-text-muted)' }}>{wbWh}</span>
                        )}
                    </span>
                );
            },
            exportValue: (row: AssemblyRequest) =>
                row.wb_supply_name || wbCabinetNo(row.wb_supply) || row.effective_wb_warehouse || '',
        },
        {
            key: 'package_type', label: 'Тип поставки',
            render: (_v, row: AssemblyRequest) => {
                const pkg = row.package_type || 'BOX';
                const badge = pkg === 'MONOPALLET' ? 'badge-warning' : pkg === 'SUPERSAFE' ? 'badge-danger' : 'badge-info';
                return <span className={`badge ${badge}`} style={{ fontSize: 11 }}>{PKG_TYPE_LABEL[pkg]}</span>;
            },
            exportValue: (row: AssemblyRequest) => PKG_TYPE_LABEL[row.package_type || 'BOX'],
        },
        {
            key: 'wb_supply', label: 'WB',
            getValue: (row: AssemblyRequest) => {
                const wb = row.wb_supply;
                if (!hasWbSupply(wb) || !wb) return '';
                return wb.wb_supply_state || WB_SYNC_BRIEF[wb.sync_status]?.label || wb.sync_status;
            },
            render: (_v, row: AssemblyRequest) => {
                const wb = row.wb_supply;
                if (!hasWbSupply(wb) || !wb) return <span style={{ color: 'var(--color-text-muted)' }}>—</span>;
                // Живой статус кабинета (В сборке / В пути…) в приоритете; иначе — стадия заноса.
                const cfg = WB_SYNC_BRIEF[wb.sync_status] || { label: wb.sync_status, className: 'badge-secondary' };
                const label = wb.wb_supply_state || cfg.label;
                return <span className={`badge ${cfg.className}`} style={{ fontSize: 11 }}>{label}</span>;
            },
            exportValue: (row: AssemblyRequest) => {
                const wb = row.wb_supply;
                if (!hasWbSupply(wb) || !wb) return '';
                return wb.wb_supply_state || WB_SYNC_BRIEF[wb.sync_status]?.label || wb.sync_status;
            },
        },
        {
            // Дата забронированного слота сдачи в WB (supplyDetails.supplyDate).
            key: 'wb_supply_date', label: 'Дата брони WB',
            getValue: (row: AssemblyRequest) => row.wb_supply?.supply_date || '',
            render: (_v, row: AssemblyRequest) => (
                row.wb_supply?.supply_date
                    ? <span>{formatDate(row.wb_supply.supply_date)}</span>
                    : <span style={{ color: 'var(--color-text-muted)' }}>—</span>
            ),
            exportValue: (row: AssemblyRequest) => (
                row.wb_supply?.supply_date ? formatDate(row.wb_supply.supply_date) : ''
            ),
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
                <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'flex-end', gap: 6, whiteSpace: 'nowrap' }}>
                    <EditableCell
                        value={row.pallets_count}
                        editable={EDITABLE_STATUSES.includes(row.status)}
                        highlight={row.status === 'IN_PROGRESS' && (!row.pallets_count || row.pallets_count <= 0)}
                        onSave={(val) => handlePalletsChange(row, val)}
                    />
                    {palletsMismatch(row) && (
                        <span
                            className="badge badge-warning"
                            style={{ fontSize: 11, padding: '2px 6px' }}
                            title={`Локально ${formatNumber(row.pallets_count, 0)} паллет, в WB-пропуске ${formatNumber(row.wb_supply?.pass_pallets ?? 0, 0)}`}
                        >
                            ≠ WB
                        </span>
                    )}
                </span>
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
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <button
                        className="btn btn-secondary"
                        onClick={handleWbSyncAll}
                        disabled={wbSyncing}
                        title="Опросить кабинет WB и обновить статусы поставок по всем заявкам"
                    >
                        {wbSyncing ? 'Синхронизация…' : '🔄 Синхронизировать WB'}
                    </button>
                    {canEdit() && (
                        <Link href={`/p/${slug}/warehouse/assembly/new`}>
                            <button className="btn btn-primary">Создать заявку</button>
                        </Link>
                    )}
                </div>
            </div>

            {/* Баннер: товары без веса, участвующие в сборках → расчёт веса неполный. */}
            {missingWeightItems.length > 0 && (
                <div
                    className="glass-card"
                    style={{
                        padding: '12px 16px', marginBottom: 16,
                        display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                        borderLeft: '3px solid var(--color-warning)',
                    }}
                >
                    <span style={{ fontSize: 18 }}>⚠️</span>
                    <div style={{ flex: 1, minWidth: 240 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text)' }}>
                            Нет веса у {formatNumber(missingWeightItems.length, 0)} товаров в сборках — расчётный вес неполный
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {missingWeightItems.slice(0, 8).join(', ')}
                            {missingWeightItems.length > 8 && ` и ещё ${formatNumber(missingWeightItems.length - 8, 0)}`}
                        </div>
                    </div>
                    <Link href={`/p/${slug}/settings?tab=duties`}>
                        <button className="btn btn-secondary btn-sm">📝 Заполнить вес</button>
                    </Link>
                </div>
            )}

            {/* Баннер: паллеты заявки расходятся с паллетами WB-пропуска. */}
            {palletMismatchRows.length > 0 && (
                <div
                    className="glass-card"
                    style={{
                        padding: '12px 16px', marginBottom: 16,
                        display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                        borderLeft: '3px solid var(--color-warning)',
                    }}
                >
                    <span style={{ fontSize: 18 }}>⚠️</span>
                    <div style={{ flex: 1, minWidth: 240 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text)' }}>
                            Паллеты не сходятся с WB-пропуском у {formatNumber(palletMismatchRows.length, 0)}{' '}
                            {pluralRu(palletMismatchRows.length, ['заявки', 'заявок', 'заявок'])}
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {palletMismatchRows.slice(0, 8).map(r => (
                                `${r.number}: ${formatNumber(r.pallets_count, 0)} ≠ ${formatNumber(r.wb_supply?.pass_pallets ?? 0, 0)} в WB`
                            )).join(' · ')}
                            {palletMismatchRows.length > 8 && ` и ещё ${formatNumber(palletMismatchRows.length - 8, 0)}`}
                        </div>
                    </div>
                </div>
            )}

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
                        <select
                            className="form-input"
                            value={sourceFilter}
                            onChange={e => { sourceFromDeeplink.current = false; setSourceFilter(e.target.value); }}
                            title="Происхождение заявки: из предраспределения машины (все или конкретная), из предброни (предзаявка) или обычная"
                        >
                            <option value="">Источник: все</option>
                            <option value="pre_dist">🚚 Из машины (все)</option>
                            {sourceVehicles.map(v => (
                                <option key={v.id} value={`veh:${v.id}`}>🚚 {v.order_no}</option>
                            ))}
                            {/* Машина из диплинка ?src_vehicle=, которой нет в справочнике, — sticky-опция. */}
                            {sourceFilter.startsWith('veh:') && !sourceVehicles.some(v => `veh:${v.id}` === sourceFilter) && (
                                <option value={sourceFilter}>🚚 Машина #{sourceFilter.slice(4)}</option>
                            )}
                            {/* Черновики с активными созданными заявками (created-groups). */}
                            {createdGroups.map(g => (
                                <option key={`draft-${g.draft_id}`} value={`draft:${g.draft_id}`}>
                                    🧩 {g.draft_name || `Черновик #${g.draft_id}`}
                                </option>
                            ))}
                            {/* Выбранный черновик, которого уже нет в активных группах, — sticky-опция. */}
                            {sourceFilter.startsWith('draft:') && !createdGroups.some(g => `draft:${g.draft_id}` === sourceFilter) && (
                                <option value={sourceFilter}>🧩 Черновик #{sourceFilter.slice(6)}</option>
                            )}
                            <option value="prebooking">🅿️ Предзаявки</option>
                            <option value="plain">Обычные</option>
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
                    {hasActiveFilters && (
                        <div className="form-group">
                            <button
                                className="btn btn-secondary"
                                onClick={resetFilters}
                                title="Сбросить все фильтры и вернуться к виду «Активные»"
                            >
                                ✕ Сбросить
                            </button>
                        </div>
                    )}
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
                            if (isRowSelectable(row.status)) toggleSelect(row.id);
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
                            {items.length > 0 && (
                                <button
                                    className="btn btn-secondary btn-sm"
                                    onClick={exportPickList}
                                    title="Excel по позициям: № заявки · WB-склад · предзаказ · артикул · баркод · коробов · штук (по текущему фильтру)"
                                >
                                    📋 Сборочный лист
                                </button>
                            )}
                            {selectedIds.size > 0 ? (
                                <>
                                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Выбрано: {formatNumber(selectedIds.size, 0)}</span>
                                    <button className="btn btn-secondary btn-sm" onClick={() => handleBulkStatus('IN_PROGRESS')} disabled={bulkDeleting || bulkStatusing != null || bulkMerging}
                                        title="Перевести все выбранные в «В сборке» одним запросом">
                                        {bulkStatusing === 'IN_PROGRESS' ? 'Перевожу…' : '→ В сборке'}
                                    </button>
                                    <button className="btn btn-success btn-sm" onClick={() => handleBulkStatus('READY')} disabled={bulkDeleting || bulkStatusing != null || bulkMerging}
                                        title="Перевести все выбранные в «Готово» одним запросом (нужны поставка WB, палеты и вес)">
                                        {bulkStatusing === 'READY' ? 'Перевожу…' : '→ Готово'}
                                    </button>
                                    {selectedIds.size >= 2 && (
                                        <button className="btn btn-secondary btn-sm" onClick={handleBulkMerge}
                                            disabled={bulkDeleting || bulkStatusing != null || bulkMerging || !mergeEligibility.ok}
                                            title={mergeEligibility.ok ? 'Объединить выбранные заявки одного направления в одну' : mergeEligibility.reason}>
                                            {bulkMerging ? 'Объединение…' : `🔗 Объединить (${formatNumber(selectedIds.size, 0)})`}
                                        </button>
                                    )}
                                    <button className="btn btn-primary btn-sm" onClick={openCreateExternal} disabled={bulkDeleting || bulkStatusing != null || bulkMerging}
                                        title="Занести выбранные на WB (преордеры, в фоне по одной) и/или создать заявки ФФ — модалка с per-заявка статусами">
                                        🚀 WB / ФФ ({formatNumber(selectedIds.size, 0)})
                                    </button>
                                    <button className="btn btn-danger btn-sm" onClick={handleBulkDelete} disabled={bulkDeleting || bulkStatusing != null || bulkMerging}>
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
            {/* «🚀 WB / ФФ» по выбранным заявкам — реюз модалки созданных заявок. */}
            {createExternal && (
                <CreatedRequestsModal
                    slug={slug}
                    rows={createExternal}
                    onClose={() => { setCreateExternal(null); load(); }}
                />
            )}
        </div>
    );
}
