'use client';
/**
 * Вкладка «Тарифы ФФ» на странице ФФ-склада:
 *   1) тарифы 5 услуг ФФ (текущая ставка + история, новая ставка закрывает старую на бэке);
 *   2) блок «Хранение по дням» — посуточные срезы штуки/короба/паллеты × ставка = ₽.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { FfServiceType, FfStorageDailyRow, FfTariffRow, FfTariffUnit } from '@/types/api';

// ─── Справочник услуг ───────────────────────────────────────────────────────

const SERVICES: { type: FfServiceType; label: string; unitLabel: string }[] = [
    { type: 'PALLETIZING',    label: 'Паллетирование',    unitLabel: '₽/паллета' },
    { type: 'BOX_PROCESSING', label: 'Обработка коробки', unitLabel: '₽/короб' },
    { type: 'STORAGE',        label: 'Хранение',          unitLabel: '₽/паллета/день' },
    { type: 'TRUCK_UNLOADING', label: 'Выгрузка фуры',    unitLabel: '₽/машина' },
    { type: 'LOADING',        label: 'Погрузка',          unitLabel: '' }, // единица — переключателем (паллета/короб)
];

const UNIT_LABEL: Record<FfTariffUnit, string> = { PALLET: '₽/паллета', BOX: '₽/короб' };

function todayIso(): string {
    return new Date().toISOString().slice(0, 10);
}

function daysAgoIso(days: number): string {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d.toISOString().slice(0, 10);
}

/** Действующая на сегодня ставка услуги (последняя по valid_from из открытых). */
function activeTariff(rows: FfTariffRow[], service: FfServiceType): FfTariffRow | null {
    const today = todayIso();
    const candidates = rows
        .filter(t => t.service_type === service && t.valid_from <= today && (!t.valid_to || t.valid_to >= today))
        .sort((a, b) => b.valid_from.localeCompare(a.valid_from));
    return candidates[0] ?? null;
}

// ─── Вкладка ────────────────────────────────────────────────────────────────

export default function FfBillingTab({ warehouseId }: { warehouseId: number }) {
    // Тарифы
    const [tariffs, setTariffs] = useState<FfTariffRow[]>([]);
    const [tariffsLoading, setTariffsLoading] = useState(true);
    const [tariffsError, setTariffsError] = useState('');

    const loadTariffs = useCallback(async (signal?: AbortSignal) => {
        setTariffsLoading(true);
        setTariffsError('');
        try {
            const rows = await api.listFfTariffs(warehouseId);
            if (signal?.aborted) return;
            setTariffs(rows);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setTariffsError(e instanceof Error ? e.message : 'Ошибка загрузки тарифов');
        } finally {
            if (!signal?.aborted) setTariffsLoading(false);
        }
    }, [warehouseId]);

    useEffect(() => {
        const controller = new AbortController();
        loadTariffs(controller.signal);
        return () => controller.abort();
    }, [loadTariffs]);

    return (
        <div className="animate-in">
            <div className="glass-card" style={{ padding: 24, marginBottom: 16 }}>
                <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>Тарифы услуг ФФ</h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16 }}>
                    Новая ставка автоматически закрывает предыдущую (история сохраняется).
                </p>
                {tariffsLoading ? (
                    <div style={{ padding: 16, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                ) : tariffsError ? (
                    <div style={{ padding: 16, color: 'var(--color-danger)' }}>{tariffsError}</div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                        {SERVICES.map(svc => (
                            <ServiceTariffRow
                                key={svc.type}
                                service={svc}
                                tariffs={tariffs.filter(t => t.service_type === svc.type)}
                                warehouseId={warehouseId}
                                onChanged={() => loadTariffs()}
                            />
                        ))}
                    </div>
                )}
            </div>

            <StorageDailyBlock warehouseId={warehouseId} />
        </div>
    );
}

// ─── Строка услуги: текущая ставка + форма новой + история ─────────────────

function ServiceTariffRow({
    service, tariffs, warehouseId, onChanged,
}: {
    service: { type: FfServiceType; label: string; unitLabel: string };
    tariffs: FfTariffRow[];
    warehouseId: number;
    onChanged: () => void;
}) {
    const isLoading_ = service.type === 'LOADING';
    const active = activeTariff(tariffs, service.type);

    const [showForm, setShowForm] = useState(false);
    const [showHistory, setShowHistory] = useState(false);
    const [rateInput, setRateInput] = useState('');
    const [unitInput, setUnitInput] = useState<FfTariffUnit>(active?.unit ?? 'PALLET');
    const [validFrom, setValidFrom] = useState(todayIso());
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const history = useMemo(
        () => [...tariffs].sort((a, b) => b.valid_from.localeCompare(a.valid_from)),
        [tariffs],
    );

    const handleSave = async () => {
        const rate = Number(rateInput.replace(',', '.'));
        if (!rateInput.trim() || !Number.isFinite(rate) || rate < 0) {
            setError('Укажите ставку (число ≥ 0)');
            return;
        }
        setSaving(true);
        setError('');
        try {
            await api.createFfTariff(warehouseId, {
                service_type: service.type,
                unit: isLoading_ ? unitInput : null,
                rate,
                valid_from: validFrom || null,
            });
            setShowForm(false);
            setRateInput('');
            onChanged();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    const handleDelete = async (tariffId: number) => {
        if (!confirm('Удалить ставку из истории?')) return;
        setError('');
        try {
            await api.deleteFfTariff(tariffId);
            onChanged();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка удаления');
        }
    };

    const unitLabel = isLoading_
        ? (active?.unit ? UNIT_LABEL[active.unit] : 'паллета/короб')
        : service.unitLabel;

    return (
        <div style={{ border: '1px solid var(--color-border)', borderRadius: 12, padding: '12px 16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <div style={{ minWidth: 200 }}>
                    <div style={{ fontSize: 14, fontWeight: 600 }}>{service.label}</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{unitLabel}</div>
                </div>
                <div style={{ flex: 1, minWidth: 160 }}>
                    {active ? (
                        <span style={{ fontSize: 15, fontWeight: 600 }}>
                            {formatNumber(active.rate)} ₽
                            <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--color-text-muted)', marginLeft: 8 }}>
                                с {formatDate(active.valid_from)}{active.valid_to ? ` по ${formatDate(active.valid_to)}` : ''}
                            </span>
                        </span>
                    ) : (
                        <span className="badge badge-warning" style={{ fontSize: 12 }}>тариф не задан</span>
                    )}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    {history.length > 0 && (
                        <button className="btn btn-secondary btn-sm" onClick={() => setShowHistory(v => !v)}>
                            История ({formatNumber(history.length, 0)})
                        </button>
                    )}
                    <button
                        className="btn btn-primary btn-sm"
                        onClick={() => {
                            setShowForm(v => !v);
                            setRateInput(active ? String(active.rate) : '');
                            setUnitInput(active?.unit ?? 'PALLET');
                            setValidFrom(todayIso());
                            setError('');
                        }}
                    >
                        {active ? 'Новая ставка' : 'Задать тариф'}
                    </button>
                </div>
            </div>

            {showForm && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap', marginTop: 12 }}>
                    <div>
                        <label className="form-label" style={{ fontSize: 12 }}>Ставка, ₽</label>
                        <input
                            className="form-input"
                            type="number"
                            min={0}
                            step="0.01"
                            value={rateInput}
                            onChange={e => setRateInput(e.target.value)}
                            style={{ width: 120 }}
                            autoFocus
                        />
                    </div>
                    {isLoading_ && (
                        <div>
                            <label className="form-label" style={{ fontSize: 12 }}>Единица</label>
                            <select
                                className="form-input"
                                value={unitInput}
                                onChange={e => setUnitInput(e.target.value as FfTariffUnit)}
                                style={{ width: 130 }}
                            >
                                <option value="PALLET">паллета</option>
                                <option value="BOX">короб</option>
                            </select>
                        </div>
                    )}
                    <div>
                        <label className="form-label" style={{ fontSize: 12 }}>Действует с</label>
                        <input
                            className="form-input"
                            type="date"
                            value={validFrom}
                            onChange={e => setValidFrom(e.target.value)}
                            style={{ width: 150 }}
                        />
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving}>
                        {saving ? 'Сохранение...' : 'Сохранить'}
                    </button>
                    <button className="btn btn-secondary btn-sm" onClick={() => setShowForm(false)} disabled={saving}>
                        Отмена
                    </button>
                </div>
            )}
            {error && <div style={{ marginTop: 8, fontSize: 13, color: 'var(--color-danger)' }}>{error}</div>}

            {showHistory && history.length > 0 && (
                <div style={{ marginTop: 12, borderTop: '1px solid var(--color-border)', paddingTop: 8 }}>
                    {history.map(t => (
                        <div key={t.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '4px 0', fontSize: 13 }}>
                            <span style={{ fontWeight: 500, minWidth: 100 }}>
                                {formatNumber(t.rate)} ₽{isLoading_ && t.unit ? ` (${t.unit === 'PALLET' ? 'паллета' : 'короб'})` : ''}
                            </span>
                            <span style={{ color: 'var(--color-text-muted)' }}>
                                {formatDate(t.valid_from)} — {t.valid_to ? formatDate(t.valid_to) : 'сейчас'}
                            </span>
                            <button
                                className="btn btn-danger btn-sm"
                                style={{ marginLeft: 'auto', padding: '2px 8px', fontSize: 11 }}
                                onClick={() => handleDelete(t.id)}
                            >
                                Удалить
                            </button>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

// ─── Блок «Хранение по дням» ────────────────────────────────────────────────

function StorageDailyBlock({ warehouseId }: { warehouseId: number }) {
    const [dateFrom, setDateFrom] = useState(daysAgoIso(30));
    const [dateTo, setDateTo] = useState(todayIso());
    const [rows, setRows] = useState<FfStorageDailyRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [recomputing, setRecomputing] = useState(false);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const data = await api.listFfStorageDaily(warehouseId, dateFrom, dateTo);
            if (signal?.aborted) return;
            // Журнальный порядок: старые сверху, свежие снизу.
            setRows([...data].sort((a, b) => a.snapshot_date.localeCompare(b.snapshot_date)));
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [warehouseId, dateFrom, dateTo]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    const totalCost = useMemo(
        () => rows.reduce((sum, r) => sum + (r.storage_cost ?? 0), 0),
        [rows],
    );
    const totalPalletDays = useMemo(
        () => rows.reduce((sum, r) => sum + r.pallets, 0),
        [rows],
    );

    const handleRecompute = async () => {
        if (!dateFrom || !dateTo) return;
        setRecomputing(true);
        setError('');
        try {
            await api.recomputeFfStorage(warehouseId, dateFrom, dateTo);
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка пересчёта');
        } finally {
            setRecomputing(false);
        }
    };

    const cols: Column[] = [
        { key: 'snapshot_date', label: 'Дата', render: (v: string) => formatDate(v) },
        { key: 'units', label: 'Штуки', align: 'right', render: (v: number) => formatNumber(v, 0) },
        { key: 'boxes', label: 'Короба', align: 'right', render: (v: number) => formatNumber(v, 0) },
        { key: 'pallets', label: 'Паллеты', align: 'right', render: (v: number) => formatNumber(v, 0) },
        {
            key: 'storage_rate', label: 'Ставка, ₽/паллета/день', align: 'right',
            render: (v: number | null) => v != null ? formatNumber(v) : (
                <span className="badge badge-warning" style={{ fontSize: 11 }} title="На эту дату тариф «Хранение» не был задан">нет тарифа</span>
            ),
        },
        {
            key: 'storage_cost', label: '₽ за день', align: 'right',
            render: (v: number | null) => v != null ? <span style={{ fontWeight: 600 }}>{formatNumber(v)}</span> : '—',
        },
    ];

    return (
        <div className="glass-card" style={{ padding: 24 }}>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                <div>
                    <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>Хранение по дням</h2>
                    <p style={{ fontSize: 13, color: 'var(--color-text-muted)', margin: 0 }}>
                        Посуточные срезы остатка: штуки → короба → паллеты × ставка.
                    </p>
                </div>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
                    <div>
                        <label className="form-label" style={{ fontSize: 12 }}>С</label>
                        <input className="form-input" type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={{ width: 150 }} />
                    </div>
                    <div>
                        <label className="form-label" style={{ fontSize: 12 }}>По</label>
                        <input className="form-input" type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={{ width: 150 }} />
                    </div>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={handleRecompute}
                        disabled={recomputing || loading}
                        title="Пересчитать ₽ за период по актуальным тарифам (штуки/короба/паллеты не меняются)"
                    >
                        {recomputing ? 'Пересчёт...' : 'Пересчитать ₽'}
                    </button>
                </div>
            </div>

            {loading ? (
                <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
            ) : error ? (
                <div style={{ padding: 16, color: 'var(--color-danger)' }}>{error}</div>
            ) : rows.length === 0 ? (
                <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Срезы копятся с момента включения — данных пока нет.
                </div>
            ) : (
                <>
                    <div style={{ display: 'flex', gap: 24, marginBottom: 12, fontSize: 14 }}>
                        <span>
                            Итого за период:{' '}
                            <strong>{formatNumber(totalCost)} ₽</strong>
                        </span>
                        <span style={{ color: 'var(--color-text-muted)' }}>
                            {formatNumber(totalPalletDays, 0)} паллето-дней · {formatNumber(rows.length, 0)} дн.
                        </span>
                    </div>
                    <TanStackDataTable
                        columns={cols}
                        data={rows}
                        exportName={`Хранение_ФФ_${warehouseId}_${dateFrom}_${dateTo}`}
                        enableSorting
                        enablePagination={rows.length > 60}
                        pageSize={60}
                        emptyText="Нет данных"
                    />
                </>
            )}
        </div>
    );
}
