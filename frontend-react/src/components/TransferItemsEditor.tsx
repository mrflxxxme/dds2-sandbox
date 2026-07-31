'use client';
import React, { useMemo, useState } from 'react';
import { formatNumber } from '@/lib/utils';
import { mergeRowsByBarcode } from '@/lib/utils/transferRows';
import type { Nomenclature } from '@/types/api';

/**
 * Редактор состава перемещения — общий для СОЗДАНИЯ переезда и правки черновика.
 *
 * Компонент управляемый: строки живут у родителя, потому что оба экрана считают
 * по ним своё (создание — валидацию остатка и подпись кнопки, правка — payload
 * PUT). Внутри остаётся только то, что одинаково: поиск по номенклатуре,
 * вставка из Excel, автодобавление пустой строки, удаление.
 *
 * Строки хранятся СТРОКАМИ, а не числами: поле «12» в процессе набора проходит
 * через «1» и через пусто, и Number() на каждый keypress превращал бы пустое
 * поле в 0. Схлопывание дублей ШК и парсинг — один раз, на отправке
 * (mergeRowsByBarcode), у родителя.
 */

export interface TransferItemRow {
    barcode: string;
    quantity: string;
}

export const emptyTransferItemRow = (): TransferItemRow => ({ barcode: '', quantity: '' });

/** Строки состава по позициям документа + хвост пустых строк для ввода. */
export function transferItemRows(
    items: { barcode: string; quantity: number }[],
    tail = 2,
): TransferItemRow[] {
    return [
        ...items.map(it => ({ barcode: it.barcode, quantity: String(it.quantity) })),
        ...Array.from({ length: tail }, emptyTransferItemRow),
    ];
}

interface Props {
    rows: TransferItemRow[];
    onChange: (rows: TransferItemRow[]) => void;
    nomenclature: Nomenclature[];
    /**
     * Остаток склада-источника по ШК. Пусто (`undefined`) — колонку не рисуем:
     * лучше без неё, чем с честными нулями напротив каждой строки.
     */
    stockMap?: Record<string, number>;
    /** Заголовок колонки остатка: «НА СКЛАДЕ» / «В БРАКЕ». */
    stockLabel?: string;
    /** Брак — колонку остатка подсвечиваем warning-цветом (как на создании). */
    stockAccent?: boolean;
    /** Высота прокручиваемой области таблицы (для модалки). */
    maxHeight?: number;
    disabled?: boolean;
}

function useNomLookup(nomenclature: Nomenclature[]) {
    return useMemo(() => {
        const byBarcode = new Map<string, Nomenclature>();
        nomenclature.forEach(n => {
            if (n.barcode) byBarcode.set(n.barcode, n);
        });
        const resolve = (barcode: string): Nomenclature | undefined => byBarcode.get(barcode);
        const label = (n: Nomenclature): string => n.article_seller || n.subject || n.name || `nmId: ${n.article_wb}`;
        return { resolve, label };
    }, [nomenclature]);
}

export default function TransferItemsEditor({
    rows,
    onChange,
    nomenclature,
    stockMap,
    stockLabel = 'НА СКЛАДЕ',
    stockAccent = false,
    maxHeight,
    disabled = false,
}: Props) {
    const [search, setSearch] = useState('');
    const nom = useNomLookup(nomenclature);

    const filledRows = rows.filter(r => r.barcode.trim() && r.quantity.trim());
    const totalQty = filledRows.reduce((s, r) => s + (parseInt(r.quantity, 10) || 0), 0);

    const updateRow = (idx: number, field: keyof TransferItemRow, value: string) => {
        const next = rows.map((r, i) => (i === idx ? { ...r, [field]: value } : r));
        // Печатаем в последней строке — сразу подставляем следующую пустую,
        // чтобы список не упирался в конец и не требовал кнопки «добавить».
        if (idx === rows.length - 1 && value.trim()) next.push(emptyTransferItemRow());
        onChange(next);
    };

    const removeRow = (idx: number) => {
        const next = rows.filter((_, i) => i !== idx);
        // Не оставляем таблицу совсем без строк — вводить было бы некуда.
        onChange(next.length ? next : [emptyTransferItemRow()]);
    };

    const handlePaste = (e: React.ClipboardEvent) => {
        if (disabled) return;
        const text = e.clipboardData.getData('text/plain');
        if (!text.includes('\t') && !text.includes('\n')) return;
        e.preventDefault();
        const lines = text.trim().split('\n').map(l => l.split('\t'));
        const parsed: TransferItemRow[] = [];
        for (const cols of lines) {
            if (cols.length < 2) continue;
            const barcode = cols[0].trim();
            const qty = cols[1].trim().replace(',', '.').replace(/[^\d]/g, '');
            if (barcode && qty) parsed.push({ barcode, quantity: qty });
        }
        // Схлопываем дубли ШК из склейки нескольких источников: одна строка на ШК
        // с суммарным количеством — иначе остаток списывается многократно и send падает.
        const newRows = mergeRowsByBarcode(parsed).map(m => ({ barcode: m.barcode, quantity: String(m.quantity) }));
        if (newRows.length > 0) onChange([...newRows, emptyTransferItemRow(), emptyTransferItemRow()]);
    };

    const addFromSearch = (n: Nomenclature) => {
        if (!n.barcode) return;
        setSearch('');
        // Товар уже в составе — не плодим вторую строку с тем же ШК.
        if (rows.some(r => r.barcode.trim() === n.barcode)) return;
        const firstEmpty = rows.findIndex(r => !r.barcode.trim());
        if (firstEmpty >= 0) {
            onChange(rows.map((r, i) => (i === firstEmpty ? { barcode: n.barcode!, quantity: '' } : r)));
        } else {
            onChange([...rows, { barcode: n.barcode!, quantity: '' }, emptyTransferItemRow()]);
        }
    };

    const filteredNom = search.trim()
        ? nomenclature.filter(n => {
            const q = search.toLowerCase();
            return (n.barcode && n.barcode.includes(q))
                || (n.article_seller && n.article_seller.toLowerCase().includes(q))
                || (n.name && n.name.toLowerCase().includes(q))
                || (n.subject && n.subject.toLowerCase().includes(q));
        }).slice(0, 10)
        : [];

    return (
        <>
            {/* Toolbar: поиск + счётчик */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
                <div style={{ flex: 1, position: 'relative', minWidth: 200 }}>
                    <input
                        className="form-input"
                        placeholder="Поиск по товарам... (артикул, баркод, название)"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        disabled={disabled}
                        style={{ width: '100%' }}
                    />
                    {filteredNom.length > 0 && (
                        <div style={{
                            position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 100,
                            background: 'var(--color-bg)', border: '1px solid var(--color-border)',
                            borderRadius: 8, boxShadow: '0 4px 16px rgba(0,0,0,0.1)', maxHeight: 300, overflow: 'auto',
                        }}>
                            {filteredNom.map(n => (
                                <div
                                    key={n.id}
                                    onClick={() => addFromSearch(n)}
                                    style={{
                                        padding: '10px 14px', cursor: 'pointer', fontSize: 13,
                                        borderBottom: '1px solid var(--color-border)',
                                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                    }}
                                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-hover)')}
                                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                                >
                                    <span style={{ fontWeight: 500 }}>{nom.label(n)}</span>
                                    <span style={{ color: 'var(--color-text-muted)', fontSize: 12, marginLeft: 12 }}>{n.barcode}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                <span style={{ fontSize: 13, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                    {formatNumber(filledRows.length, 0)} позиций, {formatNumber(totalQty)} шт.
                </span>
            </div>

            {/* Таблица состава */}
            <div
                className="glass-card"
                style={{ overflow: 'auto', padding: 0, maxHeight }}
                onPaste={handlePaste}
            >
                {/* TODO: migrate to TanStackDataTable — has inline form inputs (barcode input, quantity input, paste handler) */}
                <table className="data-table" style={{ marginBottom: 0 }}>
                    <thead>
                        <tr>
                            <th style={{ width: 40, textAlign: 'center' }}>#</th>
                            <th style={{ minWidth: 220 }}>ТОВАР</th>
                            <th style={{ minWidth: 160 }}>ШК (БАРКОД)</th>
                            {stockMap && <th style={{ width: 100, textAlign: 'right' }}>{stockLabel}</th>}
                            <th style={{ width: 120, textAlign: 'right' }}>КОЛИЧЕСТВО</th>
                            <th style={{ width: 50 }}></th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row, i) => {
                            const barcode = row.barcode.trim();
                            const n = barcode ? nom.resolve(barcode) : undefined;
                            const unknown = !!barcode && !n;
                            return (
                                <tr key={i} style={{ background: unknown ? 'rgba(239,68,68,0.04)' : undefined }}>
                                    <td style={{ textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 12 }}>
                                        {barcode ? i + 1 : ''}
                                    </td>
                                    <td style={{ fontSize: 13, color: n ? 'var(--color-text)' : unknown ? '#ef4444' : 'var(--color-text-muted)' }}>
                                        {n ? nom.label(n) : (unknown ? '(не найден)' : '—')}
                                    </td>
                                    <td>
                                        <input
                                            value={row.barcode}
                                            onChange={e => updateRow(i, 'barcode', e.target.value)}
                                            placeholder="Введите баркод..."
                                            disabled={disabled}
                                            style={{
                                                width: '100%', background: 'transparent',
                                                border: 'none', padding: '8px 4px', fontSize: 13,
                                                color: unknown ? '#ef4444' : 'var(--color-text)',
                                                outline: 'none',
                                            }}
                                        />
                                    </td>
                                    {stockMap && (
                                        <td style={{
                                            textAlign: 'right', fontSize: 13, fontWeight: 600,
                                            color: stockAccent ? 'var(--color-warning)' : 'var(--color-text)',
                                        }}>
                                            {barcode ? formatNumber(stockMap[barcode] || 0) : ''}
                                        </td>
                                    )}
                                    <td>
                                        <input
                                            type="number"
                                            value={row.quantity}
                                            onChange={e => updateRow(i, 'quantity', e.target.value)}
                                            placeholder="0"
                                            disabled={disabled}
                                            style={{
                                                width: '100%', background: 'transparent',
                                                border: 'none', padding: '8px 4px', fontSize: 13,
                                                textAlign: 'right', color: 'var(--color-text)',
                                                outline: 'none',
                                            }}
                                        />
                                    </td>
                                    <td>
                                        {barcode && (
                                            <button
                                                type="button"
                                                onClick={() => removeRow(i)}
                                                disabled={disabled}
                                                style={{
                                                    background: 'none', border: 'none', cursor: 'pointer',
                                                    color: 'var(--color-text-muted)', fontSize: 16, padding: '4px 6px',
                                                }}
                                                title="Удалить строку"
                                            >{'×'}</button>
                                        )}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 8 }}>
                Вставьте данные из Excel/Google Sheets (Ctrl+V): Баркод &#x21B9; Кол-во
            </div>
        </>
    );
}
