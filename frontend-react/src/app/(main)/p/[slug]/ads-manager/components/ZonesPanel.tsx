'use client';
import React, { useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import Tooltip from './Tooltip';
import Switch from './Switch';
import type { CampaignZones } from '@/types/api';

export type ZoneKey = 'search' | 'recommendations';

export const ZONES: { key: ZoneKey; label: string }[] = [
    { key: 'search', label: 'Поиск' },
    { key: 'recommendations', label: 'Рекомендации' },
];

/** Как устроена ставка у этого типа кампании (правила WB). */
export function zoneRuleText(z: CampaignZones): string {
    if (z.payment_type === 'cpc') return 'CPC · только поиск, ставка за клик — единая для всех фраз';
    if (z.bid_mode === 'unified') return 'CPM · единая ставка сразу на все зоны, они работают одновременно';
    return 'CPM · ручные ставки, зоны переключаются по отдельности';
}

/** Блок «Зоны показов»: тумблеры вкл/выкл зоны в WB.
 *
 *  Переключение WB разрешает только у CPM с ручной ставкой (см. `zones_locked`/`lock_reason`);
 *  в остальных случаях тумблер виден, отражает реальное состояние, но заблокирован.
 *  Разбивку статистики по зонам даёт отдельная вкладка «По зонам» (не этот блок).
 */
export default function ZonesPanel({ zones, campaignId, onChanged }: {
    zones: CampaignZones;
    campaignId: number;
    /** Зоны успешно изменены в WB — перечитать их с сервера */
    onChanged: () => void;
}) {
    const [busy, setBusy] = useState<ZoneKey | null>(null);
    const [error, setError] = useState('');
    // Инлайн-редактирование ставки зоны (реальные деньги)
    const [editZone, setEditZone] = useState<ZoneKey | null>(null);
    const [bidInput, setBidInput] = useState('');
    const [savingBid, setSavingBid] = useState<ZoneKey | null>(null);

    const locked = zones.zones_locked;

    const startEdit = (z: ZoneKey, cur: number | null) => { setEditZone(z); setBidInput(cur != null ? String(cur) : ''); setError(''); };
    const saveBid = async (z: ZoneKey) => {
        const v = Math.round(Number(bidInput.replace(',', '.')));
        if (!v || v <= 0) { setError('Ставка должна быть больше 0'); return; }
        const label = ZONES.find(x => x.key === z)?.label;
        if (!window.confirm(`Изменить ставку зоны «${label}» на ${v} ₽? Это реальные деньги.`)) return;
        setSavingBid(z); setError('');
        try {
            const res = await api.setCampaignZoneBid(campaignId, z, v);
            if (!res.ok) throw new Error(res.error || 'Не удалось изменить ставку');
            setEditZone(null);
            onChanged();  // перечитать зоны — ставка обновится с сервера
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось изменить ставку');
        } finally { setSavingBid(null); }
    };

    const toggle = async (key: ZoneKey) => {
        const next = { search: zones.placements.search ?? false, recommendations: zones.placements.recommendations ?? false };
        next[key] = !next[key];
        if (!next.search && !next.recommendations) {
            setError('Нельзя выключить обе зоны: реклама перестанет показываться.');
            return;
        }
        const zoneLabel = ZONES.find(z => z.key === key)?.label;
        if (!window.confirm(`${next[key] ? 'Включить' : 'Выключить'} зону «${zoneLabel}» в WB?`)) return;
        setBusy(key); setError('');
        try {
            await api.setCampaignZones(campaignId, next);
            onChanged();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось изменить зоны показов');
        } finally { setBusy(null); }
    };


    return (
        <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid #eef0f2' }}>
            {/* Один блок: метка + тумблеры зон в одну строку. Правило ставки не дублируем — оно в типе рекламы над шапкой. */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: '#6b7280' }}>ЗОНЫ ПОКАЗОВ</span>
                {ZONES.map(z => {
                    const on = zones.placements[z.key] ?? false;
                    // Зона, которой у кампании нет вовсе (CPC → рекомендации), не рисуется
                    if (!on && zones.bids[z.key] == null && zones.payment_type === 'cpc') return null;
                    const bid = zones.bids[z.key];
                    const card = (
                        <div style={{
                            display: 'flex', alignItems: 'center', gap: 9,
                            padding: '7px 12px', borderRadius: 10,
                            background: on ? '#fff' : '#f9fafb',
                            border: '1px solid #e5e7eb',
                            opacity: on ? 1 : 0.7,
                        }}>
                            <Switch on={on} disabled={locked || busy === z.key} ariaLabel={z.label} onClick={() => toggle(z.key)} />
                            <span style={{ fontSize: 13, fontWeight: 600, color: on ? '#111827' : '#6b7280' }}>{z.label}</span>
                            {/* Ставка зоны (₽) — клик открывает инлайн-редактор (реальные деньги) */}
                            {editZone === z.key ? (
                                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, marginLeft: 2 }}>
                                    <input autoFocus type="text" inputMode="numeric" value={bidInput}
                                        onChange={e => setBidInput(e.target.value)}
                                        onKeyDown={e => { if (e.key === 'Enter') saveBid(z.key); if (e.key === 'Escape') setEditZone(null); }}
                                        disabled={savingBid === z.key}
                                        style={{ width: 58, padding: '2px 5px', fontSize: 13, textAlign: 'right', border: '1px solid #3b82f6', borderRadius: 6, color: '#111827', background: '#fff' }} />
                                    <span style={{ fontSize: 12, color: '#6b7280' }}>₽</span>
                                    <button onClick={() => saveBid(z.key)} disabled={savingBid === z.key} className="btn btn-primary btn-sm" style={{ fontSize: 11, padding: '2px 8px' }}>{savingBid === z.key ? '…' : 'OK'}</button>
                                    <button onClick={() => setEditZone(null)} disabled={savingBid === z.key} className="btn btn-secondary btn-sm" style={{ fontSize: 11, padding: '2px 6px' }}>✕</button>
                                </span>
                            ) : (
                                <button onClick={() => startEdit(z.key, bid)} title="Изменить ставку зоны"
                                    style={{ fontSize: 13, fontWeight: 700, color: on ? '#3b82f6' : '#9ca3af', marginLeft: 2, background: 'transparent', border: 'none', borderBottom: '1px dashed #93c5fd', cursor: 'pointer', padding: '0 1px' }}>
                                    {bid != null ? `${formatNumber(bid, 0)} ₽` : 'задать'}
                                </button>
                            )}
                        </div>
                    );
                    // Подсказка объясняет, почему тумблер заблокирован
                    return <React.Fragment key={z.key}>{locked && zones.lock_reason ? <Tooltip text={zones.lock_reason}>{card}</Tooltip> : card}</React.Fragment>;
                })}
                {error && <span style={{ fontSize: 11, color: '#ef4444' }}>{error}</span>}
            </div>
        </div>
    );
}
