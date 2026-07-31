/**
 * Хелперы перемещения (переезда между складами) — контракт разбора ответа API.
 *
 * Что закрепляется:
 *  1. toMoney: Numeric приезжает СТРОКОЙ («15000.00»). Разница между «не
 *     задано» (null) и «ноль» (0) значимая — 0 ₽ забора это бесплатный
 *     переезд, а null это «стоимость ещё не заводили». Наивный Number(v)
 *     схлопывает null/''/мусор в 0/NaN и врёт в обе стороны;
 *  2. transferVehicleAssigned: ступени статуса «машина назначена» в цепочке
 *     НЕТ (DRAFT → IN_TRANSIT → COMPLETED). Признак — vehicle_assigned_at,
 *     а на старых записях (до бэкфилла) только vehicle_info;
 *  3. словарь статусов покрывает ровно коды бэкенда, а неизвестный код
 *     отдаётся как есть, а не пустой строкой;
 *  4. состав считается по items: SKU — строки, штуки — сумма quantity.
 */
import { describe, expect, it } from 'vitest';
import type { StockTransfer } from '@/types/api';
import {
    TRANSFER_REPORT_DEFAULT_DAYS,
    TRANSFER_STATUS_MAP,
    initialUnitMode,
    toMoney,
    transferReportDefaultRange,
    unitModeToFlag,
    transferDriverName,
    transferSkuCount,
    transferStatusLabel,
    transferUnits,
    transferVehicleAssigned,
    unitCountLabel,
    unitCountText,
    unitShort,
    unitWeightLabel,
} from '@/lib/transfer';

/** Статусы, которые реально отдаёт backend (TransferStatus). */
const BACKEND_STATUSES = ['DRAFT', 'IN_TRANSIT', 'COMPLETED'];

function makeTransfer(patch: Partial<StockTransfer> = {}): StockTransfer {
    return {
        id: 1,
        project_id: 1,
        from_warehouse_id: 2,
        to_warehouse_id: 14,
        number: 'TR-31',
        status: 'DRAFT',
        is_defect: false,
        items: [],
        ...patch,
    };
}

describe('toMoney', () => {
    it('разбирает Numeric-строку бэкенда', () => {
        expect(toMoney('15000.00')).toBe(15000);
        expect(toMoney('0.50')).toBe(0.5);
        // Строкой приезжает не только pickup_cost: pallet_weight_kg — тоже
        // Numeric (подтверждено HTTP-тестом бэкенда: «175.50», не 175.5).
        expect(toMoney('175.50')).toBe(175.5);
    });

    it('различает «не задано» и ноль', () => {
        expect(toMoney(null)).toBeNull();
        expect(toMoney(undefined)).toBeNull();
        expect(toMoney('')).toBeNull();
        expect(toMoney('0')).toBe(0);
        expect(toMoney(0)).toBe(0);
    });

    it('нечисловое значение — не задано, а не NaN', () => {
        expect(toMoney('—')).toBeNull();
        expect(toMoney('abc')).toBeNull();
    });
});

describe('transferVehicleAssigned', () => {
    it('черновик без машины — не назначена', () => {
        expect(transferVehicleAssigned(makeTransfer())).toBe(false);
    });

    it('назначение видно по vehicle_assigned_at, а не по статусу', () => {
        const t = makeTransfer({ status: 'DRAFT', vehicle_assigned_at: '2026-07-31T10:00:00Z' });
        expect(transferVehicleAssigned(t)).toBe(true);
    });

    it('старая запись без vehicle_assigned_at, но с госномером — тоже назначена', () => {
        expect(transferVehicleAssigned(makeTransfer({ vehicle_info: 'В874УА37' }))).toBe(true);
    });
});

describe('TRANSFER_STATUS_MAP', () => {
    it('покрывает ровно коды бэкенда', () => {
        expect(Object.keys(TRANSFER_STATUS_MAP).sort()).toEqual([...BACKEND_STATUSES].sort());
    });

    it('неизвестный статус отдаётся как есть', () => {
        expect(transferStatusLabel('WAT')).toBe('WAT');
        expect(transferStatusLabel('IN_TRANSIT')).toBe('В пути');
    });
});

describe('транспортная единица', () => {
    it('подписи дословно совпадают с заявкой на сборку', () => {
        // Источник канона — assembly/[id]/page.tsx (unitCountLabel/unitWeightLabel).
        // Разъедься подписи — у пользователя окажется два языка для одного поля.
        expect(unitCountLabel(false)).toBe('Палеты');
        expect(unitCountLabel(true)).toBe('Короба');
        expect(unitWeightLabel(false)).toBe('Вес 1 палеты');
        expect(unitWeightLabel(true)).toBe('Вес 1 короба');
        expect(unitShort(false)).toBe('пал');
        expect(unitShort(true)).toBe('кор');
    });

    it('склоняет количество по числу', () => {
        expect(unitCountText(1, false)).toBe('1 палета');
        expect(unitCountText(2, false)).toBe('2 палеты');
        expect(unitCountText(5, false)).toBe('5 палет');
        expect(unitCountText(1, true)).toBe('1 короб');
        expect(unitCountText(2, true)).toBe('2 короба');
        expect(unitCountText(12, true)).toBe('12 коробов');
    });

    it('ноль — валидное количество, отсутствие — прочерк', () => {
        expect(unitCountText(0, false)).toBe('0 палет');
        expect(unitCountText(null)).toBe('—');
        expect(unitCountText(undefined)).toBe('—');
    });

    it('единица по умолчанию — паллеты (флаг не задан)', () => {
        expect(unitCountLabel(undefined)).toBe('Палеты');
        expect(unitCountText(3, undefined)).toBe('3 палеты');
    });
});

/**
 * Регрессия на HIGH из ревью 31.07: bulk молча переворачивал единицу.
 *
 * Было так. Тумблер «Паллеты/Короба» двухпозиционный, в bulk исходное значение
 * неизвестно (переезды разные) → визуально стоял на «Паллеты». Признак «логист
 * трогал единицу» взводился ЛЮБЫМ контролом блока, включая поле количества.
 * Логист выделял TR-31 (паллеты), TR-32 (паллеты), TR-33 (КОРОБА), вводил
 * количество — и на все три уходил `shipped_as_boxes: false`, потому что бэкенд
 * применяет любое не-null значение. TR-33 становился паллетным, попадал в
 * `total_pallets`/`cost_per_pallet` (которые обязаны считать только паллетные),
 * а подпись «коробов» превращалась в «палет». Подсказка модалки при этом
 * успокаивала: «пустые поля ничего не затирают».
 *
 * Теперь единица — отдельный трёхпозиционный режим, не выводимый из соседних
 * числовых полей.
 */
describe('режим транспортной единицы (bulk)', () => {
    it('неизвестная исходная единица → режим «не менять»', () => {
        expect(initialUnitMode(null)).toBe('keep');
        expect(initialUnitMode(undefined)).toBe('keep');
    });

    it('известная единица → соответствующий режим', () => {
        expect(initialUnitMode(false)).toBe('pallets');
        expect(initialUnitMode(true)).toBe('boxes');
    });

    it('«не менять» уходит на бэкенд как null, а не как false', () => {
        // false здесь означал бы «сделать паллетным» — ровно тот баг.
        expect(unitModeToFlag('keep')).toBeNull();
    });

    it('явный выбор логиста уходит булевым', () => {
        expect(unitModeToFlag('pallets')).toBe(false);
        expect(unitModeToFlag('boxes')).toBe(true);
    });

    it('сценарий ревью: ввод количества без выбора единицы её не меняет', () => {
        // Модалка стартует из initial (bulk → null) и остаётся в 'keep', пока
        // логист не нажмёт кнопку единицы. Ввод чисел на режим не влияет.
        const mode = initialUnitMode(null);
        expect(unitModeToFlag(mode)).toBeNull();
    });
});

describe('период отчёта по переездам', () => {
    it('окно по умолчанию — 90 дней, общее для сводки и вкладки', () => {
        const { date_from, date_to } = transferReportDefaultRange();
        const days = Math.round(
            (new Date(date_to).getTime() - new Date(date_from).getTime()) / 86_400_000,
        );
        expect(days).toBe(TRANSFER_REPORT_DEFAULT_DAYS);
        expect(date_from).toMatch(/^\d{4}-\d{2}-\d{2}$/);
        expect(date_to).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    });
});

describe('состав: поле бэкенда против расчёта по items', () => {
    it('в списке состава нет — берём готовые units_total / sku_count', () => {
        const t = makeTransfer({ units_total: 340, sku_count: 12 });
        expect(transferUnits(t)).toBe(340);
        expect(transferSkuCount(t)).toBe(12);
    });

    it('в деталке поля может не быть — падаем на состав', () => {
        const t = makeTransfer({
            items: [
                { id: 1, transfer_id: 1, nomenclature_id: 10, barcode: '111', quantity: 40 },
                { id: 2, transfer_id: 1, nomenclature_id: 11, barcode: '222', quantity: 2 },
            ],
        });
        expect(transferUnits(t)).toBe(42);
        expect(transferSkuCount(t)).toBe(2);
    });

    it('ни поля, ни состава — ноль, а не падение', () => {
        const t = makeTransfer();
        delete (t as { items?: unknown }).items;
        expect(transferUnits(t)).toBe(0);
        expect(transferSkuCount(t)).toBe(0);
    });
});

describe('состав и водитель', () => {
    it('считает SKU и штуки по items', () => {
        const t = makeTransfer({
            items: [
                { id: 1, transfer_id: 1, nomenclature_id: 10, barcode: '111', quantity: 40 },
                { id: 2, transfer_id: 1, nomenclature_id: 11, barcode: '222', quantity: 2 },
            ],
        });
        expect(transferSkuCount(t)).toBe(2);
        expect(transferUnits(t)).toBe(42);
    });

    it('ФИО пустое → null, а не строка из пробелов', () => {
        expect(transferDriverName(makeTransfer())).toBeNull();
        expect(transferDriverName(makeTransfer({ driver_first_name: 'Дмитрий' }))).toBe('Дмитрий');
        expect(transferDriverName(makeTransfer({ driver_first_name: 'Дмитрий', driver_last_name: 'Крапива' })))
            .toBe('Дмитрий Крапива');
    });
});
