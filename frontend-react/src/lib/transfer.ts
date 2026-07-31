import type { StockTransfer, StockTransferStatus } from '@/types/api';
import { formatNumber, pluralRu } from '@/lib/utils';

/**
 * Общий словарь и хелперы перемещения (переезда между нашими складами).
 *
 * Цепочка коротка и ступени «машина назначена» в ней НЕТ: назначенная машина —
 * это признак черновика (непустой `vehicle_assigned_at`), а не отдельный статус.
 * Поэтому «есть машина» считаем по vehicleAssigned(), а не по status.
 */
export const TRANSFER_STATUS_MAP: Record<StockTransferStatus, { label: string; className: string }> = {
    DRAFT:      { label: 'Черновик', className: 'badge-secondary' },
    IN_TRANSIT: { label: 'В пути',   className: 'badge-info' },
    COMPLETED:  { label: 'Принято',  className: 'badge-success' },
};

/** Подпись статуса; неизвестный статус отдаём как есть, а не пустой строкой. */
export function transferStatusLabel(status: StockTransferStatus | string): string {
    return TRANSFER_STATUS_MAP[status as StockTransferStatus]?.label ?? String(status);
}

export function transferStatusClass(status: StockTransferStatus | string): string {
    return TRANSFER_STATUS_MAP[status as StockTransferStatus]?.className ?? 'badge-secondary';
}

/**
 * Numeric(18,2) приезжает СТРОКОЙ («15000.00»). Приводим к числу один раз здесь,
 * чтобы в разметке не плодить Number(...) с молчаливым NaN.
 * Пустая строка / null / нечисловое → null («не задано»), а не 0.
 */
export function toMoney(v: string | number | null | undefined): number | null {
    if (v === null || v === undefined || v === '') return null;
    const n = typeof v === 'number' ? v : Number(v);
    return Number.isFinite(n) ? n : null;
}

/** Машина назначена — по факту заполнения, а не по статусу (ступени статуса нет). */
export function transferVehicleAssigned(t: StockTransfer): boolean {
    return !!(t.vehicle_assigned_at || t.vehicle_info);
}

/** ФИО водителя одной строкой; пусто → null. */
export function transferDriverName(t: StockTransfer): string | null {
    const name = [t.driver_first_name, t.driver_last_name].filter(Boolean).join(' ').trim();
    return name || null;
}

/**
 * Всего штук в перемещении.
 *
 * Источник зависит от ответа: в СПИСКЕ состава больше нет (его убрали — он
 * тянул мегабайты ради двух чисел), там приходит готовый `units_total`. В
 * деталке состав есть, и по нему можно посчитать. Порядок «поле → состав»
 * держит оба ответа и переживает окно деплоя, когда поля ещё нет.
 */
export function transferUnits(t: StockTransfer): number {
    if (t.units_total != null) return t.units_total;
    return (t.items ?? []).reduce((s, it) => s + (it.quantity || 0), 0);
}

/** Позиций (SKU). Тот же порядок источников, что и у transferUnits. */
export function transferSkuCount(t: StockTransfer): number {
    if (t.sku_count != null) return t.sku_count;
    return (t.items ?? []).length;
}

/**
 * Период по умолчанию для отчёта логистики переездов — ЕДИНЫЙ источник для
 * вкладки «Переезды» и мини-сводки на вкладке «Перемещения».
 *
 * Иначе сводка «за период» считалась бы за всё время, а ссылка «Подробнее»
 * вела на экран с другим окном: суммы не сходятся, и логист идёт искать
 * расхождение, которого нет.
 */
export const TRANSFER_REPORT_DEFAULT_DAYS = 90;

export function transferReportDefaultRange(): { date_from: string; date_to: string } {
    const to = new Date();
    const from = new Date();
    from.setDate(from.getDate() - TRANSFER_REPORT_DEFAULT_DAYS);
    return { date_from: from.toISOString().slice(0, 10), date_to: to.toISOString().slice(0, 10) };
}

// ─── Транспортная единица: паллеты или короба ───────────────────────────────
// Подписи дословно как у заявки на сборку (assembly/[id]/page.tsx: unitCountLabel
// / unitWeightLabel, logistics/page.tsx: unitShort) — у пользователя не должно
// быть двух языков для одного и того же поля.

/** Заголовок количества: «Палеты» / «Короба». */
export function unitCountLabel(shippedAsBoxes?: boolean | null): string {
    return shippedAsBoxes ? 'Короба' : 'Палеты';
}

/** Заголовок веса ОДНОЙ единицы: «Вес 1 палеты» / «Вес 1 короба». */
export function unitWeightLabel(shippedAsBoxes?: boolean | null): string {
    return shippedAsBoxes ? 'Вес 1 короба' : 'Вес 1 палеты';
}

/** Короткая подпись в таблицах: «пал» / «кор». */
export function unitShort(shippedAsBoxes?: boolean | null): string {
    return shippedAsBoxes ? 'кор' : 'пал';
}

/**
 * Режим выбора единицы в форме назначения машины.
 *
 * `keep` существует только ради BULK: одна машина едет за несколькими
 * переездами, среди которых бывают и паллетные, и коробочные. Двухпозиционный
 * тумблер там врёт — он вынужден показать что-то одно, и это «одно» уедет на
 * все выбранные, молча перевернув чужую единицу. Поэтому третье состояние —
 * «не менять» — и оно же дефолт, когда исходное значение неизвестно.
 */
export type UnitMode = 'keep' | 'pallets' | 'boxes';

/** Исходный режим формы: известный флаг → конкретная единица, null/undefined → «не менять». */
export function initialUnitMode(shippedAsBoxes: boolean | null | undefined): UnitMode {
    if (shippedAsBoxes === null || shippedAsBoxes === undefined) return 'keep';
    return shippedAsBoxes ? 'boxes' : 'pallets';
}

/**
 * Режим → значение для API. `keep` → null: бэкенд null игнорирует и уже
 * заданную единицу НЕ трогает. Именно поэтому нельзя отдавать `false` —
 * на бэке `if payload.shipped_as_boxes is not None` перезапишет коробочный
 * переезд в паллетный.
 */
export function unitModeToFlag(mode: UnitMode): boolean | null {
    if (mode === 'keep') return null;
    return mode === 'boxes';
}

/** Количество с единицей словами: «5 палет» / «12 коробов»; null → «—». */
export function unitCountText(count: number | null | undefined, shippedAsBoxes?: boolean | null): string {
    if (count == null) return '—';
    const forms: [string, string, string] = shippedAsBoxes
        ? ['короб', 'короба', 'коробов']
        : ['палета', 'палеты', 'палет'];
    return `${formatNumber(count, 0)} ${pluralRu(count, forms)}`;
}
