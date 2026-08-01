'use client';
/**
 * Блок «Ожидаемая стоимость услуг ФФ» на карточке ПЕРЕЕЗДА.
 *
 * Отличия от близнеца у заявки на сборку (assembly/[id]/FfExpectedCostCard):
 *  • Услуга одна — TRANSFER_ASSEMBLY («Сборка переезда»), плюс ручные
 *    доп-услуги. Ставку берём у склада-ИСТОЧНИКА: работу делает он, поэтому и
 *    ссылка «задать тариф» ведёт на его карточку, а не на склад-получатель.
 *  • 🔴 Несовпадение единиц (тариф в коробах, переезд в паллетах) — ОТДЕЛЬНОЕ
 *    состояние, а не прочерк: пересчитать короба в паллеты по выдуманному
 *    коэффициенту нельзя, а молчаливый «—» читается как баг. Показываем
 *    ставку, бейдж и объяснение, сумму не выдумываем.
 *  • Объём переезда (pallets_count) может быть не заполнен — тогда бэкенд
 *    честно считает 0 × ставку = 0 ₽. Ноль в деньгах врёт сильнее пустоты,
 *    поэтому вместо него говорим «объём не указан».
 *
 * Блок необязательный: ошибку загрузки гасим внутри — карточка переезда
 * обязана жить и без него.
 */
import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatNumber, pluralRu } from '@/lib/utils';
import { toMoney } from '@/lib/transfer';
import type { FfExpectedComponent, FfTransferExpectedCost } from '@/types/api';

/** Услуг у переезда одна, но словарь полный — чтобы новый компонент бэкенда
 *  не вылез сырым енумом. «Сборка переезда» — как на вкладке «Тарифы ФФ». */
const COMPONENT_LABEL: Record<string, string> = {
    TRANSFER_ASSEMBLY: 'Сборка переезда',
    PALLETIZING: 'Паллетирование',
    BOX_PROCESSING: 'Обработка коробок',
    STORAGE: 'Хранение',
    TRUCK_UNLOADING: 'Выгрузка фуры',
    LOADING: 'Погрузка',
    CUSTOM: 'Доп-услуги ФФ',
};

/** «3 паллеты», «12 коробов» — счётная форма единицы документа. */
const UNIT_FORMS: Record<string, [string, string, string]> = {
    PALLET: ['паллета', 'паллеты', 'паллет'],
    BOX: ['короб', 'короба', 'коробов'],
    VEHICLE: ['машина', 'машины', 'машин'],
};

/** «₽ за паллету» — единица ставки. */
const UNIT_ONE: Record<string, string> = { PALLET: 'паллету', BOX: 'короб', VEHICLE: 'машину' };

/** «тариф в коробах, переезд в паллетах» — предложный падеж. */
const UNIT_IN: Record<string, string> = { PALLET: 'паллетах', BOX: 'коробах', VEHICLE: 'машинах' };

function unitOne(u: string | null): string {
    return u ? (UNIT_ONE[u] ?? u) : 'единицу';
}

function unitIn(u: string | null): string {
    return u ? (UNIT_IN[u] ?? u) : 'другой единице';
}

function volumeText(qty: number, unit: string): string {
    const forms = UNIT_FORMS[unit] ?? ['единица', 'единицы', 'единиц'];
    return `${formatNumber(qty, 0)} ${pluralRu(qty, forms)}`;
}

const ROW_STYLE: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', fontSize: 14 };
const MUTED: React.CSSProperties = { color: 'var(--color-text-muted)', fontSize: 13 };

/**
 * Одна строка расчёта. Четыре состояния, и все четыре разные по смыслу:
 * несовпадение единиц ≠ «нет тарифа» ≠ «нет объёма» ≠ посчитанная сумма.
 */
function ComponentRow({ c, docUnit, volumeUnknown }: {
    c: FfExpectedComponent;
    docUnit: string;
    volumeUnknown: boolean;
}) {
    const label = COMPONENT_LABEL[c.service_type] ?? c.service_type;
    const rate = toMoney(c.rate);
    const cost = toMoney(c.cost);

    // 🔴 Ставка есть, но в чужой единице: сумму НЕ считаем намеренно.
    if (c.unit_mismatch) {
        return (
            <div>
                <div style={ROW_STYLE}>
                    <span>{label}</span>
                    {rate !== null && (
                        <span style={MUTED}>{formatNumber(rate)} ₽ за {unitOne(c.unit)}</span>
                    )}
                    <span style={{ marginLeft: 'auto' }}>
                        <span className="badge badge-danger" style={{ fontSize: 11 }}>единица не совпала</span>
                    </span>
                </div>
                <div style={{ marginTop: 4, fontSize: 12, color: 'var(--color-text-muted)' }}>
                    тариф в {unitIn(c.unit)}, переезд в {unitIn(docUnit)} — сумма не посчитана:
                    пересчёт по выдуманному коэффициенту дал бы неверные деньги.
                    Заведите ставку «{label}» в той же единице, что и переезд.
                </div>
            </div>
        );
    }

    // Тарифа нет вовсе.
    if (rate === null) {
        return (
            <div style={ROW_STYLE}>
                <span>{label}</span>
                <span style={{ marginLeft: 'auto' }}>
                    <span className="badge badge-warning" style={{ fontSize: 11 }}>тариф не задан</span>
                </span>
            </div>
        );
    }

    // Ставка есть, а объёма нет: 0 ₽ здесь — правда, которая вводит в заблуждение.
    if (volumeUnknown) {
        return (
            <div style={ROW_STYLE}>
                <span>{label}</span>
                <span style={MUTED}>{formatNumber(rate)} ₽ за {unitOne(c.unit)}</span>
                <span style={{ marginLeft: 'auto' }}>
                    <span className="badge badge-secondary" style={{ fontSize: 11 }} title="У переезда не заполнено количество транспортных единиц">
                        объём не указан
                    </span>
                </span>
            </div>
        );
    }

    return (
        <div style={ROW_STYLE}>
            <span>{label}</span>
            <span style={MUTED}>
                {c.qty != null
                    ? `${formatNumber(c.qty, 0)}${c.unit ? ' ' + pluralRu(c.qty, UNIT_FORMS[c.unit] ?? ['ед.', 'ед.', 'ед.']) : ''} × ${formatNumber(rate)} ₽`
                    : ''}
            </span>
            <span style={{ marginLeft: 'auto', fontWeight: 600 }}>
                {cost !== null ? `${formatNumber(cost)} ₽` : '—'}
            </span>
        </div>
    );
}

export default function FfTransferExpectedCostCard({
    transferId, slug, fromWarehouseName,
}: {
    transferId: number;
    slug: string;
    /** Фолбэк имени склада-источника: в выдаче оно может быть null. */
    fromWarehouseName?: string | null;
}) {
    const [data, setData] = useState<FfTransferExpectedCost | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Доп-услуги ФФ (сумма + комментарий)
    const [amountInput, setAmountInput] = useState('');
    const [commentInput, setCommentInput] = useState('');
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState('');

    const applyData = useCallback((d: FfTransferExpectedCost) => {
        setData(d);
        setAmountInput(d.custom_cost != null ? String(d.custom_cost) : '');
        setCommentInput(d.custom_cost_comment ?? '');
    }, []);

    // StrictMode монтирует эффект дважды — без проверки abort второй ответ
    // перетирается ошибкой первого (отменённого) запроса.
    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const d = await api.getFfTransferExpectedCost(transferId);
            if (signal?.aborted) return;
            applyData(d);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [transferId, applyData]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    const dirty = data != null && (
        amountInput !== (data.custom_cost != null ? String(data.custom_cost) : '')
        || commentInput !== (data.custom_cost_comment ?? '')
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
            // Бэкенд при amount=null чистит и комментарий — блок перечитываем
            // ответом того же запроса, чтобы поля показали реальное состояние.
            const d = await api.setFfTransferCustomCost(transferId, {
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

    // Сохранение по blur — только если реально поменяли (иначе каждый клик мимо
    // поля дёргает PATCH).
    const handleBlur = () => {
        if (dirty) handleSave();
    };

    const components = (data?.components ?? []).filter(c => c.service_type !== 'CUSTOM');
    const missing = data?.missing_tariffs ?? [];
    const whName = data?.from_warehouse_name || fromWarehouseName || null;

    // Объёма нет — считать нечего; total тогда либо 0, либо только доп-услуги.
    const volumeUnknown = data != null && data.qty === 0;
    const total = toMoney(data?.total);
    const custom = toMoney(data?.custom_cost);
    const nothingToCount = volumeUnknown && !custom;
    const notes: string[] = [];
    if (volumeUnknown) notes.push('объём переезда не указан');
    if (missing.length > 0 || data?.unit_mismatch) notes.push('без услуг, у которых нет тарифа или не совпала единица');

    return (
        <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
            <h2 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 12px' }}>Ожидаемая стоимость услуг ФФ</h2>

            {loading ? (
                <div style={{ padding: 8, color: 'var(--color-text-muted)' }}>Загрузка...</div>
            ) : error ? (
                // Мягко: блок необязательный, карточка переезда из-за него не падает.
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', color: 'var(--color-text-muted)', fontSize: 13 }}>
                    <span>Не удалось загрузить ожидаемую стоимость: {error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => load()}>Повторить</button>
                </div>
            ) : !data ? (
                <div style={{ padding: 8, color: 'var(--color-text-muted)', fontSize: 13 }}>Нет данных</div>
            ) : (
                <>
                    <div style={{ ...MUTED, marginBottom: 12 }}>
                        Сборку выполняет склад-источник{whName ? ` (${whName})` : ''}
                        {' · '}
                        {volumeUnknown ? 'объём не указан' : volumeText(data.qty, data.unit)}
                        {' · ставки на '}{formatDate(data.on_date)}
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
                        {components.map((c, i) => (
                            <ComponentRow
                                key={`${c.service_type}-${i}`}
                                c={c}
                                docUnit={data.unit}
                                volumeUnknown={volumeUnknown}
                            />
                        ))}
                        {components.length === 0 && (
                            <div style={MUTED}>Компонентов расчёта нет</div>
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
                                placeholder="Комментарий (стрейч, переупаковка...)"
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
                        <div style={{ marginTop: 6, fontSize: 12, color: 'var(--color-text-muted)' }}>
                            Пустая сумма очищает и сумму, и комментарий.
                        </div>
                        {saveError && <div style={{ marginTop: 6, fontSize: 13, color: 'var(--color-danger)' }}>{saveError}</div>}
                    </div>

                    {/* Итог */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', fontSize: 15 }}>
                        <span style={{ fontWeight: 600 }}>Итого:</span>
                        <span style={{ fontWeight: 700 }}>
                            {nothingToCount || total === null ? '—' : `${formatNumber(total)} ₽`}
                        </span>
                        {nothingToCount ? (
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                объём переезда не указан — считать нечего
                            </span>
                        ) : notes.length > 0 && total !== null ? (
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{notes.join(' · ')}</span>
                        ) : null}
                    </div>

                    {(missing.length > 0 || data.unit_mismatch) && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', padding: '10px 12px', marginTop: 12, background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: 8, fontSize: 13 }}>
                            <span style={{ color: 'var(--color-warning)', fontWeight: 500 }}>
                                ⚠️ {missing.length > 0
                                    ? `Не заданы тарифы: ${missing.map(m => COMPONENT_LABEL[m] ?? m).join(', ')}`
                                    : `Тариф задан в других единицах, чем переезд (${unitIn(data.unit)})`}
                                {' — у склада-источника'}{whName ? ` (${whName})` : ''}
                            </span>
                            {/* Ставку заводят у ИСТОЧНИКА: сборку переезда делает он. */}
                            <Link href={`/p/${slug}/warehouse/${data.from_warehouse_id}?tab=ffbilling`} style={{ color: 'var(--color-accent)' }}>
                                Задать «Сборка переезда» на складе-источнике →
                            </Link>
                        </div>
                    )}
                </>
            )}
        </div>
    );
}
