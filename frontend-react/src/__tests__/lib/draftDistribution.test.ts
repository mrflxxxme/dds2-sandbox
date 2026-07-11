import { describe, expect, it } from 'vitest';
import {
    allocSrcAcrossFf,
    applyDraftCellEdit,
    buildDraftSkus,
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
