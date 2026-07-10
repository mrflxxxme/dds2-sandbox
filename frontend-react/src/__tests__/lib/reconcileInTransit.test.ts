/**
 * reconcileInTransit — вычет «уже едет в заявках» из строк черновика
 * («один мир» черновика и заявок; прод-кейс «швабры апл» 2026-07-10:
 * 30 шт ехали на Самару и 24 на Сарапул заявками pre-dist, черновик
 * предлагал те же склады повторно).
 */
import { describe, expect, it } from 'vitest';
import { inTransitMap, subtractInTransitFromRows } from '@/lib/assembly/reconcileInTransit';
import type { AssemblyDraftRow, InTransitItem } from '@/types/api';

const row = (nm: number, src: Record<string, number>, tgt: Record<string, number>): AssemblyDraftRow => ({
    nm_id: nm,
    barcode: `bc-${nm}`,
    vendor_code: `sku-${nm}`,
    src,
    tgt,
    package_type: 'BOX',
} as AssemblyDraftRow);

const sum = (r: Record<string, number>) => Object.values(r).reduce((s, v) => s + (v || 0), 0);

describe('inTransitMap', () => {
    it('агрегирует items по nm+складу, нули отбрасывает', () => {
        const items: InTransitItem[] = [
            { nm_id: 1, warehouse_name: 'Сарапул', quantity: 10 },
            { nm_id: 1, warehouse_name: 'Сарапул', quantity: 14 },
            { nm_id: 1, warehouse_name: 'Казань', quantity: 0 },
        ];
        const m = inTransitMap(items);
        expect(m.get(1)).toEqual({ Сарапул: 24 });
    });
});

describe('subtractInTransitFromRows', () => {
    it('прод-кейс швабры: вычитает едущее per-склад, src ужимается до Σtgt', () => {
        // Черновик: Самара 32 + Сарапул 12 + ЕКБ 52; едет: Самара 30, Сарапул 24.
        const rows = [row(896057749, { '5': 96 }, { 'Самара (Новосемейкино)': 32, 'Сарапул': 12, 'Екатеринбург - Перспективная 14': 52 })];
        const transit = inTransitMap([
            { nm_id: 896057749, warehouse_name: 'Самара (Новосемейкино)', quantity: 30 },
            { nm_id: 896057749, warehouse_name: 'Сарапул', quantity: 24 },
        ]);
        const res = subtractInTransitFromRows(rows, transit);
        expect(res.changed).toBe(true);
        expect(res.subtractedUnits).toBe(30 + 12); // Самара min(32,30)=30, Сарапул min(12,24)=12
        const r = res.rows[0];
        expect(r.tgt).toEqual({ 'Самара (Новосемейкино)': 2, 'Екатеринбург - Перспективная 14': 52 });
        expect(sum(r.src)).toBe(sum(r.tgt)); // Σsrc == Σtgt (carve)
        // Остаток transit: Сарапул 24−12=12 — уйдёт на prebook-проход.
        expect(res.remainingTransit.get(896057749)).toEqual({ Сарапул: 12 });
    });

    it('строка, полностью покрытая едущим, удаляется', () => {
        const rows = [row(1, { '5': 20 }, { Казань: 20 })];
        const res = subtractInTransitFromRows(rows, inTransitMap([{ nm_id: 1, warehouse_name: 'Казань', quantity: 50 }]));
        expect(res.rows).toHaveLength(0);
        expect(res.removedRows).toBe(1);
        expect(res.subtractedUnits).toBe(20);
    });

    it('SKU без пересечения не трогается (тот же объект)', () => {
        const rows = [row(1, { '5': 10 }, { Казань: 10 }), row(2, { '5': 8 }, { Тула: 8 })];
        const res = subtractInTransitFromRows(rows, inTransitMap([{ nm_id: 1, warehouse_name: 'Краснодар', quantity: 99 }]));
        expect(res.changed).toBe(false);
        expect(res.rows[1]).toBe(rows[1]);
        expect(res.rows[0]).toBe(rows[0]); // склад не совпал — вычета нет
    });

    it('transit расходуется последовательно между строками одного nm (нет двойного вычета)', () => {
        const rows = [
            row(1, { '5': 10 }, { Казань: 10 }),
            row(1, { '6': 10 }, { Казань: 10 }),
        ];
        const res = subtractInTransitFromRows(rows, inTransitMap([{ nm_id: 1, warehouse_name: 'Казань', quantity: 15 }]));
        // Первая строка съедает 10, второй остаётся вычесть 5.
        expect(res.rows).toHaveLength(1);
        expect(res.rows[0].tgt).toEqual({ Казань: 5 });
        expect(res.subtractedUnits).toBe(15);
        expect(res.remainingTransit.size).toBe(0);
    });

    it('src ужимается с крупнейших источников', () => {
        const rows = [row(1, { '5': 30, '6': 10 }, { Казань: 40 })];
        const res = subtractInTransitFromRows(rows, inTransitMap([{ nm_id: 1, warehouse_name: 'Казань', quantity: 25 }]));
        const r = res.rows[0];
        expect(r.tgt).toEqual({ Казань: 15 });
        expect(sum(r.src)).toBe(15);
        expect(r.src['6']).toBe(10); // мелкий источник не тронут, срез с крупного
        expect(r.src['5']).toBe(5);
    });
});
