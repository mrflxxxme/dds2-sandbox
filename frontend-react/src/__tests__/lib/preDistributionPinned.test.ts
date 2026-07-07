import { describe, expect, it } from 'vitest';
import { applyCellBoxDelta, buildPinnedRows, type CellEdits, type PinnedPkgOf } from '@/lib/assembly/preDistribution';
import type { PackageType, PreDistPoolRow } from '@/types/api';

/** Строка пула машины (минимальный фабричный хелпер). */
function poolRow(barcode: string, nm: number, avail: number, boxQty: number | null = null): PreDistPoolRow {
    return {
        barcode,
        article_seller: `art-${nm}`,
        article_wb: String(nm),
        name: null,
        brand: null,
        gross_qty: avail,
        distributed_qty: 0,
        available_qty: avail,
        box_qty: boxQty,
        box_size: null,
        is_newcomer: false,
    };
}

const allBox: PinnedPkgOf = () => 'BOX';
const TARGET_WH = 100;

describe('buildPinnedRows', () => {
    it('пустая карта правок → нет строк', () => {
        expect(buildPinnedRows(new Map(), [poolRow('A', 1, 50)], TARGET_WH, new Map([[1, 10]]), allBox)).toEqual([]);
    });

    it('пин boxes×ppb штук, источник = ФФ разгрузки, Σsrc==Σtgt', () => {
        const edits: CellEdits = new Map([['A', { Электросталь: 3, Тула: 2 }]]);
        const rows = buildPinnedRows(edits, [poolRow('A', 1, 500)], TARGET_WH, new Map([[1, 10]]), allBox);
        expect(rows).toHaveLength(1);
        const r = rows[0];
        expect(r.tgt).toEqual({ Электросталь: 30, Тула: 20 });
        expect(r.src).toEqual({ '100': 50 });
        expect(Object.values(r.src).reduce((s, v) => s + v, 0)).toBe(
            Object.values(r.tgt).reduce((s, v) => s + v, 0),
        );
        expect(r.package_type).toBe('BOX');
    });

    it('Σ по баркоду капится доступным остатком машины (целыми коробами)', () => {
        // avail=25, ppb=10 → максимум 2 короба (20 шт). Пины просят 3+2=5 коробов.
        const edits: CellEdits = new Map([['A', { Электросталь: 3, Тула: 2 }]]);
        const rows = buildPinnedRows(edits, [poolRow('A', 1, 25)], TARGET_WH, new Map([[1, 10]]), allBox);
        const total = rows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + v, 0), 0);
        expect(total).toBe(20); // не больше floor(25/10)*10
        // Порядок складов по имени: «Тула» < «Электросталь» → Тула набирается первой.
        expect(rows[0].tgt).toEqual({ Тула: 20 });
    });

    it('SKU без кратности короба (ppb≤0) не пинится', () => {
        const edits: CellEdits = new Map([['A', { Электросталь: 3 }]]);
        expect(buildPinnedRows(edits, [poolRow('A', 1, 500)], TARGET_WH, new Map([[1, 0]]), allBox)).toEqual([]);
    });

    it('разные типы упаковки по складам → отдельные строки на pkg', () => {
        const pkgOf: PinnedPkgOf = (_nm, wb) => (wb === 'Тула' ? 'MONOPALLET' : 'BOX') as PackageType;
        const edits: CellEdits = new Map([['A', { Электросталь: 2, Тула: 1 }]]);
        const rows = buildPinnedRows(edits, [poolRow('A', 1, 500)], TARGET_WH, new Map([[1, 10]]), pkgOf);
        expect(rows).toHaveLength(2);
        const box = rows.find(r => r.package_type === 'BOX');
        const mono = rows.find(r => r.package_type === 'MONOPALLET');
        expect(box?.tgt).toEqual({ Электросталь: 20 });
        expect(mono?.tgt).toEqual({ Тула: 10 });
    });

    it('нулевые/отрицательные коробы игнорируются', () => {
        const edits: CellEdits = new Map([['A', { Электросталь: 0, Тула: -1, Казань: 2 }]]);
        const rows = buildPinnedRows(edits, [poolRow('A', 1, 500)], TARGET_WH, new Map([[1, 10]]), allBox);
        expect(rows).toHaveLength(1);
        expect(rows[0].tgt).toEqual({ Казань: 20 });
    });
});

/** Σ коробов записи. */
const sumBoxes = (rec: Record<string, number>): number => Object.values(rec).reduce((s, v) => s + v, 0);

describe('applyCellBoxDelta — прибавление/убавление ±короб', () => {
    it('+1 короб добавляет ровно 1, −1 убирает ровно 1 (divan ppb=1, avail=49)', () => {
        // Реальный кейс машины 76: все диваны ppb=1. Заморожены 3 склада по 1 коробу.
        let rec: Record<string, number> = { Шушары: 1, Тула: 1, Казань: 1 };
        rec = applyCellBoxDelta(rec, 'Шушары', +1, 1, 49);
        expect(rec.Шушары).toBe(2);
        expect(sumBoxes(rec)).toBe(4);           // 1+1 добавился ровно 1
        rec = applyCellBoxDelta(rec, 'Шушары', -1, 1, 49);
        expect(rec.Шушары).toBe(1);
        expect(sumBoxes(rec)).toBe(3);           // вернулись
        rec = applyCellBoxDelta(rec, 'Тула', -1, 1, 49);
        expect(rec.Тула).toBeUndefined();        // 0 → склад выкинут
        expect(sumBoxes(rec)).toBe(2);
    });

    it('Σ коробов НЕ превышает остаток машины floor(avail/ppb) — кламп только на кликнутом складе', () => {
        // avail=5, ppb=1 → максимум 5 коробов. Уже 4 склада по 1 = 4.
        let rec: Record<string, number> = { A: 1, B: 1, C: 1, D: 1 };
        rec = applyCellBoxDelta(rec, 'A', +10, 1, 5);   // просим +10 на A
        expect(rec.A).toBe(2);                          // клампнуто: 5 − (B+C+D=3) = 2
        expect(sumBoxes(rec)).toBe(5);                  // Σ = avail, не больше
        rec = applyCellBoxDelta(rec, 'E', +1, 1, 5);    // новый склад при полном Σ
        expect(rec.E).toBeUndefined();                  // места нет → 0
        expect(sumBoxes(rec)).toBe(5);
    });

    it('ppb>1: кламп по коробам (avail=25, ppb=10 → максимум 2 короба)', () => {
        let rec: Record<string, number> = {};
        rec = applyCellBoxDelta(rec, 'Тула', +5, 10, 25);   // просим 5 коробов
        expect(rec.Тула).toBe(2);                            // floor(25/10)=2
        // Через buildPinnedRows это = 20 шт (не больше 25, целыми коробами).
        const rows = buildPinnedRows(new Map([['A', rec]]), [poolRow('A', 1, 25)], TARGET_WH, new Map([[1, 10]]), allBox);
        expect(sumBoxes(rows[0].tgt)).toBe(20);
    });

    it('добавление на один склад не трогает соседние (пока не упёрлись в кап)', () => {
        let rec: Record<string, number> = { Шушары: 3, Тула: 2 };
        rec = applyCellBoxDelta(rec, 'Шушары', +2, 1, 100);
        expect(rec).toEqual({ Шушары: 5, Тула: 2 });    // Тула не тронута
    });

    it('инвариант Σотпр(пины)×ppb + остаётся = avail (для рендера «Остаётся ФФ»)', () => {
        const avail = 49, ppb = 1;
        let rec: Record<string, number> = { Шушары: 1, Тула: 1 };
        rec = applyCellBoxDelta(rec, 'Шушары', +5, ppb, avail);
        const rows = buildPinnedRows(new Map([['A', rec]]), [poolRow('A', 1, avail)], TARGET_WH, new Map([[1, ppb]]), allBox);
        const shipped = rows.reduce((s, r) => s + sumBoxes(r.tgt), 0);
        const stays = Math.max(0, avail - shipped);
        expect(shipped).toBe(7);      // 6+1
        expect(stays).toBe(42);       // 49 − 7 (корректный остаток машины)
        expect(shipped + stays).toBe(avail);
    });
});
