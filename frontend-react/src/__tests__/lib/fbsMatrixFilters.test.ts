/**
 * Срезы «что делать» на матрице FBS-складов.
 *
 * Это рабочий список логиста, и каждая ошибка тут стоит либо зря увезённой
 * машины, либо непроданного товара. Три среза сознательно вложены не полностью:
 *  • «Не довезли» — можем поставить, а на складе продавца пусто;
 *  • «Нет в продаже» — то же И на FBO ноль (товар не продаётся нигде);
 *  • «Вскрыть коробá» — товар есть ТОЛЬКО коробами и ТОЛЬКО на этом ФФ.
 *
 * Ключевая граница — `fbo`: `0` это подтверждённый ноль, `null` — «зеркала нет /
 * судить нечем». Принять второе за первое значит позвать вскрывать коробá там,
 * где товар, возможно, прекрасно продаётся с FBO.
 */
import { describe, expect, it } from 'vitest';
import type { FbsMatrixRow, FbsMatrixWarehouse } from '@/types/api';
import { compareRows, matchesGapFilter } from '@/app/(main)/p/[slug]/warehouse/fbs/FbsMatrixTab';

const WH: FbsMatrixWarehouse[] = [
    { wb_warehouse_id: 1, name: 'Фрунзе Мигфул' },
    { wb_warehouse_id: 2, name: 'WMS Домодедово' },
];

function row(over: Partial<FbsMatrixRow> = {}): FbsMatrixRow {
    return {
        nomenclature_id: 1,
        cells: {},
        total_wb: 0,
        total_can: 0,
        total_boxed: 0,
        total_boxes: 0,
        fbo: 0,
        revenue: 0,
        profit: 0,
        margin_pct: null,
        sale_qty: 0,
        avg_daily_qty: 0,
        ...over,
    };
}

describe('«Не довезли»', () => {
    it('можем поставить, а на складе пусто', () => {
        const r = row({ cells: { '1': { wb: 0, can: 50 } }, total_can: 50 });
        expect(matchesGapFilter(r, 'gap', WH)).toBe(true);
    });

    it('на складе уже стоит — не недовоз', () => {
        const r = row({ cells: { '1': { wb: 30, can: 50 } }, total_can: 50 });
        expect(matchesGapFilter(r, 'gap', WH)).toBe(false);
    });

    it('везти нечего — не недовоз', () => {
        expect(matchesGapFilter(row({ cells: { '1': { wb: 0, can: 0 } } }), 'gap', WH)).toBe(false);
    });

    it('хватает ОДНОГО пустого склада из нескольких', () => {
        const r = row({
            cells: { '1': { wb: 10, can: 5 }, '2': { wb: 0, can: 40 } },
            total_can: 45,
        });
        expect(matchesGapFilter(r, 'gap', WH)).toBe(true);
    });
});

describe('«Нет в продаже»', () => {
    it('недовоз и на FBO ноль — товар не продаётся нигде', () => {
        const r = row({ cells: { '1': { wb: 0, can: 50 } }, total_can: 50, fbo: 0 });
        expect(matchesGapFilter(r, 'nosale', WH)).toBe(true);
    });

    it('на FBO продаётся — срочности нет', () => {
        const r = row({ cells: { '1': { wb: 0, can: 50 } }, total_can: 50, fbo: 137 });
        expect(matchesGapFilter(r, 'nosale', WH)).toBe(false);
    });

    it('FBO неизвестен — не зовём: это не подтверждённый ноль', () => {
        const r = row({ cells: { '1': { wb: 0, can: 50 } }, total_can: 50, fbo: null });
        expect(matchesGapFilter(r, 'nosale', WH)).toBe(false);
    });
});

describe('«Вскрыть коробá»', () => {
    it('только коробá, россыпи нет нигде, на FBO ноль', () => {
        const r = row({
            cells: { '1': { wb: 0, can: 0, boxed: 1782, boxes: 99 } },
            total_can: 0,
            total_boxed: 1782,
            fbo: 0,
        });
        expect(matchesGapFilter(r, 'boxes', WH)).toBe(true);
    });

    it('россыпь где-то есть — вскрывать не срочно, можно везти её', () => {
        const r = row({
            cells: { '1': { wb: 0, can: 0, boxed: 1782 }, '2': { wb: 0, can: 24 } },
            total_can: 24,
            total_boxed: 1782,
            fbo: 0,
        });
        expect(matchesGapFilter(r, 'boxes', WH)).toBe(false);
    });

    it('товар продаётся с FBO — вскрывать не срочно', () => {
        const r = row({
            cells: { '1': { wb: 0, can: 0, boxed: 1782 } },
            total_can: 0,
            total_boxed: 1782,
            fbo: 500,
        });
        expect(matchesGapFilter(r, 'boxes', WH)).toBe(false);
    });

    it('коробов нет — вскрывать нечего', () => {
        const r = row({ cells: { '1': { wb: 0, can: 0 } }, total_boxed: 0, fbo: 0 });
        expect(matchesGapFilter(r, 'boxes', WH)).toBe(false);
    });
});

/**
 * Сортировка по колонке склада.
 *
 * Регресс, который она закрывает: сортировали по ОДНОМУ числу («можем довезти»),
 * а у большинства строк оно ноль — везти туда нечего. Все они сходились в ничью,
 * порядок среди них оставался от бэкенда, и при прокрутке колонка выглядела
 * неотсортированной: строки со стоящим товаром всплывали вперемешку с пустыми.
 */
describe('сортировка по складу', () => {
    const wh = (over: Partial<FbsMatrixRow> = {}) => row(over);
    const byWh1 = (a: FbsMatrixRow, b: FbsMatrixRow) => compareRows(a, b, 'wh:1', -1);

    it('главный ключ — сколько можем довезти, коробá считаются', () => {
        const many = wh({ cells: { '1': { wb: 0, can: 0, boxed: 500 } } });
        const few = wh({ cells: { '1': { wb: 0, can: 100 } } });
        expect(byWh1(many, few)).toBeLessThan(0);   // 500 выше 100
    });

    it('ничья по «можем» разбивается тем, что уже стоит', () => {
        const standing = wh({ cells: { '1': { wb: 13, can: 0 } } });
        const empty = wh({ cells: { '1': { wb: 0, can: 0 } } });
        expect(byWh1(standing, empty)).toBeLessThan(0);
    });

    it('«позиции на складе нет» уходит ниже честного нуля', () => {
        const noCell = wh({ cells: {} });
        const zeroCell = wh({ cells: { '1': { wb: 0, can: 0 } } });
        expect(byWh1(zeroCell, noCell)).toBeLessThan(0);
    });

    it('полная ничья разбивается общим потенциалом — порядок не «плавает»', () => {
        const big = wh({ cells: { '1': { wb: 0, can: 0 } }, total_can: 900 });
        const small = wh({ cells: { '1': { wb: 0, can: 0 } }, total_can: 10 });
        expect(byWh1(big, small)).toBeLessThan(0);
    });

    it('направление применяется ко всем ключам сразу', () => {
        const a = wh({ cells: { '1': { wb: 13, can: 0 } } });
        const b = wh({ cells: { '1': { wb: 0, can: 0 } } });
        expect(compareRows(a, b, 'wh:1', 1)).toBeGreaterThan(0);
    });

    it('FBO: «не прочитан» — не ноль, уходит вниз', () => {
        const unknown = wh({ fbo: null });
        const zero = wh({ fbo: 0 });
        expect(compareRows(zero, unknown, 'fbo', -1)).toBeLessThan(0);
    });
});
