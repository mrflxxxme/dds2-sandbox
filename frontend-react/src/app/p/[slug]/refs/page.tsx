'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { exportToExcel } from '@/lib/utils';

export default function RefsPage() {
    const [tab, setTab] = useState<'accounts' | 'cp' | 'overrides' | 'balances' | 'categories'>('accounts');

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">📋 Справочники</h1>
                    <p className="page-subtitle">Счета, категории контрагентов, переопределения, начальные остатки</p>
                </div>
            </div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 20, flexWrap: 'wrap' }}>
                {[
                    { key: 'accounts' as const, label: 'Счета' },
                    { key: 'cp' as const, label: 'Категории контрагентов' },
                    { key: 'overrides' as const, label: 'Переопределения' },
                    { key: 'balances' as const, label: 'Начальные остатки' },
                    { key: 'categories' as const, label: '📂 Справочник категорий' },
                ].map(t => (
                    <button key={t.key} className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                        onClick={() => setTab(t.key)}>{t.label}</button>
                ))}
            </div>
            {tab === 'accounts' && <AccountsTab />}
            {tab === 'cp' && <CpCategoriesTab />}
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
                    <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Добавить</button>
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

            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr><th>ID</th><th>Счёт</th><th>Банк</th><th>Валюта</th><th>Тип</th><th>Название</th><th>Наш</th><th>Таможня</th><th></th></tr></thead>
                        <tbody>{data.map(r => (
                            <tr key={r.id}>
                                <td>{r.id}</td><td style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.account}</td><td>{r.bank}</td>
                                <td><span className={`badge badge-${r.currency === 'RUB' ? 'success' : 'warning'}`}>{r.currency}</span></td>
                                <td>{r.account_type}</td><td>{r.account_name}</td>
                                <td>{r.is_our_account ? '✓' : ''}</td><td>{r.is_customs_payee ? '✓' : ''}</td>
                                <td><button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(r.id)}>✕</button></td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет счетов</div></div>}
        </div>
    );
}

/* ─── CP Categories ─── */
function CpCategoriesTab() {
    const [data, setData] = useState<any[]>([]);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ cp_key: '', cp_name: '', cat_lvl1: '', cat_lvl2: '', note: '' });
    const [msg, setMsg] = useState('');

    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getCpCategories()); } catch { } };
    const save = async () => {
        try { await api.upsertCpCategory(form); setMsg('✅ Сохранено!'); setShowForm(false); load(); } catch (e: any) { setMsg(e.message); }
    };
    const del = async (id: number) => {
        if (!confirm('Удалить?')) return;
        try { await api.deleteCpCategory(id); load(); } catch (e: any) { setMsg(e.message); }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Категории контрагентов ({data.length})</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'cp_categories')}>📥 Excel</button>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Добавить</button>
                </div>
            </div>
            {msg && <div style={{ color: 'var(--color-success)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}

            {showForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                    <div className="form-group"><label className="form-label">CP Key (ИНН)</label><input className="form-input" value={form.cp_key} onChange={e => setForm({ ...form, cp_key: e.target.value })} /></div>
                    <div className="form-group"><label className="form-label">Название</label><input className="form-input" value={form.cp_name} onChange={e => setForm({ ...form, cp_name: e.target.value })} /></div>
                    <div className="form-group"><label className="form-label">Примечание</label><input className="form-input" value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} /></div>
                    <div className="form-group"><label className="form-label">Категория 1</label><input className="form-input" value={form.cat_lvl1} onChange={e => setForm({ ...form, cat_lvl1: e.target.value })} /></div>
                    <div className="form-group"><label className="form-label">Категория 2</label><input className="form-input" value={form.cat_lvl2} onChange={e => setForm({ ...form, cat_lvl2: e.target.value })} /></div>
                    <div style={{ paddingTop: 24 }}><button className="btn btn-primary btn-sm" onClick={save}>💾 Сохранить</button></div>
                </div>
            )}

            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr><th>ID</th><th>CP Key</th><th>Имя</th><th>Категория 1</th><th>Категория 2</th><th>Примечание</th><th></th></tr></thead>
                        <tbody>{data.map(r => (
                            <tr key={r.id}>
                                <td>{r.id}</td><td style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.cp_key}</td><td>{r.cp_name}</td>
                                <td>{r.cat_lvl1}</td><td>{r.cat_lvl2}</td><td style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>{r.note}</td>
                                <td><button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(r.id)}>✕</button></td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет категорий</div></div>}
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

            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr><th>ID</th><th>TXN ID</th><th>Категория 1</th><th>Категория 2</th><th>Комментарий</th><th>Изменено</th><th></th></tr></thead>
                        <tbody>{data.map(r => (
                            <tr key={r.id}>
                                <td>{r.id}</td><td style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.txn_id}</td>
                                <td>{r.cat_lvl1}</td><td>{r.cat_lvl2}</td>
                                <td style={{ fontSize: 12 }}>{r.comment}</td>
                                <td style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>{r.updated_at ? new Date(r.updated_at).toLocaleString('ru') : ''}</td>
                                <td><button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(r.id)}>✕</button></td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Переопределений нет</div></div>}
        </div>
    );
}

/* ─── Opening Balances ─── */
function BalancesTab() {
    const [data, setData] = useState<any[]>([]);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ date_open: new Date().toISOString().slice(0, 10), account: '', currency: 'RUB', opening_balance: 0 });
    const [msg, setMsg] = useState('');

    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getOpeningBalances()); } catch { } };
    const save = async () => {
        try { await api.upsertOpeningBalance({ ...form, opening_balance: parseFloat(String(form.opening_balance)) }); setMsg('✅ Сохранено!'); setShowForm(false); load(); } catch (e: any) { setMsg(e.message); }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Начальные остатки</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'opening_balances')}>📥 Excel</button>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Добавить</button>
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

            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr>{Object.keys(data[0]).map(k => <th key={k}>{k}</th>)}</tr></thead>
                        <tbody>{data.map((r, i) => <tr key={i}>{Object.values(r).map((v: any, j) => <td key={j}>{v ?? '—'}</td>)}</tr>)}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет начальных остатков</div></div>}
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

    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getCategoryRef()); } catch { } };
    const save = async () => {
        try { await api.addCategoryRef(form); setMsg('✅ Добавлено!'); setShowForm(false); load(); } catch (e: any) { setMsg(e.message); }
    };
    const del = async (id: number) => {
        if (!confirm('Удалить?')) return;
        try { await api.deleteCategoryRef(id); load(); } catch (e: any) { setMsg(e.message); }
    };

    const filtered = data.filter(c => c.direction === dir);

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Справочник категорий</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'categories')}>📥 Excel</button>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Добавить</button>
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

            {filtered.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr><th>ID</th><th>Категория</th><th>Подкатегория</th><th></th></tr></thead>
                        <tbody>{filtered.map(r => (
                            <tr key={r.id}>
                                <td>{r.id}</td><td style={{ fontWeight: 500 }}>{r.cat_lvl1}</td><td>{r.cat_lvl2}</td>
                                <td><button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(r.id)}>✕</button></td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет категорий {dir === 'income' ? 'прихода' : 'расхода'}</div></div>}
        </div>
    );
}
