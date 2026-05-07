/**
 * Localization Index — справочные константы федеральных округов.
 *
 * Используется для:
 * - стабильного порядка колонок в таблицах
 * - локализованных названий ru
 * - цветов group-headers (background)
 *
 * Ключи синхронизированы с backend (см. `backend/services/localization_index_service.py`).
 */

export const DISTRICT_ORDER = [
    'central',
    'south_caucasus',
    'volga',
    'ural',
    'far_east_siberia',
    'northwest',
    'abroad',
] as const;

export type DistrictKey = (typeof DISTRICT_ORDER)[number] | 'unknown';

export const DISTRICT_LABELS: Record<string, string> = {
    central: 'Центральный',
    south_caucasus: 'Южный и Северо-Кавказский',
    volga: 'Приволжский',
    ural: 'Уральский',
    far_east_siberia: 'Дальневосточный и Сибирский',
    northwest: 'Северо-Западный',
    abroad: 'Зарубеж',
    unknown: 'Неизвестно',
};

export const DISTRICT_COLORS: Record<string, string> = {
    central: '#1e40af',          // синий
    south_caucasus: '#9f1239',   // бордовый
    volga: '#a16207',            // горчичный
    ural: '#c2410c',             // оранжевый
    far_east_siberia: '#15803d', // зелёный
    northwest: '#1e3a8a',        // тёмно-синий
    abroad: '#475569',           // серый
    unknown: '#6b7280',          // нейтральный серый
};

/**
 * Зеркало backend-таблицы КТР (см. backend/services/localization_tariff.py).
 * Используется для what-if симулятора и вспомогательных расчётов на фронте.
 *
 * Граничное правило: lo ≤ pct < hi. Верхняя граница последнего диапазона —
 * 100.01, чтобы 100% попадало в самый «зелёный» бакет.
 */
export const KTR_TABLE: ReadonlyArray<readonly [number, number, number]> = [
    [95.0, 100.01, 0.5],
    [90.0, 95.0, 0.6],
    [85.0, 90.0, 0.7],
    [80.0, 85.0, 0.8],
    [75.0, 80.0, 0.9],
    [60.0, 75.0, 1.0],
    [55.0, 60.0, 1.05],
    [50.0, 55.0, 1.1],
    [45.0, 50.0, 1.2],
    [40.0, 45.0, 1.3],
    [35.0, 40.0, 1.4],
    [30.0, 35.0, 1.5],
    [25.0, 30.0, 1.55],
    [20.0, 25.0, 1.6],
    [15.0, 20.0, 1.7],
    [10.0, 15.0, 1.75],
    [5.0, 10.0, 1.8],
    [0.0, 5.0, 2.0],
] as const;

export const KRP_TABLE: ReadonlyArray<readonly [number, number, number]> = [
    [60.0, 100.01, 0.0],
    [55.0, 60.0, 2.0],
    [50.0, 55.0, 2.05],
    [45.0, 50.0, 2.05],
    [40.0, 45.0, 2.1],
    [35.0, 40.0, 2.1],
    [30.0, 35.0, 2.15],
    [25.0, 30.0, 2.2],
    [20.0, 25.0, 2.25],
    [15.0, 20.0, 2.3],
    [10.0, 15.0, 2.35],
    [5.0, 10.0, 2.45],
    [0.0, 5.0, 2.5],
] as const;

/** КТР по доле локализации (lo ≤ pct < hi). Fallback 1.00 (нейтральный). */
export function getKtr(locPct: number): number {
    for (const [lo, hi, val] of KTR_TABLE) {
        if (locPct >= lo && locPct < hi) return val;
    }
    return 1.0;
}

/** КРП (% к цене) по доле локализации. Fallback 0.00 (без удержания). */
export function getKrp(locPct: number): number {
    for (const [lo, hi, val] of KRP_TABLE) {
        if (locPct >= lo && locPct < hi) return val;
    }
    return 0.0;
}

/** Идеальный КТР — самый «зелёный» бакет (≥95% локализации). */
export const IDEAL_KTR = 0.5;
export const IDEAL_KRP = 0.0;
