/**
 * Чистая логика раскладки коробов по паллетам (без React/I/O) — юнит-тестируется.
 *
 * Манифест = массив паллет `PalletBox[]`; паллета `{pallet_no, boxes}`, короб
 * `{barcode, box_count, loose_units}`. Буфер «Не распределено» = паллета с
 * `pallet_no === 0`. Разложенные штуки на ШК = `Σ(box_count × ppb + loose_units)`.
 *
 * Инвариант сохранения (строгий, зеркалит бэкенд): по каждому ШК разложено ровно
 * `quantity` позиции заявки. ШК без кратности (ppb=null) кладётся только
 * россыпью (loose_units) — короб без кратности даёт 0 штук.
 */
import type { AssemblyRequestItem, BoxContent, PalletBox } from '@/types/api';

export type PpbOf = (barcode: string) => number | null;

/** Буфер «Не распределено». */
export const BUFFER_PALLET_NO = 0;

/** Σ штук по позициям заявки на каждый ШК. */
export function itemQtyMap(items: AssemblyRequestItem[]): Map<string, number> {
    const m = new Map<string, number>();
    for (const it of items) m.set(it.barcode, (m.get(it.barcode) ?? 0) + it.quantity);
    return m;
}

/** ppb → положительное целое либо 0 (нет кратности). */
function ppbInt(ppbOf: PpbOf, barcode: string): number {
    const p = ppbOf(barcode);
    return p && p > 0 ? Math.floor(p) : 0;
}

/**
 * Авто-раскладка: каждый ШК → целые короба (`floor(q/ppb)`) + хвост-россыпь
 * (`q%ppb`; при отсутствии кратности всё в россыпь). SKU раскидываются по
 * паллетам round-robin (каждый артикул целиком на своей паллете — mono-подобно).
 */
export function autoManifest(
    items: AssemblyRequestItem[],
    ppbOf: PpbOf,
    palletsCount: number,
): PalletBox[] {
    const qty = itemQtyMap(items);
    const barcodes = [...qty.keys()].sort();
    const count = Math.max(1, Math.floor(palletsCount || 1));
    const pallets: PalletBox[] = Array.from({ length: count }, (_, i) => ({
        pallet_no: i + 1,
        boxes: [],
    }));
    barcodes.forEach((bc, idx) => {
        const q = qty.get(bc) ?? 0;
        const ppb = ppbInt(ppbOf, bc);
        const box: BoxContent = ppb > 0
            ? { barcode: bc, box_count: Math.floor(q / ppb), loose_units: q % ppb }
            : { barcode: bc, box_count: 0, loose_units: q };
        pallets[idx % count].boxes.push(box);
    });
    return pallets;
}

/** Разложенные штуки на каждый ШК по всем паллетам (короба×ppb + россыпь). */
export function placedUnits(pallets: PalletBox[], ppbOf: PpbOf): Map<string, number> {
    const m = new Map<string, number>();
    for (const p of pallets) {
        for (const b of p.boxes) {
            const ppb = ppbInt(ppbOf, b.barcode);
            const units = b.box_count * ppb + b.loose_units;
            m.set(b.barcode, (m.get(b.barcode) ?? 0) + units);
        }
    }
    return m;
}

export interface InvariantResult {
    ok: boolean;
    mismatches: { barcode: string; placed: number; need: number }[];
}

/** Проверка строгого инварианта: placed == quantity по каждому ШК заявки. */
export function checkInvariant(
    pallets: PalletBox[],
    items: AssemblyRequestItem[],
    ppbOf: PpbOf,
): InvariantResult {
    const need = itemQtyMap(items);
    const placed = placedUnits(pallets, ppbOf);
    const mismatches: InvariantResult['mismatches'] = [];
    for (const [bc, q] of need) {
        const got = placed.get(bc) ?? 0;
        if (got !== q) mismatches.push({ barcode: bc, placed: got, need: q });
    }
    // ШК, оказавшиеся в раскладке, но не в заявке (не должно быть) — тоже нарушение.
    for (const [bc, got] of placed) {
        if (!need.has(bc) && got !== 0) mismatches.push({ barcode: bc, placed: got, need: 0 });
    }
    return { ok: mismatches.length === 0, mismatches };
}

/** Глубокая копия (иммутабельность для React-state). */
function clone(pallets: PalletBox[]): PalletBox[] {
    return pallets.map(p => ({ pallet_no: p.pallet_no, boxes: p.boxes.map(b => ({ ...b })) }));
}

function boxOf(pallet: PalletBox, barcode: string): BoxContent {
    let b = pallet.boxes.find(x => x.barcode === barcode);
    if (!b) {
        b = { barcode, box_count: 0, loose_units: 0 };
        pallet.boxes.push(b);
    }
    return b;
}

/** Убрать пустые короба (box_count=0 и loose=0) с паллеты. */
function prune(pallet: PalletBox): void {
    pallet.boxes = pallet.boxes.filter(b => b.box_count > 0 || b.loose_units > 0);
}

/**
 * Перенести `count` коробов ШК с паллеты `fromNo` на `toNo` (сплит/перенос).
 * Переносится не больше, чем есть на источнике. Возвращает НОВЫЙ массив.
 */
export function moveBoxes(
    pallets: PalletBox[],
    fromNo: number,
    toNo: number,
    barcode: string,
    count: number,
): PalletBox[] {
    if (fromNo === toNo || count <= 0) return pallets;
    const next = clone(pallets);
    const from = next.find(p => p.pallet_no === fromNo);
    const to = next.find(p => p.pallet_no === toNo);
    if (!from || !to) return pallets;
    const src = from.boxes.find(b => b.barcode === barcode);
    if (!src) return pallets;
    const n = Math.min(count, src.box_count);
    if (n <= 0) return pallets;
    src.box_count -= n;
    boxOf(to, barcode).box_count += n;
    prune(from);
    return next;
}

/** Перенести `count` штук россыпи ШК с паллеты `fromNo` на `toNo`. */
export function moveLoose(
    pallets: PalletBox[],
    fromNo: number,
    toNo: number,
    barcode: string,
    count: number,
): PalletBox[] {
    if (fromNo === toNo || count <= 0) return pallets;
    const next = clone(pallets);
    const from = next.find(p => p.pallet_no === fromNo);
    const to = next.find(p => p.pallet_no === toNo);
    if (!from || !to) return pallets;
    const src = from.boxes.find(b => b.barcode === barcode);
    if (!src) return pallets;
    const n = Math.min(count, src.loose_units);
    if (n <= 0) return pallets;
    src.loose_units -= n;
    boxOf(to, barcode).loose_units += n;
    prune(from);
    return next;
}

/** Добавить новую паллету (номер = max+1). */
export function addPallet(pallets: PalletBox[]): PalletBox[] {
    const maxNo = pallets.reduce((m, p) => Math.max(m, p.pallet_no), 0);
    return [...clone(pallets), { pallet_no: maxNo + 1, boxes: [] }];
}

/**
 * Удалить паллету: её короба уезжают в буфер «Не распределено» (не теряются).
 * Буфер (pallet_no=0) удалить нельзя.
 */
export function removePallet(pallets: PalletBox[], palletNo: number): PalletBox[] {
    if (palletNo === BUFFER_PALLET_NO) return pallets;
    const next = clone(pallets);
    const target = next.find(p => p.pallet_no === palletNo);
    if (!target || target.boxes.length === 0) {
        return next.filter(p => p.pallet_no !== palletNo);
    }
    let buffer = next.find(p => p.pallet_no === BUFFER_PALLET_NO);
    if (!buffer) {
        buffer = { pallet_no: BUFFER_PALLET_NO, boxes: [] };
        next.unshift(buffer);
    }
    for (const b of target.boxes) {
        const dst = boxOf(buffer, b.barcode);
        dst.box_count += b.box_count;
        dst.loose_units += b.loose_units;
    }
    return next.filter(p => p.pallet_no !== palletNo);
}

/** Нормализовать перед отправкой: выкинуть пустые короба, буфер оставить как есть. */
export function normalizeForSave(pallets: PalletBox[]): PalletBox[] {
    return clone(pallets)
        .map(p => ({ pallet_no: p.pallet_no, boxes: p.boxes.filter(b => b.box_count > 0 || b.loose_units > 0) }))
        .filter(p => p.boxes.length > 0 || p.pallet_no !== BUFFER_PALLET_NO);
}
