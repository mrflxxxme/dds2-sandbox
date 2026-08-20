'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import { format, subDays } from 'date-fns';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import PageHeader from '@/components/PageHeader';
import PeriodPicker from '@/components/PeriodPicker';
import InfoTip from '@/components/InfoTip';
import Toast from '@/components/Toast';
import { DESIGN_STATUS_LABEL, labelColorClass } from '@/lib/design';
import { DESIGN_METRIC_HINT, DESIGN_UI_HINT } from '@/lib/designHints';
import type {
    DesignDashboardWidget,
    DesignDashboardWidgetId,
    DesignStatsByAssigneeOut,
    DesignStatsByAttributeOut,
    DesignStatsFunnelOut,
    DesignStatsOut,
} from '@/types/api';
import DesignTabs from '../components/DesignTabs';
import { useDesignBoardPermissions } from '../components/useDesignBoardPermissions';

const WINDOW_DAYS = 30;

/** Литерал на каждый рендер ломал бы useMemo внутри пикера. */
const ANALYTICS_PRESETS = ['today', 'yesterday', '30d', '3m'];

const WIDGET_TITLE: Record<DesignDashboardWidgetId, string> = {
    metrics: '📈 Метрики',
    by_assignee: '👤 По исполнителям',
    funnel: '📊 Воронка',
    by_attribute: '🏷️ По реквизитам и меткам',
};

function pct(v: number | null): string {
    return v == null ? '—' : `${formatNumber(v * 100, 0)}%`;
}
function num(v: number | null, digits = 1): string {
    return v == null ? '—' : formatNumber(v, digits);
}

/** Дефолтное окно: последние 30 дней включительно, как показывала панель метрик. */
function defaultWindow(): { from: string; to: string } {
    // format() по ЛОКАЛЬНОЙ дате: toISOString() вечером по МСК даёт минус сутки.
    const today = new Date();
    return { from: format(subDays(today, WINDOW_DAYS - 1), 'yyyy-MM-dd'), to: format(today, 'yyyy-MM-dd') };
}

/**
 * Вкладка «Аналитика» (Р23, Р32): модульный дашборд из четырёх виджетов.
 *
 * Каждый виджет грузится СВОИМ запросом и сам обрабатывает loading/error/empty —
 * падение одного не роняет страницу. Выключенный виджет данных не запрашивает.
 */
export default function DesignAnalyticsPage() {
    const params = useParams<{ slug: string }>();
    const boardPerms = useDesignBoardPermissions();

    const [window_, setWindow] = useState(defaultWindow);
    const [layout, setLayout] = useState<DesignDashboardWidget[] | null>(null);
    const [layoutError, setLayoutError] = useState<string | null>(null);
    const [tuning, setTuning] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const mountedRef = useRef(true);

    const loadLayout = useCallback(() => {
        setLayoutError(null);
        api.getDesignDashboardLayout()
            .then((r) => { if (mountedRef.current) setLayout(r.widgets); })
            .catch((e) => {
                // Ошибка обязана отличаться от загрузки: иначе пользователь
                // навсегда остаётся на «Загрузка…» без текста и без «Повторить».
                if (mountedRef.current) {
                    setLayoutError(e instanceof Error ? e.message : 'Не удалось загрузить раскладку');
                }
            });
    }, []);

    useEffect(() => {
        mountedRef.current = true;
        loadLayout();
        return () => { mountedRef.current = false; };
    }, [loadLayout]);

    const visible = useMemo(
        () => (layout ?? []).filter((w) => w.visible).sort((a, b) => a.order - b.order),
        [layout],
    );

    /** Сохранение раскладки оптимистично: на ошибке откат + текст бэка. */
    const persist = useCallback(async (next: DesignDashboardWidget[]) => {
        const snapshot = layout;
        setLayout(next);
        try {
            const saved = await api.saveDesignDashboardLayout(next);
            if (mountedRef.current) setLayout(saved.widgets);
        } catch (e) {
            if (!mountedRef.current) return;
            setLayout(snapshot);
            setToast({ type: 'error', message: e instanceof Error ? e.message : 'Не удалось сохранить раскладку' });
        }
    }, [layout]);

    const toggle = (id: DesignDashboardWidgetId) => {
        if (!layout) return;
        void persist(layout.map((w) => (w.id === id ? { ...w, visible: !w.visible } : w)));
    };

    const move = (id: DesignDashboardWidgetId, delta: number) => {
        if (!layout) return;
        const sorted = [...layout].sort((a, b) => a.order - b.order);
        const i = sorted.findIndex((w) => w.id === id);
        const j = i + delta;
        if (i < 0 || j < 0 || j >= sorted.length) return;
        [sorted[i], sorted[j]] = [sorted[j], sorted[i]];
        void persist(sorted.map((w, idx) => ({ ...w, order: idx })));
    };

    const download = useCallback(async () => {
        setExporting(true);
        try {
            await api.downloadDesignStatsExcel(window_.from, window_.to);
        } catch (e) {
            setToast({ type: 'error', message: e instanceof Error ? e.message : 'Не удалось скачать отчёт' });
        } finally {
            if (mountedRef.current) setExporting(false);
        }
    }, [window_.from, window_.to]);

    return (
        <PageGuard page="design-tasks">
            <PageHeader
                title="🖌️ Дизайн карточек — аналитика"
                subtitle="Метрики и разрезы за выбранный период"
                actions={<DesignTabs slug={params.slug} active="analytics" canManageRefs={boardPerms.can_manage_refs} />}
            />

            <div className="glass-card" style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', padding: 12 }}>
                <PeriodPicker
                    from={window_.from}
                    to={window_.to}
                    clearable={false}
                    presetKeys={ANALYTICS_PRESETS}
                    onApply={(from, to) => { if (from && to) setWindow({ from, to }); }}
                    minWidth={230}
                />
                <InfoTip text={DESIGN_UI_HINT.analyticsWindow} icon />
                <button className="btn btn-secondary btn-sm" onClick={() => setWindow(defaultWindow())}>
                    Последние 30 дней
                </button>
                <button
                    className="btn btn-primary btn-sm"
                    style={{ marginLeft: 'auto' }}
                    disabled={exporting}
                    onClick={() => void download()}
                >
                    {exporting ? '⏳ Готовим…' : '⬇ Скачать отчёт'}
                </button>
                <button className="btn btn-secondary btn-sm" onClick={() => setTuning((v) => !v)}>
                    ⚙ Настроить виджеты
                </button>
            </div>

            {tuning && layout && (
                <div className="glass-card" style={{ marginBottom: 12, padding: 12 }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                        Раскладка персональная: у каждого своя, на других не влияет.
                    </div>
                    {[...layout].sort((a, b) => a.order - b.order).map((w, i, all) => (
                        <div key={w.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' }}>
                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', minWidth: 260 }}>
                                <input type="checkbox" checked={w.visible} onChange={() => toggle(w.id)} />
                                {WIDGET_TITLE[w.id]}
                            </label>
                            <button className="btn btn-sm btn-secondary" disabled={i === 0} onClick={() => move(w.id, -1)} aria-label="Выше">↑</button>
                            <button className="btn btn-sm btn-secondary" disabled={i === all.length - 1} onClick={() => move(w.id, 1)} aria-label="Ниже">↓</button>
                        </div>
                    ))}
                </div>
            )}

            {layoutError && (
                <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                    {layoutError} <button className="btn btn-sm btn-secondary" onClick={loadLayout}>Повторить</button>
                </div>
            )}
            {layout === null && !layoutError && (
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка…</div>
            )}
            {layout !== null && visible.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 48 }}>
                    Все виджеты скрыты. Включите нужные в «Настроить виджеты».
                </div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {visible.map((w) => (
                    <Widget key={w.id} id={w.id} from={window_.from} to={window_.to} />
                ))}
            </div>

            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
        </PageGuard>
    );
}

/** Один виджет: свой запрос, свои четыре состояния. Ошибка не выходит за карточку. */
function Widget({ id, from, to }: { id: DesignDashboardWidgetId; from: string; to: string }) {
    const [data, setData] = useState<WidgetData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    /** Поколение запроса: смена периода не должна дать медленному ответу
     *  прошлого окна перезаписать свежий (mountedRef для этого не годится —
     *  тело эффекта возвращает его в true сразу после cleanup). */
    const genRef = useRef(0);

    const load = useCallback(async () => {
        const gen = ++genRef.current;
        setLoading(true);
        setError(null);
        try {
            const res = await fetchWidget(id, from, to);
            if (gen === genRef.current) setData(res);
        } catch (e) {
            if (gen === genRef.current) {
                setError(e instanceof Error ? e.message : 'Не удалось загрузить виджет');
            }
        } finally {
            if (gen === genRef.current) setLoading(false);
        }
    }, [id, from, to]);

    useEffect(() => {
        void load();
        // Отменить сетевой ответ нечем, но поколение обесценит его результат.
        return () => { genRef.current++; };
    }, [load]);

    return (
        <div className="glass-card">
            <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>{WIDGET_TITLE[id]}</h3>
            {loading && <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>Загрузка…</div>}
            {error && !loading && (
                <div style={{ color: 'var(--color-danger)', fontSize: 13 }}>
                    {error} <button className="btn btn-sm btn-secondary" onClick={() => void load()}>Повторить</button>
                </div>
            )}
            {!loading && !error && data && <WidgetBody data={data} />}
        </div>
    );
}

/**
 * Дискриминированное объединение вместо `unknown` + каст: перепутать виджет и
 * его данные теперь нельзя — tsc не соберёт.
 */
type WidgetData =
    | { id: 'metrics'; stats: DesignStatsOut }
    | { id: 'by_assignee'; stats: DesignStatsByAssigneeOut }
    | { id: 'funnel'; stats: DesignStatsFunnelOut }
    | { id: 'by_attribute'; stats: DesignStatsByAttributeOut };

async function fetchWidget(id: DesignDashboardWidgetId, from: string, to: string): Promise<WidgetData> {
    switch (id) {
        case 'metrics':
            return { id, stats: await api.getDesignStats(from, to) };
        case 'by_assignee':
            return { id, stats: await api.getDesignStatsByAssignee(from, to) };
        case 'funnel':
            return { id, stats: await api.getDesignStatsFunnel(from, to) };
        case 'by_attribute':
            return { id, stats: await api.getDesignStatsByAttribute(from, to) };
    }
}

function WidgetBody({ data }: { data: WidgetData }) {
    switch (data.id) {
        case 'metrics':
            return <MetricsBody stats={data.stats} />;
        case 'by_assignee':
            return <AssigneeBody stats={data.stats} />;
        case 'funnel':
            return <FunnelBody stats={data.stats} />;
        case 'by_attribute':
            return <AttributeBody stats={data.stats} />;
    }
}

function MetricsBody({ stats }: { stats: DesignStatsOut }) {
    /** «Данных мало» — ровно как в v1: приёмки в окне не было, значения приглушаем. */
    const lowData = stats.avg_versions_to_accept == null && stats.median_cycle_days == null;
    const items: { key: keyof typeof DESIGN_METRIC_HINT; label: string; value: string }[] = [
        { key: 'on_time_share', label: 'В срок', value: pct(stats.on_time_share) },
        { key: 'avg_versions_to_accept', label: 'Версий до приёмки', value: num(stats.avg_versions_to_accept) },
        { key: 'median_cycle_days', label: 'Медиана цикла, дн', value: num(stats.median_cycle_days) },
        { key: 'unassigned_over_2d', label: 'Без исполнителя >2 дн', value: formatNumber(stats.unassigned_over_2d, 0) },
        { key: 'outsourced_share', label: 'Аутсорс', value: pct(stats.outsourced_share) },
        { key: 'tracked_share', label: 'С товаром', value: pct(stats.tracked_share) },
    ];
    return (
        <>
            {lowData && (
                <div style={{ marginBottom: 8 }}>
                    <span className="badge badge-warning">Данных мало</span>
                </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, opacity: lowData ? 0.6 : 1 }}>
                {items.map(({ key, label, value }) => (
                    <div key={key}>
                        <div style={{ fontSize: 20, fontWeight: 700 }}>{value}</div>
                        <InfoTip text={DESIGN_METRIC_HINT[key]} icon>
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{label}</span>
                        </InfoTip>
                    </div>
                ))}
            </div>
        </>
    );
}

function AssigneeBody({ stats }: { stats: DesignStatsByAssigneeOut }) {
    if (stats.rows.length === 0) {
        return <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>За период нет данных по исполнителям.</div>;
    }
    return (
        <>
            {stats.truncated && (
                <div style={{ color: 'var(--color-warning)', fontSize: 12, marginBottom: 8 }}>
                    Показаны не все исполнители — сузьте период.
                </div>
            )}
            <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                    <thead>
                        <tr style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-text-muted)', fontSize: 12 }}>
                            <th style={{ textAlign: 'left', padding: '8px 12px' }}>Исполнитель</th>
                            <th style={{ textAlign: 'right', padding: '8px 12px' }}>Активных</th>
                            <th style={{ textAlign: 'right', padding: '8px 12px' }}>Принято</th>
                            <th style={{ textAlign: 'right', padding: '8px 12px' }}>В срок</th>
                            <th style={{ textAlign: 'right', padding: '8px 12px' }}>Цикл, дн</th>
                            <th style={{ textAlign: 'right', padding: '8px 12px' }}>Версий</th>
                        </tr>
                    </thead>
                    <tbody>
                        {stats.rows.map((r) => (
                            <tr key={r.user_id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                <td style={{ padding: '8px 12px' }}>{r.name}</td>
                                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatNumber(r.active, 0)}</td>
                                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatNumber(r.accepted, 0)}</td>
                                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{pct(r.on_time_share)}</td>
                                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{num(r.avg_cycle_days)}</td>
                                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{num(r.avg_versions)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    );
}

function FunnelBody({ stats }: { stats: DesignStatsFunnelOut }) {
    if (stats.rows.every((r) => r.count === 0)) {
        return <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>На доске сейчас задач нет.</div>;
    }
    return (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 12 }}>
            {stats.rows.map((r) => (
                <div key={r.status} style={{ border: '1px solid var(--color-border)', borderRadius: 12, padding: 10 }}>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{DESIGN_STATUS_LABEL[r.status]}</div>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>{formatNumber(r.count, 0)}</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                        {r.avg_days_in_status == null ? 'нет данных' : `≈ ${num(r.avg_days_in_status)} дн`}
                    </div>
                </div>
            ))}
        </div>
    );
}

function AttributeBody({ stats }: { stats: DesignStatsByAttributeOut }) {
    if (stats.attributes.length === 0 && stats.labels.length === 0) {
        return (
            <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                Реквизитов и меток пока нет — заведите их во вкладке «Настройки».
            </div>
        );
    }
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {stats.attributes.map((g) => (
                <div key={g.attribute_id}>
                    <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>{g.attribute_name}</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                        {g.rows.map((r) => (
                            <span key={r.value_id ?? 'none'} className="badge badge-secondary">
                                {r.value}: {formatNumber(r.count, 0)}
                            </span>
                        ))}
                    </div>
                </div>
            ))}
            {stats.labels.length > 0 && (
                <div>
                    <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Метки</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {stats.labels.map((l) => (
                            <span key={l.label_id} className={`dds-label-chip ${labelColorClass(l.color)}`}>
                                <span className="dds-label-dot" />
                                {l.name}: {formatNumber(l.count, 0)}
                            </span>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
