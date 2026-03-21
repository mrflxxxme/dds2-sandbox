'use client';
import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
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

const STATUS_OPTIONS: { value: string; label: string }[] = [
    { value: '', label: 'Все статусы' },
    { value: 'PENDING', label: 'Ожидает сборку' },
    { value: 'IN_PROGRESS', label: 'В сборке' },
    { value: 'READY', label: 'Готово' },
    { value: 'VEHICLE_ASSIGNED', label: 'Машина назначена' },
    { value: 'SHIPPED', label: 'Отгружена' },
    { value: 'DELIVERED', label: 'Принята WB' },
    { value: 'CANCELLED', label: 'Отменена' },
];

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

    // ─── Render ───────────────────────────────────────────────────────────

    return (
        <div className="animate-in">
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
                            {STATUS_OPTIONS.map(o => (
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
                                <th style={{ textAlign: 'right' }}>Палеты</th>
                                <th style={{ textAlign: 'right' }}>Общий вес</th>
                                <th>Дата готовности</th>
                                <th>Создана</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map(item => {
                                const status = STATUS_MAP[item.status] || { label: item.status, className: '' };
                                return (
                                    <tr
                                        key={item.id}
                                        style={{ cursor: 'pointer' }}
                                        onClick={() => router.push(`/p/${slug}/warehouse/assembly/${item.id}`)}
                                    >
                                        <td style={{ fontWeight: 500 }}>{item.number}</td>
                                        <td>
                                            <span
                                                className={`badge ${status.className}`}
                                                style={item.status === 'SHIPPED' ? { opacity: 0.6 } : undefined}
                                            >
                                                {status.label}
                                            </span>
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
                                        <td style={{ textAlign: 'right' }}>{item.pallets_count}</td>
                                        <td style={{ textAlign: 'right' }}>
                                            {item.total_weight_kg ? formatNumber(item.total_weight_kg, 1) + ' кг' : '\u2014'}
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
