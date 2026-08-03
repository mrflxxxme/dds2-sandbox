/**
 * Дизайн карточек — чистая математика месячного календаря (Ф5, Р7).
 *
 * CSS grid 7 колонок, неделя с понедельника, сетка добивается днями соседних
 * месяцев до полных недель. Раскраска чипов: по дизайнеру (стабильная палитра
 * по индексу из сортировки user_id, НЕ хэш имени) / по статусу (те же цвета,
 * что бейджи колонок доски). Только var(--color-*) (design.md).
 */
import {
    eachDayOfInterval,
    endOfMonth,
    endOfWeek,
    format,
    startOfWeek,
} from 'date-fns';
import type { DesignTaskStatus } from '@/types/api';

// ─── Месячная сетка ──────────────────────────────────────────────────────────

export interface CalendarDay {
    /** ISO-дата YYYY-MM-DD (ключ группировки задач по due_date). */
    iso: string;
    /** Число месяца (1..31). */
    dayOfMonth: number;
    /** День принадлежит отображаемому месяцу (соседние — приглушены). */
    inMonth: boolean;
    /** Сегодня (по локальной дате пользователя). */
    isToday: boolean;
}

/** Первое число месяца `YYYY-MM` как локальная дата (без TZ-сдвигов UTC-парсинга). */
export function monthFirstDay(month: string): Date {
    const [y, m] = month.split('-').map(Number);
    return new Date(y, m - 1, 1);
}

/**
 * Сетка месяца: массив недель (каждая ровно 7 дней, Пн→Вс),
 * от понедельника недели 1-го числа до воскресенья недели последнего.
 */
export function buildMonthGrid(month: string, todayIso?: string): CalendarDay[][] {
    const first = monthFirstDay(month);
    const gridStart = startOfWeek(first, { weekStartsOn: 1 });
    const gridEnd = endOfWeek(endOfMonth(first), { weekStartsOn: 1 });
    const today = todayIso ?? format(new Date(), 'yyyy-MM-dd');

    const weeks: CalendarDay[][] = [];
    let week: CalendarDay[] = [];
    for (const d of eachDayOfInterval({ start: gridStart, end: gridEnd })) {
        const iso = format(d, 'yyyy-MM-dd');
        week.push({
            iso,
            dayOfMonth: d.getDate(),
            inMonth: iso.slice(0, 7) === month,
            isToday: iso === today,
        });
        if (week.length === 7) {
            weeks.push(week);
            week = [];
        }
    }
    return weeks;
}

/** Сдвиг месяца `YYYY-MM` на delta месяцев (стрелки ←/→). */
export function shiftMonth(month: string, delta: number): string {
    const [y, m] = month.split('-').map(Number);
    const total = y * 12 + (m - 1) + delta;
    const ny = Math.floor(total / 12);
    const nm = (total % 12 + 12) % 12 + 1;
    return `${String(ny).padStart(4, '0')}-${String(nm).padStart(2, '0')}`;
}

/** Текущий месяц пользователя как `YYYY-MM`. */
export function currentMonth(): string {
    return format(new Date(), 'yyyy-MM');
}

const MONTH_NAMES = [
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
] as const;

/** «Январь 2027» для шапки календаря. */
export function formatMonthTitle(month: string): string {
    const [y, m] = month.split('-').map(Number);
    return `${MONTH_NAMES[m - 1]} ${y}`;
}

export const WEEKDAY_LABELS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'] as const;

// ─── Раскраска чипов ─────────────────────────────────────────────────────────

export type CalendarColorMode = 'designer' | 'status';

/** Цвет чипа без исполнителя / неизвестного исполнителя. */
export const UNASSIGNED_COLOR = 'var(--color-text-dim)';

/** Стабильная палитра дизайнеров — только var(--color-*) (design.md). */
export const DESIGNER_PALETTE = [
    'var(--color-accent)',
    'var(--color-success)',
    'var(--color-warning)',
    'var(--color-danger)',
    'var(--color-text-muted)',
    'var(--color-accent-hover)',
] as const;

/**
 * Цвета статусов = цвета бейджей колонок доски (DESIGN_STATUS_BADGE):
 * info→accent, warning, danger, success, secondary→muted/dim.
 */
export const DESIGN_STATUS_COLOR: Record<DesignTaskStatus, string> = {
    NEW: 'var(--color-accent)',
    ASSIGNED: 'var(--color-text-muted)',
    IN_PROGRESS: 'var(--color-accent)',
    REVIEW: 'var(--color-warning)',
    REVISION: 'var(--color-danger)',
    ON_HOLD: 'var(--color-text-dim)',
    ACCEPTED: 'var(--color-success)',
    CANCELLED: 'var(--color-text-dim)',
};

/** Минимальный срез участника проекта для палитры (совместим с ответом getMembers). */
export interface DesignMemberLike {
    user_id: number;
    username: string;
    first_name?: string | null;
    last_name?: string | null;
}

/**
 * Отображаемое имя участника — ЗЕРКАЛО backend `_user_names`
 * (queries.py/workload.py): «Имя Фамилия» или username. По нему матчится
 * `assignee_name` задач календаря (в DesignTaskListItem нет assignee_user_id —
 * контракт FROZEN).
 */
export function memberDisplayName(m: DesignMemberLike): string {
    return `${m.first_name || ''} ${m.last_name || ''}`.trim() || m.username;
}

/**
 * Имя исполнителя → цвет: индекс из СТАБИЛЬНОЙ сортировки user_id по
 * возрастанию (спек F5: не хэш имени, не порядок ответа API).
 */
export function buildAssigneeColors(members: DesignMemberLike[]): Map<string, string> {
    const sorted = [...members].sort((a, b) => a.user_id - b.user_id);
    const map = new Map<string, string>();
    sorted.forEach((m, i) => {
        map.set(memberDisplayName(m), DESIGNER_PALETTE[i % DESIGNER_PALETTE.length]);
    });
    return map;
}

// ─── Персональный календарь задачи ───────────────────────────────────────────

/**
 * Срез дат задачи для её личного календаря. Совместим И с `DesignTaskDetail`
 * (все четыре даты), И с `DesignTaskListItem` (там из дат есть только
 * `due_date`) — контракт API не меняется, всё берётся из уже загруженной
 * задачи, без дополнительных запросов.
 */
export interface TaskDatesLike {
    /** Поставлена (ISO datetime). */
    created_at?: string | null;
    /** Взята в работу (ISO datetime, null пока не взяли). */
    started_at?: string | null;
    /** Срок сдачи (YYYY-MM-DD, может отсутствовать). */
    due_date?: string | null;
    /** Принята (ISO datetime). */
    accepted_at?: string | null;
    /** Нужен только для просрочки: принятая/отменённая задача не просрочена. */
    status?: DesignTaskStatus;
}

export type TaskMarkerKind = 'created' | 'started' | 'due' | 'accepted';

export interface TaskRangeMarker {
    /** День события (YYYY-MM-DD). */
    iso: string;
    kind: TaskMarkerKind;
}

export interface TaskRange {
    /** Первый день полосы работы (YYYY-MM-DD); null — срок не задан. */
    start: string | null;
    /** Последний день полосы работы; null — срок не задан. */
    end: string | null;
    /** Сам срок (акцент в сетке); null — не задан. */
    due: string | null;
    /** Откуда взято начало полосы: started_at, иначе created_at. */
    startSource: 'started' | 'created' | null;
    /** Срок в прошлом и задача не завершена. */
    isOverdue: boolean;
    /** Точки-маркеры ключевых событий (в хронологическом порядке смысла). */
    markers: TaskRangeMarker[];
    /** Месяц, на котором открывать модалку: месяц срока, иначе события, иначе текущий. */
    month: string;
}

/** День YYYY-MM-DD из ISO-строки бэка (datetime или date), null-безопасно. */
function isoDay(value: string | null | undefined): string | null {
    return value ? value.slice(0, 10) : null;
}

/** Задача завершена → просрочка неактуальна. */
function isFinished(task: TaskDatesLike, accepted: string | null): boolean {
    return task.status === 'ACCEPTED' || task.status === 'CANCELLED' || accepted != null;
}

/**
 * Диапазон работы над задачей для её личного календаря.
 *
 * Полоса: от `started_at` (взята в работу), а если задачу ещё не взяли — от
 * `created_at` (поставлена), до `due_date`. Без `due_date` полосы нет вообще
 * (показываем только точки событий и подсказку «Срок не задан»). Если работу
 * начали ПОЗЖЕ срока — полоса разворачивается от срока до дня старта, чтобы
 * переработка была видна, а не схлопывалась в ноль.
 *
 * Сравнение дат — строками YYYY-MM-DD (как `is_overdue` бэка и деталка задачи),
 * не моментами Date: иначе просрочка краснеет на сутки раньше.
 */
export function buildTaskRange(task: TaskDatesLike, todayIso?: string): TaskRange {
    const today = todayIso ?? format(new Date(), 'yyyy-MM-dd');
    const created = isoDay(task.created_at);
    const started = isoDay(task.started_at);
    const accepted = isoDay(task.accepted_at);
    const due = isoDay(task.due_date);

    const anchor = started ?? created;
    let start: string | null = null;
    let end: string | null = null;
    if (due != null) {
        start = anchor != null && anchor < due ? anchor : due;
        end = anchor != null && anchor > due ? anchor : due;
    }

    const markers: TaskRangeMarker[] = [];
    if (created) markers.push({ iso: created, kind: 'created' });
    if (started) markers.push({ iso: started, kind: 'started' });
    if (due) markers.push({ iso: due, kind: 'due' });
    if (accepted) markers.push({ iso: accepted, kind: 'accepted' });

    return {
        start,
        end,
        due,
        startSource: started != null ? 'started' : created != null ? 'created' : null,
        isOverdue: due != null && due < today && !isFinished(task, accepted),
        markers,
        month: (due ?? started ?? created ?? today).slice(0, 7),
    };
}

/** День попадает в полосу работы (границы включительно). */
export function isInTaskRange(range: TaskRange, iso: string): boolean {
    return range.start != null && range.end != null && iso >= range.start && iso <= range.end;
}

/** Позиция дня в полосе — для скругления краёв (референс: сплошная полоса). */
export type TaskRangeEdge = 'single' | 'start' | 'end' | 'middle' | null;

export function taskRangeEdge(range: TaskRange, iso: string): TaskRangeEdge {
    if (!isInTaskRange(range, iso)) return null;
    if (range.start === range.end) return 'single';
    if (iso === range.start) return 'start';
    if (iso === range.end) return 'end';
    return 'middle';
}

/** Маркеры, сгруппированные по дню (в один день может попасть несколько событий). */
export function markersByDay(range: TaskRange): Map<string, TaskMarkerKind[]> {
    const map = new Map<string, TaskMarkerKind[]>();
    for (const m of range.markers) {
        const list = map.get(m.iso);
        if (list) list.push(m.kind);
        else map.set(m.iso, [m.kind]);
    }
    return map;
}

export const TASK_MARKER_LABEL: Record<TaskMarkerKind, string> = {
    created: 'Поставлена',
    started: 'Взята в работу',
    due: 'Срок сдачи',
    accepted: 'Принята',
};

/** Цвета маркеров — только var(--color-*) (design.md). */
export const TASK_MARKER_COLOR: Record<TaskMarkerKind, string> = {
    created: 'var(--color-text-muted)',
    started: 'var(--color-accent)',
    due: 'var(--color-warning)',
    accepted: 'var(--color-success)',
};
