/** Кросс-черновичный резерв и скоуп: чистые хелперы `draftReserve`. */
import { describe, it, expect } from 'vitest';
import {
    subtractReserveFromArticles, restrictArticlesToFf, reservedTotal, reservedAt,
    reservedForBarcode, splitRowsByScope, carveScopeFromDraft,
} from '@/lib/assembly/draftReserve';
import type { AssemblyDraftRow } from '@/types/api';

const article = (barcode: string, stocks: Record<number, number>) => ({
    barcode,
    rf_stocks: Object.fromEntries(Object.entries(stocks).map(([ff, v]) => [ff, { stock: v, available: v }])),
});

describe('draftReserve', () => {
    it('subtractReserveFromArticles: вычитает per (barcode, ff), не ниже нуля, чужих не трогает', () => {
        const arts = [article('a', { 1: 100, 2: 50 }), article('b', { 1: 30 })];
        const out = subtractReserveFromArticles(arts, { a: { '1': 40, '2': 999 } });
        expect(out[0].rf_stocks[1].available).toBe(60);
        expect(out[0].rf_stocks[2].available).toBe(0);
        expect(out[1]).toBe(arts[1]);                    // без резерва — тот же объект
        expect(arts[0].rf_stocks[1].available).toBe(100); // вход не мутирован
    });

    it('пустой/отсутствующий резерв — вход как есть', () => {
        const arts = [article('a', { 1: 10 })];
        expect(subtractReserveFromArticles(arts, {})).toBe(arts);
        expect(subtractReserveFromArticles(arts, null)).toBe(arts);
    });

    it('restrictArticlesToFf: available чужих ФФ зануляется, свой остаётся', () => {
        const out = restrictArticlesToFf([article('a', { 1: 100, 2: 50 })], 2);
        expect(out[0].rf_stocks[1].available).toBe(0);
        expect(out[0].rf_stocks[2].available).toBe(50);
        const noop = restrictArticlesToFf([article('a', { 1: 100 })], null);
        expect(noop[0].rf_stocks[1].available).toBe(100);
    });

    it('reservedTotal / reservedAt / reservedForBarcode', () => {
        const res = { a: { '1': 40, '2': 10 }, b: { '1': 5 } };
        expect(reservedTotal(res)).toBe(55);
        expect(reservedAt(res, 'a', 2)).toBe(10);
        expect(reservedAt(res, 'zzz', 1)).toBe(0);
        expect(reservedForBarcode(res, 'a')).toBe(50);
    });

    it('splitRowsByScope / carveScopeFromDraft: скоуп-строки переезжают, прочие байт-в-байт', () => {
        const row = (nm: number, qty: number): AssemblyDraftRow =>
            ({ nm_id: nm, barcode: `bc${nm}`, vendor_code: `v${nm}`, src: { '1': qty }, tgt: { 'Казань': qty } });
        const rows = [row(1, 10), row(2, 20)];
        const prebook = [row(1, 5), row(3, 7)];
        const inScope = (nm: number) => nm === 1;
        const split = splitRowsByScope(rows, inScope);
        expect(split.scoped.map(r => r.nm_id)).toEqual([1]);
        expect(split.rest[0]).toBe(rows[1]);
        const carve = carveScopeFromDraft({ rows, prebook }, inScope);
        expect(carve.movedUnits).toBe(15);
        expect(carve.movedRows.length).toBe(1);
        expect(carve.movedPrebook.length).toBe(1);
        expect(carve.restRows[0]).toBe(rows[1]);
        expect(carve.restPrebook[0]).toBe(prebook[1]);
    });

    it('carve с ffId: переезжают только порции скоуп-ФФ, чужие ФФ остаются в основном', () => {
        const inScope = () => true;
        // Строка категории с ДВУХ ФФ: 10 с натали (ff 1) + 20 с Газпрома (ff 5).
        const multi: AssemblyDraftRow = {
            nm_id: 1, barcode: 'bc1', vendor_code: 'v1',
            src: { '1': 10, '5': 20 }, tgt: { 'Казань': 10, 'Тула': 20 },
        };
        // Строка чисто-газпромская — не должна переехать вовсе.
        const pureForeign: AssemblyDraftRow = {
            nm_id: 2, barcode: 'bc2', vendor_code: 'v2', src: { '5': 7 }, tgt: { 'Тула': 7 },
        };
        const carve = carveScopeFromDraft({ rows: [multi, pureForeign], prebook: [] }, inScope, 1);
        expect(carve.movedRows.length).toBe(1);
        expect(carve.movedRows[0].src).toEqual({ '1': 10 });
        expect(carve.movedUnits).toBe(10);
        // Остаток: газпромская порция мульти-строки + чистая газпромская строка.
        const restUnits = carve.restRows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
        expect(restUnits).toBe(27);
        for (const r of carve.restRows) expect(Object.keys(r.src)).toEqual(['5']);
        // Баланс обеих половин.
        for (const r of [...carve.movedRows, ...carve.restRows]) {
            const sSum = Object.values(r.src).reduce((a, v) => a + (v || 0), 0);
            const tSum = Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0);
            expect(sSum).toBe(tSum);
        }
    });
});
