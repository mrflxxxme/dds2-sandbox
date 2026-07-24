/** Shared formatting helpers for the Займы section. */
import { formatNumber, formatDate } from '@/lib/utils';
import type { LoanStatus, LoanEntityType } from '@/types/api';

// Backend Numeric fields arrive as strings — coerce before formatting.
export const money = (x: number | string | null | undefined, d = 0): string =>
    x == null || x === '' ? '—' : formatNumber(Number(x), d);

export const ratePct = (x: number | string | null | undefined): string =>
    x == null || x === '' ? '—' : `${(Number(x) * 100).toFixed(1)}%`;

export const fmtDate = formatDate;

export const STATUS_LABEL: Record<LoanStatus, string> = {
    ACTIVE: 'Активный',
    CLOSED: 'Закрыт',
    DEFAULTED: 'Дефолт',
};

export const STATUS_BADGE: Record<LoanStatus, string> = {
    ACTIVE: 'badge-success',
    CLOSED: 'badge-secondary',
    DEFAULTED: 'badge-danger',
};

export const ENTITY_LABEL: Record<LoanEntityType, string> = {
    PHYSICAL: 'Физлицо',
    IP: 'ИП',
};

export const entityLabel = (e: LoanEntityType | null | undefined): string =>
    e ? ENTITY_LABEL[e] : '—';

/** Recharts palette via CSS vars resolved to hex (charts can't read var() in some props). */
export const CHART_COLORS = ['#0071e3', '#34c759', '#ff9500', '#ff3b30', '#5e5ce6', '#af52de', '#ff2d55', '#64d2ff'];
