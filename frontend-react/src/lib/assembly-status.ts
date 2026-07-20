import type { AssemblyStatus } from '@/types/api';

/**
 * Единый словарь статусов заявок на сборку.
 *
 * Раньше эта карта жила копией в каждой странице раздела и уже разъехалась
 * (в TMA-версии не было PRE_DISTRIBUTED / RETURNED / CLOSED, а подписи
 * расходились: «Новая» против «В сборке»). Держим один источник истины.
 */
export const ASSEMBLY_STATUS_MAP: Record<AssemblyStatus, { label: string; className: string }> = {
    // PENDING — legacy: больше не используется при создании, но может встретиться в истории.
    PENDING:          { label: 'В сборке',          className: 'badge-info' },
    PRE_DISTRIBUTED:  { label: 'Распределено',      className: 'badge-secondary' },
    IN_PROGRESS:      { label: 'В сборке',          className: 'badge-info' },
    READY:            { label: 'Готово',             className: 'badge-success' },
    VEHICLE_ASSIGNED: { label: 'Машина назначена',   className: 'badge-info' },
    SHIPPED:          { label: 'Отгружена',          className: 'badge-success' },
    DELIVERED:        { label: 'Принята WB',         className: 'badge-success' },
    RETURNED:         { label: 'Возврат на склад',   className: 'badge-warning' },
    CLOSED:           { label: 'Закрыт',             className: 'badge-warning' },
    CANCELLED:        { label: 'Отменена',           className: 'badge-secondary' },
};

/** Подпись статуса; неизвестный статус отдаём как есть, а не пустой строкой. */
export function assemblyStatusLabel(status: AssemblyStatus | string): string {
    return ASSEMBLY_STATUS_MAP[status as AssemblyStatus]?.label ?? String(status);
}
