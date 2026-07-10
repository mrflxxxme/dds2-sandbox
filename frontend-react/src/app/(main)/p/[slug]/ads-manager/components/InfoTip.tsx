'use client';
import React from 'react';

/** Подсказка при наведении на сам элемент (заголовок колонки) — без значка «?».
 *  Всплывашка скруглённая, цвет фона совпадает с тёмной шапкой таблицы (#374151). */
export default function InfoTip({ text, children }: { text: string; children: React.ReactNode }) {
    return (
        <span className="itip">
            {/* Бледнеет только подпись: opacity на .itip погасила бы и саму всплывашку */}
            <span className="itip-label">{children}</span>
            <span className="itip-pop" role="tooltip">{text}</span>
            <style>{`
                /* Обычный курсор; подпись при наведении чуть бледнее — знак, что есть подсказка */
                .itip { position: relative; display: inline-flex; align-items: center; cursor: default; }
                .itip-label { transition: opacity .15s; }
                .itip:hover .itip-label { opacity: .65; }
                /* Вниз, а не вверх: над шапкой всплывашку срезает контейнер с горизонтальной прокруткой */
                .itip-pop { position: absolute; top: calc(100% + 7px); left: 50%; transform: translateX(-50%);
                    background: #374151; color: #f3f4f6; font-size: 11px; font-weight: 400; line-height: 1.4;
                    text-transform: none; letter-spacing: normal; white-space: normal; width: max-content; max-width: 240px;
                    padding: 8px 11px; border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.28); text-align: left;
                    opacity: 0; visibility: hidden; transition: opacity .15s; z-index: 90; pointer-events: none; }
                .itip:hover .itip-pop { opacity: 1; visibility: visible; }
                .itip-pop::after { content: ''; position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%);
                    border: 5px solid transparent; border-bottom-color: #374151; }
            `}</style>
        </span>
    );
}
