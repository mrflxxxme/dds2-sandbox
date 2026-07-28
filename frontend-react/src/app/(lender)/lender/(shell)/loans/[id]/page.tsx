'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { lenderGetLoan, LenderApiError } from '@/lib/api/lender';
import type {
    LenderLoanDetail,
    LoanStatus,
    LoanEntityType,
    LoanPaymentType,
} from '@/types/api';
import { formatNumber, formatDate } from '@/lib/utils';

const STATUS_BADGE: Record<LoanStatus, { label: string; cls: string }> = {
    ACTIVE: { label: 'Активный', cls: 'badge badge-success' },
    CLOSED: { label: 'Закрыт', cls: 'badge badge-secondary' },
    DEFAULTED: { label: 'Просрочен', cls: 'badge badge-danger' },
};

const ENTITY_LABEL: Record<LoanEntityType, string> = {
    PHYSICAL: 'Физлицо',
    IP: 'ИП',
};

const PAYMENT_LABEL: Record<LoanPaymentType, string> = {
    DISBURSEMENT: 'Выдача',
    PRINCIPAL_REPAY: 'Возврат тела',
    INTEREST_PAY: 'Выплата %',
    PENALTY: 'Пени',
    COMMISSION: 'Комиссия',
};

const PAYMENT_BADGE: Record<LoanPaymentType, string> = {
    DISBURSEMENT: 'badge badge-info',
    PRINCIPAL_REPAY: 'badge badge-secondary',
    INTEREST_PAY: 'badge badge-success',
    PENALTY: 'badge badge-danger',
    COMMISSION: 'badge badge-warning',
};

export default function LenderLoanDetailPage() {
    const params = useParams();
    const rawId = params?.id;
    const id = Number(Array.isArray(rawId) ? rawId[0] : rawId);

    const [loan, setLoan] = useState<LenderLoanDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(
        (signal: AbortSignal) => {
            if (!Number.isFinite(id)) {
                setError('Некорректный номер займа');
                setLoading(false);
                return;
            }
            setLoading(true);
            setError('');
            lenderGetLoan(id)
                .then((res) => {
                    if (signal.aborted) return;
                    setLoan(res);
                })
                .catch((err: unknown) => {
                    if (signal.aborted) return;
                    if (err instanceof LenderApiError && err.status === 401) return;
                    setError(err instanceof Error ? err.message : 'Ошибка загрузки');
                })
                .finally(() => {
                    if (!signal.aborted) setLoading(false);
                });
        },
        [id],
    );

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    const backLink = (
        <Link href="/lender" className="lender-back">
            ← Назад к займам
        </Link>
    );

    if (loading) {
        return (
            <div>
                {backLink}
                <div className="lender-feed">
                    <div className="lender-skeleton" />
                    <div className="lender-skeleton" />
                </div>
            </div>
        );
    }

    if (error) {
        return (
            <div>
                {backLink}
                <div className="glass-card">
                    <h1 className="page-title">Что-то пошло не так</h1>
                    <p className="page-subtitle" style={{ color: 'var(--color-danger)' }}>{error}</p>
                </div>
            </div>
        );
    }

    if (!loan) {
        return (
            <div>
                {backLink}
                <div className="glass-card empty-state">
                    <p className="page-subtitle">Заём не найден</p>
                </div>
            </div>
        );
    }

    const badge = STATUS_BADGE[loan.status];
    const ratePct =
        loan.rate != null ? `${(Number(loan.rate) * 100).toFixed(1)}% / год` : '—';

    return (
        <div className="animate-in">
            {backLink}

            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
                <h1 className="page-title" style={{ margin: 0 }}>
                    Заём № {loan.contract_number || loan.id}
                </h1>
                <span className={badge.cls}>{badge.label}</span>
                {loan.is_overdue && (
                    <span className="badge badge-danger">просрочен</span>
                )}
            </div>
            <p className="page-subtitle">
                Договор от {formatDate(loan.contract_date)}
            </p>

            <div className="lender-detail-grid" style={{ marginTop: 16 }}>
                <div className="glass-card">
                    <h2 className="page-subtitle" style={{ fontWeight: 600, marginBottom: 8 }}>
                        Финансы
                    </h2>
                    <div className="lender-kv">
                        <span className="lender-kv-key">Сумма займа</span>
                        <span className="lender-kv-val">{formatNumber(Number(loan.principal), 0)} ₽</span>
                    </div>
                    <div className="lender-kv">
                        <span className="lender-kv-key">Ставка</span>
                        <span className="lender-kv-val">{ratePct}</span>
                    </div>
                    <div className="lender-kv">
                        <span className="lender-kv-key">Остаток тела</span>
                        <span className="lender-kv-val">{formatNumber(Number(loan.remaining_principal), 0)} ₽</span>
                    </div>
                    <div className="lender-kv">
                        <span className="lender-kv-key">Возвращено тела</span>
                        <span className="lender-kv-val">{formatNumber(Number(loan.principal_repaid), 0)} ₽</span>
                    </div>
                </div>

                <div className="glass-card">
                    <h2 className="page-subtitle" style={{ fontWeight: 600, marginBottom: 8 }}>
                        Проценты
                    </h2>
                    <div className="lender-kv">
                        <span className="lender-kv-key">Начислено (период)</span>
                        <span className="lender-kv-val">{formatNumber(Number(loan.accrued_interest), 0)} ₽</span>
                    </div>
                    <div className="lender-kv">
                        <span className="lender-kv-key">В месяц</span>
                        <span className="lender-kv-val">{formatNumber(Number(loan.monthly_interest), 0)} ₽</span>
                    </div>
                    <div className="lender-kv">
                        <span className="lender-kv-key">Выплачено процентов</span>
                        <span className="lender-kv-val">{formatNumber(Number(loan.interest_paid), 0)} ₽</span>
                    </div>
                    <div className="lender-kv">
                        <span className="lender-kv-key">Проценты за весь срок</span>
                        <span className="lender-kv-val">{formatNumber(Number(loan.total_interest_projected), 0)} ₽</span>
                    </div>
                </div>

                <div className="glass-card">
                    <h2 className="page-subtitle" style={{ fontWeight: 600, marginBottom: 8 }}>
                        Сроки и реквизиты
                    </h2>
                    <div className="lender-kv">
                        <span className="lender-kv-key">Дата выдачи</span>
                        <span className="lender-kv-val">{formatDate(loan.start_date)}</span>
                    </div>
                    <div className="lender-kv">
                        <span className="lender-kv-key">Дата возврата</span>
                        <span className="lender-kv-val">{formatDate(loan.maturity_date)}</span>
                    </div>
                    <div className="lender-kv">
                        <span className="lender-kv-key">Ближайшая выплата %</span>
                        <span className="lender-kv-val">{formatDate(loan.next_interest_date)}</span>
                    </div>
                    <div className="lender-kv">
                        <span className="lender-kv-key">Тип займодавца</span>
                        <span className="lender-kv-val">
                            {loan.entity_type ? ENTITY_LABEL[loan.entity_type] : '—'}
                        </span>
                    </div>
                    {loan.lender_bank && (
                        <div className="lender-kv">
                            <span className="lender-kv-key">Банк</span>
                            <span className="lender-kv-val">{loan.lender_bank}</span>
                        </div>
                    )}
                </div>
            </div>

            <div className="glass-card">
                <h2 className="page-title" style={{ fontSize: 18, marginBottom: 12 }}>
                    История платежей
                </h2>
                {loan.payments.length === 0 ? (
                    <div className="empty-state">
                        <p className="page-subtitle">Платежей пока нет</p>
                    </div>
                ) : (
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Дата</th>
                                <th>Тип</th>
                                <th className="lender-num">Сумма</th>
                                <th>Валюта</th>
                            </tr>
                        </thead>
                        <tbody>
                            {loan.payments.map((p, i) => (
                                <tr key={`${p.paid_at}-${i}`}>
                                    <td>{formatDate(p.paid_at)}</td>
                                    <td>
                                        <span className={PAYMENT_BADGE[p.payment_type]}>
                                            {PAYMENT_LABEL[p.payment_type]}
                                        </span>
                                    </td>
                                    <td className="lender-num">{formatNumber(Number(p.amount), 0)}</td>
                                    <td>{p.currency}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
}
