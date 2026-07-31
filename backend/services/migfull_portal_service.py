# ruff: noqa: RUF002, RUF003
"""
Service: migfull-портал (plusvb.migfull.app) — создание заявки на отгрузку у ФФ «Натали».

Источник — ``AssemblyRequest`` (готовая сборка, склад «Натали»). Креды интеграции —
``IntegrationKey(service="migfull_portal")``: ``encrypted_key`` = Fernet-пароль,
``config={"login", "host"?}``, ``warehouse_id`` = склад (гейт: кнопку показываем
только для сборок с него). НЕ путать с read-only API (service="migfull").

build_draft   — локально (без портала): шапка-prefill + превью описи (короб/россыпь) +
                флаг «уже отправлена».
send_shipment — РЕАЛЬНОЕ создание заявки (шапка + загрузка описи). НЕОБРАТИМО (портал
                не даёт удалить/отменить). Анти-дубль гейт + audit ``MigfullShipmentOrder``
                + связь ``FulfillmentRequest(provider="migfull")`` со сборкой.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.integrations.migfull_client import MigfullApiError, MigfullClient
from backend.integrations.migfull_portal_client import (
    BASE_URL,
    MigfullPortalAuthError,
    MigfullPortalClient,
    MigfullPortalError,
)
from backend.integrations.resilience import CircuitOpenError
from backend.models import (
    FulfillmentRequest,
    FulfillmentStock,
    IntegrationKey,
    MigfullShipmentOrder,
    MigfullShipmentStatus,
    Nomenclature,
    Warehouse,
)
from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.fulfillment import FfRequestKind
from backend.schemas.migfull_portal import (
    DELIVERY_TYPE_LABELS,
    MigfullDeliveryTypeOption,
    MigfullDraftResponse,
    MigfullOpisLine,
    MigfullPortalConfigResponse,
    MigfullSendRequest,
    MigfullSendResult,
    MigfullShipmentPrefill,
)
from backend.services.migfull_opis import OPIS_CONTENT_TYPE, build_opis_xlsx
from backend.utils.crypto import decrypt as _decrypt
from backend.utils.time import utcnow

logger = structlog.get_logger("dds.migfull_portal_service")

MIGFULL_PORTAL_SERVICE = "migfull_portal"  # IntegrationKey портальных кредов
MIGFULL_PROVIDER = "migfull"  # provider в FulfillmentRequest (общий с read-sync)
MIGFULL_READ_SERVICE = "migfull"  # read-only API-ключ (для справочника складов назначения)
_WB_MARKETPLACE_ID = "2"  # Wildberries в migfull (все заявки склада «Натали»)
_WB_MARKETPLACE_ID_INT = 2

# Кэш справочника складов назначения (read-API тяжёлый: /destinations + /shipments)
_DEST_CACHE_TTL_SEC = 3600.0
_dest_cache: dict[int, tuple[float, list[dict]]] = {}
# Потолок ожидания индекса: /shipments у migfull бывает 20с+/таймаут, а
# @retry_with_backoff×3 превращал это в 60с+ «Загрузка заявки…» на КАЖДЫЙ draft.
# Резолв — nice-to-have (нерезолв → «склад заполнит оператор»), дольше не ждём.
_DEST_FETCH_TIMEOUT_SEC = 20.0
# Негативный кэш фейла: без него массовое создание платило бы таймаут за каждую
# строку батча (draft+send каждый зовут резолвер заново).
_DEST_FAIL_TTL_SEC = 300.0
_dest_fail_at: dict[int, float] = {}
# Потолок строк зеркала отгрузок для локального индекса складов назначения
_MIRROR_DEST_LIMIT = 3000

# Кэш cookie-сессий портала: батч отправок логинится ОДИН раз, а не на каждую
# заявку — Filament-логин портала троттлит ~5 попыток/мин (6-я+ заявка батча
# падала «вход не подтверждён»). TTL ниже 120-мин лайфтайма Laravel-сессии;
# протухание ловится и раньше — редирект на логин → перелогин (см. _with_portal_session).
_SESSION_TTL_SEC = 45 * 60.0
_portal_sessions: dict[tuple[int | None, str, str], tuple[float, dict]] = {}


# ─── Резолв склада назначения (наш WB-склад сборки → migfull destination id) ──

# Префикс маркетплейса в имени migfull («ВБ | Казань Зеленодольск», «Озон | …»)
_DEST_PREFIX_RE = re.compile(r"^\s*[^|:]{1,12}[|:]\s*")
# Служебные токены, не различающие склад
_DEST_STOP = {"мо", "спб", "сц", "рфц", "склад", "тк", "фбо", "fbo", "вб", "wb"}


def _dest_tokens(name: str | None) -> list[str]:
    """Значимые токены имени склада (для матчинга наш↔migfull)."""
    s = (name or "").lower().replace("ё", "е")
    s = _DEST_PREFIX_RE.sub("", s)  # срезать «ВБ | »
    s = re.sub(r"\([^)]*\)", " ", s)  # убрать «(Тихорецкая)»
    s = re.sub(r"[^0-9a-zа-я]+", " ", s)
    return [t for t in s.split() if len(t) >= 3 and t not in _DEST_STOP and not t.isdigit()]


def _token_match(a: str, b: str) -> bool:
    """Токен совпал точно или общим 5-символьным стемом (перспективн-ая/-ый)."""
    return a == b or (len(a) >= 5 and len(b) >= 5 and a[:5] == b[:5])


def resolve_destination(
    our_name: str | None, delivery_type: str | None, destinations: list[dict]
) -> dict | None:
    """Наш WB-склад назначения → migfull `{id, name, ...}` или None.

    Скоринг по числу совпавших значимых токенов; фильтр по `delivery_type` (если у
    записи он задан). Требуется УНИКАЛЬНЫЙ top-score — при неоднозначности возвращаем
    None (безопаснее оставить пусто, чем выставить чужой склад).
    """
    ours = _dest_tokens(our_name)
    if not ours:
        return None
    scored: list[tuple[int, dict]] = []
    for d in destinations:
        dt = d.get("delivery_type")
        if delivery_type and dt and dt != delivery_type:
            continue
        theirs = _dest_tokens(d.get("name"))
        score = sum(1 for a in ours if any(_token_match(a, b) for b in theirs))
        if score > 0:
            scored.append((score, d))
    if not scored:
        return None
    top = max(sc for sc, _ in scored)
    top_matches = [d for sc, d in scored if sc == top]
    return top_matches[0] if len(top_matches) == 1 else None


class MigfullPortalServiceError(Exception):
    """Доменная ошибка (роутер мапит в HTTPException)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ─── Креды / клиент ──────────────────────────────────────────────────────────


async def _get_key_or_none(db: AsyncSession, project_id: int) -> IntegrationKey | None:
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == MIGFULL_PORTAL_SERVICE,
            IntegrationKey.is_active.is_(True),
            IntegrationKey.is_deleted.is_(False),
        )
    )
    return result.scalar_one_or_none()


async def _get_key(db: AsyncSession, project_id: int) -> IntegrationKey:
    key = await _get_key_or_none(db, project_id)
    if key is None:
        raise MigfullPortalServiceError(
            "migfull-портал: интеграция не настроена (scripts/setup_migfull_portal_account.py)", status_code=400
        )
    return key


def _client_from_key(key: IntegrationKey) -> MigfullPortalClient:
    cfg = key.config or {}
    login = cfg.get("login") or key.label or ""
    if not login:
        raise MigfullPortalServiceError("migfull-портал: в ключе не задан login (config)", status_code=500)
    return MigfullPortalClient(
        login=login,
        password=_decrypt(key.encrypted_key),
        project_id=key.project_id,
        host=cfg.get("host") or BASE_URL,
    )


def _destinations_from_mirror_rows(rows: list[tuple]) -> list[dict]:
    """(dest_id, name, delivery_type, marketplace_id) из raw зеркала отгрузок →
    [{id, name, ...}]. Дедуп по id (строки идут от свежих к старым — свежее имя
    побеждает), строки без numeric id или имени пропускаются."""
    out: dict[int, dict] = {}
    for did, name, dtype, mkt in rows:
        if did is None or not name or did in out:
            continue
        out[int(did)] = {"id": int(did), "name": name, "delivery_type": dtype, "marketplace_id": mkt}
    return list(out.values())


async def _mirror_destinations(db: AsyncSession, project_id: int, warehouse_id: int) -> list[dict]:
    """Индекс складов назначения из НАШЕГО зеркала отгрузок (FulfillmentRequest.raw).

    Read-sync регулярно качает /shipments целиком — numeric id и имя склада уже
    лежат в raw. Локальный индекс мгновенный и всегда тёплый, в отличие от
    read-API (/shipments 20с+ → таймаут → «склад не распознан» на ровном месте).
    """
    raw = FulfillmentRequest.raw
    rows = (
        await db.execute(
            select(
                raw["destination_marketplace_id"].as_integer(),
                raw["destination_marketplace"]["name"].as_string(),
                raw["delivery_type"].as_string(),
                raw["marketplace_id"].as_integer(),
            )
            .where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.warehouse_id == warehouse_id,
                FulfillmentRequest.provider == MIGFULL_PROVIDER,
                FulfillmentRequest.kind == FfRequestKind.ASSEMBLY.value,
                FulfillmentRequest.raw.is_not(None),
            )
            .order_by(FulfillmentRequest.external_created_at.desc().nulls_last(), FulfillmentRequest.id.desc())
            .limit(_MIRROR_DEST_LIMIT)
        )
    ).all()
    return _destinations_from_mirror_rows([tuple(r) for r in rows])


async def _read_destinations(db: AsyncSession, project_id: int, warehouse_id: int) -> list[dict]:
    """Справочник складов назначения: зеркало БД + read-API (кэш 1ч), API-записи
    поверх зеркальных при совпадении id. Best-effort: оба источника пусты → []."""
    api = await _api_destinations(db, project_id, warehouse_id)
    mirror = await _mirror_destinations(db, project_id, warehouse_id)
    if not mirror:
        return api
    by_id: dict[int, dict] = {d["id"]: d for d in mirror}
    for d in api:
        by_id[d["id"]] = d
    return list(by_id.values())


async def _api_destinations(db: AsyncSession, project_id: int, warehouse_id: int) -> list[dict]:
    """Справочник складов назначения из read-API migfull (кэш 1ч). Best-effort:
    нет read-ключа / ошибка → []. Использует ОТДЕЛЬНЫЙ ключ `service="migfull"`
    (Bearer read-API), не портальный."""
    now = utcnow().timestamp()
    cached = _dest_cache.get(project_id)
    if cached and (now - cached[0]) < _DEST_CACHE_TTL_SEC:
        return cached[1]
    if (now - _dest_fail_at.get(project_id, 0.0)) < _DEST_FAIL_TTL_SEC:
        # Индекс недавно не собрался — не долбим read-API на каждый draft/send.
        return cached[1] if cached else []
    key = (
        await db.execute(
            select(IntegrationKey).where(
                IntegrationKey.project_id == project_id,
                IntegrationKey.service == MIGFULL_READ_SERVICE,
                IntegrationKey.warehouse_id == warehouse_id,
                IntegrationKey.is_active.is_(True),
                IntegrationKey.is_deleted.is_(False),
            )
        )
    ).scalar_one_or_none()
    if key is None:
        return cached[1] if cached else []
    tenant_guid = str((key.config or {}).get("tenant_guid") or "")
    if not tenant_guid:
        return cached[1] if cached else []
    try:
        client = MigfullClient(tenant_guid, _decrypt(key.encrypted_key), project_id=project_id)
        dests: list[dict] = await asyncio.wait_for(
            client.fetch_destination_index(), timeout=_DEST_FETCH_TIMEOUT_SEC
        )
    except (MigfullApiError, httpx.HTTPError, CircuitOpenError, ValueError, TimeoutError) as e:
        _dest_fail_at[project_id] = now
        logger.warning("migfull_portal.destinations_fetch_failed", project_id=project_id, error=str(e)[:120] or type(e).__name__)
        return cached[1] if cached else []
    _dest_cache[project_id] = (now, dests)
    _dest_fail_at.pop(project_id, None)
    return dests


async def _resolve_destination_id(
    db: AsyncSession, project_id: int, warehouse_id: int, wb_name: str | None
) -> dict | None:
    """Наш WB-склад назначения сборки → migfull destination {id, name} (или None).

    Резолвим ТОЛЬКО по имени (delivery_type=None): numeric id портала одинаков для
    любого filter_delivery_type (getOptionsForJs отдаёт те же склады с теми же id), а
    read-API индекс помечает ВСЕ склады как 'direct' (по историческим отгрузкам) — фильтр
    по типу доставки заявки (у нас pickup) ложно отсёк бы все склады.
    """
    dests = await _read_destinations(db, project_id, warehouse_id)
    wb_dests = [d for d in dests if d.get("marketplace_id") in (None, _WB_MARKETPLACE_ID_INT)]
    return resolve_destination(wb_name, None, wb_dests or dests)


# ─── Cookie-сессия портала (переиспользование между отправками) ──────────────


def _session_cache_key(client: MigfullPortalClient) -> tuple[int | None, str, str]:
    # login/host в ключе: смена кредов интеграции не должна подхватить чужую сессию
    return (client.project_id, client.host, client.login)


def _restore_portal_session(client: MigfullPortalClient) -> bool:
    cached = _portal_sessions.get(_session_cache_key(client))
    if cached is None or (utcnow().timestamp() - cached[0]) > _SESSION_TTL_SEC:
        return False
    return client.restore_session(cached[1])


def _save_portal_session(client: MigfullPortalClient) -> None:
    _portal_sessions[_session_cache_key(client)] = (utcnow().timestamp(), client.export_session())


def _drop_portal_session(client: MigfullPortalClient) -> None:
    _portal_sessions.pop(_session_cache_key(client), None)


_T = TypeVar("_T")


async def _with_portal_session(client: MigfullPortalClient, fn: Callable[[], Awaitable[_T]]) -> _T:
    """Выполнить портальный вызов под живой cookie-сессией.

    Сессия из кэша (батч логинится один раз — иначе Filament-троттлинг ~5 логинов/мин
    валит 6-ю+ заявку); нет кэша → логин. Сессия протухла (MigfullPortalAuthError —
    поднимается только из начального GET, ДО мутаций) → один перелогин и повтор.
    """
    if not _restore_portal_session(client):
        await client.authenticate()
    try:
        result = await fn()
    except MigfullPortalAuthError:
        _drop_portal_session(client)
        await client.authenticate()
        result = await fn()
    _save_portal_session(client)
    return result


# ─── Config ──────────────────────────────────────────────────────────────────


async def get_config(db: AsyncSession, project_id: int) -> MigfullPortalConfigResponse:
    key = await _get_key_or_none(db, project_id)
    if key is None:
        return MigfullPortalConfigResponse(configured=False)
    wh_name: str | None = None
    if key.warehouse_id is not None:
        wh_name = await db.scalar(select(Warehouse.name).where(Warehouse.id == key.warehouse_id))
    return MigfullPortalConfigResponse(configured=True, warehouse_id=key.warehouse_id, warehouse_name=wh_name)


# ─── Загрузка сборки ─────────────────────────────────────────────────────────


async def _load_assembly(db: AsyncSession, project_id: int, assembly_id: int) -> AssemblyRequest | None:
    result = await db.execute(
        select(AssemblyRequest)
        .where(
            AssemblyRequest.id == assembly_id,
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
        )
        .options(selectinload(AssemblyRequest.items), selectinload(AssemblyRequest.wb_fbo_supply))
    )
    return result.scalar_one_or_none()


# ─── Опись: состав сборки → строки (короб/россыпь) ───────────────────────────


def classify_opis_lines(
    qty_by_bc: dict[str, int],
    *,
    box_for_piece: dict[str, tuple[str, int, str | None]],  # EAN13 россыпи → (ШК короба ITF14, шт/короб, имя)
    name_for_barcode: dict[str, str | None],  # ШК → имя товара
) -> tuple[list[MigfullOpisLine], list[str]]:
    """Чистое ядро описи (без БД): короб (есть сопоставление) → ITF14 + число коробов;
    иначе россыпь EAN13 + штуки. Кол-во не кратно коробу → fallback в россыпь + warning.
    """
    lines: list[MigfullOpisLine] = []
    warnings: list[str] = []
    for bc in sorted(qty_by_bc, key=lambda b: (name_for_barcode.get(b) or "", b)):
        pieces = qty_by_bc[bc]
        if pieces <= 0:
            continue
        box = box_for_piece.get(bc)
        if box is not None:
            box_barcode, upb, box_name = box
            upb = int(upb or 1)
            if upb > 1 and pieces % upb == 0:
                lines.append(
                    MigfullOpisLine(
                        barcode=box_barcode, name=box_name, quantity=pieces // upb,
                        is_box=True, units_per_box=upb, pieces=pieces,
                    )
                )
                continue
            if upb > 1:
                warnings.append(
                    f"{name_for_barcode.get(bc) or bc}: {pieces} шт не кратно коробу ({upb}) — отправлено россыпью"
                )
        lines.append(
            MigfullOpisLine(
                barcode=bc, name=name_for_barcode.get(bc), quantity=pieces,
                is_box=False, units_per_box=1, pieces=pieces,
            )
        )
    return lines, warnings


async def _compute_opis_lines(
    db: AsyncSession, project_id: int, warehouse_id: int, assembly: AssemblyRequest
) -> tuple[list[MigfullOpisLine], list[str]]:
    """Загрузка состава сборки + сопоставления короб→россыпь из БД → classify_opis_lines."""
    qty_by_bc: dict[str, int] = {}
    nom_by_bc: dict[str, int] = {}
    for it in assembly.items:
        bc = (it.barcode or "").strip()
        if not bc:
            continue
        qty_by_bc[bc] = qty_by_bc.get(bc, 0) + int(it.quantity or 0)
        if it.nomenclature_id:
            nom_by_bc.setdefault(bc, it.nomenclature_id)
    return await compute_opis_lines_from_qty(db, project_id, warehouse_id, qty_by_bc, nom_by_bc)


async def load_opis_context(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    nom_by_bc: dict[str, int],
) -> tuple[dict[str, tuple[str, int, str | None]], dict[str, str | None]]:
    """Контекст описи из зеркала остатков ФФ: (box_for_piece, name_for_barcode).

    box_for_piece: EAN13 россыпи → (ШК короба ITF14, шт/короб, имя короба) — карта
    кратности Натали. name_for_barcode: ШК → имя товара, с фолбэком из номенклатуры
    (артикул) для ШК без строки в зеркале ФФ.
    """
    stock_rows = (
        await db.execute(
            select(FulfillmentStock).where(
                FulfillmentStock.project_id == project_id,
                FulfillmentStock.warehouse_id == warehouse_id,
            )
        )
    ).scalars().all()
    name_for_barcode: dict[str, str | None] = {}
    box_for_piece: dict[str, tuple[str, int, str | None]] = {}  # EAN13 → (ITF14, шт/короб, имя)
    for r in stock_rows:
        name_for_barcode.setdefault(r.barcode, r.name)
        if r.base_barcode and (r.units_per_box or 1) > 1:
            box_for_piece.setdefault(r.base_barcode, (r.barcode, int(r.units_per_box or 1), r.name))

    # Фолбэк имени из номенклатуры (для ШК без строки в зеркале ФФ)
    nom_ids = set(nom_by_bc.values())
    if nom_ids:
        nom_name: dict[int, str | None] = {}
        for nid, art in (
            await db.execute(
                select(Nomenclature.id, Nomenclature.article_seller).where(
                    Nomenclature.project_id == project_id, Nomenclature.id.in_(nom_ids)
                )
            )
        ).all():
            nom_name[nid] = art
        for bc, nid in nom_by_bc.items():
            if not name_for_barcode.get(bc):
                name_for_barcode[bc] = nom_name.get(nid)

    return box_for_piece, name_for_barcode


async def compute_opis_lines_from_qty(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    qty_by_bc: dict[str, int],
    nom_by_bc: dict[str, int],
) -> tuple[list[MigfullOpisLine], list[str]]:
    """Штуки по ШК → строки описи (короб/россыпь) через зеркало остатков ФФ.

    Общее ядро для обоих направлений: заявка на отгрузку (состав сборки) и
    поставка/приёмка (состав нашей InboundReceipt) — источник qty разный,
    сопоставление короб→россыпь и фолбэк имени из номенклатуры одинаковые.
    """
    qty_by_bc = {bc: q for bc, q in qty_by_bc.items() if q > 0}
    if not qty_by_bc:
        return [], []
    box_for_piece, name_for_barcode = await load_opis_context(db, project_id, warehouse_id, nom_by_bc)
    return classify_opis_lines(qty_by_bc, box_for_piece=box_for_piece, name_for_barcode=name_for_barcode)


# ─── Анти-дубль ──────────────────────────────────────────────────────────────


async def _already_sent(db: AsyncSession, project_id: int, assembly_id: int) -> tuple[bool, str | None, str | None]:
    """Уже создавали заявку для этой сборки? (audit SENT либо связанная FulfillmentRequest)."""
    order = (
        await db.execute(
            select(MigfullShipmentOrder)
            .where(
                MigfullShipmentOrder.project_id == project_id,
                MigfullShipmentOrder.assembly_request_id == assembly_id,
                MigfullShipmentOrder.status == MigfullShipmentStatus.SENT,
            )
            .order_by(MigfullShipmentOrder.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if order is not None:
        return True, order.shipment_guid, order.shipment_number
    existing = (
        await db.execute(
            select(FulfillmentRequest.external_id, FulfillmentRequest.number).where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.provider == MIGFULL_PROVIDER,
                FulfillmentRequest.assembly_request_id == assembly_id,
            ).limit(1)
        )
    ).first()
    if existing is not None:
        return True, existing.external_id, existing.number
    return False, None, None


# ─── Примечание для оператора Натали ─────────────────────────────────────────


def _default_notes(ar: AssemblyRequest, supply: object | None) -> str:
    """Примечание заявки в портале Натали — оператор видит это поле и связывает
    заявку с нашей сборкой DDS. Пишем наш № сборки (ASM) и № поставки WB.
    Склад назначения теперь выставляется программно в поле формы (не через notes).
    Это дефолт-prefill; пользователь может изменить текст в модалке перед отправкой.
    """
    parts = [f"DDS · {ar.number}"] if ar.number else ["DDS"]
    wb_supply = getattr(supply, "wb_supply_id", None) if supply is not None else None
    if wb_supply:
        parts.append(f"поставка ВБ {wb_supply}")
    return " · ".join(parts)


# ─── Draft (модалка) ─────────────────────────────────────────────────────────


async def build_draft(db: AsyncSession, project_id: int, assembly_id: int) -> MigfullDraftResponse:
    key = await _get_key(db, project_id)
    ar = await _load_assembly(db, project_id, assembly_id)
    if ar is None:
        raise MigfullPortalServiceError("Сборка не найдена", status_code=404)

    eligible = key.warehouse_id is not None and ar.warehouse_id == key.warehouse_id
    lines, warnings = await _compute_opis_lines(db, project_id, ar.warehouse_id, ar)
    already, guid, number = await _already_sent(db, project_id, assembly_id)
    supply = ar.wb_fbo_supply

    wb_name = (supply.warehouse_name if supply else None) or ar.wb_warehouse_name_manual
    dest = await _resolve_destination_id(db, project_id, ar.warehouse_id, wb_name)
    prefill = MigfullShipmentPrefill(
        number=(supply.wb_supply_id if supply else None),
        shipment_date=ar.delivery_date or ar.pickup_date,
        filter_delivery_type="pickup",  # склад назначения персистит только при Самовывозе
        notes=_default_notes(ar, supply),
        wb_warehouse_name=wb_name,
        destination_name=(dest.get("name") if dest else None),
        destination_matched=dest is not None,
        assembly_number=ar.number,
    )
    warnings = list(warnings)
    if eligible and wb_name and dest is None:
        warnings.append(
            f"Склад назначения «{wb_name}» не распознан в ФФ — заполните вручную в кабинете после создания"
        )
    return MigfullDraftResponse(
        eligible=eligible,
        already_sent=already,
        sent_guid=guid,
        sent_number=number,
        prefill=prefill,
        delivery_types=[MigfullDeliveryTypeOption(value=v, label=lbl) for v, lbl in DELIVERY_TYPE_LABELS.items()],
        opis_lines=lines,
        total_boxes=sum(line.quantity for line in lines if line.is_box),
        total_pieces=sum(line.pieces for line in lines),
        warnings=warnings,
    )


# ─── Send (реальное создание заявки) ─────────────────────────────────────────


def _opis_filename(ar: AssemblyRequest) -> str:
    safe = "".join(ch for ch in (ar.number or "opis") if ch.isalnum() or ch in "-_") or "opis"
    return f"opis_{safe}.xlsx"


async def _upsert_ff_link(
    db: AsyncSession, project_id: int, warehouse_id: int, assembly_id: int,
    guid: str, number: str | None, total_qty: int, dest: str | None,
) -> None:
    """Связать созданную заявку (provider=migfull, external_id=guid) со сборкой.

    Read-sync НЕ трогает assembly_request_id на upsert → связь переживает синки.
    """
    existing = (
        await db.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.provider == MIGFULL_PROVIDER,
                FulfillmentRequest.external_id == guid,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.assembly_request_id = assembly_id
        if number:
            existing.number = number
        return
    db.add(
        FulfillmentRequest(
            project_id=project_id,
            warehouse_id=warehouse_id,
            provider=MIGFULL_PROVIDER,
            external_id=guid,
            number=number,
            kind=FfRequestKind.ASSEMBLY.value,
            status="new",
            assembly_request_id=assembly_id,
            total_qty=total_qty or None,
            dest_warehouse=dest,
            synced_at=utcnow(),
        )
    )


async def _record_outcome(
    db: AsyncSession, *, project_id: int, assembly_id: int, warehouse_id: int,
    status: str, guid: str | None, reference: str | None, payload: dict,
    filename: str, excerpt: str | None, error: str | None, actor: str | None,
    total_qty: int, dest: str | None,
) -> MigfullShipmentOrder:
    """Зафиксировать исход. Audit-строку коммитим ПЕРВОЙ (необратимый факт отправки не
    должен зависеть от FF-link). Затем — best-effort связь со сборкой, терпящая гонку с
    read-sync по тому же guid (audit уже сохранён → анти-дубль цел в любом случае).
    """
    order = MigfullShipmentOrder(
        project_id=project_id, assembly_request_id=assembly_id, status=status,
        shipment_guid=guid, shipment_number=reference, payload=payload,
        opis_filename=filename, response_excerpt=excerpt, error=error, created_by=actor,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)

    if guid and status in (MigfullShipmentStatus.SENT, MigfullShipmentStatus.UNCERTAIN):
        try:
            await _upsert_ff_link(db, project_id, warehouse_id, assembly_id, guid, reference, total_qty, dest)
            await db.commit()
        except IntegrityError:
            # Гонка: read-sync создал FulfillmentRequest с тем же guid между нашим SELECT и INSERT.
            await db.rollback()
            existing = (
                await db.execute(
                    select(FulfillmentRequest).where(
                        FulfillmentRequest.project_id == project_id,
                        FulfillmentRequest.provider == MIGFULL_PROVIDER,
                        FulfillmentRequest.external_id == guid,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.assembly_request_id = assembly_id
                if reference:
                    existing.number = reference
                await db.commit()
            else:
                logger.warning("migfull_portal.ff_link_race", project_id=project_id, guid=guid)
    return order


async def send_shipment(
    db: AsyncSession, project_id: int, assembly_id: int, req: MigfullSendRequest, actor: str | None = None
) -> MigfullSendResult:
    key = await _get_key(db, project_id)
    ar = await _load_assembly(db, project_id, assembly_id)
    if ar is None:
        raise MigfullPortalServiceError("Сборка не найдена", status_code=404)
    if key.warehouse_id is None or ar.warehouse_id != key.warehouse_id:
        raise MigfullPortalServiceError("Эта сборка не со склада ФФ «Натали» — отправка недоступна", status_code=400)
    if ar.status == AssemblyStatus.CANCELLED.value:
        raise MigfullPortalServiceError("Нельзя отправить отменённую сборку", status_code=400)

    # Анти-дубль: создание НЕОБРАТИМО (нет delete/cancel у клиента).
    if not req.force_resend:
        already, _, num = await _already_sent(db, project_id, assembly_id)
        if already:
            raise MigfullPortalServiceError(
                f"Заявка для этой сборки уже создана в ФФ ({num or '—'}). Подтвердите повторную отправку.",
                status_code=409,
            )

    lines, warnings = await _compute_opis_lines(db, project_id, ar.warehouse_id, ar)
    if not lines:
        raise MigfullPortalServiceError("В сборке нет позиций для описи", status_code=400)

    supply = ar.wb_fbo_supply
    dest = (supply.warehouse_name if supply else None) or ar.wb_warehouse_name_manual
    matched = await _resolve_destination_id(db, project_id, ar.warehouse_id, dest)
    shipment_date = req.shipment_date or ar.delivery_date or ar.pickup_date
    # Склад назначения (destination_marketplace_id) = numeric id из резолвера (тот же id,
    # что ждёт форма портала). ⚠️ Портал migfull ПЕРСИСТИТ склад ТОЛЬКО при
    # filter_delivery_type="pickup" (Самовывоз) — при direct/transit значение молча роняется
    # (реактивный хук гидрирует реляцию только для pickup; проверено живьём). client сетит
    # filter ПЕРЕД destination_marketplace_id (см. create_shipment). filter не персистится
    # (транзитный UI-фильтр), но обязателен в момент create.
    header: dict[str, object] = {
        "marketplace_id": _WB_MARKETPLACE_ID,
        "shipment_type": "fbo",
        "number": req.number or (supply.wb_supply_id if supply else None),
        "shipment_date": shipment_date.isoformat() if shipment_date else None,
        "notes": req.notes or _default_notes(ar, supply),
        "filter_delivery_type": req.filter_delivery_type,
        # str — форма портала ждёт строковый numeric id (живьём персистит именно строку).
        "destination_marketplace_id": str(matched["id"]) if matched else None,
    }
    xlsx = build_opis_xlsx(
        lines,
        incoming_number=(supply.wb_supply_id if supply else ar.number),
        incoming_date=shipment_date.isoformat() if shipment_date else None,
    )
    filename = _opis_filename(ar)
    payload = {"header": {k: v for k, v in header.items() if v is not None},
               "opis_lines": [line.model_dump() for line in lines], "warnings": warnings}

    total_pieces = sum(line.pieces for line in lines)
    status = MigfullShipmentStatus.FAILED
    guid: str | None = None
    reference: str | None = None
    message = ""
    excerpt: str | None = None
    error: str | None = None

    def _uncertain_if_guid() -> str:
        # guid есть → шапка УЖЕ создана на портале (необратимо), даже если опись упала.
        return MigfullShipmentStatus.UNCERTAIN if guid else MigfullShipmentStatus.FAILED

    try:
        async with _client_from_key(key) as client:
            created = await _with_portal_session(client, lambda: client.create_shipment(header))
            guid = created.guid
            upload = await _with_portal_session(
                client, lambda: client.upload_opis(guid, filename, xlsx, OPIS_CONTENT_TYPE)
            )
        reference = upload.reference
        status = MigfullShipmentStatus.SENT if upload.ok else MigfullShipmentStatus.UNCERTAIN
        message = (
            f"Заявка создана ({reference or guid}), опись загружена"
            if upload.ok
            else f"Заявка создана ({reference or guid}), но загрузка описи не подтверждена — проверьте в кабинете"
        )
        excerpt = upload.excerpt or None
    except asyncio.CancelledError:
        # Окно отмены (дисконнект/таймаут/shutdown) после создания шапки: заявка могла
        # уже создаться (guid) — фиксируем audit ДО re-raise (shield от отмены коммита),
        # иначе анти-дубль ослепнет и пользователь создаст вторую необратимую заявку.
        status = _uncertain_if_guid()
        error = message = "Запрос отменён во время отправки — проверьте заявку в кабинете ФФ."
        await asyncio.shield(
            _record_outcome(
                db, project_id=project_id, assembly_id=ar.id, warehouse_id=ar.warehouse_id,
                status=status, guid=guid, reference=reference, payload=payload,
                filename=filename, excerpt=None, error=error, actor=actor,
                total_qty=total_pieces, dest=dest,
            )
        )
        raise
    except MigfullPortalError as e:
        message = error = str(e)
        status = _uncertain_if_guid()
    except (httpx.HTTPError, CircuitOpenError, ValueError) as e:
        message = error = f"Ошибка связи с порталом ФФ: {e}. Проверьте в кабинете перед повтором."
        status = _uncertain_if_guid()
    except Exception as e:  # noqa: BLE001 — необратимо: всегда фиксируем audit-исход, не ретраим
        message = error = f"Непредвиденная ошибка отправки: {e}. Проверьте в кабинете."
        status = _uncertain_if_guid()

    order = await _record_outcome(
        db, project_id=project_id, assembly_id=ar.id, warehouse_id=ar.warehouse_id,
        status=status, guid=guid, reference=reference, payload=payload,
        filename=filename, excerpt=excerpt, error=error, actor=actor,
        total_qty=total_pieces, dest=dest,
    )

    logger.info("migfull_portal.send", project_id=project_id, assembly_id=assembly_id, status=status, guid=guid)
    return MigfullSendResult(
        ok=(status == MigfullShipmentStatus.SENT),
        shipment_guid=guid,
        shipment_number=reference,
        message=message,
        order_id=order.id,
    )
