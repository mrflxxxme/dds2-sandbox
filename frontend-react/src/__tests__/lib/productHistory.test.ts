import { describe, it, expect } from 'vitest';
import { toHistoryPoints, HISTORY_LINES } from '@/app/(main)/p/[slug]/ads-manager/components/ProductHistoryChart';
import type { FunnelDayRow } from '@/types/api';

const row = (over: Partial<FunnelDayRow>): FunnelDayRow => ({
    date: '2026-06-15', opens: 0, add_to_cart: 0, orders: 0, orders_sum: 0, buyout_sum: 0, ...over,
} as FunnelDayRow);

describe('toHistoryPoints — данные графика истории артикула', () => {
    it('5 линий графика соответствуют ТЗ', () => {
        expect(HISTORY_LINES.map(l => l.key)).toEqual(['price_spp', 'open_card', 'adv_sum', 'drr', 'orders_sum_rub']);
    });

    it('цена с СПП = avg_price × (1 − spp/100), без СПП — как есть', () => {
        const pts = toHistoryPoints([
            row({ date: '2026-06-15', avg_price: 1000, spp_rate: 30 }),
            row({ date: '2026-06-16', avg_price: 1000 }),
        ]);
        expect(pts[0].price_spp).toBe(700);
        expect(pts[1].price_spp).toBe(1000);
    });

    it('сортирует по дате и коэрсит Decimal-строки бэка в числа', () => {
        const pts = toHistoryPoints([
            row({ date: '2026-06-16', adv_sum: '264.37' as unknown as number, open_card: 65 }),
            row({ date: '2026-06-15', orders_sum_rub: '1500.5' as unknown as number, drr: 12.3 }),
        ]);
        expect(pts.map(p => p.date)).toEqual(['2026-06-15', '2026-06-16']);
        expect(pts[0].orders_sum_rub).toBe(1500.5);
        expect(pts[0].drr).toBe(12.3);
        expect(pts[1].adv_sum).toBe(264.37);
        expect(pts[1].open_card).toBe(65);
    });
});
