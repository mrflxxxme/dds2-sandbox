'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import PageHeader from '@/components/PageHeader';
import TanStackDataTable from '@/components/TanStackDataTable';
import KpiCard from '@/components/KpiCard';
import MeasurementHistory from './MeasurementHistory';
import type { Column } from '@/components/DataTable';
import type {
    WarehouseMeasurement,
    MeasurementPenalty,
    WarehouseMeasurementListResponse,
    MeasurementPenaltyListResponse,
    MeasurementFiltersResponse,
    PenaltyArticleSummaryRow,
    PenaltyArticleSummaryResponse,
} from '@/types/api';

const TODAY = new Date().toISOString().slice(0, 10);
// Замеры WB появились ~май 2025 — по умолчанию показываем всю историю.
const HISTORY_START = '2025-01-01';

type Tab = 'warehouse' | 'history' | 'penalties' | 'summary';

const num = (v: string | number | null | undefined) => (v == null ? 0 : Number(v));
const dims = (l: number | null, w: number | null, h: number | null) =>
    [l, w, h].every((x) => x == null) ? '—' : `${l ?? '—'}×${w ?? '—'}×${h ?? '—'}`;

/** Отклонение объёма замера от объёма карточки, %. null если объёма карточки/замера нет. */
const cardDeviation = (r: WarehouseMeasurement): number | null => {
    const card = r.card_volume == null ? null : Number(r.card_volume);
    const meas = r.volume == null ? null : Number(r.volume);
    if (card == null || card <= 0 || meas == null) return null;
    return ((meas - card) / card) * 100;
};

/** Отклонение с цветом: <10% — зелёным (норма), ≥10% — красным с ⚠️ (обратить внимание). */
function DeviationCell({ r }: { r: WarehouseMeasurement }) {
    const d = cardDeviation(r);
    if (d == null) return <span style={{ color: 'var(--color-dim)' }}>—</span>;
    const attention = Math.abs(d) >= 10;
    const sign = d > 0 ? '+' : d < 0 ? '−' : '';
    return (
        <span style={{ color: attention ? 'var(--color-danger)' : 'var(--color-success)', fontWeight: 600 }}>
            {attention ? '⚠️ ' : ''}{sign}{formatNumber(Math.abs(d), 1)}%
        </span>
    );
}

function PhotoLinks({ urls }: { urls: string[] | null }) {
    if (!urls || urls.length === 0) return <>—</>;
    return (
        <span style={{ display: 'inline-flex', gap: 8 }}>
            {urls.map((u, i) => (
                <a key={i} href={u} target="_blank" rel="noopener noreferrer"
                   className="badge badge-info" style={{ textDecoration: 'none' }}>
                    📷 {i + 1}
                </a>
            ))}
        </span>
    );
}

const WAREHOUSE_COLS: Column[] = [
    { key: 'measured_at', label: 'Дата замера', render: (v) => formatDate(v),
      getValue: (r: WarehouseMeasurement) => r.measured_at ?? '' },
    { key: 'dim_id', label: '№ замера ВБ' },
    { key: 'nm_id', label: 'Артикул (nmID)' },
    { key: 'brand', label: 'Бренд', render: (v) => v ?? '—' },
    { key: 'subject_name', label: 'Предмет' },
    { key: 'dims', label: 'Габариты Д×Ш×В, см', align: 'center',
      render: (_v, r: WarehouseMeasurement) => dims(r.length, r.width, r.height),
      exportValue: (r: WarehouseMeasurement) => dims(r.length, r.width, r.height) },
    { key: 'volume', label: 'Замер, л', align: 'right',
      render: (v) => formatNumber(num(v), 3), getValue: (r: WarehouseMeasurement) => num(r.volume) },
    { key: 'card_volume', label: 'Карточка, л', align: 'right',
      render: (v) => (v == null ? '—' : formatNumber(num(v), 3)),
      getValue: (r: WarehouseMeasurement) => num(r.card_volume) },
    { key: 'deviation', label: 'Отклонение, %', align: 'right',
      render: (_v, r: WarehouseMeasurement) => <DeviationCell r={r} />,
      getValue: (r: WarehouseMeasurement) => cardDeviation(r) ?? 0,
      exportValue: (r: WarehouseMeasurement) => {
          const d = cardDeviation(r);
          return d == null ? '—' : `${formatNumber(d, 1)}%`;
      } },
    { key: 'photo_urls', label: 'Фото', align: 'center',
      render: (_v, r: WarehouseMeasurement) => <PhotoLinks urls={r.photo_urls} />,
      exportValue: (r: WarehouseMeasurement) => (r.photo_urls || []).join(' ') },
];

// Отклонение литража (замер vs карточка), уже посчитанное на бэке.
const devVal = (v: string | null) => (v == null ? null : Number(v));

function DevBadge({ dev }: { dev: number | null }) {
    if (dev == null) return <span style={{ color: 'var(--color-dim)' }}>—</span>;
    if (Math.abs(dev) < 0.5) return <span style={{ color: 'var(--color-success)' }}>карточка ✓</span>;
    const attention = Math.abs(dev) >= 10;
    const sign = dev > 0 ? '+' : '−';
    return (
        <span style={{ color: attention ? 'var(--color-danger)' : 'var(--color-success)', fontWeight: 600 }}>
            {attention ? '⚠️ ' : ''}{sign}{formatNumber(Math.abs(dev), 1)}%
        </span>
    );
}

const volCol = (key: 'meas_volume' | 'card_volume', label: string): Column => ({
    key, label, align: 'right',
    render: (v) => (v == null ? '—' : formatNumber(num(v), 3)),
    getValue: (r: MeasurementPenalty | PenaltyArticleSummaryRow) =>
        num((r as MeasurementPenalty)[key]),
});

const devCol: Column = {
    key: 'deviation', label: 'Отклонение, %', align: 'right',
    render: (_v, r: MeasurementPenalty | PenaltyArticleSummaryRow) => <DevBadge dev={devVal(r.deviation)} />,
    getValue: (r: MeasurementPenalty | PenaltyArticleSummaryRow) => num(r.deviation),
    exportValue: (r: MeasurementPenalty | PenaltyArticleSummaryRow) =>
        r.deviation == null ? '—' : `${formatNumber(num(r.deviation), 1)}%`,
};

// «Удержания за габариты» — источник финотчёт (точные суммы), строка = артикул × день.
const PENALTY_COLS: Column[] = [
    { key: 'rr_dt', label: 'Дата начисления', render: (v) => formatDate(v),
      getValue: (r: MeasurementPenalty) => r.rr_dt ?? '' },
    { key: 'nm_id', label: 'Артикул (nmID)' },
    { key: 'brand', label: 'Бренд', render: (v) => v ?? '—' },
    { key: 'subject_name', label: 'Предмет', render: (v) => v ?? '—' },
    volCol('meas_volume', 'Замер, л'),
    volCol('card_volume', 'Карточка, л'),
    devCol,
    { key: 'penalty', label: 'Удержание, ₽', align: 'right',
      render: (v) => formatNumber(num(v), 2), getValue: (r: MeasurementPenalty) => num(r.penalty) },
    { key: 'reversal', label: 'Сторно, ₽', align: 'right',
      render: (v) => (num(v) ? formatNumber(num(v), 2) : '—'),
      getValue: (r: MeasurementPenalty) => num(r.reversal) },
    { key: 'net', label: 'Нетто, ₽', align: 'right',
      render: (v) => <strong>{formatNumber(num(v), 2)}</strong>,
      getValue: (r: MeasurementPenalty) => num(r.net) },
];

const SUMMARY_COLS: Column[] = [
    { key: 'nm_id', label: 'Артикул (nmID)' },
    { key: 'brand', label: 'Бренд', render: (v) => v ?? '—' },
    { key: 'subject_name', label: 'Предмет', render: (v) => v ?? '—' },
    { key: 'days_count', label: 'Дней', align: 'right' },
    volCol('meas_volume', 'Замер, л'),
    volCol('card_volume', 'Карточка, л'),
    devCol,
    { key: 'total_penalty', label: 'Удержания, ₽', align: 'right',
      render: (v) => formatNumber(num(v), 2), getValue: (r: PenaltyArticleSummaryRow) => num(r.total_penalty) },
    { key: 'total_reversal', label: 'Сторно, ₽', align: 'right',
      render: (v) => (num(v) ? formatNumber(num(v), 2) : '—'),
      getValue: (r: PenaltyArticleSummaryRow) => num(r.total_reversal) },
    { key: 'net', label: 'Итого нетто, ₽', align: 'right',
      render: (v) => <strong>{formatNumber(num(v), 2)}</strong>,
      getValue: (r: PenaltyArticleSummaryRow) => num(r.net) },
];

export default function MeasurementsPage() {
    const [tab, setTab] = useState<Tab>('warehouse');
    const [dateFrom, setDateFrom] = useState(HISTORY_START);
    const [dateTo, setDateTo] = useState(TODAY);
    const [brand, setBrand] = useState('');
    const [subject, setSubject] = useState('');
    const [searchInput, setSearchInput] = useState('');
    const [search, setSearch] = useState('');
    const [filterOpts, setFilterOpts] = useState<MeasurementFiltersResponse>({ brands: [], subjects: [] });

    // Дебаунс поиска (300мс), чтобы не дёргать API на каждый символ
    useEffect(() => {
        const t = setTimeout(() => setSearch(searchInput.trim()), 300);
        return () => clearTimeout(t);
    }, [searchInput]);

    const [warehouse, setWarehouse] = useState<WarehouseMeasurementListResponse | null>(null);
    const [penalties, setPenalties] = useState<MeasurementPenaltyListResponse | null>(null);
    const [summary, setSummary] = useState<PenaltyArticleSummaryResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [syncing, setSyncing] = useState(false);
    const [syncMsg, setSyncMsg] = useState('');

    const loadFilterOpts = useCallback(async () => {
        try {
            setFilterOpts(await api.getMeasurementFilters());
        } catch {
            // фильтры не критичны — молча оставляем пустыми
        }
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const filters = {
                dateFrom, dateTo, limit: 5000,
                brand: brand || undefined,
                subject: subject || undefined,
                search: search || undefined,
            };
            if (tab === 'warehouse' || tab === 'history') {
                setWarehouse(await api.getWarehouseMeasurements(filters));
            } else if (tab === 'penalties') {
                setPenalties(await api.getMeasurementPenalties(filters));
            } else {
                setSummary(await api.getPenaltySummary(filters));
            }
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [tab, dateFrom, dateTo, brand, subject, search]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => { loadFilterOpts(); }, [loadFilterOpts]);

    const handleSync = async () => {
        setSyncing(true);
        setSyncMsg('');
        setError('');
        try {
            const r = await api.syncMeasurements(90);
            setSyncMsg(`Синхронизировано: замеры ${r.warehouse}, удержания ${r.penalties}`);
            await Promise.all([load(), loadFilterOpts()]);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка синхронизации');
        } finally {
            setSyncing(false);
        }
    };

    const p = penalties;
    const netPenalty = p ? num(p.total_penalty) + num(p.total_reversal) : 0;

    return (
        <div className="animate-in">
            <PageHeader
                title="Замеры"
                icon="📐"
                subtitle="Контрольные замеры складов WB и удержания за занижение габаритов"
                actions={
                    <button className="btn btn-primary btn-sm" onClick={handleSync} disabled={syncing}>
                        {syncing ? 'Синхронизация…' : '🔄 Синхронизировать'}
                    </button>
                }
            />

            {/* Tabs */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
                <button className={`btn btn-sm ${tab === 'warehouse' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setTab('warehouse')}>
                    📐 Замеры склада
                </button>
                <button className={`btn btn-sm ${tab === 'history' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setTab('history')}>
                    📈 История замеров
                </button>
                <button className={`btn btn-sm ${tab === 'penalties' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setTab('penalties')}>
                    ⚠️ Удержания за габариты
                </button>
                <button className={`btn btn-sm ${tab === 'summary' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setTab('summary')}>
                    📊 Сводка по артикулам
                </button>
            </div>

            {/* Search */}
            <div style={{ marginBottom: 12 }}>
                <input
                    type="search"
                    className="form-input"
                    style={{ width: 320, maxWidth: '100%' }}
                    placeholder="🔍 Поиск по артикулу или № замера"
                    value={searchInput}
                    onChange={(e) => setSearchInput(e.target.value)}
                />
            </div>

            {/* Filters */}
            <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 20, flexWrap: 'wrap' }}>
                <label style={{ fontSize: 13, color: 'var(--color-muted)' }}>Период:</label>
                <input type="date" className="form-input" style={{ width: 150 }} value={dateFrom}
                       onChange={(e) => setDateFrom(e.target.value)} />
                <span style={{ color: 'var(--color-muted)' }}>—</span>
                <input type="date" className="form-input" style={{ width: 150 }} value={dateTo}
                       onChange={(e) => setDateTo(e.target.value)} />

                <select className="form-input" style={{ minWidth: 150 }} value={brand}
                        onChange={(e) => setBrand(e.target.value)}>
                    <option value="">Все бренды</option>
                    {filterOpts.brands.map((b) => <option key={b} value={b}>{b}</option>)}
                </select>
                <select className="form-input" style={{ minWidth: 150 }} value={subject}
                        onChange={(e) => setSubject(e.target.value)}>
                    <option value="">Все предметы</option>
                    {filterOpts.subjects.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
                {(brand || subject || searchInput) && (
                    <button className="btn btn-secondary btn-sm"
                            onClick={() => { setBrand(''); setSubject(''); setSearchInput(''); }}>
                        Сбросить
                    </button>
                )}
                {syncMsg && <span style={{ fontSize: 13, color: 'var(--color-success)' }}>{syncMsg}</span>}
            </div>

            {error && (
                <div className="glass-card" style={{ marginBottom: 20, color: 'var(--color-danger)' }}>
                    {error}{' '}
                    <button className="btn btn-secondary btn-sm" onClick={load} style={{ marginLeft: 12 }}>
                        Повторить
                    </button>
                </div>
            )}

            {tab === 'penalties' && p && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                              gap: 16, marginBottom: 20 }}>
                    <KpiCard label="Удержаний, ₽" value={formatNumber(num(p.total_penalty), 2)} />
                    <KpiCard label="Сторнировано, ₽" value={formatNumber(num(p.total_reversal), 2)} />
                    <KpiCard label="Итого нетто, ₽" value={formatNumber(netPenalty, 2)} />
                    <KpiCard label="Записей за период" value={p.total} />
                </div>
            )}

            {tab === 'summary' && summary && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                              gap: 16, marginBottom: 20 }}>
                    <KpiCard label="Артикулов" value={summary.articles} />
                    <KpiCard label="Удержаний, ₽" value={formatNumber(num(summary.total_penalty), 2)} />
                    <KpiCard label="Сторнировано, ₽" value={formatNumber(num(summary.total_reversal), 2)} />
                    <KpiCard label="Итого нетто, ₽" value={formatNumber(num(summary.net), 2)} />
                </div>
            )}

            {tab === 'warehouse' && (
                <TanStackDataTable
                    columns={WAREHOUSE_COLS}
                    data={warehouse?.items ?? []}
                    loading={loading}
                    title="Замеры склада"
                    exportName="Замеры склада WB"
                    emptyIcon="📐"
                    emptyText="Нет замеров за период. Нажмите «Синхронизировать»."
                    pageSize={50}
                />
            )}
            {tab === 'history' && (
                <MeasurementHistory items={warehouse?.items ?? []} loading={loading} />
            )}
            {tab === 'penalties' && (
                <TanStackDataTable
                    columns={PENALTY_COLS}
                    data={penalties?.items ?? []}
                    loading={loading}
                    title="Удержания за габариты"
                    exportName="Удержания за габариты WB"
                    emptyIcon="⚠️"
                    emptyText="Нет удержаний за габариты за период."
                    pageSize={50}
                />
            )}
            {tab === 'summary' && (
                <TanStackDataTable
                    columns={SUMMARY_COLS}
                    data={summary?.items ?? []}
                    loading={loading}
                    title="Сводка удержаний по артикулам"
                    exportName="Сводка удержаний по артикулам"
                    emptyIcon="📊"
                    emptyText="Нет удержаний за период."
                    pageSize={50}
                />
            )}
        </div>
    );
}
