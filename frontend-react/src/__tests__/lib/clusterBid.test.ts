import { describe, it, expect } from 'vitest';
import { cpmForTargetCpc } from '@/app/(main)/p/[slug]/ads-manager/components/clusterBid';

describe('cpmForTargetCpc — целевой CPC → ставка CPM', () => {
    it('CPM = CPC × CTR% × 10', () => {
        // CTR 2% ⇒ 20 кликов на 1000 показов ⇒ ставка 300 ₽ даёт клик по 15 ₽
        expect(cpmForTargetCpc(15, 2)).toBe(300);
        expect(cpmForTargetCpc(30, 2)).toBe(600);
        expect(cpmForTargetCpc(15, 4)).toBe(600);
    });

    it('обратная проверка: из полученной ставки выходит заданный CPC', () => {
        const ctr = 3.31, targetCpc = 8;
        const cpm = cpmForTargetCpc(targetCpc, ctr)!;
        expect(cpm / (10 * ctr)).toBeCloseTo(targetCpc, 1);
    });

    it('округляет до целых рублей', () => {
        expect(cpmForTargetCpc(8, 3.31)).toBe(265);  // 264.8 → 265
    });

    it('без кликов (CTR = 0) ставка недостижима — null', () => {
        expect(cpmForTargetCpc(15, 0)).toBeNull();
    });

    it('нулевой или отрицательный CPC — null', () => {
        expect(cpmForTargetCpc(0, 5)).toBeNull();
        expect(cpmForTargetCpc(-10, 5)).toBeNull();
    });

    it('NaN на входе не превращается в ставку', () => {
        expect(cpmForTargetCpc(NaN, 5)).toBeNull();
        expect(cpmForTargetCpc(15, NaN)).toBeNull();
    });
});
