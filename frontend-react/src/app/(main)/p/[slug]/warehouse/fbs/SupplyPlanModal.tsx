'use client';
/**
 * Предпросмотр разбиения выделенных заданий на поставки.
 *
 * WB запрещает класть в одну поставку задания разных складов и разных
 * габаритов (первое задание фиксирует cargoType и crossBorderType). Поэтому
 * при нескольких складах пользователь ФИЗИЧЕСКИ обязан завести несколько
 * поставок — мы показываем это до записи, а не ловим 409 от WB после.
 *
 * Сам план ничего не пишет и режимом контура не гейтится; гасится только
 * подтверждение (оно и создаёт поставки в кабинете).
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { FbsSupplyBulkResult, FbsSupplyPlan, FbsSupplyPlanGroup } from '@/types/api';
import { cargoLabel, crossBorderLabel, supplyBlockedLabel } from './fbsShared';

interface Props {
    orderIds: number[];
    /** Режим контура разрешает запись в WB — гасит только подтверждение. */
    writeEnabled: boolean;
    writeHint: string;
    onClose: () => void;
    /** Поставки созданы: обновить списки и показать тост. */
    onDone: (msg: string) => Promise<void> | void;
}

/** WB bulk: схема принимает максимум 2000 заданий за раз. */
export const WB_PLAN_LIMIT = 2000;

export default function SupplyPlanModal({ orderIds, writeEnabled, writeHint, onClose, onDone }: Props) {
    const [plan, setPlan] = useState<FbsSupplyPlan | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [actionError, setActionError] = useState('');
    const [result, setResult] = useState<FbsSupplyBulkResult | null>(null);
    const [namePrefix, setNamePrefix] = useState('');
    const [reuseExisting, setReuseExisting] = useState(true);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.planFbsSupplies(orderIds);
            if (signal?.aborted) return;
            setPlan(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка построения плана поставок');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
        // orderIds — новый массив на каждый рендер родителя, поэтому в зависимости
        // идёт стабильная строка, иначе useEffect зацикливает запрос плана.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [orderIds.join(',')]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    const groups = plan?.groups ?? [];
    const okGroups = groups.filter(g => !g.blocked_reason);
    const blockedGroups = groups.filter(g => !!g.blocked_reason);
    const blockedOrders = blockedGroups.reduce(
        (acc, g) => acc + (g.orders_count || g.order_ids.length), 0,
    );
    const plannedOrders = okGroups.reduce(
        (acc, g) => acc + (g.orders_count || g.order_ids.length), 0,
    );

    const handleConfirm = async () => {
        setBusy(true);
        setActionError('');
        try {
            const res = await api.createFbsSuppliesBulk({
                order_ids: orderIds,
                name_prefix: namePrefix.trim() || null,
                reuse_existing: reuseExisting,
            });
            setResult(res);
        } catch (e: unknown) {
            setActionError(e instanceof Error ? e.message : 'Ошибка создания поставок');
        } finally {
            setBusy(false);
        }
    };

    // ─── Результат ───────────────────────────────────────────────────────────
    if (result) {
        const created = result.created ?? [];
        const reused = result.reused ?? [];
        const errors = result.errors ?? [];
        // Поставки УЖЕ созданы: любое закрытие этого экрана обязано вести туда
        // же, куда «Готово». Тихий `onClose` по подложке оставлял бы таблицу с
        // заданиями в статусе «Новое» и старым выделением — повторный «В
        // поставку» показал бы «уже в поставке WB-GI-…» и читался как сбой.
        const finish = () => {
            const msg = `Поставок создано: ${created.length}`
                + (reused.length ? `, доложено: ${reused.length}` : '')
                + ` · заданий: ${result.orders_attached ?? 0}`;
            onDone(msg);
        };
        return (
            <div className="modal-overlay" onClick={finish}>
                <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 640 }}>
                    <h2 className="modal-title">Поставки готовы</h2>
                    <div style={{ fontSize: 14, marginBottom: 12 }}>
                        Заданий разложено: <strong>{formatNumber(result.orders_attached ?? 0, 0)}</strong>
                        {' · '}создано поставок: <strong>{formatNumber(created.length, 0)}</strong>
                        {reused.length > 0 && <> · доложено в существующие: <strong>{formatNumber(reused.length, 0)}</strong></>}
                    </div>
                    {[...created, ...reused].length > 0 && (
                        <ul style={{ margin: '0 0 12px', paddingLeft: 20, fontSize: 13 }}>
                            {created.map(s => (
                                <li key={`c-${s.wb_supply_id}`}>
                                    <span style={{ fontFamily: 'monospace' }}>{s.wb_supply_id}</span>
                                    {s.name ? ` — ${s.name}` : ''} · создана, заданий {formatNumber(s.orders_count, 0)}
                                </li>
                            ))}
                            {reused.map(s => (
                                <li key={`r-${s.wb_supply_id}`}>
                                    <span style={{ fontFamily: 'monospace' }}>{s.wb_supply_id}</span>
                                    {s.name ? ` — ${s.name}` : ''} · доложено, заданий {formatNumber(s.orders_count, 0)}
                                </li>
                            ))}
                        </ul>
                    )}
                    {errors.length > 0 && (
                        <div style={{ fontSize: 13, color: 'var(--color-danger)', marginBottom: 12 }}>
                            <div style={{ fontWeight: 600, marginBottom: 4 }}>Часть заданий не уехала:</div>
                            <ul style={{ margin: 0, paddingLeft: 20 }}>
                                {errors.map((e, i) => <li key={i}>{e}</li>)}
                            </ul>
                        </div>
                    )}
                    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                        <button className="btn btn-primary" onClick={finish}>
                            Готово
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    // ─── План ────────────────────────────────────────────────────────────────
    return (
        <div className="modal-overlay" onClick={busy ? undefined : onClose}>
            <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 760 }}>
                <h2 className="modal-title">В поставку: {formatNumber(orderIds.length, 0)} заданий</h2>

                {loading ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                        Считаем разбиение...
                    </div>
                ) : error ? (
                    <>
                        <div style={{ padding: 16, color: 'var(--color-danger)', fontSize: 13 }}>{error}</div>
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={onClose}>Закрыть</button>
                            <button className="btn btn-primary" onClick={() => load()}>Повторить</button>
                        </div>
                    </>
                ) : groups.length === 0 ? (
                    <>
                        <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                            Ни одно выделенное задание в поставку не пойдёт — они уже разложены
                            или в терминальном статусе.
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={onClose}>Закрыть</button>
                        </div>
                    </>
                ) : (
                    <>
                        <p style={{ fontSize: 14, marginBottom: 12 }}>
                            Получится <strong>{formatNumber(okGroups.length, 0)}</strong>
                            {' '}{plural(okGroups.length, 'поставка', 'поставки', 'поставок')}
                            {' '}на <strong>{formatNumber(plannedOrders, 0)}</strong> заданий.
                            {okGroups.length > 1 && (
                                <span style={{ color: 'var(--color-text-muted)' }}>
                                    {' '}Одна поставка = один склад и один габарит: смешивать WB не разрешает.
                                </span>
                            )}
                        </p>

                        <div style={{ overflowX: 'auto', marginBottom: 12 }}>
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Склад WB</th>
                                        <th>Габарит</th>
                                        <th style={{ textAlign: 'right' }}>Заданий</th>
                                        <th>Что сделаем</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {okGroups.map((g, i) => (
                                        <tr key={`${g.wb_warehouse_id ?? 'none'}-${g.cargo_type ?? 0}-${i}`}>
                                            <td>{warehouseTitle(g)}</td>
                                            <td style={{ fontSize: 13 }}>
                                                {cargoLabel(g.cargo_type)}
                                                <span style={{ color: 'var(--color-text-muted)' }}>
                                                    {' · '}{crossBorderLabel(g.cross_border_type)}
                                                </span>
                                            </td>
                                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                {formatNumber(g.orders_count || g.order_ids.length, 0)}
                                            </td>
                                            <td style={{ fontSize: 13 }}>
                                                {reuseExisting && g.existing_supply_id ? (
                                                    <>
                                                        доложим в{' '}
                                                        <span style={{ fontFamily: 'monospace' }}>
                                                            {g.existing_supply_id}
                                                        </span>
                                                    </>
                                                ) : (
                                                    <span className="badge badge-info">создадим новую</span>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>

                        {blockedGroups.length > 0 && (
                            <div style={{ marginBottom: 12 }}>
                                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-warning)', marginBottom: 4 }}>
                                    Не поедут: {formatNumber(blockedOrders, 0)}
                                </div>
                                <ul style={{ margin: 0, paddingLeft: 20, fontSize: 13, color: 'var(--color-text-muted)' }}>
                                    {blockedGroups.map((g, i) => (
                                        <li key={`b-${i}`}>
                                            {formatNumber(g.orders_count || g.order_ids.length, 0)} заданий
                                            {' — '}{supplyBlockedLabel(g.blocked_reason || '')}
                                            {g.order_ids.length > 0 && (
                                                <span style={{ color: 'var(--color-text-dim)' }}>
                                                    {' ('}{g.order_ids.slice(0, 5).join(', ')}
                                                    {g.order_ids.length > 5 ? ' …' : ''}
                                                    {')'}
                                                </span>
                                            )}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12 }}>
                            <div style={{ flex: 1, minWidth: 220 }}>
                                <label className="form-label" style={{ fontSize: 12 }}>Префикс названия</label>
                                <input className="form-input" value={namePrefix} maxLength={80}
                                    placeholder="Например: FBS 24.07"
                                    onChange={e => setNamePrefix(e.target.value)} />
                            </div>
                            <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center', paddingBottom: 8 }}>
                                <input type="checkbox" checked={reuseExisting}
                                    onChange={e => setReuseExisting(e.target.checked)} />
                                Докладывать в активные поставки
                            </label>
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                            К префиксу добавится склад, чтобы поставки различались. Задания перейдут
                            в статус «На сборке»; поставка закроется сама при приёмке первого товара —
                            доложить в неё после этого нельзя.
                        </div>

                        {actionError && (
                            <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>{actionError}</div>
                        )}

                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={onClose} disabled={busy}>Отмена</button>
                            <button
                                className="btn btn-primary"
                                onClick={handleConfirm}
                                disabled={busy || !writeEnabled || okGroups.length === 0}
                                title={writeEnabled ? undefined : writeHint}
                            >
                                {busy
                                    ? 'Создание...'
                                    : `Создать ${formatNumber(okGroups.length, 0)} ${plural(okGroups.length, 'поставку', 'поставки', 'поставок')}`}
                            </button>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}

function warehouseTitle(g: FbsSupplyPlanGroup): string {
    if (g.wb_warehouse_name) return g.wb_warehouse_name;
    return g.wb_warehouse_id != null ? `#${g.wb_warehouse_id}` : 'без склада';
}

/** Русские окончания счётного слова — «1 поставка», «2 поставки», «5 поставок». */
function plural(n: number, one: string, few: string, many: string): string {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
}
