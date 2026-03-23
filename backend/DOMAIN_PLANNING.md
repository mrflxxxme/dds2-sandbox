# Domain: Planning (Orders, Payments, Customs, Cashflow)

## Ownership
Файлы этого домена:
- `services/planning/cashflow.py` — прогноз кэшфлоу
- `services/planning/crud.py` — CRUD заказов и платежей
- `services/planning/customs.py` — таможенные пополнения и распределения
- `services/planning/fact_links.py` — привязка факта к плану
- `services/planning/wb.py` — WB-выплаты
- `routers/planning.py` — HTTP endpoints
- `routers/planning_customs.py` — таможенные endpoints
- `routers/planning_wb_payouts.py` — WB-выплаты endpoints
- `models/planning.py` — Order, LeadTime, PlannedPayment, PlannedIncome, WbPayout, PaymentFactLink, BrandPlan
- `services/planning/brand_plan.py` — CRUD план-факт по брендам
- `models/customs.py` — CustomsTopup, CustomsAlloc, CustomsDT
- `schemas/planning.py`
- `tests/test_api_planning.py`

## Tables
- `orders` — заказы поставщикам (SoftDeleteMixin)
- `lead_time` — сроки по направлениям (ORDER=50d, AUTO=14d, CONTAINER=40d, CUSTOMS=17d)
- `planned_payments` — плановые платежи (SoftDeleteMixin)
- `planned_incomes` — плановые доходы
- `wb_payouts` — WB-выплаты (SoftDeleteMixin)
- `payment_fact_links` — привязки факт→план (SoftDeleteMixin)
- `customs_topup` — авансы на таможню
- `customs_alloc` — распределение авансов по заказам
- `customs_dt` — ДТ (декларации на товары)
- `brand_plans` — план по брендам (year, month, brand → planned revenue/orders)

## Business Rules
1. **Заказ:** order_no уникален, содержит суммы в CNY + RUB + логистику
2. **Плановые платежи:** генерируются из заказа по lead_time, статусы: planned → partial → paid
3. **Fact linking:** реальная транзакция привязывается к PlannedPayment через PaymentFactLink
4. **is_paid:** автоматически = True когда SUM(fact_links.amount_rub) >= planned_payment.amount_rub
5. **Cashflow forecast:** planned_payments (расход) + planned_incomes (доход) + opening_balance
6. **Customs:** topup → alloc по заказам → ДТ с номерами

## Known Issues & Gotchas
- **Race condition в fact_links:** commit() + update_payment_paid_amount() не атомарны
- ~~**is_paid не сбрасывается:** при удалении fact_link is_paid остаётся True~~ — **ИСПРАВЛЕНО** (2026-03-16)
- ~~**Soft delete fact_links:** запрос paid_amount НЕ фильтрует is_deleted~~ — **ИСПРАВЛЕНО** (2026-03-16)
- **Нет валидации amount_rub:** можно передать отрицательную сумму

## Dependencies
- `transactions` — fact linking привязывает PlannedPayment к Transaction
- `etl/sync_payments.py` — автоматическая синхронизация при импорте
- `fx_service.py` — конвертация CNY→RUB для заказов

## Cache Invalidation
После мутаций: `await invalidate_cache("reports:cashflow")`
