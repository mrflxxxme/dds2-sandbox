'use client';
import { useEffect, useState, useMemo } from 'react';
import { api } from '@/lib/api';
import { exportToExcel } from '@/lib/utils';
import { usePermissions } from '@/lib/hooks/usePermissions';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

export default function RefsPage() {
    const [tab, setTab] = useState<'accounts' | 'overrides' | 'balances' | 'categories'>('accounts');

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">📋 Справочники</h1>
                    <p className="page-subtitle">Счета, переопределения, начальные остатки, справочник категорий</p>
                </div>
            </div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 20, flexWrap: 'wrap' }}>
                {[
                    { key: 'accounts' as const, label: 'Счета' },
                    { key: 'overrides' as const, label: 'Переопределения' },
                    { key: 'balances' as const, label: 'Начальные остатки' },
                    { key: 'categories' as const, label: '📂 Справочник категорий' },
                ].map(t => (
                    <button key={t.key} className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                        onClick={() => setTab(t.key)}>{t.label}</button>
                ))}
            </div>
            {tab === 'accounts' && <AccountsTab />}
            {tab === 'overrides' && <OverridesTab />}
            {tab === 'balances' && <BalancesTab />}
            {tab === 'categories' && <CategoriesTab />}
        </div>
    );
}

/* ─── Accounts ─── */
function AccountsTab() {
    const [data, setData] = useState<any[]>([]);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ account: '', bank: 'VTB', currency: 'RUB', account_type: 'OPER', account_name: '', is_our_account: true, is_customs_payee: false });
    const [msg, setMsg] = useState('');
    const { canEdit } = usePermissions();

    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getAccounts()); } catch { } };

    const save = async () => {
        try { await api.upsertAccount(form); setMsg('✅ Сохранено!'); setShowForm(false); load(); } catch (e: any) { setMsg(e.message); }
    };
    const del = async (id: number) => {
        if (!confirm('Удалить?')) return;
        try { await api.deleteAccount(id); load(); } catch (e: any) { setMsg(e.message); }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Счета (REF_ACCOUNTS)</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'accounts')}>📥 Excel</button>
                    {canEdit() && <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Добавить</button>}
                </div>
            </div>
            {msg && <div style={{ color: 'var(--color-success)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}

            {showForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                    <div className="form-group"><label className="form-label">Номер счёта*</label><input className="form-input" value={form.account} onChange={e => setForm({ ...form, account: e.target.value })} /></div>
                    <div className="form-group"><label className="form-label">Банк</label>
                        <select className="form-input" value={form.bank} onChange={e => setForm({ ...form, bank: e.target.value })}>
                            <option>VTB</option><option>WB</option><option>CUSTOMS</option>
                        </select>
                    </div>
                    <div className="form-group"><label className="form-label">Валюта</label>
                        <select className="form-input" value={form.currency} onChange={e => setForm({ ...form, currency: e.target.value })}>
                            <option>RUB</option><option>CNY</option>
                        </select>
                    </div>
                    <div className="form-group"><label className="form-label">Тип</label>
                        <select className="form-input" value={form.account_type} onChange={e => setForm({ ...form, account_type: e.target.value })}>
                            <option>OPER</option><option>TRANSIT</option><option>CUSTOMS_PAYEE</option>
                        </select>
                    </div>
                    <div className="form-group"><label className="form-label">Название</label><input className="form-input" value={form.account_name} onChange={e => setForm({ ...form, account_name: e.target.value })} /></div>
                    <div style={{ display: 'flex', gap: 16, alignItems: 'center', paddingTop: 24 }}>
                        <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
                            <input type="checkbox" checked={form.is_our_account} onChange={e => setForm({ ...form, is_our_account: e.target.checked })} /> Наш
                        </label>
                        <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
                            <input type="checkbox" checked={form.is_customs_payee} onChange={e => setForm({ ...form, is_customs_payee: e.target.checked })} /> Таможня
                        </label>
                    </div>
                    <div style={{ gridColumn: '1/-1' }}><button className="btn btn-primary btn-sm" onClick={save}>💾 Сохранить</button></div>
                </div>
            )}

            <TanStackDataTable
                columns={[
                    { key: 'id', label: 'ID' },
                    { key: 'account', label: 'Счёт', render: (v: any) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span> },
                    { key: 'bank', label: 'Банк' },
                    { key: 'currency', label: 'Валюта', render: (v: any) => <span className={`badge badge-${v === 'RUB' ? 'success' : 'warning'}`}>{v}</span> },
                    { key: 'account_type', label: 'Тип' },
                    { key: 'account_name', label: 'Название' },
                    { key: 'is_our_account', label: 'Наш', render: (v: any) => <>{v ? '✓' : ''}</> },
                    { key: 'is_customs_payee', label: 'Таможня', render: (v: any) => <>{v ? '✓' : ''}</> },
                    { key: '_actions', label: '', render: (_v: any, row: any) => <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(row.id)}>✕</button>, sortable: false },
                ]}
                data={data}
                emptyText="Нет счетов"
                enableSorting
                enablePagination={false}
            />
        </div>
    );
}

/* ─── Overrides ─── */
function OverridesTab() {
    const [data, setData] = useState<any[]>([]);
    const [msg, setMsg] = useState('');
    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getOverrides()); } catch { } };
    const del = async (id: number) => {
        if (!confirm('Удалить?')) return;
        try { await api.deleteOverride(id); load(); } catch (e: any) { setMsg(e.message); }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Переопределения категорий</h3>
                {data.length > 0 && <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'overrides')}>📥 Excel</button>}
            </div>
            {msg && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}

            <TanStackDataTable
                columns={[
                    { key: 'id', label: 'ID' },
                    { key: 'txn_id', label: 'TXN ID', render: (v: any) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span> },
                    { key: 'cat_lvl1', label: 'Категория 1' },
                    { key: 'cat_lvl2', label: 'Категория 2' },
                    { key: 'comment', label: 'Комментарий', render: (v: any) => <span style={{ fontSize: 12 }}>{v}</span> },
                    { key: 'updated_at', label: 'Изменено', render: (v: any) => <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>{v ? new Date(v).toLocaleString('ru') : ''}</span> },
                    { key: '_actions', label: '', render: (_v: any, row: any) => <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(row.id)}>✕</button>, sortable: false },
                ]}
                data={data}
                emptyText="Переопределений нет"
                enableSorting
                enablePagination={false}
            />
        </div>
    );
}

/* ─── Opening Balances ─── */
function BalancesTab() {
    const [data, setData] = useState<any[]>([]);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ date_open: new Date().toISOString().slice(0, 10), account: '', currency: 'RUB', opening_balance: 0 });
    const [msg, setMsg] = useState('');
    const { canEdit } = usePermissions();

    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getOpeningBalances()); } catch { } };
    const save = async () => {
        try { await api.upsertOpeningBalance({ account: form.account, currency: form.currency, date: form.date_open, amount: parseFloat(String(form.opening_balance)) }); setMsg('✅ Сохранено!'); setShowForm(false); load(); } catch (e: any) { setMsg(e.message); }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Начальные остатки</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'opening_balances')}>📥 Excel</button>
                    {canEdit() && <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Добавить</button>}
                </div>
            </div>
            {msg && <div style={{ color: 'var(--color-success)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}

            {showForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 12 }}>
                    <div className="form-group"><label className="form-label">Дата</label><input className="form-input" type="date" value={form.date_open} onChange={e => setForm({ ...form, date_open: e.target.value })} /></div>
                    <div className="form-group"><label className="form-label">Номер счёта</label><input className="form-input" value={form.account} onChange={e => setForm({ ...form, account: e.target.value })} /></div>
                    <div className="form-group"><label className="form-label">Валюта</label>
                        <select className="form-input" value={form.currency} onChange={e => setForm({ ...form, currency: e.target.value })}>
                            <option>RUB</option><option>CNY</option>
                        </select>
                    </div>
                    <div className="form-group"><label className="form-label">Остаток</label><input className="form-input" type="number" value={form.opening_balance} onChange={e => setForm({ ...form, opening_balance: parseFloat(e.target.value) || 0 })} /></div>
                    <div style={{ gridColumn: '1/-1' }}><button className="btn btn-primary btn-sm" onClick={save}>💾 Сохранить</button></div>
                </div>
            )}

            <TanStackDataTable
                columns={data.length > 0 ? Object.keys(data[0]).map(k => ({ key: k, label: k })) : []}
                data={data}
                emptyText="Нет начальных остатков"
                enableSorting
                enablePagination={false}
            />
        </div>
    );
}

/* ─── Category Reference ─── */
function CategoriesTab() {
    const [data, setData] = useState<any[]>([]);
    const [dir, setDir] = useState<'income' | 'expense'>('income');
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ direction: 'income', cat_lvl1: '', cat_lvl2: '' });
    const [msg, setMsg] = useState('');
    const { canEdit } = usePermissions();

    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getCategoryRef()); } catch { } };
    const save = async () => {
        try { await api.addCategoryRef(form); setMsg('✅ Добавлено!'); setShowForm(false); load(); } catch (e: any) { setMsg(e.message); }
    };
    const del = async (id: number) => {
        if (!confirm('Удалить?')) return;
        try { await api.deleteCategoryRef(id); load(); } catch (e: any) { setMsg(e.message); }
    };
    const toggleCogs = async (row: any) => {
        try { await api.updateCategoryRef(row.id, !row.is_cogs); setMsg('✅ Сохранено!'); load(); } catch (e: any) { setMsg(e.message); }
    };

    const filtered = data.filter(c => c.direction === dir);
    const cogsCol: Column = {
        key: 'is_cogs', label: 'Себестоимость', sortable: false,
        render: (_v: any, row: any) => (
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }} title="Расходы этой категории исключаются из расходных отчётов (себестоимость товара)">
                <input type="checkbox" checked={!!row.is_cogs} disabled={!canEdit()} onChange={() => toggleCogs(row)} />
                {row.is_cogs ? <span style={{ color: 'var(--color-warning)' }}>исключена</span> : ''}
            </label>
        ),
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Справочник категорий</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'categories')}>📥 Excel</button>
                    {canEdit() && <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Добавить</button>}
                </div>
            </div>
            {msg && <div style={{ color: 'var(--color-success)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}

            {showForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                    <div className="form-group"><label className="form-label">Тип</label>
                        <select className="form-input" value={form.direction} onChange={e => setForm({ ...form, direction: e.target.value })}>
                            <option value="income">📥 Приход</option><option value="expense">📤 Расход</option>
                        </select>
                    </div>
                    <div className="form-group"><label className="form-label">Категория</label><input className="form-input" value={form.cat_lvl1} onChange={e => setForm({ ...form, cat_lvl1: e.target.value })} /></div>
                    <div className="form-group"><label className="form-label">Подкатегория</label><input className="form-input" value={form.cat_lvl2} onChange={e => setForm({ ...form, cat_lvl2: e.target.value })} /></div>
                    <div><button className="btn btn-primary btn-sm" onClick={save}>💾 Добавить</button></div>
                </div>
            )}

            <div style={{ display: 'flex', gap: 4, marginBottom: 12 }}>
                <button className={`btn ${dir === 'income' ? 'btn-primary' : 'btn-secondary'} btn-sm`} onClick={() => setDir('income')}>📥 Приход ({data.filter(c => c.direction === 'income').length})</button>
                <button className={`btn ${dir === 'expense' ? 'btn-primary' : 'btn-secondary'} btn-sm`} onClick={() => setDir('expense')}>📤 Расход ({data.filter(c => c.direction === 'expense').length})</button>
            </div>

            <TanStackDataTable
                columns={[
                    { key: 'id', label: 'ID' },
                    { key: 'cat_lvl1', label: 'Категория', render: (v: any) => <span style={{ fontWeight: 500 }}>{v}</span> },
                    { key: 'cat_lvl2', label: 'Подкатегория' },
                    ...(dir === 'expense' ? [cogsCol] : []),
                    { key: '_actions', label: '', render: (_v: any, row: any) => <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(row.id)}>✕</button>, sortable: false },
                ]}
                data={filtered}
                emptyText={`Нет категорий ${dir === 'income' ? 'прихода' : 'расхода'}`}
                enableSorting
                enablePagination={false}
            />
        </div>
    );
}
