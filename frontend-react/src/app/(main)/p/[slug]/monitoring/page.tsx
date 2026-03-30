'use client';

import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber } from '@/lib/utils';
import type { MonitoringOverview, MonitoringSyncLogEntry, SyncTypeStatus, SchedulerJobInfo } from '@/types/api';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

const SYNC_TYPE_LABELS: Record<string, string> = {
    funnel_auto: 'Воронка (авто)',
    backfill: 'Бэкфилл',
    ad_resync: 'Реклама (ресинк)',
    sales: 'Продажи',
    orders: 'Заказы',
    finance: 'Финансы',
    nomenclature: 'Номенклатура',
    wb_finance_weekly: 'Финансы (нед.)',
    wb_finance_daily: 'Финансы (днев.)',
    fbo_supplies: 'Поставки FBO',
    warehouse_stocks: 'Остатки WB',
    ad_campaigns: 'Рекл. кампании',
    ad_budgets: 'Бюджеты РК',
    funnel_hourly: 'Воронка (час)',
};

function getSyncTypeLabel(syncType: string): string {
    return SYNC_TYPE_LABELS[syncType] || syncType;
}

function StatusBadge({ status }: { status: string | null }) {
    if (!status) return <span className="badge badge-secondary">—</span>;
    const cls = status === 'OK' ? 'badge-success'
        : status === 'RUNNING' ? 'badge-warning'
        : status === 'ERROR' || status === 'TIMEOUT' ? 'badge-danger'
        : status === 'STALE' ? 'badge-secondary'
        : 'badge-secondary';
    return <span className={`badge ${cls}`}>{status}</span>;
}

function TimeAgo({ dateStr }: { dateStr: string | null }) {
    if (!dateStr) return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
    const d = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    const diffH = Math.floor(diffMin / 60);
    const diffD = Math.floor(diffH / 24);

    let ago: string;
    if (diffMin < 1) ago = 'только что';
    else if (diffMin < 60) ago = `${diffMin} мин назад`;
    else if (diffH < 24) ago = `${diffH} ч назад`;
    else ago = `${diffD} дн назад`;

    return (
        <span title={formatDateTime(dateStr)} style={{ fontSize: 13 }}>
            {ago}
        </span>
    );
}

function SuccessRate({ ok, total }: { ok: number; total: number }) {
    if (total === 0) return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
    const pct = Math.round((ok / total) * 100);
    const color = pct === 100 ? 'var(--color-success)' : pct >= 80 ? 'var(--color-warning)' : 'var(--color-danger)';
    return <span style={{ color, fontWeight: 600 }}>{pct}%</span>;
}

// ─── Overview Cards ─────────────────────────────────────────────────────────

function OverviewCards({ overview }: { overview: MonitoringOverview }) {
    const running = overview.sync_types.filter(s => s.is_running).length;
    const errors = overview.total_errors_24h;
    const total = overview.total_syncs_24h;
    const schedulerOk = overview.scheduler.running;

    return (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16, marginBottom: 24 }}>
            <div className="glass-card" style={{ padding: 20 }}>
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>Синхронизаций (24ч)</div>
                <div style={{ fontSize: 28, fontWeight: 700 }}>{total}</div>
            </div>
            <div className="glass-card" style={{ padding: 20 }}>
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>Ошибок (24ч)</div>
                <div style={{ fontSize: 28, fontWeight: 700, color: errors > 0 ? 'var(--color-danger)' : 'var(--color-success)' }}>{errors}</div>
            </div>
            <div className="glass-card" style={{ padding: 20 }}>
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>Сейчас запущено</div>
                <div style={{ fontSize: 28, fontWeight: 700, color: running > 0 ? 'var(--color-warning)' : 'var(--color-text)' }}>{running}</div>
            </div>
            <div className="glass-card" style={{ padding: 20 }}>
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>Планировщик</div>
                <div style={{ fontSize: 28, fontWeight: 700, color: schedulerOk ? 'var(--color-success)' : 'var(--color-danger)' }}>
                    {schedulerOk ? 'OK' : 'OFF'}
                </div>
            </div>
        </div>
    );
}

// ─── Sync Types Table ───────────────────────────────────────────────────────

function SyncTypesTable({ syncTypes }: { syncTypes: SyncTypeStatus[] }) {
    if (syncTypes.length === 0) {
        return (
            <div className="glass-card" style={{ marginBottom: 24 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>Статус синхронизаций</h3>
                <div className="empty-state"><div className="empty-state-text">Нет данных о синхронизациях</div></div>
            </div>
        );
    }

    return (
        <div style={{ marginBottom: 24 }}>
            <TanStackDataTable
                title="Статус синхронизаций"
                columns={[
                    { key: 'service', label: 'Сервис', render: (v: any) => <span className="badge badge-info">{v}</span> },
                    { key: 'sync_type', label: 'Тип', render: (v: any) => <span style={{ fontWeight: 500 }}>{getSyncTypeLabel(v)}</span> },
                    { key: 'last_status', label: 'Статус', render: (_v: any, row: any) => row.is_running ? <span className="badge badge-warning" style={{ animation: 'pulse 2s infinite' }}>RUNNING</span> : <StatusBadge status={row.last_status} /> },
                    { key: 'last_ok_at', label: 'Последний OK', render: (v: any) => <TimeAgo dateStr={v} /> },
                    { key: 'total_24h', label: 'За 24ч' },
                    { key: 'ok_24h', label: 'Успешность', render: (_v: any, row: any) => <SuccessRate ok={row.ok_24h} total={row.total_24h} /> },
                    { key: 'avg_duration_sec', label: 'Ср. время', render: (v: any) => <span style={{ fontSize: 13 }}>{v ? `${v}с` : '—'}</span> },
                    { key: 'last_error_msg', label: 'Ошибка', render: (v: any) => <span style={{ color: 'var(--color-danger)', fontSize: 12, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block' }}>{v || '—'}</span> },
                ]}
                data={syncTypes}
                enableSorting
                enablePagination={false}
            />
        </div>
    );
}

// ─── Scheduler Table ────────────────────────────────────────────────────────

function SchedulerTable({ jobs, running }: { jobs: SchedulerJobInfo[]; running: boolean }) {
    return (
        <div style={{ marginBottom: 24 }}>
            <TanStackDataTable
                title="Планировщик задач"
                actions={<StatusBadge status={running ? 'OK' : 'ERROR'} />}
                columns={[
                    { key: 'id', label: 'ID задачи', render: (v: any) => <code style={{ fontSize: 12 }}>{v}</code> },
                    { key: 'name', label: 'Название', render: (v: any) => <span style={{ fontWeight: 500 }}>{v}</span> },
                    { key: 'next_run', label: 'Следующий запуск', render: (v: any) => <span style={{ fontSize: 13 }}>{v ? formatDateTime(v) : <span style={{ color: 'var(--color-text-dim)' }}>завершена</span>}</span> },
                ]}
                data={jobs}
                emptyText="Планировщик не запущен или нет задач"
                enableSorting
                enablePagination={false}
            />
        </div>
    );
}

// ─── Sync History Table ─────────────────────────────────────────────────────

function SyncHistoryTable({
    logs,
    loading,
    filters,
    onFilterChange,
    syncTypes,
}: {
    logs: MonitoringSyncLogEntry[];
    loading: boolean;
    filters: { service?: string; sync_type?: string; status?: string };
    onFilterChange: (f: { service?: string; sync_type?: string; status?: string }) => void;
    syncTypes: SyncTypeStatus[];
}) {
    const services = [...new Set(syncTypes.map(s => s.service))];
    const types = [...new Set(syncTypes.map(s => s.sync_type))];

    const filterActions = (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <select
                className="form-input"
                style={{ width: 'auto', minWidth: 140 }}
                value={filters.service || ''}
                onChange={e => onFilterChange({ ...filters, service: e.target.value || undefined })}
            >
                <option value="">Все сервисы</option>
                {services.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <select
                className="form-input"
                style={{ width: 'auto', minWidth: 160 }}
                value={filters.sync_type || ''}
                onChange={e => onFilterChange({ ...filters, sync_type: e.target.value || undefined })}
            >
                <option value="">Все типы</option>
                {types.map(t => <option key={t} value={t}>{getSyncTypeLabel(t)}</option>)}
            </select>
            <select
                className="form-input"
                style={{ width: 'auto', minWidth: 120 }}
                value={filters.status || ''}
                onChange={e => onFilterChange({ ...filters, status: e.target.value || undefined })}
            >
                <option value="">Все статусы</option>
                <option value="OK">OK</option>
                <option value="ERROR">ERROR</option>
                <option value="RUNNING">RUNNING</option>
                <option value="STALE">STALE</option>
            </select>
        </div>
    );

    return (
        <div>
            <TanStackDataTable
                title="История синхронизаций"
                actions={filterActions}
                columns={[
                    { key: 'started_at', label: 'Время', render: (v: any) => <span style={{ fontSize: 13, whiteSpace: 'nowrap' }}>{formatDateTime(v)}</span> },
                    { key: 'service', label: 'Сервис', render: (v: any) => <span className="badge badge-info">{v}</span> },
                    { key: 'sync_type', label: 'Тип', render: (v: any) => <>{getSyncTypeLabel(v)}</> },
                    { key: 'status', label: 'Статус', render: (v: any) => <StatusBadge status={v} /> },
                    { key: 'duration_sec', label: 'Длительность', render: (v: any) => <span style={{ fontSize: 13 }}>{v ? `${v}с` : '—'}</span> },
                    { key: 'rows_fetched', label: 'Получено', format: 'number' as const },
                    { key: 'rows_inserted', label: 'Вставлено', format: 'number' as const },
                    { key: 'error_msg', label: 'Ошибка', render: (v: any) => <span style={{ color: 'var(--color-danger)', fontSize: 12, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block' }} title={v || undefined}>{v || '—'}</span> },
                ]}
                data={logs}
                loading={loading}
                emptyText="Нет записей"
                enableSorting
                enablePagination={false}
            />
        </div>
    );
}

// ─── Main Page ──────────────────────────────────────────────────────────────

export default function MonitoringPage() {
    const [overview, setOverview] = useState<MonitoringOverview | null>(null);
    const [logs, setLogs] = useState<MonitoringSyncLogEntry[]>([]);
    const [logsLoading, setLogsLoading] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [filters, setFilters] = useState<{ service?: string; sync_type?: string; status?: string }>({});
    const [autoRefresh, setAutoRefresh] = useState(true);

    const loadOverview = useCallback(async () => {
        try {
            const data = await api.getMonitoringOverview();
            setOverview(data);
            setError('');
        } catch (e: any) {
            setError(e.message);
        }
        setLoading(false);
    }, []);

    const loadLogs = useCallback(async () => {
        setLogsLoading(true);
        try {
            const data = await api.getMonitoringSyncLog({
                ...filters,
                limit: 50,
            });
            setLogs(data);
        } catch { }
        setLogsLoading(false);
    }, [filters]);

    useEffect(() => {
        loadOverview();
        loadLogs();
    }, [loadOverview, loadLogs]);

    // Auto-refresh every 15s
    useEffect(() => {
        if (!autoRefresh) return;
        const interval = setInterval(() => {
            loadOverview();
            loadLogs();
        }, 15000);
        return () => clearInterval(interval);
    }, [autoRefresh, loadOverview, loadLogs]);

    const handleFilterChange = (f: typeof filters) => {
        setFilters(f);
    };

    if (loading) {
        return <div style={{ padding: 40, color: 'var(--color-text-muted)' }}>Загрузка мониторинга...</div>;
    }

    if (error && !overview) {
        return <div className="auth-error" style={{ margin: 40 }}>{error}</div>;
    }

    return (
        <div>
            <div className="table-toolbar" style={{ marginBottom: 24 }}>
                <div>
                    <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 4 }}>Мониторинг</h1>
                    <p style={{ fontSize: 14, color: 'var(--color-text-muted)', margin: 0 }}>
                        Статус синхронизаций и планировщика
                    </p>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                        <input
                            type="checkbox"
                            checked={autoRefresh}
                            onChange={e => setAutoRefresh(e.target.checked)}
                        />
                        Авто-обновление (15с)
                    </label>
                    <button className="btn btn-primary btn-sm" onClick={() => { loadOverview(); loadLogs(); }}>
                        Обновить
                    </button>
                </div>
            </div>

            {overview && (
                <>
                    <OverviewCards overview={overview} />
                    <SyncTypesTable syncTypes={overview.sync_types} />
                    <SchedulerTable jobs={overview.scheduler.jobs} running={overview.scheduler.running} />
                </>
            )}

            <SyncHistoryTable
                logs={logs}
                loading={logsLoading}
                filters={filters}
                onFilterChange={handleFilterChange}
                syncTypes={overview?.sync_types || []}
            />
        </div>
    );
}
