# ruff: noqa: RUF001, RUF002
"""API вкладки «Вайбкодинг»: /api/v1/vibe/me и /api/v1/vibe/is-vibecoder.

Доступ режется наличием строки в vibe_authors, а не проектной ролью: обычный
пользователь (владелец своего проекта!) обязан получать 403.
"""

import uuid
from datetime import date, timedelta

import pytest_asyncio
from sqlalchemy import delete, select

from backend.models.auth import User
from backend.models.vibe import VibeAuthor, VibeCommit, VibeFile
from backend.schemas.vibe import VibeIngestCommit, VibeIngestFile
from backend.services import vibe_service
from backend.utils.time import utcnow


def _sha() -> str:
    return (uuid.uuid4().hex + uuid.uuid4().hex)[:40]


@pytest_asyncio.fixture
async def registered_user(client, db_session):
    """Зарегистрированный пользователь: заголовки + строка User. Чистит за собой."""
    username = f"vibeapi_{uuid.uuid4().hex[:8]}"
    await client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "password": "testpass123",
            "email": f"{username}@test.com",
        },
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "testpass123"}
    )
    data = resp.json()
    assert "access_token" in data, f"Login failed ({resp.status_code}): {data}"
    headers = {"Authorization": f"Bearer {data['access_token']}"}

    user = await db_session.scalar(select(User).where(User.username == username))
    assert user is not None

    yield user, headers

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
    if emails:
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
    await db_session.commit()


async def _make_vibecoder(db_session, user: User) -> str:
    email = f"dev_{uuid.uuid4().hex[:8]}@example.com"
    db_session.add(VibeAuthor(user_id=user.id, git_email=email, display_name="Денис"))
    await db_session.commit()
    return email


# ─── Доступ ─────────────────────────────────────────────────────────────────


async def test_me_403_for_non_vibecoder(client, registered_user):
    """Обычный пользователь — 403, а не пустая статистика."""
    _, headers = registered_user
    resp = await client.get("/api/v1/vibe/me", headers=headers)
    assert resp.status_code == 403


async def test_me_401_without_token(client):
    resp = await client.get("/api/v1/vibe/me")
    assert resp.status_code in (401, 403)


async def test_is_vibecoder_false_then_true(client, db_session, registered_user):
    """Флаг для сайдбара: 200 в обоих случаях, «нет» — не отказ."""
    user, headers = registered_user

    resp = await client.get("/api/v1/vibe/is-vibecoder", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"is_vibecoder": False}

    await _make_vibecoder(db_session, user)

    resp = await client.get("/api/v1/vibe/is-vibecoder", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"is_vibecoder": True}


# ─── /me ────────────────────────────────────────────────────────────────────


async def test_me_returns_stats_for_vibecoder(client, db_session, registered_user):
    """Вайбкодер видит свои поставки за период."""
    user, headers = registered_user
    email = await _make_vibecoder(db_session, user)
    today = utcnow().date()
    sha = _sha()
    await vibe_service.ingest(
        db_session,
        [
            VibeIngestCommit(
                sha=sha,
                author_email=email,
                authored_on=today,
                ctype="feat",
                scope="ads",
                title="feat(ads): колонка «Реком. ставка»",
                added=120,
                deleted=8,
                files=2,
                is_product=True,
                files_list=[
                    VibeIngestFile(path="backend/services/ads_service.py", added=100, deleted=8),
                    VibeIngestFile(
                        path="frontend-react/src/components/Bid.tsx", added=20, is_new=True
                    ),
                ],
            )
        ],
    )

    resp = await client.get("/api/v1/vibe/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body["display_name"] == "Денис"
    assert body["shipments_total"] == 1
    assert body["shipments_product"] == 1
    assert body["shipments"][0]["short"] == sha[:7]
    assert body["shipments"][0]["section"] == "Управление рекламой"
    assert body["scale"]["files"] == 2
    assert body["scale"]["components"] == 1
    assert body["by_section"] == [{"section": "Управление рекламой", "count": 1}]
    assert body["rhythm"]["window"] == 14
    assert body["rhythm"]["hit"] == 1


async def test_me_default_period_is_30_days(client, db_session, registered_user):
    """Без since/until — последние 30 дней включительно."""
    user, headers = registered_user
    await _make_vibecoder(db_session, user)

    resp = await client.get("/api/v1/vibe/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    today = utcnow().date()
    assert body["until"] == today.isoformat()
    assert body["since"] == (today - timedelta(days=29)).isoformat()
    assert len(body["by_day"]) == 30


async def test_me_empty_period_is_not_an_error(client, db_session, registered_user):
    """Вайбкодер без поставок в периоде — 200 с нулями, не 500."""
    user, headers = registered_user
    await _make_vibecoder(db_session, user)

    resp = await client.get(
        "/api/v1/vibe/me",
        headers=headers,
        params={"since": "2020-01-01", "until": "2020-01-03"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["shipments_total"] == 0
    assert body["shipments"] == []
    assert [d["shipments"] for d in body["by_day"]] == [0, 0, 0]
    assert body["scale"]["by_area"] == []


async def test_me_by_day_covers_days_without_shipments(client, db_session, registered_user):
    """by_day отдаёт все дни периода — фронт рисует пропуски."""
    user, headers = registered_user
    email = await _make_vibecoder(db_session, user)
    today = utcnow().date()
    await vibe_service.ingest(
        db_session,
        [
            VibeIngestCommit(
                sha=_sha(),
                author_email=email,
                authored_on=today,
                ctype="fix",
                scope="raw-data",
                title="fix(raw-data): дозагрузка",
                added=4,
                deleted=1,
                files=1,
            )
        ],
    )

    since = today - timedelta(days=3)
    resp = await client.get(
        "/api/v1/vibe/me",
        headers=headers,
        params={"since": since.isoformat(), "until": today.isoformat()},
    )
    assert resp.status_code == 200
    by_day = resp.json()["by_day"]
    assert len(by_day) == 4
    assert [d["shipments"] for d in by_day] == [0, 0, 0, 1]
    assert by_day[-1]["added"] == 4


# ─── Валидация периода ──────────────────────────────────────────────────────


async def test_me_rejects_inverted_period(client, db_session, registered_user):
    user, headers = registered_user
    await _make_vibecoder(db_session, user)
    resp = await client.get(
        "/api/v1/vibe/me",
        headers=headers,
        params={"since": "2026-07-17", "until": "2026-07-01"},
    )
    assert resp.status_code == 400


async def test_me_rejects_too_long_period(client, db_session, registered_user):
    """Период сверху ограничен: by_day линеен по длине периода."""
    user, headers = registered_user
    await _make_vibecoder(db_session, user)
    resp = await client.get(
        "/api/v1/vibe/me",
        headers=headers,
        params={"since": "2000-01-01", "until": date(2026, 7, 17).isoformat()},
    )
    assert resp.status_code == 400


# ─── Селектор разработчика (любой вайбкодер видит всех) ─────────────────────


async def test_authors_forbidden_for_non_vibecoder(client, db_session, registered_user):
    """Список сотрудников — внутренние данные: клиенту его не отдаём."""
    _user, headers = registered_user
    resp = await client.get("/api/v1/vibe/authors", headers=headers)
    assert resp.status_code == 403


async def test_authors_lists_vibecoders(client, db_session, registered_user):
    user, headers = registered_user
    await _make_vibecoder(db_session, user)

    resp = await client.get("/api/v1/vibe/authors", headers=headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert any(r["user_id"] == user.id and r["name"] == "Денис" for r in rows)


async def test_authors_dedups_multiple_emails_of_one_person(
    client, db_session, registered_user
):
    """Две git-почты одного человека — ОДНА строка в селекторе, не две.

    Реальный кейс: у Дениса denlyublyukatyu@gmail.com и denisdmitriev@macbook-air-7.local.
    """
    user, headers = registered_user
    await _make_vibecoder(db_session, user)
    db_session.add(
        VibeAuthor(user_id=user.id, git_email=f"second_{uuid.uuid4().hex[:6]}@example.com")
    )
    await db_session.commit()

    rows = (await client.get("/api/v1/vibe/authors", headers=headers)).json()
    assert len([r for r in rows if r["user_id"] == user.id]) == 1


async def test_me_shows_other_author_stats(client, db_session, registered_user):
    """Вайбкодер может открыть статистику другого вайбкодера."""
    user, headers = registered_user
    await _make_vibecoder(db_session, user)

    other = User(
        username=f"other_{uuid.uuid4().hex[:8]}",
        password_hash="x",
        email=f"other_{uuid.uuid4().hex[:8]}@test.com",
    )
    db_session.add(other)
    await db_session.commit()
    other_email = f"other_{uuid.uuid4().hex[:8]}@example.com"
    db_session.add(
        VibeAuthor(user_id=other.id, git_email=other_email, display_name="Влад")
    )
    await db_session.commit()

    today = utcnow().date()
    await vibe_service.ingest(
        db_session,
        [
            VibeIngestCommit(
                sha=_sha(),
                author_email=other_email,
                authored_on=today,
                ctype="feat",
                scope="ads",
                title="чужая поставка",
                added=10,
                deleted=0,
                files=1,
                is_product=True,
                files_list=[VibeIngestFile(path="backend/x.py", added=10, is_new=True)],
            )
        ],
    )

    resp = await client.get(
        f"/api/v1/vibe/me?author_id={other.id}", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Влад"
    assert body["shipments_total"] == 1

    # Своя статистика при этом пуста — данные не перемешались.
    mine = (await client.get("/api/v1/vibe/me", headers=headers)).json()
    assert mine["shipments_total"] == 0

    await db_session.execute(delete(VibeFile))
    await db_session.execute(delete(VibeCommit).where(VibeCommit.author_email == other_email))
    await db_session.execute(delete(VibeAuthor).where(VibeAuthor.user_id == other.id))
    await db_session.execute(delete(User).where(User.id == other.id))
    await db_session.commit()


async def test_me_with_unknown_author_is_404_not_403(client, db_session, registered_user):
    """Чужой/несуществующий id — 404. Слить его с 403 нельзя: тогда клиент не отличит
    «нет такого автора» от «я потерял доступ»."""
    user, headers = registered_user
    await _make_vibecoder(db_session, user)

    resp = await client.get("/api/v1/vibe/me?author_id=999999", headers=headers)
    assert resp.status_code == 404


async def test_non_vibecoder_cannot_read_others_via_author_id(
    client, db_session, registered_user
):
    """Гейт проверяется по ЗАПРАШИВАЮЩЕМУ: не-вайбкодер не обойдёт его чужим id."""
    _user, headers = registered_user
    resp = await client.get("/api/v1/vibe/me?author_id=1", headers=headers)
    assert resp.status_code == 403


async def test_authors_prefer_display_name_over_username(
    client, db_session, registered_user
):
    """Имя ищется по ВСЕМ строкам автора, а не по первой попавшейся.

    Реальный баг: у человека несколько git-почт, display_name задан только у одной.
    Строка без имени, попавшаяся первой, закрепляла username — в селекторе висели
    «admin» и «ivnfs» вместо «Влад Вяткин» и «Иван».
    """
    user, headers = registered_user
    # Первая строка — БЕЗ имени, вторая — с именем.
    db_session.add(
        VibeAuthor(user_id=user.id, git_email=f"a_{uuid.uuid4().hex[:6]}@example.com")
    )
    await db_session.commit()
    db_session.add(
        VibeAuthor(
            user_id=user.id,
            git_email=f"b_{uuid.uuid4().hex[:6]}@example.com",
            display_name="Влад Вяткин",
        )
    )
    await db_session.commit()

    rows = (await client.get("/api/v1/vibe/authors", headers=headers)).json()
    mine = [r for r in rows if r["user_id"] == user.id]
    assert len(mine) == 1
    assert mine[0]["name"] == "Влад Вяткин", "имя проиграло username"
