'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { exportToExcel, formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import CounterpartyTypeBadge, { TYPE_CONFIG } from '@/components/CounterpartyTypeBadge';
import type {
    CounterpartyListItem, CounterpartyType, CounterpartyCreate,
    CounterpartyCategorySummary,
} from '@/types/api';

const TODAY = new Date().toISOString().slice(0, 10);
const HALF_YEAR_AGO = new Date(Date.now() - 180 * 24 * 3600 * 1000).toISOString().slice(0, 10);

const COUNTERPARTY_TYPES: { value: CounterpartyType; label: string }[] = [
    { value: 'SUPPLIER', label: 'Поставщик' },
    { value: 'FULFILLMENT', label: 'Фулфилмент' },
    { value: 'CARRIER', label: 'Перевозчик' },
    { value: 'CUSTOMS_BROKER', label: 'Таможенный брокер' },
    { value: 'DESIGNER', label: 'Дизайнер' },
    { value: 'LEGAL', label: 'Юридические' },
    { value: 'LANDLORD', label: 'Аренда' },
    { value: 'IT_SERVICE', label: 'IT-сервис' },
    { value: 'MARKETPLACE', label: 'Маркетплейс' },
    { value: 'BANK', label: 'Банк' },
    { value: 'GOVERNMENT', label: 'Государство' },
    { value: 'AFFILIATED', label: 'Аффилированные' },
    { value: 'OTHER', label: 'Прочее' },
];

const emptyForm = (): CounterpartyCreate => ({
    name: '',
    primary_type: 'OTHER',
    secondary_types: [],
    inn: null,
    kpp: null,
    contract_number: null,
    notes: null,
});

export default function CounterpartyPage() {
    const params = useParams();
    const slug = params.slug as string;
    const router = useRouter();

    const [items, setItems] = useState<CounterpartyListItem[]>([]);
    const [summary, setSummary] = useState<CounterpartyCategorySummary[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Filters
    const [search, setSearch] = useState('');
    const [typeFilter, setTypeFilter] = useState<CounterpartyType | ''>('');
    const [activeOnly, setActiveOnly] = useState(false);
    const [catFilter, setCatFilter] = useState<'all' | 'none'>('all');
    const [dateFrom, setDateFrom] = useState(HALF_YEAR_AGO);
    const [dateTo, setDateTo] = useState(TODAY);

    // Bulk selection + categorization
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [bulkCat1, setBulkCat1] = useState('');
    const [bulkCat2, setBulkCat2] = useState('');
    const [bulkType, setBulkType] = useState<CounterpartyType | ''>('');
    const [bulkSaving, setBulkSaving] = useState(false);
    const [bulkMsg, setBulkMsg] = useState('');

    // Merge (select exactly 2 → pick which survives)
    const [mergeOpen, setMergeOpen] = useState(false);
    const [mergeTargetId, setMergeTargetId] = useState<number | null>(null);
    const [merging, setMerging] = useState(false);
    const [mergeMsg, setMergeMsg] = useState('');

    // Create form
    const [showCreate, setShowCreate] = useState(false);
    const [form, setForm] = useState<CounterpartyCreate>(emptyForm());
    const [saving, setSaving] = useState(false);
    const [formError, setFormError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [listRes, summaryRes] = await Promise.all([
                api.listCounterparties({
                    q: search.trim() || undefined,
                    type: typeFilter || undefined,
                    active_only: activeOnly || undefined,
                    limit: 200,
                    offset: 0,
                    date_from: dateFrom,
                    date_to: dateTo,
                }),
                api.getCounterpartiesSummary({ date_from: dateFrom, date_to: dateTo }),
            ]);
            setItems(listRes.items);
            setTotal(listRes.total);
            setSummary(summaryRes.items);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [search, typeFilter, activeOnly, dateFrom, dateTo]);

    useEffect(() => { load(); }, [load]);

    // Debounced search
    useEffect(() => {
        const timer = setTimeout(() => { load(); }, 300);
        return () => clearTimeout(timer);
    }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

    const handleCreate = async () => {
        if (!form.name.trim()) { setFormError('Введите название'); return; }
        setSaving(true);
        setFormError('');
        try {
            await api.createCounterparty({
                ...form,
                inn: form.inn?.trim() || null,
                kpp: form.kpp?.trim() || null,
                contract_number: form.contract_number?.trim() || null,
                notes: form.notes?.trim() || null,
            });
            setShowCreate(false);
            setForm(emptyForm());
            await load();
        } catch (e: unknown) {
            setFormError(e instanceof Error ? e.message : 'Ошибка создания');
        } finally {
            setSaving(false);
        }
    };

    const handleDelete = async (id: number) => {
        if (!confirm('Удалить контрагента?')) return;
        try {
            await api.deleteCounterparty(id);
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка удаления');
        }
    };

    // ─── Bulk selection / categorization ──────────────────────────────
    const visibleItems = catFilter === 'none' ? items.filter(i => !i.cat_lvl1) : items;
    const uncategorizedCount = items.filter(i => !i.cat_lvl1).length;
    const toggleSel = (id: number) => setSelected(s => {
        const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n;
    });
    const selectAllVisible = () => setSelected(new Set(visibleItems.map(i => i.id)));
    const clearSel = () => setSelected(new Set());

    const mergePair = items.filter(i => selected.has(i.id));
    const openMerge = () => {
        if (selected.size !== 2) return;
        setMergeTargetId([...selected][0]);
        setMergeMsg('');
        setMergeOpen(true);
    };
    const handleMerge = async () => {
        if (mergeTargetId == null) return;
        const sourceId = [...selected].find(id => id !== mergeTargetId);
        if (sourceId == null) return;
        setMerging(true); setMergeMsg('');
        try {
            const res = await api.mergeCounterparties(mergeTargetId, sourceId);
            setMergeOpen(false);
            clearSel();
            setBulkMsg(`✓ Объединено: перенесено операций ${res.moved.transactions ?? 0}`);
            await load();
        } catch (e: unknown) {
            setMergeMsg(e instanceof Error ? e.message : 'Ошибка объединения');
        } finally {
            setMerging(false);
        }
    };

    const handleBulkApply = async () => {
        if (selected.size === 0) return;
        if (!bulkCat1.trim() && !bulkType) { setBulkMsg('Укажите категорию или тип'); return; }
        setBulkSaving(true); setBulkMsg('');
        try {
            const res = await api.bulkSetCounterpartyCategory([...selected], {
                cat_lvl1: bulkCat1.trim() || null,
                cat_lvl2: bulkCat2.trim() || null,
                primary_type: bulkType || null,
            });
            setBulkMsg(`✓ Применено к ${res.counterparties} контрагентам (${res.transactions} операций)`);
            clearSel(); setBulkCat1(''); setBulkCat2(''); setBulkType('');
            await load();
        } catch (e: unknown) {
            setBulkMsg(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setBulkSaving(false);
        }
    };

    const columns = [
        {
            key: '_sel', label: '',
            render: (_v: unknown, row: CounterpartyListItem) => (
                <input
                    type="checkbox"
                    checked={selected.has(row.id)}
                    onChange={() => toggleSel(row.id)}
                    onClick={e => e.stopPropagation()}
                    aria-label="Выбрать"
                />
            ),
        },
        {
            key: 'inn', label: 'ИНН',
            render: (v: string | null) => v ? (
                <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span>
            ) : <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        {
            key: 'name', label: 'Название',
            render: (v: string, row: CounterpartyListItem) => (
                <button
                    className="btn btn-secondary btn-sm"
                    style={{ background: 'none', border: 'none', padding: 0, fontWeight: 500, cursor: 'pointer', textAlign: 'left' }}
                    onClick={() => router.push(`/p/${slug}/refs/counterparty/${row.id}`)}
                >
                    {v}
                </button>
            ),
        },
        {
            key: 'primary_type', label: 'Тип',
            render: (v: CounterpartyType) => <CounterpartyTypeBadge type={v} size="sm" />,
        },
        {
            key: 'cat_lvl1', label: 'Категория',
            render: (v: string | null | undefined, row: CounterpartyListItem) => v ? (
                <span className="badge badge-warning" style={{ fontSize: 11 }}>{v}{row.cat_lvl2 ? ` · ${row.cat_lvl2}` : ''}</span>
            ) : <span style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>—</span>,
        },
        {
            key: 'contract_number', label: 'Контракт',
            render: (v: string | null) => v ? (
                <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span>
            ) : <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        {
            key: 'expense_rub', label: 'Расход ₽',
            render: (_v: unknown, row: CounterpartyListItem) => {
                const rub = Number(row.expense_rub ?? 0);
                const cny = Number(row.expense_cny ?? 0);
                if (rub === 0 && cny === 0) {
                    return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
                }
                return (
                    <div style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                        {rub > 0 && <div style={{ color: 'var(--color-danger)', fontWeight: 500 }}>{formatNumber(rub)} ₽</div>}
                        {cny > 0 && <div style={{ color: 'var(--color-warning)', fontSize: 12 }}>{formatNumber(cny)} ¥</div>}
                    </div>
                );
            },
        },
        {
            key: 'tx_count', label: 'Транз.',
            render: (v: number | null | undefined) => {
                const n = v ?? 0;
                return n > 0 ? (
                    <span style={{ fontVariantNumeric: 'tabular-nums' }}>{formatNumber(n, 0)}</span>
                ) : <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
            },
        },
        {
            key: 'created_by_import', label: 'Источник',
            render: (v: boolean) => (
                <span className={`badge ${v ? 'badge-secondary' : 'badge-success'}`} style={{ fontSize: 11 }}>
                    {v ? 'импорт' : 'вручную'}
                </span>
            ),
        },
        {
            key: '_actions', label: '',
            render: (_v: unknown, row: CounterpartyListItem) => (
                <div style={{ display: 'flex', gap: 4 }}>
                    <button
                        className="btn btn-secondary btn-sm"
                        style={{ padding: '2px 8px', fontSize: 11 }}
                        onClick={() => router.push(`/p/${slug}/refs/counterparty/${row.id}`)}
                    >
                        →
                    </button>
                    <button
                        className="btn btn-danger btn-sm"
                        style={{ padding: '2px 8px', fontSize: 11 }}
                        onClick={() => handleDelete(row.id)}
                    >
                        ✕
                    </button>
                </div>
            ),
            sortable: false,
        },
    ];

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Контрагенты</h1>
                    <p className="page-subtitle">Справочник контрагентов проекта ({total})</p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => exportToExcel(items, 'counterparties')}
                        disabled={items.length === 0}
                    >
                        Excel
                    </button>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>
                        + Добавить
                    </button>
                </div>
            </div>

            {/* Period picker */}
            <div className="glass-card" style={{ marginBottom: 16, padding: '12px 16px' }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Период:</span>
                    <input type="date" className="form-input" style={{ width: 160 }} value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
                    <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                    <input type="date" className="form-input" style={{ width: 160 }} value={dateTo} onChange={e => setDateTo(e.target.value)} />
                </div>
            </div>

            {/* Category summary */}
            {summary.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                    <div style={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
                        gap: 12,
                    }}>
                        {summary
                            .filter(s => s.count_cps > 0 && (s.expense_rub > 0 || s.expense_cny > 0 || s.income_rub > 0 || s.income_cny > 0 || typeFilter === s.primary_type))
                            .map(s => {
                                const cfg = TYPE_CONFIG[s.primary_type] ?? TYPE_CONFIG.OTHER;
                                const active = typeFilter === s.primary_type;
                                return (
                                    <button
                                        key={s.primary_type}
                                        type="button"
                                        className="glass-card"
                                        style={{
                                            textAlign: 'left', cursor: 'pointer', padding: 14,
                                            border: active ? '2px solid var(--color-accent)' : undefined,
                                            background: active ? 'rgba(0,113,227,0.06)' : undefined,
                                        }}
                                        onClick={() => setTypeFilter(active ? '' : s.primary_type)}
                                    >
                                        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6, display: 'flex', gap: 6, alignItems: 'center' }}>
                                            <span>{cfg.icon}</span>
                                            <span>{cfg.label}</span>
                                            <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--color-text-dim)', fontWeight: 400 }}>
                                                {formatNumber(s.count_cps, 0)} КА
                                            </span>
                                        </div>
                                        {s.expense_rub > 0 && (
                                            <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-danger)', fontVariantNumeric: 'tabular-nums' }}>
                                                {formatNumber(s.expense_rub)} ₽
                                            </div>
                                        )}
                                        {s.expense_cny > 0 && (
                                            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-warning)', fontVariantNumeric: 'tabular-nums' }}>
                                                {formatNumber(s.expense_cny)} ¥
                                            </div>
                                        )}
                                        {s.expense_rub === 0 && s.expense_cny === 0 && (
                                            <div style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>нет расходов</div>
                                        )}
                                    </button>
                                );
                            })
                        }
                    </div>
                </div>
            )}

            {/* Filters */}
            <div className="glass-card" style={{ marginBottom: 16, padding: '12px 16px' }}>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                    <input
                        className="form-input"
                        style={{ flex: 1, minWidth: 200 }}
                        placeholder="Поиск по имени или ИНН..."
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                    />
                    <select
                        className="form-input"
                        style={{ width: 200 }}
                        value={typeFilter}
                        onChange={e => setTypeFilter(e.target.value as CounterpartyType | '')}
                    >
                        <option value="">Все типы</option>
                        {COUNTERPARTY_TYPES.map(t => (
                            <option key={t.value} value={t.value}>{t.label}</option>
                        ))}
                    </select>
                    <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
                        <input
                            type="checkbox"
                            checked={activeOnly}
                            onChange={e => setActiveOnly(e.target.checked)}
                        />
                        Только активные
                    </label>
                    <button
                        className={`btn btn-sm ${catFilter === 'none' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setCatFilter(f => f === 'none' ? 'all' : 'none')}
                        title="Показать только контрагентов без категории расхода"
                    >
                        Без категории{uncategorizedCount > 0 ? ` (${uncategorizedCount})` : ''}
                    </button>
                </div>
            </div>

            {/* Bulk action bar — выбрал N → задать тип/категорию разом */}
            {selected.size > 0 && (
                <div className="glass-card" style={{ marginBottom: 16, padding: '12px 16px', border: '2px solid var(--color-accent)' }}>
                    <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 13, fontWeight: 600 }}>Выбрано: {selected.size}</span>
                        <button className="btn btn-secondary btn-sm" onClick={selectAllVisible}>Все на странице ({visibleItems.length})</button>
                        <button className="btn btn-secondary btn-sm" onClick={clearSel}>Снять</button>
                        <span style={{ color: 'var(--color-text-dim)' }}>|</span>
                        <select className="form-input" style={{ width: 170 }} value={bulkType} onChange={e => setBulkType(e.target.value as CounterpartyType | '')}>
                            <option value="">Тип (не менять)</option>
                            {COUNTERPARTY_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                        </select>
                        <input className="form-input" style={{ width: 180 }} placeholder="Категория расхода" value={bulkCat1} onChange={e => setBulkCat1(e.target.value)} />
                        <input className="form-input" style={{ width: 150 }} placeholder="Под-категория (опц.)" value={bulkCat2} onChange={e => setBulkCat2(e.target.value)} />
                        <button className="btn btn-primary btn-sm" onClick={handleBulkApply} disabled={bulkSaving}>
                            {bulkSaving ? '…' : 'Применить к выбранным'}
                        </button>
                        {selected.size === 2 && (
                            <>
                                <span style={{ color: 'var(--color-text-dim)' }}>|</span>
                                <button className="btn btn-secondary btn-sm" onClick={openMerge} title="Объединить двух контрагентов в одного">
                                    🔗 Объединить
                                </button>
                            </>
                        )}
                    </div>
                    {bulkMsg && <div style={{ fontSize: 12, color: bulkMsg.startsWith('✓') ? 'var(--color-success)' : 'var(--color-danger)', marginTop: 8 }}>{bulkMsg}</div>}
                </div>
            )}

            {/* Merge modal — pick which of the two selected survives */}
            {mergeOpen && mergePair.length === 2 && (
                <div
                    onClick={() => !merging && setMergeOpen(false)}
                    style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}
                >
                    <div className="glass-card" onClick={e => e.stopPropagation()} style={{ width: 560, maxWidth: '92vw', padding: 24 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                            <h3 style={{ fontSize: 17, fontWeight: 600, margin: 0 }}>Объединение контрагентов</h3>
                            <button className="btn btn-secondary btn-sm" onClick={() => setMergeOpen(false)} disabled={merging}>✕</button>
                        </div>
                        <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16 }}>
                            Выберите <b>главную</b> запись. Вторая будет архивирована: все операции,
                            идентификаторы, документы, категория и реквизиты перенесутся на главную.
                            Действие необратимо.
                        </p>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 16 }}>
                            {mergePair.map(cp => {
                                const isTarget = mergeTargetId === cp.id;
                                return (
                                    <label
                                        key={cp.id}
                                        style={{
                                            display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer',
                                            padding: 12, borderRadius: 8,
                                            border: isTarget ? '2px solid var(--color-accent)' : '1px solid var(--color-border)',
                                            background: isTarget ? 'rgba(0,113,227,0.06)' : undefined,
                                        }}
                                    >
                                        <input type="radio" name="merge-target" checked={isTarget} onChange={() => setMergeTargetId(cp.id)} style={{ marginTop: 3 }} />
                                        <div style={{ flex: 1 }}>
                                            <div style={{ fontWeight: 600, fontSize: 14 }}>{cp.name}</div>
                                            <div style={{ fontSize: 12, color: 'var(--color-text-dim)', display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 2 }}>
                                                <span>ИНН: {cp.inn || '—'}</span>
                                                <span className={`badge ${cp.created_by_import ? 'badge-secondary' : 'badge-success'}`} style={{ fontSize: 10 }}>
                                                    {cp.created_by_import ? 'импорт' : 'вручную'}
                                                </span>
                                                {cp.cat_lvl1 && <span>кат: {cp.cat_lvl1}</span>}
                                            </div>
                                        </div>
                                        {isTarget && <span style={{ fontSize: 11, color: 'var(--color-accent)', fontWeight: 600 }}>останется</span>}
                                    </label>
                                );
                            })}
                        </div>
                        {mergeMsg && <div style={{ fontSize: 13, color: 'var(--color-danger)', marginBottom: 12 }}>{mergeMsg}</div>}
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary btn-sm" onClick={() => setMergeOpen(false)} disabled={merging}>Отмена</button>
                            <button className="btn btn-primary btn-sm" onClick={handleMerge} disabled={merging || mergeTargetId == null}>
                                {merging ? 'Объединение…' : 'Объединить'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Create form */}
            {showCreate && (
                <div className="glass-card" style={{ marginBottom: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                        <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Новый контрагент</h3>
                        <button className="btn btn-secondary btn-sm" onClick={() => { setShowCreate(false); setForm(emptyForm()); setFormError(''); }}>✕</button>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                        <div className="form-group" style={{ gridColumn: '1/-1' }}>
                            <label className="form-label">Название *</label>
                            <input className="form-input" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} autoFocus />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Тип</label>
                            <select className="form-input" value={form.primary_type} onChange={e => setForm(f => ({ ...f, primary_type: e.target.value as CounterpartyType }))}>
                                {COUNTERPARTY_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label">ИНН</label>
                            <input className="form-input" value={form.inn ?? ''} onChange={e => setForm(f => ({ ...f, inn: e.target.value || null }))} placeholder="10 или 12 цифр" />
                        </div>
                        <div className="form-group">
                            <label className="form-label">КПП</label>
                            <input className="form-input" value={form.kpp ?? ''} onChange={e => setForm(f => ({ ...f, kpp: e.target.value || null }))} placeholder="9 цифр" />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Номер контракта</label>
                            <input className="form-input" value={form.contract_number ?? ''} onChange={e => setForm(f => ({ ...f, contract_number: e.target.value || null }))} />
                        </div>
                        <div className="form-group" style={{ gridColumn: '1/-1' }}>
                            <label className="form-label">Примечания</label>
                            <input className="form-input" value={form.notes ?? ''} onChange={e => setForm(f => ({ ...f, notes: e.target.value || null }))} />
                        </div>
                    </div>
                    {formError && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 8 }}>{formError}</div>}
                    <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
                        <button className="btn btn-primary btn-sm" onClick={handleCreate} disabled={saving}>
                            {saving ? 'Сохранение...' : 'Сохранить'}
                        </button>
                        <button className="btn btn-secondary btn-sm" onClick={() => { setShowCreate(false); setForm(emptyForm()); setFormError(''); }}>
                            Отмена
                        </button>
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
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                    Загрузка...
                </div>
            ) : items.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>🏢</div>
                    <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Контрагентов пока нет</div>
                    <div style={{ color: 'var(--color-text-dim)', marginBottom: 20 }}>
                        {search || typeFilter
                            ? 'По заданным фильтрам ничего не найдено. Попробуйте изменить условия.'
                            : 'Добавьте контрагента вручную или запустите backfill из транзакций.'}
                    </div>
                    <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
                        + Добавить контрагента
                    </button>
                </div>
            ) : (
                <div data-testid="counterparty-list">
                    <TanStackDataTable
                        columns={columns}
                        data={visibleItems}
                        emptyText="Нет контрагентов"
                        enableSorting
                        enablePagination
                    />
                </div>
            )}
        </div>
    );
}
