import { describe, expect, it } from 'vitest';
import { buildPinnedRows, type CellEdits, type PinnedPkgOf } from '@/lib/assembly/preDistribution';
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
