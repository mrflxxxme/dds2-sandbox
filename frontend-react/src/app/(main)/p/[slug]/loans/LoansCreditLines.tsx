'use client';

import { useCallback, useEffect, useState } from 'react';
import TanStackDataTable from '@/components/TanStackDataTable';
import { api } from '@/lib/api';
import type { CreditLineDetail, CreditLineListResponse, CreditLineMovement, LoanFee, LoanFeeKind, LoanRatePeriod } from '@/types/api';
import { fmtDate, money, ratePct } from './loanFmt';

const ACCRUAL_LABEL: Record<string, string> = {
    CALENDAR_MONTH: 'календарный месяц',
    PERIOD_25: 'с 25-го по 25-е',
};

const FEE_KIND_LABEL: Record<LoanFeeKind, string> = {
    ORIGINATION: 'за выдачу',
    LIMIT_SETUP: 'за установление лимита',
    OTHER: 'прочее',
};

/** Кредитные линии (ВКЛ): лимит, выборка траншей, плавающая ставка. */
export default function LoansCreditLines({ nonce, onChanged }: { nonce: number; onChanged: () => void }) {
    const [data, setData] = useState<CreditLineListResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [openId, setOpenId] = useState<number | null>(null);
    const [drawFor, setDrawFor] = useState<CreditLineDetail | null>(null);
    const [rateFor, setRateFor] = useState<CreditLineDetail | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setData(await api.creditLines());
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load, nonce]);

    const done = () => { setDrawFor(null); setRateFor(null); load(); onChanged(); };
    const open = data?.items.find((i) => i.loan_id === openId) ?? null;

    const columns = [
        {
            key: 'contract_number', label: 'Договор',
            render: (v: string, r: CreditLineDetail) => (
                <button
                    className="btn-link"
                    style={{ fontWeight: 600, color: 'var(--color-accent)', background: 'none', border: 0, cursor: 'pointer', padding: 0 }}
                    onClick={() => setOpenId(openId === r.loan_id ? null : r.loan_id)}
                >
                    {v}
                </button>
            ),
        },
        { key: 'counterparty_name', label: 'Банк', render: (v: string | null) => v || '—' },
        {
            key: 'credit_limit', label: 'Лимит', align: 'right' as const,
            getValue: (r: CreditLineDetail) => Number(r.credit_limit ?? 0),
            render: (_v: unknown, r: CreditLineDetail) => `${money(r.credit_limit)} ₽`,
        },
        {
            key: 'drawn', label: 'Выбрано', align: 'right' as const,
            getValue: (r: CreditLineDetail) => Number(r.drawn),
            render: (_v: unknown, r: CreditLineDetail) => (
                <div>
                    <div>{money(r.drawn)} ₽</div>
                    {r.utilization != null && (
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                            {(Number(r.utilization) * 100).toFixed(0)} % лимита
                        </div>
                    )}
                </div>
            ),
        },
        {
            key: 'available', label: 'Свободно', align: 'right' as const,
            getValue: (r: CreditLineDetail) => Number(r.available ?? 0),
            render: (_v: unknown, r: CreditLineDetail) => (
                <span style={Number(r.available ?? 0) === 0 ? { color: 'var(--color-warning)', fontWeight: 600 } : undefined}>
                    {money(r.available)} ₽
                </span>
            ),
        },
        {
            key: 'current_rate', label: 'Ставка', align: 'right' as const,
            getValue: (r: CreditLineDetail) => Number(r.current_rate ?? 0),
            render: (_v: unknown, r: CreditLineDetail) => ratePct(r.current_rate),
        },
        {
            key: 'interest_due_period', label: 'Проценты за период', align: 'right' as const,
            getValue: (r: CreditLineDetail) => Number(r.interest_due_period),
            render: (_v: unknown, r: CreditLineDetail) => (
                <div>
                    <div>{money(r.interest_due_period)} ₽</div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                        набежало {money(r.accrued_interest)} ₽
                    </div>
                </div>
            ),
        },
        {
            key: 'unused_fee_period', label: 'Комиссия за резерв', align: 'right' as const,
            getValue: (r: CreditLineDetail) => Number(r.unused_fee_period),
            render: (_v: unknown, r: CreditLineDetail) => (
                <div>
                    <div>{money(r.unused_fee_period)} ₽</div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                        набежало {money(r.unused_fee_accrued)} ₽ · {ratePct(r.unused_limit_rate)}
                    </div>
                </div>
            ),
        },
        {
            key: 'total_cost_period', label: 'Всего за период', align: 'right' as const,
            getValue: (r: CreditLineDetail) => Number(r.total_cost_period),
            render: (_v: unknown, r: CreditLineDetail) => (
                <div>
                    <div style={{ fontWeight: 700 }}>{money(r.total_cost_period)} ₽</div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                        % {money(r.interest_due_period)} ₽ + ком. {money(Number(r.total_cost_period) - Number(r.interest_due_period))} ₽
                    </div>
                </div>
            ),
        },
        { key: 'maturity_date', label: 'Срок', render: (_v: unknown, r: CreditLineDetail) => fmtDate(r.maturity_date) },
        {
            key: '_act', label: '', sortable: false,
            render: (_v: unknown, r: CreditLineDetail) => (
                <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                    <button className="btn btn-primary btn-sm" onClick={() => setDrawFor(r)}>Выборка</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => setRateFor(r)}>Ставка</button>
                </div>
            ),
        },
    ];

    const movementColumns = [
        { key: 'happened_at', label: 'Дата', render: (_v: unknown, r: CreditLineMovement) => fmtDate(r.happened_at) },
        {
            key: 'kind', label: 'Операция',
            render: (_v: unknown, r: CreditLineMovement) => (
                <span className={`badge ${r.kind === 'DISBURSEMENT' ? 'badge-info' : 'badge-success'}`}>
                    {r.kind === 'DISBURSEMENT' ? 'выборка' : 'погашение'}
                </span>
            ),
        },
        {
            key: 'amount', label: 'Сумма', align: 'right' as const,
            getValue: (r: CreditLineMovement) => Number(r.amount),
            render: (_v: unknown, r: CreditLineMovement) => (
                <span style={{ color: r.kind === 'DISBURSEMENT' ? 'var(--color-text)' : 'var(--color-success)' }}>
                    {r.kind === 'DISBURSEMENT' ? '+' : '−'}{money(r.amount)} ₽
                </span>
            ),
        },
        {
            key: 'balance_after', label: 'Долг после', align: 'right' as const,
            getValue: (r: CreditLineMovement) => Number(r.balance_after),
            render: (_v: unknown, r: CreditLineMovement) => `${money(r.balance_after)} ₽`,
        },
    ];

    const rateColumns = [
        { key: 'valid_from', label: 'Действует с', render: (_v: unknown, r: LoanRatePeriod) => fmtDate(r.valid_from) },
        { key: 'rate', label: 'Ставка', align: 'right' as const, getValue: (r: LoanRatePeriod) => Number(r.rate), render: (_v: unknown, r: LoanRatePeriod) => ratePct(r.rate) },
        { key: 'base_rate', label: 'Ключевая ЦБ', align: 'right' as const, getValue: (r: LoanRatePeriod) => Number(r.base_rate ?? 0), render: (_v: unknown, r: LoanRatePeriod) => ratePct(r.base_rate) },
        { key: 'spread', label: 'Надбавка', align: 'right' as const, getValue: (r: LoanRatePeriod) => Number(r.spread ?? 0), render: (_v: unknown, r: LoanRatePeriod) => ratePct(r.spread) },
    ];

    const feeColumns = [
        {
            key: 'fee_kind', label: 'Вид',
            render: (_v: unknown, r: LoanFee) => FEE_KIND_LABEL[r.fee_kind] ?? r.fee_kind,
        },
        {
            key: 'amount', label: 'Сумма', align: 'right' as const,
            getValue: (r: LoanFee) => Number(r.amount),
            render: (_v: unknown, r: LoanFee) => `${money(r.amount)} ₽`,
        },
        { key: 'charged_at', label: 'Дата', render: (_v: unknown, r: LoanFee) => fmtDate(r.charged_at) },
        {
            key: 'amortize', label: 'Режим',
            render: (_v: unknown, r: LoanFee) => r.amortize ? 'размазана на срок' : 'целиком в месяц уплаты',
        },
        { key: 'note', label: 'Примечание', render: (v: string | null) => v || '—' },
    ];

    return (
        <div>
            {data && data.items.length > 0 && (
                <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Лимит всего</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(data.total_limit)} ₽</div>
                    </div>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Выбрано</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(data.total_drawn)} ₽</div>
                    </div>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Свободно</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(data.total_available)} ₽</div>
                    </div>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Проценты за период</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(data.total_due_period)} ₽</div>
                    </div>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Комиссии за период</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(data.total_fee_period)} ₽</div>
                    </div>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Полная стоимость периода</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(data.total_cost_period)} ₽</div>
                    </div>
                </div>
            )}

            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between' }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
                </div>
            )}

            {loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>
            ) : (data?.items.length ?? 0) === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>💳</div>
                    <div style={{ fontSize: 16, fontWeight: 600 }}>Кредитных линий нет</div>
                    <div style={{ fontSize: 13, color: 'var(--color-text-dim)', marginTop: 6 }}>
                        Заведите займ и укажите вид «Кредитная линия» — появится лимит и выборка траншей.
                    </div>
                </div>
            ) : (
                <TanStackDataTable columns={columns} data={data?.items ?? []} exportName="credit-lines" enableSorting />
            )}

            {open && (
                <div className="glass-card" style={{ padding: 20, marginTop: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, flexWrap: 'wrap', gap: 8 }}>
                        <div style={{ fontSize: 15, fontWeight: 600 }}>
                            Линия {open.contract_number}
                            <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--color-text-dim)', marginLeft: 8 }}>
                                начисление — {ACCRUAL_LABEL[open.accrual_kind] ?? open.accrual_kind}
                                {open.accrual_period_start && ` · период ${fmtDate(open.accrual_period_start)} — ${fmtDate(open.accrual_period_end)}`}
                            </span>
                        </div>
                        <button className="btn btn-secondary btn-sm" onClick={() => setOpenId(null)}>Свернуть</button>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16, marginTop: 12 }}>
                        <div>
                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Движения по линии</div>
                            <TanStackDataTable
                                columns={movementColumns}
                                data={open.movements}
                                exportName={`credit-line-${open.loan_id}-moves`}
                            />
                        </div>
                        <div>
                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>История ставок</div>
                            {open.rate_periods.length === 0 ? (
                                <div style={{ padding: 16, color: 'var(--color-text-dim)', fontSize: 13 }}>
                                    Ставок не задано — действует ставка займа.
                                </div>
                            ) : (
                                <TanStackDataTable
                                    columns={rateColumns}
                                    data={open.rate_periods}
                                    exportName={`credit-line-${open.loan_id}-rates`}
                                />
                            )}
                        </div>
                    </div>

                    {Number(open.one_off_fee_total) > 0 && (
                        <div style={{ marginTop: 16 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Разовые комиссии</div>
                            <TanStackDataTable
                                columns={feeColumns}
                                data={open.fees}
                                exportName={`credit-line-${open.loan_id}-fees`}
                            />
                            <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 6 }}>
                                В стоимость периода такая комиссия входит долей — {money(open.one_off_fee_period)} ₽, не целиком.
                            </div>
                        </div>
                    )}
                </div>
            )}

            {drawFor && <DrawModal line={drawFor} onClose={() => setDrawFor(null)} onDone={done} />}
            {rateFor && <RateModal line={rateFor} onClose={() => setRateFor(null)} onDone={done} />}
        </div>
    );
}

function DrawModal({ line, onClose, onDone }: { line: CreditLineDetail; onClose: () => void; onDone: () => void }) {
    const free = Number(line.available ?? 0);
    const [amount, setAmount] = useState('');
    const [when, setWhen] = useState(new Date().toISOString().slice(0, 10));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const value = Number(amount);
    const invalid = !Number.isFinite(value) || value <= 0 || (line.available != null && value > free);

    const submit = async () => {
        setSaving(true);
        setError('');
        try {
            await api.drawCreditLine(line.loan_id, { amount: value, drawn_at: when });
            onDone();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка выборки');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-solid" onClick={(e) => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>Выборка транша</h3>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
                </div>
                <p style={{ color: 'var(--color-text-dim)', fontSize: 13, marginTop: 0 }}>
                    Линия <b style={{ fontFamily: 'monospace' }}>{line.contract_number}</b>, свободный лимит {money(line.available)} ₽.
                </p>
                <div className="form-group">
                    <label className="form-label">Сумма транша</label>
                    <input type="number" className="form-input" value={amount} onChange={(e) => setAmount(e.target.value)} />
                    {line.available != null && (
                        <button type="button" className="btn btn-secondary btn-sm" style={{ marginTop: 8 }} onClick={() => setAmount(String(free))}>
                            Весь свободный лимит
                        </button>
                    )}
                </div>
                <div className="form-group">
                    <label className="form-label">Дата выборки</label>
                    <input type="date" className="form-input" value={when} onChange={(e) => setWhen(e.target.value)} />
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 6 }}>
                        Проценты начисляются со следующего дня после выборки.
                    </div>
                </div>
                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 10 }}>{error}</div>}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>Отмена</button>
                    <button className="btn btn-primary btn-sm" disabled={saving || invalid} onClick={submit}>
                        {saving ? 'Сохранение…' : 'Выбрать транш'}
                    </button>
                </div>
            </div>
        </div>
    );
}

function RateModal({ line, onClose, onDone }: { line: CreditLineDetail; onClose: () => void; onDone: () => void }) {
    const [from, setFrom] = useState(new Date().toISOString().slice(0, 10));
    const [base, setBase] = useState('');
    const [spread, setSpread] = useState('5');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const total = (Number(base) || 0) + (Number(spread) || 0);
    const invalid = !from || total <= 0 || total > 100;

    const submit = async () => {
        setSaving(true);
        setError('');
        try {
            await api.addRatePeriod(line.loan_id, {
                valid_from: from,
                rate: total / 100,
                base_rate: base ? Number(base) / 100 : null,
                spread: spread ? Number(spread) / 100 : null,
            });
            onDone();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-solid" onClick={(e) => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>Ставка с даты</h3>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
                </div>
                <p style={{ color: 'var(--color-text-dim)', fontSize: 13, marginTop: 0 }}>
                    Ставка меняется вслед за ключевой. Прошлые периоды не пересчитываются — старые
                    начисления остаются такими, какими их посчитал банк.
                </p>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div className="form-group">
                        <label className="form-label">Действует с</label>
                        <input type="date" className="form-input" value={from} onChange={(e) => setFrom(e.target.value)} />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Ключевая ЦБ, %</label>
                        <input type="number" step="0.01" className="form-input" value={base} onChange={(e) => setBase(e.target.value)} />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Надбавка банка, %</label>
                        <input type="number" step="0.01" className="form-input" value={spread} onChange={(e) => setSpread(e.target.value)} />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Итоговая ставка</label>
                        <div style={{ fontSize: 22, fontWeight: 700, paddingTop: 4 }}>{total.toFixed(2)} %</div>
                    </div>
                </div>
                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 10 }}>{error}</div>}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>Отмена</button>
                    <button className="btn btn-primary btn-sm" disabled={saving || invalid} onClick={submit}>
                        {saving ? 'Сохранение…' : 'Сохранить ставку'}
                    </button>
                </div>
            </div>
        </div>
    );
}
