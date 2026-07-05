'use client';
import React from 'react';
import { formatNumber } from '@/lib/utils';
import type { PackageType } from '@/types/api';

/** Иконки типа упаковки — как в «Потребность по складам»: 📦 короб · 📐 моно · 🔒 сейф. */
const PKG_ICON: Record<PackageType, string> = { BOX: '📦', MONOPALLET: '📐', SUPERSAFE: '🔒' };
const PKG_LABEL: Record<PackageType, string> = { BOX: 'Короб', MONOPALLET: 'Моно-паллета', SUPERSAFE: 'Сейф' };

/** Единый `tdBase` матрицы (совпадает с «Потребность по складам»). */
export const NEED_TD_BASE: React.CSSProperties = {
    padding: '7px 6px', textAlign: 'right', fontSize: 12,
    borderBottom: '1px solid var(--color-border)', verticalAlign: 'top',
};

/** Метка приёмки WB для ячейки отгрузки: ⌛ нет лимита приёмки (нужна предзаявка). */
export interface CellMark {
    /** Открыт по options, но лимита приёмки нет (0 дней) → ⌛ нужна предзаявка. */
    noLimit: boolean;
}

export interface NeedMatrixCellProps {
    /** Что отправляем в этот WB-склад (шт + тип упаковки). null — ничего не отправляем. */
    ship?: { qty: number; pkg: PackageType } | null;
    /** 🏬 остаток на WB (синий). */
    stock?: number;
    /** 🚚 в сборке / в пути (зелёный). */
    onWay?: number;
    /** Тонировка фона колонки по округу (полупрозрачный цвет округа). */
    tint?: string;
    /** Метка приёмки WB (📐/📦/🔒 · ⌛ предзаявка · ⛔ закрыто) — как в черновике. */
    mark?: CellMark | null;
}

/**
 * Ячейка матрицы распределения — показываем ТОЛЬКО что реально поедет (не потребность):
 *   строка 1 — сколько отправляем + иконка типа упаковки (📦 короб / 📐 моно / 🔒 сейф) + ⌛ предзаявка,
 *              либо «·», если в этот склад ничего не шлём;
 *   строка 2 — 🏬 остаток на WB (синий);
 *   строка 3 — 🚚 в сборке / в пути (зелёный).
 */
export default function NeedMatrixCell({ ship, stock = 0, onWay = 0, tint, mark }: NeedMatrixCellProps) {
    const shipQty = ship?.qty ?? 0;
    return (
        <td style={{ ...NEED_TD_BASE, background: tint }}>
            {shipQty > 0 && ship ? (
                <div style={{ fontWeight: 700, color: 'var(--color-accent)', lineHeight: 1.3 }}>
                    {formatNumber(shipQty, 0)}
                    <span style={{ fontSize: 11, color: 'var(--color-muted)' }} title={PKG_LABEL[ship.pkg]}> {PKG_ICON[ship.pkg]}</span>
                    {mark?.noLimit && (
                        <span style={{ fontSize: 11 }} title="Нет лимита приёмки — нужна предзаявка"> ⌛</span>
                    )}
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
