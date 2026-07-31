'use client';
import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { mergeRowsByBarcode } from '@/lib/utils/transferRows';
import { toMoney, transferEditError, unitCountLabel, unitWeightLabel } from '@/lib/transfer';
import TransferItemsEditor, { transferItemRows } from './TransferItemsEditor';
import type { TransferItemRow } from './TransferItemsEditor';
import type { Nomenclature, StockTransfer, TransferUpdatePayload, Warehouse } from '@/types/api';

/**
 * Правка ЧЕРНОВИКА переезда: маршрут, комментарий, брак, транспортная единица,
 * состав. Бэкенд разрешает PUT только в DRAFT — кнопку, открывающую эту
 * модалку, карточка в других статусах не показывает вовсе.
 *
 * 🔴 `shipped_as_boxes` здесь — ОБЫЧНЫЙ bool (пустое = паллеты), как на
 * создании, а не трёхзначный «null = не трогать» из «Назначить машину».
 * Поэтому форма единицы из AssignVehicleModal сюда НЕ переиспользована: она
 * протащила бы чужой контракт, где «не менять» — легальное значение.
 *
 * Остатки склада-источника подтягиваем ради колонки «на складе»: превышение
 * подсвечиваем, но НЕ блокируем сохранение — черновик остаток не держит, и
 * решать, доедет ли товар к отправке, логисту, а не форме. Жёсткий гард стоит
 * на «Отправить» (бэкенд).
 */

interface Props {
    transfer: StockTransfer;
    warehouses: Warehouse[];
    nomenclature: Nomenclature[];
    submitting: boolean;
    /** Ошибка сервера — текст как есть, форма остаётся открытой. */
    error: string;
    onSubmit: (payload: TransferUpdatePayload) => void;
    onClose: () => void;
}

export default function TransferEditModal({
    transfer,
    warehouses,
    nomenclature,
    submitting,
    error,
    onSubmit,
    onClose,
}: Props) {
    const [fromWarehouseId, setFromWarehouseId] = useState<number | ''>(transfer.from_warehouse_id);
    const [toWarehouseId, setToWarehouseId] = useState<number | ''>(transfer.to_warehouse_id);
    const [comment, setComment] = useState(transfer.comment ?? '');
    const [isDefect, setIsDefect] = useState(!!transfer.is_defect);
    const [defectReason, setDefectReason] = useState(transfer.defect_reason ?? '');
    const [shippedAsBoxes, setShippedAsBoxes] = useState(!!transfer.shipped_as_boxes);
    const [palletsCount, setPalletsCount] = useState<number | ''>(transfer.pallets_count ?? '');
    // pallet_weight_kg — Numeric, приезжает СТРОКОЙ: через toMoney, иначе
    // «175.50» попало бы в поле как строка и уехало обратно как строка.
    const [palletWeight, setPalletWeight] = useState<number | ''>(() => toMoney(transfer.pallet_weight_kg) ?? '');
    const [rows, setRows] = useState<TransferItemRow[]>(() => transferItemRows(transfer.items ?? []));
    const [clientError, setClientError] = useState('');

    // Остатки склада-источника: перезапрашиваем при смене склада/режима брака.
    // Ошибку глушим — колонка необязательная, форма без неё работает.
    const [stockMap, setStockMap] = useState<Record<string, number> | undefined>(undefined);
    useEffect(() => {
        if (fromWarehouseId === '') { setStockMap(undefined); return; }
        let cancelled = false;
        setStockMap(undefined);
        const whId = Number(fromWarehouseId);
        const request = isDefect
            ? api.getDefectStock(whId).then(rowsIn => {
                const m: Record<string, number> = {};
                rowsIn.forEach(r => { m[r.barcode] = r.defect_quantity ?? 0; });
                return m;
            })
            : api.getWarehouseStock(whId).then(rowsIn => {
                const m: Record<string, number> = {};
                rowsIn.forEach(r => { m[r.barcode] = r.quantity ?? 0; });
                return m;
            });
        request.then(m => { if (!cancelled) setStockMap(m); }).catch(() => {});
        return () => { cancelled = true; };
    }, [fromWarehouseId, isDefect]);

    const activeWarehouses = useMemo(
        // Неактивный склад из самого переезда оставляем в списке — иначе он бы
        // молча «переехал» на первый попавшийся при сохранении.
        () => warehouses.filter(w => w.is_active
            || w.id === transfer.from_warehouse_id
            || w.id === transfer.to_warehouse_id),
        [warehouses, transfer.from_warehouse_id, transfer.to_warehouse_id],
    );

    const items = useMemo(() => mergeRowsByBarcode(rows), [rows]);
    const totalQty = items.reduce((s, it) => s + it.quantity, 0);

    /**
     * Связки ФФ, которые новый маршрут осиротил бы: зеркало отгрузки живёт на
     * складе ЗАБОРА, приёмки — на складе ПОЛУЧАТЕЛЯ, и бэкенд на такую правку
     * отвечает 400. Предупреждаем ДО сохранения — иначе логист узнаёт об этом
     * из отказа после того, как переписал состав.
     */
    const orphanedFfLinks = useMemo(() => {
        return (transfer.ff_links ?? []).filter(l => {
            const expected = l.kind === 'assembly' ? fromWarehouseId : toWarehouseId;
            return expected !== '' && l.warehouse_id !== Number(expected);
        });
    }, [transfer.ff_links, fromWarehouseId, toWarehouseId]);

    // Превышение остатка — предупреждение, не блокировка (см. шапку файла).
    const overStock = useMemo(() => {
        if (!stockMap) return [];
        return items.filter(it => it.quantity > (stockMap[it.barcode] || 0));
    }, [items, stockMap]);

    const handleSubmit = () => {
        if (submitting) return;
        const validation = transferEditError({ fromWarehouseId, toWarehouseId, itemCount: items.length });
        if (validation) { setClientError(validation); return; }
        setClientError('');
        onSubmit({
            from_warehouse_id: Number(fromWarehouseId),
            to_warehouse_id: Number(toWarehouseId),
            comment: comment.trim() || null,
            is_defect: isDefect,
            // Причина без галочки «брак» — мусор в карточке: чистим вместе с флагом.
            defect_reason: isDefect ? (defectReason.trim() || null) : null,
            pallets_count: palletsCount === '' ? null : Number(palletsCount),
            pallet_weight_kg: palletWeight === '' ? null : Number(palletWeight),
            shipped_as_boxes: shippedAsBoxes,
            items,
        });
    };

    const shownError = clientError || error;

    return (
        <div className="modal-overlay" onClick={submitting ? undefined : onClose}>
            <div
                className="modal-card modal-card-wide modal-card-solid"
                onClick={e => e.stopPropagation()}
                style={{ maxHeight: '92vh', overflowY: 'auto' }}
            >
                <h2 className="modal-title" style={{ marginBottom: 8 }}>Редактировать · {transfer.number}</h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16 }}>
                    Правка доступна только в черновике: после «Отправить» состав и маршрут уже списаны со склада.
                </p>

                {shownError && (
                    <div style={{
                        marginBottom: 12, padding: '8px 12px', borderRadius: 8,
                        background: 'rgba(239, 68, 68, 0.12)', color: 'var(--color-danger)', fontSize: 13,
                    }}>
                        {shownError}
                    </div>
                )}

                {/* ─── Маршрут и комментарий ─────────────────────────────── */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Откуда *</label>
                        <select
                            className="form-input"
                            value={fromWarehouseId}
                            disabled={submitting}
                            onChange={e => setFromWarehouseId(e.target.value ? Number(e.target.value) : '')}
                        >
                            <option value="">Выберите склад...</option>
                            {activeWarehouses.map(w => (
                                <option key={w.id} value={w.id}>{w.name}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Куда *</label>
                        <select
                            className="form-input"
                            value={toWarehouseId}
                            disabled={submitting}
                            onChange={e => setToWarehouseId(e.target.value ? Number(e.target.value) : '')}
                        >
                            <option value="">Выберите склад...</option>
                            {activeWarehouses
                                // Склад-источник в списке получателей не предлагаем:
                                // одинаковые концы маршрута бэкенд отвергает 400.
                                .filter(w => w.id !== fromWarehouseId)
                                .map(w => (
                                    <option key={w.id} value={w.id}>{w.name}</option>
                                ))}
                        </select>
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Комментарий</label>
                        <input
                            className="form-input"
                            value={comment}
                            disabled={submitting}
                            onChange={e => setComment(e.target.value)}
                            placeholder="Примечание..."
                        />
                    </div>
                </div>
                {orphanedFfLinks.length > 0 && (
                    <div style={{ fontSize: 12, color: 'var(--color-warning)', marginTop: 8 }}>
                        Новый маршрут разойдётся со связками ФФ ({orphanedFfLinks.map(l => l.number || l.external_id).join(', ')}) —
                        сохранение отклонят. Сначала отвяжите их в блоке «Фулфилмент».
                    </div>
                )}

                {/* ─── Транспортная единица ──────────────────────────────── */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16, marginTop: 16 }}>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Транспортная единица</label>
                        <div style={{ display: 'flex', gap: 0 }}>
                            <button
                                type="button"
                                className={`btn btn-sm ${!shippedAsBoxes ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setShippedAsBoxes(false)}
                                disabled={submitting}
                                style={{ borderRadius: '8px 0 0 8px' }}
                            >
                                Паллеты
                            </button>
                            <button
                                type="button"
                                className={`btn btn-sm ${shippedAsBoxes ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setShippedAsBoxes(true)}
                                disabled={submitting}
                                style={{ borderRadius: '0 8px 8px 0' }}
                            >
                                Короба
                            </button>
                        </div>
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">{unitCountLabel(shippedAsBoxes)}</label>
                        <input
                            className="form-input"
                            type="number"
                            min={0}
                            value={palletsCount}
                            disabled={submitting}
                            onChange={e => setPalletsCount(e.target.value ? Number(e.target.value) : '')}
                            placeholder={shippedAsBoxes ? '12' : '5'}
                        />
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">{unitWeightLabel(shippedAsBoxes)}, кг</label>
                        <input
                            className="form-input"
                            type="number"
                            min={0}
                            step="0.1"
                            value={palletWeight}
                            disabled={submitting}
                            onChange={e => setPalletWeight(e.target.value ? Number(e.target.value) : '')}
                            placeholder={shippedAsBoxes ? '18' : '300'}
                        />
                    </div>
                </div>

                {/* ─── Брак ──────────────────────────────────────────────── */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginTop: 16, marginBottom: 16 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 14 }}>
                        <input
                            type="checkbox"
                            checked={isDefect}
                            disabled={submitting}
                            onChange={e => setIsDefect(e.target.checked)}
                            style={{ width: 16, height: 16, cursor: 'pointer' }}
                        />
                        Перемещение брака
                    </label>
                    {isDefect && (
                        <div className="form-group" style={{ margin: 0, flex: 1 }}>
                            <input
                                className="form-input"
                                value={defectReason}
                                disabled={submitting}
                                onChange={e => setDefectReason(e.target.value)}
                                placeholder="Причина брака..."
                            />
                        </div>
                    )}
                </div>

                {/* ─── Состав ────────────────────────────────────────────── */}
                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
                    Состав · {formatNumber(items.length, 0)} позиций, {formatNumber(totalQty, 0)} шт
                </div>
                <TransferItemsEditor
                    rows={rows}
                    onChange={setRows}
                    nomenclature={nomenclature}
                    stockMap={stockMap}
                    stockLabel={isDefect ? 'В БРАКЕ' : 'НА СКЛАДЕ'}
                    stockAccent={isDefect}
                    maxHeight={320}
                    disabled={submitting}
                />
                {overStock.length > 0 && (
                    <div style={{ fontSize: 12, color: 'var(--color-warning)', marginTop: 8 }}>
                        Больше остатка на складе-источнике:{' '}
                        {overStock.map(it => `${it.barcode} (${formatNumber(it.quantity, 0)} из ${formatNumber(stockMap?.[it.barcode] ?? 0, 0)})`).join(', ')}.
                        Сохранить черновик можно — отправить не получится, пока товар не приедет.
                    </div>
                )}

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={submitting}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleSubmit} disabled={submitting}>
                        {submitting ? 'Сохранение...' : 'Сохранить'}
                    </button>
                </div>
            </div>
        </div>
    );
}
