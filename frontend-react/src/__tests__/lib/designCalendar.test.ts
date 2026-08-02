import { describe, expect, it } from 'vitest';
import {
    DESIGNER_PALETTE,
    buildAssigneeColors,
    buildMonthGrid,
    formatMonthTitle,
    memberDisplayName,
    shiftMonth,
} from '@/lib/designCalendar';

describe('buildMonthGrid — месячная сетка (AC-1 F5: январь-2027)', () => {
    const weeks = buildMonthGrid('2027-01', '2027-01-15');
    const flat = weeks.flat();

    it('каждая неделя — ровно 7 дней', () => {
        expect(weeks.length).toBeGreaterThan(0);
        for (const w of weeks) expect(w).toHaveLength(7);
    });

    it('январь-2027 начинается с пятницы (индекс 4 при неделе с Пн)', () => {
        const idx = flat.findIndex((d) => d.iso === '2027-01-01');
        expect(idx).toBe(4); // Пн=0 … Пт=4 первой недели
        expect(flat[idx].inMonth).toBe(true);
        expect(flat[idx].dayOfMonth).toBe(1);
    });

    it('в месяце 31 день, сетка добита соседними месяцами до полных недель', () => {
        const inMonth = flat.filter((d) => d.inMonth);
        expect(inMonth).toHaveLength(31);
        expect(inMonth[0].iso).toBe('2027-01-01');
        expect(inMonth[30].iso).toBe('2027-01-31');
        // Хвост декабря: Пн 28.12.2026 … Чт 31.12.2026.
        expect(flat[0]).toMatchObject({ iso: '2026-12-28', inMonth: false });
        // 31.01.2027 — воскресенье: сетка заканчивается ровно им, 5 недель.
        expect(weeks).toHaveLength(5);
        expect(flat[flat.length - 1].iso).toBe('2027-01-31');
    });

    it('isToday отмечает переданную дату', () => {
        expect(flat.filter((d) => d.isToday)).toHaveLength(1);
        expect(flat.find((d) => d.isToday)?.iso).toBe('2027-01-15');
    });

    it('февраль-2027: 28 дней, недели с Пн', () => {
        const feb = buildMonthGrid('2027-02', '2027-01-15').flat();
        expect(feb.filter((d) => d.inMonth)).toHaveLength(28);
        // 1 февраля 2027 — понедельник: сетка начинается без хвоста января.
        expect(feb[0].iso).toBe('2027-02-01');
    });

    it('май-2027: 1 мая — суббота → сетка разворачивается в 6 недель', () => {
        const weeks6 = buildMonthGrid('2027-05', '2027-05-10');
        const flat6 = weeks6.flat();
        expect(weeks6).toHaveLength(6);
        for (const w of weeks6) expect(w).toHaveLength(7);
        expect(flat6).toHaveLength(42);
        // Хвост апреля: Пн 26.04.2027 … Пт 30.04; хвост июня: Вт 01.06 … Вс 06.06.
        expect(flat6[0]).toMatchObject({ iso: '2027-04-26', inMonth: false });
        expect(flat6[41]).toMatchObject({ iso: '2027-06-06', inMonth: false });
        const inMonth = flat6.filter((d) => d.inMonth);
        expect(inMonth).toHaveLength(31);
        expect(inMonth[0].iso).toBe('2027-05-01');
        // 1 мая — суббота: индекс 5 в первой неделе (Пн=0).
        expect(flat6.findIndex((d) => d.iso === '2027-05-01')).toBe(5);
    });

    it('окно сетки не выходит за «1-е − 6» / «последнее + 6» (контракт /calendar)', () => {
        // Бэк отдаёт задачи в окне month_first−6 … last_day+6 (queries.list_calendar,
        // CONTRACT.md): сетка обязана укладываться в него, иначе крайние дни пустые.
        const months = ['2027-01', '2027-02', '2027-05', '2027-08', '2026-12', '2028-02'];
        for (const month of months) {
            const flat = buildMonthGrid(month, '2027-01-15');
            const days = flat.flat();
            const [y, m] = month.split('-').map(Number);
            const first = new Date(y, m - 1, 1);
            const last = new Date(y, m, 0);
            const lowerBound = new Date(y, m - 1, -5); // 1-е − 6
            const upperBound = new Date(y, m, 6); // последнее число + 6
            const iso = (d: Date) =>
                `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

            expect(days[0].iso >= iso(lowerBound)).toBe(true);
            expect(days[days.length - 1].iso <= iso(upperBound)).toBe(true);
            // И само окно месяца сетка покрывает целиком.
            expect(days[0].iso <= iso(first)).toBe(true);
            expect(days[days.length - 1].iso >= iso(last)).toBe(true);
        }
    });
});

describe('shiftMonth — стрелки месяцев', () => {
    it('через границу года в обе стороны', () => {
        expect(shiftMonth('2027-01', -1)).toBe('2026-12');
        expect(shiftMonth('2026-12', 1)).toBe('2027-01');
        expect(shiftMonth('2027-06', 1)).toBe('2027-07');
        expect(shiftMonth('2027-06', -6)).toBe('2026-12');
    });

    it('перелёт через несколько лет в обе стороны', () => {
        expect(shiftMonth('2027-01', -13)).toBe('2025-12');
        expect(shiftMonth('2027-01', 25)).toBe('2029-02');
        expect(shiftMonth('2027-01', -24)).toBe('2025-01');
        expect(shiftMonth('2027-01', 12)).toBe('2028-01');
        // Обратимость: туда-обратно возвращает исходный месяц.
        expect(shiftMonth(shiftMonth('2027-01', -13), 13)).toBe('2027-01');
    });
});

describe('formatMonthTitle', () => {
    it('русское название месяца + год', () => {
        expect(formatMonthTitle('2027-01')).toBe('Январь 2027');
        expect(formatMonthTitle('2026-12')).toBe('Декабрь 2026');
    });
});

describe('раскраска по дизайнеру — стабильная палитра по user_id', () => {
    const members = [
        { user_id: 30, username: 'c', first_name: 'Вера', last_name: 'В' },
        { user_id: 10, username: 'a', first_name: 'Анна', last_name: 'А' },
        { user_id: 20, username: 'b', first_name: '', last_name: null },
    ];

    it('индекс — из сортировки user_id по возрастанию, не порядка ответа', () => {
        const colors = buildAssigneeColors(members);
        expect(colors.get('Анна А')).toBe(DESIGNER_PALETTE[0]); // user_id 10
        expect(colors.get('b')).toBe(DESIGNER_PALETTE[1]); // user_id 20, имя пустое → username
        expect(colors.get('Вера В')).toBe(DESIGNER_PALETTE[2]); // user_id 30
    });

    it('перестановка входного массива не меняет цвета (стабильность)', () => {
        const shuffled = [members[1], members[2], members[0]];
        expect(buildAssigneeColors(shuffled)).toEqual(buildAssigneeColors(members));
    });

    it('memberDisplayName зеркалит бэковый _user_names', () => {
        expect(memberDisplayName({ user_id: 1, username: 'u', first_name: 'И', last_name: 'Ф' })).toBe('И Ф');
        expect(memberDisplayName({ user_id: 1, username: 'u', first_name: null, last_name: null })).toBe('u');
        expect(memberDisplayName({ user_id: 1, username: 'u', first_name: 'И', last_name: '' })).toBe('И');
    });
});
