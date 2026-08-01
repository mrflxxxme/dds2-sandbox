'use client';
/**
 * Правка переезда — ОТДЕЛЬНАЯ СТРАНИЦА, а не модалка.
 *
 * Макет — зеркало «Редактирование ASM-…» (assembly/[id]/edit/page.tsx): та же
 * хлебная крошка со стрелкой, тот же крупный заголовок, та же карточка полей в
 * две колонки и отдельная карточка «Позиции (N)» с таблицей и кнопками
 * сохранения в её подвале. Переезд — зеркало заявки на сборку во всём
 * остальном (статусы, машина, единица поставки), и правка обязана выглядеть
 * так же: два разных вида для одной и той же операции — это два языка для
 * пользователя.
 *
 * Отличия от заявки — ровно там, где отличается сам документ:
 *  • вместо «Склад» + «Поставка FBO» — два конца маршрута («Откуда» / «Куда»);
 *  • нет «Даты готовности»: у переезда есть только `actual_ready_date`, и её
 *    ставит переход в READY, а не форма (PUT её не принимает) — поля, которого
 *    нет в контракте, здесь не выдумываем;
 *  • нет «Склада сдачи WB» — переезд едет между НАШИМИ складами;
 *  • есть брак (`is_defect` + причина), которого у заявки нет: он живёт рядом с
 *    комментарием и переключает колонку остатка на «В БРАКЕ».
 *
 * 🔴 `shipped_as_boxes` здесь — ОБЫЧНЫЙ bool (пустое = паллеты), как на
 * создании переезда, а НЕ трёхзначный «null = не трогать» из «Назначить
 * машину»: форма правки всегда видит текущее значение и всегда знает, что слать.
 *
 * Остатки склада-источника тянем ради колонки «На складе»: превышение
 * подсвечиваем, но НЕ блокируем сохранение — до отгрузки остаток не держится, и
 * решать, доедет ли товар к отправке, логисту, а не форме. Жёсткий гард стоит
 * на «Отправить» (бэкенд).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { mergeRowsByBarcode } from '@/lib/utils/transferRows';
import TransferItemsEditor, { emptyTransferItemRow, transferItemRows } from '@/components/TransferItemsEditor';
import type { TransferItemRow } from '@/components/TransferItemsEditor';
import { usePermissions } from '@/lib/hooks/usePermissions';
import {
    canEditTransfer,
    toMoney,
    transferEditError,
    transferStatusLabel,
    unitCountLabel,
    unitWeightLabel,
} from '@/lib/transfer';
import type { Nomenclature, StockTransfer, Warehouse } from '@/types/api';

export default function TransferEditPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const id = Number(params.id);
    const { canEdit, loading: permLoading } = usePermissions();

    // ─── Исходные данные ──────────────────────────────────────────────────
    const [transfer, setTransfer] = useState<StockTransfer | null>(null);
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [nomenclature, setNomenclature] = useState<Nomenclature[]>([]);

    // ─── Форма ────────────────────────────────────────────────────────────
    const [fromWarehouseId, setFromWarehouseId] = useState<number | ''>('');
    const [toWarehouseId, setToWarehouseId] = useState<number | ''>('');
    const [comment, setComment] = useState('');
    const [isDefect, setIsDefect] = useState(false);
    const [defectReason, setDefectReason] = useState('');
    const [shippedAsBoxes, setShippedAsBoxes] = useState(false);
    const [palletsCount, setPalletsCount] = useState<number | ''>('');
    const [palletWeight, setPalletWeight] = useState<number | ''>('');
    const [rows, setRows] = useState<TransferItemRow[]>([]);

    // ─── Состояния ────────────────────────────────────────────────────────
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState('');
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);

    /** Остаток склада-источника по ШК; undefined — колонку не рисуем. */
    const [stockMap, setStockMap] = useState<Record<string, number> | undefined>(undefined);

    // ─── Загрузка ─────────────────────────────────────────────────────────

    const load = useCallback(async () => {
        setLoading(true);
        setLoadError('');
        try {
            const [t, whs] = await Promise.all([api.getTransfer(id), api.getWarehouses()]);
            setTransfer(t);
            setWarehouses(whs);
            setFromWarehouseId(t.from_warehouse_id);
            setToWarehouseId(t.to_warehouse_id);
            setComment(t.comment ?? '');
            setIsDefect(!!t.is_defect);
            setDefectReason(t.defect_reason ?? '');
            setShippedAsBoxes(!!t.shipped_as_boxes);
            setPalletsCount(t.pallets_count ?? '');
            // pallet_weight_kg — Numeric, приезжает СТРОКОЙ: через toMoney, иначе
            // «175.50» легло бы в поле строкой и строкой же уехало обратно.
            setPalletWeight(toMoney(t.pallet_weight_kg) ?? '');
            setRows(transferItemRows(t.items ?? []));
        } catch (e: unknown) {
            setLoadError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => { load(); }, [load]);

    // Номенклатура — фоном: без неё форма живёт (колонки «Товар»/«Артикул»
    // покажут прочерк), а весит она заметно больше самой карточки.
    useEffect(() => {
        let cancelled = false;
        api.getNomenclature()
            .then(n => { if (!cancelled) setNomenclature(n); })
            .catch(() => { /* колонка названия — необязательная */ });
        return () => { cancelled = true; };
    }, []);

    // Остатки склада-источника: перезапрашиваем при смене склада/режима брака.
    // Ошибку глушим — колонка необязательная, форма без неё работает.
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

    // ─── Вычисляемое ──────────────────────────────────────────────────────

    const activeWarehouses = useMemo(
        // Неактивный склад из самого переезда оставляем в списке — иначе он бы
        // молча «переехал» на первый попавшийся при сохранении.
        () => warehouses.filter(w => w.is_active
            || w.id === transfer?.from_warehouse_id
            || w.id === transfer?.to_warehouse_id),
        [warehouses, transfer?.from_warehouse_id, transfer?.to_warehouse_id],
    );

    const items = useMemo(() => mergeRowsByBarcode(rows), [rows]);
    const totalQty = items.reduce((s, it) => s + it.quantity, 0);
    const totalWeight = palletsCount !== '' && palletWeight !== ''
        ? Number(palletsCount) * Number(palletWeight)
        : null;

    /**
     * Связки ФФ, которые новый маршрут осиротил бы: зеркало отгрузки живёт на
     * складе ЗАБОРА, приёмки — на складе ПОЛУЧАТЕЛЯ, и бэкенд на такую правку
     * отвечает 400. Предупреждаем ДО сохранения — иначе логист узнаёт об этом
     * из отказа после того, как переписал состав.
     */
    const orphanedFfLinks = useMemo(() => {
        return (transfer?.ff_links ?? []).filter(l => {
            const expected = l.kind === 'assembly' ? fromWarehouseId : toWarehouseId;
            return expected !== '' && l.warehouse_id !== Number(expected);
        });
    }, [transfer?.ff_links, fromWarehouseId, toWarehouseId]);

    // Превышение остатка — предупреждение, не блокировка (см. шапку файла).
    const overStock = useMemo(() => {
        if (!stockMap) return [];
        return items.filter(it => it.quantity > (stockMap[it.barcode] || 0));
    }, [items, stockMap]);

    // ─── Сохранение ───────────────────────────────────────────────────────

    const handleSubmit = async () => {
        if (saving) return;
        const validation = transferEditError({ fromWarehouseId, toWarehouseId, itemCount: items.length });
        if (validation) { setError(validation); return; }
        setSaving(true);
        setError('');
        try {
            await api.updateTransfer(id, {
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
            // Тост показывает карточка по ?saved=1 — она же снимет параметр с URL.
            router.push(`/p/${slug}/warehouse/transfers/${id}?saved=1`);
        } catch (e: unknown) {
            // Тексты бэкенда осмысленные и русские (включая отказ при смене
            // маршрута под живыми связками ФФ) — показываем как есть.
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
            setSaving(false);
        }
    };

    const cardHref = `/p/${slug}/warehouse/transfers/${id}`;

    // ─── Состояния: loading / error / empty / data ─────────────────────────

    if (loading) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            </div>
        );
    }

    if (loadError && !transfer) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-danger)' }}>
                    {loadError}
                    <div style={{ marginTop: 16, display: 'flex', gap: 8, justifyContent: 'center' }}>
                        <button className="btn btn-secondary" onClick={load}>Повторить</button>
                        <Link href={cardHref}>
                            <button className="btn btn-secondary">К перемещению</button>
                        </Link>
                    </div>
                </div>
            </div>
        );
    }

    if (!transfer) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>🚚</div>
                    <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Перемещение не найдено</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14, marginBottom: 16 }}>
                        Возможно, черновик удалили — вернитесь к списку перемещений
                    </div>
                    <Link href={`/p/${slug}/warehouse/assembly?tab=transfers`}>
                        <button className="btn btn-secondary">К списку</button>
                    </Link>
                </div>
            </div>
        );
    }

    /**
     * Гейт правки — тот же, что прячет кнопку на карточке (PENDING /
     * IN_PROGRESS / READY). Прямой заход по URL в другом статусе показывает
     * причину и дорогу назад, а не пустую форму, которую бэкенд отвергнет 400.
     * Права редактора — отдельный множитель: у viewer'а правки нет вовсе.
     */
    const statusClosed = !canEditTransfer(transfer.status);
    const noRights = !permLoading && !canEdit();
    if (statusClosed || noRights) {
        return (
            <div className="animate-in">
                <div className="page-header">
                    <div>
                        <Link href={cardHref} style={{ color: 'var(--color-text-muted)', textDecoration: 'none', fontSize: 14 }}>
                            &larr; {transfer.number}
                        </Link>
                        <h1 className="page-title">Редактирование {transfer.number}</h1>
                    </div>
                </div>
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>🔒</div>
                    <div style={{ fontSize: 15, fontWeight: 500, marginBottom: 6 }}>
                        {statusClosed
                            ? `Правка закрыта: переезд в статусе «${transferStatusLabel(transfer.status)}»`
                            : 'Недостаточно прав: правка перемещений доступна редакторам'}
                    </div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14, marginBottom: 16 }}>
                        {!statusClosed
                            ? 'Карточку можно смотреть — менять маршрут и состав нельзя.'
                            : transfer.status === 'CANCELLED'
                                ? 'Переезд отменён — маршрут и состав больше не меняются.'
                                : 'Править можно, пока переезд не уехал (Создан / В сборке / Готово): после отправки сток уже списан со склада-источника.'}
                    </div>
                    <Link href={cardHref}>
                        <button className="btn btn-secondary">Вернуться к {transfer.number}</button>
                    </Link>
                </div>
            </div>
        );
    }

    return (
        <div className="animate-in">
            {/* ─── Шапка ──────────────────────────────────────────────── */}
            <div className="page-header">
                <div>
                    <Link href={cardHref} style={{ color: 'var(--color-text-muted)', textDecoration: 'none', fontSize: 14 }}>
                        &larr; {transfer.number}
                    </Link>
                    <h1 className="page-title">Редактирование {transfer.number}</h1>
                    <p className="page-subtitle">
                        Править можно, пока переезд не уехал: после «Отправить» сток уже списан со склада-источника.
                    </p>
                </div>
            </div>

            {/* Ошибка — валидация формы либо текст бэкенда как есть */}
            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)', whiteSpace: 'pre-line' }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={() => setError('')}>
                        Закрыть
                    </button>
                </div>
            )}

            {/* ─── Поля ───────────────────────────────────────────────── */}
            <div className="glass-card" style={{ padding: 24, marginBottom: 16 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                    <div className="form-group">
                        <label className="form-label">Откуда</label>
                        <select
                            className="form-input"
                            value={fromWarehouseId}
                            disabled={saving}
                            onChange={e => setFromWarehouseId(e.target.value ? Number(e.target.value) : '')}
                        >
                            <option value="">Выберите склад...</option>
                            {activeWarehouses.map(w => (
                                <option key={w.id} value={w.id}>{w.name}</option>
                            ))}
                        </select>
                    </div>

                    <div className="form-group">
                        <label className="form-label">Куда</label>
                        <select
                            className="form-input"
                            value={toWarehouseId}
                            disabled={saving}
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

                    {orphanedFfLinks.length > 0 && (
                        <div style={{ gridColumn: '1 / -1', fontSize: 12, color: 'var(--color-warning)' }}>
                            Новый маршрут разойдётся со связками ФФ ({orphanedFfLinks.map(l => l.number || l.external_id).join(', ')}) —
                            сохранение отклонят. Сначала отвяжите их в блоке «Фулфилмент» на карточке.
                        </div>
                    )}

                    {/* Единица поставки: паллеты или короба — меняет только
                        подписи и смысл двух соседних полей. */}
                    <div className="form-group">
                        <label className="form-label">Единица поставки</label>
                        <select
                            className="form-input"
                            value={shippedAsBoxes ? 'boxes' : 'pallets'}
                            disabled={saving}
                            onChange={e => setShippedAsBoxes(e.target.value === 'boxes')}
                        >
                            <option value="pallets">Паллеты</option>
                            <option value="boxes">Короба</option>
                        </select>
                    </div>

                    {/* Количество единиц + вес одной + вычисляемый общий вес */}
                    <div style={{ display: 'flex', gap: 12 }}>
                        <div className="form-group" style={{ flex: 1 }}>
                            <label className="form-label">{unitCountLabel(shippedAsBoxes)}</label>
                            <input
                                className="form-input"
                                type="number"
                                min={0}
                                value={palletsCount}
                                disabled={saving}
                                onChange={e => setPalletsCount(e.target.value ? Number(e.target.value) : '')}
                                placeholder={shippedAsBoxes ? '12' : '5'}
                                style={{ width: '100%' }}
                            />
                        </div>
                        <div className="form-group" style={{ flex: 1 }}>
                            <label className="form-label">{unitWeightLabel(shippedAsBoxes)} (кг)</label>
                            <input
                                className="form-input"
                                type="number"
                                min={0}
                                step={0.1}
                                value={palletWeight}
                                disabled={saving}
                                onChange={e => setPalletWeight(e.target.value ? Number(e.target.value) : '')}
                                placeholder={shippedAsBoxes ? '18' : '300'}
                                style={{ width: '100%' }}
                            />
                        </div>
                        <div className="form-group" style={{ flex: 1 }}>
                            <label className="form-label">Общий вес</label>
                            {/* Читаемое поле, а не инпут: вес считается, а не вводится.
                                Фон — тот же --color-bg-input, что у соседних полей
                                (эталон заявки просит несуществующую переменную
                                --color-bg-secondary и остаётся прозрачным). */}
                            <div style={{
                                padding: '12px 16px', background: 'var(--color-bg-input)',
                                border: '1px solid var(--color-border)', borderRadius: 12, fontWeight: 500,
                            }}>
                                {totalWeight ? `${formatNumber(totalWeight, 1)} кг` : '—'}
                            </div>
                        </div>
                    </div>

                    <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                        <label className="form-label">Комментарий</label>
                        <textarea
                            className="form-input"
                            rows={2}
                            value={comment}
                            disabled={saving}
                            onChange={e => setComment(e.target.value)}
                            placeholder="Примечания к перемещению..."
                        />
                    </div>

                    {/* Брак — своё поле переезда, у заявки такого нет. Стоит
                        рядом с комментарием и переключает колонку остатка
                        на «В браке»: годный и бракованный сток разные. */}
                    <div style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 14 }}>
                            <input
                                type="checkbox"
                                checked={isDefect}
                                disabled={saving}
                                onChange={e => setIsDefect(e.target.checked)}
                                style={{ width: 16, height: 16, cursor: 'pointer' }}
                            />
                            Перемещение брака
                        </label>
                        {isDefect && (
                            <div className="form-group" style={{ flex: 1, minWidth: 220 }}>
                                <input
                                    className="form-input"
                                    value={defectReason}
                                    disabled={saving}
                                    onChange={e => setDefectReason(e.target.value)}
                                    placeholder="Причина брака..."
                                    style={{ width: '100%' }}
                                />
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {/* ─── Позиции ────────────────────────────────────────────── */}
            <div className="glass-card" style={{ padding: 24 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
                    <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>
                        Позиции ({formatNumber(items.length, 0)})
                        <span style={{ fontSize: 13, fontWeight: 400, color: 'var(--color-text-muted)', marginLeft: 8 }}>
                            {formatNumber(totalQty, 0)} шт
                        </span>
                    </h2>
                    <button
                        className="btn btn-secondary btn-sm"
                        disabled={saving}
                        onClick={() => setRows(prev => [...prev, emptyTransferItemRow()])}
                    >
                        + Добавить позицию
                    </button>
                </div>

                <TransferItemsEditor
                    rows={rows}
                    onChange={setRows}
                    nomenclature={nomenclature}
                    stockMap={stockMap}
                    stockLabel={isDefect ? 'В браке' : 'На складе'}
                    stockAccent={isDefect}
                    flat
                    disabled={saving}
                />

                {overStock.length > 0 && (
                    <div style={{ fontSize: 12, color: 'var(--color-warning)', marginTop: 8 }}>
                        Больше остатка на складе-источнике:{' '}
                        {overStock.map(it => `${it.barcode} (${formatNumber(it.quantity, 0)} из ${formatNumber(stockMap?.[it.barcode] ?? 0, 0)})`).join(', ')}.
                        Сохранить можно — отправить не получится, пока товар не приедет.
                    </div>
                )}

                {/* Кнопки — там же, где у заявки: в подвале карточки позиций */}
                <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', marginTop: 24 }}>
                    <Link href={cardHref}>
                        <button className="btn btn-secondary" disabled={saving}>Отмена</button>
                    </Link>
                    <button className="btn btn-primary" onClick={handleSubmit} disabled={saving}>
                        {saving ? 'Сохранение...' : 'Сохранить'}
                    </button>
                </div>
            </div>
        </div>
    );
}
