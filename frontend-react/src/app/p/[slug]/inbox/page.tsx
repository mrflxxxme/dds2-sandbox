'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

export default function InboxPage() {
    const [grouped, setGrouped] = useState<any[]>([]);
    const [allTxns, setAllTxns] = useState<any[]>([]);
    const [categories, setCategories] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [tab, setTab] = useState<'income' | 'expense' | 'single'>('income');
    const [msg, setMsg] = useState('');
    const [expanded, setExpanded] = useState<string | null>(null);

    useEffect(() => { loadData(); }, []);

    const loadData = async () => {
        setLoading(true);
        try {
            const [g, txns, cats] = await Promise.all([
                api.getUnassignedGrouped(),
                api.getUnassigned(1000),
                api.getCategoryRef(),
            ]);
            setGrouped(g || []);
            setAllTxns(txns || []);
            setCategories(cats || []);
        } catch { }
        setLoading(false);
    };

    const incomeCats: Record<string, string[]> = {};
    const expenseCats: Record<string, string[]> = {};
    categories.forEach(c => {
        const d = c.direction === 'income' ? incomeCats : expenseCats;
        if (!d[c.cat_lvl1]) d[c.cat_lvl1] = [];
        if (!d[c.cat_lvl1].includes(c.cat_lvl2)) d[c.cat_lvl1].push(c.cat_lvl2);
    });

    const totalOps = grouped.reduce((s, g) => s + (g.count || 0), 0);
    const totalInc = grouped.reduce((s, g) => s + (parseFloat(g.total_income) || 0), 0);
    const totalExp = grouped.reduce((s, g) => s + (parseFloat(g.total_expense) || 0), 0);

    const incGroups = grouped.filter(g => parseFloat(g.total_income) > 0).sort((a, b) => parseFloat(b.total_income) - parseFloat(a.total_income));
    const expGroups = grouped.filter(g => parseFloat(g.total_expense) > 0).sort((a, b) => parseFloat(b.total_expense) - parseFloat(a.total_expense));

    const assignBulk = async (cpKey: string, cpName: string, cat1: string, cat2: string) => {
        try {
            const result = await api.assignCategoryBulk({ cp_key: cpKey, counterparty: cpName, cat_lvl1: cat1, cat_lvl2: cat2 });
            setMsg(`✅ Обновлено ${result.updated || 0} операций → ${cat1} / ${cat2}`);
            loadData();
        } catch (e: any) { setMsg(e.message); }
    };

    const assignSingle = async (txnId: string, cat1: string, cat2: string, scope: string, cpKey: string) => {
        try {
            await api.assignCategory({ txn_id: txnId, cat_lvl1: cat1, cat_lvl2: cat2, scope, cp_key: cpKey });
            setMsg('✅ Категория назначена!');
            loadData();
        } catch (e: any) { setMsg(e.message); }
    };

    if (loading) return <div style={{ padding: 40, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    if (grouped.length === 0) return (
        <div className="animate-in">
            <div className="page-header"><h1 className="page-title">🔴 INBOX — Неразнесённые</h1></div>
            <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                <div style={{ fontSize: 48, marginBottom: 12 }}>🎉</div>
                <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Все операции разнесены!</div>
                <div style={{ color: 'var(--color-text-muted)' }}>INBOX пуст</div>
            </div>
        </div>
    );

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">🔴 INBOX — Неразнесённые</h1>
                    <p className="page-subtitle">Назначение категорий операциям</p>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(allTxns, 'inbox_unassigned')}>📥 Экспорт Excel</button>
            </div>

            {msg && (
                <div style={{ background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.3)', color: 'var(--color-success)', padding: '10px 14px', borderRadius: 8, fontSize: 13, marginBottom: 16 }}>
                    {msg}<span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span>
                </div>
            )}

            <div className="stats-grid">
                <div className="stat-card" style={{ borderLeft: '3px solid var(--color-danger)' }}>
                    <div className="stat-card-label">Неразнесённых</div>
                    <div className="stat-card-value">{totalOps}</div>
                </div>
                <div className="stat-card" style={{ borderLeft: '3px solid var(--color-success)' }}>
                    <div className="stat-card-label">Поступления</div>
                    <div className="stat-card-value" style={{ color: 'var(--color-success)' }}>{formatNumber(totalInc)} ₽</div>
                </div>
                <div className="stat-card" style={{ borderLeft: '3px solid var(--color-warning)' }}>
                    <div className="stat-card-label">Расходы</div>
                    <div className="stat-card-value" style={{ color: 'var(--color-warning)' }}>{formatNumber(totalExp)} ₽</div>
                </div>
            </div>

            {/* Tabs */}
            <div style={{ display: 'flex', gap: 4, marginBottom: 16 }}>
                {[
                    { key: 'income' as const, label: `📥 Поступления (${incGroups.length})` },
                    { key: 'expense' as const, label: `📤 Расходы (${expGroups.length})` },
                    { key: 'single' as const, label: '🔍 По одной' },
                ].map(t => (
                    <button key={t.key} className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                        onClick={() => setTab(t.key)}>{t.label}</button>
                ))}
            </div>

            {/* Income / Expense grouped */}
            {(tab === 'income' || tab === 'expense') && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {(tab === 'income' ? incGroups : expGroups).map((g, i) => {
                        const cats = tab === 'income' ? incomeCats : expenseCats;
                        const cpKey = g.cp_key || '';
                        const isOpen = expanded === `${tab}-${i}`;
                        const total = tab === 'income' ? parseFloat(g.total_income) : parseFloat(g.total_expense);
                        return (
                            <CpBlock key={i} group={g} total={total} cats={cats} isOpen={isOpen}
                                onToggle={() => setExpanded(isOpen ? null : `${tab}-${i}`)}
                                allTxns={allTxns.filter(t => t.cp_key === cpKey)}
                                onAssign={(cat1, cat2) => assignBulk(cpKey, g.counterparty, cat1, cat2)} />
                        );
                    })}
                </div>
            )}

            {/* Single assignment */}
            {tab === 'single' && (
                <SingleAssignment txns={allTxns} cats={{ ...incomeCats, ...expenseCats }} onAssign={assignSingle} />
            )}
        </div>
    );
}

function CpBlock({ group, total, cats, isOpen, onToggle, allTxns, onAssign }: any) {
    const [cat1, setCat1] = useState(Object.keys(cats)[0] || '');
    const [cat2, setCat2] = useState((cats[Object.keys(cats)[0]] || [''])[0] || '');
    const catKeys = Object.keys(cats);

    return (
        <div className="glass-card" style={{ padding: isOpen ? 20 : 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }} onClick={onToggle}>
                <div>
                    <span style={{ fontWeight: 600 }}>{group.counterparty || '—'}</span>
                    <span style={{ color: 'var(--color-text-dim)', fontSize: 13, marginLeft: 12 }}>{group.count} операц.</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span style={{ fontWeight: 700, color: total >= 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>
                        {formatNumber(total)} {group.currency || '₽'}
                    </span>
                    <span style={{ color: 'var(--color-text-dim)' }}>{isOpen ? '▲' : '▼'}</span>
                </div>
            </div>

            {isOpen && (
                <div style={{ marginTop: 12 }}>
                    {allTxns.length > 0 && (
                        <div style={{ maxHeight: 200, overflowY: 'auto', marginBottom: 12 }}>
                            <table className="data-table">
                                <thead><tr><th>Дата</th><th>Сумма</th><th>Назначение</th></tr></thead>
                                <tbody>
                                    {allTxns.slice(0, 20).map((t: any, j: number) => (
                                        <tr key={j}>
                                            <td style={{ fontSize: 12 }}>{formatDate(t.date)}</td>
                                            <td style={{ fontWeight: 600, color: (t.income || 0) > 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>
                                                {formatNumber(t.income || t.expense || 0)}
                                            </td>
                                            <td style={{ fontSize: 12, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                {t.purpose || '—'}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}

                    <div style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap', borderTop: '1px solid var(--color-border)', paddingTop: 12 }}>
                        <div className="form-group" style={{ minWidth: 180 }}>
                            <label className="form-label">Категория</label>
                            <select className="form-input" value={cat1} onChange={e => { setCat1(e.target.value); setCat2((cats[e.target.value] || [''])[0]); }}>
                                {catKeys.map(k => <option key={k} value={k}>{k}</option>)}
                            </select>
                        </div>
                        <div className="form-group" style={{ minWidth: 180 }}>
                            <label className="form-label">Подкатегория</label>
                            <select className="form-input" value={cat2} onChange={e => setCat2(e.target.value)}>
                                {(cats[cat1] || []).map((s: string) => <option key={s} value={s}>{s}</option>)}
                            </select>
                        </div>
                        <button className="btn btn-primary btn-sm" onClick={() => onAssign(cat1, cat2)}>✅ Применить ко всем</button>
                    </div>
                </div>
            )}
        </div>
    );
}

function SingleAssignment({ txns, cats, onAssign }: any) {
    const [selected, setSelected] = useState(0);
    const [cat1, setCat1] = useState(Object.keys(cats)[0] || '');
    const [cat2, setCat2] = useState((cats[Object.keys(cats)[0]] || [''])[0] || '');
    const [scope, setScope] = useState('txn');
    const catKeys = Object.keys(cats);
    const txn = txns[selected];

    if (!txn) return <div className="glass-card"><div className="empty-state"><div className="empty-state-text">Все разнесены!</div></div></div>;

    return (
        <div className="glass-card">
            <div className="form-group" style={{ marginBottom: 16 }}>
                <label className="form-label">Выберите операцию</label>
                <select className="form-input" value={selected} onChange={e => setSelected(parseInt(e.target.value))}>
                    {txns.slice(0, 200).map((t: any, i: number) => (
                        <option key={i} value={i}>
                            {(t.date || '').slice(0, 10)} | {(t.counterparty || '—').slice(0, 40)} | {formatNumber(t.expense || t.income || 0)} {t.currency}
                        </option>
                    ))}
                </select>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <div>
                    <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Детали</h4>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', lineHeight: 1.8 }}>
                        <div>Дата: <strong>{formatDate(txn.date)}</strong></div>
                        <div>Контрагент: <strong>{txn.counterparty || '—'}</strong></div>
                        <div>Приход: <strong style={{ color: 'var(--color-success)' }}>{formatNumber(txn.income || 0)}</strong> / Расход: <strong style={{ color: 'var(--color-danger)' }}>{formatNumber(txn.expense || 0)}</strong></div>
                        <div style={{ fontSize: 12, marginTop: 4, color: 'var(--color-text-dim)' }}>{(txn.purpose || '').slice(0, 200)}</div>
                    </div>
                </div>
                <div>
                    <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                        <button className={`btn ${scope === 'txn' ? 'btn-primary' : 'btn-secondary'} btn-sm`} onClick={() => setScope('txn')}>Только эта</button>
                        <button className={`btn ${scope === 'cp' ? 'btn-primary' : 'btn-secondary'} btn-sm`} onClick={() => setScope('cp')}>Весь контрагент</button>
                    </div>
                    <div className="form-group" style={{ marginBottom: 8 }}>
                        <label className="form-label">Категория</label>
                        <select className="form-input" value={cat1} onChange={e => { setCat1(e.target.value); setCat2((cats[e.target.value] || [''])[0]); }}>
                            {catKeys.map(k => <option key={k} value={k}>{k}</option>)}
                        </select>
                    </div>
                    <div className="form-group" style={{ marginBottom: 12 }}>
                        <label className="form-label">Подкатегория</label>
                        <select className="form-input" value={cat2} onChange={e => setCat2(e.target.value)}>
                            {(cats[cat1] || []).map((s: string) => <option key={s} value={s}>{s}</option>)}
                        </select>
                    </div>
                    <button className="btn btn-primary" style={{ width: '100%' }}
                        onClick={() => onAssign(txn.txn_id, cat1, cat2, scope, txn.cp_key)}>✅ Применить</button>
                </div>
            </div>
        </div>
    );
}
