import { describe, it, expect } from 'vitest';
import { buildDraftRows, type DraftSkuInput } from '@/lib/assembly/buildDraftRows';

/** Сумма значений Record<*, number>. */
const sum = (o: Record<string, number>) => Object.values(o).reduce((s, v) => s + v, 0);

// 60×40×40 @ дефолтной высоте (180) → 16 кор/паллету; ppb=10 → 160 шт/паллету.
const SIZE = '60x40x40';
const PPB = 10;

/** Базовый SKU-builder. */
function sku(over: Partial<DraftSkuInput> = {}): DraftSkuInput {
    return {
        nm_id: 1, barcode: 'B1', vendor_code: 'V1',
        target: { 'Коледино': 160 },
        ffStock: { 100: 1000 },
        ppb: PPB, box_size: SIZE,
        packageType: 'BOX',
        ...over,
    };
}

describe('buildDraftRows · инвариант Σsrc==Σtgt', () => {
    it('каждая строка сбалансирована (Σsrc === Σtgt)', () => {
        const rows = buildDraftRows({
            skus: [
                sku(),
                sku({ nm_id: 2, barcode: 'B2', vendor_code: 'V2', target: { 'Краснодар': 320 }, ffStock: { 100: 500 } }),
            ],
            wholePalletsOnly: false,
        });
        expect(rows.length).toBeGreaterThan(0);
        for (const r of rows) {
            expect(sum(r.src)).toBe(sum(r.tgt));
        }
    });

    it('моно/сейф типы тоже сбалансированы', () => {
        const rows = buildDraftRows({
            skus: [sku({
                target: { 'Коледино': 100, 'Краснодар': 50 },
                packageByWh: { 'Коледино': 'BOX', 'Краснодар': 'MONOPALLET' },
                ffStock: { 100: 1000 },
            })],
            wholePalletsOnly: false,
        });
        for (const r of rows) expect(sum(r.src)).toBe(sum(r.tgt));
    });
});

describe('buildDraftRows · кратность короба', () => {
    it('box-режим: все tgt-cells кратны ppb', () => {
        const rows = buildDraftRows({
            skus: [sku({ target: { 'Коледино': 95, 'Краснодар': 47 } })],
            wholePalletsOnly: false,
        });
        for (const r of rows) {
            if (r.package_type === 'BOX') {
                for (const q of Object.values(r.tgt)) expect(q % PPB).toBe(0);
            }
        }
    });

    it('Σ кратно ppb в box-режиме', () => {
        const rows = buildDraftRows({
            skus: [sku({ target: { 'Коледино': 333 }, ffStock: { 100: 1000 } })],
            wholePalletsOnly: false,
        });
        const box = rows.filter(r => r.package_type === 'BOX');
        for (const r of box) expect(sum(r.tgt) % PPB).toBe(0);
    });
});

describe('buildDraftRows · cap по ФФ-стоку', () => {
    it('Σsrc никогда не превышает доступный ФФ-сток', () => {
        const rows = buildDraftRows({
            skus: [sku({ target: { 'Коледино': 1000 }, ffStock: { 100: 250 } })],
            wholePalletsOnly: false,
        });
        const srcByFf: Record<string, number> = {};
        for (const r of rows) for (const [id, q] of Object.entries(r.src)) srcByFf[id] = (srcByFf[id] || 0) + q;
        expect(srcByFf['100'] ?? 0).toBeLessThanOrEqual(250);
    });

    it('несколько ФФ — суммарный source ≤ суммарного стока, по каждому ФФ ≤ его остатка', () => {
        const rows = buildDraftRows({
            skus: [sku({ target: { 'Коледино': 500 }, ffStock: { 100: 200, 200: 130 } })],
            wholePalletsOnly: false,
        });
        const srcByFf: Record<string, number> = {};
        for (const r of rows) for (const [id, q] of Object.entries(r.src)) srcByFf[id] = (srcByFf[id] || 0) + q;
        expect(srcByFf['100'] ?? 0).toBeLessThanOrEqual(200);
        expect(srcByFf['200'] ?? 0).toBeLessThanOrEqual(130);
        expect(sum(srcByFf)).toBeLessThanOrEqual(330);
    });

    it('source жадно с самого крупного ФФ первым', () => {
        // need 160 (1 паллета), ФФ: 200=300, 100=100 → 160 целиком с крупнейшего (200).
        const rows = buildDraftRows({
            skus: [sku({ target: { 'Коледино': 160 }, ffStock: { 100: 100, 200: 300 } })],
            wholePalletsOnly: false,
        });
        const box = rows.find(r => r.package_type === 'BOX')!;
        expect(box.src['200']).toBe(160);
        expect(box.src['100']).toBeUndefined();
    });
});

describe('buildDraftRows · целые коробы (паллеты режет нормализатор отдельно)', () => {
    it('под-паллетный объём НЕ срезается здесь — отдаётся целыми коробами', () => {
        // need 250 = 25 коробов (1.56 паллеты). buildDraftRows больше НЕ режет до целых
        // паллет (это делает normalizeDraft per-shipment) → отдаёт 250 целыми коробами.
        const rows = buildDraftRows({
            skus: [sku({ target: { 'Коледино': 250 }, ffStock: { 100: 1000 } })],
        });
        const box = rows.find(r => r.package_type === 'BOX')!;
        expect(box).toBeDefined();
        expect(sum(box.tgt)).toBe(250);
        expect(sum(box.tgt) % PPB).toBe(0);        // целые коробы
        expect(sum(box.src)).toBe(sum(box.tgt));   // инвариант держится
    });

    it('меньше целой паллеты — строка ОСТАЁТСЯ (целые коробы), не дропается', () => {
        // 90 = 9 коробов < паллеты. Раньше wholePalletsOnly дропал → теперь строка живёт
        // целыми коробами; срез до целых паллет — уже в нормализаторе.
        const rows = buildDraftRows({
            skus: [sku({ target: { 'Коледино': 90 }, ffStock: { 100: 1000 } })],
        });
        const box = rows.find(r => r.package_type === 'BOX')!;
        expect(box).toBeDefined();
        expect(sum(box.tgt)).toBe(90);
        expect(sum(box.tgt) % PPB).toBe(0);
    });

    it('не-палетизируемый box SKU держит полный box-кратный объём', () => {
        const rows = buildDraftRows({
            skus: [sku({ box_size: null, target: { 'Коледино': 250 }, ffStock: { 100: 1000 } })],
        });
        const box = rows.find(r => r.package_type === 'BOX')!;
        expect(sum(box.tgt)).toBe(250);
        expect(sum(box.tgt) % PPB).toBe(0);
        expect(sum(box.src)).toBe(sum(box.tgt));
    });
});

describe('buildDraftRows · пустые / нулевые входы', () => {
    it('пустой список SKU → []', () => {
        expect(buildDraftRows({ skus: [] })).toEqual([]);
    });

    it('нулевая потребность → []', () => {
        expect(buildDraftRows({ skus: [sku({ target: {} })] })).toEqual([]);
        expect(buildDraftRows({ skus: [sku({ target: { 'Коледино': 0 } })] })).toEqual([]);
    });

    it('нулевой ФФ-сток → []', () => {
        expect(buildDraftRows({ skus: [sku({ ffStock: {} })] })).toEqual([]);
        expect(buildDraftRows({ skus: [sku({ ffStock: { 100: 0 } })] })).toEqual([]);
    });

    it('нет габаритов (россыпь) — балансирует поштучно, не падает', () => {
        const rows = buildDraftRows({
            skus: [sku({ box_size: null, ppb: null, target: { 'Коледино': 37 }, ffStock: { 100: 1000 } })],
            wholePalletsOnly: false,
        });
        for (const r of rows) expect(sum(r.src)).toBe(sum(r.tgt));
        expect(sum(rows[0]?.tgt ?? {})).toBe(37);
    });
});

describe('buildDraftRows · содержимое строки', () => {
    it('переносит nm_id / barcode / vendor_code и package_type', () => {
        const rows = buildDraftRows({
            skus: [sku({ nm_id: 777, barcode: 'BC', vendor_code: 'VC' })],
            wholePalletsOnly: false,
        });
        const r = rows[0];
        expect(r.nm_id).toBe(777);
        expect(r.barcode).toBe('BC');
        expect(r.vendor_code).toBe('VC');
        expect(r.package_type).toBe('BOX');
    });

    it('per-WB package map разводит строки по типам упаковки', () => {
        const rows = buildDraftRows({
            skus: [sku({
                target: { 'Коледино': 160, 'Краснодар': 90 },
                packageByWh: { 'Коледино': 'BOX', 'Краснодар': 'MONOPALLET' },
                ffStock: { 100: 1000 },
            })],
            wholePalletsOnly: false,
        });
        const types = new Set(rows.map(r => r.package_type));
        expect(types.has('BOX')).toBe(true);
        expect(types.has('MONOPALLET')).toBe(true);
    });
});

describe('buildDraftRows · разные баркоды одного nm_id не схлопываются', () => {
    it('два баркода одной карточки (один nm_id) → две раздельные строки со своим qty', () => {
        const rows = buildDraftRows({
            skus: [
                sku({ nm_id: 7, barcode: 'BC-A', vendor_code: 'V-A', target: { 'Коледино': 160 }, ffStock: { 100: 1000 } }),
                sku({ nm_id: 7, barcode: 'BC-B', vendor_code: 'V-B', target: { 'Коледино': 160 }, ffStock: { 101: 1000 } }),
            ],
            wholePalletsOnly: false,
        });
        const byBc = new Map(rows.map(r => [r.barcode, r]));
        expect(byBc.has('BC-A')).toBe(true);
        expect(byBc.has('BC-B')).toBe(true);
        expect(sum(byBc.get('BC-A')!.tgt)).toBe(160);
        expect(sum(byBc.get('BC-B')!.tgt)).toBe(160);
        for (const r of rows) expect(sum(r.src)).toBe(sum(r.tgt));
    });
});

describe('buildDraftRows · ВСЕГДА целые коробы (округление вверх из ФФ)', () => {
    it('неполный короб добивается ВВЕРХ из свободного ФФ (б/габ box-SKU)', () => {
        // box_size=null → раньше хвост ехал россыпью (95 = 90 + 5). Теперь добивается до 100.
        const rows = buildDraftRows({
            skus: [sku({ box_size: null, target: { 'Коледино': 95 }, ffStock: { 100: 1000 } })],
            wholePalletsOnly: false,
        });
        const r = rows.find(x => x.package_type === 'BOX')!;
        expect(r.tgt['Коледино']).toBe(100);          // добито до целого короба
        expect(sum(r.tgt) % PPB).toBe(0);             // без россыпи
        expect(sum(r.src)).toBe(sum(r.tgt));          // баланс держится
    });

    it('не хватает ФФ на полный короб → срез ВНИЗ (без россыпи)', () => {
        const rows = buildDraftRows({
            skus: [sku({ box_size: null, target: { 'Коледино': 95 }, ffStock: { 100: 95 } })],
            wholePalletsOnly: false,
        });
        const r = rows.find(x => x.package_type === 'BOX')!;
        expect(r.tgt['Коледино']).toBe(90);           // нет ФФ добить → срез вниз
        expect(sum(r.tgt) % PPB).toBe(0);
        expect(sum(r.src)).toBe(sum(r.tgt));
    });

    it('все box-cells кратны ppb на разнородном наборе (нет россыпи)', () => {
        const rows = buildDraftRows({
            skus: [
                sku({ box_size: null, target: { 'Коледино': 95, 'Краснодар': 47 }, ffStock: { 100: 1000 } }),
                sku({ nm_id: 2, barcode: 'B2', vendor_code: 'V2', box_size: null, target: { 'Казань': 133 }, ffStock: { 100: 1000 } }),
            ],
            wholePalletsOnly: false,
        });
        for (const r of rows.filter(x => x.package_type === 'BOX')) {
            for (const q of Object.values(r.tgt)) expect(q % PPB).toBe(0);
            expect(sum(r.src)).toBe(sum(r.tgt));
        }
    });
});
