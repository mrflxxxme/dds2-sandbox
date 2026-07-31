'use client';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { COLUMN_BY_KEY, accumulate, textScaleColor, rankPos, type Shading, type ColumnDef, type ColumnGroup, type ColumnLayout, type Row } from './columns';

/* ─── Таблица воронки ──────────────────────────────────────────────────────
 * Один рендерер на все группировки. Колонки и их группы приходят раскладкой
 * (ColumnLayout), первая колонка — «подпись строки» — задаётся вызывающим,
 * потому что у дня, артикула и бренда она выглядит по-разному.
 * ─────────────────────────────────────────────────────────────────────── */

export const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

export interface Section { group: ColumnGroup | null; cols: ColumnDef[] }

/** Видимые колонки в порядке групп; extendedOnly отсекаются без расширенного режима. */
export function buildSections(layout: ColumnLayout, extended: boolean): Section[] {
    const visible = new Set(layout.visible);
    const usable = (k: string) => {
        const c = COLUMN_BY_KEY[k];
        return c && visible.has(k) && (extended || !c.extendedOnly) ? c : null;
    };
    const out: Section[] = [];
    for (const g of layout.groups) {
        const cols = g.cols.map(usable).filter(Boolean) as ColumnDef[];
        if (cols.length) out.push({ group: g, cols });
    }
    const rest = layout.ungrouped.map(usable).filter(Boolean) as ColumnDef[];
    if (rest.length) out.push({ group: null, cols: rest });
    return out;
}

export interface SortState { key: string; dir: 'asc' | 'desc' }

interface Props {
    rows: Row[];
    layout: ColumnLayout;
    extended: boolean;
    loading?: boolean;
    /** Заголовок первой колонки: «ДЕНЬ», «ТОВАР», «БРЕНД»… */
    labelHeader: string;
    labelWidth?: number;
    /** Подпись строки; depth — уровень вложенности (0 = верхний). */
    labelCell: (row: Row, depth: number) => React.ReactNode;
    rowKey: (row: Row, index: number, depth: number) => string;
    /** Дети строки для раскрытия (undefined/[] = строка не раскрывается). */
    childrenOf?: (row: Row) => Row[] | undefined;
    /* ─── Ленивое дерево: дети приезжают запросом при раскрытии ───
     * Всё дерево целиком — это десятки мегабайт на реальных данных, из которых
     * видно верхние 30 строк. Поэтому уровень запрашивается по требованию. */
    /** Ключ узла для пути к нему (nodeKey задан → дерево ленивое). */
    nodeKey?: (row: Row) => string;
    /** Есть ли уровень ниже (флаг с бэка), пока дети ещё не загружены. */
    hasChildren?: (row: Row) => boolean;
    /** Загруженные дети по пути; undefined — ещё не загружены. */
    childrenAt?: (path: string[]) => Row[] | undefined;
    /** Запросить детей узла по его пути. */
    onExpandPath?: (path: string[]) => void;
    /** Сортировка по подписи (напр. по дате); без неё первая колонка не сортируется. */
    labelValue?: (row: Row) => number | string;
    sort: SortState | null;
    onSort: (key: string) => void;
    /** Красит подпись (выходные — красным). */
    labelColor?: (row: Row) => string | undefined;
    /** Строки — это отрезки времени (группировка по дням). Тогда снимки (остатки)
     *  в ИТОГО не суммируются, а берутся за последний день периода. */
    timeSeries?: boolean;
    /** Как показывать величину в ячейке: цвет цифр / полоска / заливка. */
    shading?: Shading;
    /** Порядок строк, пока пользователь не кликнул по колонке. Применяется на КАЖДОМ
     *  уровне отдельно: у дат — своя хронология, у прочих — своя метрика. */
    defaultOrder?: (rows: Row[]) => Row[];
    emptyText?: string;
    footer?: React.ReactNode;
}

const TOTAL_BG = '#f5f3ff';

export default function FunnelTable({
    rows, layout, extended, loading, labelHeader, labelWidth = 210, labelCell, rowKey,
    childrenOf, nodeKey, hasChildren, childrenAt, onExpandPath,
    labelValue, sort, onSort, labelColor, timeSeries, shading = 'bar', defaultOrder,
    emptyText = 'Нет данных за выбранный период', footer,
}: Props) {
    const sections = useMemo(() => buildSections(layout, extended), [layout, extended]);
    const flatCols = useMemo(() => sections.flatMap(s => s.cols), [sections]);
    // Ширина по умолчанию — от единицы измерения: проценты узкие, рубли широкие
    const colWidth = (c: ColumnDef) => c.w ?? (c.unit === 'CR' ? 70 : c.unit === '#' ? 100 : 120);
    const totalWidth = useMemo(() => labelWidth + flatCols.reduce((n, c) => n + colWidth(c), 0), [flatCols, labelWidth]);
    const totals = useMemo(() => accumulate(rows), [rows]);
    // Последняя строка периода — источник значений для снимков (остатков) в ИТОГО
    const lastRow = useMemo(() => {
        if (!timeSeries || !labelValue || rows.length === 0) return null;
        return rows.reduce((best, r) => (String(labelValue(r)) > String(labelValue(best)) ? r : best), rows[0]);
    }, [rows, timeSeries, labelValue]);
    const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
    const bandRef = useRef<HTMLTableRowElement>(null);
    const [bandH, setBandH] = useState(28);

    /* ─── Плотность строк: месяц данных должен помещаться на лист ─────────────
     * Высота строки не константа, а остаток экрана, делённый на число строк
     * (не больше месяца — 31). Это МИНИМАЛЬНАЯ высота ячейки: двухстрочные
     * подписи (артикул + бренд·предмет) по-прежнему разъезжаются как надо.
     * Ниже MIN_ROW не жмём — цифры перестают читаться, лучше прокрутка.
     * ─────────────────────────────────────────────────────────────────── */
    const FIT_ROWS = 31, MIN_ROW = 13, MAX_ROW = 24;
    const scrollRef = useRef<HTMLDivElement>(null);
    const headRef = useRef<HTMLTableSectionElement>(null);
    const totalRef = useRef<HTMLTableRowElement>(null);
    const [rowH, setRowH] = useState(MAX_ROW);

    useEffect(() => {
        const box = scrollRef.current;
        if (!box) return;
        const measure = () => {
            const headH = headRef.current?.offsetHeight ?? 0;
            const totalH = totalRef.current?.offsetHeight ?? 0;
            // Место под строки: высота окна прокрутки без шапки и ИТОГО, минус
            // горизонтальная полоса прокрутки (таблица всегда шире экрана).
            const free = box.clientHeight - headH - totalH;
            const target = Math.min(Math.max(rows.length, 1), FIT_ROWS);
            setRowH(Math.max(MIN_ROW, Math.min(MAX_ROW, Math.floor(free / target))));
        };
        measure();
        const ro = new ResizeObserver(measure);
        ro.observe(box);
        if (headRef.current) ro.observe(headRef.current);
        return () => ro.disconnect();
    }, [rows.length, sections.length]);

    // Высота ряда-полосы нужна, чтобы второй ряд шапки прилипал ровно под ней
    useEffect(() => {
        const el = bandRef.current;
        if (!el) return;
        const measure = () => setBandH(el.offsetHeight);
        measure();
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, [sections.length]);

    // Сортировка сквозная: тот же порядок применяется и к раскрытым уровням,
    // иначе родители отсортированы, а дети внутри — в порядке, пришедшем с бэка
    const sortRows = useMemo(() => {
        if (!sort) return defaultOrder ?? ((rs: Row[]) => rs);
        const col = COLUMN_BY_KEY[sort.key];
        const val = (r: Row): number | string => {
            if (sort.key === '__label__') return labelValue ? labelValue(r) : '';
            return col?.value(r) ?? Number.NEGATIVE_INFINITY;
        };
        const sign = sort.dir === 'desc' ? -1 : 1;
        return (rs: Row[]) => [...rs].sort((a, b) => {
            const x = val(a), y = val(b);
            if (typeof x === 'string' || typeof y === 'string') return sign * String(x).localeCompare(String(y), 'ru');
            return sign * (x - y);
        });
    }, [sort, labelValue, defaultOrder]);
    const sorted = useMemo(() => sortRows(rows), [rows, sortRows]);

    /** Значение ИТОГО по колонке — точка отсчёта окраски (считаем один раз на рендер). */
    const totalOf = useMemo(() => {
        const map = new Map<string, number | null>();
        for (const c of flatCols) {
            const v = timeSeries && c.snapshot
                ? (lastRow ? c.value(lastRow) : null)
                : c.total === 'sum'
                    ? (Object.prototype.hasOwnProperty.call(totals, c.key) ? (totals as unknown as Record<string, number>)[c.key] : sumOf(rows, c))
                    : c.total(totals);
            map.set(c.key, v);
        }
        return map;
    }, [flatCols, totals, rows, timeSeries, lastRow]);

    /** Среднее по строкам одного уровня — точка отсчёта окраски.
     *  Аддитивные колонки: сумма / число строк. Производные (проценты, CPC, С/С ед.):
     *  их ИТОГО уже средневзвешенное, делить нельзя. */
    const baselineFor = (list: Row[]): Map<string, number | null> => {
        const map = new Map<string, number | null>();
        if (list.length === 0) return map;
        const t = accumulate(list);
        const n = list.length;
        for (const c of flatCols) {
            if (!c.dir || c.dir === 'none') continue;
            map.set(c.key, c.total === 'sum' ? sumOf(list, c) / n : c.total(t));
        }
        return map;
    };

    /** Отсортированные значения колонки на уровне — основа серой шкалы величины (по рангу). */
    const rangeFor = (list: Row[]): Map<string, number[]> => {
        const map = new Map<string, number[]>();
        if (list.length < 2) return map;   // на одной строке шкала бессмысленна
        for (const c of flatCols) {
            const vals: number[] = [];
            for (const r of list) {
                const v = c.value(r);
                if (v != null && Number.isFinite(v)) vals.push(v);
            }
            if (vals.length > 1 && vals[0] !== vals[vals.length - 1]) {
                vals.sort((a, b) => a - b);
                if (vals[0] !== vals[vals.length - 1]) map.set(c.key, vals);
            }
        }
        return map;
    };

    const topBase = useMemo(() => baselineFor(rows), [rows, flatCols, timeSeries]);   // eslint-disable-line react-hooks/exhaustive-deps
    const topRange = useMemo(() => rangeFor(rows), [rows, flatCols]);   // eslint-disable-line react-hooks/exhaustive-deps

    const arrow = (key: string) => (sort?.key === key ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : '');

    // Плотность и палитра — как в списке кампаний «Управления рекламой»:
    // th 10.5/700 на тёмном, td 12px с шагом 3px, разделители #e8eaed, без заливки строк.
    const stickyLabel = (bg: string, depth = 0, extra?: React.CSSProperties): React.CSSProperties => ({
        position: 'sticky', left: 0, zIndex: 2, background: bg,
        width: labelWidth, minWidth: labelWidth, maxWidth: labelWidth,   // ширина не зависит от раскрытия
        padding: `0 8px 0 ${8 + depth * 14}px`, borderRight: '1px solid #d1d5db',
        borderBottom: '1px solid rgba(15,23,42,.07)', boxShadow: 'inset -6px 0 6px -6px rgba(0,0,0,0.06)',
        // line-height наследуется внутрь: подписи в ячейке сжимаются вместе с ней
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 12, lineHeight: 1, ...extra,
    });

    /** Позиция значения в своей колонке: 1 — лучшее, 0 — худшее (для 'down' шкала перевёрнута). */
    const posOf = (c: ColumnDef, v: number | null, range: Map<string, number[]>): number | null => {
        if (v == null || !c.dir || c.dir === 'none') return null;
        const vals = range.get(c.key);
        if (!vals) return null;
        const p = rankPos(vals, v);
        return c.dir === 'up' ? p : 1 - p;
    };

    const cellStyle = (c: ColumnDef, s: Section, first: boolean, v: number | null, r: Row,
                       range: Map<string, number[]>): React.CSSProperties => {
        // Цвет цифр — условное форматирование внутри своей колонки;
        // колонки без направления (цена, остатки) держат свой абсолютный светофор
        const pos = posOf(c, v, range);
        return {
            position: 'relative', textAlign: 'right', padding: '0 8px', fontSize: 12, lineHeight: 1, fontFamily: MONO, whiteSpace: 'nowrap',
            borderBottom: '1px solid rgba(15,23,42,.07)',
            borderRight: '1px solid rgba(15,23,42,.06)',
            borderLeft: first && s.group ? `2px solid ${s.group.color}` : undefined,
            color: v == null ? '#c8ccd2' : pos != null ? textScaleColor(pos) : c.color?.(v, r) ?? '#111827',
            fontWeight: c.bold ? 600 : 400,
        };
    };

    /** Полоска величины под числом — не мешает читать цифры, но даёт сравнить строки. */
    const bar = (c: ColumnDef, s: Section, v: number | null, range: Map<string, number[]>) => {
        if (shading !== 'bar' || v == null) return null;
        const vals = range.get(c.key);
        if (!vals) return null;
        const pos = rankPos(vals, v);   // полоска показывает величину, без оценки
        if (pos <= 0) return null;
        return (
            <span aria-hidden style={{
                position: 'absolute', left: 4, right: 4, bottom: 1, height: 2, borderRadius: 1,
                background: `linear-gradient(to right, ${s.group?.color ?? '#94a3b8'} ${(pos * 100).toFixed(1)}%, transparent ${(pos * 100).toFixed(1)}%)`,
                opacity: .45,
            }} />
        );
    };

    const renderRow = (r: Row, i: number, depth: number, base: Map<string, number | null>, range: Map<string, number[]>,
                       parentPath: string[] = []): React.ReactNode => {
        const key = rowKey(r, i, depth);
        const lazy = !!nodeKey;
        const path = lazy ? [...parentPath, nodeKey(r)] : [];
        const kids = lazy ? childrenAt?.(path) : childrenOf?.(r);
        const canExpand = lazy ? !!hasChildren?.(r) : !!kids?.length;
        const open = expanded.has(key);
        const bg = depth > 0 ? '#fafbfc' : '#ffffff';   // без «зебры» — как в рекламе
        return (
            <React.Fragment key={key}>
                <tr className="fn-row" style={{ background: bg, height: rowH, cursor: canExpand ? 'pointer' : undefined }}
                    onClick={canExpand ? () => {
                        setExpanded(prev => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });
                        if (lazy && !open && kids === undefined) onExpandPath?.(path);
                    } : undefined}>
                    <td style={stickyLabel(bg, depth, { color: labelColor?.(r) })}>
                        <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
                            <span style={{ width: 11, flexShrink: 0, fontSize: 11, lineHeight: 1, color: canExpand ? '#64748b' : 'transparent' }}>{canExpand ? (open ? '▾' : '▶') : '·'}</span>
                            <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{labelCell(r, depth)}</span>
                        </span>
                    </td>
                    {sections.map(s => s.cols.map((c, ci) => {
                        const v = c.value(r);
                        return (
                            <td key={c.key} style={cellStyle(c, s, ci === 0, v, r, range)}>
                                {v == null ? '—' : (c.format ? c.format(v, r) : v.toLocaleString('ru-RU'))}
                                {bar(c, s, v, range)}
                            </td>
                        );
                    }))}
                </tr>
                {open && kids === undefined && (
                    <tr style={{ background: '#fafbfc' }}>
                        <td colSpan={flatCols.length + 1} style={{ padding: `2px 8px 2px ${22 + depth * 14}px`, fontSize: 11, color: 'var(--color-text-dim)' }}>Загрузка…</td>
                    </tr>
                )}
                {open && kids && (() => {
                    // дети сравниваются со своими соседями: и среднее, и диапазон — по их уровню
                    const kidsBase = baselineFor(kids);
                    const kidsRange = rangeFor(kids);
                    return sortRows(kids).map((k, ki) => renderRow(k, ki, depth + 1, kidsBase, kidsRange, path));
                })()}
            </React.Fragment>
        );
    };

    return (
        <>
            {/* Подсветка наведения кладётся background-image'ом поверх background-color:
                цветовые тиры порогов остаются видны, а box-shadow липкой колонки не ломается */}
            <style>{`
                .fn-row:hover > td { background-image: linear-gradient(rgba(15,23,42,.06), rgba(15,23,42,.06)); }
                .fn-row > td:hover {
                    background-image: linear-gradient(rgba(0,113,227,.10), rgba(0,113,227,.10));
                    box-shadow: inset 0 0 0 1px rgba(0,113,227,.55);
                }
                /* Заголовки кликабельны — показываем это наведением */
                .fn-th:hover { background-image: linear-gradient(rgba(255,255,255,.10), rgba(255,255,255,.10)); }
            `}</style>
            <div ref={scrollRef} style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
                {loading ? <div style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div> : (
                    <table style={{ borderCollapse: 'separate', borderSpacing: 0, tableLayout: 'fixed', width: '100%', minWidth: totalWidth, background: '#fff' }}>
                        <colgroup>
                            <col style={{ width: labelWidth }} />
                            {flatCols.map(c => <col key={c.key} style={{ width: colWidth(c) }} />)}
                        </colgroup>
                        <thead ref={headRef}>
                            <tr ref={bandRef}>
                                <th rowSpan={2} style={{ ...stickyLabel('#1f2937'), top: 0, zIndex: 24, verticalAlign: 'bottom', padding: '3px 8px', borderRight: '1px solid #4b5563', borderBottom: '1px solid #4b5563', cursor: labelValue ? 'pointer' : 'default', fontSize: 10.5, fontWeight: 700, letterSpacing: '.04em', color: '#e5e7eb', textAlign: 'left' }}
                                    onClick={labelValue ? () => onSort('__label__') : undefined}>
                                    {labelHeader}{arrow('__label__')}
                                </th>
                                {sections.map(s => (
                                    <th key={s.group?.key ?? '__rest__'} colSpan={s.cols.length}
                                        style={{
                                            position: 'sticky', top: 0, zIndex: 20, textAlign: 'center', padding: '3px 8px', lineHeight: 1.1,
                                            fontSize: 10, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase',
                                            background: '#1f2937', color: '#cbd5e1',
                                            borderLeft: s.group ? `2px solid ${s.group.color}` : undefined,
                                            borderBottom: '1px solid #4b5563', whiteSpace: 'nowrap',
                                        }}>
                                        {s.group
                                            ? <><span style={{ color: s.group.color }}>●</span> {s.group.label}</>
                                            : 'Прочее'}
                                    </th>
                                ))}
                            </tr>
                            <tr>
                                {sections.map(s => s.cols.map((c, ci) => (
                                    <th key={c.key} className="fn-th" title={c.title} onClick={() => onSort(c.key)}
                                        style={{
                                            position: 'sticky', top: bandH, zIndex: 19, textAlign: 'right', padding: '3px 8px', lineHeight: 1.1,
                                            fontSize: 10.5, fontWeight: 700, letterSpacing: '.02em',
                                            background: '#374151', color: sort?.key === c.key ? '#93c5fd' : '#e5e7eb',
                                            borderLeft: ci === 0 && s.group ? `2px solid ${s.group.color}` : undefined,
                                            borderRight: '1px solid rgba(255,255,255,.09)',
                                            borderBottom: '1px solid #4b5563', whiteSpace: 'nowrap', cursor: 'pointer', userSelect: 'none',
                                        }}>
                                        {c.label}{arrow(c.key)}
                                    </th>
                                )))}
                            </tr>
                        </thead>
                        <tbody>
                            {rows.length > 0 && (
                                <tr ref={totalRef} style={{ background: TOTAL_BG }}>
                                    <td style={stickyLabel(TOTAL_BG, 0, { fontWeight: 700, fontSize: 10.5, letterSpacing: '.05em', color: '#5b21b6' })}>ИТОГО</td>
                                    {sections.map(s => s.cols.map((c, ci) => {
                                        const v = totalOf.get(c.key) ?? null;
                                        return (
                                            <td key={c.key} style={{
                                                textAlign: 'right', padding: '2px 8px', fontSize: 12, lineHeight: 1.05, fontFamily: MONO, fontWeight: 700,
                                                whiteSpace: 'nowrap', color: v == null ? '#c4b5fd' : '#4c1d95', background: TOTAL_BG,
                                                borderBottom: '1px solid #ddd6fe', borderRight: '1px solid rgba(76,29,149,.10)',
                                                borderLeft: ci === 0 && s.group ? `2px solid ${s.group.color}` : undefined,
                                            }}>
                                                {v == null ? '—' : (c.format ? c.format(v, {}) : v.toLocaleString('ru-RU'))}
                                            </td>
                                        );
                                    }))}
                                </tr>
                            )}
                            {rows.length === 0 && (
                                <tr><td colSpan={flatCols.length + 1} style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>{emptyText}</td></tr>
                            )}
                            {sorted.map((r, i) => renderRow(r, i, 0, topBase, topRange))}
                        </tbody>
                    </table>
                )}
            </div>
            {footer}
        </>
    );
}

/** Сумма колонки по строкам — для 'sum'-колонок, которых нет в аккумуляторе (напр. вычисляемых). */
function sumOf(rows: Row[], c: ColumnDef): number {
    let s = 0;
    for (const r of rows) s += c.value(r) ?? 0;
    return s;
}
