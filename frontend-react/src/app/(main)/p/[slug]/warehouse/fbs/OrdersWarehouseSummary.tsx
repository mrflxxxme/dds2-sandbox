'use client';
/**
 * Сводка «сколько заданий ждёт на каждом складе WB» — прямой ответ на вопрос
 * «а если у меня будет несколько складов»: работа с FBS всегда идёт по одному
 * складу за раз (в поставку нельзя смешивать склады), и сборщику надо видеть,
 * где очередь.
 *
 * Данные берутся счётчиками status_counts: по запросу на склад с limit=1 —
 * это НЕ N+1 в смысле нагрузки (складов продавца единицы, тело ответа —
 * только цифры, на бэке список кэширован), зато цифра всегда честная и не
 * ограничена страницей выдачи.
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { FbsWarehouse } from '@/types/api';

interface Props {
    warehouses: FbsWarehouse[];
    /** Меняется после синка/мутаций заданий — повод пересчитать сводку. */
    reloadKey: number;
    /** Клик по цифре — фильтр «этот склад + этот статус». */
    onPick: (wbWarehouseId: number | '', status: string) => void;
}

interface WhCounts {
    wbWarehouseId: number | '';
    name: string;
    isNew: number;
    confirm: number;
}

export default function OrdersWarehouseSummary({ warehouses, reloadKey, onPick }: Props) {
    const [rows, setRows] = useState<WhCounts[] | null>(null);
    const [total, setTotal] = useState<WhCounts | null>(null);
    const [orphan, setOrphan] = useState<WhCounts | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const [allRes, perWh] = await Promise.all([
                api.getFbsOrders({ limit: 1 }),
                Promise.all(
                    warehouses.map(w =>
                        api.getFbsOrders({ wbWarehouseId: w.wb_warehouse_id, limit: 1 })
                            .then(r => ({ w, counts: r.status_counts ?? {} })),
                    ),
                ),
            ]);
            if (signal?.aborted) return;

            const list: WhCounts[] = perWh.map(({ w, counts }) => ({
                wbWarehouseId: w.wb_warehouse_id,
                name: w.name || `#${w.wb_warehouse_id}`,
                isNew: counts.new ?? 0,
                confirm: counts.confirm ?? 0,
            }));
            const allCounts = allRes.status_counts ?? {};
            const totals: WhCounts = {
                wbWarehouseId: '',
                name: 'Все склады',
                isNew: allCounts.new ?? 0,
                confirm: allCounts.confirm ?? 0,
            };
            // Задания склада, которого нет в нашем справочнике, иначе бы просто
            // потерялись из сводки — показываем остаток честной строкой.
            const sumNew = list.reduce((a, r) => a + r.isNew, 0);
            const sumConfirm = list.reduce((a, r) => a + r.confirm, 0);
            const restNew = Math.max(0, totals.isNew - sumNew);
            const restConfirm = Math.max(0, totals.confirm - sumConfirm);

            setRows(list);
            setTotal(totals);
            setOrphan(restNew + restConfirm > 0
                ? { wbWarehouseId: '', name: 'Без склада в справочнике', isNew: restNew, confirm: restConfirm }
                : null);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки сводки по складам');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [warehouses]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
        // reloadKey — внешний триггер пересчёта после синка/раскладки заданий
    }, [load, reloadKey]);

    if (loading && rows === null) {
        return (
            <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-text-muted)' }}>
                Сводка по складам: загрузка...
            </div>
        );
    }

    if (error) {
        return (
            <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>
                {error}
                <button className="btn btn-sm" style={{ marginLeft: 12 }} onClick={() => load()}>Повторить</button>
            </div>
        );
    }

    if (warehouses.length === 0) {
        return (
            <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-text-muted)' }}>
                Складов продавца нет — заведите их на вкладке «Склады», иначе задания некуда собирать.
            </div>
        );
    }

    const cards = [
        ...(total ? [total] : []),
        ...(rows ?? []),
        ...(orphan ? [orphan] : []),
    ];

    return (
        <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 6 }}>
                Очередь по складам WB — в одну поставку задания разных складов не кладутся
            </div>
            <div style={{
                display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 12,
            }}>
                {cards.map((c, i) => (
                    <div key={`${c.name}-${i}`} className="glass-card" style={{ padding: '12px 16px' }}>
                        <div style={{
                            fontSize: 13, fontWeight: 600, marginBottom: 8,
                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }} title={c.name}>
                            {c.name}
                        </div>
                        <div style={{ display: 'flex', gap: 16 }}>
                            <CountCell label="Новые" value={c.isNew} accent
                                onClick={() => onPick(c.wbWarehouseId, 'new')} />
                            <CountCell label="На сборке" value={c.confirm}
                                onClick={() => onPick(c.wbWarehouseId, 'confirm')} />
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

function CountCell({ label, value, accent, onClick }: {
    label: string;
    value: number;
    accent?: boolean;
    onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            title={`Показать: ${label.toLowerCase()}`}
            style={{
                background: 'none', border: 'none', padding: 0, cursor: 'pointer', textAlign: 'left',
            }}
        >
            <div style={{
                fontSize: 20, fontWeight: 700,
                color: accent && value > 0 ? 'var(--color-accent)' : undefined,
            }}>
                {formatNumber(value, 0)}
            </div>
            <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{label}</div>
        </button>
    );
}
