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
