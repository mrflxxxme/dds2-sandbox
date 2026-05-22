import { describe, it, expect } from 'vitest';
import { parseBoxMultiplicityPaste } from '@/lib/utils/boxMultiplicityPaste';

describe('parseBoxMultiplicityPaste', () => {
    it('returns empty for empty input', () => {
        expect(parseBoxMultiplicityPaste('')).toEqual({ rows: [], errors: [] });
    });

    it('parses single barcode + ppb + use=yes', () => {
        const r = parseBoxMultiplicityPaste('1234567890\t10\tда');
        expect(r.rows).toEqual([
            {
                barcode: '1234567890',
                box_qty_override: 10,
                use_box_multiplicity: true,
                raw: ['1234567890', '10', 'да'],
            },
        ]);
        expect(r.errors).toEqual([]);
    });

    it('parses multiple lines (Excel paste)', () => {
        const text = '111\t5\tда\n222\t10\tнет\n333\t15\t';
        const r = parseBoxMultiplicityPaste(text);
        expect(r.rows.length).toBe(3);
        expect(r.rows[0].barcode).toBe('111');
        expect(r.rows[0].box_qty_override).toBe(5);
        expect(r.rows[0].use_box_multiplicity).toBe(true);
        expect(r.rows[1].use_box_multiplicity).toBe(false);
        expect(r.rows[2].use_box_multiplicity).toBeUndefined();  // не передано
    });

    it('omitted ppb means do not touch (only use_flag set)', () => {
        const r = parseBoxMultiplicityPaste('111\t\tда');
        expect(r.rows[0].barcode).toBe('111');
        expect(r.rows[0].box_qty_override).toBeUndefined();  // omitted
        expect(r.rows[0].use_box_multiplicity).toBe(true);
        expect(r.errors).toEqual([]);
    });

    it('ppb = "-" or "null" or "0" clears override (sets to null)', () => {
        const r = parseBoxMultiplicityPaste('111\t-\n222\tnull\n333\t0');
        expect(r.rows.length).toBe(3);
        for (const row of r.rows) {
            expect(row.box_qty_override).toBeNull();
        }
    });

    it('accepts varied bool tokens', () => {
        const lines = [
            ['111', '5', 'да'],
            ['112', '5', 'yes'],
            ['113', '5', 'true'],
            ['114', '5', '1'],
            ['115', '5', '+'],
            ['116', '5', 'on'],
            ['117', '5', 'вкл'],
            ['121', '5', 'нет'],
            ['122', '5', 'no'],
            ['123', '5', 'false'],
            ['124', '5', '0'],
            ['125', '5', '-'],
            ['126', '5', 'off'],
            ['127', '5', 'выкл'],
        ];
        const text = lines.map(l => l.join('\t')).join('\n');
        const r = parseBoxMultiplicityPaste(text);
        expect(r.errors).toEqual([]);
        const trues = r.rows.filter(x => x.use_box_multiplicity === true).length;
        const falses = r.rows.filter(x => x.use_box_multiplicity === false).length;
        expect(trues).toBe(7);
        expect(falses).toBe(7);
    });

    it('rejects invalid ppb', () => {
        const r = parseBoxMultiplicityPaste('111\tabc\tда');
        expect(r.rows).toEqual([]);
        expect(r.errors[0].reason).toMatch(/некорректная кратность/);
    });

    it('rejects out-of-range ppb', () => {
        const r = parseBoxMultiplicityPaste('111\t99999\tда');
        expect(r.rows).toEqual([]);
        expect(r.errors[0].reason).toMatch(/некорректная кратность/);
    });

    it('rejects invalid bool', () => {
        const r = parseBoxMultiplicityPaste('111\t10\tмаябейб');
        expect(r.rows).toEqual([]);
        expect(r.errors[0].reason).toMatch(/некорректное/);
    });

    it('rejects empty barcode', () => {
        const r = parseBoxMultiplicityPaste('\t10\tда');
        expect(r.rows).toEqual([]);
        expect(r.errors[0].reason).toMatch(/пустой barcode/);
    });

    it('rejects rows with no fields to update', () => {
        const r = parseBoxMultiplicityPaste('111\t\t');
        expect(r.rows).toEqual([]);
        expect(r.errors[0].reason).toMatch(/нет полей/);
    });

    it('skips empty lines', () => {
        const r = parseBoxMultiplicityPaste('111\t10\tда\n\n\n222\t20\tнет\n');
        expect(r.rows.length).toBe(2);
        expect(r.errors).toEqual([]);
    });

    it('handles thousand-separators in ppb (1 000)', () => {
        const r = parseBoxMultiplicityPaste('111\t1 000\tда');
        expect(r.rows[0].box_qty_override).toBe(1000);
    });

    it('preserves line numbers in errors', () => {
        const r = parseBoxMultiplicityPaste('111\t10\tда\n222\tabc\tда\n333\t20\tнет');
        expect(r.errors).toEqual([
            { line: 2, reason: expect.stringMatching(/некорректная кратность/) as unknown as string, raw: '222\tabc\tда' },
        ]);
        expect(r.rows.length).toBe(2);
    });
});

describe('parseBoxMultiplicityPaste — header mode (расширенный шаблон выгрузки)', () => {
    const whMap = new Map<string, number>([
        ['ФФ-Тула', 10], ['фф-тула', 10],
        ['ФФ-Казань', 20], ['фф-казань', 20],
    ]);

    it('распознаёт header + per-ФФ строки с box_size', () => {
        const text = [
            'Артикул\tBarcode\tnm_id\tСклад\tКратность по ФФ\tРазмер коробки',
            'YY-1\t111\t1001\tФФ-Тула\t12\t30x40x20',
            'YY-2\t222\t1002\tФФ-Казань\t8\t',
        ].join('\n');
        const r = parseBoxMultiplicityPaste(text, whMap);
        expect(r.errors).toEqual([]);
        expect(r.rows.length).toBe(2);
        expect(r.rows[0]).toMatchObject({
            barcode: '111', warehouse_id: 10, box_qty_override: 12, box_size: '30x40x20',
        });
        expect(r.rows[1]).toMatchObject({
            barcode: '222', warehouse_id: 20, box_qty_override: 8,
        });
        expect(r.rows[1].box_size).toBeUndefined();  // пустой → не трогать
    });

    it('пустой склад в header-mode = SKU-level update (без warehouse_id)', () => {
        const text = 'Barcode\tСклад\tКратность по ФФ\n111\t\t50';
        const r = parseBoxMultiplicityPaste(text, whMap);
        expect(r.rows[0]).toMatchObject({ barcode: '111', box_qty_override: 50 });
        expect(r.rows[0].warehouse_id).toBeUndefined();
    });

    it('неизвестное имя склада → ошибка', () => {
        const text = 'Barcode\tСклад\tКратность по ФФ\n111\tФФ-Несуществующий\t10';
        const r = parseBoxMultiplicityPaste(text, whMap);
        expect(r.rows.length).toBe(0);
        expect(r.errors[0].reason).toMatch(/ФФ-склад не найден/);
    });

    it('clear box_size через «-»', () => {
        const text = 'Barcode\tСклад\tКратность по ФФ\tРазмер коробки\n111\tФФ-Тула\t10\t-';
        const r = parseBoxMultiplicityPaste(text, whMap);
        expect(r.rows[0].box_size).toBeNull();
    });

    it('header без обязательной Barcode-колонки → одна ошибка', () => {
        const text = 'Артикул\tСклад\tКратность по ФФ\nYY-1\tФФ-Тула\t10';
        const r = parseBoxMultiplicityPaste(text, whMap);
        expect(r.rows.length).toBe(0);
        expect(r.errors[0].reason).toMatch(/Barcode/);
    });

    it('case-insensitive резолв имени склада', () => {
        const text = 'Barcode\tСклад\tКратность по ФФ\n111\tфф-Тула\t10';
        const r = parseBoxMultiplicityPaste(text, whMap);
        expect(r.rows[0]?.warehouse_id).toBe(10);
    });
});
