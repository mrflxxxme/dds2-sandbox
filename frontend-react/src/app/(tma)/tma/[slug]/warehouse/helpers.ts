import { formatNumber } from '@/lib/utils';
import type { AssemblyRequest, TrafficLight } from './types';

export const TRAFFIC_ICONS: Record<TrafficLight, string> = {
    red: '🔴', orange: '🟠', yellow: '🟡', green: '🟢',
};

export const TRAFFIC_LABELS: Record<TrafficLight, string> = {
    red: 'Критично', orange: 'Мало', yellow: 'Норма', green: 'Достаточно',
};

export const STATUS_LABELS: Record<string, string> = {
    PENDING: 'Новая',
    IN_PROGRESS: 'В работе',
    READY: 'Готова',
    VEHICLE_ASSIGNED: 'Машина назначена',
    SHIPPED: 'Отправлена',
    DELIVERED: 'Доставлена',
    CANCELLED: 'Отменена',
};

export const STATUS_COLORS: Record<string, string> = {
    PENDING: '#ff9500',
    IN_PROGRESS: '#007aff',
    READY: '#34c759',
    VEHICLE_ASSIGNED: '#5856d6',
    SHIPPED: '#007aff',
    DELIVERED: '#34c759',
    CANCELLED: '#8e8e93',
};

export const NEXT_STATUS: Record<string, { action: string; label: string; endpoint: string }> = {
    PENDING: { action: 'start', label: 'Начать сборку', endpoint: 'start' },
    IN_PROGRESS: { action: 'ready', label: 'Сборка готова', endpoint: 'ready' },
    READY: { action: 'assign', label: 'Назначить машину', endpoint: 'assign-vehicle' },
    VEHICLE_ASSIGNED: { action: 'ship', label: 'Отправить', endpoint: 'ship' },
};

export function compactNumber(n: number): string {
    const abs = Math.abs(n);
    if (abs >= 1_000_000) return (n / 1_000_000).toFixed(1).replace('.0', '') + 'M';
    if (abs >= 1_000) return (n / 1_000).toFixed(1).replace('.0', '') + 'K';
    return formatNumber(n, 0);
}

export function formatShortDate(iso: string | null): string {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}

export function formatSyncTime(iso: string | null): string {
    if (!iso) return 'нет данных';
    const d = new Date(iso);
    const now = new Date();
    const diffMin = Math.floor((now.getTime() - d.getTime()) / 60_000);
    if (diffMin < 1) return 'только что';
    if (diffMin < 60) return `${diffMin} мин назад`;
    const diffH = Math.floor(diffMin / 60);
    if (diffH < 24) return `${diffH} ч назад`;
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}

export function totalItems(req: AssemblyRequest): number {
    return req.items.reduce((s, i) => s + i.quantity, 0);
}

export function todayISO(): string {
    return new Date().toISOString().slice(0, 10);
}
