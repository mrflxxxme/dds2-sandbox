import { describe, expect, it } from 'vitest';
import {
    DESIGN_BOARD_STATUSES,
    applyOptimisticMove,
    buildBoardColumns,
    findTaskColumn,
    type BoardColumns,
} from '@/lib/design';
import type { DesignBoardResponse, DesignTaskListItem, DesignTaskStatus } from '@/types/api';

function task(id: number, status: DesignTaskStatus, sortOrder = id * 1000): DesignTaskListItem {
    return {
        id,
        number: `DES-${id}`,
        title: `Задача ${id}`,
        status,
        nm_id: null,
        work_type: 'FULL_SET',
        complexity: 'M',
        is_urgent: false,
        due_date: null,
        is_overdue: false,
        is_outsourced: false,
        unviewed: false,
        assignee_name: null,
        author_name: null,
        versions_count: 0,
        sort_order: sortOrder,
    };
}

function boardOf(...tasks: DesignTaskListItem[]): BoardColumns {
    const res: DesignBoardResponse = { columns: {}, counts: {} };
    for (const t of tasks) {
        (res.columns[t.status] ??= []).push(t);
    }
    return buildBoardColumns(res);
}

describe('buildBoardColumns — маппинг GET /board в 6 колонок', () => {
    it('отсутствующие в ответе статусы становятся пустыми колонками', () => {
        const columns = buildBoardColumns({ columns: { NEW: [task(1, 'NEW')] }, counts: {} });
        expect(Object.keys(columns)).toEqual([...DESIGN_BOARD_STATUSES]);
        expect(columns.NEW).toHaveLength(1);
        expect(columns.ASSIGNED).toEqual([]);
        expect(columns.ACCEPTED).toEqual([]);
    });

    it('ON_HOLD/CANCELLED не попадают в колонки доски', () => {
        const columns = buildBoardColumns({
            columns: { NEW: [task(1, 'NEW')], ON_HOLD: [task(2, 'ON_HOLD')], CANCELLED: [task(3, 'CANCELLED')] },
            counts: {},
        });
        expect(Object.keys(columns)).toEqual([...DESIGN_BOARD_STATUSES]);
        expect(findTaskColumn(columns, 2)).toBeNull();
        expect(findTaskColumn(columns, 3)).toBeNull();
    });

    it('порядок карточек внутри колонки сохраняется как в ответе', () => {
        const columns = buildBoardColumns({
            columns: { NEW: [task(5, 'NEW'), task(2, 'NEW'), task(9, 'NEW')] },
            counts: {},
        });
        expect(columns.NEW.map((t) => t.id)).toEqual([5, 2, 9]);
    });
});

describe('applyOptimisticMove — оптимистичное перемещение', () => {
    it('перенос между колонками в конец: статус обновлён, after = последняя карточка', () => {
        const columns = boardOf(task(1, 'NEW'), task(2, 'ASSIGNED'), task(3, 'ASSIGNED'));
        const res = applyOptimisticMove(columns, 1, 'ASSIGNED', null);
        expect(res).not.toBeNull();
        expect(res!.changed).toBe(true);
        expect(res!.afterTaskId).toBe(3);
        expect(res!.columns.NEW).toEqual([]);
        expect(res!.columns.ASSIGNED.map((t) => t.id)).toEqual([2, 3, 1]);
        expect(res!.columns.ASSIGNED[2].status).toBe('ASSIGNED');
    });

    it('перенос в пустую колонку: after_task_id = null (в голову)', () => {
        const columns = boardOf(task(1, 'NEW'));
        const res = applyOptimisticMove(columns, 1, 'IN_PROGRESS', null)!;
        expect(res.afterTaskId).toBeNull();
        expect(res.columns.IN_PROGRESS.map((t) => t.id)).toEqual([1]);
    });

    it('бросок перед конкретной карточкой: вставка перед ней, after = предыдущая', () => {
        const columns = boardOf(task(1, 'NEW'), task(2, 'ASSIGNED'), task(3, 'ASSIGNED'));
        const res = applyOptimisticMove(columns, 1, 'ASSIGNED', 3)!;
        expect(res.columns.ASSIGNED.map((t) => t.id)).toEqual([2, 1, 3]);
        expect(res.afterTaskId).toBe(2);
    });

    it('reorder внутри колонки вниз: индексы после изъятия не съезжают', () => {
        const columns = boardOf(task(1, 'NEW'), task(2, 'NEW'), task(3, 'NEW'));
        // тащим 1 перед 3: [1,2,3] → [2,1,3]
        const res = applyOptimisticMove(columns, 1, 'NEW', 3)!;
        expect(res.changed).toBe(true);
        expect(res.columns.NEW.map((t) => t.id)).toEqual([2, 1, 3]);
        expect(res.afterTaskId).toBe(2);
    });

    it('бросок на прежнее место: changed=false (API звать не нужно)', () => {
        const columns = boardOf(task(1, 'NEW'), task(2, 'NEW'));
        // 1 перед 2 — где он и был
        const res = applyOptimisticMove(columns, 1, 'NEW', 2)!;
        expect(res.changed).toBe(false);
        expect(res.columns.NEW.map((t) => t.id)).toEqual([1, 2]);
    });

    it('исходные колонки НЕ мутируются — снапшот пригоден для отката по 4xx', () => {
        const columns = boardOf(task(1, 'NEW'), task(2, 'ASSIGNED'));
        const before = JSON.stringify(columns);
        const res = applyOptimisticMove(columns, 1, 'ACCEPTED', null)!;
        expect(res.columns.ACCEPTED.map((t) => t.id)).toEqual([1]);
        // откат = вернуть прежний объект: он бит-в-бит прежний
        expect(JSON.stringify(columns)).toBe(before);
        expect(columns.NEW.map((t) => t.id)).toEqual([1]);
        expect(columns.ACCEPTED).toEqual([]);
    });

    it('неизвестная карточка → null', () => {
        const columns = boardOf(task(1, 'NEW'));
        expect(applyOptimisticMove(columns, 999, 'ASSIGNED', null)).toBeNull();
    });
});
