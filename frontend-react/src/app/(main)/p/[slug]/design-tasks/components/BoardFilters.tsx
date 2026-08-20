'use client';

import { useMemo } from 'react';
import { formatNumber } from '@/lib/utils';
import SearchSelect from '@/components/SearchSelect';
import InfoTip from '@/components/InfoTip';
import { DESIGN_WORK_TYPE_LABEL } from '@/lib/design';
import { DESIGN_UI_HINT } from '@/lib/designHints';
import type { DesignAttributeOut, DesignLabelOut, DesignTaskListItem, DesignWorkType } from '@/types/api';

/** Что показывать вместо доски. 'board' — шесть колонок, остальное — плоский список. */
export type BoardScope = 'board' | 'ON_HOLD' | 'CANCELLED';

export interface BoardFilterState {
    scope: BoardScope;
    assignee: string;   // '' = все; UNASSIGNED — «Не назначен»
    workType: DesignWorkType | '';
    urgentOnly: boolean;
    q: string;
    /** Волна C: id метки; 0 = без фильтра. */
    labelId: number;
    /** Волна C: attributeId → выбранный valueId. Фильтруется по И между полями. */
    attributeValues: Record<number, number>;
}

/** Значение фильтра «исполнитель» для задач без исполнителя. */
export const UNASSIGNED = '__unassigned__';

export const EMPTY_BOARD_FILTERS: BoardFilterState = {
    scope: 'board',
    assignee: '',
    workType: '',
    urgentOnly: false,
    q: '',
    labelId: 0,
    attributeValues: {},
};

const WORK_TYPES = Object.keys(DESIGN_WORK_TYPE_LABEL) as DesignWorkType[];

/** Хоть один клиентский фильтр сужает выдачу (переключатель набора — не фильтр). */
export function hasActiveFilters(f: BoardFilterState): boolean {
    return !!(f.assignee || f.workType || f.urgentOnly || f.q.trim() || f.labelId
        || Object.values(f.attributeValues).some(Boolean));
}

/**
 * Применение фильтров — ЧИСТАЯ функция над уже загруженными задачами (Р21).
 * Сеть не трогается вообще: смена любого из четырёх фильтров пересчитывается
 * в браузере. Сетевой запрос делает только переключатель scope — «Отложенные»
 * и «Отменённые» на доске не лежат, это другой набор данных.
 */
export function applyBoardFilters(tasks: DesignTaskListItem[], f: BoardFilterState): DesignTaskListItem[] {
    const needle = f.q.trim().toLowerCase();
    return tasks.filter((t) => {
        if (f.assignee === UNASSIGNED) {
            if (t.assignee_name) return false;
        } else if (f.assignee && t.assignee_name !== f.assignee) {
            return false;
        }
        if (f.workType && t.work_type !== f.workType) return false;
        if (f.urgentOnly && !t.is_urgent) return false;
        if (f.labelId && !t.labels.some((l) => l.id === f.labelId)) return false;
        for (const valueId of Object.values(f.attributeValues)) {
            if (valueId && !t.attributes.some((a) => a.value_id === valueId)) return false;
        }
        if (needle
            && !t.title.toLowerCase().includes(needle)
            && !t.number.toLowerCase().includes(needle)) return false;
        return true;
    });
}

interface BoardFiltersProps {
    value: BoardFilterState;
    onChange: (next: BoardFilterState) => void;
    /** Задачи текущего набора — из них собирается список исполнителей. */
    tasks: DesignTaskListItem[];
    onHoldCount: number;
    cancelledCount: number;
    /** Справочники проекта: селекты рисуются только по активным записям. */
    labels: DesignLabelOut[];
    attributes: DesignAttributeOut[];
}

/** Строка фильтров над доской (Р21): вместо двух overlay-панелей — обычные контролы. */
export default function BoardFilters({ value, onChange, tasks, onHoldCount, cancelledCount, labels, attributes }: BoardFiltersProps) {
    const set = <K extends keyof BoardFilterState>(key: K, v: BoardFilterState[K]) =>
        onChange({ ...value, [key]: v });

    const assigneeOptions = useMemo(() => {
        const names = [...new Set(tasks.map((t) => t.assignee_name).filter((n): n is string => !!n))].sort();
        return [
            { value: UNASSIGNED, label: 'Не назначен' },
            ...names.map((n) => ({ value: n, label: n })),
        ];
    }, [tasks]);

    const scopeOptions: { value: BoardScope; label: string }[] = [
        { value: 'board', label: 'На доске' },
        { value: 'ON_HOLD', label: `Отложенные (${formatNumber(onHoldCount, 0)})` },
        { value: 'CANCELLED', label: `Отменённые (${formatNumber(cancelledCount, 0)})` },
    ];

    return (
        <div
            className="glass-card"
            style={{ marginBottom: 12, padding: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}
        >
            <InfoTip text={DESIGN_UI_HINT.boardShow} icon>
                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Показывать</span>
            </InfoTip>
            <div style={{ display: 'flex', gap: 4 }}>
                {scopeOptions.map((o) => (
                    <button
                        key={o.value}
                        className={`btn btn-sm ${value.scope === o.value ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => set('scope', o.value)}
                    >
                        {o.label}
                    </button>
                ))}
            </div>

            <SearchSelect
                value={value.assignee}
                onChange={(v) => set('assignee', v)}
                options={assigneeOptions}
                placeholder="Все исполнители"
                allLabel="Все исполнители"
                searchPlaceholder="Имя исполнителя…"
            />
            <SearchSelect
                value={value.workType}
                onChange={(v) => set('workType', v as DesignWorkType | '')}
                options={WORK_TYPES.map((w) => ({ value: w, label: DESIGN_WORK_TYPE_LABEL[w] }))}
                placeholder="Все типы работ"
                allLabel="Все типы работ"
                searchPlaceholder="Тип работы…"
            />

            {labels.length > 0 && (
                <SearchSelect
                    value={value.labelId ? String(value.labelId) : ''}
                    onChange={(v) => set('labelId', v ? Number(v) : 0)}
                    options={labels.map((l) => ({ value: String(l.id), label: l.name }))}
                    placeholder="Все метки"
                    allLabel="Все метки"
                    searchPlaceholder="Метка…"
                />
            )}
            {attributes.filter((a) => a.values.length > 0).map((a) => (
                <SearchSelect
                    key={a.id}
                    value={value.attributeValues[a.id] ? String(value.attributeValues[a.id]) : ''}
                    onChange={(v) => {
                        const next = { ...value.attributeValues };
                        if (v) next[a.id] = Number(v);
                        else delete next[a.id];
                        set('attributeValues', next);
                    }}
                    options={a.values.map((v) => ({ value: String(v.id), label: v.value }))}
                    placeholder={`Все: ${a.name}`}
                    allLabel={`Все: ${a.name}`}
                    searchPlaceholder={`${a.name}…`}
                />
            ))}

            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                <input type="checkbox" checked={value.urgentOnly} onChange={(e) => set('urgentOnly', e.target.checked)} />
                Только срочные
            </label>

            <input
                className="form-input"
                style={{ width: 220, marginLeft: 'auto' }}
                placeholder="Поиск: название или номер"
                value={value.q}
                onChange={(e) => set('q', e.target.value)}
            />
        </div>
    );
}
