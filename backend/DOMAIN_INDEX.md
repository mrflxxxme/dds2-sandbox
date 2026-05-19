# Domain Index — DDS2

Карта доменов. Детали каждого — в его `DOMAIN_<NAME>.md`. Шаблон нового домена — `.claude/templates/DOMAIN_template.md`.

| Домен | Ключевое | Doc |
|-------|----------|-----|
| Транзакции | импорт выписок, ETL, дедуп по `txn_id`, категоризация | [DOMAIN_TRANSACTIONS.md](DOMAIN_TRANSACTIONS.md) |
| Отчёты | ДДС / БДР / ОПИУ / Dashboard, кэш 300s | [DOMAIN_REPORTS.md](DOMAIN_REPORTS.md) |
| AI | чат + мульти-агенты (orchestrator → agents → synthesizer), SSE | [DOMAIN_AI.md](DOMAIN_AI.md) |
| Себестоимость | FIFO, номенклатура, пошлины | [DOMAIN_COST.md](DOMAIN_COST.md) |
| Склад | остатки, приёмка / отгрузка, FBO, box-multiplicity, WB-возвраты | [DOMAIN_WAREHOUSE.md](DOMAIN_WAREHOUSE.md) |
| WB API | HTTP-клиент, resilience, воронка, финансы, синки | [DOMAIN_WB.md](DOMAIN_WB.md) |
| Сборка | заявки на сборку FBO, статусы, AssemblyDraft | [DOMAIN_ASSEMBLY.md](DOMAIN_ASSEMBLY.md) |
| Поставки | FactoryOrder → Vehicle → Таможня → Склад | [DOMAIN_SUPPLY_CHAIN.md](DOMAIN_SUPPLY_CHAIN.md) |
| Контрагенты | upsert по ИНН, мультивалюта, займы, обороты | [DOMAIN_COUNTERPARTY.md](DOMAIN_COUNTERPARTY.md) |
| Планирование | плановые показатели, платежи, таможня, кэшфлоу | [DOMAIN_PLANNING.md](DOMAIN_PLANNING.md) |
| Локализация | индекс ИЛ / ИРП, региональные продажи, cold-start | [DOMAIN_LOCALIZATION.md](DOMAIN_LOCALIZATION.md) |
| Telegram | бот + Telegram Mini App, авторизация, дайджест | [DOMAIN_TELEGRAM.md](DOMAIN_TELEGRAM.md) |

Новый домен: скопировать `.claude/templates/DOMAIN_template.md` → `backend/DOMAIN_<NAME>.md`, добавить строку сюда.
