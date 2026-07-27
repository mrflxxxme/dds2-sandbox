'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';
import PageHeader from '@/components/PageHeader';
import TanStackDataTable from '@/components/TanStackDataTable';
import { api } from '@/lib/api';
import type { LenderDetail, LenderPeriodPoint, Loan } from '@/types/api';
import ExtendModal from '../../ExtendModal';
import LoanFormModal from '../../LoanFormModal';
import RepayModal from '../../RepayModal';
import { ENTITY_LABEL, STATUS_BADGE, STATUS_LABEL, fmtDate, money, ratePct } from '../../loanFmt';

export default function LenderCardPage() {
    const params = useParams();
    const router = useRouter();
    const slug = String(params?.slug ?? 'default');
    const lenderId = Number(params?.id);

    const [data, setData] = useState<LenderDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [nonce, setNonce] = useState(0);
    const [newLoan, setNewLoan] = useState(false);
    const [showClosed, setShowClosed] = useState(false);
    const [extending, setExtending] = useState<Loan | null>(null);
    const [repaying, setRepaying] = useState<Loan | null>(null);

    const load = useCallback(async () => {
        if (!Number.isFinite(lenderId)) {
            setError('Некорректный заёмщик');
            setLoading(false);
            return;
        }
        const controller = new AbortController();
        setLoading(true);
        setError('');
        try {
            const res = await api.lenderDetail(lenderId, 24);
            if (controller.signal.aborted) return;
            setData(res);
        } catch (e: unknown) {
            if (controller.signal.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (!controller.signal.aborted) setLoading(false);
        }
    }, [lenderId]);

    useEffect(() => { load(); }, [load, nonce]);

    const refresh = () => setNonce((n) => n + 1);

    // Закрытые займы — в архив: по умолчанию в истории только живые, иначе у
    // заёмщика с длинной цепочкой продлений таблица тонет в погашенных траншах.
    const closedCount = useMemo(
        () => (data?.loans ?? []).filter((l) => l.status !== 'ACTIVE').length,
        [data],
    );
    const visibleLoans = useMemo(
        () => (data?.loans ?? []).filter((l) => showClosed || l.status === 'ACTIVE'),
        [data, showClosed],
    );

    const loanColumns = [
        {
            key: 'contract_number', label: 'Договор',
            render: (v: string, r: Loan) => (
                <Link href={`/p/${slug}/loans/${r.id}`} style={{ fontWeight: 600, color: 'var(--color-accent)' }}>{v}</Link>
            ),
        },
        { key: 'principal', label: 'Сумма', align: 'right' as const, getValue: (r: Loan) => Number(r.principal), render: (_v: unknown, r: Loan) => `${money(r.principal)} ₽` },
        { key: 'remaining_principal', label: 'Остаток', align: 'right' as const, getValue: (r: Loan) => Number(r.remaining_principal ?? 0), render: (_v: unknown, r: Loan) => `${money(r.remaining_principal)} ₽` },
        { key: 'rate', label: 'Ставка', align: 'right' as const, getValue: (r: Loan) => Number(r.rate ?? 0), render: (_v: unknown, r: Loan) => ratePct(r.rate) },
        { key: 'interest_due_period', label: 'К выплате', align: 'right' as const, getValue: (r: Loan) => Number(r.interest_due_period ?? 0), render: (_v: unknown, r: Loan) => `${money(r.interest_due_period)} ₽` },
        { key: 'start_date', label: 'С', render: (_v: unknown, r: Loan) => fmtDate(r.start_date) },
        {
            key: 'maturity_date', label: 'Возврат',
            render: (_v: unknown, r: Loan) => {
                const overdue = r.status === 'ACTIVE' && r.days_to_maturity != null && r.days_to_maturity < 0;
                return (
                    <span style={overdue ? { color: 'var(--color-danger)', fontWeight: 600 } : undefined}>
                        {fmtDate(r.maturity_date)}{overdue ? ' ⚠' : ''}
                    </span>
                );
            },
        },
        {
            key: 'status', label: 'Статус',
            render: (_v: unknown, r: Loan) => (
                <span className={`badge ${STATUS_BADGE[r.status]}`}>{STATUS_LABEL[r.status]}</span>
            ),
        },
        {
            key: '_act', label: '', sortable: false,
            render: (_v: unknown, r: Loan) => (
                r.status === 'ACTIVE' ? (
                    <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => setRepaying(r)}>Вернуть</button>
                        <button className="btn btn-secondary btn-sm" onClick={() => setExtending(r)}>Продлить</button>
                    </div>
                ) : <span style={{ color: 'var(--color-text-dim)' }}>—</span>
            ),
        },
    ];

    const periodColumns = [
        {
            key: 'period_end', label: 'Период (дата выплаты)',
            render: (_v: unknown, r: LenderPeriodPoint) => (
                <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                    <span style={{ fontWeight: r.is_current ? 700 : 400 }}>{fmtDate(r.period_end)}</span>
                    {r.is_current && <span className="badge badge-info">текущий</span>}
                    {r.is_forecast && <span className="badge badge-secondary">прогноз</span>}
                </span>
            ),
        },
        { key: 'period_start', label: 'С', render: (_v: unknown, r: LenderPeriodPoint) => fmtDate(r.period_start) },
        {
            key: 'interest', label: 'Проценты', align: 'right' as const,
            getValue: (r: LenderPeriodPoint) => Number(r.interest),
            render: (_v: unknown, r: LenderPeriodPoint) => `${money(r.interest)} ₽`,
        },
    ];

    if (loading) {
        return <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>;
    }

    if (error) {
        return (
            <div className="glass-card" style={{ padding: 24, color: 'var(--color-danger)', display: 'flex', gap: 12, justifyContent: 'space-between' }}>
                <span>{error}</span>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={refresh}>Повторить</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => router.push(`/p/${slug}/loans`)}>К списку</button>
                </div>
            </div>
        );
    }

    if (!data) {
        return (
            <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                <div style={{ fontSize: 40, marginBottom: 12 }}>👤</div>
                <div style={{ fontSize: 16, fontWeight: 600 }}>Заёмщик не найден</div>
            </div>
        );
    }

    return (
        <div className="animate-in">
            <PageHeader
                title={data.name}
                subtitle={`${data.entity_type ? ENTITY_LABEL[data.entity_type] : 'сущность не указана'}${data.inn ? ` · ИНН ${data.inn}` : ''}${data.lender_bank ? ` · ${data.lender_bank}` : ''}`}
                actions={
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        {data.is_archived && <span className="badge badge-secondary">архив</span>}
                        <Link href={`/p/${slug}/loans`} className="btn btn-secondary btn-sm">← К заёмщикам</Link>
                        <button className="btn btn-primary btn-sm" onClick={() => setNewLoan(true)}>+ Новый займ</button>
                    </div>
                }
            />

            <div style={{ display: 'flex', gap: 12, margin: '16px 0', flexWrap: 'wrap' }}>
                <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 180px' }}>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>В займе сейчас</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{money(data.outstanding)} ₽</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                        {data.active_count} из {data.total_count} займов
                    </div>
                </div>
                <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 180px' }}>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Средняя ставка</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{ratePct(data.weighted_avg_rate)}</div>
                </div>
                <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 180px' }}>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                        К выплате {fmtDate(data.accrual_period_end)}
                    </div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{money(data.interest_due_period)} ₽</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                        начислено {money(data.accrued_interest)} ₽
                    </div>
                </div>
                <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 180px' }}>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }} title="Только новые деньги: продления одних и тех же средств не учитываются повторно">
                        Занёс за всё время
                    </div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{money(data.principal_total)} ₽</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                        вернули {money(data.principal_repaid)} ₽ · % выплачено {money(data.interest_paid)} ₽
                    </div>
                </div>
            </div>

            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
                    <div style={{ fontSize: 15, fontWeight: 600 }}>
                        История займов
                        <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--color-text-dim)', marginLeft: 8 }}>
                            {data.active_count} активных
                            {closedCount > 0 ? ` · ${closedCount} в архиве` : ''}
                        </span>
                    </div>
                    {closedCount > 0 && (
                        <label style={{ display: 'inline-flex', gap: 8, alignItems: 'center', cursor: 'pointer', fontSize: 13 }}>
                            <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} />
                            Показать архив ({closedCount})
                        </label>
                    )}
                </div>
                {visibleLoans.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                        {data.loans.length > 0 ? 'Все займы закрыты — включите «Показать архив»' : 'Займов нет'}
                    </div>
                ) : (
                    <TanStackDataTable
                        columns={loanColumns}
                        data={visibleLoans}
                        exportName={`lender-${data.counterparty_id}-loans`}
                        enableSorting
                    />
                )}
            </div>

            <div className="glass-card" style={{ padding: 20 }}>
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>Проценты по периодам</div>
                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 12 }}>
                    Период начисления — с 25-го по 25-е. Метка периода = дата выплаты.
                </div>
                {data.periods.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                        Начислений за последние 24 месяца нет
                    </div>
                ) : (
                    <TanStackDataTable
                        columns={periodColumns}
                        data={data.periods}
                        exportName={`lender-${data.counterparty_id}-periods`}
                        enableSorting
                    />
                )}
            </div>

            {newLoan && (
                <LoanFormModal
                    mode="create"
                    onClose={() => setNewLoan(false)}
                    onSaved={() => { setNewLoan(false); refresh(); }}
                />
            )}

            {repaying && (
                <RepayModal
                    loan={repaying}
                    onClose={() => setRepaying(null)}
                    onDone={() => { setRepaying(null); refresh(); }}
                />
            )}

            {extending && (
                <ExtendModal
                    loan={extending}
                    onClose={() => setExtending(null)}
                    onExtended={() => { setExtending(null); refresh(); }}
                />
            )}
        </div>
    );
}
