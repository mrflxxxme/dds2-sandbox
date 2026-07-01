'use client';
import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import { Toast } from '@/components';
import type { PreDistVehicle } from '@/types/api';

/** Вкладка «🚚 Предраспределение машин в пути» — каталог машин (CostOrder CUSTOMS/DISPATCHED).
 *  «Распределить» открывает отдельный полноэкранный экран (`./pre-dist?vehicle=<id>`),
 *  где идёт авто-раскладка груза машины по WB-складам как в «Потребность по складам». */
export default function PreDistributionView() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;

    const [vehicles, setVehicles] = useState<PreDistVehicle[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [advancingId, setAdvancingId] = useState<number | null>(null);

    const showToast = useCallback((message: string, type: 'success' | 'error') => setToast({ message, type }), []);

    const loadVehicles = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError(null);
        try {
            const data = await api.getPreDistVehicles();
            if (signal?.aborted) return;
            setVehicles(data);
        } catch (e) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки машин');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        loadVehicles(controller.signal);
        return () => controller.abort();
    }, [loadVehicles]);

    const openVehicle = useCallback(
        (v: PreDistVehicle) => router.push(`/p/${slug}/warehouse/assembly/distribute/pre-dist?vehicle=${v.id}`),
        [router, slug],
    );

    const handleAdvance = useCallback(async (vehicle: PreDistVehicle) => {
        setAdvancingId(vehicle.id);
        try {
            const res = await api.advancePreDistribution(vehicle.id);
            showToast(`Переведено в сборку: ${formatNumber(res.advanced, 0)}`, 'success');
            await loadVehicles();
        } catch (e) {
            showToast(e instanceof Error ? e.message : 'Ошибка перевода в сборку', 'error');
        } finally {
            setAdvancingId(null);
        }
    }, [showToast, loadVehicles]);

    // ─── States: loading / error / empty / data ───────────────────────────
    if (loading) {
        return <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>Загрузка машин в пути…</div>;
    }
    if (error) {
        return (
            <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>
                <div style={{ color: 'var(--color-danger)', marginBottom: 16 }}>{error}</div>
                <button className="btn btn-secondary" onClick={() => loadVehicles()}>Повторить</button>
            </div>
        );
    }
    if (!vehicles || vehicles.length === 0) {
        return <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>Нет машин в пути для предраспределения</div>;
    }

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}

            <div className="glass-card" style={{ padding: 16, color: 'var(--color-muted)', fontSize: 13 }}>
                Машины «Таможня» / «Отправлено» везут товар, ещё не принятый на ФФ. «Распределить» открывает
                экран раскладки груза по WB-складам (как «Потребность по складам»: потребность · приёмка ·
                целые коробы и паллеты), источник — остатки именно этой машины. Заявки создаются со статусом
                «Предраспределение» (без фейкового стока); при разгрузке машины станут обычными сборками.
            </div>

            <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                    <thead>
                        <tr style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-muted)', textAlign: 'left' }}>
                            <th style={{ padding: '12px 16px' }}>Машина</th>
                            <th style={{ padding: '12px 16px' }}>Статус</th>
                            <th style={{ padding: '12px 16px' }}>Склад назначения</th>
                            <th style={{ padding: '12px 16px' }}>ETA</th>
                            <th style={{ padding: '12px 16px', textAlign: 'right' }}>Всего, шт</th>
                            <th style={{ padding: '12px 16px', textAlign: 'right' }}>SKU</th>
                            <th style={{ padding: '12px 16px', textAlign: 'right' }}>Распределено</th>
                            <th style={{ padding: '12px 16px', textAlign: 'right' }}>Действия</th>
                        </tr>
                    </thead>
                    <tbody>
                        {vehicles.map(v => {
                            const remaining = (Number(v.total_qty) || 0) - (Number(v.distributed_qty) || 0);
                            return (
                                <tr key={v.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                    <td style={{ padding: '12px 16px', fontWeight: 600 }}>{v.order_no}</td>
                                    <td style={{ padding: '12px 16px' }}><span className="badge badge-info">{v.status}</span></td>
                                    <td style={{ padding: '12px 16px', color: v.target_warehouse_name ? 'var(--color-text)' : 'var(--color-muted)' }}>{v.target_warehouse_name || '—'}</td>
                                    <td style={{ padding: '12px 16px', color: 'var(--color-muted)' }}>{v.eta ? formatDate(v.eta) : '—'}</td>
                                    <td style={{ padding: '12px 16px', textAlign: 'right' }}>{formatNumber(v.total_qty, 0)}</td>
                                    <td style={{ padding: '12px 16px', textAlign: 'right' }}>{formatNumber(v.sku_count, 0)}</td>
                                    <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                                        {formatNumber(v.distributed_qty, 0)}
                                        {remaining > 0 && <span style={{ color: 'var(--color-muted)', fontSize: 12 }}> / ост. {formatNumber(remaining, 0)}</span>}
                                    </td>
                                    <td style={{ padding: '12px 16px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        <button className="btn btn-primary btn-sm" disabled={!v.can_distribute} title={v.can_distribute ? undefined : (v.block_reason || 'Распределение недоступно')} onClick={() => openVehicle(v)}>Распределить</button>
                                        {v.distributed_qty > 0 && (
                                            <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} disabled={advancingId === v.id} title="Перевести предраспределённые заявки в обычную сборку (если авто-хук разгрузки не сработал)" onClick={() => handleAdvance(v)}>
                                                {advancingId === v.id ? 'Перевод…' : 'В сборку'}
                                            </button>
                                        )}
                                        {!v.can_distribute && v.block_reason && <div style={{ color: 'var(--color-warning)', fontSize: 12, marginTop: 4 }}>{v.block_reason}</div>}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
