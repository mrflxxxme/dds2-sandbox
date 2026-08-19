'use client';
import React from 'react';
import Tooltip from './Tooltip';

/** Подсказка при наведении на сам элемент (заголовок колонки) — без значка «?».
 *  Раскрывается ВНИЗ: над шапкой места нет. Всплывашку рисует Tooltip порталом,
 *  поэтому контейнер таблицы с overflow её больше не срезает.
 *  Раньше каждый инстанс инжектил собственный <style> — теперь стили общие (globals.css).
 *
 *  icon=true — режим со значком «?» рядом с элементом: нужен там, где подсказку
 *  не на что повесить (цифра метрики, цветной чип, иконка-точка). Дефолт false =
 *  прежнее поведение, чтобы существующие потребители не поехали. */
export default function InfoTip({ text, icon = false, placement = 'bottom', children }: {
    text: string;
    icon?: boolean;
    placement?: 'top' | 'bottom';
    /** В режиме icon необязателен: значок «?» может стоять и сам по себе. */
    children?: React.ReactNode;
}) {
    if (icon) {
        return (
            <span className="dds-tip-host">
                {children}
                <Tooltip text={text} placement={placement}>
                    <span className="dds-tip-icon" role="img" aria-label="Подсказка" tabIndex={0}>?</span>
                </Tooltip>
            </span>
        );
    }
    return (
        <Tooltip text={text} placement={placement} className="dds-tip--label">
            {/* Бледнеет только подпись: opacity на обёртке погасила бы и саму всплывашку */}
            <span className="dds-tip-label">{children}</span>
        </Tooltip>
    );
}
