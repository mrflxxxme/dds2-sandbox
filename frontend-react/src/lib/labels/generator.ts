/**
 * Генератор наклеек со штрихкодами (WB/Ozon).
 *
 * Чистая логика (парсинг вставки, перенос/подбор шрифта, сборка PDF/ZIP) —
 * отдельно от браузерного рендера штрихкода (canvas), чтобы тестировалось в vitest.
 * PDF — вектор (pdf-lib), размер страницы = физический размер наклейки, поэтому
 * «300 DPI» обеспечивается печатью; штрихкод растрируется с запасом (~1200 px).
 */
import { PDFDocument, PDFFont, rgb } from 'pdf-lib';
import fontkit from '@pdf-lib/fontkit';
import JSZip from 'jszip';

// ─── Типы ────────────────────────────────────────────────────────────────────

export interface LabelRow {
    barcode: string;
    article: string;
    color: string;
    size: string;
    productName: string;
    sellerName: string;
    freeText: string;
    quantity: string; // строка из инпута; парсится при генерации
}

export type LabelSizeKey = '100x70' | '120x80' | '150x100' | '210x148';
export type LabelTextAlign = 'center' | 'left';

export interface LabelSettings {
    barcodeFormat: 'CODE128';
    labelSize: LabelSizeKey;
    textAlign: LabelTextAlign;
    sameSeller: boolean;
    sellerName: string; // используется при sameSeller
}

export const LABEL_SIZES: Record<LabelSizeKey, { w: number; h: number }> = {
    '100x70': { w: 100, h: 70 },
    '120x80': { w: 120, h: 80 },
    '150x100': { w: 150, h: 100 },
    '210x148': { w: 210, h: 148 },
};

export const EMPTY_ROW: LabelRow = {
    barcode: '', article: '', color: '', size: '', productName: '', sellerName: '', freeText: '', quantity: '',
};

/** Порядок колонок таблицы = порядок колонок при вставке из Google Таблиц. */
export const COLUMN_KEYS: (keyof LabelRow)[] = [
    'barcode', 'article', 'color', 'size', 'productName', 'sellerName', 'freeText', 'quantity',
];

const MM_TO_PT = 72 / 25.4;

// ─── Вставка из Google Таблиц ────────────────────────────────────────────────

/** TSV из буфера → грид строк/ячеек. Отбрасывает \r и хвостовую пустую строку. */
export function parseClipboardTable(text: string): string[][] {
    const lines = text.replace(/\r\n?/g, '\n').split('\n');
    while (lines.length > 0 && lines[lines.length - 1] === '') lines.pop();
    return lines.map(l => l.split('\t').map(c => c.trim()));
}

/** Вставить грид в таблицу начиная с (startRow, startCol), добавляя строки при нехватке. */
export function applyGridAt(rows: LabelRow[], grid: string[][], startRow: number, startCol: number): LabelRow[] {
    const result = rows.map(r => ({ ...r }));
    for (let i = 0; i < grid.length; i++) {
        const target = startRow + i;
        while (result.length <= target) result.push({ ...EMPTY_ROW });
        for (let j = 0; j < grid[i].length; j++) {
            const key = COLUMN_KEYS[startCol + j];
            if (key) result[target][key] = grid[i][j];
        }
    }
    return result;
}

// ─── Валидация ───────────────────────────────────────────────────────────────

export function isRowEmpty(row: LabelRow): boolean {
    return COLUMN_KEYS.every(k => !row[k].trim());
}

export function parseQuantity(q: string): number {
    const n = parseInt(q, 10);
    if (!Number.isFinite(n) || n < 1) return 1;
    return Math.min(n, 1000);
}

/** CODE-128 кодирует только ASCII (0–127). */
export function isBarcodeValueValid(value: string): boolean {
    return /^[\x20-\x7E]+$/.test(value);
}

/** Ошибки по непустым строкам; пустые строки игнорируются. */
export function validateRows(rows: LabelRow[]): string[] {
    const errors: string[] = [];
    rows.forEach((row, i) => {
        if (isRowEmpty(row)) return;
        const n = i + 1;
        if (!row.barcode.trim()) errors.push(`Строка ${n}: не заполнен штрихкод`);
        else if (!isBarcodeValueValid(row.barcode.trim())) errors.push(`Строка ${n}: штрихкод содержит недопустимые символы (CODE-128 — только латиница/цифры/знаки)`);
    });
    return errors;
}

// ─── Имена файлов ────────────────────────────────────────────────────────────

export function sanitizeFilename(name: string, fallback: string): string {
    const cleaned = name
        .replace(/[\\/:*?"<>|\x00-\x1f]/g, '')
        .replace(/\s+/g, ' ')
        .trim()
        .replace(/^\.+|\.+$/g, '')
        .slice(0, 100)
        .trim();
    return cleaned || fallback;
}

/** Дедуп имён: «имя», «имя_2», «имя_3»… */
export function uniqueFilenames(names: string[]): string[] {
    const seen = new Map<string, number>();
    return names.map(name => {
        const count = (seen.get(name) ?? 0) + 1;
        seen.set(name, count);
        return count === 1 ? name : `${name}_${count}`;
    });
}

// ─── Перенос и подбор шрифта ─────────────────────────────────────────────────

/** Минимальный интерфейс шрифта для измерения (в тестах — стаб). */
export interface TextMeasurer {
    widthOfTextAtSize(text: string, size: number): number;
}

/** Перенос по словам; слово длиннее строки рубится посимвольно (текст не обрезается). */
export function wrapText(text: string, font: TextMeasurer, size: number, maxWidth: number): string[] {
    const words = text.split(/\s+/).filter(Boolean);
    if (words.length === 0) return [];
    const lines: string[] = [];
    let current = '';
    const pushWord = (word: string) => {
        const candidate = current ? `${current} ${word}` : word;
        if (font.widthOfTextAtSize(candidate, size) <= maxWidth) { current = candidate; return; }
        if (current) { lines.push(current); current = ''; }
        // слово само по себе шире строки — рубим посимвольно
        let chunk = '';
        for (const ch of word) {
            if (font.widthOfTextAtSize(chunk + ch, size) > maxWidth && chunk) {
                lines.push(chunk);
                chunk = ch;
            } else chunk += ch;
        }
        current = chunk;
    };
    words.forEach(pushWord);
    if (current) lines.push(current);
    return lines;
}

const LINE_HEIGHT = 1.28;
const FIT_SIZES = [12, 11, 10, 9, 8, 7, 6, 5, 4];

/**
 * Подбор единого размера шрифта: наибольший из FIT_SIZES, при котором все блоки
 * (с переносами) влезают в maxHeight. Если не влез даже минимальный — вернёт его
 * (переносы гарантируют, что текст не обрезан по ширине).
 */
export function fitTextBlocks(
    blocks: string[], font: TextMeasurer, maxWidth: number, maxHeight: number,
): { size: number; lines: string[] } {
    let last: { size: number; lines: string[] } = { size: FIT_SIZES[FIT_SIZES.length - 1], lines: [] };
    for (const size of FIT_SIZES) {
        const lines = blocks.flatMap(b => wrapText(b, font, size, maxWidth));
        last = { size, lines };
        if (lines.length * size * LINE_HEIGHT <= maxHeight) return last;
    }
    // минимальный размер: пересчитать переносы под него (уже сделано в последней итерации)
    return last;
}

// ─── Сборка PDF ──────────────────────────────────────────────────────────────

export interface LabelFonts { regular: ArrayBuffer; bold: ArrayBuffer }

/** Текстовые блоки наклейки в порядке макета (пустые пропускаются). */
export function labelTextBlocks(row: LabelRow, settings: LabelSettings): string[] {
    const seller = settings.sameSeller ? settings.sellerName : row.sellerName;
    const colorSize = [
        row.color.trim() && `Цв.: ${row.color.trim()}`,
        row.size.trim() && `Раз.: ${row.size.trim()}`,
    ].filter(Boolean).join(' / ');
    return [
        seller.trim(),
        row.productName.trim(),
        row.article.trim() && `Артикул: ${row.article.trim()}`,
        colorSize,
        row.freeText.trim(),
    ].filter((b): b is string => Boolean(b));
}

/**
 * PDF одной наклейки: страница = размер наклейки, штрихкод сверху, под ним цифры,
 * ниже текстовые блоки. quantity → столько же одинаковых страниц.
 */
export async function buildLabelPdf(
    row: LabelRow, settings: LabelSettings, fonts: LabelFonts, barcodePng: Uint8Array,
): Promise<Uint8Array> {
    const { w, h } = LABEL_SIZES[settings.labelSize];
    const pageW = w * MM_TO_PT;
    const pageH = h * MM_TO_PT;
    const margin = 3 * MM_TO_PT;

    const doc = await PDFDocument.create();
    doc.registerFontkit(fontkit);
    const font = await doc.embedFont(fonts.regular, { subset: true });
    const fontBold = await doc.embedFont(fonts.bold, { subset: true });
    const png = await doc.embedPng(barcodePng);

    const contentW = pageW - margin * 2;
    const barcodeH = pageH * 0.3;
    const digitsSize = Math.min(11, Math.max(7, pageH / 22));
    const gap = 1.5 * MM_TO_PT;

    const blocks = labelTextBlocks(row, settings);
    const textTop = pageH - margin - barcodeH - gap - digitsSize * LINE_HEIGHT - gap;
    const { size, lines } = fitTextBlocks(blocks, font as PDFFont, contentW, textTop - margin);

    const copies = parseQuantity(row.quantity);
    for (let c = 0; c < copies; c++) {
        const page = doc.addPage([pageW, pageH]);
        // Штрихкод — по центру, на всю доступную ширину
        page.drawImage(png, { x: margin, y: pageH - margin - barcodeH, width: contentW, height: barcodeH });
        // Цифры штрихкода — всегда по центру под штрихкодом (жирным)
        const digits = row.barcode.trim();
        const dw = fontBold.widthOfTextAtSize(digits, digitsSize);
        page.drawText(digits, {
            x: margin + (contentW - dw) / 2,
            y: pageH - margin - barcodeH - gap - digitsSize,
            size: digitsSize, font: fontBold, color: rgb(0, 0, 0),
        });
        // Текстовые блоки
        let y = textTop - size;
        for (const line of lines) {
            const lw = font.widthOfTextAtSize(line, size);
            const x = settings.textAlign === 'center' ? margin + Math.max(0, (contentW - lw) / 2) : margin;
            page.drawText(line, { x, y, size, font, color: rgb(0, 0, 0) });
            y -= size * LINE_HEIGHT;
        }
    }
    return doc.save();
}

// ─── Штрихкод (браузер, canvas) ──────────────────────────────────────────────

/** PNG CODE-128 в высоком разрешении (вызывать только в браузере). */
export async function renderBarcodePng(value: string): Promise<Uint8Array> {
    const { default: JsBarcode } = await import('jsbarcode');
    const canvas = document.createElement('canvas');
    JsBarcode(canvas, value, {
        format: 'CODE128',
        displayValue: false,
        margin: 0,
        width: 6,       // px на модуль — с запасом под 300 DPI
        height: 400,
        background: '#ffffff',
        lineColor: '#000000',
    });
    const dataUrl = canvas.toDataURL('image/png');
    const base64 = dataUrl.split(',')[1];
    const bin = atob(base64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
}

// ─── ZIP ─────────────────────────────────────────────────────────────────────

export interface GenerateProgress { done: number; total: number }

/**
 * ZIP: отдельный PDF на каждую непустую строку, имя файла — из «Свободной надписи»
 * (fallback: артикул → штрихкод), дубликаты имён получают суффикс _2, _3…
 */
export async function generateLabelsZip(
    rows: LabelRow[],
    settings: LabelSettings,
    fonts: LabelFonts,
    makeBarcode: (value: string) => Promise<Uint8Array>,
    onProgress?: (p: GenerateProgress) => void,
): Promise<Blob> {
    const active = rows.filter(r => !isRowEmpty(r));
    const names = uniqueFilenames(active.map(r =>
        sanitizeFilename(r.freeText, sanitizeFilename(r.article, r.barcode.trim() || 'label')),
    ));
    const zip = new JSZip();
    const barcodeCache = new Map<string, Uint8Array>();
    for (let i = 0; i < active.length; i++) {
        const row = active[i];
        const value = row.barcode.trim();
        let png = barcodeCache.get(value);
        if (!png) {
            png = await makeBarcode(value);
            barcodeCache.set(value, png);
        }
        const pdf = await buildLabelPdf(row, settings, fonts, png);
        zip.file(`${names[i]}.pdf`, pdf);
        onProgress?.({ done: i + 1, total: active.length });
    }
    return zip.generateAsync({ type: 'blob' });
}
