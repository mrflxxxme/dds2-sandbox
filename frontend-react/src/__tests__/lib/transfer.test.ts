/**
 * Хелперы перемещения (переезда между складами) — контракт разбора ответа API.
 *
 * Что закрепляется:
 *  1. toMoney: Numeric приезжает СТРОКОЙ («15000.00»). Разница между «не
 *     задано» (null) и «ноль» (0) значимая — 0 ₽ забора это бесплатный
 *     переезд, а null это «стоимость ещё не заводили». Наивный Number(v)
 *     схлопывает null/''/мусор в 0/NaN и врёт в обе стороны;
 *  2. transferVehicleAssigned: признак — заполненные реквизиты машины
 *     (vehicle_assigned_at, а на старых записях только vehicle_info), а НЕ
 *     статус: у уехавшего переезда машина тоже была, и карточка её показывает;
 *  3. словарь статусов покрывает ровно коды бэкенда, а неизвестный код
 *     отдаётся как есть, а не пустой строкой;
 *  4. гейты действий разрешают ровно то, что разрешает TRANSFER_TRANSITIONS
 *     бэкенда: кнопка, отвечающая 400, хуже отсутствующей;
 *  5. состав считается по items: SKU — строки, штуки — сумма quantity.
 */
import { describe, expect, it } from 'vitest';
import type { StockTransfer, TransferFfLink } from '@/types/api';
import { ASSEMBLY_STATUS_MAP } from '@/lib/assembly-status';
import {
    TRANSFER_REPORT_DEFAULT_DAYS,
    TRANSFER_STATUS_MAP,
    canAssignTransferVehicle,
    canCloseTransfer,
    canCompleteTransfer,
    canEditTransfer,
    canMarkTransferReady,
    canPushTransferToFf,
    canReturnTransfer,
    canSendTransfer,
    canUnassignTransferVehicle,
    ffLinkLabel,
    ffLinkStage,
    filterTransferFfLinks,
    initialUnitMode,
    splitTransferFfLinks,
    toMoney,
    transferEditError,
    transferReportDefaultRange,
    unitModeToFlag,
    transferDaysStuck,
    transferDriverName,
    transferReceiveProgress,
    transferSkuCount,
    transferStatusLabel,
    transferUnits,
    transferVehicleAssigned,
    unitCountLabel,
    unitCountText,
    unitShort,
    unitWeightLabel,
} from '@/lib/transfer';

/** Статусы, которые реально отдаёт backend (TransferStatus) — зеркало заявки. */
const BACKEND_STATUSES = [
    'PENDING',
    'IN_PROGRESS',
    'READY',
    'VEHICLE_ASSIGNED',
    'SHIPPED',
    'DELIVERED',
    'RETURNED',
    'CLOSED',
    'CANCELLED',
];

function makeTransfer(patch: Partial<StockTransfer> = {}): StockTransfer {
    return {
        id: 1,
        project_id: 1,
        from_warehouse_id: 2,
        to_warehouse_id: 14,
        number: 'TR-31',
        status: 'PENDING',
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
    it('новый переезд без машины — не назначена', () => {
        expect(transferVehicleAssigned(makeTransfer())).toBe(false);
    });

    it('назначение видно по vehicle_assigned_at', () => {
        const t = makeTransfer({ status: 'VEHICLE_ASSIGNED', vehicle_assigned_at: '2026-07-31T10:00:00Z' });
        expect(transferVehicleAssigned(t)).toBe(true);
    });

    it('старая запись без vehicle_assigned_at, но с госномером — тоже назначена', () => {
        expect(transferVehicleAssigned(makeTransfer({ vehicle_info: 'В874УА37' }))).toBe(true);
    });

    it('у уехавшего переезда машина остаётся видимой', () => {
        // Статус ушёл вперёд, но госномер логисту нужен и в пути, и после
        // приёмки: признак — реквизиты, а не ступень.
        const t = makeTransfer({ status: 'SHIPPED', vehicle_info: 'В874УА37' });
        expect(transferVehicleAssigned(t)).toBe(true);
        expect(canAssignTransferVehicle(t.status)).toBe(false);
    });
});

describe('TRANSFER_STATUS_MAP', () => {
    it('покрывает ровно коды бэкенда', () => {
        expect(Object.keys(TRANSFER_STATUS_MAP).sort()).toEqual([...BACKEND_STATUSES].sort());
    });

    it('неизвестный статус отдаётся как есть', () => {
        expect(transferStatusLabel('WAT')).toBe('WAT');
    });

    it('цвета — 1:1 с заявкой на сборку', () => {
        // Одинаковая ступень обязана выглядеть одинаково на обоих экранах:
        // разъедься классы — пользователь считает переезд «другим» документом.
        for (const status of BACKEND_STATUSES) {
            const key = status as keyof typeof TRANSFER_STATUS_MAP;
            expect(TRANSFER_STATUS_MAP[key].className)
                .toBe(ASSEMBLY_STATUS_MAP[status as keyof typeof ASSEMBLY_STATUS_MAP].className);
        }
    });

    it('подписи повторяют заявку всюду, кроме четырёх осознанных мест', () => {
        // 1:1 — там, где заявочная формулировка на переезде верна.
        expect(transferStatusLabel('IN_PROGRESS')).toBe('В сборке');
        expect(transferStatusLabel('READY')).toBe('Готово');
        expect(transferStatusLabel('VEHICLE_ASSIGNED')).toBe('Машина назначена');
        expect(transferStatusLabel('RETURNED')).toBe('Возврат на склад');
        expect(transferStatusLabel('CLOSED')).toBe('Закрыт');

        // PENDING: у заявки это legacy-ступень с подписью IN_PROGRESS,
        // у переезда — живой отдельный статус, и одинаковая подпись сделала бы
        // бейдж и фильтр неразличимыми.
        expect(transferStatusLabel('PENDING')).toBe('Создан');
        expect(transferStatusLabel('PENDING')).not.toBe(transferStatusLabel('IN_PROGRESS'));

        // SHIPPED: «отгрузка» между СВОИМИ складами читается как отгрузка
        // наружу; товар физически едет — это состояние весь склад и Лист
        // логиста называют «В пути» (так же назывался прежний IN_TRANSIT).
        expect(transferStatusLabel('SHIPPED')).toBe('В пути');

        // DELIVERED: никакого WB здесь нет — принимает наш склад-получатель.
        expect(transferStatusLabel('DELIVERED')).toBe('Принято на складе');

        // CANCELLED: только род — переезд отменён, а не заявка отменена.
        expect(transferStatusLabel('CANCELLED')).toBe('Отменён');
    });
});

/**
 * Гейты действий. Показываем кнопку ТОЛЬКО там, где переход разрешён:
 * мёртвая кнопка, отвечающая 400, хуже отсутствующей. Набор зеркалит
 * TRANSFER_TRANSITIONS из backend/models/warehouse.py.
 */
describe('гейты действий по статусу', () => {
    it('правка — до отгрузки: сток ещё не списан', () => {
        expect(BACKEND_STATUSES.filter(canEditTransfer))
            .toEqual(['PENDING', 'IN_PROGRESS', 'READY']);
    });

    it('«Готов» — пока переезд собирают, плюс переотправка из возврата', () => {
        // RETURNED здесь не за компанию: бэкенд разрешает RETURNED → READY и
        // обещает переотправку в докстринге return_transfer. Без этой ветки
        // вернувшийся переезд застревал намертво — правка живёт только в
        // PENDING/IN_PROGRESS/READY, значит оставалось лишь «Закрыть».
        expect(BACKEND_STATUSES.filter(canMarkTransferReady))
            .toEqual(['PENDING', 'IN_PROGRESS', 'RETURNED']);
    });

    it('машина — из «Готово», замена — из «Машина назначена»', () => {
        expect(BACKEND_STATUSES.filter(canAssignTransferVehicle))
            .toEqual(['READY', 'VEHICLE_ASSIGNED']);
        // Снятие возвращает переезд в READY, поэтому доступно ровно там,
        // где машина уже назначена.
        expect(BACKEND_STATUSES.filter(canUnassignTransferVehicle))
            .toEqual(['VEHICLE_ASSIGNED']);
    });

    it('«Отправить» — из «Готово» и «Машина назначена»', () => {
        expect(BACKEND_STATUSES.filter(canSendTransfer))
            .toEqual(['READY', 'VEHICLE_ASSIGNED']);
        // Из PENDING отправить нельзя: форма создания поэтому делает ready+send.
        expect(canSendTransfer('PENDING')).toBe(false);
    });

    it('«Принять» — только из «В пути»', () => {
        expect(BACKEND_STATUSES.filter(canCompleteTransfer)).toEqual(['SHIPPED']);
    });

    it('«Вернуть на склад» — из «В пути» и после приёмки', () => {
        expect(BACKEND_STATUSES.filter(canReturnTransfer)).toEqual(['SHIPPED', 'DELIVERED']);
    });

    it('«Закрыть» — после возврата либо после приёмки', () => {
        expect(BACKEND_STATUSES.filter(canCloseTransfer)).toEqual(['DELIVERED', 'RETURNED']);
    });

    it('«Создать поставку у Натали» — пока переезд не принят, включая «В пути»', () => {
        // Поставку у ФФ заводят ЗАРАНЕЕ, до отправки машины, а уехавший переезд
        // ещё не оприходован — приход у ФФ заводит именно эта поставка. После
        // DELIVERED товар уже принят, RETURNED/CLOSED/CANCELLED — терминалы.
        expect(BACKEND_STATUSES.filter(canPushTransferToFf))
            .toEqual(['PENDING', 'IN_PROGRESS', 'READY', 'VEHICLE_ASSIGNED', 'SHIPPED']);
    });

    it('терминальные статусы не предлагают ничего', () => {
        for (const status of ['CLOSED', 'CANCELLED']) {
            expect(canEditTransfer(status)).toBe(false);
            expect(canMarkTransferReady(status)).toBe(false);
            expect(canAssignTransferVehicle(status)).toBe(false);
            expect(canSendTransfer(status)).toBe(false);
            expect(canCompleteTransfer(status)).toBe(false);
            expect(canReturnTransfer(status)).toBe(false);
            expect(canCloseTransfer(status)).toBe(false);
            expect(canPushTransferToFf(status)).toBe(false);
        }
    });

    it('неизвестный статус ничего не разрешает', () => {
        // Чужой код в поле не должен открывать действие «на всякий случай».
        expect(canEditTransfer('WAT')).toBe(false);
        expect(canSendTransfer('WAT')).toBe(false);
        expect(canCloseTransfer('WAT')).toBe(false);
        expect(canPushTransferToFf('WAT')).toBe(false);
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

describe('прогресс приёмки', () => {
    it('у уехавшего показываем «принято X из Y»', () => {
        const t = makeTransfer({ status: 'SHIPPED', units_total: 500, received_units: 120 });
        expect(transferReceiveProgress(t)).toEqual({ received: 120, total: 500 });
    });

    it('поля ещё нет (окно деплоя) — ноль принятых, а не падение', () => {
        const t = makeTransfer({ status: 'SHIPPED', units_total: 500 });
        expect(transferReceiveProgress(t)).toEqual({ received: 0, total: 500 });
    });

    it('до отгрузки и после приёмки прогресса нет', () => {
        // До SHIPPED он всегда нулевой («принято 0 из 500» на несобранном
        // переезде читается как потерянный товар), после — равен единица-в-единицу
        // и его место занимает сам статус.
        for (const status of ['PENDING', 'IN_PROGRESS', 'READY', 'VEHICLE_ASSIGNED', 'DELIVERED', 'RETURNED', 'CLOSED', 'CANCELLED'] as const) {
            expect(transferReceiveProgress(makeTransfer({ status, units_total: 500, received_units: 500 }))).toBeNull();
        }
    });

    it('пустой переезд не делит на ноль', () => {
        expect(transferReceiveProgress(makeTransfer({ status: 'SHIPPED', units_total: 0 }))).toBeNull();
    });
});

describe('«висит собранным N дней»', () => {
    const daysAgo = (n: number) => {
        const d = new Date();
        d.setDate(d.getDate() - n);
        return d.toISOString().slice(0, 10);
    };

    it('считает от даты готовности, а не от создания', () => {
        const t = makeTransfer({ status: 'READY', actual_ready_date: daysAgo(3), created_at: daysAgo(30) });
        expect(transferDaysStuck(t)).toBe(3);
    });

    it('машина назначена — всё ещё висит: он не уехал', () => {
        const t = makeTransfer({ status: 'VEHICLE_ASSIGNED', actual_ready_date: daysAgo(5) });
        expect(transferDaysStuck(t)).toBe(5);
    });

    it('уехавший и несобранный не висят', () => {
        expect(transferDaysStuck(makeTransfer({ status: 'SHIPPED', actual_ready_date: daysAgo(9) }))).toBeNull();
        expect(transferDaysStuck(makeTransfer({ status: 'PENDING' }))).toBeNull();
    });

    it('нет даты или дата битая — null, а не NaN дней', () => {
        expect(transferDaysStuck(makeTransfer({ status: 'READY' }))).toBeNull();
        expect(transferDaysStuck(makeTransfer({ status: 'READY', actual_ready_date: 'вчера' }))).toBeNull();
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

// ─── Связки с заявками ФФ и валидация правки черновика ──────────────────────

function makeFfLink(patch: Partial<TransferFfLink> = {}): TransferFfLink {
    return {
        id: 1,
        warehouse_id: 2,
        side: 'source',
        number: 'PVB-124',
        external_id: 'ext-1',
        kind: 'assembly',
        status: 'В работе',
        stage_title: 'Сборка',
        total_qty: 500,
        external_created_at: '2026-07-30',
        ...patch,
    };
}

describe('splitTransferFfLinks', () => {
    it('раскладывает по сторонам и держит порядок бэкенда', () => {
        const links = [
            makeFfLink({ id: 1, side: 'source', number: 'PVB-1' }),
            makeFfLink({ id: 2, side: 'dest', number: 'IN-1', kind: 'inbound' }),
            makeFfLink({ id: 3, side: 'source', number: 'PVB-2' }),
        ];
        const { source, dest } = splitTransferFfLinks(links);
        expect(source.map(l => l.number)).toEqual(['PVB-1', 'PVB-2']);
        expect(dest.map(l => l.number)).toEqual(['IN-1']);
    });

    it('на одну сторону может прийти НЕСКОЛЬКО заявок (Натали: короба и штучные — разные документы)', () => {
        const { dest } = splitTransferFfLinks([
            makeFfLink({ id: 10, side: 'dest', number: 'PVB-133 короба' }),
            makeFfLink({ id: 11, side: 'dest', number: 'PVB-134 штучные' }),
        ]);
        expect(dest).toHaveLength(2);
    });

    it('нет поля / пустой список — обе стороны пусты, а не падение', () => {
        expect(splitTransferFfLinks(undefined)).toEqual({ source: [], dest: [] });
        expect(splitTransferFfLinks(null)).toEqual({ source: [], dest: [] });
        expect(splitTransferFfLinks([])).toEqual({ source: [], dest: [] });
    });

    it('неизвестная сторона не теряется — уходит в отгрузку', () => {
        const { source, dest } = splitTransferFfLinks([
            makeFfLink({ id: 5, side: 'wat' as unknown as TransferFfLink['side'] }),
        ]);
        expect(source).toHaveLength(1);
        expect(dest).toHaveLength(0);
    });
});

describe('filterTransferFfLinks', () => {
    const links = [
        makeFfLink({ id: 1, number: 'PVB-124', stage_title: 'Сборка', external_id: 'aaa' }),
        makeFfLink({ id: 2, number: null, stage_title: 'Приёмка', external_id: 'zzz-77', status: 'Закрыта' }),
    ];

    it('пустой запрос — весь список', () => {
        expect(filterTransferFfLinks(links, '   ')).toHaveLength(2);
    });

    it('ищет по номеру, стадии, статусу и внешнему id, регистронезависимо', () => {
        expect(filterTransferFfLinks(links, 'pvb-124').map(l => l.id)).toEqual([1]);
        expect(filterTransferFfLinks(links, 'приёмка').map(l => l.id)).toEqual([2]);
        expect(filterTransferFfLinks(links, 'закрыт').map(l => l.id)).toEqual([2]);
        expect(filterTransferFfLinks(links, 'ZZZ').map(l => l.id)).toEqual([2]);
    });

    it('заявка без номера не роняет поиск', () => {
        expect(filterTransferFfLinks(links, 'нет-такого')).toEqual([]);
    });
});

describe('ffLinkLabel / ffLinkStage', () => {
    it('без номера показываем внешний id, а не внутренний', () => {
        expect(ffLinkLabel(makeFfLink())).toBe('PVB-124');
        expect(ffLinkLabel(makeFfLink({ number: null, external_id: 'ext-9' }))).toBe('ext-9');
        expect(ffLinkLabel({ number: null, external_id: '' })).toBe('—');
    });

    it('стадия: title → status → прочерк', () => {
        expect(ffLinkStage(makeFfLink())).toBe('Сборка');
        expect(ffLinkStage(makeFfLink({ stage_title: null }))).toBe('В работе');
        expect(ffLinkStage(makeFfLink({ stage_title: null, status: null }))).toBe('—');
    });
});

describe('transferEditError', () => {
    it('одинаковые склады ловим на клиенте, не дожидаясь 400', () => {
        expect(transferEditError({ fromWarehouseId: 2, toWarehouseId: 2, itemCount: 3 }))
            .toBe('Склад-источник и склад-получатель должны различаться');
    });

    it('пустой состав отправлять нечем', () => {
        expect(transferEditError({ fromWarehouseId: 2, toWarehouseId: 14, itemCount: 0 })).toMatch(/позици/i);
    });

    it('не выбранный склад — своя подсказка на каждый конец маршрута', () => {
        expect(transferEditError({ fromWarehouseId: '', toWarehouseId: 14, itemCount: 1 })).toMatch(/источник/i);
        expect(transferEditError({ fromWarehouseId: 2, toWarehouseId: '', itemCount: 1 })).toMatch(/получател/i);
    });

    it('корректная форма — ошибки нет', () => {
        expect(transferEditError({ fromWarehouseId: 2, toWarehouseId: 14, itemCount: 1 })).toBeNull();
    });
});
