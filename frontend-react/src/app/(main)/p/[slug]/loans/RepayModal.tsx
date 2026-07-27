'use client';

import { useState } from 'react';
import { api } from '@/lib/api';
import type { Loan } from '@/types/api';
import { money, ratePct } from './loanFmt';

/** Возврат тела займа: полный (закрывает займ) или частичный. */
export default function RepayModal({
    loan, onClose, onDone,
}: { loan: Loan; onClose: () => void; onDone: () => void }) {
    const remaining = Number(loan.remaining_principal ?? loan.principal);
    const today = new Date().toISOString().slice(0, 10);
    const [amount, setAmount] = useState(String(remaining));
    const [paidAt, setPaidAt] = useState(loan.maturity_date && loan.maturity_date <= today ? loan.maturity_date : today);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const value = Number(amount);
    const isFull = value >= remaining;
    const invalid = !Number.isFinite(value) || value <= 0 || value > remaining;

    const submit = async () => {
        setSaving(true);
        setError('');
        try {
            await api.repayLoan(loan.id, { amount: value, paid_at: paidAt || null, close: true });
            onDone();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка возврата');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-solid" onClick={(e) => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>Возврат займа</h3>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
                </div>
                <p style={{ color: 'var(--color-text-dim)', fontSize: 13, marginTop: 0 }}>
                    Договор <b style={{ fontFamily: 'monospace' }}>{loan.contract_number}</b> ({ratePct(loan.rate)}),
                    остаток тела {money(remaining)} ₽.
                </p>

                <div className="form-group">
                    <label className="form-label">Сумма возврата</label>
                    <input
                        type="number"
                        className="form-input"
                        value={amount}
                        onChange={(e) => setAmount(e.target.value)}
                    />
                    <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setAmount(String(remaining))}>
                            Весь остаток
                        </button>
                        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setAmount(String(Math.round(remaining / 2)))}>
                            Половина
                        </button>
                    </div>
                </div>

                <div className="form-group">
                    <label className="form-label">Дата возврата</label>
                    <input type="date" className="form-input" value={paidAt} onChange={(e) => setPaidAt(e.target.value)} />
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 6 }}>
                        Проценты за период начисляются по этот день включительно.
                    </div>
                </div>

                <div style={{ padding: '10px 14px', borderRadius: 12, background: 'var(--color-bg-card)', fontSize: 13 }}>
                    {invalid
                        ? <span style={{ color: 'var(--color-danger)' }}>Сумма должна быть от 1 до {money(remaining)} ₽</span>
                        : isFull
                            ? <>Возврат полный — займ будет <b>закрыт</b>.</>
                            : <>Частичный возврат — займ останется активным, тело станет {money(remaining - value)} ₽.</>}
                </div>

                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 10 }}>{error}</div>}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>Отмена</button>
                    <button className="btn btn-primary btn-sm" disabled={saving || invalid} onClick={submit}>
                        {saving ? 'Сохранение…' : isFull ? 'Вернуть и закрыть' : 'Записать возврат'}
                    </button>
                </div>
            </div>
        </div>
    );
}
