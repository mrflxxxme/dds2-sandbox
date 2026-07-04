import { describe, expect, it } from 'vitest';
import { hasSuggestedWeight, isWeightAutofillEligible, weightAutofillIds } from '@/lib/utils/assemblyWeight';
import type { AssemblyRequest, AssemblyStatus } from '@/types/api';

// Минимальная фабрика заявки — только поля, важные для авто-веса.
function makeReq(
    id: number,
    status: AssemblyStatus,
    total_weight_kg: number | undefined,
    suggested_total_weight_kg: number | string | null | undefined,
): AssemblyRequest {
    return {
        id,
        warehouse_id: 1,
        number: `A-${id}`,
        status,
        wb_fbo_supply_id: null,
        pallets_count: 1,
        pallet_weight_kg: 0,
        total_weight_kg,
        suggested_total_weight_kg,
        items: [],
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
    } as AssemblyRequest;
}

describe('hasSuggestedWeight', () => {
    it('true для положительного number и строки (Decimal-as-string)', () => {
        expect(hasSuggestedWeight({ suggested_total_weight_kg: 530 })).toBe(true);
        expect(hasSuggestedWeight({ suggested_total_weight_kg: '530.5' })).toBe(true);
    });
    it('false для null/пусто/ноль/нечисла', () => {
        expect(hasSuggestedWeight({ suggested_total_weight_kg: null })).toBe(false);
        expect(hasSuggestedWeight({ suggested_total_weight_kg: undefined })).toBe(false);
        expect(hasSuggestedWeight({ suggested_total_weight_kg: '' })).toBe(false);
        expect(hasSuggestedWeight({ suggested_total_weight_kg: 0 })).toBe(false);
        expect(hasSuggestedWeight({ suggested_total_weight_kg: 'abc' })).toBe(false);
    });
});

describe('isWeightAutofillEligible', () => {
    it('eligible: IN_PROGRESS + пустой вес + есть suggested', () => {
        expect(isWeightAutofillEligible(makeReq(1, 'IN_PROGRESS', 0, 530))).toBe(true);
        expect(isWeightAutofillEligible(makeReq(2, 'IN_PROGRESS', undefined, '412.3'))).toBe(true);
    });
    it('НЕ eligible: вес уже проставлен', () => {
        expect(isWeightAutofillEligible(makeReq(3, 'IN_PROGRESS', 500, 530))).toBe(false);
    });
    it('НЕ eligible: нет suggested', () => {
        expect(isWeightAutofillEligible(makeReq(4, 'IN_PROGRESS', 0, null))).toBe(false);
    });
    it('НЕ eligible: не тот статус', () => {
        expect(isWeightAutofillEligible(makeReq(5, 'READY', 0, 530))).toBe(false);
        expect(isWeightAutofillEligible(makeReq(6, 'SHIPPED', 0, 530))).toBe(false);
    });
});

describe('weightAutofillIds', () => {
    it('собирает только подходящие id', () => {
        const rows = [
            makeReq(1, 'IN_PROGRESS', 0, 530),      // ✓
            makeReq(2, 'IN_PROGRESS', 500, 530),    // вес уже есть
            makeReq(3, 'READY', 0, 530),            // статус
            makeReq(4, 'IN_PROGRESS', 0, null),     // нет suggested
            makeReq(5, 'IN_PROGRESS', undefined, '99.9'), // ✓
        ];
        expect(weightAutofillIds(rows)).toEqual([1, 5]);
    });
    it('пустой список → []', () => {
        expect(weightAutofillIds([])).toEqual([]);
    });
});
