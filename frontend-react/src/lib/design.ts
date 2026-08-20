/**
 * Дизайн карточек — чистая логика доски + словари подписей.
 *
 * Источник правды по статусам/переходам — бэк (backend/models/design.py, Р1);
 * здесь — UI-зеркало для подписей и affordance'ов (какие кнопки показать).
 * Валидность перехода решает ТОЛЬКО сервер: запрещённый dnd отбивается 400 →
 * откат + toast (Р4), фронт переходы не гейтит.
 */
import type {
    DesignBoardResponse,
    DesignComplexity,
    DesignLabelColor,
    DesignTaskListItem,
    DesignTaskStatus,
    DesignVerdict,
    DesignWorkType,
} from '@/types/api';

// ─── Палитра меток (Р26) ─────────────────────────────────────────────────────

/**
 * Десять цветов метки. Фронт НИГДЕ не пишет hex: цвет приезжает с бэка ключом,
 * а рисуется классом `.dds-label--{key}` из globals.css. Так палитра остаётся
 * в одном месте и переживает смену темы.
 */
export const LABEL_COLORS: DesignLabelColor[] = [
    'red', 'orange', 'amber', 'green', 'teal', 'blue', 'violet', 'pink', 'brown', 'slate',
];

export const LABEL_COLOR_LABEL: Record<DesignLabelColor, string> = {
    red: 'Красный',
    orange: 'Оранжевый',
    amber: 'Янтарный',
    green: 'Зелёный',
    teal: 'Бирюзовый',
    blue: 'Синий',
    violet: 'Фиолетовый',
    pink: 'Розовый',
    brown: 'Коричневый',
    slate: 'Серый',
};

/** CSS-класс модификатора цвета метки. Неизвестный ключ с бэка → нейтральный серый. */
export function labelColorClass(color: string): string {
    return (LABEL_COLORS as string[]).includes(color)
        ? `dds-label--${color}`
        : 'dds-label--slate';
}

// ─── Словари ─────────────────────────────────────────────────────────────────

/** 6 колонок доски PRD §4 (ON_HOLD/CANCELLED — вне доски, плоским списком). */
export const DESIGN_BOARD_STATUSES = [
    'NEW', 'ASSIGNED', 'IN_PROGRESS', 'REVIEW', 'REVISION', 'ACCEPTED',
] as const;

export type DesignBoardStatus = (typeof DESIGN_BOARD_STATUSES)[number];

export const DESIGN_STATUS_LABEL: Record<DesignTaskStatus, string> = {
    NEW: 'Новые заявки',
    ASSIGNED: 'Назначена',
    IN_PROGRESS: 'В работе',
    REVIEW: 'На проверке',
    REVISION: 'Правки',
    ON_HOLD: 'Отложена',
    ACCEPTED: 'Принято',
    CANCELLED: 'Отменена',
};

export const DESIGN_STATUS_BADGE: Record<DesignTaskStatus, string> = {
    NEW: 'badge-info',
    ASSIGNED: 'badge-secondary',
    IN_PROGRESS: 'badge-info',
    REVIEW: 'badge-warning',
    REVISION: 'badge-danger',
    ON_HOLD: 'badge-secondary',
    ACCEPTED: 'badge-success',
    CANCELLED: 'badge-secondary',
};

export const DESIGN_WORK_TYPE_LABEL: Record<DesignWorkType, string> = {
    MAIN_PHOTO: 'Главное фото',
    FULL_SET: 'Полный комплект',
    EDIT: 'Правка',
    RICH: 'Rich-контент',
    VIDEO: 'Видео',
};

export const DESIGN_COMPLEXITY_LABEL: Record<DesignComplexity, string> = {
    S: 'S — простая',
    M: 'M — средняя',
    L: 'L — сложная',
};

export const DESIGN_VERDICT_LABEL: Record<DesignVerdict, string> = {
    PENDING: 'На проверке',
    ACCEPTED: 'Принята',
    REJECTED: 'Возвращена',
};

/**
 * UI-зеркало словаря переходов Р1 (DESIGN_TASK_TRANSITIONS) — справочное.
 * Кнопки переходов в деталке строятся по `task.allowed_transitions` с бэка
 * (amendment M3, 2026-08-02); здесь словарь остаётся только как подпись-зеркало
 * графа для UI-логики, прав он не выражает. Сервер валидирует каждый переход сам.
 */
export const DESIGN_TRANSITIONS_UI: Record<DesignTaskStatus, DesignTaskStatus[]> = {
    NEW: ['ASSIGNED', 'ON_HOLD', 'CANCELLED'],
    ASSIGNED: ['IN_PROGRESS', 'NEW', 'ON_HOLD', 'CANCELLED'],
    IN_PROGRESS: ['REVIEW', 'ON_HOLD', 'CANCELLED'],
    REVIEW: ['ACCEPTED', 'REVISION', 'CANCELLED'],
    REVISION: ['REVIEW', 'ON_HOLD', 'CANCELLED'],
    ON_HOLD: ['NEW', 'ASSIGNED', 'IN_PROGRESS', 'REVISION', 'CANCELLED'],
    ACCEPTED: [],
    CANCELLED: [],
};

// ─── Доска: маппинг и оптимистичное перемещение ──────────────────────────────

export type BoardColumns = Record<DesignBoardStatus, DesignTaskListItem[]>;

/** GET /board → строго 6 колонок в порядке доски; отсутствующие ключи → []. */
export function buildBoardColumns(res: DesignBoardResponse): BoardColumns {
    const columns = {} as BoardColumns;
    for (const status of DESIGN_BOARD_STATUSES) {
        columns[status] = res.columns[status] ?? [];
    }
    return columns;
}

/** Колонка, в которой сейчас лежит карточка (null — не на доске). */
export function findTaskColumn(columns: BoardColumns, taskId: number): DesignBoardStatus | null {
    for (const status of DESIGN_BOARD_STATUSES) {
        if (columns[status].some((t) => t.id === taskId)) return status;
    }
    return null;
}

export interface OptimisticMove {
    /** Новое состояние колонок (исходное НЕ мутируется — годится для отката). */
    columns: BoardColumns;
    /** after_task_id для POST /move: карточка, ПОСЛЕ которой встали (null — в голову). */
    afterTaskId: number | null;
    /** false — позиция не изменилась (API звать не нужно). */
    changed: boolean;
}

/**
 * Оптимистичное перемещение карточки. `beforeTaskId` — карточка, ПЕРЕД которой
 * бросили (null — в конец колонки). Возвращает null, если карточки нет на доске.
 */
export function applyOptimisticMove(
    columns: BoardColumns,
    taskId: number,
    toStatus: DesignBoardStatus,
    beforeTaskId: number | null,
): OptimisticMove | null {
    const fromStatus = findTaskColumn(columns, taskId);
    if (!fromStatus) return null;

    const fromIndex = columns[fromStatus].findIndex((t) => t.id === taskId);
    const card = columns[fromStatus][fromIndex];

    // Новые массивы: исходный объект остаётся снапшотом для отката.
    const next = {} as BoardColumns;
    for (const status of DESIGN_BOARD_STATUSES) {
        next[status] = columns[status].filter((t) => t.id !== taskId);
    }

    const target = next[toStatus];
    let insertIndex = target.length;
    if (beforeTaskId != null && beforeTaskId !== taskId) {
        const idx = target.findIndex((t) => t.id === beforeTaskId);
        if (idx >= 0) insertIndex = idx;
    }
    const afterTaskId = insertIndex > 0 ? target[insertIndex - 1].id : null;
    target.splice(insertIndex, 0, { ...card, status: toStatus });

    const changed = toStatus !== fromStatus || insertIndex !== fromIndex;
    return { columns: next, afterTaskId, changed };
}

// ─── Мелкие утилиты ──────────────────────────────────────────────────────────

/** Скачивание blob'а под оригинальным именем (материалы/файлы версий). */
export function downloadBlob(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

/** Валидация ссылки на ТЗ-таблицу (Р13): только http/https-URL. */
export function isValidHttpUrl(value: string): boolean {
    try {
        const u = new URL(value);
        return u.protocol === 'http:' || u.protocol === 'https:';
    } catch {
        return false;
    }
}
