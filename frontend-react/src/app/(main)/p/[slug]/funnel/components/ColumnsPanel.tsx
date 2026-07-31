'use client';
import React, { useEffect, useState } from 'react';
import { COLUMN_BY_KEY, GROUP_COLORS, defaultLayout, type ColumnLayout } from './columns';
import { IcX } from '../../ads-manager/components/icons';

/* ─── Менеджер колонок ────────────────────────────────────────────────────
 * Панель справа: группы колонок с цветом и счётчиком, чекбоксы, перетаскивание
 * колонок между группами и групп между собой, зона «без группы». Раскладка
 * приходит и уходит целиком (ColumnLayout) — состояние живёт на странице.
 * DnD — нативный HTML5 (в проекте нет dnd-библиотеки, для плоских списков хватает).
 * ─────────────────────────────────────────────────────────────────────── */

type Drag =
    | { kind: 'col'; key: string; from: string }   // from: ключ группы или '__none__'
    | { kind: 'group'; key: string };

const NONE = '__none__';

export default function ColumnsPanel({ layout, onChange, onClose }: {
    layout: ColumnLayout;
    onChange: (next: ColumnLayout) => void;
    onClose: () => void;
}) {
    const [drag, setDrag] = useState<Drag | null>(null);
    const [over, setOver] = useState<string | null>(null);
    const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());

    // Панель перекрывает кнопку «Колонки», поэтому закрыть её должно быть можно
    // тремя привычными способами: Esc, клик мимо и крестик
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [onClose]);

    const visible = new Set(layout.visible);
    const isOn = (k: string) => visible.has(k);

    const setVisible = (keys: string[], on: boolean) => {
        const next = new Set(layout.visible);
        keys.forEach(k => (on ? next.add(k) : next.delete(k)));
        onChange({ ...layout, visible: [...next] });
    };

    /** Переносит колонку в группу (или в «без группы»), опционально перед колонкой beforeKey. */
    const moveCol = (key: string, toGroup: string, beforeKey?: string) => {
        const groups = layout.groups.map(g => ({ ...g, cols: g.cols.filter(c => c !== key) }));
        let ungrouped = layout.ungrouped.filter(c => c !== key);
        const insert = (arr: string[]) => {
            const at = beforeKey ? arr.indexOf(beforeKey) : -1;
            if (at === -1) arr.push(key); else arr.splice(at, 0, key);
            return arr;
        };
        if (toGroup === NONE) ungrouped = insert(ungrouped);
        else {
            const g = groups.find(x => x.key === toGroup);
            if (!g) return;
            g.cols = insert([...g.cols]);
        }
        onChange({ ...layout, groups, ungrouped });
    };

    /** Меняет порядок групп: перетаскиваемую ставим перед целевой. */
    const moveGroup = (key: string, beforeKey: string) => {
        if (key === beforeKey) return;
        const rest = layout.groups.filter(g => g.key !== key);
        const moving = layout.groups.find(g => g.key === key);
        if (!moving) return;
        const at = rest.findIndex(g => g.key === beforeKey);
        rest.splice(at === -1 ? rest.length : at, 0, moving);
        onChange({ ...layout, groups: rest });
    };

    const addGroup = () => {
        const key = `g${layout.groups.length + 1}_${layout.groups.length}`;
        const color = GROUP_COLORS[layout.groups.length % GROUP_COLORS.length];
        onChange({ ...layout, groups: [...layout.groups, { key, label: 'Новая группа', color, cols: [] }] });
    };

    /** Удаление группы не теряет колонки — они уезжают в «без группы». */
    const removeGroup = (key: string) => {
        const g = layout.groups.find(x => x.key === key);
        if (!g) return;
        onChange({ ...layout, groups: layout.groups.filter(x => x.key !== key), ungrouped: [...layout.ungrouped, ...g.cols] });
    };

    const renameGroup = (key: string, label: string) =>
        onChange({ ...layout, groups: layout.groups.map(g => (g.key === key ? { ...g, label } : g)) });

    const cycleColor = (key: string) =>
        onChange({
            ...layout,
            groups: layout.groups.map(g => (g.key === key
                ? { ...g, color: GROUP_COLORS[(GROUP_COLORS.indexOf(g.color) + 1) % GROUP_COLORS.length] }
                : g)),
        });

    const colRow = (key: string, groupKey: string) => {
        const c = COLUMN_BY_KEY[key];
        if (!c) return null;
        const dragOver = over === `col:${key}`;
        return (
            <div key={key} draggable onDragStart={() => setDrag({ kind: 'col', key, from: groupKey })}
                onDragEnd={() => { setDrag(null); setOver(null); }}
                onDragOver={e => { if (drag?.kind === 'col') { e.preventDefault(); e.stopPropagation(); setOver(`col:${key}`); } }}
                onDrop={e => {
                    if (drag?.kind !== 'col') return;
                    e.preventDefault(); e.stopPropagation();
                    moveCol(drag.key, groupKey, key);
                    setDrag(null); setOver(null);
                }}
                style={{
                    display: 'flex', alignItems: 'center', gap: 10, padding: '5px 10px 5px 22px', borderRadius: 8,
                    cursor: 'grab', fontSize: 13, color: 'var(--color-text)',
                    background: dragOver ? '#eef2ff' : undefined,
                    boxShadow: dragOver ? 'inset 0 2px 0 0 var(--color-accent)' : undefined,
                    opacity: drag?.kind === 'col' && drag.key === key ? 0.4 : 1,
                }}>
                <span style={{ color: '#cbd5e1', fontSize: 12, cursor: 'grab' }}>⠿</span>
                <input type="checkbox" checked={isOn(key)} onChange={e => setVisible([key], e.target.checked)}
                    style={{ width: 15, height: 15, accentColor: 'var(--color-accent)', cursor: 'pointer' }} />
                <span title={c.title} style={{ flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.label}</span>
                {/* Остатки и себестоимость склада — отдельный запрос к бэку и только в товарных
                    группировках; отметка объясняет, почему такая колонка иногда пуста. */}
                {c.extendedOnly && (
                    <span title="Остатки: грузятся дополнительным запросом, доступны в группировках по товарам"
                        style={{ fontSize: 10, fontWeight: 700, color: '#7c3aed', background: '#f5f3ff', borderRadius: 5, padding: '2px 6px' }}>склад</span>
                )}
                <span style={{ fontSize: 10, fontWeight: 700, color: '#64748b', background: '#f1f5f9', borderRadius: 5, padding: '2px 6px', fontFamily: 'ui-monospace, Menlo, monospace' }}>{c.unit}</span>
            </div>
        );
    };

    return (
        <>
        <div onClick={onClose} title="Закрыть настройку колонок"
            style={{ position: 'fixed', inset: 0, zIndex: 59, background: 'rgba(15,23,42,.12)' }} />
        <div style={{ position: 'fixed', top: 0, right: 0, bottom: 0, width: 380, maxWidth: '92vw', zIndex: 60, background: '#fff', borderLeft: '1px solid var(--color-border)', boxShadow: '-12px 0 32px rgba(0,0,0,.10)', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '14px 16px', borderBottom: '1px solid var(--color-border)', flexShrink: 0 }}>
                <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase' }}>Настройка колонок</span>
                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto', fontSize: 12 }} title="Вернуть раскладку по умолчанию"
                    onClick={() => onChange(defaultLayout())}>Сбросить</button>
                <button onClick={onClose} title="Закрыть" style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: '#6b7280', display: 'inline-flex' }}><IcX size={17} /></button>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', padding: '10px 16px 6px', flexShrink: 0 }}>
                <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase', color: '#94a3b8' }}>Группы колонок</span>
                <button onClick={addGroup} style={{ marginLeft: 'auto', border: 'none', background: 'transparent', color: 'var(--color-accent)', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>+ Добавить группу</button>
            </div>

            <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '0 12px 12px' }}>
                {layout.groups.map(g => {
                    const on = g.cols.filter(isOn).length;
                    const all = on === g.cols.length && g.cols.length > 0;
                    const isCollapsed = collapsed.has(g.key);
                    const groupOver = over === `group:${g.key}`;
                    return (
                        <div key={g.key}
                            onDragOver={e => { if (drag?.kind === 'group') { e.preventDefault(); setOver(`group:${g.key}`); } }}
                            onDrop={e => {
                                if (drag?.kind === 'group') { e.preventDefault(); moveGroup(drag.key, g.key); setDrag(null); setOver(null); }
                            }}
                            style={{ borderLeft: `3px solid ${g.color}`, marginBottom: 6, borderRadius: '0 10px 10px 0', background: groupOver ? '#eef2ff' : '#fff' }}>
                            <div draggable onDragStart={() => setDrag({ kind: 'group', key: g.key })}
                                onDragEnd={() => { setDrag(null); setOver(null); }}
                                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', background: `${g.color}12`, borderRadius: '0 10px 0 0', cursor: 'grab' }}>
                                <span style={{ color: '#cbd5e1', fontSize: 12 }}>⠿</span>
                                <span onClick={() => setCollapsed(prev => { const n = new Set(prev); n.has(g.key) ? n.delete(g.key) : n.add(g.key); return n; })}
                                    style={{ cursor: 'pointer', fontSize: 10, color: '#64748b', width: 10 }}>{isCollapsed ? '▸' : '▾'}</span>
                                <input type="checkbox" checked={all} ref={el => { if (el) el.indeterminate = on > 0 && !all; }}
                                    onChange={e => setVisible(g.cols, e.target.checked)}
                                    style={{ width: 15, height: 15, accentColor: g.color, cursor: 'pointer' }} />
                                <input value={g.label} onChange={e => renameGroup(g.key, e.target.value)}
                                    style={{ flex: 1, minWidth: 0, border: 'none', background: 'transparent', fontSize: 13, fontWeight: 700, color: 'var(--color-text)', padding: 0 }} />
                                <span style={{ fontSize: 11, fontWeight: 700, color: '#64748b', background: '#fff', border: '1px solid var(--color-border)', borderRadius: 6, padding: '1px 6px' }}>{on}</span>
                                <span onClick={() => cycleColor(g.key)} title="Сменить цвет группы"
                                    style={{ width: 14, height: 14, borderRadius: '50%', background: g.color, cursor: 'pointer', flexShrink: 0 }} />
                                <button onClick={() => removeGroup(g.key)} title="Убрать группу (колонки уйдут в «без группы»)"
                                    style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: '#94a3b8', display: 'inline-flex' }}><IcX size={14} /></button>
                            </div>
                            {!isCollapsed && (
                                <div onDragOver={e => { if (drag?.kind === 'col') { e.preventDefault(); setOver(`drop:${g.key}`); } }}
                                    onDrop={e => { if (drag?.kind === 'col') { e.preventDefault(); moveCol(drag.key, g.key); setDrag(null); setOver(null); } }}
                                    style={{ padding: '4px 0 6px', minHeight: 12, background: over === `drop:${g.key}` ? '#eef2ff' : undefined }}>
                                    {g.cols.map(k => colRow(k, g.key))}
                                    {g.cols.length === 0 && <div style={{ padding: '6px 22px', fontSize: 12, color: '#94a3b8' }}>Перетащите сюда колонки</div>}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>

            <div onDragOver={e => { if (drag?.kind === 'col') { e.preventDefault(); setOver(`drop:${NONE}`); } }}
                onDrop={e => { if (drag?.kind === 'col') { e.preventDefault(); moveCol(drag.key, NONE); setDrag(null); setOver(null); } }}
                style={{ borderTop: '1px solid var(--color-border)', background: over === `drop:${NONE}` ? '#eef2ff' : '#f8fafc', flexShrink: 0, maxHeight: 220, overflowY: 'auto', padding: '8px 12px 12px' }}>
                <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase', color: '#94a3b8', padding: '0 10px 6px' }}>Без группы</div>
                {layout.ungrouped.length === 0
                    ? <div style={{ padding: '0 10px', fontSize: 12, color: '#94a3b8' }}>Перетащите строку в группу выше или меняйте порядок</div>
                    : layout.ungrouped.map(k => colRow(k, NONE))}
            </div>
        </div>
        </>
    );
}
