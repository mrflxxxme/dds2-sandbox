# ruff: noqa: RUF001, RUF002
"""Сервис вкладки «Вайбкодинг»: ингест, ритм, масштаб, разрезы.

У vibe-таблиц НЕТ project_id намеренно (телеметрия репозитория, не данные
арендатора) — изоляция здесь проверяется не по проекту, а по автору: чужие
поставки не должны попадать в мою статистику.
"""

import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from backend.models.auth import User
from backend.models.vibe import VibeAuthor, VibeCommit, VibeFile
from backend.schemas.vibe import VibeIngestCommit, VibeIngestFile
from backend.services import vibe_service

TODAY = date(2026, 7, 17)


def _sha() -> str:
    """Уникальный 40-символьный sha — тесты не должны драться за ключ."""
    return (uuid.uuid4().hex + uuid.uuid4().hex)[:40]


def _commit(
    email: str,
    day: date,
    *,
    sha: str | None = None,
    ctype: str = "feat",
    scope: str = "ads",
    added: int = 10,
    deleted: int = 2,
    files: int = 1,
    is_product: bool = True,
    files_list: list[VibeIngestFile] | None = None,
) -> VibeIngestCommit:
    return VibeIngestCommit(
        sha=sha or _sha(),
        author_email=email,
        authored_on=day,
        ctype=ctype,
        scope=scope,
        title=f"{ctype}({scope}): поставка {day}",
        added=added,
        deleted=deleted,
        files=files,
        is_product=is_product,
        files_list=files_list or [],
    )


def _file(path: str, added: int = 1, deleted: int = 0, is_new: bool = False) -> VibeIngestFile:
    return VibeIngestFile(path=path, added=added, deleted=deleted, is_new=is_new)


@pytest_asyncio.fixture
async def vibe_user(db_session):
    """Пользователь + одна git-почта в vibe_authors. Чистит за собой."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        username=f"vibe_{suffix}",
        email=f"vibe_{suffix}@test.com",
        password_hash="x",
    )
    db_session.add(user)
    await db_session.flush()
    email = f"dev_{suffix}@example.com"
    db_session.add(
        VibeAuthor(user_id=user.id, git_email=email, display_name="Тестовый Вайбкодер")
    )
    await db_session.commit()

    yield user, email

    # lower(): поставки хранятся с нормализованной почтой, а в vibe_authors
    # почта может лежать в исходном регистре — иначе уборка их не найдёт.
    emails = [
        e.lower()
        for e in (
            await db_session.execute(
                select(VibeAuthor.git_email).where(VibeAuthor.user_id == user.id)
            )
        )
        .scalars()
        .all()
    ]
    shas = list(
        (
            await db_session.execute(
                select(VibeCommit.sha).where(VibeCommit.author_email.in_(emails))
            )
        )
        .scalars()
        .all()
    )
    if shas:
        await db_session.execute(delete(VibeFile).where(VibeFile.sha.in_(shas)))
        await db_session.execute(delete(VibeCommit).where(VibeCommit.sha.in_(shas)))
    await db_session.execute(delete(VibeAuthor).where(VibeAuthor.user_id == user.id))
    await db_session.execute(delete(User).where(User.id == user.id))
    await db_session.commit()


# ─── Ингест ─────────────────────────────────────────────────────────────────


async def test_ingest_is_idempotent(db_session, vibe_user):
    """Повторный прогон CI не задваивает ни поставки, ни файлы."""
    _, email = vibe_user
    sha = _sha()
    commits = [
        _commit(
            email,
            TODAY,
            sha=sha,
            files=2,
            files_list=[
                _file("backend/services/ads_service.py", added=8, deleted=1),
                _file("tests/test_ads.py", added=2, deleted=1, is_new=True),
            ],
        )
    ]

    first = await vibe_service.ingest(db_session, commits)
    assert (first.received, first.inserted, first.updated, first.files) == (1, 1, 0, 2)

    second = await vibe_service.ingest(db_session, commits)
    assert (second.received, second.inserted, second.updated, second.files) == (1, 0, 1, 2)

    rows = (
        await db_session.execute(select(VibeCommit).where(VibeCommit.sha == sha))
    ).scalars().all()
    assert len(rows) == 1
    file_rows = (
        await db_session.execute(select(VibeFile).where(VibeFile.sha == sha))
    ).scalars().all()
    assert len(file_rows) == 2

    stats = await vibe_service.get_stats(db_session, vibe_user[0].id, TODAY, TODAY)
    assert stats.shipments_total == 1
    assert stats.scale.files == 2


async def test_ingest_dedups_repeated_sha_in_one_batch(db_session, vibe_user):
    """Один sha дважды в батче — не CardinalityViolation, побеждает последний."""
    _, email = vibe_user
    sha = _sha()
    result = await vibe_service.ingest(
        db_session,
        [
            _commit(email, TODAY, sha=sha, added=1),
            _commit(email, TODAY, sha=sha, added=99),
        ],
    )
    assert result.received == 2
    assert result.inserted == 1
    row = await db_session.scalar(select(VibeCommit).where(VibeCommit.sha == sha))
    assert row.added == 99


async def test_ingest_empty_batch(db_session):
    """Пустой батч — не падение."""
    result = await vibe_service.ingest(db_session, [])
    assert (result.received, result.inserted, result.updated) == (0, 0, 0)


async def test_ingest_normalizes_email_case(db_session, vibe_user):
    """Почта склеивается в lower-case: CI прислал Dev@Example, автор видит поставку."""
    user, email = vibe_user
    await vibe_service.ingest(db_session, [_commit(email.upper(), TODAY)])
    stats = await vibe_service.get_stats(db_session, user.id, TODAY, TODAY)
    assert stats.shipments_total == 1


async def test_author_email_matched_case_insensitively(db_session, vibe_user):
    """Почта в vibe_authors с заглавными склеивается с lower-case поставкой.

    Реальный кейс: git-почта машины — `denisdmitriev@MacBook-Air-7.local`, а
    генератор (scripts/vibe_stats.py) отдаёт её в нижнем регистре.
    """
    user, _ = vibe_user
    mixed = f"Dev_{uuid.uuid4().hex[:8]}@MacBook-Air-7.local"
    db_session.add(VibeAuthor(user_id=user.id, git_email=mixed))
    await db_session.commit()

    await vibe_service.ingest(db_session, [_commit(mixed.lower(), TODAY)])
    stats = await vibe_service.get_stats(db_session, user.id, TODAY, TODAY)
    assert stats.shipments_total == 1


async def test_reingest_rewrites_files_of_commit(db_session, vibe_user):
    """Другой срез файлов того же sha перезаписывает старый, а не копится хвостом."""
    _, email = vibe_user
    sha = _sha()
    await vibe_service.ingest(
        db_session,
        [_commit(email, TODAY, sha=sha, files_list=[_file("backend/old.py"), _file("backend/gone.py")])],
    )
    await vibe_service.ingest(
        db_session,
        [_commit(email, TODAY, sha=sha, files_list=[_file("backend/old.py", added=3)])],
    )

    rows = (
        (await db_session.execute(select(VibeFile).where(VibeFile.sha == sha))).scalars().all()
    )
    assert [(r.path, r.added) for r in rows] == [("backend/old.py", 3)]


async def test_ingest_dedups_repeated_path_in_one_commit(db_session, vibe_user):
    """Дубль пути внутри files_list не роняет INSERT (PK sha+path)."""
    _, email = vibe_user
    sha = _sha()
    result = await vibe_service.ingest(
        db_session,
        [
            _commit(
                email,
                TODAY,
                sha=sha,
                files_list=[_file("backend/x.py", added=1), _file("backend/x.py", added=7)],
            )
        ],
    )
    assert result.files == 1
    row = await db_session.scalar(select(VibeFile).where(VibeFile.sha == sha))
    assert row.added == 7


# ─── Доступ и склейка почт ──────────────────────────────────────────────────


async def test_non_vibecoder_gets_none(db_session):
    """Нет строки в vibe_authors → None (роутер превратит в 403)."""
    user = User(
        username=f"plain_{uuid.uuid4().hex[:8]}",
        email=f"plain_{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x",
    )
    db_session.add(user)
    await db_session.commit()
    try:
        assert await vibe_service.get_stats(db_session, user.id, TODAY, TODAY) is None
        assert await vibe_service.is_vibecoder(db_session, user.id) is False
    finally:
        await db_session.execute(delete(User).where(User.id == user.id))
        await db_session.commit()


async def test_author_sees_shipments_from_all_his_emails(db_session, vibe_user):
    """Несколько git-почт (разные машины) = одна статистика."""
    user, email = vibe_user
    second_email = f"laptop_{uuid.uuid4().hex[:8]}@example.com"
    db_session.add(VibeAuthor(user_id=user.id, git_email=second_email))
    await db_session.commit()

    await vibe_service.ingest(
        db_session,
        [
            _commit(email, TODAY, added=10),
            _commit(second_email, TODAY, added=5),
        ],
    )

    stats = await vibe_service.get_stats(db_session, user.id, TODAY, TODAY)
    assert stats.shipments_total == 2
    assert stats.scale.added == 15
    assert await vibe_service.is_vibecoder(db_session, user.id) is True


async def test_foreign_shipments_excluded(db_session, vibe_user):
    """Чужая почта в vibe_commits в мою статистику не попадает."""
    user, email = vibe_user
    foreign = f"someone_{uuid.uuid4().hex[:8]}@example.com"
    await vibe_service.ingest(
        db_session, [_commit(email, TODAY), _commit(foreign, TODAY)]
    )
    try:
        stats = await vibe_service.get_stats(db_session, user.id, TODAY, TODAY)
        assert stats.shipments_total == 1
    finally:
        await db_session.execute(
            delete(VibeCommit).where(VibeCommit.author_email == foreign)
        )
        await db_session.commit()


# ─── Ритм ───────────────────────────────────────────────────────────────────


async def test_rhythm_pause_does_not_reset(db_session, vibe_user):
    """Пауза не обнуляет: ритм — дни с поставкой в окне, а не стрик подряд."""
    user, email = vibe_user
    # Поставки: 13 дней назад, 12, потом пауза, и сегодня.
    days = [TODAY - timedelta(days=13), TODAY - timedelta(days=12), TODAY]
    await vibe_service.ingest(db_session, [_commit(email, d) for d in days])

    stats = await vibe_service.get_stats(db_session, user.id, TODAY, TODAY)
    assert stats.rhythm.hit == 3  # стрик дал бы 1
    assert stats.rhythm.denom == 14
    assert stats.rhythm.start == TODAY - timedelta(days=13)
    assert stats.rhythm.end == TODAY


async def test_rhythm_counts_day_once(db_session, vibe_user):
    """Три поставки в один день — это один день ритма, а не три."""
    user, email = vibe_user
    await vibe_service.ingest(db_session, [_commit(email, TODAY) for _ in range(3)])
    stats = await vibe_service.get_stats(db_session, user.id, TODAY, TODAY)
    assert stats.rhythm.hit == 1
    assert stats.shipments_total == 3


async def test_rhythm_window_trimmed_by_first_shipment(db_session, vibe_user):
    """Новичок на второй день видит «2 из 2», а не «2 из 14»."""
    user, email = vibe_user
    first = TODAY - timedelta(days=1)
    await vibe_service.ingest(
        db_session, [_commit(email, first), _commit(email, TODAY)]
    )
    stats = await vibe_service.get_stats(db_session, user.id, TODAY, TODAY)
    assert stats.rhythm.hit == 2
    assert stats.rhythm.denom == 2
    assert stats.rhythm.start == first
    assert stats.rhythm.window == 14


async def test_rhythm_window_full_for_old_author(db_session, vibe_user):
    """Автор с давней первой поставкой получает полное окно в 14 дней."""
    user, email = vibe_user
    await vibe_service.ingest(
        db_session,
        [_commit(email, TODAY - timedelta(days=100)), _commit(email, TODAY)],
    )
    stats = await vibe_service.get_stats(db_session, user.id, TODAY, TODAY)
    assert stats.rhythm.denom == 14
    assert stats.rhythm.hit == 1


async def test_rhythm_for_period_before_first_shipment(db_session, vibe_user):
    """Отчёт за период ДО первой поставки: окно не уезжает в будущее, 0 из 1."""
    user, email = vibe_user
    await vibe_service.ingest(db_session, [_commit(email, TODAY)])
    old = TODAY - timedelta(days=60)

    stats = await vibe_service.get_stats(db_session, user.id, old, old)
    assert stats.shipments_total == 0
    assert stats.rhythm.hit == 0
    assert stats.rhythm.start == old
    assert stats.rhythm.end == old
    assert stats.rhythm.denom == 1


async def test_rhythm_is_about_window_not_report_period(db_session, vibe_user):
    """Период отчёта — один день, а ритм всё равно про 14 дней."""
    user, email = vibe_user
    days = [TODAY - timedelta(days=n) for n in (10, 5, 0)]
    await vibe_service.ingest(db_session, [_commit(email, d) for d in days])

    stats = await vibe_service.get_stats(db_session, user.id, TODAY, TODAY)
    assert stats.shipments_total == 1  # в периоде — только сегодняшняя
    assert stats.rhythm.hit == 3  # а в окне — все три
    # Окно начинается раньше периода отчёта (и обрезано первой поставкой автора).
    assert stats.rhythm.start == TODAY - timedelta(days=10)
    assert stats.rhythm.start < stats.since
    assert stats.rhythm.denom == 11


# ─── Разрезы ────────────────────────────────────────────────────────────────


async def test_by_day_includes_empty_days(db_session, vibe_user):
    """by_day отдаёт ВСЕ дни периода: фронт рисует пропуски нулями."""
    user, email = vibe_user
    since = TODAY - timedelta(days=4)
    await vibe_service.ingest(
        db_session, [_commit(email, TODAY, added=7, deleted=3)]
    )

    stats = await vibe_service.get_stats(db_session, user.id, since, TODAY)
    assert len(stats.by_day) == 5
    assert [d.day for d in stats.by_day] == [since + timedelta(days=n) for n in range(5)]
    assert [d.shipments for d in stats.by_day] == [0, 0, 0, 0, 1]
    assert stats.by_day[-1].added == 7
    assert stats.by_day[-1].deleted == 3
    assert stats.by_day[0].added == 0


async def test_empty_period_returns_empty_stats(db_session, vibe_user):
    """Период без поставок — корректный нулевой ответ, не падение."""
    user, _ = vibe_user
    since = TODAY - timedelta(days=2)
    stats = await vibe_service.get_stats(db_session, user.id, since, TODAY)

    assert stats is not None
    assert stats.display_name == "Тестовый Вайбкодер"
    assert stats.shipments_total == 0
    assert stats.shipments_product == 0
    assert stats.shipments == []
    assert stats.by_section == []
    assert stats.scale.files == 0
    assert stats.scale.added == 0
    assert stats.scale.by_area == []
    assert len(stats.by_day) == 3
    assert all(d.shipments == 0 for d in stats.by_day)
    # Поставок нет вовсе — обрезать окно нечем, так что оно полное: «0 из 14».
    assert stats.rhythm.hit == 0
    assert stats.rhythm.denom == 14


async def test_scale_counts_unique_files_and_areas(db_session, vibe_user):
    """Файл, тронутый двумя поставками, — один файл. Области — по префиксу пути."""
    user, email = vibe_user
    sha1, sha2 = _sha(), _sha()
    await vibe_service.ingest(
        db_session,
        [
            _commit(
                email,
                TODAY,
                sha=sha1,
                added=30,
                deleted=0,
                files_list=[
                    _file("backend/services/ads_service.py", added=20),
                    _file("frontend-react/src/components/AdsCard.tsx", added=8, is_new=True),
                    _file("migrations/versions/ads11_x.py", added=2, is_new=True),
                ],
            ),
            _commit(
                email,
                TODAY,
                sha=sha2,
                added=5,
                deleted=1,
                # Тот же путь, что в sha1 — уникальных файлов от этого не прибавится.
                files_list=[_file("backend/services/ads_service.py", added=5, deleted=1)],
            ),
        ],
    )

    stats = await vibe_service.get_stats(db_session, user.id, TODAY, TODAY)
    assert stats.scale.files == 3
    assert stats.scale.new_files == 2
    assert stats.scale.components == 1  # новый .tsx
    assert stats.scale.migrations == 1
    assert stats.scale.added == 35  # тотал — по коммитам
    assert stats.scale.deleted == 1

    by_area = {a.area: a for a in stats.scale.by_area}
    assert set(by_area) == {"Бэкенд", "Фронтенд", "Миграции БД"}
    assert by_area["Бэкенд"].files == 1
    assert by_area["Бэкенд"].added == 25  # 20 + 5 по обеим поставкам
    assert by_area["Бэкенд"].deleted == 1
    assert by_area["Фронтенд"].files == 1


async def test_by_section_only_product_shipments(db_session, vibe_user):
    """Разделы — по продуктовым поставкам; scope без словаря идёт как есть."""
    user, email = vibe_user
    await vibe_service.ingest(
        db_session,
        [
            _commit(email, TODAY, scope="ads"),
            _commit(email, TODAY, scope="ads-manager"),
            _commit(email, TODAY, scope="raw-data"),
            _commit(email, TODAY, scope="warehouse"),  # нет в словаре
            _commit(email, TODAY, scope="", is_product=True),
            _commit(email, TODAY, scope="deps", ctype="chore", is_product=False),
        ],
    )

    stats = await vibe_service.get_stats(db_session, user.id, TODAY, TODAY)
    assert stats.shipments_total == 6
    assert stats.shipments_product == 5

    sections = {row["section"]: row["count"] for row in stats.by_section}
    assert sections == {
        "Управление рекламой": 2,  # ads + ads-manager склеились
        "Сырые данные": 1,
        "warehouse": 1,
        "Без раздела": 1,
    }
    assert "Зависимости и CI" not in sections  # непродуктовая
    assert stats.scale.sections == 4


async def test_shipment_fields(db_session, vibe_user):
    """Строка поставки: короткий sha и человеческое имя раздела."""
    user, email = vibe_user
    sha = _sha()
    await vibe_service.ingest(db_session, [_commit(email, TODAY, sha=sha, scope="raw-data")])
    stats = await vibe_service.get_stats(db_session, user.id, TODAY, TODAY)
    shipment = stats.shipments[0]
    assert shipment.sha == sha
    assert shipment.short == sha[:7]
    assert shipment.section == "Сырые данные"
    assert shipment.day == TODAY
    assert stats.last_ingest is not None


@pytest.mark.parametrize(
    "path,area",
    [
        ("frontend-react/src/app/page.tsx", "Фронтенд"),
        ("backend/services/x.py", "Бэкенд"),
        ("tests/test_x.py", "Тесты"),
        ("migrations/versions/x.py", "Миграции БД"),
        ("docs/x.md", "Документация"),
        ("Makefile", "Прочее"),
        ("scripts/x.sh", "Прочее"),
    ],
)
def test_area_for_path(path, area):
    assert vibe_service.area_for_path(path) == area


@pytest.mark.parametrize(
    "scope,section",
    [
        ("ads", "Управление рекламой"),
        ("ads-manager", "Управление рекламой"),
        ("raw-data", "Сырые данные"),
        ("orders", "Заказы"),
        ("migrations", "Миграции БД"),
        ("integrations", "Интеграции"),
        ("ui", "Интерфейс"),
        ("deps", "Зависимости и CI"),
        ("mypy", "Зависимости и CI"),
        ("ci", "Зависимости и CI"),
        ("tests", "Тесты"),
        ("ADS", "Управление рекламой"),
        ("supply", "supply"),
        ("", "Без раздела"),
        (None, "Без раздела"),
    ],
)
def test_section_for_scope(scope, section):
    assert vibe_service.section_for_scope(scope) == section
