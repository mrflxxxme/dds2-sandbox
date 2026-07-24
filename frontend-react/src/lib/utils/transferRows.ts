/**
 * Утилиты строк перемещения (складской transfer).
 *
 * Список часто формируется склейкой нескольких источников (машины/черновики),
 * поэтому один и тот же штрихкод может встречаться в нескольких строках.
 * Списание остатка на бэкенде идёт построчно и накопительно, а значит дубли
 * ШК суммируются по факту — и суммарная потребность может превысить остаток,
 * хотя каждая отдельная строка ≤ остатка. Схлопываем дубли ДО отправки и ДО
 * валидации, чтобы проверка шла по суммарному количеству на штрихкод.
 */

export interface MergeableRow {
    barcode: string;
    quantity: string;
}

export interface MergedRow {
    barcode: string;
    quantity: number;
}

/**
 * Схлопывает строки по штрихкоду: одинаковые ШК суммируются в одну строку.
 * Пустые/невалидные/неположительные строки отбрасываются. Порядок результата —
 * по первому появлению штрихкода.
 */
export function mergeRowsByBarcode(rows: MergeableRow[]): MergedRow[] {
    const order: string[] = [];
    const sums = new Map<string, number>();
    for (const r of rows) {
        const barcode = r.barcode.trim();
        const qty = parseInt(r.quantity, 10);
        if (!barcode || !Number.isFinite(qty) || qty <= 0) continue;
        if (!sums.has(barcode)) order.push(barcode);
        sums.set(barcode, (sums.get(barcode) ?? 0) + qty);
    }
    return order.map(barcode => ({ barcode, quantity: sums.get(barcode)! }));
}
