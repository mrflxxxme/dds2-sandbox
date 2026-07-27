# DOMAIN_WB — WB Integration (API, Funnel, Finance, Sync)

Интеграция с Wildberries: HTTP-клиенты, resilience, синхронизация воронки/рекламы/финансов, управление API-ключами.

## Таблицы
| Модель | Назначение | Примечание |
|--------|------------|------------|
| `IntegrationKey` (`integration_keys`) | Зашифрованные API-ключи | service: wb/ozon; типы: analytics/adv/content |
| `SyncLog` (`sync_log`) | Лог синхронизаций | status: RUNNING/OK/ERROR/STALE/TIMEOUT |
| `WbFunnelDaily` (`wb_funnel_daily`) | Ежедневная воронка по nmID | |
| `WbCostOverride` (`wb_cost_override`) | Ручные себестоимости | |
| `WbWarehouseStock` (`wb_warehouse_stocks`) | Остатки WB «доступно к продаже» (statistics supplier/stocks) | не видит приёмку/межскладской транзит |
| `WbWarehouseRemains` (`wb_warehouse_remains`) | Остатки WB как в кабинете (analytics warehouse_remains) | псевдо-склады «В пути…»/«Всего…» хранятся строками; «Всего находится на складах» = дубль суммы реальных складов — исключать из суммирования (`WB_REMAINS_TOTAL_ROW`) |
| `WbFinanceRow` (`wb_finance_rows`) | Кэш финансового отчёта WB | |
| `WbFinanceSyncLog` (`wb_finance_sync_log`) | Лог синхронизации финансов | |
| `WbOrderCancelDaily` (`wb_order_cancel_daily`) | Ежедневная статистика отмен | |
| `WBFeedback` (`wb_feedbacks`) | Зеркало отзывов покупателей WB | uniq `(project_id, wb_id)`; `has_text` derived; sync_type=`feedbacks` |
| `WBFeedbackComplaint` (`wb_feedback_complaints`) | Учёт жалоб на отзывы (для удаления) | uniq `(project_id, wb_feedback_id)`; status pending/removed/rejected; НЕ авто-отправка в WB |
| `WBQuestion` (`wb_questions`) | Зеркало вопросов покупателей WB | uniq `(project_id, wb_id)`; sync_type=`questions`; миграция `wq01` |
| `WBReplyAgent` (`wb_reply_agents`) | ИИ-агенты автоответов на отзывы/вопросы | target feedback/question/both; `auto_send` хранится, но **игнорируется** (только ручное одобрение); миграция `wr01` |
| `WBFeedbackReply` (`wb_feedback_replies`) | Ответы продавца: черновик → отправка | status draft/approved/sent/error/rejected; source agent/manual; `needs_info`/`generation` — защита от выдумок (миграция `kb01`) |
| `WBProductKB` (`wb_product_kb`) | База знаний товаров для автоответов | эталонные пары вопрос/ответ по nm_id; source manual/import/card; дедуп `(project_id, nm_id, question_hash)`; миграция `kb01` |
| `WBProductCard` (`wb_product_cards`) | Зеркало публичных карточек WB | uniq `(project_id, nm_id)`; название/бренд/описание/характеристики (JSONB)/URL фото; миграция `pc01` |
| `WbTariff` (`wb_tariffs`) | Коэффициенты WB | SoftDeleteMixin |

## Бизнес-правила

### API-ключи
- Шифрование AES-256 Fernet (`utils/crypto.py`). LEGACY fallback для старых ключей — **не удалять** (требует data-migration).
- Типы ключей: analytics (воронка), adv (реклама), content (карточки).

### Resilience
- `429` → `RateLimitError`, respect `Retry-After`. **НЕ** считается failure для Circuit Breaker.
- Circuit Breaker — **per-project** (`CircuitBreakerRegistry`, `_wb_circuits`), только для 500–504: 5 failures → 120s cooldown.
- Retry: max 3 attempts, exponential backoff (2s, 4s, 8s).

### Sync
- Scheduler работает **только** в worker-контейнере (`DDS_ROLE=worker`).
- `sync_log` — **всегда** обновлять в `finally`, никогда не оставлять `RUNNING`.
- На старте worker — stale cleanup: `RUNNING` > 10 мин → `STALE`. При SIGKILL (exit 137) запись остаётся `RUNNING` и чинится этим cleanup.
- Partial data: при ошибке mid-sync сохранять уже загруженные дни.
- Все sync jobs **обязаны** ловить `asyncio.CancelledError` (не наследуется от `Exception`) — иначе `error_msg=null` в `sync_log`.
- Monitoring: статус scheduler определяется по `sync_log` (не in-memory) — `/monitoring/overview` работает в api-контейнере.

### Остатки WB: два источника
- `supplier/stocks` (statistics) → `wb_warehouse_stocks`: только «доступно к продаже». Потребители: warehouse_need, stock_forecast, assembly_load_forecast, funnel stock_costs.
- `warehouse_remains` (analytics, task-based: create → poll status → download; лимиты 1 req/min) → `wb_warehouse_remains`: цифры кабинета 1:1, включая приёмку и межскладской транзит. Потребитель: колонка «WB склады» в «Сводных остатках» (`get_unified_stock_summary`, join по barcode с fallback на nm_id; до первого синка — fallback на statistics-источник). Синк ежечасно в :20 MSK (`wb_remains_sync`), ручной — `POST /reports/stock_warehouses/sync-remains`. Этот же синк МОСТОМ пересобирает `wb_warehouse_stocks` (`_bridge_rows_from_remains`): старый источник зеркала (statistics `supplier/stocks`) с 2026-07-15 отдаёт 0 строк, а зеркало читают потребность/прогнозы/кратность/прайсинг. Псевдо-склады строками зеркала НЕ становятся: в-пути карточки едут полями `in_way_*` на строке-носителе (max qty); после моста инвалидируются `reports:warehouse_need` и `reports:stock_warehouses*`.

### Расписание WB Finance (MSK, tue–sun)
WB публикует финотчёт за прошлый день к 03–05 MSK. Прогоны: `05:00` (ранний, успевает к утреннему дайджесту), `08:00` (страховка от поздней публикации), `14:00` (добор), `catchup` на старте worker. `misfire_grace_time=3600` — задача не теряется при рестарте worker в пределах часа.

### Worker lifecycle
- `stop_grace_period: 60s` + `stop_scheduler(wait=True)` — APScheduler ждёт завершения текущих задач при деплое.
- Healthcheck проверяет scheduler (не только HTTP) — docker рестартит зависший scheduler.

### Воронка и метрики
- Воронка: transitions → add_to_cart → orders_count → orders_sum → buyout_count.
- Реклама: ad_spend, ad_views, ad_clicks → CTR, CPC, CPM.
- Unit-экономика: `revenue − cost − ads − tax = profit` per unit.
- Расширенный режим (`GET /funnel/data?extended=true`, `services/funnel/stock_costs.py`): себестоимость остатков WB (`Σ quantity_full × цена`), своих складов (`Σ quantity` — с резервом, без брака) и прогноз исчерпания (заказы/день за 7 дн, якорь на вчера + тренд 7д-vs-7д). Цена единицы: avg по `cost_order_items` → `WbCostOverride` → `WarehouseStock.cost_price`. `min_orders=N` — серверный порог по заказам. Оба параметра — для всех группировок, кроме `day`.

### Локализация (ИЛ + ИРП) — с 23.03.2026
- Источник: WB Analytics v3 `sales-funnel/products` → `localizationPercent`, `timeToReady`.
- Хранение: `wb_funnel_daily.localization_percent` + `time_to_ready_minutes`.
- Детали расчёта — `DOMAIN_LOCALIZATION`.

### Review-deduction enrichment (списания за отзыв)
WB возвращает строки удержаний за отзывы с пустыми товарными полями (`nm_id=0`, `sa_name=''`); идентификатор товара зашит только в тексте `bonus_type_name` («Списание за отзыв XXX: акция №N, товар N»).
- `wb_finance_helpers.parse_review_target` — регэксп извлекает nm_id из текста.
- `wb_finance_sync._upsert_batch` подтягивает `(brand, subject, sa_name)` через `_load_nm_meta` (DISTINCT ON по свежей продажной строке `wb_finance_rows` того же товара) — без зависимости от `nomenclature` (он у многих проектов пуст).
- Результат: BDR/OPIU/Cost-DNA разносят «Прочие удержания» по бренду/категории/артикулу; сумма не меняется, появляется только разрез.
- Бэкфил истории: `python -m scripts.backfill_review_deductions`.
- При новом типе удержаний с nm_id в тексте — расширить `_REVIEW_TARGET_RE` или добавить аналогичный helper.

### Отзывы покупателей (feedbacks) — сводная аналитика
Отзывы **зеркалятся в БД** (`wb_feedbacks`), сводка строится из зеркала, не из живого API (историю «за всё существование» живой API не отдаёт — старое уходит в архив, `take` ограничен).
- **Ключ**: `resolve_wb_key` каскад `wb_feedbacks`→`wb_analytics`→`wb`. Отдельный тип ключа `wb_feedbacks` (scope «Вопросы и отзывы») добавляется в Настройка→Интеграции — валидируется `wb_api.check_feedbacks_scope` (401/403→no_scope→400; 429/5xx/сеть→unknown→сохраняем, как wb_content/wb_advert). Резолвер берёт его первым, поэтому битый feedbacks-ключ ЗАТЕНИТ рабочий `wb` → удалять невалидный.
- Sync: `services/wb_reviews_sync.py` — активные отзывы (isAnswered false+true) + `full_backfill` тянет архив (`WBApiClient.get_feedbacks_archive`) при первом прогоне (пустое зеркало). Upsert `on_conflict (project_id, wb_id)`, дедуп ключей до executemany.
- Job: `scheduler/jobs/wb_reviews_sync.py`, ночью 03:15 MSK, `sync_type="feedbacks"`. On-demand — `POST /reviews/sync` (кнопка «Обновить»).
- Агрегаты: `services/reviews_service.get_reviews_summary(project_id, tag=None, period="1y")` (`@cached("reviews:summary", 300)`) — KPI, рейтинг/объём временны́м рядом, разрезы по категории (`Nomenclature.subject`)/бренду (`Nomenclature.brand` по nm_id, фолбэк — снапшот отзыва)/ярлыку (`ProductTagMap`→`ProductTag.name`, только `is_deleted=False`). Непривязанные nm_id → «Без категории/бренда/ярлыка».
- **Период (`period`)**: `2w/1m/3m/6m/1y/all` (дефолт `1y`) → окно `created_date >= now-Δ` во ВСЕХ блоках. Гранулярность рядов адаптивная: `day` для коротких (2w/1m), `month` для остальных; отдаётся в ответе (`granularity`). Неизвестный ключ → `1y`. `GET /reviews/summary` и `POST /reviews/sync` принимают `period` (эхо в ответе).
- **has_key = «показывать data-UI»**: True, если у проекта есть отзывы за ВСЁ время (`_has_any_feedback`) ИЛИ активный ключ — НЕ period-фильтрованный `total` (иначе пустое окно у проекта с историей ложно показало бы экран «нет ключа»). Пустое окно при has_key=True → фронт рисует «за период пусто».
- **Список** (`GET /reviews`): пагинация `take`/`skip`, ответ несёт `total` среза (по `is_answered`) → фронт «показано N из M» + «Показать ещё».
- **Проблемные новинки** (`GET /reviews/newcomers?days=30&max_rating=4.6`, `get_new_low_rated`): товары «на продаже» < `days` дней со средним рейтингом < `max_rating`. «Старт продаж» = `Nomenclature.first_sale_date`, при NULL — фолбэк на дату ПЕРВОГО отзыва (прокси). Вкладка «🆕 Проблемные новинки». Не кэшируется. Плюс **разрезы** `by_category/by_brand/by_tag` (`_group_newcomers`): агрегат проблемных новинок по предмету/бренду/ярлыку — `products` (число новинок), `avg_rating` (ТОЧНО из суммы r1..r5, не усреднением округлённых), `count`, распределение. Товар с несколькими ярлыками — в каждый; без ярлыка → «Без ярлыка».
- Grabli: джойн `feedback.nm_id → Nomenclature.article_wb` дедуплен через `GROUP BY article_wb` в подзапросе — иначе размеры (много barcode на nm_id) раздули бы счётчики.

### Жалобы на отзывы (для удаления)
`services/complaints_service.py` + `models/wb_feedback_complaints.py` (миграция `rev02_feedback_complaints`). Готовит текст по шаблону (причина «отзыв не относится к товару») для отзывов 1–3★, фиксирует факт подачи и исход.
- **Почему подача ручная:** метод `POST /api/v1/feedbacks/actions` в WB API существует, но WB **временно отключил** его — жалобы подаются только через ЛК продавца (тем, у кого было автоматизировано, предписан ручной режим). Клиент `WBApiClient.submit_feedback_complaint` написан и **дремлет за флагом** `settings.WB_FEEDBACK_COMPLAINTS_API` (default False) — включить, когда WB вернёт метод. ⚠️ Лимит методов отзывов **1 rps** (до 3 rps → блок 60 сек): массовую подачу гнать только фоновой очередью с троттлингом, не в HTTP-запросе (6.7k жалоб ≈ 2 часа).
- **Автодетект исхода** (`resolve_after_sync`): вызывается из `sync_project_feedbacks` ТОЛЬКО при `full_backfill=True` (актив+архив = полная выдача WB). Жалоба `pending`, а отзыва в выдаче нет → `removed`; отзыв на месте и жалобе >14 дней (`_REJECT_AFTER_DAYS`) → `rejected`. **Грабля:** на инкрементальном синке (без архива) отзыв мог просто уехать в архив — детект там дал бы ложное «удалён», поэтому гейт по `full_backfill` обязателен. Эндпоинты: `GET /reviews/complaints/candidates` (низкооценённые отзывы + статус), `GET /reviews/complaints` (поданные + KPI подано/удалено/не удалено/в ожидании), `POST /reviews/complaints` (зафиксировать, idempotent по отзыву), `PATCH /reviews/complaints/{id}` (исход). Фронт — вкладка «🚩 Жалобы».

### Автоответы на отзывы и вопросы (ИИ, MVP)
Единый журнал ответов `wb_feedback_replies`: черновик (LLM-агент или ручной) → одобрение → отправка в WB. Зеркало вопросов — `wb_questions` (WB отдаёт questions без `isAnswered`-поля: отвечен = `answer != null`; `subjectName` в productDetails НЕ приходит — `subject` остаётся NULL, резолв по nm_id через Nomenclature на фронте/в аналитике).
- **WB-клиент** (`integrations/wb_api.py`): `answer_feedback` (PATCH `/api/v1/feedbacks`, body `{id, text}`), `get_questions` (GET `/api/v1/questions`, take ≤ 10000, order dateAsc/dateDesc, dateFrom/dateTo unix), `answer_question` (PATCH `/api/v1/questions`, body `{id, answer: {text}}`). Те же retry/circuit breaker, что у отзывов.
- **Синк вопросов**: `reply_service.sync_project_questions` — isAnswered false+true, пауза 1.1 сек между вызовами (лимит WB **1 rps** на методы отзывов/вопросов!), upsert `on_conflict (project_id, wb_id)`. Job `wb_questions_sync` — 03:25 MSK (сразу после отзывов); on-demand — `POST /reviews/questions/sync`.
- **Агенты** (`wb_reply_agents`, CRUD `/reviews/reply-agents` + `POST .../run`): фильтры target/star_levels/nm_ids, `rules` (тон/ограничения) + `examples` (few-shot), сменный LLM-провайдер (`services/ai/reply_llm.py`, транспорт переиспользует `complaint_llm` — ключ `COMPLAINT_LLM_API_KEY`). Прогон: неотвеченные цели БЕЗ открытого ответа (draft/approved/sent — «занято»), кап `_RUN_LIMIT=25`; read-транзакция закрывается ДО походов в LLM; **ВСЕГДА status=draft** — `auto_send` осознанно игнорируется (только ручное одобрение, см. ниже «защита от выдумок»).

### База знаний товаров и защита от выдумок
Автоответы строятся СТРОГО из базы знаний `wb_product_kb` (эталонные пары «типичный вопрос → правильный ответ» по nm_id) — модель не придумывает характеристики/сроки/состав.
- **Импорт из архива**: `reply_service.import_kb_from_answered_questions` — отвеченные вопросы зеркала (`is_answered=true`, `answer_text` непуст) → записи КБ (`topic` — эвристика по ключевым словам: Размер/Доставка/Качество/Состав/Цвет/Комплект/Гарантия/Прочее, `source='import'`, `question_example`=текст вопроса). Дедуп по `(project_id, nm_id, md5(нормализованный вопрос))` — частичный unique-индекс `uq_wb_product_kb_project_nm_qhash`, повторный импорт идемпотентен. On-demand — `POST /reviews/kb/import`.
- **Подбор для генерации**: enabled-записи по nm_id цели, скоринг — пересечение слов вопроса с `topic` (×2) и `question_example` (×1), кап `_KB_LIMIT=30`. Промпт содержит записи с id (модель возвращает `used_kb_ids`).
- **Контракт LLM** (`reply_llm.draft_reply` → `{reply_text, needs_info, used_kb_ids}`): системный промпт требует отвечать только из приведённых фактов КБ; информации не хватает — `needs_info=true` → черновик `status=draft, needs_info=true` с пустым `draft_text` (UI подсвечивает, одобрить пустой нельзя — `update_draft` валидирует).
- **Нет записей КБ по nm_id** → LLM не вызывается вовсе: сразу draft-заглушка `needs_info=true`.
- **Нет LLM-ключа** (`COMPLAINT_LLM_API_KEY` пуст) → fallback `kb_direct`: точное/почти точное совпадение вопроса с записью КБ (нормализованный текст или тема + Jaccard ≥ 0.6) → `draft_text` = эталонный ответ КБ как есть, `generation='kb_direct'`; иначе `needs_info=true`. Поле `generation`: `llm` | `kb_direct` | `None` (ручной/заглушка).
- **Только ручное одобрение**: автоотправка захардкожена выключенной — `run_reply_agent` игнорирует `agent.auto_send`, каждый черновик `draft` (поле оставлено в модели для совместимости API/фронта).
- **API**: `GET /reviews/kb/products` (nm_id + число записей + имя/артикул из зеркала вопросов, фолбэк — зеркало отзывов), `GET /reviews/kb?nm_id=&enabled=`, `POST /reviews/kb`, `PATCH /reviews/kb/{id}` (`enabled=false` — мягкое отключение), `DELETE /reviews/kb/{id}` (реальный delete), `POST /reviews/kb/import`.
- **Архивный досинк вопросов** (dev, TLS-фильтр локальной сети): `scripts/dev_wb_questions_archive_sync.py` — все страницы `isAnswered=true` через SOCKS5 хоста (take ≤ 10000, пауза 1.1 сек — лимит 1 rps), ключ из `integration_keys` по id (расшифровка в памяти, не печатается), upsert тем же сервисным кодом, коммит постранично.

### Зеркало карточек WB (wb_product_cards)
Публичные API WB **без ключа продавца** (`services/wb_cards_service.py`):
- **card.json**: `https://basket-XX.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/card.json` (`vol=nm//100000`, `part=nm//1000`). Поля: `imt_name`, `subj_name`, `description`, `contents` (комплектация), `options[] {name, value, charc_type, is_variable, variable_values[]}`, `media.photo_count` (число фото — перебор по 404 НЕ нужен).
- **Basket-таблица** (`_BASKET_BOUNDS`): vol ≤ 143→1, 287→2, 431→3, 719→4, 1007→5, 1061→6, 1115→7, 1169→8, 1313→9, 1601→10, 1655→11, 1919→12, 2045→13, 2189→14, 2405→15, 2621→16, 2837→17, 3053→18, 3269→19, 3485→20, 3701→21, 3917→22, 4133→23, 4349→24, 4565→25, 4877→26, 5189→27, 5501→28, 5813→29. Дальше — экстраполяция +312/basket, но она **неточна** (замерено живьём: vol 8638→38, 8962–9104→39, 9529→40, 10139→41, 11276→43): при не-200 в зоне vol>5813 fetcher сканирует соседние basket (−1,−2,+1,−3,+2,−4) и запоминает реальный (`fetch_nm_card → basket`) — URL фото строятся по нему.
- **detail**: `https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=0&nm={nm}` → `products[0].brand` (в card.json бренда НЕТ), `pics` (запасной счётчик фото). Сбой detail не фатален (brand=NULL). **Грабля**: WAF card.wb.ru (`Status-NO-Id: PG-43-EL`) с dev-egress через SOCKS5 отдаёт 403 на raw-сокет клиент (JA3-фингерпринт linux-OpenSSL; с Windows-хоста тот же прокси — 200), поэтому в dev brand не заполняется; card.json/basket-хосты WAF не режет.
- **Фото**: `/vol{vol}/part{part}/{nm}/images/big/{i}.webp`, i=1..photo_count (кап 10). Байты НЕ скачиваем — в зеркале только URL. **TODO**: извлечение фактов с фото (размерные сетки, состав на этикетке) отложено — нужен vision LLM.
- **Синк** (`sync_project_cards`): nm_id из КБ + зеркал вопросов/отзывов (или переданный список), upsert по `(project_id, nm_id)`, троттлинг 0.5 сек, 404/ошибки пропускаются с подсчётом (прогон не валится), промежуточный коммит каждые 25 карточек. On-demand — `POST /reviews/cards/sync` (+опц. `nm_ids`), чтение — `GET /reviews/cards/{nm_id}` (карточка с фото для UI). В dev сеть идёт через SOCKS5 (`WB_CARDS_SOCKS_PROXY="host:port"`, raw-сокет — httpx без socksio); скрипт прогона — `scripts/dev_wb_cards_sync.py`.
- **Импорт в КБ** (`import_kb_from_cards`, `source='card'`): description → 1 запись `topic='Описание'` (>3000 симв. — резка по границе предложения; в зеркале — целиком), contents → `topic='Комплект'` («Комплектация: …»), каждая характеристика → answer «{name}: {value}», topic — `map_characteristic_topic` (Цвет/Состав/Комплект/Гарантия/Размер по ключевым словам имени, иначе Прочее). Внутри карточки — дедуп по нормализованному answer (contents и характеристика «Комплектация» у WB обычно дублируют один факт). Между прогонами — дедуп `md5("card:{nm}:{ключ}")`; повторный импорт ОБНОВЛЯЕТ изменившиеся answer/topic (upsert по hash), дублей нет; `enabled` (мягкое отключение продавцом) ресинк не трогает; записи `manual`/`import` не затрагиваются.
- **Отправка**: `send_pending_replies` — approved-очередь, троттлинг 1.1 сек, кап 50/прогон; успех → sent + `sent_at` + `is_answered`/`answer_text` в зеркале; ошибка WB → error + текст (429 → остановка прогона). Job `wb_replies_sender` — каждые 2 мин; кнопка — `POST /reviews/replies/send` (202 + pending, отправка фоном через `asyncio.create_task` в своей сессии).
- **Ручные черновики** (UI): `POST /reviews/replies` (manual, цель обязана быть в зеркале), `PATCH /reviews/replies/{id}` (`text` → final_text; `action` approve/reject/reopen; sent не редактируется), `GET /reviews/replies?status=` (с данными цели из зеркала + counts по статусам), `GET /reviews/questions` (зеркало вопросов).
- **Маппинг ошибок**: 429 → 429 + Retry-After; CircuitOpen → 503; `httpx.HTTPError` (сеть) → 503; ValueError WB → 502.

### Cache invalidation
После WB sync инвалидировать **точечно**: `reports:opiu`, `reports:wb_bdr`, `reports:dashboard`. Никогда не сбрасывать все ключи разом — worker starvation.
Отзывы: после `POST /reviews/sync` — `invalidate_cache("reviews:summary:project_id={id}")`.

## Зависимости
- `DOMAIN_TRANSACTIONS` — WB-выплаты матчатся с транзакциями.
- `DOMAIN_REPORTS` — БДР/ОПИУ строятся на `wb_finance_rows`.
- `DOMAIN_COST` — `nomenclature` для себестоимости в воронке.
- `DOMAIN_LOCALIZATION` — расчёт ИЛ/ИРП на полях `wb_funnel_daily`.

## Грабли
- **Дублирование retry-логики** — `wb_funnel_api`, `wb_advertising_api`, `wb_supplier_api` повторяют одинаковую retry-логику; следует использовать `resilience.retry_with_backoff`.
- **TOCTOU в scheduler locks** — `_backfill_locks`: проверка `locked()` + `acquire` не атомарны.
- **`wb_finance_sync` partial commit** — при падении mid-page часть данных уже закоммичена.
- **Float в `cost_price`** — `funnel/sync.py` использует float division вместо `Decimal`.

## Файлы
- `integrations/wb_api.py` — WB Statistics/Content/Feedbacks API клиент (отзывы, вопросы, ответы).
- `services/reply_service.py` — автоответы: синк вопросов, агенты, черновики, отправка.
- `services/ai/reply_llm.py` — LLM-генерация черновиков ответов (транспорт из `complaint_llm`).
- `scheduler/jobs/wb_questions_sync.py`, `scheduler/jobs/wb_replies_sender.py` — синк вопросов (03:25 MSK) и отправка ответов (2 мин).
- `scripts/register_wb_key.py` — одноразовая регистрация WB-ключа (ключ из env, не из файла).
- `scripts/dev_wb_socks_sync.py` — DEV-обход TLS-фильтрации локальной сети (синк через SOCKS5 хоста).
- `scripts/dev_wb_questions_archive_sync.py` — DEV-досинк архивных (отвеченных) вопросов через SOCKS5 (для базы знаний).
- `scripts/dev_wb_cards_sync.py` — DEV-синк зеркала карточек WB через SOCKS5 + импорт КБ из карточек (source='card').
- `services/wb_cards_service.py` — зеркало карточек: basket-API, fetch (raw-сокет/SOCKS5), upsert, импорт КБ из карточек.
- `integrations/resilience.py` — `CircuitBreakerRegistry` (per-project) + `retry_with_backoff`.
- `services/funnel/` — обёртки WB API, оркестратор синхронизации, анализ, backfill, capital, аномалии, рекламные кампании.
- `services/wb_finance_sync.py` (+ `wb_finance_helpers.py`) — синхронизация WB Finance Report.
- `services/wb_cancel_sync.py` — синхронизация статистики отмен.
- `services/integrations_service.py` — управление API-ключами.
- `services/tariff_service.py` — управление тарифами WB.
- `services/warehouse_stock_service.py`, `services/stock_forecast_service.py` — остатки и прогноз.
- `scheduler/jobs/` — фоновая синхронизация (`funnel.py`, `wb_finance.py`, `wb_stocks.py`).
- `routers/integrations.py`, `routers/funnel.py`, `routers/reports_stock.py` — HTTP endpoints.
- `models/integrations.py`, `models/wb_finance.py`, `models/wb_order_cancel.py`, `models/wb_tariff.py`, `models/wb_product_kb.py`, `models/wb_product_cards.py` — ORM.
- `utils/crypto.py` — шифрование API-ключей.
