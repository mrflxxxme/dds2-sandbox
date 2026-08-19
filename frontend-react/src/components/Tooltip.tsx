'use client';
import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

type Placement = 'top' | 'bottom';
type Pos = { left: number; top: number; placement: Placement };

const EDGE = 8;      // минимальный отступ от края окна
const GAP = 7;       // зазор между элементом и всплывашкой

/** Подсказка при наведении — замена нативного `title`.
 *
 *  Зачем: нативный title Chrome рисует ПОД курсором и гасит на каждом mousemove.
 *  На мелких кнопках (тумблер, иконки) подсказка мигает, а курсор выглядит дёргающимся.
 *
 *  Здесь: всплывашка привязана к элементу, `pointer-events: none` (мышь её не видит,
 *  hover не срывается), позиция считается ОДИН раз на mouseenter — слушателей mousemove нет.
 *  Рендерится порталом в body с `position: fixed`, поэтому её не режут контейнеры
 *  с `overflow` (шапки и тела таблиц) — обычный CSS-тултип там обрезался бы.
 */
export default function Tooltip({ text, placement = 'top', className = '', style, children }: {
    text: string;
    placement?: Placement;
    className?: string;
    /** Обёртка встаёт flex-item'ом на место элемента — сюда переносим его alignSelf и т.п. */
    style?: React.CSSProperties;
    children: React.ReactNode;
}) {
    const ref = useRef<HTMLSpanElement>(null);
    const popRef = useRef<HTMLSpanElement>(null);
    const [pos, setPos] = useState<Pos | null>(null);
    const popId = useId();

    const show = useCallback(() => {
        const el = ref.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        // Не хватает места сверху — раскрываемся вниз (и наоборот)
        const p: Placement = placement === 'top' && r.top < 48 ? 'bottom'
            : placement === 'bottom' && window.innerHeight - r.bottom < 48 ? 'top'
                : placement;
        setPos({ left: r.left + r.width / 2, top: p === 'top' ? r.top - GAP : r.bottom + GAP, placement: p });
    }, [placement]);

    const hide = useCallback(() => setPos(null), []);

    // Ширину всплывашки (max-content) знаем только после рендера: если она вылезла
    // за край окна — двигаем её внутрь, а стрелку оставляем над элементом.
    useLayoutEffect(() => {
        const pop = popRef.current;
        if (!pos || !pop) return;
        const half = pop.offsetWidth / 2;
        const clamped = Math.min(Math.max(pos.left, EDGE + half), window.innerWidth - EDGE - half);
        pop.style.left = `${clamped}px`;
        pop.style.setProperty('--dds-tip-arrow', `${pos.left - clamped + half}px`);
    }, [pos, text]);

    // Пока подсказка открыта, скролл/ресайз уводят её от элемента — просто прячем.
    // Слушатели живут только на время показа, в покое их нет.
    useEffect(() => {
        if (!pos) return;
        window.addEventListener('scroll', hide, { passive: true, capture: true });
        window.addEventListener('resize', hide);
        return () => {
            window.removeEventListener('scroll', hide, { capture: true });
            window.removeEventListener('resize', hide);
        };
    }, [pos, hide]);

    // На тач-устройствах hover не существует: без этого подсказка на телефоне
    // недостижима вовсе. Тап переключает, тап мимо — гасит (слушатель живёт
    // только пока открыто). Esc — для клавиатуры.
    useEffect(() => {
        if (!pos) return;
        const onDocDown = (e: PointerEvent) => {
            if (e.pointerType !== 'mouse' && !ref.current?.contains(e.target as Node)) hide();
        };
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') hide(); };
        document.addEventListener('pointerdown', onDocDown);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('pointerdown', onDocDown);
            document.removeEventListener('keydown', onKey);
        };
    }, [pos, hide]);

    return (
        <span ref={ref} className={`dds-tip ${className}`.trim()} style={style}
            aria-describedby={pos ? popId : undefined}
            onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}
            onPointerUp={e => { if (e.pointerType !== 'mouse') (pos ? hide() : show()); }}>
            {children}
            {pos && createPortal(
                <span ref={popRef} id={popId} className={`dds-tip-pop dds-tip-pop--${pos.placement}`} role="tooltip"
                    style={{ left: pos.left, top: pos.top }}>
                    {text}
                </span>,
                document.body,
            )}
        </span>
    );
}
