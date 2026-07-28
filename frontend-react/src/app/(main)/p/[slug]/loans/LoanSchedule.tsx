'use client';

import { useCallback, useEffect, useState } from 'react';
import TanStackDataTable from '@/components/TanStackDataTable';
import { api } from '@/lib/api';
import type { LoanScheduleResponse, LoanScheduleRow, LoanScheduleState } from '@/types/api';
import { money, fmtDate } from './loanFmt';

const STATE_LABEL: Record<LoanScheduleState, string> = {
    PAID: 'оплачен',
    PARTIAL: 'частично',
    OVERDUE: 'просрочен',
    DUE: 'ближайший',
    UPCOMING: 'по плану',
};

const STATE_BADGE: Record<LoanScheduleState, string> = {
    PAID: 'badge-success',
    PARTIAL: 'badge-warning',
    OVERDUE: 'badge-danger',
    DUE: 'badge-info',
    UPCOMING: 'badge-secondary',
};

/** График плановых платежей по займу — со сверкой план/факт. */
export default function LoanSchedule({ loanId }: { loanId: number }) {
    const [data, setData] = useState<LoanScheduleResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setData(await api.loanSchedule(loanId));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [loanId]);

    useEffect(() => { load(); }, [load]);

    const columns = [
        {
            key: 'seq', label: '№', align: 'right' as const, width: '48',
            render: (v: number, r: LoanScheduleRow) => (
                <span>
                    {v}
                    {r.is_fee && (
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)', fontWeight: 400 }}>комиссия за выдачу</div>
                    )}
                    {r.is_debt_plan && (
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)', fontWeight: 400 }}>план погашения</div>
                    )}
                </span>
            ),
        },
        {
            key: 'period_start', label: 'Период',
            render: (_v: unknown, r: LoanScheduleRow) => (
                r.period_start || r.period_end
                    ? `${fmtDate(r.period_start)} — ${fmtDate(r.period_end)}`
                    : '—'
            ),
        },
        { key: 'due_date', label: 'Дата платежа', render: (v: string) => fmtDate(v) },
        {
            key: 'days', label: 'Дней', align: 'right' as const,
            getValue: (r: LoanScheduleRow) => r.days ?? 0,
            render: (_v: unknown, r: LoanScheduleRow) => r.days ?? '—',
        },
        {
            key: 'principal_due', label: 'Тело', align: 'right' as const,
            getValue: (r: LoanScheduleRow) => Number(r.principal_due),
            render: (_v: unknown, r: LoanScheduleRow) => `${money(r.principal_due)} ₽`,
        },
        {
            key: 'interest_due', label: 'Проценты', align: 'right' as const,
            getValue: (r: LoanScheduleRow) => Number(r.interest_due),
            render: (_v: unknown, r: LoanScheduleRow) => `${money(r.interest_due)} ₽`,
        },
        {
            key: 'payment_total', label: 'Платёж', align: 'right' as const,
            getValue: (r: LoanScheduleRow) => Number(r.payment_total),
            render: (_v: unknown, r: LoanScheduleRow) => <span style={{ fontWeight: 600 }}>{money(r.payment_total)} ₽</span>,
        },
        {
            key: 'balance_after', label: 'Остаток после', align: 'right' as const,
            getValue: (r: LoanScheduleRow) => Number(r.balance_after ?? 0),
            render: (_v: unknown, r: LoanScheduleRow) => r.balance_after != null ? `${money(r.balance_after)} ₽` : '—',
        },
        {
            key: 'paid_total', label: 'Факт', align: 'right' as const,
            getValue: (r: LoanScheduleRow) => Number(r.paid_total),
            render: (_v: unknown, r: LoanScheduleRow) => (
                <div>
                    <div>{money(r.paid_total)} ₽</div>
                    {r.paid_at && (
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{fmtDate(r.paid_at)}</div>
                    )}
                </div>
            ),
        },
        {
            key: 'delta', label: 'Отклонение', align: 'right' as const,
            getValue: (r: LoanScheduleRow) => Number(r.delta),
            render: (_v: unknown, r: LoanScheduleRow) => {
                const delta = Number(r.delta);
                const daysLate = r.days_late;
                return (
                    <div>
                        <div style={{ color: delta < 0 ? 'var(--color-danger)' : 'var(--color-text)' }}>
                            {delta > 0 ? '+' : ''}{money(r.delta)} ₽
                        </div>
                        {daysLate != null && (
                            <div style={{ fontSize: 11, color: daysLate > 0 ? 'var(--color-danger)' : 'var(--color-text-dim)' }}>
                                {daysLate > 0 ? `+${daysLate} дн` : daysLate < 0 ? `−${Math.abs(daysLate)} дн` : '0 дн'}
                            </div>
                        )}
                    </div>
                );
            },
        },
        {
            key: 'state', label: 'Статус',
            render: (_v: unknown, r: LoanScheduleRow) => (
                <span className={`badge ${STATE_BADGE[r.state]}`}>{STATE_LABEL[r.state]}</span>
            ),
        },
    ];

    if (loading) {
        return <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>;
    }
    if (error) {
        return (
            <div className="glass-card" style={{ padding: 24, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between' }}>
                <span>{error}</span>
                <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
            </div>
        );
    }
    if (!data || data.rows.length === 0) {
        return (
            <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                <div style={{ fontSize: 40, marginBottom: 12 }}>📅</div>
                <div style={{ fontSize: 16, fontWeight: 600 }}>Графика нет</div>
                <div style={{ fontSize: 13, color: 'var(--color-text-dim)', marginTop: 6 }}>
                    График платежей заводится из договора займа — плановые даты, суммы тела и процентов по каждому платежу.
                </div>
            </div>
        );
    }

    // Экран один, но смыслов два: график кредитора (что мы обязаны по договору)
    // и план погашения накопленного долга (о чём договорились сами).
    const allDebtPlan = data.rows.every((r) => r.is_debt_plan);

    return (
        <div>
            <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 4px' }}>
                {allDebtPlan ? 'План погашения долга' : 'График платежей'}
            </h3>
            <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 12 }}>
                {allDebtPlan
                    ? 'Договорённость о том, когда и сколько платить в счёт уже начисленных процентов. Проценты по займу продолжают капать по ставке — план их гасит, а не заменяет.'
                    : 'Плановые даты и суммы из договора; рядом — факт платежей и отклонение.'}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 16 }}>
                <Metric label="Всего к возврату" value={`${money(data.total_payment)} ₽`} />
                <Metric label="Оплачено" value={`${money(data.paid_total)} ₽`} accent="var(--color-success)" />
                <Metric label="Осталось" value={`${money(data.left_total)} ₽`} accent="var(--color-accent)" />
                <Metric
                    label="Ближайший платёж"
                    value={data.next_due_date ? `${money(data.next_due_amount)} ₽` : '—'}
                    sub={data.next_due_date ? fmtDate(data.next_due_date) : undefined}
                />
                <Metric
                    label="Просрочка"
                    value={data.overdue_count > 0 ? `${data.overdue_count} шт` : '0'}
                    sub={data.overdue_count > 0 ? `${money(data.overdue_amount)} ₽` : undefined}
                    accent={data.overdue_count > 0 ? 'var(--color-danger)' : undefined}
                />
                <Metric label="Полная стоимость" value={`${money(data.total_cost)} ₽`} />
            </div>
            <TanStackDataTable columns={columns} data={data.rows} exportName="loan-schedule" enableSorting />
        </div>
    );
}

function Metric({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: string }) {
    return (
        <div className="glass-card" style={{ padding: '12px 16px' }}>
            <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: accent ?? 'var(--color-text)' }}>{value}</div>
            {sub && <div style={{ fontSize: 12, color: accent ?? 'var(--color-text-dim)', marginTop: 2 }}>{sub}</div>}
        </div>
    );
}
