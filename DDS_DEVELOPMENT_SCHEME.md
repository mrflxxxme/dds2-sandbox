# DDS Development Scheme — Full Architecture & Process Documentation

> Документ для анализа AI-агентом. Цель: рекомендации по улучшению скорости AI-агентов, безопасности и архитектуры.

---

## 1. ОБЩЕЕ ОПИСАНИЕ ПРОЕКТА

**DDS (Data-Driven Seller)** — система управленческого учёта для e-commerce (Wildberries).
Один разработчик + AI-агенты (Claude Code). Продакшен обслуживает реальных клиентов.

### Стек
| Слой | Технология | Версия |
|------|-----------|--------|
| Backend API | FastAPI + SQLAlchemy (async) | Python 3.11 |
| Database | PostgreSQL + PgBouncer | 15 |
| Cache | Redis | 7-alpine |
| Storage | MinIO (S3-compatible) | Latest |
| Frontend | Next.js + React + TypeScript | 15 / 19 / 5 |
| AI | Anthropic Claude (Sonnet/Haiku) | API |
| Infrastructure | Docker Compose + Nginx | Multi-server |
| CI/CD | GitHub Actions | Auto-deploy |
| Code Review | CodeRabbit AI | Per-PR |

### Масштаб кодовой базы
| Компонент | Файлов | Строк кода | Размер |
|-----------|--------|-----------|--------|
| Backend Python | 229 | ~44,000 | 6.0 MB |
| Frontend TypeScript | ~120 | ~25,000 | ~3.5 MB |
| Tests | 37 | ~8,000 | ~500 KB |
| Documentation | 30+ .md | ~5,000 | ~300 KB |
| **ИТОГО** | ~420 | ~82,000 | ~10 MB |

---

## 2. АРХИТЕКТУРА BACKEND

### 2.1 Слоистая архитектура (строго)
```
Router (HTTP, auth, validation) — 23 файла, 195 KB
  ↓
Service (бизнес-логика, кэш, внешние API) — 34 файла, 520 KB
  ↓
Model (ORM, без логики) — 23 файла, 150 KB
  ↓
Schema (Pydantic валидация) — 20 файлов, 55 KB
```

**Правило:** бизнес-логика ТОЛЬКО в services/. Роутеры — тонкий HTTP-слой.

### 2.2 Структура backend/
```
backend/
├── main.py              # 21K — FastAPI app, middleware, lifespan, audit
├── database.py          # 2.8K — AsyncSession, pool tuning, RLS
├── config.py            # 4.7K — Pydantic BaseSettings
├── auth.py              # 5.5K — JWT creation, user extraction
├── cache.py             # 4.8K — @cached decorator, invalidation
├── rate_limit.py        # 3.8K — Redis rate limiting
├── storage.py           # 4.0K — MinIO S3 client
├── rbac.py              # 3.2K — Role-based access control
├── project_context.py   # 2.2K — Multi-tenancy X-Project-Id
├── exceptions.py        # 1.8K — Custom HTTP exceptions
├── slow_query.py        # 1.5K — >500ms request logging
│
├── models/              # 23 файла — SQLAlchemy ORM
│   ├── mixins.py        # SoftDeleteMixin, TimestampMixin
│   ├── auth.py          # User, ProjectMember
│   ├── transactions.py  # Transaction
│   ├── integrations.py  # IntegrationKey, SyncLog, WbFunnelDaily
│   ├── planning.py      # Order, OrderItem, PlannedPayment
│   ├── cost.py          # CostOrder, CostOrderItem
│   ├── warehouse.py     # 14K — Warehouse, Stock, Receipt, Shipment, Transfer
│   ├── assembly.py      # AssemblyRequest, AssemblyItem
│   ├── supply_chain.py  # FactoryOrder, Vehicle
│   ├── refs.py          # Account, Counterparty, CategoryRule
│   ├── telegram.py      # TelegramAccount, BrandNote
│   └── ... (ещё 12 файлов)
│
├── schemas/             # 20 файлов — Pydantic request/response
├── routers/             # 23 файла — HTTP endpoints
├── services/            # 34 файла — бизнес-логика
│   ├── ai/              # AI multi-agent system
│   │   ├── orchestrator.py    # 9.5K — Multi-agent coordination
│   │   ├── agent.py           # 15K — Chat loop, Claude function calling
│   │   ├── executor.py        # 29K — 19 tools bridge
│   │   ├── memory.py          # 9.2K — Redis chat history
│   │   ├── llm_client.py      # 2.9K — Claude API wrapper
│   │   ├── agents/            # 8 файлов — domain-specific agents
│   │   ├── tools/             # 6 файлов — grouped tool implementations
│   │   └── prompts/           # 9 файлов — system prompts
│   ├── warehouse_stock_engine.py  # 36K — FIFO stock engine
│   ├── assembly_service.py        # 39K — assembly logistics
│   ├── warehouse_stock_service.py # 30K — stock queries
│   ├── fbo_supply_service.py      # 29K — FBO supplies
│   └── ... (ещё 30 файлов)
│
├── etl/                 # 10 файлов — import pipelines
│   ├── parsers/         # VTB, WB bank statement parsers
│   ├── service.py       # ETL orchestration
│   └── master_logic.py  # Main ETL workflow
│
├── integrations/        # 3 файла — external APIs
│   ├── wb_api.py        # 23K — Wildberries API client
│   ├── telegram_bot.py  # 20K — aiogram Telegram bot
│   └── resilience.py    # 7.8K — retry, circuit breaker
│
├── scheduler/jobs/      # 9 файлов — APScheduler background tasks
│   ├── funnel.py        # 18K — hourly WB funnel sync
│   ├── wb_finance.py    # 12K — hourly WB finance sync
│   └── ...
│
└── utils/               # time.py, crypto.py, file_validation.py
```

### 2.3 Ключевые модели (30+)
| Модель | Таблица | Soft Delete | Особенности |
|--------|---------|-------------|-------------|
| User | users | Нет | auth, email unique |
| Project | projects | Нет | multi-tenancy root |
| Transaction | transactions | Да | deduplicate by txn_id |
| IntegrationKey (=WbApiKey) | integration_keys | Нет | Fernet encrypted |
| SyncLog | sync_logs | Нет | RUNNING/OK/ERROR status |
| Order | orders | Да | planning, customs |
| PlannedPayment | planned_payments | Да | planned→partial→paid |
| Nomenclature | nomenclature | Да | WB product catalog |
| CostOrder | cost_orders | Да | cost allocation |
| Warehouse | warehouses | Да | FBO/FULFILLMENT type |
| InboundReceipt | inbound_receipts | Да | DRAFT→EXPECTED→ACCEPTED |
| OutboundShipment | outbound_shipments | Да | DRAFT→SHIPPED→DELIVERED |
| AssemblyRequest | assembly_requests | Да | PENDING→SHIPPED chain |
| FactoryOrder | factory_orders | Да | supply chain |
| BrandNote | brand_notes | Нет | AI agent memory |
| WbFunnelDaily | wb_funnel_daily | Нет | analytics cache |

### 2.4 API Endpoints по доменам
| Домен | Prefix | Роутер | Сервис |
|-------|--------|--------|--------|
| Auth | /api/v1/auth | auth.py | JWT + bcrypt |
| Projects | /api/v1/projects | projects.py (22K) | RBAC, settings |
| Transactions | /api/v1/transactions | import_txn.py | ETL pipeline |
| Planning | /api/v1/planning | planning.py (14K) | orders, customs |
| Cost | /api/v1/cost | cost.py (11K) | FIFO, duties |
| Reports | /api/v1/reports | reports.py + reports_wb.py + reports_stock.py | DDS, BDR, OPIU |
| Warehouse | /api/v1/warehouse | warehouse.py (15K) | stock engine |
| Assembly | /api/v1/warehouse/assembly | assembly.py (11K) | logistics |
| Funnel | /api/v1/funnel | funnel.py (27K) | WB analytics |
| Supply Chain | /api/v1/supply-chain | supply_chain.py | factory→delivery |
| Integrations | /api/v1/integrations | integrations.py | encrypted keys |
| Telegram | /api/v1/telegram, /api/v1/tma | telegram*.py | bot, TMA auth |
| Monitoring | /api/v1/monitoring | monitoring.py | health, sync |
| Refs | /api/v1/refs | refs.py | accounts, categories |
| WebSocket | /api/v1/ws | ws.py | real-time updates |

---

## 3. АРХИТЕКТУРА FRONTEND

### 3.1 Структура
```
frontend-react/
├── src/
│   ├── app/
│   │   ├── (main)/p/[slug]/     # 73 страницы — основное приложение
│   │   │   ├── page.tsx         # Dashboard
│   │   │   ├── dds/             # Движение денежных средств
│   │   │   ├── txn/             # Транзакции
│   │   │   ├── inbox/           # Неразобранные
│   │   │   ├── import/          # Импорт выписок
│   │   │   ├── reports/         # Отчёты (4 компонента)
│   │   │   ├── opiu/            # ОПИУ
│   │   │   ├── planning/        # Планирование (4 компонента)
│   │   │   ├── orders/          # Заказы
│   │   │   ├── cost/            # Себестоимость
│   │   │   ├── funnel/          # Воронка продаж (7 компонентов)
│   │   │   ├── trends/          # Тренды и аномалии
│   │   │   ├── warehouse/       # Склад (14+ вложенных страниц)
│   │   │   │   ├── stock/       # Остатки
│   │   │   │   ├── analytics/   # Аналитика склада
│   │   │   │   ├── assembly/    # Сборка заказов
│   │   │   │   ├── fbo-supplies/# FBO поставки
│   │   │   │   ├── logistics/   # Логистика
│   │   │   │   └── [id]/        # Детали + receipt/shipment/transfer
│   │   │   ├── supply-chain/    # Цепочка поставок
│   │   │   ├── settings/        # Настройки (8 компонентов)
│   │   │   ├── refs/            # Справочники
│   │   │   ├── team/            # Команда
│   │   │   └── monitoring/      # Мониторинг
│   │   │
│   │   ├── (tma)/tma/[slug]/    # 8 страниц — Telegram Mini App
│   │   │   ├── dashboard/       # Дашборд
│   │   │   ├── capital/         # Капитал
│   │   │   ├── chat/            # AI чат
│   │   │   ├── pnl/             # P&L
│   │   │   ├── pulse/           # Быстрые метрики
│   │   │   ├── funnel/          # Воронка
│   │   │   └── warehouse/       # Склад (4 таба)
│   │   │
│   │   └── globals.css          # Дизайн-система (glassmorphism)
│   │
│   ├── lib/
│   │   ├── api/                 # 16 доменных модулей
│   │   │   ├── client.ts        # HTTP client, JWT auth, auto-refresh
│   │   │   ├── auth.ts
│   │   │   ├── projects.ts
│   │   │   ├── transactions.ts
│   │   │   ├── reports.ts
│   │   │   ├── planning.ts
│   │   │   ├── cost.ts
│   │   │   ├── funnel.ts
│   │   │   ├── warehouse.ts
│   │   │   ├── supply-chain.ts
│   │   │   └── ... (ещё 6)
│   │   ├── api.ts               # Агрегатор (Object.assign)
│   │   ├── utils.ts             # formatNumber, formatDate, exportToExcel
│   │   └── hooks/               # usePermissions
│   │
│   ├── types/
│   │   └── api.ts               # 1,648 строк — 50+ TypeScript типов
│   │
│   └── components/              # 8 компонентов
│       ├── TanStackDataTable.tsx # 13.8K — таблицы с сортировкой, экспортом
│       ├── DataTable.tsx         # 7.8K — legacy таблицы
│       ├── FormModal.tsx         # 5.2K — модальные формы
│       ├── KpiCard.tsx           # 3.9K — карточки метрик
│       ├── PageHeader.tsx        # Заголовок страницы
│       ├── PageGuard.tsx         # Проверка прав
│       ├── TabLayout.tsx         # Вкладки
│       └── Toast.tsx             # Уведомления
│
├── package.json                 # Next.js 15, React 19, TanStack, Recharts, Three.js
├── tsconfig.json                # Strict mode, @/* paths
└── next.config.mjs              # Standalone output, API proxy
```

### 3.2 Дизайн-система
- **Стиль:** Apple-inspired glassmorphism
- **Цвета:** CSS-переменные `var(--color-*)` — НИКОГДА hex в компонентах
- **Скругления:** 8/12/14/20/24px по контексту
- **Тени:** `--shadow-glass` (0.04 opacity) / `--shadow-glass-hover` (0.08)
- **Glass-эффект:** `backdrop-filter: blur(24px) saturate(180%)`
- **Шрифт:** Inter, размеры 12-32px по иерархии
- **Анимации:** cubic-bezier, max 0.5s
- **TMA:** Отдельные правила (`.tma-*`, 14px скругления, компактные отступы)

### 3.3 Паттерн страницы (обязательный)
```typescript
'use client';
import { useEffect, useState, useCallback } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import type { FeatureType } from '@/types/api';

export default function FeaturePage() {
    const { slug } = useParams() as { slug: string };
    const [data, setData] = useState<FeatureType[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const loadData = useCallback(async () => {
        try { setLoading(true); setData(await api.getFeature()); }
        catch (e: any) { setError(e?.message || 'Ошибка'); }
        finally { setLoading(false); }
    }, []);

    useEffect(() => { loadData(); }, [loadData]);

    if (loading) return <div className="glass-card">Загрузка...</div>;
    if (error) return <div className="glass-card" style={{color:'var(--color-danger)'}}>{error}</div>;
    if (!data.length) return <div className="empty-state">...</div>;
    return <div>...</div>;
}
```

---

## 4. ИНФРАСТРУКТУРА

### 4.1 Docker Compose (13 сервисов)
| Сервис | Образ | RAM | Порт | Healthcheck |
|--------|-------|-----|------|-------------|
| db | PostgreSQL 15 | 1536M | Нет (внутренний) | pg_isready 5s |
| pgbouncer | PgBouncer | 64M | Нет | pg_isready 5s |
| redis | Redis 7-alpine | 256M | Нет | redis-cli ping 5s |
| minio | MinIO | 256M | 9000/9001 | mc ready 10s |
| backend | FastAPI | 1024M | 8000 | HTTP /health 15s |
| worker | APScheduler | 512M | Нет | HTTP /health 15s |
| frontend-react | Next.js | 256M | 3000 | HTTP 3000 15s |
| nginx | Nginx | 64M | 80/443 | wget /health 10s |
| certbot | Let's Encrypt | — | — | — |
| db-backup | pg_dump cron | — | — | Каждые 6ч |
| prometheus | Prometheus | — | 9090 | profile: monitoring |
| grafana | Grafana | — | 3001 | profile: monitoring |
| alertmanager | AlertManager | — | 9093 | profile: monitoring |

### 4.2 Серверная архитектура (3 сервера, WireGuard VPN)
```
┌─────────────────────────────────────────┐
│  App Server (130.49.150.69)             │
│  backend, worker, frontend, nginx       │
│  /opt/dds_app/                          │
└─────────────┬───────────────────────────┘
              │ WireGuard VPN (10.0.0.x)
┌─────────────┴───────────────────────────┐
│  Data Server (194.67.100.57)            │
│  PostgreSQL, PgBouncer, Redis, MinIO    │
│  Backups: каждые 6ч, ротация 14 дней   │
└─────────────────────────────────────────┘
              │
┌─────────────┴───────────────────────────┐
│  Monitoring Server                      │
│  Prometheus, Grafana, AlertManager      │
│  Node Exporter, PG Exporter             │
└─────────────────────────────────────────┘
```

### 4.3 CI/CD Pipeline (GitHub Actions)

#### test.yml — На каждый push/PR
```
push to main/dev →
  Job 1: conventions (check_conventions.sh)
  Job 2: pytest tests/ -x (Docker, 10 min timeout)
```

#### security.yml — При изменении зависимостей
```
pip-audit → gitleaks → Trivy filesystem scan (HIGH/CRITICAL)
```

#### cd-production.yml — При push в main
```
1. Backup DB на Data Server
2. Verify backup size > 10KB
3. Pull + docker compose up --build на App Server
4. Run migrations
5. Health check (HTTP)
6. Rollback if health fails
```

#### Вспомогательные workflows
- server-restart.yml — ручной перезапуск сервисов
- server-diagnose.yml — диагностика серверов → GitHub Issue
- server-cleanup.yml — еженедельная очистка Docker (вс 3:00 UTC)
- manual-funnel-sync.yml — ручная синхронизация данных

### 4.4 Pre-commit hooks (5 проверок)
1. **Ruff** — linter + formatter (PEP 8, security S-rules)
2. **Standard hooks** — trailing whitespace, EOF, YAML, large files (>500KB)
3. **Gitleaks** — секреты в коде
4. **Bandit** — Python security (injection, eval, weak crypto)
5. **DDS Convention Checker** — 16 кастомных проверок (см. раздел 6)

---

## 5. ЖЕЛЕЗНЫЕ ПРАВИЛА (Iron Rules)

### 5.1 База данных
| # | Правило | Нарушение = | Проверка |
|---|---------|-------------|----------|
| 1 | КАЖДЫЙ запрос фильтрует по `project_id` | Data leak между клиентами | check_conventions.sh + CI |
| 2 | `.where(Model.is_deleted == False)` для SoftDelete | Показ удалённых данных | check_conventions.sh |
| 3 | `model.soft_delete()` НИКОГДА `db.delete()` | Потеря аудит-следа | check_conventions.sh |
| 4 | `Numeric(18, 2)` для денег НИКОГДА Float | Потеря точности финансов | check_conventions.sh |
| 5 | Параметризованный SQL `:param` НИКОГДА f-string | SQL Injection | Bandit + check_conventions |
| 6 | `from backend.utils.time import utcnow` | Timezone баги | check_conventions.sh |
| 7 | `invalidate_cache(prefix)` после мутаций | Stale данные в кэше | Code review |
| 8 | Бизнес-логика в services/ НИКОГДА в routers/ | Нарушение архитектуры | Code review |

### 5.2 Frontend
| # | Правило | Нарушение = |
|---|---------|-------------|
| 1 | Типы ТОЛЬКО в `types/api.ts` НИКОГДА inline/any | Type safety потеря |
| 2 | API ТОЛЬКО через `api.*` НИКОГДА raw fetch | Нет auth/error handling |
| 3 | Числа через `formatNumber()` | Неконсистентное отображение |
| 4 | Даты через `formatDate()` | Неконсистентное отображение |
| 5 | ОБЯЗАТЕЛЬНО: loading, error, empty states | Broken UX |
| 6 | Таблицы с Excel экспортом | Функционал неполный |
| 7 | CSS-переменные `var(--color-*)` НИКОГДА hex | Дизайн-система нарушена |

### 5.3 Безопасность
| # | Правило | Нарушение = |
|---|---------|-------------|
| 1 | Нет хардкод-секретов | Утечка credentials |
| 2 | `ilike()` с `escape="\\"` | SQL injection через LIKE |
| 3 | Pydantic валидация на входе | Input validation bypass |
| 4 | API-ключи WB шифруются (Fernet) | Plain-text credentials |
| 5 | PII/секреты НЕ в логах | Data leak через логи |
| 6 | CORS ограничен в production | Cross-origin attacks |
| 7 | File upload проверка `MAX_UPLOAD_SIZE_MB` | DoS через большие файлы |
| 8 | `CancelledError` re-raise в scheduler | Worker не останавливается |
| 9 | Дочерние сущности → проверка parent.project_id | Cross-tenant access |

---

## 6. КОНВЕНЦИИ И АВТОМАТИЧЕСКИЕ ПРОВЕРКИ

### 6.1 check_conventions.sh (16 проверок)
| # | Что проверяет | Уровень |
|---|--------------|---------|
| 1 | `get_event_loop()` → `get_running_loop()` | Error |
| 2 | `print()` в backend → `logger.info()` | Error |
| 3 | Файлы >500 строк | Warning |
| 4 | `Float` для денег → `Numeric(18,2)` | Error |
| 5 | f-strings в `text()` → SQL injection | Error |
| 6 | SoftDelete без `is_deleted` фильтра | Error |
| 7 | Service без `project_id` | Warning |
| 8 | `datetime.utcnow()` / `.now()` → `utcnow()` | Error |
| 9 | `.scalars().all()` без `.limit()` | Warning |
| 10 | `db.delete()` на SoftDelete моделях | Error |
| 11 | `ilike()` без `escape=` | Error |
| 12 | `float()` в финансовых сервисах | Error |
| 13 | `except Exception` без `CancelledError` в scheduler | Error |
| 14 | File upload без size check | Error |
| 15 | Direct project mutation в routers | Warning |
| 16 | `Numeric` не (18,2) precision | Warning |

### 6.2 Ruff (ruff.toml)
- Python 3.11+, line length 120
- Правила: E W F I UP B S T20 SIM RUF (включая security)
- Игнорируются: E501 (line length), B008 (FastAPI defaults)
- Per-file исключения для миграций, тестов, Docker

### 6.3 Bandit (bandit.yaml)
- Scope: `backend/` (исключая tests, .venv, migrations)
- Skips: B101 (assert), B104 (0.0.0.0 in Docker), B324 (sha1 for dedup), B608 (SQL in specific files)

### 6.4 CodeRabbit (.coderabbit.yaml)
- Profile: "chill" (не строгий)
- Язык: русский
- Path instructions: роутеры без логики, сервисы с iron rules, модели с SoftDelete
- Auto-review: включён (кроме drafts)

---

## 7. ПРОЦЕСС РАЗРАБОТКИ (Agent TDD)

### 7.1 Фазы разработки
```
ФАЗА 0: ПОНИМАНИЕ
  Человек описывает задачу → AI читает DOMAIN_*.md → уточняющие вопросы → ТЗ → подтверждение

ФАЗА 1: ФУНДАМЕНТ (последовательно, один агент)
  Model → Alembic migration → Schema

ФАЗА 2: РЕАЛИЗАЦИЯ (параллельно, два агента)
  ┌─────────────────────────┐    ┌─────────────────────────┐
  │  Агент 1: Backend       │    │  Агент 2: Frontend      │
  │  1. Tests (RED)         │    │  1. Types в api.ts      │
  │  2. Service             │    │  2. API method          │
  │  3. Router              │    │  3. Page/component      │
  │  4. Tests (GREEN)       │    │  4. Vitest tests        │
  └─────────────────────────┘    └─────────────────────────┘

ФАЗА 3: ИНТЕГРАЦИЯ (последовательно)
  pytest → vitest → check_conventions → docs update → commit
```

### 7.2 Когда нужно ТЗ
- Новый модуль/фича
- Кросс-доменные изменения
- Изменение API контракта

### 7.3 Когда ТЗ НЕ нужно
- Баг-фиксы
- Мелкие изменения (поле, typo)
- Рефакторинг без изменения API

### 7.4 TDD цикл (обязательный)
```
1. Написать тест ПЕРВЫМ (RED) — он ДОЛЖЕН падать
2. Запустить — убедиться что ПАДАЕТ
3. Написать минимальную реализацию (GREEN)
4. Запустить — убедиться что ПРОХОДИТ
5. Рефакторинг (IMPROVE)
```

### 7.5 Обязательные тест-кейсы

**Backend:**
- Happy path (CRUD)
- project_id isolation (другой проект → пустой результат)
- is_deleted фильтрация
- Пустые данные → пустой массив
- Невалидный input → 422
- Decimal precision (финансы)
- Rate limiting (429) для WB API
- Timeout handling

**Frontend:**
- Render data correctly
- Loading state
- Empty state
- Error state
- User actions (click → API call)

### 7.6 Правила параллелизации

**БЕЗОПАСНО параллелить:**
| Агент 1 | Агент 2 | Почему безопасно |
|---------|---------|-----------------|
| Backend service + tests | Frontend page | Разные языки, директории |
| Service домен A | Service домен B | Разные файлы, таблицы |
| Backend pytest | Frontend vitest | Независимые фреймворки |

**НЕЛЬЗЯ параллелить:**
| Ситуация | Риск |
|----------|------|
| 2 Alembic миграции | Сломанная цепочка ревизий |
| 2 агента → один файл | Git конфликт |
| Service A вызывает Service B | Агент не видит изменений другого |
| opiu_service + wb_bdr_service | Связанная логика deductions |

**Singleton файлы (max 1 агент):**
- CLAUDE.md, cache.py, alembic/versions/*, check_conventions.sh

### 7.7 Шаблоны (templates)
Находятся в `.claude/templates/`:
- `new_model.py.tmpl` — SQLAlchemy модель с SoftDeleteMixin
- `new_schema.py.tmpl` — Pydantic (Create, Update, Response)
- `new_service.py.tmpl` — CRUD service с кэшем
- `new_router.py.tmpl` — HTTP endpoints
- `new_test.py.tmpl` — pytest async tests
- `new_page.tsx.tmpl` — Next.js страница

### 7.8 Runbooks (типовые сценарии)
1. **Новый API endpoint:** MAP.md → Model → Migration → Schema → Service → Router → Test → /smoke
2. **Новая страница:** Type → API method → Page → States → Excel export → /smoke
3. **Баг-фикс:** Reproduce → Root cause → Test (RED) → Fix → Test (GREEN) → /smoke
4. **Миграция:** Model → `alembic revision --autogenerate` → verify → upgrade → /smoke
5. **Новый домен:** /plan → Model+Migration → Backend||Frontend → /verify → CLAUDE.md → DOMAIN_*.md

---

## 8. КЭШИРОВАНИЕ

### 8.1 Стратегия
- Декоратор: `@cached(prefix="...", ttl=300)`
- Инвалидация: `await invalidate_cache("prefix")` — сам добавляет `:*`
- ОБЯЗАТЕЛЬНО: кэш-ключ содержит project_id
- При ошибке Redis → graceful degradation (работает без кэша)

### 8.2 Префиксы и инвалидация
```python
# invalidate_project_reports() инвалидирует 14 префиксов:
reports:balance, reports:dds_month, reports:dds_year,
reports:dashboard, reports:opiu, reports:wb_bdr,
reports:cashflow, reports:stock_forecast, reports:stock_analytics,
funnel:summary, funnel:day, funnel:brand, funnel:tariff_map,
cost:history
```

### 8.3 Каскадная инвалидация
| Мутация | Инвалидировать |
|---------|---------------|
| Импорт транзакции | `reports:*` (все отчёты) |
| Категоризация | `reports:balance`, `reports:dashboard`, `reports:dds_month` |
| WB sync | `reports:opiu`, `reports:wb_bdr`, `reports:dashboard` |
| Изменение tax_rate | `cost`, `reports:wb_bdr`, `reports:opiu`, `reports:dashboard`, `funnel` |

---

## 9. БЕЗОПАСНОСТЬ

### 9.1 Шифрование
- API-ключи WB/Ozon: Fernet symmetric (SHA-256 key derivation)
- Legacy fallback для миграции — НЕ удалять
- Ключи НЕ возвращаются в API response (только маскированные)

### 9.2 Multi-tenancy
- `get_current_project()` dependency на каждом роутере
- `X-Project-Id` header в каждом запросе
- Дочерние сущности без project_id → проверка parent.project_id
- RLS (Row Level Security) через `SET LOCAL app.project_id`

### 9.3 Audit Trail
- Middleware в main.py логирует все POST/PUT/DELETE
- user_id, project_id, status, IP, path
- AuditLog модель для записи в БД

### 9.4 Автоматические проверки
| Инструмент | Где | Что проверяет |
|-----------|-----|---------------|
| Ruff S-rules | Pre-commit | Python security patterns |
| Bandit | Pre-commit | Injection, eval, weak crypto |
| Gitleaks | Pre-commit | Секреты в коде |
| pip-audit | CI | CVE в Python зависимостях |
| Trivy | CI | Filesystem scan (HIGH/CRITICAL) |
| CodeRabbit | PR review | AI review с iron rules |
| Dependabot | Weekly | Auto-PR для обновления зависимостей |

### 9.5 Rate Limiting
- Login/register: Redis-based by IP
- AI chat: 20 req/hour per user, 100/day per project
- WB API: Semaphore + Retry-After header

---

## 10. МОНИТОРИНГ И OBSERVABILITY

### 10.1 Prometheus метрики
- HTTP request latency (histogram)
- In-progress requests (gauge)
- Custom metrics per domain

### 10.2 Логирование
- Structured JSON: timestamp, level, logger, message, request_id
- Slow query logging: >500ms requests
- Rotation: truncate >100M files (weekly cleanup)

### 10.3 Health Checks
- `/health` endpoint: DB, Redis, MinIO, scheduler connectivity
- Sync log monitoring: RUNNING > 10min → STALE
- Server diagnostics: GitHub Action → Issue

### 10.4 Sentry
- Error tracking: 20% traces, 10% profiling
- Enabled if SENTRY_DSN set

---

## 11. ДОМЕНЫ И БИЗНЕС-ЛОГИКА

### 11.1 Транзакции (ETL/Import)
- Дедупликация по txn_id (hash: дата+сумма+назначение)
- Парсеры: VTB (банк), WB (маркетплейс)
- Авто-категоризация: overrides → counterparty_categories → UNASSIGNED
- ETL использует SYNC engine (run_in_executor)

### 11.2 Планирование
- Статусы платежей: planned → partial → paid
- Race condition: fact_links.commit() + update_payment не атомарно (известный баг)
- Customs: topup → alloc → DT (декларации)

### 11.3 Себестоимость (Cost)
- FIFO расчёт: price_cny × rate + shipping + duty + util_tax = unit_cost
- Duty basis: weight/volume/amount%
- Nomenclature из WB Content API

### 11.4 Отчёты
- **DDS:** Balance = opening + SUM(net) WHERE is_cashflow2=1
- **BDR:** ad_deduction и loan_deduction — ОТДЕЛЬНЫЕ строки расхода (не операционные)
- **OPIU:** Revenue - cost - tax = profit
- **FX:** AVERAGE yearly rate (KNOWN BUG: должен быть daily)

### 11.5 Склад
- **Принцип:** `_update_stock()` — ЕДИНСТВЕННОЕ место изменения остатков
- **Receipts:** DRAFT → EXPECTED → ACCEPTED
- **Shipments:** DRAFT → SHIPPED → DELIVERED (только FULFILLMENT)
- **Transfers:** DRAFT → IN_TRANSIT → COMPLETED
- **FBO:** WbFboSupply linked to OutboundShipment

### 11.6 Сборка (Assembly)
- Статусы: PENDING → IN_PROGRESS → READY → VEHICLE_ASSIGNED → SHIPPED
- Ship: validate stock → _update_stock(delta=-qty) → create OutboundShipment
- Cancel SHIPPED: _update_stock(delta=+qty) → soft_delete shipment → rollback

### 11.7 AI Multi-Agent System
- 7 агентов: Analyst, Financier, Marketer, Advertiser, SupplyManager, Logistics, Logistician
- 19 tools: finance, marketing, logistics, supply, shipping
- Orchestrator → agents → synthesizer
- Memory: BrandNote (Redis, 10 msg, 1h TTL)
- Rate limit: 20 req/hour, 100/day
- Tool loop: max 5 rounds, truncate >15KB

### 11.8 Supply Chain
- FactoryOrder → CostOrder → Warehouse
- VehicleStatus tracking
- split_to_vehicles для разделения поставок

### 11.9 Telegram
- Bot: polling с прокси на проде
- TMA: HMAC auth
- Digest: daily at 7:00 MSK

---

## 12. KNOWN BUGS И TECH DEBT

### 12.1 Critical
| Баг | Описание | Влияние |
|-----|----------|---------|
| Unified Stock profit | -6 млрд₽ — double-counting ad_deduction | Некорректная аналитика |
| FX conversion | Average yearly rate вместо daily | Неточный P&L/DDS |

### 12.2 Medium
| Проблема | Описание |
|----------|----------|
| AuditLog user_id=0 | Telegram endpoints без user context |
| Legacy crypto | "Decrypted with legacy key" warning |
| Global CircuitBreaker | Один клиент блокирует всех |
| Race condition fact_links | commit() + update не атомарно |
| TOCTOU scheduler locks | check + acquire не атомарно |

### 12.3 Код требующий рефакторинга
| Файл | Строк | Проблема |
|------|-------|----------|
| assembly_service.py | 1,121 | Нужно разбить на модули |
| fbo_supply_service.py | 882 | Нужно разбить |
| warehouse_stock_service.py | 852 | Нужно разбить |
| warehouse_stock_engine.py | 36K (файл) | Монолитный FIFO engine |
| executor.py (AI) | 29K | 19 tools в одном файле |

### 12.4 P0 (перед продакшеном — не все исправлены)
1. Dockerfiles в dev mode → production mode
2. Project isolation: 7 моделей без project_id
3. MinIO credentials: убрать хардкод defaults
4. File upload: проверка magic bytes

---

## 13. СИСТЕМА РАЗРАБОТКИ AI-АГЕНТОВ

### 13.1 Claude Code Skills (slash commands)
| Команда | Назначение |
|---------|-----------|
| `/plan` | Планирование фичи — ТЗ перед кодом |
| `/tdd` | TDD workflow: RED → GREEN → REFACTOR |
| `/smoke` | Быстрая проверка (30 сек) |
| `/verify` | Полная верификация перед коммитом |
| `/review` | Code review — безопасность, качество |
| `/build-fix` | Исправление ошибок сборки/тестов |
| `/docs` | Автообновление документации |
| `/pause` | Сохранить прогресс для продолжения |
| `/resume` | Продолжить из сохранённого прогресса |

### 13.2 Субагенты (specialized)
| Тип | Назначение |
|-----|-----------|
| code-reviewer | Ревью кода на качество и безопасность |
| security-reviewer | Поиск уязвимостей |
| tdd-guide | TDD процесс (RED→GREEN→REFACTOR) |
| planner | Планирование фич и рефакторинга |
| build-error-resolver | Исправление ошибок сборки |
| database-reviewer | PostgreSQL оптимизация |

### 13.3 Git Worktrees (параллельная работа)
```bash
# Старт: создаёт два worktree + ветки
bash scripts/worktree-start.sh myfeature
# → .worktrees/myfeature-backend/ (feat/myfeature-backend branch)
# → .worktrees/myfeature-frontend/ (feat/myfeature-frontend branch)

# Работа: два терминала, два Claude агента
Tab 1: cd .worktrees/myfeature-backend && claude
Tab 2: cd .worktrees/myfeature-frontend && claude

# Финиш: merge в dev, cleanup
bash scripts/worktree-finish.sh myfeature
```

### 13.4 Claude Hooks (settings.json)
| Hook | Назначение |
|------|-----------|
| pre_tool_check.sh | Блокирует опасные команды (rm -rf, DROP, force push) |
| post_edit_check.sh | Напоминает обновить docs при создании файлов |
| post_stop_check.sh | Предупреждает о Float в моделях |
| Context monitor | WARNING при 35% контекста, CRITICAL при 25% |

### 13.5 Memory система
```
memory/
├── MEMORY.md              # Индекс (загружается в контекст)
├── user_profile.md        # Профиль разработчика
├── feedback_*.md          # Уроки по процессу (5 файлов)
├── project_known_bugs.md  # Баги и tech debt
├── project_dev_system.md  # Архитектура dev system
├── project_unified_stock.md  # Статус модуля
└── reference_infra.md     # Серверы, URL
```

### 13.6 Makefile targets
```bash
make dev / make stop / make logs / make status
make test / make test-fast / make test-changed / make test-unit
make lint
make migrate / make migrate-new MSG="..."
make build-backend / make build-frontend / make build-all
make seed / make shell-db / make shell-redis
make deploy / make deploy-prod
make ssl-init / make ssl-renew / make ssl-status
```

---

## 14. ПОТЕНЦИАЛЬНЫЕ ПРОБЛЕМЫ ДЛЯ АНАЛИЗА

### 14.1 Скорость AI-агентов
- Контекст ~82K строк кода → агентам нужно много читать перед работой
- 11 доменов → агент должен знать зависимости между доменами
- 37 тест-файлов → полный прогон медленный (решение: test-changed/test-fast)
- Шаблоны ускоряют создание, но не модификацию существующего кода
- Worktrees позволяют параллелить backend/frontend

### 14.2 Безопасность
- Fernet encryption для API keys (не KMS)
- RLS настроен, но 7 моделей без project_id
- CircuitBreaker глобальный (один клиент может заблокировать всех)
- Rate limiting только на login и AI chat
- File upload: size check есть, magic bytes — нет
- CORS: настроен, но проверить на production
- Secrets: env vars, но MinIO defaults хардкодены

### 14.3 Архитектура
- Монолитные сервисы (assembly 1121 строк, fbo 882, stock 852)
- Frontend: 73 страницы, но только 8 shared компонентов
- AI executor: 19 tools в одном файле (29K)
- ETL: синхронные парсеры через run_in_executor
- Scheduler: APScheduler, но без distributed locking (TOCTOU)
- WebSocket: реализован, но не для всех операций
- Отсутствие: GraphQL, API versioning, feature flags

### 14.4 Тестирование
- 37 тест-файлов для 229 backend файлов (~16% покрытие по файлам)
- Frontend тесты: в планах, но не реализованы
- E2E: Playwright в dependencies, но нет тестов
- Load testing: не настроен
- Нет тестов на конкурентность (race conditions)

---

## 15. МЕТРИКИ ПРОЕКТА (snapshot)

| Метрика | Значение |
|---------|----------|
| Backend files | 229 |
| Frontend pages | 73 (main) + 8 (TMA) |
| API endpoints | ~80+ |
| DB models | 30+ |
| Test files | 37 |
| CI workflows | 7 |
| Pre-commit hooks | 5 |
| Convention checks | 16 |
| DOMAIN docs | 10 |
| Rule files | 6 |
| Iron rules | 8 backend + 7 frontend + 9 security = 24 |
| Known bugs | 2 critical + 5 medium |
| Files >500 lines | 5 (need refactoring) |
| Environments | 2 (local + production) |
| Servers | 3 (app + data + monitoring) |
| Docker services | 13 |

---

> **Для AI-агента-рецензента:** Этот документ описывает полную архитектуру и процесс разработки проекта DDS.
> Основные области для рекомендаций:
> 1. **Скорость AI-агентов** — как ускорить цикл разработки, уменьшить время на контекст
> 2. **Безопасность** — уязвимости, missing checks, encryption, auth
> 3. **Архитектура** — рефакторинг монолитов, тестирование, масштабирование
> 4. **Процесс** — CI/CD, мониторинг, deployment, disaster recovery
