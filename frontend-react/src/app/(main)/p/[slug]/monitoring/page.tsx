'use client';

import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber } from '@/lib/utils';
import type { MonitoringOverview, MonitoringSyncLogEntry, SyncTypeStatus, SchedulerJobInfo } from '@/types/api';

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
        <div className="glass-card" style={{ marginBottom: 24 }}>
            <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>Статус синхронизаций</h3>
            <div style={{ overflowX: 'auto' }}>
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>Сервис</th>
                            <th>Тип</th>
                            <th>Статус</th>
                            <th>Последний OK</th>
                            <th>За 24ч</th>
                            <th>Успешность</th>
                            <th>Ср. время</th>
                            <th>Ошибка</th>
                        </tr>
                    </thead>
                    <tbody>
                        {syncTypes.map((s, i) => (
                            <tr key={i} style={s.is_running ? { background: 'rgba(245, 158, 11, 0.05)' } : undefined}>
                                <td><span className="badge badge-info">{s.service}</span></td>
                                <td style={{ fontWeight: 500 }}>{getSyncTypeLabel(s.sync_type)}</td>
                                <td>
                                    {s.is_running ? (
                                        <span className="badge badge-warning" style={{ animation: 'pulse 2s infinite' }}>RUNNING</span>
                                    ) : (
                                        <StatusBadge status={s.last_status} />
                                    )}
                                </td>
                                <td><TimeAgo dateStr={s.last_ok_at} /></td>
                                <td>{s.total_24h}</td>
                                <td><SuccessRate ok={s.ok_24h} total={s.total_24h} /></td>
                                <td style={{ fontSize: 13 }}>
                                    {s.avg_duration_sec ? `${s.avg_duration_sec}с` : '—'}
                                </td>
                                <td style={{ color: 'var(--color-danger)', fontSize: 12, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {s.last_error_msg || '—'}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

// ─── Scheduler Table ────────────────────────────────────────────────────────

function SchedulerTable({ jobs, running }: { jobs: SchedulerJobInfo[]; running: boolean }) {
    return (
        <div className="glass-card" style={{ marginBottom: 24 }}>
            <div className="table-toolbar">
                <h3 style={{ fontSize: 16, fontWeight: 600 }}>Планировщик задач</h3>
                <StatusBadge status={running ? 'OK' : 'ERROR'} />
            </div>
            {jobs.length === 0 ? (
                <div className="empty-state"><div className="empty-state-text">Планировщик не запущен или нет задач</div></div>
            ) : (
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>ID задачи</th>
                            <th>Название</th>
                            <th>Следующий запуск</th>
                        </tr>
                    </thead>
                    <tbody>
                        {jobs.map(j => (
                            <tr key={j.id}>
                                <td><code style={{ fontSize: 12 }}>{j.id}</code></td>
                                <td style={{ fontWeight: 500 }}>{j.name}</td>
                                <td style={{ fontSize: 13 }}>
                                    {j.next_run ? formatDateTime(j.next_run) : <span style={{ color: 'var(--color-text-dim)' }}>завершена</span>}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}
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

    return (
        <div className="glass-card">
            <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>История синхронизаций</h3>
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
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

            {loading ? (
                <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
            ) : logs.length === 0 ? (
                <div className="empty-state"><div className="empty-state-text">Нет записей</div></div>
            ) : (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Время</th>
                                <th>Сервис</th>
                                <th>Тип</th>
                                <th>Статус</th>
                                <th>Длительность</th>
                                <th>Получено</th>
                                <th>Вставлено</th>
                                <th>Ошибка</th>
                            </tr>
                        </thead>
                        <tbody>
                            {logs.map(l => (
                                <tr key={l.id}>
                                    <td style={{ fontSize: 13, whiteSpace: 'nowrap' }}>{formatDateTime(l.started_at)}</td>
                                    <td><span className="badge badge-info">{l.service}</span></td>
                                    <td>{getSyncTypeLabel(l.sync_type)}</td>
                                    <td><StatusBadge status={l.status} /></td>
                                    <td style={{ fontSize: 13 }}>{l.duration_sec ? `${l.duration_sec}с` : '—'}</td>
                                    <td>{formatNumber(l.rows_fetched)}</td>
                                    <td>{formatNumber(l.rows_inserted)}</td>
                                    <td style={{ color: 'var(--color-danger)', fontSize: 12, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={l.error_msg || undefined}>
                                        {l.error_msg || '—'}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
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
