'use client';
/**
 * Колонки вкладки «Остатки»: расшифровка формулы, остаток на FBO, инлайн-поле
 * «Кол-во» (потоварная замена количества), сводка по разрезу и лог трансляции.
 * Вынесено из StockTab, чтобы вкладка осталась читаемой.
 */
import { useEffect, useRef, useState } from 'react';
import { formatDateTime, formatNumber } from '@/lib/utils';
import type { Column } from '@/components/DataTable';
import type { FbsStockRow, FbsWarehouse } from '@/types/api';
import {
    OVERRIDE_HINT,
    PUSH_STATUS_BADGE,
    PUSH_STATUS_LABEL,
    PUSH_TRIGGER_LABEL,
    blockedReasonLabel,
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
            key: 'article_seller', label: 'Товар', width: '230px',
            render: (v: string | null, row: FbsStockRow) => (
                <div>
                    <div style={{ fontWeight: 500 }}>{v || row.barcode}</div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                        {row.barcode}{row.nm_id ? ` · nm ${row.nm_id}` : ''}
                    </div>
                    {(row.brand || row.subject || row.subcategory_name) && (
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                            {[row.brand, row.subject, row.subcategory_name].filter(Boolean).join(' · ')}
                        </div>
                    )}
                </div>
            ),
            exportValue: (row: FbsStockRow) => row.article_seller || row.barcode,
        },
        {
            key: 'brand', label: 'Бренд', width: '120px',
            render: (v: string | null) => v || <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        {
            key: 'subcategory_name', label: 'Под-категория', width: '140px',
            render: (v: string | null) => v || <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        {
            key: 'chrt_id', label: 'chrtId', align: 'right',
            headerTitle: 'Ключ трансляции остатков в WB. Без него позиция физически не уходит.',
            render: (v: number | null) => v == null
                ? <span className="badge badge-warning" style={{ fontSize: 11 }}>нет</span>
                : <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span>,
        },
        {
            key: 'qty_ledger', label: 'Наш учёт', align: 'right',
            headerTitle: 'WarehouseStock.quantity — годный остаток (брак не входит)',
            render: (v: number) => formatNumber(v, 0),
        },
        {
            key: 'qty_ff_mirror', label: 'Зеркало ФФ', align: 'right',
            headerTitle: 'FulfillmentStock.qty_good — остаток по данным WMS провайдера',
            render: (v: number | null) => v == null
                ? <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                : formatNumber(v, 0),
        },
        {
            key: 'qty_source', label: 'Источник', align: 'right',
            headerTitle: 'Итог по выбранному источнику склада (ledger / зеркало / минимум)',
            render: (v: number) => <strong>{formatNumber(v, 0)}</strong>,
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
            headerTitle: 'Резерв активных заявок на сборку (PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED)',
            render: (v: number) => v ? formatNumber(v, 0) : '—',
        },
        {
            key: 'fbs_open', label: '− FBS-заказы', align: 'right', cellStyle: deductStyle,
            headerTitle: 'Открытые сборочные задания FBS по этому складу WB (new / confirm)',
            render: (v: number) => v ? formatNumber(v, 0) : '—',
        },
        {
            key: 'buffer', label: '− Буфер', align: 'right', cellStyle: deductStyle,
            headerTitle: 'Страховой запас: % от источника + абсолютная добавка',
            render: (v: number) => v ? formatNumber(v, 0) : '—',
        },
        {
            key: 'defect', label: 'Брак', align: 'right',
            headerTitle: 'Справочно: брак не входит в источник и не вычитается повторно',
            render: (v: number) => v ? formatNumber(v, 0) : '—',
        },
        {
            key: 'qty_computed', label: 'Расчёт', align: 'right',
            headerTitle: 'Свободный остаток по формуле — ДО ручного количества',
            render: (v: number) => formatNumber(v ?? 0, 0),
        },
        {
            key: 'qty_available', label: '= Отдаём', align: 'right',
            cellStyle: { background: 'rgba(34,197,94,0.06)' },
            headerTitle: 'Столько уйдёт в WB: min(ручное количество, расчёт), либо 0 при «не отдавать»',
            render: (v: number, row: FbsStockRow) => {
                // «Расчёт» фиксируется ДО вычетов уровня позиции (абсолютный буфер,
                // открытые FBS-задания, потолок склада), поэтому расхождение само по
                // себе ручным количеством не является: без override оранжевый цвет
                // гнал бы искать несуществующее ограничение.
                const trimmed = (row.qty_computed ?? 0) !== v;
                const cut = trimmed && row.override_qty != null;
                const delta = formatNumber((row.qty_computed ?? 0) - v, 0);
                return (
                    <strong
                        style={{
                            color: row.blocked_reason
                                ? 'var(--color-text-dim)'
                                : cut ? 'var(--color-warning)' : 'var(--color-text)',
                        }}
                        title={cut
                            ? `Ручное количество урезало расчёт ${formatNumber(row.qty_computed ?? 0, 0)} шт`
                            : trimmed
                                ? `Складские лимиты (буфер, открытые FBS-заказы, потолок склада) урезали расчёт на ${delta} шт`
                                : undefined}
                    >
                        {formatNumber(v, 0)}
                    </strong>
                );
            },
        },
        {
            key: '__qty', label: 'Кол-во', width: '120px', sortable: false, align: 'right',
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
        },
        {
            key: 'qty_sent', label: 'Отправлено', align: 'right',
            headerTitle: 'Что реально ушло в WB прошлым прогоном',
            render: (v: number | null) => v == null ? '—' : formatNumber(v, 0),
        },
        {
            key: 'qty_confirmed', label: 'Подтв. WB', align: 'right',
            headerTitle: 'Проверка после PUT: WB отвечает 204 даже когда остаток не обновился',
            render: (v: number | null, row: FbsStockRow) => {
                if (v == null) return '—';
                const mismatch = row.qty_sent != null && row.qty_sent !== v;
                return (
                    <span style={{ color: mismatch ? 'var(--color-danger)' : undefined, fontWeight: mismatch ? 600 : 400 }}>
                        {formatNumber(v, 0)}
                    </span>
                );
            },
        },
        {
            key: 'blocked_reason', label: 'Причина', sortable: false,
            render: (v: string | null) => v
                ? <span className="badge badge-warning" style={{ fontSize: 11 }}>{blockedReasonLabel(v)}</span>
                : <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
            exportValue: (row: FbsStockRow) => row.blocked_reason ? blockedReasonLabel(row.blocked_reason) : '',
        },
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
 * Ручное количество на строке. Локальный стейт, чтобы набор цифр не
 * перерисовывал таблицу; запись — по Enter или потере фокуса. Пустое поле
 * снимает ограничение, 0 означает «не отдавать».
 */
export function QtyCell({ row, busy, onCommit }: {
    row: FbsStockRow;
    busy: boolean;
    /** qty = null — снять ручное количество. */
    onCommit: (qty: number | null) => void;
}) {
    const initial = row.override_qty == null ? '' : String(row.override_qty);
    const [val, setVal] = useState(initial);
    /** Escape гасит запись: blur() после него всё равно вызвал бы commit со старым val. */
    const skipCommit = useRef(false);

    // Внешнее обновление (пересчёт превью, массовое применение) важнее локального ввода
    useEffect(() => { setVal(initial); }, [initial]);

    const commit = () => {
        if (skipCommit.current) { skipCommit.current = false; return; }
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
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
            <input
                className="form-input"
                type="number"
                min={0}
                step="1"
                style={{
                    width: 82, padding: '4px 6px', textAlign: 'right',
                    borderColor: zeroed ? 'var(--color-danger)' : undefined,
                }}
                value={val}
                disabled={busy}
                placeholder="расчёт"
                title={OVERRIDE_HINT}
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

/** Компактный счётчик над таблицей. */
export function MiniKpi({ label, value, danger }: { label: string; value: number; danger?: boolean }) {
    return (
        <div className="glass-card" style={{ padding: '12px 16px', textAlign: 'center' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: danger ? 'var(--color-danger)' : undefined }}>
                {formatNumber(value, 0)}
            </div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>{label}</div>
        </div>
    );
}
