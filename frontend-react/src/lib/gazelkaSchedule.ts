/**
 * График Газельки: какие даты отправки и доставки склад реально принимает.
 *
 * Портал держит расписание в JS-переменной `schedule` своей формы и гасит недоступные
 * дни в календаре; попытка отправить заявку на «серый» день кончается 500-й от их
 * сервера. Бэкенд снимает это расписание живьём (`GazelkaFormOptions.schedule`), здесь
 * мы разворачиваем его в списки конкретных дат для селектов модалки.
 */
import type { GazelkaFormOptions, GazelkaSchedulePlan, GazelkaSelectOption } from '@/types/api';

/** Сколько ближайших допустимых дат показывать в селекте. */
const DATE_OPTIONS_COUNT = 12;
/** Горизонт поиска, дней — как в их форме (защита от склада без единого рабочего дня). */
const SEARCH_HORIZON = 90;

const WEEKDAY_NAMES = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

/** ISO-дата (YYYY-MM-DD) → Date в локальной зоне; без сдвига через UTC-парсинг. */
function parseISO(iso: string): Date {
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(y, m - 1, d);
}

export function toISO(d: Date): string {
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${d.getFullYear()}-${m}-${day}`;
}

function addDays(d: Date, days: number): Date {
    const out = new Date(d);
    out.setDate(out.getDate() + days);
    return out;
}

function todayISO(): string {
    return toISO(new Date());
}

function maxISO(a: string, b: string): string {
    return a > b ? a : b;
}

/** «Пн/Ср/Пт» — для подписи к полю. */
export function formatAllowedDays(days: number[] | null): string {
    if (!days || days.length === 0 || days.length === 7) return 'любой день';
    return [...days].sort((a, b) => a - b).map(d => WEEKDAY_NAMES[d]).join('/');
}

/** Ярлык даты для селекта: «вт, 14 июля». */
export function formatDateOption(iso: string): string {
    return parseISO(iso).toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'long' });
}

/** Ближайшая дата не раньше `fromISO`, чей день недели допустим. null — не нашли. */
export function nextAllowedDate(fromISO: string, days: number[] | null): string | null {
    if (!days || days.length === 0) return fromISO;
    const start = parseISO(fromISO);
    for (let i = 0; i < SEARCH_HORIZON; i++) {
        const candidate = addDays(start, i);
        if (days.includes(candidate.getDay())) return toISO(candidate);
    }
    return null;
}

/** Список ближайших допустимых дат начиная с `fromISO`. */
export function allowedDates(fromISO: string, days: number[] | null, count = DATE_OPTIONS_COUNT): string[] {
    const out: string[] = [];
    const start = parseISO(fromISO);
    for (let i = 0; i < SEARCH_HORIZON && out.length < count; i++) {
        const candidate = addDays(start, i);
        if (!days || days.length === 0 || days.includes(candidate.getDay())) out.push(toISO(candidate));
    }
    return out;
}

/** График выбранного направления: «{price_id}-{place_id}». null — направление не обслуживается. */
export function findPlan(
    options: GazelkaFormOptions,
    priceId: string,
    address: string,
    marketplaceId: string,
): GazelkaSchedulePlan | null {
    const place = findPlace(options.delivery_warehouses, address, marketplaceId);
    if (!place?.place_id) return null;
    return options.schedule[`${priceId}-${place.place_id}`] ?? null;
}

/** Опция склада: название неуникально между маркетплейсами — ищем пару (название, mpid). */
export function findPlace(
    warehouses: GazelkaSelectOption[],
    address: string,
    marketplaceId: string,
): GazelkaSelectOption | null {
    return warehouses.find(o => o.value === address && o.marketplace_id === marketplaceId) ?? null;
}

/** Склады выбранного маркетплейса, у которых есть активный график из выбранного города. */
export function warehousesFor(
    options: GazelkaFormOptions,
    priceId: string,
    marketplaceId: string,
): GazelkaSelectOption[] {
    return options.delivery_warehouses.filter(
        o => o.marketplace_id === marketplaceId && o.place_id && options.schedule[`${priceId}-${o.place_id}`],
    );
}

/** Нижняя граница даты отправки: то, что отдал портал, но не в прошлом. */
export function minDeparture(options: GazelkaFormOptions): string {
    return maxISO(options.min_departure_date ?? todayISO(), todayISO());
}

/** Самая ранняя доставка при данной отправке: +ETA дней, затем ближайший приёмный день. */
export function earliestDelivery(plan: GazelkaSchedulePlan, departureISO: string): string | null {
    return nextAllowedDate(toISO(addDays(parseISO(departureISO), plan.eta_days)), plan.delivery_days);
}

export interface DateChoices {
    departures: string[];
    deliveries: string[];
    /**
     * Выбранная дата отправки, если она есть среди допустимых; иначе ''.
     * Прежняя дата (протухший prefill, дата редактируемой заявки) не должна молча
     * уезжать в портал: селект её не показывает, а значит выбранной она не является.
     */
    departure: string;
}

/** Даты для селектов: отправки — от нижней границы портала, доставки — от отправки + ETA. */
export function dateChoices(
    options: GazelkaFormOptions,
    plan: GazelkaSchedulePlan,
    departureISO: string,
): DateChoices {
    const departures = allowedDates(minDeparture(options), plan.loading_days);
    const departure = departures.includes(departureISO) ? departureISO : '';
    if (!departure) return { departures, deliveries: [], departure: '' };

    const earliest = earliestDelivery(plan, departure);
    return {
        departures,
        deliveries: earliest ? allowedDates(earliest, plan.delivery_days) : [],
        departure,
    };
}

/** Дата доставки, только если она допустима при выбранной отправке; иначе ''. */
export function validDelivery(choices: DateChoices, deliveryISO: string): string {
    return choices.deliveries.includes(deliveryISO) ? deliveryISO : '';
}
