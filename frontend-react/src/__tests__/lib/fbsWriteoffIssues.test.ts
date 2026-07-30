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
    PSEUDO_STATUS_LABEL,
    TRANSIT_DANGER_DAYS,
    TRANSIT_WARN_DAYS,
    WRITEOFF_REASON_LABEL,
    daysSince,
    transitDaysColor,
    writeoffReasonLabel,
} from '@/app/(main)/p/[slug]/warehouse/fbs/fbsShared';

/** Коды причин, которые РЕАЛЬНО отдаёт бэкенд (`FbsWriteoffIssueRow.reason`). */
const BACKEND_REASONS = ['no_stock', 'no_card', 'no_link'];

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
        expect(transitDaysColor(30)).toBe('var(--color-danger)');
    });

    it('пороги согласованы: warning наступает раньше danger', () => {
        expect(TRANSIT_WARN_DAYS).toBeLessThan(TRANSIT_DANGER_DAYS);
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
});
