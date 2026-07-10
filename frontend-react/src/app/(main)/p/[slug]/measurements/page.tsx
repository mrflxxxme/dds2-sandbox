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

const PENALTY_COLS: Column[] = [
    { key: 'penalty_date', label: 'Дата удержания', render: (v) => formatDate(v),
      getValue: (r: MeasurementPenalty) => r.penalty_date ?? '' },
    { key: 'dim_id', label: '№ замера ВБ' },
    { key: 'nm_id', label: 'Артикул (nmID)' },
    { key: 'brand', label: 'Бренд', render: (v) => v ?? '—' },
    { key: 'subject_name', label: 'Предмет' },
    { key: 'dec_dims', label: 'Заявлено Д×Ш×В', align: 'center',
      render: (_v, r: MeasurementPenalty) => dims(r.dec_length, r.dec_width, r.dec_height),
      exportValue: (r: MeasurementPenalty) => dims(r.dec_length, r.dec_width, r.dec_height) },
    { key: 'act_dims', label: 'Замер WB Д×Ш×В', align: 'center',
      render: (_v, r: MeasurementPenalty) => dims(r.act_length, r.act_width, r.act_height),
      exportValue: (r: MeasurementPenalty) => dims(r.act_length, r.act_width, r.act_height) },
    { key: 'prc_over', label: 'Превышение, %', align: 'right',
      render: (v) => (v == null ? '—' : formatNumber(num(v), 1)),
      getValue: (r: MeasurementPenalty) => num(r.prc_over) },
    { key: 'units_count', label: 'Единиц', align: 'right', render: (v) => v ?? '—' },
    { key: 'penalty_amount', label: 'Удержание, ₽', align: 'right',
      render: (v) => formatNumber(num(v), 2), getValue: (r: MeasurementPenalty) => num(r.penalty_amount) },
    { key: 'reversal_amount', label: 'Сторно, ₽', align: 'right',
      render: (v) => (num(v) ? formatNumber(num(v), 2) : '—'),
      getValue: (r: MeasurementPenalty) => num(r.reversal_amount) },
    { key: 'is_valid', label: 'Актуально', align: 'center',
      render: (v) => (v ? <span className="badge badge-success">да</span>
                         : <span className="badge badge-secondary">нет</span>),
      exportValue: (r: MeasurementPenalty) => (r.is_valid ? 'да' : 'нет') },
    { key: 'photo_urls', label: 'Фото', align: 'center',
      render: (_v, r: MeasurementPenalty) => <PhotoLinks urls={r.photo_urls} />,
      exportValue: (r: MeasurementPenalty) => (r.photo_urls || []).join(' ') },
];

const SUMMARY_COLS: Column[] = [
    { key: 'nm_id', label: 'Артикул (nmID)' },
    { key: 'brand', label: 'Бренд', render: (v) => v ?? '—' },
    { key: 'subject_name', label: 'Предмет', render: (v) => v ?? '—' },
    { key: 'measurements_count', label: 'Замеров', align: 'right' },
    { key: 'penalties_count', label: 'Начислений', align: 'right' },
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
                    emptyText="Нет удержаний за период. Нажмите «Синхронизировать»."
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
