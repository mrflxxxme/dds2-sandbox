/**
 * Расширение батча checkWbAcceptance в headless-раннере авто-синка — ТОЛЬКО
 * из предброни (ревью MEDIUM 2026-07-23, квота WB 6/мин): readyPkgWbs нужен
 * обратной промоции целых паллет предбронь→rows, а она смотрит только
 * направления предброни. Rows-SKU вне расчёта приёмку для демоции не
 * используют — их включение лишь выедало квоту. Дедуп с расчётными SKU
 * (checkedKeys) обязан сохраниться.
 */
import { describe, expect, it } from 'vitest';
import { collectExtraAcceptanceItems } from '@/lib/assembly/draftAutoSyncRunner';
import type { AssemblyDraftRow } from '@/types/api';

const row = (nm: number, barcode: string, tgt: Record<string, number>): AssemblyDraftRow => ({
    nm_id: nm,
    barcode,
    vendor_code: `vc-${nm}`,
    src: { '1': Object.values(tgt).reduce((s, v) => s + v, 0) },
    tgt,
});

describe('collectExtraAcceptanceItems — extra-ШК батча приёмки только из предброни', () => {
    it('берёт ШК из prebook и агрегирует направления одного ключа', () => {
        const prebook = [
            row(10, 'bc10', { 'Тула': 30, 'Казань': 0 }),
            row(10, 'bc10', { 'Тула': 15 }), // второй хвост того же SKU
        ];
        const out = collectExtraAcceptanceItems(prebook, new Set());
        expect(out).toEqual([{ nm_id: 10, barcode: 'bc10', distribution: { 'Тула': 45 } }]);
    });

    it('дедуп с расчётными SKU: ключ из checkedKeys не дублируется в батче', () => {
        const prebook = [row(10, 'bc10', { 'Тула': 30 }), row(20, 'bc20', { 'Екб': 8 })];
        const out = collectExtraAcceptanceItems(prebook, new Set(['10::bc10']));
        expect(out).toEqual([{ nm_id: 20, barcode: 'bc20', distribution: { 'Екб': 8 } }]);
    });

    it('строки без barcode и с нулевыми объёмами не попадают в батч', () => {
        const prebook = [
            row(30, '', { 'Тула': 5 }), // без ШК приёмку не спросить
            row(40, 'bc40', { 'Тула': 0 }), // нулевое направление — нечего проверять
        ];
        expect(collectExtraAcceptanceItems(prebook, new Set())).toEqual([]);
    });

    it('пустая предбронь → пустой батч (rows раннер сюда не передаёт)', () => {
        expect(collectExtraAcceptanceItems([], new Set())).toEqual([]);
    });
});
