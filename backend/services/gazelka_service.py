# ruff: noqa: RUF002, RUF003
"""
Service: Gazelka (gazelka.space) — передача заявки логиста перевозчику.

Источников ДВА, и они равноправны:
  • ``AssemblyRequest`` — сборка на маркетплейс (``is_marketplace='yes'``,
    дискриминатор заказа в портале = № поставки WB);
  • ``StockTransfer`` — ПЕРЕЕЗД между нашими складами (``is_marketplace='no'``,
    № поставки нет вовсе → дискриминатор = маркер «TR-№» в notes, см.
    ``_transfer_marker``).

Креды интеграции лежат в ``IntegrationKey`` (service="gazelka"):
``encrypted_key`` = Fernet-пароль, ``config={"login", "customer_id", "host"?}``,
``warehouse_id`` = склад Газельки. Гейт допуска: у сборки — склад отгрузки
(``warehouse_id``), у переезда — склад-ИСТОЧНИК (``from_warehouse_id``): груз
Газелька забирает именно оттуда.

build_draft / build_transfer_draft — логин + снятие справочников ИХ формы +
               предзаполнение из документа.
send_order / send_transfer_order   — РЕАЛЬНОЕ создание заявки во внешнем сервисе
               (необратимо), пишет audit-строку ``GazelkaOrder`` с исходом и
               выдержкой ответа.
"""

import asyncio
import html as _html
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import httpx
import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from backend.integrations.gazelka_client import (
    BASE_URL,
    ApplyForm,
    DeliveryPlace,
    GazelkaApiError,
    GazelkaClient,
    GazelkaCreateResult,
    SchedulePlan,
)
from backend.cache import invalidate_cache
from backend.integrations.resilience import CircuitOpenError
from backend.models import GazelkaOrder, GazelkaOrderStatus, IntegrationKey, Warehouse
from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.warehouse import OutboundShipment, StockTransfer, TransferStatus
from backend.models.wb_fbo import WbFboSupply
from backend.schemas.gazelka import (
    GazelkaConfigResponse,
    GazelkaDraftResponse,
    GazelkaEditDraft,
    GazelkaFormOptions,
    GazelkaLinkKind,
    GazelkaMatchCandidate,
    GazelkaMatchRequest,
    GazelkaMatchResult,
    GazelkaOrderList,
    GazelkaOrderRow,
    GazelkaPrefill,
    GazelkaSchedulePlan,
    GazelkaSelectOption,
    GazelkaSendRequest,
    GazelkaSendResult,
)
from backend.schemas.warehouse import TransferAssignVehicle
from backend.utils.time import utcnow
from backend.utils.crypto import decrypt as _decrypt

logger = structlog.get_logger("dds.gazelka_service")

GAZELKA_SERVICE = "gazelka"

# Маркеры WB в названиях маркетплейсов Газельки (для дефолта marketplace_id)
_WB_MARKERS = ("wildberries", "вайлдберриз", "вб")

# Наш город отгрузки: прайс-лист Газельки по умолчанию
PRICE_LIST_HOME = "Иваново"


class GazelkaServiceError(Exception):
    """Доменная ошибка отправки (роутер мапит в HTTPException)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ─── Креды / клиент ──────────────────────────────────────────────────────────


async def _get_key_or_none(db: AsyncSession, project_id: int) -> IntegrationKey | None:
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == GAZELKA_SERVICE,
            IntegrationKey.is_active.is_(True),
            IntegrationKey.is_deleted.is_(False),
        )
    )
    return result.scalar_one_or_none()


async def _get_key(db: AsyncSession, project_id: int) -> IntegrationKey:
    key = await _get_key_or_none(db, project_id)
    if key is None:
        raise GazelkaServiceError(
            "Газелька: интеграция не настроена (scripts/setup_gazelka_account.py)", status_code=400
        )
    return key


def _client_from_key(key: IntegrationKey) -> GazelkaClient:
    cfg = key.config or {}
    login = cfg.get("login") or key.label or ""
    customer_id = cfg.get("customer_id")
    if not login or not customer_id:
        raise GazelkaServiceError("Газелька: в ключе не задан login/customer_id (config)", status_code=500)
    return GazelkaClient(
        login=login,
        password=_decrypt(key.encrypted_key),
        customer_id=str(customer_id),
        project_id=key.project_id,
        host=cfg.get("host") or BASE_URL,
    )


# ─── Config (для гейта кнопки на фронте) ─────────────────────────────────────


async def get_config(db: AsyncSession, project_id: int) -> GazelkaConfigResponse:
    key = await _get_key_or_none(db, project_id)
    if key is None:
        return GazelkaConfigResponse(configured=False)
    wh_name: str | None = None
    if key.warehouse_id is not None:
        wh_name = await db.scalar(select(Warehouse.name).where(Warehouse.id == key.warehouse_id))
    return GazelkaConfigResponse(
        configured=True,
        warehouse_id=key.warehouse_id,
        warehouse_name=wh_name,
        # Список, а не одно поле: гейт переезда идёт по складу-ИСТОЧНИКУ, и когда
        # ключей интеграции станет два, фронт не придётся переучивать. Сегодня
        # ключ ровно один → ровно один склад (или пусто, если склад не задан —
        # тогда отправлять некуда и кнопку показывать нельзя).
        warehouse_ids=[key.warehouse_id] if key.warehouse_id is not None else [],
    )


# ─── Загрузка документа (сборка / переезд) ───────────────────────────────────


async def _load_assembly(db: AsyncSession, project_id: int, assembly_id: int) -> AssemblyRequest | None:
    result = await db.execute(
        select(AssemblyRequest)
        .where(
            AssemblyRequest.id == assembly_id,
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
        )
        .options(selectinload(AssemblyRequest.wb_fbo_supply))
    )
    return result.scalar_one_or_none()


async def _load_transfer(db: AsyncSession, project_id: int, transfer_id: int) -> StockTransfer | None:
    """Переезд для газельного диалога. Позиции НЕ подгружаем: форме портала нужны
    только паллеты/короба/вес, а состав переезда бывает на тысячи строк."""
    result = await db.execute(
        select(StockTransfer).where(
            StockTransfer.id == transfer_id,
            StockTransfer.project_id == project_id,
            StockTransfer.is_deleted.is_(False),
        )
    )
    return result.scalar_one_or_none()


async def _warehouse_label(db: AsyncSession, project_id: int, warehouse_id: int) -> str | None:
    """«Имя склада, адрес» — свободный текст получателя для формы портала.

    Адрес обязателен по смыслу: в dropdown портала наших складов нет вовсе, и
    водитель едет ровно по тому, что написано в `delivery_address_x2`.
    """
    row = (
        await db.execute(
            select(Warehouse.name, Warehouse.address).where(
                Warehouse.id == warehouse_id,
                Warehouse.project_id == project_id,
                Warehouse.is_deleted.is_(False),
            )
        )
    ).first()
    if row is None:
        return None
    name, address = row
    return ", ".join(p for p in (name, address) if p) or None


# ─── Справочники / предзаполнение ────────────────────────────────────────────


def _opt_list(form: ApplyForm, name: str, skip: tuple[str, ...] = ()) -> list[GazelkaSelectOption]:
    """Опции их select → схема, без плейсхолдеров и дублей по value."""
    out: list[GazelkaSelectOption] = []
    seen: set[str] = set()
    for value, label in form.selects.get(name, []):
        if value in skip or not label or value in seen:
            continue
        seen.add(value)
        out.append(GazelkaSelectOption(value=value, label=label))
    return out


def _warehouse_options(form: ApplyForm) -> list[GazelkaSelectOption]:
    """Склады назначения с привязкой к маркетплейсу и графику.

    Дедуп по ``value`` тут недопустим: одно название принадлежит нескольким
    маркетплейсам («Волгоград» — Ozon place 87 и WB place 77), и графики у них разные.
    """
    seen: set[tuple[str, str]] = set()
    out: list[GazelkaSelectOption] = []
    for place in form.places:
        key = (place.value, place.marketplace_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            GazelkaSelectOption(
                value=place.value,
                label=place.label,
                place_id=place.place_id,
                marketplace_id=place.marketplace_id,
            )
        )
    return out


def _default_price_id(form: ApplyForm) -> str | None:
    """Прайс-лист по умолчанию: наш город отгрузки — Иваново.

    Порядок предпочтений: «Иваново» → выбранное порталом (`option[selected]`) → первая
    опция. Первая опция сама по себе — НЕ дефолт (у портала это Симферополь).
    """
    options = form.selects.get("price_id") or []
    for value, label in options:
        if value and label.strip().lower() == PRICE_LIST_HOME.lower():
            return value
    return form.defaults.get("price_id") or (options[0][0] if options else None)


def _options_from_form(form: ApplyForm) -> GazelkaFormOptions:
    return GazelkaFormOptions(
        entities=_opt_list(form, "entity_id", skip=("",)),
        price_lists=_opt_list(form, "price_id", skip=("",)),
        marketplaces=_opt_list(form, "marketplace_id", skip=("", "0")),
        delivery_warehouses=_warehouse_options(form),
        supply_types=_opt_list(form, "monomix", skip=("", "0")),
        timeslots=_opt_list(form, "daily_delivery_timeslot", skip=("",)),
        default_entity_id=form.defaults.get("entity_id") or None,
        default_price_id=_default_price_id(form),
        schedule={
            key: GazelkaSchedulePlan(
                loading_days=plan.loading_days,
                delivery_days=plan.delivery_days,
                eta_days=plan.eta_days,
            )
            for key, plan in form.schedule.items()
            if plan.active
        },
        min_departure_date=form.min_departure,
        min_delivery_date=form.min_delivery,
    )


# ─── График: допустимые дни отправки/доставки ────────────────────────────────

_DOW_NAMES = ("Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб")
_SEARCH_HORIZON = 21  # дней вперёд ищем ближайший подходящий день (как их JS)


def _dow(d: date) -> int:
    """День недели в конвенции портала/JS: 0 = воскресенье."""
    return (d.weekday() + 1) % 7


def _days_label(days: list[int] | None) -> str:
    return "/".join(_DOW_NAMES[d] for d in sorted(days)) if days else "любой день"


def _next_allowed(start: date, days: list[int] | None) -> date | None:
    """Ближайшая (включая ``start``) дата с допустимым днём недели."""
    if not days:
        return start
    for offset in range(_SEARCH_HORIZON):
        candidate = start + timedelta(days=offset)
        if _dow(candidate) in days:
            return candidate
    return None


def _find_place(form: ApplyForm, address: str | None, marketplace_id: str | None) -> DeliveryPlace | None:
    """Опция склада по (название, маркетплейс) — название само по себе неуникально."""
    if not address:
        return None
    for place in form.places:
        if place.value == address and (not marketplace_id or place.marketplace_id == marketplace_id):
            return place
    return None


def _find_plan(form: ApplyForm, price_id: str, place: DeliveryPlace) -> SchedulePlan | None:
    plan = form.schedule.get(f"{price_id}-{place.place_id}")
    return plan if plan is not None and plan.active else None


def _suggest_dates(
    plan: SchedulePlan, earliest_departure: date
) -> tuple[date | None, date | None]:
    """Ближайшие допустимые (дата отправки, дата доставки) — как считает их форма."""
    departure = _next_allowed(earliest_departure, plan.loading_days)
    if departure is None:
        return None, None
    delivery = _next_allowed(departure + timedelta(days=plan.eta_days), plan.delivery_days)
    return departure, delivery


def _validate_schedule(form: ApplyForm, req: GazelkaSendRequest) -> None:
    """Отбить заявку с недопустимыми датами ДО реального POST (портал отвечает 500).

    Для не-маркетплейсной доставки график не применяется — там свободные даты.
    """
    if (req.is_marketplace or "").lower() != "yes":
        return
    if not form.places or not form.schedule:
        # Портал сменил разметку — парсер вернул пусто. Это неотличимо от «направление
        # не обслуживается», поэтому не блокируем весь WB-поток, а пропускаем проверку.
        logger.warning("gazelka.schedule_unparsed", places=len(form.places), rows=len(form.schedule))
        return
    place = _find_place(form, req.delivery_address, req.marketplace_id)
    if place is None:
        raise GazelkaServiceError(
            f"Газелька: склад «{req.delivery_address}» не обслуживается для выбранного маркетплейса",
            status_code=400,
        )
    plan = _find_plan(form, req.price_id, place)
    if plan is None:
        raise GazelkaServiceError(
            f"Газелька: направление на «{place.label}» из выбранного города не обслуживается",
            status_code=400,
        )

    min_departure = form.min_departure or utcnow().date()
    if req.departure_date is None or req.delivery_date is None:
        raise GazelkaServiceError("Газелька: укажите дату отправки и дату доставки", status_code=400)

    suggested_dep, suggested_del = _suggest_dates(plan, min_departure)
    if req.departure_date < min_departure:
        raise GazelkaServiceError(
            f"Газелька: дата отправки раньше {min_departure.strftime('%d.%m.%Y')}. "
            f"Ближайшая доступная — {suggested_dep.strftime('%d.%m.%Y') if suggested_dep else '—'}",
            status_code=400,
        )
    if plan.loading_days and _dow(req.departure_date) not in plan.loading_days:
        raise GazelkaServiceError(
            f"Газелька: «{place.label}» грузят по {_days_label(plan.loading_days)}. "
            f"Ближайшая дата отправки — {suggested_dep.strftime('%d.%m.%Y') if suggested_dep else '—'}",
            status_code=400,
        )

    earliest_delivery = _next_allowed(req.departure_date + timedelta(days=plan.eta_days), plan.delivery_days)
    if plan.delivery_days and _dow(req.delivery_date) not in plan.delivery_days:
        raise GazelkaServiceError(
            f"Газелька: «{place.label}» принимает доставку по {_days_label(plan.delivery_days)}. "
            f"Ближайшая дата доставки — {earliest_delivery.strftime('%d.%m.%Y') if earliest_delivery else '—'}",
            status_code=400,
        )
    if earliest_delivery is not None and req.delivery_date < earliest_delivery:
        raise GazelkaServiceError(
            f"Газелька: доставка не раньше {earliest_delivery.strftime('%d.%m.%Y')} "
            f"(путь занимает {plan.eta_days} дн. от даты отправки)",
            status_code=400,
        )
    if suggested_del is None:  # график есть, но подобрать дату не удалось — не блокируем
        logger.warning("gazelka.schedule_unresolved", place=place.label, price_id=req.price_id)


# Служебные слова WB-названий, не несущие смысла для матча («Склад Шушары», «СЦ Домодедово М4»)
_NOISE_TOKENS = frozenset(("склад", "сц", "рц"))
# Спец-склады (крупногабарит/питание) — не подставляем их вместо обычного склада города
_SPEC_TOKENS = frozenset(("сгт", "кгт", "питание"))
_TOKEN_RE = re.compile(r"[^\w]+", re.UNICODE)
_MIN_STEM = 5  # общий префикс короче — разные слова («новосемейкино» ≠ «новосибирск»)
_MAX_SUFFIX = 3  # столько букв может отличаться в хвосте («Перспективная» ≈ «Перспективный»)


def _tokens(name: str) -> set[str]:
    """Нормализованные значимые токены названия склада."""
    flat = name.strip().lower().replace("ё", "е")
    return {t for t in _TOKEN_RE.split(flat) if t and t not in _NOISE_TOKENS}


def _same_word(a: str, b: str) -> bool:
    """Одно слово с точностью до окончания: «Перспективная» ≈ «Перспективный»."""
    if a == b:
        return True
    common = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        common += 1
    return common >= _MIN_STEM and common >= max(len(a), len(b)) - _MAX_SUFFIX


def _token_score(wb: set[str], opt: set[str]) -> int:
    """Сколько токенов WB-названия нашли пару в опции (с учётом склонений)."""
    return sum(1 for w in wb if any(_same_word(w, o) for o in opt))


def _match_warehouse(
    wb_name: str | None,
    options: GazelkaFormOptions,
    marketplace_id: str | None,
    price_id: str | None = None,
) -> str | None:
    """Сматчить склад WB-поставки с их dropdown (value == label у delivery_address).

    Названия у сторон разные: «Склад Шушары» ↔ «Санкт-Петербург (Шушары)»,
    «Екатеринбург - Перспективная 14» ↔ «Екатеринбург (Перспективный)». Поэтому матч
    идёт по значимым токенам с префиксным сравнением, а не по вхождению подстроки.

    Кандидаты — только склады выбранного маркетплейса (у Ozon и WB одноимённые склады
    с разными графиками) и, если задан ``price_id``, только с активным графиком оттуда.
    При неоднозначности склад НЕ угадываем: пусть логист выберет сам.
    """
    if not wb_name:
        return None
    candidates = [
        o
        for o in options.delivery_warehouses
        if (not marketplace_id or o.marketplace_id == marketplace_id)
        and (not price_id or (o.place_id and f"{price_id}-{o.place_id}" in options.schedule))
    ]
    wb_tokens = _tokens(wb_name)
    for opt in candidates:
        if _tokens(opt.label) == wb_tokens:
            return opt.value

    # FBS-склад берём, только если сама поставка FBS: наши поставки — FBO.
    # Спец-склад (СГТ/питание) — только если он назван в самой поставке.
    wants_fbs = bool({"fbs", "фбс"} & wb_tokens)
    wants_spec = bool(_SPEC_TOKENS & wb_tokens)
    scored: list[tuple[int, str]] = []
    for opt in candidates:
        opt_tokens = _tokens(opt.label)
        score = _token_score(wb_tokens, opt_tokens)
        if not score:
            continue
        if not wants_fbs and ({"fbs", "фбс"} & opt_tokens):
            score -= 1
        if not wants_spec and (_SPEC_TOKENS & opt_tokens):
            score -= 1
        scored.append((score, opt.value))

    if not scored:
        return None
    best = max(s for s, _ in scored)
    winners = {value for score, value in scored if score == best}
    return winners.pop() if best > 0 and len(winners) == 1 else None


def _default_marketplace(options: GazelkaFormOptions) -> str | None:
    for opt in options.marketplaces:
        if opt.label.strip().lower() in _WB_MARKERS:
            return opt.value
    return None


def _prefill_dates(
    ar: AssemblyRequest, form: ApplyForm, price_id: str, address: str | None, marketplace_id: str | None
) -> tuple[date | None, date | None]:
    """Даты сборки, подтянутые к графику склада: не в прошлое и в допустимые дни недели.

    Даты сборки часто уже протухли (заявку шлют позже, чем планировали) — портал такие
    отвергает пятисоткой, поэтому предлагаем ближайшие рабочие.
    """
    min_departure = form.min_departure or utcnow().date()
    place = _find_place(form, address, marketplace_id)
    plan = _find_plan(form, price_id, place) if place else None
    if plan is None:
        return max(ar.pickup_date, min_departure) if ar.pickup_date else None, ar.delivery_date
    earliest = max(ar.pickup_date, min_departure) if ar.pickup_date else min_departure
    return _suggest_dates(plan, earliest)


def _prefill_from_assembly(
    ar: AssemblyRequest, form: ApplyForm, options: GazelkaFormOptions
) -> GazelkaPrefill:
    supply = ar.wb_fbo_supply
    wb_name = (supply.warehouse_name if supply else None) or ar.wb_warehouse_name_manual
    total_weight: Decimal | None = None
    if ar.pallets_count and ar.pallet_weight_kg is not None:
        total_weight = Decimal(ar.pallets_count) * ar.pallet_weight_kg
    marketplace_id = _default_marketplace(options)
    price_id = options.default_price_id or ""
    address = _match_warehouse(wb_name, options, marketplace_id, price_id)
    departure_date, delivery_date = _prefill_dates(ar, form, price_id, address, marketplace_id)
    return GazelkaPrefill(
        customer_phone=form.inputs.get("customer_phone") or None,
        delivery_address=address,
        delivery_address_x2=wb_name,
        departure_date=departure_date,
        delivery_date=delivery_date,
        delivery_contact=None,
        daily_delivery_timeslot=None,
        supply_id=(supply.wb_supply_id if supply else None),
        marketplace_id=marketplace_id,
        pallets=ar.pallets_count or 0,
        boxes=0,
        weight=total_weight,
        notes=None,
    )


# ─── Маркер переезда в notes: дискриминатор заказа вместо № поставки ─────────
#
# У сборки заказ портала опознаётся по `supply_id` (№ поставки WB). У переезда
# поставки НЕТ ВООБЩЕ, а `_plan_matches_payload` без неё сверяет только даты и
# паллеты — то есть чужой заказ на 3 паллеты в те же дни считался бы нашим.
# Цена ошибки несимметрична: ложный «не нашли» → FAILED и повторная отправка, а
# каждая отправка это РЕАЛЬНЫЙ заказ у перевозчика без отката. Поэтому в notes
# уходит номер переезда («TR-32 …»), и он же служит ключом сверки.
_TRANSFER_MARKER_RE = re.compile(r"\bTR-(\d+)\b", re.IGNORECASE)


def _transfer_marker(notes: object) -> str | None:
    """Номер переезда из notes заявки портала: «TR-32 Переезд …» → «TR-32».

    Сравниваем ЦЕЛИКОМ извлечённый номер, а не вхождением подстроки: «TR-3» иначе
    матчился бы внутрь «TR-32». Регистр портал местами меняет (оператор правит
    заявку руками), поэтому IGNORECASE, а результат нормализуем в верхний регистр.
    """
    s = _html.unescape(notes) if isinstance(notes, str) else ""
    m = _TRANSFER_MARKER_RE.search(s)
    return f"TR-{m.group(1)}" if m else None


def _transfer_notes(tr: StockTransfer, dest_label: str | None) -> str:
    """Notes заявки портала: маркер «TR-№» ПЕРВЫМ, дальше человеческая расшифровка.

    Первым — чтобы логист видел принадлежность заказа сразу в списке кабинета, а
    не искал её в хвосте комментария.
    """
    tail = f"Переезд на склад {dest_label}" if dest_label else "Переезд между складами"
    return f"{tr.number} {tail}"


def _prefill_from_transfer(
    tr: StockTransfer, form: ApplyForm, options: GazelkaFormOptions, dest_label: str | None
) -> GazelkaPrefill:
    """Предзаполнение формы портала из переезда «наш склад → наш склад».

    🔴 `price_id` отдаём ДЕФОЛТНЫМ (`PRICE_LIST_HOME` = Иваново, см.
    `_default_price_id`). Маппинга «наш склад-источник → прайс-лист портала» в
    системе НЕТ — прайс это ГОРОД ОТПРАВЛЕНИЯ у перевозчика, а у нас на складе
    хранится только адрес строкой, и угадывание города по ней молча увезло бы
    заказ из чужого города по чужому тарифу. Поэтому: **если склад-источник не в
    домашнем городе, логист ОБЯЗАН выбрать прайс-лист руками** — подсказка в
    диалоге, гарда на бэке нет и быть не может.

    Остальное:
    • `is_marketplace='no'` — маршрут маркетплейса не касается, поэтому
      `marketplace_id`/`supply_id` пусты, а `_validate_schedule` для такой заявки
      возвращается сразу (графики сдачи на маркетплейс к нам не относятся);
    • `delivery_address` — ЯВНАЯ ПУСТАЯ СТРОКА, не None: `_merge_payload`
      трактует None как «оставить дефолт формы», а дефолт их select'а — ПЕРВАЯ
      опция, то есть чужой склад маркетплейса. Груз уехал бы не туда;
    • адрес получателя идёт свободным текстом в `delivery_address_x2`;
    • единица (паллеты/короба) берётся из `shipped_as_boxes`: `pallets_count`
      хранит количество ИМЕННО в этой единице.
    """
    unit_count = tr.pallets_count or 0
    total_weight: Decimal | None = None
    if unit_count and tr.pallet_weight_kg is not None:
        total_weight = Decimal(unit_count) * tr.pallet_weight_kg
    # Дата забора у переезда часто уже в прошлом (машину назначали заранее) —
    # портал такие не принимает, поэтому подтягиваем к нижней границе их формы.
    # График дней недели тут не применяется: он есть только у маркетплейсных
    # направлений, а мы едем на свой склад.
    min_departure = form.min_departure or utcnow().date()
    departure = max(tr.pickup_date, min_departure) if tr.pickup_date else min_departure
    delivery = tr.delivery_date if (tr.delivery_date and tr.delivery_date >= departure) else None
    return GazelkaPrefill(
        is_marketplace="no",
        price_id=options.default_price_id,
        customer_phone=form.inputs.get("customer_phone") or None,
        delivery_address="",
        delivery_address_x2=dest_label,
        departure_date=departure,
        delivery_date=delivery,
        delivery_contact=None,
        daily_delivery_timeslot=None,
        supply_id=None,
        marketplace_id=None,
        pallets=0 if tr.shipped_as_boxes else unit_count,
        boxes=unit_count if tr.shipped_as_boxes else 0,
        weight=total_weight,
        notes=_transfer_notes(tr, dest_label),
    )


async def _last_sent(
    db: AsyncSession, project_id: int, doc_id: int, kind: GazelkaLinkKind = "assembly"
) -> tuple[bool, str | None]:
    """Была ли отправка по документу: SENT или UNCERTAIN (могла создаться — тоже блокирует повтор).

    Ключ — ПАРА (тип, id): id сборки и id переезда живут в разных счётчиках, и
    ключ по одному лишь `assembly_request_id` означал бы, что переезд №55 обходит
    защиту, зато чужая сборка №55 её ложно включает.
    """
    link_column = (
        GazelkaOrder.stock_transfer_id if kind == "transfer" else GazelkaOrder.assembly_request_id
    )
    row = await db.execute(
        select(GazelkaOrder)
        .where(
            GazelkaOrder.project_id == project_id,
            link_column == doc_id,
            GazelkaOrder.status.in_([GazelkaOrderStatus.SENT, GazelkaOrderStatus.UNCERTAIN]),
        )
        .order_by(GazelkaOrder.created_at.desc())
        .limit(1)
    )
    order = row.scalar_one_or_none()
    if order is None:
        return False, None
    return True, order.gazelka_ref


# ─── Draft (диалог) ──────────────────────────────────────────────────────────


async def _fetch_apply_form(key: IntegrationKey) -> ApplyForm:
    """Логин + снятие ИХ формы. Общее тело обоих драфтов (сборка/переезд)."""
    try:
        async with _client_from_key(key) as client:
            await client.authenticate()
            return await client.fetch_apply_form()
    except GazelkaApiError as e:
        raise GazelkaServiceError(str(e), status_code=e.status_code) from e
    except (httpx.HTTPError, CircuitOpenError, ValueError) as e:
        raise GazelkaServiceError(f"Газелька недоступна: {e}", status_code=502) from e


async def build_draft(db: AsyncSession, project_id: int, assembly_id: int) -> GazelkaDraftResponse:
    key = await _get_key(db, project_id)
    ar = await _load_assembly(db, project_id, assembly_id)
    if ar is None:
        raise GazelkaServiceError("Сборка не найдена", status_code=404)

    eligible = key.warehouse_id is not None and ar.warehouse_id == key.warehouse_id

    form = await _fetch_apply_form(key)
    options = _options_from_form(form)
    prefill = _prefill_from_assembly(ar, form, options)
    already_sent, sent_ref = await _last_sent(db, project_id, assembly_id)
    return GazelkaDraftResponse(
        eligible=eligible,
        already_sent=already_sent,
        sent_ref=sent_ref,
        options=options,
        prefill=prefill,
    )


async def build_transfer_draft(db: AsyncSession, project_id: int, transfer_id: int) -> GazelkaDraftResponse:
    """Диалог отправки ПЕРЕЕЗДА — зеркало `build_draft`.

    Гейт (`eligible`) — по складу-ИСТОЧНИКУ: Газелька забирает груз оттуда, куда
    привязан ключ, а куда груз едет, ей всё равно (это наш второй склад, его в
    их справочнике нет).
    """
    key = await _get_key(db, project_id)
    tr = await _load_transfer(db, project_id, transfer_id)
    if tr is None:
        raise GazelkaServiceError("Переезд не найден", status_code=404)

    eligible = key.warehouse_id is not None and tr.from_warehouse_id == key.warehouse_id

    form = await _fetch_apply_form(key)
    options = _options_from_form(form)
    dest_label = await _warehouse_label(db, project_id, tr.to_warehouse_id)
    prefill = _prefill_from_transfer(tr, form, options, dest_label)
    already_sent, sent_ref = await _last_sent(db, project_id, transfer_id, kind="transfer")
    return GazelkaDraftResponse(
        eligible=eligible,
        already_sent=already_sent,
        sent_ref=sent_ref,
        options=options,
        prefill=prefill,
    )


# ─── Send (реальное создание заявки) ─────────────────────────────────────────


def _s(v: object) -> str | None:
    return None if v is None else str(v)


def _payload_from_request(req: GazelkaSendRequest) -> dict[str, object]:
    """GazelkaSendRequest → поля их формы (action=save_plan добавит клиент)."""
    payload: dict[str, object] = {
        "entity_id": req.entity_id,
        "payer_id": req.payer_id,
        "price_id": req.price_id,
        "is_marketplace": req.is_marketplace or "",
        "marketplace_id": req.marketplace_id,
        "supply_id": req.supply_id,
        "delivery_address": req.delivery_address,
        "delivery_address_x2": req.delivery_address_x2,
        "departure_date": req.departure_date.isoformat() if req.departure_date else None,
        "delivery_date": req.delivery_date.isoformat() if req.delivery_date else None,
        "delivery_time": req.delivery_time,
        "daily_delivery_timeslot": req.daily_delivery_timeslot,
        "delivery_contact": req.delivery_contact,
        "customer_phone": req.customer_phone,
        "monomix": req.monomix,
        "pallets": str(req.pallets),
        "boxes": str(req.boxes),
        "weight2": _s(req.weight2),
        "weight": _s(req.weight),
        "volume": _s(req.volume),
        "length": str(req.length),
        "height": str(req.height),
        "width": str(req.width),
        "notes": req.notes,
    }
    if req.palleting:
        payload["palleting"] = "on"  # как шлёт браузер: у чекбокса нет value=
    # None-поля НЕ выкидываем в пустоту: клиент подставит дефолт формы (портал 500-ит,
    # если именованное поле формы вовсе отсутствует в теле POST).
    return payload


# ─── Подтверждение создания по списку «Запланированных» ─────────────────────
#
# У портала нет машинного признака успеха save_plan (ср. migfull/«Натали», где
# Livewire отдаёт redirect с GUID и errors). Редирект-эвристика клиента даёт
# ложные ошибки, хотя заявка реально создана → дубли при повторной отправке.
# Поэтому исход подтверждаем по факту: снимок id «Запланированных» ДО POST,
# после POST — ищем НОВУЮ строку, совпадающую с нашим payload.


def _plan_ids(data: dict) -> set[str]:
    """id всех заявок из ответа fetch_planned."""
    return {str(p.get("id")) for p in data.get("plans") or [] if p.get("id") is not None}


def _plan_matches_payload(plan: dict, payload: dict[str, object]) -> bool:
    """Строка портала совпадает с нашим payload по полям, которые мы задавали.

    Сравниваем только заполненные нами поля (None = дефолт формы, не проверяем).
    supply_id — по вхождению (портал/операторы дописывают текст вокруг номера).

    Когда supply_id пуст (это ПЕРЕЕЗД — поставки у него нет), дискриминатором
    становится маркер «TR-№» из notes. Без него от заказа осталась бы пара
    «даты + паллеты», под которую попадает любая чужая заявка того же дня.
    Если маркера нет и в нашем payload (старая сборка без поставки) — ведём себя
    как раньше и сверяем только даты с паллетами.
    """
    supply = str(payload.get("supply_id") or "")
    if supply:
        if supply not in str(_u(plan.get("supply_id")) or ""):
            return False
    else:
        marker = _transfer_marker(payload.get("notes"))
        if marker and _transfer_marker(plan.get("notes")) != marker:
            return False
    for key in ("departure_date", "delivery_date"):
        want = payload.get(key)
        if want and _clean_date(plan.get(key)) != str(want):
            return False
    pallets = payload.get("pallets")
    if pallets is not None and _to_int(plan.get("pallets")) != _to_int(pallets):
        return False
    return True


def _find_created_plan(before_ids: set[str], after: dict, payload: dict[str, object]) -> str | None:
    """id новой (не было в before_ids) заявки, похожей на нашу; None = не найдена.

    Чужая одновременная заявка отсеется несовпадением payload (для переезда —
    маркером «TR-№» в notes, см. `_plan_matches_payload`); из нескольких
    совпавших (дубли) берём самую свежую.
    """
    new = [p for p in after.get("plans") or [] if p.get("id") is not None and str(p.get("id")) not in before_ids]
    matched = [str(p["id"]) for p in new if _plan_matches_payload(p, payload)]
    if not matched:
        return None
    return max(matched, key=lambda s: int(s) if s.isdigit() else -1)


async def _snapshot_planned_ids(client: GazelkaClient) -> set[str] | None:
    """id «Запланированных» до POST; None = снять не удалось (подтверждение недоступно)."""
    try:
        return _plan_ids(await client.fetch_planned())
    except (GazelkaApiError, httpx.HTTPError, CircuitOpenError, ValueError):
        return None


async def _confirm_created(
    client: GazelkaClient, before_ids: set[str] | None, payload: dict[str, object]
) -> tuple[bool, str | None]:
    """(проверили ли список, id созданной заявки). (False, None) = сверка недоступна."""
    if before_ids is None:
        return False, None
    try:
        after = await client.fetch_planned()
    except (GazelkaApiError, httpx.HTTPError, CircuitOpenError, ValueError):
        return False, None
    return True, _find_created_plan(before_ids, after, payload)


@dataclass
class _SendOutcome:
    """Исход РЕАЛЬНОГО POST в портал — один на оба типа документа."""

    status: str
    ref: str | None
    message: str
    excerpt: str | None
    error: str | None


async def _post_order(key: IntegrationKey, req: GazelkaSendRequest, payload: dict[str, object]) -> _SendOutcome:
    """Отправить форму в портал и определить исход. `GazelkaServiceError` (недопустимые
    даты) пробрасывается: заявку не создавали, audit-строка не нужна."""
    login = (key.config or {}).get("login") or ""

    def _scrub(text: str) -> str:
        return text.replace(login, "[login]") if login else text

    status = GazelkaOrderStatus.FAILED
    ref: str | None = None
    message = ""
    excerpt: str | None = None
    error: str | None = None

    try:
        async with _client_from_key(key) as client:
            await client.authenticate()
            # Форму снимаем ОДИН раз: с неё берём и график для валидации, и CSRF для POST.
            form = await client.fetch_apply_form()
            _validate_schedule(form, req)
            # Снимок «Запланированных» ДО POST: редирект-эвристика ненадёжна,
            # реальный исход подтверждаем появлением новой строки в списке.
            before_ids = await _snapshot_planned_ids(client)
            result: GazelkaCreateResult | None = None
            post_exc: Exception | None = None
            try:
                result = await client.create_order(payload, form=form)
            except (httpx.HTTPError, CircuitOpenError, ValueError) as e:
                post_exc = e  # POST мог дойти до портала — исход сверим по списку
            if result is not None and result.ok:
                status = GazelkaOrderStatus.SENT
                _, confirmed = await _confirm_created(client, before_ids, payload)
                ref = confirmed or result.ref
                message = result.message
                excerpt = result.excerpt
            else:
                checked, confirmed = await _confirm_created(client, before_ids, payload)
                if confirmed is not None:
                    # Портал не подтвердил редиректом, но заявка появилась в списке —
                    # это успех (ложная ошибка ломала доверие и плодила дубли).
                    status = GazelkaOrderStatus.SENT
                    ref = confirmed
                    message = f"Заявка отправлена в Газельку (№{confirmed} в «Запланированных»)"
                    excerpt = result.excerpt if result is not None else None
                elif post_exc is not None:
                    if checked:  # сверили список — заявки нет: FAILED, повтор безопасен
                        message = _scrub(
                            f"Газелька ответила ошибкой, заявка не появилась в «Запланированных»: {post_exc}"
                        )
                    else:  # сверка недоступна — заявка МОГЛА создаться, повтор только с подтверждением
                        status = GazelkaOrderStatus.UNCERTAIN
                        message = _scrub(f"Ошибка связи с Газелькой: {post_exc}. Сверьте в кабинете перед повтором.")
                    error = message
                else:  # POST прошёл без редиректа-успеха (result есть), в списке заявки нет
                    if checked:  # сверили — не создана: FAILED, повтор безопасен
                        message = (
                            "Газелька отклонила заявку — она не появилась в «Запланированных». "
                            "Проверьте данные формы и попробуйте ещё раз."
                        )
                        error = message
                    else:  # сверка недоступна — могла создаться
                        status = GazelkaOrderStatus.UNCERTAIN
                        message = result.message if result is not None else ""
                    excerpt = result.excerpt if result is not None else None
    except GazelkaServiceError:
        raise  # недопустимые даты/склад — заявку не создавали, audit-строка не нужна
    except GazelkaApiError as e:
        message = _scrub(str(e))
        error = message
    except (httpx.HTTPError, CircuitOpenError, ValueError) as e:
        # Сбой до POST (логин/снятие формы) — заявку не создавали.
        message = _scrub(f"Ошибка связи с Газелькой: {e}. Сверьте в кабинете перед повтором.")
        error = message

    return _SendOutcome(status=status, ref=ref, message=message, excerpt=excerpt, error=error)


async def _persist_send(
    db: AsyncSession,
    project_id: int,
    kind: GazelkaLinkKind,
    doc_id: int,
    payload: dict[str, object],
    outcome: _SendOutcome,
    actor: str | None,
) -> GazelkaSendResult:
    """Записать попытку отправки в audit и вернуть результат для UI.

    Заполняем РОВНО ОДНУ ссылку: CHECK `ck_gazelka_orders_single_link` запрещает
    пару, иначе один заказ портала закрывал бы и сборку, и переезд.
    """
    order = GazelkaOrder(
        project_id=project_id,
        assembly_request_id=doc_id if kind == "assembly" else None,
        stock_transfer_id=doc_id if kind == "transfer" else None,
        status=outcome.status,
        gazelka_ref=outcome.ref,
        payload=payload,
        response_excerpt=outcome.excerpt,
        error=outcome.error,
        created_by=actor,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)

    logger.info(
        "gazelka.send",
        project_id=project_id,
        kind=kind,
        doc_id=doc_id,
        status=outcome.status,
        gazelka_ref=outcome.ref,
    )
    return GazelkaSendResult(
        ok=(outcome.status == GazelkaOrderStatus.SENT),
        ref=outcome.ref,
        message=outcome.message,
        gazelka_order_id=order.id,
    )


async def send_order(
    db: AsyncSession,
    project_id: int,
    assembly_id: int,
    req: GazelkaSendRequest,
    actor: str | None = None,
) -> GazelkaSendResult:
    key = await _get_key(db, project_id)
    ar = await _load_assembly(db, project_id, assembly_id)
    if ar is None:
        raise GazelkaServiceError("Сборка не найдена", status_code=404)
    if key.warehouse_id is None or ar.warehouse_id != key.warehouse_id:
        raise GazelkaServiceError("Эта сборка не со склада Газельки — отправка недоступна", status_code=400)

    # Идемпотентность: повторная отправка той же сборки = вторая реальная заявка.
    # Без явного force_resend отказываем (защита от дабл-клика/stale-вкладки/retry).
    if not req.force_resend:
        already_sent, _ = await _last_sent(db, project_id, assembly_id)
        if already_sent:
            raise GazelkaServiceError(
                "Заявка для этой сборки уже отправлялась в Газельку. "
                "Сверьте вкладку «Запланированные» и подтвердите повторную отправку.",
                status_code=409,
            )

    payload = _payload_from_request(req)
    outcome = await _post_order(key, req, payload)
    return await _persist_send(db, project_id, "assembly", ar.id, payload, outcome, actor)


async def send_transfer_order(
    db: AsyncSession,
    project_id: int,
    transfer_id: int,
    req: GazelkaSendRequest,
    actor: str | None = None,
) -> GazelkaSendResult:
    """РЕАЛЬНАЯ отправка ПЕРЕЕЗДА перевозчику — зеркало `send_order`.

    Гейт по складу-ИСТОЧНИКУ: Газелька приезжает туда, куда привязан ключ.

    🔴 Маркер «TR-№» в notes приклеиваем ПРИНУДИТЕЛЬНО, даже если логист стёр его
    в диалоге: без маркера у переезда не остаётся ни одного дискриминатора
    (поставки нет), подтверждение исхода упало бы на «даты + паллеты», а из
    ложного FAILED растёт повторная отправка — то есть второй РЕАЛЬНЫЙ заказ у
    перевозчика, отменять который нечем.
    """
    key = await _get_key(db, project_id)
    tr = await _load_transfer(db, project_id, transfer_id)
    if tr is None:
        raise GazelkaServiceError("Переезд не найден", status_code=404)
    if key.warehouse_id is None or tr.from_warehouse_id != key.warehouse_id:
        raise GazelkaServiceError("Этот переезд не со склада Газельки", status_code=400)

    if not req.force_resend:
        already_sent, _ = await _last_sent(db, project_id, transfer_id, kind="transfer")
        if already_sent:
            raise GazelkaServiceError(
                "Заявка для этого переезда уже отправлялась в Газельку. "
                "Сверьте вкладку «Запланированные» и подтвердите повторную отправку.",
                status_code=409,
            )

    payload = _payload_from_request(req)
    if _transfer_marker(payload.get("notes")) != tr.number:
        notes = str(payload.get("notes") or "").strip()
        payload["notes"] = f"{tr.number} {notes}".strip()
    outcome = await _post_order(key, req, payload)
    return await _persist_send(db, project_id, "transfer", tr.id, payload, outcome, actor)


# ─── Списки заявок из портала (read) ─────────────────────────────────────────

# Коды статусов портала (best-effort; неизвестные показываем как «Статус N»)
_STATUS_LABELS = {"2": "Запланирована", "3": "Принята в работу", "31": "В маршруте", "4": "Завершена"}
_MONO_LABELS = {
    "1": "Моно", "2": "Микс", "3": "Суперсейф", "4": "КГТ", "5": "FBS", "6": "Транзит", "7": "Питание",
}


def _status_label(code: object) -> str:
    return _STATUS_LABELS.get(str(code or ""), f"Статус {code}")


# Статусы НАШЕЙ сборки (AssemblyRequest) — «где заявка находится»
_ASSEMBLY_STATUS_LABELS = {
    "PENDING": "Ожидает",
    "IN_PROGRESS": "В сборке",
    "READY": "Готова",
    "VEHICLE_ASSIGNED": "Машина назначена",
    "SHIPPED": "Отгружена",
    "DELIVERED": "Доставлена",
    "CANCELLED": "Отменена",
    "CLOSED": "Закрыта",
}


def _assembly_status_label(code: object) -> str | None:
    if not code:
        return None
    return _ASSEMBLY_STATUS_LABELS.get(str(code), str(code))


# Статусы ПЕРЕЕЗДА (StockTransfer). Шкала — зеркало сборочной, но подписи свои:
# «Отгружена» у переезда читается как «уехал», «Доставлена» — «принят получателем».
_TRANSFER_STATUS_LABELS = {
    "PENDING": "Ожидает",
    "IN_PROGRESS": "В сборке",
    "READY": "Готов",
    "VEHICLE_ASSIGNED": "Машина назначена",
    "SHIPPED": "Уехал",
    "DELIVERED": "Принят",
    "RETURNED": "Возвращён",
    "CANCELLED": "Отменён",
    "CLOSED": "Закрыт",
}


def _doc_status_label(kind: GazelkaLinkKind, code: object) -> str | None:
    """Подпись статуса нашего документа — по его типу, а не по угадыванию."""
    if not code:
        return None
    table = _TRANSFER_STATUS_LABELS if kind == "transfer" else _ASSEMBLY_STATUS_LABELS
    return table.get(str(code), str(code))


def _u(v: object) -> str | None:
    """HTML-unescape строкового значения портала; пусто/не-строка → None."""
    if not isinstance(v, str):
        return None
    s = _html.unescape(v).strip()
    return s or None


def _to_int(v: object) -> int:
    try:
        return int(str(v or 0))
    except (ValueError, TypeError):
        return 0


def _clean_date(v: object) -> str | None:
    s = str(v or "").strip()[:10]
    return None if (not s or s.startswith("1970")) else s


def _date_or_none(v: object) -> date | None:
    s = str(v or "").strip()[:10]
    if not s or s.startswith("1970"):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _dec_or_none(v: object) -> Decimal | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _marketplace_name(mid: object, marketplaces: list[dict]) -> str | None:
    sid = str(mid or "")
    for m in marketplaces:
        if str(m.get("id")) == sid:
            return _u(m.get("name"))
    return None


#: gazelka_ref → (тип документа, id, номер, статус). Тип обязан ехать вместе с
#: id: счётчики сборок и переездов независимы, и id 55 существует в обоих.
_LinkedDoc = tuple[GazelkaLinkKind, int, str | None, str | None]


async def _linked_map(db: AsyncSession, project_id: int) -> dict[str, _LinkedDoc]:
    """gazelka_ref → (kind, id, number, status) нашего документа: SENT + MATCHED.

    Сортировка по id → последняя запись побеждает (ручной матч поверх старой отправки).

    Мягко удалённый ПЕРЕЕЗД связь теряет (фильтр в ON-условии + требование, чтобы
    строка джойна материализовалась): иначе синк применил бы машину и авто-шип к
    удалённому документу, то есть списал бы сток по тому, чего уже нет.
    """
    rows = await db.execute(
        select(
            GazelkaOrder.gazelka_ref,
            GazelkaOrder.assembly_request_id,
            AssemblyRequest.number,
            AssemblyRequest.status,
            GazelkaOrder.stock_transfer_id,
            StockTransfer.number,
            StockTransfer.status,
        )
        .join(AssemblyRequest, AssemblyRequest.id == GazelkaOrder.assembly_request_id, isouter=True)
        .join(
            StockTransfer,
            and_(
                StockTransfer.id == GazelkaOrder.stock_transfer_id,
                StockTransfer.project_id == project_id,
                StockTransfer.is_deleted.is_(False),
            ),
            isouter=True,
        )
        .where(
            GazelkaOrder.project_id == project_id,
            GazelkaOrder.status.in_([GazelkaOrderStatus.SENT, GazelkaOrderStatus.MATCHED]),
            GazelkaOrder.gazelka_ref.isnot(None),
        )
        .order_by(GazelkaOrder.id)
    )
    out: dict[str, _LinkedDoc] = {}
    for ref, aid, anum, astatus, tid, tnum, tstatus in rows.all():
        if not ref:
            continue
        if aid is not None:
            out[str(ref)] = ("assembly", aid, anum, astatus)
        elif tid is not None and tnum is not None:
            out[str(ref)] = ("transfer", tid, tnum, tstatus)
    return out


def _row_from_plan(
    plan: dict,
    marketplaces: list[dict],
    linked: dict[str, _LinkedDoc],
    *,
    editable: bool,
    joins: dict[str, dict[str, dict]] | None = None,
) -> GazelkaOrderRow:
    gid = str(plan.get("id") or "")
    link = linked.get(gid)
    row = GazelkaOrderRow(
        gazelka_id=gid,
        status=str(plan.get("status") or ""),
        status_label=_status_label(plan.get("status")),
        application_date=_u(plan.get("application_date")),
        departure_date=_clean_date(plan.get("departure_date")),
        departure_time=_u(plan.get("departure_time")),
        departure_address=_u(plan.get("departure_address")),
        delivery_date=_clean_date(plan.get("delivery_date")),
        delivery_time=_u(plan.get("delivery_time")),
        delivery_address=_u(plan.get("delivery_address")),
        marketplace=_marketplace_name(plan.get("marketplace_id"), marketplaces),
        monomix=_MONO_LABELS.get(str(plan.get("monomix") or "")),
        pallets=_to_int(plan.get("pallets")),
        boxes=_to_int(plan.get("boxes")),
        weight=_u(plan.get("weight")),
        supply_id=_u(plan.get("supply_id")),
        rate=_u(plan.get("rate")),
        entity=_u(plan.get("entity")),
        notes=_u(plan.get("notes")),
        editable=editable,
        # Обобщённая связь (kind/id/number/status) — новый контракт, заполняется
        # для обоих типов документа.
        linked_kind=link[0] if link else None,
        linked_id=link[1] if link else None,
        linked_number=link[2] if link else None,
        linked_status=_doc_status_label(link[0], link[3]) if link else None,
        # Старые linked_assembly_* оставлены ТОЛЬКО для сборки: их читает готовый
        # UI, и положить в них id переезда значило бы дать ему открыть чужую
        # карточку по чужому id.
        linked_assembly_id=link[1] if link and link[0] == "assembly" else None,
        linked_assembly_number=link[2] if link and link[0] == "assembly" else None,
        linked_assembly_status=(
            _assembly_status_label(link[3]) if link and link[0] == "assembly" else None
        ),
    )
    if joins:
        route = joins["routes"].get(str(plan.get("route_id") or ""))
        if route:
            row.route_number = str(route.get("id") or "") or None
            row.route_date = _clean_date(route.get("date"))
            row.finish_time = _u(route.get("finish_time"))
            drv = joins["drivers"].get(str(route.get("driver_id") or ""))
            if drv:
                row.driver_name = _u(drv.get("name"))
                row.driver_phone = _u(drv.get("phone"))
                row.driver_passport = _u(drv.get("passport"))
            veh = joins["vehicles"].get(str(route.get("vehicle_id") or ""))
            if veh:
                parts = [_u(veh.get("vehicle_make")), _u(veh.get("vehicle_number"))]
                row.vehicle = " ".join(p for p in parts if p) or None
            car = joins["carriers"].get(str(route.get("carrier_id") or ""))
            if car:
                row.carrier = _u(car.get("organization"))
    return row


async def list_planned(db: AsyncSession, project_id: int) -> GazelkaOrderList:
    key = await _get_key(db, project_id)
    try:
        async with _client_from_key(key) as client:
            await client.authenticate()
            data = await client.fetch_planned()
    except GazelkaApiError as e:
        raise GazelkaServiceError(str(e), status_code=e.status_code) from e
    except (httpx.HTTPError, CircuitOpenError, ValueError) as e:
        raise GazelkaServiceError(f"Газелька недоступна: {e}", status_code=502) from e

    linked = await _linked_map(db, project_id)
    supply_idx = await _assembly_supply_index(db, project_id)
    mkts = data.get("marketplaces") or []
    rows = []
    for p in data.get("plans") or []:
        row = _row_from_plan(p, mkts, linked, editable=True)
        _attach_suggestion(row, p, supply_idx)
        rows.append(row)
    return GazelkaOrderList(items=rows, count=len(rows))


def _vehicle_assigned_ids(rows: list[GazelkaOrderRow]) -> set[int]:
    """Сборки, чьей заявке Газелька уже назначила машину (в маршруте есть ТС/водитель)."""
    return {
        row.linked_assembly_id
        for row in rows
        if row.linked_assembly_id is not None and (row.vehicle or row.driver_name)
    }


async def _promote_vehicle_assigned(db: AsyncSession, project_id: int, assembly_ids: set[int]) -> set[int]:
    """READY → VEHICLE_ASSIGNED для сборок, чьим заявкам Газелька назначила машину.

    Логистику связанных заявок ведёт агрегатор (ручной assign_vehicle заблокирован),
    поэтому статус «Машина назначена» зеркалим отсюда — с вкладки «Активные», где
    портал отдаёт маршрут с ТС/водителем. Реквизиты машины НЕ копируем (решение
    пользователя 17.07.2026) — только статус + история + момент назначения.
    Прочие статусы не трогаем: не READY — либо ещё не готова, либо уже уехала
    (авто-шип по приёмке WB умеет и READY, и VEHICLE_ASSIGNED). Вернувшийся id
    → set промоушнутых (для патча бейджей в уже построенных строках).
    """
    if not assembly_ids:
        return set()
    result = await db.execute(
        select(AssemblyRequest).where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.id.in_(assembly_ids),
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status == AssemblyStatus.READY.value,
        )
    )
    to_promote = result.scalars().all()
    if not to_promote:
        return set()
    from backend.services.assembly.status import _log_status_change

    for ar in to_promote:
        ar.status = AssemblyStatus.VEHICLE_ASSIGNED
        ar.vehicle_assigned_at = utcnow()
        await _log_status_change(
            db, project_id, ar.id, AssemblyStatus.READY, AssemblyStatus.VEHICLE_ASSIGNED,
            changed_by="gazelka", comment="Газелька: машина назначена",
        )
    promoted = {ar.id for ar in to_promote}
    await db.commit()
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")
    await invalidate_cache("reports:warehouse_need")
    logger.info("gazelka.vehicle_assigned_sync", project_id=project_id, assembly_ids=sorted(promoted))
    return promoted


async def list_active(db: AsyncSession, project_id: int) -> GazelkaOrderList:
    key = await _get_key(db, project_id)
    try:
        async with _client_from_key(key) as client:
            await client.authenticate()
            data = await client.fetch_active()
    except GazelkaApiError as e:
        raise GazelkaServiceError(str(e), status_code=e.status_code) from e
    except (httpx.HTTPError, CircuitOpenError, ValueError) as e:
        raise GazelkaServiceError(f"Газелька недоступна: {e}", status_code=502) from e

    linked = await _linked_map(db, project_id)
    supply_idx = await _assembly_supply_index(db, project_id)
    mkts = data.get("marketplaces") or []
    joins = {
        k: {str(r.get("id")): r for r in (data.get(k) or [])}
        for k in ("routes", "drivers", "vehicles", "carriers", "places")
    }
    rows = []
    for p in data.get("plans") or []:
        row = _row_from_plan(p, mkts, linked, editable=False, joins=joins)
        _attach_suggestion(row, p, supply_idx)
        rows.append(row)
    # Синк статуса из портала: у связанной заявки появилась машина → сборка
    # «Машина назначена» (бейдж в уже построенных строках патчим тут же).
    promoted = await _promote_vehicle_assigned(db, project_id, _vehicle_assigned_ids(rows))
    for row in rows:
        if row.linked_assembly_id in promoted:
            row.linked_assembly_status = _assembly_status_label(AssemblyStatus.VEHICLE_ASSIGNED.value)
    return GazelkaOrderList(items=rows, count=len(rows))


# ─── Фоновый синк статусов заявок (шедулер, каждые 15 мин) ───────────────────


def _split_driver_name(name: str | None) -> tuple[str | None, str | None]:
    """ФИО Газельки «Фамилия Имя [Отчество]» → (first, last). Порядок — как в кабинете WB."""
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    if not parts:
        return None, None
    first = parts[1] if len(parts) > 1 else None
    return first, parts[0]


def _parse_rate(v: object) -> Decimal | None:
    """Тариф Газельки → Decimal: убираем разделители тысяч (пробел/NBSP), запятую→точку."""
    s = (_u(v) or "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    return _dec_or_none(s)


@dataclass
class _GazelkaLogistics:
    """Реквизиты машины/водителя/тарифа из активной заявки портала (для зеркала в сборку)."""

    car_number: str | None
    car_model: str | None
    driver_first: str | None
    driver_last: str | None
    driver_phone: str | None
    carrier: str | None
    pickup_cost: Decimal | None
    delivery_date: date | None
    pickup_date: date | None
    has_vehicle: bool


def _extract_logistics(plan: dict, joins: dict[str, dict[str, dict]]) -> _GazelkaLogistics:
    """Достать реквизиты из плана + JOIN-справочников (route→driver/vehicle/carrier)."""
    route = joins["routes"].get(str(plan.get("route_id") or "")) or {}
    drv = joins["drivers"].get(str(route.get("driver_id") or "")) or {}
    veh = joins["vehicles"].get(str(route.get("vehicle_id") or "")) or {}
    car = joins["carriers"].get(str(route.get("carrier_id") or "")) or {}
    first, last = _split_driver_name(_u(drv.get("name")))
    car_number = _u(veh.get("vehicle_number"))
    car_model = _u(veh.get("vehicle_make"))
    return _GazelkaLogistics(
        car_number=car_number,
        car_model=car_model,
        driver_first=first,
        driver_last=last,
        driver_phone=_u(drv.get("phone")),
        carrier=_u(car.get("organization")),
        pickup_cost=_parse_rate(plan.get("rate")),
        delivery_date=_date_or_none(plan.get("delivery_date")),
        pickup_date=_date_or_none(route.get("date")) or _date_or_none(plan.get("departure_date")),
        has_vehicle=bool(car_number or car_model or first or last),
    )


async def _reconcile_active_order(
    db: AsyncSession,
    project_id: int,
    assembly_id: int,
    code: str,
    info: _GazelkaLogistics,
    stats: dict[str, int],
) -> None:
    """Синхронизировать ОДНУ связанную заявку: назначение машины / пропуск / отгрузка."""
    from backend.services import wb_supply_service
    from backend.services.assembly import status as assembly_status

    # 1. Газелька назначила машину (Принята в работу / В маршруте) → реквизиты в сборку,
    #    промоушн READY→VEHICLE_ASSIGNED, зеркало WB-пропуска.
    if info.has_vehicle and code in ("3", "31"):
        new_status = await assembly_status.apply_gazelka_logistics(
            db,
            project_id,
            assembly_id,
            car_number=info.car_number,
            car_model=info.car_model,
            driver_phone=info.driver_phone,
            driver_first_name=info.driver_first,
            driver_last_name=info.driver_last,
            carrier_name=info.carrier,
            pickup_cost=info.pickup_cost,
            delivery_date=info.delivery_date,
            pickup_date=info.pickup_date,
            promote=True,
        )
        if new_status == AssemblyStatus.VEHICLE_ASSIGNED.value:
            stats["assigned"] += 1
        # Занос пропуска в кабинет WB (best-effort; требует брони supply_id, иначе ждёт).
        try:
            if await wb_supply_service.try_autopush_pass_by_assembly(db, project_id, assembly_id):
                stats["passed"] += 1
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("gazelka.sync_states.pass_push_failed", project_id=project_id, assembly_id=assembly_id)

    # 2. «В маршруте» (код 31) → авто-шип: товар физически уехал. Отгрузка несёт газельный
    #    тариф как pickup_cost. Идемпотентно: уже SHIPPED → ValueError (глушим).
    if code == "31":
        try:
            await assembly_status.ship_request(db, project_id, assembly_id, allow_gazelka_ready=True)
            stats["shipped"] += 1
        except ValueError:
            pass

    # 3. Бэкфилл: сборки, отгруженные ДО фичи (авто-шип по приёмке WB из READY), ушли в снимок
    #    отгрузки без тарифа/перевозчика. Пока заявка видна в кабинете — дозаполняем пустой снимок
    #    (в листе оплаты не «—»). Идемпотентно: заполненный снимок не трогаем.
    await _backfill_cost(db, project_id, assembly_id, info, stats)


def _transfer_vehicle_payload(info: _GazelkaLogistics) -> TransferAssignVehicle | None:
    """Реквизиты агрегатора → тело назначения машины на переезд; None = назначать нечем.

    `TransferAssignVehicle` требует хотя бы один опознавательный признак
    (госномер / перевозчик / «логистику оказывает склад забора»). У портала
    бывает маршрут, где из ТС известна только марка или одно ФИО водителя, — с
    таким телом схема бросит ValidationError и утащит с собой авто-шип. Поэтому
    отсутствие признака — штатный отказ от назначения, а не ошибка синка.
    """
    if not (info.car_number or info.carrier):
        return None
    return TransferAssignVehicle(
        vehicle_info=info.car_number,
        vehicle_brand=info.car_model,
        driver_first_name=info.driver_first,
        driver_last_name=info.driver_last,
        driver_phone=info.driver_phone,
        carrier_name=info.carrier,
        pickup_cost=info.pickup_cost,
        pickup_date=info.pickup_date,
        delivery_date=info.delivery_date,
    )


async def _reconcile_active_transfer(
    db: AsyncSession,
    project_id: int,
    transfer_id: int,
    code: str,
    info: _GazelkaLogistics,
    stats: dict[str, int],
) -> None:
    """Синхронизировать ОДИН связанный переезд: машина от агрегатора → авто-шип.

    🔴 Ветки WB-пропуска здесь НЕТ и быть не может: переезд едет между НАШИМИ
    складами, на маркетплейс он не заезжает — пропускать его некуда.

    Авто-шип сужен до READY | VEHICLE_ASSIGNED: между отбором кандидата и этим
    вызовом лежат десятки секунд HTTP к порталу, и за это окно логист успевает
    снять машину или отправить переезд руками. Без сужения общий карв-аут
    списал бы сток второй раз.
    """
    from backend.services import warehouse_outbound

    if info.has_vehicle and code in ("3", "31"):
        payload = _transfer_vehicle_payload(info)
        if payload is None:
            logger.warning(
                "gazelka.sync_states.transfer_vehicle_unidentified",
                project_id=project_id,
                transfer_id=transfer_id,
            )
        else:
            await warehouse_outbound.apply_gazelka_transfer_logistics(db, project_id, transfer_id, payload)
            stats["assigned"] += 1

    if code == "31":
        try:
            await warehouse_outbound.send_transfer(
                db,
                project_id,
                transfer_id,
                allowed_from=frozenset({TransferStatus.READY, TransferStatus.VEHICLE_ASSIGNED}),
            )
            stats["shipped"] += 1
        except ValueError:
            pass  # уже уехал / состав пуст — идемпотентно, как у сборки

    await _backfill_transfer_cost(db, project_id, transfer_id, info, stats)


async def _backfill_transfer_cost(
    db: AsyncSession,
    project_id: int,
    transfer_id: int,
    info: _GazelkaLogistics,
    stats: dict[str, int],
) -> None:
    """Близнец `assembly.status.backfill_gazelka_shipment_cost` — по забору ПЕРЕЕЗДА.

    Переезды, уехавшие до этой фичи (машину им назначали руками или не назначали
    вовсе), ушли в `OutboundShipment` без тарифа и перевозчика: в «Оплатах» и в
    отчёте логистики переездов такая строка — сплошное «—», а добрать её задним
    числом нечем. Пока заявка видна в кабинете Газельки, берём её ставку.

    Идемпотентно: заполненное НЕ перетираем (там может лежать ручной ввод
    логиста, который агрегатор знать не обязан). Забор — носитель денег, поэтому
    ставку зеркалим и в сам переезд, если там пусто.
    """
    if info.pickup_cost is None:
        return
    try:
        if await _apply_transfer_cost(db, project_id, transfer_id, info.pickup_cost, info.carrier):
            stats["cost_backfilled"] += 1
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.warning(
            "gazelka.sync_states.transfer_backfill_failed", project_id=project_id, transfer_id=transfer_id
        )


async def _apply_transfer_cost(
    db: AsyncSession,
    project_id: int,
    transfer_id: int,
    pickup_cost: Decimal,
    carrier_name: str | None,
) -> bool:
    """Дозаполнить ПУСТОЙ снимок забора переезда тарифом/перевозчиком. True = изменили."""
    from backend.services.assembly.status import _resolve_carrier_by_name

    transfer = await _load_transfer(db, project_id, transfer_id)
    if transfer is None or TransferStatus(transfer.status) not in (
        TransferStatus.SHIPPED, TransferStatus.DELIVERED, TransferStatus.RETURNED, TransferStatus.CLOSED,
    ):
        return False  # не уехал — логистику ведёт штатный путь назначения машины
    shipment = (
        await db.execute(
            select(OutboundShipment).where(
                OutboundShipment.project_id == project_id,
                OutboundShipment.stock_transfer_id == transfer_id,
                OutboundShipment.is_deleted.is_(False),
            )
        )
    ).scalar_one_or_none()
    if shipment is None:
        return False  # забора нет (переезд отправляли до появления носителя денег)

    changed = False
    if shipment.pickup_cost is None:
        shipment.pickup_cost = pickup_cost
        if transfer.pickup_cost is None:
            transfer.pickup_cost = pickup_cost
        changed = True
    if shipment.counterparty_id is None and carrier_name:
        cp_id = await _resolve_carrier_by_name(db, project_id, carrier_name)
        if cp_id is not None:
            shipment.counterparty_id = cp_id
            if transfer.counterparty_id is None:
                transfer.counterparty_id = cp_id
            changed = True

    if changed:
        await db.commit()
        # Забор переезда — строка «Оплат» и слагаемое сверки счетов ФФ; тариф,
        # доехавший до него, обязан появиться в отчётах сразу (iron rule 7).
        await invalidate_cache("reports:balance")
        await invalidate_cache("ff_billing:invoices")
    return changed


async def _backfill_cost(
    db: AsyncSession,
    project_id: int,
    assembly_id: int,
    info: _GazelkaLogistics,
    stats: dict[str, int],
) -> None:
    """Дозаполнить пустой снимок отгрузки тарифом/перевозчиком заявки (best-effort)."""
    if info.pickup_cost is None:
        return
    from backend.services.assembly import status as assembly_status

    try:
        if await assembly_status.backfill_gazelka_shipment_cost(
            db, project_id, assembly_id, info.pickup_cost, info.carrier
        ):
            stats["cost_backfilled"] += 1
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.warning("gazelka.sync_states.backfill_failed", project_id=project_id, assembly_id=assembly_id)


async def _auto_link_unmatched(
    db: AsyncSession,
    project_id: int,
    plans: list[dict],
    supply_idx: dict[str, tuple[int, str]],
    linked: dict[str, _LinkedDoc],
) -> int:
    """Авто-связать заявки портала со сборкой по № поставки WB — без клика «Сопоставить».

    Логист заводит заявки прямо в кабинете Газельки (SENT-записей нет, всё матчилось руками).
    Здесь связываем автоматически, но ТОЛЬКО при ОДНОЗНАЧНОМ совпадении: № поставки заявки
    указывает ровно на одну нашу сборку, и та ещё не связана с другой заявкой (не переклеиваем).
    Пишем MATCHED (как ручной матч) → дальше синк подхватит машину/пропуск/отгрузку.
    Мутирует ``linked`` на месте (добавляет новые связи для последующего reconcile).

    Переездов это не касается: их дискриминатор — маркер «TR-№» в notes, и он
    попадает туда ТОЛЬКО нашей отправкой (`send_transfer_order`), то есть связь
    уже есть. Автосвязывать переезд по маркеру, введённому человеком в кабинете,
    значило бы доверить чужому тексту право двигать наш сток авто-шипом.
    """
    if not supply_idx:
        return 0
    linked_assemblies = {lid for kind, lid, _num, _st in linked.values() if kind == "assembly"}
    created = 0
    for plan in plans:
        gid = str(plan.get("id") or "")
        if not gid or gid in linked:
            continue
        hit: tuple[int, str] | None = None
        for num in _extract_wb_numbers(plan.get("supply_id")):
            cand = supply_idx.get(num)
            if cand:
                hit = cand
                break
        if hit is None:
            continue
        aid, number = hit
        if aid in linked_assemblies:
            continue  # сборка уже связана с другой заявкой Газельки — не переклеиваем
        db.add(
            GazelkaOrder(
                project_id=project_id,
                assembly_request_id=aid,
                status=GazelkaOrderStatus.MATCHED,
                gazelka_ref=gid,
                payload={"_autolink": True},
                created_by="gazelka",
            )
        )
        linked[gid] = ("assembly", aid, number, None)
        linked_assemblies.add(aid)
        created += 1
    if created:
        await db.commit()
        logger.info("gazelka.sync_states.autolinked", project_id=project_id, count=created)
    return created


async def sync_gazelka_states(db: AsyncSession, project_id: int) -> dict[str, int]:
    """Фоновый синк заявок Газельки (шедулер). Best-effort, без исключений наружу.

    • Авто-связь несвязанных заявок (planned+active) со сборкой по № поставки WB.
    • Для СВЯЗАННЫХ заявок из кабинета перевозчика:
      – назначена машина/водитель → сборка READY→VEHICLE_ASSIGNED + реквизиты + WB-пропуск
        (авто-занос в кабинет, как при ручном назначении машины);
      – статус «В маршруте» (код 31) → авто-шип сборки (тариф → стоимость забора).
    • То же для ПЕРЕЕЗДА, но БЕЗ WB-пропуска (см. `_reconcile_active_transfer`).
    Сбой одной заявки не роняет синк остальных.
    """
    stats = {"autolinked": 0, "assigned": 0, "passed": 0, "shipped": 0, "cost_backfilled": 0}
    key = await _get_key_or_none(db, project_id)
    if key is None:
        return stats
    try:
        async with _client_from_key(key) as client:
            await client.authenticate()
            data = await client.fetch_active()
            planned = await client.fetch_planned()
            completed = await client.fetch_completed()
    except (GazelkaApiError, httpx.HTTPError, CircuitOpenError, ValueError) as e:
        logger.warning("gazelka.sync_states.unavailable", project_id=project_id, error=str(e))
        return stats

    linked = await _linked_map(db, project_id)
    # Авто-связь по № поставки: запланированные + активные + завершённые (заведённые в кабинете руками).
    supply_idx = await _assembly_supply_index(db, project_id)
    all_plans = (planned.get("plans") or []) + (data.get("plans") or []) + (completed.get("plans") or [])
    stats["autolinked"] = await _auto_link_unmatched(db, project_id, all_plans, supply_idx, linked)

    joins = {
        k: {str(r.get("id")): r for r in (data.get(k) or [])}
        for k in ("routes", "drivers", "vehicles", "carriers")
    }
    for plan in data.get("plans") or []:
        lk = linked.get(str(plan.get("id") or ""))
        if not lk:
            continue
        kind, doc_id = lk[0], lk[1]
        code = str(plan.get("status") or "")
        info = _extract_logistics(plan, joins)
        try:
            if kind == "transfer":
                await _reconcile_active_transfer(db, project_id, doc_id, code, info, stats)
            else:
                await _reconcile_active_order(db, project_id, doc_id, code, info, stats)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning(
                "gazelka.sync_states.reconcile_failed",
                project_id=project_id,
                kind=kind,
                doc_id=doc_id,
                gazelka_id=str(plan.get("id") or ""),
            )

    # Бэкфилл стоимости из ЗАВЕРШЁННЫХ: заявки, ушедшие из «Активных» (отгружены до фичи),
    # несут тариф на странице «Завершённые» → дозаполняем пустой снимок отгрузки.
    completed_joins = {
        k: {str(r.get("id")): r for r in (completed.get(k) or [])}
        for k in ("routes", "drivers", "vehicles", "carriers")
    }
    for plan in completed.get("plans") or []:
        lk = linked.get(str(plan.get("id") or ""))
        if not lk:
            continue
        info = _extract_logistics(plan, completed_joins)
        try:
            if lk[0] == "transfer":
                await _backfill_transfer_cost(db, project_id, lk[1], info, stats)
            else:
                await _backfill_cost(db, project_id, lk[1], info, stats)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning(
                "gazelka.sync_states.completed_backfill_failed",
                project_id=project_id,
                gazelka_id=str(plan.get("id") or ""),
            )

    logger.info("gazelka.sync_states", project_id=project_id, **stats)
    return stats


async def list_completed(db: AsyncSession, project_id: int) -> GazelkaOrderList:
    """Завершённые заявки из кабинета Газельки: ``/customer/orders?completed=1``.

    Тот же встроенный JSON, что у «Активных» (водитель/ТС/перевозчик/тариф + статус),
    с пометкой связи с нашей сборкой. У портала это отдельная страница «Завершённые заявки».
    """
    key = await _get_key(db, project_id)
    try:
        async with _client_from_key(key) as client:
            await client.authenticate()
            data = await client.fetch_completed()
    except GazelkaApiError as e:
        raise GazelkaServiceError(str(e), status_code=e.status_code) from e
    except (httpx.HTTPError, CircuitOpenError, ValueError) as e:
        raise GazelkaServiceError(f"Газелька недоступна: {e}", status_code=502) from e

    linked = await _linked_map(db, project_id)
    supply_idx = await _assembly_supply_index(db, project_id)
    mkts = data.get("marketplaces") or []
    joins = {
        k: {str(r.get("id")): r for r in (data.get(k) or [])}
        for k in ("routes", "drivers", "vehicles", "carriers", "places")
    }
    rows = []
    for p in data.get("plans") or []:
        row = _row_from_plan(p, mkts, linked, editable=False, joins=joins)
        _attach_suggestion(row, p, supply_idx)
        rows.append(row)
    return GazelkaOrderList(items=rows, count=len(rows))


async def get_ttn(db: AsyncSession, project_id: int, plan_id: str) -> tuple[bytes, str]:
    key = await _get_key(db, project_id)
    try:
        async with _client_from_key(key) as client:
            await client.authenticate()
            return await client.fetch_ttn(plan_id)
    except GazelkaApiError as e:
        raise GazelkaServiceError(str(e), status_code=e.status_code) from e
    except (httpx.HTTPError, CircuitOpenError, ValueError) as e:
        raise GazelkaServiceError(f"Газелька недоступна: {e}", status_code=502) from e


# ─── Редактирование заявки ───────────────────────────────────────────────────


def _values_from_plan(p: dict) -> GazelkaSendRequest:
    """Текущие значения заявки портала → форма (для предзаполнения редактирования)."""
    mid = str(p.get("marketplace_id") or "")
    return GazelkaSendRequest(
        entity_id=str(p.get("entity_id") or "") or "0",
        payer_id=str(p.get("payer_id") or "") or "0",
        price_id=str(p.get("price_id") or "") or "0",
        is_marketplace="yes" if mid not in ("", "0") else "no",
        marketplace_id=mid if mid not in ("", "0") else None,
        supply_id=_u(p.get("supply_id")),
        delivery_address=_u(p.get("delivery_address")),
        delivery_address_x2=_u(p.get("delivery_address")),
        departure_date=_date_or_none(p.get("departure_date")),
        delivery_date=_date_or_none(p.get("delivery_date")),
        delivery_time=_u(p.get("delivery_time")),
        daily_delivery_timeslot=None,
        delivery_contact=_u(p.get("delivery_contact")),
        customer_phone=_u(p.get("customer_phone")),
        monomix=str(p.get("monomix") or "") or None,
        pallets=_to_int(p.get("pallets")),
        boxes=_to_int(p.get("boxes")),
        weight=_dec_or_none(p.get("weight")),
        weight2=None,
        volume=_dec_or_none(p.get("volume")),
        length=_to_int(p.get("length")) or 60,
        height=_to_int(p.get("height")) or 40,
        width=_to_int(p.get("width")) or 40,
        palleting=str(p.get("palleting") or "f") == "t",
        notes=_u(p.get("notes")),
    )


async def build_edit_draft(db: AsyncSession, project_id: int, plan_id: str) -> GazelkaEditDraft:
    key = await _get_key(db, project_id)
    try:
        async with _client_from_key(key) as client:
            await client.authenticate()
            planned = await client.fetch_planned()
            form = await client.fetch_edit_form(plan_id)
    except GazelkaApiError as e:
        raise GazelkaServiceError(str(e), status_code=e.status_code) from e
    except (httpx.HTTPError, CircuitOpenError, ValueError) as e:
        raise GazelkaServiceError(f"Газелька недоступна: {e}", status_code=502) from e

    plan = next((p for p in planned.get("plans") or [] if str(p.get("id")) == str(plan_id)), None)
    if plan is None:
        raise GazelkaServiceError("Заявка не найдена среди запланированных", status_code=404)
    return GazelkaEditDraft(
        gazelka_id=str(plan_id),
        options=_options_from_form(form),
        values=_values_from_plan(plan),
    )


async def save_edit(
    db: AsyncSession,
    project_id: int,
    plan_id: str,
    req: GazelkaSendRequest,
    actor: str | None = None,
) -> GazelkaSendResult:
    key = await _get_key(db, project_id)
    login = (key.config or {}).get("login") or ""

    def _scrub(text: str) -> str:
        return text.replace(login, "[login]") if login else text

    payload = _payload_from_request(req)
    status = GazelkaOrderStatus.FAILED
    message = ""
    excerpt: str | None = None
    error: str | None = None

    try:
        async with _client_from_key(key) as client:
            await client.authenticate()
            form = await client.fetch_edit_form(plan_id)
            _validate_schedule(form, req)
            result = await client.update_order(plan_id, payload, form=form)
        status = GazelkaOrderStatus.SENT if result.ok else GazelkaOrderStatus.UNCERTAIN
        message = result.message
        excerpt = result.excerpt
    except GazelkaServiceError:
        raise
    except GazelkaApiError as e:
        message = _scrub(str(e))
        error = message
    except (httpx.HTTPError, CircuitOpenError, ValueError) as e:
        message = _scrub(f"Ошибка связи с Газелькой: {e}. Сверьте в кабинете.")
        error = message

    # Audit-строку правки вешаем на ТОТ ЖЕ документ, что и связь заказа: иначе
    # правка заявки переезда легла бы в лог со ссылкой в никуда.
    link = (await _linked_map(db, project_id)).get(str(plan_id))
    order = GazelkaOrder(
        project_id=project_id,
        assembly_request_id=link[1] if link and link[0] == "assembly" else None,
        stock_transfer_id=link[1] if link and link[0] == "transfer" else None,
        status=status,
        gazelka_ref=str(plan_id),
        payload={**payload, "_edit": True},
        response_excerpt=excerpt,
        error=error,
        created_by=actor,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)

    return GazelkaSendResult(
        ok=(status == GazelkaOrderStatus.SENT),
        ref=str(plan_id),
        message=message or "Изменения сохранены",
        gazelka_order_id=order.id,
    )


# ─── Матчинг существующих заявок портала ↔ наши сборки ───────────────────────

_WB_NUM_RE = re.compile(r"\d{6,}")  # № поставки WB — длинные числовые серии


def _extract_wb_numbers(supply_id: object) -> list[str]:
    """Длинные числовые серии из supply_id портала (там бывает «Казань 40299154 PVB-…»)."""
    s = _html.unescape(supply_id) if isinstance(supply_id, str) else ""
    return _WB_NUM_RE.findall(s)


async def _assembly_supply_index(db: AsyncSession, project_id: int) -> dict[str, tuple[int, str]]:
    """№ поставки WB → (assembly_id, number) — для авто-подсказки матчинга."""
    rows = await db.execute(
        select(AssemblyRequest.id, AssemblyRequest.number, WbFboSupply.wb_supply_id)
        .join(WbFboSupply, WbFboSupply.id == AssemblyRequest.wb_fbo_supply_id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            WbFboSupply.wb_supply_id.isnot(None),
        )
    )
    out: dict[str, tuple[int, str]] = {}
    for aid, number, sup in rows.all():
        if sup:
            out[str(sup)] = (aid, number)
    return out


def _attach_suggestion(row: GazelkaOrderRow, plan: dict, supply_idx: dict[str, tuple[int, str]]) -> None:
    """Если строка не связана — подсказать сборку по № поставки WB из supply_id.

    Подсказка всегда сборочная: № поставки бывает только у неё. Переезд узнаётся
    маркером «TR-№», но маркер в кабинете может набрать и человек, а подсказка —
    это заготовка для клика «Сопоставить», за который отвечает логист.
    """
    if row.linked_id is not None:
        return
    for num in _extract_wb_numbers(plan.get("supply_id")):
        hit = supply_idx.get(num)
        if hit:
            row.suggested_assembly_id, row.suggested_assembly_number = hit
            row.suggested_kind, row.suggested_id, row.suggested_number = "assembly", hit[0], hit[1]
            return


async def match_order(
    db: AsyncSession, project_id: int, plan_id: str, req: GazelkaMatchRequest, actor: str | None = None
) -> GazelkaMatchResult:
    """Связать существующую заявку портала с нашим документом (ручной матч).

    Ровно одна из ссылок непуста — это гарантирует валидатор `GazelkaMatchRequest`
    (и дублирует CHECK в БД).
    """
    await _get_key(db, project_id)  # интеграция должна быть настроена
    kind: GazelkaLinkKind = "transfer" if req.transfer_id is not None else "assembly"
    if kind == "transfer":
        tr = await _load_transfer(db, project_id, req.transfer_id or 0)
        if tr is None:
            raise GazelkaServiceError("Переезд не найден", status_code=404)
        doc_id, doc_number = tr.id, tr.number
    else:
        ar = await _load_assembly(db, project_id, req.assembly_id or 0)
        if ar is None:
            raise GazelkaServiceError("Сборка не найдена", status_code=404)
        doc_id, doc_number = ar.id, ar.number

    # Одна ручная связь на заявку портала — убираем прежние MATCHED для этого gazelka_ref
    existing = await db.execute(
        select(GazelkaOrder).where(
            GazelkaOrder.project_id == project_id,
            GazelkaOrder.gazelka_ref == str(plan_id),
            GazelkaOrder.status == GazelkaOrderStatus.MATCHED,
        )
    )
    for old in existing.scalars():
        await db.delete(old)  # no-soft-delete-check: GazelkaOrder — audit/link без SoftDeleteMixin

    db.add(
        GazelkaOrder(
            project_id=project_id,
            assembly_request_id=doc_id if kind == "assembly" else None,
            stock_transfer_id=doc_id if kind == "transfer" else None,
            status=GazelkaOrderStatus.MATCHED,
            gazelka_ref=str(plan_id),
            payload={"_match": True},
            created_by=actor,
        )
    )
    await db.commit()
    logger.info("gazelka.match", project_id=project_id, gazelka_id=plan_id, kind=kind, doc_id=doc_id)
    return GazelkaMatchResult(
        ok=True,
        # Старую пару отдаём ТОЛЬКО для сборки — её читает готовый UI и ведёт по
        # ней на карточку заявки; id переезда там открыл бы чужой документ.
        linked_assembly_id=doc_id if kind == "assembly" else None,
        linked_assembly_number=doc_number if kind == "assembly" else None,
        linked_kind=kind,
        linked_id=doc_id,
        linked_number=doc_number,
    )


async def unmatch_order(db: AsyncSession, project_id: int, plan_id: str) -> GazelkaMatchResult:
    """Снять ручную связь заявки портала со сборкой (SENT-записи не трогаем)."""
    rows = await db.execute(
        select(GazelkaOrder).where(
            GazelkaOrder.project_id == project_id,
            GazelkaOrder.gazelka_ref == str(plan_id),
            GazelkaOrder.status == GazelkaOrderStatus.MATCHED,
        )
    )
    for old in rows.scalars():
        await db.delete(old)  # no-soft-delete-check: GazelkaOrder — audit/link без SoftDeleteMixin
    await db.commit()
    return GazelkaMatchResult(ok=True)


async def list_match_candidates(
    db: AsyncSession,
    project_id: int,
    search: str | None = None,
    limit: int = 50,
    kind: GazelkaLinkKind = "assembly",
) -> list[GazelkaMatchCandidate]:
    """Наши документы — кандидаты на ручное сопоставление с заявкой портала.

    `kind` выбирает список: сборки (поиск по номеру / № поставки) либо переезды
    (поиск по номеру). Дефолт — сборки: старые клиенты параметра не знают.
    """
    linked = await _linked_map(db, project_id)
    # Ключ занятости — ПАРА (тип, id): без типа переезд №55 «занимал» бы сборку №55.
    linked_to_gazelka = {(lkind, lid): gref for gref, (lkind, lid, _n, _s) in linked.items()}

    if kind == "transfer":
        return await _transfer_match_candidates(db, project_id, search, limit, linked_to_gazelka)

    query = (
        select(
            AssemblyRequest.id,
            AssemblyRequest.number,
            Warehouse.name,
            WbFboSupply.wb_supply_id,
            AssemblyRequest.delivery_date,
            AssemblyRequest.pallets_count,
            AssemblyRequest.status,
        )
        .join(Warehouse, Warehouse.id == AssemblyRequest.warehouse_id, isouter=True)
        .join(WbFboSupply, WbFboSupply.id == AssemblyRequest.wb_fbo_supply_id, isouter=True)
        .where(AssemblyRequest.project_id == project_id, AssemblyRequest.is_deleted.is_(False))
        .order_by(AssemblyRequest.id.desc())
        .limit(limit)
    )
    if search:
        esc = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"
        query = query.where(or_(AssemblyRequest.number.ilike(like), WbFboSupply.wb_supply_id.ilike(like)))

    rows = await db.execute(query)
    return [
        GazelkaMatchCandidate(
            kind="assembly",
            assembly_id=aid,
            number=number,
            warehouse_name=wh,
            wb_supply_id=sup,
            delivery_date=ddate,
            pallets_count=pallets,
            status=status,
            already_linked_to=linked_to_gazelka.get(("assembly", aid)),
        )
        for aid, number, wh, sup, ddate, pallets, status in rows.all()
    ]


async def _transfer_match_candidates(
    db: AsyncSession,
    project_id: int,
    search: str | None,
    limit: int,
    linked_to_gazelka: dict[tuple[str, int], str],
) -> list[GazelkaMatchCandidate]:
    """Переезды-кандидаты. `assembly_id` несёт id ПЕРЕЕЗДА — так задан контракт схемы.

    Складом показываем МАРШРУТ «источник → получатель»: у переезда одного склада
    мало, а логист сопоставляет заказ портала именно по направлению.
    Гейта по складу Газельки тут намеренно нет: заказ в кабинете уже существует,
    и отфильтровать половину списка значит спрятать от логиста ровно тот переезд,
    который он ищет.
    """
    src = aliased(Warehouse)
    dst = aliased(Warehouse)
    query = (
        select(
            StockTransfer.id,
            StockTransfer.number,
            src.name,
            dst.name,
            StockTransfer.delivery_date,
            StockTransfer.pallets_count,
            StockTransfer.status,
        )
        .join(src, src.id == StockTransfer.from_warehouse_id, isouter=True)
        .join(dst, dst.id == StockTransfer.to_warehouse_id, isouter=True)
        .where(StockTransfer.project_id == project_id, StockTransfer.is_deleted.is_(False))
        .order_by(StockTransfer.id.desc())
        .limit(limit)
    )
    if search:
        esc = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(StockTransfer.number.ilike(f"%{esc}%"))

    rows = await db.execute(query)
    return [
        GazelkaMatchCandidate(
            kind="transfer",
            assembly_id=tid,
            number=number,
            warehouse_name=" → ".join(p for p in (from_name, to_name) if p) or None,
            wb_supply_id=None,  # поставки у переезда нет и быть не может
            delivery_date=ddate,
            pallets_count=pallets,
            status=status,
            already_linked_to=linked_to_gazelka.get(("transfer", tid)),
        )
        for tid, number, from_name, to_name, ddate, pallets, status in rows.all()
    ]
