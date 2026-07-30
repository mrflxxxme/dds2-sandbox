'use client';

import { useEffect, useState } from 'react';

const MONTH_NAMES = [
    'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
    'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
];

/**
 * Компактный выбор месяца: два селекта (месяц + год) вместо нативного
 * type="month" — его браузерный попап перекрывал модалки. value: 'YYYY-MM'
 * или '' (не выбран); onChange зовётся только с полной парой.
 */
export default function MonthField({
    value, onChange, yearFrom = 2020,
}: { value: string; onChange: (v: string) => void; yearFrom?: number }) {
    const now = new Date();
    const years: number[] = [];
    for (let y = yearFrom; y <= now.getFullYear() + 2; y++) years.push(y);

    const [vy, vm] = /^\d{4}-\d{2}$/.test(value) ? value.split('-') : ['', ''];
    // Частичный выбор живёт локально; наружу уходит только полная пара.
    const [month, setMonth] = useState(vm);
    const [year, setYear] = useState(vy);

    useEffect(() => {
        setMonth(vm);
        setYear(vy);
    }, [vm, vy]);

    const emit = (m: string, y: string) => {
        setMonth(m);
        setYear(y);
        if (m && y) onChange(`${y}-${m}`);
        // Сброс любой половины при заполненном значении = очистка (для
        // опциональных границ «с/по»); частичный выбор с нуля не эмитится.
        else if (value) onChange('');
    };

    return (
        <div style={{ display: 'flex', gap: 4 }}>
            <select
                className="form-input"
                style={{ width: 110 }}
                value={month}
                onChange={(e) => emit(e.target.value, year || String(now.getFullYear()))}
            >
                <option value="">месяц</option>
                {MONTH_NAMES.map((name, i) => (
                    <option key={name} value={String(i + 1).padStart(2, '0')}>{name}</option>
                ))}
            </select>
            <select
                className="form-input"
                style={{ width: 84 }}
                value={year}
                onChange={(e) => emit(month, e.target.value)}
            >
                <option value="">год</option>
                {years.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
        </div>
    );
}
