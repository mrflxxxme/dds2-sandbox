import React from 'react';
import type { AdsManagerCampaign, AdsScheduleSetting } from '@/types/api';

// ─── Форматтеры (округление до 0,1) ───
export const fmt = (n: number | undefined) => (Number(n) || 0).toLocaleString('ru-RU', { maximumFractionDigits: 1 });
export const num = (v: unknown) => Number(v) || 0;
export const fmtPct = (n: number | undefined) => (Number(n) || 0).toFixed(1) + '%';
export const iso = (d: Date) => d.toISOString().slice(0, 10);

// ─── Стили таблиц ───
export const thStyle: React.CSSProperties = { background: '#f3f4f6', color: '#374151', fontSize: 10.5, fontWeight: 700, textAlign: 'right', borderBottom: '1px solid #d1d5db', padding: '5px 8px', whiteSpace: 'nowrap' };
export const thLeft: React.CSSProperties = { ...thStyle, textAlign: 'left' };
// Тёмная шапка таблицы кампаний (тот же вид, что у списка кампаний) — переиспользуем в разделах
export const cThStyle: React.CSSProperties = { ...thStyle, background: '#374151', color: '#e5e7eb', borderBottom: '1px solid #4b5563', position: 'sticky', top: 0, zIndex: 3 };
export const cThLeft: React.CSSProperties = { ...cThStyle, textAlign: 'left' };
export const tdStyle: React.CSSProperties = { textAlign: 'right', borderBottom: '1px solid #e8eaed', padding: '3px 8px', fontSize: 12, whiteSpace: 'nowrap', color: '#111827' };
export const tdLeft: React.CSSProperties = { ...tdStyle, textAlign: 'left' };

// Статус кампании: активна — зелёный, приостановлена — красный, завершена — серый
// Целевой ДРР менеджера — общий на весь раздел: задаётся в кластеризаторе, читается ещё и
// светофором колонки ДРР в «По дням», чтобы порог был один и тот же на обеих вкладках.
export const TARGET_DRR_KEY = 'ads_cluster_target_drr';

/** Целевой ДРР: переопределение менеджера (localStorage) → значение кампании → 8. */
export function readTargetDrr(fromCampaign?: number | null): number {
    try {
        const v = Number(localStorage.getItem(TARGET_DRR_KEY));
        if (isFinite(v) && v > 0) return v;
    } catch { /* SSR / приватный режим */ }
    return fromCampaign && fromCampaign > 0 ? fromCampaign : 8;
}

export const STATUS_BADGE: Record<number, string> = { 9: 'badge-success', 11: 'badge-danger', 7: 'badge-secondary' };

/** Пропсы подложки модалки: закрываем ТОЛЬКО когда и нажатие, и отпускание были на самой
 *  подложке. Иначе выделение текста в поле (мышь ушла за край окна) или протяжка по кнопкам
 *  заканчиваются click'ом на подложке — и окно захлопывается вместе с несохранённой формой. */
export function useOverlayClose(onClose: () => void) {
    const downOnOverlay = React.useRef(false);
    return {
        onMouseDown: (e: React.MouseEvent) => { downOnOverlay.current = e.target === e.currentTarget; },
        onClick: (e: React.MouseEvent) => {
            if (e.target === e.currentTarget && downOnOverlay.current) onClose();
            downOnOverlay.current = false;
        },
    };
}

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

/** Аббревиатура типа рекламы для авто-имени кампании (3 буквы):
 *  CPM единая → «АВТ», CPM ручная → «АУК», CPC → «CPC». */
export function campaignTypeAbbr(payment: string, bid: string): string {
    if (payment === 'cpc') return 'CPC';
    return bid === 'unified' ? 'АВТ' : 'АУК';
}

/** Авто-наименование кампании по шаблону «nmID артикул_продавца ТИП» (обрезано до maxLen).
 *  Пустые части опускаются — если каталог ещё не подтянул артикул, будет «nmID ТИП». */
export function buildAutoCampaignName(nm: number, vendorCode: string, payment: string, bid: string, maxLen = 50): string {
    return [String(nm), (vendorCode || '').trim(), campaignTypeAbbr(payment, bid)]
        .filter(Boolean).join(' ').slice(0, maxLen);
}

// Фото и ссылка на карточку WB переехали в общий слой (`@/lib/wbMedia`) — их
// показывают и остатки FBS, и воронка. Реэкспорт, чтобы импорты рекламы,
// привыкшие брать их отсюда, остались рабочими.
export { productImageUrl, wbImageUrl, wbProductUrl } from '@/lib/wbMedia';

// Дефолтное окно паузы: ВБ доливает бюджет в 00:00 МСК — глушим сразу после
// долива и запускаем к утреннему трафику.
export const DEFAULT_SCHEDULE: AdsScheduleSetting = { enabled: true, pause_hour: 0, resume_hour: 9 };

const hh = (h: number) => `${String(h).padStart(2, '0')}:00`;

/** Короткий ярлык активного расписания для кнопки/ячейки: «⏸ 00:00–09:00». */
export function scheduleLabel(s: AdsScheduleSetting): string {
    return `${hh(s.pause_hour)}–${hh(s.resume_hour)}`;
}

// Известные сигнатуры ошибок WB → понятный менеджеру текст. Матчим по подстроке (lowercase),
// первое совпадение выигрывает. Ключи — фрагменты, которые WB стабильно возвращает в теле ошибки.
const ADS_ERROR_MAP: [string, string][] = [
    ['no budget to start', 'Недостаточно бюджета для запуска. Пополните бюджет кампании и попробуйте снова.'],
    ['has no budget', 'Недостаточно бюджета для запуска. Пополните бюджет кампании и попробуйте снова.'],
    ['not found', 'Кампания не найдена в кабинете WB. Обновите список и попробуйте снова.'],
    ['rate limit', 'WB временно ограничил частоту запросов. Подождите немного и повторите.'],
    ['too many requests', 'WB временно ограничил частоту запросов. Подождите немного и повторите.'],
    ['status unchanged', 'WB не изменил статус кампании — возможно, он уже такой или кампания сейчас недоступна для изменения.'],
];

/** Техническую ошибку WB/бэка (сырой JSON, gRPC-префиксы, «HTTP 400: …») превращает в
 *  понятное менеджеру сообщение. Известные сигнатуры переводим точно; для незнакомых —
 *  снимаем HTTP/JSON-обёртку и технические префиксы, чтобы не показывать сырой код. */
export function humanizeAdsError(raw: unknown, fallback = 'Не удалось выполнить действие'): string {
    const src = raw instanceof Error ? raw.message : typeof raw === 'string' ? raw : '';
    if (!src.trim()) return fallback;
    // Вытаскиваем вложенный текст из "HTTP 400: {"error":"...."}" (или голого JSON-тела)
    let inner = src;
    const brace = src.indexOf('{');
    if (brace !== -1) {
        try {
            const obj = JSON.parse(src.slice(brace)) as Record<string, unknown>;
            const val = obj?.error ?? obj?.message ?? obj?.detail;
            if (typeof val === 'string' && val.trim()) inner = val;
        } catch { /* не JSON — оставляем строку как есть */ }
    }
    const low = inner.toLowerCase();
    const known = ADS_ERROR_MAP.find(([sig]) => low.includes(sig));
    if (known) return known[1];
    // Незнакомая ошибка: снимаем «HTTP NNN:» и gRPC-префикс метода (advert…ViaGRPC:),
    // показываем хотя бы очищенный текст вместо сырого JSON.
    const cleaned = inner.replace(/^HTTP\s+\d+:\s*/i, '').replace(/^advert\w*ViaGRPC:\s*/i, '').trim();
    return cleaned || fallback;
}
