'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import KpiCard from '@/components/KpiCard';
import { lenderMe, lenderListLoans, LenderApiError } from '@/lib/api/lender';
import type { LenderMeResponse, LenderLoanRow, LoanStatus } from '@/types/api';
import { formatNumber, formatDate } from '@/lib/utils';

const STATUS_BADGE: Record<LoanStatus, { label: string; cls: string }> = {
    ACTIVE: { label: 'Активный', cls: 'badge badge-success' },
    CLOSED: { label: 'Закрыт', cls: 'badge badge-secondary' },
    DEFAULTED: { label: 'Просрочен', cls: 'badge badge-danger' },
};

/** Active loans first, then by nearest maturity. */
function sortLoans(items: LenderLoanRow[]): LenderLoanRow[] {
    const rank: Record<LoanStatus, number> = { ACTIVE: 0, DEFAULTED: 1, CLOSED: 2 };
    return [...items].sort((a, b) => {
        if (rank[a.status] !== rank[b.status]) return rank[a.status] - rank[b.status];
        const am = a.maturity_date ? new Date(a.maturity_date).getTime() : Infinity;
        const bm = b.maturity_date ? new Date(b.maturity_date).getTime() : Infinity;
        return am - bm;
    });
}

export default function LenderHomePage() {
    const [me, setMe] = useState<LenderMeResponse | null>(null);
    const [loans, setLoans] = useState<LenderLoanRow[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback((signal: AbortSignal) => {
        setLoading(true);
        setError('');
        Promise.all([lenderMe(), lenderListLoans()])
            .then(([meRes, loansRes]) => {
                if (signal.aborted) return;
                setMe(meRes);
                setLoans(loansRes.items);
            })
            .catch((err: unknown) => {
                if (signal.aborted) return;
                // 401 is handled inside the client (redirect to /lender/login).
                if (err instanceof LenderApiError && err.status === 401) return;
                setError(err instanceof Error ? err.message : 'Ошибка загрузки');
            })
            .finally(() => {
                if (!signal.aborted) setLoading(false);
            });
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    if (loading) {
        return (
            <div className="lender-feed">
                <div className="lender-skeleton" />
                <div className="lender-skeleton" />
                <div className="lender-skeleton" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="glass-card">
                <h1 className="page-title">Что-то пошло не так</h1>
                <p className="page-subtitle" style={{ color: 'var(--color-danger)' }}>{error}</p>
            </div>
        );
    }

    const totals = me?.totals;
    const sorted = loans ? sortLoans(loans) : [];

    return (
        <div className="animate-in">
            <h1 className="page-title">Здравствуйте, {me?.display_name || me?.username}</h1>
            <p className="page-subtitle">Ваши займы и начисленные проценты</p>

            {totals && (
                <div className="lender-kpi-row" style={{ marginTop: 20 }}>
                    <KpiCard
                        label="В займе сейчас"
                        value={`${formatNumber(Number(totals.outstanding), 0)} ₽`}
                        sub={`Активных: ${formatNumber(Number(totals.active_count), 0)}`}
                        color="var(--color-accent)"
                    />
                    <KpiCard
                        label="Ставка в месяц"
                        value={`${formatNumber(Number(totals.monthly_interest), 0)} ₽/мес`}
                        color="var(--color-warning)"
                    />
                    <KpiCard
                        label="Проценты (период)"
                        value={`${formatNumber(Number(totals.accrued_interest), 0)} ₽`}
                        sub={`Выплачено: ${formatNumber(Number(totals.interest_paid), 0)} ₽`}
                        color="var(--color-success)"
                    />
                    <KpiCard
                        label="Ближайшая выплата %"
                        value={formatDate(totals.next_interest_date)}
                        color="var(--color-accent)"
                    />
                    <KpiCard
                        label="Ближайший возврат"
                        value={formatDate(totals.next_maturity_date)}
                        color="var(--color-accent)"
                    />
                </div>
            )}

            {sorted.length === 0 ? (
                <div className="glass-card empty-state" style={{ marginTop: 20 }}>
                    <p className="page-subtitle">У вас пока нет активных займов</p>
                </div>
            ) : (
                <div className="lender-loan-grid" style={{ marginTop: 8 }}>
                    {sorted.map((loan) => {
                        const badge = STATUS_BADGE[loan.status];
                        return (
                            <Link
                                key={loan.id}
                                href={`/lender/loans/${loan.id}`}
                                className="glass-card lender-loan-card"
                            >
                                <div className="lender-loan-head">
                                    <span className="lender-loan-title">
                                        № {loan.contract_number || loan.id}
                                    </span>
                                    <span className={badge.cls}>{badge.label}</span>
                                </div>

                                <div className="lender-kv">
                                    <span className="lender-kv-key">Сумма займа</span>
                                    <span className="lender-kv-val">
                                        {formatNumber(Number(loan.principal), 0)} ₽
                                    </span>
                                </div>
                                <div className="lender-kv">
                                    <span className="lender-kv-key">Ставка</span>
                                    <span className="lender-kv-val">
                                        {loan.rate != null
                                            ? `${(Number(loan.rate) * 100).toFixed(1)}% / год`
                                            : '—'}
                                    </span>
                                </div>
                                <div className="lender-kv">
                                    <span className="lender-kv-key">Остаток тела</span>
                                    <span className="lender-kv-val">
                                        {formatNumber(Number(loan.remaining_principal), 0)} ₽
                                    </span>
                                </div>
                                <div className="lender-kv">
                                    <span className="lender-kv-key">Проценты (период)</span>
                                    <span className="lender-kv-val">
                                        {formatNumber(Number(loan.accrued_interest), 0)} ₽
                                    </span>
                                </div>
                                <div className="lender-kv">
                                    <span className="lender-kv-key">В месяц</span>
                                    <span className="lender-kv-val">
                                        {formatNumber(Number(loan.monthly_interest), 0)} ₽
                                    </span>
                                </div>
                                <div className="lender-kv">
                                    <span className="lender-kv-key">Выплата %</span>
                                    <span className="lender-kv-val">
                                        {formatDate(loan.next_interest_date)}
                                    </span>
                                </div>
                                <div className="lender-kv">
                                    <span className="lender-kv-key">Возврат</span>
                                    <span className="lender-kv-val">
                                        {formatDate(loan.maturity_date)}
                                        {loan.is_overdue && (
                                            <span className="badge badge-danger" style={{ marginLeft: 8 }}>
                                                просрочен
                                            </span>
                                        )}
                                        {!loan.is_overdue &&
                                            loan.status === 'ACTIVE' &&
                                            loan.days_to_maturity != null && (
                                                <span className="badge badge-secondary" style={{ marginLeft: 8 }}>
                                                    {formatNumber(Number(loan.days_to_maturity), 0)} дн
                                                </span>
                                            )}
                                    </span>
                                </div>
                            </Link>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
