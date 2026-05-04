# Vehicle Qty Drift Confirm — Design

## Архитектурная картина

```
┌─────────────────────────────────────────────────────────────────┐
│  Frontend: VehicleDetailsPage                                   │
│  ┌───────────────────────────────────────┐                      │
│  │ <ItemRow qty=24 />  ← inline edit     │                      │
│  │   onBlur(32) → updateVehicleItem(...)  │                      │
│  └───────────┬───────────────────────────┘                      │
│              │                                                   │
│  ┌───────────▼───────────────────────────┐                      │
│  │ api/supply-chain.ts updateVehicleItem │                      │
│  │   PATCH ?mode=strict {qty: 32}         │                      │
│  │   on 422 → throw FactoryQtyExceeded    │                      │
│  └───────────┬───────────────────────────┘                      │
└──────────────┼───────────────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────────────┐
│  Backend: routers/supply_chain.py                                │
│  PATCH /vehicles/{no}/items/{id}                                 │
│    body: VehicleItemUpdate{qty?, mode?, ...}                     │
└──────────────┬───────────────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────────────┐
│  services/supply_chain/vehicle_delivery.py                       │
│  update_vehicle_item(...)                                        │
│    delta = new_qty - cost_item.qty                               │
│    ├─ delta > 0: _adjust_assigned_qty(fo, delta, mode)           │
│    │    └─ raises FactoryQtyExceeded if strict & exceeds         │
│    ├─ delta < 0: fo.assigned_qty += delta (max 0)                │
│    └─ delta == 0: no-op return                                   │
│    cost_item.qty = new_qty                                       │
│    recalculate_order_items + refresh_factory_order_statuses      │
│    + _invalidate_supplier_catalog                                │
└──────────────┬───────────────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────────────┐
│  services/supply_chain/factory_orders.py                         │
│  _adjust_assigned_qty(db, fo_item, delta, mode, user_name)       │
│    available = fo.qty - fo.assigned_qty                          │
│    if delta > available:                                         │
│      if mode=="strict": raise FactoryQtyExceeded(detail=...)     │
│      if mode=="extend_plan":                                     │
│        extend_by = delta - available                             │
│        fo.qty += extend_by                                       │
│        write FactoryOrderHistory(qty_extended_from_vehicle)      │
│    fo.assigned_qty += delta                                      │
│    return AdjustResult(extended_by=extend_by_or_0)               │
└──────────────────────────────────────────────────────────────────┘
```

## Backend

### Модели — без изменений

`FactoryOrder`, `FactoryOrderItem`, `FactoryOrderHistory`, `CostOrder`, `CostOrderItem` — никаких новых колонок, `qty_drift` вычисляется на лету.

### Новый exception

```python
# backend/services/supply_chain/factory_orders.py
class FactoryQtyExceeded(Exception):
    """Raised by _adjust_assigned_qty when delta exceeds available and mode=strict."""

    def __init__(self, detail: dict):
        self.detail = detail
        super().__init__(detail.get("error", "exceeds_factory_qty"))
```

Маппится в роутере на HTTPException 422 с `detail` как dict (FastAPI поддерживает structured detail).

### Единая utility `_adjust_assigned_qty`

```python
# backend/services/supply_chain/factory_orders.py

from dataclasses import dataclass
from typing import Literal

@dataclass
class AdjustResult:
    new_assigned_qty: int
    extended_by: int  # 0 если план не расширялся
    history_entry_id: int | None  # id записи FactoryOrderHistory если extend_plan


async def _adjust_assigned_qty(
    db: AsyncSession,
    fo_item: FactoryOrderItem,
    delta: int,
    mode: Literal["strict", "extend_plan"],
    user_name: str | None,
    *,
    cost_order_no: str | None = None,  # для history details
) -> AdjustResult:
    """
    Adjust FactoryOrderItem.assigned_qty by delta.

    - delta > 0: validate against available; raise FactoryQtyExceeded if strict and exceeds
    - delta > 0 + extend_plan + exceeds: bump fo.qty by (delta - available) and write history
    - delta < 0: decrement assigned_qty (clamp at 0)
    - delta == 0: no-op

    Does NOT commit — caller controls transaction.
    Does NOT call refresh_factory_order_statuses — caller responsible.
    """
    if delta == 0:
        return AdjustResult(new_assigned_qty=fo_item.assigned_qty, extended_by=0, history_entry_id=None)

    if delta < 0:
        fo_item.assigned_qty = max(0, fo_item.assigned_qty + delta)
        return AdjustResult(new_assigned_qty=fo_item.assigned_qty, extended_by=0, history_entry_id=None)

    # delta > 0
    available = fo_item.qty - fo_item.assigned_qty
    extended_by = 0
    history_id = None

    if delta > available:
        if mode == "strict":
            # load fo for error detail
            fo_result = await db.execute(
                select(FactoryOrder).where(
                    FactoryOrder.id == fo_item.factory_order_id,
                    FactoryOrder.project_id == fo_item.project_id,
                    FactoryOrder.is_deleted == False,
                )
            )
            fo = fo_result.scalar_one_or_none()
            raise FactoryQtyExceeded(detail={
                "error": "exceeds_factory_qty",
                "fo_id": fo_item.factory_order_id,
                "fo_number": fo.order_number if fo else None,
                "foi_id": fo_item.id,
                "barcode": fo_item.barcode,
                "subject": fo_item.subject,
                "fo_qty": fo_item.qty,
                "fo_assigned": fo_item.assigned_qty,
                "available": available,
                "attempted_delta": delta,
                "in_mix_group": fo_item.mix_group_id is not None,
                "mix_group_id": fo_item.mix_group_id,
            })
        # mode == "extend_plan"
        extended_by = delta - available
        fo_item.qty += extended_by
        history = FactoryOrderHistory(
            project_id=fo_item.project_id,
            factory_order_id=fo_item.factory_order_id,
            event_type="qty_extended_from_vehicle",
            details=(
                f"+{extended_by} шт. в позицию {fo_item.barcode}"  # noqa: RUF001
                + (f" (машина {cost_order_no})" if cost_order_no else "")
            ),
            changed_at=utcnow(),
            changed_by=user_name,
        )
        db.add(history)
        await db.flush()
        history_id = history.id

    fo_item.assigned_qty += delta
    return AdjustResult(
        new_assigned_qty=fo_item.assigned_qty,
        extended_by=extended_by,
        history_entry_id=history_id,
    )
```

### `update_vehicle_item` — расширение

```python
# backend/services/supply_chain/vehicle_delivery.py

async def update_vehicle_item(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    item_id: int,
    new_qty: int | None = None,
    box_size_override: str | None = None,
    pcs_per_box_override: int | None = None,
    box_detail_override: list[int] | None = None,
    set_box_size_override: bool = False,
    set_pcs_per_box_override: bool = False,
    set_box_detail_override: bool = False,
    mode: Literal["strict", "extend_plan"] = "strict",
    user_name: str | None = None,
) -> dict:
    cost_item = ... # load с join CostOrder (как сейчас)

    affected_fo_ids: set[int] = set()
    qty_changed = new_qty is not None and new_qty != cost_item.qty

    if qty_changed and cost_item.factory_order_item_id:
        fo_item = await _load_fo_item(db, project_id, cost_item.factory_order_item_id)
        if fo_item:
            delta = new_qty - cost_item.qty
            # raises FactoryQtyExceeded if strict & exceeds
            adjust_result = await _adjust_assigned_qty(
                db, fo_item, delta, mode, user_name,
                cost_order_no=order_no,
            )
            affected_fo_ids.add(fo_item.factory_order_id)

    if new_qty is not None:
        cost_item.qty = new_qty
    # ... existing override handling unchanged

    # No-op shortcut: nothing changed at all
    if not qty_changed and not (set_box_size_override or set_pcs_per_box_override or set_box_detail_override):
        return {"ok": True, "item_id": item_id, "noop": True}

    await db.commit()

    if qty_changed or set_box_size_override or set_pcs_per_box_override:
        from backend.services.cost.items import recalculate_order_items
        await recalculate_order_items(db, project_id, order_no)

    if affected_fo_ids:
        await refresh_factory_order_statuses(db, project_id, affected_fo_ids)

    await _invalidate_supplier_catalog(project_id)
    return {"ok": True, "item_id": item_id, "extended_by": adjust_result.extended_by if qty_changed else 0}
```

### `_enrich_vehicle` — добавление `qty_drift`

```python
# в _enrich_vehicle, после batch-load fo_item_map:

# Compute qty_drift per cost_item:
# qty_drift = max(0, sum(active CostOrderItem.qty for same foi_id) - fo_item.qty)
# но per-row: cost_item.qty - (cost_item.qty / sum) * fo_item.qty
# Простая модель: drift только если sum > fo.qty, и распределяется пропорционально.
# Реальная: qty_drift на этой строке = cost_item.qty -
#                                       max(0, fo_item.qty - sum_other_vehicles_qty)
# где sum_other_vehicles_qty = sum(CostOrderItem.qty WHERE foi_id=X AND id != cost_item.id AND is_deleted=False)

if fo_item_ids:
    sum_by_foi_result = await db.execute(
        select(
            CostOrderItem.factory_order_item_id,
            func.coalesce(func.sum(CostOrderItem.qty), 0),
        )
        .where(
            CostOrderItem.factory_order_item_id.in_(fo_item_ids),
            CostOrderItem.project_id == project_id,
            CostOrderItem.is_deleted == False,
        )
        .group_by(CostOrderItem.factory_order_item_id)
    )
    sum_by_foi = {row[0]: row[1] for row in sum_by_foi_result}
else:
    sum_by_foi = {}

# в цикле по vehicle.items:
qty_drift = None
if cost_item.factory_order_item_id:
    fo_data = fo_item_map.get(cost_item.factory_order_item_id)
    sum_total = sum_by_foi.get(cost_item.factory_order_item_id, 0)
    fo_qty = ...  # из fo_item_map (надо добавить fo_qty в SELECT)
    if fo_qty is not None and sum_total > fo_qty:
        # на этой позиции drift = доля от общего превышения
        # упрощение: drift = max(0, cost_item.qty - (fo_qty - (sum_total - cost_item.qty)))
        other_vehicles_qty = sum_total - cost_item.qty
        room_for_this = max(0, fo_qty - other_vehicles_qty)
        qty_drift = max(0, cost_item.qty - room_for_this)
```

`VehicleItemSchema` получает поле `qty_drift: int | None = None`.

### Schemas

```python
# backend/schemas/supply_chain.py

class VehicleItemUpdate(BaseModel):
    qty: int | None = None
    box_size_override: str | None = None
    pcs_per_box_override: int | None = None
    box_detail_override: list[int] | None = None
    mode: Literal["strict", "extend_plan"] = "strict"


class VehicleItemSchema(BaseModel):
    # ... existing fields
    qty_drift: int | None = None  # >0 если в БД есть рассинхрон с фабричным заказом
```

### Router

```python
# backend/routers/supply_chain.py

from backend.services.supply_chain.factory_orders import FactoryQtyExceeded

@router.patch("/vehicles/{order_no}/items/{item_id}", dependencies=[Depends(rate_limit_write)])
async def update_vehicle_item(
    order_no: str,
    item_id: int,
    payload: VehicleItemUpdate,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        fields = payload.model_dump(exclude_unset=True)
        display_name = user.first_name or user.username or user.email
        return await vehicle_delivery.update_vehicle_item(
            db, project.id, order_no, item_id,
            new_qty=payload.qty,
            box_size_override=payload.box_size_override,
            pcs_per_box_override=payload.pcs_per_box_override,
            box_detail_override=payload.box_detail_override,
            set_box_size_override="box_size_override" in fields,
            set_pcs_per_box_override="pcs_per_box_override" in fields,
            set_box_detail_override="box_detail_override" in fields,
            mode=payload.mode,
            user_name=display_name,
        )
    except FactoryQtyExceeded as e:
        raise HTTPException(422, detail=e.detail) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
```

### API Contract

**Request**: `PATCH /api/v1/supply-chain/vehicles/{order_no}/items/{item_id}`
```json
{
  "qty": 32,
  "mode": "strict"  // optional, default "strict"
}
```

**Response 200** (success, не превысили):
```json
{ "ok": true, "item_id": 4523, "extended_by": 0 }
```

**Response 200** (success, mode=extend_plan, расширили план):
```json
{ "ok": true, "item_id": 4523, "extended_by": 4 }
```

**Response 422** (mode=strict, превышение):
```json
{
  "detail": {
    "error": "exceeds_factory_qty",
    "fo_id": 16,
    "fo_number": "16/04",
    "foi_id": 4523,
    "barcode": "2049448537820",
    "subject": "одеяло_101x152_4кг",
    "fo_qty": 80,
    "fo_assigned": 76,
    "available": 4,
    "attempted_delta": 8,
    "in_mix_group": false,
    "mix_group_id": null
  }
}
```

**Response 404**: item не найден (как сейчас).

### Backward compat impact

**Меняется поведение:**
1. PATCH без `mode` теперь по умолчанию `strict` — старые клиенты получат 422 при попытке превысить план. До этого — тихо успешно. Это исправление бага.
2. PATCH в пределах плана теперь синхронизирует `FactoryOrderItem.assigned_qty` (раньше не трогал — комментарий sc15 в коде). Это меняет инвариант, но в пользу консистентности shipment_matrix и refresh_factory_order_statuses.

**Не меняется:**
- Поведение при `delta == 0` (no-op, теперь явное)
- Уменьшение qty (раньше assigned_qty не уменьшался — теперь уменьшается, исправление того же бага)
- box_size_override / pcs_per_box_override / box_detail_override — не трогают FOI

### Использование `_adjust_assigned_qty` в других местах

| Точка | Сейчас | После рефакторинга |
|---|---|---|
| `add_items_to_vehicle` | inline `if item_req.qty > remaining: raise ValueError` | `await _adjust_assigned_qty(db, fo_item, delta=qty, mode="strict", user_name=...)` (raises FactoryQtyExceeded — но в этой точке мы ловим как ValueError-совместимо для backward compat роутера) |
| `remove_item_from_vehicle` | inline `fo.assigned_qty = max(0, ... - qty)` | `await _adjust_assigned_qty(db, fo_item, delta=-qty, mode="strict", user_name=None)` |
| `clear_all_vehicle_items` | как remove | как remove, в цикле |
| `delete_vehicle` | как remove | как remove |
| `split_to_vehicles` | inline validation | `_adjust_assigned_qty(...)` |

**Примечание:** `add_items_to_vehicle` / `split_to_vehicles` используют `mode="strict"` с FactoryQtyExceeded. В роутере для них — оставляем 400 + текстовый message (back-compat для UI), не 422-структуру (она нужна только для inline confirm в UI машины).

## Frontend

### Типы

```typescript
// frontend-react/src/types/api.ts

export interface VehicleItemUpdate {
  qty?: number;
  box_size_override?: string | null;
  pcs_per_box_override?: number | null;
  box_detail_override?: number[] | null;
  mode?: "strict" | "extend_plan";
}

export interface VehicleItem {
  // ... existing fields
  qty_drift?: number | null;  // >0 если в БД рассинхрон
}

export interface FactoryQtyExceededDetail {
  error: "exceeds_factory_qty";
  fo_id: number;
  fo_number: string | null;
  foi_id: number;
  barcode: string;
  subject: string | null;
  fo_qty: number;
  fo_assigned: number;
  available: number;
  attempted_delta: number;
  in_mix_group: boolean;
  mix_group_id: string | null;
}

export class FactoryQtyExceededError extends Error {
  detail: FactoryQtyExceededDetail;
  constructor(detail: FactoryQtyExceededDetail) {
    super(`Factory plan exceeded: foi=${detail.foi_id}`);
    this.name = "FactoryQtyExceededError";
    this.detail = detail;
  }
}
```

### API клиент

```typescript
// frontend-react/src/lib/api/supply-chain.ts

export async function updateVehicleItem(
  orderNo: string,
  itemId: number,
  payload: VehicleItemUpdate,
): Promise<{ ok: true; item_id: number; extended_by: number; noop?: boolean }> {
  try {
    return await client.patch(
      `/supply-chain/vehicles/${encodeURIComponent(orderNo)}/items/${itemId}`,
      payload,
    );
  } catch (e: any) {
    // client.ts должен пробрасывать .response.status и .response.data
    if (e?.status === 422 && e?.detail?.error === "exceeds_factory_qty") {
      throw new FactoryQtyExceededError(e.detail);
    }
    throw e;
  }
}
```

> Точная сигнатура зависит от `client.ts`. Если он сейчас отдаёт `Response`-like — agent адаптирует. Главное: 422 с `detail.error="exceeds_factory_qty"` превращается в `FactoryQtyExceededError`.

### Компоненты страницы

**Дерево:**
```
VehicleDetailsPage
├─ VehicleHeader
│   ├─ StatusBadge
│   └─ <DriftBadge count={pendingDriftCount} /> ← красный если >0
├─ VehicleItemsTable
│   └─ map(items, item =>
│       <ItemRow item={item} onQtyChange={...} />
│       {driftStateById[item.id] && <DriftConfirmRow detail={driftStateById[item.id]} />}
│     )
└─ <button disabled={pendingDriftCount > 0} onClick={openStatusModal}>
     Изменить статус
   </button>
```

**`<ItemRow>` qty cell:**
```tsx
<td className="...">
  <input
    type="number"
    value={localQty}
    onChange={...}
    onBlur={() => commitQty()}
    className={cn(
      "...",
      driftStateById[item.id] && "border-orange-500 bg-orange-50",
      item.qty_drift && item.qty_drift > 0 && "border-orange-300",
    )}
  />
  {/* Постоянная оранжевая точка для existing drift из БД */}
  {item.qty_drift != null && item.qty_drift > 0 && (
    <button
      className="absolute top-1 right-1 w-2 h-2 rounded-full bg-orange-500"
      title={`Расхождение с фабричным заказом: +${item.qty_drift} шт.`}
      onClick={() => openDriftConfirmFromExisting(item)}
    />
  )}
</td>
```

**`commitQty()`:**
```tsx
const commitQty = async () => {
  if (localQty === item.qty) return; // no-op
  try {
    await updateVehicleItem(orderNo, item.id, { qty: localQty, mode: "strict" });
    setDriftStateById(prev => { const n = {...prev}; delete n[item.id]; return n; });
    refetchVehicle();
    flashGreen(item.id);
  } catch (e) {
    if (e instanceof FactoryQtyExceededError) {
      setDriftStateById(prev => ({
        ...prev,
        [item.id]: {
          ...e.detail,
          oldQty: item.qty,
          newQty: localQty,
        },
      }));
    } else {
      toast.error(e.message);
    }
  }
};
```

**`<DriftConfirmRow>`:**
```tsx
<tr className="bg-orange-50 border-l-4 border-orange-500">
  <td colSpan={N}>
    <div className="p-3 space-y-2">
      <div className="font-semibold text-orange-900">
        ⚠ Заказ #{detail.fo_number} ({detail.subject}): план превышен на {detail.attempted_delta - detail.available} шт.
      </div>
      <div className="text-sm text-gray-700">
        Фабричный заказ: план {detail.fo_qty} шт, распределено {detail.fo_assigned}, остаток {detail.available}<br/>
        В этой машине: было {detail.oldQty} → стало {detail.newQty}
      </div>
      {detail.in_mix_group && (
        <div className="text-sm text-blue-700 bg-blue-50 p-2 rounded">
          ⓘ Позиция в mix-группе. Расширение коснётся только этого баркода ({detail.barcode})
        </div>
      )}
      {(detail.attempted_delta - detail.available) > 1000 && (
        <div className="text-sm text-red-700 bg-red-50 p-2 rounded">
          ⚠ Большая дельта: +{detail.attempted_delta - detail.available}. Проверьте ввод!
        </div>
      )}
      <div className="flex gap-2">
        <Button onClick={onExtend} variant="primary">
          ➕ Расширить план до {detail.fo_qty + (detail.attempted_delta - detail.available)}
        </Button>
        <Button onClick={onRevert} variant="secondary">
          ↩ Откатить к {detail.oldQty}
        </Button>
      </div>
    </div>
  </td>
</tr>
```

**`onExtend`:**
```tsx
const onExtend = async () => {
  await updateVehicleItem(orderNo, item.id, { qty: detail.newQty, mode: "extend_plan" });
  toast.success(`Заказ #${detail.fo_number}: план +${detail.attempted_delta - detail.available} шт`);
  setDriftStateById(prev => { const n = {...prev}; delete n[item.id]; return n; });
  refetchVehicle();
};
```

**`onRevert`:**
```tsx
const onRevert = () => {
  setLocalQty(detail.oldQty);
  setDriftStateById(prev => { const n = {...prev}; delete n[item.id]; return n; });
  // без сетевого вызова — qty в БД не менялось
};
```

**`openDriftConfirmFromExisting`** (для existing БД-расхождений):
```tsx
const openDriftConfirmFromExisting = (item: VehicleItem) => {
  // Дёргаем fake-ошибку используя текущие данные машины + qty_drift
  // Минимально: нужно знать fo_qty / fo_assigned / fo_number — добавить в /vehicles/{no} response
  // расширенные fo поля или сделать отдельный fetch FOI.
  // Простой вариант: сделать PATCH с qty=item.qty (no-op в качестве "проверки")
  // — backend вернёт текущее состояние или 422 если другие машины уже раздули план.
  // Альтернатива: отдельный GET endpoint `/factory-orders/items/{foi_id}/state` (см. tasks.md).
};
```

> **Решение** в tasks: используем подход «обогатить response `_enrich_vehicle`» — в `VehicleItemSchema` добавить не только `qty_drift`, но и `fo_qty`, `fo_assigned`, `fo_number` (они уже частично загружаются в batch — расширим SELECT). Тогда existing drift показывается без доп.запросов.

### Pending drift в шапке

```tsx
const pendingDriftCount = Object.keys(driftStateById).length;

// в header машины:
{pendingDriftCount > 0 && (
  <Badge variant="destructive">
    Несохранённые расхождения: {pendingDriftCount}
  </Badge>
)}

// в кнопке смены статуса:
<Button
  disabled={pendingDriftCount > 0}
  title={pendingDriftCount > 0 ? "Решите расхождения qty перед сменой статуса" : undefined}
  onClick={openStatusModal}
>
  Изменить статус
</Button>
```

## Кэш

- `_invalidate_supplier_catalog(project_id)` после любой мутации — как сейчас
- `refresh_factory_order_statuses(db, project_id, affected_fo_ids)` после любой мутации `assigned_qty` — как сейчас
- При `extend_plan` дополнительно: ничего (план фабрики не кэшируется отдельно; supply_catalog инвалидируется уже)

## Тесты

### Backend (pytest, `tests/test_vehicle_qty_drift.py` или extend `tests/test_supply_chain_vehicles.py`)

| # | Тест | Что проверяет |
|---|---|---|
| 1 | `test_strict_pass_within_plan` | delta=4, available=4 → ok, fo.assigned_qty +=4 |
| 2 | `test_strict_fail_exceeds_plan` | delta=8, available=4 → FactoryQtyExceeded с правильным detail |
| 3 | `test_extend_plan_with_zero_available` | delta=8, available=0 → fo.qty +=8, history запись |
| 4 | `test_extend_plan_with_partial_available` | delta=8, available=4 → fo.qty +=4 (не +=8) |
| 5 | `test_decrement_clamp` | delta=-12 при assigned=10 → assigned=0 (не отрицательное) |
| 6 | `test_noop_zero_delta` | new_qty == old_qty → возвращает {noop: true}, ничего не дёргает |
| 7 | `test_mix_group_extends_only_current_foi` | mix-group из 3 позиций, extend_plan на одной — qty других не меняется |
| 8 | `test_idempotency_double_call` | повтор PATCH с тем же qty → no-op, нет лишних history записей |
| 9 | `test_history_record_on_extend` | event_type, details формат, changed_by, changed_at |
| 10 | `test_qty_drift_in_enriched_schema` | в БД есть рассинхрон → /vehicles/{no} возвращает qty_drift > 0 |
| 11 | `test_refresh_factory_order_statuses_called_after_extend` | после extend_plan заказ может перейти в DISTRIBUTED |
| 12 | `test_router_returns_422_with_structured_detail` | через TestClient PATCH с превышением → 422 с полным detail |

### Frontend (vitest, `frontend-react/src/lib/api/supply-chain.test.ts`)

| # | Тест | Что проверяет |
|---|---|---|
| 1 | `parses_422_into_FactoryQtyExceededError` | mock 422 ответа → throw FactoryQtyExceededError с правильным detail |
| 2 | `passes_through_non_422_errors` | mock 500 → обычная Error |
| 3 | `success_response_typed` | mock 200 → возвращает `{ok, item_id, extended_by}` |

### E2E (опционально, playwright `frontend-react/tests/e2e/vehicle-drift.spec.ts`)

| # | Сценарий |
|---|---|
| 1 | Открыть машину → изменить qty с превышением → видна DriftConfirmRow → клик «Расширить» → toast + ячейка зелёная + перезагрузка показывает новое qty |

## Документация

После merge обновить:
- `backend/DOMAIN_SUPPLY_CHAIN.md` — секция «Редактирование позиций машины» добавить упоминание `mode` и drift confirm
- `frontend-react/src/app/(main)/p/[slug]/supply-chain/i18n.tsx` — переводы для UI текстов (RU/ZH)

## Сводка изменений по файлам

| Файл | Изменение | Владелец фазы |
|---|---|---|
| `backend/services/supply_chain/factory_orders.py` | + `_adjust_assigned_qty`, `FactoryQtyExceeded`, `AdjustResult` | Phase 1 (lead) |
| `backend/services/supply_chain/vehicle_delivery.py` | `update_vehicle_item` (mode + sync), `_enrich_vehicle` (qty_drift, fo_qty/assigned/number в schema), `add/remove/clear/delete` → использовать `_adjust_assigned_qty` | Phase 2 backend |
| `backend/schemas/supply_chain.py` | `VehicleItemUpdate.mode`, `VehicleItemSchema.qty_drift/fo_qty/fo_assigned` | Phase 1 (lead) |
| `backend/routers/supply_chain.py` | catch `FactoryQtyExceeded` → 422 structured | Phase 2 backend |
| `tests/test_supply_chain_vehicles.py` (или новый) | 12 тестов | Phase 2 backend |
| `frontend-react/src/types/api.ts` | + типы | Phase 2 frontend |
| `frontend-react/src/lib/api/supply-chain.ts` | `updateVehicleItem` парсит 422 | Phase 2 frontend |
| `frontend-react/src/app/(main)/p/[slug]/supply-chain/vehicles/[order_no]/page.tsx` | inline DriftConfirmRow, dot для existing drift, header badge, status disable | Phase 2 frontend |
| `frontend-react/src/app/(main)/p/[slug]/supply-chain/i18n.tsx` | RU/ZH строки | Phase 2 frontend |
| `frontend-react/src/lib/api/supply-chain.test.ts` | vitest | Phase 2 frontend |
| `backend/DOMAIN_SUPPLY_CHAIN.md` | обновление секции | Phase 3 (после merge) |
