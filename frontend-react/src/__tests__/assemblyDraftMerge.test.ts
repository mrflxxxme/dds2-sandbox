import { describe, expect, it } from 'vitest';
import { consolidatingLanes, findDuplicateLanes } from '@/lib/utils/assemblyDraftMerge';
import type { AssemblyDraft } from '@/types/api';

// Minimal draft factory
function makeDraft(id: number, rows: AssemblyDraft['distribution']['rows']): AssemblyDraft {
    return {
        id,
        project_id: 1,
        name: `Draft #${id}`,
        comment: null,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        distribution: {
            source_warehouse_ids: [],
            target_warehouse_names: [],
            rows,
            pallets_count: 1,
            pallet_weight_kg: 0,
            estimated_ready_date: null,
        },
    };
}

const nameById = (id: number) => `Склад-${id}`;

describe('findDuplicateLanes', () => {
    it('returns empty when < 2 drafts', () => {
        const d = makeDraft(1, [{ nm_id: 1, barcode: 'A', vendor_code: '', src: { '10': 100 }, tgt: { Электросталь: 100 } }]);
        expect(findDuplicateLanes([d], nameById)).toEqual([]);
    });

    it('detects duplicate lane across two drafts', () => {
        const d1 = makeDraft(1, [{ nm_id: 1, barcode: 'A', vendor_code: '', src: { '10': 100 }, tgt: { Электросталь: 100 } }]);
        const d2 = makeDraft(2, [{ nm_id: 2, barcode: 'B', vendor_code: '', src: { '10': 60 }, tgt: { Электросталь: 60 } }]);

        const result = findDuplicateLanes([d1, d2], nameById);
        expect(result).toHaveLength(1);
        expect(result[0].ffId).toBe(10);
        expect(result[0].wbName).toBe('Электросталь');
        expect(result[0].draftIds).toEqual(expect.arrayContaining([1, 2]));
        expect(result[0].pieces).toBe(160); // 100 + 60
        expect(result[0].ffName).toBe('Склад-10');
    });

    it('returns nothing when lanes are disjoint', () => {
        const d1 = makeDraft(1, [{ nm_id: 1, barcode: 'A', vendor_code: '', src: { '10': 100 }, tgt: { Электросталь: 100 } }]);
        const d2 = makeDraft(2, [{ nm_id: 2, barcode: 'B', vendor_code: '', src: { '20': 50 }, tgt: { Казань: 50 } }]);

        expect(findDuplicateLanes([d1, d2], nameById)).toHaveLength(0);
    });

    it('ignores rows with zero src or tgt', () => {
        const d1 = makeDraft(1, [{ nm_id: 1, barcode: 'A', vendor_code: '', src: { '10': 0 }, tgt: { Электросталь: 100 } }]);
        const d2 = makeDraft(2, [{ nm_id: 2, barcode: 'B', vendor_code: '', src: { '10': 50 }, tgt: { Электросталь: 0 } }]);

        expect(findDuplicateLanes([d1, d2], nameById)).toHaveLength(0);
    });

    it('detects only overlapping lanes when drafts partially overlap', () => {
        // d1: ff=10 → Электросталь и Казань
        // d2: ff=10 → Электросталь only
        // → дубль только по Электросталь
        const d1 = makeDraft(1, [
            { nm_id: 1, barcode: 'A', vendor_code: '', src: { '10': 100 }, tgt: { Электросталь: 60, Казань: 40 } },
        ]);
        const d2 = makeDraft(2, [
            { nm_id: 2, barcode: 'B', vendor_code: '', src: { '10': 80 }, tgt: { Электросталь: 80 } },
        ]);

        const result = findDuplicateLanes([d1, d2], nameById);
        expect(result).toHaveLength(1);
        expect(result[0].wbName).toBe('Электросталь');
    });

    it('handles three drafts — draftIds includes all with the lane', () => {
        const makeRow = (qty: number) => ({ nm_id: 1, barcode: 'X', vendor_code: '', src: { '10': qty }, tgt: { Электросталь: qty } });
        const d1 = makeDraft(1, [makeRow(100)]);
        const d2 = makeDraft(2, [makeRow(50)]);
        const d3 = makeDraft(3, [makeRow(30)]);

        const result = findDuplicateLanes([d1, d2, d3], nameById);
        expect(result).toHaveLength(1);
        expect(result[0].draftIds).toHaveLength(3);
        expect(result[0].pieces).toBe(180);
    });
});

describe('consolidatingLanes', () => {
    it('returns lanes where ≥2 selected drafts share the lane', () => {
        const lanes = [
            { ffId: 10, ffName: 'Склад-10', wbName: 'Электросталь', draftIds: [1, 2], pieces: 150 },
            { ffId: 10, ffName: 'Склад-10', wbName: 'Казань', draftIds: [1, 3], pieces: 60 },
        ];
        // Selected: 1 and 2 → first lane consolidates, second doesn't (3 not selected)
        const result = consolidatingLanes(new Set([1, 2]), lanes);
        expect(result).toHaveLength(1);
        expect(result[0].wbName).toBe('Электросталь');
    });

    it('returns empty when selection has no overlap with duplicate lanes', () => {
        const lanes = [
            { ffId: 10, ffName: 'Склад-10', wbName: 'Электросталь', draftIds: [1, 2], pieces: 100 },
        ];
        expect(consolidatingLanes(new Set([3, 4]), lanes)).toHaveLength(0);
    });
});
