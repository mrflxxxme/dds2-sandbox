/**
 * Авто-редистрибуция ПРЕДБРОНИ по приёмке WB (self-heal на загрузке).
 *
 * Проблема: предбронь черновика могла накопиться, когда распределение клало
 * товар на ⌛-склад (нет лимита приёмки, нужна предзаявка), которого НЕТ в
 * whitelist `preorder_allowed_warehouses`. Такой товар отгрузить нельзя, но он
 * «висит» в под-вкладке «Предзаявка». Backend `check_acceptance_and_redistribute`
 * теперь уводит его на свободный/whitelist склад по приоритету (см.
 * warehouse_acceptance_service). Здесь — ПРИМЕНЕНИЕ ответа бэка к строкам
 * предброни: переписываем ЦЕЛЬ (WB) из splits, СОХРАНЯЯ источник (ФФ).
 *
 * Инварианты:
 *  - `as_is`-строки НЕ трогаем (осознанный ручной «Оставить так»).
 *  - Консервация Σ per (nm, barcode): суммарный src ФФ == суммарной новой цели
 *    (бэк-редистрибуция qty сохраняет; carve NWC расходует пул ровно).
 *  - Идемпотентность: уже-корректная предбронь (open-лимит / whitelist) даёт те
 *    же splits → canon не меняется → эффект не PUT-ит.
 */
import type { AssemblyDraftRow, AcceptanceCheckPerItem, PackageType } from '@/types/api';

const itemKey = (nm: number, barcode: string): string => `${nm}::${barcode}`;

/** Канон строки для сравнения «изменилось ли» (тот же порядок, что в page.tsx). */
function rowCanon(r: AssemblyDraftRow): string {
    const s = Object.entries(r.src).filter(([, q]) => (q || 0) > 0).sort(([a], [b]) => (a < b ? -1 : 1)).map(([k, q]) => `${k}:${q}`).join(',');
    const t = Object.entries(r.tgt).filter(([, q]) => (q || 0) > 0).sort(([a], [b]) => (a < b ? -1 : 1)).map(([k, q]) => `${k}:${q}`).join(',');
    return `${r.nm_id}|${r.barcode}|${r.package_type || 'BOX'}|${r.as_is ? 1 : 0}|${s}|${t}`;
}

const groupCanon = (rows: AssemblyDraftRow[]): string => rows.map(rowCanon).sort().join(';');

/** NWC-carve: раскладывает пул ФФ по ячейкам (wb×pkg) по порядку, min(need, ffLeft). */
function carve(
    nm: number,
    barcode: string,
    vendor_code: string,
    srcPool: Record<string, number>,
    cells: { wb: string; pkg: PackageType; qty: number }[],
): AssemblyDraftRow[] {
    const ffs = Object.keys(srcPool).filter(f => (srcPool[f] || 0) > 0);
    const out: AssemblyDraftRow[] = [];
    let fi = 0;
    let ffLeft = fi < ffs.length ? srcPool[ffs[fi]] : 0;
    for (const cell of cells) {
        let need = cell.qty;
        while (need > 0 && fi < ffs.length) {
            if (ffLeft <= 0) { fi++; ffLeft = fi < ffs.length ? srcPool[ffs[fi]] : 0; continue; }
            const take = Math.min(need, ffLeft);
            out.push({ nm_id: nm, barcode, vendor_code, src: { [ffs[fi]]: take }, tgt: { [cell.wb]: take }, package_type: cell.pkg });
            need -= take;
            ffLeft -= take;
        }
    }
    return out;
}

/**
 * Применяет ответ acceptance-check (splits с перераспределённой целью) к предброни.
 * @param prebook   текущие строки предброни
 * @param respByKey ответ бэка, индексированный по `${nm_id}::${barcode}`
 * @returns { rows, changed } — новые строки предброни и флаг реального изменения
 */
export function applyAcceptanceRedistToPrebook(
    prebook: AssemblyDraftRow[],
    respByKey: Map<string, AcceptanceCheckPerItem>,
): { rows: AssemblyDraftRow[]; changed: boolean } {
    const passthrough: AssemblyDraftRow[] = [];
    const byKey = new Map<string, AssemblyDraftRow[]>();
    for (const r of prebook) {
        if (r.as_is) { passthrough.push(r); continue; }   // ручной «Оставить так» не трогаем
        const k = itemKey(r.nm_id, r.barcode);
        const g = byKey.get(k);
        if (g) g.push(r); else byKey.set(k, [r]);
    }

    const out: AssemblyDraftRow[] = [...passthrough];
    let changed = false;
    for (const [k, group] of byKey) {
        const item = respByKey.get(k);
        if (!item || !item.splits?.length) { out.push(...group); continue; }
        // Суммарный источник ФФ по группе (товар физически на этих ФФ — не меняется).
        const srcPool: Record<string, number> = {};
        for (const r of group) for (const [ff, q] of Object.entries(r.src)) srcPool[ff] = (srcPool[ff] || 0) + (q || 0);
        // Ячейки цели из splits (уже перераспределены бэком; порядок BOX→MONO→SUPER сохранён).
        const cells: { wb: string; pkg: PackageType; qty: number }[] = [];
        for (const sp of item.splits) {
            for (const [wb, q] of Object.entries(sp.distribution || {})) {
                if ((q || 0) > 0) cells.push({ wb, pkg: sp.package_type, qty: q });
            }
        }
        const srcTotal = Object.values(srcPool).reduce((a, v) => a + (v || 0), 0);
        const cellTotal = cells.reduce((a, c) => a + c.qty, 0);
        // Защита консервации: если бэк вернул иную сумму (дроп в to=null / рассинхрон) —
        // НЕ трогаем группу (лучше оставить как есть, чем потерять/задвоить товар).
        if (cellTotal !== srcTotal || cellTotal === 0) { out.push(...group); continue; }
        const rebuilt = carve(group[0].nm_id, group[0].barcode, group[0].vendor_code, srcPool, cells);
        if (groupCanon(rebuilt) !== groupCanon(group)) changed = true;
        out.push(...rebuilt);
    }
    return { rows: out, changed };
}
