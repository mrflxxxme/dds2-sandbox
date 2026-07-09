/**
 * Короб-гранулярный срез смешанных BOX-паллет (`snapToWholePallets` + boxOf).
 *
 * Инцидент-первоисточник (Екб-Персп14 × Хамза, 80х160_синий, 2026-07-02): срез
 * излишка шёл ПОШТУЧНО (rm = floor(rem×upp)) — хвост 14 шт SKU с ppb=22 был разрезан
 * на kept 3 / dropped 11, после чего добивка до короба стала невозможна навсегда
 * (gap 11 > свободных 8). Правило юзера: SKU с кратностью НИКОГДА не режется на
 * некратные коробу части; новой россыпи упаковка не создаёт.
 *
 * Инвариант среза: для каждой линии kept ≡ 0 (mod ppb) ЛИБО ≡ исходной россыпи
 * (mod ppb) — т.е. упаковка может снять только целые коробы и/или исходный
 * суб-коробочный остаток целиком.
 */
import { describe, it, expect } from 'vitest';
import { snapToWholePallets } from '@/lib/utils/boxPallet';
import { normalizeDraft, consolidatePrebookWholePallets, type NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';
import { topUpPrebookLooseBoxes } from '@/lib/utils/assemblyRoundBoxes';
import type { AssemblyDraftRow } from '@/types/api';

const sum = (m: Record<string, number>) => Object.values(m).reduce((a, v) => a + (v || 0), 0);

describe('snapToWholePallets + boxOf: срез только целыми коробами', () => {
    // Геометрия: upp=20 (4 короба по 5), две линии.
    const upp = () => 20;
    const box = () => 5;

    it('излишек снимается целыми коробами + исходной россыпью — некратных частей нет', () => {
        // X=15 (3 короба), Y=53 (10 коробов + россыпь 3). fill=3.4, keep=3, excess=0.4.
        const res = snapToWholePallets({ X: 15, Y: 53 }, upp, box);
        // Поштучный срез дал бы X: 15−8=7 (некратно!). Короб-гранулярный: X −5, Y −3 (россыпь).
        expect(res.kept.X % 5).toBe(0);
        expect((res.kept.Y ?? 0) % 5).toBe(0);
        expect(sum(res.kept) + sum(res.dropped)).toBe(68);
        // Ровно keep=3 паллеты осталось.
        expect(sum(res.kept) / 20).toBe(3);
    });

    it('репро Екб: хвост SKU с ppb=22 НЕ режется на некратные части (было 36 → kept 8)', () => {
        // X=36 (1 короб 22 + россыпь 14, upp 352: 16 коробов × 22), Y=98 — сосед с ppb 10.
        const uppOf = (k: string) => (k === 'X' ? 352 : 100);
        const boxOf = (k: string) => (k === 'X' ? 22 : 10);
        // fill = 36/352 + 0.98 = 1.0823. Поштучный срез: budget floor(0.0823×352)=28 →
        // kept X=8 (некратно 22 — механика инцидента 14→3/11). Короб-гранулярный: X цел
        // либо снят коробами/россыпью — kept ≡ 0 или ≡ 14 (mod 22).
        const res = snapToWholePallets({ X: 36, Y: 98 }, uppOf, boxOf);
        const keptX = res.kept.X ?? 0;
        expect([0, 36 % 22]).toContain(keptX % 22);  // некратность не создана
        expect((res.dropped.Y ?? 0) % 10 === 0 || (res.dropped.Y ?? 0) % 10 === 98 % 10).toBe(true);
        expect(sum(res.kept) + sum(res.dropped)).toBe(134);
    });

    it('россыпь линии снимается ЦЕЛИКОМ прежде коробов (kept становится кратным)', () => {
        // X=17 (3 короба + россыпь 2), Y=55 (кратно). fill=3.6, keep=3, excess=0.6.
        const res = snapToWholePallets({ X: 17, Y: 55 }, upp, box);
        expect(res.kept.X).toBe(5);                  // 17 − 2 (россыпь) − 10 (2 короба)
        expect(res.dropped.X).toBe(12);
        expect(res.kept.Y).toBe(55);                 // Y не тронут
        expect(sum(res.kept) / 20).toBe(3);
    });

    it('идемпотентность: повторный snap результата ничего не меняет (fuzz)', () => {
        let seed = 42;
        const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
        for (let i = 0; i < 300; i++) {
            const n = 1 + Math.floor(rnd() * 5);
            const km: Record<string, number> = {};
            const ppbBy: Record<string, number> = {};
            const uppBy: Record<string, number> = {};
            for (let j = 0; j < n; j++) {
                const p = [4, 5, 10, 22][Math.floor(rnd() * 4)];
                ppbBy[`k${j}`] = p;
                uppBy[`k${j}`] = p * (2 + Math.floor(rnd() * 15));
                km[`k${j}`] = Math.floor(rnd() * 120);
            }
            const uppOf = (k: string) => uppBy[k] ?? null;
            const boxOf = (k: string) => ppbBy[k] ?? null;
            const once = snapToWholePallets(km, uppOf, boxOf);
            const twice = snapToWholePallets(once.kept, uppOf, boxOf);
            expect(twice.kept).toEqual(once.kept);       // фикс-поинт
            expect(sum(twice.dropped)).toBe(0);
            expect(sum(once.kept) + sum(once.dropped)).toBe(sum(km)); // консервация
            // Некратность не создана: kept ≡ 0 или ≡ исходной россыпи (mod ppb).
            for (const [k, u] of Object.entries(once.kept)) {
                const p = ppbBy[k];
                expect([0, (km[k] || 0) % p]).toContain(u % p);
            }
        }
    });

    it('без boxOf — легаси-поведение (поштучный срез) не изменилось', () => {
        const res = snapToWholePallets({ X: 15, Y: 53 }, upp);
        expect(sum(res.kept) + sum(res.dropped)).toBe(68);
        // Поштучно: излишек 0.35 сверх tol → срез до keep с точностью до штуки.
        expect(sum(res.kept)).toBeGreaterThanOrEqual(60);
    });

    it('tol НЕ промоутит недобранную паллету в «целую» (находка адверсарного ревью)', () => {
        // bpp=2 (upp=44, короб 22): tol=0.5 поднимал keep — полупустая паллета ехала.
        const upp44 = () => 44;
        const box22 = () => 22;
        // 66 = 1.5 паллеты → едет РОВНО 1 целая (44), 22 в предбронь; не 66 как «целая».
        const a = snapToWholePallets({ X: 66 }, upp44, box22);
        expect(a.kept.X).toBe(44);
        expect(a.dropped.X).toBe(22);
        // Один короб (0.5 паллеты) — НЕ целая паллета, всё в предбронь на «Дозабить».
        const b = snapToWholePallets({ X: 22 }, upp44, box22);
        expect(b.kept.X ?? 0).toBe(0);
        expect(b.dropped.X).toBe(22);
        // 90% паллеты (198/220) — недобор, в предбронь (раньше keep=floor(0.9+0.1)=1 ехал).
        const c = snapToWholePallets({ X: 198 }, () => 220, box22);
        expect(c.kept.X ?? 0).toBe(0);
        expect(c.dropped.X).toBe(198);
    });
});

describe('регрессия PUT-шторма (адверсарное ревью): normalizeDraft сходится за ≤2 прогона', () => {
    // Репро шторма: BOX-строка 76 шт (ppb 5, upp 20 → footprint 3.8), свободно 170 шт
    // на ЧУЖОМ ФФ. Старый keep=floor(fill+tol) «промоутил» 76 как 4 целых паллеты,
    // фаза-A каждый цикл занимала +короб с чужого ФФ, трим ронял его в предбронь —
    // 34 PUT подряд, весь пул высасывался. Теперь строка режется до целых паллет
    // целыми коробами на ПЕРВОМ прогоне и фиксируется.
    const mkCtx = (rows: AssemblyDraftRow[], prebook: AssemblyDraftRow[]): NormalizeDraftCtx => {
        const AVAILABLE: Record<number, number> = { 3: 170 };
        const used: Record<number, number> = {};
        for (const r of [...rows, ...prebook]) for (const [ff, q] of Object.entries(r.src)) used[Number(ff)] = (used[Number(ff)] || 0) + (q || 0);
        const pool: Record<number, number> = {};
        for (const [ff, av] of Object.entries(AVAILABLE)) {
            const free = av - (used[Number(ff)] || 0);
            if (free > 0) pool[Number(ff)] = free;
        }
        return {
            ppbOf: () => 5,
            boxSizeOf: () => '40x40x40',
            overrides: { '40x40x40': 4 },   // bpp 4 → upp 20
            freeByNm: { 1: pool },
        };
    };

    it('строка 76 шт при пуле 170 на чужом ФФ: фикс-поинт ≤2 итераций, пул не высасывается', () => {
        let rows: AssemblyDraftRow[] = [{ nm_id: 1, barcode: 'bc', vendor_code: 'v', src: { '7': 76 }, tgt: { 'Екб': 76 }, package_type: 'BOX' }];
        let prebook: AssemblyDraftRow[] = [];
        let puts = 0;
        for (let i = 0; i < 10; i++) {
            const res = normalizeDraft(rows, mkCtx(rows, prebook));
            if (!res.changed) break;
            puts += 1;
            rows = res.rows;
            prebook = [...prebook, ...res.dropped];
        }
        expect(puts).toBeLessThanOrEqual(2);
        // Rows — только целые паллеты целыми коробами.
        for (const r of rows) for (const q of Object.values(r.tgt)) expect(q % 5).toBe(0);
        // Пул не высосан: суммарный резерв ЧУЖОГО ФФ (3) ограничен одним добором, не 170.
        const ff3 = [...rows, ...prebook].reduce((s, r) => s + (r.src['3'] || 0), 0);
        expect(ff3).toBeLessThanOrEqual(10);
    });
});

describe('инцидент-композиция: конс-упаковка воссоединяет разрезанный хвост, добивка достраивает короб', () => {
    // 80х160_синий: ppb 22, upp 352 (16 коробов), направление Екб, ФФ Хамза (3).
    const ctx: NormalizeDraftCtx = {
        ppbOf: () => 22,
        boxSizeOf: () => '60x40x40',
        overrides: { '60x40x40': 16 },
    };
    const row = (qty: number): AssemblyDraftRow => ({
        nm_id: 599228796, barcode: 'bc', vendor_code: '80х160_синий',
        src: { '3': qty }, tgt: { 'Екатеринбург - Перспективная 14': qty }, package_type: 'BOX',
    });

    it('порция rows 3 + предбронь 11 → единый хвост 14 → добивка свободными 8 → целый короб 22', () => {
        const cons = consolidatePrebookWholePallets([row(3), row(11)], ctx);
        // 14 шт < паллеты → всё в prebook, БЕЗ среза на некратные части.
        expect(cons.toDraft).toHaveLength(0);
        const merged = cons.prebook;
        expect(merged.reduce((s, r) => s + (r.tgt['Екатеринбург - Перспективная 14'] || 0), 0)).toBe(14);
        const free = { 599228796: { 3: 8 } };
        const topped = topUpPrebookLooseBoxes(merged, ctx.ppbOf, free);
        expect(topped.changed).toBe(true);
        expect(topped.rows.reduce((s, r) => s + (r.tgt['Екатеринбург - Перспективная 14'] || 0), 0)).toBe(22);
        expect(free[599228796][3]).toBe(0);
    });
});
