'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import { usePermissions } from '@/lib/hooks/usePermissions';
import type { Warehouse, StockTransfer, CounterpartyListItem } from '@/types/api';
import type { Column } from '@/components/DataTable';

export default function WarehousePage() {
    const params = useParams();
    const slug = params.slug as string;
    const router = useRouter();
    const { canEdit } = usePermissions();

    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [transfers, setTransfers] = useState<StockTransfer[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [showCreate, setShowCreate] = useState(false);

    // Create form state
    const [formName, setFormName] = useState('');
    const [formType, setFormType] = useState<'FULFILLMENT' | 'EXTERNAL'>('FULFILLMENT');
    const [formCountry, setFormCountry] = useState('');
    const [formAddress, setFormAddress] = useState('');
    const [formDays, setFormDays] = useState('');
    const [formCounterpartyId, setFormCounterpartyId] = useState<number | null>(null);
    const [counterparties, setCounterparties] = useState<CounterpartyListItem[]>([]);
    const [cpSearch, setCpSearch] = useState('');
    const [saving, setSaving] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [wh, tr] = await Promise.all([
                api.getWarehouses(),
                api.getTransfers(true),
            ]);
            setWarehouses(wh);
            setTransfers(tr);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    useEffect(() => {
        // Load counterparty list for the dropdown (non-blocking for the page)
        api.listCounterparties({ limit: 500, active_only: true })
            .then(res => setCounterparties(res.items))
            .catch(() => { /* ignore — dropdown will be empty */ });
    }, []);

    const handleCreate = async () => {
        if (!formName.trim()) return;
        setSaving(true);
        try {
            await api.createWarehouse({
                name: formName.trim(),
                warehouse_type: formType,
                country: formCountry.trim() || undefined,
                address: formAddress.trim() || undefined,
                assembly_days: formDays ? parseInt(formDays) : undefined,
                counterparty_id: formCounterpartyId ?? null,
            });
            setShowCreate(false);
            setFormName('');
            setFormCountry('');
            setFormAddress('');
            setFormDays('');
            setFormCounterpartyId(null);
            setCpSearch('');
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setSaving(false);
    };

    const handleDelete = async (id: number) => {
        if (!confirm('Удалить склад?')) return;
        try {
            await api.deleteWarehouse(id);
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
    };

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    if (error) return <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>  {error}</div>;

    const warehouseCols: Column[] = [
        { key: 'name', label: 'Название' },
        {
            key: 'warehouse_type', label: 'Тип',
            render: (v: string) => (
                <span className={`badge ${v === 'FULFILLMENT' ? 'badge-info' : ''}`}>
                    {v === 'FULFILLMENT' ? 'Фулфилмент' : 'Внешний'}
                </span>
            ),
        },
        { key: 'country', label: 'Страна' },
        { key: 'assembly_days', label: 'Дни сборки', align: 'right' as const },
        {
            key: 'total_stock', label: 'Остатки', align: 'right' as const,
            render: (v: number) => v ? formatNumber(v, 0) : '—',
        },
        {
            key: 'vehicles_in_transit', label: 'В пути', align: 'center' as const,
            render: (v: number) => v > 0 ? (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 10px', borderRadius: 12, background: 'rgba(59,130,246,0.08)', color: 'var(--color-primary)', fontSize: 12, fontWeight: 600 }}>
                    {v} маш.
                </span>
            ) : null,
        },
        {
            key: 'is_active', label: 'Статус',
            render: (v: boolean) => v ? 'Активен' : 'Архив',
        },
        {
            key: 'actions', label: '',
            render: (_: unknown, row: Warehouse) => (
                <button className="btn btn-danger btn-sm" onClick={(e) => { e.stopPropagation(); handleDelete(row.id); }}>
                    Удалить
                </button>
            ),
        },
    ];

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Склады</h1>
                    <p className="page-subtitle">Управление складами, приёмка, отгрузка, перемещение</p>
                </div>
                {canEdit() && <button className="btn btn-primary" onClick={() => setShowCreate(true)}>+ Создать склад</button>}
            </div>

            {/* В пути */}
            {transfers.length > 0 && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 20 }}>
                    <h3 style={{ margin: '0 0 12px', fontSize: 15 }}>В пути ({transfers.length})</h3>
                    {transfers.map(t => (
                        <div key={t.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--color-border)' }}>
                            <span><strong>{t.number}</strong> — {t.items?.length || 0} поз.</span>
                            <span style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                                {t.created_at ? formatDate(t.created_at) : ''}
                            </span>
                        </div>
                    ))}
                </div>
            )}

            {/* Таблица складов */}
            <TanStackDataTable
                columns={warehouseCols}
                data={warehouses}
                emptyText="Нет складов"
                emptyIcon="🏢"
                enableSorting
                enablePagination={false}
                onRowClick={(row) => router.push(`/p/${slug}/warehouse/${row.id}`)}
            />

            {/* Модалка создания */}
            {showCreate && (
                <div className="modal-overlay" onClick={() => setShowCreate(false)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 480 }}>
                        <h2 className="modal-title">Создать склад</h2>
                        <div className="form-group">
                            <label className="form-label">Название *</label>
                            <input className="form-input" value={formName} onChange={e => setFormName(e.target.value)} placeholder="Основной склад" autoFocus />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Тип</label>
                            <select className="form-input" value={formType} onChange={e => setFormType(e.target.value as 'FULFILLMENT' | 'EXTERNAL')}>
                                <option value="FULFILLMENT">Фулфилмент (отгрузка на WB)</option>
                                <option value="EXTERNAL">Внешний (фабрика, ТК, транзит)</option>
                            </select>
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                            <div className="form-group">
                                <label className="form-label">Страна</label>
                                <input className="form-input" value={formCountry} onChange={e => setFormCountry(e.target.value)} placeholder="Россия" />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Дни на сборку</label>
                                <input className="form-input" type="number" value={formDays} onChange={e => setFormDays(e.target.value)} placeholder="3" />
                            </div>
                        </div>
                        <div className="form-group">
                            <label className="form-label">Адрес</label>
                            <input className="form-input" value={formAddress} onChange={e => setFormAddress(e.target.value)} placeholder="г. Москва, ул. ..." />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Контрагент</label>
                            <input
                                className="form-input"
                                placeholder="Поиск по имени или ИНН..."
                                value={cpSearch}
                                onChange={e => setCpSearch(e.target.value)}
                                style={{ marginBottom: 6 }}
                            />
                            <select
                                className="form-input"
                                value={formCounterpartyId ?? ''}
                                onChange={e => setFormCounterpartyId(e.target.value ? Number(e.target.value) : null)}
                            >
                                <option value="">— Не привязан —</option>
                                {counterparties
                                    .filter(cp =>
                                        !cpSearch.trim() ||
                                        cp.name.toLowerCase().includes(cpSearch.toLowerCase()) ||
                                        (cp.inn ?? '').includes(cpSearch)
                                    )
                                    .slice(0, 200)
                                    .map(cp => (
                                        <option key={cp.id} value={cp.id}>
                                            {cp.name}{cp.inn ? ` · ${cp.inn}` : ''}
                                        </option>
                                    ))}
                            </select>
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={() => setShowCreate(false)}>Отмена</button>
                            <button className="btn btn-primary" onClick={handleCreate} disabled={saving || !formName.trim()}>
                                {saving ? 'Сохранение...' : 'Создать'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
