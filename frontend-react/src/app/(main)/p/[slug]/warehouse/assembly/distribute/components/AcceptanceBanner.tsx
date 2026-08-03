'use client';
import React from 'react';
import { formatNumber, formatDateTime } from '@/lib/utils';

export interface AcceptanceSummary {
    /** Приёмка проверена (был ответ WB). */
    checked: boolean;
    /** Проверка не удалась (WB недоступна) — разложено без проверки складов. */
    failed: boolean;
    /** Сколько SKU проверено. */
    skuCount: number;
    /** SKU с моно-паллетой (📐). */
    monoCount: number;
    /** SKU, разнесённых на >1 тип упаковки (📦+📐 split). */
    splitCount: number;
    /** ↪ перемещено с закрытых складов на открытые. */
    movedQty: number;
    /** ⛔ осталось на закрытых складах (не поедет). */
    droppedQty: number;
    /** Когда проверено (ISO). */
    checkedAt: string | null;
}

/** Чип баннера. */
function Chip({ children, color }: { children: React.ReactNode; color?: string }) {
    return (
        <span
            className="badge badge-secondary"
            style={{ fontSize: 12, padding: '2px 10px', color: color ?? 'var(--color-text)', whiteSpace: 'nowrap' }}
        >
            {children}
        </span>
    );
}

/**
 * Богатый баннер результата проверки приёмки WB — общий для «Черновика сборки» и экрана
 * «Распределить машину». Показывает: проверено ли, число SKU, моно/split, перемещённое и
 * потерянное на закрытых складах количество. При недоступной приёмке — предупреждение.
 */
export default function AcceptanceBanner({ summary }: { summary: AcceptanceSummary | null }) {
    if (!summary || (!summary.checked && !summary.failed)) return null;

    if (summary.failed) {
        return (
            <div
                className="glass-card"
                style={{
                    padding: '10px 14px', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 10,
                    fontSize: 13, color: 'var(--color-warning)',
                    background: 'color-mix(in srgb, var(--color-warning) 8%, transparent)',
                    border: '1px solid color-mix(in srgb, var(--color-warning) 30%, transparent)',
                }}
            >
                <span style={{ fontSize: 16 }}>⚠️</span>
                <span>WB-приёмка недоступна — раскладка сделана без проверки лимитов складов.</span>
            </div>
        );
    }

    return (
        <div
            className="glass-card"
            style={{
                padding: '10px 14px', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                fontSize: 13,
                background: 'color-mix(in srgb, var(--color-success) 7%, transparent)',
                border: '1px solid color-mix(in srgb, var(--color-success) 28%, transparent)',
            }}
        >
            <span style={{ fontWeight: 600, color: 'var(--color-success)' }}>✅ WB-приёмка проверена</span>
            <Chip>{formatNumber(summary.skuCount, 0)} SKU</Chip>
            {summary.monoCount > 0 && <Chip>📐 моно: {formatNumber(summary.monoCount, 0)}</Chip>}
            {summary.splitCount > 0 && <Chip>📦+📐 split: {formatNumber(summary.splitCount, 0)}</Chip>}
            {summary.movedQty > 0 && (
                <Chip color="var(--color-accent)">↪ {formatNumber(summary.movedQty, 0)} перемещено с закрытых</Chip>
            )}
            {summary.droppedQty > 0 && (
                <Chip color="var(--color-danger)">⛔ {formatNumber(summary.droppedQty, 0)} на закрытых</Chip>
            )}
            {summary.checkedAt && (
                <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--color-text-muted)' }}>
                    {formatDateTime(summary.checkedAt)}
                </span>
            )}
        </div>
    );
}
