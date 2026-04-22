'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Loan, LoanDirection, LoanStatus, LoanListResponse, LoanCreate, CounterpartyListItem } from '@/types/api';

const DIRECTION_LABELS: Record<LoanDirection, string> = {
    INCOMING: 'Получен',
    OUTGOING: 'Выдан',
    AFFILIATED: 'Аффилированный',
};

const STATUS_LABELS: Record<LoanStatus, string> = {
    ACTIVE: 'Активный',
    CLOSED: 'Закрыт',
    DEFAULTED: 'Дефолт',
};

const TODAY = new Date().toISOString().slice(0, 10);

const emptyLoanForm = (): LoanCreate => ({
    counterparty_id: 0,
    direction: 'INCOMING',
    principal: 0,
    currency: 'RUB',
    rate: null,
    contract_number: '',
    contract_date: TODAY,
    start_date: TODAY,
    maturity_date: null,
    status: 'ACTIVE',
    notes: null,
});

export default function LoansPage() {
    const params = useParams();
    const slug = params.slug as string;
    const router = useRouter();

    const [data, setData] = useState<LoanListResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Filters
    const [dirFilter, setDirFilter] = useState<LoanDirection | ''>('');
    const [statusFilter, setStatusFilter] = useState<LoanStatus | ''>('ACTIVE');
    const [cpFilter, setCpFilter] = useState<number | null>(null);
    const [cpSearch, setCpSearch] = useState('');
    const [counterparties, setCounterparties] = useState<CounterpartyListItem[]>([]);

    // Create form
    const [showCreate, setShowCreate] = useState(false);
    const [form, setForm] = useState<LoanCreate>(emptyLoanForm());
    const [saving, setSaving] = useState(false);
    const [formError, setFormError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const res = await api.listLoans({
                direction: dirFilter || undefined,
                status: statusFilter || undefined,
                counterparty_id: cpFilter ?? undefined,
            });
            setData(res);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [dirFilter, statusFilter, cpFilter]);

    useEffect(() => { load(); }, [load]);

    useEffect(() => {
        api.listCounterparties({ limit: 200 })
            .then(r => setCounterparties(r.items))
            .catch(() => {});
    }, []);

    const filteredCps = counterparties.filter(cp =>
        !cpSearch || cp.name.toLowerCase().includes(cpSearch.toLowerCase()) || (cp.inn ?? '').includes(cpSearch)
    );

    const handleCreate = async () => {
        if (!form.contract_number.trim()) { setFormError('Введите номер договора'); return; }
        if (!form.counterparty_id) { setFormError('Выберите контрагента'); return; }
        if (!form.principal || form.principal <= 0) { setFormError('Введите сумму займа'); return; }
        setSaving(true);
        setFormError('');
        try {
            await api.createLoan({
                ...form,
                rate: form.rate ?? null,
                maturity_date: form.maturity_date || null,
            });
            setShowCreate(false);
            setForm(emptyLoanForm());
            await load();
        } catch (e: unknown) {
            setFormError(e instanceof Error ? e.message : 'Ошибка создания займа');
        } finally {
            setSaving(false);
        }
    };

    const columns = [
        {
            key: 'id', label: 'КА',
            render: (_v: number, row: Loan) => {
                const cp = counterparties.find(c => c.id === row.counterparty_id);
                return (
                    <button
                        className="btn btn-secondary btn-sm"
                        style={{ background: 'none', border: 'none', padding: 0, fontWeight: 500, cursor: 'pointer', textAlign: 'left' }}
                        onClick={() => router.push(`/p/${slug}/refs/loans/${row.id}`)}
                    >
                        {cp?.name ?? `#${row.counterparty_id}`}
                    </button>
                );
            },
        },
        {
            key: 'direction', label: 'Направление',
            render: (v: LoanDirection) => (
                <span className={`badge ${v === 'INCOMING' ? 'badge-info' : v === 'OUTGOING' ? 'badge-warning' : 'badge-secondary'}`}>
                    {DIRECTION_LABELS[v]}
                </span>
            ),
        },
        {
            key: 'principal', label: 'Сумма',
            render: (v: number, row: Loan) => `${formatNumber(v)} ${row.currency}`,
        },
        {
            key: 'rate', label: 'Ставка %',
            render: (v: number | null) => v != null ? `${(v * 100).toFixed(2)}%` : '—',
        },
        {
            key: 'contract_number', label: 'Договор',
            render: (v: string) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span>,
        },
        {
            key: 'start_date', label: 'С',
            render: (v: string) => formatDate(v),
        },
        {
            key: 'maturity_date', label: 'По',
            render: (v: string | null) => v ? formatDate(v) : '—',
        },
        {
            key: 'status', label: 'Статус',
            render: (v: LoanStatus) => (
                <span className={`badge ${v === 'ACTIVE' ? 'badge-success' : v === 'CLOSED' ? 'badge-secondary' : 'badge-danger'}`}>
                    {STATUS_LABELS[v]}
                </span>
            ),
        },
        {
            key: '_actions', label: '',
            render: (_v: unknown, row: Loan) => (
                <button
                    className="btn btn-secondary btn-sm"
                    style={{ padding: '2px 8px', fontSize: 11 }}
                    onClick={() => router.push(`/p/${slug}/refs/loans/${row.id}`)}
                >
                    →
                </button>
            ),
            sortable: false,
        },
    ];

    const items = data?.items ?? [];

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Займы</h1>
                    <p className="page-subtitle">Управление займами проекта</p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => exportToExcel(items, 'loans')}
                        disabled={items.length === 0}
                    >
                        Excel
                    </button>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>
                        + Добавить займ
                    </button>
                </div>
            </div>

            {/* Totals footer */}
            {data && (
                <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                    {(Object.entries(data.totals_by_direction) as [LoanDirection, typeof data.totals_by_direction.INCOMING][]).map(([dir, t]) => (
                        t.count > 0 && (
                            <div key={dir} className="glass-card" style={{ padding: '10px 16px', flex: '1 1 160px' }}>
                                <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>{DIRECTION_LABELS[dir]}</div>
                                <div style={{ fontSize: 15, fontWeight: 700 }}>
                                    {t.count} займ{t.count > 1 ? 'а' : ''}
                                </div>
                                {t.sum_rub > 0 && <div style={{ fontSize: 12 }}>₽ {formatNumber(t.sum_rub)}</div>}
                                {t.sum_cny > 0 && <div style={{ fontSize: 12 }}>¥ {formatNumber(t.sum_cny)}</div>}
                            </div>
                        )
                    ))}
                </div>
            )}

            {/* Filters */}
            <div className="glass-card" style={{ marginBottom: 16, padding: '12px 16px' }}>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                    <select
                        className="form-input"
                        style={{ width: 180 }}
                        value={dirFilter}
                        onChange={e => setDirFilter(e.target.value as LoanDirection | '')}
                    >
                        <option value="">Все направления</option>
                        <option value="INCOMING">Получен</option>
                        <option value="OUTGOING">Выдан</option>
                        <option value="AFFILIATED">Аффилированный</option>
                    </select>
                    <select
                        className="form-input"
                        style={{ width: 160 }}
                        value={statusFilter}
                        onChange={e => setStatusFilter(e.target.value as LoanStatus | '')}
                    >
                        <option value="">Все статусы</option>
                        <option value="ACTIVE">Активные</option>
                        <option value="CLOSED">Закрытые</option>
                        <option value="DEFAULTED">Дефолт</option>
                    </select>
                    <select
                        className="form-input"
                        style={{ width: 200 }}
                        value={cpFilter ?? ''}
                        onChange={e => setCpFilter(e.target.value ? Number(e.target.value) : null)}
                    >
                        <option value="">Все контрагенты</option>
                        {counterparties.map(cp => (
                            <option key={cp.id} value={cp.id}>{cp.name}</option>
                        ))}
                    </select>
                </div>
            </div>

            {/* Create form */}
            {showCreate && (
                <div className="glass-card" style={{ marginBottom: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                        <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Новый займ</h3>
                        <button className="btn btn-secondary btn-sm" onClick={() => { setShowCreate(false); setForm(emptyLoanForm()); setFormError(''); }}>✕</button>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                        <div className="form-group">
                            <label className="form-label">Контрагент *</label>
                            <select className="form-input" value={form.counterparty_id || ''} onChange={e => setForm(f => ({ ...f, counterparty_id: Number(e.target.value) }))}>
                                <option value="">— Выберите —</option>
                                {counterparties.map(cp => (
                                    <option key={cp.id} value={cp.id}>{cp.name}</option>
                                ))}
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label">Направление</label>
                            <select className="form-input" value={form.direction} onChange={e => setForm(f => ({ ...f, direction: e.target.value as LoanDirection }))}>
                                <option value="INCOMING">Получен</option>
                                <option value="OUTGOING">Выдан</option>
                                <option value="AFFILIATED">Аффилированный</option>
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label">Сумма *</label>
                            <input type="number" className="form-input" value={form.principal || ''} onChange={e => setForm(f => ({ ...f, principal: parseFloat(e.target.value) || 0 }))} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Валюта</label>
                            <select className="form-input" value={form.currency} onChange={e => setForm(f => ({ ...f, currency: e.target.value }))}>
                                <option value="RUB">RUB</option>
                                <option value="CNY">CNY</option>
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label">Ставка % год.</label>
                            <input type="number" step="0.01" className="form-input" value={form.rate != null ? (form.rate * 100).toFixed(2) : ''} onChange={e => setForm(f => ({ ...f, rate: e.target.value ? parseFloat(e.target.value) / 100 : null }))} placeholder="18.50" />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Номер договора *</label>
                            <input className="form-input" value={form.contract_number} onChange={e => setForm(f => ({ ...f, contract_number: e.target.value }))} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Дата договора</label>
                            <input type="date" className="form-input" value={form.contract_date} onChange={e => setForm(f => ({ ...f, contract_date: e.target.value }))} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Дата начала</label>
                            <input type="date" className="form-input" value={form.start_date} onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Дата погашения</label>
                            <input type="date" className="form-input" value={form.maturity_date ?? ''} onChange={e => setForm(f => ({ ...f, maturity_date: e.target.value || null }))} />
                        </div>
                    </div>
                    {formError && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 8 }}>{formError}</div>}
                    <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
                        <button className="btn btn-primary btn-sm" onClick={handleCreate} disabled={saving}>{saving ? 'Сохранение...' : 'Сохранить'}</button>
                        <button className="btn btn-secondary btn-sm" onClick={() => { setShowCreate(false); setForm(emptyLoanForm()); setFormError(''); }}>Отмена</button>
                    </div>
                </div>
            )}

            {/* Error */}
            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between' }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
                </div>
            )}

            {/* Loading */}
            {loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка...</div>
            ) : items.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>💰</div>
                    <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Займов нет</div>
                    <div style={{ color: 'var(--color-text-dim)', marginBottom: 20 }}>
                        {dirFilter || statusFilter || cpFilter
                            ? 'По заданным фильтрам ничего не найдено.'
                            : 'Создайте займ для отслеживания кредитной нагрузки.'}
                    </div>
                    <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
                        + Создать займ
                    </button>
                </div>
            ) : (
                <TanStackDataTable
                    columns={columns}
                    data={items}
                    emptyText="Нет займов"
                    enableSorting
                    enablePagination
                />
            )}
        </div>
    );
}
