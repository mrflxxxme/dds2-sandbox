/**
 * Парсер вставки из буфера обмена для страницы кратности коробок.
 *
 * Поддерживает два режима:
 *
 * 1. Позиционный (3 колонки, без заголовка):
 *      barcode<TAB>кратность<TAB>учитывать
 *
 * 2. Header-режим (расширенный, для шаблона выгрузки):
 *      Артикул<TAB>Barcode<TAB>nm_id<TAB>Склад<TAB>Кратность по ФФ<TAB>Размер коробки
 *    Если первая непустая строка содержит ключевые слова из заголовков —
 *    парсер переключается в header-mode, маппит колонки по именам.
 *    Допускает per-ФФ строки: каждая строка несёт `warehouse_name` →
 *    резолвится в `warehouse_id` через переданную карту.
 *
 * Колонки опциональные. Пустое значение — поле не трогается (partial update).
 * Допустимые «учитывать»: да/нет, yes/no, true/false, 1/0, +/-, on/off, вкл/выкл.
 */

export interface BoxMultiplicityPasteRow {
    barcode: string;
    warehouse_id?: number;             // если per-ФФ строка (резолв из имени склада)
    box_qty_override?: number | null;  // для SKU-level либо per-ФФ box_qty
    box_size?: string | null;          // per-ФФ размер коробки
    use_box_multiplicity?: boolean;
    /** Сырая строка для UI preview (то что пользователь вставил). */
    raw: string[];
}

export interface BoxMultiplicityPasteResult {
    rows: BoxMultiplicityPasteRow[];
    errors: Array<{ line: number; reason: string; raw: string }>;
}

/** Карта имени склада → ID. Нужна для резолва per-ФФ строк в header-режиме. */
export type WarehouseNameMap = Map<string, number>;

const HEADER_KEYWORDS = ['артикул', 'barcode', 'баркод', 'nm_id', 'склад', 'кратность', 'размер', 'учитывать'];

function _looksLikeHeader(cols: string[]): boolean {
    const lowered = cols.map(c => c.trim().toLowerCase());
    return lowered.some(c => HEADER_KEYWORDS.some(kw => c.includes(kw)));
}

interface HeaderMap {
    barcode: number;
    article?: number;       // read-only — для контекста
    nm_id?: number;         // read-only — для контекста
    warehouse?: number;
    box_qty?: number;
    box_size?: number;
    use?: number;
}

function _parseHeader(cols: string[]): HeaderMap | null {
    const map: Partial<HeaderMap> = {};
    cols.forEach((raw, idx) => {
        const c = raw.trim().toLowerCase();
        if (!c) return;
        if (c.includes('barcode') || c.includes('баркод')) map.barcode = idx;
        else if (c.includes('nm_id') || c === 'nm id' || c.includes('артикул wb')) map.nm_id = idx;
        else if (c.includes('артикул')) map.article = idx;
        else if (c.includes('склад') || c.includes('warehouse')) map.warehouse = idx;
        else if (c.includes('размер')) map.box_size = idx;
        else if (c.includes('кратность')) map.box_qty = idx;
        else if (c.includes('учитывать')) map.use = idx;
    });
    if (map.barcode === undefined) return null;
    return map as HeaderMap;
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

// Валидирует ppb-токен: возвращает {value, error}. `value === undefined` = поле не трогать,
// `value === null` = очистка, число = новое значение.
function _parsePpbToken(raw: string): { value?: number | null; error?: string } {
    if (raw === '') return {};
    if (raw === '-' || raw.toLowerCase() === 'null' || raw === '0') return { value: null };
    const n = parseInt10(raw);
    if (n === null || n < 1 || n > 10000) {
        return { error: `некорректная кратность: «${raw}» (нужно число 1–10000, либо «-»/«null»/пусто для очистки)` };
    }
    return { value: n };
}

export function parseBoxMultiplicityPaste(
    text: string,
    warehouseMap?: WarehouseNameMap,
): BoxMultiplicityPasteResult {
    const result: BoxMultiplicityPasteResult = { rows: [], errors: [] };
    if (!text) return result;

    // Не trim leading/trailing — иначе пустая первая колонка (\t...) теряется.
    const lines = text.split(/\r?\n/);

    // ─── Header-detect: первая непустая строка ───────────────────────────
    let headerMap: HeaderMap | null = null;
    let startLine = 0;
    for (let i = 0; i < lines.length; i++) {
        if (!lines[i]) continue;
        const cols = lines[i].split('\t').map(c => c.trim());
        if (_looksLikeHeader(cols)) {
            headerMap = _parseHeader(cols);
            startLine = i + 1;
            if (!headerMap) {
                result.errors.push({
                    line: i + 1,
                    reason: 'header не содержит обязательной колонки «Barcode»',
                    raw: lines[i],
                });
                return result;
            }
        }
        break;
    }

    for (let i = startLine; i < lines.length; i++) {
        const line = lines[i];
        const lineNo = i + 1;
        if (!line) continue;
        const cols = line.split('\t').map(c => c.trim());

        // Резолв позиций колонок в зависимости от режима.
        const barcodeIdx = headerMap ? headerMap.barcode : 0;
        const barcode = cols[barcodeIdx] || '';
        if (!barcode) {
            result.errors.push({ line: lineNo, reason: 'пустой barcode', raw: line });
            continue;
        }

        const row: BoxMultiplicityPasteRow = { barcode, raw: cols };

        if (headerMap) {
            // ─── Header-режим: per-ФФ при наличии warehouse, иначе SKU-level
            if (headerMap.warehouse !== undefined) {
                const whName = (cols[headerMap.warehouse] || '').trim();
                if (whName) {
                    const whId = warehouseMap?.get(whName) ?? warehouseMap?.get(whName.toLowerCase());
                    if (whId === undefined) {
                        result.errors.push({
                            line: lineNo,
                            reason: `ФФ-склад не найден: «${whName}»`,
                            raw: line,
                        });
                        continue;
                    }
                    row.warehouse_id = whId;
                }
            }
            if (headerMap.box_qty !== undefined) {
                const { value, error } = _parsePpbToken(cols[headerMap.box_qty] || '');
                if (error) {
                    result.errors.push({ line: lineNo, reason: error, raw: line });
                    continue;
                }
                if (value !== undefined) row.box_qty_override = value;
            }
            if (headerMap.box_size !== undefined) {
                const sizeRaw = (cols[headerMap.box_size] || '').trim();
                if (sizeRaw !== '') {
                    row.box_size = (sizeRaw === '-' || sizeRaw.toLowerCase() === 'null') ? null : sizeRaw;
                }
            }
            if (headerMap.use !== undefined) {
                const useRaw = (cols[headerMap.use] || '').trim();
                if (useRaw !== '') {
                    const b = parseBool(useRaw);
                    if (b === null) {
                        result.errors.push({
                            line: lineNo,
                            reason: `некорректное «учитывать»: «${useRaw}»`,
                            raw: line,
                        });
                        continue;
                    }
                    row.use_box_multiplicity = b;
                }
            }
        } else {
            // ─── Позиционный режим (legacy): col 1 = кратность, col 2 = учитывать
            const { value, error } = _parsePpbToken(cols[1] || '');
            if (error) {
                result.errors.push({ line: lineNo, reason: error, raw: line });
                continue;
            }
            if (value !== undefined) row.box_qty_override = value;

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
        }

        // Если ни одно мутирующее поле не передано — строка бесполезна.
        const hasUpdate = row.box_qty_override !== undefined
            || row.box_size !== undefined
            || row.use_box_multiplicity !== undefined;
        if (!hasUpdate) {
            result.errors.push({
                line: lineNo,
                reason: 'нет полей для обновления (укажи кратность, размер или «учитывать»)',
                raw: line,
            });
            continue;
        }

        result.rows.push(row);
    }

    return result;
}
