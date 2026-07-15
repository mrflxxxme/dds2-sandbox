'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { buildUrgentShip, type UrgentShipRow, type UrgencyBucket } from '@/lib/assembly/urgentShip';
import type { AssemblyDraftRow, StockAnalyticsArticle, StockNeedResponse } from '@/types/api';

const VISIBLE_ROWS = 12;
const VISIBLE_CHIPS = 16;
const TREND_DAYS = 7;
const LOC_WINDOW_DAYS = 30;

interface Props {
    slug: string;
    rows: AssemblyDraftRow[];
    stockNeed: StockNeedResponse | null;
}

/** Бейдж «Запас, дн» — палитра светофора Аналитики остатков. */
function DaysBadge({ row }: { row: UrgentShipRow }) {
    const style: Record<UrgencyBucket, React.CSSProperties> = {
        zero: { background: 'var(--color-danger)', color: '#fff' },
        critical: { background: 'rgba(255,68,68,0.14)', color: 'var(--color-danger)' },
        danger: { background: 'rgba(245,158,11,0.16)', color: 'var(--color-warning)' },
    };
    return (
        <span
            title={row.bucket === 'zero' ? 'Остаток WB уже обнулился — теряем заказы прямо сейчас' : `Запаса хватит на ${formatNumber(row.daysLeft, 0)} дн (WB + сборки + путь)`}
            style={{
                display: 'inline-block', minWidth: 34, padding: '2px 8px', borderRadius: 8,
                fontWeight: 700, fontSize: 11, textAlign: 'center', ...style[row.bucket],
            }}
        >
            {row.bucket === 'zero' ? '0 · ноль!' : `${formatNumber(row.daysLeft, 0)} дн`}
        </span>
    );
}

/** % локализации: цвет от цели распределения (по умолчанию 75%). */
function LocPct({ pct, target }: { pct: number | null; target: number }) {
    if (pct === null) return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
    const color = pct >= target ? 'var(--color-success)' : pct >= 50 ? 'var(--color-warning)' : 'var(--color-danger)';
    return <span style={{ color, fontWeight: 600 }}>{formatNumber(pct, 0)}%</span>;
}

/** «Срочно к отправке»: SKU с запасом ≤14 дн (уже с учётом созданных сборок и
 *  пути), которые лежат в ЭТОМ черновике, но ещё не превращены в заявки, — и
 *  сколько реализации теряем за каждый день промедления. Заменил панель
 *  «Добавить из потребности»: срочные SKU вне черновика показаны компактной
 *  строкой-предупреждением (добавляются через «Заполнить» или вкладку «Потребность»). */
export default function UrgentShipPanel({ slug, rows, stockNeed }: Props) {
    const [articles, setArticles] = useState<StockAnalyticsArticle[] | null>(null);
    const [locMap, setLocMap] = useState<Map<number, number>>(new Map());
    const [state, setState] = useState<'loading' | 'error' | 'ready'>('loading');
    const [showAll, setShowAll] = useState(false);

    const load = useCallback(async () => {
        setState('loading');
        const today = new Date();
        const from = new Date(today.getTime() - LOC_WINDOW_DAYS * 86400_000);
        const iso = (d: Date) => d.toISOString().slice(0, 10);
        // Локализация — best-effort: без неё блок живёт, в колонке будет «—».
        const [analyticsRes, locRes] = await Promise.allSettled([
            api.getStockAnalytics(TREND_DAYS, undefined, undefined, undefined, 'wb_assembly_transit'),
            api.getLocalizationSkus(iso(from), iso(today)),
        ]);
        if (analyticsRes.status !== 'fulfilled') { setState('error'); return; }
        setArticles(analyticsRes.value.articles || []);
        if (locRes.status === 'fulfilled') {
            setLocMap(new Map(locRes.value.map(s => [s.nm_id, s.loc_pct])));
        }
        setState('ready');
    }, []);

    useEffect(() => { load(); }, [load]);

    const draftQtyByNm = useMemo(() => {
        const m = new Map<number, number>();
        for (const r of rows) {
            const qty = Object.values(r.tgt).reduce((s, v) => s + (v || 0), 0);
            if (qty > 0) m.set(r.nm_id, (m.get(r.nm_id) || 0) + qty);
        }
        return m;
    }, [rows]);

    // Плечо поставки = средняя сборка на ФФ + средняя доставка до склада WB.
    const leadDays = useMemo(() => {
        const delivery = stockNeed?.summary?.avg_delivery_days || 7;
        const asmDays = (stockNeed?.rf_warehouses ?? []).map(w => w.assembly_days).filter(d => d > 0);
        const assembly = asmDays.length ? asmDays.reduce((s, d) => s + d, 0) / asmDays.length : 0;
        return Math.round((delivery + assembly) * 10) / 10;
    }, [stockNeed]);

    const summary = useMemo(
        () => buildUrgentShip({ articles: articles ?? [], draftQtyByNm, locPctByNm: locMap, leadDays, trendDays: TREND_DAYS }),
        [articles, draftQtyByNm, locMap, leadDays],
    );

    const locTarget = stockNeed?.summary?.localization_target ?? 75;
    const visible = showAll ? summary.inDraft : summary.inDraft.slice(0, VISIBLE_ROWS);
    const hidden = summary.inDraft.length - visible.length;

    /* ── loading / error / empty ── */
    if (state === 'loading') {
        return (
            <div className="glass-card" style={{ padding: 14, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ fontWeight: 700 }}>🚨 Срочно к отправке</span>
                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>считаем запас и потери по Аналитике остатков…</span>
            </div>
        );
    }
    if (state === 'error') {
        return (
            <div className="glass-card" style={{ padding: 14, marginBottom: 12, borderLeft: '3px solid var(--color-danger)', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 700, color: 'var(--color-danger)' }}>🚨 Срочно к отправке: аналитика остатков не загрузилась</span>
                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={load}>↻ Повторить</button>
            </div>
        );
    }
    if (summary.inDraft.length === 0 && summary.missing.length === 0) {
        return (
            <div className="glass-card" style={{ padding: 14, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 700, color: 'var(--color-success)' }}>✅ Срочных SKU нет</span>
                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                    у всех артикулов запас на WB (с учётом сборок и пути) больше 14 дней
                </span>
                <Link className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} href={`/p/${slug}/warehouse/analytics`}>
                    📊 Аналитика остатков →
                </Link>
            </div>
        );
    }

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 12, borderLeft: '3px solid var(--color-danger)' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 4 }}>
                <span style={{ fontWeight: 700, fontSize: 15 }}>🚨 Срочно к отправке — уже в черновике, ещё не в сборке</span>
                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                    запас ≤ 14 дн с учётом созданных сборок и пути · каждый день промедления — минус реализация
                </span>
                <Link className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} href={`/p/${slug}/warehouse/analytics`}>
                    📊 Аналитика остатков →
                </Link>
            </div>

            {/* KPI strip */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '10px 0 12px' }}>
                <span className="badge" style={{ fontSize: 13, fontWeight: 700, padding: '6px 12px', background: 'rgba(255,68,68,0.12)', color: 'var(--color-danger)' }}
                    title={`Потерянная реализация, если создать сборку сегодня: ₽/день × (плечо ${formatNumber(leadDays, 1)} дн − запас). Плечо = средняя сборка ФФ + доставка до WB.`}>
                    💸 теряем ~{formatNumber(summary.totalLoss, 0)} ₽
                </span>
                {summary.zeroCount > 0 && (
                    <span className="badge" style={{ fontSize: 12, padding: '6px 10px', background: 'var(--color-danger)', color: '#fff' }}>
                        ⛔ уже на нуле: {formatNumber(summary.zeroCount, 0)}
                    </span>
                )}
                {summary.criticalCount > 0 && (
                    <span className="badge" style={{ fontSize: 12, padding: '6px 10px', background: 'rgba(255,68,68,0.12)', color: 'var(--color-danger)' }}>
                        &lt;7 дн: {formatNumber(summary.criticalCount, 0)}
                    </span>
                )}
                {summary.dangerCount > 0 && (
                    <span className="badge" style={{ fontSize: 12, padding: '6px 10px', background: 'rgba(245,158,11,0.14)', color: 'var(--color-warning)' }}>
                        7–14 дн: {formatNumber(summary.dangerCount, 0)}
                    </span>
                )}
            </div>

            {/* Таблица сегмента «в черновике» */}
            {summary.inDraft.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                        <thead>
                            <tr style={{ color: 'var(--color-text-muted)', fontSize: 11, textAlign: 'left' }}>
                                <th style={{ padding: '4px 8px', fontWeight: 600 }}>Артикул</th>
                                <th style={{ padding: '4px 8px', fontWeight: 600 }}>Категория</th>
                                <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }} title={`Локализация за ${LOC_WINDOW_DAYS} дн (цель ${formatNumber(locTarget, 0)}%)`}>Локализация</th>
                                <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'center' }}>Запас</th>
                                <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }}>В черновике</th>
                                <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }}>₽/день</th>
                                <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }} title={`₽/день × дни простоя в нуле до прихода поставки (плечо ${formatNumber(leadDays, 1)} дн)`}>Теряем</th>
                            </tr>
                        </thead>
                        <tbody>
                            {visible.map(r => (
                                <tr key={r.nm_id} style={{ borderTop: '1px solid var(--color-border)', background: r.bucket === 'zero' ? 'rgba(255,68,68,0.05)' : undefined }}>
                                    <td style={{ padding: '5px 8px', fontWeight: 600, whiteSpace: 'nowrap' }} title={`nm ${r.nm_id}`}>{r.vendor}</td>
                                    <td style={{ padding: '5px 8px', color: 'var(--color-text-muted)' }}>{r.subject || '—'}</td>
                                    <td style={{ padding: '5px 8px', textAlign: 'right' }}><LocPct pct={r.locPct} target={locTarget} /></td>
                                    <td style={{ padding: '5px 8px', textAlign: 'center' }}><DaysBadge row={r} /></td>
                                    <td style={{ padding: '5px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>{formatNumber(r.draftQty, 0)} шт</td>
                                    <td style={{ padding: '5px 8px', textAlign: 'right', whiteSpace: 'nowrap', color: 'var(--color-text-muted)' }}>{formatNumber(r.dailyRevenue, 0)} ₽</td>
                                    <td style={{ padding: '5px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {r.loss > 0
                                            ? <span style={{ fontWeight: 700, color: 'var(--color-danger)' }} title={`${formatNumber(r.gapDays, 1)} дн простоя × ${formatNumber(r.dailyRevenue, 0)} ₽/день`}>−{formatNumber(r.loss, 0)} ₽</span>
                                            : <span style={{ color: 'var(--color-text-dim)' }} title="Запас перекрывает плечо поставки — при отправке сегодня простоя не будет">—</span>}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {(hidden > 0 || showAll) && (
                        <button className="btn btn-secondary btn-sm" style={{ marginTop: 8 }} onClick={() => setShowAll(v => !v)}>
                            {showAll ? 'Свернуть' : `Показать ещё ${formatNumber(hidden, 0)}`}
                        </button>
                    )}
                </div>
            ) : (
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', padding: '4px 0' }}>
                    В черновике срочных SKU нет — но есть срочные вне черновика ↓
                </div>
            )}

            {/* Сегмент «вне черновика» — компактные чипы */}
            {summary.missing.length > 0 && (
                <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px dashed var(--color-border)' }}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
                        <span style={{ fontWeight: 700, fontSize: 12.5, color: 'var(--color-warning)' }}>
                            ⚠️ Срочные ВНЕ черновика — {formatNumber(summary.missing.length, 0)} арт. (~{formatNumber(summary.missingLoss, 0)} ₽)
                        </span>
                        <span style={{ fontSize: 11.5, color: 'var(--color-text-muted)' }}>
                            есть свободный сток на ФФ — добавьте «Заполнить черновик из потребности» или на вкладке «Потребность по складам»
                        </span>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                        {summary.missing.slice(0, VISIBLE_CHIPS).map(r => (
                            <span key={r.nm_id}
                                title={`nm ${r.nm_id} · запас ${formatNumber(r.daysLeft, 0)} дн · свободно на ФФ ${formatNumber(r.ffFree, 0)} шт${r.loss > 0 ? ` · теряем ~${formatNumber(r.loss, 0)} ₽` : ''}`}
                                style={{
                                    fontSize: 11, padding: '2px 8px', borderRadius: 6,
                                    background: r.bucket === 'zero' ? 'rgba(255,68,68,0.14)' : 'rgba(245,158,11,0.12)',
                                    color: r.bucket === 'zero' ? 'var(--color-danger)' : 'var(--color-warning)',
                                    fontWeight: 600,
                                }}>
                                {r.vendor} · {r.bucket === 'zero' ? '0 дн' : `${formatNumber(r.daysLeft, 0)} дн`}{r.loss > 0 ? ` · −${formatNumber(r.loss, 0)} ₽` : ''}
                            </span>
                        ))}
                        {summary.missing.length > VISIBLE_CHIPS && (
                            <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>…ещё {formatNumber(summary.missing.length - VISIBLE_CHIPS, 0)}</span>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
