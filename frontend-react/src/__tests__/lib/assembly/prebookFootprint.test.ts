import { describe, it, expect } from 'vitest';
import { palletFootprint, planTopUpBoxes, type TopUpCandidate } from '@/lib/assembly/prebookFootprint';
import { snapToWholePallets } from '@/lib/utils/boxPallet';

// Геометрия для теста: штук в полной паллете per SKU (upp). Разные upp = смешанная
// паллета, где показ по ОДНОМУ репрезентативному SKU врёт (баг, что чиним).
const UPP: Record<number, number> = { 1: 100, 2: 40, 3: 100 };
const uppOf = (nm: number): number => UPP[nm] ?? 0;

describe('palletFootprint — истинный footprint смешанной паллеты', () => {
    it('= Σ qty_i / upp_i (каждый SKU по своей геометрии)', () => {
        const fp = palletFootprint([{ nmId: 1, qty: 30 }, { nmId: 2, qty: 20 }], uppOf);
        expect(fp).toBeCloseTo(30 / 100 + 20 / 40, 9); // 0.3 + 0.5 = 0.8
    });

    it('одиночный SKU: совпадает с наивным qty/upp (не сломали простой случай)', () => {
        expect(palletFootprint([{ nmId: 1, qty: 250 }], uppOf)).toBeCloseTo(2.5, 9);
    });

    it('SKU без геометрии (upp=0) не входит в footprint', () => {
        expect(palletFootprint([{ nmId: 1, qty: 100 }, { nmId: 99, qty: 999 }], uppOf)).toBeCloseTo(1, 9);
    });

    it('ЛОВИТ баг rep-SKU: смешанная группа, показ по первому SKU завышал бы footprint', () => {
        const items = [{ nmId: 2, qty: 20 }, { nmId: 1, qty: 30 }]; // rep=SKU2 (upp 40)
        const trueFp = palletFootprint(items, uppOf); // 0.5 + 0.3 = 0.8
        const totalUnits = 20 + 30;
        const repFp = totalUnits / uppOf(2); // 50/40 = 1.25 — старый (неверный) показ
        expect(trueFp).toBeCloseTo(0.8, 9);
        expect(repFp).toBeCloseTo(1.25, 9);
        expect(Math.abs(repFp - trueFp)).toBeGreaterThan(0.15); // расхождение реально
    });
});

describe('planTopUpBoxes — минимальный добор целыми коробами', () => {
    const cand: TopUpCandidate[] = [{ nmId: 3, bpp: 10, ppb: 10, freeBoxes: 5 }]; // короб = 1/10 паллеты

    it('закрывает shortfall минимумом целых коробов', () => {
        const plan = planTopUpBoxes(0.2, cand); // нужно 0.2 паллеты = 2 короба по 0.1
        expect(plan.feasible).toBe(true);
        expect(plan.needBoxes).toBe(2);
        expect(plan.rows).toEqual([{ nmId: 3, boxes: 2, units: 20 }]);
        expect(plan.filledFootprint).toBeCloseTo(0.2, 9);
    });

    it('не хватает свободного ФФ → feasible=false, берёт что есть', () => {
        const plan = planTopUpBoxes(0.9, cand); // нужно 9 коробов, есть 5
        expect(plan.feasible).toBe(false);
        expect(plan.needBoxes).toBe(5);
        expect(plan.filledFootprint).toBeCloseTo(0.5, 9);
    });

    it('кандидат без геометрии (bpp null) пропускается', () => {
        const plan = planTopUpBoxes(0.2, [{ nmId: 4, bpp: null, ppb: 10, freeBoxes: 5 }]);
        expect(plan.feasible).toBe(false);
        expect(plan.needBoxes).toBe(0);
    });
});

describe('интеграция: показ предброни == авторитет snapToWholePallets', () => {
    // Смешанная поставка на одну отгрузку (ФФ→WB): SKU1 130, SKU2 20.
    const km = { '1': 130, '2': 20 };
    const snap = snapToWholePallets(km, (k) => uppOf(Number(k)));

    it('срезанный хвост (предбронь) имеет footprint < 1 паллеты', () => {
        const droppedItems = Object.entries(snap.dropped).map(([k, q]) => ({ nmId: Number(k), qty: q }));
        const fp = palletFootprint(droppedItems, uppOf);
        expect(fp).toBeGreaterThan(0);
        expect(fp).toBeLessThan(1);
    });

    it('добор по плану доводит хвост ровно до целой паллеты (snapToWholePallets оставляет её)', () => {
        const dropped = { ...snap.dropped };
        const droppedItems = Object.entries(dropped).map(([k, q]) => ({ nmId: Number(k), qty: q }));
        const fp = palletFootprint(droppedItems, uppOf);
        const shortfall = Math.ceil(fp) - fp;

        const plan = planTopUpBoxes(shortfall, [{ nmId: 3, bpp: 10, ppb: 10, freeBoxes: 20 }]);
        expect(plan.feasible).toBe(true);

        // Добавляем план в хвост и прогоняем АВТОРИТЕТ повторно.
        const combined: Record<string, number> = { ...dropped };
        for (const r of plan.rows) combined[String(r.nmId)] = (combined[String(r.nmId)] || 0) + r.units;
        const fpCombined = palletFootprint(
            Object.entries(combined).map(([k, q]) => ({ nmId: Number(k), qty: q })), uppOf,
        );
        expect(fpCombined).toBeGreaterThanOrEqual(1 - 1e-9); // достигли целой паллеты

        const snap2 = snapToWholePallets(combined, (k) => uppOf(Number(k)));
        const keptFp = palletFootprint(
            Object.entries(snap2.kept).map(([k, q]) => ({ nmId: Number(k), qty: q })), uppOf,
        );
        expect(keptFp).toBeGreaterThanOrEqual(1 - 1e-9); // целая паллета уехала в черновик
    });
});
