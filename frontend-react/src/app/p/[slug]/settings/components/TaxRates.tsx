'use client';
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';

const MONTH_NAMES = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
const QUARTER_MONTHS = [[0,1,2],[3,4,5],[6,7,8],[9,10,11]];
const REGIME_LABELS: Record<string,string> = {
    'usn_income': 'УСН «Доходы»',
    'usn_income_expense_vat': 'УСН «Доходы – Расходы» с фикс. НДС',
};

interface MonthRate { month: number; usn_rate: number; nds_rate: number; cost_as_expense: boolean; }

export function TaxRates() {
    const currentYear = new Date().getFullYear();
    const [year, setYear] = useState(currentYear);
    const [taxRegime, setTaxRegime] = useState('usn_income');
    const [months, setMonths] = useState<MonthRate[]>(Array.from({ length: 12 }, (_, i) => ({ month: i + 1, usn_rate: 0, nds_rate: 0, cost_as_expense: false })));
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState('');
    const [editRegime, setEditRegime] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try { const data = await api.getTaxRates(year); setTaxRegime(data.tax_regime || 'usn_income'); setMonths(data.months || Array.from({ length: 12 }, (_, i) => ({ month: i + 1, usn_rate: 0, nds_rate: 0, cost_as_expense: false }))); } catch { }
        setLoading(false);
    }, [year]);
    useEffect(() => { load(); }, [load]);

    const getMonth = (idx: number): MonthRate => months.find(m => m.month === idx + 1) || { month: idx + 1, usn_rate: 0, nds_rate: 0, cost_as_expense: false };
    const updateRate = (monthIdx: number, field: 'usn_rate' | 'nds_rate', value: number) => { setMonths(prev => prev.map(m => m.month === monthIdx + 1 ? { ...m, [field]: value } : { ...m })); };
    const updateQuarterRate = (qIdx: number, field: 'usn_rate' | 'nds_rate', value: number) => { QUARTER_MONTHS[qIdx].forEach(mi => updateRate(mi, field, value)); };
    const updateCostAsExpense = (quarterIdx: number, value: boolean) => { setMonths(prev => prev.map(m => { const mi = m.month - 1; return QUARTER_MONTHS[quarterIdx].includes(mi) ? { ...m, cost_as_expense: value } : { ...m }; })); };
    const changeRegime = (regime: string) => { setTaxRegime(regime); setEditRegime(false); };
    const quarterAvg = (qIdx: number, field: 'usn_rate' | 'nds_rate'): number => { const vals = QUARTER_MONTHS[qIdx].map(mi => getMonth(mi)[field]); return +(vals.reduce((a, b) => a + b, 0) / 3).toFixed(2); };
    const save = async () => { setSaving(true); try { await api.saveTaxRates({ year, tax_regime: taxRegime, months }); setMsg('✅ Налоговые ставки сохранены!'); } catch (e: any) { setMsg(`❌ ${e.message}`); } setSaving(false); };

    if (loading) return <div style={{ padding: 40, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    const inputStyle = { width: 65, fontSize: 13, textAlign: 'center' as const, background: 'var(--color-bg)', border: '1px solid var(--color-border)', borderRadius: 6, padding: '4px 6px', color: 'var(--color-text)' };

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <h3 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>Налоговые ставки</h3>
                    <select className="form-input" value={year} onChange={e => setYear(+e.target.value)} style={{ width: 90, fontSize: 14 }}>
                        {[currentYear - 1, currentYear, currentYear + 1].map(y => <option key={y} value={y}>{y}</option>)}
                    </select>
                </div>
                <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>{saving ? '⏳ Сохранение...' : '💾 Сохранить'}</button>
            </div>

            {msg && (<div style={{ fontSize: 13, marginBottom: 12, padding: '8px 12px', borderRadius: 6, background: msg.startsWith('✅') ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)', color: msg.startsWith('✅') ? 'var(--color-success)' : 'var(--color-danger)' }}>{msg}<span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span></div>)}

            <div className="glass-card" style={{ padding: 20 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                    <div style={{ width: 36, height: 36, borderRadius: 8, background: 'linear-gradient(135deg, rgba(139,92,246,0.2), rgba(59,130,246,0.2))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16 }}>📋</div>
                    <div>
                        <span style={{ fontWeight: 700, fontSize: 15 }}>{REGIME_LABELS[taxRegime] || taxRegime}</span>
                        {editRegime ? (
                            <select className="form-input" value={taxRegime} onChange={e => changeRegime(e.target.value)} style={{ marginLeft: 12, fontSize: 12, width: 'auto' }} autoFocus>
                                {Object.entries(REGIME_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                            </select>
                        ) : (<span style={{ marginLeft: 12, fontSize: 12, color: 'var(--color-accent)', cursor: 'pointer' }} onClick={() => setEditRegime(true)}>Изменить</span>)}
                    </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
                    {QUARTER_MONTHS.map((qMonths, qIdx) => (
                        <div key={qIdx} style={{ border: '1px solid var(--color-border)', borderRadius: 8, padding: 12, background: 'var(--color-bg-input)' }}>
                            <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr 1fr', gap: 6, alignItems: 'center', marginBottom: 10 }}>
                                <span style={{ fontWeight: 600, fontSize: 13 }}>{qIdx + 1} квартал</span>
                                <div style={{ textAlign: 'center' }}><div style={{ fontSize: 10, color: 'var(--color-text-dim)', marginBottom: 2 }}>УСН, %</div><input type="number" step="0.01" style={inputStyle} value={quarterAvg(qIdx, 'usn_rate') || ''} onChange={e => updateQuarterRate(qIdx, 'usn_rate', +e.target.value || 0)} /></div>
                                <div style={{ textAlign: 'center' }}><div style={{ fontSize: 10, color: 'var(--color-text-dim)', marginBottom: 2 }}>НДС, %</div><input type="number" step="0.01" style={inputStyle} value={quarterAvg(qIdx, 'nds_rate') || ''} onChange={e => updateQuarterRate(qIdx, 'nds_rate', +e.target.value || 0)} /></div>
                            </div>
                            {qMonths.map(mi => {
                                const mr = getMonth(mi);
                                return (
                                    <div key={mi} style={{ display: 'grid', gridTemplateColumns: 'auto 1fr 1fr', gap: 6, alignItems: 'center', padding: '4px 0', borderTop: '1px solid var(--color-border)' }}>
                                        <span style={{ fontSize: 12, color: 'var(--color-text-dim)', minWidth: 70 }}>{MONTH_NAMES[mi]}</span>
                                        <input type="number" step="0.01" style={inputStyle} value={mr.usn_rate || ''} onChange={e => updateRate(mi, 'usn_rate', +e.target.value || 0)} />
                                        <input type="number" step="0.01" style={inputStyle} value={mr.nds_rate || ''} onChange={e => updateRate(mi, 'nds_rate', +e.target.value || 0)} />
                                    </div>
                                );
                            })}
                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, fontSize: 11, color: 'var(--color-text-dim)', cursor: 'pointer' }}>
                                <input type="checkbox" checked={getMonth(qMonths[0]).cost_as_expense} onChange={e => updateCostAsExpense(qIdx, e.target.checked)} />
                                Учитывать себестоимость товара как официальный расход
                            </label>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
