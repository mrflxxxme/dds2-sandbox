import { describe, it, expect } from 'vitest';
import {
    parseClipboardTable, applyGridAt, isRowEmpty, parseQuantity, validateRows,
    sanitizeFilename, uniqueFilenames, wrapText, fitTextBlocks, labelTextBlocks,
    isBarcodeValueValid, EMPTY_ROW, LabelRow, LabelSettings, TextMeasurer,
} from '@/lib/labels/generator';

// Стаб шрифта: ширина символа = 0.6 × размер
const fakeFont: TextMeasurer = {
    widthOfTextAtSize: (text: string, size: number) => text.length * size * 0.6,
};

const row = (over: Partial<LabelRow>): LabelRow => ({ ...EMPTY_ROW, ...over });

const settings = (over: Partial<LabelSettings> = {}): LabelSettings => ({
    barcodeFormat: 'CODE128', labelSize: '100x70', textAlign: 'center', sameSeller: false, sellerName: '', ...over,
});

describe('parseClipboardTable — вставка из Google Таблиц', () => {
    it('разбирает TSV с \\r\\n и хвостовым переводом строки', () => {
        const grid = parseClipboardTable('a\tb\r\nc\td\r\n');
        expect(grid).toEqual([['a', 'b'], ['c', 'd']]);
    });

    it('тримит ячейки', () => {
        expect(parseClipboardTable(' a \t b ')).toEqual([['a', 'b']]);
    });
});

describe('applyGridAt — заполнение таблицы', () => {
    it('вставка с (0,0) заполняет колонки по порядку и добавляет строки', () => {
        const grid = [
            ['2050263290036', '946288655', 'Серый', '210x60', 'Диван', 'ООО «ПлюсВайб»', 'divan_darkgrey', '2'],
            ['2050263290037', '946288656', 'Синий', '210x60', 'Диван', 'ООО «ПлюсВайб»', 'divan_blue', '1'],
        ];
        const rows = applyGridAt([{ ...EMPTY_ROW }], grid, 0, 0);
        expect(rows).toHaveLength(2);
        expect(rows[0].barcode).toBe('2050263290036');
        expect(rows[0].quantity).toBe('2');
        expect(rows[1].freeText).toBe('divan_blue');
    });

    it('вставка со смещением пишет с нужной колонки и не трогает остальные', () => {
        const base = [row({ barcode: 'KEEP' })];
        const rows = applyGridAt(base, [['Красный', '100x50']], 0, 2); // с колонки «Цвет»
        expect(rows[0].barcode).toBe('KEEP');
        expect(rows[0].color).toBe('Красный');
        expect(rows[0].size).toBe('100x50');
    });

    it('лишние колонки за пределами таблицы игнорируются', () => {
        const rows = applyGridAt([{ ...EMPTY_ROW }], [['a', 'b', 'c']], 0, 6); // freeText, quantity, за край
        expect(rows[0].freeText).toBe('a');
        expect(rows[0].quantity).toBe('b');
    });
});

describe('валидация', () => {
    it('isRowEmpty: строка без значений пуста', () => {
        expect(isRowEmpty({ ...EMPTY_ROW })).toBe(true);
        expect(isRowEmpty(row({ barcode: 'x' }))).toBe(false);
    });

    it('parseQuantity: дефолт 1, кламп 1..1000', () => {
        expect(parseQuantity('')).toBe(1);
        expect(parseQuantity('0')).toBe(1);
        expect(parseQuantity('5')).toBe(5);
        expect(parseQuantity('99999')).toBe(1000);
        expect(parseQuantity('abc')).toBe(1);
    });

    it('CODE-128 — только ASCII', () => {
        expect(isBarcodeValueValid('2050263290036')).toBe(true);
        expect(isBarcodeValueValid('ABC-123_x')).toBe(true);
        expect(isBarcodeValueValid('ШК123')).toBe(false);
        expect(isBarcodeValueValid('')).toBe(false);
    });

    it('validateRows: пустые строки игнорируются, без ШК/кириллица — ошибка с номером строки', () => {
        const errs = validateRows([
            row({ barcode: '123', productName: 'ok' }),
            { ...EMPTY_ROW },
            row({ productName: 'нет ШК' }),
            row({ barcode: 'ШК' }),
        ]);
        expect(errs).toHaveLength(2);
        expect(errs[0]).toContain('Строка 3');
        expect(errs[1]).toContain('Строка 4');
    });
});

describe('имена файлов', () => {
    it('sanitizeFilename чистит запрещённые символы и падает на fallback', () => {
        expect(sanitizeFilename('divan/dark*grey?', 'fb')).toBe('divandarkgrey');
        expect(sanitizeFilename('  ', 'fb')).toBe('fb');
        expect(sanitizeFilename('...', 'fb')).toBe('fb');
    });

    it('uniqueFilenames дедупит суффиксами _2, _3', () => {
        expect(uniqueFilenames(['a', 'b', 'a', 'a'])).toEqual(['a', 'b', 'a_2', 'a_3']);
    });
});

describe('перенос и подбор шрифта', () => {
    it('wrapText переносит по словам в пределах ширины', () => {
        // size 10 → символ 6pt; maxWidth 60 → 10 символов на строку
        const lines = wrapText('Диван бескаркасный большой', fakeFont, 10, 60);
        expect(lines.length).toBeGreaterThan(1);
        for (const l of lines) expect(fakeFont.widthOfTextAtSize(l, 10)).toBeLessThanOrEqual(60);
    });

    it('слово длиннее строки рубится посимвольно (не обрезается)', () => {
        const lines = wrapText('СуперДлинноеСлово', fakeFont, 10, 30); // 5 символов на строку
        expect(lines.join('')).toBe('СуперДлинноеСлово');
        for (const l of lines) expect(l.length).toBeLessThanOrEqual(5);
    });

    it('fitTextBlocks подбирает максимальный влезающий размер', () => {
        const blocks = ['Продавец', 'Название товара'];
        const roomy = fitTextBlocks(blocks, fakeFont, 300, 200);
        expect(roomy.size).toBe(12);
        const tight = fitTextBlocks(blocks, fakeFont, 60, 40);
        expect(tight.size).toBeLessThan(12);
        expect(tight.lines.join(' ')).toContain('товара'); // текст сохранён целиком
    });
});

describe('labelTextBlocks — состав наклейки', () => {
    it('порядок: продавец, название, артикул, цвет/размер, свободная надпись', () => {
        const blocks = labelTextBlocks(row({
            barcode: '2050263290036', article: '946288655', color: 'Серый', size: '210x60',
            productName: 'Диван бескаркасный', sellerName: 'ООО «ПлюсВайб»', freeText: 'divan_darkgrey',
        }), settings());
        expect(blocks).toEqual([
            'ООО «ПлюсВайб»',
            'Диван бескаркасный',
            'Артикул: 946288655',
            'Цв.: Серый / Раз.: 210x60',
            'divan_darkgrey',
        ]);
    });

    it('sameSeller подменяет продавца из настроек', () => {
        const blocks = labelTextBlocks(row({ barcode: '1', sellerName: 'Игнор' }), settings({ sameSeller: true, sellerName: 'ООО «Общий»' }));
        expect(blocks[0]).toBe('ООО «Общий»');
    });

    it('пустые поля выпадают, один из пары цвет/размер — без слэша', () => {
        const blocks = labelTextBlocks(row({ barcode: '1', color: 'Серый' }), settings());
        expect(blocks).toEqual(['Цв.: Серый']);
    });
});
