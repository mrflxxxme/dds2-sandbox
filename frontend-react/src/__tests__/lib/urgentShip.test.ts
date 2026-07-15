import { describe, it, expect } from 'vitest';
import { buildUrgentShip, classifyUrgency } from '@/lib/assembly/urgentShip';
import type { StockAnalyticsArticle } from '@/types/api';

/** Минимальный артикул аналитики остатков для тестов. */
function art(over: Partial<StockAnalyticsArticle> & { nm_id: number }): StockAnalyticsArticle {
    return {
        vendor_code: `SKU-${over.nm_id}`,
        subject: 'Ковры',
        brand: 'НУ-НУ',
        orders_30d: 30,
        trend_pct: 0,
        avg_daily: 1,
        stocks_wb: 10,
        days_left: 10,
        traffic_light: 'orange',
        forecast: [],
        stocks_rf: 100,
        revenue_bdr: 700,
        ...over,
    };
}

describe('classifyUrgency', () => {
    it('0 и меньше — «уже ноль»', () => {
        expect(classifyUrgency(0)).toBe('zero');
        expect(classifyUrgency(-3)).toBe('zero');
    });
    it('меньше 7 — критично, 7–14 — опасно (пороги Аналитики остатков)', () => {
        expect(classifyUrgency(1)).toBe('critical');
        expect(classifyUrgency(6)).toBe('critical');
        expect(classifyUrgency(7)).toBe('danger');
        expect(classifyUrgency(14)).toBe('danger');
    });
    it('больше 14 — не срочно', () => {
        expect(classifyUrgency(15)).toBeNull();
        expect(classifyUrgency(30)).toBeNull();
    });
});

describe('buildUrgentShip', () => {
    it('делит на сегменты: в черновике / вне черновика (только с ФФ-стоком)', () => {
        const res = buildUrgentShip({
            articles: [
                art({ nm_id: 1, days_left: 3 }),                 // в черновике
                art({ nm_id: 2, days_left: 5, stocks_rf: 40 }),  // вне черновика, ФФ есть
                art({ nm_id: 3, days_left: 5, stocks_rf: 0 }),   // вне черновика, ФФ пуст → скрыт
                art({ nm_id: 4, days_left: 20 }),                // не срочный
            ],
            draftQtyByNm: new Map([[1, 24]]),
            leadDays: 10,
        });
        expect(res.inDraft.map(r => r.nm_id)).toEqual([1]);
        expect(res.inDraft[0].draftQty).toBe(24);
        expect(res.missing.map(r => r.nm_id)).toEqual([2]);
        expect(res.missing[0].ffFree).toBe(40);
    });

    it('потери = ₽/день × разрыв (плечо − запас), не ниже нуля', () => {
        const res = buildUrgentShip({
            articles: [
                art({ nm_id: 1, days_left: 4, revenue_bdr: 700 }),   // daily 100, gap 6 → 600
                art({ nm_id: 2, days_left: 12, revenue_bdr: 1400 }), // запас > плеча → 0
            ],
            draftQtyByNm: new Map([[1, 1], [2, 1]]),
            leadDays: 10,
        });
        const byNm = new Map(res.inDraft.map(r => [r.nm_id, r]));
        expect(byNm.get(1)?.dailyRevenue).toBe(100);
        expect(byNm.get(1)?.gapDays).toBe(6);
        expect(byNm.get(1)?.loss).toBe(600);
        expect(byNm.get(2)?.gapDays).toBe(0);
        expect(byNm.get(2)?.loss).toBe(0);
        expect(res.totalLoss).toBe(600);
    });

    it('отрицательный запас клампится в 0: разрыв = всё плечо', () => {
        const res = buildUrgentShip({
            articles: [art({ nm_id: 1, days_left: -2, revenue_bdr: 70 })],
            draftQtyByNm: new Map([[1, 5]]),
            leadDays: 8,
        });
        expect(res.inDraft[0].bucket).toBe('zero');
        expect(res.inDraft[0].daysLeft).toBe(0);
        expect(res.inDraft[0].gapDays).toBe(8);
        expect(res.inDraft[0].loss).toBe(80);
    });

    it('сортировка: сначала «уже ноль», внутри корзины — по потерям', () => {
        const res = buildUrgentShip({
            articles: [
                art({ nm_id: 1, days_left: 8, revenue_bdr: 7000 }),  // danger, потери большие
                art({ nm_id: 2, days_left: 0, revenue_bdr: 70 }),    // zero, потери маленькие
                art({ nm_id: 3, days_left: 3, revenue_bdr: 700 }),   // critical
                art({ nm_id: 4, days_left: 3, revenue_bdr: 7000 }),  // critical, потери больше
            ],
            draftQtyByNm: new Map([[1, 1], [2, 1], [3, 1], [4, 1]]),
            leadDays: 10,
        });
        expect(res.inDraft.map(r => r.nm_id)).toEqual([2, 4, 3, 1]);
    });

    it('локализация подтягивается по nm_id, отсутствие — null', () => {
        const res = buildUrgentShip({
            articles: [art({ nm_id: 1, days_left: 2 }), art({ nm_id: 2, days_left: 2 })],
            draftQtyByNm: new Map([[1, 1], [2, 1]]),
            locPctByNm: new Map([[1, 82.5]]),
            leadDays: 7,
        });
        const byNm = new Map(res.inDraft.map(r => [r.nm_id, r]));
        expect(byNm.get(1)?.locPct).toBe(82.5);
        expect(byNm.get(2)?.locPct).toBeNull();
    });

    it('счётчики корзин и суммы считаются по сегменту «в черновике»', () => {
        const res = buildUrgentShip({
            articles: [
                art({ nm_id: 1, days_left: 0, revenue_bdr: 700 }),
                art({ nm_id: 2, days_left: 5, revenue_bdr: 700 }),
                art({ nm_id: 3, days_left: 10, revenue_bdr: 700 }),
                art({ nm_id: 4, days_left: 0, revenue_bdr: 700, stocks_rf: 9 }), // вне черновика
            ],
            draftQtyByNm: new Map([[1, 1], [2, 1], [3, 1]]),
            leadDays: 10,
        });
        expect(res.zeroCount).toBe(1);
        expect(res.criticalCount).toBe(1);
        expect(res.dangerCount).toBe(1);
        expect(res.totalLoss).toBe(1000 + 500 + 0);
        expect(res.missingLoss).toBe(1000);
    });

    it('revenue_bdr отсутствует → потери 0, строка остаётся видимой', () => {
        const res = buildUrgentShip({
            articles: [art({ nm_id: 1, days_left: 1, revenue_bdr: undefined })],
            draftQtyByNm: new Map([[1, 3]]),
            leadDays: 10,
        });
        expect(res.inDraft).toHaveLength(1);
        expect(res.inDraft[0].loss).toBe(0);
    });
});
