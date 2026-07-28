'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { LoanChain, LoanMirrorSide } from '@/types/api';
import { money, ratePct } from './loanFmt';

/**
 * Вторая сторона договора — займ между своими юрлицами.
 *
 * Живёт на карточке самого займа, а не в отдельном разделе: для проекта это
 * обычный заимодавец, и особенное тут только одно — тот же договор есть в книге
 * второго проекта с обратным знаком. Это свойство договора, а не повод для
 * параллельного экрана.
 */
export default function LoanMirrorBlock({ loanId, nonce }: { loanId: number; nonce: number }) {
    const [chain, setChain] = useState<LoanChain | null>(null);
    const [error, setError] = useState('');
    const [syncing, setSyncing] = useState(false);

    const load = useCallback(async () => {
        setError('');
        try {
            setChain(await api.loanChain(loanId));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
    }, [loanId]);

    useEffect(() => { load(); }, [load, nonce]);

    const sync = async () => {
        setSyncing(true);
        setError('');
        try {
            setChain(await api.syncLoanMirror(loanId));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка синхронизации');
        } finally {
            setSyncing(false);
        }
    };

    // Блок появляется только у займов, у которых вторая сторона реально заведена.
    const other = chain?.sides.find((s: LoanMirrorSide) => s.loan_id !== loanId);
    if (!chain || !other) return null;

    return (
        <div className="glass-card" style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Вторая сторона договора</h3>
                <span className={`badge ${other.direction === 'INCOMING' ? 'badge-danger' : 'badge-success'}`}>
                    {other.direction === 'INCOMING' ? 'у них долг' : 'у них актив'}
                </span>
            </div>
            <div style={{ fontSize: 13, color: 'var(--color-text-dim)', marginTop: 6 }}>
                Тот же договор ведётся в проекте <b>{other.project_name ?? `#${other.project_id}`}</b> —
                там он числится за «{other.counterparty_name ?? '—'}» с обратным знаком. Движения общие:
                введённые с любой стороны доезжают до второй.
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginTop: 12 }}>
                <Cell label="Тело" value={`${money(other.outstanding)} ₽`} />
                <Cell label="Начислено всего" value={`${money(other.accrued_total)} ₽`} />
                <Cell label="Уплачено" value={`${money(other.interest_paid)} ₽`} />
                <Cell
                    label="Долг по процентам"
                    value={`${money(other.interest_debt)} ₽`}
                    accent={Number(other.interest_debt) > 0 ? 'var(--color-warning)' : undefined}
                />
                <Cell label="Ставка" value={ratePct(other.current_rate)} />
            </div>

            {error && (
                <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 10 }}>{error}</div>
            )}
            {!chain.in_sync && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginTop: 12, flexWrap: 'wrap' }}>
                    <span style={{ color: 'var(--color-warning)', fontSize: 13 }}>
                        ⚠ {chain.sync_note ?? 'Книги двух сторон разошлись.'}
                    </span>
                    <button className="btn btn-primary btn-sm" disabled={syncing} onClick={sync}>
                        {syncing ? 'Синхронизация…' : 'Синхронизировать'}
                    </button>
                </div>
            )}
        </div>
    );
}

function Cell({ label, value, accent }: { label: string; value: string; accent?: string }) {
    return (
        <div>
            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{label}</div>
            <div style={{ fontSize: 15, fontWeight: 600, color: accent ?? 'var(--color-text)' }}>{value}</div>
        </div>
    );
}
