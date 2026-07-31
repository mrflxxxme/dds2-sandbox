'use client';
/**
 * «Переделать в перемещение» — заявка на сборку превращается в переезд между
 * нашими складами (основной сценарий: заявка уже закрыта/отменена, а товар на
 * самом деле поехал не на WB, а на другой наш склад — например на транзитный).
 *
 * Чекбокс «перенести связку ФФ» по умолчанию ВЫКЛЮЧЕН: зеркала ФФ остаются
 * историей заявки, переносить их нужно только когда физически едет тот же
 * груз, который ФФ обрабатывал.
 *
 * Ошибку бэкенда (например «сток уже списан, N единиц») показываем текстом как
 * есть — это ожидаемый пользовательский сценарий, а не краш.
 */
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { toMoney, unitCountText, unitWeightLabel } from '@/lib/transfer';
import type { AssemblyToTransferResponse, Warehouse } from '@/types/api';

interface Props {
    assemblyId: number;
    assemblyNumber: string;
    /** Склад-источник заявки — его из списка получателей убираем (переезд «в себя» бессмыслен). */
    fromWarehouseId: number;
    /** Транспортная единица заявки — переезжает на перемещение как есть (показываем подсказкой). */
    palletsCount?: number | null;
    /**
     * Вес ОДНОЙ единицы, кг. Тип `AssemblyRequest.pallet_weight_kg` объявлен
     * числом, но схема заявки отдаёт `Decimal` — в JSON это СТРОКА («64.00»).
     * Принимаем оба варианта и приводим через toMoney: иначе умножение сработало
     * бы на приведении, а formatNumber напечатал бы «64.00» вместо «64,0».
     */
    palletWeightKg?: number | string | null;
    shippedAsBoxes?: boolean | null;
    onClose: () => void;
    /** Успех: лид-страница показывает тост и уводит на деталку перемещения. */
    onSuccess: (result: AssemblyToTransferResponse) => void;
}

export default function ConvertToTransferModal({
    assemblyId,
    assemblyNumber,
    fromWarehouseId,
    palletsCount,
    palletWeightKg,
    shippedAsBoxes,
    onClose,
    onSuccess,
}: Props) {
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [warehousesLoading, setWarehousesLoading] = useState(true);
    const [toWarehouseId, setToWarehouseId] = useState<number | ''>('');
    const [comment, setComment] = useState('');
    // ВЫКЛЮЧЕН по умолчанию — канон: зеркала ФФ остаются историей заявки.
    const [moveFfLinks, setMoveFfLinks] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    // Общий вес = количество × вес ОДНОЙ единицы (отдельного поля у переезда нет).
    // Вес приводим явно: Decimal приезжает строкой (см. тип пропа).
    const unitWeight = toMoney(palletWeightKg ?? null);
    const totalWeight = palletsCount != null && unitWeight !== null
        ? palletsCount * unitWeight
        : null;

    useEffect(() => {
        let cancelled = false;
        // Транзитные склады — обычные записи справочника (id не хардкодим).
        api.getWarehouses()
            .then(rows => { if (!cancelled) setWarehouses(rows.filter(w => w.id !== fromWarehouseId)); })
            .catch(() => { if (!cancelled) setError('Не удалось загрузить список складов'); })
            .finally(() => { if (!cancelled) setWarehousesLoading(false); });
        return () => { cancelled = true; };
    }, [fromWarehouseId]);

    const handleSubmit = async () => {
        if (toWarehouseId === '' || saving) return;
        setSaving(true);
        setError('');
        try {
            const result = await api.convertAssemblyToTransfer(assemblyId, {
                to_warehouse_id: Number(toWarehouseId),
                comment: comment.trim() || undefined,
                move_ff_links: moveFfLinks,
            });
            onSuccess(result);
        } catch (e: unknown) {
            // Текст бэкенда показываем как есть (списанный сток и т.п.).
            setError(e instanceof Error ? e.message : 'Не удалось переделать заявку в перемещение');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
                <h2 className="modal-title">Переделать в перемещение</h2>
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16 }}>
                    Состав заявки {assemblyNumber} превратится в переезд между нашими складами:
                    у перемещения будет своя машина, логистика и оплата забора.
                </div>

                {/* Транспортная единица переезжает вместе с составом — показываем,
                    что именно, чтобы «потерянные паллеты» не всплыли на переезде. */}
                {palletsCount != null && (
                    <div
                        style={{
                            marginBottom: 16, padding: '8px 12px', borderRadius: 8,
                            background: 'rgba(99, 102, 241, 0.10)', fontSize: 13,
                        }}
                        title={totalWeight !== null
                            ? `${formatNumber(palletsCount, 0)} × ${formatNumber(unitWeight!, 1)} кг (${unitWeightLabel(shippedAsBoxes).toLowerCase()})`
                            : undefined}
                    >
                        Из заявки перенесётся: {unitCountText(palletsCount, shippedAsBoxes)}
                        {totalWeight !== null && `, ${formatNumber(totalWeight, 0)} кг`}
                    </div>
                )}

                {error && (
                    <div style={{
                        marginBottom: 12, padding: '8px 12px', borderRadius: 8,
                        background: 'rgba(239, 68, 68, 0.12)', color: 'var(--color-danger)', fontSize: 13,
                    }}>
                        {error}
                    </div>
                )}

                <div className="form-group">
                    <label className="form-label">Склад-получатель *</label>
                    <select
                        className="form-input"
                        value={toWarehouseId}
                        onChange={e => setToWarehouseId(e.target.value ? Number(e.target.value) : '')}
                        disabled={warehousesLoading}
                        autoFocus
                    >
                        <option value="">{warehousesLoading ? 'Загрузка складов…' : '— выберите склад —'}</option>
                        {warehouses.map(w => (
                            <option key={w.id} value={w.id}>{w.name}</option>
                        ))}
                    </select>
                </div>

                <div className="form-group">
                    <label className="form-label">Комментарий</label>
                    <input
                        className="form-input"
                        value={comment}
                        onChange={e => setComment(e.target.value)}
                        placeholder="Почему груз поехал на другой склад"
                    />
                </div>

                <div className="form-group" style={{ borderTop: '1px solid var(--color-border)', paddingTop: 12 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                        <input
                            type="checkbox"
                            checked={moveFfLinks}
                            onChange={e => setMoveFfLinks(e.target.checked)}
                        />
                        <span style={{ fontSize: 13 }}>Перенести связку ФФ на перемещение</span>
                    </label>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 6 }}>
                        По умолчанию зеркала ФФ остаются историей заявки.
                    </div>
                </div>

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Отмена</button>
                    <button
                        className="btn btn-primary"
                        onClick={handleSubmit}
                        disabled={saving || toWarehouseId === ''}
                    >
                        {saving ? 'Создание…' : 'Переделать'}
                    </button>
                </div>
            </div>
        </div>
    );
}

/** Подпись успеха — общая для тоста лида: «TR-31 создан из ASM-1 · 12 SKU / 340 шт». */
export function convertSuccessMessage(r: AssemblyToTransferResponse): string {
    const ff = r.ff_links_moved > 0 ? ` · связок ФФ перенесено: ${formatNumber(r.ff_links_moved, 0)}` : '';
    return `${r.transfer_number} создано из ${r.assembly_number} · `
        + `${formatNumber(r.items_count, 0)} SKU / ${formatNumber(r.units_total, 0)} шт${ff}`;
}
