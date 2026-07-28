'use client';

import { useCallback, useEffect, useState } from 'react';
import TanStackDataTable from '@/components/TanStackDataTable';
import { api } from '@/lib/api';
import type { LoanAccrualDay, LoanAccrualDaysResponse } from '@/types/api';
import { fmtDate, money, ratePct } from './loanFmt';

/**
 * Подневная расшифровка начисления.
 *
 * Все своды (месяц, период выплат, ОПиУ) стоят на одном дневном начислении и
 * показывают итог. Спорить с банком можно только по дням — видно тело на дату,
 * ставку этого дня и сумму за сутки.
 */
export default function LoanAccrualDays({ loanId, nonce }: { loanId: number; nonce: number }) {
    const [data, setData] = useState<LoanAccrualDaysResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [open, setOpen] = useState(false);
    const [from, setFrom] = useState('');
    const [to, setTo] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const res = await api.loanAccrualDays(loanId, from || undefined, to || undefined);
            setData(res);
            if (!from) setFrom(res.date_from);
            if (!to) setTo(res.date_to);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [loanId, from, to]);

    // Грузим только когда блок раскрыли: это расшифровка, а не главный экран.
    useEffect(() => { if (open) load(); }, [open, nonce]); // eslint-disable-line react-hooks/exhaustive-deps

    const columns = [
        { key: 'date', label: 'Дата', render: (_v: unknown, r: LoanAccrualDay) => fmtDate(r.date) },
        {
            key: 'balance', label: 'Тело', align: 'right' as const,
            getValue: (r: LoanAccrualDay) => Number(r.balance),
            render: (_v: unknown, r: LoanAccrualDay) => `${money(r.balance)} ₽`,
        },
        {
            key: 'rate', label: 'Ставка', align: 'right' as const,
            getValue: (r: LoanAccrualDay) => Number(r.rate ?? 0),
            render: (_v: unknown, r: LoanAccrualDay) => ratePct(r.rate),
        },
        {
            key: 'interest', label: 'За день', align: 'right' as const,
            getValue: (r: LoanAccrualDay) => Number(r.interest),
            render: (_v: unknown, r: LoanAccrualDay) => `${money(r.interest, 2)} ₽`,
        },
        {
            key: 'cumulative', label: 'Нарастающим', align: 'right' as const,
            getValue: (r: LoanAccrualDay) => Number(r.cumulative),
            render: (_v: unknown, r: LoanAccrualDay) => `${money(r.cumulative, 2)} ₽`,
        },
        {
            key: 'movement', label: 'Движение тела', align: 'right' as const,
            getValue: (r: LoanAccrualDay) => Number(r.movement ?? 0),
            render: (_v: unknown, r: LoanAccrualDay) => {
                if (r.movement == null) return '—';
                const positive = Number(r.movement) > 0;
                return (
                    <span style={{ color: positive ? 'var(--color-text)' : 'var(--color-success)' }}>
                        {positive ? '+' : '−'}{money(Math.abs(Number(r.movement)))} ₽
                    </span>
                );
            },
        },
    ];

    return (
        <div className="glass-card" style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                <div>
                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Начисление по дням</h3>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                        Расшифровка: тело на дату, ставка этого дня и сумма за сутки. Сумма дней
                        сходится со сводом — считает та же функция.
                    </div>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={() => setOpen((v) => !v)}>
                    {open ? 'Свернуть' : 'Показать'}
                </button>
            </div>

            {open && (
                <div style={{ marginTop: 12 }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 12 }}>
                        <div className="form-group" style={{ margin: 0 }}>
                            <label className="form-label">С</label>
                            <input type="date" className="form-input" value={from} onChange={(e) => setFrom(e.target.value)} />
                        </div>
                        <div className="form-group" style={{ margin: 0 }}>
                            <label className="form-label">По</label>
                            <input type="date" className="form-input" value={to} onChange={(e) => setTo(e.target.value)} />
                        </div>
                        <button className="btn btn-primary btn-sm" disabled={loading} onClick={load}>
                            {loading ? 'Считаю…' : 'Пересчитать'}
                        </button>
                    </div>

                    {error && (
                        <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 10 }}>{error}</div>
                    )}
                    {loading && !data ? (
                        <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>
                    ) : !data ? null : data.rows.length === 0 ? (
                        <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                            За этот период начислений не было.
                        </div>
                    ) : (
                        <>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 12 }}>
                                <Cell label="Начислено за период" value={`${money(data.total_interest, 2)} ₽`} />
                                <Cell label="Среднее тело" value={`${money(data.avg_balance)} ₽`} />
                                <Cell label="Эффективная ставка" value={ratePct(data.effective_rate)} />
                                <Cell label="Дней" value={String(data.rows.length)} />
                            </div>
                            {data.from_schedule && (
                                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 8 }}>
                                    Проценты разложены из графика договора, а не посчитаны формулой:
                                    у аннуитета банк считает своё округление и даёт льготные дни.
                                </div>
                            )}
                            <TanStackDataTable
                                columns={columns}
                                data={data.rows}
                                exportName={`loan-${loanId}-accrual-days`}
                                enableSorting
                            />
                        </>
                    )}
                </div>
            )}
        </div>
    );
}

function Cell({ label, value }: { label: string; value: string }) {
    return (
        <div>
            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{label}</div>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{value}</div>
        </div>
    );
}
