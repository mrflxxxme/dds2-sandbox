'use client';
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { IcX } from '../../ads-manager/components/icons';

/* ─── Конструктор цепочки группировок ──────────────────────────────────────
 * Панель выезжает из кнопки «Группировка», а не перекрывает таблицу модалкой:
 * видно, что именно настраиваешь, и результат под рукой.
 * Позиция — fixed по прямоугольнику кнопки: тулбар лежит в карточке с
 * overflow:hidden, absolute-поповер там бы обрезался.
 * Слева доступные измерения, справа активные в порядке вложенности: первое —
 * верхний уровень дерева, последнее — листья.
 * ─────────────────────────────────────────────────────────────────────── */

export interface Dim { key: string; label: string }

const WIDTH = 560;
const GAP = 6;

export default function GroupingPopover({ anchor, all, active, maxChain, onApply, onClose }: {
    /** Кнопка-якорь: панель встаёт под ней. */
    anchor: HTMLElement | null;
    all: Dim[];
    active: string[];
    maxChain: number;
    onApply: (dims: string[]) => void;
    onClose: () => void;
}) {
    const [chain, setChain] = useState<string[]>(active);
    const [drag, setDrag] = useState<string | null>(null);
    const [over, setOver] = useState<string | null>(null);
    const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
    const panelRef = useRef<HTMLDivElement>(null);

    // Позиционируемся до первой отрисовки, чтобы панель не «прыгнула»
    useLayoutEffect(() => {
        if (!anchor) return;
        const place = () => {
            const r = anchor.getBoundingClientRect();
            const left = Math.max(8, Math.min(r.left, window.innerWidth - WIDTH - 8));
            setPos({ top: r.bottom + GAP, left });
        };
        place();
        window.addEventListener('resize', place);
        return () => window.removeEventListener('resize', place);
    }, [anchor]);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [onClose]);

    const byKey = Object.fromEntries(all.map(d => [d.key, d]));
    const available = all.filter(d => !chain.includes(d.key));
    const full = chain.length >= maxChain;

    const add = (key: string, beforeKey?: string) => {
        if (chain.includes(key) || full) return;
        const next = chain.filter(k => k !== key);
        const at = beforeKey ? next.indexOf(beforeKey) : -1;
        if (at === -1) next.push(key); else next.splice(at, 0, key);
        setChain(next);
    };
    const remove = (key: string) => setChain(chain.filter(k => k !== key));
    const reorder = (key: string, beforeKey: string) => {
        if (key === beforeKey) return;
        const next = chain.filter(k => k !== key);
        const at = next.indexOf(beforeKey);
        next.splice(at === -1 ? next.length : at, 0, key);
        setChain(next);
    };

    const rowBase: React.CSSProperties = {
        display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', borderRadius: 8,
        fontSize: 13, cursor: 'grab', userSelect: 'none',
    };
    const colHead: React.CSSProperties = {
        padding: '7px 12px', fontSize: 10.5, fontWeight: 700, letterSpacing: '.06em',
        textTransform: 'uppercase', color: '#94a3b8', background: '#f8fafc', borderBottom: '1px solid var(--color-border)',
    };

    return (
        <>
            {/* Прозрачная подложка: клик мимо закрывает, таблица при этом видна */}
            <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 70 }} />
            <div ref={panelRef} role="dialog" aria-label="Группировка" className="grp-pop"
                style={{
                    position: 'fixed', top: pos?.top ?? -9999, left: pos?.left ?? -9999, zIndex: 71,
                    width: WIDTH, maxWidth: 'calc(100vw - 16px)', background: '#fff', borderRadius: 14,
                    border: '1px solid var(--color-border)', boxShadow: '0 16px 44px rgba(15,23,42,.18)',
                    overflow: 'hidden', visibility: pos ? 'visible' : 'hidden',
                }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', borderBottom: '1px solid var(--color-border)' }}>
                    <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase' }}>Группировка</span>
                    <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                        порядок = вложенность{full && <b style={{ color: '#b45309' }}> · максимум {maxChain}</b>}
                    </span>
                    <button onClick={onClose} title="Закрыть (Esc)" style={{ marginLeft: 'auto', border: 'none', background: 'transparent', cursor: 'pointer', color: '#6b7280', display: 'inline-flex' }}>
                        <IcX size={16} />
                    </button>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr' }}>
                    <div style={{ borderRight: '1px solid var(--color-border)' }}>
                        <div style={colHead}>Доступные</div>
                        <div onDragOver={e => { if (drag && chain.includes(drag)) e.preventDefault(); }}
                            onDrop={e => { if (drag && chain.includes(drag)) { e.preventDefault(); remove(drag); setDrag(null); setOver(null); } }}
                            style={{ padding: 6, overflowY: 'auto', height: 236 }}>
                            {available.map(d => (
                                <div key={d.key} draggable onDragStart={() => setDrag(d.key)} onDragEnd={() => { setDrag(null); setOver(null); }}
                                    onClick={() => add(d.key)}
                                    title={full ? `Максимум ${maxChain} уровней` : 'Добавить уровень'}
                                    style={{ ...rowBase, color: full ? '#cbd5e1' : 'var(--color-text)', cursor: full ? 'not-allowed' : 'grab', opacity: drag === d.key ? 0.4 : 1 }}>
                                    <span style={{ color: '#cbd5e1', fontSize: 12 }}>⠿</span>
                                    {d.label}
                                </div>
                            ))}
                            {available.length === 0 && <div style={{ padding: 10, fontSize: 12, color: '#94a3b8' }}>Все измерения выбраны</div>}
                        </div>
                    </div>

                    <div>
                        <div style={colHead}>Активные</div>
                        <div onDragOver={e => { if (drag) { e.preventDefault(); setOver('__end__'); } }}
                            onDrop={e => { if (drag) { e.preventDefault(); chain.includes(drag) ? setChain([...chain.filter(k => k !== drag), drag]) : add(drag); setDrag(null); setOver(null); } }}
                            style={{ padding: 6, overflowY: 'auto', height: 236, background: over === '__end__' ? '#f5f3ff' : undefined }}>
                            {chain.map((k, i) => (
                                <div key={k} draggable onDragStart={() => setDrag(k)} onDragEnd={() => { setDrag(null); setOver(null); }}
                                    onDragOver={e => { if (drag) { e.preventDefault(); e.stopPropagation(); setOver(k); } }}
                                    onDrop={e => {
                                        if (!drag) return;
                                        e.preventDefault(); e.stopPropagation();
                                        chain.includes(drag) ? reorder(drag, k) : add(drag, k);
                                        setDrag(null); setOver(null);
                                    }}
                                    style={{
                                        ...rowBase, marginBottom: 5, background: '#f5f3ff', color: '#4c1d95', fontWeight: 500,
                                        boxShadow: over === k ? 'inset 0 2px 0 0 #7c3aed' : undefined,
                                        opacity: drag === k ? 0.4 : 1,
                                    }}>
                                    <span style={{ width: 19, height: 19, borderRadius: '50%', background: '#7c3aed', color: '#fff', fontSize: 11, fontWeight: 700, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>{i + 1}</span>
                                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{byKey[k]?.label ?? k}</span>
                                    <button onClick={e => { e.stopPropagation(); remove(k); }} title="Убрать уровень"
                                        style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: '#a78bfa', display: 'inline-flex' }}><IcX size={14} /></button>
                                </div>
                            ))}
                            {chain.length === 0 && <div style={{ padding: 10, fontSize: 12, color: '#94a3b8' }}>Нажмите на измерение слева или перетащите его сюда</div>}
                        </div>
                    </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 14px', borderTop: '1px solid var(--color-border)', background: '#f8fafc' }}>
                    <span style={{ fontSize: 12, color: 'var(--color-text-dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {chain.length > 0 ? chain.map(k => byKey[k]?.label ?? k).join(' → ') : 'Ничего не выбрано'}
                    </span>
                    <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 8, flexShrink: 0 }}>
                        <button className="btn btn-secondary btn-sm" onClick={onClose}>Отмена</button>
                        <button className="btn btn-primary btn-sm" disabled={chain.length === 0}
                            onClick={() => { onApply(chain); onClose(); }}>Применить</button>
                    </span>
                </div>

                <style>{`
                    .grp-pop { animation: grpIn .16s cubic-bezier(.25,1,.5,1); transform-origin: top left; }
                    @keyframes grpIn {
                        from { opacity: 0; transform: translateY(-6px) scale(.985); }
                        to   { opacity: 1; transform: translateY(0) scale(1); }
                    }
                `}</style>
            </div>
        </>
    );
}
