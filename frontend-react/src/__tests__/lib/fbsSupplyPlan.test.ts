/**
 * Чанкование стикеров и расшифровка причин блокировки в плане поставок.
 *
 * Инвариант, который тут закреплён: «Стикеры всей поставки» бьют выделение на
 * пачки ПО 100 — это жёсткий лимит WB на запрос. Раньше UI просто запрещал
 * выделять больше сотни, и поставка целиком (обычно больше) распечатать себя
 * не давала. Чанки обязаны покрывать вход без потерь и без дублей.
 */
import { describe, expect, it } from 'vitest';
import {
    WB_STICKER_CHUNK,
    chunkIds,
    supplyBlockedLabel,
} from '@/app/(main)/p/[slug]/warehouse/fbs/fbsShared';

describe('chunkIds', () => {
    it('пустой вход — пустой результат', () => {
        expect(chunkIds([])).toEqual([]);
    });

    it('меньше лимита — одна пачка', () => {
        expect(chunkIds([1, 2, 3])).toEqual([[1, 2, 3]]);
    });

    it('ровно лимит — одна пачка, не две (иначе лишний 4XX за пустой запрос)', () => {
        const ids = Array.from({ length: WB_STICKER_CHUNK }, (_, i) => i + 1);
        const chunks = chunkIds(ids);
        expect(chunks).toHaveLength(1);
        expect(chunks[0]).toHaveLength(WB_STICKER_CHUNK);
    });

    it('больше лимита — пачки по 100, вход покрыт полностью и без дублей', () => {
        const ids = Array.from({ length: 250 }, (_, i) => i + 1);
        const chunks = chunkIds(ids);
        expect(chunks.map(c => c.length)).toEqual([100, 100, 50]);
        expect(chunks.flat()).toEqual(ids);
        expect(new Set(chunks.flat()).size).toBe(ids.length);
    });
});

describe('supplyBlockedLabel', () => {
    it('известный код — человеческая формулировка', () => {
        expect(supplyBlockedLabel('already_in_supply')).toBe('задание уже в другой поставке');
    });

    it('готовая фраза с бэка отдаётся как есть, а не теряется', () => {
        const raw = 'Габаритный залипон: поставка зафиксирована на cargoType 1';
        expect(supplyBlockedLabel(raw)).toBe(raw);
    });
});
