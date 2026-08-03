---
name: bulk-ids-unbounded-amplification
description: Bulk-эндпоинты DDS2 принимают `ids: list[int]` без верхней границы — rate_limit_write считает ЗАПРОСЫ, не элементы; это сквозной паттерн, а не регрессия конкретной фичи
metadata:
  type: project
---

`rate_limit_write` (`backend/utils/rate_limit.py:95`) — 60 запросов/мин НА IP, лимит по числу HTTP-запросов. Ни одна bulk-схема границу списка не задаёт: `AssignVehicleBulk.items`, `ShipBulk.ids`, `DeleteBulk.ids`, `StatusBulk` (`backend/schemas/assembly.py`), `TransferAssignVehicleBulk.ids` (`backend/schemas/warehouse.py`). Сервисы обрабатывают список поштучно, с `db.commit()` и `invalidate_cache(...)` НА КАЖДЫЙ элемент.

Амплификация усиливается тем, что `invalidate_cache` (`backend/cache.py:110`) делает полный `SCAN` по всему кейспейсу Redis, а Redis один на инсталляцию → нагрузка задевает все проекты. Дополнительный трюк: если операция не меняет статус (назначение машины), один и тот же id можно повторить N раз — «валидных» документов для атаки нужен ровно один.

**Why:** паттерн заведён давно и одинаков во всех доменах; не является дефектом конкретной новой фичи. Прецедент осознанного лечения амплификации в проекте — `rate_limit_acceptance_force` (суб-лимит 6/мин поверх основного бакета, добавлен после HIGH security-ревью 2026-07-03), т.е. владелец такие вещи чинит точечно, а не блокирующим гейтом.

**How to apply:** в ревью новых bulk-эндпоинтов помечать как MEDIUM (не CRITICAL) с явной оговоркой «то же самое уже есть в assembly bulk — фикс уровня паттерна, не гейт на эту фичу». Рекомендуемый фикс — `ids: list[int] = Field(max_length=200)` + дедуп, без блокирующих правок. Связано с [[page-permissions-frontend-only]].
