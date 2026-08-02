# DECISIONS-LOG — модуль «Дизайн карточек»

<!-- HEAD-SUMMARY: append-only журнал решений: ратификации архитектора и owned-решения агентов (передняя растяжка, CHARTER §7). Пост-фактум аудит-трейл. Формат записи — CHARTER §11. Руками не переписывать, только дописывать. -->

### 2026-08-02T12:00:00Z | spec | arch · ратифицировано архитектором
- Fork: Формат спецификации модуля
- Decision: Пакет в репо `docs/specs/design/` (keystone CHARTER + 8 phase-specs + STATUS + DECISIONS-LOG + CONTRACT после Ф2) по lean-адаптации WIZOR; роли агентов — маппинг на существующий ростер DDS2 без новых агентов
- Rationale: Явный выбор архитектора (AskUserQuestion, сессия 2026-08-02); DDS2 уже имеет харнесс (CLAUDE.md, /review, /verify) — дублирование создало бы второй центр тяжести
- Reversibility: reversible

### 2026-08-02T12:00:00Z | spec | arch · ратифицировано архитектором
- Fork: Маппинг ролей PRD на RBAC DDS2
- Decision: Через `member_role`: ведущий дизайнер = owner/admin (is_lead); менеджер и дизайнер = editor, различаются полями задачи (автор/исполнитель); руководитель = любой с page-ключом `design-tasks`. Без новых сущностей
- Rationale: Явный выбор архитектора (сессия 2026-08-02)
- Reversibility: reversible

### 2026-08-02T12:00:00Z | spec | arch · ратифицировано архитектором
- Fork: Состав волн и темп
- Decision: Полный скоуп PRD (оба этапа, включая календарь), asap; в спецификацию заложены оркестрация, тесты-гейты, аудит и правила эскалации: агентам свобода, чувствительные зоны (миграции, models/schemas, rbac, push/деплой) под охраной tripwire §8
- Rationale: Явный выбор архитектора (сессия 2026-08-02): «делаем агентами + 1 человеком на контроле, asap»
- Reversibility: reversible

### 2026-08-02T12:00:00Z | spec | arch · ратифицировано архитектором
- Fork: Срочность задачи — булев «срочно» (точно PRD v4) vs 3 уровня приоритета (ТЗ v1)
- Decision: Булев `is_urgent` (Р9); подсветка-сигнал, порядок не меняет; уровни → потом миграцией при потребности
- Rationale: Явный выбор архитектора (сессия 2026-08-02): точно по PRD, меньше ложных градаций
- Reversibility: reversible

### 2026-08-02T12:00:00Z | spec | impl · 🟡 на ратификацию
- Fork: Технические решения Р1–Р8, Р10–Р14 (статусная модель, товарная привязка, sort_order, board API, viewed-отметка, TG-уведомления, календарь, dnd, кэш, RBAC-на-бэке, терминология, обязательные поля, нумерация)
- Decision: Рекомендации агентов сведены в CHARTER §2 со статусом «🟡 на ратификацию»; действуют как рабочие допущения до явного подтверждения архитектора; ратификация (или правки) переводят их в ✅ отдельной записью здесь
- Rationale: Выведены из PRD v4.0 + исследования кодовой базы (доноры payment_request / ab_photo_tests / draft_staleness_watch) и WIZOR-методологии; архитектором НЕ подтверждались — честный статус вместо приписанной ратификации
- Reversibility: до Ф0 — reversible; схемные (Р1, Р2, Р14) hard-to-reverse после мержа Ф0 — поэтому подпись Ф0 обязательна ДО мержа (CHARTER §8)

### 2026-08-02T14:00:00Z | spec | arch · ратифицировано архитектором
- Fork: Ратификация свода Р1–Р8, Р10–Р14
- Decision: Все технические решения CHARTER §2 ратифицированы без правок («Ратифицирую всё»); статусы переведены в ✅
- Rationale: Явное подтверждение архитектора (сессия 2026-08-02)
- Reversibility: см. по-решению

### 2026-08-02T14:00:00Z | spec | arch · ратифицировано архитектором
- Fork: Контур исполнения и доставки (Р16)
- Decision: Изолированная песочница: полная private-копия репо в GitHub-профиле архитектора (remote `sandbox`), все коммиты фаз — только туда, в `origin` не пушится ничего; деплоя/хостинга нет; итог — локально запускаемый `docker compose` стек с модулем. Промежуточные подписи Ф0/Ф2 свёрнуты в финальную приёмку; исполнение фаз непрерывное по готовности гейтов
- Rationale: Директива архитектора 2026-08-02: «тестировать новую сборку изолированно, не затрагивая прод, не пушить в основной репозиторий, не размещать на хостингах»
- Reversibility: reversible

### 2026-08-02T15:30:00Z | F0 | arch · решение lead
- Fork: Асимметрия ON_HOLD⇄REVISION в ратифицированном словаре Р1 (HIGH code-ревью: REVISION→ON_HOLD есть, обратного ребра нет — «возврат откуда отложили» для Правок невозможен)
- Decision: Добавлено ребро ON_HOLD→REVISION; словарь, докстринг, спека F0/CHARTER Р1 и golden-snapshot-тест синхронизированы
- Rationale: Механика held_from_status (Р1) и PRD «отложенные возвращаются к работе» обещают симметрию; ребро — исправление бага спеки, не смена продуктового поведения. Вынесено архитектору в отчёте пост-фактум
- Reversibility: reversible (убрать ребро — одна строка + тест)

### 2026-08-02T15:30:00Z | F0 | impl · owned-решения фазы
- Fork: Свод owned-решений Ф0 и fix-цикла T3-ревью
- Decision: (1) DB-гарантия изоляции детей: uq (id, project_id) на design_tasks/design_submissions + составные FK (task_id, project_id) у 4 детей и (submission_id, project_id) у файлов; (2) отдельные FK-индексы task_id ×4 + ix_design_tasks_author; (3) три индекса доски — partial WHERE is_deleted=false; (4) partial-unique номера продублирован в модель (анти-autogenerate-дрейф); (5) константы переименованы: DESIGN_BOARD_STATUSES/DESIGN_ACTIVE_STATUSES (коллизия с ab_photo_tests); (6) схемы: URL cap 1000, description 5000, комментарии 2000, nm_id ge=1 lt=2^63, move только в 6 board-статусов; (7) comments relationship без delete-orphan (iron rule 3); (8) docker-compose.sandbox.yml закоммичен как часть деливерабла Р16 (секретов нет)
- Rationale: Findings T3-ревью (db W1–W3, W6; security 1–3; code M1–M3, L2–L3) — все закрыты до заморозки схемы; полный список в отчётах ревьюеров
- Reversibility: аддитивные constraint'ы/индексы — reversible до данных; после наполнения (1) hard-to-reverse

### 2026-08-02T17:00:00Z | F1 | arch · решения lead (две развилки ревью)
- Fork: (а) транзакция+row-lock через MinIO-загрузки (H1 db = H3 code = №1 sec); (б) SVG/mime-обход allowlist (№2 sec, паттерн повторяется в донорах counterparty/payment_requests)
- Decision: (а) вариант А — короткие транзакции: version_no+строка версии коммитятся до заливки, файлы вне лока, вторая транзакция на строки файлов, окно закрыто гвардом «PENDING с файлами»; (б) вариант Б для модуля design — blocklist +{svg,svgz,html,htm,xhtml,xml,mjs,php}, приоритет расширения над клиентским mime; доноры вне скоупа — заведён отдельный таск в основной репо
- Rationale: (а) канон learnings (клин пула 2026-07-16), рекомендация двух ревьюеров; (б) stored-XSS вектор дешевле закрыть сейчас; Ф2 обязан добавить attachment+nosniff на download
- Reversibility: reversible

### 2026-08-02T17:00:00Z | F1 | impl · owned-решения фазы и fix-цикла
- Fork: Свод owned-решений Ф1
- Decision: advisory-lock нумерации pg_advisory_xact_lock(0xDE516, project_id) (совместим с PgBouncer, под ним же max(sort_order)); advisory 0xDE517 на move_task (анти-deadlock); read-side вынесен в queries.py (<500 строк); get_board — row_number() OVER (PARTITION BY status) <= 200, ACCEPTED внутри партиции по accepted_at DESC; populate_existing на FOR UPDATE; вердикт бьёт в переданную версию + запрет второй PENDING; вход в REVISION авто-отклоняет текущую PENDING (гвард честный); assign(None) вне NEW/ASSIGNED/ON_HOLD запрещён; notify-диспатч после commit на всех путях переходов; delete_task через soft_delete (author|lead); NEW всегда снимает исполнителя; can_edit автора — все статусы кроме REVIEW и терминалов; MAX_FILES_PER_SUBMISSION=10, суммарно 100 МБ; download закрыт по is_deleted задачи; tracked_share по созданным в окне
- Rationale: спека F1 + carry-over трёх ревью Ф0/Ф1; детали в отчётах агентов impl-f1/fix-f1
- Reversibility: reversible
