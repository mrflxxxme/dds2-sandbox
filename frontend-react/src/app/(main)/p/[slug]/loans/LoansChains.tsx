'use client';

import { useCallback, useEffect, useState } from 'react';
import TanStackDataTable from '@/components/TanStackDataTable';
import { api } from '@/lib/api';
import type { LoanChain, LoanChainListResponse, LoanChainMovement, LoanAccrualMonth, LoanMirrorSide } from '@/types/api';
import { fmtDate, money, ratePct, monthLabel } from './loanFmt';

const MOVEMENT_LABEL: Record<string, string> = {
    DISBURSEMENT: 'выдача',
    PRINCIPAL_REPAY: 'возврат тела',
    INTEREST_PAY: 'выплата %',
    PENALTY: 'пени',
    COMMISSION: 'комиссия',
};

/** Займы между своими проектами: одна сделка — две книги (долг у ООО, актив у ИП). */
export default function LoansChains({ nonce, onChanged }: { nonce: number; onChanged: () => void }) {
    const [data, setData] = useState<LoanChainListResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [openKey, setOpenKey] = useState<string | null>(null);
    const [syncingFor, setSyncingFor] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setData(await api.loanChains());
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load, nonce]);

    const sync = async (chain: LoanChain) => {
        const firstSide = chain.sides[0];
        if (!firstSide) return;
        setError('');
        setSyncingFor(String(chain.sides[0]?.loan_id ?? chain.contract_number));
        try {
            await api.syncLoanMirror(firstSide.loan_id);
            await load();
            onChanged();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка синхронизации');
        } finally {
            setSyncingFor(null);
        }
    };

    const movementColumns = [
        { key: 'happened_at', label: 'Дата', render: (_v: unknown, r: LoanChainMovement) => fmtDate(r.happened_at) },
        {
            key: 'kind', label: 'Операция',
            render: (_v: unknown, r: LoanChainMovement) => MOVEMENT_LABEL[r.kind] ?? r.kind,
        },
        {
            key: 'amount', label: 'Сумма', align: 'right' as const,
            getValue: (r: LoanChainMovement) => Number(r.amount),
            render: (_v: unknown, r: LoanChainMovement) => `${money(r.amount)} ₽`,
        },
        {
            key: 'balance_after', label: 'Тело после', align: 'right' as const,
            getValue: (r: LoanChainMovement) => Number(r.balance_after ?? 0),
            render: (_v: unknown, r: LoanChainMovement) => r.balance_after == null ? '—' : `${money(r.balance_after)} ₽`,
        },
    ];

    const monthlyColumns = [
        { key: 'month', label: 'Месяц', render: (_v: unknown, r: LoanAccrualMonth) => monthLabel(r.month) },
        { key: 'days', label: 'Дней', align: 'right' as const, getValue: (r: LoanAccrualMonth) => r.days },
        {
            key: 'interest', label: 'Проценты', align: 'right' as const,
            getValue: (r: LoanAccrualMonth) => Number(r.interest),
            render: (_v: unknown, r: LoanAccrualMonth) => `${money(r.interest)} ₽`,
        },
    ];

    const totalOutstanding = Number(data?.total_outstanding ?? 0);
    const totalInterestDebt = Number(data?.total_interest_debt ?? 0);

    return (
        <div>
            {data && data.items.length > 0 && (
                <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Тело</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(data?.total_outstanding)} ₽</div>
                    </div>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Долг по процентам</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(data?.total_interest_debt)} ₽</div>
                    </div>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Всего</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(totalOutstanding + totalInterestDebt)} ₽</div>
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
            ) : error && !data ? null : (data?.items.length ?? 0) === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>🔗</div>
                    <div style={{ fontSize: 16, fontWeight: 600 }}>Займов между проектами нет</div>
                    <div style={{ fontSize: 13, color: 'var(--color-text-dim)', marginTop: 6 }}>
                        Это займы, у которых заведена вторая сторона в другом проекте — например,
                        ИП учредителя выдал деньги ООО: у ООО долг, у ИП — актив по одному и тому же договору.
                    </div>
                </div>
            ) : (
                (data?.items ?? []).map((chain) => {
                    const chainKey = String(chain.sides[0]?.loan_id ?? chain.contract_number);
                    const isOpen = openKey === chainKey;
                    return (
                        <div key={chainKey} className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
                                <div>
                                    <div style={{ fontSize: 15, fontWeight: 700 }}>{chain.contract_number}</div>
                                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 2 }}>
                                        {chain.contract_date ? fmtDate(chain.contract_date) : '—'}
                                        {' · '}
                                        {chain.maturity_date ? `до ${fmtDate(chain.maturity_date)}` : 'до востребования'}
                                    </div>
                                </div>
                            </div>

                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16, marginTop: 16 }}>
                                {chain.sides.map((side: LoanMirrorSide) => (
                                    <div
                                        key={side.loan_id}
                                        style={{
                                            padding: 14,
                                            borderRadius: 14,
                                            border: '1px solid var(--color-border)',
                                            background: 'var(--color-bg-card)',
                                        }}
                                    >
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                                            <div style={{ fontWeight: 600 }}>{side.project_name ?? `Проект #${side.project_id}`}</div>
                                            <span className={`badge ${side.direction === 'INCOMING' ? 'badge-danger' : 'badge-success'}`}>
                                                {side.direction === 'INCOMING' ? 'долг' : 'актив'}
                                            </span>
                                        </div>
                                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 8 }}>
                                            {side.counterparty_name ?? '—'}
                                        </div>
                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 13 }}>
                                            <div>
                                                <div style={{ color: 'var(--color-text-dim)', fontSize: 11 }}>Тело</div>
                                                <div style={{ fontWeight: 600 }}>{money(side.outstanding)} ₽</div>
                                            </div>
                                            <div>
                                                <div style={{ color: 'var(--color-text-dim)', fontSize: 11 }}>Начислено всего</div>
                                                <div style={{ fontWeight: 600 }}>{money(side.accrued_total)} ₽</div>
                                            </div>
                                            <div>
                                                <div style={{ color: 'var(--color-text-dim)', fontSize: 11 }}>Уплачено</div>
                                                <div style={{ fontWeight: 600 }}>{money(side.interest_paid)} ₽</div>
                                            </div>
                                            <div>
                                                <div style={{ color: 'var(--color-text-dim)', fontSize: 11 }}>Долг по процентам</div>
                                                <div style={{ fontWeight: 600, color: Number(side.interest_debt) > 0 ? 'var(--color-warning)' : undefined }}>
                                                    {money(side.interest_debt)} ₽
                                                </div>
                                            </div>
                                            <div>
                                                <div style={{ color: 'var(--color-text-dim)', fontSize: 11 }}>Ставка</div>
                                                <div style={{ fontWeight: 600 }}>{ratePct(side.current_rate)}</div>
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>

                            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 16, marginTop: 12, fontSize: 13, flexWrap: 'wrap' }}>
                                <span>Тело {money(chain.outstanding)} ₽</span>
                                <span>+ долг по процентам {money(chain.interest_debt)} ₽</span>
                                <span style={{ fontWeight: 700 }}>= {money(chain.total_debt)} ₽</span>
                            </div>

                            {!chain.in_sync && (
                                <div
                                    className="glass-card"
                                    style={{
                                        marginTop: 12,
                                        padding: 12,
                                        display: 'flex',
                                        justifyContent: 'space-between',
                                        alignItems: 'center',
                                        gap: 12,
                                        flexWrap: 'wrap',
                                        border: '1px solid var(--color-warning)',
                                    }}
                                >
                                    <span style={{ color: 'var(--color-warning)', fontSize: 13 }}>
                                        ⚠ {chain.sync_note ?? 'Книги двух сторон разошлись.'}
                                    </span>
                                    <button
                                        className="btn btn-primary btn-sm"
                                        disabled={syncingFor === chainKey}
                                        onClick={() => sync(chain)}
                                    >
                                        {syncingFor === chainKey ? 'Синхронизация…' : 'Синхронизировать'}
                                    </button>
                                </div>
                            )}

                            <div style={{ marginTop: 12 }}>
                                <button
                                    className="btn btn-secondary btn-sm"
                                    onClick={() => setOpenKey(isOpen ? null : chainKey)}
                                >
                                    {isOpen ? 'Свернуть' : 'Показать движения и начисления'}
                                </button>
                            </div>

                            {isOpen && (
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16, marginTop: 12 }}>
                                    <div>
                                        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Движения</div>
                                        <TanStackDataTable
                                            columns={movementColumns}
                                            data={chain.movements}
                                            exportName={`loan-chain-${chain.contract_number}`}
                                        />
                                    </div>
                                    <div>
                                        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Начисление по месяцам</div>
                                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 8 }}>
                                            Считается по календарным месяцам по дням — это база для ОПиУ.
                                        </div>
                                        <TanStackDataTable
                                            columns={monthlyColumns}
                                            data={chain.monthly}
                                            exportName={`loan-chain-${chain.contract_number}-monthly`}
                                        />
                                    </div>
                                </div>
                            )}
                        </div>
                    );
                })
            )}
        </div>
    );
}
