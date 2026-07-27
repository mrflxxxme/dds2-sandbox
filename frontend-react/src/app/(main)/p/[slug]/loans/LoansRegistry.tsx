'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import TanStackDataTable from '@/components/TanStackDataTable';
import { api } from '@/lib/api';
import type { Loan, LoanListResponse, LoanStatus, LoanDirection, LoanEntityType } from '@/types/api';
import { money, ratePct, fmtDate, STATUS_LABEL, STATUS_BADGE, entityLabel } from './loanFmt';

export default function LoansRegistry({ nonce }: { nonce: number }) {
    const params = useParams();
    const slug = params.slug as string;
    const router = useRouter();

    const [data, setData] = useState<LoanListResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const [status, setStatus] = useState<LoanStatus | ''>('');
    const [direction, setDirection] = useState<LoanDirection | ''>('INCOMING');
    const [entity, setEntity] = useState<LoanEntityType | ''>('');
    const [search, setSearch] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const res = await api.listLoans({
                status: status || undefined,
                direction: direction || undefined,
                entity_type: entity || undefined,
                limit: 500,
            });
            setData(res);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [status, direction, entity]);

    useEffect(() => { load(); }, [load, nonce]);

    const items = useMemo(() => {
        const all = data?.items ?? [];
        if (!search.trim()) return all;
        const q = search.toLowerCase();
        return all.filter((l) =>
            (l.counterparty_name ?? '').toLowerCase().includes(q) ||
            (l.contract_number ?? '').toLowerCase().includes(q)
        );
    }, [data, search]);

    const columns = [
        {
            key: 'counterparty_name', label: 'Заёмщик',
            getValue: (r: Loan) => r.counterparty_name ?? `#${r.counterparty_id}`,
            render: (_v: unknown, r: Loan) => (
                <button
                    className="btn-link"
                    style={{ background: 'none', border: 'none', padding: 0, fontWeight: 600, cursor: 'pointer', textAlign: 'left', color: 'var(--color-accent)' }}
                    onClick={() => router.push(`/p/${slug}/loans/${r.id}`)}
                >
                    {r.counterparty_name ?? `#${r.counterparty_id}`}
                </button>
            ),
        },
        {
            key: 'entity_type', label: 'Тип',
            getValue: (r: Loan) => entityLabel(r.entity_type),
            render: (_v: unknown, r: Loan) => r.entity_type
                ? <span className={`badge ${r.entity_type === 'IP' ? 'badge-info' : 'badge-secondary'}`}>{entityLabel(r.entity_type)}</span>
                : <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        {
            key: 'contract_number', label: 'Договор',
            render: (v: string, r: Loan) => (
                <span style={{ fontFamily: 'monospace', fontSize: 12 }}>
                    {v}{r.parent_loan_id ? ' 🔁' : ''}
                </span>
            ),
        },
        { key: 'principal', label: 'Сумма', align: 'right' as const, getValue: (r: Loan) => Number(r.principal), render: (_v: unknown, r: Loan) => `${money(r.principal)} ₽` },
        { key: 'remaining_principal', label: 'Остаток', align: 'right' as const, getValue: (r: Loan) => Number(r.remaining_principal ?? 0), render: (_v: unknown, r: Loan) => `${money(r.remaining_principal)} ₽` },
        { key: 'rate', label: 'Ставка', align: 'right' as const, getValue: (r: Loan) => Number(r.rate ?? 0), render: (_v: unknown, r: Loan) => ratePct(r.rate) },
        {
            key: 'accrued_interest', label: 'Начислено % (период)', align: 'right' as const,
            getValue: (r: Loan) => Number(r.accrued_interest ?? 0),
            render: (_v: unknown, r: Loan) => (
                <span title={r.accrual_period_start ? `Период ${fmtDate(r.accrual_period_start)} → ${fmtDate(r.accrual_period_end)}` : undefined}>
                    {money(r.accrued_interest)} ₽
                </span>
            ),
        },
        { key: 'interest_due_period', label: 'К выплате', align: 'right' as const, getValue: (r: Loan) => Number(r.interest_due_period ?? 0), render: (_v: unknown, r: Loan) => `${money(r.interest_due_period)} ₽` },
        { key: 'start_date', label: 'С', render: (v: string) => fmtDate(v) },
        {
            key: 'maturity_date', label: 'Возврат',
            render: (v: string | null, r: Loan) => {
                if (!v) return '—';
                const overdue = r.status === 'ACTIVE' && r.days_to_maturity != null && r.days_to_maturity < 0;
                return <span style={overdue ? { color: 'var(--color-danger)', fontWeight: 600 } : undefined}>{fmtDate(v)}{overdue ? ' ⚠' : ''}</span>;
            },
        },
        {
            key: 'status', label: 'Статус',
            render: (v: LoanStatus) => <span className={`badge ${STATUS_BADGE[v]}`}>{STATUS_LABEL[v]}</span>,
        },
    ];

    return (
        <div>
            {/* Filters */}
            <div className="glass-card" style={{ marginBottom: 16, padding: '12px 16px' }}>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                    <input className="form-input" style={{ width: 220 }} placeholder="Поиск: заёмщик / договор" value={search} onChange={(e) => setSearch(e.target.value)} />
                    <select className="form-input" style={{ width: 150 }} value={status} onChange={(e) => setStatus(e.target.value as LoanStatus | '')}>
                        <option value="">Все статусы</option>
                        <option value="ACTIVE">Активные</option>
                        <option value="CLOSED">Закрытые</option>
                        <option value="DEFAULTED">Дефолт</option>
                    </select>
                    <select className="form-input" style={{ width: 160 }} value={entity} onChange={(e) => setEntity(e.target.value as LoanEntityType | '')}>
                        <option value="">Физ + ИП</option>
                        <option value="PHYSICAL">Физлицо</option>
                        <option value="IP">ИП</option>
                    </select>
                    <select className="form-input" style={{ width: 160 }} value={direction} onChange={(e) => setDirection(e.target.value as LoanDirection | '')}>
                        <option value="INCOMING">Полученные</option>
                        <option value="OUTGOING">Выданные</option>
                        <option value="">Все</option>
                    </select>
                    <div style={{ marginLeft: 'auto', color: 'var(--color-text-dim)', fontSize: 13 }}>
                        Найдено: {items.length}
                    </div>
                </div>
            </div>

            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between' }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
                </div>
            )}

            {loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>
            ) : items.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>📒</div>
                    <div style={{ fontSize: 16, fontWeight: 600 }}>Займы не найдены</div>
                    <div style={{ color: 'var(--color-text-dim)', marginTop: 6 }}>Измените фильтры или добавьте займ.</div>
                </div>
            ) : (
                <TanStackDataTable columns={columns} data={items} exportName="loans" enableSorting enablePagination pageSize={50} />
            )}
        </div>
    );
}
