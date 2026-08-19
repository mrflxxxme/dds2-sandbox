'use client';
import React, { useEffect, useMemo, useState } from 'react';

/** Лупа. Иконка инлайнится: компонент общий, а набор icons.tsx — локальный для ads-manager. */
const IcSearch = ({ size = 15 }: { size?: number }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" />
    </svg>
);

/** Раскрывающийся фильтр с поисковой строкой сверху (одиночный выбор). */
export default function SearchSelect({ value, onChange, options, placeholder, allLabel = 'Все', minWidth = 160, maxWidth = 260, showAll = true, searchPlaceholder = 'Поиск…', customOption }: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
    placeholder: string;
    allLabel?: string;
    minWidth?: number;
    maxWidth?: number;
    showAll?: boolean;  // список без «Все»: там, где пустое значение не имеет смысла (шаг сетки)
    searchPlaceholder?: string;
    /** Своё значение прямо из строки поиска: вернуть вариант или null, если ввод не годится. */
    customOption?: (query: string) => { value: string; label: string } | null;
}) {
    const [open, setOpen] = useState(false);
    const [q, setQ] = useState('');
    const selected = options.find(o => o.value === value) ?? (value ? { value, label: value } : undefined);
    const custom = customOption?.(q.trim()) ?? null;
    const filtered = useMemo(() => {
        const s = q.trim().toLowerCase();
        return s ? options.filter(o => o.label.toLowerCase().includes(s)) : options;
    }, [options, q]);
    const close = () => { setOpen(false); setQ(''); };
    const pick = (v: string) => { onChange(v); close(); };

    // Esc закрывает список — вместе с кликом мимо и повторным кликом по кнопке
    // это три привычных способа свернуть, чтобы не искать единственный верный
    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [open]);

    return (
        <div style={{ position: 'relative', display: 'inline-block' }}>
            {/* Рамка (обычная, наведение, открытый список) — на классе .filter-trigger:
                inline-border перебивал бы :hover и подсветки не было бы */}
            <button type="button" className="filter-trigger" aria-expanded={open} onClick={() => setOpen(o => !o)}
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, width: '100%', minWidth, maxWidth, background: 'var(--color-bg-card)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)', cursor: 'pointer' }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{selected ? selected.label : placeholder}</span>
                <span style={{ color: 'var(--color-text-dim)', fontSize: 11, flexShrink: 0 }}>⌄</span>
            </button>
            {open && (<>
                <div style={{ position: 'fixed', inset: 0, zIndex: 40 }} onClick={close} />
                <div style={{ position: 'absolute', left: 0, top: '100%', marginTop: 6, zIndex: 41, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', width: 'max(260px, 100%)', maxWidth: 380, overflow: 'hidden' }}>
                    <div style={{ position: 'relative', padding: 8, borderBottom: '1px solid #f3f4f6' }}>
                        <span style={{ position: 'absolute', left: 17, top: '50%', transform: 'translateY(-50%)', color: '#9ca3af', display: 'inline-flex' }}><IcSearch size={15} /></span>
                        <input autoFocus placeholder={searchPlaceholder} value={q} onChange={e => setQ(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter' && custom) pick(custom.value); }}
                            style={{ width: '100%', boxSizing: 'border-box', background: '#fff', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px 6px 32px', fontSize: 13, color: 'var(--color-text)' }} />
                    </div>
                    <div style={{ maxHeight: 280, overflowY: 'auto', padding: 6 }}>
                        {custom && (
                            <div className="ss-opt" onClick={() => pick(custom.value)} style={{ color: 'var(--color-accent)' }}>{custom.label}</div>
                        )}
                        {showAll && <div className="ss-opt" onClick={() => pick('')} style={value === '' ? { background: '#eff6ff' } : undefined}>{allLabel}</div>}
                        {filtered.map(o => (
                            <div key={o.value} className="ss-opt" onClick={() => pick(o.value)} title={o.label}
                                style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', ...(value === o.value ? { background: '#eff6ff' } : {}) }}>{o.label}</div>
                        ))}
                        {filtered.length === 0 && <div style={{ padding: '10px 8px', fontSize: 12, color: '#9ca3af', textAlign: 'center' }}>Ничего не найдено</div>}
                    </div>
                </div>
            </>)}
        </div>
    );
}
