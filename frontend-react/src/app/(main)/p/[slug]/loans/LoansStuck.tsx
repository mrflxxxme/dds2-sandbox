'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import TanStackDataTable from '@/components/TanStackDataTable';
import { api } from '@/lib/api';
import type { Loan, LoanStuckItem, LoanStuckResponse } from '@/types/api';
import ExtendModal from './ExtendModal';
import RepayModal from './RepayModal';
import { fmtDate, money, ratePct } from './loanFmt';

/** Зависшие займы: срок вышел, а возврата или продления не сделали. */
export default function LoansStuck({ nonce, onChanged }: { nonce: number; onChanged: () => void }) {
    const params = useParams();
    const slug = String(params?.slug ?? 'default');
    const [data, setData] = useState<LoanStuckResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [repaying, setRepaying] = useState<Loan | null>(null);
    const [extending, setExtending] = useState<Loan | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setData(await api.stuckLoans());
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load, nonce]);

    const done = () => { setRepaying(null); setExtending(null); load(); onChanged(); };

    const columns = [
        {
            key: 'counterparty_name', label: 'Заёмщик',
            getValue: (r: LoanStuckItem) => r.loan.counterparty_name ?? '',
            render: (_v: unknown, r: LoanStuckItem) => (
                <Link href={`/p/${slug}/loans/lenders/${r.loan.counterparty_id}`} style={{ fontWeight: 600, color: 'var(--color-accent)' }}>
                    {r.loan.counterparty_name ?? '—'}
                </Link>
            ),
        },
        {
            key: 'contract_number', label: 'Договор',
            getValue: (r: LoanStuckItem) => r.loan.contract_number,
            render: (_v: unknown, r: LoanStuckItem) => (
                <Link href={`/p/${slug}/loans/${r.loan.id}`} style={{ fontFamily: 'monospace', color: 'var(--color-accent)' }}>
                    {r.loan.contract_number}
                </Link>
            ),
        },
        {
            key: 'remaining', label: 'Остаток', align: 'right' as const,
            getValue: (r: LoanStuckItem) => Number(r.loan.remaining_principal ?? 0),
            render: (_v: unknown, r: LoanStuckItem) => `${money(r.loan.remaining_principal)} ₽`,
        },
        {
            key: 'rate', label: 'Ставка', align: 'right' as const,
            getValue: (r: LoanStuckItem) => Number(r.loan.rate ?? 0),
            render: (_v: unknown, r: LoanStuckItem) => ratePct(r.loan.rate),
        },
        {
            key: 'maturity_date', label: 'Срок вышел',
            getValue: (r: LoanStuckItem) => r.loan.maturity_date ?? '',
            render: (_v: unknown, r: LoanStuckItem) => (
                <span style={{ color: 'var(--color-danger)', fontWeight: 600 }}>{fmtDate(r.loan.maturity_date)}</span>
            ),
        },
        {
            key: 'days_overdue', label: 'Дней', align: 'right' as const,
            getValue: (r: LoanStuckItem) => r.days_overdue,
            render: (_v: unknown, r: LoanStuckItem) => (
                <span className={`badge ${r.days_overdue > 30 ? 'badge-danger' : 'badge-warning'}`}>{r.days_overdue}</span>
            ),
        },
        {
            key: 'accrued_since_maturity', label: '% после срока', align: 'right' as const,
            getValue: (r: LoanStuckItem) => Number(r.accrued_since_maturity),
            render: (_v: unknown, r: LoanStuckItem) => `${money(r.accrued_since_maturity)} ₽`,
        },
        {
            key: '_act', label: '', sortable: false,
            render: (_v: unknown, r: LoanStuckItem) => (
                <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                    <button className="btn btn-primary btn-sm" onClick={() => setRepaying(r.loan)}>Вернуть</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => setExtending(r.loan)}>Продлить</button>
                </div>
            ),
        },
    ];

    return (
        <div>
            {data && data.count > 0 && (
                <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Зависших займов</div>
                        <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--color-danger)' }}>{data.count}</div>
                    </div>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Тело без решения</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(data.total_amount)} ₽</div>
                    </div>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Набежало после срока</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(data.total_accrued_since_maturity)} ₽</div>
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
            ) : (data?.count ?? 0) === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>✅</div>
                    <div style={{ fontSize: 16, fontWeight: 600 }}>Зависших займов нет</div>
                    <div style={{ fontSize: 13, color: 'var(--color-text-dim)', marginTop: 6 }}>
                        По всем займам с истёкшим сроком есть возврат или продление.
                    </div>
                </div>
            ) : (
                <>
                    <div className="glass-card" style={{ padding: '12px 16px', marginBottom: 12, fontSize: 13, color: 'var(--color-text-dim)' }}>
                        Срок договора кончился, но возврат не отмечен и продление не оформлено.
                        Проценты на такие займы продолжают начисляться.
                    </div>
                    <TanStackDataTable
                        columns={columns}
                        data={data?.items ?? []}
                        exportName="stuck-loans"
                        enableSorting
                    />
                </>
            )}

            {repaying && <RepayModal loan={repaying} onClose={() => setRepaying(null)} onDone={done} />}
            {extending && <ExtendModal loan={extending} onClose={() => setExtending(null)} onExtended={done} />}
        </div>
    );
}
