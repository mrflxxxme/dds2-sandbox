'use client';
import React, { useEffect, useMemo, useState } from 'react';
import { IcSearch, IcSliders } from '../../ads-manager/components/icons';
import type { ExchangeSubject, ExchangeSupplier } from '@/types/api';

/** Значения фильтров биржи (то, что уходит в запрос). */
export interface ExchangeFilters {
    subjectIds: string[];
    brands: string[];
    supplierIds: string[];
    ratingMin: string;   // пусто = без ограничения
    stock: '' | 'in' | 'out';
}

export const EMPTY_FILTERS: ExchangeFilters = {
    subjectIds: [], brands: [], supplierIds: [], ratingMin: '', stock: '',
};

/** Сколько разделов реально задано — для счётчика на кнопке. */
export function countActive(f: ExchangeFilters): number {
    return (f.subjectIds.length ? 1 : 0) + (f.brands.length ? 1 : 0) + (f.supplierIds.length ? 1 : 0)
        + (f.ratingMin ? 1 : 0) + (f.stock ? 1 : 0);
}

type SectionKey = 'subject' | 'brand' | 'rating' | 'supplier' | 'stock';

const SECTIONS: { key: SectionKey; label: string }[] = [
    { key: 'subject', label: 'Предмет' },
    { key: 'brand', label: 'Бренд' },
    { key: 'rating', label: 'Рейтинг' },
    { key: 'supplier', label: 'Продавец' },
    { key: 'stock', label: 'Остатки' },
];

/** Чеклист с поиском — общее тело для «Предмет»/«Бренд»/«Продавец». */
function CheckList({ options, values, onToggle }: {
    options: { value: string; label: string }[];
    values: string[];
    onToggle: (v: string) => void;
}) {
    const [q, setQ] = useState('');
    const filtered = useMemo(() => {
        const s = q.trim().toLowerCase();
        return (s ? options.filter(o => o.label.toLowerCase().includes(s)) : options).slice(0, 500);
    }, [options, q]);
    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            <div style={{ position: 'relative', marginBottom: 8 }}>
                <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#9ca3af', display: 'inline-flex' }}>
                    <IcSearch size={15} />
                </span>
                <input autoFocus placeholder="Поиск" value={q} onChange={e => setQ(e.target.value)}
                    style={{ width: '100%', boxSizing: 'border-box', background: '#fff', border: '1px solid var(--color-border)', borderRadius: 8, padding: '7px 10px 7px 32px', fontSize: 13, color: 'var(--color-text)' }} />
            </div>
            <div style={{ overflowY: 'auto', flex: 1, minHeight: 0 }}>
                {filtered.length === 0 && <div style={{ padding: 10, fontSize: 13, color: 'var(--color-text-muted)' }}>Ничего не найдено</div>}
                {filtered.map(o => (
                    <label key={o.value} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 4px', fontSize: 13, cursor: 'pointer' }}>
                        <input type="checkbox" checked={values.includes(o.value)} onChange={() => onToggle(o.value)} />
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.label}</span>
                    </label>
                ))}
            </div>
        </div>
    );
}

/**
 * Кнопка «Фильтры» с раскрывающейся панелью — устройство как на бирже WB:
 * слева разделы (у заданных — точка), справа содержимое, внизу «Применить»/«Сбросить».
 * Правки копятся в ЧЕРНОВИКЕ и уходят наружу только по «Применить» — иначе каждый
 * чекбокс дёргал бы биржу отдельным запросом.
 */
export default function FiltersPanel({ value, onApply, subjects, brands, suppliers }: {
    value: ExchangeFilters;
    onApply: (f: ExchangeFilters) => void;
    subjects: ExchangeSubject[];
    brands: string[];
    suppliers: ExchangeSupplier[];
}) {
    const [open, setOpen] = useState(false);
    const [section, setSection] = useState<SectionKey>('subject');
    const [draft, setDraft] = useState<ExchangeFilters>(value);

    // При каждом открытии черновик = применённые значения (отменённые правки не залипают).
    useEffect(() => { if (open) setDraft(value); }, [open, value]);

    const activeCount = countActive(value);
    const hasDot: Record<SectionKey, boolean> = {
        subject: draft.subjectIds.length > 0,
        brand: draft.brands.length > 0,
        rating: !!draft.ratingMin,
        supplier: draft.supplierIds.length > 0,
        stock: !!draft.stock,
    };

    const toggle = (key: 'subjectIds' | 'brands' | 'supplierIds', v: string) => {
        setDraft(d => ({
            ...d,
            [key]: d[key].includes(v) ? d[key].filter(x => x !== v) : [...d[key], v],
        }));
    };

    const apply = () => { onApply(draft); setOpen(false); };
    const reset = () => { setDraft(EMPTY_FILTERS); onApply(EMPTY_FILTERS); setOpen(false); };

    return (
        <div style={{ position: 'relative', display: 'inline-block' }}>
            <button type="button" onClick={() => setOpen(o => !o)}
                style={{
                    display: 'inline-flex', alignItems: 'center', gap: 8, background: 'var(--color-bg-card)',
                    border: `1px solid ${open || activeCount ? 'var(--color-accent)' : 'var(--color-border)'}`,
                    borderRadius: 8, padding: '6px 12px', fontSize: 13, color: 'var(--color-text)', cursor: 'pointer',
                }}>
                <IcSliders size={15} />Фильтры
                {activeCount > 0 && (
                    <span className="badge badge-info" style={{ fontSize: 11, padding: '0 6px' }}>{activeCount}</span>
                )}
            </button>

            {open && (<>
                <div style={{ position: 'fixed', inset: 0, zIndex: 40 }} onClick={() => setOpen(false)} />
                <div style={{
                    position: 'absolute', left: 0, top: '100%', marginTop: 6, zIndex: 41, background: '#fff',
                    border: '1px solid #e5e7eb', borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
                    width: 620, maxWidth: '90vw', overflow: 'hidden', display: 'flex', flexDirection: 'column',
                }}>
                    <div style={{ display: 'flex', minHeight: 320 }}>
                        {/* Разделы */}
                        <div style={{ width: 180, borderRight: '1px solid #f3f4f6', padding: 8, flexShrink: 0 }}>
                            {SECTIONS.map(sec => (
                                <button key={sec.key} type="button" onClick={() => setSection(sec.key)}
                                    style={{
                                        display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%',
                                        border: 'none', borderRadius: 8, padding: '9px 10px', fontSize: 14, cursor: 'pointer',
                                        background: section === sec.key ? 'var(--color-bg-hover)' : 'transparent',
                                        color: 'var(--color-text)', fontWeight: section === sec.key ? 600 : 400, textAlign: 'left',
                                    }}>
                                    {sec.label}
                                    {hasDot[sec.key] && (
                                        <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--color-accent)', flexShrink: 0 }} />
                                    )}
                                </button>
                            ))}
                        </div>

                        {/* Содержимое раздела */}
                        <div style={{ flex: 1, padding: 12, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
                            {section === 'subject' && (
                                <CheckList values={draft.subjectIds} onToggle={v => toggle('subjectIds', v)}
                                    options={subjects.map(s => ({ value: String(s.id), label: s.name }))} />
                            )}
                            {section === 'brand' && (
                                <CheckList values={draft.brands} onToggle={v => toggle('brands', v)}
                                    options={brands.map(b => ({ value: b, label: b }))} />
                            )}
                            {section === 'supplier' && (
                                <CheckList values={draft.supplierIds} onToggle={v => toggle('supplierIds', v)}
                                    options={suppliers.map(s => ({ value: String(s.id), label: s.name }))} />
                            )}
                            {section === 'rating' && (
                                <div>
                                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 10 }}>
                                        Рейтинг не ниже
                                    </div>
                                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                                        {['', '4', '4.5', '4.8', '4.9'].map(v => (
                                            <button key={v || 'any'} type="button" onClick={() => setDraft(d => ({ ...d, ratingMin: v }))}
                                                className={`btn btn-sm ${draft.ratingMin === v ? 'btn-primary' : 'btn-secondary'}`}>
                                                {v === '' ? 'Любой' : `от ${v}`}
                                            </button>
                                        ))}
                                    </div>
                                    <input value={draft.ratingMin} onChange={e => setDraft(d => ({ ...d, ratingMin: e.target.value }))}
                                        inputMode="decimal" placeholder="свой минимум, напр. 4,7" aria-label="Рейтинг от"
                                        style={{ marginTop: 12, width: 200, background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '7px 10px', fontSize: 13, color: 'var(--color-text)' }} />
                                </div>
                            )}
                            {section === 'stock' && (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                    {([['', 'Все варианты'], ['in', 'С остатками'], ['out', 'Без остатков']] as const).map(([v, label]) => (
                                        <label key={v || 'any'} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 4px', fontSize: 14, cursor: 'pointer' }}>
                                            <input type="radio" name="cex-stock" checked={draft.stock === v}
                                                onChange={() => setDraft(d => ({ ...d, stock: v }))} />
                                            {label}
                                        </label>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>

                    <div style={{ display: 'flex', gap: 8, padding: 12, borderTop: '1px solid #f3f4f6' }}>
                        <button className="btn btn-sm btn-primary" onClick={apply}>Применить</button>
                        <button className="btn btn-sm btn-secondary" onClick={reset}>Сбросить</button>
                    </div>
                </div>
            </>)}
        </div>
    );
}
