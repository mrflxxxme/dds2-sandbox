/**
 * Двусторонний клапан авто-синка (прод-кейс 2026-07-22: 5 целых паллет
 * апл→Екатеринбург застряли в предброни при «приём открыт · 14 дн» — демоция
 * в предбронь была, обратной промоции у фоновых акторов не было).
 */
import { describe, expect, it } from 'vitest';
import {
    buildFreePoolByNm,
    makeAutoSyncNormalizePair,
    readyPkgWbsFromAvailability,
} from '@/lib/assembly/autoSyncNormalize';
import type { NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';
import type { AcceptanceFlags, AssemblyDraftRow, PackageType, StockNeedArticle } from '@/types/api';

const row = (nm: number, tgt: Record<string, number>, src: Record<string, number>, pkg: PackageType = 'BOX'): AssemblyDraftRow =>
    ({ nm_id: nm, barcode: `bc${nm}`, vendor_code: `art-${nm}`, src, tgt, package_type: pkg });

// ppb 10, короб 60x40x50, override 10 кор/пал → паллета = 100 шт.
const ctx: NormalizeDraftCtx = {
    ppbOf: () => 10,
    boxSizeOf: () => '60x40x50',
    overrides: { '60x40x50': 10 },
};

const article = (nm: number, rf: Record<number, number>): StockNeedArticle => ({
    nm_id: nm,
    vendor_code: `art-${nm}`,
    barcode: `bc${nm}`,
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
});

describe('makeAutoSyncNormalizePair — обратная промоция предброни', () => {
    it('целая паллета ГОТОВОГО направления уезжает из предброни в rows, хвост остаётся', () => {
        const pair = makeAutoSyncNormalizePair(ctx, [], new Set(['BOX::Тула']));
        const out = pair([], [
            row(1, { 'Тула': 150 }, { '5': 150 }),   // 1.5 паллеты, направление готово
            row(2, { 'Казань': 150 }, { '5': 150 }), // готовности нет — держим целиком
        ]);
        const sum = (rs: AssemblyDraftRow[], wb: string) => rs.reduce((s, r) => s + (r.tgt[wb] || 0), 0);
        expect(sum(out.rows, 'Тула')).toBe(100);      // целая паллета промотирована
        expect(sum(out.prebook, 'Тула')).toBe(50);    // под-паллетный хвост остался
        expect(sum(out.rows, 'Казань')).toBe(0);      // «нет данных ≠ готов»
        expect(sum(out.prebook, 'Казань')).toBe(150);
    });

    it('фикс-поинт: повторный прогон от собственного результата ничего не меняет', () => {
        const pair = makeAutoSyncNormalizePair(ctx, [], new Set(['BOX::Тула']));
        const first = pair([], [row(1, { 'Тула': 150 }, { '5': 150 })]);
        const second = pair(first.rows, first.prebook);
        const total = (o: { rows: AssemblyDraftRow[]; prebook: AssemblyDraftRow[] }) => ({
            rows: o.rows.reduce((s, r) => s + (r.tgt['Тула'] || 0), 0),
            prebook: o.prebook.reduce((s, r) => s + (r.tgt['Тула'] || 0), 0),
        });
        expect(total(second)).toEqual(total(first));
    });

    it('реальный пул ФФ: некратный хвост rows добирается до целого короба, а не срезается', () => {
        // 95 шт = 9.5 кор; available=100 (95 уже занято планом + 5 свободно) —
        // добор до целого короба → 100 шт (= целая паллета) остаются в rows.
        // С пустым пулом хвост был бы срезан.
        const pair = makeAutoSyncNormalizePair(ctx, [article(1, { 5: 100 })], new Set(['BOX::Тула']));
        const out = pair([row(1, { 'Тула': 95 }, { '5': 95 })], []);
        expect(out.rows.reduce((s, r) => s + (r.tgt['Тула'] || 0), 0)).toBe(100);
        expect(out.prebook).toHaveLength(0);
    });
});

describe('buildFreePoolByNm', () => {
    it('вычитает занятое переданным планом из available', () => {
        const pool = buildFreePoolByNm(
            [article(1, { 5: 100 })],
            [row(1, { 'Тула': 60 }, { '5': 60 })],
            [row(1, { 'Казань': 30 }, { '5': 30 })],
        );
        expect(pool[1]?.[5]).toBe(10); // 100 − 60 − 30
    });
});

describe('readyPkgWbsFromAvailability', () => {
    it('готово = тип принимается И дни (free+paid) > 0; can без meta — не готово', () => {
        const flags: Record<string, AcceptanceFlags> = {
            'Тула': {
                warehouse_id: 1,
                can_box: true,
                can_monopallet: true,
                can_supersafe: false,
                box_meta: { free_days_14: 3, paid_days_14: 0, min_coefficient: 0 },
                mono_meta: null, // coefficients не загрузились — «нет данных ≠ готов»
            },
            'Казань': {
                warehouse_id: 2,
                can_box: false, // приём закрыт
                can_monopallet: true,
                can_supersafe: false,
                mono_meta: { free_days_14: 0, paid_days_14: 2, min_coefficient: 2 },
            },
        };
        const ready = readyPkgWbsFromAvailability([flags]);
        expect(ready).toEqual(new Set(['BOX::Тула', 'MONOPALLET::Казань']));
    });
});
