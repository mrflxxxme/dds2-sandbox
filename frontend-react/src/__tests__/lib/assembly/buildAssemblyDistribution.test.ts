import { describe, it, expect } from 'vitest';
import {
    buildDistributionSkus,
    finalizeDistribution,
    seedNewcomerRows,
    computeOnHold,
    needByNmFromStockNeed,
    type AvailabilityOf,
    type DistSku,
    type DistributionGeom,
} from '@/lib/assembly/buildAssemblyDistribution';
import type { AssemblyDraftRow, StockNeedResponse } from '@/types/api';

const sum = (o: Record<string, number>) => Object.values(o).reduce((s, v) => s + v, 0);

const SIZE = '60x40x40';
const PPB = 10;

/** Минимальный StockNeed: per-WB need по nm. */
function mkNeed(warehouses: { name: string; need: Record<number, number> }[]): StockNeedResponse {
    return {
        warehouses: warehouses.map(w => ({
            name: w.name,
            total_need: sum(w.need),
            articles: Object.fromEntries(Object.entries(w.need).map(([nm, need]) => [nm, { need, stock: 0, avg_daily: 0 }])),
        })),
        articles: [], rf_warehouses: [], brands: [], subjects: [], supply_days: 14, analysis_days: 14,
        mode: 'actual', total_warehouses: 0, total_articles: 0,
        summary: { total_need: 0, total_can_send: 0, total_deficit: 0, avg_delivery_days: 0, deficit_count: 0, can_send_count: 0, no_wb_count: 0 },
    } as StockNeedResponse;
}

const GEOM: DistributionGeom = {
    ppbOf: () => PPB,
    boxSizeOf: () => SIZE,
    palletOverrides: {},
};

/** WB-обращённая ФОРМА раскладки: per (nm × упаковка × WB-склад) → штук. Источник (src) НЕ учитываем. */
function wbForm(rows: AssemblyDraftRow[]): Record<string, number> {
    const m: Record<string, number> = {};
    for (const r of rows) {
        for (const [wb, q] of Object.entries(r.tgt)) {
            if ((q || 0) <= 0) continue;
            const k = `${r.nm_id}::${r.package_type ?? 'BOX'}::${wb}`;
            m[k] = (m[k] || 0) + q;
        }
    }
    return m;
}

/** Источники (ФФ-склады) строк раскладки. */
function srcWarehouses(rows: AssemblyDraftRow[]): Set<string> {
    const s = new Set<string>();
    for (const r of rows) for (const ff of Object.keys(r.src)) if ((r.src[ff] || 0) > 0) s.add(ff);
    return s;
}

const SKUS: DistSku[] = [
    { nm_id: 1, barcode: 'B1', vendor_code: 'ART1', is_newcomer: false, available: 10_000 },
];

describe('buildAssemblyDistribution · форма черновик ≡ машина, отличие только источник/кап', () => {
    it('при равном спросе и НЕограничивающей доступности форма ИДЕНТИЧНА (только src разный)', () => {
        const stockNeed = mkNeed([{ name: 'Электросталь', need: { 1: 60 } }, { name: 'Коледино', need: { 1: 40 } }]);

        // Черновик: наш ФФ-сток, мульти-склад, доступности с запасом.
        const draftAvail: AvailabilityOf = () => ({ 501: 500, 502: 500 });
        // Машина: весь остаток пула сидит на ФФ разгрузки (один источник), тоже с запасом.
        const machineAvail: AvailabilityOf = () => ({ 500: 1000 });

        const draftSkus = buildDistributionSkus(SKUS, stockNeed, draftAvail, GEOM);
        const machineSkus = buildDistributionSkus(SKUS, stockNeed, machineAvail, GEOM);
        const draftRows = finalizeDistribution(draftSkus, GEOM, false).rows;
        const machineRows = finalizeDistribution(machineSkus, GEOM, false).rows;

        // Главный инвариант плана: WB-форма раскладки бит-в-бит одинакова.
        expect(wbForm(machineRows)).toEqual(wbForm(draftRows));
        // Спрос кратен коробу → отправляем весь: 100 шт.
        expect(sum(wbForm(machineRows))).toBe(100);
        // Отличие ТОЛЬКО в источнике: машина везёт с ФФ разгрузки (500), черновик — из своего
        // ФФ-пула (501/502; жадный сорсинг целыми коробами может уложиться в один склад).
        expect([...srcWarehouses(machineRows)]).toEqual(['500']);
        const draftSrc = srcWarehouses(draftRows);
        expect(draftSrc.has('500')).toBe(false);
        expect([...draftSrc].every(ff => ff === '501' || ff === '502')).toBe(true);
    });

    it('машинный кап < спроса → машина отправляет меньше черновика; остаток — на источнике (onHold)', () => {
        const stockNeed = mkNeed([{ name: 'Электросталь', need: { 1: 60 } }, { name: 'Коледино', need: { 1: 40 } }]);
        const draftAvail: AvailabilityOf = () => ({ 501: 1000 }); // с запасом
        const machineAvail: AvailabilityOf = () => ({ 500: 30 });  // на машине всего 30 шт

        const draftRows = finalizeDistribution(buildDistributionSkus(SKUS, stockNeed, draftAvail, GEOM), GEOM, false).rows;
        const machineRows = finalizeDistribution(buildDistributionSkus(SKUS, stockNeed, machineAvail, GEOM), GEOM, false).rows;

        expect(sum(wbForm(draftRows))).toBe(100);   // черновик кладёт весь спрос
        expect(sum(wbForm(machineRows))).toBe(30);   // машина — только что есть в пуле (кратно коробу)
        // Остаток на источнике = 0 (весь пул 30 разложен). Проверяем onHold для более крупного пула:
        const onHold = computeOnHold(SKUS, machineAvail, machineRows);
        expect(onHold.get('B1') ?? 0).toBe(0);
    });

    it('целые паллеты: под-паллетный хвост уходит в предбронь (prebook side-output)', () => {
        // Спрос 170 шт при паллете 160 (16 кор × ppb 10): 1 целая паллета едет, 10 — в предбронь.
        const stockNeed = mkNeed([{ name: 'Электросталь', need: { 1: 170 } }]);
        const avail: AvailabilityOf = () => ({ 500: 1000 });
        const skus = buildDistributionSkus(SKUS, stockNeed, avail, GEOM);
        const { rows, prebook } = finalizeDistribution(skus, GEOM, true);
        expect(sum(wbForm(rows))).toBe(160);       // одна целая паллета
        expect(sum(wbForm(prebook))).toBe(10);      // хвост < паллеты → предбронь
        // Инвариант «россыпь запрещена»: всё отгружаемое — целыми коробами (кратно ppb).
        for (const q of Object.values(wbForm(rows))) expect(q % PPB).toBe(0);
    });

    it('extraRows (дозабор из остатка) вливаются ДО нормализации → неполная паллета набирается и уезжает', () => {
        // Спрос 100 шт = 10 коробов < паллеты (160) → без дозабора всё в предбронь.
        const stockNeed = mkNeed([{ name: 'Электросталь', need: { 1: 100 } }]);
        const avail: AvailabilityOf = () => ({ 500: 1000 });
        const skus = buildDistributionSkus(SKUS, stockNeed, avail, GEOM);
        const base = finalizeDistribution(skus, GEOM, true);
        expect(sum(wbForm(base.rows))).toBe(0);        // ничего не набрало паллету
        expect(sum(wbForm(base.prebook))).toBe(100);    // всё в предброни (дозабрать)

        // Дозабор: +60 шт (6 коробов) того же nm на тот же склад → 160 = целая паллета уезжает.
        const extra: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'B1', vendor_code: 'ART1', src: { 500: 60 }, tgt: { 'Электросталь': 60 }, package_type: 'BOX' },
        ];
        const topped = finalizeDistribution(skus, GEOM, true, extra);
        expect(sum(wbForm(topped.rows))).toBe(160);     // целая паллета собралась и уехала
        expect(sum(wbForm(topped.prebook))).toBe(0);      // хвоста в предброни больше нет
    });

    it('новинки: засев целыми коробами по анкерам из доступности, coverage-aware', () => {
        const newcomer: DistSku[] = [
            { nm_id: 9, barcode: 'B9', vendor_code: 'ART9', is_newcomer: true, available: 100 },
        ];
        const avail: AvailabilityOf = () => ({ 500: 100 });
        const anchors = [
            { warehouse: 'Электросталь', share_pct: 50 },
            { warehouse: 'Коледино', share_pct: 50 },
        ];
        const seeded = seedNewcomerRows({
            skus: newcomer,
            anchors,
            availabilityOf: avail,
            shippedByBarcode: new Map(),
            coverageOf: () => 0, // покрытия нет — сеем полностью
            ppbOf: () => PPB,
        });
        const total = seeded.reduce((s, r) => s + sum(r.tgt), 0);
        expect(total).toBeGreaterThan(0);
        expect(total % PPB).toBe(0);                       // целые коробы
        // Источник строки засева — ФФ разгрузки (500).
        expect([...srcWarehouses(seeded)]).toEqual(['500']);
        // Σsrc == Σtgt (инвариант).
        for (const r of seeded) expect(sum(r.src)).toBe(sum(r.tgt));
    });

    it('needByNmFromStockNeed игнорирует нулевую/отрицательную потребность', () => {
        const stockNeed = mkNeed([{ name: 'Электросталь', need: { 1: 0 } }, { name: 'Коледино', need: { 1: 40 } }]);
        const byNm = needByNmFromStockNeed(stockNeed);
        expect(byNm.get(1)).toEqual({ 'Коледино': 40 });
    });
});

describe('buildDistributionSkus — потребность nm раздаётся баркодам РОВНО один раз', () => {
    // Машина: размерные варианты одной карточки — отдельные строки пула (poolToDistSkus
    // не дедупит по nm). Копия ПОЛНОЙ per-nm потребности каждому баркоду планировала
    // до N× спроса — потребность должна партиционироваться между баркодами.
    const MULTI: DistSku[] = [
        { nm_id: 1, barcode: 'B1', vendor_code: 'ART1', is_newcomer: false, available: 200 },
        { nm_id: 1, barcode: 'B2', vendor_code: 'ART1', is_newcomer: false, available: 200 },
    ];
    const availBy = (by: Record<string, Record<number, number>>): AvailabilityOf =>
        (_nm, bc) => by[bc] ?? {};

    it('ёмкости первого баркода хватает → второй не получает потребности вовсе', () => {
        const stockNeed = mkNeed([{ name: 'Коледино', need: { 1: 100 } }]);
        const skus = buildDistributionSkus(MULTI, stockNeed, availBy({ B1: { 500: 200 }, B2: { 500: 200 } }), GEOM);
        expect(skus).toHaveLength(1);
        expect(skus[0].barcode).toBe('B1');
        expect(skus[0].target).toEqual({ 'Коледино': 100 });
    });

    it('дефицит первого баркода → второй добирает ОСТАТОК (Σ target == need, не N×)', () => {
        const stockNeed = mkNeed([{ name: 'Коледино', need: { 1: 100 } }]);
        const skus = buildDistributionSkus(MULTI, stockNeed, availBy({ B1: { 500: 60 }, B2: { 500: 60 } }), GEOM);
        expect(skus).toHaveLength(2);
        expect(skus[0].target).toEqual({ 'Коледино': 60 });
        expect(skus[1].target).toEqual({ 'Коледино': 40 });
    });

    it('план машины с двумя баркодами карточки не превышает потребность склада', () => {
        const stockNeed = mkNeed([{ name: 'Коледино', need: { 1: 100 } }]);
        const skus = buildDistributionSkus(MULTI, stockNeed, availBy({ B1: { 500: 100 }, B2: { 500: 100 } }), GEOM);
        const rows = finalizeDistribution(skus, GEOM, false).rows;
        expect(sum(wbForm(rows))).toBe(100); // до фикса: 200 (каждый баркод планировал полную потребность)
    });

    it('одиночный баркод (черновик): потребность передаётся целиком БЕЗ ёмкостного капа', () => {
        // Кап и форму дефицита (приоритеты «схемы воришек») накладывает buildDraftRows —
        // здесь одиночному представителю nm потребность не режем (поведение прежнее).
        const stockNeed = mkNeed([{ name: 'Электросталь', need: { 1: 60 } }, { name: 'Коледино', need: { 1: 40 } }]);
        const skus = buildDistributionSkus(SKUS, stockNeed, () => ({ 500: 30 }), GEOM);
        expect(skus[0].target).toEqual({ 'Электросталь': 60, 'Коледино': 40 });
    });
});
