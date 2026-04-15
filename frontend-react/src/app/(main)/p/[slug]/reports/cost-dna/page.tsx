'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import PageHeader from '@/components/PageHeader';
import PageGuard from '@/components/PageGuard';
import type { CostDnaResponse, CostDnaCategory, CostDnaTotals } from '@/types/api';

/** Quick-pick period presets (days). Custom range via date inputs. */
const PRESETS = [7, 14, 30, 60] as const;

/** Format Date → YYYY-MM-DD in local TZ (no UTC shift — matches <input type="date">) */
function ymd(d: Date): string {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
}

function addDays(d: Date, n: number): Date {
    const r = new Date(d);
    r.setDate(r.getDate() + n);
    return r;
}

/** Rolling window: [yesterday - (days-1) ... yesterday] — same math as backend legacy default */
function defaultDates(days: number): { from: string; to: string } {
    const yesterday = addDays(new Date(), -1);
    const from = addDays(yesterday, -(days - 1));
    return { from: ymd(from), to: ymd(yesterday) };
}

/** Arrow + color for margin trend */
function MarginTrendArrow({ trend }: { trend: 'up' | 'down' | null }) {
    if (!trend) return null;
    const isUp = trend === 'up';
    return (
        <span
            style={{
                marginLeft: 4,
                fontSize: 11,
                color: isUp ? 'var(--color-success)' : 'var(--color-danger)',
                fontWeight: 600,
            }}
        >
            {isUp ? '\u25B2' : '\u25BC'}
        </span>
    );
}

/** Format percent cell — returns em-dash if null */
function pct(v: number | null | undefined): string {
    if (v == null) return '\u2014';
    return formatNumber(v) + '%';
}

/** Build flat rows for Excel export */
function buildExcelRows(data: CostDnaResponse): Record<string, unknown>[] {
    const rows: Record<string, unknown>[] = data.categories.map((c) => ({
        'Категория': c.category,
        'Доля %': c.revenue_share_pct,
        'Выручка': c.revenue,
        'Фабрика %': c.cost_factory_pct,
        'Пошлина %': c.cost_duty_pct,
        'Доставка %': c.cost_delivery_pct,
        'НДС %': c.cost_vat_pct,
        'Себестоимость Итого %': c.cost_total_pct,
        'Комиссия %': c.mp_commission_pct,
        'Логистика %': c.mp_logistics_pct,
        'Хранение %': c.mp_storage_pct,
        'Реклама %': c.mp_advertising_pct,
        'Прочее %': c.mp_other_pct,
        'Маркетплейс Итого %': c.mp_total_pct,
        'Налоги %': c.tax_pct,
        'Маржа %': c.margin_pct,
        'Тренд': c.margin_trend ?? '',
    }));
    // Footer "ИТОГО"
    const t = data.totals;
    rows.push({
        'Категория': 'ИТОГО',
        'Доля %': 100,
        'Выручка': t.revenue,
        'Фабрика %': t.cost_factory_pct,
        'Пошлина %': t.cost_duty_pct,
        'Доставка %': t.cost_delivery_pct,
        'НДС %': t.cost_vat_pct,
        'Себестоимость Итого %': t.cost_total_pct,
        'Комиссия %': t.mp_commission_pct,
        'Логистика %': t.mp_logistics_pct,
        'Хранение %': t.mp_storage_pct,
        'Реклама %': t.mp_advertising_pct,
        'Прочее %': t.mp_other_pct,
        'Маркетплейс Итого %': t.mp_total_pct,
        'Налоги %': t.tax_pct,
        'Маржа %': t.margin_pct,
        'Тренд': t.margin_trend ?? '',
    });
    return rows;
}

/** Cell style for numeric data cells */
const cellStyle: React.CSSProperties = {
    padding: '8px 10px',
    textAlign: 'right',
    fontVariantNumeric: 'tabular-nums',
    whiteSpace: 'nowrap',
    borderBottom: '1px solid var(--color-border)',
    fontSize: 13,
};

/** Header cell base style */
const thBase: React.CSSProperties = {
    padding: '10px 8px',
    textAlign: 'center',
    fontSize: 12,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.03em',
    background: 'var(--color-bg-card)',
    borderBottom: '1px solid var(--color-border)',
    whiteSpace: 'nowrap',
};

export default function CostDnaPage() {
    const initial = defaultDates(30);
    const [dateFrom, setDateFrom] = useState<string>(initial.from);
    const [dateTo, setDateTo] = useState<string>(initial.to);
    const [data, setData] = useState<CostDnaResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const loadData = useCallback(async () => {
        // client-side guards to avoid round-trips on obviously invalid input
        if (!dateFrom || !dateTo) return;
        if (dateFrom > dateTo) {
            setError('Дата "от" не может быть позже даты "до"');
            setLoading(false);
            return;
        }
        try {
            setLoading(true);
            setError(null);
            const res = await api.getCostDna({ dateFrom, dateTo });
            setData(res);
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : 'Ошибка загрузки';
            setError(msg);
        } finally {
            setLoading(false);
        }
    }, [dateFrom, dateTo]);

    useEffect(() => {
        loadData();
    }, [loadData]);

    const applyPreset = useCallback((days: number) => {
        const { from, to } = defaultDates(days);
        setDateFrom(from);
        setDateTo(to);
    }, []);

    /** Which preset matches current range (for button highlighting) */
    const activePreset: number | null = (() => {
        const yesterday = defaultDates(30).to;
        if (dateTo !== yesterday) return null;
        for (const d of PRESETS) {
            if (dateFrom === defaultDates(d).from) return d;
        }
        return null;
    })();

    const handleExport = useCallback(() => {
        if (!data) return;
        exportToExcel(
            buildExcelRows(data),
            `cost_dna_${data.period_days}d_${data.date_from}_${data.date_to}`,
        );
    }, [data]);

    /** Period toolbar — presets + custom date range (BDR-compatible) */
    const dateInputStyle: React.CSSProperties = {
        padding: '6px 10px',
        fontSize: 13,
        borderRadius: 8,
        border: '1px solid var(--color-border)',
        background: 'var(--color-bg-card)',
        color: 'var(--color-text)',
        fontFamily: 'inherit',
    };

    const toolbar = (
        <div
            className="glass-card"
            style={{
                display: 'flex',
                gap: 12,
                alignItems: 'center',
                padding: '10px 16px',
                marginBottom: 16,
                flexWrap: 'wrap',
            }}
        >
            <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Период:</span>
            {PRESETS.map((d) => (
                <button
                    key={d}
                    onClick={() => applyPreset(d)}
                    className={`btn btn-sm ${activePreset === d ? 'btn-primary' : 'btn-secondary'}`}
                >
                    {d} дней
                </button>
            ))}
            <input
                type="date"
                value={dateFrom}
                max={dateTo || undefined}
                onChange={(e) => setDateFrom(e.target.value)}
                style={dateInputStyle}
                aria-label="Дата от"
            />
            <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>—</span>
            <input
                type="date"
                value={dateTo}
                min={dateFrom || undefined}
                onChange={(e) => setDateTo(e.target.value)}
                style={dateInputStyle}
                aria-label="Дата до"
            />
            {data && (
                <span style={{ fontSize: 12, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>
                    {data.period_days} д.
                </span>
            )}
        </div>
    );

    return (
        <PageGuard page="reports">
            <div className="animate-in">
                <PageHeader
                    title="ДНК Себестоимости"
                    subtitle="Разложение каждого рубля выручки по категориям WB"
                />

                {toolbar}

                {loading && <div className="glass-card">Загрузка...</div>}

                {!loading && error && (
                    <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                        {error}
                    </div>
                )}

                {!loading && !error && data && data.categories.length === 0 && (
                    <div className="empty-state">
                        <div className="empty-state-icon">🧬</div>
                        <p>Нет данных за выбранный период</p>
                    </div>
                )}

                {!loading && !error && data && data.categories.length > 0 && (
                    <CostDnaContent data={data} onExport={handleExport} />
                )}
            </div>
        </PageGuard>
    );
}

/* ─── Content (KPI cards + table) ─────────────────────────────────────── */

function CostDnaContent({ data, onExport }: { data: CostDnaResponse; onExport: () => void }) {
    const t = data.totals;
    const marginColor =
        t.margin_trend === 'up'
            ? 'var(--color-success)'
            : t.margin_trend === 'down'
                ? 'var(--color-danger)'
                : 'var(--color-accent)';

    return (
        <>
            {/* KPI cards */}
            <div className="stats-grid" style={{ marginBottom: 16 }}>
                <div className="stat-card" style={{ borderLeft: '3px solid var(--color-accent)' }}>
                    <div className="stat-card-label">Выручка</div>
                    <div className="stat-card-value" style={{ color: 'var(--color-accent)', fontSize: 22 }}>
                        {formatNumber(t.revenue)} ₽
                    </div>
                </div>
                <div className="stat-card" style={{ borderLeft: '3px solid var(--color-warning)' }}>
                    <div className="stat-card-label">Себестоимость</div>
                    <div className="stat-card-value" style={{ color: 'var(--color-warning)', fontSize: 22 }}>
                        {t.cost_total_pct > 0 ? pct(t.cost_total_pct) : '\u2014'}
                    </div>
                </div>
                <div className="stat-card" style={{ borderLeft: '3px solid var(--color-text-muted)' }}>
                    <div className="stat-card-label">Маркетплейс</div>
                    <div className="stat-card-value" style={{ color: 'var(--color-text)', fontSize: 22 }}>
                        {pct(t.mp_total_pct)}
                    </div>
                </div>
                <div className="stat-card" style={{ borderLeft: `3px solid ${marginColor}` }}>
                    <div className="stat-card-label">Маржа</div>
                    <div
                        className="stat-card-value"
                        style={{ color: marginColor, fontSize: 22, display: 'flex', alignItems: 'baseline' }}
                    >
                        {pct(t.margin_pct)}
                        <MarginTrendArrow trend={t.margin_trend} />
                    </div>
                </div>
            </div>

            {/* Tax-not-configured notice */}
            {!data.has_tax_settings && (
                <div
                    className="glass-card"
                    style={{
                        padding: '10px 14px',
                        marginBottom: 16,
                        fontSize: 13,
                        color: 'var(--color-text-dim)',
                    }}
                >
                    ⚠ Налоги не настроены — добавьте ставки в настройках проекта
                </div>
            )}

            {/* Table card */}
            <div className="glass-card" style={{ padding: 0, overflow: 'auto' }}>
                <div
                    style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        padding: '12px 16px',
                        borderBottom: '1px solid var(--color-border)',
                    }}
                >
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--color-text)' }}>
                        Разложение по категориям
                    </div>
                    <button className="btn btn-secondary btn-sm" onClick={onExport}>
                        📥 Excel
                    </button>
                </div>

                <CostDnaTable data={data} />
            </div>
        </>
    );
}

/* ─── Table ───────────────────────────────────────────────────────────── */

function CostDnaTable({ data }: { data: CostDnaResponse }) {
    const { categories, totals, has_tax_settings } = data;

    return (
        <table
            className="data-table"
            style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 0 }}
        >
            <thead>
                {/* Row 1: group headers */}
                <tr>
                    <th
                        rowSpan={2}
                        style={{
                            ...thBase,
                            textAlign: 'left',
                            position: 'sticky',
                            left: 0,
                            zIndex: 3,
                            minWidth: 180,
                        }}
                    >
                        Категория
                    </th>
                    <th rowSpan={2} style={thBase}>Доля %</th>
                    <th rowSpan={2} style={thBase}>Выручка</th>
                    <th colSpan={5} style={thBase}>Себестоимость</th>
                    <th colSpan={6} style={thBase}>Маркетплейс</th>
                    <th
                        rowSpan={2}
                        style={thBase}
                        title={
                            has_tax_settings
                                ? undefined
                                : 'Налоги не настроены — добавьте ставки в настройках проекта'
                        }
                    >
                        <span style={{ color: has_tax_settings ? undefined : 'var(--color-text-dim)' }}>
                            Налоги
                        </span>
                    </th>
                    <th colSpan={2} style={thBase}>Маржа</th>
                </tr>
                {/* Row 2: sub-headers */}
                <tr>
                    {/* Cost components */}
                    <th style={thBase}>Фабрика</th>
                    <th style={thBase}>Пошлина</th>
                    <th style={thBase}>Доставка</th>
                    <th style={thBase}>НДС</th>
                    <th style={thBase}>Итого</th>
                    {/* Marketplace components */}
                    <th style={thBase}>Комиссия</th>
                    <th style={thBase}>Логистика</th>
                    <th style={thBase}>Хранение</th>
                    <th style={thBase}>Реклама</th>
                    <th style={thBase}>Прочее</th>
                    <th style={thBase}>Итого</th>
                    {/* Margin sub-headers */}
                    <th style={thBase}>%</th>
                    <th style={thBase}>Тренд</th>
                </tr>
            </thead>

            <tbody>
                {categories.map((c) => (
                    <CategoryRow key={c.category} c={c} />
                ))}
            </tbody>

            <tfoot>
                <TotalsRow t={totals} />
            </tfoot>
        </table>
    );
}

/* ─── Category row ────────────────────────────────────────────────────── */

function CategoryRow({ c }: { c: CostDnaCategory }) {
    const labelCellStyle: React.CSSProperties = {
        padding: '8px 12px',
        textAlign: 'left',
        position: 'sticky',
        left: 0,
        background: 'var(--color-bg-card)',
        borderBottom: '1px solid var(--color-border)',
        fontSize: 13,
        fontWeight: 500,
        whiteSpace: 'nowrap',
    };

    const marginColor =
        c.margin_trend === 'up'
            ? 'var(--color-success)'
            : c.margin_trend === 'down'
                ? 'var(--color-danger)'
                : 'var(--color-text)';

    return (
        <tr>
            <td style={labelCellStyle}>{c.category}</td>
            <td style={cellStyle}>{pct(c.revenue_share_pct)}</td>
            <td style={cellStyle}>{formatNumber(c.revenue)}</td>

            {/* Cost block */}
            {c.has_cost_data ? (
                <>
                    <td style={cellStyle}>{pct(c.cost_factory_pct)}</td>
                    <td style={cellStyle}>{pct(c.cost_duty_pct)}</td>
                    <td style={cellStyle}>{pct(c.cost_delivery_pct)}</td>
                    <td style={cellStyle}>{pct(c.cost_vat_pct)}</td>
                    <td style={{ ...cellStyle, fontWeight: 600 }}>{pct(c.cost_total_pct)}</td>
                </>
            ) : (
                <td
                    colSpan={5}
                    style={{
                        ...cellStyle,
                        textAlign: 'center',
                        color: 'var(--color-text-dim)',
                        fontStyle: 'italic',
                    }}
                >
                    нет данных для прогноза
                </td>
            )}

            {/* Marketplace block */}
            <td style={cellStyle}>{pct(c.mp_commission_pct)}</td>
            <td style={cellStyle}>{pct(c.mp_logistics_pct)}</td>
            <td style={cellStyle}>{pct(c.mp_storage_pct)}</td>
            <td style={cellStyle}>{pct(c.mp_advertising_pct)}</td>
            <td style={cellStyle}>{pct(c.mp_other_pct)}</td>
            <td style={{ ...cellStyle, fontWeight: 600 }}>{pct(c.mp_total_pct)}</td>

            {/* Tax */}
            <td style={cellStyle}>{pct(c.tax_pct)}</td>

            {/* Margin */}
            {c.has_cost_data && c.margin_pct != null ? (
                <>
                    <td style={{ ...cellStyle, color: marginColor, fontWeight: 600 }}>
                        {pct(c.margin_pct)}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'center' }}>
                        <MarginTrendArrow trend={c.margin_trend} />
                    </td>
                </>
            ) : (
                <td
                    colSpan={2}
                    style={{
                        ...cellStyle,
                        textAlign: 'center',
                        color: 'var(--color-text-dim)',
                        fontStyle: 'italic',
                    }}
                >
                    нет данных для прогноза
                </td>
            )}
        </tr>
    );
}

/* ─── Totals row ──────────────────────────────────────────────────────── */

function TotalsRow({ t }: { t: CostDnaTotals }) {
    const totalCellStyle: React.CSSProperties = {
        ...cellStyle,
        fontWeight: 700,
        background: 'var(--color-bg-card)',
        borderTop: '2px solid var(--color-border)',
        position: 'sticky',
        bottom: 0,
    };

    const labelCellStyle: React.CSSProperties = {
        ...totalCellStyle,
        textAlign: 'left',
        padding: '10px 12px',
        position: 'sticky',
        left: 0,
        zIndex: 2,
    };

    const marginColor =
        t.margin_trend === 'up'
            ? 'var(--color-success)'
            : t.margin_trend === 'down'
                ? 'var(--color-danger)'
                : 'var(--color-text)';

    return (
        <tr>
            <td style={labelCellStyle}>ИТОГО</td>
            <td style={totalCellStyle}>100.00%</td>
            <td style={totalCellStyle}>{formatNumber(t.revenue)}</td>
            <td style={totalCellStyle}>{pct(t.cost_factory_pct)}</td>
            <td style={totalCellStyle}>{pct(t.cost_duty_pct)}</td>
            <td style={totalCellStyle}>{pct(t.cost_delivery_pct)}</td>
            <td style={totalCellStyle}>{pct(t.cost_vat_pct)}</td>
            <td style={totalCellStyle}>{pct(t.cost_total_pct)}</td>
            <td style={totalCellStyle}>{pct(t.mp_commission_pct)}</td>
            <td style={totalCellStyle}>{pct(t.mp_logistics_pct)}</td>
            <td style={totalCellStyle}>{pct(t.mp_storage_pct)}</td>
            <td style={totalCellStyle}>{pct(t.mp_advertising_pct)}</td>
            <td style={totalCellStyle}>{pct(t.mp_other_pct)}</td>
            <td style={totalCellStyle}>{pct(t.mp_total_pct)}</td>
            <td style={totalCellStyle}>{pct(t.tax_pct)}</td>
            <td style={{ ...totalCellStyle, color: marginColor }}>{pct(t.margin_pct)}</td>
            <td style={{ ...totalCellStyle, textAlign: 'center' }}>
                <MarginTrendArrow trend={t.margin_trend} />
            </td>
        </tr>
    );
}
