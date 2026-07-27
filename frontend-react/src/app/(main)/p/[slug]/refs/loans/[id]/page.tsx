'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import LoanScheduleChart from '@/components/LoanScheduleChart';
import type {
    LoanDetail, LoanDirection, LoanStatus, LoanUpdate, LoanPayment, LoanPaymentType,
} from '@/types/api';

const DIRECTION_LABELS: Record<LoanDirection, string> = {
    INCOMING: 'Получен',
    OUTGOING: 'Выдан',
    AFFILIATED: 'Аффилированный',
};

const STATUS_LABELS: Record<LoanStatus, string> = {
    ACTIVE: 'Активный',
    CLOSED: 'Закрыт',
    DEFAULTED: 'Дефолт',
};

const PAYMENT_TYPE_LABELS: Record<LoanPaymentType, string> = {
    DISBURSEMENT: 'Выдача',
    PRINCIPAL_REPAY: 'Погашение основного долга',
    INTEREST_PAY: 'Уплата процентов',
    PENALTY: 'Штраф',
    COMMISSION: 'Комиссия',
};

const PAYMENT_TYPE_BADGES: Record<LoanPaymentType, string> = {
    DISBURSEMENT: 'badge-info',
    PRINCIPAL_REPAY: 'badge-success',
    INTEREST_PAY: 'badge-warning',
    PENALTY: 'badge-danger',
    COMMISSION: 'badge-secondary',
};

export default function LoanDetailPage() {
    const params = useParams();
    const slug = params.slug as string;
    const id = Number(params.id);
    const router = useRouter();

    const [detail, setDetail] = useState<LoanDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Edit
    const [editing, setEditing] = useState(false);
    const [editForm, setEditForm] = useState<LoanUpdate>({});
    const [saving, setSaving] = useState(false);

    const loadDetail = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const d = await api.getLoan(id);
            setDetail(d);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => { loadDetail(); }, [loadDetail]);

    const handleSave = async () => {
        setSaving(true);
        try {
            await api.updateLoan(id, editForm);
            setEditing(false);
            await loadDetail();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    if (loading && !detail) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            </div>
        );
    }

    if (error && !detail) {
        return (
            <div className="animate-in">
                <div
                    className="glass-card"
                    style={{ padding: 32, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                >
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={loadDetail}>Повторить</button>
                </div>
            </div>
        );
    }

    if (!detail) return null;

    const payments = detail.payments ?? [];
    const summary = detail.schedule_summary ?? {
        principal_paid: 0, interest_paid: 0, penalty_paid: 0, remaining: 0,
    };

    return (
        <div className="animate-in">
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => router.push(`/p/${slug}/refs/loans`)}
                    >
                        ← Назад
                    </button>
                    <div>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                            <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>
                                Займ · {detail.contract_number}
                            </h1>
                            <span className={`badge ${detail.status === 'ACTIVE' ? 'badge-success' : detail.status === 'CLOSED' ? 'badge-secondary' : 'badge-danger'}`}>
                                {STATUS_LABELS[detail.status]}
                            </span>
                            <span
                                className={`badge ${detail.direction === 'INCOMING' ? 'badge-info' : detail.direction === 'OUTGOING' ? 'badge-warning' : 'badge-secondary'}`}
                            >
                                {DIRECTION_LABELS[detail.direction]}
                            </span>
                        </div>
                        {detail.counterparty_name && (
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>
                                Контрагент:{' '}
                                <button
                                    className="btn btn-secondary btn-sm"
                                    style={{ background: 'none', border: 'none', padding: 0, fontWeight: 500, cursor: 'pointer', color: 'var(--color-accent)' }}
                                    onClick={() => router.push(`/p/${slug}/refs/counterparty/${detail.counterparty_id}`)}
                                >
                                    {detail.counterparty_name}
                                </button>
                                {detail.counterparty_inn && (
                                    <span style={{ fontFamily: 'monospace', marginLeft: 8, color: 'var(--color-text-dim)' }}>
                                        ИНН: {detail.counterparty_inn}
                                    </span>
                                )}
                            </div>
                        )}
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => {
                            setEditForm({
                                principal: detail.principal,
                                currency: detail.currency,
                                rate: detail.rate,
                                direction: detail.direction,
                                contract_number: detail.contract_number,
                                contract_date: detail.contract_date,
                                start_date: detail.start_date,
                                maturity_date: detail.maturity_date,
                                status: detail.status,
                                notes: detail.notes,
                            });
                            setEditing(true);
                        }}
                    >
                        Изменить
                    </button>
                </div>
            </div>

            {error && (
                <div style={{ color: 'var(--color-danger)', marginBottom: 16, fontSize: 13 }}>{error}</div>
            )}

            {/* Edit form */}
            {editing && (
                <div className="glass-card" style={{ marginBottom: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                        <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Редактировать займ</h3>
                        <button className="btn btn-secondary btn-sm" onClick={() => setEditing(false)}>✕</button>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                        <div className="form-group">
                            <label className="form-label">Сумма</label>
                            <input
                                type="number"
                                className="form-input"
                                value={editForm.principal ?? ''}
                                onChange={e => setEditForm(f => ({ ...f, principal: parseFloat(e.target.value) || 0 }))}
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Валюта</label>
                            <select
                                className="form-input"
                                value={editForm.currency ?? 'RUB'}
                                onChange={e => setEditForm(f => ({ ...f, currency: e.target.value }))}
                            >
                                <option value="RUB">RUB</option>
                                <option value="CNY">CNY</option>
                                <option value="USD">USD</option>
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label">Ставка % год.</label>
                            <input
                                type="number"
                                step="0.01"
                                className="form-input"
                                value={editForm.rate != null ? (editForm.rate * 100).toFixed(2) : ''}
                                onChange={e => setEditForm(f => ({ ...f, rate: e.target.value ? parseFloat(e.target.value) / 100 : null }))}
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Статус</label>
                            <select
                                className="form-input"
                                value={editForm.status ?? 'ACTIVE'}
                                onChange={e => setEditForm(f => ({ ...f, status: e.target.value as LoanStatus }))}
                            >
                                <option value="ACTIVE">Активный</option>
                                <option value="CLOSED">Закрыт</option>
                                <option value="DEFAULTED">Дефолт</option>
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label">Дата договора</label>
                            <input
                                type="date"
                                className="form-input"
                                value={editForm.contract_date ?? ''}
                                onChange={e => setEditForm(f => ({ ...f, contract_date: e.target.value }))}
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Дата погашения</label>
                            <input
                                type="date"
                                className="form-input"
                                value={editForm.maturity_date ?? ''}
                                onChange={e => setEditForm(f => ({ ...f, maturity_date: e.target.value || null }))}
                            />
                        </div>
                        <div className="form-group" style={{ gridColumn: '1/-1' }}>
                            <label className="form-label">Примечания</label>
                            <input
                                className="form-input"
                                value={editForm.notes ?? ''}
                                onChange={e => setEditForm(f => ({ ...f, notes: e.target.value || null }))}
                            />
                        </div>
                    </div>
                    <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
                        <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving}>
                            {saving ? 'Сохранение...' : 'Сохранить'}
                        </button>
                        <button className="btn btn-secondary btn-sm" onClick={() => setEditing(false)}>Отмена</button>
                    </div>
                </div>
            )}

            {/* KPI cards */}
            <div
                style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
                    gap: 16,
                    marginBottom: 20,
                }}
            >
                <div className="glass-card" style={{ padding: '12px 16px' }}>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>Основной долг</div>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>
                        {formatNumber(detail.principal)} {detail.currency}
                    </div>
                </div>
                <div className="glass-card" style={{ padding: '12px 16px' }}>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>Ставка</div>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>
                        {detail.rate != null ? `${(detail.rate * 100).toFixed(2)}%` : '—'}
                    </div>
                </div>
                <div className="glass-card" style={{ padding: '12px 16px' }}>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>Погашено основного</div>
                    <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-success)' }}>
                        {formatNumber(summary.principal_paid)}
                    </div>
                </div>
                <div className="glass-card" style={{ padding: '12px 16px' }}>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>Уплачено процентов</div>
                    <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-warning)' }}>
                        {formatNumber(summary.interest_paid)}
                    </div>
                </div>
                <div className="glass-card" style={{ padding: '12px 16px' }}>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>Остаток долга</div>
                    <div
                        style={{
                            fontSize: 20, fontWeight: 700,
                            color: summary.remaining > 0 ? 'var(--color-danger)' : 'var(--color-success)',
                        }}
                    >
                        {formatNumber(summary.remaining)}
                    </div>
                </div>
            </div>

            {/* Loan info */}
            <div className="glass-card" style={{ marginBottom: 20, padding: 16 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
                    <div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>Дата договора</div>
                        <div style={{ fontSize: 14, fontWeight: 500 }}>{formatDate(detail.contract_date)}</div>
                    </div>
                    <div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>Дата начала</div>
                        <div style={{ fontSize: 14, fontWeight: 500 }}>{formatDate(detail.start_date)}</div>
                    </div>
                    <div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>Дата погашения</div>
                        <div style={{ fontSize: 14, fontWeight: 500 }}>{detail.maturity_date ? formatDate(detail.maturity_date) : '—'}</div>
                    </div>
                    {detail.notes && (
                        <div style={{ gridColumn: '1/-1' }}>
                            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>Примечания</div>
                            <div style={{ fontSize: 14 }}>{detail.notes}</div>
                        </div>
                    )}
                </div>
            </div>

            {/* Schedule chart */}
            <div style={{ marginBottom: 20 }}>
                <LoanScheduleChart payments={payments} principal={detail.principal} currency={detail.currency} />
            </div>

            {/* Payments list */}
            <div className="glass-card" style={{ padding: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Платежи ({payments.length})</h3>
                    {payments.length > 0 && (
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => exportToExcel(payments as unknown as Record<string, any>[], 'loan_payments')}
                        >
                            Excel
                        </button>
                    )}
                </div>
                {payments.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>
                        Платежей пока нет
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        {payments
                            .slice()
                            .sort((a, b) => b.paid_at.localeCompare(a.paid_at))
                            .map((p: LoanPayment) => (
                                <div
                                    key={p.id}
                                    style={{
                                        display: 'flex',
                                        justifyContent: 'space-between',
                                        alignItems: 'center',
                                        padding: '10px 0',
                                        borderBottom: '1px solid var(--color-border)',
                                    }}
                                >
                                    <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                                        <span
                                            className={`badge ${PAYMENT_TYPE_BADGES[p.payment_type]}`}
                                            style={{ fontSize: 11 }}
                                        >
                                            {PAYMENT_TYPE_LABELS[p.payment_type]}
                                        </span>
                                        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                            {formatDate(p.paid_at)}
                                        </span>
                                        {p.transaction_id && (
                                            <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                                                #tx-{p.transaction_id}
                                            </span>
                                        )}
                                    </div>
                                    <div style={{ fontSize: 14, fontWeight: 600 }}>
                                        {formatNumber(p.amount)} {p.currency}
                                    </div>
                                </div>
                            ))}
                    </div>
                )}
            </div>
        </div>
    );
}
