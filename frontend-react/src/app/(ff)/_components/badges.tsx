'use client';

import type { FfAssemblyStatus, FfAcceptanceStatus } from '@/types/ff';

/** Project label as a neutral pill — shown on every row. */
export function ProjectBadge({ name }: { name: string }) {
    return <span className="badge badge-secondary">{name}</span>;
}

const ASSEMBLY_MAP: Record<FfAssemblyStatus, { label: string; className: string }> = {
    PENDING: { label: 'Ожидает сборку', className: 'badge-secondary' },
    IN_PROGRESS: { label: 'В сборке', className: 'badge-info' },
    READY: { label: 'Готово', className: 'badge-warning' },
    VEHICLE_ASSIGNED: { label: 'Машина назначена', className: 'badge-info' },
    SHIPPED: { label: 'Отгружена', className: 'badge-success' },
    DELIVERED: { label: 'Принята WB', className: 'badge-success' },
    RETURNED: { label: 'Возврат на склад', className: 'badge-danger' },
    CLOSED: { label: 'Закрыта', className: 'badge-secondary' },
    CANCELLED: { label: 'Отменена', className: 'badge-danger' },
};

export function AssemblyStatusBadge({ status }: { status: FfAssemblyStatus }) {
    const s = ASSEMBLY_MAP[status];
    return <span className={`badge ${s.className}`}>{s.label}</span>;
}

const ACCEPTANCE_MAP: Record<FfAcceptanceStatus, { label: string; className: string }> = {
    EXPECTED: { label: 'Ожидается', className: 'badge-secondary' },
    ACCEPTED: { label: 'Принята', className: 'badge-success' },
};

export function AcceptanceStatusBadge({ status }: { status: FfAcceptanceStatus }) {
    const s = ACCEPTANCE_MAP[status];
    return <span className={`badge ${s.className}`}>{s.label}</span>;
}

export function InWorkBadge() {
    return <span className="badge badge-info">В работе</span>;
}

export function ReturnBadge() {
    return <span className="badge badge-warning">Возврат WB</span>;
}

const STUCK_THRESHOLD_DAYS = 2;

/**
 * Days an assembly has been waiting in its current state, or null if not applicable.
 * IN_PROGRESS/PENDING → since created_at; READY → since actual_ready_date (it's been
 * assembled and is waiting for a vehicle). Date-only floor approximation, consistent
 * with the logist card's «висит N дн».
 */
export function assemblyStuckDays(
    status: FfAssemblyStatus,
    createdAt: string | null,
    actualReadyDate: string | null,
): number | null {
    let base: string | null = null;
    if (status === 'PENDING' || status === 'IN_PROGRESS') base = createdAt;
    else if (status === 'READY') base = actualReadyDate ?? createdAt;
    if (!base) return null;
    const ms = Date.now() - new Date(base).getTime();
    if (Number.isNaN(ms) || ms < 0) return null;
    return Math.floor(ms / 86_400_000);
}

/** «⚠ висит N дн» — shown when an assembly is stuck ≥ threshold in IN_PROGRESS/READY. */
export function StuckBadge({ days }: { days: number | null }) {
    if (days == null || days < STUCK_THRESHOLD_DAYS) return null;
    return (
        <span className={`badge ${days >= 5 ? 'badge-danger' : 'badge-warning'}`}>
            ⚠ висит {days}&nbsp;дн
        </span>
    );
}
