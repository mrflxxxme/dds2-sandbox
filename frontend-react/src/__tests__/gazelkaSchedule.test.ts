import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import {
    allowedDates,
    dateChoices,
    earliestDelivery,
    findPlan,
    formatAllowedDays,
    nextAllowedDate,
    validDelivery,
    warehousesFor,
} from '@/lib/gazelkaSchedule';
import type { GazelkaFormOptions, GazelkaSchedulePlan } from '@/types/api';

// Казань WB (place 25): грузят Пн/Ср, принимают Вт/Чт, путь 1 день.
const KAZAN: GazelkaSchedulePlan = { loading_days: [1, 3], delivery_days: [2, 4], eta_days: 1 };
// Тула WB (place 18): без ограничений по дням, путь 2 дня.
const TULA: GazelkaSchedulePlan = { loading_days: null, delivery_days: null, eta_days: 2 };

function options(): GazelkaFormOptions {
    return {
        entities: [],
        price_lists: [],
        marketplaces: [],
        delivery_warehouses: [
            { value: 'Казань', label: 'Казань', place_id: '25', marketplace_id: '4' },
            { value: 'Казань', label: 'Казань', place_id: '26', marketplace_id: '1' },
            { value: 'Тула', label: 'Тула', place_id: '18', marketplace_id: '4' },
            { value: 'Сочи', label: 'Сочи', place_id: '31', marketplace_id: '4' },
        ],
        supply_types: [],
        timeslots: [],
        default_entity_id: '6596',
        default_price_id: '1',
        schedule: { '1-25': KAZAN, '1-18': TULA, '1-26': { ...KAZAN, loading_days: [5] } },
        min_departure_date: '2026-07-10',
        min_delivery_date: '2026-07-11',
    };
}

beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 10)); // пт, 10 июля 2026
});
afterEach(() => vi.useRealTimers());

describe('nextAllowedDate', () => {
    it('возвращает саму дату, когда день недели подходит', () => {
        expect(nextAllowedDate('2026-07-13', [1, 3])).toBe('2026-07-13'); // пн
    });

    it('перескакивает на ближайший допустимый день', () => {
        expect(nextAllowedDate('2026-07-10', [1, 3])).toBe('2026-07-13'); // пт → пн
    });

    it('без ограничений отдаёт исходную дату', () => {
        expect(nextAllowedDate('2026-07-10', null)).toBe('2026-07-10');
    });

    it('не спотыкается о границу месяца', () => {
        expect(nextAllowedDate('2026-07-30', [0])).toBe('2026-08-02'); // чт → вс
    });
});

describe('allowedDates', () => {
    it('перечисляет только допустимые дни недели', () => {
        expect(allowedDates('2026-07-10', [1, 3], 4)).toEqual([
            '2026-07-13', '2026-07-15', '2026-07-20', '2026-07-22',
        ]);
    });

    it('без ограничений — подряд идущие дни', () => {
        expect(allowedDates('2026-07-10', null, 3)).toEqual(['2026-07-10', '2026-07-11', '2026-07-12']);
    });
});

describe('earliestDelivery', () => {
    it('добавляет ETA и подтягивает к приёмному дню', () => {
        expect(earliestDelivery(KAZAN, '2026-07-13')).toBe('2026-07-14'); // пн + 1 = вт ✓
        expect(earliestDelivery(KAZAN, '2026-07-15')).toBe('2026-07-16'); // ср + 1 = чт ✓
    });

    it('пропускает неприёмные дни', () => {
        // отправка сб 18-го + 1 = вс → ближайший приёмный вт 21-го
        expect(earliestDelivery(KAZAN, '2026-07-18')).toBe('2026-07-21');
    });
});

describe('findPlan / warehousesFor', () => {
    it('различает одноимённые склады разных маркетплейсов', () => {
        expect(findPlan(options(), '1', 'Казань', '4')?.loading_days).toEqual([1, 3]);
        expect(findPlan(options(), '1', 'Казань', '1')?.loading_days).toEqual([5]);
    });

    it('направление без графика для города — недоступно', () => {
        expect(findPlan(options(), '5', 'Казань', '4')).toBeNull();
    });

    it('прячет склады чужого маркетплейса и без активного графика', () => {
        const values = warehousesFor(options(), '1', '4').map(o => o.place_id);
        expect(values).toEqual(['25', '18']); // Сочи (нет графика) и Казань-Ozon отфильтрованы
    });
});

describe('dateChoices', () => {
    it('не предлагает даты в прошлом и вне дней погрузки', () => {
        const { departures } = dateChoices(options(), KAZAN, '');
        expect(departures[0]).toBe('2026-07-13'); // пн
        expect(departures[1]).toBe('2026-07-15'); // ср
    });

    it('протухшая дата отправки не считается выбранной и не тянет за собой доставки', () => {
        const choices = dateChoices(options(), KAZAN, '2026-06-30');
        expect(choices.departure).toBe(''); // иначе уехала бы в портал → 500
        expect(choices.deliveries).toEqual([]);
        expect(validDelivery(choices, '2026-07-04')).toBe('');
    });

    it('доставки пересчитываются от выбранной отправки', () => {
        const choices = dateChoices(options(), KAZAN, '2026-07-15');
        expect(choices.departure).toBe('2026-07-15');
        expect(choices.deliveries[0]).toBe('2026-07-16');
        expect(validDelivery(choices, '2026-07-16')).toBe('2026-07-16');
        expect(validDelivery(choices, '2026-07-15')).toBe(''); // не приёмный день
    });

    it('min_departure_date из прошлого не открывает прошлые даты', () => {
        const stale = { ...options(), min_departure_date: '2026-01-01' };
        expect(dateChoices(stale, TULA, '').departures[0]).toBe('2026-07-10'); // сегодня
    });
});

describe('formatAllowedDays', () => {
    it('сортирует дни по порядку недели', () => {
        expect(formatAllowedDays([5, 1, 3])).toBe('Пн/Ср/Пт');
        expect(formatAllowedDays(null)).toBe('любой день');
    });
});
