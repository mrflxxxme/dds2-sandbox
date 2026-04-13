'use client';
import { useEffect, useState, useMemo, useCallback } from 'react';
import { api } from '@/lib/api';
import { exportToExcel, formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { Nomenclature } from '@/types/api';

interface AreaRow { barcode: string; area_m2: string }

const EMPTY_AREA_ROW = (): AreaRow => ({ barcode: '', area_m2: '' });

export function DutyRules() {
    const [rules, setRules] = useState<any[]>([]);
    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);
    const [categories, setCategories] = useState<string[]>([]);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ subject: '', basis: 'INVOICE', rate: '0', util_collect_rub: '0', note: '' });
    const [msg, setMsg] = useState('');
    const [vatRate, setVatRate] = useState<string>('22');
    const [vatSaving, setVatSaving] = useState(false);

    // Area paste grid
    const [areaRows, setAreaRows] = useState<AreaRow[]>(() => Array.from({ length: 5 }, EMPTY_AREA_ROW));
    const [areaSaving, setAreaSaving] = useState(false);
    const [editingBarcode, setEditingBarcode] = useState<string | null>(null);
    const [editValue, setEditValue] = useState('');

    const loadNomenclature = useCallback(async () => {
        try {
            const nom = await api.getNomenclature();
            setNomenclature(nom);
            setCategories([...new Set(nom.map((n: Nomenclature) => n.subject).filter(Boolean))].sort() as string[]);
        } catch { }
    }, []);

    useEffect(() => { loadRules(); loadNomenclature(); loadVatRate(); }, [loadNomenclature]);
    useEffect(() => { if (!msg) return; const t = setTimeout(() => setMsg(''), 5000); return () => clearTimeout(t); }, [msg]);

    const loadRules = async () => { try { setRules(await api.getDutyRules()); } catch { } };
    const loadVatRate = async () => { try { const res = await api.getVatRate(); setVatRate(String(res.vat_rate)); } catch { } };

    const saveVatRate = async () => {
        setVatSaving(true);
        try { await api.setVatRate(parseFloat(vatRate) || 22); setMsg('✅ Ставка НДС сохранена!'); } catch (e: any) { setMsg(`❌ ${e.message}`); }
        setVatSaving(false);
    };
    const save = async () => {
        if (!form.subject) { setMsg('❌ Выберите категорию'); return; }
        try {
            await api.addDutyRule({ subject: form.subject, basis: form.basis, rate: parseFloat(form.rate) || 0, util_collect_rub: parseFloat(form.util_collect_rub) || 0, note: form.note || null });
            setMsg('✅ Правило сохранено!'); setShowForm(false); setForm({ subject: '', basis: 'INVOICE', rate: '0', util_collect_rub: '0', note: '' }); loadRules();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };
    const del = async (id: number) => { if (!confirm('Удалить правило?')) return; try { await api.deleteDutyRule(id); loadRules(); } catch (e: any) { setMsg(e.message); } };
    const editRule = (r: any) => {
        setForm({ subject: r.subject, basis: r.basis, rate: String(r.rate), util_collect_rub: String(r.util_collect_rub), note: r.note || '' }); setShowForm(true);
    };

    const basisLabels: Record<string, string> = { INVOICE: 'От инвойса (%)', WEIGHT: 'От веса (€/кг)', AREA: 'За м² (€/м²)' };
    const ruledSubjects = new Set(rules.map(r => r.subject));
    const unruled = categories.filter(c => !ruledSubjects.has(c));

    // Nomenclature items with area_m2
    const nomMap = useMemo(() => {
        const m: Record<string, Nomenclature> = {};
        for (const n of nomenclature) { if (n.barcode) m[n.barcode] = n; }
        return m;
    }, [nomenclature]);

    const itemsWithArea = useMemo(() =>
        nomenclature.filter(n => n.area_m2 && n.area_m2 > 0),
    [nomenclature]);

    // Area paste handlers
    const updateAreaRow = (idx: number, field: keyof AreaRow, value: string) => {
        setAreaRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: value };
            if (idx === next.length - 1 && value) {
                next.push(EMPTY_AREA_ROW());
            }
            return next;
        });
    };

    const handleAreaPaste = (e: React.ClipboardEvent, idx: number, field: keyof AreaRow) => {
        const text = e.clipboardData.getData('text');
        if (text.includes('\t') || text.includes('\n')) {
            e.preventDefault();
            const lines = text.trim().split('\n').filter(Boolean);
            setAreaRows(prev => {
                const next = [...prev];
                for (let i = 0; i < lines.length; i++) {
                    const cols = lines[i].split('\t');
                    const rowIdx = idx + i;
                    if (rowIdx >= next.length) next.push(EMPTY_AREA_ROW());
                    next[rowIdx] = {
                        barcode: (cols[0] || '').trim(),
                        area_m2: (cols[1] || '').replace(',', '.').trim(),
                    };
                }
                if (next[next.length - 1].barcode) next.push(EMPTY_AREA_ROW());
                return next;
            });
        }
    };

    const validAreaRows = areaRows.filter(r => r.barcode.trim() && r.area_m2.trim());

    const saveArea = async () => {
        if (!validAreaRows.length) { setMsg('❌ Нет данных для сохранения'); return; }
        setAreaSaving(true);
        try {
            const items = validAreaRows.map(r => ({
                barcode: r.barcode.trim(),
                area_m2: parseFloat(r.area_m2.replace(',', '.')) || 0,
            }));
            const res = await api.bulkUpdateNomenclatureArea(items);
            const parts = [`✅ Обновлено: ${res.updated}`];
            if (res.not_found.length) parts.push(`Не найдено: ${res.not_found.join(', ')}`);
            setMsg(parts.join(' | '));
            setAreaRows(Array.from({ length: 5 }, EMPTY_AREA_ROW));
            loadNomenclature();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
        setAreaSaving(false);
    };

    // Inline edit / delete for existing area values
    const startEdit = (barcode: string, area: number) => { setEditingBarcode(barcode); setEditValue(String(area)); };
    const cancelEdit = () => { setEditingBarcode(null); setEditValue(''); };
    const saveEdit = async () => {
        if (!editingBarcode) return;
        const area = parseFloat(editValue.replace(',', '.'));
        if (isNaN(area) || area <= 0) { setMsg('❌ Введите корректную площадь'); return; }
        try {
            await api.bulkUpdateNomenclatureArea([{ barcode: editingBarcode, area_m2: area }]);
            setMsg('✅ Площадь обновлена');
            setEditingBarcode(null);
            loadNomenclature();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };
    const deleteArea = async (barcode: string) => {
        if (!confirm('Удалить площадь для этого баркода?')) return;
        try {
            await api.bulkUpdateNomenclatureArea([{ barcode, area_m2: 0 }]);
            setMsg('✅ Площадь удалена');
            loadNomenclature();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };
    const loadAllToGrid = () => {
        const rows: AreaRow[] = itemsWithArea.map(n => ({
            barcode: n.barcode || '',
            area_m2: String(n.area_m2 || ''),
        }));
        rows.push(EMPTY_AREA_ROW());
        setAreaRows(rows);
    };

    const columns: Column[] = useMemo(() => [
        { key: 'id', label: 'ID', render: (v: any) => <span style={{ fontSize: 12 }}>{v}</span> },
        { key: 'subject', label: 'Категория', render: (v: any) => <span style={{ fontWeight: 500 }}>{v}</span> },
        {
            key: 'basis', label: 'Базис',
            render: (v: any) => <span className="badge badge-info" style={{ fontSize: 10 }}>{basisLabels[v] || v}</span>,
        },
        { key: 'rate', label: 'Ставка', render: (v: any) => <span style={{ fontFamily: 'monospace' }}>{v}</span> },
        { key: 'util_collect_rub', label: 'Утиль ₽', render: (v: any) => <span style={{ fontFamily: 'monospace' }}>{v}</span> },
        { key: 'note', label: 'Примечание', render: (v: any) => <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>{v || '—'}</span> },
        {
            key: '_actions', label: '',
            sortable: false,
            render: (_v: any, row: any) => (
                <div style={{ display: 'flex', gap: 4 }}>
                    <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => editRule(row)}>✎</button>
                    <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(row.id)}>✕</button>
                </div>
            ),
        },
    ], []);

    const areaColumns: Column[] = [
        { key: 'barcode', label: 'Баркод', render: (v: any) => <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{v}</span> },
        { key: 'subject', label: 'Категория', render: (v: any) => <span style={{ fontSize: 13 }}>{v || '—'}</span> },
        { key: 'article_seller', label: 'Артикул', render: (v: any) => <span style={{ fontSize: 13 }}>{v || '—'}</span> },
        {
            key: 'area_m2', label: 'Площадь м²',
            render: (v: any, row: any) => editingBarcode === row.barcode ? (
                <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                    <input
                        className="form-input"
                        value={editValue}
                        onChange={e => setEditValue(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit(); }}
                        autoFocus
                        style={{ width: 100, fontSize: 13, fontFamily: 'monospace', textAlign: 'right', padding: '4px 8px' }}
                    />
                    <button className="btn btn-success btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={saveEdit}>✓</button>
                    <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={cancelEdit}>✕</button>
                </div>
            ) : (
                <span style={{ fontFamily: 'monospace', fontWeight: 600, cursor: 'pointer' }} onClick={() => startEdit(row.barcode, v)}>{formatNumber(v)}</span>
            ),
        },
        {
            key: '_actions', label: '',
            sortable: false,
            render: (_v: any, row: any) => editingBarcode === row.barcode ? null : (
                <div style={{ display: 'flex', gap: 4 }}>
                    <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => startEdit(row.barcode, row.area_m2)}>✎</button>
                    <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => deleteArea(row.barcode)}>✕</button>
                </div>
            ),
        },
    ];

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* Duty Rules Card */}
            <div className="glass-card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <div>
                        <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Пошлина / Утиль / НДС</h3>
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 2 }}>Категорий: {categories.length} | С правилами: {rules.length} | Без правил: {unruled.length}</div>
                    </div>
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(rules, 'duty_rules')}>📥 Excel</button>
                        <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Добавить правило</button>
                    </div>
                </div>
                {msg && <div style={{ fontSize: 13, marginBottom: 8, padding: '6px 12px', borderRadius: 6, background: msg.startsWith('✅') ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)', color: msg.startsWith('✅') ? 'var(--color-success)' : 'var(--color-danger)' }}>{msg} <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span></div>}

                {/* VAT Rate */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, padding: '12px 16px', borderRadius: 8, background: 'var(--color-bg-input)', border: '1px solid var(--color-border)' }}>
                    <span style={{ fontSize: 14, fontWeight: 600 }}>📋 НДС (для всех категорий):</span>
                    <input className="form-input" type="number" step="0.01" value={vatRate} onChange={e => setVatRate(e.target.value)} onKeyDown={e => e.key === 'Enter' && saveVatRate()} style={{ width: 80, fontSize: 14, textAlign: 'center' }} />
                    <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>%</span>
                    <button className="btn btn-primary btn-sm" onClick={saveVatRate} disabled={vatSaving} style={{ fontSize: 12 }}>{vatSaving ? '⏳' : '💾'} Сохранить</button>
                </div>

                {showForm && (
                    <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16 }}>
                        <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Правило пошлины</h4>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12 }}>
                            <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Категория (subject)*</label><select className="form-input" value={form.subject} onChange={e => setForm({ ...form, subject: e.target.value })} style={{ fontSize: 13 }}><option value="">— Выбрать —</option>{categories.map(c => <option key={c} value={c}>{c}</option>)}</select></div>
                            <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Базис расчёта</label><select className="form-input" value={form.basis} onChange={e => setForm({ ...form, basis: e.target.value })} style={{ fontSize: 13 }}><option value="INVOICE">От инвойса (%)</option><option value="WEIGHT">От веса (€/кг)</option><option value="AREA">За м² (€/м²)</option></select></div>
                            <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Ставка</label><input className="form-input" type="number" step="0.01" value={form.rate} onChange={e => setForm({ ...form, rate: e.target.value })} style={{ fontSize: 13 }} /></div>
                            <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Утиль. сбор (₽)</label><input className="form-input" type="number" step="0.01" value={form.util_collect_rub} onChange={e => setForm({ ...form, util_collect_rub: e.target.value })} style={{ fontSize: 13 }} /></div>
                            <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Примечание</label><input className="form-input" value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} style={{ fontSize: 13 }} /></div>
                        </div>
                        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                            <button className="btn btn-primary btn-sm" onClick={save}>💾 Сохранить</button>
                            <button className="btn btn-secondary btn-sm" onClick={() => setShowForm(false)}>Отмена</button>
                        </div>
                    </div>
                )}

                <TanStackDataTable
                    columns={columns}
                    data={rules}
                    emptyText="Нет правил. Добавьте правила для категорий из Номенклатуры."
                    enableSorting
                    enablePagination={false}
                />

                {unruled.length > 0 && (
                    <div style={{ marginTop: 16, padding: 12, background: 'rgba(245,158,11,0.08)', borderRadius: 8, border: '1px solid rgba(245,158,11,0.2)' }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-warning)', marginBottom: 8 }}>⚠️ Категории без правил ({unruled.length}):</div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                            {unruled.slice(0, 30).map(c => (<span key={c} className="badge badge-warning" style={{ fontSize: 10, cursor: 'pointer' }} onClick={() => { setForm({ ...form, subject: c }); setShowForm(true); }}>{c}</span>))}
                            {unruled.length > 30 && <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>...и ещё {unruled.length - 30}</span>}
                        </div>
                    </div>
                )}
            </div>

            {/* Area per Barcode Card */}
            <div className="glass-card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <div>
                        <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Площадь по баркодам (м²)</h3>
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 2 }}>
                            Для расчёта пошлины по площади (базис &laquo;За м²&raquo;). Баркодов с площадью: {itemsWithArea.length}
                        </div>
                    </div>
                    {itemsWithArea.length > 0 && (
                        <div style={{ display: 'flex', gap: 8 }}>
                            <button className="btn btn-secondary btn-sm" onClick={loadAllToGrid}>✎ Редактировать все</button>
                            <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(itemsWithArea.map(n => ({ barcode: n.barcode, subject: n.subject, article_seller: n.article_seller, area_m2: n.area_m2 })), 'barcode_area_m2')}>📥 Excel</button>
                        </div>
                    )}
                </div>

                {/* Paste grid */}
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16 }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                        Вставьте из Excel: Баркод, Площадь м²
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '32px 1fr 120px', gap: '4px 8px', alignItems: 'center' }}>
                        <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-dim)' }}>#</div>
                        <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-dim)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>БАРКОД</div>
                        <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-dim)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>ПЛОЩАДЬ М²</div>

                        {areaRows.map((row, i) => {
                            const nom = row.barcode.trim() ? nomMap[row.barcode.trim()] : null;
                            const isFound = row.barcode.trim() && nom;
                            const isNotFound = row.barcode.trim() && !nom;
                            return (
                                <div key={i} style={{ display: 'contents' }}>
                                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', textAlign: 'center' }}>{i + 1}</div>
                                    <div style={{ position: 'relative' }}>
                                        <input
                                            className="form-input"
                                            value={row.barcode}
                                            onChange={e => updateAreaRow(i, 'barcode', e.target.value)}
                                            onPaste={e => handleAreaPaste(e, i, 'barcode')}
                                            placeholder="Баркод"
                                            style={{
                                                fontSize: 13, fontFamily: 'monospace', padding: '6px 10px',
                                                borderColor: isFound ? 'var(--color-success)' : isNotFound ? 'var(--color-danger)' : undefined,
                                                background: isFound ? 'rgba(16,185,129,0.05)' : isNotFound ? 'rgba(239,68,68,0.05)' : undefined,
                                            }}
                                        />
                                        {isFound && nom && (
                                            <div style={{ fontSize: 10, color: 'var(--color-text-dim)', marginTop: 1 }}>
                                                {nom.subject} {nom.article_seller ? `| ${nom.article_seller}` : ''}
                                            </div>
                                        )}
                                    </div>
                                    <input
                                        className="form-input"
                                        value={row.area_m2}
                                        onChange={e => updateAreaRow(i, 'area_m2', e.target.value)}
                                        placeholder="0.00"
                                        style={{ fontSize: 13, fontFamily: 'monospace', textAlign: 'right', padding: '6px 10px' }}
                                    />
                                </div>
                            );
                        })}
                    </div>
                    <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
                        <button className="btn btn-primary btn-sm" onClick={saveArea} disabled={areaSaving || !validAreaRows.length}>
                            {areaSaving ? '⏳' : '💾'} Сохранить ({validAreaRows.length})
                        </button>
                        <button className="btn btn-secondary btn-sm" onClick={() => setAreaRows(Array.from({ length: 5 }, EMPTY_AREA_ROW))}>Очистить</button>
                    </div>
                </div>

                {/* Table of existing area values */}
                {itemsWithArea.length > 0 && (
                    <TanStackDataTable
                        columns={areaColumns}
                        data={itemsWithArea.map(n => ({ barcode: n.barcode, subject: n.subject, article_seller: n.article_seller, area_m2: n.area_m2 }))}
                        emptyText=""
                        enableSorting
                        enablePagination={false}
                    />
                )}
            </div>
        </div>
    );
}
