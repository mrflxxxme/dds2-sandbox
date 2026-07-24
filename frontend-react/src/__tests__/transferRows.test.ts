import { describe, expect, it } from 'vitest';
import { mergeRowsByBarcode } from '@/lib/utils/transferRows';

describe('mergeRowsByBarcode', () => {
    it('суммирует дубликаты одного штрихкода в одну строку', () => {
        const merged = mergeRowsByBarcode([
            { barcode: '2043160691778', quantity: '182' },
            { barcode: '2043160691778', quantity: '52' },
            { barcode: '2043160691778', quantity: '52' },
        ]);
        expect(merged).toEqual([{ barcode: '2043160691778', quantity: 286 }]);
    });

    it('сохраняет порядок по первому появлению штрихкода', () => {
        const merged = mergeRowsByBarcode([
            { barcode: 'B', quantity: '1' },
            { barcode: 'A', quantity: '2' },
            { barcode: 'B', quantity: '3' },
        ]);
        expect(merged).toEqual([
            { barcode: 'B', quantity: 4 },
            { barcode: 'A', quantity: 2 },
        ]);
    });

    it('отбрасывает пустые, неположительные и невалидные строки', () => {
        const merged = mergeRowsByBarcode([
            { barcode: '', quantity: '5' },
            { barcode: 'A', quantity: '' },
            { barcode: 'A', quantity: '0' },
            { barcode: 'A', quantity: '-3' },
            { barcode: 'A', quantity: 'abc' },
            { barcode: 'A', quantity: '7' },
        ]);
        expect(merged).toEqual([{ barcode: 'A', quantity: 7 }]);
    });

    it('схлопывает реальный список из 27 строк в уникальные ШК с верными суммами', () => {
        const raw: [string, string][] = [
            ['2049483805892', '46'], ['2049483854449', '19'], ['2049483941149', '23'],
            ['2049484069743', '23'], ['2049484084814', '37'], ['2049483805892', '46'],
            ['2049483941149', '23'], ['2049484069743', '23'], ['2049484084814', '46'],
            ['2049483805892', '46'], ['2049484069743', '23'], ['2049484084814', '23'],
            ['2049484115402', '8'], ['2043465571911', '24'], ['2043465657738', '12'],
            ['2043160691778', '182'], ['2044294122275', '20'], ['2048219456759', '28'],
            ['2043465571911', '36'], ['2043465657738', '12'], ['2045409330257', '40'],
            ['2043160691778', '52'], ['2044294122275', '20'], ['2043465571911', '12'],
            ['2043465657738', '12'], ['2045409330257', '40'], ['2044294122275', '20'],
            ['2043160691778', '52'],
        ];
        const merged = mergeRowsByBarcode(raw.map(([barcode, quantity]) => ({ barcode, quantity })));
        const byBarcode = Object.fromEntries(merged.map(m => [m.barcode, m.quantity]));
        expect(merged).toHaveLength(12);
        expect(byBarcode['2043160691778']).toBe(286);
        expect(byBarcode['2049483805892']).toBe(138);
        expect(byBarcode['2049484069743']).toBe(69);
        expect(byBarcode['2044294122275']).toBe(60);
        expect(byBarcode['2043465571911']).toBe(72);
    });
});
