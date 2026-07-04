/**
 * Построение групп предброни (направление × ФФ-источник × упаковка) из строк-хвостов —
 * чистая, переиспользуемая версия inline-логики раздела «Сборка» (`distribute/page.tsx`).
 *
 * Развилка раздел↔машина — только ИСТОЧНИК доступности:
 *   • раздел: `articles` = stockNeed.articles.rf_stocks (наш ФФ, мульти-склад);
 *   • машина: `articles` = пул на ФФ разгрузки (один источник) — или пусто (v1 без дозабора).
 * Всё остальное (карточки по городам, footprint смешанной паллеты Σqty/upp, моно-раскладка
 * packMonoPallets, дозабор planTopUpBoxes) — общее, зеркалит авторитет `snapToWholePallets`.
 *
 * Чистая функция (без React/IO) — переиспользуется экраном машины и разделом.
 */
import type { AssemblyDraftRow, PackageType } from '@/types/api';
import { allocatePairs } from '@/lib/utils/assemblyPreview';
import {
    effectiveBoxesPerPallet,
    maxPalletHeightCm,
    packMonoPallets,
    MONO_MAX_PALLET_ARTICLES,
} from '@/lib/utils/boxPallet';
import { palletFootprint, planTopUpBoxes, type TopUpCandidate } from '@/lib/assembly/prebookFootprint';
import type { PrebookGroup, PrebookMonoPallet, PrebookTopUp } from '@/app/(main)/p/[slug]/warehouse/assembly/distribute/components/PrebookView';

export interface PrebookGroupsSources {
    /** Строки предброни (под-паллетные хвосты) — из побочного выхода нормализатора. */
    prebook: AssemblyDraftRow[];
    /** Строки, УЖЕ уезжающие (черновик/раскладка) — для вычета занятого источника (inUse). */
    usedRows: AssemblyDraftRow[];
    /** Box-SKU-кандидаты дозабора: nm + vendor + доступно per ФФ (шт). Раздел — из
     *  stockNeed.articles.rf_stocks; машина — из пула на ФФ разгрузки (пусто = без дозабора). */
    articles: { nm_id: number; vendor_code: string; rfStocks: Record<number, number> }[];
    /** ФФ-склад id → имя. */
    ffName: (ffId: number) => string;
    /** Кратность короба per nm. */
    ppbOf: (nm: number) => number;
    /** Кратность короба на конкретном ФФ (фолбэк — ppbOf). */
    ppbAt: (nm: number, ffId: number) => number;
    /** Габариты короба «ДxШxВ» per nm. */
    boxSizeOf: (nm: number) => string | null;
    /** Override «коробов на паллету» по канон-размеру короба. */
    palletOverrides: Record<string, number>;
}

/** Собрать группы предброни (карточки направлений) из строк-хвостов. */
export function buildPrebookGroups(s: PrebookGroupsSources): PrebookGroup[] {
    const { prebook, usedRows, articles, ffName, ppbOf, ppbAt, boxSizeOf, palletOverrides } = s;
    if (prebook.length === 0) return [];

    const nmVendor = new Map<number, string>();
    const rfAvailByNm = new Map<number, Record<number, number>>();
    for (const a of articles) {
        nmVendor.set(a.nm_id, a.vendor_code || '');
        rfAvailByNm.set(a.nm_id, a.rfStocks || {});
    }
    const inUse: Record<number, Record<number, number>> = {};
    for (const r of [...usedRows, ...prebook]) {
        const m = (inUse[r.nm_id] ??= {});
        for (const [ff, q] of Object.entries(r.src)) m[Number(ff)] = (m[Number(ff)] || 0) + (q || 0);
    }
    // Свободные целые коробы любого box-SKU per ФФ (смешанная паллета — любой SKU),
    // отсортированные по убыванию запаса. Footprint кандидата зависит от склада-цели
    // (высота паллеты), поэтому bpp считаем на месте, ниже — по каждому направлению.
    const freeBoxesByFf = new Map<number, { nmId: number; ppb: number; freeBoxes: number }[]>();
    for (const a of articles) {
        const gppb = ppbOf(a.nm_id) || 0;
        if (gppb <= 0 || !boxSizeOf(a.nm_id)) continue;
        for (const [ff, avail] of Object.entries(a.rfStocks || {})) {
            // Короб кандидата — кратность ЕГО ФФ (может отличаться по складам).
            const ppb = ppbAt(a.nm_id, Number(ff)) || gppb;
            if (ppb <= 0) continue;
            const boxes = Math.floor(((avail || 0) - (inUse[a.nm_id]?.[Number(ff)] || 0)) / ppb);
            if (boxes > 0) {
                const arr = freeBoxesByFf.get(Number(ff)) ?? [];
                arr.push({ nmId: a.nm_id, ppb, freeBoxes: boxes });
                freeBoxesByFf.set(Number(ff), arr);
            }
        }
    }
    for (const arr of freeBoxesByFf.values()) arr.sort((x, y) => y.freeBoxes - x.freeBoxes);

    type Acc = { pkg: PackageType; wb: string; ffId: number; items: { nm_id: number; vendor_code: string; ff: string; boxes: number; qty: number; looseUnits: number; ppb: number; freeUnits: number }[]; qty: number };
    const map = new Map<string, Acc>();
    for (const r of prebook) {
        const pkg = r.package_type || 'BOX';
        const gppb = ppbOf(r.nm_id) || 0;
        // Разбиваем строку по парам (ФФ→склад) тем же allocatePairs, что и коммит:
        // одна строка может сорсить один склад с НЕСКОЛЬКИХ ФФ — каждая порция идёт
        // в СВОЮ карточку ФФ (иначе весь tgt приписался бы первому ФФ → неверный
        // источник в заявке и завышенный footprint группы).
        for (const [pairKey, q] of allocatePairs(r.src, r.tgt)) {
            if ((q || 0) <= 0) continue;
            const sep = pairKey.indexOf('::');
            const ffId = Number(pairKey.slice(0, sep));
            const wb = pairKey.slice(sep + 2);
            // Кратность порции — короб ЕЁ ФФ (глобальный min давал псевдо-россыпь).
            const ppb = ppbAt(r.nm_id, ffId) || gppb;
            const ffLabel = ffId >= 0 ? ffName(ffId) : '—';
            const key = `${pkg}::${wb}::${ffId}`;
            let g = map.get(key);
            if (!g) { g = { pkg, wb, ffId, items: [], qty: 0 }; map.set(key, g); }
            // ТОЛЬКО ЦЕЛЫЕ коробы (floor, не round — round маскировал неполный короб:
            // 70/18=3.9→4). Остаток россыпью (`q % ppb`) — не кратен коробу, не едет
            // целым коробом (правило «кратность только коробки»), показываем отдельно.
            const fullBoxes = ppb > 0 ? Math.floor(q / ppb) : 0;
            const looseUnits = ppb > 0 ? q - fullBoxes * ppb : q;
            // freeUnits: свободно этого SKU на ЭТОМ ФФ — «почему остаток не добит»
            // (добор автоматический только когда свободного хватает до целого короба).
            const freeUnits = Math.max(0, (rfAvailByNm.get(r.nm_id)?.[ffId] || 0) - (inUse[r.nm_id]?.[ffId] || 0));
            g.items.push({ nm_id: r.nm_id, vendor_code: r.vendor_code, ff: ffLabel, boxes: fullBoxes, qty: q, looseUnits, ppb, freeUnits });
            g.qty += q;
        }
    }
    const out: PrebookGroup[] = [];
    for (const g of map.values()) {
        // ИСТИННЫЙ footprint смешанной паллеты: КАЖДЫЙ SKU по своей геометрии
        // короба (Σ qty_i / upp_i), зеркало snapToWholePallets. Показ по одному
        // репрезентативному SKU врал до 3× на смешанных группах (замер на живых).
        // Кратность/вместимость — короба ФФ ГРУППЫ (показ = авторитет normalize).
        const ppbOfG = (nm: number): number => ppbAt(nm, g.ffId) || ppbOf(nm) || 0;
        const uppOf = (nm: number): number => {
            const bpp = effectiveBoxesPerPallet(boxSizeOf(nm) ?? null, maxPalletHeightCm(g.wb), palletOverrides);
            const ppb = ppbOfG(nm);
            return bpp && ppb ? bpp * ppb : 0;
        };
        // BOX — объёмный footprint смешанной паллеты (Σ qty_i/upp_i, зеркало snapToWholePallets).
        // МОНО — по ГОТОВЫМ паллетам (packMonoPallets: 100% ИЛИ полный 3-арт слот).
        let footprint: number;
        let monoPallets: PrebookMonoPallet[] | undefined;
        let monoPartials: PrebookMonoPallet[] | undefined;
        let monoTailFrac: number | undefined;
        let pmDropped: Record<string, number> | null = null;
        let monoWhole = 0;
        if (g.pkg === 'MONOPALLET') {
            const km: Record<string, number> = {};
            for (const i of g.items) km[String(i.nm_id)] = (km[String(i.nm_id)] || 0) + i.qty;
            const pm = packMonoPallets(km, (k) => uppOf(Number(k)) || null, MONO_MAX_PALLET_ARTICLES, (k) => ppbOfG(Number(k)));
            const remFrac = Object.entries(pm.dropped).reduce((sum, [k, v]) => { const u = uppOf(Number(k)); return sum + (u > 0 ? v / u : 0); }, 0);
            monoWhole = pm.pallets.length;
            pmDropped = pm.dropped;
            monoTailFrac = remFrac;
            footprint = pm.pallets.length + Math.min(0.99, remFrac);
            monoPallets = pm.pallets.map(bin => ({
                fillPct: Math.min(1, bin.reduce((sum, b) => { const u = uppOf(Number(b.key)); return sum + (u > 0 ? b.units / u : 0); }, 0)),
                items: bin.map(b => {
                    const nm = Number(b.key);
                    const ppb = ppbOf(nm) || 0;
                    return { nm_id: nm, vendor: nmVendor.get(nm) || `nm ${nm}`, units: b.units, boxes: ppb > 0 ? Math.round(b.units / ppb) : 0 };
                }),
            }));
            const tailsAll = Object.entries(pm.dropped)
                .map(([k, v]) => { const nm = Number(k); const u = uppOf(nm); const ppb = ppbOf(nm) || 0; return { nm, units: v, fp: u > 0 ? v / u : 0, ppb }; })
                .filter(t => t.units > 0)
                .sort((a, b) => b.fp - a.fp);
            monoPartials = [];
            for (let i = 0; i < tailsAll.length; i += MONO_MAX_PALLET_ARTICLES) {
                const chunk = tailsAll.slice(i, i + MONO_MAX_PALLET_ARTICLES);
                monoPartials.push({
                    fillPct: Math.min(1, chunk.reduce((sum, t) => sum + t.fp, 0)),
                    items: chunk.map(t => ({ nm_id: t.nm, vendor: nmVendor.get(t.nm) || `nm ${t.nm}`, units: t.units, boxes: t.ppb > 0 ? Math.floor(t.units / t.ppb) : 0 })),
                });
            }
        } else {
            footprint = palletFootprint(g.items.map(i => ({ nmId: i.nm_id, qty: i.qty })), uppOf);
        }
        const boxes = g.items.reduce((sum, i) => sum + i.boxes, 0);
        const frac = footprint - Math.floor(footprint);
        const fillPct = footprint > 0 ? Math.min(0.99, frac || 0.99) : 0.5;
        // Дозабор per-ФФ: неполную паллету этого ФФ на wb дособрать до целой из
        // свободных коробов ЭТОГО ЖЕ ФФ (короб с двух ФФ собрать нельзя). Оценка
        // ОПТИМИСТИЧНА (приёмку WB не дёргаем на рендере); точная — при клике «Дозабить».
        let topUp: PrebookTopUp | null = null;
        if (g.pkg === 'BOX' && footprint > 0 && g.ffId >= 0) {
            const shortfall = Math.ceil(footprint) - footprint;
            const candidates: TopUpCandidate[] = (freeBoxesByFf.get(g.ffId) ?? []).map(c => ({
                nmId: c.nmId, ppb: c.ppb, freeBoxes: c.freeBoxes,
                bpp: effectiveBoxesPerPallet(boxSizeOf(c.nmId) ?? null, maxPalletHeightCm(g.wb), palletOverrides),
            }));
            const plan = planTopUpBoxes(shortfall, candidates);
            if (shortfall > 1e-9 && plan.feasible) {
                topUp = {
                    ff: ffName(g.ffId),
                    needBoxes: plan.needBoxes,
                    pallets: Math.max(1, Math.ceil(footprint)),
                    candidates: plan.rows.map(pr => ({ vendor: nmVendor.get(pr.nmId) || `nm ${pr.nmId}`, boxes: pr.boxes })),
                };
            }
        }
        // МОНО-хвост: чем дозабрать до ещё одной ЦЕЛОЙ из свободного ФФ (ТОП-3 крупнейших хвоста).
        let tailTopUp: PrebookTopUp | null = null;
        if (g.pkg === 'MONOPALLET' && g.ffId >= 0 && pmDropped) {
            const tails = Object.entries(pmDropped)
                .map(([k, v]) => { const u = uppOf(Number(k)); return { nm: Number(k), fp: u > 0 ? v / u : 0 }; })
                .filter(t => t.fp > 0)
                .sort((a, b) => b.fp - a.fp)
                .slice(0, MONO_MAX_PALLET_ARTICLES);
            const topFp = tails.reduce((sum, t) => sum + t.fp, 0);
            const shortfall = topFp > 1e-9 ? 1 - topFp : 0;
            if (shortfall > 1e-9) {
                const topNm = new Set(tails.map(t => t.nm));
                const cands: TopUpCandidate[] = (freeBoxesByFf.get(g.ffId) ?? [])
                    .filter(c => topNm.has(c.nmId))
                    .map(c => ({
                        nmId: c.nmId, ppb: c.ppb, freeBoxes: c.freeBoxes,
                        bpp: effectiveBoxesPerPallet(boxSizeOf(c.nmId) ?? null, maxPalletHeightCm(g.wb), palletOverrides),
                    }));
                const plan = planTopUpBoxes(shortfall, cands);
                if (plan.feasible && plan.rows.length > 0) {
                    tailTopUp = {
                        ff: ffName(g.ffId),
                        needBoxes: plan.needBoxes,
                        pallets: monoWhole + 1,
                        candidates: plan.rows.map(pr => ({ vendor: nmVendor.get(pr.nmId) || `nm ${pr.nmId}`, boxes: pr.boxes })),
                    };
                }
            }
        }
        const ffLabel = g.ffId >= 0 ? ffName(g.ffId) : '—';
        const looseUnits = g.items.reduce((sum, i) => sum + (i.looseUnits || 0), 0);
        out.push({ pkg: g.pkg, wb: g.wb, ff: ffLabel, ffId: g.ffId, items: g.items, boxes, qty: g.qty, looseUnits, footprint, fillPct, topUp, tailTopUp, monoPallets, monoPartials, monoTailFrac });
    }
    return out;
}
