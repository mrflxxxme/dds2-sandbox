/**
 * Волна A v2: клиентская фильтрация доски (Р21).
 *
 * Смысл теста — зафиксировать, что фильтрация это ЧИСТАЯ функция над уже
 * загруженными задачами. Пока она такова, смена фильтра физически не может
 * сходить в сеть, и AC-4 «0 запросов» держится структурно, а не по договорённости.
 */
import { describe, expect, it } from 'vitest';
import {
    EMPTY_BOARD_FILTERS,
    UNASSIGNED,
    applyBoardFilters,
    type BoardFilterState,
} from '@/app/(main)/p/[slug]/design-tasks/components/BoardFilters';
import type { DesignTaskListItem } from '@/types/api';

function task(over: Partial<DesignTaskListItem>): DesignTaskListItem {
    return {
        id: 1,
        number: 'DES-1',
        title: 'Инфографика ковёр',
        status: 'NEW',
        work_type: 'FULL_SET',
        complexity: 'M',
        is_urgent: false,
        is_outsourced: false,
        is_overdue: false,
        unviewed: false,
        due_date: null,
        assignee_name: null,
        nm_id: null,
        versions_count: 0,
        ...over,
    } as DesignTaskListItem;
}

const TASKS: DesignTaskListItem[] = [
    task({ id: 1, number: 'DES-1', title: 'Ковёр 80x150', assignee_name: 'Аня', is_urgent: true }),
    task({ id: 2, number: 'DES-2', title: 'Плед серый', assignee_name: 'Борис', work_type: 'MAIN_PHOTO' }),
    task({ id: 3, number: 'ABC-9', title: 'Ковёр 60x100', assignee_name: null }),
];

const f = (over: Partial<BoardFilterState>): BoardFilterState => ({ ...EMPTY_BOARD_FILTERS, ...over });
const ids = (rows: DesignTaskListItem[]) => rows.map((t) => t.id);

describe('applyBoardFilters', () => {
    it('без фильтров отдаёт всё в исходном порядке', () => {
        expect(ids(applyBoardFilters(TASKS, EMPTY_BOARD_FILTERS))).toEqual([1, 2, 3]);
    });

    it('фильтрует по исполнителю', () => {
        expect(ids(applyBoardFilters(TASKS, f({ assignee: 'Аня' })))).toEqual([1]);
    });

    it('«Не назначен» ловит именно задачи без исполнителя', () => {
        expect(ids(applyBoardFilters(TASKS, f({ assignee: UNASSIGNED })))).toEqual([3]);
    });

    it('фильтрует по типу работы', () => {
        expect(ids(applyBoardFilters(TASKS, f({ workType: 'MAIN_PHOTO' })))).toEqual([2]);
    });

    it('«только срочные» оставляет помеченные', () => {
        expect(ids(applyBoardFilters(TASKS, f({ urgentOnly: true })))).toEqual([1]);
    });

    it('поиск идёт и по названию, и по номеру, без учёта регистра', () => {
        expect(ids(applyBoardFilters(TASKS, f({ q: 'КОВЁР' })))).toEqual([1, 3]);
        expect(ids(applyBoardFilters(TASKS, f({ q: 'abc' })))).toEqual([3]);
        expect(ids(applyBoardFilters(TASKS, f({ q: '  плед ' })))).toEqual([2]);
    });

    it('фильтры складываются через И', () => {
        expect(ids(applyBoardFilters(TASKS, f({ q: 'ковёр', urgentOnly: true })))).toEqual([1]);
        expect(ids(applyBoardFilters(TASKS, f({ q: 'ковёр', assignee: 'Борис' })))).toEqual([]);
    });

    it('не мутирует исходный массив', () => {
        const before = [...TASKS];
        applyBoardFilters(TASKS, f({ urgentOnly: true }));
        expect(TASKS).toEqual(before);
    });
});
