'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate, formatNumber, exportToExcel } from '@/lib/utils';
import CreatePaymentRequestModal from './CreatePaymentRequestModal';
import type { PaymentRequestStatus, ShippableShipmentRow } from '@/types/api';

type SortKey =
    | 'number' | 'wb_supply_number' | 'destination' | 'source_warehouse'
    | 'pickup_date' | 'shipped_date' | 'pallets_count' | 'carrier_name' | 'pickup_cost' | 'oplata';
type SortState = { key: SortKey; dir: 'asc' | 'desc' } | null;

function sortVal(r: ShippableShipmentRow, key: SortKey): string | number {
    switch (key) {
        case 'pickup_cost': return r.pickup_cost != null ? Number(r.pickup_cost) : -Infinity;
        case 'pallets_count': return r.pallets_count ?? -Infinity;
        case 'pickup_date': return r.pickup_date ?? '';
        case 'shipped_date': return r.shipped_date ?? '';
        case 'oplata': return r.request_status ?? (r.matched_transaction_id ? 'Оплачено (авто)' : '');
        default: return (r[key] as string | null) ?? '';
    }
}

// Локальная карта статусов (без зависимости от страницы «Оплаты»).
const STATUS_MAP: Record<PaymentRequestStatus, { label: string; className: string }> = {
    DRAFT:          { label: 'Черновик',         className: 'badge-secondary' },
    PENDING_REVIEW: { label: 'На проверке',      className: 'badge-warning' },
    DRAFT_CREATED:  { label: 'Платёжка создана', className: 'badge-info' },
    PAID:           { label: 'Оплачено',         className: 'badge-success' },
    REJECTED:       { label: 'Отклонено',        className: 'badge-danger' },
    CANCELLED:      { label: 'Отменено',         className: 'badge-secondary' },
};

type Filter = 'all' | 'unpaid' | 'requested' | 'archived';

const FILTERS: { key: Filter; label: string }[] = [
    { key: 'all',       label: 'Все заборы' },
    { key: 'unpaid',    label: 'Без заявки' },
    { key: 'requested', label: 'Заявка есть' },
    { key: 'archived',  label: '🗄 Архив' },
];

type ModalState = { initialShipmentId?: number; initialShipmentIds?: number[]; editRequestId?: number } | null;

export default function ShipmentPaymentsTab() {
    const router = useRouter();
    const params = useParams();
    const slug = params.slug as string;
    const [rows, setRows] = useState<ShippableShipmentRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [search, setSearch] = useState('');
    const [filter, setFilter] = useState<Filter>('all');
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [busy, setBusy] = useState(false);
    const [modal, setModal] = useState<ModalState>(null);
    const [sort, setSort] = useState<SortState>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const archived = filter === 'archived';
            const hasReq = filter === 'unpaid' ? false : filter === 'requested' ? true : undefined;
            const data = await api.listShippable(search || undefined, hasReq, archived);
            setRows(data);
            setSelected(new Set());
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки заборов');
        }
        setLoading(false);
    }, [search, filter]);

    useEffect(() => { load(); }, [load]);

    // Debounced search
    useEffect(() => {
        const t = setTimeout(() => load(), 300);
        return () => clearTimeout(t);
    }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

    const toggle = (id: number) => {
        setSelected(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });
    };
    const allSelected = rows.length > 0 && rows.every(r => selected.has(r.outbound_shipment_id));
    const toggleAll = () => {
        setSelected(allSelected ? new Set() : new Set(rows.map(r => r.outbound_shipment_id)));
    };

    const handleSort = (key: SortKey) => setSort(prev =>
        prev?.key === key ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' });

    const sortedRows = useMemo(() => {
        if (!sort) return rows;
        const arr = [...rows];
        arr.sort((a, b) => {
            const va = sortVal(a, sort.key), vb = sortVal(b, sort.key);
            const c = (typeof va === 'number' && typeof vb === 'number')
                ? va - vb : String(va).localeCompare(String(vb), 'ru');
            return sort.dir === 'asc' ? c : -c;
        });
        return arr;
    }, [rows, sort]);

    // Bulk «одна оплата на N заборов»: только неоплаченные заборы ОДНОГО перевозчика.
    const selectedRows = rows.filter(r => selected.has(r.outbound_shipment_id));
    const bulkCarrierIds = new Set(selectedRows.map(r => r.counterparty_id));
    const canBulkPay = selectedRows.length >= 1
        && selectedRows.every(r => !r.already_requested && !r.matched_transaction_id)
        && bulkCarrierIds.size === 1 && [...bulkCarrierIds][0] != null;

    const handleArchive = async () => {
        if (selected.size === 0) return;
        setBusy(true);
        try {
            const ids = [...selected];
            if (filter === 'archived') await api.unarchiveShipments(ids);
            else await api.archiveShipments(ids);
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка архивации');
        }
        setBusy(false);
    };

    const openShipment = (row: ShippableShipmentRow) => {
        if (row.assembly_request_id != null) router.push(`/p/${slug}/warehouse/assembly/${row.assembly_request_id}`);
    };

    const handleExport = () => {
        exportToExcel(rows.map(r => ({
            '№ отгрузки': r.number,
            'ФБО поставка': r.wb_supply_number ?? '',
            'Направление': r.destination ?? '',
            'Склад забора': r.source_warehouse ?? '',
            'Дата забора': r.pickup_date ? formatDate(r.pickup_date) : '',
            'Дата отгрузки': r.shipped_date ? formatDate(r.shipped_date) : '',
            'Палет': r.pallets_count ?? '',
            'Перевозчик': r.carrier_name ?? '',
            'ИНН': r.carrier_inn ?? '',
            'Сумма': r.pickup_cost != null ? Number(r.pickup_cost) : '',
            'Оплата': r.request_status
                ? (STATUS_MAP[r.request_status]?.label ?? r.request_status)
                : r.matched_transaction_id ? 'Оплачено (авто)' : 'нет заявки',
        })), 'shipments_for_payment');
    };

    const modalEl = modal && (
        <CreatePaymentRequestModal
            key={modal.editRequestId != null ? `edit-${modal.editRequestId}` : `new-${modal.initialShipmentId ?? (modal.initialShipmentIds ?? []).join('_')}`}
            initialShipmentId={modal.initialShipmentId}
            initialShipmentIds={modal.initialShipmentIds}
            editRequestId={modal.editRequestId}
            onClose={() => { setModal(null); load(); }}
            onSuccess={() => load()}
        />
    );

    return (
        <div>
            <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                Заборы (отгрузки) — создайте заявку на оплату перевозчику на основе забора.
            </div>

            {/* Filters + bulk actions */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
                    {FILTERS.map(f => (
                        <button
                            key={f.key}
                            className={`btn btn-sm ${filter === f.key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setFilter(f.key)}
                        >
                            {f.label}
                        </button>
                    ))}
                </div>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', justifyContent: 'space-between' }}>
                    <div className="form-group" style={{ margin: 0, flex: 1, maxWidth: 320 }}>
                        <label className="form-label" style={{ fontSize: 12 }}>Поиск</label>
                        <input
                            className="form-input"
                            placeholder="Номер отгрузки, склад, перевозчик..."
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                        />
                    </div>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        {rows.length > 0 && (
                            <button className="btn btn-sm btn-secondary" onClick={toggleAll}>
                                {allSelected ? 'Снять выбор' : 'Выбрать все'}
                            </button>
                        )}
                        {filter !== 'archived' && selected.size > 0 && (
                            <button
                                className="btn btn-sm btn-primary"
                                disabled={!canBulkPay || busy}
                                title={canBulkPay
                                    ? 'Создать ОДНУ заявку на оплату за выбранные заборы (один счёт)'
                                    : 'Выберите неоплаченные заборы ОДНОГО перевозчика'}
                                onClick={() => canBulkPay && setModal({ initialShipmentIds: [...selected] })}
                            >
                                💳 Создать оплату ({selected.size})
                            </button>
                        )}
                        <button
                            className={`btn btn-sm ${filter === 'archived' ? 'btn-secondary' : 'btn-danger'}`}
                            onClick={handleArchive}
                            disabled={selected.size === 0 || busy}
                        >
                            {filter === 'archived' ? `Из архива (${selected.size})` : `🗄 В архив (${selected.size})`}
                        </button>
                        <button className="btn btn-sm btn-secondary" onClick={handleExport} disabled={rows.length === 0}>📥 Excel</button>
                    </div>
                </div>
            </div>

            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)' }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={load}>Повторить</button>
                </div>
            )}

            {loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
            ) : rows.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>🚚</div>
                    <p>{filter === 'archived' ? 'Архив пуст.' : 'Нет заборов для оплаты.'}</p>
                </div>
            ) : (
                <div className="glass-card">
                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>
                        Заборов: {rows.length}{selected.size > 0 ? ` · выбрано ${selected.size}` : ''}
                    </h3>
                    <div style={{ overflowX: 'auto' }}>
                        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                            <thead>
                                <tr style={{ borderBottom: '1px solid var(--color-border)', textAlign: 'left' }}>
                                    <th style={{ padding: '8px 8px', width: 32 }}>
                                        <input type="checkbox" checked={allSelected} onChange={toggleAll} style={{ cursor: 'pointer' }} title="Выбрать все" />
                                    </th>
                                    <Th sortKey="number" sort={sort} onSort={handleSort}>№ отгрузки</Th>
                                    <Th sortKey="wb_supply_number" sort={sort} onSort={handleSort}>ФБО поставка</Th>
                                    <Th sortKey="destination" sort={sort} onSort={handleSort}>Направление</Th>
                                    <Th sortKey="source_warehouse" sort={sort} onSort={handleSort}>Склад забора</Th>
                                    <Th sortKey="pickup_date" sort={sort} onSort={handleSort}>Дата забора</Th>
                                    <Th sortKey="shipped_date" sort={sort} onSort={handleSort}>Дата отгрузки</Th>
                                    <Th right sortKey="pallets_count" sort={sort} onSort={handleSort}>Палет</Th>
                                    <Th sortKey="carrier_name" sort={sort} onSort={handleSort}>Перевозчик</Th>
                                    <Th right sortKey="pickup_cost" sort={sort} onSort={handleSort}>Сумма</Th>
                                    <Th right sortKey="oplata" sort={sort} onSort={handleSort}>Оплата</Th>
                                </tr>
                            </thead>
                            <tbody>
                                {sortedRows.map(r => {
                                    const sel = selected.has(r.outbound_shipment_id);
                                    return (
                                        <tr key={r.outbound_shipment_id} style={{ borderBottom: '1px solid var(--color-border)', background: sel ? 'rgba(0,122,255,0.06)' : undefined }}>
                                            <td style={{ padding: '8px 8px' }}>
                                                <input type="checkbox" checked={sel} onChange={() => toggle(r.outbound_shipment_id)} style={{ cursor: 'pointer' }} />
                                            </td>
                                            <td style={{ padding: '8px 8px' }}>
                                                <button onClick={() => openShipment(r)} title="Открыть заявку" style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--color-accent)', fontWeight: 600, fontSize: 13 }}>
                                                    {r.number} ↗
                                                </button>
                                            </td>
                                            <td style={{ padding: '8px 8px', fontFamily: 'monospace', fontSize: 12 }}>{r.wb_supply_number ?? '—'}</td>
                                            <td style={{ padding: '8px 8px', color: 'var(--color-text-muted)' }}>{r.destination ?? '—'}</td>
                                            <td style={{ padding: '8px 8px', color: 'var(--color-text-muted)' }}>{r.source_warehouse ?? '—'}</td>
                                            <td style={{ padding: '8px 8px', whiteSpace: 'nowrap' }}>{r.pickup_date ? formatDate(r.pickup_date) : '—'}</td>
                                            <td style={{ padding: '8px 8px', whiteSpace: 'nowrap' }}>{r.shipped_date ? formatDate(r.shipped_date) : '—'}</td>
                                            <td style={{ padding: '8px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{r.pallets_count ?? '—'}</td>
                                            <td style={{ padding: '8px 8px' }}>
                                                <div>{r.carrier_name ?? '—'}</div>
                                                {r.carrier_inn && <div style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--color-text-muted)' }}>{r.carrier_inn}</div>}
                                            </td>
                                            <td style={{ padding: '8px 8px', textAlign: 'right', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
                                                {r.pickup_cost != null ? `${formatNumber(Number(r.pickup_cost), 2)} ₽` : '—'}
                                            </td>
                                            <td style={{ padding: '8px 8px', textAlign: 'right' }}>
                                                {r.request_id && r.request_status ? (
                                                    <button
                                                        className={`badge ${STATUS_MAP[r.request_status]?.className ?? ''}`}
                                                        onClick={() => setModal({ editRequestId: r.request_id! })}
                                                        style={{ border: 'none', cursor: 'pointer' }}
                                                        title={r.request_status === 'DRAFT' ? 'Открыть и отредактировать черновик' : 'Открыть заявку'}
                                                    >
                                                        {STATUS_MAP[r.request_status]?.label ?? r.request_status} ↗
                                                    </button>
                                                ) : r.matched_transaction_id ? (
                                                    <span
                                                        className="badge badge-success"
                                                        title={`Авто-связка с выпиской${r.matched_txn_date ? ` от ${formatDate(r.matched_txn_date)}` : ''} по ИНН + сумме`}
                                                    >
                                                        ✅ Оплачено (авто)
                                                    </span>
                                                ) : (
                                                    <button className="btn btn-sm btn-primary" onClick={() => setModal({ initialShipmentId: r.outbound_shipment_id })}>
                                                        💳 Создать оплату
                                                    </button>
                                                )}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {modalEl}
        </div>
    );
}

function Th({ children, right, sortKey, sort, onSort }: {
    children: React.ReactNode; right?: boolean;
    sortKey?: SortKey; sort?: SortState; onSort?: (k: SortKey) => void;
}) {
    const active = sortKey != null && sort?.key === sortKey;
    return (
        <th
            onClick={sortKey && onSort ? () => onSort(sortKey) : undefined}
            style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: active ? 'var(--color-accent)' : 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', textAlign: right ? 'right' : 'left', whiteSpace: 'nowrap', cursor: sortKey ? 'pointer' : undefined, userSelect: 'none' }}
        >
            {children}{active ? (sort!.dir === 'asc' ? ' ▲' : ' ▼') : ''}
        </th>
    );
}
