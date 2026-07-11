'use client';
import React from 'react';
import Tooltip from './Tooltip';

/** Подсказка при наведении на сам элемент (заголовок колонки) — без значка «?».
 *  Раскрывается ВНИЗ: над шапкой места нет. Всплывашку рисует Tooltip порталом,
 *  поэтому контейнер таблицы с overflow её больше не срезает.
 *  Раньше каждый инстанс инжектил собственный <style> — теперь стили общие (globals.css). */
export default function InfoTip({ text, children }: { text: string; children: React.ReactNode }) {
    return (
        <Tooltip text={text} placement="bottom" className="dds-tip--label">
            {/* Бледнеет только подпись: opacity на обёртке погасила бы и саму всплывашку */}
            <span className="dds-tip-label">{children}</span>
        </Tooltip>
    );
}
