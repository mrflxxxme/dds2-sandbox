# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты Ф6 «АБ-мост + панель метрик» (AC-1..AC-5 спека F6).

Мост строго поверх сервиса funnel: WB/MinIO донора замоканы зеркалом
_FakeWb из test_ab_photo_tests.py; чтение файлов версии — через
design_files.download_submission_file с замоканным download_file
(паттерн no_minio из test_api_design_tasks.py). PNG-фикстуры — реальные
байты PIL ≥700×900: PIL-валидация донора НЕ мокается (AC-3 честный).
"""

import io
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio

from sqlalchemy import func, select

from backend.integrations.wb_content_api import WbContentError
from backend.models.ab_tests import AbPhotoTest, AbPhotoVariant
from backend.models.design import (
    DesignSubmission,
    DesignSubmissionFile,
    DesignTaskStatus,
    DesignVerdict,
)
from backend.models.integrations import WbAdCampaign
from backend.schemas.design import DesignAbTestIn
from backend.services.design import ab_bridge
from backend.services.design import files as design_files
from backend.services.design import stats as design_stats
from backend.services.funnel import ab_photo_tests
from backend.utils.time import utcnow
from tests.design_helpers import actor, add_submission, make_task, make_team

BASE = "/api/v1/design-tasks"

NM_ID = 111
CAMPAIGN_ID = 222


async def _add_campaign(db, project_id: int, campaign_id: int) -> WbAdCampaign:
    """Кампания проекта — валидатор моста требует принадлежности campaign_id проекту."""
    camp = WbAdCampaign(project_id=project_id, campaign_id=campaign_id, name=f"camp-{campaign_id}")
    db.add(camp)
    await db.commit()
    return camp


def _png_bytes(w: int = 700, h: int = 900) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


# ─── Фикстуры ────────────────────────────────────────────────────────────────


@pytest.fixture
def wb_fakes(monkeypatch):
    """WB/MinIO донора и download_file дизайна — на общий in-memory storage."""
    import backend.integrations.wb_content_api as content_api
    import backend.services.funnel.ab_photo_tests as svc

    storage: dict[str, bytes] = {}

    async def fetch_card(key, nm_id):
        return {"title": "Товар", "vendor_code": "vc-1", "photos": [{"big": "u1"}]}

    async def put_photo(key, content, content_type="image/jpeg"):
        storage[key] = content

    async def download_wb(url):
        return b"ORIGINAL"

    async def fake_keys(db, project_id):
        return "content-key", "adv-key", "analytics-key"

    async def fake_download_file(object_name):
        return storage.get(object_name)

    monkeypatch.setattr(content_api, "fetch_card", fetch_card)
    monkeypatch.setattr(svc, "_put_photo", put_photo)
    monkeypatch.setattr(svc, "_download_wb_photo", download_wb)
    monkeypatch.setattr(svc, "_get_keys", fake_keys)
    monkeypatch.setattr(design_files, "download_file", fake_download_file)
    return SimpleNamespace(storage=storage)


async def _add_accepted_version(
    db, project_id: int, task, by_user_id: int, files: list[tuple[str, bytes, str]],
    storage: dict[str, bytes], version_no: int = 1,
) -> DesignSubmission:
    """Версия с verdict=ACCEPTED и файлами (байты — в fake-storage)."""
    sub = DesignSubmission(
        project_id=project_id,
        task_id=task.id,
        version_no=version_no,
        submitted_by_user_id=by_user_id,
        verdict=DesignVerdict.ACCEPTED.value,
    )
    db.add(sub)
    await db.flush()
    for idx, (name, data, mime) in enumerate(files):
        path = f"design/{project_id}/{task.id}/v{version_no}/{idx}_{name}"
        storage[path] = data
        db.add(
            DesignSubmissionFile(
                project_id=project_id,
                submission_id=sub.id,
                minio_path=path,
                original_filename=name,
                mime_type=mime,
                file_size=len(data),
            )
        )
    await db.commit()
    return sub


async def _accepted_task(db, project_id, team, storage, files, nm_id=NM_ID):
    task = await make_task(
        db,
        project_id,
        team.author.id,
        status=DesignTaskStatus.ACCEPTED,
        assignee_id=team.designer.id,
        nm_id=nm_id,
        accepted_at=utcnow(),
    )
    await _add_accepted_version(db, project_id, task, team.designer.id, files, storage)
    return task


async def _variants(db, project_id, test_id) -> list[AbPhotoVariant]:
    res = await db.execute(
        select(AbPhotoVariant)
        .where(AbPhotoVariant.project_id == project_id, AbPhotoVariant.test_id == test_id)
        .order_by(AbPhotoVariant.position)
        .limit(20)
    )
    return list(res.scalars().all())


async def _alive_tests_count(db, project_id) -> int:
    res = await db.execute(
        select(func.count())
        .select_from(AbPhotoTest)
        .where(AbPhotoTest.project_id == project_id, AbPhotoTest.is_deleted == False)  # noqa: E712
    )
    return int(res.scalar() or 0)


# ─── AC-1: полный мост — тест из 2 png принятой версии ───────────────────────


async def test_ac1_create_test_from_accepted_task_with_two_png(db_session, project, wb_fakes):
    team = await make_team(db_session, project)
    task = await _accepted_task(
        db_session, project.id, team, wb_fakes.storage,
        [("a.png", _png_bytes(), "image/png"), ("b.png", _png_bytes(), "image/png")],
    )
    await _add_campaign(db_session, project.id, 222)

    out = await ab_bridge.create_ab_test_from_task(
        db_session, project.id, task.id, actor(team.author), "editor",
        DesignAbTestIn(campaign_id=222),
    )
    assert out.ab_test_id is not None and out.prefill is None

    test = (
        await db_session.execute(select(AbPhotoTest).where(AbPhotoTest.id == out.ab_test_id))
    ).scalar_one()
    assert test.project_id == project.id
    assert test.nm_id == NM_ID  # AC-1: корректный nm_id
    assert test.campaign_id == 222
    assert test.status == "draft"  # автозапуска нет — запускает человек
    assert task.number in (test.comment or "")  # обратная ссылка DES-N

    variants = await _variants(db_session, project.id, test.id)
    loaded = [v for v in variants if not v.is_control]
    assert len(loaded) == 2  # AC-1: два загруженных варианта из 2 png
    assert len(variants) == 3  # + контроль донора (позиция 0)
    assert variants[0].is_control


# ─── AC-2: гварды с точными текстами ─────────────────────────────────────────


async def test_ac2_not_accepted_task_rejected(db_session, project, wb_fakes):
    team = await make_team(db_session, project)
    task = await make_task(
        db_session, project.id, team.author.id,
        status=DesignTaskStatus.IN_PROGRESS, assignee_id=team.designer.id, nm_id=NM_ID,
    )
    with pytest.raises(ValueError) as e:
        await ab_bridge.create_ab_test_from_task(
            db_session, project.id, task.id, actor(team.author), "editor",
            DesignAbTestIn(campaign_id=222),
        )
    assert str(e.value) == "Тест создаётся только по принятой задаче"
    assert await _alive_tests_count(db_session, project.id) == 0


async def test_ac2_no_nm_id_rejected(db_session, project, wb_fakes):
    team = await make_team(db_session, project)
    task = await make_task(
        db_session, project.id, team.author.id,
        status=DesignTaskStatus.ACCEPTED, assignee_id=team.designer.id,
        nm_id=None, accepted_at=utcnow(),
    )
    with pytest.raises(ValueError) as e:
        await ab_bridge.create_ab_test_from_task(
            db_session, project.id, task.id, actor(team.author), "editor",
            DesignAbTestIn(campaign_id=222),
        )
    assert str(e.value) == (
        "Привяжите товар к задаче — тест сравнивает карточку конкретного артикула"
    )


async def test_ac2_version_without_images_rejected(db_session, project, wb_fakes):
    team = await make_team(db_session, project)
    task = await _accepted_task(
        db_session, project.id, team, wb_fakes.storage,
        [("layout.pdf", PDF, "application/pdf")],  # только pdf — изображений нет
    )
    with pytest.raises(ValueError) as e:
        await ab_bridge.create_ab_test_from_task(
            db_session, project.id, task.id, actor(team.author), "editor",
            DesignAbTestIn(campaign_id=222),
        )
    assert str(e.value) == "В принятой версии нет изображений"
    assert await _alive_tests_count(db_session, project.id) == 0


# ─── AC-3: ошибка донора транслируется, половинки теста не остаётся ──────────


async def test_ac3_invalid_image_translates_donor_error_and_compensates(
    db_session, project, wb_fakes
):
    team = await make_team(db_session, project)
    task = await _accepted_task(
        db_session, project.id, team, wb_fakes.storage,
        [
            ("ok.png", _png_bytes(), "image/png"),  # валидный — уже станет вариантом
            ("small.png", _png_bytes(300, 300), "image/png"),  # < 700×900 — PIL донора
        ],
    )
    await _add_campaign(db_session, project.id, 333)
    with pytest.raises(ValueError, match="700×900"):  # текст донора как есть
        await ab_bridge.create_ab_test_from_task(
            db_session, project.id, task.id, actor(team.lead), "admin",
            DesignAbTestIn(campaign_id=333),
        )
    # Компенсация: живых тестов нет, вариантов (вкл. контроль и первый ok.png) нет.
    assert await _alive_tests_count(db_session, project.id) == 0
    orphans = (
        await db_session.execute(
            select(func.count())
            .select_from(AbPhotoVariant)
            .where(AbPhotoVariant.project_id == project.id)
        )
    ).scalar()
    assert int(orphans or 0) == 0
    # Проверка донора «нет активного теста по nm_id» разблокирована — повтор возможен.
    dup = (
        await db_session.execute(
            select(func.count()).select_from(AbPhotoTest).where(
                AbPhotoTest.project_id == project.id,
                AbPhotoTest.nm_id == NM_ID,
                AbPhotoTest.status.in_(("draft", "running", "paused")),
                AbPhotoTest.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar()
    assert int(dup or 0) == 0


# ─── Prefill-ветка (campaign_id обязателен у донора, у задачи его нет) ───────


async def test_prefill_returned_without_campaign_id(db_session, project, wb_fakes):
    team = await make_team(db_session, project)
    task = await _accepted_task(
        db_session, project.id, team, wb_fakes.storage,
        [("a.png", _png_bytes(), "image/png")],
    )
    out = await ab_bridge.create_ab_test_from_task(
        db_session, project.id, task.id, actor(team.author), "editor", DesignAbTestIn()
    )
    assert out.ab_test_id is None
    assert out.prefill is not None
    assert out.prefill.nm_id == NM_ID
    assert out.prefill.from_design_task == task.number
    assert task.number in out.prefill.comment
    assert await _alive_tests_count(db_session, project.id) == 0  # тест НЕ создан


async def test_permission_only_author_or_lead(db_session, project, wb_fakes):
    """permissions.can_create_ab_test: editor-не-автор (дизайнер) — 403."""
    team = await make_team(db_session, project)
    task = await _accepted_task(
        db_session, project.id, team, wb_fakes.storage,
        [("a.png", _png_bytes(), "image/png")],
    )
    with pytest.raises(PermissionError):
        await ab_bridge.create_ab_test_from_task(
            db_session, project.id, task.id, actor(team.designer), "editor",
            DesignAbTestIn(campaign_id=222),
        )


async def test_foreign_campaign_id_rejected(db_session, project, wb_fakes):
    """campaign_id вне проекта → понятный ValueError (400), НЕ тихое создание/500."""
    team = await make_team(db_session, project)
    task = await _accepted_task(
        db_session, project.id, team, wb_fakes.storage,
        [("a.png", _png_bytes(), "image/png")],
    )
    # кампании 999 в проекте нет (её никто не заводил)
    with pytest.raises(ValueError) as e:
        await ab_bridge.create_ab_test_from_task(
            db_session, project.id, task.id, actor(team.author), "editor",
            DesignAbTestIn(campaign_id=999),
        )
    assert str(e.value) == "Рекламная кампания не найдена в проекте"
    assert await _alive_tests_count(db_session, project.id) == 0


async def test_gif_only_version_rejected_before_donor(db_session, project, wb_fakes):
    """Версия из одного GIF: донор его не принимает → 400 понятным текстом ДО create_test."""
    team = await make_team(db_session, project)
    task = await _accepted_task(
        db_session, project.id, team, wb_fakes.storage,
        [("banner.gif", _png_bytes(), "image/gif")],  # mime GIF — не JPEG/PNG/WebP
    )
    await _add_campaign(db_session, project.id, 222)
    with pytest.raises(ValueError) as e:
        await ab_bridge.create_ab_test_from_task(
            db_session, project.id, task.id, actor(team.author), "editor",
            DesignAbTestIn(campaign_id=222),
        )
    assert str(e.value) == (
        "В принятой версии нет подходящих изображений (нужны JPEG, PNG или WebP)"
    )
    assert await _alive_tests_count(db_session, project.id) == 0


async def test_gif_dropped_but_valid_png_proceeds(db_session, project, wb_fakes):
    """Смешанная версия: GIF молча отфильтрован, валидный PNG становится вариантом."""
    team = await make_team(db_session, project)
    task = await _accepted_task(
        db_session, project.id, team, wb_fakes.storage,
        [("banner.gif", _png_bytes(), "image/gif"), ("ok.png", _png_bytes(), "image/png")],
    )
    await _add_campaign(db_session, project.id, 222)
    out = await ab_bridge.create_ab_test_from_task(
        db_session, project.id, task.id, actor(team.author), "editor",
        DesignAbTestIn(campaign_id=222),
    )
    assert out.ab_test_id is not None
    variants = await _variants(db_session, project.id, out.ab_test_id)
    assert len([v for v in variants if not v.is_control]) == 1  # только png, gif выброшен


# ─── AC-4: изоляция проектов ─────────────────────────────────────────────────


async def test_ac4_foreign_project_task_not_found(db_session, project, other_project, wb_fakes):
    team = await make_team(db_session, other_project)
    task = await _accepted_task(
        db_session, other_project.id, team, wb_fakes.storage,
        [("a.png", _png_bytes(), "image/png")],
    )
    with pytest.raises(ValueError) as e:
        await ab_bridge.create_ab_test_from_task(
            db_session, project.id, task.id, actor(team.author), "editor",
            DesignAbTestIn(campaign_id=222),
        )
    assert str(e.value) == "Задача не найдена"  # текст из _NOT_FOUND_TEXTS роутера → 404


# ─── API-контракт ручки POST /{task_id}/ab-test ──────────────────────────────


def _h(headers: dict, project_id: int) -> dict:
    return {**headers, "X-Project-Id": str(project_id)}


def _msg(resp) -> str:
    body = resp.json()
    if "error" in body:
        return body["error"]["message"]
    return str(body.get("detail"))


async def _owner_project(client, auth_headers, name: str):
    resp = await client.post(
        "/api/v1/projects", json={"name": f"{name} {uuid.uuid4().hex[:6]}"}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    me = await client.get("/api/v1/auth/me", headers=auth_headers)
    return resp.json(), me.json()["id"]


@pytest_asyncio.fixture
async def api_env(client, auth_headers, db_session, wb_fakes):
    """Проект владельца (lead) + принятая задача с 2 png (автор = владелец)."""
    proj, uid = await _owner_project(client, auth_headers, "Design F6")
    task = await make_task(
        db_session, proj["id"], uid,
        status=DesignTaskStatus.ACCEPTED, nm_id=555001, accepted_at=utcnow(),
    )
    await _add_accepted_version(
        db_session, proj["id"], task, uid,
        [("a.png", _png_bytes(), "image/png"), ("b.png", _png_bytes(), "image/png")],
        wb_fakes.storage,
    )
    await _add_campaign(db_session, proj["id"], 777)
    return SimpleNamespace(project=proj, uid=uid, task=task, h=_h(auth_headers, proj["id"]))


async def test_api_isolation_foreign_project_404(client, auth_headers, api_env):
    """AC-4 (HTTP): задача проекта A с X-Project-Id проекта B → 404."""
    project_b, _ = await _owner_project(client, auth_headers, "Design F6 B")
    resp = await client.post(
        f"{BASE}/{api_env.task.id}/ab-test",
        json={"campaign_id": 222},
        headers=_h(auth_headers, project_b["id"]),
    )
    assert resp.status_code == 404, resp.text


async def test_api_not_accepted_400_with_text(client, auth_headers, db_session, api_env):
    task = await make_task(
        db_session, api_env.project["id"], api_env.uid,
        status=DesignTaskStatus.NEW, nm_id=555002,
    )
    resp = await client.post(f"{BASE}/{task.id}/ab-test", headers=api_env.h)
    assert resp.status_code == 400, resp.text
    assert _msg(resp) == "Тест создаётся только по принятой задаче"


async def test_api_wb_content_error_maps_to_502(
    client, db_session, api_env, monkeypatch
):
    """create_test донора кидает WbContentError (401/429/5xx/сеть WB) → ручка 502, не 500."""
    async def boom(*args, **kwargs):
        raise WbContentError("WB Content API: 429 Too Many Requests")

    monkeypatch.setattr(ab_photo_tests, "create_test", boom)
    resp = await client.post(
        f"{BASE}/{api_env.task.id}/ab-test", json={"campaign_id": 777}, headers=api_env.h
    )
    assert resp.status_code == 502, resp.text
    assert "429" in _msg(resp)  # текст ошибки донора виден, не проглочен 500
    assert await _alive_tests_count(db_session, api_env.project["id"]) == 0


async def test_api_prefill_without_body_then_create_with_campaign(
    client, db_session, api_env
):
    """Без тела — prefill для редиректа; с campaign_id — созданный тест."""
    resp = await client.post(f"{BASE}/{api_env.task.id}/ab-test", headers=api_env.h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ab_test_id"] is None
    assert body["prefill"]["nm_id"] == 555001
    assert body["prefill"]["from_design_task"] == api_env.task.number

    resp = await client.post(
        f"{BASE}/{api_env.task.id}/ab-test", json={"campaign_id": 777}, headers=api_env.h
    )
    assert resp.status_code == 200, resp.text
    test_id = resp.json()["ab_test_id"]
    assert isinstance(test_id, int)
    variants = await _variants(db_session, api_env.project["id"], test_id)
    assert len([v for v in variants if not v.is_control]) == 2


# ─── AC-5: GET /stats — точные значения на фикстуре из 6 задач ───────────────


async def test_ac5_stats_exact_values_on_six_tasks(db_session, project):
    """2 в срок, 1 просрочена, 1 аутсорс (среди принятых), 2 без товара."""
    team = await make_team(db_session, project)
    base = utcnow() - timedelta(days=30)

    # Принятые: циклы 2, 4, 10 дней; версии до приёмки 1, 2, 3.
    t1 = await make_task(
        db_session, project.id, team.author.id, status=DesignTaskStatus.ACCEPTED,
        assignee_id=team.designer.id, nm_id=101, is_outsourced=True,
        created_at=base, accepted_at=base + timedelta(days=2),
        due_date=(base + timedelta(days=3)).date(),
    )
    await add_submission(db_session, t1, team.designer.id, verdict=DesignVerdict.ACCEPTED)

    t2 = await make_task(
        db_session, project.id, team.author.id, status=DesignTaskStatus.ACCEPTED,
        assignee_id=team.designer.id, nm_id=102,
        created_at=base, accepted_at=base + timedelta(days=4),
        due_date=(base + timedelta(days=5)).date(),
    )
    await add_submission(db_session, t2, team.designer.id, verdict=DesignVerdict.REJECTED)
    await add_submission(
        db_session, t2, team.designer.id, verdict=DesignVerdict.ACCEPTED, version_no=2
    )

    t3 = await make_task(  # просрочена: принята на 10-й день при сроке на 5-й
        db_session, project.id, team.author.id, status=DesignTaskStatus.ACCEPTED,
        assignee_id=team.designer2.id, nm_id=103,
        created_at=base, accepted_at=base + timedelta(days=10),
        due_date=(base + timedelta(days=5)).date(),
    )
    await add_submission(db_session, t3, team.designer2.id, verdict=DesignVerdict.REJECTED)
    await add_submission(
        db_session, t3, team.designer2.id, verdict=DesignVerdict.REJECTED, version_no=2
    )
    await add_submission(
        db_session, t3, team.designer2.id, verdict=DesignVerdict.ACCEPTED, version_no=3
    )

    # Без товара: NEW без исполнителя 3 дня (единственная в unassigned_over_2d)…
    await make_task(
        db_session, project.id, team.author.id, status=DesignTaskStatus.NEW,
        nm_id=None, created_at=utcnow() - timedelta(days=3),
    )
    # …и свежая в работе с исполнителем.
    await make_task(
        db_session, project.id, team.author.id, status=DesignTaskStatus.IN_PROGRESS,
        assignee_id=team.designer.id, nm_id=None,
    )
    # Свежая NEW без исполнителя (< 2 дней — в unassigned_over_2d не входит).
    await make_task(
        db_session, project.id, team.author.id, status=DesignTaskStatus.NEW, nm_id=104
    )

    out = await design_stats.get_stats(db_session, project.id)
    assert out.on_time_share == pytest.approx(2 / 3)  # 2 из 3 принятых — в срок
    assert out.avg_versions_to_accept == pytest.approx(2.0)  # (1+2+3)/3
    assert out.median_cycle_days == pytest.approx(4.0)  # медиана {2, 4, 10}
    assert out.unassigned_over_2d == 1
    assert out.outsourced_share == pytest.approx(1 / 3)
    assert out.tracked_share == pytest.approx(4 / 6)  # 101/102/103/104 из шести
