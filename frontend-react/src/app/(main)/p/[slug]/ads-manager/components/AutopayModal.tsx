import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/utils';
import type { AdsManagerCampaign, WbAutorefillSetting, WbAutorefillResponse } from '@/types/api';
import { IcWallet } from './icons';
import Switch from './Switch';
import { fmt, humanizeAdsError, useOverlayClose } from './adsShared';

// Стили — как в ScheduleModal (одна визуальная семья окон раздела)
const CARD: React.CSSProperties = { border: '1px solid #e5e7eb', borderRadius: 12, background: '#f9fafb', padding: '18px 20px' };
const CARD_TITLE: React.CSSProperties = { fontSize: 13, fontWeight: 700, color: '#4b5563', textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 16 };
const FIELD_LABEL: React.CSSProperties = { fontSize: 14, color: '#374151', display: 'block', fontWeight: 500 };
const INPUT: React.CSSProperties = { marginTop: 6, background: '#fff', border: '1px solid #d1d5db', borderRadius: 8, padding: '10px 12px', fontSize: 16, color: '#111827', width: '100%', boxSizing: 'border-box' };
const HINT: React.CSSProperties = { fontSize: 13, color: '#6b7280', marginTop: 12, lineHeight: 1.45 };

/** Минимум ВБ на разовый долив (проверяет и бэкенд, и сам кабинет). */
export const MIN_AUTOREFILL = 1000;

/** Форма до ответа кабинета: правило ещё не заводили. */
const EMPTY: WbAutorefillSetting = {
    enabled: false, threshold: 500, amount: MIN_AUTOREFILL, daily_limit: true, limit: 1,
    unified_account: true, status: null, history: [],
};

/** Источник долива в истории кабинета. */
const SOURCE_LABEL: Record<string, string> = { net: 'Баланс', account: 'Счёт', bonus: 'Промобонусы' };

/** Модалка «Автопополнение бюджета» — зеркало настройки кабинета ВБ.
 *  Доливает сам ВБ по своему правилу; мы читаем его из кабинета и пишем туда же
 *  (ads-gate /proxy/autorefill/v2). Своей логики доливов у ДДС нет. */
export default function AutopayModal({ campaign, onClose, onSaved }: {
    campaign: AdsManagerCampaign;
    onClose: () => void;
    onSaved?: (s: WbAutorefillSetting) => void;
}) {
    // null — ещё читаем настройку из кабинета
    const [resp, setResp] = useState<WbAutorefillResponse | null>(null);
    const [form, setForm] = useState<WbAutorefillSetting>(EMPTY);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [loadError, setLoadError] = useState('');

    useEffect(() => {
        const controller = new AbortController();
        api.getCampaignAutorefill(campaign.campaign_id)
            .then(r => {
                if (controller.signal.aborted) return;
                setResp(r);
                if (r.settings) setForm(r.settings);
            })
            .catch(e => {
                if (controller.signal.aborted) return;
                setResp({ session: 'ACTIVE', settings: null });
                setLoadError(humanizeAdsError(e, 'Не удалось прочитать настройку из кабинета ВБ'));
            });
        return () => controller.abort();
    }, [campaign.campaign_id]);

    // Подложка закрывает окно только «настоящим» кликом по ней (не концом выделения текста)
    const overlay = useOverlayClose(onClose);

    const session = resp?.session;
    const noAccess = session === 'EXPIRED' || session === 'NONE';
    const loading = resp === null;
    const tooSmall = form.enabled && form.amount < MIN_AUTOREFILL;
    const history = resp?.settings?.history ?? [];

    const save = async () => {
        setSaving(true);
        setError('');
        try {
            const res = await api.setCampaignAutorefill(campaign.campaign_id, {
                enabled: form.enabled, threshold: form.threshold, amount: form.amount,
                daily_limit: form.daily_limit, limit: form.limit, unified_account: form.unified_account,
            });
            onSaved?.(res.settings ?? form);
            onClose();
        } catch (e) {
            setError(humanizeAdsError(e, 'ВБ не принял настройку автопополнения'));
            setSaving(false);
        }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }} {...overlay}>
            <div className="glass-card" style={{ width: 700, maxWidth: '96vw', maxHeight: '92vh', display: 'flex', flexDirection: 'column', padding: 0, background: '#fff', overflow: 'hidden' }} onClick={e => e.stopPropagation()}>
                {/* Шапка */}
                <div style={{ padding: '22px 28px 16px', borderBottom: '1px solid #eef0f2' }}>
                    <h3 style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 21, fontWeight: 700, margin: '0 0 4px', color: '#111827' }}><IcWallet size={20} />Автопополнение бюджета</h3>
                    <div style={{ fontSize: 14, color: '#6b7280' }}>
                        {campaign.name || `#${campaign.campaign_id}`} · #{campaign.campaign_id} · настройка кабинета ВБ
                    </div>
                </div>

                {/* Тело (скроллится) */}
                <div style={{ padding: 24, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 18 }}>
                    {loading && <div style={{ fontSize: 14, color: '#6b7280' }}>Читаем настройку из кабинета ВБ…</div>}

                    {/* Нет доступа к кабинету — говорим прямо, а не показываем пустую форму:
                        «выключено» прочиталось бы как факт, и кампания осталась бы без бюджета. */}
                    {!loading && noAccess && (
                        <div style={{ ...CARD, background: '#fff7ed', border: '1px solid #fed7aa' }}>
                            <div style={{ fontSize: 13.5, color: '#9a3412', lineHeight: 1.45 }}>
                                {session === 'NONE'
                                    ? 'Доступ к кабинету ВБ не настроен — прочитать настройку автопополнения нечем. Добавьте доступ WB в «Интеграциях», и правило появится здесь.'
                                    : 'Доступ к кабинету ВБ истёк — настройки сейчас не видно и не изменить. Обновите доступ WB в «Интеграциях».'}
                                {' '}Само автопополнение при этом продолжает работать по правилу, сохранённому у ВБ.
                            </div>
                        </div>
                    )}

                    {!loading && !noAccess && loadError && (
                        <div style={{ ...CARD, background: '#fef2f2', border: '1px solid #fecaca' }}>
                            <div style={{ fontSize: 13.5, color: '#b91c1c', lineHeight: 1.45 }}>{loadError}</div>
                        </div>
                    )}

                    {!loading && !noAccess && !loadError && (
                        <>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                                <Switch on={form.enabled} ariaLabel="Пополнять автоматически" onClick={() => setForm(f => ({ ...f, enabled: !f.enabled }))} />
                                <span style={{ fontSize: 16, fontWeight: 500, color: form.enabled ? '#111827' : '#6b7280' }}>Пополнять автоматически</span>
                            </div>

                            <div style={CARD}>
                                <div style={CARD_TITLE}>Условия пополнения</div>
                                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                                    <label style={FIELD_LABEL}>
                                        Если бюджет меньше, ₽
                                        <input type="number" min={0} step={50} value={form.threshold} aria-label="Порог остатка, ₽"
                                            onChange={e => setForm(f => ({ ...f, threshold: Math.max(0, Number(e.target.value) || 0) }))} style={INPUT} />
                                    </label>
                                    <label style={FIELD_LABEL}>
                                        Пополнять на, ₽
                                        <input type="number" min={MIN_AUTOREFILL} step={50} value={form.amount} aria-label="Сумма долива, ₽"
                                            onChange={e => setForm(f => ({ ...f, amount: Math.max(0, Number(e.target.value) || 0) }))}
                                            style={{ ...INPUT, borderColor: tooSmall ? '#fca5a5' : '#d1d5db' }} />
                                    </label>
                                </div>
                                <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 15, color: '#374151', marginTop: 16, cursor: 'pointer' }}>
                                    <input type="checkbox" checked={form.daily_limit} style={{ width: 17, height: 17 }} aria-label="Ограничить число пополнений в день"
                                        onChange={e => setForm(f => ({ ...f, daily_limit: e.target.checked }))} />
                                    не чаще
                                    <input type="number" min={1} step={1} value={form.limit} disabled={!form.daily_limit} aria-label="Пополнений в день"
                                        onChange={e => setForm(f => ({ ...f, limit: Math.max(1, Number(e.target.value) || 1) }))}
                                        style={{ ...INPUT, marginTop: 0, width: 64, padding: '6px 10px', opacity: form.daily_limit ? 1 : 0.5 }} />
                                    раза в день
                                </label>
                                <div style={{ ...HINT, color: tooSmall ? '#ef4444' : '#6b7280' }}>
                                    {tooSmall
                                        ? `Минимальный бюджет — ${fmt(MIN_AUTOREFILL)} ₽ (ограничение ВБ).`
                                        : 'Доливает сам ВБ, как только остаток падает ниже порога. Настройка общая с кабинетом: изменение здесь видно там, и наоборот.'}
                                </div>
                            </div>

                            <div style={CARD}>
                                <div style={CARD_TITLE}>Источник списания</div>
                                <div style={{ fontSize: 14.5, color: '#111827' }}>Единый счёт кабинета Продвижения</div>
                                <div style={HINT}>Промобонусы как источник переключаются в кабинете ВБ — отсюда их не трогаем.</div>
                            </div>

                            {error && <div style={{ fontSize: 13.5, color: '#ef4444' }}>{error}</div>}
                        </>
                    )}

                    {/* История — то, что реально долил ВБ (приезжает вместе с настройкой) */}
                    {!loading && !noAccess && (
                        <div style={CARD}>
                            <div style={CARD_TITLE}>История автопополнений</div>
                            {history.length === 0 ? (
                                <div style={{ fontSize: 14, color: '#6b7280', padding: '4px 0' }}>Пополнений ещё не было.</div>
                            ) : (
                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                                    <thead>
                                        <tr style={{ color: '#6b7280', textAlign: 'left' }}>
                                            <th style={{ fontWeight: 500, padding: '0 0 8px' }}>Дата</th>
                                            <th style={{ fontWeight: 500, padding: '0 0 8px' }}>Источник</th>
                                            <th style={{ fontWeight: 500, padding: '0 0 8px', textAlign: 'right' }}>Сумма</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {history.slice(0, 8).map(e => (
                                            <tr key={e.id} style={{ borderTop: '1px solid #eef0f2' }}>
                                                <td style={{ padding: '8px 0', color: '#374151', whiteSpace: 'nowrap' }}>{e.date ? formatDateTime(e.date) : '—'}</td>
                                                <td style={{ padding: '8px 0', color: '#6b7280' }}>{SOURCE_LABEL[e.source || ''] || e.source || '—'}</td>
                                                <td style={{ padding: '8px 0', textAlign: 'right', whiteSpace: 'nowrap', fontWeight: 600, color: '#10b981' }}>+ {fmt(e.sum)} ₽</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            )}
                        </div>
                    )}
                </div>

                {/* Футер */}
                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', padding: '16px 28px', borderTop: '1px solid #eef0f2' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={saving}>{noAccess ? 'Закрыть' : 'Отмена'}</button>
                    {!noAccess && (
                        <button className="btn btn-primary" onClick={save} disabled={saving || loading || tooSmall || !!loadError}>
                            {saving ? 'Сохраняем в ВБ…' : 'Сохранить'}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}
