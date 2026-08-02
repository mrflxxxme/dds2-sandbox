# Локальный запуск песочницы — модуль «Дизайн карточек»

<!-- HEAD-SUMMARY: как поднять изолированный docker-стек с модулем на своей машине. Контур Р16: копия репо в личном GitHub, прод и основной локальный стек не затрагиваются, наружу ничего не публикуется. -->

Изолированный контур: собственные контейнеры, собственные тома, порты только на `127.0.0.1`, внешние интеграции (WB, Telegram, AI) выключены. Основной стек `dds2` и прод не затрагиваются — можно запускать параллельно.

## 0. Что нужно

Docker Desktop (проверено на 28.5.1) и ~4 ГБ свободной памяти. Git. Больше ничего — Python и Node внутри образов.

## 1. Получить код

```bash
git clone -b claude/dds2-card-design-spec-8a2392 https://github.com/mrflxxxme/dds2-sandbox.git dds2-sandbox
```

Если репозиторий уже склонирован — `git fetch && git checkout claude/dds2-card-design-spec-8a2392 && git pull`.

## 2. Создать `.env`

Из корня склонированного репозитория:

```bash
cp docs/specs/design/sandbox.env.example .env
```

Файл уже содержит рабочие значения для локального запуска. Менять ничего не нужно; при желании замените `SECRET_KEY` на свой (`openssl rand -hex 32`).

## 3. Поднять стек

```bash
docker compose -p dds2-sandbox -f docker-compose.yml -f docker-compose.sandbox.yml up -d --build
```

Два `-f` обязательны: без второго подхватится dev-override с портами 8000/3000/5432, которые конфликтуют с основным стеком. Первая сборка — 5–15 минут (образы backend и frontend), последующие запуски секунды.

Миграции применяются автоматически при старте backend (`alembic upgrade head` в entrypoint) — отдельная команда не нужна.

Проверить, что всё поднялось:

```bash
docker compose -p dds2-sandbox ps
```

Ожидаемо 7 контейнеров в статусе healthy: backend, frontend-react, worker, db, pgbouncer, redis, minio.

| Что | Адрес |
|---|---|
| Интерфейс | http://127.0.0.1:13000 |
| API напрямую (curl, Swagger `/docs`) | http://127.0.0.1:18000 |
| PostgreSQL (DBeaver и т.п.) | 127.0.0.1:15434, база `dds_db`, пользователь `dds`, пароль из `.env` |

Интерфейс сам проксирует `/api/*` на backend внутри docker-сети, поэтому для обычной работы достаточно порта 13000.

## 4. Создать пользователя и проект

Первый зарегистрированный пользователь становится владельцем своего проекта и автоматически получает доступ ко всем разделам, включая «Дизайн карточек».

```bash
curl -X POST http://127.0.0.1:18000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"lead","password":"sandbox12345","email":"lead@example.com"}'
```

Ответ сразу содержит `access_token` — отдельный вход не нужен. Возьмите его и создайте проект:

```bash
curl -X POST http://127.0.0.1:18000/api/v1/projects \
  -H "Content-Type: application/json" -H "Authorization: Bearer ВСТАВЬТЕ_ТОКЕН" \
  -d '{"name":"Песочница"}'
```

Дальше откройте http://127.0.0.1:13000, войдите теми же логином и паролем — раздел «Дизайн карточек» появится в меню в группе «Продажи».

Проверено на живой песочнице 2026-08-02: регистрация → создание проекта → `GET /design-tasks/board` отвечает 200 с шестью пустыми колонками, без каких-либо дополнительных настроек прав.

## 5. Что можно проверить в интерфейсе

Доска из шести столбцов с перетаскиванием, список вторым видом, календарь на месяц, экран загрузки команды, сквозной экран по брендам. Полный рабочий цикл: создать заявку (с подсказкой товара) → красная отметка у ведущего → назначить исполнителя → перетащить в «В работе» → сдать файлы → вернуть с причиной → пересдать → принять. Журнал переходов на деталке фиксирует каждый шаг.

Чего в песочнице не будет: подсказок товара по реальному каталогу (справочник номенклатуры пуст — можно завести пару строк вручную), картинок карточек WB (внешние запросы отключены, показывается заглушка), Telegram-уведомлений (бот не подключён — операции при этом проходят штатно, уведомления просто пропускаются).

## 6. Тесты и проверки

```bash
docker compose -p dds2-sandbox exec backend pytest tests/ -q
```

Только модуль:

```bash
docker compose -p dds2-sandbox exec backend pytest tests/test_design_models.py tests/test_design_state.py tests/test_design_service.py tests/test_design_permissions.py tests/test_design_notify.py tests/test_design_ab_bridge.py tests/test_api_design_tasks.py -q
```

Типы и юнит-тесты фронта:

```bash
docker compose -p dds2-sandbox exec frontend-react npx tsc --noEmit
```

## 7. Обновление, остановка, удаление

После правок кода образ надо пересобрать — исходники запечены в образ, bind-mount'ов нет:

```bash
docker compose -p dds2-sandbox -f docker-compose.yml -f docker-compose.sandbox.yml up -d --build backend worker
```

Остановить, сохранив данные:

```bash
docker compose -p dds2-sandbox stop
```

Удалить полностью вместе с базой и файлами:

```bash
docker compose -p dds2-sandbox -f docker-compose.yml -f docker-compose.sandbox.yml down -v
```

## 8. Если что-то не так

**`POSTGRES_PASSWORD must be set in .env`** — забыт шаг 2 или команда запущена не из корня репозитория.

**Порт занят** — сдвиньте порты в `docker-compose.sandbox.yml` (там три строки) и перезапустите.

**Интерфейс открывается, но данные не грузятся** — посмотрите `docker compose -p dds2-sandbox logs backend --tail 50`; чаще всего backend ещё не прошёл миграции (30–60 секунд после старта).

**Регистрация отвечает 403** — в `.env` выставлен `REGISTER_ENABLED=false`; верните `true` и пересоздайте backend командой из раздела 7.

**Пересобирали фронт — разлогинило** — пересборка фронта перезапускает и backend (зависимость compose), выданные токены становятся недействительными. Просто войдите заново.
