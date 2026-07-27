'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { Loan, LoanCreate, LoanEntityType, LoanDirection, LoanStatus, CounterpartyListItem } from '@/types/api';

const today = () => new Date().toISOString().slice(0, 10);

type Mode = 'create' | 'edit';

interface FormState {
    counterparty_id: number;
    direction: LoanDirection;
    principal: string;
    currency: string;
    ratePct: string;
    entity_type: '' | LoanEntityType;
    lender_bank: string;
    contract_number: string;
    contract_date: string;
    start_date: string;
    maturity_date: string;
    status: LoanStatus;
    notes: string;
}

function fromLoan(loan?: Loan): FormState {
    return {
        counterparty_id: loan?.counterparty_id ?? 0,
        direction: loan?.direction ?? 'INCOMING',
        principal: loan ? String(Number(loan.principal)) : '',
        currency: loan?.currency ?? 'RUB',
        ratePct: loan?.rate != null ? String(+(Number(loan.rate) * 100).toFixed(2)) : '',
        entity_type: loan?.entity_type ?? '',
        lender_bank: loan?.lender_bank ?? '',
        contract_number: loan?.contract_number ?? '',
        contract_date: loan?.contract_date ?? today(),
        start_date: loan?.start_date ?? today(),
        maturity_date: loan?.maturity_date ?? '',
        status: loan?.status ?? 'ACTIVE',
        notes: loan?.notes ?? '',
    };
}

export default function LoanFormModal({
    mode, loan, onClose, onSaved,
}: { mode: Mode; loan?: Loan; onClose: () => void; onSaved: () => void }) {
    const [form, setForm] = useState<FormState>(fromLoan(loan));
    const [cps, setCps] = useState<CounterpartyListItem[]>([]);
    const [cpSearch, setCpSearch] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        if (mode !== 'create') return;
        // Server-side search (debounced) so borrowers beyond the first 500 stay reachable;
        // surface load failures instead of silently swallowing them.
        const handle = setTimeout(() => {
            api.listCounterparties({ q: cpSearch || undefined, limit: 500 })
                .then((r) => setCps(r.items))
                .catch(() => setError('Не удалось загрузить список заёмщиков'));
        }, 250);
        return () => clearTimeout(handle);
    }, [mode, cpSearch]);

    const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }));

    const submit = async () => {
        if (mode === 'create' && !form.counterparty_id) { setError('Выберите заёмщика'); return; }
        if (!form.contract_number.trim()) { setError('Укажите номер договора'); return; }
        const principal = Number(form.principal);
        if (!principal || principal <= 0) { setError('Укажите сумму займа'); return; }
        setSaving(true);
        setError('');
        const rate = form.ratePct ? Number(form.ratePct) / 100 : null;
        try {
            if (mode === 'create') {
                const payload: LoanCreate = {
                    counterparty_id: form.counterparty_id,
                    direction: form.direction,
                    principal,
                    currency: form.currency,
                    rate,
                    contract_number: form.contract_number.trim(),
                    contract_date: form.contract_date,
                    start_date: form.start_date,
                    maturity_date: form.maturity_date || null,
                    status: form.status,
                    entity_type: form.entity_type || null,
                    lender_bank: form.lender_bank || null,
                    notes: form.notes || null,
                };
                await api.createLoan(payload);
            } else if (loan) {
                await api.updateLoan(loan.id, {
                    direction: form.direction,
                    principal,
                    currency: form.currency,
                    rate,
                    contract_number: form.contract_number.trim(),
                    contract_date: form.contract_date,
                    start_date: form.start_date,
                    maturity_date: form.maturity_date || null,
                    status: form.status,
                    entity_type: form.entity_type || null,
                    lender_bank: form.lender_bank || null,
                    notes: form.notes || null,
                });
            }
            onSaved();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    const filteredCps = cps.filter((c) =>
        !cpSearch || c.name.toLowerCase().includes(cpSearch.toLowerCase()) || (c.inn ?? '').includes(cpSearch)
    );

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-solid modal-card-wide" style={{ maxHeight: '90vh', overflow: 'auto' }} onClick={(e) => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>{mode === 'create' ? 'Новый займ' : 'Редактировать займ'}</h3>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    {mode === 'create' && (
                        <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                            <label className="form-label">Заёмщик *</label>
                            <input className="form-input" placeholder="Поиск заёмщика…" value={cpSearch} onChange={(e) => setCpSearch(e.target.value)} style={{ marginBottom: 6 }} />
                            <select className="form-input" value={form.counterparty_id || ''} onChange={(e) => set('counterparty_id', Number(e.target.value))}>
                                <option value="">— выберите —</option>
                                {filteredCps.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                            </select>
                        </div>
                    )}

                    <Field label="Сумма займа *">
                        <input type="number" className="form-input" value={form.principal} onChange={(e) => set('principal', e.target.value)} />
                    </Field>
                    <Field label="Валюта">
                        <select className="form-input" value={form.currency} onChange={(e) => set('currency', e.target.value)}>
                            <option value="RUB">RUB</option>
                            <option value="CNY">CNY</option>
                        </select>
                    </Field>
                    <Field label="Ставка, % годовых">
                        <input type="number" step="0.01" className="form-input" placeholder="28" value={form.ratePct} onChange={(e) => set('ratePct', e.target.value)} />
                    </Field>
                    <Field label="На кого оформлен">
                        <select className="form-input" value={form.entity_type} onChange={(e) => set('entity_type', e.target.value as '' | LoanEntityType)}>
                            <option value="">— не указано —</option>
                            <option value="PHYSICAL">Физлицо</option>
                            <option value="IP">ИП</option>
                        </select>
                    </Field>
                    <Field label="Номер договора *">
                        <input className="form-input" value={form.contract_number} onChange={(e) => set('contract_number', e.target.value)} />
                    </Field>
                    <Field label="Банк выплат">
                        <input className="form-input" placeholder="сбер / альфа" value={form.lender_bank} onChange={(e) => set('lender_bank', e.target.value)} />
                    </Field>
                    <Field label="Дата начала">
                        <input type="date" className="form-input" value={form.start_date} onChange={(e) => set('start_date', e.target.value)} />
                    </Field>
                    <Field label="Дата возврата">
                        <input type="date" className="form-input" value={form.maturity_date} onChange={(e) => set('maturity_date', e.target.value)} />
                    </Field>
                    <Field label="Статус">
                        <select className="form-input" value={form.status} onChange={(e) => set('status', e.target.value as LoanStatus)}>
                            <option value="ACTIVE">Активный</option>
                            <option value="CLOSED">Закрыт</option>
                            <option value="DEFAULTED">Дефолт</option>
                        </select>
                    </Field>
                    <Field label="Направление">
                        <select className="form-input" value={form.direction} onChange={(e) => set('direction', e.target.value as LoanDirection)}>
                            <option value="INCOMING">Получен (мы заняли)</option>
                            <option value="OUTGOING">Выдан (нам должны)</option>
                            <option value="AFFILIATED">Аффилированный</option>
                        </select>
                    </Field>
                    <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                        <label className="form-label">Заметки</label>
                        <textarea className="form-input" rows={2} value={form.notes} onChange={(e) => set('notes', e.target.value)} />
                    </div>
                </div>

                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 10 }}>{error}</div>}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>Отмена</button>
                    <button className="btn btn-primary btn-sm" disabled={saving} onClick={submit}>{saving ? 'Сохранение…' : 'Сохранить'}</button>
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
