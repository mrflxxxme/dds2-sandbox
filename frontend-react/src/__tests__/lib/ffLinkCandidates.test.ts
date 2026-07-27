import { describe, expect, it } from 'vitest';
import { filterFfLinkCandidates, splitFfLinkCandidates, whStem, whNamesMatch } from '@/lib/utils/ffLinkCandidates';
import type { FfLinkCandidate } from '@/types/api';

function makeCandidate(overrides: Partial<FfLinkCandidate> & { doc_id: number }): FfLinkCandidate {
    return {
        doc_kind: 'assembly',
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

describe('whNamesMatch (стем-токенное совпадение складов сдачи)', () => {
    it('сводит migfull «ВБ | Екатеринбург Перспективный» и наш «Екатеринбург - Перспективная 14»', () => {
        const a = 'ВБ | Екатеринбург Перспективный';
        const b = 'Екатеринбург - Перспективная 14';
        expect(whNamesMatch(a, b)).toBe(true);
        expect(whNamesMatch(b, a)).toBe(true);
    });

    it('игнорирует маркетплейс-префикс и пунктуацию', () => {
        expect(whNamesMatch('Коледино', 'МСК Коледино')).toBe(true);
        expect(whNamesMatch('Москва (Коледино)', 'Коледино')).toBe(true);
        expect(whNamesMatch('WB Казань', 'Казань')).toBe(true);
    });

    it('разные улицы одного города — не совпадают', () => {
        expect(whNamesMatch('Екатеринбург Испытателей 14', 'Екатеринбург - Перспективная 14')).toBe(false);
    });

    it('улица-адрес: номера дома расходятся, склад тот же («Целиком» 12/2 ≡ WB 14)', () => {
        expect(whNamesMatch('Екатеринбург - Перспективная 14', 'Екатеринбург - Перспективный 12/2')).toBe(true);
        expect(whNamesMatch('Екатеринбург - Перспективный 12/2', 'Екатеринбург - Перспективная 14')).toBe(true);
    });

    it('топоним + порядковый номер: число различает склады', () => {
        expect(whNamesMatch('Чехов 1', 'Чехов 2')).toBe(false);
        expect(whNamesMatch('Подольск 3', 'Подольск 4')).toBe(false);
        expect(whNamesMatch('Чехов 1', 'Чехов 1')).toBe(true);
        expect(whNamesMatch('Чехов', 'Чехов 1')).toBe(true); // номер только с одной стороны → ок
    });

    it('пустой склад ФФ-заявки → совпадает со всеми; пустой наш → нет', () => {
        expect(whNamesMatch(null, 'Коледино')).toBe(true);
        expect(whNamesMatch('ВБ', 'Коледино')).toBe(true); // только маркер → слов нет
        expect(whNamesMatch('Коледино', null)).toBe(false);
    });

    it('разные города — не совпадают', () => {
        expect(whNamesMatch('Казань', 'Коледино')).toBe(false);
    });
});

describe('whStem', () => {
    it('схлопывает родовые окончания прилагательных', () => {
        expect(whStem('перспективный')).toBe(whStem('перспективная'));
        expect(whStem('центральный')).toBe('центральн');
    });
    it('не трогает короткие/не-прилагательные слова', () => {
        expect(whStem('белая')).toBe('белая');
        expect(whStem('коледино')).toBe('коледино');
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
