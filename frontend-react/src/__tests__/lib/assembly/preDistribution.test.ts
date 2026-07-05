import { describe, it, expect } from 'vitest';
import {
    applyAcceptanceSplits,
    buildPoolSkus,
    buildTopUpRows,
    finalizePoolRows,
    planBoxTopUp,
    rowsToPreDistRows,
    splitByKratnost,
    type AcceptanceSplitMap,
    type PoolDistInput,
} from '@/lib/assembly/preDistribution';
import type { DraftSkuInput } from '@/lib/assembly/buildDraftRows';
import type { AssemblyDraftRow, PackageType, PreDistPoolRow, StockNeedResponse } from '@/types/api';

const sum = (o: Record<string, number>) => Object.values(o).reduce((s, v) => s + v, 0);

// 60×40×40 @ 180 → 16 кор/паллету; ppb=10 → 160 шт/паллету (как в buildDraftRows.test).
const SIZE = '60x40x40';
const PPB = 10;
const FF = 500; // ФФ-склад разгрузки машины

function poolRow(over: Partial<PreDistPoolRow>): PreDistPoolRow {
    return {
        barcode: 'B1', article_seller: 'ART1', article_wb: '1', name: 'Ковёр', brand: 'НУ',
        gross_qty: 100, distributed_qty: 0, available_qty: 100, box_qty: null, box_size: null, is_newcomer: false, ...over,
    };
}

/** Минимальный StockNeed: per-WB need по nm + базовые поля статей. */
function mkNeed(
    warehouses: { name: string; need: Record<number, number> }[],
    articles: { nm_id: number; barcode: string; vendor_code: string }[],
): StockNeedResponse {
    return {
        warehouses: warehouses.map(w => ({
            name: w.name,
            total_need: sum(w.need),
            articles: Object.fromEntries(Object.entries(w.need).map(([nm, need]) => [nm, { need, stock: 0, avg_daily: 0 }])),
        })),
        articles: articles.map(a => ({
            nm_id: a.nm_id, barcode: a.barcode, vendor_code: a.vendor_code,
            brand: '', subject: '', total_need: 0, revenue_30d: 0, rf_stocks: {},
            in_assembly: 0, in_transit: 0, in_transit_date: null, can_send: 0, deficit: 0, stocks_wb: 0,
        })),
        rf_warehouses: [], brands: [], subjects: [], supply_days: 14, analysis_days: 14,
        mode: 'actual', total_warehouses: 0, total_articles: 0,
        summary: { total_need: 0, total_can_send: 0, total_deficit: 0, avg_delivery_days: 0, deficit_count: 0, can_send_count: 0, no_wb_count: 0 },
    } as StockNeedResponse;
}

function mkInput(over: Partial<PoolDistInput>): PoolDistInput {
    return {
        poolRows: [], targetWarehouseId: FF, stockNeed: null,
        nmPpb: new Map([[1, PPB], [2, PPB]]),
        nmBoxSize: new Map([[1, SIZE], [2, SIZE]]),
        palletOverrides: {}, ...over,
    };
}

describe('buildPoolSkus · пул × потребность, источник = машина', () => {
    it('ffStock = доступно в пуле на склад разгрузки; target = потребность WB по nm', () => {
        const input = mkInput({
            poolRows: [poolRow({ barcode: 'B1', article_wb: '1', available_qty: 100 })],
            stockNeed: mkNeed([{ name: 'Коледино', need: { 1: 160 } }], [{ nm_id: 1, barcode: 'B1', vendor_code: 'V1' }]),
        });
        const { skus } = buildPoolSkus(input);
        expect(skus).toHaveLength(1);
        expect(skus[0].ffStock).toEqual({ [FF]: 100 });   // источник = машина, не ФФ-сток
        expect(skus[0].target).toEqual({ 'Коледино': 160 });
        expect(skus[0].ppb).toBe(PPB);
    });

    it('SKU без потребности WB исключается (весь товар на хранение)', () => {
        const input = mkInput({
            poolRows: [poolRow({ barcode: 'B9', article_wb: '9', available_qty: 50 })],
            stockNeed: mkNeed([{ name: 'Коледино', need: { 1: 160 } }], [{ nm_id: 1, barcode: 'B1', vendor_code: 'V1' }]),
        });
        expect(buildPoolSkus(input).skus).toHaveLength(0);
    });

    it('доступно 0 → SKU не попадает в раскладку', () => {
        const input = mkInput({
            poolRows: [poolRow({ barcode: 'B1', article_wb: '1', available_qty: 0 })],
            stockNeed: mkNeed([{ name: 'Коледино', need: { 1: 160 } }], [{ nm_id: 1, barcode: 'B1', vendor_code: 'V1' }]),
        });
        expect(buildPoolSkus(input).skus).toHaveLength(0);
    });
});

describe('finalizePoolRows · целые коробы/паллеты, кап пулом, баланс', () => {
    it('Σsrc===Σtgt, источник = склад машины, кап ≤ доступно в пуле', () => {
        const input = mkInput({
            poolRows: [
                poolRow({ barcode: 'B1', article_wb: '1', available_qty: 500 }),
                poolRow({ barcode: 'B2', article_wb: '2', available_qty: 500 }),
            ],
            stockNeed: mkNeed(
                [{ name: 'Коледино', need: { 1: 320, 2: 160 } }],
                [{ nm_id: 1, barcode: 'B1', vendor_code: 'V1' }, { nm_id: 2, barcode: 'B2', vendor_code: 'V2' }],
            ),
        });
        const { skus } = buildPoolSkus(input);
        const rows = finalizePoolRows(skus, input);
        expect(rows.length).toBeGreaterThan(0);
        const availByBc: Record<string, number> = { B1: 500, B2: 500 };
        const shipByBc: Record<string, number> = {};
        for (const r of rows) {
            expect(sum(r.src)).toBe(sum(r.tgt));           // баланс
            expect(Object.keys(r.src)).toEqual([String(FF)]); // единственный источник = машина
            for (const q of Object.values(r.tgt)) expect(q % PPB).toBe(0); // целые коробы
            shipByBc[r.barcode] = (shipByBc[r.barcode] || 0) + sum(r.tgt);
        }
        for (const bc of Object.keys(shipByBc)) expect(shipByBc[bc]).toBeLessThanOrEqual(availByBc[bc]);
    });

    it('меньше одной паллеты → остаётся на ФФ (целые паллеты)', () => {
        const input = mkInput({
            poolRows: [poolRow({ barcode: 'B2', article_wb: '2', available_qty: 35 })], // 35 < 160 (паллета)
            stockNeed: mkNeed([{ name: 'Коледино', need: { 2: 200 } }], [{ nm_id: 2, barcode: 'B2', vendor_code: 'V2' }]),
        });
        const { skus } = buildPoolSkus(input);
        const rows = finalizePoolRows(skus, input);
        // 35 шт < целой паллеты (160) → ничего не уезжает, весь товар на хранении.
        expect(sum(Object.fromEntries(rows.flatMap(r => Object.entries(r.tgt))))).toBe(0);
    });

    it('ровно 2 паллеты уезжают целиком', () => {
        const input = mkInput({
            poolRows: [poolRow({ barcode: 'B1', article_wb: '1', available_qty: 500 })],
            stockNeed: mkNeed([{ name: 'Коледино', need: { 1: 320 } }], [{ nm_id: 1, barcode: 'B1', vendor_code: 'V1' }]),
        });
        const rows = finalizePoolRows(buildPoolSkus(input).skus, input);
        const ship = rows.filter(r => r.barcode === 'B1').reduce((s, r) => s + sum(r.tgt), 0);
        expect(ship).toBe(320); // 2 целые паллеты по 160
    });
});

describe('applyAcceptanceSplits · приёмка (closed→open + тип упаковки)', () => {
    it('сплит на BOX/MONOPALLET по складам', () => {
        const sku: DraftSkuInput = {
            nm_id: 1, barcode: 'B1', vendor_code: 'V1',
            target: { 'Коледино': 100, 'Казань': 50 }, ffStock: { [FF]: 200 }, ppb: PPB, box_size: SIZE, packageType: 'BOX',
        };
        const splitMap: AcceptanceSplitMap = new Map<string, { package_type: PackageType; distribution: Record<string, number> }[]>([
            ['1::B1', [
                { package_type: 'BOX', distribution: { 'Коледино': 100 } },
                { package_type: 'MONOPALLET', distribution: { 'Казань': 50 } },
            ]],
        ]);
        const out = applyAcceptanceSplits([sku], splitMap);
        expect(out).toHaveLength(2);
        expect(out.find(s => s.packageType === 'MONOPALLET')?.target).toEqual({ 'Казань': 50 });
    });

    it('нет сплита → SKU как есть', () => {
        const sku: DraftSkuInput = { nm_id: 1, barcode: 'B1', vendor_code: 'V1', target: { 'Коледино': 100 }, ffStock: { [FF]: 200 }, ppb: PPB, box_size: SIZE };
        expect(applyAcceptanceSplits([sku], new Map())).toEqual([sku]);
        expect(applyAcceptanceSplits([sku], null)).toEqual([sku]);
    });
});

describe('rowsToPreDistRows · свёртка в позиции запроса', () => {
    it('агрегирует per barcode × WB × упаковка', () => {
        const rows: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'B1', vendor_code: 'V1', src: { [FF]: 160 }, tgt: { 'Коледино': 160 }, package_type: 'BOX' },
            { nm_id: 1, barcode: 'B1', vendor_code: 'V1', src: { [FF]: 160 }, tgt: { 'Коледино': 160 }, package_type: 'BOX' },
            { nm_id: 2, barcode: 'B2', vendor_code: 'V2', src: { [FF]: 50 }, tgt: { 'Казань': 50 }, package_type: 'MONOPALLET' },
        ];
        const out = rowsToPreDistRows(rows);
        const b1 = out.find(r => r.barcode === 'B1' && r.wb_warehouse_name === 'Коледино');
        expect(b1?.qty).toBe(320); // 160+160 свёрнуто
        expect(b1?.package_type).toBe('BOX');
        const b2 = out.find(r => r.barcode === 'B2');
        expect(b2?.qty).toBe(50);
        expect(b2?.package_type).toBe('MONOPALLET');
    });
});

describe('finalizePoolRows · режим коробами vs целые паллеты', () => {
    const shipped = (rows: AssemblyDraftRow[]) => rows.reduce((s, r) => s + sum(r.tgt), 0);
    const mkSmall = () => {
        const input = mkInput({
            poolRows: [poolRow({ barcode: 'B1', article_wb: '1', available_qty: 100 })],
            stockNeed: mkNeed(
                [{ name: 'Коледино', need: { 1: 30 } }],
                [{ nm_id: 1, barcode: 'B1', vendor_code: 'V1' }],
            ),
        });
        return { input, skus: buildPoolSkus(input).skus };
    };

    it('мелкая потребность: «коробами» отгружает целые коробы, «паллетами» — меньше (обнуляет хвост < паллеты)', () => {
        const { input, skus } = mkSmall();
        const box = shipped(finalizePoolRows(skus, input, false));
        const pallet = shipped(finalizePoolRows(skus, input, true));
        expect(box).toBe(30);          // 3 целых короба по 10 (160 шт/паллета не набирается)
        expect(pallet).toBeLessThan(box);
    });

    it('по умолчанию (без флага) = строго целые паллеты', () => {
        const { input, skus } = mkSmall();
        expect(shipped(finalizePoolRows(skus, input))).toBeLessThan(30);
    });
});

describe('splitByKratnost · россыпь запрещена (SKU без кратности не отгружаем)', () => {
    it('делит по наличию ppb: с кратностью → shippable, без (0/null) → heldNoPpb', () => {
        const skus = [{ nm_id: 1 }, { nm_id: 2 }, { nm_id: 3 }];
        const ppb = new Map<number, number | null>([[1, 10], [2, 0], [3, null]]);
        const { shippable, heldNoPpb } = splitByKratnost(skus, (nm) => ppb.get(nm));
        expect(shippable.map(s => s.nm_id)).toEqual([1]);
        expect(heldNoPpb.map(s => s.nm_id)).toEqual([2, 3]);
    });
});

describe('planBoxTopUp · дозабор кандидатов из ОСТАТКА машины', () => {
    it('маппит nm-кандидата на баркоды с остатком (onHold), целыми коробами, крупнейший первым', () => {
        const pool = [
            poolRow({ barcode: 'B1a', article_wb: '1', available_qty: 100 }),
            poolRow({ barcode: 'B1b', article_wb: '1', available_qty: 40 }),
        ];
        // Разложено: B1a 95 (остаток 5 < короб → исключён), B1b 0 (остаток 40 = 4 короба).
        const alloc = new Map<string, number>([['B1a', 95]]);
        const adds = planBoxTopUp([{ nmId: 1, boxes: 3 }], 'Коледино', pool, alloc, new Map([[1, PPB]]));
        expect(adds).toEqual([{ barcode: 'B1b', wb: 'Коледино', units: 30 }]); // 3 короба = 30, с B1b
    });

    it('весь остаток разложен → дозабирать нечем (пусто)', () => {
        const pool = [poolRow({ barcode: 'B1', article_wb: '1', available_qty: 100 })];
        const alloc = new Map<string, number>([['B1', 100]]);
        expect(planBoxTopUp([{ nmId: 1, boxes: 2 }], 'Коледино', pool, alloc, new Map([[1, PPB]]))).toEqual([]);
    });
});

describe('buildTopUpRows · накопленный дозабор → строки ЦЕЛЫХ коробов', () => {
    it('materializ: barcode→wb→штук в строку BOX, источник = склад машины', () => {
        const pool = [poolRow({ barcode: 'B1', article_wb: '1', article_seller: 'ART1', available_qty: 50 })];
        const manual = new Map<string, Record<string, number>>([['B1', { 'Коледино': 30 }]]);
        const rows = buildTopUpRows(manual, pool, FF, new Map([[1, PPB]]));
        expect(rows).toHaveLength(1);
        expect(rows[0]).toMatchObject({ nm_id: 1, barcode: 'B1', package_type: 'BOX', src: { [FF]: 30 }, tgt: { 'Коледино': 30 } });
    });

    it('кап по остатку машины: не больше available, целыми коробами', () => {
        const pool = [poolRow({ barcode: 'B1', article_wb: '1', available_qty: 25 })]; // 25 → 2 короба
        const manual = new Map<string, Record<string, number>>([['B1', { 'Коледино': 50 }]]); // просят 50
        const rows = buildTopUpRows(manual, pool, FF, new Map([[1, PPB]]));
        expect(sum(rows[0].tgt)).toBe(20); // min(50, floor(25/10)*10) = 20
    });

    it('reserved-кап: топап не пере-подписывает баркод сверх (available − занятое потребностью)', () => {
        const pool = [poolRow({ barcode: 'B1', article_wb: '1', available_qty: 100 })];
        const manual = new Map<string, Record<string, number>>([['B1', { 'Коледино': 90 }]]); // просят 90 (гонка клика)
        const reserved = new Map<string, number>([['B1', 60]]); // потребность уже заняла 60
        const rows = buildTopUpRows(manual, pool, FF, new Map([[1, PPB]]), reserved);
        expect(sum(rows[0].tgt)).toBe(40); // min(90, floor((100-60)/10)*10) = 40 → Σsrc(B1)=60+40=100 ≤ avail
    });
});
