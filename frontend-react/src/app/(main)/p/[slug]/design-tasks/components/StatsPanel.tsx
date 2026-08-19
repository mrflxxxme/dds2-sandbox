'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { format, subDays } from 'date-fns';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import InfoTip from '@/components/InfoTip';
import { DESIGN_METRIC_HINT } from '@/lib/designHints';
import type { DesignStatsOut } from '@/types/api';

const WINDOW_DAYS = 30;

function pct(v: number | null): string {
    return v == null ? '—' : `${formatNumber(v * 100, 0)}%`;
}

function num(v: number | null, digits = 1): string {
    return v == null ? '—' : formatNumber(v, digits);
}

/** Панель метрик PRD §10 ПОД списком: 6 значений GET /stats за последние 30 дней.
 *  Список задач первее метрик (правка заказчика); в волне D уедет во вкладку «Аналитика». */
export default function StatsPanel() {
    const [stats, setStats] = useState<DesignStatsOut | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const mountedRef = useRef(true);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            // format() по ЛОКАЛЬНОЙ дате: toISOString() вечером по МСК даёт минус сутки.
            const dateFrom = format(subDays(new Date(), WINDOW_DAYS), 'yyyy-MM-dd');
            const s = await api.getDesignStats(dateFrom);
            if (!mountedRef.current) return;
            setStats(s);
        } catch (e) {
            if (!mountedRef.current) return;
            setError(e instanceof Error ? e.message : 'Не удалось загрузить метрики');
        } finally {
            if (mountedRef.current) setLoading(false);
        }
    }, []);

    useEffect(() => {
        mountedRef.current = true;
        void load();
        return () => { mountedRef.current = false; };
    }, [load]);

    if (loading) {
        return (
            <div className="glass-card" style={{ marginTop: 16, color: 'var(--color-text-muted)', fontSize: 13 }}>
                Метрики: загрузка…
            </div>
        );
    }
    if (error) {
        return (
            <div className="glass-card" style={{ marginTop: 16, color: 'var(--color-danger)', fontSize: 13 }}>
                Метрики: {error}{' '}
                <button className="btn btn-sm btn-secondary" onClick={() => void load()}>Повторить</button>
            </div>
        );
    }
    if (!stats) return null;

    /**
     * «Данных мало» = в окне нет ни одной приёмки: бэк (services/design/stats.py)
     * отдаёт метрики приёмки как null ровно при accepted_count == 0 в том же
     * окне, что и цифры панели. Отдельный запрос «принятые за всё время» мерил
     * другое окно и врал на кейсе «приёмки были полгода назад».
     */
    const lowData = stats.avg_versions_to_accept == null && stats.median_cycle_days == null;

    const items: { key: string; label: string; value: string }[] = [
        { key: 'on_time_share', label: 'В срок', value: pct(stats.on_time_share) },
        { key: 'avg_versions_to_accept', label: 'Версий до приёмки', value: num(stats.avg_versions_to_accept) },
        { key: 'median_cycle_days', label: 'Медиана цикла, дн', value: num(stats.median_cycle_days) },
        { key: 'unassigned_over_2d', label: 'Без исполнителя >2 дн', value: formatNumber(stats.unassigned_over_2d, 0) },
        { key: 'outsourced_share', label: 'Аутсорс', value: pct(stats.outsourced_share) },
        { key: 'tracked_share', label: 'С товаром', value: pct(stats.tracked_share) },
    ];

    return (
        <div className="glass-card" style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>📈 Метрики за 30 дней</h3>
                {lowData && (
                    <span className="badge badge-warning" title={`За последние ${WINDOW_DAYS} дней нет принятых задач — метрики приёмки пока точка отсчёта, не тренд`}>
                        Данных мало
                    </span>
                )}
            </div>
            <div
                style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                    gap: 12,
                    opacity: lowData ? 0.6 : 1,
                }}
            >
                {items.map(({ key, label, value }) => (
                    <div key={key}>
                        <div style={{ fontSize: 20, fontWeight: 700 }}>{value}</div>
                        <InfoTip text={DESIGN_METRIC_HINT[key]} icon>
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{label}</span>
                        </InfoTip>
                    </div>
                ))}
            </div>
        </div>
    );
}
