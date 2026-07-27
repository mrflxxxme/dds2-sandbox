'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import type { LoanDetail, LoanPaymentType } from '@/types/api';
import { money, ratePct, fmtDate, STATUS_LABEL, STATUS_BADGE, entityLabel } from '../loanFmt';
import LoanFormModal from '../LoanFormModal';
import ExtendModal from '../ExtendModal';
import LoanSchedule from '../LoanSchedule';

const PAY_LABEL: Record<LoanPaymentType, string> = {
    DISBURSEMENT: 'Выдача',
    PRINCIPAL_REPAY: 'Возврат тела',
    INTEREST_PAY: 'Выплата %',
    PENALTY: 'Пени',
    COMMISSION: 'Комиссия',
};

export default function LoanDetailPage() {
    const params = useParams();
    const slug = params.slug as string;
    const id = Number(params.id);
    const router = useRouter();

    const [detail, setDetail] = useState<LoanDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [editing, setEditing] = useState(false);
    const [extending, setExtending] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setDetail(await api.getLoan(id));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => { load(); }, [load]);

    if (loading && !detail) {
        return <div className="animate-in"><div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div></div>;
    }
    if (error && !detail) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 24, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between' }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
                </div>
            </div>
        );
    }
    if (!detail) return null;

    const d = detail;
    const overdue = d.status === 'ACTIVE' && d.days_to_maturity != null && d.days_to_maturity < 0;

    return (
        <div className="animate-in">
            <div style={{ marginBottom: 12 }}>
                <button className="btn btn-secondary btn-sm" onClick={() => router.push(`/p/${slug}/loans`)}>← К займам</button>
            </div>

            <div className="page-header">
                <div>
                    <h1 className="page-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        {d.counterparty_name ?? `#${d.counterparty_id}`}
                        <span className={`badge ${STATUS_BADGE[d.status]}`}>{STATUS_LABEL[d.status]}</span>
                        {d.entity_type && <span className={`badge ${d.entity_type === 'IP' ? 'badge-info' : 'badge-secondary'}`}>{entityLabel(d.entity_type)}</span>}
                    </h1>
                    <p className="page-subtitle">
                        Договор <span style={{ fontFamily: 'monospace' }}>{d.contract_number}</span>
                        {d.parent_loan_id && (
                            <> · <button className="btn-link" style={linkBtn} onClick={() => router.push(`/p/${slug}/loans/${d.parent_loan_id}`)}>← предыдущий договор</button></>
                        )}
                        {d.has_extension && <> · продлён</>}
                    </p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => setEditing(true)}>✏ Редактировать</button>
                    {d.status === 'ACTIVE' && <button className="btn btn-primary btn-sm" onClick={() => setExtending(true)}>🔁 Продлить</button>}
                </div>
            </div>

            {/* Summary grid */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 12, marginBottom: 16 }}>
                <Metric label="Сумма займа" value={`${money(d.principal)} ₽`} />
                <Metric label="Остаток тела" value={`${money(d.remaining_principal)} ₽`} accent="#0071e3" />
                <Metric label="Ставка" value={ratePct(d.rate)} />
                <Metric label="Начислено % (период)" value={`${money(d.accrued_interest)} ₽`} accent="#ff3b30" />
                <Metric label="Проценты в месяц" value={`${money(d.monthly_interest)} ₽`} accent="#ff9500" />
                <Metric label="Выплачено %" value={`${money(d.schedule_summary.interest_paid)} ₽`} />
                <Metric label="Возвращено тела" value={`${money(d.schedule_summary.principal_paid)} ₽`} />
                <Metric label="Дата начала" value={fmtDate(d.start_date)} />
                <Metric label="Дата возврата" value={fmtDate(d.maturity_date)} accent={overdue ? '#ff3b30' : undefined} sub={overdue ? 'просрочен' : (d.days_to_maturity != null ? `через ${d.days_to_maturity} дн` : undefined)} />
                <Metric label="След. выплата %" value={fmtDate(d.next_interest_date)} />
                {d.lender_bank && <Metric label="Банк выплат" value={d.lender_bank} />}
            </div>

            {/* Payments */}
            <div className="glass-card">
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>История платежей</h3>
                {d.payments.length === 0 ? (
                    <div style={{ color: 'var(--color-text-dim)', padding: 12 }}>Платежей пока нет.</div>
                ) : (
                    <table className="data-table" style={{ width: '100%' }}>
                        <thead>
                            <tr>
                                <th style={{ textAlign: 'left' }}>Дата</th>
                                <th style={{ textAlign: 'left' }}>Тип</th>
                                <th style={{ textAlign: 'right' }}>Сумма</th>
                            </tr>
                        </thead>
                        <tbody>
                            {d.payments.map((p) => (
                                <tr key={p.id}>
                                    <td>{fmtDate(p.paid_at)}</td>
                                    <td><span className={`badge ${p.payment_type === 'INTEREST_PAY' ? 'badge-warning' : p.payment_type === 'PRINCIPAL_REPAY' ? 'badge-success' : 'badge-secondary'}`}>{PAY_LABEL[p.payment_type]}</span></td>
                                    <td style={{ textAlign: 'right', fontWeight: 600 }}>{money(p.amount)} {p.currency}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            <div style={{ marginTop: 16 }}>
                <LoanSchedule loanId={id} />
            </div>

            {d.notes && (
                <div className="glass-card" style={{ marginTop: 16 }}>
                    <h3 style={{ fontSize: 14, fontWeight: 600, margin: '0 0 6px' }}>Заметки</h3>
                    <div style={{ color: 'var(--color-text)', whiteSpace: 'pre-wrap' }}>{d.notes}</div>
                </div>
            )}

            {editing && (
                <LoanFormModal mode="edit" loan={d} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); load(); }} />
            )}
            {extending && (
                <ExtendModal loan={d} onClose={() => setExtending(false)} onExtended={(newId) => { setExtending(false); router.push(`/p/${slug}/loans/${newId}`); }} />
            )}
        </div>
    );
}

const linkBtn: React.CSSProperties = { background: 'none', border: 'none', padding: 0, color: 'var(--color-accent)', cursor: 'pointer', font: 'inherit' };

function Metric({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: string }) {
    return (
        <div className="glass-card" style={{ padding: '12px 16px' }}>
            <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: accent ?? 'var(--color-text)' }}>{value}</div>
            {sub && <div style={{ fontSize: 12, color: accent ?? 'var(--color-text-dim)', marginTop: 2 }}>{sub}</div>}
        </div>
    );
}
