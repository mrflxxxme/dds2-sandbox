'use client';

import { useState } from 'react';
import { api } from '@/lib/api';
import type { LoanDetail } from '@/types/api';
import { money, ratePct } from './loanFmt';

const overlay: React.CSSProperties = {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex',
    alignItems: 'center', justifyContent: 'center', zIndex: 1000, padding: 16,
};

export default function ExtendModal({
    loan, onClose, onExtended,
}: { loan: LoanDetail; onClose: () => void; onExtended: (newId: number) => void }) {
    const remaining = Number(loan.remaining_principal ?? loan.principal);
    const [ratePctVal, setRatePctVal] = useState(loan.rate != null ? String(+(Number(loan.rate) * 100).toFixed(2)) : '');
    const [maturity, setMaturity] = useState('');
    const [contract, setContract] = useState('');
    const [principal, setPrincipal] = useState(String(remaining));
    const [recordRepay, setRecordRepay] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const submit = async () => {
        setSaving(true);
        setError('');
        try {
            const res = await api.extendLoan(loan.id, {
                new_rate: ratePctVal ? Number(ratePctVal) / 100 : null,
                new_maturity_date: maturity || null,
                new_contract_number: contract.trim() || null,
                principal: principal ? Number(principal) : null,
                record_repayment: recordRepay,
            });
            onExtended(res.id);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка продления');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div style={overlay} onClick={onClose}>
            <div className="glass-card" style={{ maxWidth: 480, width: '92%' }} onClick={(e) => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>Продлить займ</h3>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
                </div>
                <p style={{ color: 'var(--color-text-dim)', fontSize: 13, marginTop: 0 }}>
                    Текущий договор <b style={{ fontFamily: 'monospace' }}>{loan.contract_number}</b> ({ratePct(loan.rate)}) будет закрыт,
                    создастся новый на остаток {money(remaining)} ₽ с новой ставкой/сроком.
                </p>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <Field label="Новая ставка, % год.">
                        <input type="number" step="0.01" className="form-input" value={ratePctVal} onChange={(e) => setRatePctVal(e.target.value)} />
                    </Field>
                    <Field label="Сумма (тело)">
                        <input type="number" className="form-input" value={principal} onChange={(e) => setPrincipal(e.target.value)} />
                    </Field>
                    <Field label="Новый номер (авто)">
                        <input className="form-input" placeholder={`${loan.contract_number}-…`} value={contract} onChange={(e) => setContract(e.target.value)} />
                    </Field>
                    <Field label="Новый срок возврата">
                        <input type="date" className="form-input" value={maturity} onChange={(e) => setMaturity(e.target.value)} />
                    </Field>
                </div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, fontSize: 13, cursor: 'pointer' }}>
                    <input type="checkbox" checked={recordRepay} onChange={(e) => setRecordRepay(e.target.checked)} />
                    Отметить возврат тела по старому договору (закрытие)
                </label>

                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 10 }}>{error}</div>}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>Отмена</button>
                    <button className="btn btn-primary btn-sm" disabled={saving} onClick={submit}>{saving ? 'Продление…' : 'Продлить'}</button>
                </div>
            </div>
        </div>
    );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div className="form-group">
            <label className="form-label">{label}</label>
            {children}
        </div>
    );
}
