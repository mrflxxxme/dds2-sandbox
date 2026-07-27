'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';
import TanStackDataTable from '@/components/TanStackDataTable';
import { api } from '@/lib/api';
import type { LoanByLenderResponse, LoanLenderRollup, LenderAccessInfo, LenderAccessCreated } from '@/types/api';
import { money, ratePct, fmtDate, entityLabel } from './loanFmt';

export default function LoansLenders({ nonce, onChanged }: { nonce: number; onChanged: () => void }) {
    const params = useParams();
    const slug = String(params?.slug ?? 'default');
    const [showArchived, setShowArchived] = useState(false);
    const [entity, setEntity] = useState<'ALL' | 'IP' | 'PHYSICAL'>('ALL');
    const [period, setPeriod] = useState('');  // '' = текущий период
    const [data, setData] = useState<LoanByLenderResponse | null>(null);
    const [access, setAccess] = useState<Record<number, LenderAccessInfo>>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState<number | null>(null);
    const [creds, setCreds] = useState<LenderAccessCreated | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [byLender, accessList] = await Promise.all([
                api.loansByLender(period || undefined),
                api.listLenderAccess(),
            ]);
            setData(byLender);
            const map: Record<number, LenderAccessInfo> = {};
            accessList.items.forEach((a) => { map[a.counterparty_id] = a; });
            setAccess(map);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [period]);

    useEffect(() => { load(); }, [load, nonce]);

    const grant = async (cpId: number) => {
        setBusy(cpId);
        try {
            const res = await api.createLenderAccess({ counterparty_id: cpId });
            setCreds(res);
            await load();
            onChanged();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setBusy(null);
        }
    };

    const reset = async (cpId: number) => {
        if (!confirm('Сбросить пароль заёмщику? Текущий перестанет работать.')) return;
        setBusy(cpId);
        try {
            const res = await api.resetLenderPassword(cpId);
            setCreds(res);
            await load();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setBusy(null);
        }
    };

    const revoke = async (cpId: number) => {
        if (!confirm('Отозвать доступ заёмщика? Он не сможет войти.')) return;
        setBusy(cpId);
        try {
            await api.revokeLenderAccess(cpId);
            await load();
            onChanged();
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setBusy(null);
        }
    };

    const visible = useMemo(
        () => (data?.items ?? []).filter((r) => (showArchived || !r.is_archived)
            && (entity === 'ALL' || r.entity_type === entity)),
        [data, showArchived, entity],
    );
    // Итоги — по видимым строкам, чтобы фильтр ИП/физ менял и карточки
    const totals = useMemo(() => visible.reduce(
        (acc, r) => ({
            out: acc.out + Number(r.outstanding),
            accrued: acc.accrued + Number(r.accrued_interest),
            due: acc.due + Number(r.interest_due_period),
            live: acc.live + (r.is_archived ? 0 : 1),
        }),
        { out: 0, accrued: 0, due: 0, live: 0 },
    ), [visible]);
    const entityCard = (code: 'IP' | 'PHYSICAL') => {
        const e = data?.by_entity.find((x) => x.entity_type === code);
        return { due: e?.interest_due_period ?? 0, out: e?.outstanding ?? 0 };
    };
    // Последние 24 периода выплат (25-е число) для исторического среза
    const periodOptions = useMemo(() => {
        const now = new Date();
        const base = new Date(Date.UTC(now.getFullYear(), now.getMonth(), 25));
        if (now.getDate() < 25) base.setUTCMonth(base.getUTCMonth() - 1);
        return Array.from({ length: 24 }, (_, i) => {
            const d = new Date(base);
            d.setUTCMonth(d.getUTCMonth() - i + 1);
            return d.toISOString().slice(0, 10);
        });
    }, []);

    const columns = [
        {
            key: 'name', label: 'Заёмщик',
            render: (v: string, r: LoanLenderRollup) => (
                <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                    <Link href={`/p/${slug}/loans/lenders/${r.counterparty_id}`} style={{ fontWeight: 600, color: 'var(--color-accent)' }}>
                        {v}
                    </Link>
                    {r.is_archived && <span className="badge badge-secondary">архив</span>}
                </span>
            ),
        },
        {
            key: 'entity_type', label: 'Тип',
            getValue: (r: LoanLenderRollup) => entityLabel(r.entity_type),
            render: (_v: unknown, r: LoanLenderRollup) => r.entity_type
                ? <span className={`badge ${r.entity_type === 'IP' ? 'badge-info' : 'badge-secondary'}`}>{entityLabel(r.entity_type)}</span>
                : <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        { key: 'lender_bank', label: 'Банк', render: (v: string | null) => v || '—' },
        { key: 'outstanding', label: 'В займе', align: 'right' as const, getValue: (r: LoanLenderRollup) => Number(r.outstanding), render: (_v: unknown, r: LoanLenderRollup) => `${money(r.outstanding)} ₽` },
        { key: 'weighted_avg_rate', label: 'Ставка', align: 'right' as const, getValue: (r: LoanLenderRollup) => Number(r.weighted_avg_rate ?? 0), render: (_v: unknown, r: LoanLenderRollup) => ratePct(r.weighted_avg_rate) },
        { key: 'accrued_interest', label: 'Набежало', align: 'right' as const, getValue: (r: LoanLenderRollup) => Number(r.accrued_interest), render: (_v: unknown, r: LoanLenderRollup) => `${money(r.accrued_interest)} ₽` },
        { key: 'interest_due_period', label: 'К выплате', align: 'right' as const, getValue: (r: LoanLenderRollup) => Number(r.interest_due_period), render: (_v: unknown, r: LoanLenderRollup) => `${money(r.interest_due_period)} ₽` },
        { key: 'monthly_interest', label: '%/мес', align: 'right' as const, getValue: (r: LoanLenderRollup) => Number(r.monthly_interest), render: (_v: unknown, r: LoanLenderRollup) => `${money(r.monthly_interest)} ₽` },
        { key: 'active_count', label: 'Займов', align: 'right' as const, getValue: (r: LoanLenderRollup) => r.active_count, render: (_v: unknown, r: LoanLenderRollup) => `${r.active_count} / ${r.total_count}` },
        { key: 'next_interest_date', label: 'След. %', render: (_v: unknown, r: LoanLenderRollup) => fmtDate(r.next_interest_date) },
        { key: 'next_maturity_date', label: 'Возврат', render: (_v: unknown, r: LoanLenderRollup) => fmtDate(r.next_maturity_date) },
        {
            key: '_access', label: 'Доступ', sortable: false,
            render: (_v: unknown, r: LoanLenderRollup) => {
                const a = access[r.counterparty_id];
                const isBusy = busy === r.counterparty_id;
                if (!a) {
                    return <button className="btn btn-primary btn-sm" disabled={isBusy} onClick={() => grant(r.counterparty_id)}>{isBusy ? '…' : 'Выдать доступ'}</button>;
                }
                return (
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                        <span className={`badge ${a.is_active ? 'badge-success' : 'badge-secondary'}`} title={a.username ?? ''}>
                            {a.is_active ? `✓ ${a.username}` : 'отозван'}
                        </span>
                        <button className="btn btn-secondary btn-sm" disabled={isBusy} onClick={() => reset(r.counterparty_id)} title="Сбросить пароль">🔑</button>
                        {a.is_active
                            ? <button className="btn btn-secondary btn-sm" disabled={isBusy} onClick={() => revoke(r.counterparty_id)} title="Отозвать">🚫</button>
                            : <button className="btn btn-secondary btn-sm" disabled={isBusy} onClick={() => reset(r.counterparty_id)} title="Включить заново">↻</button>}
                    </div>
                );
            },
        },
    ];

    return (
        <div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
                <select
                    className="form-input"
                    style={{ width: 'auto', minWidth: 190 }}
                    value={period}
                    onChange={(e) => setPeriod(e.target.value)}
                >
                    <option value="">Текущий период</option>
                    {periodOptions.map((d) => (
                        <option key={d} value={d}>К выплате {fmtDate(d)}</option>
                    ))}
                </select>
                <select
                    className="form-input"
                    style={{ width: 'auto', minWidth: 140 }}
                    value={entity}
                    onChange={(e) => setEntity(e.target.value as 'ALL' | 'IP' | 'PHYSICAL')}
                >
                    <option value="ALL">Физ + ИП</option>
                    <option value="IP">Только ИП</option>
                    <option value="PHYSICAL">Только физлица</option>
                </select>
                <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                    {period
                        ? `Исторический срез: что было к выплате ${fmtDate(period)}`
                        : '«Набежало» — на сегодня, «К выплате» — за весь период целиком'}
                </span>
            </div>

            {data && (
                <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                            {entity === 'ALL' ? 'Всего в займах' : entity === 'IP' ? 'В займах у ИП' : 'В займах у физлиц'}
                        </div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(totals.out)} ₽</div>
                    </div>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}
                             title="Сколько процентов набежало с начала периода по сегодняшний день">
                            Набежало с {fmtDate(data.accrual_period_start)}
                        </div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(totals.accrued)} ₽</div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                            {period ? 'период закрыт' : 'растёт каждый день'}
                        </div>
                    </div>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}
                             title="Проценты за ВЕСЬ период 25→25 — сумма платежа на дату выплаты">
                            К выплате {fmtDate(data.accrual_period_end)}
                        </div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{money(totals.due)} ₽</div>
                        {entity === 'ALL' && (
                            <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                                ИП {money(entityCard('IP').due)} ₽ · физ {money(entityCard('PHYSICAL').due)} ₽
                            </div>
                        )}
                    </div>
                    <div className="glass-card" style={{ padding: '12px 18px', flex: '1 1 200px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Заёмщиков</div>
                        <div style={{ fontSize: 22, fontWeight: 700 }}>{totals.live}</div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                            в займе · {data.archived_count} в архиве
                        </div>
                    </div>
                </div>
            )}

            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between' }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
                </div>
            )}

            {data && data.archived_count > 0 && (
                <label style={{ display: 'inline-flex', gap: 8, alignItems: 'center', marginBottom: 12, cursor: 'pointer', fontSize: 13 }}>
                    <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
                    Показать архив ({data.archived_count})
                </label>
            )}

            {loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>
            ) : visible.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>👤</div>
                    <div style={{ fontSize: 16, fontWeight: 600 }}>
                        {(data?.items.length ?? 0) > 0 ? 'Все заёмщики в архиве' : 'Заёмщиков пока нет'}
                    </div>
                </div>
            ) : (
                <TanStackDataTable columns={columns} data={visible} exportName="lenders" enableSorting enablePagination pageSize={50} />
            )}

            {/* Credentials modal — shown once after grant/reset */}
            {creds && (
                <div className="modal-overlay" style={overlay} onClick={() => setCreds(null)}>
                    <div className="glass-card" style={{ maxWidth: 440, width: '90%' }} onClick={(e) => e.stopPropagation()}>
                        <h3 style={{ margin: '0 0 8px', fontSize: 17, fontWeight: 700 }}>Доступ выдан</h3>
                        <p style={{ color: 'var(--color-text-dim)', fontSize: 13, marginTop: 0 }}>
                            Передайте заёмщику <b>{creds.counterparty_name}</b> эти данные. Пароль показывается один раз.
                        </p>
                        <div style={{ display: 'grid', gap: 10, margin: '12px 0' }}>
                            <CredRow label="Ссылка на портал" value={`${origin()}/lender/login`} />
                            <CredRow label="Логин" value={creds.username ?? ''} />
                            <CredRow label="Пароль" value={creds.password ?? ''} mono />
                        </div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button className="btn btn-primary btn-sm" onClick={() => setCreds(null)}>Готово</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function origin(): string {
    return typeof window !== 'undefined' ? window.location.origin : '';
}

function CredRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'var(--color-bg-card)', borderRadius: 12, padding: '8px 12px', border: '1px solid var(--color-border)' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{label}</div>
                <div style={{ fontFamily: mono ? 'monospace' : undefined, fontSize: 14, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis' }}>{value}</div>
            </div>
            <button className="btn btn-secondary btn-sm" onClick={() => navigator.clipboard?.writeText(value)} title="Копировать">⧉</button>
        </div>
    );
}

const overlay: React.CSSProperties = {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex',
    alignItems: 'center', justifyContent: 'center', zIndex: 1000, padding: 16,
};
