"""АБ-тесты главного фото карточки WB — сервис.

Механика (согласована 13.07.2026, разбор Marpla — память dds-ab-tests-photos):
- Тест = артикул × рекламная кампания × (контроль + до 10 вариантов фото).
- Ротация «кругами»: круг закрывается по истечении round_minutes (смена фото
  гарантирована по времени — на слабом трафике круг не растягивается), либо ДОСРОЧНО,
  когда активное фото набрало views_per_round показов. Порядок — round-robin по position.
- Метрики круга — дельты накопительных СУТОЧНЫХ строк из НАШЕЙ БД: реклама
  wb_ad_nm_daily (показы/клики/корзины/заказы/расход по nmId) + органика
  wb_funnel_daily (переходы/корзины/заказы всех источников). Обе таблицы обновляет
  ежечасный funnel-синк. Тик НЕ ходит в WB за статистикой вообще: точечные
  fullstats-запросы не пробиваются через per-seller лимитер, который выедают
  прод-синки (16.07 проверено вживую — 10 попыток подряд 429). Смена фото идёт
  через Content API — у него свой лимит, с синками не конкурирует.
- Финиш: каждый невыключенный вариант набрал target_views (или прошло max_days) →
  возвращаем исходное фото, победитель — по CTR (клики/показы), НЕ применяется
  автоматически (кнопка «Применить победителя»).
- Защита от гонок: перед каждой сменой фото сверяем галерею карточки со снимком
  original_media (число фото и набор URL). Разошлось → пауза с pause_reason.
"""

import asyncio
import io
import logging
from typing import Any
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
import pytz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import AbPhotoRound, AbPhotoTest, AbPhotoVariant, WbAdCampaign, WbAdNmDaily, WbFunnelDaily
from backend.storage import get_minio
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

MSK = pytz.timezone("Europe/Moscow")

MAX_VARIANTS = 10  # загружаемых, не считая контроля
_APPLY_RETRY_LIMIT = 3  # подряд неудачных попыток смены фото → пауза теста
ACTIVE_STATUSES = ("draft", "running", "paused")

_ADV_KEYS = ("views", "clicks", "atbs", "orders", "spend", "orders_sum")
_ORG_KEYS = ("open", "cart", "orders")


# ── Чистые помощники (юнит-тестируются без БД) ──────────────────────────────


def compute_tick_delta(last_stat: dict | None, today: str, adv: dict, org: dict) -> tuple[dict, dict]:
    """Дельта накопительных счётчиков с прошлого тика + новый last_stat.

    Счётчики WB обнуляются в полночь МСК: если stat_date в last_stat не сегодняшний,
    базой считаем нули. Отрицательные дельты (WB корректирует счётчик вниз задним
    числом) зажимаем в 0 — зеркало get_intraday_metrics.
    """
    prev_adv: dict = {}
    prev_org: dict = {}
    if last_stat and last_stat.get("stat_date") == today:
        prev_adv = last_stat.get("adv") or {}
        prev_org = last_stat.get("organic") or {}
    delta = {
        **{k: max(0, int(adv.get(k) or 0) - int(prev_adv.get(k) or 0)) for k in ("views", "clicks", "atbs", "orders")},
        **{
            k: round(max(0.0, float(adv.get(k) or 0) - float(prev_adv.get(k) or 0)), 2)
            for k in ("spend", "orders_sum")
        },
        **{
            f"organic_{k}": max(0, int(org.get(k) or 0) - int(prev_org.get(k) or 0))
            for k in _ORG_KEYS
        },
    }
    new_last = {
        "stat_date": today,
        "adv": {k: adv.get(k) or 0 for k in _ADV_KEYS},
        "organic": {k: org.get(k) or 0 for k in _ORG_KEYS},
    }
    return delta, new_last


def next_variant(variants: list, current_id: int | None) -> Any:
    """Следующий вариант round-robin по position среди невыключенных.

    Если текущий вариант исключён (его нет среди активных) — возвращаем первый активный,
    даже единственный: исключённое фото надо снять с карточки. None — крутить некого:
    активных нет, либо текущий и так единственный активный.
    """
    active = sorted((v for v in variants if v.excluded_at is None), key=lambda v: v.position)
    if not active:
        return None
    ids = [v.id for v in active]
    if current_id in ids:
        if len(active) < 2:
            return None
        return active[(ids.index(current_id) + 1) % len(active)]
    return active[0]


def should_rotate(round_views: int, round_started_at: datetime, test: Any, now: datetime) -> bool:
    """Круг закрыт: вышло время round_minutes (смена гарантирована — на слабом трафике
    круг не висит вечно) ИЛИ досрочно набраны views_per_round показов."""
    return (
        now - round_started_at >= timedelta(minutes=test.round_minutes)
        or round_views >= test.views_per_round
    )


def variants_progress(variants: list, rounds: list) -> dict[int, int]:
    """Суммарные рекламные показы по вариантам (по всем кругам, включая открытый)."""
    totals: dict[int, int] = {v.id: 0 for v in variants}
    for r in rounds:
        if r.variant_id in totals:
            totals[r.variant_id] += r.views
    return totals


def is_finished(test: Any, variants: list, totals: dict[int, int], now: datetime) -> bool:
    """Тест завершён: у КАЖДОГО невыключенного варианта target_views показов,
    либо сработал предохранитель max_days."""
    if test.started_at and now - test.started_at >= timedelta(days=test.max_days):
        return True
    active = [v for v in variants if v.excluded_at is None]
    if not active:
        return True
    return all(totals.get(v.id, 0) >= test.target_views for v in active)


def cycle_wins(closed_rounds: list, min_views: int = 100) -> dict[int, int]:
    """«Побед в раундах по CTR»: закрытые круги группируются в циклы (цикл — до повтора
    варианта), в каждом цикле побеждает лучший CTR среди кругов с >= min_views показов.

    closed_rounds — в порядке round_no; элементы с .variant_id/.views/.clicks.
    """
    wins: dict[int, int] = {}
    batch: list = []
    seen: set[int] = set()

    def judge(rounds_batch: list) -> None:
        best_id, best_ctr = None, -1.0
        for r in rounds_batch:
            if r.views < min_views:
                continue
            ctr = r.clicks / r.views
            if ctr > best_ctr:
                best_id, best_ctr = r.variant_id, ctr
        if best_id is not None:
            wins[best_id] = wins.get(best_id, 0) + 1

    for r in closed_rounds:
        if r.variant_id in seen:
            judge(batch)
            batch, seen = [], set()
        batch.append(r)
        seen.add(r.variant_id)
    if len(batch) > 1:  # хвостовой неполный цикл судим, только если есть с кем сравнивать
        judge(batch)
    return wins


def media_matches_snapshot(current_photos: list, original_media: dict | None) -> bool:
    """Галерея не менялась извне: то же число фото и тот же набор URL «big».

    Замена байтов главного фото через media/file сохраняет URL (стабильные пути
    /images/big/N.webp), поэтому сама ротация снимок не ломает.
    """
    if not original_media:
        return True
    orig = original_media.get("photos") or []
    if len(current_photos) != len(orig):
        return False
    cur_urls = {(p.get("big") or "") for p in current_photos}
    orig_urls = {(p.get("big") or "") for p in orig}
    return cur_urls == orig_urls


# ── MinIO ────────────────────────────────────────────────────────────────────


def _photo_key(project_id: int, test_id: int, name: str) -> str:
    return f"ab-tests/{project_id}/{test_id}/{name}"


async def _put_photo(key: str, content: bytes, content_type: str = "image/jpeg") -> None:
    client = await get_minio()
    if client is None:
        raise ValueError("Хранилище фото (MinIO) недоступно")
    await client.put_object(
        settings.MINIO_BUCKET, key, io.BytesIO(content), length=len(content), content_type=content_type
    )


async def get_photo(key: str) -> bytes | None:
    """Байты фото варианта из MinIO (для отдачи в UI и загрузки на WB)."""
    import aiohttp

    client = await get_minio()
    if client is None:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            resp = await client.get_object(settings.MINIO_BUCKET, key, session)
            data: bytes = await resp.read()
            resp.close()
        return data
    except Exception:
        return None


async def get_variant_photo(db: AsyncSession, project_id: int, test_id: int, variant_id: int) -> bytes | None:
    """Байты фото варианта (для отдачи в UI). None — вариант/файл не найден."""
    variants = await _get_variants(db, project_id, test_id)
    variant = next((v for v in variants if v.id == variant_id), None)
    if variant is None:
        return None
    return await get_photo(variant.image_key)


async def _download_wb_photo(url: str) -> bytes | None:
    """Скачать текущее фото карточки с CDN WB (для контроля/возврата исходного)."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return r.content
    except httpx.HTTPError:
        pass
    return None


# ── Ключи ────────────────────────────────────────────────────────────────────


async def _get_keys(db: AsyncSession, project_id: int) -> tuple[str, str, str]:
    """(content_key, adv_key, analytics_key). ValueError с понятным текстом, если ключей нет.

    Реклама (fullstats) и воронка (органика) — разные категории токена и у проекта
    могут быть разными ключами (зеркало приоритетов sync.py): adv = «Продвижение»,
    analytics = «Аналитика»/основной.
    """
    from backend.services.funnel.wb_api_client import get_wb_key

    content_key = await get_wb_key(db, project_id, "wb_content")
    if not content_key:
        raise ValueError(
            "Нет ключа с категорией «Контент» (запись). Добавьте его в Настройки → Интеграции — "
            "без него сервис не может менять фото карточки."
        )
    adv_key = await get_wb_key(db, project_id, "wb_advert") or await get_wb_key(db, project_id, "wb")
    analytics_key = (
        await get_wb_key(db, project_id, "wb_analytics")
        or await get_wb_key(db, project_id, "wb")
        or adv_key
    )
    if not adv_key or not analytics_key:
        raise ValueError("Нет WB-ключа с категориями «Продвижение»/«Аналитика» для сбора статистики.")
    return content_key, adv_key, analytics_key


# ── CRUD ─────────────────────────────────────────────────────────────────────


async def _get_test(db: AsyncSession, project_id: int, test_id: int) -> AbPhotoTest:
    test = (
        await db.execute(
            select(AbPhotoTest).where(
                AbPhotoTest.project_id == project_id,
                AbPhotoTest.id == test_id,
                AbPhotoTest.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if test is None:
        raise ValueError("Тест не найден")
    return test


async def _get_variants(db: AsyncSession, project_id: int, test_id: int) -> list[AbPhotoVariant]:
    return list(
        (
            await db.execute(
                select(AbPhotoVariant)
                .where(AbPhotoVariant.project_id == project_id, AbPhotoVariant.test_id == test_id)
                .order_by(AbPhotoVariant.position)
                .limit(MAX_VARIANTS + 1)
            )
        )
        .scalars()
        .all()
    )


async def _get_rounds(db: AsyncSession, project_id: int, test_id: int) -> list[AbPhotoRound]:
    return list(
        (
            await db.execute(
                select(AbPhotoRound)
                .where(AbPhotoRound.project_id == project_id, AbPhotoRound.test_id == test_id)
                .order_by(AbPhotoRound.round_no)
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )


async def create_test(
    db: AsyncSession,
    project_id: int,
    *,
    nm_id: int,
    campaign_id: int,
    name: str = "",
    comment: str | None = None,
    views_per_round: int = 5000,
    round_minutes: int = 45,
    target_views: int = 10000,
    max_days: int = 7,
) -> AbPhotoTest:
    """Создать черновик теста: снимок галереи карточки + контрольный вариант (позиция 0)."""
    from backend.integrations.wb_content_api import fetch_card

    existing = (
        await db.execute(
            select(AbPhotoTest).where(
                AbPhotoTest.project_id == project_id,
                AbPhotoTest.nm_id == nm_id,
                AbPhotoTest.status.in_(ACTIVE_STATUSES),
                AbPhotoTest.is_deleted == False,  # noqa: E712
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"По артикулу {nm_id} уже есть незавершённый тест (№{existing.id}).")

    content_key, _, _ = await _get_keys(db, project_id)
    card = await fetch_card(content_key, nm_id)
    if card is None:
        raise ValueError(f"Карточка {nm_id} не найдена в кабинете (проверьте ключ «Контент»).")
    photos = card["photos"]
    if not photos:
        raise ValueError(f"У карточки {nm_id} нет фото.")
    main_url = photos[0].get("big") or ""
    original_bytes = await _download_wb_photo(main_url)
    if not original_bytes:
        raise ValueError("Не удалось скачать текущее главное фото с CDN WB — попробуйте ещё раз.")

    round_minutes = max(15, round_minutes)  # ниже 15 минут круги грязные (CDN + лаг статистики + тик 5 мин)
    test = AbPhotoTest(
        project_id=project_id,
        nm_id=nm_id,
        campaign_id=campaign_id,
        name=name or f"{nm_id}",
        comment=comment,
        views_per_round=max(100, views_per_round),
        round_minutes=round_minutes,
        target_views=max(1000, target_views),
        max_days=max(1, max_days),
        original_photo_url=main_url,
        original_media={"photos": photos, "title": card["title"], "vendor_code": card["vendor_code"]},
    )
    db.add(test)
    await db.flush()

    control_key = _photo_key(project_id, test.id, "control.jpg")
    await _put_photo(control_key, original_bytes)
    db.add(
        AbPhotoVariant(
            project_id=project_id, test_id=test.id, position=0, is_control=True, image_key=control_key
        )
    )
    await db.commit()
    await db.refresh(test)
    return test


async def add_variant(db: AsyncSession, project_id: int, test_id: int, content: bytes) -> AbPhotoVariant:
    """Загрузить вариант фото (только в черновике). Валидация: JPEG/PNG/WebP, ≥700×900, ≤10 МБ."""
    test = await _get_test(db, project_id, test_id)
    if test.status != "draft":
        raise ValueError("Фото можно добавлять только в черновик (до запуска теста).")
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("Фото больше 10 МБ — WB такое не примет.")
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(content))
        img.verify()
        img = Image.open(io.BytesIO(content))
        if img.format not in ("JPEG", "PNG", "WEBP"):
            raise ValueError(f"Формат {img.format} не поддерживается WB — нужен JPEG/PNG/WebP.")
        if img.width < 700 or img.height < 900:
            raise ValueError(f"Фото {img.width}×{img.height} — WB требует минимум 700×900.")
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001 — битый файл
        raise ValueError("Файл не похож на изображение.") from e

    variants = await _get_variants(db, project_id, test_id)
    if len([v for v in variants if not v.is_control]) >= MAX_VARIANTS:
        raise ValueError(f"Максимум {MAX_VARIANTS} вариантов, не считая текущего фото.")
    position = max((v.position for v in variants), default=0) + 1
    key = _photo_key(project_id, test_id, f"v{position}.jpg")
    await _put_photo(key, content)
    variant = AbPhotoVariant(project_id=project_id, test_id=test_id, position=position, image_key=key)
    db.add(variant)
    await db.commit()
    await db.refresh(variant)
    return variant


async def delete_variant(db: AsyncSession, project_id: int, test_id: int, variant_id: int) -> None:
    """Удалить вариант из черновика (контроль удалить нельзя)."""
    test = await _get_test(db, project_id, test_id)
    if test.status != "draft":
        raise ValueError("Удалять фото можно только из черновика.")
    variants = await _get_variants(db, project_id, test_id)
    variant = next((v for v in variants if v.id == variant_id), None)
    if variant is None:
        raise ValueError("Вариант не найден")
    if variant.is_control:
        raise ValueError("Контрольное фото удалить нельзя.")
    await db.delete(variant)  # no-soft-delete-check: AbPhotoVariant без SoftDeleteMixin, черновик без кругов
    await db.commit()


async def start_test(db: AsyncSession, project_id: int, test_id: int) -> AbPhotoTest:
    """Запуск: первый круг — контроль (фото уже стоит на карточке, ничего не меняем)."""
    test = await _get_test(db, project_id, test_id)
    if test.status != "draft":
        raise ValueError("Запустить можно только черновик.")
    variants = await _get_variants(db, project_id, test_id)
    if len(variants) < 2:
        raise ValueError("Нужен хотя бы один вариант фото, кроме текущего.")
    await _get_keys(db, project_id)  # ключи на месте — иначе понятная ошибка до старта

    control = next(v for v in variants if v.is_control)
    now = utcnow()
    test.status = "running"
    test.started_at = now
    test.active_variant_id = control.id
    test.last_stat = None
    db.add(
        AbPhotoRound(
            project_id=project_id, test_id=test.id, variant_id=control.id, round_no=1, started_at=now
        )
    )
    await db.commit()
    await db.refresh(test)
    return test


async def set_variant_excluded(
    db: AsyncSession, project_id: int, test_id: int, variant_id: int, excluded: bool
) -> None:
    """Минусик (исключить из ротации) / плюсик (вернуть). Данные кругов остаются.

    Если исключили активный вариант — ротация произойдёт на ближайшем тике
    (next_variant его уже не выберет, а should_rotate дожмёт круг по времени/показам).
    """
    test = await _get_test(db, project_id, test_id)
    variants = await _get_variants(db, project_id, test_id)
    variant = next((v for v in variants if v.id == variant_id), None)
    if variant is None:
        raise ValueError("Вариант не найден")
    if excluded and variant.is_control and test.status == "draft":
        raise ValueError("Контроль нельзя исключить до старта.")
    variant.excluded_at = utcnow() if excluded else None
    await db.commit()


async def stop_test(db: AsyncSession, project_id: int, test_id: int) -> AbPhotoTest:
    """Досрочное завершение вручную: вернуть исходное фото, посчитать победителя."""
    test = await _get_test(db, project_id, test_id)
    if test.status not in ("running", "paused"):
        raise ValueError("Останавливать можно только идущий тест.")
    content_key, _, _ = await _get_keys(db, project_id)
    await _finish_test(db, test, content_key)
    await db.commit()
    await db.refresh(test)
    return test


async def resume_test(db: AsyncSession, project_id: int, test_id: int) -> AbPhotoTest:
    """Снять с паузы: пересникать галерею (принимаем текущее состояние как новую базу)."""
    from backend.integrations.wb_content_api import fetch_card

    test = await _get_test(db, project_id, test_id)
    if test.status != "paused":
        raise ValueError("Тест не на паузе.")
    content_key, _, _ = await _get_keys(db, project_id)
    card = await fetch_card(content_key, test.nm_id)
    if card and card["photos"]:
        media = dict(test.original_media or {})
        media["photos"] = card["photos"]
        test.original_media = media
    test.status = "running"
    test.pause_reason = None
    await db.commit()
    await db.refresh(test)
    return test


async def apply_winner(db: AsyncSession, project_id: int, test_id: int) -> AbPhotoTest:
    """Поставить фото-победителя на карточку (по кнопке, не автоматически)."""
    from backend.integrations.wb_content_api import upload_main_photo

    test = await _get_test(db, project_id, test_id)
    if test.status != "finished" or not test.winner_variant_id:
        raise ValueError("Победитель ещё не определён.")
    variants = await _get_variants(db, project_id, test_id)
    winner = next((v for v in variants if v.id == test.winner_variant_id), None)
    if winner is None:
        raise ValueError("Вариант-победитель не найден.")
    content = await get_photo(winner.image_key)
    if content is None:
        raise ValueError("Фото победителя не найдено в хранилище.")
    content_key, _, _ = await _get_keys(db, project_id)
    await upload_main_photo(content_key, test.nm_id, content)
    test.winner_applied_at = utcnow()
    await db.commit()
    await db.refresh(test)
    return test


# ── Тик (scheduler) ──────────────────────────────────────────────────────────


async def tick_project(db: AsyncSession, project_id: int) -> dict:
    """Один проход по идущим тестам проекта: дельты → ротация → финиш."""
    tests = (
        (
            await db.execute(
                select(AbPhotoTest).where(
                    AbPhotoTest.project_id == project_id,
                    AbPhotoTest.status == "running",
                    AbPhotoTest.is_deleted == False,  # noqa: E712
                ).limit(100)
            )
        )
        .scalars()
        .all()
    )
    if not tests:
        return {"ticked": 0}
    try:
        content_key, _, _ = await _get_keys(db, project_id)
    except ValueError as e:
        logger.warning(f"AB tick: project {project_id} без ключей: {e}")
        return {"ticked": 0, "error": "no_keys"}

    ticked = 0
    for test in tests:
        try:
            await _tick_test(db, test, content_key)
            ticked += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — один тест не роняет остальные
            logger.error(f"AB tick failed: test {test.id} project {project_id}: {e}")
    return {"ticked": ticked}


async def _read_today_stats(
    db: AsyncSession, project_id: int, campaign_id: int, nm_id: int, today: str
) -> tuple[dict | None, dict | None]:
    """Накопительные счётчики «за сегодня» из НАШИХ таблиц (наполняет ежечасный синк).

    None — строки за сегодня ещё нет (синк не добежал): вызывающий не двигает базу
    дельт, ничего не теряется. Числа суточные накопительные — ровно то, что нужно
    compute_tick_delta.
    """
    day = date.fromisoformat(today)
    adv_row = (
        await db.execute(
            select(WbAdNmDaily).where(
                WbAdNmDaily.project_id == project_id,
                WbAdNmDaily.campaign_id == campaign_id,
                WbAdNmDaily.nm_id == nm_id,
                WbAdNmDaily.date == day,
            ).limit(1)
        )
    ).scalar_one_or_none()
    adv = None
    if adv_row is not None:
        adv = {
            "views": adv_row.views or 0,
            "clicks": adv_row.clicks or 0,
            "atbs": adv_row.atbs or 0,
            "orders": adv_row.orders or 0,
            "spend": float(adv_row.spend or 0),
            "orders_sum": float(adv_row.orders_sum or 0),
        }
    org_row = (
        await db.execute(
            select(WbFunnelDaily).where(
                WbFunnelDaily.project_id == project_id,
                WbFunnelDaily.nm_id == nm_id,
                WbFunnelDaily.date == day,
            ).limit(1)
        )
    ).scalar_one_or_none()
    org = None
    if org_row is not None:
        org = {
            "open": org_row.open_card or 0,
            "cart": org_row.add_to_cart or 0,
            "orders": org_row.orders_count or 0,
            "orders_sum": float(org_row.orders_sum_rub or 0),
        }
    return adv, org


async def _tick_test(db: AsyncSession, test: AbPhotoTest, content_key: str) -> None:
    now = utcnow()
    today = pytz.UTC.localize(now).astimezone(MSK).strftime("%Y-%m-%d")

    adv, org = await _read_today_stats(db, test.project_id, test.campaign_id, test.nm_id, today)
    if adv is None and org is None:
        return  # WB недоступен — тик пропускаем, дельты не теряются (накопительные счётчики)
    adv = adv or {}
    org = org or {}

    delta, new_last = compute_tick_delta(test.last_stat, today, adv, org)
    # adv недоступен, а org есть (или наоборот) → не двигаем базу недоступной части,
    # чтобы не потерять дельту на следующем тике
    if not adv and test.last_stat:
        new_last["adv"] = (test.last_stat or {}).get("adv") or new_last["adv"]
        for k in ("views", "clicks", "atbs", "orders", "spend", "orders_sum"):
            delta[k] = 0
    if not org and test.last_stat:
        new_last["organic"] = (test.last_stat or {}).get("organic") or new_last["organic"]
        for k in _ORG_KEYS:
            delta[f"organic_{k}"] = 0

    rounds = await _get_rounds(db, test.project_id, test.id)
    current = next((r for r in rounds if r.ended_at is None), None)
    if current is None:  # не должно случаться, но самолечимся
        variants = await _get_variants(db, test.project_id, test.id)
        current = AbPhotoRound(
            project_id=test.project_id,
            test_id=test.id,
            variant_id=test.active_variant_id or variants[0].id,
            round_no=(rounds[-1].round_no + 1) if rounds else 1,
            started_at=now,
        )
        db.add(current)
        rounds.append(current)

    current.views += delta["views"]
    current.clicks += delta["clicks"]
    current.atbs += delta["atbs"]
    current.orders += delta["orders"]
    current.spend = Decimal(str(current.spend or 0)) + Decimal(str(delta["spend"]))
    current.orders_sum = Decimal(str(current.orders_sum or 0)) + Decimal(str(delta["orders_sum"]))
    current.organic_open += delta["organic_open"]
    current.organic_cart += delta["organic_cart"]
    current.organic_orders += delta["organic_orders"]
    test.last_stat = new_last

    # Пометка «кампания стояла» — по статусу из нашей БД (синк каждые 30 мин)
    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == test.project_id,
                WbAdCampaign.campaign_id == test.campaign_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if camp is not None and camp.status != 9:
        flags = dict(current.flags or {})
        flags["campaign_paused"] = True
        current.flags = flags

    variants = await _get_variants(db, test.project_id, test.id)
    totals = variants_progress(variants, rounds)

    if is_finished(test, variants, totals, now):
        await _finish_test(db, test, content_key, current_round=current)
        await db.commit()
        return

    active_variant = next((v for v in variants if v.id == test.active_variant_id), None)
    active_is_excluded = active_variant is not None and active_variant.excluded_at is not None
    if should_rotate(current.views, current.started_at, test, now) or active_is_excluded:
        await _rotate(db, test, variants, rounds, current, content_key, now)

    await db.commit()


async def _rotate(
    db: AsyncSession,
    test: AbPhotoTest,
    variants: list,
    rounds: list,
    current: AbPhotoRound,
    content_key: str,
    now: datetime,
) -> None:
    from backend.integrations.wb_content_api import WbContentError, fetch_card, upload_main_photo

    nxt = next_variant(variants, test.active_variant_id)
    if nxt is None:  # остался один вариант — тест продолжает мерить его до финиша
        return
    if nxt.id == test.active_variant_id:
        return

    # Защита от внешних правок: галерея должна соответствовать снимку
    card = await fetch_card(content_key, test.nm_id)
    if card is not None and not media_matches_snapshot(card["photos"], test.original_media):
        test.status = "paused"
        test.pause_reason = (
            "Галерея карточки изменилась вне теста (число или состав фото). "
            "Проверьте карточку и продолжите тест."
        )
        logger.warning(f"AB test {test.id}: карточка {test.nm_id} изменена извне — пауза")
        return

    content = await get_photo(nxt.image_key)
    if content is None:
        test.status = "paused"
        test.pause_reason = "Фото варианта не найдено в хранилище."
        return
    try:
        await upload_main_photo(content_key, test.nm_id, content)
    except WbContentError as e:
        flags = dict(current.flags or {})
        flags["apply_errors"] = int(flags.get("apply_errors") or 0) + 1
        current.flags = flags
        if flags["apply_errors"] >= _APPLY_RETRY_LIMIT:
            test.status = "paused"
            test.pause_reason = f"WB не принимает смену фото: {e}"
        logger.warning(f"AB test {test.id}: смена фото не прошла ({e}), попытка {flags['apply_errors']}")
        return

    current.ended_at = now
    test.active_variant_id = nxt.id
    db.add(
        AbPhotoRound(
            project_id=test.project_id,
            test_id=test.id,
            variant_id=nxt.id,
            round_no=current.round_no + 1,
            started_at=now,
        )
    )


async def _finish_test(
    db: AsyncSession, test: AbPhotoTest, content_key: str, current_round: AbPhotoRound | None = None
) -> None:
    """Закрыть тест: вернуть исходное фото, зафиксировать победителя по CTR."""
    from backend.integrations.wb_content_api import WbContentError, upload_main_photo

    now = utcnow()
    rounds = await _get_rounds(db, test.project_id, test.id)
    for r in rounds:
        if r.ended_at is None:
            r.ended_at = now

    variants = await _get_variants(db, test.project_id, test.id)
    best_id, best_ctr = None, -1.0
    for v in variants:
        if v.excluded_at is not None:
            continue
        views = sum(r.views for r in rounds if r.variant_id == v.id)
        clicks = sum(r.clicks for r in rounds if r.variant_id == v.id)
        if views >= 100 and clicks / views > best_ctr:
            best_id, best_ctr = v.id, clicks / views
    test.winner_variant_id = best_id

    # Возврат исходного фото (если сейчас стоит не контроль)
    restore_note: str | None = None
    control = next((v for v in variants if v.is_control), None)
    if control is not None and test.active_variant_id != control.id:
        content = await get_photo(control.image_key)
        if content is None:
            logger.error(f"AB test {test.id}: исходное фото отсутствует в хранилище, вернуть нечем")
            restore_note = "Исходное фото не найдено в хранилище — фото на карточке не восстановлено."
        else:
            try:
                await upload_main_photo(content_key, test.nm_id, content)
            except WbContentError as e:
                # Карточка осталась на вариантном фото — финишировать нельзя, иначе
                # тест «завершён», а живое фото залипло без пути ретрая. Пауза даёт
                # ретрай: повторный «Стоп» или resume → тик добьёт финиш.
                logger.error(f"AB test {test.id}: не удалось вернуть исходное фото: {e}")
                test.status = "paused"
                test.pause_reason = (
                    f"Не удалось вернуть исходное фото: {e}. "
                    "Тест не завершён — остановите ещё раз или снимите с паузы."
                )
                return

    test.pause_reason = restore_note
    test.status = "finished"
    test.finished_at = now
    test.active_variant_id = control.id if control else None


def _iso(dt: datetime | None) -> str | None:
    """Naive-UTC datetime → ISO с +00:00: фронтовый new Date() покажет локальное время
    (без суффикса таймзоны браузер трактует строку как локальную — время уезжало на -3ч)."""
    if dt is None:
        return None
    return pytz.UTC.localize(dt).isoformat()


# ── Выдача результатов ───────────────────────────────────────────────────────


async def get_test_results(db: AsyncSession, project_id: int, test_id: int) -> dict:
    """Таблица теста: колонки-варианты с агрегатами + история кругов."""
    test = await _get_test(db, project_id, test_id)
    variants = await _get_variants(db, project_id, test_id)
    rounds = await _get_rounds(db, project_id, test_id)
    closed = [r for r in rounds if r.ended_at is not None]
    wins = cycle_wins(closed)

    per_variant: list[dict[str, Any]] = []
    best_ctr = 0.0
    for v in variants:
        vr = [r for r in rounds if r.variant_id == v.id]
        views = sum(r.views for r in vr)
        clicks = sum(r.clicks for r in vr)
        ctr = clicks / views * 100 if views else 0.0
        if v.excluded_at is None and views >= 100:
            best_ctr = max(best_ctr, ctr)
        per_variant.append(
            {
                "id": v.id,
                "position": v.position,
                "is_control": v.is_control,
                "excluded": v.excluded_at is not None,
                "is_active": v.id == test.active_variant_id and test.status == "running",
                "is_winner": v.id == test.winner_variant_id,
                "rounds": len(vr),
                "views": views,
                "clicks": clicks,
                "ctr": round(ctr, 2),
                "atbs": sum(r.atbs for r in vr),
                "orders": sum(r.orders for r in vr),
                "spend": float(sum(Decimal(str(r.spend or 0)) for r in vr)),
                "orders_sum": float(sum(Decimal(str(r.orders_sum or 0)) for r in vr)),
                "organic_open": sum(r.organic_open for r in vr),
                "organic_cart": sum(r.organic_cart for r in vr),
                "organic_orders": sum(r.organic_orders for r in vr),
                "round_wins": wins.get(v.id, 0),
                "progress_pct": min(100, round(views / test.target_views * 100)) if test.target_views else 0,
                "enough_data": views >= 1000,
            }
        )
    for pv in per_variant:
        pv["ctr_gap"] = round(best_ctr - pv["ctr"], 2) if pv["views"] >= 100 and best_ctr > 0 else None

    return {
        "test": {
            "id": test.id,
            "nm_id": test.nm_id,
            "campaign_id": test.campaign_id,
            "name": test.name,
            "comment": test.comment,
            "status": test.status,
            "pause_reason": test.pause_reason,
            "views_per_round": test.views_per_round,
            "round_minutes": test.round_minutes,
            "target_views": test.target_views,
            "max_days": test.max_days,
            "title": (test.original_media or {}).get("title") or "",
            "vendor_code": (test.original_media or {}).get("vendor_code") or "",
            "started_at": _iso(test.started_at),
            "finished_at": _iso(test.finished_at),
            "winner_variant_id": test.winner_variant_id,
            "winner_applied_at": _iso(test.winner_applied_at),
        },
        "variants": per_variant,
        "rounds": [
            {
                "round_no": r.round_no,
                "variant_id": r.variant_id,
                "started_at": _iso(r.started_at),
                "ended_at": _iso(r.ended_at),
                "views": r.views,
                "clicks": r.clicks,
                "ctr": round(r.clicks / r.views * 100, 2) if r.views else 0.0,
                "atbs": r.atbs,
                "orders": r.orders,
                "organic_open": r.organic_open,
                "organic_cart": r.organic_cart,
                "flags": r.flags or {},
            }
            for r in rounds
        ],
    }


async def list_tests(db: AsyncSession, project_id: int) -> list[dict]:
    """Список тестов проекта для страницы раздела (без кругов)."""
    tests = (
        (
            await db.execute(
                select(AbPhotoTest)
                .where(AbPhotoTest.project_id == project_id, AbPhotoTest.is_deleted == False)  # noqa: E712
                .order_by(AbPhotoTest.created_at.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    if not tests:
        return []
    out = []
    for t in tests:
        variants = await _get_variants(db, project_id, t.id)
        rounds = await _get_rounds(db, project_id, t.id)
        totals = variants_progress(variants, rounds)
        active_cnt = len([v for v in variants if v.excluded_at is None])
        target_total = t.target_views * active_cnt if active_cnt else 0
        got = sum(totals.get(v.id, 0) for v in variants if v.excluded_at is None)
        out.append(
            {
                "id": t.id,
                "nm_id": t.nm_id,
                "campaign_id": t.campaign_id,
                "name": t.name,
                "status": t.status,
                "pause_reason": t.pause_reason,
                "title": (t.original_media or {}).get("title") or "",
                "vendor_code": (t.original_media or {}).get("vendor_code") or "",
                "variants_count": len(variants),
                "progress_pct": min(100, round(got / target_total * 100)) if target_total else 0,
                "created_at": _iso(t.created_at),
                "started_at": _iso(t.started_at),
                "finished_at": _iso(t.finished_at),
            }
        )
    return out
