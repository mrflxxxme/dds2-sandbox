'use client';
import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { mergeRowsByBarcode } from '@/lib/utils/transferRows';
import { unitCountLabel, unitWeightLabel } from '@/lib/transfer';
import TransferItemsEditor, { emptyTransferItemRow } from '@/components/TransferItemsEditor';
import type { TransferItemRow } from '@/components/TransferItemsEditor';
import type { Nomenclature, Warehouse } from '@/types/api';

/* ─── Page ────────────────────────────────────────────────────────────────── */

export default function NewTransferPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const fromWarehouseId = Number(params.id);

    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);
    const [fromName, setFromName] = useState('');
    const [loading, setLoading] = useState(true);

    const [toWarehouseId, setToWarehouseId] = useState<number | ''>('');
    const [formComment, setFormComment] = useState('');
    const [isDefect, setIsDefect] = useState(false);
    const [defectReason, setDefectReason] = useState('');
    // Транспортная единица переезда — та же, что у заявки на сборку: флаг меняет
    // только ЕДИНИЦУ измерения двух полей ниже и подписи (см. lib/transfer.ts).
    //
    // 🔴 Семантика пустого значения здесь НЕ такая, как в «Назначить машину».
    // На создании (StockTransferCreate) shipped_as_boxes — обычный bool с
    // дефолтом false: пустое = «паллеты». Трёхзначность («null = не трогай»)
    // живёт только в TransferAssignVehicle, где есть что не трогать. Поэтому
    // компонент формы назначения машины здесь намеренно НЕ переиспользован:
    // он протащил бы сюда чужой контракт.
    const [shippedAsBoxes, setShippedAsBoxes] = useState(false);
    const [palletsCount, setPalletsCount] = useState<number | ''>('');
    const [palletWeight, setPalletWeight] = useState<number | ''>('');
    const [rows, setRows] = useState<TransferItemRow[]>(() => Array.from({ length: 8 }, emptyTransferItemRow));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [stockMap, setStockMap] = useState<Record<string, number>>({});
    const [defectMap, setDefectMap] = useState<Record<string, number>>({});

    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const [whs, nomData, whStock, defectStock] = await Promise.all([
                api.getWarehouses(),
                api.getNomenclature(),
                api.getWarehouseStock(fromWarehouseId),
                api.getDefectStock(fromWarehouseId),
            ]);
            setWarehouses(whs);
            const wh = whs.find(w => w.id === fromWarehouseId);
            setFromName(wh?.name || `Склад #${fromWarehouseId}`);
            setNomenclature(nomData);
            const sm: Record<string, number> = {};
            whStock.forEach((r: { barcode: string; quantity: number }) => { sm[r.barcode] = r.quantity; });
            setStockMap(sm);
            const dm: Record<string, number> = {};
            defectStock.forEach((r: { barcode: string; defect_quantity: number }) => { dm[r.barcode] = r.defect_quantity; });
            setDefectMap(dm);
        } catch { /* ignore */ }
        setLoading(false);
    }, [fromWarehouseId]);

    useEffect(() => { loadData(); }, [loadData]);

    /* ─── Computed ────────────────────────────────────────────────────────── */

    const filledRows = rows.filter(r => r.barcode.trim() && r.quantity.trim());
    const otherWarehouses = warehouses.filter(w => w.id !== fromWarehouseId && w.is_active);

    /* ─── Submit ──────────────────────────────────────────────────────────── */

    const handleCreate = async () => {
        if (!toWarehouseId) { setError('Выберите склад назначения'); return; }
        if (filledRows.length === 0) { setError('Добавьте хотя бы одну позицию'); return; }
        const zero = filledRows.filter(r => (parseInt(r.quantity, 10) || 0) <= 0);
        if (zero.length > 0) {
            setError(`Количество должно быть больше нуля: ${zero.map(r => r.barcode.trim()).join(', ')}`);
            return;
        }
        // Схлопываем дубли ШК и валидируем по СУММЕ на штрихкод, а не построчно:
        // одна строка «52 ≤ остатка» проходит, а сумма трёх таких строк — нет,
        // и тогда backend-send падает построчным «have 0».
        const items = mergeRowsByBarcode(filledRows);
        const availMap = isDefect ? defectMap : stockMap;
        const over = items.filter(it => it.quantity > (availMap[it.barcode] || 0));
        if (over.length > 0) {
            setError(`Недостаточно остатка: ${over.map(it => `${it.barcode} (нужно ${formatNumber(it.quantity)}, есть ${formatNumber(availMap[it.barcode] || 0)})`).join(', ')}`);
            return;
        }
        setSaving(true);
        setError('');
        try {
            const transfer = await api.createTransfer({
                from_warehouse_id: fromWarehouseId,
                to_warehouse_id: Number(toWarehouseId),
                comment: formComment.trim() || undefined,
                is_defect: isDefect || undefined,
                defect_reason: isDefect && defectReason.trim() ? defectReason.trim() : undefined,
                items,
                // Оценка необязательна: пустые поля уходят как null («не задано»),
                // а флаг единицы — всегда, у него на этом пути обычный дефолт.
                pallets_count: palletsCount === '' ? null : Number(palletsCount),
                pallet_weight_kg: palletWeight === '' ? null : Number(palletWeight),
                shipped_as_boxes: shippedAsBoxes,
            });
            if (transfer?.id) {
                // Auto-send (source deducted immediately, destination gets in_transit).
                // Destination must accept via "Принять": брак — вкладка «Брак»,
                // обычные — вкладка «Перемещения»
                try {
                    await api.sendTransfer(transfer.id);
                } catch (sendErr: unknown) {
                    // Откат черновика, чтобы не оставлять сироту (ретрай создал бы дубль)
                    try {
                        await api.cancelTransfer(transfer.id);
                    } catch {
                        setError(`Отправка не удалась, черновик ${transfer.number} остался — управляйте им на вкладке «Перемещения». ` +
                            (sendErr instanceof Error ? sendErr.message : ''));
                        setSaving(false);
                        return;
                    }
                    throw sendErr;
                }
            }
            router.push(`/p/${slug}/warehouse/${fromWarehouseId}`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        }
        setSaving(false);
    };

    const goBack = () => router.push(`/p/${slug}/warehouse/${fromWarehouseId}`);

    /* ─── Render ──────────────────────────────────────────────────────────── */

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    return (
        <div className="animate-in">
            {/* Header */}
            <div className="page-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <button
                        onClick={goBack}
                        className="btn btn-secondary"
                        style={{ padding: '6px 12px', fontSize: 18, lineHeight: 1 }}
                        title="Назад"
                    >&larr;</button>
                    <div>
                        <h1 className="page-title">Новое перемещение</h1>
                        <p className="page-subtitle">Со склада: {fromName}</p>
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary" onClick={goBack}>Отмена</button>
                    <button
                        className="btn btn-primary"
                        onClick={handleCreate}
                        disabled={saving || filledRows.length === 0 || !toWarehouseId}
                    >
                        {saving ? 'Сохранение...' : 'Создать перемещение'}
                    </button>
                </div>
            </div>

            {error && (
                <div style={{
                    color: 'var(--color-danger)', background: 'rgba(239,68,68,0.06)',
                    padding: '10px 16px', borderRadius: 8, marginBottom: 16, fontSize: 13,
                }}>
                    {error}
                </div>
            )}

            {/* Parameters */}
            <div className="glass-card" style={{ padding: 20, marginBottom: 20 }}>
                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Параметры перемещения</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Откуда</label>
                        <input className="form-input" value={fromName} disabled style={{ background: 'var(--color-hover)' }} />
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Куда *</label>
                        <select
                            className="form-input"
                            value={toWarehouseId}
                            onChange={e => setToWarehouseId(e.target.value ? Number(e.target.value) : '')}
                        >
                            <option value="">Выберите склад...</option>
                            {otherWarehouses.map(w => (
                                <option key={w.id} value={w.id}>{w.name}</option>
                            ))}
                        </select>
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Комментарий</label>
                        <input className="form-input" value={formComment} onChange={e => setFormComment(e.target.value)} placeholder="Примечание..." />
                    </div>
                </div>
                {/* Транспортная единица — как у заявки на сборку: переключатель
                    меняет только подписи и смысл двух соседних полей. Оценка
                    необязательна — переезд можно завести и без неё, уточнив при
                    назначении машины. */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16, marginTop: 16 }}>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label">Транспортная единица</label>
                        <div style={{ display: 'flex', gap: 0 }}>
                            <button
                                type="button"
                                className={`btn btn-sm ${!shippedAsBoxes ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setShippedAsBoxes(false)}
                                style={{ borderRadius: '8px 0 0 8px' }}
                            >
                                Паллеты
                            </button>
                            <button
                                type="button"
                                className={`btn btn-sm ${shippedAsBoxes ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setShippedAsBoxes(true)}
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
                            onChange={e => setPalletWeight(e.target.value ? Number(e.target.value) : '')}
                            placeholder={shippedAsBoxes ? '18' : '300'}
                        />
                    </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginTop: 16 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 14 }}>
                        <input
                            type="checkbox"
                            checked={isDefect}
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
                                onChange={e => setDefectReason(e.target.value)}
                                placeholder="Причина брака..."
                            />
                        </div>
                    )}
                </div>
            </div>

            <TransferItemsEditor
                rows={rows}
                onChange={setRows}
                nomenclature={nomenclature}
                stockMap={isDefect ? defectMap : stockMap}
                stockLabel={isDefect ? 'В БРАКЕ' : 'НА СКЛАДЕ'}
                stockAccent={isDefect}
            />

            {/* Bottom action bar */}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20, paddingBottom: 20 }}>
                <button className="btn btn-secondary" onClick={goBack}>Отмена</button>
                <button
                    className="btn btn-primary"
                    onClick={handleCreate}
                    disabled={saving || filledRows.length === 0 || !toWarehouseId}
                >
                    {saving ? 'Сохранение...' : `Создать перемещение (${filledRows.length} поз.)`}
                </button>
            </div>
        </div>
    );
}
