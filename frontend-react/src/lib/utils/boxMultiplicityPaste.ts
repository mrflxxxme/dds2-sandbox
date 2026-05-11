/**
 * Парсер вставки из буфера обмена для страницы кратности коробок.
 *
 * Ожидаемый формат (TAB-separated, как Excel):
 *   barcode<TAB>кратность<TAB>учитывать
 *
 * Колонки 2 и 3 — опциональные. Если значение пустое — поле не трогается
 * (partial update). Допустимые «учитывать»: да/нет, yes/no, true/false,
 * 1/0, +/-, on/off, вкл/выкл.
 *
 * Возвращает структурированный список + список ошибок построчно.
 */

export interface BoxMultiplicityPasteRow {
    barcode: string;
    box_qty_override?: number | null;  // если поле передано
    use_box_multiplicity?: boolean;
    /** Сырая строка для UI preview (то что пользователь вставил). */
    raw: string[];
}

export interface BoxMultiplicityPasteResult {
    rows: BoxMultiplicityPasteRow[];
    errors: Array<{ line: number; reason: string; raw: string }>;
}

const BOOL_TRUE = new Set(['да', 'yes', 'true', '1', '+', 'on', 'вкл']);
const BOOL_FALSE = new Set(['нет', 'no', 'false', '0', '-', 'off', 'выкл']);

const parseInt10 = (s: string): number | null => {
    const cleaned = s.replace(/\s/g, '').replace(',', '.');
    if (!cleaned) return null;
    const n = Number.parseInt(cleaned, 10);
    return Number.isFinite(n) ? n : null;
};

const parseBool = (s: string): boolean | null => {
    const v = s.trim().toLowerCase();
    if (v === '') return null;
    if (BOOL_TRUE.has(v)) return true;
    if (BOOL_FALSE.has(v)) return false;
    return null;
};

export function parseBoxMultiplicityPaste(text: string): BoxMultiplicityPasteResult {
    const result: BoxMultiplicityPasteResult = { rows: [], errors: [] };
    if (!text) return result;

    // Не trim leading/trailing — иначе пустая первая колонка (\t...) теряется.
    const lines = text.split(/\r?\n/);

    let lineNo = 0;
    for (const line of lines) {
        lineNo++;
        if (!line) continue;
        const cols = line.split('\t').map(c => c.trim());
        const barcode = cols[0] || '';
        if (!barcode) {
            result.errors.push({ line: lineNo, reason: 'пустой barcode', raw: line });
            continue;
        }

        const row: BoxMultiplicityPasteRow = { barcode, raw: cols };

        // ─── Колонка 2: кратность (опционально) ──────────────────────────
        const ppbRaw = cols[1] || '';
        if (ppbRaw === '') {
            // не передано — поле не трогаем
        } else if (ppbRaw === '-' || ppbRaw.toLowerCase() === 'null' || ppbRaw === '0') {
            // явная очистка ручного override
            row.box_qty_override = null;
        } else {
            const n = parseInt10(ppbRaw);
            if (n === null || n < 1 || n > 10000) {
                result.errors.push({
                    line: lineNo,
                    reason: `некорректная кратность: «${ppbRaw}» (нужно число 1–10000, либо «-»/«null»/пусто для очистки)`,
                    raw: line,
                });
                continue;
            }
            row.box_qty_override = n;
        }

        // ─── Колонка 3: учитывать (опционально) ──────────────────────────
        const useRaw = cols[2] || '';
        if (useRaw !== '') {
            const b = parseBool(useRaw);
            if (b === null) {
                result.errors.push({
                    line: lineNo,
                    reason: `некорректное «учитывать»: «${useRaw}» (нужно да/нет, 1/0, +/-, true/false)`,
                    raw: line,
                });
                continue;
            }
            row.use_box_multiplicity = b;
        }

        // Если оба поля не переданы — строка бесполезна (нечего обновлять).
        if (row.box_qty_override === undefined && row.use_box_multiplicity === undefined) {
            result.errors.push({
                line: lineNo,
                reason: 'нет полей для обновления (укажи кратность или «учитывать»)',
                raw: line,
            });
            continue;
        }

        result.rows.push(row);
    }

    return result;
}
