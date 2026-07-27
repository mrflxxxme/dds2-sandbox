'use client';
/**
 * Колонки вкладки «Остатки»: расшифровка формулы, остаток на FBO, инлайн-поле
 * «Кол-во» (потоварная замена количества), сводка по разрезу и лог трансляции.
 * Вынесено из StockTab, чтобы вкладка осталась читаемой.
 */
import { useEffect, useRef, useState } from 'react';
import { formatDateTime, formatNumber } from '@/lib/utils';
import { wbProductUrl } from '@/lib/wbMedia';
import type { Column } from '@/components/DataTable';
import WbThumb from '@/components/WbThumb';
import type { FbsStockRow, FbsWarehouse } from '@/types/api';
import {
    OVERRIDE_HINT,
    PUSH_STATUS_BADGE,
    PUSH_STATUS_LABEL,
    PUSH_TRIGGER_LABEL,
    blockedReasonLabel,
    num,
} from './fbsShared';

// ─── Разрезы ────────────────────────────────────────────────────────────────

/** Разрез группировки таблицы (данные приходят прямо в строке превью). */
export type GroupBy = '' | 'brand' | 'subject' | 'subcategory';

export const GROUP_LABEL: Record<Exclude<GroupBy, ''>, string> = {
    brand: 'Бренд',
    subject: 'Предмет',
    subcategory: 'Под-категория',
};

/** Ключ «разрез не заполнен» — такие позиции сводятся в отдельную строку. */
export const GROUP_NONE_KEY = '__none__';

export interface GroupAgg {
    key: string;
    label: string;
    subcategory_id: number | null;
    brand: string | null;
    subject: string | null;
    positions: number;
    units_computed: number;
    units_available: number;
    /** Сколько позиций группы с ручным количеством — видно, где мы вмешались. */
    overridden: number;
}

// ─── Расхождения с кабинетом WB ─────────────────────────────────────────────

/**
 * Чем строка расходится с кабинетом WB — два состояния, которые стоят денег:
 *
 *  `over`    — в кабинете остаток БОЛЬШЕ того, что мы отдадим. WB продолжает
 *              продавать то, чего на складе нет: заказ придёт, собрать будет
 *              нечем, дальше отмена и штраф.
 *  `missing` — в кабинете НОЛЬ, а свободный остаток у нас есть: позиция просто
 *              не продаётся по FBS, и это не видно ниоткуда, кроме этой таблицы.
 *
 * `null` — либо всё сходится, либо судить не о чем: `qty_wb == null` означает
 * «кабинет не прочитан или у позиции нет chrtId», и молча считать это нулём
 * нельзя — весь каталог загорелся бы «в WB ноль».
 */
export type StockAlert = 'over' | 'missing';

export const STOCK_ALERT_LABEL: Record<StockAlert, string> = {
    over: 'В WB больше, чем отдадим',
    missing: 'В WB ноль, а товар есть',
};

export const STOCK_ALERT_HINT: Record<StockAlert, string> = {
    over: 'В кабинете WB остаток больше того, что мы можем поставить: WB продаёт то, '
        + 'чего на складе уже нет. Передайте остатки, чтобы выровнять кабинет.',
    missing: 'В кабинете WB ноль, а свободный остаток у нас есть — позиция не продаётся '
        + 'по FBS. Передайте остатки либо проверьте, не стоит ли ручное «не отдавать».',
};

type AlertInput = Pick<FbsStockRow, 'qty_wb' | 'qty_available'>;

export function stockAlertOf(row: AlertInput): StockAlert | null {
    if (row.qty_wb == null) return null;
    const wb = num(row.qty_wb);
    const plan = num(row.qty_available);
    if (wb > plan) return 'over';
    if (wb === 0 && plan > 0) return 'missing';
    return null;
}

// ─── Коробá: очередь на поштучную приёмку ───────────────────────────────────

/**
 * Состояние позиции относительно коробов — от «просто лежит» к «мёртвый груз»:
 *
 *  `boxed`    — коробá есть, но россыпь тоже: товар продаётся, запас про запас;
 *  `no_loose` — коробá есть, а в остатке НОЛЬ: по FBS не продаётся, пока ФФ не
 *               вскроет короб и не примет поштучно;
 *  `dead`     — то же, и на FBO тоже ноль: товар не продаётся НИГДЕ, лежит мёртвым
 *               грузом. Первый кандидат на вскрытие.
 *
 * `null` — коробов нет, состояние неприменимо. FBO неизвестен (`null`) в `dead`
 * не попадает: молчание — не то же самое, что подтверждённый ноль.
 */
export type BoxState = 'boxed' | 'no_loose' | 'dead';

type BoxInput = Pick<FbsStockRow, 'qty_ff_boxed' | 'qty_source' | 'fbo_qty'>;

export function boxStateOf(row: BoxInput): BoxState | null {
    if (num(row.qty_ff_boxed) <= 0) return null;
    if (num(row.qty_source) > 0) return 'boxed';
    return row.fbo_qty === 0 ? 'dead' : 'no_loose';
}

export const BOX_FILTER_LABEL: Record<BoxState, string> = {
    boxed: 'В коробах',
    no_loose: 'В коробах, нет в остатке',
    dead: 'Не продаётся нигде',
};

/** Попадает ли строка под фильтр: `dead` вложен в `no_loose`, тот — в `boxed`. */
export function matchesBoxFilter(row: BoxInput, filter: BoxState): boolean {
    const state = boxStateOf(row);
    if (state === null) return false;
    if (filter === 'boxed') return true;
    if (filter === 'no_loose') return state === 'no_loose' || state === 'dead';
    return state === 'dead';
}

/** Класс подсветки строки таблицы (пусто — расхождения нет). */
export function stockRowClassName(row: AlertInput): string {
    const alert = stockAlertOf(row);
    if (alert === 'over') return 'fbs-row-over';
    if (alert === 'missing') return 'fbs-row-missing';
    return '';
}

// ─── Колонки таблицы остатков ───────────────────────────────────────────────

export interface StockColumnDeps {
    selectedIds: Set<number>;
    onToggleSelected: (nomenclatureId: number) => void;
    /** Идёт сохранение количества — редакторы гасим, чтобы не слать гонку. */
    busy: boolean;
    /** Записать ручное количество; qty = null снимает ограничение. */
    onSetOverride: (row: FbsStockRow, qty: number | null) => void;
    /** Выделить все показанные строки / снять всё — чекбокс в шапке таблицы. */
    onToggleAll: (checked: boolean) => void;
    /** Сколько строк сейчас показано и сколько из них выделено — состояние чекбокса шапки. */
    shownCount: number;
    shownSelectedCount: number;
}

export function buildStockColumns(deps: StockColumnDeps): Column[] {
    const { selectedIds, onToggleSelected, busy, onSetOverride, onToggleAll, shownCount, shownSelectedCount } = deps;
    const allShownSelected = shownCount > 0 && shownSelectedCount === shownCount;
    const deductStyle = { background: 'rgba(239,68,68,0.05)' };
    return [
        {
            key: '__sel', label: '✓', width: '40px', sortable: false, align: 'center',
            headerTitle: allShownSelected
                ? 'Снять выделение со всех показанных строк'
                : `Выделить все показанные строки (${shownCount})`,
            // Чекбокс в шапке — то место, где его ищут в первую очередь: кнопка
            // «Выделить показанные» живёт над таблицей и в длинном списке уходит
            // из поля зрения вместе с шапкой фильтров.
            renderHeader: () => (
                <input
                    type="checkbox"
                    checked={allShownSelected}
                    // Частичное выделение — «−», а не пустой чекбокс: иначе
                    // выделив половину строк, человек видит то же самое, что и
                    // не выделив ничего.
                    ref={el => { if (el) el.indeterminate = shownSelectedCount > 0 && !allShownSelected; }}
                    disabled={shownCount === 0}
                    onChange={e => onToggleAll(e.target.checked)}
                />
            ),
            getValue: () => '',
            exportValue: () => '',
            render: (_v: unknown, row: FbsStockRow) => (
                <input
                    type="checkbox"
                    checked={row.nomenclature_id != null && selectedIds.has(row.nomenclature_id)}
                    disabled={row.nomenclature_id == null}
                    title={row.nomenclature_id == null ? 'Позиция не связана с номенклатурой' : undefined}
                    onChange={() => { if (row.nomenclature_id != null) onToggleSelected(row.nomenclature_id); }}
                />
            ),
        },
        {
            key: 'article_seller', label: 'Товар', width: '290px',
            render: (v: string | null, row: FbsStockRow) => (
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                    {row.nm_id ? (
                        <a
                            href={wbProductUrl(row.nm_id)}
                            target="_blank"
                            rel="noopener noreferrer"
                            title="Открыть карточку товара на Wildberries"
                        >
                            <WbThumb nmId={row.nm_id} size={40} />
                        </a>
                    ) : (
                        <WbThumb nmId={null} size={40} />
                    )}
                    <div style={{ minWidth: 0 }}>
                        <div style={{ fontWeight: 500 }}>{v || row.barcode}</div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                            {row.barcode}
                            {row.nm_id ? (
                                <>
                                    {' · '}
                                    <a
                                        href={wbProductUrl(row.nm_id)}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        style={{ color: 'var(--color-accent)' }}
                                        title="Открыть карточку товара на Wildberries"
                                    >
                                        nm {row.nm_id}
                                    </a>
                                </>
                            ) : null}
                        </div>
                        {(row.brand || row.subject || row.subcategory_name) && (
                            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                                {[row.brand, row.subject, row.subcategory_name].filter(Boolean).join(' · ')}
                            </div>
                        )}
                    </div>
                </div>
            ),
            exportValue: (row: FbsStockRow) => row.article_seller || row.barcode,
        },
        {
            key: 'brand', label: 'Бренд', width: '120px',
            render: (v: string | null) => v || <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        {
            // «Наш учёт» и «Зеркало ФФ» жили отдельными колонками рядом с
            // «Источником», а источник — это одна из них (или минимум из двух):
            // на складе без WMS-интеграции все три колонки показывали одно и то
            // же число. Осталась одна цифра — та, из которой реально считается
            // отдача, — а слагаемые всплывают подписью ТОЛЬКО когда расходятся.
            key: 'qty_source', label: 'Остаток', align: 'right',
            headerTitle: 'Остаток, который РЕАЛЬНО можно отгрузить, — то, из чего считается отдача. '
                + 'Недоступное вычтено: наш брак (отдельный счётчик), у ФФ — битое, собранное под '
                + 'чужую отгрузку и идущее в приёмке. Собранное под наши заявки тут не вычитается: '
                + 'его снимает колонка «− В сборке». '
                + 'Какая цифра берётся, задаёт настройка склада «источник остатка»: наш учёт, '
                + 'зеркало ФФ или минимум из двух. Когда зеркало ФФ расходится с нашим учётом, '
                + 'обе цифры показаны подписью под числом.',
            render: (v: number, row: FbsStockRow) => {
                // Сравниваем СВОБОДНЫЕ стороны (их считает бэкенд): наш учёт держит
                // товар до отгрузки, а WMS обычно уже отобрал его под заявку — на
                // сырых цифрах это читалось как расхождение, которого нет.
                // «Остаток» = один из этих двух, так что подпись объясняет выбор.
                const ledgerFree = num(row.qty_ledger_free);
                const mirrorFree = row.qty_ff_free == null ? null : num(row.qty_ff_free);
                return (
                    <div>
                        <strong>{formatNumber(num(v), 0)}</strong>
                        {mirrorFree != null && mirrorFree !== ledgerFree && (
                            <div
                                style={{ fontSize: 11, color: 'var(--color-text-dim)' }}
                                title={'Свободно по нашему учёту против свободного у ФФ (обе цифры — '
                                    + 'уже за вычетом сборки). «Остаток» берётся по настройке склада; '
                                    + 'разбор расхождения — на вкладке «Фулфилмент» самого склада'}
                            >
                                учёт {formatNumber(ledgerFree, 0)} · ФФ {formatNumber(mirrorFree, 0)}
                            </div>
                        )}
                    </div>
                );
            },
            exportValue: (row: FbsStockRow) => num(row.qty_source),
        },
        {
            // Коробá — не остаток, а очередь на вскрытие: продать штуку из
            // невскрытого короба нельзя, пока ФФ не примет его поштучно.
            key: 'qty_ff_boxed', label: 'В коробах', align: 'right', headerWrap: true,
            headerTitle: 'Лежит у ФФ коробами. В «Остаток» НЕ входит: маркетплейс покупает штуку, '
                + 'а короб продать нельзя — чтобы остаток встал в FBS, фулфилмент должен вскрыть '
                + 'короб и принять товар поштучно. Это и есть список «что можно перенести '
                + 'из коробов в россыпь».',
            render: (v: number, row: FbsStockRow) => {
                const pieces = num(v);
                if (!pieces) return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
                const boxes = num(row.ff_box_count);
                return (
                    <div>
                        <strong style={{ color: 'var(--color-accent)' }}>{formatNumber(pieces, 0)}</strong>
                        {boxes > 0 && (
                            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                                {formatNumber(boxes, 0)} кор.
                            </div>
                        )}
                    </div>
                );
            },
        },
        {
            key: 'fbo_qty', label: 'FBO', align: 'right',
            headerTitle: 'Остаток на складах WB (FBO). Прочерк — данных нет. '
                + 'В режиме «Только то, чего нет на FBO» позиция с живым FBO-остатком не транслируется.',
            render: (v: number | null) => {
                if (v == null) return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
                return (
                    <span style={{ color: v > 0 ? 'var(--color-warning)' : undefined }}>
                        {formatNumber(v, 0)}
                    </span>
                );
            },
        },
        {
            key: 'reserved_assembly', label: '− В сборке', align: 'right', cellStyle: deductStyle,
            headerTitle: 'Резерв активных заявок на сборку (PENDING/IN_PROGRESS/READY/'
                + 'VEHICLE_ASSIGNED). УЖЕ вычтен из «Остатка» — колонка объясняет, куда делась '
                + 'разница с нашим учётом, а не вычитается из него второй раз. WMS обычно тоже '
                + 'снимает этот товар со своего остатка, когда отбирает его под заявку.',
            render: (v: number) => v ? formatNumber(v, 0) : '—',
        },
        {
            key: 'fbs_open', label: '− Продано FBS', align: 'right', cellStyle: deductStyle,
            headerTitle: 'Заказы покупателей по FBS на этом складе продавца, которые ещё не уехали: '
                + 'сборочные задания WB в статусах «Новое» и «На сборке». Товар по ним физически '
                + 'уйдёт со склада, поэтому вычитается. Это НЕ наша заявка на сборку — она в '
                + 'соседней колонке «− В сборке» (отгрузка на склады WB, FBO).',
            render: (v: number) => v ? formatNumber(v, 0) : '—',
        },
        // Колонки «Брак» здесь нет намеренно: брак уже вычтен из «Остатка» с обеих
        // сторон (наш — отдельным счётчиком `defect_quantity`, у ФФ — там, где
        // провайдер его считает), и справочная цифра рядом только занимала место.
        // Поле `defect` в ответе осталось — им пользуется разбор расхождений;
        // разбор брака по складу живёт на вкладке «Фулфилмент» самого склада.
        {
            key: 'qty_computed', label: 'Можем отдать', align: 'right', headerWrap: true,
            headerTitle: 'Потолок отдачи: Остаток минус проданное по FBS и буфер — всё, кроме '
                + 'ручного количества. Сборка сюда не входит: она уже снята в «Остатке». '
                + 'Поднять выдачу выше этого числа нельзя — WB иначе продаст то, чего нет.',
            render: (v: number, row: FbsStockRow) => (
                <span style={{ color: row.blocked_reason ? 'var(--color-text-dim)' : undefined }}
                    title={row.blocked_reason ? blockedReasonLabel(row.blocked_reason) : undefined}>
                    {formatNumber(num(v), 0)}
                </span>
            ),
        },
        {
            // Колонка «= Отдаём» снята: её число — то же самое, что уедет в WB,
            // и держать рядом «отдаём», «в WB» и «кол-во» значило показывать
            // одну величину трижды. Что реально уйдёт, видно в поле «Кол-во»
            // (подпись «уйдёт N», когда цифра кабинета нам не по остатку).
            key: 'qty_wb', label: 'В WB', align: 'right',
            headerTitle: 'Живой остаток в кабинете WB на этом складе продавца — факт, который '
                + 'прямо сейчас видит покупатель. Число уже нетто: WB сам вычитает заказанное. '
                + 'Читается автоматически вместе с таблицей. Прочерк = позиция без chrtId либо '
                + 'кабинет не ответил.',
            render: (v: number | null, row: FbsStockRow) => {
                if (v == null) return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
                // Расхождение с тем, что мы собираемся отдать, — главный сигнал
                // экрана: именно из-за него раньше приходилось открывать сверку.
                const diff = num(v) - num(row.qty_available);
                const alert = stockAlertOf(row);
                return (
                    <span title={alert
                        ? STOCK_ALERT_HINT[alert]
                        : diff === 0
                            ? 'Совпадает с тем, что отдаём'
                            : `В кабинете на ${formatNumber(Math.abs(diff), 0)} шт меньше, чем отдаём`}>
                        <strong style={{ color: alert ? 'var(--color-danger)' : undefined }}>
                            {formatNumber(num(v), 0)}
                        </strong>
                        {diff !== 0 && (
                            <span style={{
                                fontSize: 11,
                                marginLeft: 4,
                                color: diff > 0 ? 'var(--color-danger)' : 'var(--color-accent)',
                            }}>
                                {diff > 0 ? '+' : ''}{formatNumber(diff, 0)}
                            </span>
                        )}
                    </span>
                );
            },
        },
        {
            key: '__qty', label: 'Кол-во', width: '128px', sortable: false, align: 'right',
            headerTitle: OVERRIDE_HINT,
            getValue: () => '',
            exportValue: (row: FbsStockRow) => (row.override_qty == null ? '' : String(row.override_qty)),
            render: (_v: unknown, row: FbsStockRow) => (
                <QtyCell
                    row={row}
                    busy={busy}
                    onCommit={qty => onSetOverride(row, qty)}
                />
            ),
        }
    ];
}

// ─── Колонки сводки по разрезу ──────────────────────────────────────────────

export interface GroupColumnDeps {
    groupBy: Exclude<GroupBy, ''>;
    /** Показать в таблице только эту группу (тот же фильтр, что в панели разрезов). */
    onFilterGroup: (group: GroupAgg) => void;
    /** Выделить все строки группы — дальше массовое проставление количества. */
    onSelectGroup: (group: GroupAgg) => void;
}

export function buildGroupColumns(deps: GroupColumnDeps): Column[] {
    const { groupBy, onFilterGroup, onSelectGroup } = deps;
    return [
        { key: 'label', label: GROUP_LABEL[groupBy], width: '220px' },
        { key: 'positions', label: 'Позиций', align: 'right', render: (v: number) => formatNumber(v, 0) },
        {
            key: 'units_computed', label: 'Расчёт, шт', align: 'right',
            headerTitle: 'Сумма свободного остатка по формуле — до ручного количества',
            render: (v: number) => formatNumber(v, 0),
        },
        {
            key: 'units_available', label: 'Отдаём, шт', align: 'right',
            render: (v: number, row: GroupAgg) => (
                <strong style={{ color: row.units_available !== row.units_computed ? 'var(--color-warning)' : undefined }}>
                    {formatNumber(v, 0)}
                </strong>
            ),
        },
        {
            key: 'overridden', label: 'С ручным кол-вом', align: 'right',
            headerTitle: 'Сколько позиций группы мы проставили руками',
            render: (v: number) => v ? formatNumber(v, 0) : '—',
        },
        {
            key: '__gactions', label: 'Действия', sortable: false, width: '240px',
            getValue: () => '',
            exportValue: () => '',
            render: (_v: unknown, row: GroupAgg) => (
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <button
                        className="btn btn-sm"
                        onClick={() => onFilterGroup(row)}
                        title="Показать в таблице только эту группу"
                    >
                        Фильтр
                    </button>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => onSelectGroup(row)}
                        title="Выделить все позиции группы — потом проставить им количество"
                    >
                        Выделить группу
                    </button>
                </div>
            ),
        },
    ];
}

// ─── Колонки лога трансляции ────────────────────────────────────────────────

export function buildPushColumns(warehouses: FbsWarehouse[]): Column[] {
    return [
        {
            key: 'started_at', label: 'Старт',
            render: (v: string) => <span style={{ fontSize: 13 }}>{formatDateTime(v)}</span>,
        },
        {
            key: 'wb_warehouse_id', label: 'Склад WB',
            render: (v: number | null) => {
                if (v == null) return 'все';
                const wh = warehouses.find(w => w.wb_warehouse_id === v);
                return wh?.name || `#${v}`;
            },
        },
        { key: 'trigger', label: 'Запуск', render: (v: string) => PUSH_TRIGGER_LABEL[v] ?? v },
        {
            key: 'status', label: 'Статус',
            render: (v: string) => (
                <span className={`badge ${PUSH_STATUS_BADGE[v] ?? 'badge-secondary'}`}>
                    {PUSH_STATUS_LABEL[v] ?? v}
                </span>
            ),
        },
        { key: 'rows_total', label: 'Позиций', align: 'right', render: (v: number) => formatNumber(v, 0) },
        { key: 'rows_changed', label: 'Изменилось', align: 'right', render: (v: number) => formatNumber(v, 0) },
        { key: 'rows_sent', label: 'Отправлено', align: 'right', render: (v: number) => formatNumber(v, 0) },
        {
            key: 'rows_no_chrt', label: 'Без chrtId', align: 'right',
            render: (v: number) => v
                ? <span style={{ color: 'var(--color-warning)', fontWeight: 600 }}>{formatNumber(v, 0)}</span>
                : '—',
        },
        {
            key: 'rows_mismatch', label: 'Расхождений', align: 'right',
            headerTitle: 'Позиции, где подтверждённый WB остаток не совпал с отправленным',
            render: (v: number) => v
                ? <span style={{ color: 'var(--color-danger)', fontWeight: 600 }}>{formatNumber(v, 0)}</span>
                : '—',
        },
        {
            key: 'error_msg', label: 'Ошибка', sortable: false,
            render: (v: string | null) => v
                ? <span style={{ fontSize: 12, color: 'var(--color-danger)' }} title={v}>{v.slice(0, 120)}</span>
                : '—',
        },
    ];
}

// ─── Инлайн-поле «Кол-во» ───────────────────────────────────────────────────

/**
 * Разобрать введённое в поле «Кол-во».
 *
 * Возвращает `{ ok: false }` на мусоре (буквы, минус, дробь) — такое значение
 * НЕ отправляем и откатываем поле: молча превратить «-5» в 0 значит снять
 * товар с продажи без ведома пользователя. Пустая строка — это не мусор,
 * а команда «снять ограничение», поэтому у неё отдельный ответ `qty: null`.
 */
export function parseOverrideInput(raw: string): { ok: boolean; qty: number | null } {
    const s = raw.trim();
    if (s === '') return { ok: true, qty: null };
    const n = Number(s.replace(',', '.'));
    if (!Number.isFinite(n) || n < 0) return { ok: false, qty: null };
    return { ok: true, qty: Math.trunc(n) };
}

/**
 * Что показать в поле «Кол-во» до правки.
 *
 * Своё ручное количество важнее всего — это решение человека. Если его нет,
 * подставляем ЦИФРУ ИЗ КАБИНЕТА (`qty_wb`): экран о том, что сейчас стоит в WB,
 * и поле обязано быть редактируемой версией той же цифры, а не пустотой,
 * в которую надо вписывать вслепую. Кабинет не прочитан (`null`) — поле пустое,
 * там работает обычный расчёт.
 */
export function qtyCellInitial(row: Pick<FbsStockRow, 'override_qty' | 'qty_wb'>): string {
    if (row.override_qty != null) return String(row.override_qty);
    if (row.qty_wb != null) return String(num(row.qty_wb));
    return '';
}

/**
 * Ручное количество на строке — редактируемая цифра кабинета WB. Локальный
 * стейт, чтобы набор цифр не перерисовывал таблицу; запись — по Enter или
 * потере фокуса. Пустое поле снимает ограничение, 0 означает «не отдавать».
 *
 * Записываем ТОЛЬКО когда значение реально изменили руками: поле теперь
 * предзаполнено остатком кабинета, и наивное сравнение с `override_qty`
 * превращало бы любой клик мимо в запись ручного количества.
 */
export function QtyCell({ row, busy, onCommit }: {
    row: FbsStockRow;
    busy: boolean;
    /** qty = null — снять ручное количество. */
    onCommit: (qty: number | null) => void;
}) {
    const initial = qtyCellInitial(row);
    const [val, setVal] = useState(initial);
    /** Escape гасит запись: blur() после него всё равно вызвал бы commit со старым val. */
    const skipCommit = useRef(false);

    // Внешнее обновление (пересчёт превью, массовое применение) важнее локального ввода
    useEffect(() => { setVal(initial); }, [initial]);

    const commit = () => {
        if (skipCommit.current) { skipCommit.current = false; return; }
        if (val === initial) return;                 // поле не трогали
        const parsed = parseOverrideInput(val);
        if (!parsed.ok) { setVal(initial); return; }
        // Ничего не изменилось — не дёргаем сервер и не перетираем чужую правку
        if ((parsed.qty ?? null) === (row.override_qty ?? null)) {
            setVal(initial);
            return;
        }
        onCommit(parsed.qty);
    };

    if (row.nomenclature_id == null) {
        return <span style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>—</span>;
    }

    const zeroed = row.override_qty === 0;
    // Цифра из кабинета — ещё не наше решение: показываем её приглушённо, чтобы
    // «мы так задали» и «столько стоит в WB» не выглядели одинаково.
    const fromWb = row.override_qty == null && row.qty_wb != null;
    // Введённое может быть недостижимо: ручное количество — потолок, итог всегда
    // min(введённое, «Можем отдать»). Без этой подписи человек считал бы, что в
    // WB уедет ровно то, что он видит в поле.
    const wanted = parseOverrideInput(val);
    const willSend = num(row.qty_available);
    const shortfall = wanted.ok && wanted.qty != null && wanted.qty !== willSend;

    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
            <div style={{ textAlign: 'right' }}>
                <input
                    className="form-input"
                    type="number"
                    min={0}
                    step="1"
                    style={{
                        width: 82, padding: '4px 6px', textAlign: 'right',
                        color: fromWb ? 'var(--color-text-muted)' : undefined,
                        borderColor: zeroed ? 'var(--color-danger)' : undefined,
                    }}
                    value={val}
                    disabled={busy}
                    placeholder="расчёт"
                    title={fromWb
                        ? `Сейчас в кабинете WB: ${formatNumber(num(row.qty_wb), 0)} шт. ${OVERRIDE_HINT}`
                        : OVERRIDE_HINT}
                    onChange={e => setVal(e.target.value)}
                    onBlur={commit}
                    onKeyDown={e => {
                        if (e.key === 'Enter') { e.currentTarget.blur(); }
                        if (e.key === 'Escape') {
                            skipCommit.current = true;
                            setVal(initial);
                            e.currentTarget.blur();
                        }
                    }}
                />
                {shortfall && (
                    <div
                        style={{ fontSize: 11, color: 'var(--color-warning)', marginTop: 2 }}
                        title={'В WB уедет min(введённое, «Можем отдать») — поднять выдачу выше '
                            + 'свободного остатка нельзя'}
                    >
                        уйдёт {formatNumber(willSend, 0)}
                    </div>
                )}
            </div>
            {zeroed && (
                <span
                    className="badge badge-danger"
                    style={{ fontSize: 10 }}
                    title="Позиция не отдаётся на WB: ручное количество 0"
                >
                    0
                </span>
            )}
        </div>
    );
}

/**
 * Компактный счётчик над таблицей. С `onClick` работает как фильтр: счётчики
 * расхождений с кабинетом — то место, куда смотрят первым, и заставлять искать
 * для них отдельный тумблер в перегруженной панели фильтров незачем.
 */
export function MiniKpi({ label, value, danger, warning, title, active, onClick }: {
    label: string;
    value: number;
    danger?: boolean;
    warning?: boolean;
    title?: string;
    active?: boolean;
    onClick?: () => void;
}) {
    const color = danger ? 'var(--color-danger)' : warning ? 'var(--color-warning)' : undefined;
    return (
        <div
            className="glass-card"
            style={{
                padding: '12px 16px',
                textAlign: 'center',
                cursor: onClick ? 'pointer' : undefined,
                outline: active ? '2px solid var(--color-accent)' : undefined,
            }}
            title={title}
            onClick={onClick}
            role={onClick ? 'button' : undefined}
            tabIndex={onClick ? 0 : undefined}
            onKeyDown={onClick
                ? e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(); } }
                : undefined}
        >
            <div style={{ fontSize: 22, fontWeight: 700, color }}>
                {formatNumber(value, 0)}
            </div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>{label}</div>
        </div>
    );
}
