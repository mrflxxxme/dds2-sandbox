import { describe, it, expect } from 'vitest';
import { effectiveDrr, campaignEffAverages, recommendBid, recommendProjectedBid, type EffCtx } from '@/app/(main)/p/[slug]/ads-manager/components/clusterEff';
import type { SearchCluster } from '@/types/api';

// Минимальный кластер — заполняем только нужные метрики.
const cl = (o: Partial<SearchCluster>): SearchCluster => ({
    norm_query: 'q', views: 0, clicks: 0, ctr: 0, cpc: 0, cpm: 0, spend: 0,
    orders: 0, atbs: 0, shks: 0, avg_pos: 0, cr: 0, cpo: null, drr: null,
    relevant: true, tier: 'target', reason: '', is_minused: false, bid: null, locked: false,
    ...o,
} as SearchCluster);

// Кампания: AOV 1000 ₽, cart→order 25%, click→order 5%
const ctx: EffCtx = { targetDrr: 10, aov: 1000, avgCr: 5, avgCr2: 25 };

describe('effectiveDrr — эффективный ДРР (%)', () => {
    it('есть заказы → фактический ДРР, projected=false', () => {
        expect(effectiveDrr(cl({ orders: 3, drr: 8 }), ctx)).toEqual({ value: 8, projected: false });
    });

    it('locked → null (не судим)', () => {
        expect(effectiveDrr(cl({ locked: true, orders: 1, drr: 5 }), ctx)).toEqual({ value: null, projected: false });
    });

    it('без заказов, есть корзины → прогноз по корзинам × cart→order', () => {
        // 4 корзины × 25% = 1 прогнозный заказ; выручка 1×1000; ДРР = 200/1000 = 20%
        const r = effectiveDrr(cl({ clicks: 20, atbs: 4, spend: 200 }), ctx);
        expect(r.projected).toBe(true);
        expect(r.value).toBeCloseTo(20, 5);
    });

    it('без заказов и без корзин → прогноз по кликам × click→order', () => {
        // 40 кликов × 5% = 2 прогнозных заказа; выручка 2000; ДРР = 400/2000 = 20%
        const r = effectiveDrr(cl({ clicks: 40, atbs: 0, spend: 400 }), ctx);
        expect(r.projected).toBe(true);
        expect(r.value).toBeCloseTo(20, 5);
    });

    it('плохой беззаказный (мало кликов, большой расход) → высокий прогнозный ДРР (красный)', () => {
        // 10 кликов × 5% = 0.5 заказа; выручка 500; ДРР = 450/500 = 90%
        const r = effectiveDrr(cl({ clicks: 10, atbs: 0, spend: 450 }), ctx);
        expect(r.value).toBeCloseTo(90, 5);
    });

    it('нет активности/сигнала (нет кликов и корзин) → null', () => {
        expect(effectiveDrr(cl({ clicks: 0, atbs: 0, spend: 0 }), ctx)).toEqual({ value: null, projected: true });
    });

    it('нет AOV → прогноз невозможен, null', () => {
        expect(effectiveDrr(cl({ clicks: 40, spend: 400 }), { ...ctx, aov: 0 }).value).toBeNull();
    });
});

describe('recommendBid — рекомендация ставки (цель ДРР 8%)', () => {
    const opts = { targetDrr: 8, defaultBid: 610 };

    it('подъём: ДРР 1,3% + не топ (поз. 1,9) → шаг к целому 2%, ставка 700×2/1,3', () => {
        const r = recommendBid(cl({ orders: 5, drr: 1.3, bid: 700, avg_pos: 1.9 }), opts);
        expect(r).not.toBeNull();
        expect(r!.dir).toBe('up');
        expect(r!.stepDrr).toBe(2);
        expect(r!.bid).toBe(Math.round(700 * 2 / 1.3));   // ≈1077
        expect(r!.ceilBid).toBe(Math.round(700 * 8 / 1.3)); // потолок по 8% ≈4308
    });

    it('уже топ-1 (поз. 1,0) при низком ДРР → рекомендации нет', () => {
        expect(recommendBid(cl({ orders: 5, drr: 1.3, bid: 700, avg_pos: 1.0 }), opts)).toBeNull();
    });

    it('снижение: ДРР 24,8% > 8% → шаг к 24%, ставка вниз', () => {
        const r = recommendBid(cl({ orders: 2, drr: 24.8, bid: 610, avg_pos: 12 }), opts);
        expect(r!.dir).toBe('down');
        expect(r!.stepDrr).toBe(24);
        expect(r!.bid).toBe(Math.round(610 * 24 / 24.8));
    });

    it('ДРР ровно на цели (8%) → держать (null)', () => {
        expect(recommendBid(cl({ orders: 3, drr: 8, bid: 500, avg_pos: 5 }), opts)).toBeNull();
    });

    it('нет заказов → рекомендации нет', () => {
        expect(recommendBid(cl({ orders: 0, drr: null, bid: 700, avg_pos: 5 }), opts)).toBeNull();
    });

    it('нет своей ставки → берём базовую ставку кампании (defaultBid)', () => {
        const r = recommendBid(cl({ orders: 4, drr: 4, bid: null, avg_pos: 6 }), opts);
        expect(r!.dir).toBe('up');
        expect(r!.stepDrr).toBe(5);
        expect(r!.bid).toBe(Math.round(610 * 5 / 4));
    });

    it('нет ни своей, ни базовой ставки → null', () => {
        expect(recommendBid(cl({ orders: 4, drr: 4, bid: null, avg_pos: 6 }), { targetDrr: 8, defaultBid: null })).toBeNull();
    });

    // ── Порог заказов для ПОДЪЁМА (ДРР по 1–2 заказам ненадёжен) ──
    it('подъём заблокирован при заказах < порога (по умолчанию 3)', () => {
        expect(recommendBid(cl({ orders: 2, drr: 4, bid: 700, avg_pos: 6 }), opts)).toBeNull();
    });

    it('подъём разрешён при заказах = порогу', () => {
        expect(recommendBid(cl({ orders: 3, drr: 4, bid: 700, avg_pos: 6 }), opts)!.dir).toBe('up');
    });

    it('снижение НЕ гейтится порогом заказов (резать расход безопаснее)', () => {
        // orders 1 < 3, но ДРР 24,8% > цели → снижение всё равно рекомендуем
        expect(recommendBid(cl({ orders: 1, drr: 24.8, bid: 610, avg_pos: 12 }), opts)!.dir).toBe('down');
    });

    it('порог заказов переопределяется minOrdersUp', () => {
        expect(recommendBid(cl({ orders: 2, drr: 4, bid: 700, avg_pos: 6 }), { ...opts, minOrdersUp: 1 })!.dir).toBe('up');
    });

    // ── Кламп к минимальной ставке WB (по умолчанию 150 ₽) ──
    it('снижение ниже минимума WB клампится к минимуму', () => {
        // база 155, ДРР 20% → шаг 19, ставка 155×19/20 ≈ 147 < 150 → зажимается к 150
        const r = recommendBid(cl({ orders: 2, drr: 20, bid: 155, avg_pos: 8 }), opts);
        expect(r!.dir).toBe('down');
        expect(r!.bid).toBe(150);
    });

    it('база уже на минимуме WB → снижения нет (null)', () => {
        expect(recommendBid(cl({ orders: 2, drr: 20, bid: 150, avg_pos: 8 }), opts)).toBeNull();
    });

    it('минимум WB переопределяется minBid', () => {
        const r = recommendBid(cl({ orders: 2, drr: 20, bid: 155, avg_pos: 8 }), { ...opts, minBid: 100 });
        expect(r!.dir).toBe('down');
        expect(r!.bid).toBe(Math.round(155 * 19 / 20));  // 147, кламп 100 не срабатывает
    });

    it('доказанная рекомендация помечена projected=false', () => {
        expect(recommendBid(cl({ orders: 5, drr: 1.3, bid: 700, avg_pos: 1.9 }), opts)!.projected).toBe(false);
    });

    // ── Якорь = оплаченная CPM, а не текущая ставка (фикс разгона рекомендации) ──
    it('якорь формулы — оплаченная CPM, а не текущая ставка', () => {
        // bid=1000, но реально оплачено cpm=742; ДРР 2,2% считаем от cpm.
        const r = recommendBid(cl({ orders: 5, drr: 2.2, cpm: 742, bid: 1000, avg_pos: 4 }), opts);
        expect(r!.dir).toBe('up');
        expect(r!.stepDrr).toBe(3);
        expect(r!.bid).toBe(Math.round(742 * 3 / 2.2));     // от cpm (≈1012), НЕ от ставки 1000
        expect(r!.ceilBid).toBe(Math.round(742 * 8 / 2.2)); // потолок от cpm ≈2698
    });

    it('нет разгона ВВЕРХ: ставку уже подняли, ДРР ещё старый (низкий) → рекомендации нет', () => {
        // Применили потолок: bid=2698, но оплаченная cpm=742 и ДРР 2,2% пока не обновились.
        // Целевой шаг от cpm (≈1012) уже НИЖЕ текущей ставки → поднимать некуда, не 10000.
        expect(recommendBid(cl({ orders: 5, drr: 2.2, cpm: 742, bid: 2698, avg_pos: 4 }), opts)).toBeNull();
    });

    it('нет разгона ВНИЗ: ставку уже снизили, ДРР ещё старый (высокий) → рекомендации нет', () => {
        // Снизили до потолка bid=425, оплаченная cpm=727 и ДРР 13,7% пока старые.
        // Целевой шаг от cpm (≈690) уже ВЫШЕ текущей ставки → снижать некуда, не пол WB.
        expect(recommendBid(cl({ orders: 5, drr: 13.7, cpm: 727, bid: 425, avg_pos: 12 }), opts)).toBeNull();
    });

    it('сходимость: после применения потолка «Сразу к T» повторная рекомендация исчезает', () => {
        const c = { orders: 5, cpm: 742, drr: 2.2, avg_pos: 4 };
        const ceil = recommendBid(cl({ ...c, bid: 1000 }), opts)!.ceilBid;   // потолок от cpm
        // применили ceil → ставка = ceil, cpm/drr ещё те же → рекомендации больше нет
        expect(recommendBid(cl({ ...c, bid: ceil }), opts)).toBeNull();
    });

    it('нет cpm (0 показов/расхода) → фолбэк на ставку (старое поведение)', () => {
        const r = recommendBid(cl({ orders: 5, drr: 1.3, cpm: 0, bid: 700, avg_pos: 1.9 }), opts);
        expect(r!.bid).toBe(Math.round(700 * 2 / 1.3));   // якорь = ставка 700
    });
});

describe('recommendProjectedBid — рекомендация по ПРОГНОЗНОМУ ДРР (беззаказные)', () => {
    const opts = { targetDrr: 8, defaultBid: 610 };

    it('снижение при высоком прогнозном ДРР — свободно, даже без корзин', () => {
        // 0 заказов, 0 корзин, прогнозный ДРР 20% > 8% → снижаем (резать вероятный слив)
        const r = recommendProjectedBid(cl({ orders: 0, atbs: 0, bid: 610, avg_pos: 8 }), 20, opts);
        expect(r!.dir).toBe('down');
        expect(r!.projected).toBe(true);
        expect(r!.bid).toBe(Math.round(610 * 19 / 20));
    });

    it('подъём при низком прогнозном ДРР — только при достаточном сигнале корзин (≥3)', () => {
        const r = recommendProjectedBid(cl({ orders: 0, atbs: 5, bid: 610, avg_pos: 6 }), 4, opts);
        expect(r!.dir).toBe('up');
        expect(r!.projected).toBe(true);
        expect(r!.stepDrr).toBe(5);
    });

    it('подъём заблокирован при корзинах < 3 (сигнал воронки слабый)', () => {
        expect(recommendProjectedBid(cl({ orders: 0, atbs: 2, bid: 610, avg_pos: 6 }), 4, opts)).toBeNull();
    });

    it('подъём заблокирован при топ-1 (поз. 1,0)', () => {
        expect(recommendProjectedBid(cl({ orders: 0, atbs: 5, bid: 610, avg_pos: 1.0 }), 4, opts)).toBeNull();
    });

    it('порог корзин переопределяется minCartsUp', () => {
        expect(recommendProjectedBid(cl({ orders: 0, atbs: 1, bid: 610, avg_pos: 6 }), 4, { ...opts, minCartsUp: 1 })!.dir).toBe('up');
    });

    it('у фразы С заказами → null (это путь recommendBid)', () => {
        expect(recommendProjectedBid(cl({ orders: 3, atbs: 5, bid: 610, avg_pos: 6 }), 4, opts)).toBeNull();
    });

    it('прогнозный ДРР ≤ 0 или не число → null', () => {
        expect(recommendProjectedBid(cl({ orders: 0, atbs: 5, bid: 610, avg_pos: 6 }), 0, opts)).toBeNull();
        expect(recommendProjectedBid(cl({ orders: 0, atbs: 5, bid: 610, avg_pos: 6 }), NaN, opts)).toBeNull();
    });

    it('нет базовой ставки → null', () => {
        expect(recommendProjectedBid(cl({ orders: 0, atbs: 5, bid: null, avg_pos: 6 }), 4, { targetDrr: 8, defaultBid: null })).toBeNull();
    });
});

describe('campaignEffAverages — средние конверсии кампании', () => {
    it('считает click→order и cart→order из сумм', () => {
        const clusters = [
            cl({ clicks: 40, atbs: 4, orders: 2 }),
            cl({ clicks: 60, atbs: 4, orders: 0 }),
        ];
        const a = campaignEffAverages(clusters, 10, 1500);
        expect(a.avgCr).toBeCloseTo(2, 5);    // 2 заказа / 100 кликов
        expect(a.avgCr2).toBeCloseTo(25, 5);  // 2 заказа / 8 корзин
        expect(a.aov).toBe(1500);
        expect(a.targetDrr).toBe(10);
    });
});
