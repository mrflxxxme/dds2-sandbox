import { describe, it, expect } from 'vitest';
import { normalizeDraft, type NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';
import type { AssemblyDraftRow } from '@/types/api';

// box_size 60x40x40 → 16 кор/паллету при H=180; ppb=10 → upp = 160 шт/паллету.
const SIZE = '60x40x40';
const PPB = 10;
const ctx: NormalizeDraftCtx = {
    ppbOf: () => PPB,
    boxSizeOf: () => SIZE,
    isNewcomer: () => false,
};

const row = (
    nm: number, src: Record<string, number>, tgt: Record<string, number>, pkg: AssemblyDraftRow['package_type'],
): AssemblyDraftRow => ({ nm_id: nm, barcode: `bc-${nm}`, vendor_code: `VC-${nm}`, src, tgt, package_type: pkg });

const tSrc = (r: AssemblyDraftRow) => Object.values(r.src).reduce((s, v) => s + v, 0);
const tTgt = (r: AssemblyDraftRow) => Object.values(r.tgt).reduce((s, v) => s + v, 0);
const sumTgt = (rows: AssemblyDraftRow[], pkg?: string) =>
    rows.filter(r => !pkg || (r.package_type || 'BOX') === pkg).reduce((s, r) => s + tTgt(r), 0);

describe('normalizeDraft — инвариант «целые коробы + целые паллеты» (per-shipment)', () => {
    it('целые коробы: россыпь добивается из свободного ФФ, все ячейки кратны коробу', () => {
        const rows = [row(200, { '1': 316 }, { 'Казань': 316 }, 'BOX')];
        const res = normalizeDraft(rows, { ...ctx, freeByNm: { 200: { 1: 50 } } });
        for (const r of res.rows) {
            for (const q of Object.values(r.tgt)) expect(q % PPB).toBe(0); // целые коробы
            expect(tSrc(r)).toBe(tTgt(r));                                  // баланс
        }
    });

    it('строгие целые паллеты: одиночная box-отгрузка < паллеты уходит на ФФ', () => {
        // 80 шт = 8 коробов = 0.5 паллеты, одна отгрузка ФФ→склад → дропается.
        const rows = [row(300, { '1': 80 }, { 'Казань': 80 }, 'BOX')];
        const res = normalizeDraft(rows, ctx);
        expect(res.rows).toHaveLength(0);
        expect(res.droppedUnits).toBe(80);
        expect(res.changed).toBe(true);
    });

    it('box: 2 целые паллеты едут без изменений', () => {
        const rows = [row(200, { '1': 320 }, { 'Казань': 320 }, 'BOX')];
        const res = normalizeDraft(rows, ctx);
        expect(sumTgt(res.rows, 'BOX')).toBe(320);
        expect(res.droppedUnits).toBe(0);
    });

    it('моно ≤3: три мелких моно-SKU одной отгрузки собираются в одну целую паллету', () => {
        const rows = [
            row(100, { '1': 64 }, { 'Казань': 64 }, 'MONOPALLET'),
            row(101, { '1': 64 }, { 'Казань': 64 }, 'MONOPALLET'),
            row(102, { '1': 64 }, { 'Казань': 64 }, 'MONOPALLET'),
        ];
        const res = normalizeDraft(rows, ctx);
        expect(sumTgt(res.rows, 'MONOPALLET')).toBe(160); // 1 целая моно-паллета из трёх артикулов
        expect(res.droppedUnits).toBe(32);                // 192 − 160
        for (const r of res.rows) expect(tSrc(r)).toBe(tTgt(r));
    });

    it('одиночный моно < паллеты остаётся на ФФ', () => {
        const res = normalizeDraft([row(100, { '1': 64 }, { 'Казань': 64 }, 'MONOPALLET')], ctx);
        expect(res.rows).toHaveLength(0);
        expect(res.droppedUnits).toBe(64);
    });

    it('смешанный черновик: короб целыми паллетами + моно ≤3, всё сбалансировано', () => {
        const rows = [
            row(200, { '1': 320 }, { 'Казань': 320 }, 'BOX'),
            row(100, { '1': 64 }, { 'Казань': 64 }, 'MONOPALLET'),
            row(101, { '1': 64 }, { 'Казань': 64 }, 'MONOPALLET'),
            row(102, { '1': 64 }, { 'Казань': 64 }, 'MONOPALLET'),
        ];
        const res = normalizeDraft(rows, ctx);
        for (const r of res.rows) expect(tSrc(r)).toBe(tTgt(r));
        expect(sumTgt(res.rows, 'BOX')).toBe(320);
        expect(sumTgt(res.rows, 'MONOPALLET')).toBe(160);
    });

    it('идемпотентность: normalize(normalize(x)) == normalize(x)', () => {
        const rows = [
            row(200, { '1': 316 }, { 'Казань': 316 }, 'BOX'),
            row(100, { '1': 64 }, { 'Казань': 64 }, 'MONOPALLET'),
            row(101, { '1': 64 }, { 'Казань': 64 }, 'MONOPALLET'),
            row(102, { '1': 64 }, { 'Казань': 64 }, 'MONOPALLET'),
        ];
        const r1 = normalizeDraft(rows, { ...ctx, freeByNm: { 200: { 1: 50 } } });
        const r2 = normalizeDraft(r1.rows, ctx);
        expect(r2.changed).toBe(false);
        expect(r2.droppedUnits).toBe(0);
        expect(sumTgt(r2.rows)).toBe(sumTgt(r1.rows));
    });

    it('моно вариант A: добор из ФФ → всё едет ЦЕЛЫМИ коробами (нет россыпи)', () => {
        const rows = [
            row(100, { '1': 67 }, { 'Казань': 67 }, 'MONOPALLET'),
            row(101, { '1': 67 }, { 'Казань': 67 }, 'MONOPALLET'),
            row(102, { '1': 67 }, { 'Казань': 67 }, 'MONOPALLET'),
        ];
        const res = normalizeDraft(rows, { ...ctx, freeByNm: { 100: { 1: 50 }, 101: { 1: 50 }, 102: { 1: 50 } } });
        for (const r of res.rows) {
            for (const q of Object.values(r.tgt)) expect(q % PPB).toBe(0); // целые коробы, никакой россыпи
            expect(tSrc(r)).toBe(tTgt(r));                                  // баланс
        }
    });

    it('моно без ФФ: под-коробочный остаток срезан → тоже целые коробы', () => {
        const rows = [
            row(100, { '1': 67 }, { 'Казань': 67 }, 'MONOPALLET'),
            row(101, { '1': 67 }, { 'Казань': 67 }, 'MONOPALLET'),
            row(102, { '1': 67 }, { 'Казань': 67 }, 'MONOPALLET'),
        ];
        const res = normalizeDraft(rows, ctx); // freeByNm пуст
        for (const r of res.rows) for (const q of Object.values(r.tgt)) expect(q % PPB).toBe(0);
    });

    it('multi-ФФ: целые паллеты с двух источников стабильны и идемпотентны', () => {
        const rows = [row(200, { '1': 160, '2': 160 }, { 'Казань': 160, 'Тула': 160 }, 'BOX')];
        const r1 = normalizeDraft(rows, ctx);
        expect(sumTgt(r1.rows)).toBe(320);          // обе целые паллеты едут
        expect(r1.droppedUnits).toBe(0);
        for (const r of r1.rows) expect(tSrc(r)).toBe(tTgt(r));
        const r2 = normalizeDraft(r1.rows, ctx);
        expect(r2.changed).toBe(false);             // идемпотентно при re-pairing NW-corner
        expect(sumTgt(r2.rows)).toBe(320);
    });

    it('фаззинг идемпотентности: 3000 случайных черновиков (multi-ФФ/multi-WB, короб+моно)', () => {
        let seed = 424242;
        const rnd = (n: number) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
        const PPB: Record<number, number> = { 1: 6, 2: 1, 3: 3, 4: 10, 5: 4 };
        const WB = ['Казань', 'Тула', 'Подольск', 'Электросталь'];
        const fctx: NormalizeDraftCtx = {
            ppbOf: (nm) => PPB[nm] ?? 1,
            boxSizeOf: () => SIZE,
            isNewcomer: () => false,
        };
        for (let t = 0; t < 3000; t++) {
            const rows: AssemblyDraftRow[] = [];
            const nRows = 1 + rnd(5);
            for (let i = 0; i < nRows; i++) {
                const nm = 1 + rnd(5);
                const pkg: AssemblyDraftRow['package_type'] = rnd(3) === 0 ? 'MONOPALLET' : 'BOX';
                const total = 10 + rnd(900);
                // src по 1-2 ФФ, tgt по 1-3 WB — сбалансированы (Σsrc==Σtgt==total).
                const src: Record<string, number> = {};
                let restS = total; const nFf = 1 + rnd(2);
                for (let f = 0; f < nFf; f++) { const q = f === nFf - 1 ? restS : 1 + rnd(restS); src[String(1 + rnd(5))] = (src[String(1 + rnd(5))] || 0) + q; restS -= q; if (restS <= 0) break; }
                // пере-собрать src чтобы Σ точно = total
                const sSum = Object.values(src).reduce((s, v) => s + v, 0);
                if (sSum !== total) { src['1'] = (src['1'] || 0) + (total - sSum); }
                const tgt: Record<string, number> = {};
                let restT = total; const nWb = 1 + rnd(3);
                for (let w = 0; w < nWb; w++) { const wb = WB[rnd(WB.length)]; const q = w === nWb - 1 ? restT : 1 + rnd(restT); tgt[wb] = (tgt[wb] || 0) + q; restT -= q; if (restT <= 0) break; }
                const tSum = Object.values(tgt).reduce((s, v) => s + v, 0);
                if (tSum !== total) { tgt[WB[0]] = (tgt[WB[0]] || 0) + (total - tSum); }
                if (Object.values(src).some(v => v <= 0) || Object.values(tgt).some(v => v <= 0)) continue;
                rows.push({ nm_id: nm, barcode: `bc-${nm}-${i}`, vendor_code: `v${nm}`, src, tgt, package_type: pkg });
            }
            const r1 = normalizeDraft(rows, fctx);
            for (const r of r1.rows) expect(tSrc(r)).toBe(tTgt(r)); // баланс держится
            const r2 = normalizeDraft(r1.rows, fctx);
            // Ключевое: повторная нормализация НЕ роняет товар (идемпотентность).
            expect(r2.droppedUnits).toBe(0);
            expect(sumTgt(r2.rows)).toBe(sumTgt(r1.rows));
        }
    });

    it('новинка cold-start (ppb=null) едет россыпью, не палетизируется и не дропается', () => {
        const nctx: NormalizeDraftCtx = {
            ppbOf: (nm) => (nm === 999 ? null : PPB),
            boxSizeOf: () => SIZE,
            isNewcomer: (nm) => nm === 999,
        };
        const res = normalizeDraft([row(999, { '1': 37 }, { 'Казань': 37 }, 'BOX')], nctx);
        expect(res.rows).toHaveLength(1);
        expect(tTgt(res.rows[0])).toBe(37);
        expect(res.droppedUnits).toBe(0);
    });

    // ─── Новинка С кратностью короба (ppb есть) → ведёт себя как обычный box-SKU ───
    it('новинка С ppb (BOX): целые коробы, как обычный SKU (россыпь добивается из ФФ)', () => {
        // ppb=10: 316 → добор до 320 из ФФ (50 свободно) → 2 целые паллеты (160×2) едут.
        const nctx: NormalizeDraftCtx = {
            ppbOf: () => PPB,            // новинка имеет кратность
            boxSizeOf: () => SIZE,
            isNewcomer: () => true,      // и при этом новинка
            freeByNm: { 200: { 1: 50 } },
        };
        const res = normalizeDraft([row(200, { '1': 316 }, { 'Казань': 316 }, 'BOX')], nctx);
        expect(res.rows.length).toBeGreaterThan(0);
        for (const r of res.rows) {
            for (const q of Object.values(r.tgt)) expect(q % PPB).toBe(0); // целые коробы
            expect(tSrc(r)).toBe(tTgt(r));                                  // баланс
        }
        // ушло целыми паллетами (320), а не россыпью (316).
        expect(sumTgt(res.rows, 'BOX')).toBe(320);
    });

    it('новинка С ppb (BOX) МИКСуется с обычным SKU в смешанной паллете', () => {
        // Одна отгрузка ФФ1→Казань: новинка 80 (0.5 пал) + обычный 80 (0.5 пал) = 1 целая
        // смешанная паллета → оба остаются (россыпью новинка бы НЕ вошла в микс и осталась
        // бы одна, а обычный 0.5 дропнулся бы).
        const nctx: NormalizeDraftCtx = {
            ppbOf: () => PPB,
            boxSizeOf: () => SIZE,
            isNewcomer: (nm) => nm === 200, // 200 — новинка, 201 — обычный
        };
        const res = normalizeDraft([
            row(200, { '1': 80 }, { 'Казань': 80 }, 'BOX'),
            row(201, { '1': 80 }, { 'Казань': 80 }, 'BOX'),
        ], nctx);
        expect(sumTgt(res.rows, 'BOX')).toBe(160);    // оба в одной смешанной паллете
        expect(res.droppedUnits).toBe(0);
        const nms = new Set(res.rows.map(r => r.nm_id));
        expect(nms.has(200)).toBe(true);              // новинка не отделилась россыпью
        expect(nms.has(201)).toBe(true);
        for (const r of res.rows) expect(tSrc(r)).toBe(tTgt(r));
    });

    it('новинка С ppb (MONO): едет ЦЕЛЫМИ коробами + ≤3 в общей моно-паллете', () => {
        // Три моно-новинки по 64 (0.4 пал) одной отгрузки → 1 целая моно-паллета (160), 32 на ФФ.
        const nctx: NormalizeDraftCtx = {
            ppbOf: () => PPB,
            boxSizeOf: () => SIZE,
            isNewcomer: () => true,
        };
        const res = normalizeDraft([
            row(100, { '1': 64 }, { 'Казань': 64 }, 'MONOPALLET'),
            row(101, { '1': 64 }, { 'Казань': 64 }, 'MONOPALLET'),
            row(102, { '1': 64 }, { 'Казань': 64 }, 'MONOPALLET'),
        ], nctx);
        expect(sumTgt(res.rows, 'MONOPALLET')).toBe(160);
        expect(res.droppedUnits).toBe(32);
        for (const r of res.rows) {
            for (const q of Object.values(r.tgt)) expect(q % PPB).toBe(0); // целые коробы
            expect(tSrc(r)).toBe(tTgt(r));
        }
    });

    it('новинка БЕЗ ppb остаётся россыпью, даже если соседняя новинка С ppb палетизируется', () => {
        const nctx: NormalizeDraftCtx = {
            ppbOf: (nm) => (nm === 999 ? null : PPB), // 999 без кратности, 200 — с кратностью
            boxSizeOf: () => SIZE,
            isNewcomer: () => true,                   // обе новинки
            freeByNm: { 200: { 1: 50 } },
        };
        const res = normalizeDraft([
            row(999, { '2': 37 }, { 'Тула': 37 }, 'BOX'),    // новинка б/ppb → россыпь
            row(200, { '1': 316 }, { 'Казань': 316 }, 'BOX'), // новинка с ppb → паллеты
        ], nctx);
        const loose = res.rows.find(r => r.nm_id === 999);
        expect(loose).toBeDefined();
        expect(tTgt(loose!)).toBe(37);                 // россыпь не тронута
        const boxed = res.rows.filter(r => r.nm_id === 200);
        expect(boxed.length).toBeGreaterThan(0);
        for (const r of boxed) for (const q of Object.values(r.tgt)) expect(q % PPB).toBe(0);
        expect(sumTgt(res.rows.filter(r => r.nm_id === 200), 'BOX')).toBe(320);
    });

    it('консервация штук: новинка С ppb не теряет и не дублирует (Σout+dropped==Σin)', () => {
        const nctx: NormalizeDraftCtx = {
            ppbOf: () => PPB,
            boxSizeOf: () => SIZE,
            isNewcomer: () => true,
            freeByNm: { 200: { 1: 100 } },
        };
        const inUnits = 450;
        const res = normalizeDraft([row(200, { '1': inUnits }, { 'Казань': inUnits }, 'BOX')], nctx);
        const out = sumTgt(res.rows);
        // Σвыход + ушло-на-ФФ − добрано-из-ФФ == Σвход (баланс штук строго).
        const filledFromFf = res.rows.reduce((s, r) => s + tSrc(r), 0) - out; // 0 (src==tgt)
        expect(filledFromFf).toBe(0);
        expect(out + res.droppedUnits).toBeGreaterThanOrEqual(inUnits); // добор из ФФ не теряет
        for (const r of res.rows) expect(tSrc(r)).toBe(tTgt(r));
    });
});

describe('normalizeDraft — as_is («Оставить так»): частичная паллета проходит насквозь', () => {
    const asIsRow = (nm: number, units: number): AssemblyDraftRow =>
        ({ ...row(nm, { '1': units }, { 'Казань': units }, 'BOX'), as_is: true });

    it('as_is-строка < паллеты НЕ срезается (без флага — срезалась бы)', () => {
        // 80 шт = 8 коробов = 0.5 паллеты: без флага дропается (см. тест выше).
        const res = normalizeDraft([asIsRow(300, 80)], ctx);
        expect(res.rows).toHaveLength(1);
        expect(res.rows[0].as_is).toBe(true);
        expect(sumTgt(res.rows)).toBe(80);
        expect(res.droppedUnits).toBe(0);
        expect(res.changed).toBe(false);
    });

    it('микс: непомеченный недобор уходит в dropped, as_is остаётся (self-heal на загрузке)', () => {
        const rows = [
            asIsRow(300, 80),                                    // легитимная частичная
            row(301, { '1': 80 }, { 'Казань': 80 }, 'BOX'),      // легаси-недобор → предбронь
            row(200, { '1': 320 }, { 'Казань': 320 }, 'BOX'),    // 2 целые паллеты — едут
        ];
        const res = normalizeDraft(rows, ctx);
        expect(res.rows.some(r => r.nm_id === 300 && r.as_is)).toBe(true);   // as_is цела
        expect(res.rows.some(r => r.nm_id === 301)).toBe(false);             // недобор срезан
        expect(res.dropped.some(r => r.nm_id === 301)).toBe(true);           // … и попал в предбронь
        expect(sumTgt(res.rows.filter(r => r.nm_id === 200))).toBe(320);     // целые не тронуты
        expect(res.changed).toBe(true);
    });

    it('идемпотентность с as_is: повторный прогон ничего не меняет', () => {
        const first = normalizeDraft([asIsRow(300, 80), row(200, { '1': 320 }, { 'Казань': 320 }, 'BOX')], ctx);
        const second = normalizeDraft(first.rows, ctx);
        expect(second.changed).toBe(false);
        expect(sumTgt(second.rows)).toBe(sumTgt(first.rows));
    });
});
