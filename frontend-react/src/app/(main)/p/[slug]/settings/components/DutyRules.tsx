'use client';
import { useEffect, useState, useMemo, useCallback } from 'react';
import { api } from '@/lib/api';
import { exportToExcel } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { Nomenclature, MissingAreaBarcode, MissingWeightBarcode, DutyException } from '@/types/api';
import { BarcodeMetricManager } from './BarcodeMetricManager';

export function DutyRules() {
    const [rules, setRules] = useState<any[]>([]);
    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);
    const [missingArea, setMissingArea] = useState<MissingAreaBarcode[]>([]);
    const [missingWeight, setMissingWeight] = useState<MissingWeightBarcode[]>([]);
    const [categories, setCategories] = useState<string[]>([]);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ subject: '', basis: 'INVOICE', rate: '0', util_collect_rub: '0', note: '' });
    const [msg, setMsg] = useState('');
    const [vatRate, setVatRate] = useState<string>('22');
    const [vatSaving, setVatSaving] = useState(false);

    // Article-level duty exceptions
    const [exceptions, setExceptions] = useState<DutyException[]>([]);
    const [showExcForm, setShowExcForm] = useState(false);
    const [excForm, setExcForm] = useState({ article_seller: '', basis: 'INVOICE', rate: '0', note: '' });

    const loadNomenclature = useCallback(async () => {
        try {
            setNomenclature(await api.getNomenclature());
        } catch { }
    }, []);

    const loadMissingArea = useCallback(async () => {
        try {
            setMissingArea(await api.getMissingAreaBarcodes());
        } catch { }
    }, []);

    const loadMissingWeight = useCallback(async () => {
        try {
            setMissingWeight(await api.getMissingWeightBarcodes());
        } catch { }
    }, []);

    const loadCategories = useCallback(async () => {
        try {
            setCategories(await api.getNomenclatureSubjects());
        } catch { }
    }, []);

    useEffect(() => {
        loadRules(); loadExceptions(); loadNomenclature(); loadMissingArea(); loadMissingWeight(); loadCategories(); loadVatRate();
    }, [loadNomenclature, loadMissingArea, loadMissingWeight, loadCategories]);
    useEffect(() => { if (!msg) return; const t = setTimeout(() => setMsg(''), 5000); return () => clearTimeout(t); }, [msg]);

    const loadRules = async () => { try { setRules(await api.getDutyRules()); } catch { } };
    const loadExceptions = async () => { try { setExceptions(await api.getDutyExceptions()); } catch { } };
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

    const saveExc = async () => {
        if (!excForm.article_seller.trim()) { setMsg('❌ Укажите артикул'); return; }
        try {
            await api.addDutyException({ article_seller: excForm.article_seller.trim(), basis: excForm.basis, rate: parseFloat(excForm.rate) || 0, note: excForm.note || null });
            setMsg('✅ Исключение сохранено!'); setShowExcForm(false); setExcForm({ article_seller: '', basis: 'INVOICE', rate: '0', note: '' }); loadExceptions();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };
    const delExc = async (id: number) => { if (!confirm('Удалить исключение?')) return; try { await api.deleteDutyException(id); loadExceptions(); } catch (e: any) { setMsg(e.message); } };
    const editExc = (e: DutyException) => {
        setExcForm({ article_seller: e.article_seller, basis: e.basis, rate: String(e.rate), note: e.note || '' }); setShowExcForm(true);
    };

    const basisLabels: Record<string, string> = { INVOICE: 'От инвойса (%)', WEIGHT: 'От веса (€/кг)', AREA: 'За м² (€/м²)' };
    const ruledSubjects = new Set(rules.map(r => r.subject));
    const unruled = categories.filter(c => !ruledSubjects.has(c));

    // Known seller articles — datalist suggestions for the exception form
    const articleOptions = useMemo(() => {
        const set = new Set<string>();
        for (const n of nomenclature) { if (n.article_seller) set.add(n.article_seller); }
        return [...set].sort();
    }, [nomenclature]);

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

    const excColumns: Column[] = useMemo(() => [
        { key: 'id', label: 'ID', render: (v: any) => <span style={{ fontSize: 12 }}>{v}</span> },
        { key: 'article_seller', label: 'Артикул', render: (v: any) => <span style={{ fontWeight: 500, fontFamily: 'monospace', fontSize: 13 }}>{v}</span> },
        {
            key: 'basis', label: 'Базис',
            render: (v: any) => <span className="badge badge-info" style={{ fontSize: 10 }}>{basisLabels[v] || v}</span>,
        },
        { key: 'rate', label: 'Ставка', render: (v: any) => <span style={{ fontFamily: 'monospace' }}>{v}</span> },
        { key: 'note', label: 'Примечание', render: (v: any) => <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>{v || '—'}</span> },
        {
            key: '_actions', label: '',
            sortable: false,
            render: (_v: any, row: any) => (
                <div style={{ display: 'flex', gap: 4 }}>
                    <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => editExc(row)}>✎</button>
                    <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => delExc(row.id)}>✕</button>
                </div>
            ),
        },
    ], []);

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

            {/* Duty Exceptions by Article Card */}
            <div className="glass-card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <div>
                        <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Исключения по артикулам</h3>
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 2 }}>
                            Своя пошлина для конкретных артикулов — приоритетнее правила категории. Утиль-сбор берётся из категории. Исключений: {exceptions.length}
                        </div>
                    </div>
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(exceptions, 'duty_exceptions')}>📥 Excel</button>
                        <button className="btn btn-primary btn-sm" onClick={() => setShowExcForm(!showExcForm)}>+ Добавить исключение</button>
                    </div>
                </div>

                {showExcForm && (
                    <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16 }}>
                        <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Исключение пошлины (по артикулу)</h4>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
                            <div className="form-group">
                                <label className="form-label" style={{ fontSize: 11 }}>Артикул продавца*</label>
                                <input className="form-input" list="exc-article-options" value={excForm.article_seller} onChange={e => setExcForm({ ...excForm, article_seller: e.target.value })} placeholder="Артикул" style={{ fontSize: 13, fontFamily: 'monospace' }} />
                                <datalist id="exc-article-options">{articleOptions.map(a => <option key={a} value={a} />)}</datalist>
                            </div>
                            <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Базис расчёта</label><select className="form-input" value={excForm.basis} onChange={e => setExcForm({ ...excForm, basis: e.target.value })} style={{ fontSize: 13 }}><option value="INVOICE">От инвойса (%)</option><option value="WEIGHT">От веса (€/кг)</option><option value="AREA">За м² (€/м²)</option></select></div>
                            <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Ставка</label><input className="form-input" type="number" step="0.01" value={excForm.rate} onChange={e => setExcForm({ ...excForm, rate: e.target.value })} style={{ fontSize: 13 }} /></div>
                            <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Примечание</label><input className="form-input" value={excForm.note} onChange={e => setExcForm({ ...excForm, note: e.target.value })} style={{ fontSize: 13 }} /></div>
                        </div>
                        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                            <button className="btn btn-primary btn-sm" onClick={saveExc}>💾 Сохранить</button>
                            <button className="btn btn-secondary btn-sm" onClick={() => setShowExcForm(false)}>Отмена</button>
                        </div>
                    </div>
                )}

                <TanStackDataTable
                    columns={excColumns}
                    data={exceptions}
                    emptyText="Нет исключений. Добавьте, если у отдельных артикулов внутри категории своя пошлина."
                    enableSorting
                    enablePagination={false}
                />
            </div>

            {/* Area per Barcode */}
            <BarcodeMetricManager
                metricKey="area_m2"
                title="Площадь по баркодам (м²)"
                unit="м²"
                basisLabel="За м²"
                hint="Для расчёта пошлины по площади (базис «За м²»)."
                nomenclature={nomenclature}
                missing={missingArea}
                onBulkUpdate={(items) => api.bulkUpdateNomenclatureArea(items.map(i => ({ barcode: i.barcode, area_m2: i.value })))}
                onReload={() => { loadNomenclature(); loadMissingArea(); }}
                setMsg={setMsg}
                exportName="barcode_area_m2"
            />

            {/* Weight per Barcode */}
            <BarcodeMetricManager
                metricKey="weight_kg"
                title="Вес по баркодам (кг)"
                unit="кг"
                basisLabel="От веса"
                hint="Для расчёта пошлины по весу (базис «От веса»)."
                nomenclature={nomenclature}
                missing={missingWeight}
                onBulkUpdate={(items) => api.bulkUpdateNomenclatureWeight(items.map(i => ({ barcode: i.barcode, weight_kg: i.value })))}
                onReload={() => { loadNomenclature(); loadMissingWeight(); }}
                setMsg={setMsg}
                exportName="barcode_weight_kg"
            />
        </div>
    );
}
