import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { AdsAccountBalance, AdsManagerCampaign } from '@/types/api';
import { IcWallet } from './icons';
import { fmt, humanizeAdsError, useOverlayClose } from './adsShared';

// Стили — как в ScheduleModal (одна визуальная семья окон раздела)
const CARD: React.CSSProperties = { border: '1px solid #e5e7eb', borderRadius: 12, background: '#f9fafb', padding: '18px 20px' };
const CARD_TITLE: React.CSSProperties = { fontSize: 13, fontWeight: 700, color: '#4b5563', textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 16 };
const FIELD_LABEL: React.CSSProperties = { fontSize: 14, color: '#374151', display: 'block', fontWeight: 500 };
const INPUT: React.CSSProperties = { marginTop: 6, background: '#fff', border: '1px solid #d1d5db', borderRadius: 8, padding: '10px 12px', fontSize: 16, color: '#111827', width: '100%', boxSizing: 'border-box' };
const HINT: React.CSSProperties = { fontSize: 13, color: '#6b7280', marginTop: 12, lineHeight: 1.45 };

/** Минимальная сумма пополнения у WB — её же проверяет бэкенд. */
export const MIN_DEPOSIT = 1000;
/** Быстрые суммы: типовой дневной долив кампании. */
const QUICK_SUMS = [1000, 2000, 5000, 10000];

/** Источники списания WB (параметр type в /adv/v1/budget/deposit). */
const SOURCES: { key: number; label: string; hint: string; wallet: keyof Pick<AdsAccountBalance, 'balance' | 'net'> }[] = [
    { key: 0, label: 'Счёт', hint: 'Счёт кабинета Продвижения — деньги, которые вы завели на рекламу', wallet: 'balance' },
    { key: 1, label: 'Баланс', hint: 'Баланс взаиморасчётов — удержание из будущих продаж', wallet: 'net' },
];

/** Модалка «Пополнить бюджет»: сумма + источник списания, деньги уходят сразу.
 *  Кошелёк кабинета (счёт / баланс / бонусы) тянем из WB, чтобы было видно, откуда спишется. */
export default function DepositModal({ campaign, onClose, onDeposited }: {
    campaign: AdsManagerCampaign;
    onClose: () => void;
    onDeposited: (budgetAfter: number | null, amount: number) => void;
}) {
    const [amount, setAmount] = useState<string>(String(MIN_DEPOSIT));
    const [source, setSource] = useState(0);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    // null — грузим кошелёк, undefined — WB не отдал (не блокирует пополнение)
    const [wallet, setWallet] = useState<AdsAccountBalance | null | undefined>(null);

    useEffect(() => {
        const controller = new AbortController();
        api.getAdsBalance()
            .then(w => { if (!controller.signal.aborted) setWallet(w.ok ? w : undefined); })
            .catch(() => { if (!controller.signal.aborted) setWallet(undefined); });
        return () => controller.abort();
    }, []);

    // Подложка закрывает окно только «настоящим» кликом по ней (не концом выделения текста)
    const overlay = useOverlayClose(onClose);

    const value = Math.floor(Number(amount.replace(',', '.')) || 0);
    const tooSmall = value < MIN_DEPOSIT;
    const available = wallet ? wallet[SOURCES[source].wallet] : null;
    // WB решает сам, хватает ли денег — мы только предупреждаем, не блокируем
    const notEnough = available != null && value > available;

    const submit = async () => {
        if (tooSmall || busy) return;
        setBusy(true);
        setError('');
        try {
            const res = await api.depositCampaignBudget(campaign.campaign_id, value, source);
            onDeposited(res.budget_after, value);
            onClose();
        } catch (e) {
            setError(humanizeAdsError(e, 'Не удалось пополнить бюджет'));
            setBusy(false);
        }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }} {...overlay}>
            <div className="glass-card" style={{ width: 560, maxWidth: '96vw', maxHeight: '92vh', display: 'flex', flexDirection: 'column', padding: 0, background: '#fff', overflow: 'hidden' }} onClick={e => e.stopPropagation()}>
                {/* Шапка */}
                <div style={{ padding: '22px 28px 16px', borderBottom: '1px solid #eef0f2' }}>
                    <h3 style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 21, fontWeight: 700, margin: '0 0 4px', color: '#111827' }}><IcWallet size={20} />Пополнить бюджет</h3>
                    <div style={{ fontSize: 14, color: '#6b7280' }}>
                        {campaign.name || `#${campaign.campaign_id}`} · #{campaign.campaign_id} · остаток {fmt(campaign.budget)} ₽
                    </div>
                </div>

                {/* Тело */}
                <div style={{ padding: 24, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 18 }}>
                    <div style={CARD}>
                        <div style={CARD_TITLE}>Сумма пополнения</div>
                        <label style={FIELD_LABEL}>
                            ₽ (минимум {fmt(MIN_DEPOSIT)})
                            <input type="number" min={MIN_DEPOSIT} step={100} value={amount} autoFocus
                                aria-label="Сумма пополнения, ₽"
                                onChange={e => setAmount(e.target.value)}
                                onKeyDown={e => { if (e.key === 'Enter') submit(); }}
                                style={{ ...INPUT, borderColor: tooSmall ? '#fca5a5' : '#d1d5db' }} />
                        </label>
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                            {QUICK_SUMS.map(s => (
                                <button key={s} onClick={() => setAmount(String(s))} type="button"
                                    style={{ padding: '6px 14px', fontSize: 13, fontWeight: 600, borderRadius: 24, cursor: 'pointer', border: `1px solid ${value === s ? '#3b82f6' : '#e5e7eb'}`, background: value === s ? '#eff6ff' : '#fff', color: value === s ? '#1d4ed8' : '#374151' }}>
                                    +{fmt(s)} ₽
                                </button>
                            ))}
                        </div>
                        {tooSmall && <div style={{ ...HINT, color: '#ef4444' }}>WB не принимает пополнение меньше {fmt(MIN_DEPOSIT)} ₽.</div>}
                    </div>

                    <div style={CARD}>
                        <div style={CARD_TITLE}>Откуда списать</div>
                        <div style={{ display: 'inline-flex', border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}>
                            {SOURCES.map(s => (
                                <button key={s.key} onClick={() => setSource(s.key)} type="button" title={s.hint}
                                    aria-pressed={source === s.key}
                                    style={{ padding: '8px 20px', fontSize: 14, fontWeight: 500, border: 'none', cursor: 'pointer', background: source === s.key ? '#3b82f6' : '#fff', color: source === s.key ? '#fff' : '#374151' }}>
                                    {s.label}
                                </button>
                            ))}
                        </div>
                        <div style={HINT}>
                            {SOURCES[source].hint}.{' '}
                            {wallet === null ? 'Смотрим кошелёк кабинета…'
                                : wallet === undefined ? 'Остатки кабинета WB сейчас не отдаёт — сумму проверит сам WB при списании.'
                                    : <>Сейчас там <b style={{ color: notEnough ? '#ef4444' : '#111827' }}>{fmt(available ?? 0)} ₽</b>{wallet.bonus > 0 ? <> · бонусы {fmt(wallet.bonus)} ₽ (списываются в кабинете WB)</> : null}.</>}
                        </div>
                        {notEnough && (
                            <div style={{ ...HINT, color: '#ef4444' }}>
                                Запрошено больше, чем есть на источнике — WB, скорее всего, откажет. Уменьшите сумму или пополните кабинет на WB.
                            </div>
                        )}
                    </div>

                    {error &&<div style={{ fontSize: 13.5, color: '#ef4444' }}>{error}</div>}
                </div>

                {/* Футер */}
                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', padding: '16px 28px', borderTop: '1px solid #eef0f2' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={busy}>Отмена</button>
                    <button className="btn btn-primary" onClick={submit} disabled={busy || tooSmall}>
                        {busy ? 'Пополняем…' : `Пополнить на ${fmt(value)} ₽`}
                    </button>
                </div>
            </div>
        </div>
    );
}
