// Чистые предикаты фильтрации таблицы «Кратность короба» (box-multiplicity).
// Вынесены из BoxMultiplicityView, чтобы тестировать логику фильтра без рендера
// и переиспользовать «липкость» отредактированной строки (см. matchesStockFilter).
import type { BoxMultiplicityRow } from '@/types/api';
import { parseBoxSize } from '@/lib/utils/boxPallet';

export type StockFilter = 'all' | 'rf' | 'partial' | 'mixed' | 'no_box_qty' | 'no_pallet';

// Резолв «применяемой» кратности SKU для сводки: ФФ с остатком и заданной кратностью.
export function skuHasBoxQty(row: BoxMultiplicityRow): boolean {
    return row.per_warehouse.some(p => p.rf_stock > 0 && p.box_qty !== null);
}

// Можно ли SKU палетизировать: есть ФФ с остатком, у которого И кратность, И
// габариты коробки (box_size парсится). Без обоих паллету не сформировать и
// корректно распределить нельзя — такие SKU подсвечиваем («Нет габаритов»).
export function skuCanPalletize(row: BoxMultiplicityRow): boolean {
    return row.per_warehouse.some(
        p => p.rf_stock > 0 && p.box_qty !== null && parseBoxSize(p.box_size) !== null,
    );
}

// Частичная кратность: среди ФФ с остатком есть И с заданной, И без — не везде заполнено.
export function skuHasPartialBoxQty(row: BoxMultiplicityRow): boolean {
    const stocked = row.per_warehouse.filter(p => p.rf_stock > 0);
    if (stocked.length === 0) return false;
    const withQty = stocked.filter(p => p.box_qty !== null).length;
    return withQty > 0 && withQty < stocked.length;
}

// Разные кратности: у одного товара возможно несколько разных кратностей.
// Срабатывает, если: (а) backend нашёл >1 ppb в истории всех машин (любой статус приёмки), ИЛИ
// (б) среди резолвнутых ФФ есть >1 различных box_qty, ИЛИ
// (в) на каком-то ФФ принято несколько машин с разной кратностью (machine_variants > 0).
export function skuHasMixedBoxQty(row: BoxMultiplicityRow): boolean {
    if (row.has_mixed_ppb) return true;
    if (row.per_warehouse.some(p => p.machine_variants > 0)) return true;
    const distinct = new Set<number>();
    for (const p of row.per_warehouse) {
        if (p.box_qty !== null) distinct.add(p.box_qty);
    }
    return distinct.size > 1;
}

// Проходит ли строка активный chip-фильтр остатков/кратности (без бренд/предмет/поиск).
export function matchesStockFilter(row: BoxMultiplicityRow, filter: StockFilter): boolean {
    switch (filter) {
        case 'rf': return row.rf_stock > 0;
        case 'partial': return skuHasPartialBoxQty(row);
        case 'mixed': return skuHasMixedBoxQty(row);
        case 'no_box_qty': return row.rf_stock > 0 && !skuHasBoxQty(row);
        // «Нет габаритов» — кратность есть, но размер коробки не задан → паллету не сформировать.
        case 'no_pallet': return row.rf_stock > 0 && skuHasBoxQty(row) && !skuCanPalletize(row);
        default: return true;
    }
}

// Строка видима, если проходит фильтр остатков ЛИБО «липкая» — только что отредактирована.
// Липкость держит отредактированную строку в списке до смены фильтра, чтобы после задания
// кратности (строка иначе тут же выпала бы из «Нет кратности») можно было дозадать и размер.
export function passesRowFilter(
    row: BoxMultiplicityRow,
    filter: StockFilter,
    stickyNmIds: ReadonlySet<number>,
): boolean {
    return matchesStockFilter(row, filter) || stickyNmIds.has(row.nm_id);
}
