'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';

import { api } from '@/lib/api';
import { formatDate, formatDateTime } from '@/lib/utils';
import type { WbFboSupply } from '@/types/api';

const STATUS_LABEL: Record<string, string> = {
    ACTIVE: 'Запланирована',
    ON_DELIVERY: 'В пути',
    IN_PROGRESS: 'Разгрузка разрешена',
    ACCEPTED: 'Принята',
    CANCELLED: 'Отменена',
};

export default function FboArchivePage() {
    const { slug } = useParams() as { slug: string };

    const [supplies, setSupplies] = useState<WbFboSupply[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const [search, setSearch] = useState('');
    const [searchInput, setSearchInput] = useState('');
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');
    const [page, setPage] = useState(0);
    const PAGE_SIZE = 50;

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const resp = await api.getFboSupplies({
                archived_view: true,
                search: search || undefined,
                date_from: dateFrom || undefined,
                date_to: dateTo || undefined,
                sort_by: 'created_at_wb',
                sort_order: 'desc',
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
            });
            setSupplies(resp.items);
            setTotal(resp.total);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [search, dateFrom, dateTo, page]);

    useEffect(() => { load(); }, [load]);

    const handleRestore = async (supplyId: number) => {
        try {
            await api.setFboSupplyArchive(supplyId, false);
            setSupplies(prev => prev.filter(s => s.id !== supplyId));
            setTotal(prev => Math.max(0, prev - 1));
        } catch (e) {
            alert('Не удалось восстановить: ' + (e instanceof Error ? e.message : 'ошибка'));
        }
    };

    const handleResetToAuto = async (supplyId: number) => {
        try {
            await api.setFboSupplyArchive(supplyId, null);
            setSupplies(prev => prev.filter(s => s.id !== supplyId));
            setTotal(prev => Math.max(0, prev - 1));
        } catch (e) {
            alert('Не удалось сбросить флаг: ' + (e instanceof Error ? e.message : 'ошибка'));
        }
    };

    const totalPages = Math.ceil(total / PAGE_SIZE);

    return (
        <div className="animate-in" style={{ padding: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                <div>
                    <h1 style={{ fontSize: 28, fontWeight: 700, letterSpacing: '-0.03em' }}>Архив поставок FBO</h1>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>
                        Всего: {total}
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button
                        className="btn btn-primary"
                        onClick={async () => {
                            if (!confirm('Восстановить все поставки, привязанные к нашим заявкам или отгрузкам?')) return;
                            try {
                                const r = await api.restoreLinkedFromArchive();
                                alert(`Восстановлено: ${r.restored}`);
                                load();
                            } catch (e) {
                                alert('Ошибка: ' + (e instanceof Error ? e.message : 'неизвестно'));
                            }
                        }}
                        title="Вернуть в актуальный список все поставки, которые были привязаны к нашим сборкам/отгрузкам"
                    >
                        ♻️ Восстановить связанные
                    </button>
                    <Link href={`/p/${slug}/warehouse/fbo-supplies`} className="btn btn-secondary">
                        ← К актуальным
                    </Link>
                </div>
            </div>

            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                    <input
                        type="text"
                        className="form-input"
                        placeholder="Поиск по номеру, ID..."
                        value={searchInput}
                        onChange={e => setSearchInput(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') { setSearch(searchInput); setPage(0); } }}
                        style={{ flex: '1 1 240px', minWidth: 200 }}
                    />
                    <input
                        type="date"
                        className="form-input"
                        value={dateFrom}
                        onChange={e => { setDateFrom(e.target.value); setPage(0); }}
                        title="Создана с"
                    />
                    <input
                        type="date"
                        className="form-input"
                        value={dateTo}
                        onChange={e => { setDateTo(e.target.value); setPage(0); }}
                        title="Создана по"
                    />
                </div>
            </div>

            {loading && <div className="glass-card" style={{ padding: 24, textAlign: 'center' }}>Загрузка...</div>}
            {error && <div className="glass-card" style={{ padding: 24, color: 'var(--color-danger)' }}>{error}</div>}
            {!loading && !error && supplies.length === 0 && (
                <div className="glass-card" style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Архив пуст
                </div>
            )}
            {!loading && !error && supplies.length > 0 && (
                <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                                <th style={{ padding: '12px 16px', textAlign: 'left', fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-muted)' }}>Поставка</th>
                                <th style={{ padding: '12px 16px', textAlign: 'left', fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-muted)' }}>Статус</th>
                                <th style={{ padding: '12px 16px', textAlign: 'left', fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-muted)' }}>Склад</th>
                                <th style={{ padding: '12px 16px', textAlign: 'right', fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-muted)' }}>Кол-во</th>
                                <th style={{ padding: '12px 16px', textAlign: 'left', fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-muted)' }}>Заявка / Отгрузка</th>
                                <th style={{ padding: '12px 16px', textAlign: 'left', fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-muted)' }}>Создана</th>
                                <th style={{ padding: '12px 16px', textAlign: 'left', fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-muted)' }}>Тип</th>
                                <th style={{ padding: '12px 16px', textAlign: 'right', fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-muted)' }}>Действие</th>
                            </tr>
                        </thead>
                        <tbody>
                            {supplies.map(s => (
                                <tr key={s.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                    <td style={{ padding: '12px 16px', fontSize: 14 }}>
                                        <div style={{ fontWeight: 600 }}>{s.wb_supply_id}</div>
                                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                            {s.cargo_type || ''}{s.cargo_type ? ' · ' : ''}{s.name || ''}
                                        </div>
                                    </td>
                                    <td style={{ padding: '12px 16px', fontSize: 14 }}>
                                        <span className="badge badge-secondary">
                                            {STATUS_LABEL[s.wb_status] || s.wb_status}
                                        </span>
                                    </td>
                                    <td style={{ padding: '12px 16px', fontSize: 14 }}>
                                        {s.warehouse_name || '—'}
                                    </td>
                                    <td style={{ padding: '12px 16px', fontSize: 14, textAlign: 'right' }}>
                                        {s.total_qty || 0}
                                    </td>
                                    <td style={{ padding: '12px 16px', fontSize: 14 }}>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' }}>
                                            {s.assembly_request_id && (
                                                <Link href={`/p/${slug}/warehouse/assembly/${s.assembly_request_id}`}>
                                                    <span className={`badge ${
                                                        s.assembly_request_status === 'DELIVERED' ? 'badge-success' :
                                                        s.assembly_request_status === 'SHIPPED' ? 'badge-success' :
                                                        s.assembly_request_status === 'VEHICLE_ASSIGNED' ? 'badge-info' :
                                                        s.assembly_request_status === 'READY' ? 'badge-warning' :
                                                        s.assembly_request_status === 'CANCELLED' ? 'badge-danger' :
                                                        'badge-secondary'
                                                    }`} style={{ cursor: 'pointer' }}>
                                                        {s.assembly_request_number}
                                                    </span>
                                                </Link>
                                            )}
                                            {s.outbound_shipment_id && s.outbound_shipment_warehouse_id && (
                                                <Link href={`/p/${slug}/warehouse/${s.outbound_shipment_warehouse_id}/shipment/${s.outbound_shipment_id}`}>
                                                    <span className={`badge ${
                                                        s.outbound_shipment_status === 'DELIVERED' ? 'badge-success' :
                                                        s.outbound_shipment_status === 'SHIPPED' ? 'badge-info' :
                                                        s.outbound_shipment_status === 'CANCELLED' ? 'badge-danger' :
                                                        'badge-secondary'
                                                    }`} style={{ cursor: 'pointer' }}>
                                                        {s.outbound_shipment_number || 'Отгружена'}
                                                    </span>
                                                </Link>
                                            )}
                                            {!s.assembly_request_id && !s.outbound_shipment_id && (
                                                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>—</span>
                                            )}
                                        </div>
                                    </td>
                                    <td style={{ padding: '12px 16px', fontSize: 13, color: 'var(--color-text-muted)' }}>
                                        {formatDateTime(s.created_at_wb)}
                                    </td>
                                    <td style={{ padding: '12px 16px', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                        {s.is_archived === true ? 'вручную' : 'авто (по дате)'}
                                    </td>
                                    <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                                        <button
                                            className="btn btn-primary btn-sm"
                                            onClick={() => handleRestore(s.id)}
                                            title="Вернуть в актуальный список (отметить как FALSE)"
                                        >
                                            ♻️ Восстановить
                                        </button>
                                        {s.is_archived === true && (
                                            <button
                                                className="btn btn-secondary btn-sm"
                                                onClick={() => handleResetToAuto(s.id)}
                                                title="Сбросить флаг: вернётся к авто-правилу по дате"
                                                style={{ marginLeft: 6 }}
                                            >
                                                Сбросить флаг
                                            </button>
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {totalPages > 1 && (
                <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 16 }}>
                    <button className="btn btn-secondary btn-sm" disabled={page === 0} onClick={() => setPage(p => p - 1)}>← Назад</button>
                    <span style={{ alignSelf: 'center', fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Стр. {page + 1} из {totalPages}
                    </span>
                    <button className="btn btn-secondary btn-sm" disabled={page + 1 >= totalPages} onClick={() => setPage(p => p + 1)}>Вперёд →</button>
                </div>
            )}

            {/* Подсказка */}
            <div style={{ marginTop: 16, fontSize: 12, color: 'var(--color-text-muted)', textAlign: 'center' }}>
                Поставки в архиве: либо отправлены вручную, либо созданы до даты начала учёта проекта.
            </div>
        </div>
    );
}
