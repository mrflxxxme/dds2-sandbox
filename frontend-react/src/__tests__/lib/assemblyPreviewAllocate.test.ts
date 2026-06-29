import { describe, it, expect } from 'vitest';
import { allocatePairs } from '@/lib/utils/assemblyPreview';

/** Σ по каждому ФФ и по каждому складу из раскладки пар "ffId::wbName" → qty. */
function marginals(alloc: Map<string, number>): { perSrc: Record<string, number>; perTgt: Record<string, number> } {
    const perSrc: Record<string, number> = {};
    const perTgt: Record<string, number> = {};
    for (const [key, q] of alloc) {
        expect(q).toBeGreaterThan(0);
        const [sid, tname] = key.split('::');
        perSrc[sid] = (perSrc[sid] || 0) + q;
        perTgt[tname] = (perTgt[tname] || 0) + q;
    }
    return { perSrc, perTgt };
}

function checkBalanced(src: Record<string, number>, tgt: Record<string, number>): void {
    const { perSrc, perTgt } = marginals(allocatePairs(src, tgt));
    const expSrc = Object.fromEntries(Object.entries(src).filter(([, v]) => v > 0));
    const expTgt = Object.fromEntries(Object.entries(tgt).filter(([, v]) => v > 0));
    expect(perSrc).toEqual(expSrc); // каждый ФФ отгружает ровно свой запас
    expect(perTgt).toEqual(expTgt); // каждый склад получает ровно свою потребность
}

describe('allocatePairs — сохранение обоих маргиналов (зеркало backend _allocate_pairs)', () => {
    it('контрпример старой joint-pro-rata: src={1:1,2:1}, tgt={A:1,B:1}', () => {
        // Старый алгоритм давал {(1,A):1,(1,B):1} → ФФ1 «отгружал» 2, ФФ2 выпадал.
        const { perSrc } = marginals(allocatePairs({ '1': 1, '2': 1 }, { A: 1, B: 1 }));
        expect(perSrc).toEqual({ '1': 1, '2': 1 });
    });

    it('маргиналы держатся на разных сбалансированных сетках', () => {
        checkBalanced({ '1': 1, '2': 1 }, { A: 1, B: 1 });
        checkBalanced({ '10': 6, '20': 4 }, { Электросталь: 5, Казань: 5 });
        checkBalanced({ '10': 10 }, { A: 6, B: 4 });
        checkBalanced({ '1': 7, '2': 11, '3': 5 }, { X: 9, Y: 9, Z: 5 });
        checkBalanced({ '5': 3, '1': 100 }, { Z: 50, A: 53 }); // ключи намеренно не отсортированы
    });

    it('итог суммой равен min(Σsrc, Σtgt) и не превышает ни одну сторону', () => {
        const total = (m: Record<string, number>) => Object.values(m).reduce((s, v) => s + v, 0);
        const src = { '1': 6, '2': 4 };
        const tgt = { A: 5, B: 5 };
        let shipped = 0;
        for (const [, q] of allocatePairs(src, tgt)) shipped += q;
        expect(shipped).toBe(Math.min(total(src), total(tgt)));
    });

    it('пустые src/tgt → пустая раскладка', () => {
        expect(allocatePairs({}, { A: 5 }).size).toBe(0);
        expect(allocatePairs({ '1': 5 }, {}).size).toBe(0);
        expect(allocatePairs({ '1': 0 }, { A: 0 }).size).toBe(0);
    });
});
