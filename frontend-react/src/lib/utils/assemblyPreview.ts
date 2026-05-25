// Предпросмотр заявок: разворот строк черновика в (ФФ → WB → товар) тем же
// алгоритмом, что и backend commit, + группировки и колонки Excel-выгрузки.
// Используется страницей distribute/preview.
import type { AssemblyDraftRow, HandedUnit, PackageType } from '@/types/api';
import type { ExcelExportColumn } from '@/lib/utils';

export interface PreviewLine {
    ffId: number;
    wbName: string;
    nmId: number;
    vendor: string;
    barcode: string;
    pkg: PackageType;
    isNew: boolean;
    qty: number;
}

export const PKG_LABEL_RU: Record<string, string> = {
    BOX: 'Короб', MONOPALLET: 'Моно', SUPERSAFE: 'Суперсейф',
};

/** Зеркало backend `_allocate_pairs` (pro-rata + largest-remainder) — чтобы
 *  предпросмотр «ФФ → WB → товар» совпадал с заявками, которые создаст commit.
 *  Возвращает Map "ffId::wbName" → qty. */
export function allocatePairs(src: Record<string, number>, tgt: Record<string, number>): Map<string, number> {
    const srcItems = Object.entries(src)
        .map(([k, v]) => [Number(k), Math.trunc(Number(v) || 0)] as [number, number])
        .filter(([, v]) => v > 0);
    const tgtItems = Object.entries(tgt)
        .map(([k, v]) => [k, Math.trunc(Number(v) || 0)] as [string, number])
        .filter(([, v]) => v > 0);
    const out = new Map<string, number>();
    if (!srcItems.length || !tgtItems.length) return out;
    const total = srcItems.reduce((s, [, v]) => s + v, 0);
    if (total <= 0) return out;

    const raw: Array<{ sid: number; tname: string; q: number; residue: number }> = [];
    let floorSum = 0;
    for (const [sid, sv] of srcItems) {
        for (const [tname, tv] of tgtItems) {
            const num = sv * tv;
            const q = Math.floor(num / total);
            raw.push({ sid, tname, q, residue: (num - q * total) / total });
            floorSum += q;
        }
    }
    const remainder = total - floorSum;
    raw.sort((a, b) => b.residue - a.residue || a.sid - b.sid
        || (a.tname < b.tname ? -1 : a.tname > b.tname ? 1 : 0));
    raw.forEach((r, i) => {
        const q = r.q + (i < remainder ? 1 : 0);
        if (q > 0) out.set(`${r.sid}::${r.tname}`, q);
    });
    return out;
}

/** Разворачивает строки черновика в плоский список (ФФ → WB → товар). */
export function buildPreviewLines(rows: AssemblyDraftRow[], newcomerNmIds: Set<number>): PreviewLine[] {
    const out: PreviewLine[] = [];
    for (const r of rows) {
        const pkg = (r.package_type || 'BOX') as PackageType;
        const isNew = newcomerNmIds.has(r.nm_id);
        const vendor = r.vendor_code || `nm:${r.nm_id}`;
        for (const [key, qty] of allocatePairs(r.src, r.tgt)) {
            if (qty <= 0) continue;
            const [ffStr, wbName] = key.split('::');
            out.push({ ffId: Number(ffStr), wbName, nmId: r.nm_id, vendor, barcode: r.barcode || '', pkg, isNew, qty });
        }
    }
    return out;
}

export const sumQty = (ls: PreviewLine[]) => ls.reduce((s, l) => s + l.qty, 0);
// Заявок = уникальные (склад × упаковка). Новинки и обычные на один склад — одна заявка.
export const reqCountOf = (ls: PreviewLine[]) => new Set(ls.map(l => `${l.wbName}::${l.pkg}`)).size;
export const skuCountOf = (ls: PreviewLine[]) => new Set(ls.map(l => l.nmId)).size;

/** Группировка по складу-источнику (ФФ), отсортированных по убыванию объёма. */
export function groupByFf(lines: PreviewLine[]): Array<{ ffId: number; lines: PreviewLine[] }> {
    const m = new Map<number, PreviewLine[]>();
    for (const l of lines) {
        const arr = m.get(l.ffId) ?? [];
        arr.push(l);
        m.set(l.ffId, arr);
    }
    return [...m.entries()]
        .map(([ffId, ls]) => ({ ffId, lines: ls }))
        .sort((a, b) => sumQty(b.lines) - sumQty(a.lines));
}

/** Группировка строк ФФ по WB-цели, отсортированных по убыванию объёма. */
export function groupByWb(ls: PreviewLine[]): Array<{ wb: string; items: PreviewLine[] }> {
    const m = new Map<string, PreviewLine[]>();
    for (const l of ls) {
        const arr = m.get(l.wbName) ?? [];
        arr.push(l);
        m.set(l.wbName, arr);
    }
    return [...m.entries()]
        .map(([wb, items]) => ({
            wb,
            items: items.slice().sort((a, b) => b.qty - a.qty || a.vendor.localeCompare(b.vendor)),
        }))
        .sort((a, b) => sumQty(b.items) - sumQty(a.items));
}

// Имя листа Excel: ≤31 симв., без запрещённых символов [ ] : * ? / \.
export const excelSheetName = (s: string) => (s.replace(/[^\p{L}\p{N} _.()-]/gu, ' ').trim().slice(0, 31) || 'Склад');

export const PREVIEW_EXPORT_COLUMNS: ExcelExportColumn[] = [
    { key: 'vendor', label: 'Артикул' },
    { key: 'barcode', label: 'Баркод' },
    { key: 'wb', label: 'WB-склад (цель)' },
    { key: 'qty', label: 'Шт' },
    { key: 'boxes', label: 'Коробок' },
    { key: 'box_qty', label: 'В коробе' },
    { key: 'pkg', label: 'Упаковка' },
    { key: 'type', label: 'Тип' },
];
export const PREVIEW_EXPORT_COLUMNS_FF: ExcelExportColumn[] = [
    { key: 'ff', label: 'Склад-источник' },
    ...PREVIEW_EXPORT_COLUMNS,
];

// ─── Заявки-юниты склада (черновик + переданные на ФФ) ──────────────────────
export type UnitStatus = 'draft' | 'handed';

export interface UnitItem {
    nmId: number;
    vendor: string;
    barcode: string;
    qty: number;
    /** Товар-новинка (derived из newcomer_nm_ids) — для бейджа 🆕 в позициях. */
    isNew: boolean;
}

export interface DraftUnit {
    ffId: number;
    wbName: string;
    pkg: PackageType;
    /** В юните есть хотя бы один товар-новинка (для пометки 🆕 на карточке). */
    hasNewcomer: boolean;
    status: UnitStatus;
    /** true = снимок в handed_units (вырезан из rows: ручной черновик или передан на ФФ). */
    frozen: boolean;
    items: UnitItem[];
    qty: number;
}

/** Все заявки-юниты склада-источника: черновик (вычисляем из rows) +
 *  переданные на ФФ (замороженные снимки handed_units). rows/handed уже должны
 *  быть отфильтрованы по нужному срезу (упаковка × тип товара). */
export function buildFfUnits(
    rows: AssemblyDraftRow[],
    handed: HandedUnit[],
    newcomerNmIds: Set<number>,
    ffId: number,
): DraftUnit[] {
    const newUnit = (wbName: string, pkg: PackageType, status: UnitStatus, frozen: boolean): DraftUnit =>
        ({ ffId, wbName, pkg, hasNewcomer: false, status, frozen, items: [], qty: 0 });
    const addItem = (u: DraftUnit, it: UnitItem) => {
        u.items.push(it);
        u.qty += it.qty;
        if (it.isNew) u.hasNewcomer = true;
    };

    // Авто-часть из rows, ключ wb::pkg (новинки + обычные вместе).
    const draftMap = new Map<string, DraftUnit>();
    for (const l of buildPreviewLines(rows, newcomerNmIds)) {
        if (l.ffId !== ffId) continue;
        const key = `${l.wbName}::${l.pkg}`;
        let u = draftMap.get(key);
        if (!u) { u = newUnit(l.wbName, l.pkg, 'draft', false); draftMap.set(key, u); }
        addItem(u, { nmId: l.nmId, vendor: l.vendor, barcode: l.barcode, qty: l.qty, isNew: l.isNew });
    }

    // Замороженная часть из handed_units (тот же ключ). Несколько снимков на ключ
    // (старые черновики, где новинки/обычные были раздельны) сливаются по баркоду;
    // статус 'handed' побеждает 'draft'.
    const handedMap = new Map<string, DraftUnit>();
    for (const h of handed) {
        if (h.source_ff_id !== ffId) continue;
        const key = `${h.target_wb_name}::${h.package_type}`;
        let u = handedMap.get(key);
        if (!u) { u = newUnit(h.target_wb_name, h.package_type, 'draft', true); handedMap.set(key, u); }
        if (h.status === 'handed') u.status = 'handed';
        for (const it of h.items) {
            addItem(u, { nmId: it.nm_id, vendor: it.vendor_code, barcode: it.barcode, qty: it.qty, isNew: newcomerNmIds.has(it.nm_id) });
        }
    }

    // Одна карточка на (wb, pkg). Смешанный случай (часть в снимке + часть ещё в
    // rows — старый черновик «в полёте») → показываем как ОДИН черновик: пока есть
    // авто-часть, юнит не передан целиком; позиции объединяем по баркоду.
    const out: DraftUnit[] = [];
    const keys = new Set<string>([...handedMap.keys(), ...draftMap.keys()]);
    for (const key of keys) {
        const d = draftMap.get(key);
        const h = handedMap.get(key);
        if (d && h) {
            const merged = newUnit(d.wbName, d.pkg, 'draft', false);
            const byBc = new Map<string, UnitItem>();
            for (const it of [...h.items, ...d.items]) {
                const ex = byBc.get(it.barcode);
                if (ex) { ex.qty += it.qty; ex.isNew = ex.isNew || it.isNew; }
                else { const ni = { ...it }; byBc.set(it.barcode, ni); merged.items.push(ni); }
            }
            merged.qty = merged.items.reduce((s, it) => s + it.qty, 0);
            merged.hasNewcomer = merged.items.some(it => it.isNew);
            out.push(merged);
        } else {
            out.push((d ?? h) as DraftUnit);
        }
    }
    return out.sort((a, b) => b.qty - a.qty);
}

/** Найти один юнит склада по (wb, упаковка). */
export function findDraftUnit(
    rows: AssemblyDraftRow[],
    handed: HandedUnit[],
    newcomerNmIds: Set<number>,
    ffId: number,
    wbName: string,
    pkg: PackageType,
): DraftUnit | null {
    return buildFfUnits(rows, handed, newcomerNmIds, ffId).find(
        u => u.wbName === wbName && u.pkg === pkg,
    ) ?? null;
}
