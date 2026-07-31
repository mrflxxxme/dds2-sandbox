'use client';
/**
 * Вкладка «Переезды» на «Листе логиста» — стоимость логистики переездов между
 * нашими складами (ФФ → ФФ, транзит).
 *
 * Почему отдельный экран, а не строки в общих отчётах логистики: там маршруты
 * «склад → склад WB» и метрики завязаны на поставку маркетплейса. Переезд туда
 * не ложится принципиально (нет WB-поставки, другой смысл ₽/шт), и смешение
 * ломало бы обе картины. Поэтому — свой отчёт со своим эндпоинтом.
 *
 * Денежные поля приходят СТРОКОЙ (Numeric) — везде через toMoney().
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import {
    CartesianGrid,
    ComposedChart,
    Bar,
    Legend as RechartsLegend,
    Line,
    ResponsiveContainer,
    Tooltip as RechartsTooltip,
    XAxis,
    YAxis,
} from 'recharts';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import KpiCard from '@/components/KpiCard';
import type { Column } from '@/components/DataTable';
import type {
    TransferLogisticsCarrierRow,
    TransferLogisticsDetailRow,
    TransferLogisticsReport,
    TransferLogisticsRouteRow,
    Warehouse,
} from '@/types/api';
import { TRANSFER_STATUS_MAP, toMoney, transferReportDefaultRange, unitCountText, unitShort } from '@/lib/transfer';

type GroupBy = 'day' | 'week' | 'month';

const GROUPS: { key: GroupBy; label: string }[] = [
    { key: 'day', label: 'День' },
    { key: 'week', label: 'Неделя' },
    { key: 'month', label: 'Месяц' },
];

const rub = (v: string | number | null | undefined, digits = 0): string => {
    const n = toMoney(v ?? null);
    return n === null ? '—' : `${formatNumber(n, digits)} ₽`;
};

// Период по умолчанию — общий с мини-сводкой на вкладке «Перемещения»
// (lib/transfer.ts), иначе цифры двух экранов не сойдутся.
const DEFAULT_RANGE = transferReportDefaultRange();

/** Справочник складов приходит пропом от страницы: два блока переездов на
 *  «Листе логиста» качали его независимо и заново при каждом переключении. */
export default function TransferLogisticsTab({ warehouses }: { warehouses: Warehouse[] }) {
    const params = useParams();
    const slug = params.slug as string;

    // Даты ведём в двух состояниях: ввод — мгновенный, запрос — отложенный.
    // <input type="date"> в Chrome шлёт change на каждом валидном промежуточном
    // значении, и набор года посегментно давал 4 полных запроса отчёта.
    // Кнопочные фильтры (группировка, склады, перевозчик) debounce не трогает —
    // там один клик = одно осознанное значение.
    const [dateFromInput, setDateFromInput] = useState(DEFAULT_RANGE.date_from);
    const [dateToInput, setDateToInput] = useState(DEFAULT_RANGE.date_to);
    const [dateFrom, setDateFrom] = useState(DEFAULT_RANGE.date_from);
    const [dateTo, setDateTo] = useState(DEFAULT_RANGE.date_to);

    useEffect(() => {
        const t = setTimeout(() => {
            setDateFrom(dateFromInput);
            setDateTo(dateToInput);
        }, 400);
        return () => clearTimeout(t);
    }, [dateFromInput, dateToInput]);
    const [groupBy, setGroupBy] = useState<GroupBy>('month');
    const [fromWarehouseId, setFromWarehouseId] = useState<number | ''>('');
    const [toWarehouseId, setToWarehouseId] = useState<number | ''>('');
    const [counterpartyId, setCounterpartyId] = useState<number | ''>('');

    const [data, setData] = useState<TransferLogisticsReport | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    // Список перевозчиков берём из НЕотфильтрованного ответа: иначе выбранный
    // перевозчик остался бы единственным вариантом и фильтр стало бы не снять.
    const [carrierOptions, setCarrierOptions] = useState<TransferLogisticsCarrierRow[]>([]);
    const loadSeq = useRef(0);

    const load = useCallback(async () => {
        const seq = ++loadSeq.current;
        setLoading(true);
        setError('');
        try {
            const r = await api.getTransferLogisticsReport({
                date_from: dateFrom || undefined,
                date_to: dateTo || undefined,
                group_by: groupBy,
                from_warehouse_id: fromWarehouseId === '' ? undefined : fromWarehouseId,
                to_warehouse_id: toWarehouseId === '' ? undefined : toWarehouseId,
                counterparty_id: counterpartyId === '' ? undefined : counterpartyId,
            });
            if (seq !== loadSeq.current) return; // устаревший ответ (быстрые клики по фильтрам)
            setData(r);
            if (counterpartyId === '' && fromWarehouseId === '' && toWarehouseId === '') {
                setCarrierOptions(r.by_carrier.filter(c => c.counterparty_id != null));
            }
        } catch (e: unknown) {
            if (seq !== loadSeq.current) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (seq === loadSeq.current) setLoading(false);
        }
    }, [dateFrom, dateTo, groupBy, fromWarehouseId, toWarehouseId, counterpartyId]);

    useEffect(() => { load(); }, [load]);

    const chartData = useMemo(() => (data?.by_period ?? []).map(p => ({
        period: p.period,
        total_cost: toMoney(p.total_cost) ?? 0,
        cost_per_unit: toMoney(p.cost_per_unit) ?? 0,
    })), [data]);

    const routeCols: Column[] = [
        {
            key: 'route', label: 'Откуда → Куда',
            getValue: (row: TransferLogisticsRouteRow) => `${row.from_warehouse ?? row.from_warehouse_id} → ${row.to_warehouse ?? row.to_warehouse_id}`,
            render: (_v, row: TransferLogisticsRouteRow) => (
                <span>
                    {row.from_warehouse ?? `Склад ${row.from_warehouse_id}`}
                    <span style={{ color: 'var(--color-text-muted)' }}> → </span>
                    {row.to_warehouse ?? `Склад ${row.to_warehouse_id}`}
                </span>
            ),
            exportValue: (row: TransferLogisticsRouteRow) => `${row.from_warehouse ?? row.from_warehouse_id} → ${row.to_warehouse ?? row.to_warehouse_id}`,
        },
        {
            key: 'transfers_count', label: 'Переездов', align: 'right',
            render: (v: number) => formatNumber(v, 0),
        },
        {
            key: 'total_cost', label: 'Сумма', align: 'right',
            getValue: (row: TransferLogisticsRouteRow) => toMoney(row.total_cost) ?? 0,
            render: (_v, row: TransferLogisticsRouteRow) => rub(row.total_cost),
            exportValue: (row: TransferLogisticsRouteRow) => toMoney(row.total_cost) ?? '',
        },
        {
            key: 'avg_cost', label: 'Средняя', align: 'right',
            getValue: (row: TransferLogisticsRouteRow) => toMoney(row.avg_cost) ?? 0,
            render: (_v, row: TransferLogisticsRouteRow) => rub(row.avg_cost),
            exportValue: (row: TransferLogisticsRouteRow) => toMoney(row.avg_cost) ?? '',
        },
        {
            key: 'total_units', label: 'Штук', align: 'right',
            render: (v: number) => formatNumber(v, 0),
        },
        {
            key: 'cost_per_unit', label: '₽/шт', align: 'right',
            getValue: (row: TransferLogisticsRouteRow) => toMoney(row.cost_per_unit) ?? 0,
            render: (_v, row: TransferLogisticsRouteRow) => rub(row.cost_per_unit, 2),
            exportValue: (row: TransferLogisticsRouteRow) => toMoney(row.cost_per_unit) ?? '',
        },
        {
            key: 'total_pallets', label: 'Паллет', align: 'right',
            headerTitle: 'Паллеты паллетных переездов маршрута; коробочные сюда не входят',
            render: (v: number) => formatNumber(v ?? 0, 0),
        },
        {
            key: 'cost_per_pallet', label: '₽/паллета', align: 'right',
            headerTitle: 'Считается только по паллетным переездам маршрута',
            getValue: (row: TransferLogisticsRouteRow) => toMoney(row.cost_per_pallet) ?? 0,
            render: (_v, row: TransferLogisticsRouteRow) => rub(row.cost_per_pallet, 2),
            exportValue: (row: TransferLogisticsRouteRow) => toMoney(row.cost_per_pallet) ?? '',
        },
    ];

    const carrierCols: Column[] = [
        {
            key: 'counterparty_name', label: 'Перевозчик',
            render: (_v, row: TransferLogisticsCarrierRow) => row.counterparty_name || 'Не указан',
        },
        {
            key: 'transfers_count', label: 'Переездов', align: 'right',
            render: (v: number) => formatNumber(v, 0),
        },
        {
            key: 'total_cost', label: 'Сумма', align: 'right',
            getValue: (row: TransferLogisticsCarrierRow) => toMoney(row.total_cost) ?? 0,
            render: (_v, row: TransferLogisticsCarrierRow) => rub(row.total_cost),
            exportValue: (row: TransferLogisticsCarrierRow) => toMoney(row.total_cost) ?? '',
        },
        {
            key: 'avg_cost', label: 'Средняя', align: 'right',
            getValue: (row: TransferLogisticsCarrierRow) => toMoney(row.avg_cost) ?? 0,
            render: (_v, row: TransferLogisticsCarrierRow) => rub(row.avg_cost),
            exportValue: (row: TransferLogisticsCarrierRow) => toMoney(row.avg_cost) ?? '',
        },
    ];

    const rowCols: Column[] = [
        {
            key: 'transfer_number', label: 'Переезд', width: '120px',
            render: (_v, row: TransferLogisticsDetailRow) => (
                <Link
                    href={`/p/${slug}/warehouse/transfers/${row.transfer_id}`}
                    onClick={e => e.stopPropagation()}
                    style={{ color: 'var(--color-accent)', fontWeight: 600 }}
                    title="Открыть карточку перемещения"
                >
                    {row.transfer_number}
                </Link>
            ),
            exportValue: (row: TransferLogisticsDetailRow) => row.transfer_number,
        },
        {
            key: 'shipped_date', label: 'Дата', width: '110px',
            render: (_v, row: TransferLogisticsDetailRow) => formatDate(row.shipped_date),
            exportValue: (row: TransferLogisticsDetailRow) => row.shipped_date || '',
        },
        {
            key: 'route', label: 'Маршрут',
            getValue: (row: TransferLogisticsDetailRow) => `${row.from_warehouse ?? ''} → ${row.to_warehouse ?? ''}`,
            render: (_v, row: TransferLogisticsDetailRow) => (
                <span>
                    {row.from_warehouse ?? '—'}
                    <span style={{ color: 'var(--color-text-muted)' }}> → </span>
                    {row.to_warehouse ?? '—'}
                </span>
            ),
            exportValue: (row: TransferLogisticsDetailRow) => `${row.from_warehouse ?? ''} → ${row.to_warehouse ?? ''}`,
        },
        {
            key: 'vehicle_info', label: 'Машина', width: '130px',
            render: (_v, row: TransferLogisticsDetailRow) => row.vehicle_info || '—',
        },
        {
            key: 'counterparty_name', label: 'Перевозчик',
            render: (_v, row: TransferLogisticsDetailRow) => row.counterparty_name || '—',
        },
        {
            key: 'pickup_cost', label: 'Стоимость', align: 'right', width: '120px',
            getValue: (row: TransferLogisticsDetailRow) => toMoney(row.pickup_cost) ?? 0,
            render: (_v, row: TransferLogisticsDetailRow) => rub(row.pickup_cost),
            exportValue: (row: TransferLogisticsDetailRow) => toMoney(row.pickup_cost) ?? '',
        },
        {
            key: 'pallets_count', label: 'Кол-во ед.', align: 'right', width: '120px',
            headerTitle: 'Транспортная единица переезда: паллеты или короба',
            getValue: (row: TransferLogisticsDetailRow) => row.pallets_count ?? -1,
            render: (_v, row: TransferLogisticsDetailRow) => (
                row.pallets_count == null ? '—' : unitCountText(row.pallets_count, row.shipped_as_boxes)
            ),
            exportValue: (row: TransferLogisticsDetailRow) => (
                row.pallets_count == null ? '' : `${row.pallets_count} ${unitShort(row.shipped_as_boxes)}`
            ),
        },
        {
            key: 'units_total', label: 'Штук / SKU', align: 'right', width: '120px',
            getValue: (row: TransferLogisticsDetailRow) => row.units_total,
            render: (_v, row: TransferLogisticsDetailRow) => (
                <span>
                    {formatNumber(row.units_total, 0)}
                    <span style={{ color: 'var(--color-text-muted)' }}> / </span>
                    {formatNumber(row.sku_count, 0)}
                </span>
            ),
            exportValue: (row: TransferLogisticsDetailRow) => `${row.units_total} / ${row.sku_count}`,
        },
        {
            key: 'transfer_status', label: 'Статус', width: '120px',
            render: (_v, row: TransferLogisticsDetailRow) => {
                const st = TRANSFER_STATUS_MAP[row.transfer_status as keyof typeof TRANSFER_STATUS_MAP];
                return <span className={`badge ${st?.className ?? 'badge-secondary'}`}>{st?.label ?? row.transfer_status}</span>;
            },
            exportValue: (row: TransferLogisticsDetailRow) =>
                TRANSFER_STATUS_MAP[row.transfer_status as keyof typeof TRANSFER_STATUS_MAP]?.label ?? row.transfer_status,
        },
        {
            key: 'is_paid', label: 'Оплата', width: '150px',
            getValue: (row: TransferLogisticsDetailRow) => (row.is_paid ? 1 : 0),
            render: (_v, row: TransferLogisticsDetailRow) => (
                row.is_paid
                    ? <span className="badge badge-success" title={row.payment_request_number || 'Оплачено'}>
                        {row.payment_request_number || 'Оплачено'}
                    </span>
                    : <span className="badge badge-warning" title="Заявки на оплату нет либо она не проведена">не оплачено</span>
            ),
            exportValue: (row: TransferLogisticsDetailRow) =>
                row.is_paid ? (row.payment_request_number || 'Оплачено') : 'не оплачено',
        },
    ];

    const s = data?.summary;
    const isEmpty = !s || s.transfers_count === 0;
    const unpaid = toMoney(s?.unpaid_cost ?? null);
    // В выборке есть и паллетные, и коробочные переезды → ₽/паллета покрывает
    // только часть набора, и это надо сказать вслух.
    const mixedUnits = !!s && (s.total_pallets ?? 0) > 0 && (s.total_boxes ?? 0) > 0;
    // Только коробочные переезды: cost_per_pallet приходит null, total_pallets = 0.
    // Это не ошибка данных — так и подписываем, иначе «—» читается как поломка.
    const boxesOnly = !!s && (s.total_pallets ?? 0) === 0 && (s.total_boxes ?? 0) > 0;

    return (
        <div className="animate-in">
            {/* Фильтры */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Период с</label>
                        <input className="form-input" type="date" value={dateFromInput} onChange={e => setDateFromInput(e.target.value)} />
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">по</label>
                        <input className="form-input" type="date" value={dateToInput} onChange={e => setDateToInput(e.target.value)} />
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Динамика по</label>
                        <div style={{ display: 'flex', gap: 0 }}>
                            {GROUPS.map((g, i) => (
                                <button
                                    key={g.key}
                                    className={`btn btn-sm ${groupBy === g.key ? 'btn-primary' : 'btn-secondary'}`}
                                    onClick={() => setGroupBy(g.key)}
                                    style={{ borderRadius: i === 0 ? '8px 0 0 8px' : i === GROUPS.length - 1 ? '0 8px 8px 0' : 0 }}
                                >
                                    {g.label}
                                </button>
                            ))}
                        </div>
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Откуда</label>
                        <select
                            className="form-input"
                            value={fromWarehouseId}
                            onChange={e => setFromWarehouseId(e.target.value ? Number(e.target.value) : '')}
                        >
                            <option value="">Все склады</option>
                            {warehouses.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                        </select>
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Куда</label>
                        <select
                            className="form-input"
                            value={toWarehouseId}
                            onChange={e => setToWarehouseId(e.target.value ? Number(e.target.value) : '')}
                        >
                            <option value="">Все склады</option>
                            {warehouses.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                        </select>
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Перевозчик</label>
                        <select
                            className="form-input"
                            value={counterpartyId}
                            onChange={e => setCounterpartyId(e.target.value ? Number(e.target.value) : '')}
                        >
                            <option value="">Все перевозчики</option>
                            {carrierOptions.map(c => (
                                <option key={c.counterparty_id!} value={c.counterparty_id!}>
                                    {c.counterparty_name || `Контрагент #${c.counterparty_id}`}
                                </option>
                            ))}
                        </select>
                    </div>
                </div>
            </div>

            {loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            ) : error ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-danger)' }}>
                    {error}
                    <div style={{ marginTop: 12 }}>
                        <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
                    </div>
                </div>
            ) : isEmpty ? (
                <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>🚚</div>
                    <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>За период переездов не было</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                        Расширьте период или снимите фильтры по складам и перевозчику
                    </div>
                </div>
            ) : (
                <>
                    {/* Плитки-итоги */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 16 }}>
                        <KpiCard label="Переездов" value={formatNumber(s!.transfers_count, 0)} />
                        <KpiCard label="Стоимость" value={rub(s!.total_cost)} />
                        <KpiCard label="Средняя за переезд" value={rub(s!.avg_cost)} />
                        <KpiCard label="₽ на штуку" value={rub(s!.cost_per_unit, 2)} />
                        {/* ₽/паллета считается ТОЛЬКО по паллетным переездам: короб и
                            паллета — разные единицы, их знаменатели не складываются.
                            Если в выборке есть и те и другие — подписываем явно,
                            иначе цифра читалась бы как средняя по всему. */}
                        <KpiCard
                            label="₽ на паллету"
                            value={rub(s!.cost_per_pallet, 2)}
                            sub={boxesOnly ? 'в выборке только короба' : mixedUnits ? 'только по паллетным' : undefined}
                        />
                        <KpiCard label="Штук" value={formatNumber(s!.total_units, 0)} />
                        <KpiCard label="Оплачено" value={rub(s!.paid_cost)} color="#22c55e" />
                        <KpiCard
                            label="Не оплачено"
                            value={rub(s!.unpaid_cost)}
                            color={unpaid && unpaid > 0 ? '#f59e0b' : undefined}
                            sub={unpaid && unpaid > 0 ? 'ждёт заявки на оплату' : undefined}
                        />
                    </div>

                    {/* Динамика по периодам */}
                    <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Динамика стоимости переездов</div>
                        {chartData.length > 0 ? (
                            <ResponsiveContainer width="100%" height={300}>
                                <ComposedChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                                    <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                                    <XAxis dataKey="period" tick={{ fontSize: 11 }} angle={-25} textAnchor="end" height={60} interval={0} />
                                    <YAxis yAxisId="left" tick={{ fontSize: 12 }} />
                                    <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 12 }} />
                                    <RechartsTooltip
                                        formatter={(value: number, n: string) => [
                                            `${formatNumber(value, n === 'cost_per_unit' ? 2 : 0)} ₽`,
                                            n === 'cost_per_unit' ? '₽/шт' : 'Стоимость',
                                        ]}
                                        contentStyle={{ background: 'var(--color-bg-primary)', border: '1px solid var(--color-border)', borderRadius: 8, fontSize: 13 }}
                                    />
                                    <RechartsLegend formatter={(v: string) => (v === 'cost_per_unit' ? '₽/шт' : 'Стоимость')} />
                                    <Bar yAxisId="left" dataKey="total_cost" name="total_cost" fill="var(--color-accent)" opacity={0.7} />
                                    <Line yAxisId="right" type="monotone" dataKey="cost_per_unit" name="cost_per_unit" stroke="var(--color-warning)" strokeWidth={2} dot={{ r: 3 }} />
                                </ComposedChart>
                            </ResponsiveContainer>
                        ) : (
                            <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Нет точек за период</div>
                        )}
                    </div>

                    {/* По маршрутам */}
                    <div style={{ marginBottom: 16 }}>
                        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>По маршрутам</div>
                        <TanStackDataTable
                            columns={routeCols}
                            data={data!.by_route}
                            emptyText="Нет данных"
                            emptyIcon="🗺️"
                            exportName="transfer_logistics_by_route"
                        />
                    </div>

                    {/* По перевозчикам */}
                    <div style={{ marginBottom: 16 }}>
                        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>По перевозчикам</div>
                        <TanStackDataTable
                            columns={carrierCols}
                            data={data!.by_carrier}
                            emptyText="Нет данных"
                            emptyIcon="🚚"
                            exportName="transfer_logistics_by_carrier"
                        />
                    </div>

                    {/* Построчный список переездов */}
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Переезды за период</div>
                        <TanStackDataTable
                            columns={rowCols}
                            data={data!.rows}
                            emptyText="Нет переездов"
                            emptyIcon="📦"
                            pageSize={50}
                            exportName="transfer_logistics_rows"
                        />
                    </div>
                </>
            )}
        </div>
    );
}
