/**
 * Прод-кейс draft 62 (2026-07-17): категорийный черновик, направление
 * «Екатеринбург - Перспективная 14» × BOX держал в rows НЕПОЛНУЮ паллету —
 * 5 коробов (90 шт, 5 SKU), превью показывало «1 пал · ⚠ ~50%».
 *
 * Тест фиксирует ПАРИТЕТ фронтовых путей на РЕАЛЬНОЙ прод-геометрии (снята с
 * dds-data 2026-07-17): и write-путь авто-синка матрицы (buildDraftSkus →
 * splitByKratnost → applyAcceptanceSplits → finalizeDraftDistribution), и
 * нормализатор черновика (normalizeDraft), и превью-математика (palletsForLines)
 * считают эти 5 коробов ПОЛ-ПАЛЛЕТОЙ → в rows им не место, только в предбронь.
 *
 * Корень прод-аномалии был НЕ во фронте: серверный дельта-гейт PUT
 * (`_subtract_in_transit`, backend/services/assembly_draft_service.py) вычитал
 * «уже едущее» из целой смешанной паллеты и оставлял остаток направления в
 * rows. Фикс гейта (демоция остатков урезанных направлений в предбронь) — в
 * tests/test_assembly_in_transit.py::test_gate_demotes_partial_direction_remains_to_prebook.
 */
import { describe, expect, it } from 'vitest';
import {
    buildDraftSkus,
    finalizeDraftDistribution,
    splitByKratnost,
    type DraftDistInput,
} from '@/lib/assembly/draftDistribution';
import { applyAcceptanceSplits } from '@/lib/assembly/buildAssemblyDistribution';
import { normalizeDraft } from '@/lib/utils/normalizeDraft';
import { maxPalletHeightCm, palletsForLines } from '@/lib/utils/boxPallet';
import type { AssemblyDraftRow, StockNeedArticle, StockNeedResponse } from '@/types/api';

const EKB = 'Екатеринбург - Перспективная 14';
const TULA = 'Тула';
const FF = 5; // склад «Газпром» (project 4)

// ── Прод-геометрия (dds-data, 2026-07-17) ───────────────────────────────────
// Кратности: BLACKOUT'ы/MRAMOR — машинные (V-0022/V-0026 приняты на Газпром),
// KOSIHKA — ручной per-ФФ override (box_qty_per_warehouse, wh 2/3/5/12).
const PPB: Record<number, number> = {
    889640865: 18, // BLACKOUT_200х240_серый, ШК 2049698944782
    889654227: 18, // BLACKOUT_200х250_светло-серый, 2049699162222
    889627421: 16, // BLACKOUT_200х260_темно-серый, 2049698717805
    355662901: 20, // KOSIHKA_210x90_160x90_бежевый, 2043160330608
    889697279: 18, // MRAMOR_150х250_светло-бежевый, 2049699805778
};
const BOX_SIZE: Record<number, string> = {
    889640865: '60x40x50',
    889654227: '60x40x50',
    889627421: '60x40x50',
    355662901: '60х40х50', // кириллические «х» — как лежит в box_qty_per_warehouse
    889697279: '60x40x50',
};
// project_settings.pallet_boxes_by_size (prod, project 4): канон-ключи латиницей.
const PALLET_OVERRIDES: Record<string, number> = {
    '36x36x80': 11, '60x40x50': 10, '68x28x26': 24, '60x40x40': 16, '60x16x9': 120,
    '62x32x60': 10, '62x32x64': 10, '61x61x51': 6, '62x32x62': 10, '62x32x76': 10,
    '62x32x77': 10, '32x41x95': 6, '63x64x37': 8, '64x64x44': 8,
};
const BARCODES: Record<number, string> = {
    889640865: '2049698944782',
    889654227: '2049699162222',
    889627421: '2049698717805',
    355662901: '2043160330608',
    889697279: '2049699805778',
};

function article(nm: number, avail: number): StockNeedArticle {
    return {
        nm_id: nm,
        vendor_code: `art-${nm}`,
        barcode: BARCODES[nm],
        brand: '',
        subject: '',
        total_need: 0,
        revenue_30d: 0,
        rf_stocks: { [FF]: { stock: avail, available: avail } },
        in_assembly: 0,
        in_transit: 0,
        in_transit_date: null,
        can_send: 0,
        deficit: 0,
        stocks_wb: 0,
    } as unknown as StockNeedArticle;
}

function needResp(needByNm: Record<number, Record<string, number>>): StockNeedResponse {
    const whNames = new Set<string>();
    for (const t of Object.values(needByNm)) for (const w of Object.keys(t)) whNames.add(w);
    return {
        warehouses: [...whNames].map((name) => ({
            name,
            articles: Object.fromEntries(
                Object.entries(needByNm)
                    .filter(([, t]) => (t[name] || 0) > 0)
                    .map(([nm, t]) => [nm, { need: t[name], stock: 0 }]),
            ),
        })),
        articles: [],
    } as unknown as StockNeedResponse;
}

/** Строки прод-аномалии: 5 коробов (по 1 на SKU) на ЕКБ, Σ 90 шт. */
function prodAnomalyRows(): AssemblyDraftRow[] {
    return (Object.keys(PPB) as unknown as number[]).map((nmKey) => {
        const nm = Number(nmKey);
        const qty = PPB[nm];
        return {
            nm_id: nm,
            barcode: BARCODES[nm],
            vendor_code: `art-${nm}`,
            src: { [String(FF)]: qty },
            tgt: { [EKB]: qty },
            package_type: 'BOX' as const,
        };
    });
}

const geomCtx = {
    ppbOf: (nm: number) => PPB[nm] ?? null,
    ppbAt: (nm: number, ffId: number) => (ffId === FF ? PPB[nm] ?? null : null),
    boxSizeOf: (nm: number) => BOX_SIZE[nm] ?? null,
    overrides: PALLET_OVERRIDES,
    classOf: () => '*',
};

describe('прод-кейс draft 62: пол-паллеты (5 коробов) на Екатеринбург', () => {
    it('write-путь авто-синка кладёт под-паллетное направление в ПРЕДБРОНЬ, не в rows', () => {
        const input: DraftDistInput = {
            articles: [
                article(889640865, 18), article(889654227, 18), article(889627421, 16),
                article(355662901, 40), article(889697279, 18),
            ],
            stockNeed: needResp({
                889640865: { [EKB]: 18 },
                889654227: { [EKB]: 18 },
                889627421: { [EKB]: 16 },
                355662901: { [EKB]: 20, [TULA]: 20 },
                889697279: { [EKB]: 18 },
            }),
            nmPpb: new Map(Object.entries(PPB).map(([nm, p]) => [Number(nm), p])),
            nmBoxSize: new Map(Object.entries(BOX_SIZE).map(([nm, s]) => [Number(nm), s])),
            palletOverrides: PALLET_OVERRIDES,
            newcomerSet: new Set(),
            ppbAt: geomCtx.ppbAt,
            classOf: geomCtx.classOf,
        };
        // Зеркало DraftMatrixView.computeDistribution (авто-синк): целые паллеты, ≥1 короб.
        const { skus: allSkus } = buildDraftSkus(input);
        const { shippable: autoSkus } = splitByKratnost(allSkus, (nm) => input.nmPpb.get(nm));
        const effective = applyAcceptanceSplits(autoSkus, null);
        const whole = finalizeDraftDistribution(effective, input, true, [], true);

        // 5 коробов = 0.5 паллеты (bpp=10 по оверрайду «60x40x50») → rows ПУСТЫ.
        expect(whole.rows).toEqual([]);
        const prebookUnits = whole.prebook.reduce(
            (s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0,
        );
        expect(prebookUnits).toBe(90 + 20); // ЕКБ 90 + Тула 20 — оба направления < паллеты
    });

    it('normalizeDraft (сейв/self-heal) демотирует прод-аномалию целиком', () => {
        const norm = normalizeDraft(prodAnomalyRows(), { ...geomCtx, freeByNm: {} });
        expect(norm.rows).toEqual([]);
        const droppedUnits = norm.dropped.reduce(
            (s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0,
        );
        expect(droppedUnits).toBe(90);
    });

    it('превью-футпринт согласен: 5 коробов = 0.5 паллеты («1 пал · ⚠ ~50%»)', () => {
        const lines = prodAnomalyRows().map((r) => ({
            units: r.tgt[EKB],
            boxQty: PPB[r.nm_id],
            boxSize: BOX_SIZE[r.nm_id],
            group: '*',
        }));
        const count = palletsForLines(lines, maxPalletHeightCm(EKB), 'box', PALLET_OVERRIDES);
        expect(count.fill).toBeCloseTo(0.5, 9);
        expect(count.pallets).toBe(1); // дисплей: «1 пал», заполненность ~50%
        expect(count.unknownLines).toBe(0);
    });
});
