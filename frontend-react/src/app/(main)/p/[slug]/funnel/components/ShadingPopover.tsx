'use client';
import React, { useEffect, useLayoutEffect, useState } from 'react';
import { IcX } from '../../ads-manager/components/icons';
import { SHADING_OPTIONS, type Shading } from './columns';

/* ─── Подсветка значений ───────────────────────────────────────────────────
 * Цвет цифры задаёт сама метрика (как в прежнем разделе): ДРР, маржа, СПП, CTR,
 * прибыль и расход держат свой светофор, остальные цифры чёрные. Здесь выбирается
 * только, показывать ли рядом величину полоской. Панель выезжает из своей кнопки.
 * ─────────────────────────────────────────────────────────────────────── */

const WIDTH = 340;
const GAP = 6;

export default function ShadingPopover({ anchor, value, onChange, onClose }: {
    anchor: HTMLElement | null;
    value: Shading;
    onChange: (next: Shading) => void;
    onClose: () => void;
}) {
    const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

    useLayoutEffect(() => {
        if (!anchor) return;
        const place = () => {
            const r = anchor.getBoundingClientRect();
            const left = Math.max(8, Math.min(r.right - WIDTH, window.innerWidth - WIDTH - 8));
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

    return (
        <>
            <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 70 }} />
            <div role="dialog" aria-label="Подсветка значений" className="shd-pop"
                style={{
                    position: 'fixed', top: pos?.top ?? -9999, left: pos?.left ?? -9999, zIndex: 71,
                    width: WIDTH, maxWidth: 'calc(100vw - 16px)', background: '#fff', borderRadius: 14,
                    border: '1px solid var(--color-border)', boxShadow: '0 16px 44px rgba(15,23,42,.18)',
                    overflow: 'hidden', visibility: pos ? 'visible' : 'hidden',
                }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', borderBottom: '1px solid var(--color-border)' }}>
                    <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase' }}>Подсветка значений</span>
                    <button onClick={onClose} title="Закрыть (Esc)" style={{ marginLeft: 'auto', border: 'none', background: 'transparent', cursor: 'pointer', color: '#6b7280', display: 'inline-flex' }}>
                        <IcX size={16} />
                    </button>
                </div>

                <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--color-border)', background: '#f8fafc' }}>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                        Цвет цифры задаёт сама метрика: ДРР, маржа, СПП, CTR, прибыль и расход горят
                        своим светофором, крупные значения дня подсвечиваются мягкой заливкой,
                        остальные цифры остаются чёрными.
                    </div>
                </div>

                <div style={{ padding: '12px 14px' }}>
                    <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '.06em', textTransform: 'uppercase', color: '#94a3b8', marginBottom: 7 }}>Показывать величину</div>
                    <div style={{ display: 'inline-flex', gap: 3, background: '#eef2f7', borderRadius: 8, padding: 3 }}>
                        {SHADING_OPTIONS.map(o => (
                            <button key={o.key} type="button" title={o.hint} onClick={() => onChange(o.key)}
                                style={{
                                    fontSize: 12, padding: '5px 14px', borderRadius: 6, border: 'none', cursor: 'pointer', whiteSpace: 'nowrap',
                                    background: value === o.key ? '#fff' : 'transparent',
                                    color: value === o.key ? '#1e3a8a' : '#6b7280',
                                    fontWeight: value === o.key ? 600 : 500,
                                    boxShadow: value === o.key ? '0 1px 2px rgba(0,0,0,.1)' : undefined,
                                }}>{o.label}</button>
                        ))}
                    </div>
                </div>

                <style>{`
                    .shd-pop { animation: shdIn .16s cubic-bezier(.25,1,.5,1); transform-origin: top right; }
                    @keyframes shdIn {
                        from { opacity: 0; transform: translateY(-6px) scale(.985); }
                        to   { opacity: 1; transform: translateY(0) scale(1); }
                    }
                `}</style>
            </div>
        </>
    );
}
