'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import type { AdSubject, AdNmCard } from '@/types/api';
import { IcSearch, IcX } from './icons';
import WbThumb from '@/components/WbThumb';
import { useOverlayClose } from './adsShared';

const MAX = 50;

type Enriched = AdNmCard & { brand?: string; vendor_code?: string };

/** Модалка добавления товаров в кампанию (как в кабинете WB).
 *  Категория (предмет) выбирается из выпадающего списка, товары — таблицей с фото,
 *  поиск идёт и по названию, и по nmID. Возвращает выбранные карточки наружу. */
export default function AddProductsModal({ initialSelected, onClose, onApply }: {
    initialSelected: Map<number, string>;
    onClose: () => void;
    onApply: (selected: Map<number, string>) => void;
}) {
    const [subjects, setSubjects] = useState<AdSubject[]>([]);
    const [subjectOpen, setSubjectOpen] = useState(false);
    const [subjectId, setSubjectId] = useState<number | null>(null);
    const [cards, setCards] = useState<Enriched[]>([]);
    const [cardsLoading, setCardsLoading] = useState(false);
    const [search, setSearch] = useState('');
    const [selected, setSelected] = useState<Map<number, string>>(() => new Map(initialSelected));
    // nm → {brand, vendor_code} из каталога артикулов (в supplier/nms этого нет)
    const [catalog, setCatalog] = useState<Record<number, { brand: string; vendor_code: string }>>({});

    useEffect(() => {
        api.getAdSubjects().then(r => setSubjects(r.subjects)).catch(() => {});
        api.getAdArticleCatalog().then(rows => {
            const m: Record<number, { brand: string; vendor_code: string }> = {};
            rows.forEach(r => { m[r.nm_id] = { brand: r.brand || '', vendor_code: r.vendor_code || '' }; });
            setCatalog(m);
        }).catch(() => {});
    }, []);

    const loadCards = useCallback(async (sid: number) => {
        setCardsLoading(true); setCards([]);
        try { setCards((await api.getAdNms([sid])).nms); }
        catch { /* оставим пусто */ }
        finally { setCardsLoading(false); }
    }, []);
    useEffect(() => { if (subjectId != null) loadCards(subjectId); }, [subjectId, loadCards]);

    const toggle = (c: Enriched) => setSelected(prev => {
        const next = new Map(prev);
        if (next.has(c.nm)) next.delete(c.nm);
        else if (next.size < MAX) next.set(c.nm, c.title);
        return next;
    });

    const visible = useMemo(() => {
        const q = search.trim().toLowerCase();
        if (!q) return cards;
        // Поиск по названию, nmID и артикулу продавца
        return cards.filter(c =>
            c.title.toLowerCase().includes(q)
            || String(c.nm).includes(q)
            || (catalog[c.nm]?.vendor_code || '').toLowerCase().includes(q));
    }, [cards, search, catalog]);

    const allVisibleSelected = visible.length > 0 && visible.every(c => selected.has(c.nm));
    const toggleAll = () => setSelected(prev => {
        const next = new Map(prev);
        if (allVisibleSelected) visible.forEach(c => next.delete(c.nm));
        else for (const c of visible) { if (next.size >= MAX) break; if (!next.has(c.nm)) next.set(c.nm, c.title); }
        return next;
    });

    const subjectName = subjects.find(s => s.id === subjectId)?.name;

    // Подложка закрывает окно только «настоящим» кликом по ней (не концом выделения текста)
    const overlay = useOverlayClose(onClose);

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1100, background: 'rgba(17,24,39,.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }} {...overlay}>
            <div className="glass-card static" onClick={e => e.stopPropagation()}
                style={{ width: 720, maxWidth: '100%', height: '82vh', display: 'flex', flexDirection: 'column', padding: 24, background: '#fff' }}>
                <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
                    <h2 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Добавление товаров</h2>
                    <button onClick={onClose} style={{ marginLeft: 'auto', border: 'none', background: 'none', cursor: 'pointer', color: '#9ca3af' }}><IcX size={20} /></button>
                </div>

                {/* Выбор категории + поиск */}
                <div style={{ display: 'flex', gap: 12, marginBottom: 14 }}>
                    <div style={{ position: 'relative', flex: 1 }}>
                        <button onClick={() => setSubjectOpen(o => !o)}
                            style={{ width: '100%', textAlign: 'left', padding: '11px 14px', borderRadius: 12, border: '1px solid #d1d5db', background: '#fff', cursor: 'pointer', fontSize: 14, color: subjectName ? '#111827' : '#9ca3af', display: 'flex', alignItems: 'center' }}>
                            {subjectName || 'Категории'}
                            <span style={{ marginLeft: 'auto', fontSize: 11, color: '#6b7280' }}>{subjectOpen ? '▲' : '▼'}</span>
                        </button>
                        {subjectOpen && (
                            <>
                                <div style={{ position: 'fixed', inset: 0, zIndex: 1 }} onClick={() => setSubjectOpen(false)} />
                                <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, marginTop: 4, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, boxShadow: '0 12px 32px rgba(0,0,0,.14)', zIndex: 2, maxHeight: 320, overflow: 'auto' }}>
                                    {subjects.map(s => (
                                        <div key={s.id} onClick={() => { setSubjectId(s.id); setSubjectOpen(false); setSearch(''); }}
                                            style={{ padding: '11px 16px', fontSize: 14, cursor: 'pointer', background: s.id === subjectId ? '#f3f4f6' : '#fff' }} className="menu-row">
                                            {s.name} <span style={{ color: '#9ca3af', fontSize: 12 }}>({s.count})</span>
                                        </div>
                                    ))}
                                </div>
                            </>
                        )}
                    </div>
                    <div style={{ position: 'relative', flex: 1, display: 'flex', alignItems: 'center' }}>
                        <span style={{ position: 'absolute', right: 12, color: '#9ca3af', display: 'inline-flex' }}><IcSearch size={16} /></span>
                        <input value={search} onChange={e => setSearch(e.target.value)} disabled={subjectId == null}
                            placeholder={`Поиск среди ${cards.length} товаров`}
                            style={{ width: '100%', padding: '11px 38px 11px 14px', borderRadius: 12, border: '1px solid #d1d5db', fontSize: 14, background: subjectId == null ? '#f9fafb' : '#fff' }} />
                    </div>
                </div>

                {/* Таблица товаров */}
                <div style={{ flex: 1, overflow: 'auto', border: '1px solid #eef0f2', borderRadius: 12 }}>
                    {subjectId == null && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af', fontSize: 14 }}>Для добавления товаров выберите категорию</div>}
                    {subjectId != null && cardsLoading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af', fontSize: 14 }}>Загрузка товаров…</div>}
                    {subjectId != null && !cardsLoading && (
                        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                            <thead>
                                <tr style={{ position: 'sticky', top: 0, background: '#fff', zIndex: 1 }}>
                                    <th style={{ width: 44, padding: '10px 8px', textAlign: 'center' }}>
                                        <input type="checkbox" checked={allVisibleSelected} onChange={toggleAll} />
                                    </th>
                                    <th style={{ width: 64, padding: '10px 8px', textAlign: 'left', fontSize: 11, color: '#6b7280', fontWeight: 600 }}>ФОТО</th>
                                    <th style={{ padding: '10px 8px', textAlign: 'left', fontSize: 11, color: '#6b7280', fontWeight: 600 }}>БРЕНД · АРТИКУЛ · НАЗВАНИЕ</th>
                                </tr>
                            </thead>
                            <tbody>
                                {visible.map(c => {
                                    const on = selected.has(c.nm);
                                    const disabled = !on && selected.size >= MAX;
                                    const info = catalog[c.nm];
                                    return (
                                        <tr key={c.nm} onClick={() => !disabled && toggle(c)}
                                            style={{ borderTop: '1px solid #f4f4f5', cursor: disabled ? 'not-allowed' : 'pointer', background: on ? '#f3f0ff' : '#fff', opacity: disabled ? 0.5 : 1 }}>
                                            <td style={{ padding: '8px', textAlign: 'center' }} onClick={e => e.stopPropagation()}>
                                                <input type="checkbox" checked={on} disabled={disabled} onChange={() => toggle(c)} />
                                            </td>
                                            <td style={{ padding: '8px' }}><WbThumb nmId={c.nm} size={44} /></td>
                                            <td style={{ padding: '8px', fontSize: 12.5 }}>
                                                <div style={{ color: '#9ca3af', fontSize: 11 }}>{info?.brand ? `${info.brand} · ` : ''}{c.nm}</div>
                                                <div style={{ color: '#111827', fontWeight: 500 }}>{c.title}</div>
                                            </td>
                                        </tr>
                                    );
                                })}
                                {visible.length === 0 && <tr><td colSpan={3} style={{ padding: 30, textAlign: 'center', color: '#9ca3af', fontSize: 13 }}>Ничего не найдено</td></tr>}
                            </tbody>
                        </table>
                    )}
                </div>

                {/* Футер */}
                <div style={{ display: 'flex', alignItems: 'center', marginTop: 16 }}>
                    <button onClick={() => onApply(selected)} disabled={selected.size === 0} className="btn btn-primary" style={{ fontSize: 14 }}>Добавить</button>
                    <button onClick={onClose} className="btn btn-secondary" style={{ fontSize: 14, marginLeft: 8 }}>Отменить</button>
                    <div style={{ marginLeft: 'auto', textAlign: 'right', fontSize: 12, color: '#6b7280' }}>
                        <div>Выбрано: <b style={{ color: '#111827' }}>{selected.size}</b></div>
                        <div>Максимум — {MAX}</div>
                    </div>
                </div>
            </div>
        </div>
    );
}
