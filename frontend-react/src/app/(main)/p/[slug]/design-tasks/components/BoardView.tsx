'use client';

import { useState } from 'react';
import { formatNumber } from '@/lib/utils';
import {
    DESIGN_BOARD_STATUSES,
    DESIGN_STATUS_LABEL,
    findTaskColumn,
    type BoardColumns,
    type DesignBoardStatus,
} from '@/lib/design';
import TaskCard from './TaskCard';

interface BoardViewProps {
    columns: BoardColumns;
    /**
     * Флаг `permissions.can_reorder` из ответа GET /board (§6.9 — считает бэк).
     * false → dnd разрешён только МЕЖДУ колонками, перестановка внутри своей
     * колонки заблокирована (Р3/Р4; бэк вернул бы 403 на такой move).
     */
    canReorder: boolean;
    onOpen: (taskId: number) => void;
    /** Запрос перемещения (страница решает: сразу move или модалка причины для REVISION). */
    onMoveRequest: (taskId: number, toStatus: DesignBoardStatus, beforeTaskId: number | null) => void;
}

/** Доска: 6 колонок, нативный HTML5 drag&drop (Р8). */
export default function BoardView({ columns, canReorder, onOpen, onMoveRequest }: BoardViewProps) {
    const [dragTaskId, setDragTaskId] = useState<number | null>(null);
    const [dragOverCol, setDragOverCol] = useState<DesignBoardStatus | null>(null);

    const handleDrop = (toStatus: DesignBoardStatus, beforeTaskId: number | null) => {
        setDragOverCol(null);
        if (dragTaskId == null) return;
        const fromStatus = findTaskColumn(columns, dragTaskId);
        // Перестановка внутри колонки — только по флагу бэка can_reorder; прочим dnd только между колонками.
        if (fromStatus === toStatus && !canReorder) {
            setDragTaskId(null);
            return;
        }
        onMoveRequest(dragTaskId, toStatus, beforeTaskId);
        setDragTaskId(null);
    };

    return (
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', overflowX: 'auto', paddingBottom: 8 }}>
            {DESIGN_BOARD_STATUSES.map((status) => {
                const tasks = columns[status];
                return (
                    <div
                        key={status}
                        onDragOver={(e) => {
                            e.preventDefault();
                            if (dragOverCol !== status) setDragOverCol(status);
                        }}
                        onDragLeave={() => setDragOverCol((c) => (c === status ? null : c))}
                        onDrop={(e) => {
                            e.preventDefault();
                            handleDrop(status, null);
                        }}
                        style={{
                            flex: '0 0 240px',
                            minWidth: 240,
                            background: dragOverCol === status ? 'var(--color-bg-hover)' : 'transparent',
                            border: `1px ${dragOverCol === status ? 'dashed var(--color-accent)' : 'solid transparent'}`,
                            borderRadius: 12,
                            padding: 6,
                            transition: 'background 0.2s',
                        }}
                    >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 6px 10px' }}>
                            <span style={{ fontSize: 13, fontWeight: 600 }}>{DESIGN_STATUS_LABEL[status]}</span>
                            <span
                                className="badge badge-secondary"
                                style={{ fontSize: 11 }}
                            >
                                {formatNumber(tasks.length, 0)}
                            </span>
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minHeight: 60 }}>
                            {tasks.map((t) => (
                                <TaskCard
                                    key={t.id}
                                    t={t}
                                    draggable
                                    onDragStart={(e) => {
                                        e.dataTransfer.effectAllowed = 'move';
                                        e.dataTransfer.setData('text/plain', String(t.id));
                                        setDragTaskId(t.id);
                                    }}
                                    onDragOver={(e) => {
                                        e.preventDefault();
                                        e.stopPropagation();
                                        if (dragOverCol !== status) setDragOverCol(status);
                                    }}
                                    onDrop={(e) => {
                                        e.preventDefault();
                                        e.stopPropagation();
                                        // Бросок на карточку = вставить ПЕРЕД ней.
                                        handleDrop(status, t.id);
                                    }}
                                    onClick={() => onOpen(t.id)}
                                />
                            ))}
                            {tasks.length === 0 && (
                                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', textAlign: 'center', padding: 12 }}>
                                    Пусто
                                </div>
                            )}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
