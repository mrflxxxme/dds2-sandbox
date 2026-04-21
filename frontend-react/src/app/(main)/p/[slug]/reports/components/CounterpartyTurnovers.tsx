'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import type {
    CounterpartyTurnoversResponse,
    CounterpartyType,
    CounterpartyTurnoverRow,
} from '@/types/api';
import { getCounterpartyTypeLabel } from '@/components/CounterpartyTypeBadge';

const TODAY = new Date().toISOString().slice(0, 10);
const MONTHS_AGO_3 = new Date(Date.now() - 90 * 24 * 3600 * 1000).toISOString().slice(0, 10);

const TYPE_OPTIONS: { value: CounterpartyType | ''; label: string }[] = [
    { value: '', label: 'Все типы' },
    { value: 'SUPPLIER', label: 'Поставщики' },
    { value: 'FULFILLMENT', label: 'Фулфилмент' },
    { value: 'CARRIER', label: 'Перевозчики' },
    { value: 'CUSTOMS_BROKER', label: 'Таможенные брокеры' },
    { value: 'DESIGNER', label: 'Дизайнеры' },
    { value: 'LEGAL', label: 'Юридические' },
    { value: 'LANDLORD', label: 'Аренда' },
    { value: 'IT_SERVICE', label: 'IT-сервисы' },
    { value: 'MARKETPLACE', label: 'Маркетплейсы' },
    { value: 'BANK', label: 'Банки' },
    { value: 'GOVERNMENT', label: 'Государство' },
    { value: 'AFFILIATED', label: 'Аффилированные' },
    { value: 'OTHER', label: 'Прочее' },
];

export function CounterpartyTurnovers() {
    const [dateFrom, setDateFrom] = useState(MONTHS_AGO_3);
    const [dateTo, setDateTo] = useState(TODAY);
    const [type, setType] = useState<CounterpartyType | ''>('');
    const [currency, setCurrency] = useState<'RUB' | 'CNY'>('RUB');

    const [data, setData] = useState<CounterpartyTurnoversResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const r = await api.getCounterpartyTurnovers({
                date_from: dateFrom,
                date_to: dateTo,
                type: type || undefined,
                currency,
            });
            setData(r);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [dateFrom, dateTo, type, currency]);

    useEffect(() => { load(); }, [load]);

    // Build an ordered list of months present in any row to be column headers.
    const months = useMemo(() => {
        if (!data) return [];
        const set = new Set<string>();
        for (const row of data.rows) {
            for (const m of row.months) set.add(m.month);
        }
        return Array.from(set).sort();
    }, [data]);

    const handleExport = () => {
        if (!data) return;
        const rows = data.rows.map((r: CounterpartyTurnoverRow) => {
            const byMonth: Record<string, number> = {};
            for (const m of r.months) byMonth[m.month] = m.net;
            const base: Record<string, string | number> = {
                'ИНН': r.inn ?? '',
                'Контрагент': r.name,
                'Тип': getCounterpartyTypeLabel(r.primary_type),
                'Транзакций': r.tx_count,
                'Приход': r.total.in,
                'Расход': r.total.out,
                'Нетто': r.total.net,
            };
            for (const m of months) {
                base[m] = byMonth[m] ?? 0;
            }
            return base;
        });
        exportToExcel(rows as unknown as Record<string, any>[], `counterparty_turnovers_${currency}_${dateFrom}_${dateTo}`);
    };

    if (loading && !data) {
        return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;
    }

    return (
        <div>
            <div className="glass-card" style={{ marginBottom: 16, padding: '12px 16px' }}>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                    <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
                        С
                        <input
                            type="date"
                            className="form-input"
                            style={{ width: 150 }}
                            value={dateFrom}
                            onChange={e => setDateFrom(e.target.value)}
                        />
                    </label>
                    <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
                        По
                        <input
                            type="date"
                            className="form-input"
                            style={{ width: 150 }}
                            value={dateTo}
                            onChange={e => setDateTo(e.target.value)}
                        />
                    </label>
                    <select
                        className="form-input"
                        style={{ width: 200 }}
                        value={type}
                        onChange={e => setType(e.target.value as CounterpartyType | '')}
                    >
                        {TYPE_OPTIONS.map(o => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                    </select>
                    <div style={{ display: 'inline-flex', gap: 4 }}>
                        {(['RUB', 'CNY'] as const).map(c => (
                            <button
                                key={c}
                                className={`btn ${currency === c ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                                onClick={() => setCurrency(c)}
                            >
                                {c === 'RUB' ? '₽ RUB' : '¥ CNY'}
                            </button>
                        ))}
                    </div>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={handleExport}
                        disabled={!data || data.rows.length === 0}
                        style={{ marginLeft: 'auto' }}
                    >
                        Excel
                    </button>
                </div>
            </div>

            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between' }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
                </div>
            )}

            {!loading && data && data.rows.length === 0 && (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>📊</div>
                    <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>
                        Нет оборотов за выбранный период
                    </div>
                    <div style={{ color: 'var(--color-text-dim)' }}>
                        Попробуйте изменить период или тип контрагента.
                    </div>
                </div>
            )}

            {data && data.rows.length > 0 && (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            <tr style={{ borderBottom: '2px solid var(--color-border)' }}>
                                <th style={{ padding: '10px 8px', textAlign: 'left', position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 1 }}>Контрагент</th>
                                <th style={{ padding: '10px 8px', textAlign: 'left' }}>ИНН</th>
                                <th style={{ padding: '10px 8px', textAlign: 'right' }}>Приход</th>
                                <th style={{ padding: '10px 8px', textAlign: 'right' }}>Расход</th>
                                <th style={{ padding: '10px 8px', textAlign: 'right' }}>Нетто</th>
                                {months.map(m => (
                                    <th key={m} style={{ padding: '10px 8px', textAlign: 'right', minWidth: 100, fontFamily: 'monospace', fontSize: 12 }}>
                                        {m}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {data.rows.map((r: CounterpartyTurnoverRow) => {
                                const monthMap: Record<string, number> = {};
                                for (const m of r.months) monthMap[m.month] = m.net;
                                return (
                                    <tr key={r.counterparty_id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                        <td style={{ padding: '8px', position: 'sticky', left: 0, background: 'var(--color-bg-card)' }}>
                                            <div style={{ fontWeight: 500 }}>{r.name}</div>
                                            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                                                {getCounterpartyTypeLabel(r.primary_type)}
                                            </div>
                                        </td>
                                        <td style={{ padding: '8px', fontFamily: 'monospace', fontSize: 12 }}>{r.inn ?? '—'}</td>
                                        <td style={{ padding: '8px', textAlign: 'right', color: 'var(--color-success)' }}>
                                            {formatNumber(r.total.in)}
                                        </td>
                                        <td style={{ padding: '8px', textAlign: 'right', color: 'var(--color-danger)' }}>
                                            {formatNumber(r.total.out)}
                                        </td>
                                        <td
                                            style={{
                                                padding: '8px',
                                                textAlign: 'right',
                                                fontWeight: 600,
                                                color: r.total.net >= 0 ? 'var(--color-success)' : 'var(--color-danger)',
                                            }}
                                        >
                                            {formatNumber(r.total.net)}
                                        </td>
                                        {months.map(m => (
                                            <td
                                                key={m}
                                                style={{
                                                    padding: '8px',
                                                    textAlign: 'right',
                                                    fontFamily: 'monospace',
                                                    fontSize: 12,
                                                    color: (monthMap[m] ?? 0) > 0
                                                        ? 'var(--color-success)'
                                                        : (monthMap[m] ?? 0) < 0
                                                            ? 'var(--color-danger)'
                                                            : 'var(--color-text-dim)',
                                                }}
                                            >
                                                {monthMap[m] != null ? formatNumber(monthMap[m]) : '—'}
                                            </td>
                                        ))}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
