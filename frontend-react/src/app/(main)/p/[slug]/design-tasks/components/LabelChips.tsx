'use client';

import { formatNumber } from '@/lib/utils';
import Tooltip from '@/components/Tooltip';
import { labelColorClass } from '@/lib/design';
import type { DesignLabelRef } from '@/types/api';

const MAX_DOTS = 5;  // спек: до пяти кругов, дальше «+N»

/**
 * Метки задачи. На карточке и в строке списка — ряд цветных кругов, которые
 * НЕ меняют высоту строки; названия раскрываются подсказкой. В деталке
 * (`expanded`) — полноценные чипы с названиями.
 */
export default function LabelChips({ labels, expanded = false }: {
    labels: DesignLabelRef[];
    expanded?: boolean;
}) {
    if (labels.length === 0) return null;

    if (expanded) {
        return (
            <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 6 }}>
                {labels.map((l) => (
                    <span key={l.id} className={`dds-label-chip ${labelColorClass(l.color)}`}>
                        <span className="dds-label-dot" />
                        {l.name}
                    </span>
                ))}
            </span>
        );
    }

    const shown = labels.slice(0, MAX_DOTS);
    const extra = labels.length - shown.length;
    return (
        <Tooltip text={labels.map((l) => l.name).join(', ')}>
            <span className="dds-label-dots">
                {shown.map((l) => (
                    <span key={l.id} className={labelColorClass(l.color)}>
                        <span className="dds-label-dot" />
                    </span>
                ))}
                {extra > 0 && (
                    <span style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>
                        +{formatNumber(extra, 0)}
                    </span>
                )}
            </span>
        </Tooltip>
    );
}
