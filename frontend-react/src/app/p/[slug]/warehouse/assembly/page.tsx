'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import { Toast } from '@/components';
import type { AssemblyRequest, AssemblyStatus, Warehouse } from '@/types/api';

// ─── Status config ──────────────────────────────────────────────────────────

const STATUS_MAP: Record<AssemblyStatus, { label: string; className: string }> = {
    PENDING:          { label: 'Ожидает сборку',    className: 'badge-warning' },
    IN_PROGRESS:      { label: 'В сборке',          className: 'badge-info' },
    READY:            { label: 'Готово',             className: 'badge-success' },
    VEHICLE_ASSIGNED: { label: 'Машина назначена',   className: 'badge-info' },
    SHIPPED:          { label: 'Отгружена',          className: 'badge-success' },
    DELIVERED:        { label: 'Принята WB',         className: 'badge-success' },
    CANCELLED:        { label: 'Отменена',           className: 'badge-secondary' },
};

const EDITABLE_STATUSES: AssemblyStatus[] = ['PENDING', 'IN_PROGRESS', 'READY'];

const STATUS_OPTIONS_FILTER: { value: string; label: string }[] = [
    { value: '', label: 'Все статусы' },
    { value: 'PENDING', label: 'Ожидает сборку' },
    { value: 'IN_PROGRESS', label: 'В сборке' },
    { value: 'READY', label: 'Готово' },
    { value: 'VEHICLE_ASSIGNED', label: 'Машина назначена' },
    { value: 'SHIPPED', label: 'Отгружена' },
    { value: 'DELIVERED', label: 'Принята WB' },
    { value: 'CANCELLED', label: 'Отменена' },
];

// ─── Status colors for Tailwind badge ────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
    PENDING:          'bg-amber-100 text-amber-700 border-amber-200 focus:ring-amber-400',
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
}: {
    item: AssemblyRequest;
    onStatusChange: (id: number, newStatus: AssemblyStatus) => void;
}) {
    const status = STATUS_MAP[item.status] || { label: item.status, className: '' };
    const canEdit = EDITABLE_STATUSES.includes(item.status);
    const colorClass = STATUS_COLORS[item.status] || 'bg-slate-100 text-slate-700 border-slate-200';

    if (!canEdit) {
        return (
            <span className={`inline-block text-xs font-semibold rounded-full py-1.5 px-3 border ${colorClass}`}>
                {status.label}
            </span>
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
                <option value="PENDING">Ожидает сборку</option>
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

// ─── Component ──────────────────────────────────────────────────────────────

export default function AssemblyListPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;

    // Data
    const [items, setItems] = useState<AssemblyRequest[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    // Filters
    const [warehouseId, setWarehouseId] = useState<number | ''>('');
    const [statusFilter, setStatusFilter] = useState('');
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');
    const [searchInput, setSearchInput] = useState('');
    const [search, setSearch] = useState('');
    const [page, setPage] = useState(0);
    const PAGE_SIZE = 50;

    // Warehouse options (FULFILLMENT only)
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);

    useEffect(() => {
        api.getWarehouses()
            .then(whs => setWarehouses(whs.filter(w => w.warehouse_type === 'FULFILLMENT')))
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
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
            });
            setItems(resp.items);
            setTotal(resp.total);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [warehouseId, statusFilter, search, dateFrom, dateTo, page]);

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
            } else if (newStatus === 'PENDING') {
                await api.updateAssemblyRequest(id, { status: 'PENDING' } as never);
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
            : item.pallet_weight_kg || 0;

        const newTotal = newPallets * newPalletWeight;
        updateItemLocal(item.id, { pallets_count: newPallets, total_weight_kg: newTotal });

        try {
            await api.updateAssemblyRequest(item.id, {
                pallets_count: newPallets,
                pallet_weight_kg: Number(newPalletWeight.toFixed(2)),
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
                pallet_weight_kg: Number(newPalletWeight.toFixed(2)),
            });
            setToast({ message: 'Вес обновлён', type: 'success' });
        } catch (e: unknown) {
            updateItemLocal(item.id, { total_weight_kg: oldWeight });
            setToast({ message: e instanceof Error ? e.message : 'Ошибка сохранения', type: 'error' });
        }
    };

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
                    <p className="page-subtitle">Всего: {total}</p>
                </div>
                <Link href={`/p/${slug}/warehouse/assembly/new`}>
                    <button className="btn btn-primary">Создать заявку</button>
                </Link>
            </div>

            {/* Filters */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap' }}>
                    <div className="form-group" style={{ flex: 1, minWidth: 200 }}>
                        <input
                            className="form-input"
                            placeholder="Поиск по номеру... (Enter)"
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
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>№</th>
                                <th>Статус</th>
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
                                return (
                                    <tr
                                        key={item.id}
                                        style={{ cursor: 'pointer' }}
                                        onClick={() => router.push(`/p/${slug}/warehouse/assembly/${item.id}`)}
                                    >
                                        <td style={{ fontWeight: 500 }}>{item.number}</td>
                                        <td>
                                            <StatusBadge item={item} onStatusChange={handleStatusChange} />
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
                                        <td>{formatDate(item.estimated_ready_date)}</td>
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
