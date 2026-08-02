import type { GazelkaConfig, StockTransfer, StockTransferStatus, TransferFfLink, TransferFfSide } from '@/types/api';
import { formatNumber, pluralRu } from '@/lib/utils';

/**
 * Общий словарь и хелперы перемещения (переезда между нашими складами).
 *
 * Переезд — ЗЕРКАЛО заявки на сборку: те же ступени, та же машина внутри
 * цепочки, поэтому и словарь строится от `ASSEMBLY_STATUS_MAP`
 * (`src/lib/assembly-status.ts`).
 *
 * 🔴 ЦВЕТА — 1:1 с заявкой, без исключений: одинаковая ступень обязана
 * выглядеть одинаково на обоих экранах.
 *
 * ПОДПИСИ — тоже 1:1, кроме четырёх мест, где заявочная формулировка на
 * переезде прямо врёт (каждое объяснено построчно ниже).
 */
export const TRANSFER_STATUS_MAP: Record<StockTransferStatus, { label: string; className: string }> = {
    // ≠ заявки («В сборке»). У заявки PENDING — legacy-ступень, схлопнутая на
    // IN_PROGRESS одной подписью. У переезда PENDING живой и означает ДРУГОЕ:
    // «создан, работа не начата». Одинаковая подпись на двух соседних
    // статусах сделала бы бейдж и фильтр неразличимыми.
    PENDING:          { label: 'Создан',            className: 'badge-info' },
    IN_PROGRESS:      { label: 'В сборке',          className: 'badge-info' },
    READY:            { label: 'Готово',            className: 'badge-success' },
    VEHICLE_ASSIGNED: { label: 'Машина назначена',  className: 'badge-info' },
    // ≠ заявки («Отгружена»). Во-первых род: заявка отгружена, а переезд —
    // мужской. Во-вторых смысл: «отгрузка» между СВОИМИ складами читается как
    // отгрузка наружу, тогда как товар физически едет между нашими складами.
    // Это состояние весь склад и Лист логиста уже называют «В пути» — и так же
    // назывался прежний статус IN_TRANSIT, который сюда и переехал.
    SHIPPED:          { label: 'В пути',            className: 'badge-success' },
    // ≠ заявки («Принята WB»). Никакого WB здесь нет — принимает НАШ
    // склад-получатель, и «Принято на складе» говорит ровно это.
    DELIVERED:        { label: 'Принято на складе', className: 'badge-success' },
    RETURNED:         { label: 'Возврат на склад',  className: 'badge-warning' },
    CLOSED:           { label: 'Закрыт',            className: 'badge-warning' },
    // ≠ заявки («Отменена») — только род: переезд отменён.
    CANCELLED:        { label: 'Отменён',           className: 'badge-secondary' },
};

/** Подпись статуса; неизвестный статус отдаём как есть, а не пустой строкой. */
export function transferStatusLabel(status: StockTransferStatus | string): string {
    return TRANSFER_STATUS_MAP[status as StockTransferStatus]?.label ?? String(status);
}

export function transferStatusClass(status: StockTransferStatus | string): string {
    return TRANSFER_STATUS_MAP[status as StockTransferStatus]?.className ?? 'badge-secondary';
}

// ─── Гейты действий по статусу ──────────────────────────────────────────────
// ЕДИНЫЙ источник «что сейчас можно»: карточка, вкладка «Перемещения», Лист
// логиста и вкладка склада спрашивают этот файл, а не сравнивают статус
// строкой у себя. Разъедься гейты — часть экранов показала бы кнопку, которая
// вернёт 400 («мёртвая кнопка»), а часть спрятала бы живое действие.
//
// Набор зеркалит серверный TRANSFER_TRANSITIONS (backend/models/warehouse.py).
// Он намеренно НЕ пытается предсказать 400 по другим причинам (пустой состав,
// нехватка стока) — это забота бэкенда, текст ошибки показываем как есть.

/** Правка и удаление черновика: до отгрузки сток ещё не тронут. */
export const TRANSFER_EDITABLE_STATUSES: ReadonlySet<StockTransferStatus> =
    new Set<StockTransferStatus>(['PENDING', 'IN_PROGRESS', 'READY']);

/** Статусы, из которых переезд ещё может уехать (кнопка «Отправить»). */
export const TRANSFER_SENDABLE_STATUSES: ReadonlySet<StockTransferStatus> =
    new Set<StockTransferStatus>(['READY', 'VEHICLE_ASSIGNED']);

/**
 * Правка состава и маршрута. Раньше это был один DRAFT, теперь три ступени:
 * ФФ может собирать переезд (IN_PROGRESS) и уже собрать его (READY) — состав
 * при этом ещё живой, потому что сток списывается только на отгрузке.
 */
export function canEditTransfer(status: StockTransferStatus | string): boolean {
    return TRANSFER_EDITABLE_STATUSES.has(status as StockTransferStatus);
}

/**
 * «Готов» — отметить сборку переезда законченной.
 *
 * RETURNED тоже здесь: это ПЕРЕОТПРАВКА вернувшегося переезда. Бэкенд её
 * разрешает (`TRANSFER_TRANSITIONS`) и прямо обещает в докстринге
 * `return_transfer` («либо переотправляют, либо закрывают»), но без этой ветки
 * вернувшийся переезд застревал в интерфейсе намертво: ни переотправить, ни
 * отредактировать (правка живёт в PENDING/IN_PROGRESS/READY), оставалось только
 * закрыть. Сток при переходе не двигается — он уже возвращён на склад-источник
 * самим возвратом.
 */
export function canMarkTransferReady(status: StockTransferStatus | string): boolean {
    return status === 'PENDING' || status === 'IN_PROGRESS' || status === 'RETURNED';
}

/**
 * Назначить/изменить машину. Из READY — назначение, из VEHICLE_ASSIGNED —
 * замена реквизитов уже назначенной. В PENDING/IN_PROGRESS машины нет: пока
 * ФФ не собрал, везти нечего.
 */
export function canAssignTransferVehicle(status: StockTransferStatus | string): boolean {
    return status === 'READY' || status === 'VEHICLE_ASSIGNED';
}

/** Снять машину — только когда она назначена; переезд вернётся в READY. */
export function canUnassignTransferVehicle(status: StockTransferStatus | string): boolean {
    return status === 'VEHICLE_ASSIGNED';
}

/**
 * СТАТУСНЫЙ гейт «Отправить» — зеркало серверного `_TRANSFER_SEND_FROM`.
 * Сам по себе кнопку не включает: у READY нужна ещё логистика, см.
 * `canSendTransferNow`. Оставлен отдельно, потому что это разные вопросы —
 * «стадия подходит» и «документ оформлен».
 */
export function canSendTransfer(status: StockTransferStatus | string): boolean {
    return TRANSFER_SENDABLE_STATUSES.has(status as StockTransferStatus);
}

/**
 * Оформлена ли логистика — зеркало серверного `has_logistics`
 * (`warehouse_outbound._create_transfer_pickup`). Ровно тот же набор полей:
 * разъедься они, фронт показал бы «Отправить» там, где бэкенд ответит 400, либо
 * спрятал бы кнопку у переезда, который бэкенд отправить готов.
 *
 * `logistics_by_warehouse` входит сюда осознанно: «логистику оказывает склад
 * забора» — это ОФОРМЛЕННАЯ логистика (перевозчик берётся из контрагента
 * склада), просто без госномера.
 */
export function transferHasLogistics(t: StockTransfer): boolean {
    return !!(
        t.vehicle_assigned_at
        || t.vehicle_info
        || t.vehicle_brand
        || t.driver_phone
        || t.counterparty_id
        || toMoney(t.pickup_cost) !== null
        || t.pickup_date
        || t.delivery_date
        || t.logistics_by_warehouse
    );
}

/**
 * Можно ли жать «Отправить» ПРЯМО СЕЙЧАС.
 *
 * 🔴 Канон юзера 01.08.2026: «сначала же назначить машину, а потом либо ФФ
 * закрывает заявку и она отправляется автоматически, либо сам логист отправит».
 * Раньше кнопка висела на голом READY (TR-32: машины нет, стоимости нет) —
 * нажатие списывало сток со склада-источника, а забор при этом НЕ создавался
 * (гард `has_logistics`), то есть переезд уезжал мимо оплат и мимо отчёта
 * логистики, и добрать его задним числом было нечем.
 *
 * VEHICLE_ASSIGNED проходит всегда — машина там по определению. У READY
 * требуем хотя бы признак оформления: это сохраняет живые сценарии «везёт сам
 * склад» и «машины нет, но стоимость известна», не заставляя логиста выдумывать
 * госномер.
 */
/**
 * Перекладка ВНУТРИ одного склада: источник и получатель совпадают.
 *
 * 🔴 Канон юзера 02.08.2026: «когда внутри склада — за него не нужно платить и
 * машину назначать, это просто внутренний процесс». Товар физически никуда не
 * едет (например, годное пометили браком и перенесли в другую ячейку), везти
 * его некому и платить не за что.
 *
 * Брак сам по себе признаком НЕ является — юзер это отдельно уточнил: переезд
 * «склад → склад» остаётся обычной перевозкой, даже если едет брак. Везли-то
 * его машиной, и деньги за рейс реальные.
 */
export function isInternalTransfer(t: StockTransfer): boolean {
    return t.from_warehouse_id === t.to_warehouse_id;
}

export function canSendTransferNow(t: StockTransfer): boolean {
    if (!canSendTransfer(t.status)) return false;
    // Внутренняя перекладка уезжает без оформления: требовать машину значило бы
    // заставить кладовщика выдумывать перевозчика для товара, который не выехал
    // за ворота.
    if (isInternalTransfer(t)) return true;
    if (t.status === 'VEHICLE_ASSIGNED') return true;
    return transferHasLogistics(t);
}

/**
 * Подсказка, почему «Отправить» недоступна. null — доступна либо неприменима
 * (не тот статус). Пустая задизейбленная кнопка без объяснения читается как
 * поломка интерфейса.
 */
export function transferSendBlockReason(t: StockTransfer): string | null {
    if (!canSendTransfer(t.status)) return null;
    if (canSendTransferNow(t)) return null;
    return 'Сначала назначьте машину — иначе переезд уедет мимо оплат и отчёта логистики';
}

/**
 * «Указать перевозчика и стоимость» на УЖЕ УЕХАВШЕМ переезде.
 *
 * 🔴 Это НЕ `canAssignTransferVehicle`: там назначение машины со сменой статуса
 * READY → VEHICLE_ASSIGNED (зеркало `TRANSFER_TRANSITIONS`), а здесь статус не
 * меняется и сток не двигается — дописывается только логистика и создаётся
 * забор. Единственный способ дать деньги старым переездам (TR-1…TR-31 уехали до
 * появления машины на перемещении: у них нет ни забора, ни строки в отчёте
 * «Переезды», ни строки в «Оплатах»).
 *
 * CANCELLED сюда не входит: отменённый переезд никто не вёз.
 */
export const TRANSFER_LOGISTICS_EDITABLE_STATUSES: ReadonlySet<StockTransferStatus> =
    new Set<StockTransferStatus>(['SHIPPED', 'DELIVERED', 'RETURNED', 'CLOSED']);

export function canSetTransferLogistics(status: StockTransferStatus | string): boolean {
    return TRANSFER_LOGISTICS_EDITABLE_STATUSES.has(status as StockTransferStatus);
}

/**
 * Переезд можно отдать Газельке: склад-ИСТОЧНИК обслуживается ключом
 * интеграции, стадия — до отгрузки, и агрегатор его ещё не ведёт.
 *
 * Зеркало `isGazelkaEligible` заявки, но гейт по `from_warehouse_id`: у заявки
 * это `warehouse_id` (откуда забирают), у переезда — тот конец маршрута, с
 * которого груз увозят.
 */
export const TRANSFER_GAZELKA_STATUSES: ReadonlySet<StockTransferStatus> =
    new Set<StockTransferStatus>(['READY', 'VEHICLE_ASSIGNED']);

export function canPushTransferToGazelka(
    t: StockTransfer,
    gazelkaWarehouseIds: number[] | null | undefined,
): boolean {
    if (t.via_gazelka) return false;
    if (!TRANSFER_GAZELKA_STATUSES.has(t.status)) return false;
    return (gazelkaWarehouseIds ?? []).includes(t.from_warehouse_id);
}

/**
 * Склады-ИСТОЧНИКИ, с которых Газельке можно отдавать груз, — из конфига
 * интеграции для `canPushTransferToGazelka`.
 *
 * Фолбэк на одиночный `warehouse_id` не косметика: список `warehouse_ids`
 * приехал вместе с переездом, и в окно деплоя (фронт новый, бэкенд ещё старый)
 * поля в ответе нет — без фолбэка кнопка «Отправить в Газельку» молча пропала
 * бы у ВСЕХ переездов, и это читалось бы как «интеграция сломалась».
 * Не настроенная Газелька — пустой список, а не «все склады».
 */
export function gazelkaSourceWarehouseIds(cfg: GazelkaConfig | null | undefined): number[] {
    if (!cfg?.configured) return [];
    if (cfg.warehouse_ids?.length) return cfg.warehouse_ids;
    return cfg.warehouse_id != null ? [cfg.warehouse_id] : [];
}

/** «Принять» — оприходовать на складе-получателе. */
export function canCompleteTransfer(status: StockTransferStatus | string): boolean {
    return status === 'SHIPPED';
}

/** «Вернуть на склад» — получатель не принял, товар едет обратно на источник. */
export function canReturnTransfer(status: StockTransferStatus | string): boolean {
    return status === 'SHIPPED' || status === 'DELIVERED';
}

/** «Закрыть» — терминал: после возврата либо после нормальной приёмки. */
export function canCloseTransfer(status: StockTransferStatus | string): boolean {
    return status === 'RETURNED' || status === 'DELIVERED';
}

/**
 * «Создать поставку у Натали» — завести приёмку (PVB-…) в WMS ФФ из состава
 * переезда. Гейт по статусу; про склад-получателя спрашивает сам экран
 * (портал migfull настроен на ОДИН склад, статусы про это ничего не знают).
 *
 * Разрешено до фактической приёмки включительно с «в пути»: поставку у ФФ
 * обычно заводят заранее, ещё до отправки машины, а уехавший переезд ещё не
 * оприходован — приход у ФФ заводит именно эта поставка. После DELIVERED
 * товар уже оприходован (бэкенд отвечает блокировкой), а RETURNED / CLOSED /
 * CANCELLED — терминалы, в которых везти к Натали нечего.
 */
export const TRANSFER_FF_PUSHABLE_STATUSES: ReadonlySet<StockTransferStatus> =
    new Set<StockTransferStatus>(['PENDING', 'IN_PROGRESS', 'READY', 'VEHICLE_ASSIGNED', 'SHIPPED']);

export function canPushTransferToFf(status: StockTransferStatus | string): boolean {
    return TRANSFER_FF_PUSHABLE_STATUSES.has(status as StockTransferStatus);
}

/**
 * «Создать отгрузку у Натали» — завести заявку на отгрузку (ЗАК-…) в WMS ФФ,
 * когда склад Натали у переезда ИСТОЧНИК (кейс TR-33 «натали → фф питер
 * Газпром»). Зеркало `canPushTransferToFf`, но БЕЗ «в пути».
 *
 * Отличие ровно одно и оно осознанное: у уехавшего переезда (SHIPPED) товар
 * Натали уже отдала, сток списан — новая заявка на отгрузку означала бы
 * «соберите и выдайте ещё раз», и ФФ отгрузил бы те же единицы дважды. У
 * приёмки такой опасности нет (приход у ФФ как раз и заводится этой поставкой),
 * поэтому там «в пути» разрешён. Бэкенд отвечает 400 на те же статусы.
 */
export const TRANSFER_FF_SHIPMENT_PUSHABLE_STATUSES: ReadonlySet<StockTransferStatus> =
    new Set<StockTransferStatus>(['PENDING', 'IN_PROGRESS', 'READY', 'VEHICLE_ASSIGNED']);

export function canPushTransferShipmentToFf(status: StockTransferStatus | string): boolean {
    return canPushTransferToFf(status)
        && TRANSFER_FF_SHIPMENT_PUSHABLE_STATUSES.has(status as StockTransferStatus);
}

/**
 * Отмена (DELETE = CANCELLED + soft-delete). Шире правки: машину сняли не
 * обязательно — отменить назначенный переезд бэкенд разрешает. А вот
 * отгруженный уже нет: сток списан, отмена его не вернёт (для этого «Вернуть
 * на склад»).
 */
export function canCancelTransfer(status: StockTransferStatus | string): boolean {
    return canEditTransfer(status) || status === 'VEHICLE_ASSIGNED';
}

/**
 * Прогресс приёмки «принято X из Y».
 *
 * Считает бэкенд (`received_units`) — и в списке, и в карточке: один GROUP BY
 * по журналу движений на пачку, запроса на строку нет.
 *
 * Показываем ТОЛЬКО у SHIPPED, и это не косметика: у DELIVERED прогресс равен
 * единица-в-единицу (там его место занимает сам статус), а до отгрузки он
 * всегда нулевой — «принято 0 из 500» на несобранном переезде читалось бы как
 * потерянный товар. null — показывать нечего.
 */
export function transferReceiveProgress(t: StockTransfer): { received: number; total: number } | null {
    if (t.status !== 'SHIPPED') return null;
    const total = t.units_total ?? 0;
    if (total <= 0) return null;
    const received = t.received_units ?? 0;
    return { received, total };
}

/**
 * Сколько дней переезд ВИСИТ собранным, но не уехал. Полный аналог `daysStuck`
 * для заявки (Лист логиста): считаем от `actual_ready_date` (полночь).
 * null — не применимо (ещё не собран, уже уехал либо даты нет).
 */
export function transferDaysStuck(t: StockTransfer): number | null {
    if (t.status !== 'READY' && t.status !== 'VEHICLE_ASSIGNED') return null;
    if (!t.actual_ready_date) return null;
    const ready = new Date(`${t.actual_ready_date}T00:00:00`);
    if (Number.isNaN(ready.getTime())) return null;
    const days = Math.floor((Date.now() - ready.getTime()) / 86_400_000);
    return days >= 0 ? days : null;
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

/**
 * Машина назначена — по ФАКТУ заполнения реквизитов, а не по статусу.
 *
 * Ступень VEHICLE_ASSIGNED теперь есть, но статус и реквизиты — разные вопросы:
 * у отгруженного (SHIPPED) и принятого (DELIVERED) переезда машина тоже была,
 * и карточка обязана её показать. Плюс старые записи до бэкфилла несут только
 * `vehicle_info` без `vehicle_assigned_at`.
 * Для «можно ли сейчас назначить/снять» — canAssignTransferVehicle().
 */
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
 * Общий вес груза, кг. Отдельного поля у переезда НЕТ (в отличие от заявки с
 * её `total_weight_kg`): `pallet_weight_kg` — вес ОДНОЙ единицы, поэтому
 * считаем сами.
 *
 * Нет любого из двух множителей → null («не взвешивали»), а не 0: ноль в
 * колонке веса читается как «взвесили и получилось ничего».
 */
export function transferTotalWeight(t: StockTransfer): number | null {
    const one = toMoney(t.pallet_weight_kg);
    if (one === null || t.pallets_count == null) return null;
    return one * t.pallets_count;
}

/**
 * ФАКТ ₽ за одну единицу груза (паллету либо короб) — аналог «₽/палета»
 * заявки, но у переезда это НЕ прогноз: делим уже известную стоимость забора
 * на количество единиц.
 *
 * null — делить нечего (стоимости нет) либо не на что (единицы не заводили).
 * Ноль единиц отдельно: деление дало бы Infinity, и карточка показала бы «∞ ₽/пал».
 */
export function transferCostPerUnit(t: StockTransfer): number | null {
    const cost = toMoney(t.pickup_cost);
    if (cost === null) return null;
    if (!t.pallets_count || t.pallets_count <= 0) return null;
    return cost / t.pallets_count;
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

// ─── Связки с заявками ФФ ───────────────────────────────────────────────────
// Одна сторона маршрута может нести НЕСКОЛЬКО заявок ФФ: у Натали короба и
// штучные приезжают отдельными документами. Поэтому всюду списки, а не «первая».

/** Подпись заявки ФФ: номер, а без него — внешний id (не «Заявка #внутренний_id»). */
export function ffLinkLabel(link: Pick<TransferFfLink, 'number' | 'external_id'>): string {
    return link.number || link.external_id || '—';
}

/** Стадия заявки ФФ одной строкой: стадия → статус → прочерк. */
export function ffLinkStage(link: Pick<TransferFfLink, 'stage_title' | 'status'>): string {
    return link.stage_title || link.status || '—';
}

/**
 * Разложить связки по сторонам маршрута. Порядок внутри стороны сохраняем как
 * пришёл (бэкенд отдаёт свежие сверху).
 */
export function splitTransferFfLinks(links: TransferFfLink[] | null | undefined): Record<TransferFfSide, TransferFfLink[]> {
    const out: Record<TransferFfSide, TransferFfLink[]> = { source: [], dest: [] };
    for (const link of links ?? []) {
        // Неизвестная сторона (чужой код в поле) — к отгрузке, чтобы связка не
        // исчезла с экрана: «пропала» хуже, чем «не в той подсекции».
        out[link.side === 'dest' ? 'dest' : 'source'].push(link);
    }
    return out;
}

/**
 * Клиентский поиск по кандидатам: номер, внешний id, стадия, статус.
 *
 * Дженерик, а не `TransferFfLink[]`: та же модалка ищет и по кандидатам СБОРКИ
 * (`AssemblyFfCandidate` — тот же тип без `side`). Ограничение по Pick честно
 * говорит, что читаем ровно четыре поля, а возврат `T[]` сохраняет исходный тип
 * у всех вызывающих.
 */
export function filterTransferFfLinks<
    T extends Pick<TransferFfLink, 'number' | 'external_id' | 'stage_title' | 'status'>,
>(links: T[], query: string): T[] {
    const q = query.trim().toLowerCase();
    if (!q) return links;
    return links.filter(l =>
        (l.number ?? '').toLowerCase().includes(q)
        || l.external_id.toLowerCase().includes(q)
        || (l.stage_title ?? '').toLowerCase().includes(q)
        || (l.status ?? '').toLowerCase().includes(q));
}

// ─── Правка черновика ───────────────────────────────────────────────────────

/**
 * Клиентская валидация формы правки переезда. Проверяем ровно то, на что
 * бэкенд отвечает 400 — чтобы пользователь узнал об ошибке до сетевого вызова,
 * а не после. Возвращает текст ошибки либо null.
 */
export function transferEditError(values: {
    fromWarehouseId: number | '';
    toWarehouseId: number | '';
    itemCount: number;
}): string | null {
    if (values.fromWarehouseId === '') return 'Выберите склад-источник';
    if (values.toWarehouseId === '') return 'Выберите склад-получатель';
    if (values.fromWarehouseId === values.toWarehouseId) {
        return 'Склад-источник и склад-получатель должны различаться';
    }
    if (values.itemCount === 0) return 'Добавьте хотя бы одну позицию — пустой состав отправлять нечем';
    return null;
}
