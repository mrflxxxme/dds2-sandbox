'use client';
import React, { useEffect, useRef, useState } from 'react';
import { IcPencil, IcCheck, IcX } from './icons';

/** Название кампании: показывается текстом, по клику на карандаш открывается поле
 *  редактирования с галочкой (применить) и крестиком (отмена) и подсказкой о лимите.
 *  Enter применяет, Escape отменяет. Пустое имя применить нельзя. */
export default function EditableName({ value, onChange, size = 22, maxLength = 128 }: {
    value: string;
    onChange: (name: string) => void;
    size?: number;
    maxLength?: number;
}) {
    const MAX = maxLength;
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(value);
    const ref = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (editing) { setDraft(value); requestAnimationFrame(() => { ref.current?.focus(); ref.current?.select(); }); }
    }, [editing, value]);

    const apply = () => {
        const v = draft.trim().slice(0, MAX);
        if (v) onChange(v);
        setEditing(false);
    };

    if (!editing) {
        return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
                <span style={{ fontSize: size, fontWeight: 700, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value || 'Без названия'}</span>
                <button onClick={() => setEditing(true)} aria-label="Изменить название"
                    style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#9ca3af', display: 'inline-flex', padding: 4 }}>
                    <IcPencil size={Math.round(size * 0.7)} />
                </button>
            </div>
        );
    }

    const tooLong = draft.length >= MAX;
    return (
        <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ position: 'relative', flex: 1, display: 'flex', alignItems: 'center' }}>
                    <input ref={ref} value={draft} maxLength={MAX}
                        onChange={e => setDraft(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') apply(); if (e.key === 'Escape') setEditing(false); }}
                        style={{ width: '100%', fontSize: 15, padding: '9px 34px 9px 12px', borderRadius: 10, border: '1.5px solid #8b5cf6', outline: 'none' }} />
                    {draft && (
                        <button onClick={() => { setDraft(''); ref.current?.focus(); }} aria-label="Очистить"
                            style={{ position: 'absolute', right: 8, border: 'none', background: 'none', cursor: 'pointer', color: '#c4c4c4', display: 'inline-flex' }}>
                            <IcX size={15} />
                        </button>
                    )}
                </div>
                <button onClick={apply} disabled={!draft.trim()} aria-label="Применить"
                    style={{ border: '1px solid #e5e7eb', background: '#f3f4f6', borderRadius: 8, cursor: draft.trim() ? 'pointer' : 'not-allowed', color: '#374151', display: 'inline-flex', padding: 8, opacity: draft.trim() ? 1 : 0.5 }}>
                    <IcCheck size={16} />
                </button>
                <button onClick={() => setEditing(false)} aria-label="Отмена"
                    style={{ border: '1px solid #e5e7eb', background: '#f3f4f6', borderRadius: 8, cursor: 'pointer', color: '#374151', display: 'inline-flex', padding: 8 }}>
                    <IcX size={16} />
                </button>
            </div>
            <div style={{ fontSize: 11.5, color: tooLong ? '#ef4444' : '#9ca3af', marginTop: 5 }}>
                Не более {MAX} символов · осталось {MAX - draft.length}
            </div>
        </div>
    );
}
