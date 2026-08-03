---
name: payroll-global-tariff-intentional
description: Тарифная лестница ЗП (payroll_tariff_step) намеренно глобальна — без project_id; не предлагать блокирующий фикс, только admin-гейт
metadata:
  type: project
---

`PayrollTariffStep` (`backend/models/payroll.py`) не имеет `project_id`: одна лестница на все проекты инсталляции, версионирование через `valid_from`. Это ЯВНОЕ решение владельца от 2026-07-28 (зафиксировано в docstring модели и роутера).

**Why:** владелец считает справочник уровнем инсталляции (аналог `category_ref`), а не тенанта — все проекты принадлежат одному бизнесу.

**How to apply:** при ревью `PUT /api/v1/payroll/tariff` и `payroll_service.replace_tariff` НЕ помечать «запрос без project_id» как CRITICAL и не предлагать блокирующий фикс / миграцию с добавлением `project_id`. Остаётся валидным замечанием только authz: эндпоинт доступен любому участнику любого проекта без `require_admin`, а `invalidate_cache("payroll:sheet")` сбрасывает ведомости всех проектов. См. [[page-permissions-frontend-only]].
