'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { ForecastItem, ForecastResponse, TrafficLight } from '@/types/api';

/** Цвет светофора → CSS-токен (запас в днях после поставки). */
const LIGHT_COLOR: Record<TrafficLight, string> = {
    red: 'var(--color-danger)',
    orange: 'var(--color-warning)',
    yellow: 'var(--color-warning)',
    green: 'var(--color-success)',
};
const LIGHT_LABEL: Record<TrafficLight, string> = {
    red: '< 7 дн', orange: '8–14 дн', yellow: '15–29 дн', green: '30+ дн',
};
const LIGHT_ORDER: TrafficLight[] = ['red', 'orange', 'yellow', 'green'];
const lightBg = (l: TrafficLight) => `color-mix(in srgb, ${LIGHT_COLOR[l]} 16%, transparent)`;

type MatrixMetric = 'days' | 'stock_in' | 'projected';
const METRICS: { key: MatrixMetric; label: string }[] = [
    { key: 'days', label: 'Дней покрытия' },
    { key: 'stock_in', label: 'Остаток + входит' },
    { key: 'projected', label: 'Прогноз на приход' },
];

function Dot({ light }: { light: TrafficLight }) {
    return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{
                width: 10, height: 10, borderRadius: 8,
                background: LIGHT_COLOR[light], display: 'inline-block', flexShrink: 0,
            }} />
            {LIGHT_LABEL[light]}
        </span>
    );
}

const metricValue = (it: ForecastItem, m: MatrixMetric): number =>
    m === 'days' ? it.days_cover
        : m === 'stock_in' ? it.current_stock + it.incoming  // сортировка по итогу (остаток+входит)
            : it.projected_on_arrival;

const cellTitle = (it: ForecastItem): string =>
    `Остаток ${formatNumber(it.current_stock, 0)} + входит ${formatNumber(it.incoming, 0)} `
    + `→ ${it.days_cover >= 999 ? '∞' : formatNumber(it.days_cover, 0)} дн `
    + `(${formatNumber(it.avg_daily, 1)} шт/дн)`;

/** Вкладка «Прогноз / Локализация»: загрузка WB-складов с учётом черновика —
 *  текущий остаток + входящая − продажи за lead-time → дни покрытия/светофор +
 *  примерный индекс локализации до/после поставки. Низ — матрица артикул×склад. */
interface ForecastViewProps {
    draftId: number;
    /** Удалить SKU (неликвид) из черновика + авто-дозабивка паллет. */
    onRemoveSkus?: (nmIds: number[]) => Promise<{ removed: number; consolidated: boolean }>;
    onToast?: (message: string, type: 'success' | 'error') => void;
}

export default function ForecastView({ draftId, onRemoveSkus, onToast }: ForecastViewProps) {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [data, setData] = useState<ForecastResponse | null>(null);
    const [view, setView] = useState<'matrix' | 'list'>('matrix');
    const [metric, setMetric] = useState<MatrixMetric>('days');
    // Фильтр «неликвид» (затоварка): SKU с днями покрытия ≥ порога (или ∞ — нет продаж).
    const [illiquidOnly, setIlliquidOnly] = useState(false);
    const [illiquidDays, setIlliquidDays] = useState(180);
    const [skuKind, setSkuKind] = useState<'all' | 'new' | 'regular'>('all');  // фильтр новинки/обычные
    const [deleting, setDeleting] = useState(false);
    // Сортировка матрицы. key: 'article' | 'incoming' | 'loc' | имя WB-склада.
    const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' }>({ key: 'incoming', dir: 'desc' });
    const onSort = useCallback((key: string) => {
        setSort(s => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: key === 'article' ? 'asc' : 'desc' }));
    }, []);
    const arrow = (key: string) => (sort.key === key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : '');

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            setData(await api.getDraftForecast(draftId));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить прогноз');
        } finally {
            setLoading(false);
        }
    }, [draftId]);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            setError(null);
            try {
                const res = await api.getDraftForecast(draftId);
                if (!cancelled) setData(res);
            } catch (e: unknown) {
                if (!cancelled) setError(e instanceof Error ? e.message : 'Не удалось загрузить прогноз');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [draftId]);

    // ─── Пивот для матрицы: артикулы (строки) × склады (столбцы) ──────────────
    const matrix = useMemo(() => {
        if (!data) return null;
        // Столбцы — из сводки по складам (уже отсортированы по входящей).
        const columns = data.warehouses;
        const locByNm = new Map<number, { cur: number; after: number }>();
        for (const s of data.sku_localization) locByNm.set(s.nm_id, { cur: s.loc_pct_current, after: s.loc_pct_after });
        const newcomerSet = new Set(data.newcomer_nm_ids);
        // Строки — уникальные артикулы.
        type Row = { nm_id: number; vendor: string; isNew: boolean; incoming: number; current: number; vel: number; daysCover: number; locCur: number | null; locAfter: number | null };
        const byNm = new Map<number, Row>();
        const cell = new Map<string, ForecastItem>();
        for (const it of data.items) {
            cell.set(`${it.nm_id}::${it.warehouse_name}`, it);
            const loc = locByNm.get(it.nm_id);
            const r = byNm.get(it.nm_id) ?? { nm_id: it.nm_id, vendor: it.vendor_code, isNew: newcomerSet.has(it.nm_id), incoming: 0, current: 0, vel: 0, daysCover: 0, locCur: loc?.cur ?? null, locAfter: loc?.after ?? null };
            r.incoming += it.incoming;
            r.current += it.current_stock;
            r.vel += it.avg_daily;  // суммарная скорость продаж SKU (по всем складам)
            byNm.set(it.nm_id, r);
        }
        // Дни покрытия по SKU = (остаток+входит)/скорость; нет продаж → 999 (∞, явный неликвид).
        const rows = [...byNm.values()];
        for (const r of rows) r.daysCover = r.vel > 0 ? Math.round((r.current + r.incoming) / r.vel) : 999;
        return { columns, rows, cell };
    }, [data]);

    // Фильтр неликвида + сортировка строк матрицы (метрика влияет на сорт по складу).
    const sortedRows = useMemo(() => {
        if (!matrix) return [];
        let base = illiquidOnly ? matrix.rows.filter(r => r.daysCover >= illiquidDays) : matrix.rows;
        if (skuKind !== 'all') base = base.filter(r => (skuKind === 'new') === r.isNew);
        const dir = sort.dir === 'asc' ? 1 : -1;
        const valOf = (r: typeof matrix.rows[number]): number | string => {
            if (sort.key === 'article') return (r.vendor || `nm:${r.nm_id}`).toLowerCase();
            if (sort.key === 'incoming') return r.incoming;
            if (sort.key === 'loc') return r.locAfter ?? -1;
            if (sort.key === 'days') return r.daysCover;
            const it = matrix.cell.get(`${r.nm_id}::${sort.key}`);
            return it ? metricValue(it, metric) : Number.NEGATIVE_INFINITY; // пустые ячейки — в конец при desc
        };
        return [...base].sort((a, b) => {
            const va = valOf(a), vb = valOf(b);
            if (typeof va === 'string' || typeof vb === 'string') return String(va).localeCompare(String(vb)) * dir;
            return (va - vb) * dir;
        });
    }, [matrix, sort, metric, illiquidOnly, illiquidDays, skuKind]);

    // Удаление: один SKU или все отфильтрованные неликвиды. С подтверждением.
    const doRemove = useCallback(async (nmIds: number[], label: string) => {
        if (!onRemoveSkus || nmIds.length === 0) return;
        if (!window.confirm(`Удалить ${label} из черновика? Товар останется на ФФ, паллеты переупакуются.`)) return;
        setDeleting(true);
        try {
            const res = await onRemoveSkus(nmIds);
            setData(await api.getDraftForecast(draftId)); // обновить прогноз после удаления
            onToast?.(`Удалено SKU: ${res.removed}${res.consolidated ? ' · паллеты дозабиты' : ''}`, 'success');
        } catch (e: unknown) {
            onToast?.(e instanceof Error ? e.message : 'Не удалось удалить', 'error');
        } finally {
            setDeleting(false);
        }
    }, [onRemoveSkus, onToast, draftId]);

    const columns: Column[] = useMemo(() => [
        { key: 'warehouse_name', label: 'WB-склад' },
        { key: 'district_label', label: 'Округ' },
        {
            key: 'vendor_code', label: 'Артикул',
            render: (_v, r) => r.vendor_code || `nm:${r.nm_id}`,
            exportValue: (r) => r.vendor_code || `nm:${r.nm_id}`,
        },
        { key: 'current_stock', label: 'Остаток сейчас', align: 'right', format: 'number' },
        { key: 'incoming', label: 'Входит', align: 'right', format: 'number' },
        { key: 'avg_daily', label: 'Продаж/дн', align: 'right', render: (v) => formatNumber(v, 1) },
        {
            key: 'projected_on_arrival', label: 'Остаток на приход', align: 'right',
            render: (v) => (
                <span style={{ color: v < 0 ? 'var(--color-danger)' : 'var(--color-text)', fontWeight: v < 0 ? 600 : 400 }}>
                    {formatNumber(v, 0)}
                </span>
            ),
        },
        { key: 'days_cover', label: 'Дней покрытия', align: 'right', render: (v) => (v >= 999 ? '∞' : formatNumber(v, 0)) },
        {
            key: 'traffic_light', label: 'Запас', sortable: true,
            render: (v) => <Dot light={v as TrafficLight} />,
            exportValue: (r) => LIGHT_LABEL[r.traffic_light as TrafficLight],
        },
    ], []);

    const exportMatrix = useCallback(() => {
        if (!matrix) return;
        const rows = sortedRows.map((r) => {
            const row: Record<string, string | number> = {
                article: r.vendor || `nm:${r.nm_id}`,
                is_new: r.isNew ? 'новинка' : '',
                loc_current: r.locCur == null ? '' : r.locCur,
                loc_after: r.locAfter == null ? '' : r.locAfter,
                days_cover: r.daysCover >= 999 ? '∞' : r.daysCover,
            };
            for (const w of matrix.columns) {
                const it = matrix.cell.get(`${r.nm_id}::${w.warehouse_name}`);
                row[w.warehouse_name] = it ? metricValue(it, metric) : '';
            }
            row.total_incoming = r.incoming;
            return row;
        });
        const cols = [
            { key: 'article', label: 'Артикул' },
            { key: 'is_new', label: 'Новинка' },
            { key: 'loc_current', label: 'Локализация сейчас %' },
            { key: 'loc_after', label: 'Локализация после %' },
            { key: 'days_cover', label: 'Дней покрытия' },
            ...matrix.columns.map((w) => ({ key: w.warehouse_name, label: w.warehouse_name })),
            { key: 'total_incoming', label: 'Σ входит' },
        ];
        const metricLabel = METRICS.find((m) => m.key === metric)?.label ?? metric;
        exportToExcel(rows, `Матрица_${metricLabel}`, cols);
    }, [matrix, metric, sortedRows]);

    if (loading) {
        return <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>Загрузка прогноза…</div>;
    }
    if (error) {
        return (
            <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>
                <p style={{ color: 'var(--color-danger)', marginBottom: 12 }}>⚠ {error}</p>
                <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
            </div>
        );
    }
    if (!data || data.items.length === 0) {
        return (
            <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>
                Черновик пуст или нет данных об остатках — добавьте товары во вкладке «Потребность по складам».
            </div>
        );
    }

    const { localization: loc, lead_time: lt, summary } = data;
    const locImproved = loc.index_after < loc.index_current; // КТР ниже = лучше

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }} className="animate-in">
            {/* KPI-карточки */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
                <div className="glass-card" style={{ padding: 20 }}>
                    <div style={{ fontSize: 13, color: 'var(--color-muted)', marginBottom: 8 }}>Индекс локализации (КТР)</div>
                    <div style={{ fontSize: 28, fontWeight: 700, display: 'flex', alignItems: 'baseline', gap: 8 }}>
                        <span style={{ color: 'var(--color-dim)' }}>{formatNumber(loc.index_current, 2)}</span>
                        <span style={{ color: 'var(--color-muted)' }}>→</span>
                        <span style={{ color: locImproved ? 'var(--color-success)' : 'var(--color-danger)' }}>
                            {formatNumber(loc.index_after, 2)}
                        </span>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--color-dim)', marginTop: 4 }}>ниже = лучше</div>
                </div>
                <div className="glass-card" style={{ padding: 20 }}>
                    <div style={{ fontSize: 13, color: 'var(--color-muted)', marginBottom: 8 }}>Доля локализации</div>
                    <div style={{ fontSize: 28, fontWeight: 700, display: 'flex', alignItems: 'baseline', gap: 8 }}>
                        <span style={{ color: 'var(--color-dim)' }}>{formatNumber(loc.avg_loc_pct_current, 1)}%</span>
                        <span style={{ color: 'var(--color-muted)' }}>→</span>
                        <span style={{ color: 'var(--color-success)' }}>{formatNumber(loc.avg_loc_pct_after, 1)}%</span>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--color-dim)', marginTop: 4 }}>спроса покрыто локально (горизонт {loc.horizon_days} дн)</div>
                </div>
                <div className="glass-card" style={{ padding: 20 }}>
                    <div style={{ fontSize: 13, color: 'var(--color-muted)', marginBottom: 8 }}>Lead-time до WB</div>
                    <div style={{ fontSize: 28, fontWeight: 700 }}>{formatNumber(lt.total_days, 1)} дн</div>
                    <div style={{ fontSize: 12, color: 'var(--color-dim)', marginTop: 4 }}>
                        сборка+отправка {formatNumber(lt.assembly_ship_days, 1)} + доставка {formatNumber(lt.delivery_days, 1)}
                        {!lt.has_history && ' (по умолчанию — нет истории)'}
                    </div>
                </div>
                <div className="glass-card" style={{ padding: 20 }}>
                    <div style={{ fontSize: 13, color: 'var(--color-muted)', marginBottom: 8 }}>Входит в поставку</div>
                    <div style={{ fontSize: 28, fontWeight: 700 }}>{formatNumber(summary.total_incoming, 0)} шт</div>
                    <div style={{ fontSize: 12, color: 'var(--color-dim)', marginTop: 4 }}>
                        {formatNumber(summary.sku_count, 0)} SKU · {formatNumber(summary.warehouse_count, 0)} складов
                    </div>
                </div>
            </div>

            {/* Светофор-итоги */}
            <div className="glass-card" style={{ padding: 16, display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'center' }}>
                <span style={{ fontSize: 13, color: 'var(--color-muted)' }}>Запас после поставки (склад×SKU):</span>
                {LIGHT_ORDER.map((l) => (
                    <span key={l} style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                        <Dot light={l} />
                        <strong>{formatNumber(summary.traffic_light_counts[l] || 0, 0)}</strong>
                    </span>
                ))}
            </div>

            {/* Локализация по округам */}
            <div className="glass-card" style={{ padding: 20 }}>
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>Локализация по федеральным округам (примерно)</div>
                <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            <tr style={{ textAlign: 'right', color: 'var(--color-muted)' }}>
                                <th style={{ textAlign: 'left', padding: '6px 8px' }}>Округ</th>
                                <th style={{ padding: '6px 8px' }}>Спрос ({loc.horizon_days} дн)</th>
                                <th style={{ padding: '6px 8px' }}>Покрытие сейчас</th>
                                <th style={{ padding: '6px 8px' }}>После поставки</th>
                                <th style={{ padding: '6px 8px' }}>Локализация %</th>
                            </tr>
                        </thead>
                        <tbody>
                            {loc.by_district.map((d) => (
                                <tr key={d.district} style={{ borderTop: '1px solid var(--color-border)' }}>
                                    <td style={{ textAlign: 'left', padding: '6px 8px' }}>{d.label}</td>
                                    <td style={{ textAlign: 'right', padding: '6px 8px' }}>{formatNumber(d.demand, 0)}</td>
                                    <td style={{ textAlign: 'right', padding: '6px 8px', color: 'var(--color-dim)' }}>{formatNumber(d.avail_current, 0)}</td>
                                    <td style={{ textAlign: 'right', padding: '6px 8px' }}>{formatNumber(d.avail_after, 0)}</td>
                                    <td style={{ textAlign: 'right', padding: '6px 8px' }}>
                                        <span style={{ color: 'var(--color-dim)' }}>{formatNumber(d.local_pct_current, 0)}%</span>
                                        <span style={{ color: 'var(--color-muted)' }}> → </span>
                                        <strong style={{ color: 'var(--color-success)' }}>{formatNumber(d.local_pct_after, 0)}%</strong>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>

            {/* Тулбар: вид (матрица/список) + метрика + экспорт */}
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                <div style={{ display: 'flex', gap: 4 }}>
                    <button className={`btn btn-sm ${view === 'matrix' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setView('matrix')}>Матрица</button>
                    <button className={`btn btn-sm ${view === 'list' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setView('list')}>Список</button>
                </div>
                {view === 'matrix' && (
                    <>
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                            {METRICS.map((m) => (
                                <button
                                    key={m.key}
                                    className={`btn btn-sm ${metric === m.key ? 'btn-primary' : 'btn-secondary'}`}
                                    onClick={() => setMetric(m.key)}
                                >{m.label}</button>
                            ))}
                        </div>
                        <button className="btn btn-sm btn-secondary" style={{ marginLeft: 'auto' }} onClick={exportMatrix}>⬇ Excel</button>
                    </>
                )}
            </div>

            {/* Фильтр неликвида (затоварка) + массовое удаление */}
            {view === 'matrix' && (
                <div className="glass-card" style={{ padding: '10px 16px', display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center' }}>
                    <div style={{ display: 'flex', gap: 4 }}>
                        {([['all', 'Все'], ['new', '🆕 Новинки'], ['regular', 'Обычные']] as const).map(([k, l]) => (
                            <button
                                key={k}
                                className={`btn btn-sm ${skuKind === k ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setSkuKind(k)}
                            >{l}</button>
                        ))}
                    </div>
                    <span style={{ width: 1, alignSelf: 'stretch', background: 'var(--color-border)' }} />
                    <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }} title="Показать только затоваренные SKU (дней покрытия ≥ порога; ∞ = нет продаж)">
                        <input type="checkbox" checked={illiquidOnly} onChange={e => setIlliquidOnly(e.target.checked)} />
                        🐌 Только неликвид (затоварка)
                    </label>
                    <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6, opacity: illiquidOnly ? 1 : 0.5 }}>
                        дней покрытия ≥
                        <input
                            type="number" min={1} value={illiquidDays} disabled={!illiquidOnly}
                            onChange={e => setIlliquidDays(Math.max(1, Number(e.target.value) || 1))}
                            style={{ width: 70 }} className="form-input"
                        />
                    </label>
                    {illiquidOnly && (
                        <span style={{ fontSize: 13, color: 'var(--color-muted)' }}>
                            найдено: <strong>{formatNumber(sortedRows.length, 0)}</strong> SKU
                        </span>
                    )}
                    {illiquidOnly && onRemoveSkus && sortedRows.length > 0 && (
                        <button
                            className="btn btn-sm btn-danger" style={{ marginLeft: 'auto' }} disabled={deleting}
                            onClick={() => doRemove(sortedRows.map(r => r.nm_id), `${sortedRows.length} неликвидных SKU`)}
                        >{deleting ? 'Удаление…' : `🗑 Удалить неликвид (${sortedRows.length})`}</button>
                    )}
                </div>
            )}

            {/* Матрица артикул × склад */}
            {view === 'matrix' && matrix && (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                    <table style={{ borderCollapse: 'collapse', fontSize: 13, minWidth: '100%' }}>
                        <thead>
                            <tr>
                                <th onClick={() => onSort('article')} style={{
                                    position: 'sticky', left: 0, zIndex: 2, background: 'var(--color-bg-card)',
                                    textAlign: 'left', padding: '10px 12px', minWidth: 160, cursor: 'pointer',
                                    borderBottom: '1px solid var(--color-border)',
                                }}>Артикул{arrow('article')}</th>
                                <th onClick={() => onSort('loc')} title="Доля локализации: до → после поставки (выше = лучше)" style={{
                                    padding: '10px 10px', textAlign: 'right', whiteSpace: 'nowrap', cursor: 'pointer',
                                    borderBottom: '1px solid var(--color-border)', fontWeight: 600,
                                }}>Локализация{arrow('loc')}</th>
                                <th onClick={() => onSort('days')} title="Дней покрытия SKU: (остаток+входит)/скорость; ∞ = нет продаж (явный неликвид)" style={{
                                    padding: '10px 8px', textAlign: 'right', whiteSpace: 'nowrap', cursor: 'pointer',
                                    borderBottom: '1px solid var(--color-border)', fontWeight: 600,
                                }}>Дней{arrow('days')}</th>
                                {matrix.columns.map((w) => (
                                    <th key={w.warehouse_name} onClick={() => onSort(w.warehouse_name)} title={w.district_label} style={{
                                        padding: '10px 8px', textAlign: 'right', whiteSpace: 'nowrap', cursor: 'pointer',
                                        borderBottom: '1px solid var(--color-border)', fontWeight: 600,
                                    }}>{w.warehouse_name}{arrow(w.warehouse_name)}</th>
                                ))}
                                <th onClick={() => onSort('incoming')} style={{ padding: '10px 12px', textAlign: 'right', cursor: 'pointer', borderBottom: '1px solid var(--color-border)' }}>Σ входит{arrow('incoming')}</th>
                                {onRemoveSkus && <th style={{ borderBottom: '1px solid var(--color-border)' }} />}
                            </tr>
                        </thead>
                        <tbody>
                            {sortedRows.map((r) => (
                                <tr key={r.nm_id}>
                                    <td style={{
                                        position: 'sticky', left: 0, zIndex: 1, background: 'var(--color-bg-card)',
                                        padding: '8px 12px', borderBottom: '1px solid var(--color-border)',
                                        fontWeight: 500, whiteSpace: 'nowrap',
                                    }} title={`nm:${r.nm_id}`}>
                                        {r.isNew && <span title="Новинка (cold-start)" style={{ marginRight: 5 }}>🆕</span>}
                                        {r.vendor || `nm:${r.nm_id}`}
                                    </td>
                                    <td style={{ padding: '8px 10px', textAlign: 'right', whiteSpace: 'nowrap', borderBottom: '1px solid var(--color-border)' }}>
                                        {r.locAfter == null ? <span style={{ color: 'var(--color-dim)' }}>—</span> : (
                                            <>
                                                <span style={{ color: 'var(--color-dim)' }}>{formatNumber(r.locCur ?? 0, 0)}%</span>
                                                <span style={{ color: 'var(--color-muted)' }}> → </span>
                                                <strong style={{ color: (r.locAfter > (r.locCur ?? 0)) ? 'var(--color-success)' : 'var(--color-text)' }}>{formatNumber(r.locAfter, 0)}%</strong>
                                            </>
                                        )}
                                    </td>
                                    <td style={{
                                        padding: '8px 8px', textAlign: 'right', whiteSpace: 'nowrap',
                                        borderBottom: '1px solid var(--color-border)',
                                        color: r.daysCover >= illiquidDays ? 'var(--color-danger)' : 'var(--color-text)',
                                        fontWeight: r.daysCover >= illiquidDays ? 700 : 400,
                                    }} title="Дней покрытия SKU">{r.daysCover >= 999 ? '∞' : formatNumber(r.daysCover, 0)}</td>
                                    {matrix.columns.map((w) => {
                                        const it = matrix.cell.get(`${r.nm_id}::${w.warehouse_name}`);
                                        if (!it) {
                                            return <td key={w.warehouse_name} style={{ padding: '8px', textAlign: 'right', color: 'var(--color-dim)', borderBottom: '1px solid var(--color-border)' }}>·</td>;
                                        }
                                        const val = metricValue(it, metric);
                                        const isDays = metric === 'days';
                                        const isProj = metric === 'projected';
                                        const isStockIn = metric === 'stock_in';
                                        return (
                                            <td key={w.warehouse_name} title={cellTitle(it)} style={{
                                                padding: '8px', textAlign: 'right', whiteSpace: 'nowrap',
                                                borderBottom: '1px solid var(--color-border)',
                                                background: isDays ? lightBg(it.traffic_light) : undefined,
                                                color: isProj && val < 0 ? 'var(--color-danger)' : 'var(--color-text)',
                                                fontWeight: (isProj && val < 0) || (isDays && it.traffic_light === 'red') ? 700 : 400,
                                            }}>
                                                {isStockIn ? (
                                                    <>
                                                        {formatNumber(it.current_stock, 0)}
                                                        {it.incoming > 0 && <span style={{ color: 'var(--color-accent)' }}> +{formatNumber(it.incoming, 0)}</span>}
                                                    </>
                                                ) : (isDays && val >= 999 ? '∞' : formatNumber(val, 0))}
                                            </td>
                                        );
                                    })}
                                    <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600, borderBottom: '1px solid var(--color-border)' }}>{formatNumber(r.incoming, 0)}</td>
                                    {onRemoveSkus && (
                                        <td style={{ padding: '8px 8px', textAlign: 'center', borderBottom: '1px solid var(--color-border)' }}>
                                            <button className="btn btn-sm btn-secondary" title="Удалить SKU из черновика" disabled={deleting} style={{ padding: '2px 8px' }} onClick={() => doRemove([r.nm_id], r.vendor || `nm:${r.nm_id}`)}>✕</button>
                                        </td>
                                    )}
                                </tr>
                            ))}
                        </tbody>
                        <tfoot>
                            <tr>
                                <td style={{ position: 'sticky', left: 0, background: 'var(--color-bg-card)', padding: '10px 12px', fontWeight: 700 }}>Итого входит</td>
                                <td style={{ borderTop: '1px solid var(--color-border)' }} />
                                <td style={{ borderTop: '1px solid var(--color-border)' }} />
                                {matrix.columns.map((w) => (
                                    <td key={w.warehouse_name} style={{ padding: '10px 8px', textAlign: 'right', fontWeight: 700 }}>{formatNumber(w.incoming, 0)}</td>
                                ))}
                                <td style={{ padding: '10px 12px', textAlign: 'right', fontWeight: 700 }}>{formatNumber(summary.total_incoming, 0)}</td>
                                {onRemoveSkus && <td />}
                            </tr>
                        </tfoot>
                    </table>
                    <div style={{ padding: '10px 12px', fontSize: 12, color: 'var(--color-muted)', display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
                        {metric === 'days' && LIGHT_ORDER.map((l) => <Dot key={l} light={l} />)}
                        {metric === 'stock_in' && <span>ячейка: остаток <span style={{ color: 'var(--color-accent)' }}>+входящая</span></span>}
                        <span>· 🆕 — новинка · наведи на ячейку для деталей</span>
                    </div>
                </div>
            )}

            {/* Список (склад × SKU) */}
            {view === 'list' && (
                <TanStackDataTable
                    columns={columns}
                    data={data.items}
                    title="Прогноз запаса по WB-складам"
                    exportName="Прогноз_загрузки_складов"
                    emptyText="Нет позиций"
                    pageSize={50}
                />
            )}
        </div>
    );
}
