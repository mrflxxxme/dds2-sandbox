'use client';
import React from 'react';
import { formatNumber } from '@/lib/utils';
import BoxDetailCell from '@/components/BoxDetailCell';
import type { PackageType } from '@/types/api';

const PKG_LABEL: Record<PackageType, string> = { BOX: 'Короб', MONOPALLET: 'Моно', SUPERSAFE: 'Сейф' };

/** Единый `tdBase` матрицы (совпадает с «Потребность по складам»). */
export const NEED_TD_BASE: React.CSSProperties = {
    padding: '7px 6px', textAlign: 'right', fontSize: 12,
    borderBottom: '1px solid var(--color-border)', verticalAlign: 'top',
};

export interface NeedMatrixCellProps {
    /** Что отправляем в этот WB-склад (шт + тип упаковки). null — ничего не отправляем. */
    ship?: { qty: number; pkg: PackageType } | null;
    /** Кратность короба (для счётчика коробов). */
    ppb?: number | null;
    /** 🏬 остаток на WB (синий). */
    stock?: number;
    /** 🚚 в сборке / в пути (зелёный). */
    onWay?: number;
    /** Потребность склада (показываем ↗N, если сами туда ничего не шлём). */
    need?: number;
    /** Тонировка фона колонки по округу (полупрозрачный цвет округа). */
    tint?: string;
}

/**
 * Ячейка матрицы распределения — единый стек, как в «Потребность по складам»:
 *   строка 1 — сколько отправляем (+ бейдж типа упаковки + счётчик коробов);
 *   строка 2 — 🏬 остаток на WB (синий);
 *   строка 3 — 🚚 в сборке / в пути (зелёный).
 * Переиспользуема обоими экранами (черновик потребности и «Распределить машину»).
 */
export default function NeedMatrixCell({ ship, ppb, stock = 0, onWay = 0, need = 0, tint }: NeedMatrixCellProps) {
    const shipQty = ship?.qty ?? 0;
    return (
        <td style={{ ...NEED_TD_BASE, background: tint }}>
            {shipQty > 0 && ship ? (
                <div style={{ fontWeight: 700, color: 'var(--color-accent)', lineHeight: 1.3 }}>
                    {formatNumber(shipQty, 0)}
                    {ship.pkg !== 'BOX' && <span style={{ fontSize: 9, color: 'var(--color-muted)' }}> {PKG_LABEL[ship.pkg]}</span>}
                    <span style={{ fontSize: 10, color: 'var(--color-muted)', marginLeft: 4 }}>
                        📦<BoxDetailCell qty={shipQty} pcsPerBox={ppb ?? 0} />
                    </span>
                </div>
            ) : need > 0 ? (
                <div style={{ color: 'var(--color-dim)', lineHeight: 1.3 }} title="Потребность склада — сами сюда не отправляем">
                    ↗{formatNumber(need, 0)}
                </div>
            ) : (
                <div style={{ color: 'var(--color-dim)', lineHeight: 1.3 }}>·</div>
            )}
            {stock > 0 && (
                <div style={{ fontSize: 10, color: 'var(--color-accent)', whiteSpace: 'nowrap', lineHeight: 1.4 }} title="Остаток на Wildberries">
                    🏬 {formatNumber(stock, 0)}
                </div>
            )}
            {onWay > 0 && (
                <div style={{ fontSize: 10, color: 'var(--color-success)', whiteSpace: 'nowrap', lineHeight: 1.4 }} title="В сборке / в пути">
                    🚚 {formatNumber(onWay, 0)}
                </div>
            )}
        </td>
    );
}
