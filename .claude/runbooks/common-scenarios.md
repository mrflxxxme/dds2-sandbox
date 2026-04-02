# Runbooks — типовые сценарии DDS2

## 1. Новый API endpoint (backend)

```
1. Прочитать backend/MAP.md + DOMAIN_*.md по теме
2. Model → alembic revision --autogenerate
3. Schema (request + response) в schemas/
4. Service в services/ (project_id, is_deleted, limit)
5. Router в routers/ (HTTP only, делегирует service)
6. Тест в tests/ (fixtures → test CRUD → edge cases)
7. /smoke → /verify → коммит
```

## 2. Новая страница (frontend)

```
1. Тип в types/api.ts
2. Метод в lib/api/{domain}.ts + экспорт в lib/api.ts
3. Страница в app/(main)/p/[slug]/{page}/page.tsx
4. Паттерн: 'use client' → useState → useCallback → useEffect
5. Обязательно: loading + error + empty + data states
6. Таблицы с exportName, числа через formatNumber
7. Обернуть в PageGuard если нужны права
8. /smoke → проверить в браузере
```

## 3. Баг-фикс

```
1. Воспроизвести (логи / тест)
2. Найти корень проблемы (НЕ симптом)
3. Написать тест который падает
4. Минимальный фикс
5. Тест проходит → /smoke → коммит fix:
```

## 4. Новая миграция БД

```
1. Изменить Model
2. docker compose exec backend alembic revision --autogenerate -m "описание"
3. Проверить сгенерированный файл (типы, индексы, defaults)
4. docker compose exec backend alembic upgrade head
5. Обновить Schema если нужно
6. /smoke
```
**ВАЖНО:** миграции ТОЛЬКО последовательно, никогда параллельно.

## 5. Новый домен/модуль (полный цикл)

```
1. /plan — ТЗ на подтверждение
2. Model + Migration (Фаза 1)
3. Backend: Schema → Service → Router → Test  }  параллельно
   Frontend: Type → API method → Page          }  (Фаза 2)
4. /verify → коммит feat:
5. Обновить CLAUDE.md таблицу доменов
6. Создать DOMAIN_*.md если сложный домен
```

## 6. Рефакторинг

```
1. /plan — показать что и зачем меняется
2. Написать/обновить тесты ДО рефакторинга
3. Рефакторить малыми шагами, каждый шаг — тесты проходят
4. /verify после каждого шага
5. Коммит refactor:
```

## 7. Интеграция с WB API

```
1. Прочитать DOMAIN_WB.md
2. Semaphore для rate limiting
3. Retry-After header → ждать
4. Partial save — сохранять данные порциями
5. sync_log обновлять в finally (ВСЕГДА)
6. Тест с мок-данными
```

## 8. Backend + Frontend фича параллельно

```
1. /plan → ТЗ → подтверждение
2. Фаза 1: Model + Migration + Schema (один поток)
3. Фаза 2 (два параллельных агента):
   Agent 1 (backend): Service → Router → Tests
   Agent 2 (frontend): Type → API → Page
4. Фаза 3: /verify → коммит
```
Файлы НЕ пересекаются — безопасно параллелить.

## 9. Матрица параллельных агентов

Главное правило: **файлы НЕ должны пересекаться**. Количество агентов не ограничено, ограничены домены.

### Безопасные комбинации
| Agent 1 | Agent 2 | Agent 3 | Файлы |
|---------|---------|---------|-------|
| Backend (services, routers) | Frontend (app, lib, types) | — | 0 пересечений |
| Backend | Frontend | Docs (DOMAIN_*, CLAUDE.md) | 0 пересечений |
| Backend service A | Backend service B | Frontend | Ок если разные файлы в backend |

### Опасные комбинации (НЕ ДЕЛАТЬ)
| Комбинация | Почему опасно |
|-----------|---------------|
| Backend + Тесты того же сервиса | Оба пишут в один service/test файл |
| Frontend web + Frontend TMA | Общие types/api.ts, lib/api.ts |
| Два агента + одна миграция | Alembic head conflict |
| Backend + Migration параллельно | Migration зависит от модели |

### Правила
- Миграции — ТОЛЬКО последовательно, ОДИН агент
- `types/api.ts` и `lib/api.ts` — ОДИН агент за раз
- `globals.css` — ОДИН агент за раз
- Если нужен общий файл — один агент делает, второй ждёт
