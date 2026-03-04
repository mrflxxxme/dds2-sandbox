---
name: db-backup
description: Бэкап и восстановление базы данных PostgreSQL (ручной и автоматический)
---

# Skill: Бэкап и восстановление БД

Используй этот skill когда нужно сделать бэкап, восстановить БД из бэкапа, или диагностировать проблемы с данными.

## Архитектура

- **Контейнер `db-backup`** — автоматически делает `pg_dump` каждые 6 часов
- **Файлы хранятся** в `./backups/` на хосте (НЕ в Docker-томе)
- **Ротация** — 14 дней, старые удаляются автоматически
- **Формат** — `pg_dump` → gzip (`.sql.gz`)

## Скрипты

| Файл | Назначение |
|------|------------|
| `scripts/backup.sh` | pg_dump + gzip + ротация |
| `scripts/restore.sh` | Восстановление с подтверждением |

## Ручной бэкап

```bash
docker compose exec db-backup /scripts/backup.sh
```

## Восстановление из бэкапа

```bash
# Список доступных бэкапов
ls -lh backups/

# Восстановление (интерактивно, с подтверждением)
docker compose exec -it db-backup /scripts/restore.sh dds_db_YYYYMMDD_HHMMSS.sql.gz

# После восстановления — перезапустить бэкенд
docker compose restart backend
```

## Восстановление из .sql (без gzip)

```bash
# Старый формат бэкапов (без сжатия)
docker compose exec -T db psql -U dds -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='dds_db' AND pid<>pg_backend_pid();"
docker compose exec -T db psql -U dds -d postgres -c "DROP DATABASE dds_db;"
docker compose exec -T db psql -U dds -d postgres -c "CREATE DATABASE dds_db OWNER dds;"
docker compose exec -T db psql -U dds -d dds_db < backups/backup_file.sql
docker compose restart backend
```

## Быстрая диагностика

```bash
# Проверить что бэкап-контейнер работает
docker compose logs db-backup --tail 10

# Счётчик данных в базе
docker compose exec -T db psql -U dds -d dds_db -c "
SELECT 'projects' as tbl, count(*) FROM projects
UNION ALL SELECT 'users', count(*) FROM users
UNION ALL SELECT 'transactions', count(*) FROM transactions
UNION ALL SELECT 'orders', count(*) FROM orders
ORDER BY tbl;"

# Проверить Docker-тома (если данные пропали)
docker volume ls
```

## Переменные окружения (docker-compose.yml)

| Переменная | Дефолт | Описание |
|------------|--------|----------|
| `BACKUP_RETENTION_DAYS` | 14 | Дней хранения бэкапов |
| `BACKUP_INTERVAL_HOURS` | 6 | Интервал между бэкапами |

## ⚠️ Известные грабли

1. **Переименование папки проекта** → Docker создаёт новые пустые тома. Бэкапы в `./backups/` при этом сохраняются.
2. **Сброс пароля admin** после восстановления:
```bash
docker compose exec -T backend python -c "
import asyncio
from backend.database import AsyncSessionLocal
from backend.models.auth import User
from backend.auth import hash_password
from sqlalchemy import select

async def reset_pw():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == 'admin'))
        user = result.scalar_one_or_none()
        if user:
            user.password_hash = hash_password('admin')
            await db.commit()
            print('Password reset to admin')

asyncio.run(reset_pw())"
```
