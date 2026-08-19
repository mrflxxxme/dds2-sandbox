/**
 * Волна A v2: пресеты общего PeriodPicker (Р22).
 *
 * Пикер переехал из ads-manager в общие компоненты и обслуживает теперь четыре
 * прежние страницы плюс календарь дизайна. Пресеты — единственная его чистая
 * логика, и «30 дней» обязан остаться ВКЛЮЧИТЕЛЬНЫМ окном (29 назад + сегодня),
 * иначе у всех четырёх потребителей поедут цифры отчётов.
 */
import { describe, expect, it } from 'vitest';
import { format } from 'date-fns';
import { PRESETS, preset } from '@/components/PeriodPicker';

const TODAY = new Date(2026, 7, 20); // 20 августа 2026
const ymd = (d?: Date) => (d ? format(d, 'yyyy-MM-dd') : null);

describe('preset', () => {
    it('«Сегодня» — один день', () => {
        const r = preset('today', TODAY);
        expect([ymd(r.from), ymd(r.to)]).toEqual(['2026-08-20', '2026-08-20']);
    });

    it('«Вчера» — один предыдущий день', () => {
        const r = preset('yesterday', TODAY);
        expect([ymd(r.from), ymd(r.to)]).toEqual(['2026-08-19', '2026-08-19']);
    });

    it('«30 дней» — включительное окно: 29 дней назад плюс сегодня', () => {
        const r = preset('30d', TODAY);
        expect([ymd(r.from), ymd(r.to)]).toEqual(['2026-07-22', '2026-08-20']);
    });

    it('месячные пресеты отсчитываются назад от сегодня', () => {
        expect(ymd(preset('3m', TODAY).from)).toBe('2026-05-20');
        expect(ymd(preset('6m', TODAY).from)).toBe('2026-02-20');
        expect(ymd(preset('12m', TODAY).from)).toBe('2025-08-20');
    });

    it('у каждого объявленного пресета есть реализация, конец не раньше начала', () => {
        for (const { key } of PRESETS) {
            const r = preset(key, TODAY);
            expect(r.from, key).toBeDefined();
            expect(ymd(r.to)! >= ymd(r.from)!, key).toBe(true);
        }
    });
});
