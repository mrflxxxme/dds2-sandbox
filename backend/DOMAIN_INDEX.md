# Domain Index — DDS2

Полная карта доменов проекта. Каждый домен документирован в собственном `DOMAIN_<NAME>.md`. Шаблон новых доменов — `.claude/templates/DOMAIN_template.md`.

| Домен | Ключевое | Файлы | Doc |
|-------|----------|-------|-----|
| Транзакции | dedupe by txn_id | `etl/`, `transactions_service` | [DOMAIN_TRANSACTIONS.md](DOMAIN_TRANSACTIONS.md) |
| Отчёты | кэш 300s; синк ОПИУ+БДР | `reports/`, `opiu_service`, `wb_bdr_service` | [DOMAIN_REPORTS.md](DOMAIN_REPORTS.md) |
| AI Chat | SSE streaming, file upload | `routers/ai_chat`, `models/ai_chat` | [DOMAIN_AI.md](DOMAIN_AI.md) |
| AI Агенты | orchestrator → agents → synthesizer | `services/ai/` | [DOMAIN_AI.md](DOMAIN_AI.md) |
| Себестоимость | FIFO; duty per container | `cost/`, `cost_parsers` | [DOMAIN_COST.md](DOMAIN_COST.md) |
| Склад | FBO vs FBS; WB sync; acceptance-check + box-multiplicity | `warehouse_*`, `fbo_supply_service`, `warehouse_acceptance_service`, `box_multiplicity_service` | [DOMAIN_WAREHOUSE.md](DOMAIN_WAREHOUSE.md) |
| WB API | Semaphore, Retry-After, partial save | `integrations/`, `funnel/` | [DOMAIN_WB.md](DOMAIN_WB.md) |
| Сборка | CRUD + status + analytics | `assembly/` | [DOMAIN_ASSEMBLY.md](DOMAIN_ASSEMBLY.md) |
| Поставки | FactoryOrder → CostOrder → Warehouse | `supply_chain/` | [DOMAIN_SUPPLY_CHAIN.md](DOMAIN_SUPPLY_CHAIN.md) |
| Контрагенты | upsert by INN; мультивалюта | `counterparty_service` | [DOMAIN_COUNTERPARTY.md](DOMAIN_COUNTERPARTY.md) |
| Обороты контрагентов | агрегация платежей | `counterparty_turnovers_service` | [DOMAIN_COUNTERPARTY_TURNOVERS.md](DOMAIN_COUNTERPARTY_TURNOVERS.md) |
| Займы | мультивалюта, проценты | `loan_service` | [DOMAIN_LOAN.md](DOMAIN_LOAN.md) |
| Планирование | плановые показатели + факт | `services/planning_*` | [DOMAIN_PLANNING.md](DOMAIN_PLANNING.md) |
| Локализация | региональные продажи, cold-start | `localization/` | [DOMAIN_LOCALIZATION.md](DOMAIN_LOCALIZATION.md) |
| Telegram | bot интеграция | `telegram/` | [DOMAIN_TELEGRAM.md](DOMAIN_TELEGRAM.md) |

## При добавлении нового домена
1. Скопировать `.claude/templates/DOMAIN_template.md` → `backend/DOMAIN_<NAME>.md`
2. Добавить строку в эту таблицу (по алфавиту в категории)
3. Заполнить разделы шаблона: Ownership, Tables, Business Rules, Endpoints, Known Issues
