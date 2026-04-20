'use client';
import { useEffect, useState, ChangeEvent } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import { usePermissions } from '@/lib/hooks/usePermissions';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type {
    UnassignedGroupRow, Transaction, CategoryRef,
    AutoCategorizeRule, AutoCategorizePreview,
} from '@/types/api';

export default function InboxPage() {
    const [grouped, setGrouped] = useState<UnassignedGroupRow[]>([]);
    const [allTxns, setAllTxns] = useState<Transaction[]>([]);
    const [categories, setCategories] = useState<CategoryRef[]>([]);
    const [loading, setLoading] = useState(true);
    const [tab, setTab] = useState<'income' | 'expense' | 'single'>('income');
    const [msg, setMsg] = useState('');
    const [expanded, setExpanded] = useState<string | null>(null);
    const { canEdit } = usePermissions();
    // Auto-categorize state
    const [showRules, setShowRules] = useState(false);
    const [rules, setRules] = useState<AutoCategorizeRule[]>([]);
    const [preview, setPreview] = useState<AutoCategorizePreview[] | null>(null);
    const [autoLoading, setAutoLoading] = useState(false);
    const [newKeyword, setNewKeyword] = useState('');
    const [newCat1, setNewCat1] = useState('');
    const [newCat2, setNewCat2] = useState('');
    const [newDirection, setNewDirection] = useState('expense');

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
        } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка загрузки данных'); }
        setLoading(false);
    };

    const loadRules = async () => {
        try { const r = await api.getAutoCategorizeRules(); setRules(r || []); } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка загрузки правил'); }
    };
    const loadPreview = async () => {
        setAutoLoading(true);
        try { const p = await api.previewAutoCategorize(); setPreview(p || []); } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка предпросмотра'); }
        setAutoLoading(false);
    };
    const applyAutoCat = async () => {
        setAutoLoading(true);
        try {
            const r = await api.applyAutoCategorize();
            setMsg(`⚡ Авто-разнесено: ${r.updated} операций`);
            setPreview(null);
            loadData();
        } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
        setAutoLoading(false);
    };
    const addRule = async () => {
        if (!newKeyword || !newCat1) return;
        try {
            await api.createAutoCategorizeRule({ keyword: newKeyword, cat_lvl1: newCat1, cat_lvl2: newCat2 || undefined, direction: newDirection });
            setNewKeyword(''); setNewCat1(''); setNewCat2('');
            loadRules();
            setMsg('✅ Правило добавлено');
        } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
    };
    const delRule = async (id: number) => {
        try { await api.deleteAutoCategorizeRule(id); loadRules(); } catch { }
    };
    const openRulesPanel = () => { setShowRules(true); loadRules(); };

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
        } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
    };

    const assignByIds = async (txnIds: string[], cat1: string, cat2: string) => {
        try {
            const result = await api.assignCategoryByIds({ txn_ids: txnIds, cat_lvl1: cat1, cat_lvl2: cat2 });
            setMsg(`✅ Обновлено ${result.updated || 0} из ${txnIds.length} выбранных операций → ${cat1} / ${cat2}`);
            loadData();
        } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
    };

    const assignSingle = async (txnId: string, cat1: string, cat2: string, scope: string, cpKey: string) => {
        try {
            await api.assignCategory({ txn_id: txnId, cat_lvl1: cat1, cat_lvl2: cat2, scope, cp_key: cpKey });
            setMsg('✅ Категория назначена!');
            loadData();
        } catch (e: unknown) { setMsg(e instanceof Error ? e.message : 'Ошибка'); }
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
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-sm" style={{ background: 'linear-gradient(135deg, #f59e0b, #ef4444)', color: '#fff', fontWeight: 700 }}
                        onClick={() => { loadPreview(); }}>⚡ Авто-разнести</button>
                    <button className="btn btn-secondary btn-sm" onClick={openRulesPanel}>⚙️ Правила</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(allTxns, 'inbox_unassigned')}>📥 Excel</button>
                </div>
            </div>

            {/* Auto-categorize preview modal */}
            {preview !== null && (
                <div className="glass-card" style={{ marginBottom: 16, border: '2px solid var(--color-warning)', position: 'relative' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                        <h3 style={{ margin: 0, fontSize: 16 }}>⚡ Превью авто-разнёски ({preview.length} совпадений)</h3>
                        <button className="btn btn-secondary btn-sm" onClick={() => setPreview(null)}>✕ Закрыть</button>
                    </div>
                    {preview.length === 0 ? (
                        <div style={{ textAlign: 'center', padding: 20, color: 'var(--color-text-muted)' }}>
                            Нет совпадений. Добавьте правила через ⚙️ Правила
                        </div>
                    ) : (
                        <>
                            <div style={{ marginBottom: 12 }}>
                                <TanStackDataTable
                                    columns={[
                                        { key: 'date', label: 'Дата', render: (v: any) => <span style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{formatDate(v)}</span> },
                                        { key: 'counterparty', label: 'Контрагент', render: (v: any) => <span style={{ fontSize: 12 }}>{(v || '—').slice(0, 30)}</span> },
                                        { key: 'expense', label: 'Сумма', render: (_v: any, row: any) => <span style={{ fontWeight: 600, whiteSpace: 'nowrap', color: row.expense > 0 ? 'var(--color-danger)' : 'var(--color-success)' }}>{formatNumber(row.expense > 0 ? row.expense : row.income)}</span> },
                                        { key: 'matched_keyword', label: 'Ключевое слово', render: (v: any) => <span style={{ background: 'rgba(245,158,11,0.15)', color: '#f59e0b', padding: '2px 8px', borderRadius: 4, fontSize: 12, fontWeight: 600 }}>{v}</span> },
                                        { key: 'suggested_cat_lvl1', label: '→ Категория', render: (_v: any, row: any) => <span style={{ fontSize: 12, fontWeight: 600 }}>{row.suggested_cat_lvl1}{row.suggested_cat_lvl2 ? ` / ${row.suggested_cat_lvl2}` : ''}</span> },
                                        { key: 'purpose', label: 'Назначение', render: (v: any) => <span style={{ fontSize: 11, color: 'var(--color-text-dim)', maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block' }}>{v}</span> },
                                    ]}
                                    data={preview}
                                    enableSorting
                                    maxHeight={400}
                                    enablePagination={false}
                                />
                            </div>
                            {canEdit() && (
                                <button className="btn btn-primary" style={{ width: '100%' }} disabled={autoLoading}
                                    onClick={applyAutoCat}>
                                    {autoLoading ? '⏳ Применяю...' : `✅ Применить авто-разнёску (${preview.length} операций)`}
                                </button>
                            )}
                        </>
                    )}
                </div>
            )}

            {/* Rules management panel */}
            {showRules && (
                <div className="glass-card" style={{ marginBottom: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                        <h3 style={{ margin: 0, fontSize: 16 }}>⚙️ Правила авто-категоризации</h3>
                        <button className="btn btn-secondary btn-sm" onClick={() => setShowRules(false)}>✕</button>
                    </div>
                    <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                        Если в назначении платежа найдётся ключевое слово — операция автоматически получит указанную категорию.
                    </p>
                    {/* Existing rules */}
                    {rules.length > 0 && (
                        <div style={{ marginBottom: 16 }}>
                            <TanStackDataTable
                                columns={[
                                    { key: 'keyword', label: 'Ключевое слово', render: (v: any) => <strong>{v}</strong> },
                                    { key: 'direction', label: 'Направление', render: (v: any) => <span style={{ fontSize: 12 }}>{v === 'income' ? '📥 Доход' : '📤 Расход'}</span> },
                                    { key: 'cat_lvl1', label: 'Категория' },
                                    { key: 'cat_lvl2', label: 'Подкатегория', render: (v: any) => <>{v || '—'}</> },
                                    { key: '_actions', label: '', width: '50', render: (_v: any, row: any) => <button className="btn btn-sm" style={{ color: 'var(--color-danger)', background: 'none', padding: '2px 6px' }} onClick={() => delRule(row.id)}>🗑</button>, sortable: false },
                                ]}
                                data={rules}
                                enableSorting
                                enablePagination={false}
                            />
                        </div>
                    )}
                    {/* Add new rule */}
                    <div style={{ display: 'flex', gap: 8, alignItems: 'end', flexWrap: 'wrap', borderTop: '1px solid var(--color-border)', paddingTop: 12 }}>
                        <div className="form-group" style={{ minWidth: 150 }}>
                            <label className="form-label">Ключевое слово</label>
                            <input className="form-input" value={newKeyword} onChange={(e: ChangeEvent<HTMLInputElement>) => setNewKeyword(e.target.value)} placeholder="транспортн" />
                        </div>
                        <div className="form-group" style={{ minWidth: 100 }}>
                            <label className="form-label">Направление</label>
                            <select className="form-input" value={newDirection} onChange={(e: ChangeEvent<HTMLSelectElement>) => setNewDirection(e.target.value)}>
                                <option value="expense">Расход</option>
                                <option value="income">Доход</option>
                            </select>
                        </div>
                        <div className="form-group" style={{ minWidth: 150 }}>
                            <label className="form-label">Категория</label>
                            <select className="form-input" value={newCat1} onChange={(e: ChangeEvent<HTMLSelectElement>) => { setNewCat1(e.target.value); setNewCat2((newDirection === 'income' ? incomeCats : expenseCats)[e.target.value]?.[0] || ''); }}>
                                <option value="">—</option>
                                {Object.keys(newDirection === 'income' ? incomeCats : expenseCats).map(k => <option key={k} value={k}>{k}</option>)}
                            </select>
                        </div>
                        <div className="form-group" style={{ minWidth: 150 }}>
                            <label className="form-label">Подкатегория</label>
                            <select className="form-input" value={newCat2} onChange={(e: ChangeEvent<HTMLSelectElement>) => setNewCat2(e.target.value)}>
                                <option value="">—</option>
                                {((newDirection === 'income' ? incomeCats : expenseCats)[newCat1] || []).map((s: string) => <option key={s} value={s}>{s}</option>)}
                            </select>
                        </div>
                        <button className="btn btn-primary btn-sm" onClick={addRule}>➕ Добавить</button>
                    </div>
                </div>
            )}

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
                                onAssign={(cat1, cat2) => assignBulk(cpKey, g.counterparty, cat1, cat2)}
                                onAssignByIds={(txnIds: string[], cat1: string, cat2: string) => assignByIds(txnIds, cat1, cat2)} />
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

interface CpBlockProps {
    group: UnassignedGroupRow;
    total: number;
    cats: Record<string, string[]>;
    isOpen: boolean;
    onToggle: () => void;
    allTxns: Transaction[];
    onAssign: (cat1: string, cat2: string) => void;
    onAssignByIds: (txnIds: string[], cat1: string, cat2: string) => void;
}

function CpBlock({ group, total, cats, isOpen, onToggle, allTxns, onAssign, onAssignByIds }: CpBlockProps) {
    const [cat1, setCat1] = useState(Object.keys(cats)[0] || '');
    const [cat2, setCat2] = useState((cats[Object.keys(cats)[0]] || [''])[0] || '');
    const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
    const catKeys = Object.keys(cats);

    const visibleTxns = allTxns.slice(0, 20);
    const allChecked = visibleTxns.length > 0 && visibleTxns.every((t: Transaction) => selectedIds.has(t.txn_id));
    const someChecked = selectedIds.size > 0;

    const toggleOne = (txnId: string) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            if (next.has(txnId)) next.delete(txnId); else next.add(txnId);
            return next;
        });
    };

    const toggleAll = () => {
        if (allChecked) {
            setSelectedIds(new Set());
        } else {
            setSelectedIds(new Set(visibleTxns.map((t: Transaction) => t.txn_id)));
        }
    };

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
                    {/* TODO: migrate to TanStackDataTable — table with checkbox selection per row */}
                    {allTxns.length > 0 && (
                        <div style={{ maxHeight: 300, overflowY: 'auto', marginBottom: 12 }}>
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th style={{ width: 32, textAlign: 'center' }}>
                                            <input type="checkbox" checked={allChecked} onChange={toggleAll}
                                                style={{ cursor: 'pointer', accentColor: 'var(--color-primary)' }} />
                                        </th>
                                        <th>Дата</th>
                                        <th>Сумма</th>
                                        <th>Назначение</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {visibleTxns.map((t: Transaction, j: number) => (
                                        <tr key={j} style={{ background: selectedIds.has(t.txn_id) ? 'rgba(99,102,241,0.08)' : undefined }}>
                                            <td style={{ textAlign: 'center' }}>
                                                <input type="checkbox" checked={selectedIds.has(t.txn_id)}
                                                    onChange={() => toggleOne(t.txn_id)}
                                                    style={{ cursor: 'pointer', accentColor: 'var(--color-primary)' }} />
                                            </td>
                                            <td style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{formatDate(t.date)}</td>
                                            <td style={{ fontWeight: 600, whiteSpace: 'nowrap', color: t.income > 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>
                                                {formatNumber(t.income > 0 ? t.income : t.expense)}
                                            </td>
                                            <td style={{ fontSize: 12, whiteSpace: 'normal', wordBreak: 'break-word', minWidth: 300 }}>
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
                        {someChecked && (
                            <button className="btn btn-sm" style={{ background: 'var(--color-warning)', color: '#000', fontWeight: 600 }}
                                onClick={() => { onAssignByIds(Array.from(selectedIds), cat1, cat2); setSelectedIds(new Set()); }}>
                                ☑ Применить к выбранным ({selectedIds.size})
                            </button>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}

interface SingleAssignmentProps {
    txns: Transaction[];
    cats: Record<string, string[]>;
    onAssign: (txnId: string, cat1: string, cat2: string, scope: string, cpKey: string) => void;
}

function SingleAssignment({ txns, cats, onAssign }: SingleAssignmentProps) {
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
                    {txns.slice(0, 200).map((t: Transaction, i: number) => (
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
                        onClick={() => onAssign(txn.txn_id, cat1, cat2, scope, txn.cp_key ?? '')}>✅ Применить</button>
                </div>
            </div>
        </div>
    );
}
