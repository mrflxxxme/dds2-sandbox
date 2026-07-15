import { describe, it, expect } from 'vitest';
import { applyAcceptanceRedistToPrebook } from '@/lib/assembly/prebookRedistribute';
import type { AssemblyDraftRow, AcceptanceCheckPerItem } from '@/types/api';

function item(nm: number, barcode: string, splits: { package_type: 'BOX' | 'MONOPALLET' | 'SUPERSAFE'; distribution: Record<string, number> }[]): AcceptanceCheckPerItem {
    return {
        nm_id: nm, barcode, availability: {}, package_type: splits[0]?.package_type ?? 'BOX',
        distribution: splits[0]?.distribution ?? {}, splits: splits.map(s => ({ ...s, warnings: [] })), warnings: [],
    };
}
const key = (nm: number, bc: string) => `${nm}::${bc}`;

describe('applyAcceptanceRedistToPrebook', () => {
    it('переносит ⌛-не-whitelist цель на приоритетный склад из splits, сохраняя ФФ', () => {
        // Воронеж моно ⌛ не в whitelist → бэк вернул splits с целью Электросталь.
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'BC1', vendor_code: 'v1', src: { '5': 12 }, tgt: { 'Воронеж': 12 }, package_type: 'MONOPALLET' },
        ];
        const resp = new Map([[key(1, 'BC1'), item(1, 'BC1', [{ package_type: 'MONOPALLET', distribution: { 'Электросталь': 12 } }])]]);
        const { rows, changed } = applyAcceptanceRedistToPrebook(prebook, resp);
        expect(changed).toBe(true);
        expect(rows).toHaveLength(1);
        expect(rows[0].tgt).toEqual({ 'Электросталь': 12 });
        expect(rows[0].src).toEqual({ '5': 12 });   // ФФ сохранён
        expect(rows[0].package_type).toBe('MONOPALLET');
    });

    it('идемпотентно: цель уже совпадает со splits → changed=false', () => {
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'BC1', vendor_code: 'v1', src: { '5': 12 }, tgt: { 'Электросталь': 12 }, package_type: 'MONOPALLET' },
        ];
        const resp = new Map([[key(1, 'BC1'), item(1, 'BC1', [{ package_type: 'MONOPALLET', distribution: { 'Электросталь': 12 } }])]]);
        const { changed } = applyAcceptanceRedistToPrebook(prebook, resp);
        expect(changed).toBe(false);
    });

    it('as_is-строки не трогает', () => {
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 1, barcode: 'BC1', vendor_code: 'v1', src: { '5': 12 }, tgt: { 'Воронеж': 12 }, package_type: 'MONOPALLET', as_is: true },
        ];
        const resp = new Map([[key(1, 'BC1'), item(1, 'BC1', [{ package_type: 'MONOPALLET', distribution: { 'Электросталь': 12 } }])]]);
        const { rows, changed } = applyAcceptanceRedistToPrebook(prebook, resp);
        expect(changed).toBe(false);
        expect(rows[0].tgt).toEqual({ 'Воронеж': 12 });
    });

    it('нет ответа по баркоду → строка без изменений', () => {
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 9, barcode: 'BCX', vendor_code: 'v', src: { '5': 5 }, tgt: { 'Воронеж': 5 }, package_type: 'MONOPALLET' },
        ];
        const { rows, changed } = applyAcceptanceRedistToPrebook(prebook, new Map());
        expect(changed).toBe(false);
        expect(rows[0].tgt).toEqual({ 'Воронеж': 5 });
    });

    it('split по типам упаковки: часть в короб, часть в моно — консервация Σ', () => {
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 2, barcode: 'BC2', vendor_code: 'v2', src: { '5': 10, '7': 6 }, tgt: { 'Воронеж': 16 }, package_type: 'MONOPALLET' },
        ];
        const resp = new Map([[key(2, 'BC2'), item(2, 'BC2', [
            { package_type: 'BOX', distribution: { 'Воронеж': 10 } },
            { package_type: 'MONOPALLET', distribution: { 'Электросталь': 6 } },
        ])]]);
        const { rows, changed } = applyAcceptanceRedistToPrebook(prebook, resp);
        expect(changed).toBe(true);
        const totalSrc = rows.reduce((s, r) => s + Object.values(r.src).reduce((a, v) => a + v, 0), 0);
        const totalTgt = rows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + v, 0), 0);
        expect(totalSrc).toBe(16);
        expect(totalTgt).toBe(16);
        expect(rows.some(r => r.package_type === 'BOX' && r.tgt['Воронеж'])).toBe(true);
        expect(rows.some(r => r.package_type === 'MONOPALLET' && r.tgt['Электросталь'])).toBe(true);
    });

    it('несоответствие суммы (дроп to=null) → группа не трогается (без потери товара)', () => {
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 3, barcode: 'BC3', vendor_code: 'v3', src: { '5': 20 }, tgt: { 'Воронеж': 20 }, package_type: 'MONOPALLET' },
        ];
        // Бэк дропнул часть (to=null) → splits суммарно 15, а не 20.
        const resp = new Map([[key(3, 'BC3'), item(3, 'BC3', [{ package_type: 'MONOPALLET', distribution: { 'Электросталь': 15 } }])]]);
        const { rows, changed } = applyAcceptanceRedistToPrebook(prebook, resp);
        expect(changed).toBe(false);
        expect(rows[0].tgt).toEqual({ 'Воронеж': 20 });
    });

    it('дефолт: полный дроп (splits пусты) → группа не трогается (предбронь не теряем)', () => {
        const prebook: AssemblyDraftRow[] = [
            { nm_id: 4, barcode: 'BC4', vendor_code: 'v4', src: { '5': 30 }, tgt: { 'Коледино': 30 }, package_type: 'BOX' },
        ];
        const resp = new Map([[key(4, 'BC4'), item(4, 'BC4', [])]]);
        const { rows, changed } = applyAcceptanceRedistToPrebook(prebook, resp);
        expect(changed).toBe(false);
        expect(rows[0].tgt).toEqual({ 'Коледино': 30 });
    });

    describe('dropUnplaced (засев новинок на машине)', () => {
        it('полный дроп (splits пусты: сдать некуда) → группа НЕ сеется вовсе', () => {
            // Якорь закрыт по приёмке, открытых складов нет НИГДЕ → бэк вычеркнул всё
            // (moves to=null, splits=[]). Черновик в этом кейсе оставляет новинку на ФФ —
            // засев обязан вести себя так же, а не везти заявку на закрытый якорь.
            const seeded: AssemblyDraftRow[] = [
                { nm_id: 5, barcode: 'BC5', vendor_code: 'v5', src: { '12': 40 }, tgt: { 'Санкт-Петербург Уткина Заводь': 40 }, package_type: 'BOX' },
            ];
            const resp = new Map([[key(5, 'BC5'), item(5, 'BC5', [])]]);
            const { rows, changed } = applyAcceptanceRedistToPrebook(seeded, resp, { dropUnplaced: true });
            expect(changed).toBe(true);
            expect(rows).toHaveLength(0);
        });

        it('частичная сумма (cellTotal < srcTotal) → сеем только доставляемое, хвост не сеем', () => {
            const seeded: AssemblyDraftRow[] = [
                { nm_id: 6, barcode: 'BC6', vendor_code: 'v6', src: { '12': 20 }, tgt: { 'Воронеж': 20 }, package_type: 'BOX' },
            ];
            const resp = new Map([[key(6, 'BC6'), item(6, 'BC6', [{ package_type: 'BOX', distribution: { 'Электросталь': 15 } }])]]);
            const { rows, changed } = applyAcceptanceRedistToPrebook(seeded, resp, { dropUnplaced: true });
            expect(changed).toBe(true);
            expect(rows).toHaveLength(1);
            expect(rows[0].tgt).toEqual({ 'Электросталь': 15 });
            expect(rows[0].src).toEqual({ '12': 15 });   // хвост 5 шт остаётся в остатке машины
        });

        it('перебор (cellTotal > srcTotal, рассинхрон) → группа не трогается (не задваиваем)', () => {
            const seeded: AssemblyDraftRow[] = [
                { nm_id: 7, barcode: 'BC7', vendor_code: 'v7', src: { '12': 10 }, tgt: { 'Воронеж': 10 }, package_type: 'BOX' },
            ];
            const resp = new Map([[key(7, 'BC7'), item(7, 'BC7', [{ package_type: 'BOX', distribution: { 'Электросталь': 25 } }])]]);
            const { rows, changed } = applyAcceptanceRedistToPrebook(seeded, resp, { dropUnplaced: true });
            expect(changed).toBe(false);
            expect(rows[0].tgt).toEqual({ 'Воронеж': 10 });
        });

        it('перенос с консервацией суммы работает как в дефолте', () => {
            const seeded: AssemblyDraftRow[] = [
                { nm_id: 8, barcode: 'BC8', vendor_code: 'v8', src: { '12': 16 }, tgt: { 'Воронеж': 16 }, package_type: 'BOX' },
            ];
            const resp = new Map([[key(8, 'BC8'), item(8, 'BC8', [{ package_type: 'BOX', distribution: { 'Электросталь': 16 } }])]]);
            const { rows, changed } = applyAcceptanceRedistToPrebook(seeded, resp, { dropUnplaced: true });
            expect(changed).toBe(true);
            expect(rows).toHaveLength(1);
            expect(rows[0].tgt).toEqual({ 'Электросталь': 16 });
            expect(rows[0].src).toEqual({ '12': 16 });
        });
    });
});
