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

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { exportToExcel, formatDate, formatNumber } from '@/lib/utils';
import { FfLinkModal } from '../../../[id]/ff-shared';
import type {
    FboAnomalySupply,
    FfMismatchRow,
    LinkAnomaliesResponse,
    StockMismatchWarehouseRow,
    SupplyDiscrepancyRow,
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

// Какая категория аномалий FBO раскрыта (мини-карточки кликабельны).
type FboBucket = 'without' | 'under' | 'excess' | null;

// Соответствие категории фильтру страницы «Поставки FBO» (deep-link).
const FBO_FILTER_PARAM: Record<Exclude<FboBucket, null>, string> = {
    without: 'without_assembly',
    under: 'partial',
    excess: 'excess',
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

    // Развороты: склад в блоке расхождения остатков; категория аномалий FBO.
    const [expandedWh, setExpandedWh] = useState<number | null>(null);
    const [fboBucket, setFboBucket] = useState<FboBucket>(null);

    // Архив заявки ФФ прямо из блока «без нашей сборки» (локальная пометка)
    const [archivingId, setArchivingId] = useState<number | null>(null);
    const [archiveError, setArchiveError] = useState('');
    // Клиентский фильтр по складу внутри блока «Заявки ФФ без нашей сборки».
    const [ffWhFilter, setFfWhFilter] = useState<number | ''>('');
    // Заявка ФФ, для которой открыта модалка «Связать» (реюз FfLinkModal).
    const [linkFor, setLinkFor] = useState<UnlinkedFfRow | null>(null);

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

    // Убрать заявку ФФ в локальный архив прямо из блока «без нашей сборки» → уйдёт из списка
    const archiveFf = useCallback(async (row: UnlinkedFfRow) => {
        setArchivingId(row.ff_request_id);
        setArchiveError('');
        try {
            await api.archiveFulfillmentRequest(row.warehouse_id, row.ff_request_id);
            setReloadTick(t => t + 1);
        } catch (e: unknown) {
            setArchiveError(e instanceof Error ? e.message : 'Не удалось убрать заявку ФФ в архив');
        } finally {
            setArchivingId(null);
        }
    }, []);

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
        const all = data?.ff_without_assembly ?? [];
        const rows = ffWhFilter ? all.filter(r => r.warehouse_id === ffWhFilter) : all;
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
    }, [data, ffWhFilter]);

    const exportSupplyDiscrepancies = useCallback(() => {
        const rows = data?.supply_discrepancies ?? [];
        if (rows.length === 0) return;
        const problems = (r: SupplyDiscrepancyRow) =>
            [
                r.date_mismatch && 'Дата',
                r.pallet_mismatch && 'Паллеты',
                r.car_number_mismatch && 'Номер≠ВБ',
                r.pass_missing && 'Нет на ВБ',
                r.pass_missing_dds && 'Нет в ДДС',
            ]
                .filter(Boolean)
                .join(', ');
        exportToExcel(
            rows.map(r => ({
                number: r.number,
                status: statusBadge(r.status).label,
                source_warehouse: r.source_warehouse_name || '',
                warehouse: r.warehouse_name || '',
                delivery_date: r.delivery_date ? formatDate(r.delivery_date) : '',
                planned_date: r.planned_date ? formatDate(r.planned_date) : '',
                date_diff: r.date_diff_days ?? '',
                pallets_count: r.pallets_count,
                pass_pallets: r.pass_pallets ?? '',
                wb_car_number: r.wb_car_number || '',
                problems: problems(r),
            })),
            'assembly_supply_discrepancies',
            [
                { key: 'number', label: '№' },
                { key: 'status', label: 'Статус' },
                { key: 'source_warehouse', label: 'Со склада' },
                { key: 'warehouse', label: 'Склад ВБ' },
                { key: 'delivery_date', label: 'Дата сдачи' },
                { key: 'planned_date', label: 'Дата брони' },
                { key: 'date_diff', label: 'Δ дней' },
                { key: 'pallets_count', label: 'Наши паллеты' },
                { key: 'pass_pallets', label: 'Паллеты пропуска' },
                { key: 'wb_car_number', label: 'Номер машины ВБ' },
                { key: 'problems', label: 'Проблемы' },
            ],
        );
    }, [data]);

    const exportStockMismatch = useCallback(() => {
        const whs = data?.stock_mismatch ?? [];
        if (whs.length === 0) return;
        const flat = whs.flatMap(w =>
            w.rows.map(r => ({
                warehouse: w.warehouse_name || '',
                provider: w.provider || '',
                barcode: r.barcode,
                article_seller: r.article_seller || '',
                brand: r.brand || '',
                name: r.name || '',
                ff_good: r.ff_good,
                ff_fbs: r.ff_fbs ?? 0,
                our_quantity: r.our_quantity,
                our_defect: r.our_defect,
                diff: r.diff,
                direction: r.diff > 0 ? 'у ФФ больше' : 'у нас больше',
            })),
        );
        if (flat.length === 0) return;
        exportToExcel(
            flat,
            'assembly_stock_mismatch',
            [
                { key: 'warehouse', label: 'Склад' },
                { key: 'provider', label: 'Провайдер' },
                { key: 'barcode', label: 'ШК' },
                { key: 'article_seller', label: 'Артикул' },
                { key: 'brand', label: 'Бренд' },
                { key: 'name', label: 'Название' },
                { key: 'ff_good', label: 'ФФ шт' },
                { key: 'ff_fbs', label: 'Отгружено FBS' },
                { key: 'our_quantity', label: 'Наш шт' },
                { key: 'our_defect', label: 'Наш брак' },
                { key: 'diff', label: 'Δ' },
                { key: 'direction', label: 'Направление' },
            ],
        );
    }, [data]);

    // ─── Derived ──────────────────────────────────────────────────────────
    const mismatch: FfMismatchRow[] = useMemo(() => data?.ff_composition_mismatch ?? [], [data]);
    const asmWithoutFf: UnlinkedAssemblyRow[] = useMemo(() => data?.assemblies_without_ff ?? [], [data]);
    const ffWithoutAsm: UnlinkedFfRow[] = useMemo(() => data?.ff_without_assembly ?? [], [data]);
    const stockMismatch: StockMismatchWarehouseRow[] = useMemo(() => data?.stock_mismatch ?? [], [data]);
    const supplyDiscrepancies: SupplyDiscrepancyRow[] = useMemo(() => data?.supply_discrepancies ?? [], [data]);

    // Склады, встречающиеся в блоке «без нашей сборки» — для локального фильтра.
    const ffWhOptions = useMemo(() => {
        const seen = new Map<number, string>();
        for (const r of ffWithoutAsm) {
            if (!seen.has(r.warehouse_id)) seen.set(r.warehouse_id, r.warehouse_name || `#${r.warehouse_id}`);
        }
        return [...seen.entries()].map(([id, name]) => ({ id, name }));
    }, [ffWithoutAsm]);

    // Отфильтрованный по складу срез блока «без нашей сборки» (питает и таблицу, и Excel).
    const ffWithoutAsmView = useMemo(
        () => (ffWhFilter ? ffWithoutAsm.filter(r => r.warehouse_id === ffWhFilter) : ffWithoutAsm),
        [ffWithoutAsm, ffWhFilter],
    );

    // После рефетча (архив/связь) выбранный склад мог исчезнуть из списка — сбрасываем
    // фильтр, иначе селект показывает пустое значение, а таблица — «на складе пусто».
    useEffect(() => {
        if (ffWhFilter && !ffWhOptions.some(w => w.id === ffWhFilter)) setFfWhFilter('');
    }, [ffWhOptions, ffWhFilter]);
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
                            count={ffWithoutAsmView.length}
                            color="var(--color-accent)"
                            onExport={exportFfWithoutAsm}
                            note="Заявка фулфилмента, к которой не привязана наша сборка."
                        />
                        {ffWithoutAsm.length > 0 && ffWhOptions.length > 1 && (
                            <div style={{ padding: '0 20px 12px' }}>
                                <select
                                    className="form-input"
                                    style={{ width: 'auto', minWidth: 150 }}
                                    value={ffWhFilter}
                                    onChange={e => setFfWhFilter(e.target.value ? Number(e.target.value) : '')}
                                >
                                    <option value="">Все склады</option>
                                    {ffWhOptions.map(w => (
                                        <option key={w.id} value={w.id}>{w.name}</option>
                                    ))}
                                </select>
                            </div>
                        )}
                        {archiveError && (
                            <div style={{ padding: '8px 16px', color: 'var(--color-danger)', fontSize: 13 }}>{archiveError}</div>
                        )}
                        {ffWithoutAsmView.length === 0 ? (
                            <BlockEmpty text={ffWhFilter ? 'На этом складе непривязанных заявок ФФ нет' : 'Все заявки ФФ привязаны — всё чисто'} />
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
                                            <th>Действия</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {ffWithoutAsmView.map(row => (
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
                                                <td style={{ whiteSpace: 'nowrap' }}>
                                                    <Link
                                                        href={`/p/${slug}/warehouse/${row.warehouse_id}/ff-request/${row.ff_request_id}`}
                                                        className="btn btn-sm btn-secondary"
                                                        style={{ marginRight: 8 }}
                                                    >
                                                        Открыть
                                                    </Link>
                                                    <button
                                                        className="btn btn-sm btn-primary"
                                                        title="Связать с нашей заявкой на сборку"
                                                        style={{ marginRight: 8 }}
                                                        onClick={() => setLinkFor(row)}
                                                    >
                                                        Связать
                                                    </button>
                                                    <button
                                                        className="btn btn-sm btn-secondary"
                                                        title="Убрать заявку ФФ в архив (локальная пометка) — уйдёт из блока"
                                                        onClick={() => archiveFf(row)}
                                                        disabled={archivingId === row.ff_request_id}
                                                    >
                                                        {archivingId === row.ff_request_id ? '...' : 'В архив'}
                                                    </button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>

                    {/* 4. Расхождение остатков с ФФ (наш склад vs API-зеркало) */}
                    <StockMismatchBlock
                        rows={stockMismatch}
                        slug={slug}
                        expandedWh={expandedWh}
                        onToggle={wid => setExpandedWh(prev => (prev === wid ? null : wid))}
                        onExport={exportStockMismatch}
                    />

                    {/* 5. Расхождение поставок ФФ (машина назначена / в пути) */}
                    <SupplyDiscrepancyBlock
                        rows={supplyDiscrepancies}
                        slug={slug}
                        onExport={exportSupplyDiscrepancies}
                    />

                    {/* 6. Аномалии поставок FBO */}
                    {fbo && (
                        <div className="glass-card" style={{ padding: 0, overflow: 'hidden', borderLeft: '3px solid var(--color-danger)' }}>
                            <BlockHeader
                                icon="📦"
                                title="Аномалии поставок FBO"
                                count={fbo.without_assembly_count + fbo.under_accepted_count + fbo.excess_count}
                                color="var(--color-danger)"
                                note="По всем складам — расхождения FBO-поставок ВБ. Кликните карточку, чтобы увидеть сами поставки."
                            />
                            {!fboHasAnomalies ? (
                                <BlockEmpty text="Нет аномалий FBO — всё чисто" />
                            ) : (
                                <>
                                    <div style={{ padding: '4px 20px 16px', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                                        <FboMiniCard
                                            label="Без заявки"
                                            value={fbo.without_assembly_count}
                                            color="var(--color-warning)"
                                            active={fboBucket === 'without'}
                                            onClick={() => setFboBucket(b => (b === 'without' ? null : 'without'))}
                                        />
                                        <FboMiniCard
                                            label="Недоприёмка"
                                            value={fbo.under_accepted_count}
                                            sub={`${formatNumber(fbo.under_accepted_qty, 0)} шт`}
                                            color="var(--color-danger)"
                                            active={fboBucket === 'under'}
                                            onClick={() => setFboBucket(b => (b === 'under' ? null : 'under'))}
                                        />
                                        <FboMiniCard
                                            label="Излишек"
                                            value={fbo.excess_count}
                                            sub={`${formatNumber(fbo.excess_qty, 0)} шт`}
                                            color="var(--color-accent)"
                                            active={fboBucket === 'excess'}
                                            onClick={() => setFboBucket(b => (b === 'excess' ? null : 'excess'))}
                                        />
                                    </div>
                                    {fboBucket === 'without' && (
                                        <FboSupplyTable
                                            slug={slug}
                                            supplies={fbo.without_assembly_supplies}
                                            totalCount={fbo.without_assembly_count}
                                            bucket="without"
                                        />
                                    )}
                                    {fboBucket === 'under' && (
                                        <FboSupplyTable
                                            slug={slug}
                                            supplies={fbo.under_accepted_supplies}
                                            totalCount={fbo.under_accepted_count}
                                            bucket="under"
                                        />
                                    )}
                                    {fboBucket === 'excess' && (
                                        <FboSupplyTable
                                            slug={slug}
                                            supplies={fbo.excess_supplies}
                                            totalCount={fbo.excess_count}
                                            bucket="excess"
                                        />
                                    )}
                                </>
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

            {linkFor && (
                <FfLinkModal
                    warehouseId={linkFor.warehouse_id}
                    kind="assembly"
                    request={{ id: linkFor.ff_request_id, number: linkFor.number, external_id: '' }}
                    onClose={() => setLinkFor(null)}
                    onLinked={() => {
                        setLinkFor(null);
                        setReloadTick(t => t + 1);
                    }}
                />
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

/** Блок «Расхождение поставок ФФ»: сборки (машина назначена / в пути), чья WB-поставка
 *  расходится по дате сдачи, паллетам или неоформленному пропуску. */
function SupplyDiscrepancyBlock({
    rows,
    slug,
    onExport,
}: {
    rows: SupplyDiscrepancyRow[];
    slug: string;
    onExport: () => void;
}) {
    return (
        <div className="glass-card" style={{ padding: 0, overflow: 'hidden', borderLeft: '3px solid var(--color-danger)' }}>
            <BlockHeader
                icon="🚚"
                title="Расхождение поставок ФФ"
                count={rows.length}
                color="var(--color-danger)"
                onExport={onExport}
                note="Машина назначена или в пути, но что-то расходится: дата сдачи вне окна брони (±1 день), паллеты не совпадают, номер машины ≠ кабинету WB, либо пропуск не оформлен (нет на ВБ / нет в ДДС)."
            />
            {rows.length === 0 ? (
                <BlockEmpty text="Поставки сходятся — всё чисто" />
            ) : (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table" style={{ fontSize: 13 }}>
                        <thead>
                            <tr>
                                <th>Сборка</th>
                                <th>Статус</th>
                                <th>Со склада</th>
                                <th>Склад ВБ</th>
                                <th>Дата сдачи</th>
                                <th>Дата брони</th>
                                <th style={{ textAlign: 'right' }}>Δ дней</th>
                                <th style={{ textAlign: 'right' }}>Наши пал.</th>
                                <th style={{ textAlign: 'right' }}>Пал. пропуска</th>
                                <th>Номер ВБ</th>
                                <th>Проблемы</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map(row => (
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
                                    <td style={{ color: 'var(--color-text-muted)' }}>{row.source_warehouse_name || '—'}</td>
                                    <td style={{ color: 'var(--color-text-muted)' }}>{row.warehouse_name || '—'}</td>
                                    <td style={{ color: row.date_mismatch ? 'var(--color-danger)' : 'var(--color-text-muted)' }}>
                                        {row.delivery_date ? formatDate(row.delivery_date) : '—'}
                                    </td>
                                    <td style={{ color: 'var(--color-text-muted)' }}>
                                        {row.planned_date ? formatDate(row.planned_date) : '—'}
                                    </td>
                                    <td style={{ textAlign: 'right', fontWeight: row.date_mismatch ? 700 : 400, color: row.date_mismatch ? 'var(--color-danger)' : 'var(--color-text)' }}>
                                        {row.date_diff_days == null ? '—' : `${row.date_diff_days > 0 ? '+' : ''}${formatNumber(row.date_diff_days, 0)}`}
                                    </td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(row.pallets_count, 0)}</td>
                                    <td style={{ textAlign: 'right', color: row.pallet_mismatch ? 'var(--color-danger)' : 'var(--color-text-muted)', fontWeight: row.pallet_mismatch ? 700 : 400 }}>
                                        {row.pass_pallets == null ? '—' : formatNumber(row.pass_pallets, 0)}
                                    </td>
                                    <td style={{ color: row.car_number_mismatch ? 'var(--color-danger)' : 'var(--color-text-muted)', fontWeight: row.car_number_mismatch ? 700 : 400 }}>
                                        {row.wb_car_number || '—'}
                                    </td>
                                    <td style={{ whiteSpace: 'nowrap' }}>
                                        {row.date_mismatch && <span className="badge badge-danger" style={{ fontSize: 11, marginRight: 4 }}>Дата</span>}
                                        {row.pallet_mismatch && <span className="badge badge-warning" style={{ fontSize: 11, marginRight: 4 }}>Паллеты</span>}
                                        {row.car_number_mismatch && <span className="badge badge-danger" style={{ fontSize: 11, marginRight: 4 }}>Номер ≠ ВБ</span>}
                                        {row.pass_missing && <span className="badge badge-secondary" style={{ fontSize: 11, marginRight: 4 }}>Нет на ВБ</span>}
                                        {row.pass_missing_dds && <span className="badge badge-warning" style={{ fontSize: 11 }}>Нет в ДДС</span>}
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

/** Мини-карточка-счётчик аномалий FBO. Кликабельна — разворачивает список поставок. */
function FboMiniCard({
    label,
    value,
    sub,
    color,
    active = false,
    onClick,
}: {
    label: string;
    value: number;
    sub?: string;
    color: string;
    active?: boolean;
    onClick?: () => void;
}) {
    const clickable = !!onClick && value > 0;
    return (
        <button
            type="button"
            onClick={clickable ? onClick : undefined}
            disabled={!clickable}
            style={{
                flex: '1 1 140px',
                minWidth: 140,
                padding: '14px 16px',
                borderRadius: 12,
                textAlign: 'left',
                border: active ? `1px solid ${color}` : '1px solid var(--color-border)',
                background: `color-mix(in srgb, ${color} ${active ? 12 : 6}%, transparent)`,
                cursor: clickable ? 'pointer' : 'default',
                boxShadow: active ? `0 0 0 1px ${color} inset` : 'none',
                transition: 'background 0.2s, border-color 0.2s',
            }}
        >
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    {label}
                </span>
                {clickable && (
                    <span style={{ fontSize: 11, color: 'var(--color-text-muted)', marginLeft: 'auto' }}>
                        {active ? '▾' : '▸'}
                    </span>
                )}
            </div>
            <div style={{ fontSize: 28, fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1.1, color, marginTop: 6 }}>
                {formatNumber(value, 0)}
            </div>
            {sub && (
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 4 }}>{sub}</div>
            )}
        </button>
    );
}

/** Список аномальных FBO-поставок одной категории. Каждая строка — deep-link на «Поставки FBO». */
function FboSupplyTable({
    slug,
    supplies,
    totalCount,
    bucket,
}: {
    slug: string;
    supplies: FboAnomalySupply[];
    totalCount: number;
    bucket: Exclude<FboBucket, null>;
}) {
    const filterParam = FBO_FILTER_PARAM[bucket];
    const supplyHref = (s: FboAnomalySupply) =>
        s.wb_supply_id
            ? `/p/${slug}/warehouse/fbo-supplies?supply=${encodeURIComponent(s.wb_supply_id)}`
            : `/p/${slug}/warehouse/fbo-supplies?filter=${filterParam}`;
    const hidden = totalCount - supplies.length;
    return (
        <div style={{ padding: '0 20px 16px' }}>
            {supplies.length === 0 ? (
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Нет поставок в этой категории.</div>
            ) : (
                <div style={{ overflowX: 'auto', border: '1px solid var(--color-border)', borderRadius: 12 }}>
                    <table className="data-table" style={{ fontSize: 13, margin: 0 }}>
                        <thead>
                            <tr>
                                <th>Поставка</th>
                                <th>Склад ВБ</th>
                                <th>Сборка</th>
                                <th style={{ textAlign: 'right' }}>Заявлено</th>
                                <th style={{ textAlign: 'right' }}>Принято</th>
                                <th style={{ textAlign: 'right' }}>Δ</th>
                                <th>Дата</th>
                                <th />
                            </tr>
                        </thead>
                        <tbody>
                            {supplies.map(s => (
                                <tr key={s.supply_id}>
                                    <td>
                                        <Link
                                            href={supplyHref(s)}
                                            style={{ color: 'var(--color-accent)', fontWeight: 600, textDecoration: 'none' }}
                                        >
                                            {s.wb_supply_id || s.name || `#${s.supply_id}`}
                                        </Link>
                                    </td>
                                    <td style={{ color: 'var(--color-text-muted)' }}>{s.warehouse_name || '—'}</td>
                                    <td style={{ color: 'var(--color-text-muted)' }}>{s.assembly_request_number || '—'}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(s.total_qty, 0)}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(s.accepted_qty, 0)}</td>
                                    <td style={{ textAlign: 'right' }}>
                                        <span
                                            style={{
                                                fontWeight: 700,
                                                color: s.diff > 0
                                                    ? 'var(--color-accent)'
                                                    : s.diff < 0
                                                        ? 'var(--color-danger)'
                                                        : 'var(--color-text)',
                                            }}
                                        >
                                            {s.diff > 0 ? '+' : ''}{formatNumber(s.diff, 0)}
                                        </span>
                                    </td>
                                    <td style={{ color: 'var(--color-text-muted)' }}>
                                        {s.actual_date ? formatDate(s.actual_date) : s.planned_date ? formatDate(s.planned_date) : '—'}
                                    </td>
                                    <td style={{ textAlign: 'right' }}>
                                        <Link
                                            href={supplyHref(s)}
                                            style={{ color: 'var(--color-accent)', textDecoration: 'none', fontSize: 12, whiteSpace: 'nowrap' }}
                                        >
                                            Открыть →
                                        </Link>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            {hidden > 0 && (
                <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-muted)' }}>
                    Показаны первые {formatNumber(supplies.length, 0)} из {formatNumber(totalCount, 0)}.{' '}
                    <Link
                        href={`/p/${slug}/warehouse/fbo-supplies?filter=${filterParam}`}
                        style={{ color: 'var(--color-accent)', textDecoration: 'none', fontWeight: 600 }}
                    >
                        Показать все →
                    </Link>
                </div>
            )}
        </div>
    );
}

/** Блок «Расхождение остатков с ФФ»: строки по складам с разворотом по SKU. */
function StockMismatchBlock({
    rows,
    slug,
    expandedWh,
    onToggle,
    onExport,
}: {
    rows: StockMismatchWarehouseRow[];
    slug: string;
    expandedWh: number | null;
    onToggle: (warehouseId: number) => void;
    onExport: () => void;
}) {
    return (
        <div className="glass-card" style={{ padding: 0, overflow: 'hidden', borderLeft: '3px solid var(--color-warning)' }}>
            <BlockHeader
                icon="📊"
                title="Расхождение остатков с ФФ"
                count={rows.length}
                color="var(--color-warning)"
                onExport={onExport}
                note="Наш остаток не сходится с зеркалом склада по API. Δ = ФФ − наш (кликните склад, чтобы развернуть по SKU)."
            />
            {rows.length === 0 ? (
                <BlockEmpty text="Остатки сходятся с ФФ — всё чисто" />
            ) : (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table" style={{ fontSize: 13 }}>
                        <thead>
                            <tr>
                                <th>Склад</th>
                                <th>Провайдер</th>
                                <th style={{ textAlign: 'right' }}>У ФФ больше</th>
                                <th style={{ textAlign: 'right' }}>У нас больше</th>
                                <th style={{ textAlign: 'right' }}>Δ нетто</th>
                                <th>Синк</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map(w => {
                                const open = expandedWh === w.warehouse_id;
                                // FBS-отгрузки, ещё не списанные провайдером, вычтены из ФФ-стороны
                                // диффа — без пометки снятый ими «шум» выглядел бы как чудо-сходимость.
                                const hasFbs = (w.ff_fbs_qty ?? 0) > 0;
                                const rowHasFbs = w.rows.some(r => (r.ff_fbs ?? 0) > 0);
                                return (
                                    <Fragment key={w.warehouse_id}>
                                        <tr
                                            onClick={() => onToggle(w.warehouse_id)}
                                            style={{ cursor: 'pointer' }}
                                        >
                                            <td style={{ fontWeight: 600 }}>
                                                <span style={{ color: 'var(--color-text-muted)', marginRight: 6, fontSize: 11 }}>
                                                    {open ? '▾' : '▸'}
                                                </span>
                                                {w.warehouse_name || `#${w.warehouse_id}`}
                                                {hasFbs && (
                                                    <div
                                                        style={{ fontSize: 11, fontWeight: 400, color: 'var(--color-text-muted)', marginLeft: 17 }}
                                                        title={'Отгружено по FBS у нас, провайдер выбытие ещё не отразил — '
                                                            + 'эти штуки вычтены из остатка ФФ до сравнения'}
                                                    >
                                                        FBS-поправка: {formatNumber(w.ff_fbs_qty, 0)} шт
                                                    </div>
                                                )}
                                            </td>
                                            <td style={{ color: 'var(--color-text-muted)' }}>{w.provider || '—'}</td>
                                            <td style={{ textAlign: 'right' }}>
                                                {w.surplus_ff_qty > 0 ? (
                                                    <span style={{ color: 'var(--color-accent)', fontWeight: 600 }}>
                                                        +{formatNumber(w.surplus_ff_qty, 0)}
                                                        <span style={{ color: 'var(--color-text-muted)', fontWeight: 400, fontSize: 11 }}>
                                                            {' '}/ {formatNumber(w.surplus_ff_sku, 0)} SKU
                                                        </span>
                                                    </span>
                                                ) : '—'}
                                            </td>
                                            <td style={{ textAlign: 'right' }}>
                                                {w.surplus_our_qty > 0 ? (
                                                    <span style={{ color: 'var(--color-danger)', fontWeight: 600 }}>
                                                        −{formatNumber(w.surplus_our_qty, 0)}
                                                        <span style={{ color: 'var(--color-text-muted)', fontWeight: 400, fontSize: 11 }}>
                                                            {' '}/ {formatNumber(w.surplus_our_sku, 0)} SKU
                                                        </span>
                                                    </span>
                                                ) : '—'}
                                            </td>
                                            <td style={{ textAlign: 'right', fontWeight: 700, color: w.net_diff > 0 ? 'var(--color-accent)' : w.net_diff < 0 ? 'var(--color-danger)' : 'var(--color-text)' }}>
                                                {w.net_diff > 0 ? '+' : ''}{formatNumber(w.net_diff, 0)}
                                            </td>
                                            <td style={{ color: 'var(--color-text-muted)', fontSize: 12 }}>
                                                {w.synced_at ? formatDate(w.synced_at) : '—'}
                                            </td>
                                        </tr>
                                        {open && (
                                            <tr>
                                                <td colSpan={6} style={{ padding: 0, background: 'color-mix(in srgb, var(--color-warning) 4%, transparent)' }}>
                                                    <div style={{ padding: '8px 20px 12px' }}>
                                                        <div style={{ overflowX: 'auto', border: '1px solid var(--color-border)', borderRadius: 12, background: 'var(--color-bg-card)' }}>
                                                            <table className="data-table" style={{ fontSize: 12, margin: 0 }}>
                                                                <thead>
                                                                    <tr>
                                                                        <th>ШК</th>
                                                                        <th>Артикул</th>
                                                                        <th>Бренд</th>
                                                                        <th style={{ textAlign: 'right' }}>ФФ шт</th>
                                                                        {rowHasFbs && (
                                                                            <th
                                                                                style={{ textAlign: 'right' }}
                                                                                title="Отгружено по FBS у нас, провайдер ещё не списал — вычтено из «ФФ шт»"
                                                                            >
                                                                                Отгружено FBS
                                                                            </th>
                                                                        )}
                                                                        <th style={{ textAlign: 'right' }}>Наш шт</th>
                                                                        {w.provider === 'migfull' && <th style={{ textAlign: 'right' }}>Наш брак</th>}
                                                                        <th style={{ textAlign: 'right' }}>Δ</th>
                                                                    </tr>
                                                                </thead>
                                                                <tbody>
                                                                    {w.rows.map(r => (
                                                                        <tr key={r.barcode}>
                                                                            <td style={{ fontFamily: 'monospace' }}>{r.barcode}</td>
                                                                            <td>{r.article_seller || r.name || '—'}</td>
                                                                            <td style={{ color: 'var(--color-text-muted)' }}>{r.brand || '—'}</td>
                                                                            <td style={{ textAlign: 'right' }}>{formatNumber(r.ff_good, 0)}</td>
                                                                            {rowHasFbs && (
                                                                                <td style={{ textAlign: 'right', color: (r.ff_fbs ?? 0) > 0 ? 'var(--color-accent)' : 'var(--color-text-muted)' }}>
                                                                                    {formatNumber(r.ff_fbs ?? 0, 0)}
                                                                                </td>
                                                                            )}
                                                                            <td style={{ textAlign: 'right' }}>{formatNumber(r.our_quantity, 0)}</td>
                                                                            {w.provider === 'migfull' && (
                                                                                <td style={{ textAlign: 'right', color: r.our_defect > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)' }}>
                                                                                    {formatNumber(r.our_defect, 0)}
                                                                                </td>
                                                                            )}
                                                                            <td style={{ textAlign: 'right', fontWeight: 700, color: r.diff > 0 ? 'var(--color-accent)' : 'var(--color-danger)' }}>
                                                                                {r.diff > 0 ? '+' : ''}{formatNumber(r.diff, 0)}
                                                                            </td>
                                                                        </tr>
                                                                    ))}
                                                                </tbody>
                                                            </table>
                                                        </div>
                                                        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                            {w.truncated && <span>Показаны крупнейшие {formatNumber(w.rows.length, 0)} из {formatNumber(w.sku_total, 0)} SKU. </span>}
                                                            <Link
                                                                href={`/p/${slug}/warehouse/${w.warehouse_id}?tab=ff-stocks`}
                                                                style={{ color: 'var(--color-accent)', textDecoration: 'none', fontWeight: 600 }}
                                                            >
                                                                Открыть остатки склада →
                                                            </Link>
                                                        </div>
                                                    </div>
                                                </td>
                                            </tr>
                                        )}
                                    </Fragment>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
