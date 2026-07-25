'use client';
/**
 * Первичная сверка склада продавца («захват» склада).
 *
 * Зачем. Трансляция остатков ДЕЛЬТОВАЯ: в WB уезжает только то, что изменилось
 * с прошлой отправки. Позиция, которую мы считаем в ноль и НИКОГДА не отправляли,
 * из дельты выпадает вовсе — слать «ноль впервые» бессмысленно. Но если продавец
 * до подключения проставил остатки в кабинете руками («Управление остатками»),
 * они там и останутся: WB продолжит их продавать, а на нашем привязанном складе
 * товара может не быть. Узнаёт об этом продавец по отменённым заказам.
 *
 * Поэтому перед первым включением трансляции мы спрашиваем у WB ФАКТИЧЕСКИЕ
 * остатки по всей номенклатуре проекта (GET /fbs/stock/reconcile — чтение,
 * в safe-режиме тоже работает) и показываем, чем мы не управляем. Обнуление
 * (POST /fbs/stock/reconcile) — это уже запись в кабинет, её гейтит режим контура.
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { exportToExcel, formatNumber } from '@/lib/utils';
import type { FbsActionResult, FbsReconcileOut, FbsReconcileRow, FbsStockRow } from '@/types/api';
import { num } from './fbsShared';
import { MiniKpi } from './stockColumns';

// ─── Словарь причин ─────────────────────────────────────────────────────────

/**
 * Почему позиция висит в кабинете, а мы её не отдаём.
 *
 * Ключи — ТОЧНЫЙ список кодов из `stock_service.RECONCILE_REASONS` (бэкенд
 * отдаёт коды, человеческий текст живёт только здесь). Расхождение словаря
 * с бэкендом невидимо в рантайме — оно молча уезжает в фолбэк, поэтому
 * контракт закреплён тестами с обеих сторон. Неизвестный ключ всё равно
 * показываем как есть: новая причина не должна превращаться в пустую ячейку.
 */
export const RECONCILE_REASON_LABEL: Record<string, string> = {
    not_linked: 'склад не привязан',
    override_zero: 'не отдаём: ручное кол-во 0',
    zero: 'нулевой остаток',
    no_stock: 'нет на привязанном складе',
    wb_zero: 'в кабинете 0 — дельта не догонит',
};

export function reconcileReasonLabel(reason: string | null | undefined): string {
    if (!reason) return '—';
    return RECONCILE_REASON_LABEL[reason] ?? reason;
}

// ─── Чистые помощники (покрыты тестами) ─────────────────────────────────────

/**
 * Позиции, которыми мы не управляем — ровно их и обнуляет кнопка.
 * `mismatch` не трогаем: там мы товар отдаём, разошлась только цифра —
 * её выправит обычная трансляция, а обнуление сняло бы товар с продажи.
 */
export function unmanagedRows(res: FbsReconcileOut | null): FbsReconcileRow[] {
    if (!res) return [];
    return res.rows.filter(r => r.kind === 'unmanaged');
}

/**
 * Позиции, у которых разошлась цифра. Обнулять их нельзя — товар живой, — но
 * и сам пуш их не догонит: он сравнивает расчёт с тем, что мы отправили в
 * прошлый раз, и при `qty_sent == расчёт` молчит. Лечит их выравнивание базы
 * дельты по факту кабинета (то же действие, POST /fbs/stock/reconcile).
 */
export function mismatchRows(res: FbsReconcileOut | null): FbsReconcileRow[] {
    if (!res) return [];
    return res.rows.filter(r => r.kind === 'mismatch');
}

/** Сколько штук висит в кабинете по этим строкам (для подтверждения). */
export function unmanagedUnits(rows: readonly FbsReconcileRow[]): number {
    return rows.reduce((acc, r) => acc + num(r.wb_amount), 0);
}

/**
 * Почему кнопку «Обнулить их в WB» жать нельзя — пустая строка означает «можно».
 *
 * Каждая причина зеркалит отказ бэкенда, чтобы пользователь узнавал о ней
 * до необратимого действия, а не из красного тоста:
 *  • нет привязок — наш расчёт пуст, «неуправляем» весь каталог кабинета,
 *    и обнуление снесло бы все продажи по FBS (бэкенд отдаст 422);
 *  • склад в режиме «Наблюдение» — в кабинет не пишем НИКОГДА, а обнуление
 *    это запись: она сняла бы с продажи товар, остатки которого продавец ведёт
 *    руками (бэкенд отдаст 409);
 *  • склад в обработке у WB — PUT вернёт 409, и каждый 4xx стоит 10 запросов
 *    бакета (тот же гард гасит кнопку «Передать остатки», бэкенд отдаст 409);
 *  • режим контура не разрешает запись в WB.
 */
export function zeroBlockReason(opts: {
    writeEnabled: boolean;
    writeHint: string;
    isProcessing?: boolean;
    noLinks?: boolean;
    /** Склад в режиме «Транслировать остатки». `false`/не передан — наблюдение. */
    translating?: boolean;
}): string {
    if (opts.noLinks) {
        return 'Склад продавца не привязан ни к одному нашему складу — сначала привяжите склад: '
            + 'иначе обнулится весь каталог кабинета';
    }
    if (opts.translating === false) {
        return 'Склад в режиме «Наблюдение» — в кабинет ничего не пишем. '
            + 'Переключите режим на вкладке «Склады», если хотите обнулить эти позиции';
    }
    if (opts.isProcessing) return 'Склад WB в обработке — остатки не принимаются';
    if (!opts.writeEnabled) return opts.writeHint;
    return '';
}

/**
 * Текст подтверждения перед обнулением. Число позиций и штук обязательны:
 * без них пользователь соглашается вслепую, а операция снимает товар с продажи.
 */
export function confirmZeroText(positions: number, units: number, warehouseName: string): string {
    return (
        `Обнулить в кабинете WB ${formatNumber(positions, 0)} позиций `
        + `на ${formatNumber(units, 0)} шт (склад «${warehouseName}»)?\n\n`
        + 'Эти позиции перестанут продаваться по FBS с этого склада. '
        + 'Обратно их вернёт обычная трансляция, как только на привязанном складе появится остаток.'
    );
}

/**
 * Склад ещё ни разу не транслировался из системы.
 *
 * Признак — отсутствие `qty_sent` во ВСЕХ строках превью: состояние остатка
 * (`wb_fbs_stock_states`) пишется только тем, что мы реально отправляли.
 * Пустое превью тоже считаем «не транслировался»: предупреждение лишним
 * не будет, а молчание стоит отменённых заказов.
 */
export function isFirstTimeWarehouse(rows: readonly FbsStockRow[]): boolean {
    return !rows.some(r => r.qty_sent != null);
}

/** Заголовок результата сверки — одной фразой, как просит владелец. */
export function reconcileHeadline(res: FbsReconcileOut): string {
    if (res.unmanaged_positions === 0) {
        return res.mismatch_positions > 0
            ? `Неуправляемых позиций нет, но у ${formatNumber(res.mismatch_positions, 0)} расходится количество`
            : 'Расхождений нет';
    }
    return (
        `В кабинете висит ${formatNumber(res.unmanaged_positions, 0)} позиций `
        + `на ${formatNumber(res.unmanaged_units, 0)} шт, которыми мы не управляем`
    );
}

// ─── Панель сверки ──────────────────────────────────────────────────────────

/**
 * Сколько строк таблицы реально рисуем. Потолок сверки на бэкенде — 20 000
 * строк номенклатуры, и в сценарии «захвата» расходится почти весь ассортимент:
 * десятки тысяч `<tr>` без виртуализации вешают вкладку на секунды, причём
 * сверка стартует сама (`autoRun`) — фриз без действия пользователя.
 * Строки отсортированы по опасности (больше висит в кабинете — выше), полный
 * список всегда доступен кнопкой «В Excel».
 */
const RENDER_LIMIT = 300;

interface PanelProps {
    wbWarehouseId: number;
    warehouseName: string;
    /** Режим контура разрешает запись в WB — обнуление это ЗАПИСЬ. */
    writeEnabled: boolean;
    writeHint: string;
    /** Склад WB в обработке — остатки он не примет, обнуление тоже (бэкенд отдаст 409). */
    isProcessing?: boolean;
    /**
     * У склада продавца нет ни одной привязки: наш расчёт пуст, и «неуправляем»
     * тут весь каталог кабинета. Обнуление в этом состоянии сносит все продажи
     * по FBS — бэкенд его запрещает, кнопку гасим здесь же.
     */
    noLinks?: boolean;
    /**
     * Склад в режиме «Транслировать остатки». `false` — наблюдение: ЧИТАТЬ сверку
     * можно, а обнулять нельзя (бэкенд отдаст 409) — в кабинет в этом режиме
     * не пишем вовсе.
     */
    translating?: boolean;
    /** Запускать сверку сразу при монтировании (модалка «Сверить с WB»). */
    autoRun?: boolean;
    onToast?: (msg: string) => void;
    /** После обнуления — обновить превью снаружи. */
    onApplied?: () => void;
}

export function ReconcilePanel({
    wbWarehouseId, warehouseName, writeEnabled, writeHint, isProcessing = false, noLinks = false,
    translating, autoRun = false, onToast, onApplied,
}: PanelProps) {
    const [data, setData] = useState<FbsReconcileOut | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [applying, setApplying] = useState(false);
    const [applyError, setApplyError] = useState('');

    const run = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        setApplyError('');
        try {
            const res = await api.getFbsStockReconcile(wbWarehouseId);
            if (signal?.aborted) return;
            setData(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка сверки с WB');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [wbWarehouseId]);

    useEffect(() => {
        if (!autoRun) return;
        const controller = new AbortController();
        run(controller.signal);
        return () => controller.abort();
    }, [autoRun, run]);

    const unmanaged = unmanagedRows(data);
    const mismatch = mismatchRows(data);
    const zeroBlockHint = zeroBlockReason({ writeEnabled, writeHint, isProcessing, noLinks, translating });

    /**
     * POST /fbs/stock/reconcile. `chrtIds` — ИМЕННО те строки, которые
     * пользователь видел на экране: пустой список бэкенд трактует как «все
     * unmanaged на момент запроса», а картина могла измениться между сверкой
     * и нажатием кнопки.
     */
    const submitApply = async (
        chrtIds: number[],
        okToast: (res: FbsActionResult) => string,
        failFallback: string,
    ) => {
        setApplying(true);
        setApplyError('');
        try {
            const res = await api.applyFbsStockReconcile({ wb_warehouse_id: wbWarehouseId, chrt_ids: chrtIds });
            // Ответ синхронный и честный: `ok: false` — прогон завершился PARTIAL
            // или ERROR (WB ответил 204, но остаток не обнулил; PUT упал на 4xx).
            // Зелёный тост на таком исходе врал бы, что склад «захвачен», а товар
            // продолжал бы продаваться. `message` — единственное место, где до
            // пользователя доезжает текст ошибки WB и число расхождений верификации.
            const failed = res?.ok === false;
            if (!failed) onToast?.(okToast(res));
            onApplied?.();
            await run();
            // Строго ПОСЛЕ пересверки: `run` чистит `applyError`, иначе сообщение
            // об ошибке мигнуло бы и исчезло.
            if (failed) setApplyError(res.message || failFallback);
        } catch (e: unknown) {
            setApplyError(e instanceof Error ? e.message : failFallback);
        } finally {
            setApplying(false);
        }
    };

    const handleZero = async () => {
        if (unmanaged.length === 0 || zeroBlockHint) return;
        const units = unmanagedUnits(unmanaged);
        if (!confirm(confirmZeroText(unmanaged.length, units, warehouseName))) return;
        await submitApply(
            unmanaged.map(r => r.chrt_id),
            res => `Обнулено позиций в WB: ${formatNumber(
                typeof res?.affected === 'number' ? res.affected : unmanaged.length, 0,
            )}`,
            'Обнуление прошло не полностью — см. лог трансляции',
        );
    };

    /**
     * Выровнять базу дельты по факту кабинета для `mismatch`-строк.
     *
     * В WB ничего не пишет — правится наш `qty_sent`, после чего ближайшая
     * обычная трансляция отправит верное число. Шлём chrtId ТОЛЬКО расходящихся
     * строк: пустой список бэкенд понял бы как «обнулить все неуправляемые».
     */
    const handleAlign = async () => {
        if (mismatch.length === 0 || zeroBlockHint) return;
        await submitApply(
            mismatch.map(r => r.chrt_id),
            res => res?.message || 'База дельты выровнена по кабинету',
            'Не удалось выровнять цифры — см. лог трансляции',
        );
    };

    const handleExcel = () => {
        if (!data) return;
        exportToExcel(
            data.rows,
            `FBS_сверка_${warehouseName || wbWarehouseId}`,
            [
                { key: 'article_seller', label: 'Артикул' },
                { key: 'barcode', label: 'Баркод' },
                { key: 'nm_id', label: 'nmID' },
                { key: 'chrt_id', label: 'chrtID' },
                { key: 'wb_amount', label: 'В кабинете WB', exportValue: (r: FbsReconcileRow) => num(r.wb_amount) },
                { key: 'our_amount', label: 'Отдаём мы', exportValue: (r: FbsReconcileRow) => num(r.our_amount) },
                {
                    key: 'kind', label: 'Тип',
                    exportValue: (r: FbsReconcileRow) => (r.kind === 'unmanaged' ? 'не управляем' : 'расходится'),
                },
                { key: 'reason', label: 'Причина', exportValue: (r: FbsReconcileRow) => reconcileReasonLabel(r.reason) },
            ],
        );
    };

    // ─── Состояния ──────────────────────────────────────────────────────────

    if (loading) {
        return (
            <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                Спрашиваем у WB фактические остатки по всей номенклатуре — это может занять до минуты...
            </div>
        );
    }

    if (error) {
        return (
            <div style={{ padding: 16, fontSize: 13, color: 'var(--color-danger)' }}>
                {error}
                <button className="btn btn-sm" style={{ marginLeft: 12 }} onClick={() => run()}>
                    Повторить
                </button>
            </div>
        );
    }

    if (!data) {
        return (
            <div style={{ padding: 16, fontSize: 13, color: 'var(--color-text-muted)' }}>
                <div style={{ marginBottom: 12, lineHeight: 1.5 }}>
                    Сверка спросит у WB фактические остатки по всей номенклатуре проекта и покажет позиции,
                    которые продаются в кабинете помимо нашей трансляции. Кабинет при этом не меняется.
                </div>
                <button className="btn btn-primary btn-sm" onClick={() => run()}>
                    🔎 Сверить с WB
                </button>
            </div>
        );
    }

    const clean = data.unmanaged_positions === 0 && data.mismatch_positions === 0;

    return (
        <>
            <div style={{
                display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                gap: 12, marginBottom: 12,
            }}>
                <MiniKpi label="Проверено позиций" value={data.checked} />
                <MiniKpi label="Не управляем, поз." value={data.unmanaged_positions} danger={data.unmanaged_positions > 0} />
                <MiniKpi label="Не управляем, шт" value={data.unmanaged_units} danger={data.unmanaged_units > 0} />
                <MiniKpi label="Разошлась цифра" value={data.mismatch_positions} />
            </div>

            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
                {clean ? '✅ ' : '⚠️ '}{reconcileHeadline(data)}
            </div>

            {data.rows_no_chrt > 0 && (
                <div style={{ fontSize: 12, color: 'var(--color-warning)', marginBottom: 8 }}>
                    Позиций без chrtId: <strong>{formatNumber(data.rows_no_chrt, 0)}</strong> — их состояние
                    в кабинете проверить нечем, ключ трансляции у WB именно chrtId.
                </div>
            )}

            {applyError && (
                <div style={{ fontSize: 13, color: 'var(--color-danger)', marginBottom: 8 }}>{applyError}</div>
            )}

            {clean ? (
                <div style={{
                    padding: 32, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13, lineHeight: 1.5,
                }}>
                    <div style={{ fontSize: 32, marginBottom: 8 }}>🎯</div>
                    В кабинете WB нет остатков помимо тех, что отдаёт наш расчёт.
                    Проверено позиций: {formatNumber(data.checked, 0)}.
                </div>
            ) : (
                <div style={{ maxHeight: '46vh', overflow: 'auto', marginBottom: 12 }}>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Товар</th>
                                <th>Баркод</th>
                                <th style={{ textAlign: 'right' }}>chrtID</th>
                                <th style={{ textAlign: 'right' }}>В кабинете WB</th>
                                <th style={{ textAlign: 'right' }}>Отдаём мы</th>
                                <th>Причина</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.rows.slice(0, RENDER_LIMIT).map(r => (
                                <tr key={r.chrt_id}>
                                    <td>
                                        <div style={{ fontWeight: 500 }}>{r.article_seller || '—'}</div>
                                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                            {r.nm_id ? `nm ${r.nm_id}` : ''}
                                            {r.kind === 'unmanaged'
                                                ? <span className="badge badge-danger" style={{ marginLeft: 6, fontSize: 10 }}>
                                                    не управляем
                                                </span>
                                                : <span className="badge badge-warning" style={{ marginLeft: 6, fontSize: 10 }}>
                                                    расходится
                                                </span>}
                                        </div>
                                    </td>
                                    <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.barcode || '—'}</td>
                                    <td style={{ textAlign: 'right', fontFamily: 'monospace', fontSize: 12 }}>
                                        {r.chrt_id}
                                    </td>
                                    <td style={{
                                        textAlign: 'right', fontWeight: 700,
                                        color: r.kind === 'unmanaged' ? 'var(--color-danger)' : undefined,
                                    }}>
                                        {formatNumber(num(r.wb_amount), 0)}
                                    </td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(num(r.our_amount), 0)}</td>
                                    <td style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                        {reconcileReasonLabel(r.reason)}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {data.rows.length > RENDER_LIMIT && (
                        <div style={{ padding: 8, fontSize: 12, color: 'var(--color-text-muted)' }}>
                            Показаны первые {formatNumber(RENDER_LIMIT, 0)} строк из{' '}
                            {formatNumber(data.rows.length, 0)} (самые крупные остатки — сверху).
                            Полный список — кнопкой «В Excel». Обнуление работает по ВСЕМ неуправляемым позициям,
                            а не только по показанным.
                        </div>
                    )}
                </div>
            )}

            {data.mismatch_positions > 0 && (
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8, lineHeight: 1.5 }}>
                    У {formatNumber(data.mismatch_positions, 0)} поз. цифра расходится с кабинетом. Сам дельта-пуш
                    их не догонит: он сравнивает расчёт с тем, что мы отправляли в прошлый раз. Обнуление
                    выровняет базу дельты по факту кабинета — ближайшая трансляция отправит верное число
                    (то же делает чекбокс «Слать всё (force)» на вкладке «Остатки»).
                </div>
            )}

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <button className="btn btn-secondary btn-sm" onClick={() => run()} disabled={applying}>
                    🔄 Пересверить
                </button>
                <button className="btn btn-secondary btn-sm" onClick={handleExcel} disabled={data.rows.length === 0}>
                    📊 В Excel
                </button>
                <div style={{ flex: 1 }} />
                {unmanaged.length > 0 && (
                    <>
                        {zeroBlockHint && (
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{zeroBlockHint}</span>
                        )}
                        <button
                            className="btn btn-danger btn-sm"
                            onClick={handleZero}
                            disabled={!!zeroBlockHint || applying}
                            title={zeroBlockHint || 'Отправить в WB amount = 0 по этим позициям'}
                        >
                            {applying ? 'Обнуление...' : `Обнулить их в WB (${formatNumber(unmanaged.length, 0)})`}
                        </button>
                    </>
                )}
            </div>
        </>
    );
}

// ─── Модалка «Сверить с WB» (вкладка «Остатки») ─────────────────────────────

interface ReconcileModalProps {
    wbWarehouseId: number;
    warehouseName: string;
    writeEnabled: boolean;
    writeHint: string;
    /** Склад WB в обработке — обнуление недоступно так же, как и пуш. */
    isProcessing?: boolean;
    /** Нет привязок — обнулять нельзя: «неуправляем» весь каталог кабинета. */
    noLinks?: boolean;
    /** Склад транслирует остатки. В наблюдении сверку читаем, но не обнуляем. */
    translating?: boolean;
    onToast?: (msg: string) => void;
    onApplied?: () => void;
    onClose: () => void;
}

export function ReconcileModal({
    wbWarehouseId, warehouseName, writeEnabled, writeHint, isProcessing, noLinks, translating,
    onToast, onApplied, onClose,
}: ReconcileModalProps) {
    return (
        <div className="modal-overlay" onClick={onClose}>
            <div
                className="modal-card modal-card-solid"
                onClick={e => e.stopPropagation()}
                style={{ maxWidth: 940, width: '100%' }}
            >
                <h2 className="modal-title">Сверка с кабинетом WB — «{warehouseName}»</h2>
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 16, lineHeight: 1.5 }}>
                    Трансляция дельтовая: она шлёт только изменившееся. Позиции, которые мы не отдаём
                    и никогда не отправляли, остаются в кабинете как есть — и продолжают продаваться.
                </div>

                <ReconcilePanel
                    wbWarehouseId={wbWarehouseId}
                    warehouseName={warehouseName}
                    writeEnabled={writeEnabled}
                    writeHint={writeHint}
                    isProcessing={isProcessing}
                    noLinks={noLinks}
                    translating={translating}
                    autoRun
                    onToast={onToast}
                    onApplied={onApplied}
                />

                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose}>Закрыть</button>
                </div>
            </div>
        </div>
    );
}

// ─── Модалка «Перед включением трансляции» ──────────────────────────────────

interface EnableModalProps {
    wbWarehouseId: number;
    warehouseName: string;
    /**
     * Склад ещё ни разу не транслировался. null — неизвестно (превью не
     * загружено): предупреждаем всё равно, но мягче.
     */
    firstTime: boolean | null;
    writeEnabled: boolean;
    writeHint: string;
    /** Склад WB в обработке — обнуление в панели сверки недоступно. */
    isProcessing?: boolean;
    /** Нет привязок — обнулять нельзя: «неуправляем» весь каталог кабинета. */
    noLinks?: boolean;
    /** Склад в режиме трансляции (модалка открывается только из него — тумблер выключен). */
    translating?: boolean;
    /** Идёт сохранение настройки — кнопка подтверждения гасится. */
    busy?: boolean;
    /**
     * Ошибка включения трансляции. Рисуется ВНУТРИ модалки, рядом с кнопкой
     * подтверждения: карточка под `modal-overlay` не видна, и пользователь
     * читал бы провалившееся сохранение как «кнопка не работает».
     */
    error?: string;
    onToast?: (msg: string) => void;
    onConfirm: () => void;
    onClose: () => void;
}

/**
 * Предупреждение ПЕРЕД включением трансляции.
 *
 * Включение не блокируется — блокировать нечего, настройка наша. Но продавец
 * обязан узнать про руками проставленные остатки ДО того, как WB начнёт
 * продавать то, чего на складе нет.
 */
export function EnableTranslationModal({
    wbWarehouseId, warehouseName, firstTime, writeEnabled, writeHint, isProcessing, noLinks, translating,
    busy = false, error = '', onToast, onConfirm, onClose,
}: EnableModalProps) {
    return (
        <div className="modal-overlay" onClick={onClose}>
            <div
                className="modal-card modal-card-solid"
                onClick={e => e.stopPropagation()}
                style={{ maxWidth: 940, width: '100%' }}
            >
                <h2 className="modal-title">Перед включением трансляции — «{warehouseName}»</h2>

                <div style={{
                    padding: 12, marginBottom: 16, borderRadius: 12,
                    border: '1px solid var(--color-border)', borderLeft: '4px solid var(--color-warning)',
                    fontSize: 13, lineHeight: 1.6,
                }}>
                    {firstTime === false ? (
                        <div style={{ marginBottom: 8 }}>
                            Этот склад уже транслировался из системы, но проверить кабинет всё равно стоит:
                            позиции, которые мы ни разу не отправляли, в дельту не попадают.
                        </div>
                    ) : (
                        <div style={{ marginBottom: 8 }}>
                            <strong>На этом складе в кабинете WB уже могут быть проставлены остатки — они
                            не обнулятся сами.</strong>{firstTime === null
                                ? ' Проверить по превью, транслировался ли склад раньше, сейчас нельзя.'
                                : ' Мы ещё ни разу не транслировали остатки на этот склад.'}
                        </div>
                    )}
                    <div>
                        Трансляция дельтовая: она отправляет только то, что изменилось с прошлого раза.
                        Позицию, которую мы считаем в ноль и никогда не отправляли, она не тронет вовсе —
                        остаток, проставленный в кабинете руками, останется и продолжит продаваться.
                        Товара на нашем складе при этом может не быть, и узнаете вы об этом по отменённым заказам.
                    </div>
                </div>

                <ReconcilePanel
                    wbWarehouseId={wbWarehouseId}
                    warehouseName={warehouseName}
                    writeEnabled={writeEnabled}
                    writeHint={writeHint}
                    isProcessing={isProcessing}
                    noLinks={noLinks}
                    translating={translating}
                    onToast={onToast}
                />

                {error && (
                    <div style={{ marginTop: 16, fontSize: 13, color: 'var(--color-danger)' }}>{error}</div>
                )}

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16, flexWrap: 'wrap' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={busy}>Отмена</button>
                    <button className="btn btn-primary" onClick={onConfirm} disabled={busy}>
                        {busy ? 'Включение...' : 'Всё равно включить трансляцию'}
                    </button>
                </div>
            </div>
        </div>
    );
}
