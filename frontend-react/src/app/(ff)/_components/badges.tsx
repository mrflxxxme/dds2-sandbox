'use client';

import type { FfAssemblyStatus, FfAcceptanceStatus } from '@/types/ff';

/** Project label as a neutral pill — shown on every row. */
export function ProjectBadge({ name }: { name: string }) {
    return <span className="badge badge-secondary">{name}</span>;
}

const ASSEMBLY_LABELS: Record<FfAssemblyStatus, string> = {
    PENDING: 'Новая',
    IN_PROGRESS: 'В работе',
    READY: 'Готова',
    VEHICLE_ASSIGNED: 'Машина назначена',
    SHIPPED: 'Отгружена',
    DELIVERED: 'Доставлена',
    RETURNED: 'Возвращена',
    CLOSED: 'Закрыта',
    CANCELLED: 'Отменена',
};

const ASSEMBLY_VARIANTS: Record<FfAssemblyStatus, string> = {
    PENDING: 'badge-secondary',
    IN_PROGRESS: 'badge-info',
    READY: 'badge-warning',
    VEHICLE_ASSIGNED: 'badge-info',
    SHIPPED: 'badge-success',
    DELIVERED: 'badge-success',
    RETURNED: 'badge-danger',
    CLOSED: 'badge-secondary',
    CANCELLED: 'badge-danger',
};

export function AssemblyStatusBadge({ status }: { status: FfAssemblyStatus }) {
    return <span className={`badge ${ASSEMBLY_VARIANTS[status]}`}>{ASSEMBLY_LABELS[status]}</span>;
}

const ACCEPTANCE_LABELS: Record<FfAcceptanceStatus, string> = {
    EXPECTED: 'Ожидается',
    ACCEPTED: 'Принята',
};

const ACCEPTANCE_VARIANTS: Record<FfAcceptanceStatus, string> = {
    EXPECTED: 'badge-warning',
    ACCEPTED: 'badge-success',
};

export function AcceptanceStatusBadge({ status }: { status: FfAcceptanceStatus }) {
    return <span className={`badge ${ACCEPTANCE_VARIANTS[status]}`}>{ACCEPTANCE_LABELS[status]}</span>;
}

export function ReturnBadge() {
    return <span className="badge badge-danger">Возврат WB</span>;
}
