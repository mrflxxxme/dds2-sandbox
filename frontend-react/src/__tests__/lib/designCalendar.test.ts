import { describe, expect, it } from 'vitest';
import {
    DESIGNER_PALETTE,
    buildAssigneeColors,
    buildMonthGrid,
    buildTaskRange,
    formatMonthTitle,
    isInTaskRange,
    markersByDay,
    MAX_CALENDAR_MONTHS,
    countMonthsInRange,
    defaultCalendarRange,
    memberDisplayName,
    monthsInRange,
    shiftCalendarRange,
    shiftMonth,
    taskRangeEdge,
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

describe('buildTaskRange — диапазон работы для персонального календаря задачи', () => {
    const TODAY = '2027-03-20';

    it('start = started_at, когда задачу взяли в работу', () => {
        const r = buildTaskRange(
            {
                created_at: '2027-03-01T09:15:00',
                started_at: '2027-03-05T12:00:00',
                due_date: '2027-03-12',
                status: 'IN_PROGRESS',
            },
            TODAY,
        );
        expect(r.start).toBe('2027-03-05');
        expect(r.end).toBe('2027-03-12');
        expect(r.due).toBe('2027-03-12');
        expect(r.startSource).toBe('started');
    });

    it('start = created_at, когда started_at пуст (задачу ещё не взяли)', () => {
        const r = buildTaskRange(
            { created_at: '2027-03-01T09:15:00', started_at: null, due_date: '2027-03-12', status: 'NEW' },
            TODAY,
        );
        expect(r.start).toBe('2027-03-01');
        expect(r.end).toBe('2027-03-12');
        expect(r.startSource).toBe('created');
    });

    it('карточка доски (есть только due_date) — полоса в один день, без падения', () => {
        const r = buildTaskRange({ due_date: '2027-03-12', status: 'ASSIGNED' }, TODAY);
        expect(r.start).toBe('2027-03-12');
        expect(r.end).toBe('2027-03-12');
        expect(r.startSource).toBeNull();
        expect(r.markers).toEqual([{ iso: '2027-03-12', kind: 'due' }]);
        expect(taskRangeEdge(r, '2027-03-12')).toBe('single');
    });

    it('работу начали ПОЗЖЕ срока — полоса разворачивается от срока до старта', () => {
        const r = buildTaskRange(
            { created_at: '2027-03-01T09:00:00', started_at: '2027-03-18T09:00:00', due_date: '2027-03-12', status: 'IN_PROGRESS' },
            TODAY,
        );
        expect(r.start).toBe('2027-03-12');
        expect(r.end).toBe('2027-03-18');
        expect(r.due).toBe('2027-03-12');
    });

    it('стартовый месяц модалки — месяц срока; без срока — месяц события; совсем без дат — текущий', () => {
        expect(buildTaskRange({ created_at: '2027-01-30T10:00:00', due_date: '2027-02-03' }, TODAY).month).toBe('2027-02');
        expect(buildTaskRange({ created_at: '2027-01-30T10:00:00' }, TODAY).month).toBe('2027-01');
        expect(buildTaskRange({}, TODAY).month).toBe('2027-03');
    });

    it('маркеры собираются по всем заданным датам и группируются по дню', () => {
        const r = buildTaskRange(
            {
                created_at: '2027-03-01T09:00:00',
                started_at: '2027-03-01T18:00:00', // тот же день, что постановка
                due_date: '2027-03-12',
                accepted_at: '2027-03-11T08:00:00',
                status: 'ACCEPTED',
            },
            TODAY,
        );
        expect(r.markers.map((m) => m.kind)).toEqual(['created', 'started', 'due', 'accepted']);
        const byDay = markersByDay(r);
        expect(byDay.get('2027-03-01')).toEqual(['created', 'started']);
        expect(byDay.get('2027-03-11')).toEqual(['accepted']);
        expect(byDay.get('2027-03-12')).toEqual(['due']);
    });
});

describe('buildTaskRange — без срока', () => {
    const TODAY = '2027-03-20';

    it('полосы нет, остаётся только точка постановки', () => {
        const r = buildTaskRange({ created_at: '2027-03-02T09:00:00', due_date: null, status: 'NEW' }, TODAY);
        expect(r.start).toBeNull();
        expect(r.end).toBeNull();
        expect(r.due).toBeNull();
        expect(r.isOverdue).toBe(false);
        expect(r.markers).toEqual([{ iso: '2027-03-02', kind: 'created' }]);
    });

    it('ни один день не попадает в диапазон', () => {
        const r = buildTaskRange({ created_at: '2027-03-02T09:00:00', due_date: null }, TODAY);
        for (const iso of ['2027-03-01', '2027-03-02', '2027-03-20', '2027-04-01']) {
            expect(isInTaskRange(r, iso)).toBe(false);
            expect(taskRangeEdge(r, iso)).toBeNull();
        }
    });
});

describe('isInTaskRange / taskRangeEdge — попадание дня в диапазон', () => {
    const TODAY = '2027-03-20';
    const range = buildTaskRange(
        { created_at: '2027-03-01T09:00:00', started_at: '2027-03-05T09:00:00', due_date: '2027-03-12', status: 'IN_PROGRESS' },
        TODAY,
    );

    it('границы включительно, соседние дни — вне', () => {
        expect(isInTaskRange(range, '2027-03-04')).toBe(false);
        expect(isInTaskRange(range, '2027-03-05')).toBe(true);
        expect(isInTaskRange(range, '2027-03-09')).toBe(true);
        expect(isInTaskRange(range, '2027-03-12')).toBe(true);
        expect(isInTaskRange(range, '2027-03-13')).toBe(false);
    });

    it('края диапазона скругляются, середина — прямая', () => {
        expect(taskRangeEdge(range, '2027-03-05')).toBe('start');
        expect(taskRangeEdge(range, '2027-03-08')).toBe('middle');
        expect(taskRangeEdge(range, '2027-03-12')).toBe('end');
        expect(taskRangeEdge(range, '2027-03-13')).toBeNull();
    });

    it('диапазон через границу месяца сравнивается строками YYYY-MM-DD корректно', () => {
        const r = buildTaskRange(
            { created_at: '2027-02-25T09:00:00', started_at: null, due_date: '2027-03-03', status: 'NEW' },
            TODAY,
        );
        expect(isInTaskRange(r, '2027-02-24')).toBe(false);
        expect(isInTaskRange(r, '2027-02-28')).toBe(true);
        expect(isInTaskRange(r, '2027-03-01')).toBe(true);
        expect(isInTaskRange(r, '2027-03-04')).toBe(false);
    });
});

describe('buildTaskRange — просрочка', () => {
    const TODAY = '2027-03-20';

    it('срок в прошлом и задача не завершена → просрочка', () => {
        expect(buildTaskRange({ due_date: '2027-03-19', status: 'IN_PROGRESS' }, TODAY).isOverdue).toBe(true);
    });

    it('срок сегодня или в будущем → не просрочка (сравнение ДАТ, не моментов)', () => {
        expect(buildTaskRange({ due_date: TODAY, status: 'IN_PROGRESS' }, TODAY).isOverdue).toBe(false);
        expect(buildTaskRange({ due_date: '2027-03-21', status: 'IN_PROGRESS' }, TODAY).isOverdue).toBe(false);
    });

    it('принятая / отменённая / с accepted_at задача не просрочена', () => {
        expect(buildTaskRange({ due_date: '2027-03-01', status: 'ACCEPTED' }, TODAY).isOverdue).toBe(false);
        expect(buildTaskRange({ due_date: '2027-03-01', status: 'CANCELLED' }, TODAY).isOverdue).toBe(false);
        expect(buildTaskRange({ due_date: '2027-03-01', accepted_at: '2027-02-28T10:00:00' }, TODAY).isOverdue).toBe(false);
    });

    it('без срока просрочки не бывает', () => {
        expect(buildTaskRange({ created_at: '2026-01-01T00:00:00', status: 'IN_PROGRESS' }, TODAY).isOverdue).toBe(false);
    });
});

describe('произвольный диапазон календаря (волна A v2, Р22)', () => {
    it('дефолт — ровно два блока: текущий месяц и следующий', () => {
        const r = defaultCalendarRange(new Date(2026, 7, 20));
        expect([r.from, r.to]).toEqual(['2026-08-01', '2026-09-30']);
        expect(monthsInRange(r)).toEqual(['2026-08', '2026-09']);
    });

    it('дефолт на стыке года не ломается', () => {
        const r = defaultCalendarRange(new Date(2026, 11, 5));
        expect([r.from, r.to]).toEqual(['2026-12-01', '2027-01-31']);
        expect(monthsInRange(r)).toEqual(['2026-12', '2027-01']);
    });

    it('блок на каждый пересечённый месяц, даже если задет одним днём', () => {
        expect(monthsInRange({ from: '2026-08-31', to: '2026-10-01' })).toEqual(['2026-08', '2026-09', '2026-10']);
        expect(monthsInRange({ from: '2026-08-10', to: '2026-08-11' })).toEqual(['2026-08']);
    });

    it('стрелка сдвигает диапазон, сохраняя его длину в днях', () => {
        const start = defaultCalendarRange(new Date(2026, 7, 20));
        const next = shiftCalendarRange(start, 1);
        expect([next.from, next.to]).toEqual(['2026-09-01', '2026-10-31']);
        expect(monthsInRange(next)).toEqual(['2026-09', '2026-10']);

        const back = shiftCalendarRange(next, -1);
        expect([back.from, back.to]).toEqual([start.from, start.to]);
    });

    it('длина произвольного диапазона переживает сдвиг бит-в-бит', () => {
        const r = { from: '2026-03-05', to: '2026-03-19' };  // 15 дней
        const moved = shiftCalendarRange(r, 2);
        expect([moved.from, moved.to]).toEqual(['2026-05-05', '2026-05-19']);
    });
});

describe('предел числа блоков календаря (правка по ревью волны A)', () => {
    it('месяцев не больше шести, счётчик при этом отдаёт правду', () => {
        const year = { from: '2026-01-01', to: '2026-12-31' };
        expect(countMonthsInRange(year)).toBe(12);
        expect(monthsInRange(year)).toHaveLength(MAX_CALENDAR_MONTHS);
        expect(monthsInRange(year)[0]).toBe('2026-01');
    });

    it('дефолт и трёхмесячный период под предел не попадают', () => {
        const def = defaultCalendarRange(new Date(2026, 7, 20));
        expect(countMonthsInRange(def)).toBe(2);
        expect(monthsInRange(def)).toHaveLength(2);

        const q = { from: '2026-05-20', to: '2026-08-20' };
        expect(countMonthsInRange(q)).toBe(4);
        expect(monthsInRange(q)).toHaveLength(4);
    });

    it('счётчик корректен на стыке года', () => {
        expect(countMonthsInRange({ from: '2026-11-15', to: '2027-02-01' })).toBe(4);
    });
});
