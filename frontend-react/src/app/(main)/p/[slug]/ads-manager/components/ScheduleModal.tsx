import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/utils';
import type { AdsManagerCampaign, AdsScheduleSetting, AdsScheduleLogEntry, AdsWbAutopayStatus } from '@/types/api';
import { IcClock } from './icons';
import Switch from './Switch';
import { DEFAULT_SCHEDULE, fmt, useOverlayClose } from './adsShared';

// Стили в духе секции «Управление рекламой» — крупнее и контрастнее
const CARD: React.CSSProperties = { border: '1px solid #e5e7eb', borderRadius: 12, background: '#f9fafb', padding: '18px 20px' };
const CARD_TITLE: React.CSSProperties = { fontSize: 13, fontWeight: 700, color: '#4b5563', textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 16 };
const FIELD_LABEL: React.CSSProperties = { fontSize: 14, color: '#374151', display: 'block', fontWeight: 500 };
const INPUT: React.CSSProperties = { marginTop: 6, background: '#fff', border: '1px solid #d1d5db', borderRadius: 8, padding: '10px 12px', fontSize: 16, color: '#111827', width: '100%', boxSizing: 'border-box' };
const HINT: React.CSSProperties = { fontSize: 13, color: '#6b7280', marginTop: 12, lineHeight: 1.45 };

const HOURS = Array.from({ length: 24 }, (_, h) => h);

/** Модалка «Пауза по расписанию»: деньгами рулит автопополнение ВБ (долив в 00:00 МСК),
 *  ДДС глушит кампанию в окне «плохих» часов и запускает обратно по его окончании.
 *  Без campaign (форма создания) — только настройка, история не показывается. */
export default function ScheduleModal({ campaign, initial, onClose, onSave }: {
    campaign?: AdsManagerCampaign;
    initial: AdsScheduleSetting | undefined;
    onClose: () => void;
    onSave: (s: AdsScheduleSetting) => void | Promise<void>;
}) {
    const [form, setForm] = useState<AdsScheduleSetting>(initial ?? DEFAULT_SCHEDULE);
    const [saving, setSaving] = useState(false);

    // История пауз/запусков этой кампании — прямо в окне
    const [log, setLog] = useState<AdsScheduleLogEntry[] | null>(campaign ? null : []);
    // Автопополнение ВБ: null — проверяем, undefined — доливов не замечено
    const [wbAutopay, setWbAutopay] = useState<AdsWbAutopayStatus | null | undefined>(campaign ? null : undefined);
    useEffect(() => {
        if (!campaign) return;
        let alive = true;
        api.getScheduleLog(campaign.campaign_id)
            .then(rows => { if (alive) setLog(rows); })
            .catch(() => { if (alive) setLog([]); });
        api.getWbAutopayStatus(campaign.campaign_id)
            .then(m => { if (alive) setWbAutopay(m[String(campaign.campaign_id)]); })
            .catch(() => { if (alive) setWbAutopay(undefined); });
        return () => { alive = false; };
    }, [campaign]);

    // Подложка закрывает окно только «настоящим» кликом по ней (не концом выделения текста)
    const overlay = useOverlayClose(onClose);

    const emptyWindow = form.pause_hour === form.resume_hour;

    const handleSave = async () => {
        setSaving(true);
        try { await onSave(form); onClose(); }
        catch { setSaving(false); }
    };

    const hourSelect = (value: number, onChange: (h: number) => void, aria: string) => (
        <select value={value} aria-label={aria} onChange={e => onChange(Number(e.target.value))} style={INPUT}>
            {HOURS.map(h => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
        </select>
    );

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }} {...overlay}>
            <div className="glass-card" style={{ width: 700, maxWidth: '96vw', maxHeight: '92vh', display: 'flex', flexDirection: 'column', padding: 0, background: '#fff', overflow: 'hidden' }} onClick={e => e.stopPropagation()}>
                {/* Шапка */}
                <div style={{ padding: '22px 28px 16px', borderBottom: '1px solid #eef0f2' }}>
                    <h3 style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 21, fontWeight: 700, margin: '0 0 4px', color: '#111827' }}><IcClock size={20} />Пауза по расписанию</h3>
                    <div style={{ fontSize: 14, color: '#6b7280' }}>
                        {campaign ? <>{campaign.name || `#${campaign.campaign_id}`} · #{campaign.campaign_id}</> : 'Применится к кампании сразу после её создания'}
                    </div>
                </div>

                {/* Тело (скроллится) */}
                <div style={{ padding: 24, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 18 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        <Switch on={form.enabled} ariaLabel="Ставить на паузу по расписанию" onClick={() => setForm(f => ({ ...f, enabled: !f.enabled }))} />
                        <span style={{ fontSize: 16, fontWeight: 500, color: form.enabled ? '#111827' : '#6b7280' }}>Ставить на паузу по расписанию</span>
                    </div>

                    <div style={CARD}>
                        <div style={CARD_TITLE}>Окно паузы (МСК)</div>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                            <label style={FIELD_LABEL}>
                                Пауза в
                                {hourSelect(form.pause_hour, h => setForm(f => ({ ...f, pause_hour: h })), 'Час паузы (МСК)')}
                            </label>
                            <label style={FIELD_LABEL}>
                                Запуск в
                                {hourSelect(form.resume_hour, h => setForm(f => ({ ...f, resume_hour: h })), 'Час запуска (МСК)')}
                            </label>
                        </div>
                        {emptyWindow && (
                            <div style={{ ...HINT, color: '#ef4444' }}>Часы паузы и запуска совпадают — окно пустое, расписание работать не будет.</div>
                        )}
                        <div style={HINT}>
                            ВБ доливает бюджет своим автопополнением в 00:00 МСК. ДДС поставит кампанию на паузу
                            в {String(form.pause_hour).padStart(2, '0')}:00 и запустит в {String(form.resume_hour).padStart(2, '0')}:00 —
                            дневной бюджет не сгорает в ночные часы с низкой конверсией. Проверка раз в 15 минут.
                            Кампанию, остановленную вручную, расписание не запускает; запущенную вручную ночью — повторно не глушит.
                        </div>
                    </div>

                    {/* Автопополнение ВБ: API ВБ его статус не отдаёт — определяем по фактическим
                        доливам бюджета (события budget_change за 7 дней, ручные через ДДС исключены) */}
                    {campaign && (
                        <div style={CARD}>
                            <div style={{ ...CARD_TITLE, display: 'flex', alignItems: 'center', gap: 10 }}>
                                Автопополнение ВБ
                                {wbAutopay === null ? (
                                    <span style={{ fontWeight: 600, fontSize: 11, letterSpacing: 0.2, textTransform: 'none', padding: '2px 10px', borderRadius: 24, background: '#f3f4f6', color: '#6b7280' }}>проверяем…</span>
                                ) : wbAutopay ? (
                                    <span style={{ fontWeight: 600, fontSize: 11, letterSpacing: 0.2, textTransform: 'none', padding: '2px 10px', borderRadius: 24, background: '#d1fae5', color: '#047857' }}>работает</span>
                                ) : (
                                    <span style={{ fontWeight: 600, fontSize: 11, letterSpacing: 0.2, textTransform: 'none', padding: '2px 10px', borderRadius: 24, background: '#fee2e2', color: '#b91c1c' }}>доливов не замечено</span>
                                )}
                            </div>
                            <div style={{ ...HINT, marginTop: 0 }}>
                                {wbAutopay === null
                                    ? 'Смотрим историю бюджета за последние 7 дней…'
                                    : wbAutopay
                                        ? <>ВБ доливал бюджет этой кампании {wbAutopay.count > 1 ? `${wbAutopay.count} раз(а) за 7 дней, ` : ''}последний раз {wbAutopay.last_ts ? formatDateTime(wbAutopay.last_ts) : '—'} на +{fmt(wbAutopay.last_amount)} ₽. Расписание паузы переносит долитый бюджет на дневные часы.</>
                                        : 'За последние 7 дней доливов бюджета со стороны ВБ не видно. Если автопополнение в кабинете ВБ выключено — к утреннему запуску кампания останется со вчерашним остатком; включите его в кабинете, настройте «Автопополнение» здесь или пополняйте вручную. Определяется по истории бюджета, поэтому у кампании без расходов или только что созданной доливов может не быть видно.'}
                            </div>
                        </div>
                    )}

                    {/* История пауз/запусков — прямо в окне */}
                    {campaign && (
                        <div style={CARD}>
                            <div style={CARD_TITLE}>История</div>
                            {log == null ? (
                                <div style={{ fontSize: 14, color: '#6b7280', padding: '4px 0' }}>Загрузка…</div>
                            ) : log.length === 0 ? (
                                <div style={{ fontSize: 14, color: '#6b7280', padding: '4px 0' }}>Пауз и запусков по расписанию ещё не было.</div>
                            ) : (
                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                                    <thead>
                                        <tr style={{ color: '#6b7280', textAlign: 'left' }}>
                                            <th style={{ fontWeight: 500, padding: '0 0 8px' }}>Дата</th>
                                            <th style={{ fontWeight: 500, padding: '0 0 8px' }}>Действие</th>
                                            <th style={{ fontWeight: 500, padding: '0 0 8px', textAlign: 'right' }}>Результат</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {log.slice(0, 8).map((e, i) => {
                                            const ok = e.status === 'ok';
                                            return (
                                                <tr key={i} style={{ borderTop: '1px solid #eef0f2' }}>
                                                    <td style={{ padding: '8px 0', color: '#374151', whiteSpace: 'nowrap' }}>{formatDateTime(e.ts)}</td>
                                                    <td style={{ padding: '8px 0', color: '#374151' }}>{e.kind === 'pause' ? '⏸ Пауза' : '▶ Запуск'}</td>
                                                    <td style={{ padding: '8px 0', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                        <span title={e.reason || undefined} style={{ fontWeight: 600, color: ok ? '#10b981' : '#ef4444' }}>
                                                            {ok ? 'Успешно' : 'Ошибка'}
                                                        </span>
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            )}
                        </div>
                    )}
                </div>

                {/* Футер */}
                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', padding: '16px 28px', borderTop: '1px solid #eef0f2' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleSave} disabled={saving || (form.enabled && emptyWindow)}>{saving ? 'Сохранение…' : 'Сохранить'}</button>
                </div>
            </div>
        </div>
    );
}
