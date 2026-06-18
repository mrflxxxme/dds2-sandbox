import { describe, expect, it } from 'vitest';
import { filterFfLinkCandidates, splitFfLinkCandidates, normWhName, whNamesMatch } from '@/lib/utils/ffLinkCandidates';
import type { FfLinkCandidate } from '@/types/api';

function makeCandidate(overrides: Partial<FfLinkCandidate> & { doc_id: number }): FfLinkCandidate {
    return {
        number: `ASM-${overrides.doc_id}`,
        status: 'PENDING',
        created_at: null,
        total_qty: 0,
        fbo_supply_number: null,
        dest_warehouse: null,
        score: null,
        reason: null,
        warehouse_match: true,
        linked_ff_count: 0,
        ...overrides,
    };
}

describe('filterFfLinkCandidates', () => {
    const candidates = [
        makeCandidate({ doc_id: 1, number: 'ASM-101', fbo_supply_number: 'WB-555', dest_warehouse: 'Коледино' }),
        makeCandidate({ doc_id: 2, number: 'ASM-202', fbo_supply_number: null, dest_warehouse: 'Казань' }),
        makeCandidate({ doc_id: 3, number: 'RCP-303' }),
    ];

    it('returns all on empty/whitespace query', () => {
        expect(filterFfLinkCandidates(candidates, '')).toHaveLength(3);
        expect(filterFfLinkCandidates(candidates, '   ')).toHaveLength(3);
    });

    it('matches by number case-insensitively', () => {
        const r = filterFfLinkCandidates(candidates, 'asm-1');
        expect(r.map(c => c.doc_id)).toEqual([1]);
    });

    it('matches by fbo_supply_number', () => {
        const r = filterFfLinkCandidates(candidates, 'wb-555');
        expect(r.map(c => c.doc_id)).toEqual([1]);
    });

    it('matches by dest_warehouse (кириллица)', () => {
        const r = filterFfLinkCandidates(candidates, 'каза');
        expect(r.map(c => c.doc_id)).toEqual([2]);
    });

    it('null fields do not crash and do not match', () => {
        const r = filterFfLinkCandidates(candidates, 'nomatch');
        expect(r).toHaveLength(0);
    });
});

describe('splitFfLinkCandidates', () => {
    it('scored — по убыванию score, others — по убыванию created_at', () => {
        const { scored, others } = splitFfLinkCandidates([
            makeCandidate({ doc_id: 1, score: 40, reason: 'ШК 40%' }),
            makeCandidate({ doc_id: 2, created_at: '2026-06-01T00:00:00Z' }),
            makeCandidate({ doc_id: 3, score: 90, reason: 'ШК 90%' }),
            makeCandidate({ doc_id: 4, created_at: '2026-06-10T00:00:00Z' }),
            makeCandidate({ doc_id: 5, created_at: null }),
        ]);
        expect(scored.map(c => c.doc_id)).toEqual([3, 1]);
        expect(others.map(c => c.doc_id)).toEqual([4, 2, 5]);
    });

    it('score=0 попадает в scored (не теряется как falsy)', () => {
        const { scored, others } = splitFfLinkCandidates([
            makeCandidate({ doc_id: 1, score: 0 }),
        ]);
        expect(scored.map(c => c.doc_id)).toEqual([1]);
        expect(others).toHaveLength(0);
    });
});
