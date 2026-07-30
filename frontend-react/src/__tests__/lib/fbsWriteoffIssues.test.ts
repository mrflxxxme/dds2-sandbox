/**
 * Панель «Не списано со склада» и «Зависли в пути» — чистая логика UI.
 *
 * Что закрепляется:
 *  1. словарь причин writeoff-issues: ключи — машинные коды бэкенда
 *     (`GET /fbs/orders/writeoff-issues`, reason). Промах словаря в рантайме
 *     невидим — сработает фолбэк «показать код как есть», и в колонке
 *     «Причина» вылезет `no_stock`; контракт держится только этой парой
 *     тестов (зеркало на бэке — тест ручки writeoff-issues);
 *  2. псевдо-статус `in_delivery_stuck` имеет человеческий ярлык — он уходит
 *     фильтром в `GET /fbs/orders?status=…` наравне с in_delivery/sorted;
 *  3. пороги подсветки «В пути, дн» согласованы: подсветка начинается ровно
 *     с порога чипа «Зависли в пути», иначе счётчик и цвет спорили бы.
 */
import { describe, expect, it } from 'vitest';
import {
    CABINET_STATUS_LABEL,
    NOT_SCANNED_CABINET_KEYS,
    ORDER_AGE_DANGER_HOURS,
    ORDER_AGE_WARN_HOURS,
    PSEUDO_STATUS_LABEL,
    TRANSIT_DANGER_DAYS,
    TRANSIT_STALE_DAYS,
    TRANSIT_WARN_DAYS,
    WRITEOFF_REASON_LABEL,
    cabinetOrderStatus,
    daysSince,
    durationSinceLabel,
    hoursAgoLabel,
    orderAgeColor,
    transitDaysColor,
    writeoffReasonLabel,
} from '@/app/(main)/p/[slug]/warehouse/fbs/fbsShared';
import type { FbsCabinetStatusKey } from '@/app/(main)/p/[slug]/warehouse/fbs/fbsShared';

/**
 * Коды причин, которые РЕАЛЬНО отдаёт бэкенд (`ALLOWED_WRITEOFF_REASONS`,
 * `FbsWriteoffIssueRow.reason`). `queued` — не проблема, а очередь: остаток
 * есть, задание ждёт ближайшего прогона списания.
 */
const BACKEND_REASONS = ['no_stock', 'no_card', 'no_link', 'queued'];

describe('WRITEOFF_REASON_LABEL', () => {
    it('покрывает все коды бэкенда человеческим текстом', () => {
        for (const code of BACKEND_REASONS) {
            const label = writeoffReasonLabel(code);
            expect(label, `код ${code} без перевода`).not.toBe(code);
            expect(label.length).toBeGreaterThan(3);
        }
    });

    it('тексты закреплены дословно — их видит пользователь и Excel-выгрузка', () => {
        expect(writeoffReasonLabel('no_stock')).toBe('нет остатка на складе');
        expect(writeoffReasonLabel('no_card')).toBe('нет карточки товара');
        expect(writeoffReasonLabel('no_link')).toBe('склад не привязан');
        // queued — спокойный тон: это очередь на списание, не авария
        expect(writeoffReasonLabel('queued')).toBe('ждёт списания (остаток есть)');
    });

    it('неизвестный код показываем как есть, а не «—»', () => {
        expect(writeoffReasonLabel('mystery_code')).toBe('mystery_code');
    });

    it('в словаре нет мёртвых ключей, о которых бэкенд не знает', () => {
        expect(Object.keys(WRITEOFF_REASON_LABEL).sort()).toEqual([...BACKEND_REASONS].sort());
    });
});

describe('псевдо-статус in_delivery_stuck', () => {
    it('имеет ярлык — чип и пустое состояние берут текст отсюда', () => {
        expect(PSEUDO_STATUS_LABEL.in_delivery_stuck).toBe('Зависли в пути');
    });

    it('соседние псевдо-статусы на месте (общий словарь, не три копии)', () => {
        expect(PSEUDO_STATUS_LABEL.in_delivery).toBe('Ещё в доставке');
        expect(PSEUDO_STATUS_LABEL.sorted).toBe('Отсортировано');
    });
});

describe('transitDaysColor', () => {
    it('до порога задержки — без подсветки', () => {
        expect(transitDaysColor(null)).toBeNull();
        expect(transitDaysColor(undefined)).toBeNull();
        expect(transitDaysColor(0)).toBeNull();
        expect(transitDaysColor(TRANSIT_WARN_DAYS - 1)).toBeNull();
    });

    it('с порога чипа «Зависли в пути» — warning, дальше — danger', () => {
        expect(transitDaysColor(TRANSIT_WARN_DAYS)).toBe('var(--color-warning)');
        expect(transitDaysColor(TRANSIT_DANGER_DAYS - 1)).toBe('var(--color-warning)');
        expect(transitDaysColor(TRANSIT_DANGER_DAYS)).toBe('var(--color-danger)');
        expect(transitDaysColor(TRANSIT_STALE_DAYS)).toBe('var(--color-danger)');
    });

    it('за потолком окна «зависло» — приглушение, не тревога: бэк такие строки '
        + 'в чип не считает, это застывший wb_status старого задания', () => {
        expect(transitDaysColor(TRANSIT_STALE_DAYS + 1)).toBe('var(--color-text-dim)');
        expect(transitDaysColor(45)).toBe('var(--color-text-dim)');
        expect(transitDaysColor(365)).toBe('var(--color-text-dim)');
    });

    it('пороги согласованы: warning раньше danger, потолок — дальше обоих', () => {
        expect(TRANSIT_WARN_DAYS).toBeLessThan(TRANSIT_DANGER_DAYS);
        expect(TRANSIT_DANGER_DAYS).toBeLessThan(TRANSIT_STALE_DAYS);
    });
});

describe('daysSince (возраст самого старого несписанного)', () => {
    const now = Date.parse('2026-07-30T12:00:00Z');

    it('null и мусор — null: возраст честно неизвестен', () => {
        expect(daysSince(null, now)).toBeNull();
        expect(daysSince(undefined, now)).toBeNull();
        expect(daysSince('не дата', now)).toBeNull();
    });

    it('считает ПОЛНЫЕ дни', () => {
        expect(daysSince('2026-07-30T09:00:00Z', now)).toBe(0);
        expect(daysSince('2026-07-27T12:00:00Z', now)).toBe(3);
        // 2.9 суток — ещё 2 полных дня, не 3
        expect(daysSince('2026-07-27T14:00:00Z', now)).toBe(2);
    });

    it('будущая дата (перекос часов) — «сегодня», не отрицательное', () => {
        expect(daysSince('2026-07-31T00:00:00Z', now)).toBe(0);
    });

    it('naive-UTC без Z читается как UTC, а не как локальное время', () => {
        // Бэк шлёт datetime без таймзоны; без нормализации Date.parse в поясе
        // восточнее UTC завышал бы возраст на смещение пояса.
        expect(daysSince('2026-07-27T12:00:00', now)).toBe(daysSince('2026-07-27T12:00:00Z', now));
        expect(daysSince('2026-07-27T12:00:00', now)).toBe(3);
        // явное смещение уважаем — оно уже несёт таймзону
        expect(daysSince('2026-07-27T12:00:00+00:00', now)).toBe(3);
    });
});

describe('hoursAgoLabel (возраст заказа как в кабинете WB)', () => {
    const now = Date.parse('2026-07-30T12:00:00Z');

    it('форматы: минуты, часы+минуты, дни', () => {
        expect(hoursAgoLabel('2026-07-30T11:59:40Z', now)).toBe('только что');
        expect(hoursAgoLabel('2026-07-30T11:48:00Z', now)).toBe('12 мин назад');
        expect(hoursAgoLabel('2026-07-30T06:07:00Z', now)).toBe('5 ч 53 мин назад');
        expect(hoursAgoLabel('2026-07-30T07:00:00Z', now)).toBe('5 ч назад');
        expect(hoursAgoLabel('2026-07-27T08:00:00Z', now)).toBe('3 дн 4 ч назад');
        expect(hoursAgoLabel('2026-07-27T12:00:00Z', now)).toBe('3 дн назад');
    });

    it('naive-UTC без Z читается как UTC (не локальное)', () => {
        expect(hoursAgoLabel('2026-07-30T06:07:00', now)).toBe('5 ч 53 мин назад');
    });

    it('null/мусор → null', () => {
        expect(hoursAgoLabel(null, now)).toBeNull();
        expect(hoursAgoLabel('не дата', now)).toBeNull();
    });
});

describe('cabinetOrderStatus (статусы кабинета WB — канон 30.07)', () => {
    it('фазы поставки: не закрыта → На сборке; закрыта без скана → Отгрузите товар; скан → Ждёт сортировки', () => {
        expect(cabinetOrderStatus('new', null, false, false)).toBe('new');
        expect(cabinetOrderStatus('confirm', 'waiting', false, false)).toBe('assembling');
        expect(cabinetOrderStatus('complete', 'waiting', true, false)).toBe('ship_goods');
        expect(cabinetOrderStatus('complete', 'waiting', true, true)).toBe('awaiting_sort');
    });

    it('ось WB перебивает фазу: sorted/ПВЗ/выкуп/брак', () => {
        expect(cabinetOrderStatus('complete', 'sorted', true, true)).toBe('sorted');
        expect(cabinetOrderStatus('complete', 'ready_for_pickup', true, true)).toBe('ready_for_pickup');
        expect(cabinetOrderStatus('complete', 'sold', true, true)).toBe('sold');
        expect(cabinetOrderStatus('complete', 'defect', true, true)).toBe('defect');
    });

    it('отмены с обеих осей → cancelled', () => {
        expect(cabinetOrderStatus('cancel', 'canceled', true, true)).toBe('cancelled');
        expect(cabinetOrderStatus('new', 'declined_by_client', false, false)).toBe('cancelled');
    });

    it('у каждого ключа есть ярлык и бейдж (контракт словаря)', () => {
        const keys: FbsCabinetStatusKey[] = [
            'new', 'assembling', 'ship_goods', 'awaiting_sort', 'sorted',
            'ready_for_pickup', 'postponed', 'sold', 'defect', 'cancelled',
        ];
        for (const k of keys) {
            expect(CABINET_STATUS_LABEL[k].label.length, `ключ ${k}`).toBeGreaterThan(2);
            expect(CABINET_STATUS_LABEL[k].badge).toMatch(/^badge-/);
        }
        expect(CABINET_STATUS_LABEL.ship_goods.label).toBe('Отгрузите товар');
        expect(CABINET_STATUS_LABEL.awaiting_sort.label).toBe('Ждёт сортировки');
        expect(CABINET_STATUS_LABEL.sorted.label).toBe('Отсортировано');
    });
});

describe('orderAgeColor (подсветка, пока WB не отсканировал)', () => {
    const now = Date.parse('2026-07-30T12:00:00Z');

    it('не отсканирован (new/assembling/ship_goods): >24ч красный, >12ч оранжевый', () => {
        expect(orderAgeColor('2026-07-29T10:00:00Z', 'new', now)).toBe('var(--color-danger)');
        expect(orderAgeColor('2026-07-29T23:00:00Z', 'assembling', now)).toBe('var(--color-warning)');
        // Кейс ASM-899: передано, но QR не отсканирован — 1 дн 17 ч, красим.
        expect(orderAgeColor('2026-07-28T18:31:10Z', 'ship_goods', now)).toBe('var(--color-danger)');
        expect(orderAgeColor('2026-07-30T08:00:00Z', 'new', now)).toBeNull();
    });

    it('после скана QR — не красим (зона логистики WB)', () => {
        expect(orderAgeColor('2026-07-25T10:00:00Z', 'awaiting_sort', now)).toBeNull();
        expect(orderAgeColor('2026-07-25T10:00:00Z', 'sorted', now)).toBeNull();
        expect(orderAgeColor('2026-07-25T10:00:00Z', 'sold', now)).toBeNull();
        expect(orderAgeColor('2026-07-25T10:00:00Z', 'cancelled', now)).toBeNull();
    });

    it('пороги согласованы: warn < danger; не-скановые ключи зафиксированы', () => {
        expect(ORDER_AGE_WARN_HOURS).toBeLessThan(ORDER_AGE_DANGER_HOURS);
        expect([...NOT_SCANNED_CABINET_KEYS].sort()).toEqual(['assembling', 'new', 'ship_goods']);
    });
});

describe('durationSinceLabel (колонка «В пути» — часы вместо «0 дней»)', () => {
    const now = Date.parse('2026-07-30T12:00:00Z');

    it('суб-суточный транзит показывает часы, а не «0»', () => {
        expect(durationSinceLabel('2026-07-29T13:03:24Z', now)).toBe('22 ч 56 мин');
        expect(durationSinceLabel('2026-07-30T11:48:00Z', now)).toBe('12 мин');
        expect(durationSinceLabel('2026-07-30T07:00:00Z', now)).toBe('5 ч');
    });

    it('дни + часы, без слова «назад»', () => {
        expect(durationSinceLabel('2026-07-27T08:00:00Z', now)).toBe('3 дн 4 ч');
        expect(durationSinceLabel('2026-07-27T12:00:00Z', now)).toBe('3 дн');
    });

    it('null/мусор → null', () => {
        expect(durationSinceLabel(null, now)).toBeNull();
        expect(durationSinceLabel('не дата', now)).toBeNull();
    });
});
