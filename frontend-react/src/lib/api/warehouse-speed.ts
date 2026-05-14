/** Warehouse Speed Priority API methods
 *
 * Backend: backend/routers/warehouse_speed.py — карта приоритетов WB-складов
 * по скорости доставки в города (округа РФ). Используется на странице
 * (main)/p/[slug]/warehouse/speed для расчёта «реалистичного потолка ИЛ»
 * (индекс локализации) при текущем составе портфеля.
 */
import { ApiClient } from './client';
import type {
    BasketEvaluation,
    CitySpeedDTO,
    OkrugInfo,
    SpeedMeta,
} from '@/types/api';

export function addWarehouseSpeedMethods(api: ApiClient) {
    return {
        warehouseSpeed: {
            /** Метаданные карты: версия, источник, число городов, округов. */
            getMeta() {
                return api.request<SpeedMeta>('GET', '/api/v1/warehouse/speed/meta');
            },

            /** Топ-якоря и топ-воришки по каждому ФО. */
            getOkrugInfo() {
                return api.request<OkrugInfo[]>('GET', '/api/v1/warehouse/speed/okrug-info');
            },

            /** Оценка корзины складов: реалистичный потолок ИЛ + warnings.
             *
             *  ВАЖНО: query через URLSearchParams — имена WB-складов содержат
             *  «&», пробелы, кириллицу; template literals ломают endpoint.
             *  Пустой массив warehouses → бросаем ошибку, не отправляем `warehouses=`. */
            evaluate(warehouses: string[], includeCities = false) {
                if (!warehouses || warehouses.length === 0) {
                    throw new Error('warehouseSpeed.evaluate: warehouses array must be non-empty');
                }
                const q = new URLSearchParams();
                q.set('warehouses', warehouses.join(','));
                if (includeCities) q.set('include_cities', 'true');
                return api.request<BasketEvaluation>(
                    'GET',
                    `/api/v1/warehouse/speed/evaluate?${q.toString()}`,
                );
            },

            /** Полный список городов с приоритетами складов (для отладки/Excel). */
            getCities() {
                return api.request<CitySpeedDTO[]>('GET', '/api/v1/warehouse/speed/cities');
            },
        },
    };
}
