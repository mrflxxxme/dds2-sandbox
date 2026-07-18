import { describe, expect, it } from 'vitest';
import {
    allocSrcAcrossFf,
    applyDraftCellEdit,
    applyDraftPrebookEdit,
    buildAutoSyncPlan,
    buildDraftSkus,
    buildWriteDistribution,
    buildPinnedRowsFf,
    buildTopUpRowsFf,
    planBoxTopUpFf,
    type CellEdits,
    type PinnedPkgOf,
    type DraftDistInput,
    type AcceptanceSplitMap,
} from '@/lib/assembly/draftDistribution';
import { applyAcceptanceSplits } from '@/lib/assembly/buildAssemblyDistribution';
import { buildDraftRows, type DraftSkuInput } from '@/lib/assembly/buildDraftRows';
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

    it('cellPkg=MONOPALLET: правка создаёт МОНО-строку, BOX-строки не тронуты, чужая предбронь не поглощается', () => {
        const boxRow = { nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 52 }, tgt: { 'ЕКБ': 52 } };
        const monoPrebook = [{ nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 8 }, tgt: { 'Казань': 8 }, package_type: 'MONOPALLET' as const }];
        // Правим МОНО-ячейку Невинномысска: BOX-строка ЕКБ остаётся, моно-предбронь
        // Казани сливается в точный моно-план (та же упаковка).
        const out = applyDraftCellEdit([boxRow], monoPrebook, art(), 'Невинномысск', 2, 4, 'MONOPALLET');
        expect(out).not.toBeNull();
        const mono = out!.rows.find((r) => r.package_type === 'MONOPALLET');
        const box = out!.rows.find((r) => (r.package_type ?? 'BOX') === 'BOX');
        expect(box?.tgt).toEqual({ 'ЕКБ': 52 });
        expect(mono?.tgt).toEqual({ 'Невинномысск': 8, 'Казань': 8 });
        expect(out!.prebook).toEqual([]);
        // Обратный кейс: правка BOX-ячейки НЕ поглощает моно-предбронь (остаётся предбронью).
        const out2 = applyDraftCellEdit([boxRow], monoPrebook, art(), 'ЕКБ', 1, 4, 'BOX');
        expect(out2).not.toBeNull();
        expect(out2!.prebook).toEqual(monoPrebook);
        expect(out2!.rows.find((r) => (r.package_type ?? 'BOX') === 'BOX')?.tgt).toEqual({ 'ЕКБ': 56 });
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


describe('applyDraftPrebookEdit — моно-предбронь степпером (моно без лимита → предзаявка)', () => {
    const art = () => article('A', 1, { 4: 300, 5: 20 });

    it('инкремент создаёт МОНО-строку в предброни, строки-заявки не тронуты', () => {
        const rows = [{ nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 52 }, tgt: { 'ЕКБ': 52 } }];
        const out = applyDraftPrebookEdit(rows, [], art(), 'Тула', 2, 4, 'MONOPALLET');
        expect(out).not.toBeNull();
        expect(out!.rows).toEqual(rows); // заявки как были
        expect(out!.prebook).toHaveLength(1);
        expect(out!.prebook[0].package_type).toBe('MONOPALLET');
        expect(out!.prebook[0].tgt).toEqual({ 'Тула': 8 });
        expect(sumRec(out!.prebook[0].src)).toBe(8);
    });

    it('декремент уменьшает предбронь и НЕ конвертирует её в строки', () => {
        const prebook = [{ nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 12 }, tgt: { 'Тула': 12 }, package_type: 'MONOPALLET' as const }];
        const out = applyDraftPrebookEdit([], prebook, art(), 'Тула', -1, 4, 'MONOPALLET');
        expect(out).not.toBeNull();
        expect(out!.rows).toEqual([]);
        expect(out!.prebook).toHaveLength(1);
        expect(out!.prebook[0].tgt).toEqual({ 'Тула': 8 });
    });

    it('кап: строки+чужая предбронь бронируют сток, превышение → null', () => {
        const rows = [{ nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 300 }, tgt: { 'ЕКБ': 300 } }];
        // свободно 320 − 300 (строки) = 20 → 6 коробов по 4 = 24 не лезут
        expect(applyDraftPrebookEdit(rows, [], art(), 'Тула', 6, 4, 'MONOPALLET')).toBeNull();
        // 5 коробов = 20 — ок
        const ok = applyDraftPrebookEdit(rows, [], art(), 'Тула', 5, 4, 'MONOPALLET');
        expect(ok).not.toBeNull();
        expect(sumRec(ok!.prebook[0].src)).toBe(20);
    });

    it('BOX-предбронь того же SKU не тронута (правится только своя упаковка)', () => {
        const prebook: Parameters<typeof applyDraftPrebookEdit>[1] = [
            { nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 8 }, tgt: { 'Казань': 8 } },
            { nm_id: 1, barcode: 'A', vendor_code: 'art-1', src: { '4': 4 }, tgt: { 'Тула': 4 }, package_type: 'MONOPALLET' },
        ];
        const out = applyDraftPrebookEdit([], prebook, art(), 'Тула', 1, 4, 'MONOPALLET');
        expect(out).not.toBeNull();
        const box = out!.prebook.find(r => (r.package_type ?? 'BOX') === 'BOX');
        const mono = out!.prebook.find(r => r.package_type === 'MONOPALLET');
        expect(box?.tgt).toEqual({ 'Казань': 8 });
        expect(mono?.tgt).toEqual({ 'Тула': 8 });
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

describe('applyDraftCellEdit / applyDraftPrebookEdit — мульти-баркодный nm (barcode в ключе слияния)', () => {
    // Одна WB-карточка (nm) может нести несколько баркодов (размерные варианты).
    // Идентичность строки — (nm × barcode × упаковка × as_is), см. keyOfDraftRow и
    // backend `_merge_rows`: строка ЧУЖОГО баркода не сливается в правку — иначе её
    // план молча переатрибуцировался бы на баркод статьи (чужой физический товар).
    const otherBc = () => ({ nm_id: 1, barcode: 'B2', vendor_code: 'art-1', src: { '4': 50 }, tgt: { 'Казань': 50 } });

    it('строка другого баркода не поглощается правкой и сохраняет свой barcode', () => {
        const out = applyDraftCellEdit([otherBc()], [], article('B1', 1, { 4: 300 }), 'Коледино', 1, 10);
        expect(out).not.toBeNull();
        const b2 = out!.rows.find((r) => r.barcode === 'B2');
        expect(b2?.tgt).toEqual({ 'Казань': 50 }); // нетронута
        const b1 = out!.rows.find((r) => r.barcode === 'B1');
        expect(b1?.tgt).toEqual({ 'Коледино': 10 }); // правка легла в свой баркод
    });

    it('кап учитывает бронь строк чужого баркода (rf_stocks агрегирован per nm)', () => {
        // Свободно 60 − 50 (строка B2) = 10 → 2 короба (20) не лезут, 1 (10) — впритык.
        expect(applyDraftCellEdit([otherBc()], [], article('B1', 1, { 4: 60 }), 'Коледино', 2, 10)).toBeNull();
        expect(applyDraftCellEdit([otherBc()], [], article('B1', 1, { 4: 60 }), 'Коледино', 1, 10)).not.toBeNull();
    });

    it('декремент в ноль не трогает строку чужого баркода', () => {
        const own = { nm_id: 1, barcode: 'B1', vendor_code: 'art-1', src: { '4': 10 }, tgt: { 'Коледино': 10 } };
        const out = applyDraftCellEdit([own, otherBc()], [], article('B1', 1, { 4: 300 }), 'Коледино', -1, 10);
        expect(out).not.toBeNull();
        expect(out!.rows).toEqual([otherBc()]);
    });

    it('applyDraftPrebookEdit: предбронь чужого баркода той же упаковки не поглощается', () => {
        const otherMono = { nm_id: 1, barcode: 'B2', vendor_code: 'art-1', src: { '4': 8 }, tgt: { 'Казань': 8 }, package_type: 'MONOPALLET' as const };
        const out = applyDraftPrebookEdit([], [otherMono], article('B1', 1, { 4: 300 }), 'Тула', 1, 4, 'MONOPALLET');
        expect(out).not.toBeNull();
        const b2 = out!.prebook.find((r) => r.barcode === 'B2');
        expect(b2?.tgt).toEqual({ 'Казань': 8 }); // чужой баркод остался предбронью как был
        const b1 = out!.prebook.find((r) => r.barcode === 'B1');
        expect(b1?.tgt).toEqual({ 'Тула': 4 });
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

describe('buildWriteDistribution — preserveCells (дозабор/«Оставить так» переживают авто-синк)', () => {
    type Row = { nm_id: number; barcode: string; vendor_code: string; src: Record<string, number>; tgt: Record<string, number>; package_type: 'BOX'; as_is?: boolean };
    const row = (nm: number, tgt: Record<string, number>, src: Record<string, number>, extra: Partial<Row> = {}): Row =>
        ({ nm_id: nm, barcode: `bc${nm}`, vendor_code: `art-${nm}`, src, tgt, package_type: 'BOX', ...extra });
    /** Σ tgt[wb] по всем строкам nm. */
    const cellQty = (rows: { nm_id: number; tgt: Record<string, number> }[], nm: number, wb: string) =>
        rows.filter(r => r.nm_id === nm).reduce((s, r) => s + (r.tgt[wb] || 0), 0);

    it('дозабранная ячейка переживает замену по nm, расчёт добавляется без потери', () => {
        const dist = { rows: [row(1, { 'СПБ Шушары': 30 }, { '5': 30 })], prebook: [] };
        const calc = [row(1, { 'Екатеринбург': 60 }, { '4': 60 })];
        const out = buildWriteDistribution(dist, calc, [], new Set(), new Set(['1::СПБ Шушары']));
        expect(cellQty(out.rows, 1, 'СПБ Шушары')).toBe(30);   // до фикса: 0 (владение по nm целиком)
        expect(cellQty(out.rows, 1, 'Екатеринбург')).toBe(60);
        // Источники сохранены: дозабор с ФФ 5, расчёт с ФФ 4.
        const srcSum: Record<string, number> = {};
        for (const r of out.rows) for (const [ff, q] of Object.entries(r.src)) srcSum[ff] = (srcSum[ff] || 0) + q;
        expect(srcSum).toEqual({ '4': 60, '5': 30 });
    });

    it('ячейка расчёта на сохранённый (nm, wb) вырезается — нет задвоения', () => {
        const dist = { rows: [row(1, { 'СПБ Шушары': 30 }, { '5': 30 })], prebook: [] };
        const calc = [row(1, { 'Екатеринбург': 60, 'СПБ Шушары': 45 }, { '4': 105 })];
        const out = buildWriteDistribution(dist, calc, [], new Set(), new Set(['1::СПБ Шушары']));
        expect(cellQty(out.rows, 1, 'СПБ Шушары')).toBe(30);   // ручное решение, не 30+45
        expect(cellQty(out.rows, 1, 'Екатеринбург')).toBe(60);
        const total = out.rows.reduce((s, r) => s + sumRec(r.tgt), 0);
        expect(total).toBe(90);
    });

    it('смешанная строка: сохранённая ячейка карвится, остальные ячейки заменяются расчётом', () => {
        const dist = { rows: [row(1, { 'Казань': 15, 'СПБ Шушары': 30 }, { '5': 45 })], prebook: [] };
        const calc = [row(1, { 'Казань': 40 }, { '5': 40 })];
        const out = buildWriteDistribution(dist, calc, [], new Set(), new Set(['1::СПБ Шушары']));
        expect(cellQty(out.rows, 1, 'СПБ Шушары')).toBe(30);
        expect(cellQty(out.rows, 1, 'Казань')).toBe(40);       // старые 15 заменены расчётом
    });

    it('as_is-строка («Оставить так») с сохранённой ячейкой переживает', () => {
        const dist = { rows: [row(1, { 'СПБ Шушары': 19 }, { '5': 19 }, { as_is: true })], prebook: [] };
        const calc = [row(1, { 'Екатеринбург': 38 }, { '4': 38 })];
        const out = buildWriteDistribution(dist, calc, [], new Set(), new Set(['1::СПБ Шушары']));
        const asIs = out.rows.filter(r => r.as_is);
        expect(asIs).toHaveLength(1);
        expect(asIs[0].tgt).toEqual({ 'СПБ Шушары': 19 });
        expect(cellQty(out.rows, 1, 'Екатеринбург')).toBe(38);
    });

    it('стейл-ключ (ячейки нет в черновике) не режет план расчёта', () => {
        const dist = { rows: [], prebook: [] };
        const calc = [row(2, { 'СПБ Шушары': 45 }, { '4': 45 })];
        const out = buildWriteDistribution(dist, calc, [], new Set(), new Set(['2::СПБ Шушары']));
        expect(cellQty(out.rows, 2, 'СПБ Шушары')).toBe(45);   // ключ протух → расчёт владеет
    });

    it('предбронь: сохранённая ⌛-ячейка предброни переживает замену', () => {
        const dist = { rows: [], prebook: [row(3, { 'Казань': 57 }, { '5': 57 })] };
        const calcPb = [row(3, { 'Казань': 19 }, { '5': 19 })];
        const out = buildWriteDistribution(dist, [], calcPb, new Set(), new Set(['3::Казань']));
        expect(cellQty(out.prebook, 3, 'Казань')).toBe(57);    // дозабранные паллеты, не хвост расчёта
    });

    it('purge guarded-новинки не убивает сохранённую ячейку (явный клик = решение человека)', () => {
        const dist = { rows: [row(1, { 'Екатеринбург': 20, 'СПБ Шушары': 30 }, { '5': 50 })], prebook: [] };
        const out = buildWriteDistribution(dist, [], [], new Set([1]), new Set(['1::СПБ Шушары']));
        expect(cellQty(out.rows, 1, 'СПБ Шушары')).toBe(30);
        expect(cellQty(out.rows, 1, 'Екатеринбург')).toBe(0);  // остальное guarded-вычищено
    });

    it('без preserveCells поведение прежнее — замена по nm целиком', () => {
        const dist = { rows: [row(1, { 'СПБ Шушары': 30 }, { '5': 30 })], prebook: [] };
        const calc = [row(1, { 'Екатеринбург': 60 }, { '4': 60 })];
        const out = buildWriteDistribution(dist, calc, [], new Set());
        expect(cellQty(out.rows, 1, 'СПБ Шушары')).toBe(0);
        expect(cellQty(out.rows, 1, 'Екатеринбург')).toBe(60);
    });
});

describe('buildAutoSyncPlan — preserveCells', () => {
    type Row = { nm_id: number; barcode: string; vendor_code: string; src: Record<string, number>; tgt: Record<string, number>; package_type: 'BOX' };
    const row = (nm: number, tgt: Record<string, number>, src: Record<string, number>): Row =>
        ({ nm_id: nm, barcode: `bc${nm}`, vendor_code: `art-${nm}`, src, tgt, package_type: 'BOX' });

    it('дозабранная ячейка не откатывается авто-синком', () => {
        const dist = { rows: [row(1, { 'СПБ Шушары': 30 }, { '5': 30 })], prebook: [] };
        const calc = [row(1, { 'Екатеринбург': 60 }, { '4': 60 })];
        const out = buildAutoSyncPlan(dist, calc, [], new Set(), new Set(), new Set(['1::СПБ Шушары']));
        expect(out).not.toBeNull();
        const spb = out!.rows.reduce((s, r) => s + (r.nm_id === 1 ? (r.tgt['СПБ Шушары'] || 0) : 0), 0);
        expect(spb).toBe(30);
    });

    it('фикс-поинт: повторный прогон от собственного результата → null (нет PUT-цикла)', () => {
        const dist = { rows: [row(1, { 'СПБ Шушары': 30 }, { '5': 30 })], prebook: [] };
        const calc = [row(1, { 'Екатеринбург': 60 }, { '4': 60 })];
        const preserve = new Set(['1::СПБ Шушары']);
        const first = buildAutoSyncPlan(dist, calc, [], new Set(), new Set(), preserve);
        expect(first).not.toBeNull();
        const second = buildAutoSyncPlan({ rows: first!.rows, prebook: first!.prebook }, calc, [], new Set(), new Set(), preserve);
        expect(second).toBeNull();
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

describe('applyAcceptanceSplits — сплит приёмки BOX+MONO (прод-кейс 150х200_серый)', () => {
    const splitInput = () => {
        const base: DraftSkuInput = {
            nm_id: 42, barcode: 'bc42', vendor_code: '150х200_серый',
            target: { 'Екатеринбург': 154, 'Тула': 64, 'Казань': 9 },
            ffStock: { 3: 1928 },
            ppb: 13, box_size: '60x40x40', packageType: 'BOX',
        };
        const splitMap: AcceptanceSplitMap = new Map<string, { package_type: PackageType; distribution: Record<string, number> }[]>([['42::bc42', [
            { package_type: 'BOX', distribution: { 'Екатеринбург': 154, 'Тула': 64 } },
            { package_type: 'MONOPALLET', distribution: { 'Казань': 9 } },
        ]]]);
        return { base, splitMap };
    };

    it('BOX-часть НЕ теряется (был last-wins по nm::barcode в buildDraftRows)', () => {
        const { base, splitMap } = splitInput();
        const effective = applyAcceptanceSplits([base], splitMap);
        const rows = buildDraftRows({ skus: effective });
        const boxQty = rows.filter(r => r.package_type === 'BOX')
            .reduce((s, r) => s + sumRec(r.tgt), 0);
        const monoQty = rows.filter(r => r.package_type === 'MONOPALLET')
            .reduce((s, r) => s + sumRec(r.tgt), 0);
        expect(boxQty).toBeGreaterThan(0);   // до фикса: 0 (перетёрт моно-сплитом)
        expect(monoQty).toBeGreaterThan(0);  // моно-часть тоже жива
        // BOX едет целыми коробами по своим складам.
        const boxTgt = Object.assign({}, ...rows.filter(r => r.package_type === 'BOX').map(r => r.tgt));
        expect(boxTgt['Екатеринбург'] % 13).toBe(0);
        expect(boxTgt['Екатеринбург']).toBeGreaterThan(0);
    });

    it('сплиты сорсят ОБЩИЙ ФФ-сток, а не каждый свой (нет двойного счёта)', () => {
        const { base, splitMap } = splitInput();
        // Стока хватает только на BOX-часть: моно должно конкурировать, а не удвоить пул.
        base.ffStock = { 3: 200 };
        const effective = applyAcceptanceSplits([base], splitMap);
        const rows = buildDraftRows({ skus: effective });
        const totalSrc = rows.reduce((s, r) => s + sumRec(r.src as Record<string, number>), 0);
        expect(totalSrc).toBeLessThanOrEqual(200);
    });
});
