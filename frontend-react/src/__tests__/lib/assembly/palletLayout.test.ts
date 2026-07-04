import { describe, expect, it } from 'vitest';
import type { AssemblyRequestItem } from '@/types/api';
import {
    autoManifest,
    checkInvariant,
    moveBoxes,
    moveLoose,
    placedUnits,
    removePallet,
} from '@/lib/assembly/palletLayout';

function item(barcode: string, quantity: number): AssemblyRequestItem {
    return { id: 0, nomenclature_id: 0, barcode, quantity, stock_quantity: 0 };
}

// ppb: bc1 = 10, bc2 = 10, bc3 = нет кратности
const ppbOf = (bc: string): number | null => (bc === 'bc3' ? null : 10);

describe('autoManifest', () => {
    it('раскладывает целые короба + хвост-россыпь', () => {
        const m = autoManifest([item('bc1', 23)], ppbOf, 1);
        expect(m[0].boxes[0]).toEqual({ barcode: 'bc1', box_count: 2, loose_units: 3 });
    });

    it('ШК без кратности — всё в россыпь', () => {
        const m = autoManifest([item('bc3', 7)], ppbOf, 1);
        expect(m[0].boxes[0]).toEqual({ barcode: 'bc3', box_count: 0, loose_units: 7 });
    });

    it('round-robin по паллетам', () => {
        const m = autoManifest([item('bc1', 10), item('bc2', 10)], ppbOf, 2);
        expect(m).toHaveLength(2);
        expect(m[0].boxes[0].barcode).toBe('bc1');
        expect(m[1].boxes[0].barcode).toBe('bc2');
    });
});

describe('checkInvariant', () => {
    it('ok, когда разложено ровно quantity', () => {
        const m = autoManifest([item('bc1', 23)], ppbOf, 1);
        expect(checkInvariant(m, [item('bc1', 23)], ppbOf).ok).toBe(true);
    });

    it('ловит недостачу', () => {
        const m = [{ pallet_no: 1, boxes: [{ barcode: 'bc1', box_count: 1, loose_units: 0 }] }];
        const r = checkInvariant(m, [item('bc1', 23)], ppbOf);
        expect(r.ok).toBe(false);
        expect(r.mismatches[0]).toEqual({ barcode: 'bc1', placed: 10, need: 23 });
    });
});

describe('moveBoxes (сплит/перенос)', () => {
    it('переносит N из M коробов между паллетами', () => {
        const start = [
            { pallet_no: 1, boxes: [{ barcode: 'bc1', box_count: 4, loose_units: 0 }] },
            { pallet_no: 2, boxes: [] },
        ];
        const next = moveBoxes(start, 1, 2, 'bc1', 1);
        expect(next[0].boxes[0].box_count).toBe(3);
        expect(next[1].boxes[0].box_count).toBe(1);
        // Инвариант сохраняется (штук всего столько же).
        expect(placedUnits(next, ppbOf).get('bc1')).toBe(40);
    });

    it('не переносит больше, чем есть на источнике', () => {
        const start = [
            { pallet_no: 1, boxes: [{ barcode: 'bc1', box_count: 2, loose_units: 0 }] },
            { pallet_no: 2, boxes: [] },
        ];
        const next = moveBoxes(start, 1, 2, 'bc1', 5);
        expect(next[1].boxes[0].box_count).toBe(2);
        expect(next[0].boxes).toHaveLength(0); // источник опустел → короб убран
    });
});

describe('moveLoose', () => {
    it('переносит россыпь', () => {
        const start = [
            { pallet_no: 1, boxes: [{ barcode: 'bc1', box_count: 0, loose_units: 5 }] },
            { pallet_no: 2, boxes: [] },
        ];
        const next = moveLoose(start, 1, 2, 'bc1', 2);
        expect(next[0].boxes[0].loose_units).toBe(3);
        expect(next[1].boxes[0].loose_units).toBe(2);
    });
});

describe('removePallet', () => {
    it('короба удалённой паллеты уезжают в буфер, не теряются', () => {
        const start = [
            { pallet_no: 1, boxes: [{ barcode: 'bc1', box_count: 2, loose_units: 3 }] },
        ];
        const next = removePallet(start, 1);
        const buffer = next.find(p => p.pallet_no === 0);
        expect(buffer?.boxes[0]).toEqual({ barcode: 'bc1', box_count: 2, loose_units: 3 });
        expect(next.find(p => p.pallet_no === 1)).toBeUndefined();
    });
});
