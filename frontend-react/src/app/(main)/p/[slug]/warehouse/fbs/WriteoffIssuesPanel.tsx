'use client';
/**
 * Панель «Не списано со склада» — вкладка «Заказы» FBS.
 *
 * Поставка передана, а списать товар с нашего склада нечем: остаток 0, нет
 * карточки или склад продавца не привязан. Такие задания молча висят
 * `written_off_at IS NULL`, продажа не проведена по книгам, и до этой панели
 * единственным следом был warning в логе воркера. Списание идемпотентно
 * ретраится каждые 5 минут — строка исчезает сама, как только причина
 * устранена; задача панели — показать, ЧТО устранять.
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber, pluralRu } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { FbsWriteoffIssueRow, FbsWriteoffIssues } from '@/types/api';
import { daysSince, num, writeoffReasonLabel } from './fbsShared';

interface Props {
    /** Меняется после синка / бэкфилла / раскладки — повод перечитать сводку. */
    reloadKey: number;
    /** Счётчик тиков автообновления страницы; 0 — тиков ещё не было. */
    refreshTick: number;
}

export default function WriteoffIssuesPanel({ reloadKey, refreshTick }: Props) {
    const [data, setData] = useState<FbsWriteoffIssues | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [expanded, setExpanded] = useState(false);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        // error НЕ чистим на старте: во время ретрая плашка/строка ошибки
        // должна остаться на экране (иначе панель на секунду пропадает совсем).
        try {
            const res = await api.getFbsWriteoffIssues();
            if (signal?.aborted) return;
            setData(res);
            setError('');
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки сводки списаний');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
        // reloadKey / refreshTick — внешние триггеры перечитать сводку
    }, [load, reloadKey, refreshTick]);

    // Ошибка не валит вкладку: панель — дополнение к списку заданий, поэтому
    // показываем компактную плашку с повтором, а не пустой экран. Плашка —
    // только пока данных НЕТ вовсе: когда тревога «Не списано: N» уже показана,
    // ошибка перечитывания не должна её гасить — она уедет строкой в панель.
    if (error && data === null) {
        return (
            <div
                className="glass-card"
                style={{
                    padding: 12, marginBottom: 12, fontSize: 13,
                    color: 'var(--color-danger)', display: 'flex', alignItems: 'center', gap: 12,
                }}
            >
                <span style={{ flex: 1 }}>Сводка «Не списано со склада» не загрузилась: {error}</span>
                <button className="btn btn-sm" onClick={() => load()} disabled={loading}>
                    {loading ? 'Загрузка…' : 'Повторить'}
                </button>
            </div>
        );
    }

    // Загрузка и пустая сводка рисуются одинаково — ничем. Панель существует
    // только как тревога: мигать «проверяем…» на каждом заходе ради блока,
    // которого в норме нет, значило бы приучить не смотреть на жёлтое.
    if (loading && data === null) return null;
    const total = num(data?.total_orders);
    if (total === 0) return null;

    const rows = data?.rows ?? [];

    const cols: Column[] = [
        {
            key: 'article', label: 'Товар',
            render: (v: string | null, row: FbsWriteoffIssueRow) => (
                <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 500 }}>{v || row.barcode || '—'}</div>
                    {row.barcode && (
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)', fontFamily: 'monospace' }}>
                            {row.barcode}
                        </div>
                    )}
                </div>
            ),
            exportValue: (row: FbsWriteoffIssueRow) => row.article || row.barcode || '',
        },
        {
            key: 'wb_warehouse_name', label: 'Склад продавца',
            render: (v: string | null, row: FbsWriteoffIssueRow) =>
                v || (row.wb_warehouse_id != null ? `#${row.wb_warehouse_id}` : '—'),
        },
        {
            key: 'warehouse_name', label: 'Наш склад',
            render: (v: string | null, row: FbsWriteoffIssueRow) => v
                || (row.warehouse_id != null ? `#${row.warehouse_id}` : (
                    <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                )),
            exportValue: (row: FbsWriteoffIssueRow) => row.warehouse_name
                ?? (row.warehouse_id != null ? `#${row.warehouse_id}` : ''),
        },
        {
            key: 'stuck', label: 'Не списано', align: 'right',
            headerTitle: 'Сколько переданных заданий по этому товару не проведено по складу',
            // queued — очередь, не алярм: остаток есть, спишется ближайшим
            // прогоном. Жёлтым горят только строки с реальной причиной.
            render: (v: number, row: FbsWriteoffIssueRow) => (
                <span style={{
                    fontWeight: 700,
                    color: row.reason === 'queued' ? undefined : 'var(--color-warning)',
                }}>
                    {formatNumber(num(v), 0)}
                </span>
            ),
            exportValue: (row: FbsWriteoffIssueRow) => num(row.stuck),
        },
        {
            key: 'our_qty', label: 'Остаток у нас', align: 'right',
            render: (v: number, row: FbsWriteoffIssueRow) => (
                <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                    <span>{formatNumber(num(v), 0)}</span>
                    {num(row.our_defect) > 0 && (
                        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                            брак: {formatNumber(num(row.our_defect), 0)}
                        </span>
                    )}
                </span>
            ),
            exportValue: (row: FbsWriteoffIssueRow) => num(row.our_qty),
        },
        {
            key: 'ff_loose', label: 'У ФФ россыпью', align: 'right',
            headerTitle: 'Россыпь в зеркале ФФ. Товар физически может лежать у провайдера — '
                + 'тогда отстал наш учёт, а не кончился товар',
            render: (v: number | null) => v == null
                ? <span style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>зеркала нет</span>
                : (
                    <span style={{ color: num(v) > 0 ? 'var(--color-accent)' : undefined, fontWeight: num(v) > 0 ? 600 : 400 }}>
                        {formatNumber(num(v), 0)}
                    </span>
                ),
            exportValue: (row: FbsWriteoffIssueRow) => row.ff_loose == null ? '' : num(row.ff_loose),
        },
        {
            key: 'oldest_at', label: 'Возраст', align: 'right',
            headerTitle: 'Самое старое несписанное задание по этому товару',
            render: (v: string | null) => {
                const days = daysSince(v);
                if (days == null) return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
                return (
                    <span title={v ? formatDateTime(v) : undefined}>
                        {days === 0 ? 'сегодня' : `${formatNumber(days, 0)} дн назад`}
                    </span>
                );
            },
            exportValue: (row: FbsWriteoffIssueRow) => daysSince(row.oldest_at) ?? '',
        },
        {
            key: 'reason', label: 'Причина',
            render: (v: string) => writeoffReasonLabel(v),
            exportValue: (row: FbsWriteoffIssueRow) => writeoffReasonLabel(row.reason),
        },
    ];

    return (
        <div
            className="glass-card"
            style={{ padding: 16, marginBottom: 12, borderLeft: '4px solid var(--color-warning)' }}
        >
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                <div style={{ fontSize: 20, lineHeight: 1.2 }}>⚠️</div>
                <div style={{ flex: 1, minWidth: 240 }}>
                    <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-warning)', marginBottom: 4 }}>
                        Не списано со склада: {formatNumber(total, 0)}{' '}
                        {pluralRu(total, ['задание', 'задания', 'заданий'])}
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Поставки переданы, а списать товар с нашего склада нечем — эти продажи не
                        проведены по книгам и до устранения причины будут маскироваться под
                        расхождение с ФФ. Списание повторяется автоматически: устраните причину из
                        колонки «Причина», и строка исчезнет сама.
                    </div>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={() => setExpanded(v => !v)}>
                    {expanded ? 'Скрыть' : `Показать (${formatNumber(rows.length, 0)})`}
                </button>
            </div>
            {/* Ошибка ПЕРЕЧИТЫВАНИЯ — строкой внутри панели: уже показанная
                тревога «Не списано: N» не гасится из-за одного упавшего запроса. */}
            {error && (
                <div style={{
                    marginTop: 8, fontSize: 12, color: 'var(--color-danger)',
                    display: 'flex', alignItems: 'center', gap: 8,
                }}>
                    <span style={{ flex: 1 }}>
                        Не удалось обновить сводку: {error} — показаны прошлые данные.
                    </span>
                    <button className="btn btn-sm" onClick={() => load()} disabled={loading}>
                        {loading ? 'Загрузка…' : 'Повторить'}
                    </button>
                </div>
            )}
            {expanded && (
                <div style={{ marginTop: 12 }}>
                    {data?.truncated && (
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 6 }}>
                            Показаны первые {formatNumber(rows.length, 0)}{' '}
                            {pluralRu(rows.length, ['группа', 'группы', 'групп'])} — выдача срезана
                            сервером, итог «{formatNumber(total, 0)}» считает всё.
                        </div>
                    )}
                    <TanStackDataTable
                        columns={cols}
                        data={rows}
                        exportName="FBS_не_списано_со_склада"
                        enableSorting
                        enablePagination={rows.length > 50}
                        emptyText="Все переданные задания списаны"
                        // Очередь (queued) приглушена: это не проблема, а ожидание
                        // ближайшего прогона списания — алярм-строки читаются первыми.
                        rowClassName={(row: FbsWriteoffIssueRow) =>
                            row.reason === 'queued' ? 'fbs-row-queued' : ''}
                    />
                </div>
            )}
        </div>
    );
}
