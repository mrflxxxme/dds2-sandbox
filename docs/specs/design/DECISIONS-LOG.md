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

### 2026-08-02T20:30:00Z | F2 | arch · решения lead (fix-цикл до фриза)
- Fork: Развилки трёх ревью Ф2: (а) /all-projects vs page-гейт Р11; (б) 503-глотание на submissions; (в) minio_path в ответах; (г) пустая PENDING клинит задачу; (д) SVG-MIME; (е) double-submit в переиспользуемую PENDING
- Decision: (а) Р11 приоритетнее спекового waiver — сквозной экран скоупится по get_effective_pages; (б) HTTPException passthrough, MinIO-down = 503 (канон ретраев фронта); (в) minio_path удалён из Out-схем до фриза (канон counterparty); (г) пустая PENDING переиспользуется повторной сдачей той же version_no; (д) MIME-блок {image/svg+xml, text/html, xhtml, xml} явно; (е) при переиспользовании PENDING файлы ЗАМЕНЯЮТСЯ, не сливаются («повторить сдачу» = чистая сдача)
- Rationale: Findings api-designer 1–12, code HIGH-1/2, security 1–2; все закрыты до подписи фриза — после стали бы breaking для Ф3
- Reversibility: reversible

### 2026-08-02T19:00:00Z | F2 | impl · owned-решения фазы (роутер + фриз контракта)
- Fork: Свод owned-решений Ф2 (HTTP-слой, carry-over ревью Ф1)
- Decision: (1) все write-ручки задачи возвращают свежую DesignTaskDetail (единый формат для UI-рефреша; материалы/комментарии — свой Out-объект, DELETE — 204); (2) маппинг ValueError→404 только по точным текстам {«Задача не найдена», «Версия сдачи не найдена»} (frozenset в роутере), остальные ValueError → 400, PermissionError → 403, HTTPException files.py — сквозной; (3) POST /submissions: роутер создаёт версию сервисом и переводит в REVIEW той же матрицей change_status (гвард видит PENDING с файлами); капы на входе: ≤10 файлов (400), файл ≤20 МБ и суммарно ≤100 МБ (413), порядок проверок каждого файла — донор pr:710; (4) permissions: схема расширена до ПОЛНОГО набора 15 флагов compute_permissions, фильтрация-пересечение в queries.get_task убрана, паритет закреплён тестом; (5) download-ручки: attachment; filename*=UTF-8'' + X-Content-Type-Options: nosniff; download_submission_file получил фильтр по sub_id из URL (честная принадлежность файла версии); (6) добавлен сервис files.delete_material (DesignMaterial — не SoftDelete-модель → жёсткий DELETE строки, право: автор материала | автор заявки | lead; сирота MinIO логируется warning); (7) календарь — queries.list_calendar: окно [1-е числа − 6 дн; последний день + 6 дн], limit 500; (8) /all-projects — queries.list_tasks_all_projects: скоуп подзапросом членства ProjectMember (is_deleted=false у членства и проекта), cap 200, project_name заполняется, обогащение по-проектно с сохранением глобального порядка; (9) member_role — отдельная dependency get_member_role (второй SELECT к ProjectMember за запрос; кэш не нужен при масштабе Р10); (10) комментарии в контракте Ф2 — только текст (DesignCommentIn body 1..2000), вложения комментариев — вне контракта (сервис-хелпер Ф1 остаётся)
- Rationale: спека F2 + обязательные carry-over ревью Ф1 (полный набор флагов §6.9, nosniff, капы Query, comment в move, порядок проверок файлов); DELETE материала нужен таблице спека, сервиса в Ф1 не было — добавлен в files.py, а не в роутер (правило 8)
- Reversibility: reversible (кроме пункта 4 после фриза CONTRACT.md — расширение схемы аддитивно, сужение = эскалация)
