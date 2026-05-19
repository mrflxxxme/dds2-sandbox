# DOMAIN_PLANNING — Planning (Orders, Payments, Customs, Cashflow)

Планирование закупок: заказы поставщикам → плановые платежи по lead-time → привязка факта → прогноз кэшфлоу. Плюс таможенные авансы и план-факт по брендам.

## Таблицы
| Модель | Назначение | Примечание |
|--------|------------|------------|
| `Order` (`orders`) | Заказы поставщикам, суммы CNY + RUB + логистика | `order_no` уникален; SoftDeleteMixin |
| `LeadTime` (`lead_time`) | Сроки по направлениям | ORDER=50d, AUTO=14d, CONTAINER=40d, CUSTOMS=17d |
| `PlannedPayment` (`planned_payments`) | Плановые платежи | SoftDeleteMixin; статусы planned → partial → paid |
| `PlannedIncome` (`planned_incomes`) | Плановые доходы | |
| `WbPayout` (`wb_payouts`) | WB-выплаты | SoftDeleteMixin |
| `PaymentFactLink` (`payment_fact_links`) | Привязки факт → план | SoftDeleteMixin |
| `CustomsTopup` (`customs_topup`) | Авансы на таможню | |
| `CustomsAlloc` (`customs_alloc`) | Распределение авансов по заказам | |
| `CustomsDT` (`customs_dt`) | ДТ (декларации на товары) | |
| `BrandPlan` (`brand_plans`) | План по брендам | `(year, month, brand)` → planned revenue/orders |

## Бизнес-правила
- **Плановые платежи** генерируются из заказа по `lead_time`.
- **Fact linking:** реальная транзакция привязывается к `PlannedPayment` через `PaymentFactLink`.
- **`is_paid`** автоматически = True, когда `SUM(fact_links.amount_rub) >= planned_payment.amount_rub`. Запрос `paid_amount` фильтрует `is_deleted` — удаление fact_link корректно сбрасывает `is_paid`.
- **Cashflow forecast** = `planned_payments` (расход) + `planned_incomes` (доход) + `opening_balance`.
- **Customs:** `topup` → `alloc` по заказам → ДТ с номерами.

## Зависимости
- `DOMAIN_TRANSACTIONS` — fact linking привязывает `PlannedPayment` к `Transaction`; `etl/sync_payments.py` синхронизирует при импорте.
- `fx_service.py` — конвертация CNY → RUB для заказов.

## Грабли
- **Race condition в fact_links** — `commit()` + `update_payment_paid_amount()` не атомарны.
- **Нет валидации `amount_rub`** — можно передать отрицательную сумму.
- После мутаций инвалидировать `reports:cashflow`.

## Файлы
- `services/planning/crud.py` — CRUD заказов и платежей.
- `services/planning/cashflow.py` — прогноз кэшфлоу.
- `services/planning/customs.py` — таможенные пополнения и распределения.
- `services/planning/fact_links.py` — привязка факта к плану.
- `services/planning/wb.py` — WB-выплаты.
- `services/planning/brand_plan.py` — CRUD план-факт по брендам.
- `routers/planning.py`, `routers/planning_customs.py`, `routers/planning_wb_payouts.py` — HTTP endpoints.
- `models/planning.py`, `models/customs.py` — ORM.
- `schemas/planning.py` — Pydantic схемы.
