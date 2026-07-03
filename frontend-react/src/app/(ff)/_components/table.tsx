'use client';

/**
 * Shared table primitives for the FF-portal list pages: client-side sorting
 * (lists are capped server-side at 500 rows → instant), sortable headers, and a
 * segmented tab control (stock projects / archive switch). Dependency-free so the
 * list-page and detail-page work can consume the same primitives.
 */

import { useCallback, useMemo, useState, type ReactNode } from 'react';

export type SortDir = 'asc' | 'desc';
export type SortValue = string | number | null | undefined;

/**
 * Client-side sort over `rows`. `getValue(row, key)` returns the comparable value
 * for a column key. Nulls sort last; strings compare with the ru locale. Wrap
 * `getValue` in `useCallback` to avoid re-sorting on every render (optional —
 * cheap at ≤500 rows).
 */
export function useTableSort<T>(
    rows: T[],
    getValue: (row: T, key: string) => SortValue,
    initial?: { key: string; dir?: SortDir },
) {
    const [sort, setSort] = useState<{ key: string | null; dir: SortDir }>({
        key: initial?.key ?? null,
        dir: initial?.dir ?? 'asc',
    });

    const toggleSort = useCallback((key: string) => {
        setSort((s) =>
            s.key === key
                ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
                : { key, dir: 'asc' },
        );
    }, []);

    const sorted = useMemo(() => {
        if (!sort.key) return rows;
        const key = sort.key;
        const arr = [...rows];
        arr.sort((a, b) => {
            const va = getValue(a, key);
            const vb = getValue(b, key);
            if (va == null && vb == null) return 0;
            if (va == null) return 1;
            if (vb == null) return -1;
            let cmp: number;
            if (typeof va === 'number' && typeof vb === 'number') cmp = va - vb;
            else cmp = String(va).localeCompare(String(vb), 'ru');
            return sort.dir === 'asc' ? cmp : -cmp;
        });
        return arr;
    }, [rows, sort, getValue]);

    return { sorted, sortKey: sort.key, sortDir: sort.dir, toggleSort };
}

/** Clickable sortable `<th>`. Pass `align="right"` (adds `.ff-num`) for numbers. */
export function SortableTh({
    label,
    sortKey,
    activeKey,
    dir,
    onSort,
    align,
}: {
    label: ReactNode;
    sortKey: string;
    activeKey: string | null;
    dir: SortDir;
    onSort: (key: string) => void;
    align?: 'left' | 'right';
}) {
    const active = activeKey === sortKey;
    const cls =
        `ff-th-sort${active ? ' ff-th-active' : ''}` +
        `${align === 'right' ? ' ff-num' : ''}`;
    return (
        <th className={cls} onClick={() => onSort(sortKey)}>
            <span className="ff-th-label">
                {label}
                <span className="ff-th-arrow">{active ? (dir === 'asc' ? '▲' : '▼') : ''}</span>
            </span>
        </th>
    );
}

export interface FfTabItem {
    key: string;
    label: string;
    count?: number;
}

/** Segmented control for stock-project / archive switching. */
export function FfTabs({
    items,
    active,
    onChange,
}: {
    items: FfTabItem[];
    active: string;
    onChange: (key: string) => void;
}) {
    return (
        <div className="ff-tabs" role="tablist">
            {items.map((it) => (
                <button
                    key={it.key}
                    type="button"
                    role="tab"
                    aria-selected={active === it.key}
                    className={`ff-tab${active === it.key ? ' ff-tab-active' : ''}`}
                    onClick={() => onChange(it.key)}
                >
                    {it.label}
                    {it.count != null && <span className="ff-tab-count">{it.count}</span>}
                </button>
            ))}
        </div>
    );
}
