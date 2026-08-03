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
| Вайбкодинг | статистика по git; БЕЗ `project_id`, доступ по `vibe_authors`, данные льёт CI | [DOMAIN_VIBE.md](DOMAIN_VIBE.md) |
| Сборка | заявки на сборку FBO, статусы, AssemblyDraft | [DOMAIN_ASSEMBLY.md](DOMAIN_ASSEMBLY.md) |
| Поставки | FactoryOrder → Vehicle → Таможня → Склад | [DOMAIN_SUPPLY_CHAIN.md](DOMAIN_SUPPLY_CHAIN.md) |
| Контрагенты | upsert по ИНН, мультивалюта, займы, обороты | [DOMAIN_COUNTERPARTY.md](DOMAIN_COUNTERPARTY.md) |
| Заявки на оплату | оплата перевозчику по отгрузке: реквизиты + счёт/акт → черновик платёжки в банк (Faktura write) → авто-матч выписки → PAID | [DOMAIN_PAYMENT_REQUEST.md](DOMAIN_PAYMENT_REQUEST.md) |
| Планирование | плановые показатели, платежи, таможня, кэшфлоу | [DOMAIN_PLANNING.md](DOMAIN_PLANNING.md) |
| Локализация | индекс ИЛ / ИРП, региональные продажи, cold-start | [DOMAIN_LOCALIZATION.md](DOMAIN_LOCALIZATION.md) |
| Telegram | бот + Telegram Mini App, авторизация, дайджест | [DOMAIN_TELEGRAM.md](DOMAIN_TELEGRAM.md) |
| Фулфилмент | интеграция skladbot/wmscelicom (позже migfull): остатки-зеркало, заявки, связь с документами; **FF billing** — тарифы услуг ФФ, посуточное хранение, счета ФФ | [DOMAIN_FULFILLMENT.md](DOMAIN_FULFILLMENT.md) |
| WB FBS | продажи со склада продавца: склады WB ↔ наши, трансляция остатков по `chrtId`, сборочные задания, поставки; обратный гейт «продано по FBS → нельзя в сборку» | [DOMAIN_WB_FBS.md](DOMAIN_WB_FBS.md) |
| Дизайн карточек | канбан задач на инфографику: 8 статусов + словарь переходов, версии сдач с вердиктом, материалы в MinIO, TG-уведомления и утренняя сводка, мост в АБ-тесты фото | [DOMAIN_DESIGN.md](DOMAIN_DESIGN.md) |
| Зарплата | команды владеют брендами/категориями/пересечениями бренд×категория (композит вытесняет общий), членство с границами по месяцам; база недели (Пн–Вс, месяц по четвергу) = net_payout БДР `period_mode='report'` × глобальная тарифная лестница; фикс-оклады периодами (`PayrollSalaryPeriod`, история изменений оклада), план выплат 10/25 M+1, официалка из выписки по `counterparty_id`, строка «ФОТ (начислено)» с подстроками в ОПиУ (исключение выплат из opex скоупится датами периодов); «Агентство» — клиенты консалтинга (формат оплаты периодами: fixed/percent/profit_share), fee от БДР кабинета клиента или ручных сумм, manager-доля команде. `models/payroll.py`, `schemas/payroll.py`, `services/payroll_service.py`, `services/payroll_agency_service.py`, `routers/payroll.py` (page-гейт `salary`) | — |

Новый домен: скопировать `.claude/templates/DOMAIN_template.md` → `backend/DOMAIN_<NAME>.md`, добавить строку сюда.
