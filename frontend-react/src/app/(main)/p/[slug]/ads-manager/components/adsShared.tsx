import React from 'react';
import type { AdsManagerCampaign, AdsAutopaySetting, AdsAutopayLogEntry } from '@/types/api';

// ─── Форматтеры (округление до 0,1) ───
export const fmt = (n: number | undefined) => (Number(n) || 0).toLocaleString('ru-RU', { maximumFractionDigits: 1 });
export const num = (v: unknown) => Number(v) || 0;
export const fmtPct = (n: number | undefined) => (Number(n) || 0).toFixed(1) + '%';
export const iso = (d: Date) => d.toISOString().slice(0, 10);

// ─── Стили таблиц ───
export const thStyle: React.CSSProperties = { background: '#f3f4f6', color: '#374151', fontSize: 10.5, fontWeight: 700, textAlign: 'right', borderBottom: '1px solid #d1d5db', padding: '5px 8px', whiteSpace: 'nowrap' };
export const thLeft: React.CSSProperties = { ...thStyle, textAlign: 'left' };
export const tdStyle: React.CSSProperties = { textAlign: 'right', borderBottom: '1px solid #e8eaed', padding: '3px 8px', fontSize: 12, whiteSpace: 'nowrap', color: '#111827' };
export const tdLeft: React.CSSProperties = { ...tdStyle, textAlign: 'left' };

// Статус кампании: активна — зелёный, приостановлена — красный, завершена — серый
export const STATUS_BADGE: Record<number, string> = { 9: 'badge-success', 11: 'badge-danger', 7: 'badge-secondary' };

export function mskTime(isoStr: string | null): string {
    if (!isoStr) return 'до первого синка';
    try {
        return new Date(isoStr).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' });
    } catch { return '—'; }
}

/** Ссылка на страницу кампании в кабинете WB (deep-link на редактор с периодом и товаром).
 *  Пример: /campaigns/edit/{id}?advertID={id}&from=...Z&to=...Z&nmId=... */
export function wbCampaignUrl(c: AdsManagerCampaign, opts?: { from?: string; to?: string }): string {
    const params = new URLSearchParams({ advertID: String(c.campaign_id) });
    if (opts?.from) params.set('from', `${opts.from}T00:00:00Z`);
    if (opts?.to) params.set('to', `${opts.to}T00:00:00Z`);
    const nm = c.nm_ids?.[0];
    if (nm != null) params.set('nmId', String(nm));
    return `https://cmp.wildberries.ru/campaigns/edit/${c.campaign_id}?${params.toString()}`;
}

/** Подпись типа рекламы (модель оплаты WB). Режим ставки CPM (единая/ручная)
 *  придёт из bid_mode, когда синк начнёт его сохранять. */
export function adTypeLabel(c: AdsManagerCampaign): { text: string; hint: string; color: string } {
    const t = (c.campaign_type || '').toLowerCase();
    const mode = c.bid_mode === 'unified' ? ' · единая ставка' : c.bid_mode === 'manual' ? ' · ручная ставка' : '';
    if (t === 'cpc') return { text: 'Реклама: CPC', hint: 'CPC — оплата за клик', color: '#3b82f6' };  // синий
    if (c.advert_type === 8) return { text: `Реклама: Авто · рекомендации${mode}`, hint: 'Автоматическая кампания (рекомендации/каталог)', color: '#6d28d9' };  // фиолетовый
    if (t === 'cpm') return { text: `Реклама: CPM · аукцион${mode}`, hint: 'CPM — оплата за 1000 показов (аукцион)', color: '#6d28d9' };  // фиолетовый
    return { text: `Реклама: ${c.campaign_type || '—'}`, hint: '', color: '#6b7280' };
}

/** Бейдж типа кампании с цветовой кодировкой:
 *  CPC — синий; CPM-аукцион — фиолетовый; авто/рекомендации (advert_type=8) — розовый. */
export function campaignTypeBadge(c: AdsManagerCampaign): { label: string; bg: string; color: string } {
    const t = (c.campaign_type || '').toLowerCase();
    if (t === 'cpc') return { label: 'CPC', bg: '#e0f2fe', color: '#0369a1' };        // синий
    if (c.advert_type === 8) return { label: 'Авто', bg: '#fce7f3', color: '#be185d' }; // розовый — авто/рекомендации
    if (t === 'cpm') {
        // Единая — фиолетовый, ручная — розовый (разный режим ставки виден цветом)
        if (c.bid_mode === 'manual') return { label: 'CPM · ручная', bg: '#fce7f3', color: '#be185d' };
        if (c.bid_mode === 'unified') return { label: 'CPM · единая', bg: '#ede9fe', color: '#6d28d9' };
        return { label: 'CPM', bg: '#ede9fe', color: '#6d28d9' };  // режим ещё не синкнулся
    }
    return { label: 'Авто', bg: '#fce7f3', color: '#be185d' };                          // нет payment_type — авто
}

// Известные (неравномерные) границы vol → номер basket-хоста WB.
const WB_BASKET_RANGES: [number, number][] = [
    [143, 1], [287, 2], [431, 3], [719, 4], [1007, 5], [1061, 6], [1115, 7], [1169, 8],
    [1313, 9], [1601, 10], [1655, 11], [1919, 12], [2045, 13], [2189, 14], [2405, 15],
    [2621, 16], [2837, 17], [3053, 18], [3269, 19], [3485, 20], [3701, 21], [3917, 22],
    [4133, 23], [4349, 24], [4565, 25], [4877, 26], [5189, 27], [5501, 28], [5813, 29], [6125, 30],
];

/** URL нашего кэша фото товара (MinIO): резолвится+сохраняется 1 раз, дальше отдаётся из хранилища. */
export function productImageUrl(nmId: number): string {
    const base = process.env.NEXT_PUBLIC_API_URL || '';
    return `${base}/api/v1/media/product-image/${nmId}`;
}

/** URL главного фото товара WB по nm_id (публичный CDN, ключ не нужен).
 *  Хост basket-XX по vol-диапазону; для vol выше таблицы — продолжаем шагом 312
 *  (WB периодически добавляет баскеты). Битую картинку прячем через onError в <WbThumb>. */
export function wbImageUrl(nmId: number, size: 'small' | 'big' | 'c246x328' | 'c516x688' = 'c246x328'): string {
    const vol = Math.floor(nmId / 100000);
    const part = Math.floor(nmId / 1000);
    let n = WB_BASKET_RANGES.find(([max]) => vol <= max)?.[1];
    if (n == null) n = 30 + Math.ceil((vol - 6125) / 312);  // за пределами таблицы
    const host = String(n).padStart(2, '0');
    return `https://basket-${host}.wbbasket.ru/vol${vol}/part${part}/${nmId}/images/${size}/1.webp`;
}

export const DEFAULT_AUTOPAY: AdsAutopaySetting = {
    enabled: true, mode: 'to_target', amount: 1000, hour: 9, threshold_pct: 50,
    low_balance_threshold: 1000, topup_amount: 1000, daily_cap: 1,
};

/** Короткий ярлык активного автопополнения для кнопки/ячейки (зависит от режима). */
export function autopayLabel(s: AdsAutopaySetting): string {
    if (s.mode === 'low_balance') return `< ${fmt(s.low_balance_threshold)} → +${fmt(s.topup_amount)} ₽`;
    return `${String(s.hour).padStart(2, '0')}:00 · ${fmt(s.amount)} ₽`;
}

export const AUTOPAY_STATUS_BADGE: Record<AdsAutopayLogEntry['status'], { label: string; cls: string }> = {
    ok: { label: 'Успешно', cls: 'badge-success' },
    error: { label: 'Ошибка', cls: 'badge-danger' },
    unknown: { label: 'Неизвестно', cls: 'badge-warning' },
};
