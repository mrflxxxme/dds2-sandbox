/**
 * Добивка предбронь-хвостов до ЦЕЛОГО КОРОБА из свободного остатка ТОГО ЖЕ ФФ
 * (`topUpPrebookLooseBoxes`). Правило юзера: «если можно дозабить полную коробку —
 * дозабивай; россыпь легитимна только когда на ЭТОМ ФФ свободного не осталось».
 *
 * Кейс-первоисточник: Екб-Персп14 × ФФ Хамза, 80х160_синий — хвост 14 шт при
 * ppb=22 и свободных 8 шт на том же ФФ висел в предброни «0 кор / 0 шт» навсегда:
 * фаза-A добора гоняется только по rows, «Дозабить» ищет только целые коробы.
 */
import { describe, it, expect } from 'vitest';
import { topUpPrebookLooseBoxes, releaseUnfixableLooseBoxes } from '@/lib/utils/assemblyRoundBoxes';
import type { AssemblyDraftRow } from '@/types/api';

const FF = 3;
const WB = 'Екатеринбург - Перспективная 14';
const PPB = 22;

const row = (qty: number, over: Partial<AssemblyDraftRow> = {}): AssemblyDraftRow => ({
    nm_id: 599228796, barcode: 'bc1', vendor_code: '80х160_синий',
    src: { [String(FF)]: qty }, tgt: { [WB]: qty }, package_type: 'BOX', ...over,
});
const sumSrc = (r: AssemblyDraftRow): number => Object.values(r.src).reduce((a, v) => a + (v || 0), 0);
const sumTgt = (r: AssemblyDraftRow): number => Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0);

describe('topUpPrebookLooseBoxes: добивка хвоста до целого короба с того же ФФ', () => {
    it('хвост 14 при ppb=22 и свободных 8 на том же ФФ → 22 (целый короб), пул выпит', () => {
        const free = { 599228796: { [FF]: 8 } };
        const res = topUpPrebookLooseBoxes([row(14)], () => PPB, free);
        expect(res.changed).toBe(true);
        expect(res.filledUp).toBe(8);
        expect(res.rows[0].tgt[WB]).toBe(22);
        expect(res.rows[0].src[String(FF)]).toBe(22);
        expect(free[599228796][FF]).toBe(0);
    });

    it('свободных меньше гэпа (7 < 8) → россыпь остаётся как есть (легитимна)', () => {
        const free = { 599228796: { [FF]: 7 } };
        const res = topUpPrebookLooseBoxes([row(14)], () => PPB, free);
        expect(res.changed).toBe(false);
        expect(res.rows[0].tgt[WB]).toBe(14);
        expect(free[599228796][FF]).toBe(7);
    });

    it('свободное есть только на ДРУГОМ ФФ → не добивает (короб пакуется на одном ФФ)', () => {
        const free = { 599228796: { 1: 100 } };
        const res = topUpPrebookLooseBoxes([row(14)], () => PPB, free);
        expect(res.changed).toBe(false);
        expect(res.rows[0].tgt[WB]).toBe(14);
        expect(free[599228796][1]).toBe(100);
    });

    it('as_is-строка не трогается (юзер явно зафиксировал частичную)', () => {
        const free = { 599228796: { [FF]: 8 } };
        const res = topUpPrebookLooseBoxes([row(14, { as_is: true })], () => PPB, free);
        expect(res.changed).toBe(false);
        expect(res.rows[0].tgt[WB]).toBe(14);
    });

    it('МОНО и SKU без кратности не трогаются', () => {
        const free = { 599228796: { [FF]: 100 } };
        const mono = topUpPrebookLooseBoxes([row(14, { package_type: 'MONOPALLET' })], () => PPB, free);
        expect(mono.changed).toBe(false);
        const noPpb = topUpPrebookLooseBoxes([row(14)], () => null, free);
        expect(noPpb.changed).toBe(false);
    });

    it('мульти-src строка: добивается только порция своего ФФ, баланс Σsrc==Σtgt цел', () => {
        // A:14 (хвост, gap 8) + B:22 (целый короб) на один склад; свободно A=8.
        const r: AssemblyDraftRow = {
            nm_id: 1, barcode: 'bc', vendor_code: 'v',
            src: { '3': 14, '5': 22 }, tgt: { [WB]: 36 }, package_type: 'BOX',
        };
        const free = { 1: { 3: 8 } };
        const res = topUpPrebookLooseBoxes([r], () => PPB, free);
        expect(res.changed).toBe(true);
        expect(res.filledUp).toBe(8);
        expect(res.rows[0].src['3']).toBe(22);
        expect(res.rows[0].src['5']).toBe(22);
        expect(res.rows[0].tgt[WB]).toBe(44);
        expect(sumSrc(res.rows[0])).toBe(sumTgt(res.rows[0]));
    });

    it('идемпотентность: повторный прогон после добивки — no-op', () => {
        const free = { 599228796: { [FF]: 8 } };
        const first = topUpPrebookLooseBoxes([row(14)], () => PPB, free);
        const second = topUpPrebookLooseBoxes(first.rows, () => PPB, free);
        expect(second.changed).toBe(false);
        expect(second.rows[0].tgt[WB]).toBe(22);
    });

    it('пул расходуется последовательно: на два хвоста одного SKU хватает только первому', () => {
        // Два направления по 14 (gap 8 каждому), свободно 10 → добит первый, второму не хватило.
        const r1 = row(14);
        const r2 = row(14, { tgt: { 'Казань': 14 } });
        const free = { 599228796: { [FF]: 10 } };
        const res = topUpPrebookLooseBoxes([r1, r2], () => PPB, free);
        expect(res.filledUp).toBe(8);
        expect(res.rows[0].tgt[WB]).toBe(22);
        expect(res.rows[1].tgt['Казань']).toBe(14);
        expect(free[599228796][FF]).toBe(2);
    });
});

describe('releaseUnfixableLooseBoxes: «добить нечем» → не резервируем, освобождаем на ФФ (решение юзера)', () => {
    it('хвост 4 при коробе 22 и свободных 0 → строка снята, штуки вернулись в пул', () => {
        const free = { 599228796: { [FF]: 0 } };
        const res = releaseUnfixableLooseBoxes([row(4)], () => PPB, free);
        expect(res.changed).toBe(true);
        expect(res.releasedUnits).toBe(4);
        expect(res.rows).toHaveLength(0);
        expect(free[599228796][FF]).toBe(4); // вернулись в свободный ФФ
    });

    it('добиваемый хвост (свободных хватает) НЕ отпускается — его добьёт topUp', () => {
        const free = { 599228796: { [FF]: 8 } };
        const res = releaseUnfixableLooseBoxes([row(14)], () => PPB, free);
        expect(res.changed).toBe(false);
        expect(res.rows[0].tgt[WB]).toBe(14);
    });

    it('смешанная порция: россыпь отпускается, целые коробы остаются в резерве', () => {
        // 25 = 1 короб (22) + 3 россыпью; свободных 0 → 3 на ФФ, 22 остаются ждать паллету.
        const free = { 599228796: { [FF]: 0 } };
        const res = releaseUnfixableLooseBoxes([row(25)], () => PPB, free);
        expect(res.releasedUnits).toBe(3);
        expect(res.rows[0].tgt[WB]).toBe(22);
        expect(res.rows[0].src[String(FF)]).toBe(22);
        expect(free[599228796][FF]).toBe(3);
    });

    it('as_is, МОНО и SKU без кратности не трогаются', () => {
        const free = { 599228796: { [FF]: 0 } };
        expect(releaseUnfixableLooseBoxes([row(4, { as_is: true })], () => PPB, free).changed).toBe(false);
        expect(releaseUnfixableLooseBoxes([row(4, { package_type: 'MONOPALLET' })], () => PPB, free).changed).toBe(false);
        expect(releaseUnfixableLooseBoxes([row(4)], () => null, free).changed).toBe(false);
    });

    it('связка topUp → release: добиваемое добивается, недобиваемое отпускается', () => {
        // Екб 14 (gap 8, свободно 8) добьётся; Казань 4 (gap 18, свободно 0 после) отпустится.
        const r1 = row(14);
        const r2 = row(4, { tgt: { 'Казань': 4 } });
        const free = { 599228796: { [FF]: 8 } };
        const topped = topUpPrebookLooseBoxes([r1, r2], () => PPB, free);
        expect(topped.rows[0].tgt[WB]).toBe(22);
        const rel = releaseUnfixableLooseBoxes(topped.rows, () => PPB, free);
        expect(rel.releasedUnits).toBe(4);
        expect(rel.rows).toHaveLength(1); // осталась только Екб-строка с целым коробом
        expect(rel.rows[0].tgt[WB]).toBe(22);
    });
});
