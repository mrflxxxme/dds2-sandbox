"""Юнит-тесты чистой логики АБ-тестов главного фото (без БД и WB).

Косвенно фиксируют контракт: дельты накопительных счётчиков с обнулением в полночь МСК,
round-robin ротация со skip исключённых, финиш по target_views/max_days, «победы в
раундах» по циклам, сверка галереи со снимком.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

from backend.services.funnel.ab_photo_tests import (
    compute_tick_delta,
    cycle_wins,
    is_finished,
    media_matches_snapshot,
    next_variant,
    should_rotate,
    variants_progress,
)


def _variant(vid, position, excluded=False, control=False):
    return SimpleNamespace(
        id=vid, position=position, excluded_at=datetime(2026, 7, 1) if excluded else None, is_control=control
    )


def _round(variant_id, views, clicks=0, round_no=1):
    return SimpleNamespace(variant_id=variant_id, views=views, clicks=clicks, round_no=round_no)


def _test(views_per_round=5000, round_minutes=45, target_views=10000, max_days=7, started_at=None):
    return SimpleNamespace(
        views_per_round=views_per_round,
        round_minutes=round_minutes,
        target_views=target_views,
        max_days=max_days,
        started_at=started_at,
    )


# ── compute_tick_delta ───────────────────────────────────────────────────────


def test_delta_first_tick_uses_full_cumulative():
    adv = {"views": 100, "clicks": 10, "atbs": 3, "orders": 1, "spend": 50.5, "orders_sum": 1000.0}
    org = {"open": 20, "cart": 5, "orders": 2}
    delta, last = compute_tick_delta(None, "2026-07-13", adv, org)
    assert delta["views"] == 100 and delta["clicks"] == 10
    assert delta["spend"] == 50.5
    assert delta["organic_open"] == 20 and delta["organic_orders"] == 2
    assert last["stat_date"] == "2026-07-13"
    assert last["adv"]["views"] == 100


def test_delta_same_day_increment():
    _, last = compute_tick_delta(
        None, "2026-07-13", {"views": 100, "clicks": 10}, {"open": 20, "cart": 0, "orders": 0}
    )
    delta, _ = compute_tick_delta(
        last, "2026-07-13", {"views": 150, "clicks": 12}, {"open": 25, "cart": 1, "orders": 0}
    )
    assert delta["views"] == 50 and delta["clicks"] == 2
    assert delta["organic_open"] == 5 and delta["organic_cart"] == 1


def test_delta_negative_correction_clamped_to_zero():
    _, last = compute_tick_delta(None, "2026-07-13", {"views": 100, "clicks": 10}, {"open": 20})
    delta, _ = compute_tick_delta(last, "2026-07-13", {"views": 90, "clicks": 11}, {"open": 20})
    assert delta["views"] == 0  # WB скорректировал вниз — не уводим круг в минус
    assert delta["clicks"] == 1


def test_delta_midnight_reset_starts_from_zero_base():
    _, last = compute_tick_delta(None, "2026-07-13", {"views": 5000}, {"open": 100})
    delta, new_last = compute_tick_delta(last, "2026-07-14", {"views": 30}, {"open": 2})
    assert delta["views"] == 30  # новый день: база 0, а не вчерашние 5000
    assert new_last["stat_date"] == "2026-07-14"


# ── next_variant / ротация ───────────────────────────────────────────────────


def test_next_variant_round_robin_with_wrap():
    vs = [_variant(1, 0, control=True), _variant(2, 1), _variant(3, 2)]
    assert next_variant(vs, 1).id == 2
    assert next_variant(vs, 2).id == 3
    assert next_variant(vs, 3).id == 1  # wrap


def test_next_variant_skips_excluded():
    vs = [_variant(1, 0), _variant(2, 1, excluded=True), _variant(3, 2)]
    assert next_variant(vs, 1).id == 3


def test_next_variant_none_when_single_active():
    vs = [_variant(1, 0), _variant(2, 1, excluded=True)]
    assert next_variant(vs, 1) is None


def test_next_variant_current_excluded_falls_back_to_first_active():
    vs = [_variant(1, 0, excluded=True), _variant(2, 1), _variant(3, 2)]
    assert next_variant(vs, 1).id == 2


def test_should_rotate_by_time_even_without_views():
    # смена гарантирована по времени: слабый трафик не растягивает круг
    t = _test(views_per_round=5000, round_minutes=45)
    now = datetime(2026, 7, 13, 12, 0)
    early = now - timedelta(minutes=30)
    old = now - timedelta(minutes=45)
    assert not should_rotate(0, early, t, now)  # ни показов, ни времени
    assert should_rotate(0, old, t, now)  # время вышло — меняем в любом случае


def test_should_rotate_early_by_views():
    # большой трафик: показы набраны раньше времени — меняем досрочно
    t = _test(views_per_round=5000, round_minutes=45)
    now = datetime(2026, 7, 13, 12, 0)
    early = now - timedelta(minutes=10)
    assert should_rotate(5000, early, t, now)
    assert not should_rotate(4999, early, t, now)


# ── финиш ────────────────────────────────────────────────────────────────────


def test_is_finished_when_all_active_reach_target():
    vs = [_variant(1, 0), _variant(2, 1)]
    t = _test(target_views=1000, started_at=datetime(2026, 7, 13))
    now = datetime(2026, 7, 14)
    assert not is_finished(t, vs, {1: 1000, 2: 999}, now)
    assert is_finished(t, vs, {1: 1000, 2: 1000}, now)


def test_is_finished_ignores_excluded_variants():
    vs = [_variant(1, 0), _variant(2, 1, excluded=True)]
    t = _test(target_views=1000, started_at=datetime(2026, 7, 13))
    assert is_finished(t, vs, {1: 1000, 2: 0}, datetime(2026, 7, 14))


def test_is_finished_by_max_days_safety_valve():
    vs = [_variant(1, 0), _variant(2, 1)]
    t = _test(target_views=10**9, max_days=7, started_at=datetime(2026, 7, 1))
    assert not is_finished(t, vs, {1: 0, 2: 0}, datetime(2026, 7, 7, 23, 0))
    assert is_finished(t, vs, {1: 0, 2: 0}, datetime(2026, 7, 8, 0, 0))


def test_variants_progress_sums_all_rounds():
    vs = [_variant(1, 0), _variant(2, 1)]
    rounds = [_round(1, 100), _round(2, 200), _round(1, 50)]
    assert variants_progress(vs, rounds) == {1: 150, 2: 200}


# ── победы в раундах ─────────────────────────────────────────────────────────


def test_cycle_wins_judges_each_full_cycle_by_ctr():
    rounds = [
        _round(1, 1000, 40, 1),  # ctr 4%
        _round(2, 1000, 60, 2),  # ctr 6% — победа цикла 1
        _round(1, 1000, 70, 3),  # ctr 7% — победа цикла 2
        _round(2, 1000, 50, 4),
    ]
    assert cycle_wins(rounds) == {2: 1, 1: 1}


def test_cycle_wins_ignores_low_view_rounds():
    rounds = [_round(1, 50, 40, 1), _round(2, 1000, 10, 2)]  # 50 показов < min_views
    assert cycle_wins(rounds) == {2: 1}


def test_cycle_wins_tail_single_round_not_judged():
    rounds = [_round(1, 1000, 40, 1), _round(2, 1000, 60, 2), _round(1, 1000, 99, 3)]
    # хвост из одного круга (вариант 1) не судится — не с кем сравнивать
    assert cycle_wins(rounds) == {2: 1}


# ── сверка галереи ───────────────────────────────────────────────────────────


def test_media_matches_same_gallery():
    photos = [{"big": "u1"}, {"big": "u2"}]
    assert media_matches_snapshot(photos, {"photos": photos})


def test_media_mismatch_on_count_change():
    assert not media_matches_snapshot([{"big": "u1"}], {"photos": [{"big": "u1"}, {"big": "u2"}]})


def test_media_mismatch_on_url_change():
    assert not media_matches_snapshot(
        [{"big": "u1"}, {"big": "u3"}], {"photos": [{"big": "u1"}, {"big": "u2"}]}
    )


def test_media_matches_when_no_snapshot():
    assert media_matches_snapshot([{"big": "u1"}], None)


def test_next_variant_rotates_off_excluded_even_to_single_active():
    # активный вариант исключили, остался один активный — надо снять исключённое фото
    vs = [_variant(1, 0, excluded=True), _variant(2, 1)]
    assert next_variant(vs, 1).id == 2


# ── Интеграционный прогон полного цикла (реальная БД, WB замокан) ────────────


class _FakeWb:
    """Стенд WB: галерея + загрузки фото (статистика теперь читается из БД)."""

    def __init__(self):
        self.gallery = [{"big": "u1"}, {"big": "u2"}]
        self.uploads: list[bytes] = []
        self.storage: dict[str, bytes] = {}

    async def fetch_card(self, key, nm_id):
        return {"title": "Тестовый товар", "vendor_code": "vc-1", "photos": self.gallery}

    async def upload(self, key, nm_id, content, filename="photo.jpg"):
        self.uploads.append(content)

    async def put_photo(self, key, content, content_type="image/jpeg"):
        self.storage[key] = content

    async def get_photo(self, key):
        return self.storage.get(key)

    async def download_wb(self, url):
        return b"ORIGINAL"


def _jpeg_bytes(w=700, h=900) -> bytes:
    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("RGB", (w, h), (200, 100, 50)).save(buf, format="JPEG")
    return buf.getvalue()


async def test_full_lifecycle_rotation_and_finish(db_session, project, monkeypatch):
    """Черновик → 2 фото → старт → тики: ротация по кругам → финиш с возвратом оригинала."""
    from datetime import timedelta as _td

    from datetime import date as _date

    from backend.models import WbAdNmDaily, WbFunnelDaily

    import backend.integrations.wb_content_api as content_api
    import backend.services.funnel.ab_photo_tests as svc

    wb = _FakeWb()
    monkeypatch.setattr(content_api, "fetch_card", wb.fetch_card)
    monkeypatch.setattr(content_api, "upload_main_photo", wb.upload)
    monkeypatch.setattr(svc, "_put_photo", wb.put_photo)
    monkeypatch.setattr(svc, "get_photo", wb.get_photo)
    monkeypatch.setattr(svc, "_download_wb_photo", wb.download_wb)

    async def fake_keys(db, project_id):
        return "content-key", "adv-key", "analytics-key"

    monkeypatch.setattr(svc, "_get_keys", fake_keys)

    t0 = datetime(2026, 7, 13, 8, 0, 0)
    now = {"v": t0}
    monkeypatch.setattr(svc, "utcnow", lambda: now["v"])

    day = _date(2026, 7, 13)  # МСК-день всех тиков в этом сценарии
    adv_daily = WbAdNmDaily(project_id=project.id, campaign_id=222, nm_id=111, date=day)
    org_daily = WbFunnelDaily(project_id=project.id, nm_id=111, date=day)
    db_session.add_all([adv_daily, org_daily])
    await db_session.commit()

    def set_cum(views=None, clicks=None, org_open=None):
        """Обновить накопительные суточные счётчики — как это делает ежечасный синк."""
        if views is not None:
            adv_daily.views = views
        if clicks is not None:
            adv_daily.clicks = clicks
        if org_open is not None:
            org_daily.open_card = org_open

    # Черновик: контроль скачан с CDN и лёг в MinIO
    test = await svc.create_test(
        db_session, project.id, nm_id=111, campaign_id=222,
        views_per_round=100, round_minutes=30, target_views=1000, max_days=7,
    )
    assert test.status == "draft"
    assert any(k.endswith("control.jpg") for k in wb.storage)

    # «до 10 вариантов»: тест из ДВУХ загруженных фото (контроль + 2)
    v1 = await svc.add_variant(db_session, project.id, test.id, _jpeg_bytes())
    v2 = await svc.add_variant(db_session, project.id, test.id, _jpeg_bytes())
    assert v1.position == 1 and v2.position == 2

    test = await svc.start_test(db_session, project.id, test.id)
    assert test.status == "running"
    control_id = test.active_variant_id

    # Тик 1: показов мало (50 < 100), время не вышло → без ротации
    set_cum(views=50, clicks=3, org_open=40)
    now["v"] = t0 + _td(minutes=10)
    await svc.tick_project(db_session, project.id)
    await db_session.refresh(test)
    assert test.active_variant_id == control_id
    assert wb.uploads == []

    # Тик 2: показы так и не набраны, но время круга вышло → смена ПО ВРЕМЕНИ
    now["v"] = t0 + _td(minutes=35)
    await svc.tick_project(db_session, project.id)
    await db_session.refresh(test)
    assert test.active_variant_id == v1.id
    assert len(wb.uploads) == 1

    # Тик 3: большой трафик — 100+ показов за 10 минут → ДОСРОЧНАЯ смена по показам
    set_cum(views=(adv_daily.views or 0) + 150)
    now["v"] = now["v"] + _td(minutes=10)
    await svc.tick_project(db_session, project.id)
    await db_session.refresh(test)
    assert test.active_variant_id == v2.id
    assert len(wb.uploads) == 2

    # Гоняем тики, пока тест не закончится (каждый круг: +400 показов, +35 мин)
    for i in range(20):
        if test.status != "running":
            break
        set_cum(
            views=(adv_daily.views or 0) + 400,
            clicks=(adv_daily.clicks or 0) + 20 + i,  # разный CTR по кругам
            org_open=(org_daily.open_card or 0) + 30,
        )
        now["v"] = now["v"] + _td(minutes=35)
        await svc.tick_project(db_session, project.id)
        await db_session.refresh(test)

    assert test.status == "finished"
    assert test.winner_variant_id is not None
    # последний аплоад — возврат исходного фото (байты контроля)
    assert wb.uploads[-1] == b"ORIGINAL"

    results = await svc.get_test_results(db_session, project.id, test.id)
    per_variant = {v["id"]: v for v in results["variants"]}
    # каждый невыключенный вариант набрал цель
    assert all(pv["views"] >= 1000 for pv in per_variant.values())
    # органика разложилась по кругам
    assert sum(pv["organic_open"] for pv in per_variant.values()) == (org_daily.open_card or 0)
    # все круги закрыты
    assert all(r["ended_at"] for r in results["rounds"])


async def test_finish_restore_failure_pauses_instead_of_finishing(db_session, project, monkeypatch):
    """Провал возврата исходного фото при финише НЕ финиширует тест: карточка осталась
    на вариантном фото, поэтому тест уходит в paused с причиной, а повторный «Стоп»
    после оживления WB добивает финиш и возвращает оригинал."""
    from datetime import timedelta as _td

    from datetime import date as _date

    from backend.models import WbAdNmDaily, WbFunnelDaily

    import backend.integrations.wb_content_api as content_api
    import backend.services.funnel.ab_photo_tests as svc
    from backend.integrations.wb_content_api import WbContentError

    wb = _FakeWb()
    fail = {"on": False}

    async def flaky_upload(key, nm_id, content, filename="photo.jpg"):
        if fail["on"]:
            raise WbContentError("WB media/file: 500")
        wb.uploads.append(content)

    monkeypatch.setattr(content_api, "fetch_card", wb.fetch_card)
    monkeypatch.setattr(content_api, "upload_main_photo", flaky_upload)
    monkeypatch.setattr(svc, "_put_photo", wb.put_photo)
    monkeypatch.setattr(svc, "get_photo", wb.get_photo)
    monkeypatch.setattr(svc, "_download_wb_photo", wb.download_wb)

    async def fake_keys(db, project_id):
        return "content-key", "adv-key", "analytics-key"

    monkeypatch.setattr(svc, "_get_keys", fake_keys)

    t0 = datetime(2026, 7, 13, 8, 0, 0)
    now = {"v": t0}
    monkeypatch.setattr(svc, "utcnow", lambda: now["v"])

    day = _date(2026, 7, 13)  # МСК-день всех тиков в этом сценарии
    adv_daily = WbAdNmDaily(project_id=project.id, campaign_id=222, nm_id=111, date=day)
    org_daily = WbFunnelDaily(project_id=project.id, nm_id=111, date=day)
    db_session.add_all([adv_daily, org_daily])
    await db_session.commit()

    def set_cum(views=None, clicks=None, org_open=None):
        """Обновить накопительные суточные счётчики — как это делает ежечасный синк."""
        if views is not None:
            adv_daily.views = views
        if clicks is not None:
            adv_daily.clicks = clicks
        if org_open is not None:
            org_daily.open_card = org_open

    test = await svc.create_test(
        db_session, project.id, nm_id=111, campaign_id=222,
        views_per_round=100, round_minutes=30, target_views=1000, max_days=7,
    )
    v1 = await svc.add_variant(db_session, project.id, test.id, _jpeg_bytes())
    test = await svc.start_test(db_session, project.id, test.id)

    # ротация по времени: активным становится вариант v1 (не контроль)
    now["v"] = t0 + _td(minutes=35)
    await svc.tick_project(db_session, project.id)
    await db_session.refresh(test)
    assert test.active_variant_id == v1.id

    # стоп при лежащем WB: возврат оригинала не прошёл → paused, не finished
    fail["on"] = True
    test = await svc.stop_test(db_session, project.id, test.id)
    assert test.status == "paused"
    assert "исходное фото" in (test.pause_reason or "").lower()
    assert test.active_variant_id == v1.id  # честно: на карточке всё ещё вариант
    assert test.finished_at is None

    # WB ожил: повторный «Стоп» добивает финиш и возвращает оригинал
    fail["on"] = False
    test = await svc.stop_test(db_session, project.id, test.id)
    assert test.status == "finished"
    assert test.pause_reason is None
    assert test.active_variant_id != v1.id
    assert wb.uploads[-1] == b"ORIGINAL"


async def test_running_test_campaigns_prioritized_in_sync_order(db_session, project, monkeypatch):
    """Кампании активных АБ-тестов поднимаются в голову очереди fetch_ad_stats:
    на больших кабинетах TIME_BUDGET скипает хвост чанков — тестовая кампания
    обязана попадать в первый чанк, иначе тест слепнет (прод-инцидент 16.07)."""
    import backend.integrations.wb_content_api as content_api
    import backend.services.funnel.ab_photo_tests as svc

    wb = _FakeWb()
    monkeypatch.setattr(content_api, "fetch_card", wb.fetch_card)
    monkeypatch.setattr(svc, "_put_photo", wb.put_photo)
    monkeypatch.setattr(svc, "_download_wb_photo", wb.download_wb)

    async def fake_keys(db, project_id):
        return "content-key", "adv-key", "analytics-key"

    monkeypatch.setattr(svc, "_get_keys", fake_keys)

    test = await svc.create_test(db_session, project.id, nm_id=111, campaign_id=555)
    await svc.add_variant(db_session, project.id, test.id, _jpeg_bytes())
    await svc.start_test(db_session, project.id, test.id)

    # воспроизводим переупорядочивание из sync.py
    from sqlalchemy import select as _select

    from backend.models import AbPhotoTest

    campaign_ids = [111111, 222222, 555, 333333]
    ab_campaigns = set(
        (
            await db_session.execute(
                _select(AbPhotoTest.campaign_id).where(
                    AbPhotoTest.project_id == project.id,
                    AbPhotoTest.status == "running",
                    AbPhotoTest.is_deleted == False,  # noqa: E712
                ).limit(100)
            )
        ).scalars().all()
    )
    reordered = [c for c in campaign_ids if c in ab_campaigns] + [c for c in campaign_ids if c not in ab_campaigns]
    assert reordered[0] == 555
    assert sorted(reordered) == sorted(campaign_ids)
