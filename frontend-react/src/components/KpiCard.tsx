'use client';

import React from 'react';
import dynamic from 'next/dynamic';
import { formatNumber } from '@/lib/utils';

/* Tremor SparkAreaChart — dynamic import to avoid SSR issues */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const SparkAreaChart = dynamic<any>(
    () => import('@tremor/react').then((mod) => mod.SparkAreaChart),
    { ssr: false },
);

/** Map DDS hex colors to Tremor color names */
function colorToTremor(hex: string): string {
    const map: Record<string, string> = {
        '#22c55e': 'emerald',
        '#ef4444': 'red',
        '#a78bfa': 'violet',
        '#f59e0b': 'amber',
        '#3b82f6': 'blue',
        '#0071e3': 'blue',
    };
    return map[hex] ?? 'blue';
}

export interface KpiCardProps {
    /** Metric name */
    label: string;
    /** Formatted value */
    value: string | number;
    /** Percentage change, e.g. +5.2 or -3.1 */
    delta?: number;
    /** Direction of change */
    deltaType?: 'increase' | 'decrease' | 'unchanged';
    /** Array of values for sparkline */
    sparkData?: number[];
    /** Optional icon (emoji or ReactNode) */
    icon?: React.ReactNode;
    /** Accent color for the card left border and sparkline */
    color?: string;
    /** Sub-label text */
    sub?: string;
}

/**
 * KpiCard — stat card with optional sparkline (Tremor SparkAreaChart).
 *
 * Uses `.stat-card` from the DDS design system.
 * Sparkline rendered only when `sparkData` is provided and non-empty.
 */
export default function KpiCard({
    label,
    value,
    delta,
    deltaType,
    sparkData,
    icon,
    color = 'var(--color-accent)',
    sub,
}: KpiCardProps) {
    /* Determine delta direction */
    const resolvedDeltaType =
        deltaType ?? (delta != null ? (delta > 0 ? 'increase' : delta < 0 ? 'decrease' : 'unchanged') : undefined);

    const deltaColor =
        resolvedDeltaType === 'increase'
            ? '#22c55e'
            : resolvedDeltaType === 'decrease'
              ? '#ef4444'
              : 'var(--color-text-muted)';

    const deltaArrow =
        resolvedDeltaType === 'increase' ? '\u2191' : resolvedDeltaType === 'decrease' ? '\u2193' : '';

    /* Format value */
    const displayValue = typeof value === 'number' ? formatNumber(value) : value;

    /* Spark data for Tremor chart */
    const chartData = sparkData?.map((v, i) => ({ index: i, value: v })) ?? [];

    return (
        <div className="stat-card" style={{ borderLeft: `3px solid ${color}` }}>
            {/* Label row */}
            <div className="stat-card-label" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {icon && <span style={{ fontSize: 16, lineHeight: 1 }}>{icon}</span>}
                {label}
            </div>

            {/* Value + delta row */}
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <div className="stat-card-value" style={{ color, fontSize: 22 }}>{displayValue}</div>
                {delta != null && (
                    <span style={{ fontSize: 13, fontWeight: 600, color: deltaColor }}>
                        {deltaArrow} {delta > 0 ? '+' : ''}{formatNumber(delta, 1)}%
                    </span>
                )}
            </div>

            {/* Sub-label */}
            {sub && <div className="stat-card-sub" style={{ fontSize: 11, opacity: 0.6 }}>{sub}</div>}

            {/* Sparkline */}
            {chartData.length > 1 && (
                <div style={{ marginTop: 8, height: 32 }}>
                    <SparkAreaChart
                        data={chartData}
                        categories={['value']}
                        index="index"
                        colors={[colorToTremor(color)]}
                        className="tremor-spark-dds"
                    />
                </div>
            )}
        </div>
    );
}
