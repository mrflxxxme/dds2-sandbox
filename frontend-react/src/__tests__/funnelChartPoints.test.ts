import { describe, it, expect } from 'vitest';
import { dailyPoints, CHART_SERIES, DEFAULT_CHART_SERIES } from '@/app/(main)/p/[slug]/funnel/components/FunnelChart';
import type { Row } from '@/app/(main)/p/[slug]/funnel/components/columns';

/** Строки воронки за день: в детализированном режиме их несколько на одну дату. */
const row = (date: string, o: Partial<Row>): Row => ({ date, ...o }) as Row;

describe('dailyPoints — свод дня для графика', () => {
    it('складывает строки одной даты и сортирует дни по возрастанию', () => {
        const pts = dailyPoints([
            row('2026-07-02', { orders_count: 3, orders_sum_rub: 300, adv_sum: 30 }),
            row('2026-07-01', { orders_count: 1, orders_sum_rub: 100, adv_sum: 10 }),
            row('2026-07-01', { orders_count: 4, orders_sum_rub: 400, adv_sum: 40 }),
        ]);
        expect(pts.map(p => p.date)).toEqual(['2026-07-01', '2026-07-02']);
        expect(pts[0].v.orders_count).toBe(5);
        expect(pts[0].v.orders_sum_rub).toBe(500);
    });

    it('производные метрики выводит из сумм, а не усредняет дневные проценты', () => {
        // ДРР дня = расход / сумма заказов по ВСЕМ строкам дня: 60 / 600 = 10 %,
        // а среднее двух строчных ДРР (20 % и 8 %) дало бы 14 % — так считать нельзя.
        const pts = dailyPoints([
            row('2026-07-01', { orders_count: 1, orders_sum_rub: 100, adv_sum: 20 }),
            row('2026-07-01', { orders_count: 5, orders_sum_rub: 500, adv_sum: 40 }),
        ]);
        expect(pts[0].v.drr).toBeCloseTo(10, 6);
    });

    it('строки без даты игнорирует, пустой вход даёт пустой ряд', () => {
        expect(dailyPoints([row('', { orders_count: 9 })])).toEqual([]);
        expect(dailyPoints([])).toEqual([]);
    });
});

describe('каталог метрик графика', () => {
    it('не содержит складских колонок — они грузятся отдельным запросом', () => {
        expect(CHART_SERIES.some(s => s.col.extendedOnly)).toBe(false);
    });

    it('проценты и средние рисует линией, потоковые метрики — объёмом', () => {
        const by = (k: string) => CHART_SERIES.find(s => s.key === k);
        expect(by('revenue')?.bar).toBe(true);
        expect(by('orders_count')?.bar).toBe(true);
        expect(by('drr')?.bar).toBe(false);
        expect(by('avg_price')?.bar).toBe(false);
    });

    it('метрики по умолчанию есть в каталоге', () => {
        for (const key of DEFAULT_CHART_SERIES) {
            expect(CHART_SERIES.some(s => s.key === key)).toBe(true);
        }
    });
});
