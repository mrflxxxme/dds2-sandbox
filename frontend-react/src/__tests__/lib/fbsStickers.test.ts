/**
 * Стикеры FBS: соответствие «файл ↔ задание» и отбор годных статусов.
 *
 * Регресс, который тут закреплён: WB возвращает элемент БЕЗ `file` для части
 * заданий, и после `stickers.map(s => s.file).filter(...)` массив файлов
 * становился короче исходного — индекс `i` начинал указывать на ЧУЖОЙ
 * `order_id`, и сборщик клеил этикетку не на ту коробку. Ошибка тихая:
 * ловится только на приёмке WB.
 *
 * Второй инвариант: отменённое задание остаётся в поставке (`cancel_order` не
 * чистит `supply_id`), а бэк роняет ВЕСЬ запрос стикеров на первом негодном
 * id — отбирать годные обязан фронт.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
    deliverStickers,
    isActiveOrder,
    isStickerReady,
    selectStickerIds,
} from '@/app/(main)/p/[slug]/warehouse/fbs/fbsShared';
import type { FbsSticker } from '@/types/api';

/** base64 однобайтовых «файлов» — содержимое неважно, важны имена. */
const FILE_A = 'QQ==';
const FILE_B = 'Qg==';

let downloaded: string[];

beforeEach(() => {
    downloaded = [];
    // jsdom не реализует ни createObjectURL, ни настоящую навигацию по <a download>.
    Object.defineProperty(URL, 'createObjectURL', { value: () => 'blob:stub', writable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: () => undefined, writable: true });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
        this: HTMLAnchorElement,
    ) {
        downloaded.push(this.download);
    });
    // Окно печати «заблокировано» → пачка картинок уходит файлами по одному,
    // и имя каждого файла становится проверяемым.
    vi.spyOn(window, 'open').mockReturnValue(null);
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe('deliverStickers — имя файла всегда от СВОЕГО задания', () => {
    it('одиночный файл после пустого элемента подписан своим order_id', () => {
        const stickers: FbsSticker[] = [
            { order_id: 111, file: null },
            { order_id: 222, file: FILE_A },
        ];

        expect(deliverStickers(stickers, 'png')).toBe(1);
        expect(downloaded).toEqual(['fbs-стикер-222.png']);
    });

    it('пачка файлов не съезжает по индексу относительно заданий', () => {
        const stickers: FbsSticker[] = [
            { order_id: 111, file: null },
            { order_id: 222, file: FILE_A },
            { order_id: 333, file: FILE_B },
        ];

        expect(deliverStickers(stickers, 'png')).toBe(2);
        expect(downloaded).toEqual(['fbs-стикер-222.png', 'fbs-стикер-333.png']);
    });

    it('ни одного файла — ноль, вызывающий покажет объяснение', () => {
        expect(deliverStickers([{ order_id: 111, file: null }], 'png')).toBe(0);
        expect(downloaded).toEqual([]);
    });
});

describe('отбор заданий по статусу', () => {
    it('стикер печатается только для confirm/complete', () => {
        expect(isStickerReady('confirm')).toBe(true);
        expect(isStickerReady('complete')).toBe(true);
        expect(isStickerReady('new')).toBe(false);
        expect(isStickerReady('cancel')).toBe(false);
        expect(isStickerReady('cancel_carrier')).toBe(false);
        expect(isStickerReady(null)).toBe(false);
    });

    it('передавать нечего, если все задания отменены', () => {
        expect(isActiveOrder('new')).toBe(true);
        expect(isActiveOrder('confirm')).toBe(true);
        expect(isActiveOrder('cancel')).toBe(false);
        expect(isActiveOrder('cancel_carrier')).toBe(false);
    });
});

describe('selectStickerIds — «Выбрать все» не роняет печать целиком', () => {
    /** Статусы «виденных» заданий: выделение уходит за пределы страницы. */
    const statuses = new Map<number, string>([
        [1, 'new'],
        [2, 'confirm'],
        [3, 'complete'],
        [4, 'cancel'],
        [5, 'cancel_carrier'],
    ]);

    it('на вкладке «Все» отсеивает new и отменённые, годные остаются', () => {
        expect(selectStickerIds([1, 2, 3, 4, 5], statuses)).toEqual([2, 3]);
    });

    it('порядок выделения сохраняется — пачки уходят как есть', () => {
        expect(selectStickerIds([3, 2], statuses)).toEqual([3, 2]);
    });

    it('задание с неизвестным статусом не печатаем — пачка дороже', () => {
        expect(selectStickerIds([2, 999], statuses)).toEqual([2]);
    });

    it('годных нет — пустой список, запрос в WB не уходит вовсе', () => {
        expect(selectStickerIds([1, 4], statuses)).toEqual([]);
    });
});
