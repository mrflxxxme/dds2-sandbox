---
description: Стандартный рабочий процесс при любом изменении кода в проекте DDS
---

// turbo-all

# Рабочий процесс DDS

## 🗺️ Шаг 0: Определи домен и прочитай контекст

> **ПЕРЕД ЛЮБЫМ ИЗМЕНЕНИЕМ** определи, к какому домену относится задача, и прочитай соответствующий файл.

| Если задача про... | Прочитай |
|---------------------|----------|
| Импорт, транзакции, категоризация | `backend/DOMAIN_TRANSACTIONS.md` |
| Отчёты, ДДС, БДР, ОПИУ, дашборд | `backend/DOMAIN_REPORTS.md` |
| Заказы, платежи, таможня, кэшфлоу | `backend/DOMAIN_PLANNING.md` |
| Себестоимость, номенклатура, пошлины | `backend/DOMAIN_COST.md` |
| WB API, воронка, синхронизация | `backend/DOMAIN_WB.md` |
| Фронтенд страницы/компоненты | `frontend-react/DOMAIN_FRONTEND.md` |

Если задача затрагивает **несколько доменов** — прочитай ВСЕ затронутые файлы.
Обрати внимание на секции **Dependencies** и **Known Issues** — там описаны связи между доменами.

---

## ⛔ Архитектурные правила (ОБЯЗАТЕЛЬНЫЕ)

> **Проверяй ЭТИ правила при КАЖДОМ изменении кода. Нарушил — исправь ДО коммита.**

### Backend

| Правило | Что делать |
|---------|-----------|
| **Бизнес-логика в services/** | Роутер тонкий — вызывает service-функцию, НЕ содержит логику |
| **`utcnow()` из `backend/utils/time`** | Не `datetime.utcnow()` и не `datetime.now(timezone.utc)` — оба запрещены |
| **`Mapped[]` + `mapped_column()`** | Не `Column()` — новый SQLAlchemy стиль |
| **`Numeric(18,2)` для денег** | Не `Float` — точные вычисления |
| **SQL: `:param` binding** | Не f-string — безопасность |
| **`project_id`** | Каждый запрос фильтрует по project_id |
| **`is_deleted == False`** | Фильтр для SoftDeleteMixin моделей |
| **Пагинация (`limit/offset`)** | Для всех list-эндпоинтов |
| **`@cached(ttl=300)`** | Для тяжёлых GET (отчёты, агрегации) + `invalidate_cache()` при мутации |
| **Модели в `models/domain.py`** | Не в монолитном `models.py` |
| **Схемы в `schemas/domain.py`** | Не в монолитном `schemas.py` |
| **Logging** | `logger = logging.getLogger("dds.module")` |

### Frontend

| Правило | Что делать |
|---------|-----------|
| **Loading / Error / Empty states** | Обязательны в каждом компоненте |
| **`formatNumber()` / `formatDate()`** | Для всех чисел и дат |
| **«Excel»** | Кнопка для каждой таблицы |
| **`useCallback`** | Для функций загрузки данных |
| **Типы в `types/api.ts`** | Не inline |
| **CSS классы из `globals.css`** | Не inline стили |

---

## ⛔ Docker — запрещённые команды

> **НИКОГДА** не запускай `docker compose exec backend python3 -c "from backend..."` для ad-hoc скриптов.
> `backend/services/__init__.py` тянет все модули → scheduler → Redis → **процесс зависает навсегда**.
>
> Для тестов: `docker compose exec backend pytest tests/...`
> Для скриптов: standalone Python локально (без `import backend.*`)

---

## Обязательные правила процесса

### 1. Документация
- **Перед началом работы** — прочитай `AGENTS.md` + доменный DOMAIN_*.md файл.
- **После завершения изменений** — обнови DOMAIN_*.md если изменились таблицы, зависимости или known issues.

### 2. Тесты — запусти ПЕРЕД коммитом
```bash
docker compose exec backend pytest tests/ -x --tb=short
bash scripts/check_conventions.sh
```
> Если тесты упали — исправь ДО коммита!

### 3. Git — коммит и пуш
- **После каждого завершённого блока работы** — сделай коммит и пуш на GitHub.
- Коммиты на русском: `feat:` / `fix:` / `infra:` / `refactor:` / `test:`

**Всегда работаем в ветке `dev`!** Ветка `main` — стабильная продакшн версия.

```bash
git checkout dev
git add -A && git commit -m "feat: описание изменений"
git push origin dev
```

### 4. Cross-Domain Changes (изменения затрагивающие несколько доменов)

Если изменение затрагивает несколько доменов:
1. Прочитай ВСЕ затронутые DOMAIN_*.md
2. Проверь секцию **Dependencies** — какие модули зависят от изменяемого кода
3. Обнови кэш-инвалидацию во ВСЕХ затронутых сервисах
4. Запусти ПОЛНЫЙ набор тестов (не только для своего модуля)
5. Обнови DOMAIN_*.md если изменились зависимости

### 5. Сборка и проверка
**Изменения кода** (.py, .tsx) — hot-reload автоматический.
**Изменения зависимостей** (package.json, requirements, Dockerfile) — нужна пересборка:
```bash
docker compose up -d --build backend      # backend changes
docker compose up -d --build frontend-react  # frontend changes
```

### 6. Ключевые файлы
| Область | Путь |
|---------|------|
| API клиент | `frontend-react/src/lib/api.ts` |
| TypeScript типы | `frontend-react/src/types/api.ts` |
| Страницы | `frontend-react/src/app/p/[slug]/<module>/page.tsx` |
| Backend роутеры | `backend/routers/*.py` |
| Backend сервисы | `backend/services/*.py` |
| Модели БД | `backend/models/*.py` |
| Схемы Pydantic | `backend/schemas/*.py` |
| Конфигурация | `backend/config.py` |
