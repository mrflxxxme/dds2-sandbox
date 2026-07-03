/**
 * Баг: `roundDraftRowsToWholeBoxes` бракова́л всю строку по ПУСТОЙ глобальной кратности
 * (`ppbOf`), хотя короб задан ПО-СКЛАДСКИ (`ppbAt`) — как у SKU, чья кратность приходит
 * только с ФФ (напр. Хамза). Итог: россыпь оставалась в черновике («1 кор + N шт»), хотя
 * показ и паллет-срез эту кратность видят. Фикс: резолв кратности по-порционно (ppbAt →
 * global-фолбэк), без брака строки по пустой global.
 */
import { describe, it, expect } from 'vitest';
import { roundDraftRowsToWholeBoxes } from '@/lib/utils/assemblyRoundBoxes';
import type { AssemblyDraftRow } from '@/types/api';

const row = (nm: number, src: Record<string, number>, tgt: Record<string, number>): AssemblyDraftRow =>
    ({ nm_id: nm, barcode: `bc${nm}`, vendor_code: `v${nm}`, package_type: 'BOX', src, tgt } as AssemblyDraftRow);

describe('roundDraftRowsToWholeBoxes: кратность только по складу (global пуста)', () => {
    it('срезает россыпь по ppbAt, когда ppbOf пуста (баг Хамза→ЕКБ 36 при коробе 22)', () => {
        // global ppb = null, по-складская (ФФ 3) = 22. Свободного ФФ нет → 14 срезается.
        const res = roundDraftRowsToWholeBoxes(
            [row(111, { '3': 36 }, { 'ЕКБ': 36 })],
            () => null,                 // ppbOf (global) — ПУСТО
            {},                         // freeByNm — свободного ФФ нет
            undefined,
            () => 22,                   // ppbAt (по складу) — есть
        );
        const ekb = res.rows[0]?.tgt['ЕКБ'] ?? 0;
        expect(ekb % 22).toBe(0);       // никакой россыпи
        expect(ekb).toBe(22);           // 36 → 1 целый короб
        expect(res.trimmedDown).toBe(14); // 14 шт россыпи ушли на ФФ
    });

    it('добивает вверх из свободного ФФ по ppbAt при пустой global', () => {
        const res = roundDraftRowsToWholeBoxes(
            [row(111, { '3': 36 }, { 'ЕКБ': 36 })],
            () => null,
            { 111: { 3: 100 } },        // на ФФ 3 есть свободные — добьёт до 44 (2 короба)
            undefined,
            () => 22,
        );
        const ekb = res.rows[0]?.tgt['ЕКБ'] ?? 0;
        expect(ekb).toBe(44);
        expect(ekb % 22).toBe(0);
    });

    it('нет кратности НИГДЕ (ни global, ни per-wh) → строка как есть (россыпь-новинка)', () => {
        const res = roundDraftRowsToWholeBoxes(
            [row(111, { '3': 36 }, { 'ЕКБ': 36 })],
            () => null,
            {},
            undefined,
            () => null,
        );
        expect(res.rows[0]?.tgt['ЕКБ']).toBe(36);   // не трогаем — округлять нечем
        expect(res.changed).toBe(0);
    });

    it('global-кратность по-прежнему работает (регресс не сломан)', () => {
        const res = roundDraftRowsToWholeBoxes(
            [row(111, { '3': 36 }, { 'ЕКБ': 36 })],
            () => 22,                   // global есть
            {},
            undefined,
            () => null,                 // per-wh нет → фолбэк на global
        );
        expect(res.rows[0]?.tgt['ЕКБ']).toBe(22);
    });
});
