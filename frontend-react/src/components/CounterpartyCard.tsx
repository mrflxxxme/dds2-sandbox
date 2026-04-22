'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { formatNumber } from '@/lib/utils';
import CounterpartyTypeBadge from './CounterpartyTypeBadge';
import type { CounterpartyListItem } from '@/types/api';

interface CounterpartyCardProps {
    counterparty: CounterpartyListItem;
    stats_rub?: { in_sum: number; out_sum: number; tx_count: number };
    stats_cny?: { in_sum: number; out_sum: number; tx_count: number };
    onClick?: () => void;
}

export default function CounterpartyCard({ counterparty, stats_rub, stats_cny, onClick }: CounterpartyCardProps) {
    const params = useParams();
    const slug = params?.slug as string;

    const cardContent = (
        <div
            className="glass-card"
            style={{
                cursor: onClick ? 'pointer' : 'default',
                transition: 'box-shadow 0.2s',
                padding: '16px 20px',
            }}
            onClick={onClick}
        >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {counterparty.name}
                    </div>
                    {counterparty.inn && (
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)', fontFamily: 'monospace' }}>
                            ИНН: {counterparty.inn}
                        </div>
                    )}
                    {counterparty.contract_number && !counterparty.inn && (
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)', fontFamily: 'monospace' }}>
                            Контракт: {counterparty.contract_number}
                        </div>
                    )}
                </div>
                <CounterpartyTypeBadge type={counterparty.primary_type} size="sm" />
            </div>

            {(stats_rub || stats_cny) && (
                <div style={{ display: 'flex', gap: 16, marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--color-border)' }}>
                    {stats_rub && stats_rub.tx_count > 0 && (
                        <div>
                            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>₽ оборот</div>
                            <div style={{ fontSize: 13, fontWeight: 500 }}>
                                {formatNumber(stats_rub.in_sum + stats_rub.out_sum)}
                            </div>
                        </div>
                    )}
                    {stats_cny && stats_cny.tx_count > 0 && (
                        <div>
                            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>¥ оборот</div>
                            <div style={{ fontSize: 13, fontWeight: 500 }}>
                                {formatNumber(stats_cny.in_sum + stats_cny.out_sum)}
                            </div>
                        </div>
                    )}
                    {stats_rub && (
                        <div>
                            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>Транзакций</div>
                            <div style={{ fontSize: 13, fontWeight: 500 }}>{stats_rub.tx_count + (stats_cny?.tx_count ?? 0)}</div>
                        </div>
                    )}
                </div>
            )}

            {counterparty.created_by_import && (
                <div style={{ marginTop: 8 }}>
                    <span style={{ fontSize: 11, color: 'var(--color-text-dim)', background: 'var(--color-border)', padding: '2px 6px', borderRadius: 4 }}>
                        авто-импорт
                    </span>
                </div>
            )}
        </div>
    );

    if (slug && !onClick) {
        return (
            <Link href={`/p/${slug}/refs/counterparty/${counterparty.id}`} style={{ textDecoration: 'none', color: 'inherit' }}>
                {cardContent}
            </Link>
        );
    }

    return cardContent;
}
