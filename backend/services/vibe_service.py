# ruff: noqa: RUF001, RUF002
"""Vibecoding: логика вкладки «Вайбкодинг» и ингест данных от CI.

Про отсутствие project_id — см. докстринг `backend/models/vibe.py`: это телеметрия
репозитория, а не данные арендатора. Доступ режется наличием строки в `vibe_authors`:
нет строки → `get_stats` вернёт None → роутер отдаст 403.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.auth import User
from backend.models.vibe import VibeAuthor, VibeCommit, VibeFile
from backend.schemas.vibe import (
    VibeAuthorRef,
    VibeAreaVolume,
    VibeDayVolume,
    VibeIngestCommit,
    VibeIngestResult,
    VibeRhythm,
    VibeScale,
    VibeShipment,
    VibeStats,
)
from backend.utils.time import utcnow

# Ритм считается по скользящему окну в 14 дней, а не по периоду отчёта: вопрос
# «в ритме ли я сейчас» не зависит от того, какой период выбран на экране.
RHYTHM_WINDOW_DAYS = 14

# Потолки выборок (Iron rule: .scalars().all() — всегда с .limit()).
# 5000 поставок ≈ 3 года работы соло-разработчика; период сверху ограничен роутером.
SHIPMENTS_LIMIT = 5000
FILES_LIMIT = 200_000

# Батчи UPSERT: asyncpg рвётся на 32767 параметрах в одном statement
# (строки × колонок). Держим с запасом.
_COMMIT_CHUNK = 2000  # 11 колонок → 22k параметров
_FILE_CHUNK = 5000  # 5 колонок → 25k параметров


# ─── Классификация ──────────────────────────────────────────────────────────

# Область кода по префиксу пути. Порядок важен — первый матч выигрывает.
_AREA_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("frontend-react/", "Фронтенд"),
    ("backend/", "Бэкенд"),
    ("tests/", "Тесты"),
    ("migrations/", "Миграции БД"),
    ("docs/", "Документация"),
)
AREA_OTHER = "Прочее"

# Раздел продукта по scope коммита. Не найден — показываем сам scope: новый раздел
# появится на вкладке сразу, без правки этого словаря.
_SECTION_BY_SCOPE: dict[str, str] = {
    "ads": "Управление рекламой",
    "ads-manager": "Управление рекламой",
    "raw-data": "Сырые данные",
    "orders": "Заказы",
    "migrations": "Миграции БД",
    "integrations": "Интеграции",
    "ui": "Интерфейс",
    "deps": "Зависимости и CI",
    "mypy": "Зависимости и CI",
    "ci": "Зависимости и CI",
    "tests": "Тесты",
}
SECTION_NONE = "Без раздела"

_MIGRATIONS_PREFIX = "migrations/"
_COMPONENT_SUFFIX = ".tsx"


def area_for_path(path: str) -> str:
    """Область кода по пути файла: «Фронтенд» / «Бэкенд» / … / «Прочее»."""
    for prefix, area in _AREA_BY_PREFIX:
        if path.startswith(prefix):
            return area
    return AREA_OTHER


def section_for_scope(scope: str | None) -> str:
    """Человеческое имя раздела по scope коммита."""
    key = (scope or "").strip().lower()
    if not key:
        return SECTION_NONE
    return _SECTION_BY_SCOPE.get(key, key)


def _norm_email(email: str) -> str:
    """git-почта нормализуется в lower-case: склейка автора идёт по ней."""
    return (email or "").strip().lower()


# ─── Чтение ─────────────────────────────────────────────────────────────────


async def _author_emails(db: AsyncSession, user_id: int) -> tuple[list[str], str | None]:
    """Все git-почты пользователя + display_name. Пустой список = не вайбкодер.

    Почт может быть несколько (разные машины) — поставки со всех склеиваются в
    одну статистику.
    """
    rows = (
        (
            await db.execute(
                select(VibeAuthor.git_email, VibeAuthor.display_name)
                .where(VibeAuthor.user_id == user_id)
                .order_by(VibeAuthor.id)
                .limit(50)
            )
        )
        .all()
    )
    emails = [_norm_email(r.git_email) for r in rows]
    display = next((r.display_name for r in rows if r.display_name), None)
    return emails, display


async def _rhythm(db: AsyncSession, emails: list[str], until: date) -> VibeRhythm:
    """Дней с поставкой в окне 14 дней, заканчивающемся в `until`.

    Не стрик: пауза не обнуляет. Окно обрезается по дате ПЕРВОЙ поставки автора —
    иначе новичок на второй день работы видит «2 из 14» и читает это как провал,
    хотя пропущенных дней у него нет.
    """
    first_day = await db.scalar(
        select(func.min(VibeCommit.authored_on)).where(VibeCommit.author_email.in_(emails))
    )

    start = until - timedelta(days=RHYTHM_WINDOW_DAYS - 1)
    if first_day is not None and first_day > start:
        start = first_day
    # Автор начал позже конца окна (или поставок нет вовсе) — окно вырождается в день.
    if start > until:
        start = until

    hit = 0
    if first_day is not None:
        hit = (
            await db.scalar(
                select(func.count(func.distinct(VibeCommit.authored_on))).where(
                    VibeCommit.author_email.in_(emails),
                    VibeCommit.authored_on >= start,
                    VibeCommit.authored_on <= until,
                )
            )
            or 0
        )

    denom = (until - start).days + 1
    return VibeRhythm(
        hit=hit,
        denom=denom,
        window=RHYTHM_WINDOW_DAYS,
        start=start,
        end=until,
    )


async def _load_commits(
    db: AsyncSession, emails: list[str], since: date, until: date
) -> list[VibeCommit]:
    """Поставки автора за период, свежие сверху."""
    return list(
        (
            await db.execute(
                select(VibeCommit)
                .where(
                    VibeCommit.author_email.in_(emails),
                    VibeCommit.authored_on >= since,
                    VibeCommit.authored_on <= until,
                )
                .order_by(VibeCommit.authored_on.desc(), VibeCommit.sha)
                .limit(SHIPMENTS_LIMIT)
            )
        )
        .scalars()
        .all()
    )


async def _load_files(db: AsyncSession, shas: list[str]) -> list[VibeFile]:
    """Файлы перечисленных поставок. Один запрос — без N+1 по коммитам."""
    if not shas:
        return []
    return list(
        (
            await db.execute(
                select(VibeFile).where(VibeFile.sha.in_(shas)).limit(FILES_LIMIT)
            )
        )
        .scalars()
        .all()
    )


def _build_scale(commits: list[VibeCommit], files: list[VibeFile]) -> VibeScale:
    """Масштаб: сколько сделано. Файлы считаются УНИКАЛЬНЫМИ путями.

    Из `vibe_commits.files` уникальных не вывести: один файл, тронутый тремя
    поставками, — это 1 файл, а сумма счётчиков даст 3.
    """
    paths: set[str] = set()
    new_paths: set[str] = set()
    area_paths: dict[str, set[str]] = defaultdict(set)
    area_added: dict[str, int] = defaultdict(int)
    area_deleted: dict[str, int] = defaultdict(int)

    for f in files:
        paths.add(f.path)
        if f.is_new:
            new_paths.add(f.path)
        area = area_for_path(f.path)
        area_paths[area].add(f.path)
        area_added[area] += f.added
        area_deleted[area] += f.deleted

    components = sum(1 for p in new_paths if p.endswith(_COMPONENT_SUFFIX))
    migrations = sum(1 for p in new_paths if p.startswith(_MIGRATIONS_PREFIX))
    sections = len({section_for_scope(c.scope) for c in commits if c.is_product})

    by_area = [
        VibeAreaVolume(
            area=area,
            files=len(area_paths[area]),
            added=area_added[area],
            deleted=area_deleted[area],
        )
        for area in sorted(area_paths, key=lambda a: (-len(area_paths[a]), a))
    ]

    return VibeScale(
        files=len(paths),
        new_files=len(new_paths),
        components=components,
        migrations=migrations,
        sections=sections,
        # Итог строк — по коммитам, а не по файлам: так тотал сходится со списком
        # поставок даже если строки файлов почему-то не доехали.
        added=sum(c.added for c in commits),
        deleted=sum(c.deleted for c in commits),
        by_area=by_area,
    )


def _build_by_day(commits: list[VibeCommit], since: date, until: date) -> list[VibeDayVolume]:
    """ВСЕ дни периода, включая пустые: фронт рисует пропуски, а не сжимает их."""
    agg: dict[date, list[int]] = {}
    for c in commits:
        row = agg.setdefault(c.authored_on, [0, 0, 0])
        row[0] += 1
        row[1] += c.added
        row[2] += c.deleted

    out: list[VibeDayVolume] = []
    day = since
    while day <= until:
        shipments, added, deleted = agg.get(day, (0, 0, 0))
        out.append(VibeDayVolume(day=day, shipments=shipments, added=added, deleted=deleted))
        day += timedelta(days=1)
    return out


def _build_by_section(commits: list[VibeCommit]) -> list[dict]:
    """Поставки по разделам — только продуктовые: раздел есть у того, что видит юзер."""
    counts: dict[str, int] = defaultdict(int)
    for c in commits:
        if c.is_product:
            counts[section_for_scope(c.scope)] += 1
    return [
        {"section": section, "count": count}
        for section, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


async def get_stats(
    db: AsyncSession, user_id: int, since: date, until: date
) -> VibeStats | None:
    """Статистика вкладки за период. None — пользователь не вайбкодер (нет строки).

    Пустой период — не ошибка: вернётся нулевой ответ с полным `by_day`.
    """
    emails, display = await _author_emails(db, user_id)
    if not emails:
        return None

    commits = await _load_commits(db, emails, since, until)
    files = await _load_files(db, [c.sha for c in commits])
    rhythm = await _rhythm(db, emails, until)
    last_ingest = await db.scalar(select(func.max(VibeCommit.ingested_at)))

    shipments = [
        VibeShipment(
            sha=c.sha,
            short=c.sha[:7],
            day=c.authored_on,
            ctype=c.ctype,
            scope=c.scope,
            section=section_for_scope(c.scope),
            title=c.title,
            added=c.added,
            deleted=c.deleted,
            files=c.files,
            is_product=c.is_product,
        )
        for c in commits
    ]

    return VibeStats(
        display_name=display or emails[0],
        since=since,
        until=until,
        shipments_total=len(commits),
        shipments_product=sum(1 for c in commits if c.is_product),
        rhythm=rhythm,
        scale=_build_scale(commits, files),
        by_day=_build_by_day(commits, since, until),
        by_section=_build_by_section(commits),
        shipments=shipments,
        last_ingest=last_ingest.date() if last_ingest else None,
    )


async def list_authors(db: AsyncSession) -> list[VibeAuthorRef]:
    """Все вайбкодеры — для селектора. Один человек может иметь несколько git-почт,
    поэтому группируем по user_id, а не по строкам таблицы.

    Имя берём первое непустое из `display_name`; если его не проставили при привязке —
    падаем на username, иначе в селекторе будет пусто.
    """
    rows = (
        await db.execute(
            select(VibeAuthor.user_id, VibeAuthor.display_name, User.username)
            .join(User, User.id == VibeAuthor.user_id)
            .order_by(VibeAuthor.user_id)
        )
    ).all()

    # display_name проставлен НЕ у каждой строки: почт у человека несколько, имя обычно
    # задают при первой привязке. Поэтому сначала ищем имя по ВСЕМ его строкам и только
    # потом падаем на username — иначе строка без имени, попавшаяся первой, навсегда
    # закрепляла бы логин (в селекторе было «admin» и «ivnfs» вместо живых имён).
    display: dict[int, str] = {}
    fallback: dict[int, str] = {}
    for user_id, display_name, username in rows:
        if display_name and display_name.strip() and user_id not in display:
            display[user_id] = display_name.strip()
        fallback.setdefault(user_id, (username or "").strip())

    names = {uid: display.get(uid) or fallback.get(uid) or f"#{uid}" for uid in fallback}
    return [
        VibeAuthorRef(user_id=uid, name=name)
        for uid, name in sorted(names.items(), key=lambda kv: kv[1].lower())
    ]


async def is_vibecoder(db: AsyncSession, user_id: int) -> bool:
    """Дешёвый флаг для сайдбара: один индексированный SELECT по user_id."""
    return (
        await db.scalar(
            select(VibeAuthor.id).where(VibeAuthor.user_id == user_id).limit(1)
        )
    ) is not None


# ─── Ингест ─────────────────────────────────────────────────────────────────


def _file_rows(commit: VibeIngestCommit) -> list[dict[str, Any]]:
    """`files_list` поставки → строки vibe_files. Дедуп по path (last wins).

    Дедуп обязателен до executemany: PK (sha, path), два одинаковых пути в одном
    INSERT — CardinalityViolation.
    """
    by_path: dict[str, dict[str, Any]] = {}
    for item in commit.files_list:
        path = item.path.strip()
        if not path:
            continue
        by_path[path] = {
            "sha": commit.sha,
            "path": path,
            "added": item.added,
            "deleted": item.deleted,
            "is_new": item.is_new,
        }
    return list(by_path.values())


async def ingest(db: AsyncSession, commits: list[VibeIngestCommit]) -> VibeIngestResult:
    """UPSERT поставок от CI. Идемпотентно: повторный прогон не задваивает.

    Ключ — полный sha. Файлы приходят внутри коммита (`files_list`) и
    перезаписываются целиком (delete по sha + insert): частичный UPSERT оставил
    бы хвосты от прежней версии коммита, если CI пришлёт другой срез.
    """
    received = len(commits)
    if not commits:
        return VibeIngestResult(received=0, inserted=0, updated=0, files=0)

    # Дедуп по sha ДО executemany: два одинаковых ключа в одном INSERT →
    # CardinalityViolation (ON CONFLICT не может обновить строку дважды).
    by_sha: dict[str, VibeIngestCommit] = {}
    for c in commits:
        by_sha[c.sha] = c

    shas = list(by_sha)
    existing = set(
        (
            await db.execute(select(VibeCommit.sha).where(VibeCommit.sha.in_(shas)))
        )
        .scalars()
        .all()
    )
    updated = len(existing)
    inserted = len(shas) - updated

    now = utcnow()
    rows = [
        {
            "sha": c.sha,
            "author_email": _norm_email(c.author_email),
            "authored_on": c.authored_on,
            "ctype": c.ctype,
            "scope": c.scope or "",
            "title": c.title,
            "added": c.added,
            "deleted": c.deleted,
            "files": c.files,
            "is_product": c.is_product,
            "ingested_at": now,
        }
        for c in by_sha.values()
    ]

    for i in range(0, len(rows), _COMMIT_CHUNK):
        chunk = rows[i : i + _COMMIT_CHUNK]
        stmt = pg_insert(VibeCommit).values(chunk)
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[VibeCommit.sha],
                set_={
                    "author_email": stmt.excluded.author_email,
                    "authored_on": stmt.excluded.authored_on,
                    "ctype": stmt.excluded.ctype,
                    "scope": stmt.excluded.scope,
                    "title": stmt.excluded.title,
                    "added": stmt.excluded.added,
                    "deleted": stmt.excluded.deleted,
                    "files": stmt.excluded.files,
                    "is_product": stmt.excluded.is_product,
                    "ingested_at": stmt.excluded.ingested_at,
                },
            )
        )

    # Файлы: перезапись по sha. Чистим только те sha, что реально пришли в батче —
    # коммит без files_list (CI не собрал файлы) не должен обнулять прежние строки.
    file_rows: list[dict[str, Any]] = []
    touched = [sha for sha, c in by_sha.items() if c.files_list]
    for sha in touched:
        file_rows.extend(_file_rows(by_sha[sha]))

    for i in range(0, len(touched), _COMMIT_CHUNK):
        await db.execute(
            delete(VibeFile).where(VibeFile.sha.in_(touched[i : i + _COMMIT_CHUNK]))
        )
    for i in range(0, len(file_rows), _FILE_CHUNK):
        await db.execute(pg_insert(VibeFile).values(file_rows[i : i + _FILE_CHUNK]))

    await db.commit()
    return VibeIngestResult(
        received=received, inserted=inserted, updated=updated, files=len(file_rows)
    )
