'use client';

import { useCallback, useEffect, useState } from 'react';
import TanStackDataTable from '@/components/TanStackDataTable';
import { api } from '@/lib/api';
import type { LoanLentItem, LoanLentResponse } from '@/types/api';
import { money, ratePct } from './loanFmt';

/**
 * «Выдано» — обратная сторона портфеля: не сколько мы должны, а сколько должны нам.
 *
 * Живёт блоком на дашборде, а не отдельной вкладкой: это те же деньги под теми же
 * процентами, просто с другим знаком, и смотреть их надо рядом со стоимостью денег.
 */
export default function LoansLent({ nonce }: { nonce: number }) {
    const [data, setData] = useState<LoanLentResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setData(await api.loansLent());
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load, nonce]);

    // Блок дашборда, а не страница: пока грузится — молчим, чтобы не дёргать
    // макет скелетом ради второстепенной цифры.
    if (loading) return null;
    if (error) {
        return (
            <div className="glass-card" style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', color: 'var(--color-danger)' }}>
                <span>Выданные займы: {error}</span>
                <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
            </div>
        );
    }
    // Нет выданных займов — блока нет вовсе. Пустая карточка «нам никто не должен»
    // на экране долгов только шумит.
    if (!data || data.items.length === 0) return null;

    const columns = [
        {
            key: 'name', label: 'Получатель',
            render: (_v: unknown, r: LoanLentItem) => (
                <div>
                    <div style={{ fontWeight: 600 }}>{r.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                        {r.contract_number}
                        {r.mirror_project_name && (
                            <span className="badge badge-info" style={{ marginLeft: 6 }}>
                                🔗 {r.mirror_project_name}
                            </span>
                        )}
                    </div>
                </div>
            ),
        },
        {
            key: 'rate', label: 'Ставка', align: 'right' as const,
            getValue: (r: LoanLentItem) => Number(r.rate ?? 0),
            render: (_v: unknown, r: LoanLentItem) => ratePct(r.rate),
        },
        {
            key: 'outstanding', label: 'Тело', align: 'right' as const,
            getValue: (r: LoanLentItem) => Number(r.outstanding),
            render: (_v: unknown, r: LoanLentItem) => `${money(r.outstanding)} ₽`,
        },
        {
            key: 'accrued_total', label: 'Начислено %', align: 'right' as const,
            getValue: (r: LoanLentItem) => Number(r.accrued_total),
            render: (_v: unknown, r: LoanLentItem) => `${money(r.accrued_total)} ₽`,
        },
        {
            key: 'interest_received', label: 'Получено %', align: 'right' as const,
            getValue: (r: LoanLentItem) => Number(r.interest_received),
            render: (_v: unknown, r: LoanLentItem) => `${money(r.interest_received)} ₽`,
        },
        {
            key: 'interest_due', label: 'Долг по %', align: 'right' as const,
            getValue: (r: LoanLentItem) => Number(r.interest_due),
            render: (_v: unknown, r: LoanLentItem) => (
                <span style={{ color: Number(r.interest_due) > 0 ? 'var(--color-warning)' : 'var(--color-text-dim)' }}>
                    {money(r.interest_due)} ₽
                </span>
            ),
        },
        {
            key: 'total_due', label: 'Итого должен', align: 'right' as const,
            getValue: (r: LoanLentItem) => Number(r.total_due),
            render: (_v: unknown, r: LoanLentItem) => (
                <span style={{ fontWeight: 600 }}>{money(r.total_due)} ₽</span>
            ),
        },
        {
            key: 'accrued_month', label: 'За месяц', align: 'right' as const,
            getValue: (r: LoanLentItem) => Number(r.accrued_month),
            render: (_v: unknown, r: LoanLentItem) => `${money(r.accrued_month)} ₽`,
        },
    ];

    return (
        <div className="glass-card" style={{ marginBottom: 16 }}>
            <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 4px' }}>Выдано — сколько должны нам</h3>
            <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 12 }}>
                Займы, где кредитор — мы. Проценты считаются тем же движком, что и по нашим долгам,
                поэтому «за месяц» здесь можно вычитать из стоимости денег напрямую.
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 12 }}>
                <Cell label="Тело у получателей" value={`${money(data.total_outstanding)} ₽`} />
                <Cell label="Долг по процентам" value={`${money(data.total_interest_due)} ₽`} />
                <Cell label="Итого нам должны" value={`${money(data.total_due)} ₽`} strong />
                <Cell label="Доход за месяц" value={`${money(data.month_income)} ₽`} />
                <Cell label="Процентов получено" value={`${money(data.total_received)} ₽`} />
            </div>

            <TanStackDataTable
                columns={columns}
                data={data.items}
                exportName="loans-lent"
                enableSorting
                enablePagination={false}
            />
        </div>
    );
}

function Cell({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
    return (
        <div>
            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{label}</div>
            <div style={{ fontSize: strong ? 20 : 16, fontWeight: strong ? 700 : 600 }}>{value}</div>
        </div>
    );
}
