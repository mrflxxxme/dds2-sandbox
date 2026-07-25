'use client';
/**
 * Экран поставки — то, что открывается кликом по строке, как в кабинете WB.
 *
 * В кабинете поставка не «разворачивается» внутри списка: клик уводит на
 * отдельный экран с шапкой (склад, габарит, заказы, грузоместа, дата, QR),
 * панелью действий и списком заданий. Разворот строки этого не заменял:
 * шапка не помещалась, а действия терялись между таблицами.
 */
import { useState } from 'react';
import { formatDateTime, formatNumber } from '@/lib/utils';
import type { FbsSupply, FbsWarehouse } from '@/types/api';
import { cargoCabinetLabel, copyText, crossBorderLabel } from './fbsShared';
import PickListModal from './PickListModal';
import SupplyOrdersPanel from './SupplyOrdersPanel';

interface Props {
    supply: FbsSupply;
    warehouses: FbsWarehouse[];
    /** Режим контура разрешает запись в WB: передача/удаление — запись, лист подбора и стикеры — чтение. */
    writeEnabled: boolean;
    writeHint: string;
    /** Идёт действие над этой поставкой (передача / удаление / QR). */
    busy: boolean;
    onBack: () => void;
    onDeliver: (s: FbsSupply) => void;
    onDelete: (s: FbsSupply) => void;
    onBarcode: (s: FbsSupply) => void;
    onToast: (msg: string) => void;
}

export default function SupplyDetailScreen({
    supply, warehouses, writeEnabled, writeHint, busy,
    onBack, onDeliver, onDelete, onBarcode, onToast,
}: Props) {
    const [pickList, setPickList] = useState(false);

    const wh = warehouses.find(w => w.wb_warehouse_id === supply.wb_warehouse_id);
    const warehouseName = wh?.name
        || (supply.wb_warehouse_id != null ? `#${supply.wb_warehouse_id}` : '');

    const handleCopyQr = async () => {
        const ok = await copyText(supply.qr_barcode || supply.wb_supply_id);
        onToast(ok ? 'QR поставки скопирован' : 'Скопировать не удалось — выделите значение вручную');
    };

    return (
        <div className="animate-in">
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
                <button className="btn btn-secondary btn-sm" onClick={onBack}>← к поставкам</button>
            </div>

            {/* Шапка поставки — по образцу экрана поставки в кабинете WB */}
            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                    <div style={{ flex: 1, minWidth: 240 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                            <h2 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>
                                {supply.name || supply.wb_supply_id}
                            </h2>
                            <span className={`badge ${supply.done ? 'badge-success' : 'badge-info'}`}>
                                {supply.done ? 'Передана' : 'Активная'}
                            </span>
                            {supply.is_b2b && <span className="badge badge-secondary">B2B</span>}
                        </div>
                        <div style={{ fontFamily: 'monospace', fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>
                            {supply.wb_supply_id}
                        </div>
                    </div>
                </div>

                <div style={{
                    display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
                    gap: 12, marginTop: 16,
                }}>
                    <HeaderField label="Склад продавца" value={warehouseName || '—'} />
                    <HeaderField
                        label="Габарит"
                        value={cargoCabinetLabel(supply.cargo_type)}
                        hint="Габарит фиксирует первое добавленное задание — смешивать WB не разрешает"
                        sub={crossBorderLabel(supply.cross_border_type)}
                    />
                    <HeaderField label="Заказы" value={formatNumber(supply.orders_count, 0)} />
                    <HeaderField
                        label="Грузоместа"
                        value="—"
                        hint="WB не отдаёт число грузомест в API поставки — оно фиксируется при приёмке"
                    />
                    <HeaderField
                        label="Создана"
                        value={supply.created_at_wb ? formatDateTime(supply.created_at_wb) : '—'}
                    />
                    <HeaderField
                        label="Закрыта"
                        value={supply.closed_at ? formatDateTime(supply.closed_at) : '—'}
                        hint="Поставка закрывается сама при приёмке первого товара"
                    />
                </div>

                {/* QR поставки: сам код с копированием + скачивание файла после передачи */}
                <div style={{
                    display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                    marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--color-border)',
                }}>
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>QR поставки</span>
                    <span style={{ fontFamily: 'monospace', fontSize: 13 }}>
                        {supply.qr_barcode || supply.wb_supply_id}
                    </span>
                    <button className="btn btn-sm" onClick={handleCopyQr} title="Скопировать код поставки">
                        📋 Копировать
                    </button>
                    {supply.done ? (
                        <button className="btn btn-secondary btn-sm" disabled={busy}
                            onClick={() => onBarcode(supply)}>
                            ⬇️ Скачать QR
                        </button>
                    ) : (
                        <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                            файл QR WB отдаёт только после передачи в доставку
                        </span>
                    )}
                    <div style={{ flex: 1 }} />
                    {!supply.done && supply.orders_count === 0 && (
                        <button className="btn btn-danger btn-sm"
                            disabled={!writeEnabled || busy}
                            title={writeEnabled ? undefined : writeHint}
                            onClick={() => onDelete(supply)}>
                            Удалить поставку
                        </button>
                    )}
                </div>
            </div>

            {/* Панель действий + задания поставки */}
            <div className="glass-card" style={{ padding: 20 }}>
                <SupplyOrdersPanel
                    supply={supply}
                    writeEnabled={writeEnabled}
                    writeHint={writeHint}
                    busy={busy}
                    onDeliver={onDeliver}
                    onBarcode={onBarcode}
                    onPickList={() => setPickList(true)}
                    onToast={onToast}
                />
            </div>

            {pickList && (
                <PickListModal
                    supply={supply}
                    warehouseName={warehouseName}
                    onClose={() => setPickList(false)}
                />
            )}
        </div>
    );
}

function HeaderField({ label, value, sub, hint }: {
    label: string;
    value: string;
    sub?: string;
    hint?: string;
}) {
    return (
        <div title={hint}>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 2 }}>{label}</div>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{value}</div>
            {sub && <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>{sub}</div>}
        </div>
    );
}
