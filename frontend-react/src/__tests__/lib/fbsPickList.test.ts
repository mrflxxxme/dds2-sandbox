/**
 * Экран поставки FBS: выделение ВСЕХ отфильтрованных заданий и печатный
 * лист подбора.
 *
 * Инварианты, закреплённые тут:
 *  1. «Выбрать все» берёт задания со ВСЕХ страниц выборки, а не только с
 *     видимой: у поставки бывает 47 заданий на трёх экранах, и массовое
 *     действие с половиной выделения — тихая потеря работы сборщика.
 *  2. Сбор дедупит id: между страницами могло приехать новое задание и
 *     сдвинуть окно, тогда одна строка попадётся дважды.
 *  3. Лист подбора печатается СВОИМ документом — данные приходят из WB и
 *     обязаны экранироваться, иначе артикул с `<` ломает вёрстку.
 */
import { describe, expect, it, vi } from 'vitest';
import {
    buildPickListPrintHtml,
    cargoCabinetLabel,
    collectAllOrderIds,
    escapeHtml,
} from '@/app/(main)/p/[slug]/warehouse/fbs/fbsShared';
import type { FbsPickList } from '@/types/api';

function page(ids: number[]) {
    return { items: ids.map(wb_order_id => ({ wb_order_id })) };
}

describe('collectAllOrderIds', () => {
    it('одна страница целиком: 47 заданий берутся за один запрос', async () => {
        const ids = Array.from({ length: 47 }, (_, i) => i + 1);
        const fetchPage = vi.fn(async () => page(ids));

        const res = await collectAllOrderIds(fetchPage, 47, { pageSize: 500 });

        expect(res.ids).toEqual(ids);
        expect(res.truncated).toBe(false);
        expect(fetchPage).toHaveBeenCalledTimes(1);
        // limit не раздувается сверх нужного — лишних строк с бэка не тянем
        expect(fetchPage).toHaveBeenCalledWith(0, 47);
    });

    it('несколько страниц: собирает всё, а не только первую', async () => {
        const fetchPage = vi.fn(async (offset: number, limit: number) =>
            page(Array.from({ length: Math.min(limit, 250 - offset) }, (_, i) => offset + i + 1)));

        const res = await collectAllOrderIds(fetchPage, 250, { pageSize: 100 });

        expect(res.ids).toHaveLength(250);
        expect(res.ids[0]).toBe(1);
        expect(res.ids[249]).toBe(250);
        expect(fetchPage).toHaveBeenCalledTimes(3);
    });

    it('сдвиг окна между страницами не даёт дублей', async () => {
        const pages = [page([1, 2, 3]), page([3, 4, 5]), page([6])];
        let call = 0;
        const fetchPage = vi.fn(async () => pages[call++] ?? page([]));

        const res = await collectAllOrderIds(fetchPage, 9, { pageSize: 3 });

        expect(res.ids).toEqual([1, 2, 3, 4, 5, 6]);
        expect(new Set(res.ids).size).toBe(res.ids.length);
    });

    it('выдача кончилась раньше total — пустые запросы не крутятся', async () => {
        const fetchPage = vi.fn(async () => page([1, 2]));

        const res = await collectAllOrderIds(fetchPage, 1000, { pageSize: 100 });

        expect(res.ids).toEqual([1, 2]);
        expect(fetchPage).toHaveBeenCalledTimes(1);
    });

    it('выборка больше потолка — берём потолок и честно помечаем truncated', async () => {
        const fetchPage = vi.fn(async (offset: number, limit: number) =>
            page(Array.from({ length: limit }, (_, i) => offset + i + 1)));

        const res = await collectAllOrderIds(fetchPage, 300, { pageSize: 100, max: 200 });

        expect(res.ids).toHaveLength(200);
        expect(res.truncated).toBe(true);
    });

    it('пустая выборка — ни одного запроса', async () => {
        const fetchPage = vi.fn(async () => page([]));
        const res = await collectAllOrderIds(fetchPage, 0);
        expect(res).toEqual({ ids: [], truncated: false });
        expect(fetchPage).not.toHaveBeenCalled();
    });
});

describe('cargoCabinetLabel', () => {
    it('габарит словами кабинета WB', () => {
        expect(cargoCabinetLabel(1)).toBe('МГТ');
        expect(cargoCabinetLabel(2)).toBe('СГТ');
        expect(cargoCabinetLabel(3)).toBe('КГТ+');
    });

    it('пустая поставка ещё не имеет габарита', () => {
        expect(cargoCabinetLabel(null)).toBe('—');
    });

    it('незнакомый код показывается, а не прячется под «—»', () => {
        expect(cargoCabinetLabel(9)).toBe('#9');
    });
});

describe('escapeHtml', () => {
    it('спецсимволы уходят в сущности', () => {
        expect(escapeHtml('<b>&"\'')).toBe('&lt;b&gt;&amp;&quot;&#39;');
    });

    it('null/undefined — пустая строка, а не «null»', () => {
        expect(escapeHtml(null)).toBe('');
        expect(escapeHtml(undefined)).toBe('');
    });
});

describe('buildPickListPrintHtml', () => {
    const pick: FbsPickList = {
        wb_supply_id: 'WB-GI-123',
        supply_name: 'FBS 24.07',
        wb_warehouse_id: 7,
        wb_warehouse_name: 'Основной',
        cargo_type: 2,
        done: false,
        created_at_wb: '2026-07-24T10:00:00Z',
        orders_count: 47,
        positions_count: 2,
        total_qty: 50,
        total_amount: 1000,
        rows: [
            { article: 'ART-1', subject: 'Ковёр', barcode: '2000000000011', qty: 30, stock_available: 40 },
            { article: '<script>', subject: null, barcode: null, qty: 20, stock_available: null },
        ],
    };

    it('шапка несёт поставку, склад и габарит словами кабинета', () => {
        const html = buildPickListPrintHtml(pick);
        expect(html).toContain('WB-GI-123');
        expect(html).toContain('Основной');
        expect(html).toContain('СГТ');
        expect(html).toContain('Активная');
    });

    it('строки идут по товару, итог — позиции и штуки', () => {
        const html = buildPickListPrintHtml(pick);
        expect(html).toContain('ART-1');
        expect(html).toContain('2000000000011');
        expect(html).toContain('Позиций: 2');
        expect(html).toContain('>50<');
    });

    it('данные из WB экранируются — сырой тег в документ не попадает', () => {
        const html = buildPickListPrintHtml(pick);
        expect(html).toContain('&lt;script&gt;');
        expect(html).not.toContain('<script>');
    });

    it('поставка без заданий печатается пустым бланком, а не падает', () => {
        const html = buildPickListPrintHtml({
            ...pick, rows: [], positions_count: 0, total_qty: 0, orders_count: 0,
        });
        expect(html).toContain('В поставке нет заданий');
    });
});
