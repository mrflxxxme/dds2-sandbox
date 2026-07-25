'use client';
/**
 * Лист подбора поставки — документ для сборщика.
 *
 * В кабинете WB у поставки есть кнопка «Лист подбора»: агрегат ПО ТОВАРУ
 * (что и сколько снять со склада), а не по заказам — сборщик ходит по складу
 * с ним, а не со списком из 47 заданий. Отсюда же печать (отдельным окном,
 * без интерфейсных элементов) и выгрузка в Excel.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { exportToExcel, formatDateTime, formatNumber } from '@/lib/utils';
import type { FbsPickList, FbsPickListRow, FbsSupply } from '@/types/api';
import { buildPickListPrintHtml, cargoCabinetLabel, num, printHtmlDocument } from './fbsShared';

interface Props {
    supply: FbsSupply;
    /** Имя склада WB из справочника — в ответе оно может быть пустым. */
    warehouseName?: string;
    onClose: () => void;
}

export default function PickListModal({ supply, warehouseName, onClose }: Props) {
    const [pick, setPick] = useState<FbsPickList | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actionError, setActionError] = useState('');

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getFbsPickList(supply.wb_supply_id);
            if (signal?.aborted) return;
            setPick(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки листа подбора');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [supply.wb_supply_id]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    /** Имя склада из справочника важнее пустого поля ответа — печать и Excel берут его же. */
    const doc = useMemo<FbsPickList | null>(() => {
        if (!pick) return null;
        return { ...pick, wb_warehouse_name: pick.wb_warehouse_name || warehouseName || null };
    }, [pick, warehouseName]);

    const rows = doc?.rows ?? [];

    const handlePrint = () => {
        if (!doc) return;
        setActionError('');
        if (!printHtmlDocument(buildPickListPrintHtml(doc))) {
            setActionError('Браузер заблокировал окно печати — разрешите всплывающие окна для этого сайта');
        }
    };

    const handleExcel = () => {
        if (!doc) return;
        exportToExcel(
            rows,
            `Лист_подбора_${doc.wb_supply_id}`,
            [
                { key: 'article', label: 'Артикул' },
                { key: 'subject', label: 'Предмет' },
                { key: 'brand', label: 'Бренд' },
                { key: 'barcode', label: 'Баркод' },
                { key: 'qty', label: 'Кол-во', exportValue: (r: FbsPickListRow) => r.qty },
                {
                    key: 'stock_available', label: 'Остаток на складе',
                    exportValue: (r: FbsPickListRow) => r.stock_available ?? null,
                },
                { key: 'nm_id', label: 'nmID' },
                { key: 'chrt_id', label: 'chrtID' },
                { key: 'amount', label: 'Сумма, ₽', exportValue: (r: FbsPickListRow) => num(r.amount) },
            ],
        );
    };

    const header: [string, string][] = doc ? [
        ['Склад продавца', doc.wb_warehouse_name || (doc.wb_warehouse_id != null ? `#${doc.wb_warehouse_id}` : '—')],
        ['Габарит', cargoCabinetLabel(doc.cargo_type)],
        ['Заказы', formatNumber(doc.orders_count, 0)],
        ['Создана', doc.created_at_wb ? formatDateTime(doc.created_at_wb) : '—'],
    ] : [];

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card" onClick={e => e.stopPropagation()}
                style={{ maxWidth: 940, width: '100%' }}>
                <h2 className="modal-title">
                    Лист подбора {supply.name || supply.wb_supply_id}
                </h2>
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    Что и сколько снять со склада: строки собраны по товару, а не по заданиям.
                </div>

                {doc && (
                    <div style={{
                        display: 'flex', flexWrap: 'wrap', gap: '4px 24px', marginBottom: 12, fontSize: 13,
                    }}>
                        {header.map(([k, v]) => (
                            <div key={k}>
                                <span style={{ color: 'var(--color-text-muted)' }}>{k}: </span>{v}
                            </div>
                        ))}
                    </div>
                )}

                {actionError && (
                    <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>{actionError}</div>
                )}

                {loading ? (
                    <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                        Загрузка листа подбора...
                    </div>
                ) : error ? (
                    <div style={{ padding: 24, color: 'var(--color-danger)', fontSize: 13 }}>
                        {error}
                        <button className="btn btn-sm" style={{ marginLeft: 12 }} onClick={() => load()}>
                            Повторить
                        </button>
                    </div>
                ) : rows.length === 0 ? (
                    <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                        📋 В поставке нет заданий — подбирать нечего.
                    </div>
                ) : (
                    <div style={{ maxHeight: '50vh', overflow: 'auto', marginBottom: 12 }}>
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th style={{ width: 36, textAlign: 'right' }}>#</th>
                                    <th>Артикул</th>
                                    <th>Предмет</th>
                                    <th>Баркод</th>
                                    <th style={{ textAlign: 'right' }}>Кол-во</th>
                                    <th style={{ textAlign: 'right' }}>Остаток на складе</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((r, i) => {
                                    const short = r.stock_available != null && r.stock_available < r.qty;
                                    return (
                                        <tr key={`${r.barcode ?? r.chrt_id ?? r.nomenclature_id ?? i}`}>
                                            <td style={{ textAlign: 'right', color: 'var(--color-text-dim)' }}>{i + 1}</td>
                                            <td>
                                                <div style={{ fontWeight: 500 }}>{r.article || '—'}</div>
                                                <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                                    {r.brand ? `${r.brand} · ` : ''}{r.nm_id ? `nm ${r.nm_id}` : ''}
                                                </div>
                                            </td>
                                            <td>{r.subject || '—'}</td>
                                            <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.barcode || '—'}</td>
                                            <td style={{ textAlign: 'right', fontWeight: 600 }}>
                                                {formatNumber(r.qty, 0)}
                                            </td>
                                            <td style={{
                                                textAlign: 'right',
                                                color: short ? 'var(--color-danger)' : undefined,
                                            }}
                                                title={short ? 'Остатка на привязанных складах меньше, чем нужно собрать' : undefined}>
                                                {r.stock_available == null ? '—' : formatNumber(r.stock_available, 0)}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                            <tfoot>
                                <tr>
                                    <td colSpan={4} style={{ fontWeight: 600 }}>
                                        Позиций: {formatNumber(doc?.positions_count ?? rows.length, 0)}
                                    </td>
                                    <td style={{ textAlign: 'right', fontWeight: 700 }}>
                                        {formatNumber(doc?.total_qty ?? 0, 0)}
                                    </td>
                                    <td />
                                </tr>
                            </tfoot>
                        </table>
                    </div>
                )}

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                    <button className="btn btn-secondary" onClick={onClose}>Закрыть</button>
                    <button className="btn btn-secondary" onClick={handleExcel} disabled={rows.length === 0}>
                        📊 В Excel
                    </button>
                    <button className="btn btn-primary" onClick={handlePrint} disabled={rows.length === 0}>
                        🖨️ Печать
                    </button>
                </div>
            </div>
        </div>
    );
}
