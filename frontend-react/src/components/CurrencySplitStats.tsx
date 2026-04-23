'use client';

import { formatNumber } from '@/lib/utils';
import type { CounterpartyStats } from '@/types/api';

interface CurrencySplitStatsProps {
    rub: CounterpartyStats;
    cny: CounterpartyStats;
    loading?: boolean;
}

function StatBlock({
    label,
    symbol,
    stats,
    color,
}: {
    label: string;
    symbol: string;
    stats: CounterpartyStats;
    color: string;
}) {
    return (
        <div className="glass-card" style={{ flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color, marginBottom: 12 }}>
                {symbol} {label}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>Приход</div>
                    <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--color-success)' }}>
                        {formatNumber(stats.in_sum)}
                    </div>
                </div>
                <div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>Расход</div>
                    <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--color-danger)' }}>
                        {formatNumber(stats.out_sum)}
                    </div>
                </div>
                <div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>Нетто</div>
                    <div
                        style={{
                            fontSize: 18, fontWeight: 700,
                            color: stats.net >= 0 ? 'var(--color-success)' : 'var(--color-danger)',
                        }}
                    >
                        {formatNumber(stats.net)}
                    </div>
                </div>
                <div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 4 }}>Транзакций</div>
                    <div style={{ fontSize: 18, fontWeight: 700 }}>
                        {formatNumber(stats.tx_count, 0)}
                    </div>
                </div>
            </div>
        </div>
    );
}

export default function CurrencySplitStats({ rub, cny, loading }: CurrencySplitStatsProps) {
    if (loading) {
        return (
            <div style={{ display: 'flex', gap: 16 }}>
                {[0, 1].map(i => (
                    <div key={i} className="glass-card" style={{ flex: 1, height: 120 }}>
                        <div className="skeleton" style={{ height: '100%', borderRadius: 8 }} />
                    </div>
                ))}
            </div>
        );
    }

    const showRub = rub.tx_count > 0;
    const showCny = cny.tx_count > 0;
    if (!showRub && !showCny) return null;

    return (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {showRub && <StatBlock label="Рубли" symbol="₽" stats={rub} color="var(--color-accent)" />}
            {showCny && <StatBlock label="Юани" symbol="¥" stats={cny} color="var(--color-warning)" />}
        </div>
    );
}
