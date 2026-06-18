'use client';

/**
 * Вкладка «Связи и расхождения» страницы «Анализ сборки».
 *
 * Четыре блока:
 *  1. ⚠️ Расхождение наполнения — сборки, чей состав не сходится с привязанными ФФ-заявками.
 *  2. 🔗 Наши сборки без заявки ФФ — собрали, но ФФ-заявку не привязали.
 *  3. 🔗 Заявки ФФ без нашей сборки — ФФ ждёт, у нас сборки нет.
 *  4. 📦 Аномалии поставок FBO — сводка-счётчики (project-global), drill на /warehouse/fbo-supplies.
 *
 * Свой селект ФФ-склада сверху. Блоки 1–3 — таблицы с Excel-экспортом, блок 4 — мини-карточки.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { exportToExcel, formatDate, formatNumber } from '@/lib/utils';
import type {
    FfMismatchRow,
    LinkAnomaliesResponse,
    UnlinkedAssemblyRow,
    UnlinkedFfRow,
    Warehouse,
} from '@/types/api';

// ─── Config ─────────────────────────────────────────────────────────────────

const STATUS_BADGE: Record<string, { label: string; cls: string }> = {
    PENDING:          { label: 'В сборке',         cls: 'badge-info' },
    IN_PROGRESS:      { label: 'В сборке',         cls: 'badge-info' },
    READY:            { label: 'Готово',           cls: 'badge-success' },
    VEHICLE_ASSIGNED: { label: 'Машина назначена', cls: 'badge-info' },
    SHIPPED:          { label: 'Отгружена',        cls: 'badge-success' },
    DELIVERED:        { label: 'Принята WB',       cls: 'badge-success' },
    CLOSED:           { label: 'Закрыт',           cls: 'badge-warning' },
    CANCELLED:        { label: 'Отменена',         cls: 'badge-secondary' },
};

function statusBadge(status: string | null): { label: string; cls: string } {
    if (!status) return { label: '—', cls: 'badge-secondary' };
    return STATUS_BADGE[status] ?? { label: status, cls: 'badge-secondary' };
}

const MODE_LABEL: Record<FfMismatchRow['mode'], string> = {
    barcode: 'по ШК',
    total: 'по кол-ву',
};

// ─── UI atoms ───────────────────────────────────────────────────────────────

function SkeletonCard({ height = 96 }: { height?: number }) {
    return (
        <div
            className="glass-card"
            style={{
                height,
                background: 'linear-gradient(90deg, rgba(0,0,0,0.04) 0%, rgba(0,0,0,0.07) 50%, rgba(0,0,0,0.04) 100%)',
                backgroundSize: '200% 100%',
                animation: 'shimmer 1.4s linear infinite',
            }}
        />
    );
}

function StatusCell({ status }: { status: string | null }) {
    const badge = statusBadge(status);
    return <span className={`badge ${badge.cls}`} style={{ fontSize: 11 }}>{badge.label}</span>;
}

/** Шапка блока: иконка, заголовок, счётчик-бейдж, кнопка Excel. */
function BlockHeader({
    icon,
    title,
    count,
    color,
    onExport,
    note,
}: {
    icon: string;
    title: string;
    count: number;
    color: string;
    onExport?: () => void;
    note?: string;
}) {
    return (
        <div style={{ padding: '14px 20px 12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span>{icon}</span>
                <span style={{ fontSize: 14, fontWeight: 600 }}>{title}</span>
                <span
                    className="badge"
                    style={{
                        background: `color-mix(in srgb, ${color} 14%, transparent)`,
                        color,
                        fontSize: 12,
                        fontWeight: 700,
                    }}
                >
                    {formatNumber(count, 0)}
                </span>
                {onExport && (
                    <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        style={{ marginLeft: 'auto' }}
                        onClick={onExport}
                        disabled={count === 0}
                        title="Выгрузить эту секцию в Excel"
                    >
                        ⬇ Excel
                    </button>
                )}
            </div>
            {note && (
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 4 }}>{note}</div>
            )}
        </div>
    );
}

/** Пустое состояние внутри блока — «всё чисто». */
function BlockEmpty({ text }: { text: string }) {
    return (
        <div style={{ padding: '4px 20px 18px', fontSize: 13, color: 'var(--color-success)' }}>
            ✓ {text}
        </div>
    );
}

// ─── Tab ────────────────────────────────────────────────────────────────────

export default function LinkAnomaliesTab({ slug }: { slug: string }) {
    const [data, setData] = useState<LinkAnomaliesResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [reloadTick, setReloadTick] = useState(0);

    const [warehouseId, setWarehouseId] = useState<number | ''>('');
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);

    // ─── Load FF-warehouses (один раз) ────────────────────────────────────
    useEffect(() => {
        const controller = new AbortController();
        api.getWarehouses()
            .then(whs => {
                if (controller.signal.aborted) return;
                setWarehouses(whs.filter(w => w.warehouse_type === 'FULFILLMENT'));
            })
            .catch(() => {});
        return () => controller.abort();
    }, []);

    // ─── Load anomalies ───────────────────────────────────────────────────
    const load = useCallback(async (signal: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const resp = await api.getAssemblyLinkAnomalies({
                warehouse_ids: warehouseId ? String(warehouseId) : undefined,
            });
            if (signal.aborted) return;
            setData(resp);
        } catch (e: unknown) {
            if (signal.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (!signal.aborted) setLoading(false);
        }
    }, [warehouseId]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load, reloadTick]);

    // ─── Exports ──────────────────────────────────────────────────────────
    const exportMismatch = useCallback(() => {
        const rows = data?.ff_composition_mismatch ?? [];
        if (rows.length === 0) return;
        exportToExcel(
            rows.map(r => ({
                number: r.number,
                status: statusBadge(r.status).label,
                warehouse: r.warehouse_name || '',
                ff_requests: r.ff_request_numbers.join(', '),
                our_total: r.our_total,
                ff_total: r.ff_total,
                diff: r.diff,
                mode: MODE_LABEL[r.mode],
            })),
            'assembly_ff_composition_mismatch',
            [
                { key: 'number', label: '№' },
                { key: 'status', label: 'Статус' },
                { key: 'warehouse', label: 'Склад' },
                { key: 'ff_requests', label: 'Заявки ФФ' },
                { key: 'our_total', label: 'Наш шт' },
                { key: 'ff_total', label: 'ФФ шт' },
                { key: 'diff', label: 'Δ' },
                { key: 'mode', label: 'Режим' },
            ],
        );
    }, [data]);

    const exportAsmWithoutFf = useCallback(() => {
        const rows = data?.assemblies_without_ff ?? [];
        if (rows.length === 0) return;
        exportToExcel(
            rows.map(r => ({
                number: r.number,
                status: statusBadge(r.status).label,
                warehouse: r.warehouse_name || '',
                provider: r.provider || '',
                total_qty: r.total_qty,
                age_days: r.age_days,
            })),
            'assembly_without_ff',
            [
                { key: 'number', label: '№' },
                { key: 'status', label: 'Статус' },
                { key: 'warehouse', label: 'Склад' },
                { key: 'provider', label: 'Провайдер' },
                { key: 'total_qty', label: 'Шт' },
                { key: 'age_days', label: 'Дней без привязки' },
            ],
        );
    }, [data]);

    const exportFfWithoutAsm = useCallback(() => {
        const rows = data?.ff_without_assembly ?? [];
        if (rows.length === 0) return;
        exportToExcel(
            rows.map(r => ({
                provider: r.provider,
                number: r.number || '—',
                warehouse: r.warehouse_name || '',
                stage_title: r.stage_title || '',
                status: r.status || '',
                total_qty: r.total_qty ?? 0,
                created: r.external_created_at ? formatDate(r.external_created_at) : '',
            })),
            'ff_without_assembly',
            [
                { key: 'provider', label: 'Провайдер' },
                { key: 'number', label: '№' },
                { key: 'warehouse', label: 'Склад' },
                { key: 'stage_title', label: 'Стадия' },
                { key: 'status', label: 'Статус' },
                { key: 'total_qty', label: 'Шт' },
                { key: 'created', label: 'Создана' },
            ],
        );
    }, [data]);

    // ─── Derived ──────────────────────────────────────────────────────────
    const mismatch: FfMismatchRow[] = useMemo(() => data?.ff_composition_mismatch ?? [], [data]);
    const asmWithoutFf: UnlinkedAssemblyRow[] = useMemo(() => data?.assemblies_without_ff ?? [], [data]);
    const ffWithoutAsm: UnlinkedFfRow[] = useMemo(() => data?.ff_without_assembly ?? [], [data]);
    const fbo = data?.fbo;
    const fboHasAnomalies =
        !!fbo && (fbo.without_assembly_count > 0 || fbo.under_accepted_count > 0 || fbo.excess_count > 0);

    // ─── Render ───────────────────────────────────────────────────────────

    return (
        <div className="animate-in">
            {/* Свой селект ФФ-склада */}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 16 }}>
                <select
                    className="form-input"
                    style={{ width: 'auto', minWidth: 150 }}
                    value={warehouseId}
                    onChange={e => setWarehouseId(e.target.value ? Number(e.target.value) : '')}
                >
                    <option value="">Все склады</option>
                    {warehouses.map(w => (
                        <option key={w.id} value={w.id}>{w.name}</option>
                    ))}
                </select>
            </div>

            {/* Error */}
            {error && !loading && (
                <div className="glass-card" style={{ padding: 20, color: 'var(--color-danger)', marginBottom: 16 }}>
                    <div style={{ fontWeight: 600, marginBottom: 8 }}>Не удалось загрузить связи и расхождения</div>
                    <div style={{ fontSize: 13, marginBottom: 12 }}>{error}</div>
                    <button type="button" className="btn btn-danger btn-sm" onClick={() => setReloadTick(t => t + 1)}>
                        Повторить
                    </button>
                </div>
            )}

            {/* Loading */}
            {loading && !error && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    <SkeletonCard height={160} />
                    <SkeletonCard height={160} />
                    <SkeletonCard height={120} />
                </div>
            )}

            {/* Data */}
            {!loading && !error && data && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {/* 1. Расхождение наполнения */}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden', borderLeft: '3px solid var(--color-warning)' }}>
                        <BlockHeader
                            icon="⚠️"
                            title="Расхождение наполнения"
                            count={mismatch.length}
                            color="var(--color-warning)"
                            onExport={exportMismatch}
                            note="Наш состав сборки не сходится с суммой привязанных заявок ФФ."
                        />
                        {mismatch.length === 0 ? (
                            <BlockEmpty text="Нет расхождений — всё чисто" />
                        ) : (
                            <div style={{ overflowX: 'auto' }}>
                                <table className="data-table" style={{ fontSize: 13 }}>
                                    <thead>
                                        <tr>
                                            <th>№</th>
                                            <th>Статус</th>
                                            <th>Склад</th>
                                            <th>Заявки ФФ</th>
                                            <th style={{ textAlign: 'right' }}>Наш шт</th>
                                            <th style={{ textAlign: 'right' }}>ФФ шт</th>
                                            <th style={{ textAlign: 'right' }}>Δ</th>
                                            <th>Режим</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {mismatch.map(row => (
                                            <tr key={row.assembly_id}>
                                                <td>
                                                    <Link
                                                        href={`/p/${slug}/warehouse/assembly/${row.assembly_id}`}
                                                        style={{ color: 'var(--color-accent)', fontWeight: 600, textDecoration: 'none' }}
                                                    >
                                                        {row.number}
                                                    </Link>
                                                </td>
                                                <td><StatusCell status={row.status} /></td>
                                                <td style={{ color: 'var(--color-text-muted)' }}>{row.warehouse_name || '—'}</td>
                                                <td style={{ color: 'var(--color-text-muted)' }}>
                                                    {row.ff_request_numbers.length > 0 ? row.ff_request_numbers.join(', ') : '—'}
                                                </td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(row.our_total, 0)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(row.ff_total, 0)}</td>
                                                <td style={{ textAlign: 'right' }}>
                                                    <span
                                                        style={{
                                                            fontWeight: 700,
                                                            color: row.diff > 0
                                                                ? 'var(--color-warning)'
                                                                : row.diff < 0
                                                                    ? 'var(--color-danger)'
                                                                    : 'var(--color-text)',
                                                        }}
                                                    >
                                                        {row.diff > 0 ? '+' : ''}{formatNumber(row.diff, 0)}
                                                    </span>
                                                </td>
                                                <td style={{ color: 'var(--color-text-muted)' }}>{MODE_LABEL[row.mode]}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>

                    {/* 2. Наши сборки без заявки ФФ */}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden', borderLeft: '3px solid var(--color-accent)' }}>
                        <BlockHeader
                            icon="🔗"
                            title="Наши сборки без заявки ФФ"
                            count={asmWithoutFf.length}
                            color="var(--color-accent)"
                            onExport={exportAsmWithoutFf}
                            note="Сборка на ФФ-складе, к которой не привязана заявка фулфилмента."
                        />
                        {asmWithoutFf.length === 0 ? (
                            <BlockEmpty text="Все сборки привязаны — всё чисто" />
                        ) : (
                            <div style={{ overflowX: 'auto' }}>
                                <table className="data-table" style={{ fontSize: 13 }}>
                                    <thead>
                                        <tr>
                                            <th>№</th>
                                            <th>Статус</th>
                                            <th>Склад</th>
                                            <th>Провайдер</th>
                                            <th style={{ textAlign: 'right' }}>Шт</th>
                                            <th style={{ textAlign: 'right' }}>Дней без привязки</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {asmWithoutFf.map(row => (
                                            <tr key={row.assembly_id}>
                                                <td>
                                                    <Link
                                                        href={`/p/${slug}/warehouse/assembly/${row.assembly_id}`}
                                                        style={{ color: 'var(--color-accent)', fontWeight: 600, textDecoration: 'none' }}
                                                    >
                                                        {row.number}
                                                    </Link>
                                                </td>
                                                <td><StatusCell status={row.status} /></td>
                                                <td style={{ color: 'var(--color-text-muted)' }}>{row.warehouse_name || '—'}</td>
                                                <td style={{ color: 'var(--color-text-muted)' }}>{row.provider || '—'}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(row.total_qty, 0)}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(row.age_days, 0)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>

                    {/* 3. Заявки ФФ без нашей сборки */}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden', borderLeft: '3px solid var(--color-accent)' }}>
                        <BlockHeader
                            icon="🔗"
                            title="Заявки ФФ без нашей сборки"
                            count={ffWithoutAsm.length}
                            color="var(--color-accent)"
                            onExport={exportFfWithoutAsm}
                            note="Заявка фулфилмента, к которой не привязана наша сборка."
                        />
                        {ffWithoutAsm.length === 0 ? (
                            <BlockEmpty text="Все заявки ФФ привязаны — всё чисто" />
                        ) : (
                            <div style={{ overflowX: 'auto' }}>
                                <table className="data-table" style={{ fontSize: 13 }}>
                                    <thead>
                                        <tr>
                                            <th>Провайдер</th>
                                            <th>№</th>
                                            <th>Склад</th>
                                            <th>Стадия</th>
                                            <th>Статус</th>
                                            <th style={{ textAlign: 'right' }}>Шт</th>
                                            <th>Создана</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {ffWithoutAsm.map(row => (
                                            <tr key={row.ff_request_id}>
                                                <td style={{ fontWeight: 500 }}>{row.provider}</td>
                                                <td>{row.number || '—'}</td>
                                                <td style={{ color: 'var(--color-text-muted)' }}>{row.warehouse_name || '—'}</td>
                                                <td style={{ color: 'var(--color-text-muted)' }}>{row.stage_title || '—'}</td>
                                                <td style={{ color: 'var(--color-text-muted)' }}>{row.status || '—'}</td>
                                                <td style={{ textAlign: 'right' }}>{formatNumber(row.total_qty ?? 0, 0)}</td>
                                                <td style={{ color: 'var(--color-text-muted)' }}>
                                                    {row.external_created_at ? formatDate(row.external_created_at) : '—'}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>

                    {/* 4. Аномалии поставок FBO */}
                    {fbo && (
                        <div className="glass-card" style={{ padding: 0, overflow: 'hidden', borderLeft: '3px solid var(--color-danger)' }}>
                            <BlockHeader
                                icon="📦"
                                title="Аномалии поставок FBO"
                                count={fbo.without_assembly_count + fbo.under_accepted_count + fbo.excess_count}
                                color="var(--color-danger)"
                                note="По всем складам — расхождения FBO-поставок ВБ."
                            />
                            {!fboHasAnomalies ? (
                                <BlockEmpty text="Нет аномалий FBO — всё чисто" />
                            ) : (
                                <div style={{ padding: '4px 20px 16px', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                                    <FboMiniCard
                                        label="Без заявки"
                                        value={fbo.without_assembly_count}
                                        color="var(--color-warning)"
                                    />
                                    <FboMiniCard
                                        label="Недоприёмка"
                                        value={fbo.under_accepted_count}
                                        sub={`${formatNumber(fbo.under_accepted_qty, 0)} шт`}
                                        color="var(--color-danger)"
                                    />
                                    <FboMiniCard
                                        label="Излишек"
                                        value={fbo.excess_count}
                                        sub={`${formatNumber(fbo.excess_qty, 0)} шт`}
                                        color="var(--color-accent)"
                                    />
                                </div>
                            )}
                            <div style={{ padding: '0 20px 16px' }}>
                                <Link
                                    href={`/p/${slug}/warehouse/fbo-supplies`}
                                    style={{ color: 'var(--color-accent)', fontWeight: 600, textDecoration: 'none', fontSize: 13 }}
                                >
                                    Открыть поставки FBO →
                                </Link>
                            </div>
                        </div>
                    )}
                </div>
            )}

            <style jsx>{`
                @keyframes shimmer {
                    0% {
                        background-position: 200% 0;
                    }
                    100% {
                        background-position: -200% 0;
                    }
                }
            `}</style>
        </div>
    );
}

/** Мини-карточка-счётчик аномалий FBO. */
function FboMiniCard({
    label,
    value,
    sub,
    color,
}: {
    label: string;
    value: number;
    sub?: string;
    color: string;
}) {
    return (
        <div
            style={{
                flex: '1 1 140px',
                minWidth: 140,
                padding: '14px 16px',
                borderRadius: 12,
                border: '1px solid var(--color-border)',
                background: `color-mix(in srgb, ${color} 6%, transparent)`,
            }}
        >
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>
                {label}
            </div>
            <div style={{ fontSize: 28, fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1.1, color }}>
                {formatNumber(value, 0)}
            </div>
            {sub && (
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 4 }}>{sub}</div>
            )}
        </div>
    );
}
