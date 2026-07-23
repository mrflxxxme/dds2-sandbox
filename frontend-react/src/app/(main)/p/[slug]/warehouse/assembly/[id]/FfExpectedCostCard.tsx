'use client';
/**
 * Блок «Ожидаемая стоимость услуг ФФ» на карточке сборки:
 * разбивка по компонентам (Паллетирование N×ставка=₽, Обработка коробок, Погрузка, ...),
 * редактируемое поле «Доп-услуги ФФ» (сумма + комментарий, PATCH ff-custom-cost),
 * плашка про незаданные тарифы со ссылкой на страницу склада.
 */
import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { FfAssemblyExpectedCost, FfExpectedComponent } from '@/types/api';

const COMPONENT_LABEL: Record<string, string> = {
    PALLETIZING: 'Паллетирование',
    BOX_PROCESSING: 'Обработка коробок',
    STORAGE: 'Хранение',
    TRUCK_UNLOADING: 'Выгрузка фуры',
    LOADING: 'Погрузка',
    CUSTOM: 'Доп-услуги ФФ',
};

const UNIT_SHORT: Record<string, string> = {
    PALLET: 'паллет',
    BOX: 'короб.',
    VEHICLE: 'маш.',
};

function componentRow(c: FfExpectedComponent): React.ReactNode {
    const label = COMPONENT_LABEL[c.service_type] ?? c.service_type;
    if (c.rate == null) {
        return (
            <>
                <span>{label}</span>
                <span style={{ marginLeft: 'auto' }}>
                    <span className="badge badge-warning" style={{ fontSize: 11 }}>тариф не задан</span>
                </span>
            </>
        );
    }
    return (
        <>
            <span>{label}</span>
            <span style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                {c.qty != null ? `${formatNumber(c.qty, 0)}${c.unit ? ' ' + (UNIT_SHORT[c.unit] ?? c.unit) : ''} × ${formatNumber(c.rate)} ₽` : ''}
            </span>
            <span style={{ marginLeft: 'auto', fontWeight: 600 }}>
                {c.cost != null ? formatNumber(c.cost) + ' ₽' : '—'}
            </span>
        </>
    );
}

export default function FfExpectedCostCard({
    assemblyId, warehouseId, slug,
}: {
    assemblyId: number;
    warehouseId: number;
    slug: string;
}) {
    const [data, setData] = useState<FfAssemblyExpectedCost | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Доп-услуги ФФ (сумма + комментарий)
    const [amountInput, setAmountInput] = useState('');
    const [commentInput, setCommentInput] = useState('');
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState('');

    const applyData = useCallback((d: FfAssemblyExpectedCost) => {
        setData(d);
        setAmountInput(d.custom_cost != null ? String(d.custom_cost) : '');
        setCommentInput(d.custom_cost_comment ?? '');
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        (async () => {
            setLoading(true);
            setError('');
            try {
                const d = await api.getFfExpectedCost(assemblyId);
                if (controller.signal.aborted) return;
                applyData(d);
            } catch (e: unknown) {
                if (controller.signal.aborted) return;
                setError(e instanceof Error ? e.message : 'Ошибка загрузки');
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        })();
        return () => controller.abort();
    }, [assemblyId, applyData]);

    const dirty = data != null && (
        amountInput !== (data.custom_cost != null ? String(data.custom_cost) : '') ||
        commentInput !== (data.custom_cost_comment ?? '')
    );

    const handleSave = async () => {
        if (!data || saving) return;
        const trimmed = amountInput.trim();
        const amount = trimmed === '' ? null : Number(trimmed.replace(',', '.'));
        if (amount != null && (!Number.isFinite(amount) || amount < 0)) {
            setSaveError('Сумма должна быть числом ≥ 0');
            return;
        }
        setSaving(true);
        setSaveError('');
        try {
            const d = await api.setFfCustomCost(assemblyId, {
                amount,
                comment: commentInput.trim() || null,
            });
            applyData(d);
        } catch (e: unknown) {
            setSaveError(e instanceof Error ? e.message : 'Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    // Сохранение по blur — только если реально поменяли (иначе каждый клик мимо поля дёргает PATCH).
    const handleBlur = () => {
        if (dirty) handleSave();
    };

    const components = (data?.components ?? []).filter(c => c.service_type !== 'CUSTOM');
    const missing = data?.missing_tariffs ?? [];

    return (
        <div className="glass-card" style={{ padding: 24, marginBottom: 16 }}>
            <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>Ожидаемая стоимость услуг ФФ</h2>

            {loading ? (
                <div style={{ padding: 8, color: 'var(--color-text-muted)' }}>Загрузка...</div>
            ) : error ? (
                <div style={{ padding: 8, color: 'var(--color-danger)', fontSize: 13 }}>{error}</div>
            ) : !data ? (
                <div style={{ padding: 8, color: 'var(--color-text-muted)', fontSize: 13 }}>Нет данных</div>
            ) : (
                <>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                        {formatNumber(data.pallets, 0)} паллет · {formatNumber(data.boxes, 0)} короб. · {formatNumber(data.units, 0)} шт
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
                        {components.map((c, i) => (
                            <div key={`${c.service_type}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 14 }}>
                                {componentRow(c)}
                            </div>
                        ))}
                        {components.length === 0 && (
                            <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Компонентов расчёта нет</div>
                        )}
                    </div>

                    {/* Доп-услуги ФФ */}
                    <div style={{ borderTop: '1px solid var(--color-border)', paddingTop: 12, marginBottom: 12 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Доп-услуги ФФ</div>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                            <input
                                className="form-input"
                                type="number"
                                min={0}
                                step="0.01"
                                placeholder="Сумма, ₽"
                                value={amountInput}
                                onChange={e => setAmountInput(e.target.value)}
                                onBlur={handleBlur}
                                style={{ width: 130 }}
                            />
                            <input
                                className="form-input"
                                type="text"
                                placeholder="Комментарий (стикеровка, переупаковка...)"
                                value={commentInput}
                                onChange={e => setCommentInput(e.target.value)}
                                onBlur={handleBlur}
                                maxLength={300}
                                style={{ flex: 1, minWidth: 220 }}
                            />
                            <button className="btn btn-secondary btn-sm" onClick={handleSave} disabled={saving || !dirty}>
                                {saving ? 'Сохранение...' : 'Сохранить'}
                            </button>
                        </div>
                        {saveError && <div style={{ marginTop: 6, fontSize: 13, color: 'var(--color-danger)' }}>{saveError}</div>}
                    </div>

                    {/* Итог */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 15 }}>
                        <span style={{ fontWeight: 600 }}>Итого:</span>
                        <span style={{ fontWeight: 700 }}>
                            {data.total != null ? formatNumber(data.total) + ' ₽' : '—'}
                        </span>
                        {missing.length > 0 && data.total != null && (
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>без услуг, у которых нет тарифа</span>
                        )}
                    </div>

                    {missing.length > 0 && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', padding: '10px 12px', marginTop: 12, background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: 8, fontSize: 13 }}>
                            <span style={{ color: 'var(--color-warning)', fontWeight: 500 }}>
                                ⚠️ Не заданы тарифы: {missing.map(m => COMPONENT_LABEL[m] ?? m).join(', ')}
                            </span>
                            <Link href={`/p/${slug}/warehouse/${warehouseId}?tab=ffbilling`} style={{ color: 'var(--color-accent)' }}>
                                Задать на странице склада →
                            </Link>
                        </div>
                    )}
                </>
            )}
        </div>
    );
}
