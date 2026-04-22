'use client';

import type { CounterpartyType } from '@/types/api';

interface CounterpartyTypeBadgeProps {
    type: CounterpartyType;
    size?: 'sm' | 'md';
}

const TYPE_CONFIG: Record<CounterpartyType, { label: string; icon: string; badgeClass: string }> = {
    SUPPLIER: { label: 'Поставщик', icon: '🏭', badgeClass: 'badge-warning' },
    FULFILLMENT: { label: 'Фулфилмент', icon: '📦', badgeClass: 'badge-info' },
    CARRIER: { label: 'Перевозчик', icon: '🚚', badgeClass: 'badge-secondary' },
    CUSTOMS_BROKER: { label: 'Таможенный брокер', icon: '🛃', badgeClass: 'badge-secondary' },
    DESIGNER: { label: 'Дизайнер', icon: '🎨', badgeClass: 'badge-secondary' },
    LEGAL: { label: 'Юридические услуги', icon: '⚖️', badgeClass: 'badge-secondary' },
    LANDLORD: { label: 'Аренда', icon: '🏠', badgeClass: 'badge-secondary' },
    IT_SERVICE: { label: 'IT-сервис', icon: '💻', badgeClass: 'badge-info' },
    MARKETPLACE: { label: 'Маркетплейс', icon: '🛒', badgeClass: 'badge-success' },
    BANK: { label: 'Банк', icon: '🏦', badgeClass: 'badge-info' },
    GOVERNMENT: { label: 'Государство / ФНС', icon: '🏛️', badgeClass: 'badge-danger' },
    AFFILIATED: { label: 'Аффилированное лицо', icon: '🔗', badgeClass: 'badge-warning' },
    OTHER: { label: 'Прочее', icon: '•', badgeClass: 'badge-secondary' },
};

export default function CounterpartyTypeBadge({ type, size = 'md' }: CounterpartyTypeBadgeProps) {
    const config = TYPE_CONFIG[type] ?? TYPE_CONFIG.OTHER;
    const fontSize = size === 'sm' ? 11 : 12;

    return (
        <span
            className={`badge ${config.badgeClass}`}
            style={{ fontSize, gap: 4, display: 'inline-flex', alignItems: 'center' }}
            title={config.label}
        >
            <span>{config.icon}</span>
            <span>{config.label}</span>
        </span>
    );
}

export function getCounterpartyTypeLabel(type: CounterpartyType): string {
    return TYPE_CONFIG[type]?.label ?? type;
}

export { TYPE_CONFIG };
