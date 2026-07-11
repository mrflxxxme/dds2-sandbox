import { describe, expect, it } from 'vitest';
import {
    allocSrcAcrossFf,
    applyDraftCellEdit,
    buildAutoSyncPlan,
    buildDraftSkus,
    buildWriteDistribution,
    buildPinnedRowsFf,
    buildTopUpRowsFf,
    planBoxTopUpFf,
    type CellEdits,
    type PinnedPkgOf,
    type DraftDistInput,
} from '@/lib/assembly/draftDistribution';
import type { PackageType, StockNeedArticle } from '@/types/api';

/** Артикул потребности (минимальный фабричный хелпер). rf = { ffId: available }. */
function article(barcode: string, nm: number, rf: Record<number, number>): StockNeedArticle {
    return {
        nm_id: nm,
        vendor_code: `art-${nm}`,
        barcode,
        brand: '',
        subject: '',
        total_need: 0,
        revenue_30d: 0,
        rf_stocks: Object.fromEntries(Object.entries(rf).map(([ff, av]) => [ff, { stock: av, available: av }])),
        in_assembly: 0,
        in_transit: 0,
        in_transit_date: null,
        can_send: 0,
        deficit: 0,
        stocks_wb: 0,
    };
}

const allBox: PinnedPkgOf = () => 'BOX';
const sumRec = (r: Record<string, number>) => Object.values(r).reduce((s, v) => s + v, 0);

describe('allocSrcAcrossFf', () => {
    it('раскладывает по ФФ, крупнейший остаток первым', () => {
        expect(allocSrcAcrossFf(30, { 1: 10, 2: 40 })).toEqual({ '2': 30 });
    });
    it('перетекает на следующий ФФ, когда первого не хватает', () => {
        expect(allocSrcAcrossFf(50, { 1: 40, 2: 20 })).toEqual({ '1': 40, '2': 10 });
    });
    it('учитывает reserved per ФФ', () => {
        expect(allocSrcAcrossFf(30, { 1: 40 }, { 1: 20 })).toEqual({ '1': 20 });
    });
    it('total<=0 → пусто', () => {
        expect(allocSrcAcrossFf(0, { 1: 40 })).toEqual({});
    });
});

describe('buildPinnedRowsFf', () => {
    it('пустая карта правок → нет строк', () => {
        expect(buildPinnedRowsFf(new Map(), [article('A', 1, { 1: 50 })], new Map([[1, 10]]), allBox)).toEqual([]);
    });

    it('пин boxes×ppb; src раскладывается по НЕСКОЛЬКИМ ФФ, Σsrc==Σtgt', () => {
        // 5 коробов × 10 = 50 шт. Остаток: ФФ1=40, ФФ2=20 → src разложится 40+10.
        const edits: CellEdits = new Map([['A', { Электросталь: 3, Тула: 2 }]]);
        const rows = buildPinnedRowsFf(edits, [article('A', 1, { 1: 40, 2: 20 })], new Map([[1, 10]]), allBox);
        expect(rows).toHaveLength(1);
        const r = rows[0];
        expect(r.tgt).toEqual({ Электросталь: 30, Тула: 20 });
        expect(r.src).toEqual({ '1': 40, '2': 10 });
        expect(sumRec(r.src)).toBe(sumRec(r.tgt));
    });

    it('Σ по баркоду капится суммарным свободным ФФ-остатком (целыми коробами)', () => {
        // Σavail=25, ppb=10 → максимум 2 короба (20 шт). Пины просят 5 коробов.
        const edits: CellEdits = new Map([['A', { Электросталь: 3, Тула: 2 }]]);
        const rows = buildPinnedRowsFf(edits, [article('A', 1, { 1: 15, 2: 10 })], new Map([[1, 10]]), allBox);
        const total = rows.reduce((s, r) => s + sumRec(r.tgt), 0);
        expect(total).toBe(20);
        expect(rows.every((r) => sumRec(r.src) === sumRec(r.tgt))).toBe(true);
    });

    it('разные типы упаковки → отдельные строки, src не пере-подписывает ФФ сверх наличия', () => {
        const pkgOf: PinnedPkgOf = (_nm, wb) => (wb === 'Тула' ? 'MONOPALLET' : 'BOX') as PackageType;
        const edits: CellEdits = new Map([['A', { Электросталь: 2, Тула: 1 }]]);
        const rows = buildPinnedRowsFf(edits, [article('A', 1, { 1: 100 })], new Map([[1, 10]]), pkgOf);
        expect(rows).toHaveLength(2);
        const srcTotal = rows.reduce((s, r) => s + sumRec(r.src), 0);
        expect(srcTotal).toBe(30); // 20 (box) + 10 (mono), не больше наличия
        expect(rows.every((r) => sumRec(r.src) === sumRec(r.tgt))).toBe(true);
    });

    it('SKU без кратности (ppb≤0) не пинится', () => {
        const edits: CellEdits = new Map([['A', { Электросталь: 3 }]]);
        expect(buildPinnedRowsFf(edits, [article('A', 1, { 1: 500 })], new Map([[1, 0]]), allBox)).toEqual([]);
    });
});

describe('buildTopUpRowsFf', () => {
    it('дозабор целыми коробами, src по ФФ, кап по available−reserved', () => {
        const topup = new Map([['A', { Тула: 40 }]]);
        const reserved = new Map([['A', 25]]); // 30 занято? avail 50, reserved 25 → свободно 25 → 2 короба
        const rows = buildTopUpRowsFf(topup, [article('A', 1, { 1: 30, 2: 20 })], new Map([[1, 10]]), reserved);
        expect(rows).toHaveLength(1);
        expect(sumRec(rows[0].tgt)).toBe(20); // floor(25/10)*10 капнутый до 40 запроса
        expect(sumRec(rows[0].src)).toBe(sumRec(rows[0].tgt));
    });
});

describe('planBoxTopUpFf', () => {
    it('раскладывает коробы на баркод из свободного ФФ-стока', () => {
        const out = planBoxTopUpFf([{ nmId: 1, boxes: 3 }], 'Тула', [article('A', 1, { 1: 40 })], new Map(), new Map([[1, 10]]));
        expect(out).toEqual([{ barcode: 'A', wb: 'Тула', units: 30 }]);
    });
    it('капится свободным (available − allocByBc), целыми коробами', () => {
        const out = planBoxTopUpFf([{ nmId: 1, boxes: 5 }], 'Тула', [article('A', 1, { 1: 40 })], new Map([['A', 15]]), new Map([[1, 10]]));
        expect(out).toEqual([{ barcode: 'A', wb: 'Тула', units: 20 }]); // free=25 → 2 короба
    });
});


describe('buildDraftSkus — канал новинок (cold-start)', () => {
    const input = (
        articles: StockNeedArticle[],
        newcomerSet: Set<number>,
        newcomerAlloc?: Map<number, Record<string, number>>,
    ): DraftDistInput => ({
        articles,
        stockNeed: null, // need-канал пуст — изолируем канал новинок
        nmPpb: new Map([[1, 4]]),
        nmBoxSize: new Map(),
        palletOverrides: {},
        newcomerSet,
        newcomerAlloc,
    });

    it('новинка получает target из backend cold-start allocations', () => {
        const a = article('A', 1, { 4: 300 });
        const alloc = new Map([[1, { 'Екатеринбург - Перспективная 14': 52, Краснодар: 8 }]]);
        const { skus } = buildDraftSkus(input([a], new Set([1]), alloc));
        expect(skus).toHaveLength(1);
        expect(skus[0].target).toEqual({ 'Екатеринбург - Перспективная 14': 52, Краснодар: 8 });
        expect(sumRec(skus[0].ffStock as unknown as Record<string, number>)).toBe(300);
    });

    it('гвард пересорта: guarded nm приходит без alloc → новинка остаётся с нулями', () => {
        const a = article('A', 1, { 4: 300 });
        const { skus } = buildDraftSkus(input([a], new Set([1]), new Map()));
        expect(skus).toHaveLength(0);
    });

    it('без newcomerAlloc — прежнее поведение (новинки без авто-раскладки)', () => {
        const a = article('A', 1, { 4: 300 });
        const { skus } = buildDraftSkus(input([a], new Set([1]), undefined));
        expect(skus).toHaveLength(0);
    });

    it('alloc чужого nm (не новинки) не сеется — канал только для is_newcomer', () => {
        const a = article('A', 1, { 4: 300 });
        const { skus } = buildDraftSkus(input([a], new Set(), new Map([[1, { 'Тула': 10 }]])));
        expect(skus).toHaveLength(0);
    });

    it('новинка без свободного ФФ-остатка не сеется даже с alloc', () => {
        const a = article('A', 1, { 4: 0 });
        const { skus } = buildDraftSkus(input([a], new Set([1]), new Map([[1, { 'Тула': 10 }]])));
        expect(skus).toHaveLength(0);
    });
});


describe('applyDraftCellEdit — правка ячейки черновика', () => {
    const art = () => article('A', 1, { 4: 300, 5: 20 });

    it('инкремент на пустом черновике создаёт BOX-строку с src по ФФ', () => {
        const out = applyDraftCellEdit([], [], art(), 'Екатеринбург - Перспективная 14', 2, 4);
        expect(out).not.toBeNull();
        expect(out!.rows).toHaveLength(1);
        expect(out!.rows[0].tgt).toEqual({ 'Екатеринбург - Перспективная 14': 8 });
        expect(sumRec(out!.rows[0].src)).toBe(8);
        expect(out!.prebook).toEqual([]);
    });

    it('строки и предбронь SKU сливаются в одну сумму, правка двигает только склад ячейки', () => {
        const rows = [{ nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 52 }, tgt: { 'ЕКБ': 52 } }];
        const prebook = [{ nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 100 }, tgt: { 'Электросталь': 100 } }];
        const out = applyDraftCellEdit(rows, prebook, art(), 'ЕКБ', -1, 4);
        expect(out).not.toBeNull();
        expect(out!.rows).toHaveLength(1);
        expect(out!.rows[0].tgt).toEqual({ 'ЕКБ': 48, 'Электросталь': 100 });
        expect(sumRec(out!.rows[0].src)).toBe(148);
        expect(out!.prebook).toEqual([]); // предбронь поглощена в rows
    });

    it('превышение свободного ФФ-остатка → null (клик игнорируется)', () => {
        const out = applyDraftCellEdit([], [], article('A', 1, { 4: 10 }), 'Тула', 3, 4); // 12 > 10
        expect(out).toBeNull();
    });

    it('декремент в ноль убирает SKU из плана', () => {
        const rows = [{ nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 4 }, tgt: { 'Тула': 4 } }];
        const out = applyDraftCellEdit(rows, [], art(), 'Тула', -1, 4);
        expect(out).not.toBeNull();
        expect(out!.rows).toEqual([]);
        expect(out!.prebook).toEqual([]);
    });

    it('MONOPALLET-строки SKU не трогаются и учитываются в капе', () => {
        const mono = { nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 36 }, tgt: { 'Казань': 36 }, package_type: 'MONOPALLET' as const };
        const out = applyDraftCellEdit([mono], [], article('A', 1, { 4: 40 }), 'Тула', 1, 4);
        expect(out).not.toBeNull();
        expect(out!.rows).toHaveLength(2);
        const monoKept = out!.rows.find((r) => r.package_type === 'MONOPALLET');
        expect(monoKept?.tgt).toEqual({ 'Казань': 36 });
        // BOX-слой: 4 шт при свободных 40−36=4 — впритык проходит
        const box = out!.rows.find((r) => (r.package_type ?? 'BOX') === 'BOX');
        expect(box?.tgt).toEqual({ 'Тула': 4 });
        // а ещё +1 короб уже не влезет
        expect(applyDraftCellEdit([mono], [], article('A', 1, { 4: 40 }), 'Тула', 2, 4)).toBeNull();
    });
});


describe('buildWriteDistribution — запись пересчёта с чисткой guarded-новинок', () => {
    const row = (nm: number, bc: string, tgt: Record<string, number>, src: Record<string, number> = { '4': 10 }) =>
        ({ nm_id: nm, barcode: bc, vendor_code: `art-${nm}`, src, tgt });

    it('SKU расчёта заменяется, чужой SKU не тронут', () => {
        const dist = { rows: [row(1, 'A', { 'Тула': 10 }), row(2, 'B', { 'Казань': 5 })], prebook: [row(1, 'A', { 'Пенза': 3 })] };
        const out = buildWriteDistribution(dist, [row(1, 'A', { 'Тула': 20 }, { '4': 20 })], [], new Set());
        expect(out.rows.map((r) => [r.nm_id, r.tgt])).toEqual([[2, { 'Казань': 5 }], [1, { 'Тула': 20 }]]);
        expect(out.prebook).toEqual([]); // предбронь SKU 1 заменена (расчёт дал пустую)
    });

    it('guarded-новинка (purge) вычищается целиком: и строки, и предбронь', () => {
        const dist = { rows: [row(7, 'M', { 'ЕКБ': 24 }), row(2, 'B', { 'Казань': 5 })], prebook: [row(7, 'M', { 'Электросталь': 60 })] };
        const out = buildWriteDistribution(dist, [], [], new Set([7]));
        expect(out.rows.map((r) => r.nm_id)).toEqual([2]);
        expect(out.prebook).toEqual([]);
    });

    it('списки складов пересчитываются из итоговых строк', () => {
        const dist = { rows: [row(7, 'M', { 'ЕКБ': 24 }, { '4': 24 })], prebook: [] };
        const out = buildWriteDistribution(dist, [row(2, 'B', { 'Казань': 8 }, { '5': 8 })], [], new Set([7]));
        expect(out.source_warehouse_ids).toEqual([5]);
        expect(out.target_warehouse_names).toEqual(['Казань']);
    });

    it('без purge и без владения — черновик нетронут', () => {
        const dist = { rows: [row(1, 'A', { 'Тула': 10 })], prebook: [row(1, 'A', { 'Пенза': 3 })] };
        const out = buildWriteDistribution(dist, [], [], new Set());
        expect(out.rows).toHaveLength(1);
        expect(out.prebook).toHaveLength(1);
    });
});


describe('applyDraftCellEdit — фиксы аудита 2026-07-11', () => {
    it('src новой BOX-строки НЕ пере-подписывает ФФ, забронированный kept MONO-строкой', () => {
        // Кейс аудита: rf {1:36, 2:10}; MONO src={1:36}. +2 короба ppb=5 → src должен уйти на ФФ2.
        const mono = { nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '1': 36 }, tgt: { 'Казань': 36 }, package_type: 'MONOPALLET' as const };
        const out = applyDraftCellEdit([mono], [], article('A', 1, { 1: 36, 2: 10 }), 'Тула', 2, 5);
        expect(out).not.toBeNull();
        const box = out!.rows.find((r) => (r.package_type ?? 'BOX') === 'BOX');
        expect(box?.src).toEqual({ '2': 10 });
    });

    it('декремент перезаложенного SKU проходит (кап только на рост)', () => {
        // План 20 при живом остатке 0 (сток ушёл после записи) — уменьшить МОЖНО.
        const row = { nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 20 }, tgt: { 'Тула': 20 } };
        const out = applyDraftCellEdit([row], [], article('A', 1, { 4: 0 }), 'Тула', -1, 4);
        expect(out).not.toBeNull();
        expect(out!.rows[0].tgt).toEqual({ 'Тула': 16 });
        expect(sumRec(out!.rows[0].src)).toBe(16); // Σsrc==Σtgt даже при нулевом живом остатке
    });

    it('as_is-строка («Оставить так») не мержится и сохраняет флаг', () => {
        const asIs = { nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 10 }, tgt: { 'Тула': 10 }, as_is: true };
        const out = applyDraftCellEdit([asIs], [], article('A', 1, { 4: 50 }), 'Казань', 1, 4);
        expect(out).not.toBeNull();
        const keptAsIs = out!.rows.find((r) => r.as_is);
        expect(keptAsIs?.tgt).toEqual({ 'Тула': 10 });
        const box = out!.rows.find((r) => !r.as_is);
        expect(box?.tgt).toEqual({ 'Казань': 4 });
    });
});

describe('buildWriteDistribution — владение по nm целиком', () => {
    it('старая MONO-строка SKU заменяется BOX-результатом расчёта (нет задвоения)', () => {
        const oldMono = { nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 36 }, tgt: { 'Казань': 36 }, package_type: 'MONOPALLET' as const };
        const calcBox = { nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 20 }, tgt: { 'Казань': 20 }, package_type: 'BOX' as const };
        const out = buildWriteDistribution({ rows: [oldMono], prebook: [] }, [calcBox], [], new Set());
        expect(out.rows).toEqual([calcBox]); // MONO того же nm вычищена, суммы не задвоены
    });
});


describe('buildAutoSyncPlan — авто-синк черновика с расчётом', () => {
    const row = (nm: number, bc: string, tgt: Record<string, number>, src: Record<string, number> = { '4': 10 }) =>
        ({ nm_id: nm, barcode: bc, vendor_code: `art-${nm}`, src, tgt });

    it('авто-SKU приводится к расчёту, ручной SKU не тронут', () => {
        const dist = { rows: [row(1, 'A', { 'Тула': 10 }), row(2, 'B', { 'Казань': 5 })], prebook: [] };
        const calc = [row(1, 'A', { 'Тула': 20 }, { '4': 20 }), row(2, 'B', { 'Казань': 50 }, { '4': 50 })];
        const out = buildAutoSyncPlan(dist, calc, [], new Set(), new Set([2]));
        expect(out).not.toBeNull();
        const byNm = new Map(out!.rows.map((r) => [r.nm_id, r.tgt]));
        expect(byNm.get(1)).toEqual({ 'Тула': 20 });  // авто → расчёт
        expect(byNm.get(2)).toEqual({ 'Казань': 5 }); // ручной → как было
    });

    it('guarded-новинка вычищается, но ручная guarded — нет', () => {
        const dist = { rows: [row(7, 'M', { 'ЕКБ': 24 }), row(8, 'N', { 'Тула': 12 })], prebook: [row(7, 'M', { 'Электросталь': 60 })] };
        const out = buildAutoSyncPlan(dist, [], [], new Set([7, 8]), new Set([8]));
        expect(out).not.toBeNull();
        expect(out!.rows.map((r) => r.nm_id)).toEqual([8]); // 7 вычищен (гвард), 8 защищён (ручной)
        expect(out!.prebook).toEqual([]);
    });

    it('нет дельты → null (не спамим PUT на каждом заходе)', () => {
        const calcRow = row(1, 'A', { 'Тула': 20 }, { '4': 20 });
        const dist = { rows: [calcRow], prebook: [] };
        expect(buildAutoSyncPlan(dist, [calcRow], [], new Set(), new Set())).toBeNull();
    });
});
