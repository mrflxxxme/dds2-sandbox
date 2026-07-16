'use client';
import React, { useState } from 'react';
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
    /** Приёмка закрыта по всем типам (короб/моно/сейф) → ⛔ склад не принимает. */
    closed?: boolean;
    /** Короб закрыт, но моно открыто → 📐 «+» кладёт моно-паллетный план. */
    monoOnly?: boolean;
}

/** Управление ручной правкой ячейки (режим «✏️ Редактировать»): ±1 короб. */
export interface CellEditControl {
    /** Текущее число коробов в этот склад. */
    boxes: number;
    /** Кратность короба (шт/короб) — для подписи «×ppb». */
    ppb: number;
    /** Клик по −/+ (delta = ±1 короб). */
    onDelta: (delta: number) => void;
    /** «+» недоступен — исчерпан остаток машины по баркоду. */
    disableInc?: boolean;
}

export interface NeedMatrixCellProps {
    /** Что отправляем в этот WB-склад (шт + тип упаковки). null — ничего не отправляем. */
    ship?: { qty: number; pkg: PackageType } | null;
    /** 🏬 остаток на WB (синий). */
    stock?: number;
    /** 🔧 в сборке на ФФ — ещё не отправлено (зелёный). */
    asm?: number;
    /** 🚚 в пути на склад WB (зелёный). */
    transit?: number;
    /** Тонировка фона колонки по округу (полупрозрачный цвет округа). */
    tint?: string;
    /** Метка приёмки WB (📐/📦/🔒 · ⌛ предзаявка · ⛔ закрыто) — как в черновике. */
    mark?: CellMark | null;
    /** Режим правки: ±1 короб-степпер вместо статичного числа. null — только чтение. */
    edit?: CellEditControl | null;
}

/** Мини-кнопка степпера правки ячейки. */
const STEP_BTN: React.CSSProperties = {
    width: 18, height: 18, lineHeight: '16px', padding: 0, fontSize: 13, fontWeight: 700,
    borderRadius: 6, border: '1px solid var(--color-border)', background: 'var(--color-bg-card)',
    color: 'var(--color-text)', cursor: 'pointer',
};
const STEP_BTN_OFF: React.CSSProperties = { ...STEP_BTN, opacity: 0.35, cursor: 'not-allowed' };

/** Число коробов — редактируемое поле («простой ввод»: вписать сколько коробов,
 *  Enter/blur применяет как абсолютное значение; хэндлеры экрана капят рост). */
function BoxCountInput({ boxes, ppb, onSet }: { boxes: number; ppb: number; onSet: (n: number) => void }) {
    const [draft, setDraft] = useState<string | null>(null);
    const commit = () => {
        if (draft == null) return;
        const n = Math.floor(Number(draft));
        setDraft(null);
        if (Number.isFinite(n) && n >= 0 && n !== boxes) onSet(n);
    };
    return (
        <input
            type="number" min={0} inputMode="numeric" aria-label="Коробов на склад"
            value={draft ?? String(boxes)}
            onFocus={(ev) => ev.currentTarget.select()}
            onChange={(ev) => setDraft(ev.currentTarget.value)}
            onBlur={commit}
            onKeyDown={(ev) => {
                if (ev.key === 'Enter') ev.currentTarget.blur();          // blur → commit
                else if (ev.key === 'Escape') setDraft(null);             // откат к текущему, без blur (иначе stale-commit)
            }}
            title={`${formatNumber(boxes, 0)} короб × ${formatNumber(ppb, 0)} шт — впиши число коробов (Enter — применить)`}
            style={{
                width: 40, padding: '1px 3px', fontSize: 12, fontWeight: 700, textAlign: 'right',
                borderRadius: 6, border: '1px solid var(--color-border)', background: 'var(--color-bg-card)',
                color: boxes > 0 ? 'var(--color-accent)' : 'var(--color-dim)',
            }}
        />
    );
}

/**
 * Ячейка матрицы распределения — показываем ТОЛЬКО что реально поедет (не потребность):
 *   строка 1 — сколько отправляем + иконка типа упаковки (📦 короб / 📐 моно / 🔒 сейф) + ⌛ предзаявка,
 *              либо «·», если в этот склад ничего не шлём;
 *   строка 2 — 🏬 остаток на WB (синий);
 *   строка 3 — 🔧 в сборке на ФФ (зелёный);
 *   строка 4 — 🚚 в пути на склад WB (зелёный).
 */
export default function NeedMatrixCell({ ship, stock = 0, asm = 0, transit = 0, tint, mark, edit }: NeedMatrixCellProps) {
    const shipQty = ship?.qty ?? 0;
    const pkg = ship?.pkg ?? 'BOX';
    return (
        <td style={{ ...NEED_TD_BASE, background: tint }}>
            {edit ? (
                <div style={{ lineHeight: 1.25 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 4 }}>
                        <button type="button" title="− короб" aria-label="Убрать короб"
                            style={edit.boxes <= 0 ? STEP_BTN_OFF : STEP_BTN}
                            disabled={edit.boxes <= 0} onClick={() => edit.onDelta(-1)}>−</button>
                        <BoxCountInput boxes={edit.boxes} ppb={edit.ppb} onSet={(n) => edit.onDelta(n - edit.boxes)} />
                        <span style={{ fontSize: 9, fontWeight: 500, color: 'var(--color-muted)' }}>кор</span>
                        <button type="button" title="+ короб" aria-label="Добавить короб"
                            style={edit.disableInc ? STEP_BTN_OFF : STEP_BTN}
                            disabled={edit.disableInc} onClick={() => edit.onDelta(1)}>+</button>
                    </div>
                    {edit.boxes > 0 ? (
                        <div style={{ fontSize: 10, color: mark?.closed ? 'var(--color-danger)' : 'var(--color-muted)', whiteSpace: 'nowrap' }}>
                            = {formatNumber(edit.boxes * edit.ppb, 0)} шт
                            <span title={PKG_LABEL[pkg]}> {PKG_ICON[pkg]}</span>
                            {mark?.closed
                                ? <span title="Приёмка закрыта — склад не принимает"> ⛔</span>
                                : <>
                                    {mark?.monoOnly && <span title={mark.noLimit ? 'Только моно, и лимита нет — сдаётся ТОЛЬКО предзаявкой' : 'Короб не принимается — этот план едет моно-паллетой'}> 📐</span>}
                                    {mark?.noLimit && <span title="Нет лимита приёмки — нужна предзаявка"> ⌛</span>}
                                </>}
                        </div>
                    ) : (mark?.closed || mark?.noLimit || mark?.monoOnly) && (
                        // Пустая ячейка в режиме правки: метку видно ДО клика — не надо
                        // «тыкать в каждый склад», чтобы узнать, можно ли туда сдать.
                        <div style={{ fontSize: 9.5, whiteSpace: 'nowrap', color: mark.closed ? 'var(--color-danger)' : 'var(--color-warning)' }}>
                            {mark.closed
                                ? <span title="Приёмка закрыта — склад не принимает">⛔ закрыт</span>
                                : mark.monoOnly
                                    ? (mark.noLimit
                                        ? <span title="Короб закрыт, у моно нет лимита приёмки — сдаётся ТОЛЬКО предзаявкой">📐 предзаявка</span>
                                        : <span title="Короб не принимается, моно открыто — «+» положит моно-паллетный план">📐 моно</span>)
                                    : <span title="Нет лимита приёмки — нужна предзаявка">⌛ предзаявка</span>}
                        </div>
                    )}
                </div>
            ) : shipQty > 0 && ship ? (
                <div style={{ fontWeight: 700, color: mark?.closed ? 'var(--color-danger)' : 'var(--color-accent)', lineHeight: 1.3 }}>
                    {formatNumber(shipQty, 0)}
                    <span style={{ fontSize: 11, color: 'var(--color-muted)' }} title={PKG_LABEL[ship.pkg]}> {PKG_ICON[ship.pkg]}</span>
                    {mark?.closed
                        ? <span style={{ fontSize: 11 }} title="Приёмка закрыта — склад не принимает"> ⛔</span>
                        : <>
                            {mark?.monoOnly && <span style={{ fontSize: 11 }} title={mark.noLimit ? 'Только моно, и лимита нет — сдаётся ТОЛЬКО предзаявкой' : 'Короб не принимается — едет моно-паллетой'}> 📐</span>}
                            {mark?.noLimit && <span style={{ fontSize: 11 }} title="Нет лимита приёмки — нужна предзаявка"> ⌛</span>}
                        </>}
                </div>
            ) : (
                <div style={{ color: 'var(--color-dim)', lineHeight: 1.3 }}>·</div>
            )}
            {stock > 0 && (
                <div style={{ fontSize: 10, color: 'var(--color-accent)', whiteSpace: 'nowrap', lineHeight: 1.4 }} title="Остаток на Wildberries">
                    🏬 {formatNumber(stock, 0)}
                </div>
            )}
            {asm > 0 && (
                <div style={{ fontSize: 10, color: 'var(--color-success)', whiteSpace: 'nowrap', lineHeight: 1.4 }} title="В сборке на ФФ (ещё не отправлено)">
                    🔧 {formatNumber(asm, 0)}
                </div>
            )}
            {transit > 0 && (
                <div style={{ fontSize: 10, color: 'var(--color-success)', whiteSpace: 'nowrap', lineHeight: 1.4 }} title="В пути на склад WB">
                    🚚 {formatNumber(transit, 0)}
                </div>
            )}
        </td>
    );
}
