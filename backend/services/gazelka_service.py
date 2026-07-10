# ruff: noqa: RUF002, RUF003
"""
Service: Gazelka (gazelka.space) — передача заявки логиста перевозчику.

Источник — ``AssemblyRequest`` (готовая сборка, склад «Натали»). Креды интеграции
лежат в ``IntegrationKey`` (service="gazelka"): ``encrypted_key`` = Fernet-пароль,
``config={"login", "customer_id", "host"?}``, ``warehouse_id`` = склад Газельки
(гейт: кнопку показываем только для отгрузок с него).

build_draft  — логин + снятие справочников ИХ формы + предзаполнение из сборки.
send_order   — РЕАЛЬНОЕ создание заявки во внешнем сервисе (необратимо), пишет
               audit-строку ``GazelkaOrder`` с исходом и выдержкой ответа.
"""

import html as _html
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import httpx
import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.integrations.gazelka_client import (
    BASE_URL,
    ApplyForm,
    DeliveryPlace,
    GazelkaApiError,
    GazelkaClient,
    SchedulePlan,
)
from backend.integrations.resilience import CircuitOpenError
from backend.models import GazelkaOrder, GazelkaOrderStatus, IntegrationKey, Warehouse
from backend.models.assembly import AssemblyRequest
from backend.models.wb_fbo import WbFboSupply
from backend.schemas.gazelka import (
    GazelkaConfigResponse,
    GazelkaDraftResponse,
    GazelkaEditDraft,
    GazelkaFormOptions,
    GazelkaMatchCandidate,
    GazelkaMatchResult,
    GazelkaOrderList,
    GazelkaOrderRow,
    GazelkaPrefill,
    GazelkaSchedulePlan,
    GazelkaSelectOption,
    GazelkaSendRequest,
    GazelkaSendResult,
)
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
    return GazelkaConfigResponse(configured=True, warehouse_id=key.warehouse_id, warehouse_name=wh_name)


# ─── Загрузка сборки ─────────────────────────────────────────────────────────


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


async def _last_sent(db: AsyncSession, project_id: int, assembly_id: int) -> tuple[bool, str | None]:
    row = await db.execute(
        select(GazelkaOrder)
        .where(
            GazelkaOrder.project_id == project_id,
            GazelkaOrder.assembly_request_id == assembly_id,
            GazelkaOrder.status == GazelkaOrderStatus.SENT,
        )
        .order_by(GazelkaOrder.created_at.desc())
        .limit(1)
    )
    order = row.scalar_one_or_none()
    if order is None:
        return False, None
    return True, order.gazelka_ref


# ─── Draft (диалог) ──────────────────────────────────────────────────────────


async def build_draft(db: AsyncSession, project_id: int, assembly_id: int) -> GazelkaDraftResponse:
    key = await _get_key(db, project_id)
    ar = await _load_assembly(db, project_id, assembly_id)
    if ar is None:
        raise GazelkaServiceError("Сборка не найдена", status_code=404)

    eligible = key.warehouse_id is not None and ar.warehouse_id == key.warehouse_id

    try:
        async with _client_from_key(key) as client:
            await client.authenticate()
            form = await client.fetch_apply_form()
    except GazelkaApiError as e:
        raise GazelkaServiceError(str(e), status_code=e.status_code) from e
    except (httpx.HTTPError, CircuitOpenError, ValueError) as e:
        raise GazelkaServiceError(f"Газелька недоступна: {e}", status_code=502) from e

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
                "Заявка для этой сборки уже отправлена в Газельку. Подтвердите повторную отправку.",
                status_code=409,
            )

    login = (key.config or {}).get("login") or ""

    def _scrub(text: str) -> str:
        return text.replace(login, "[login]") if login else text

    payload = _payload_from_request(req)
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
            result = await client.create_order(payload, form=form)
        status = GazelkaOrderStatus.SENT if result.ok else GazelkaOrderStatus.UNCERTAIN
        ref = result.ref
        message = result.message
        excerpt = result.excerpt
    except GazelkaServiceError:
        raise  # недопустимые даты/склад — заявку не создавали, audit-строка не нужна
    except GazelkaApiError as e:
        message = _scrub(str(e))
        error = message
    except (httpx.HTTPError, CircuitOpenError, ValueError) as e:
        # Сбой во время/после POST — заявка МОГЛА создаться. Не ретраим, просим сверить.
        message = _scrub(f"Ошибка связи с Газелькой: {e}. Сверьте в кабинете перед повтором.")
        error = message

    order = GazelkaOrder(
        project_id=project_id,
        assembly_request_id=ar.id,
        status=status,
        gazelka_ref=ref,
        payload=payload,
        response_excerpt=excerpt,
        error=error,
        created_by=actor,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)

    logger.info(
        "gazelka.send",
        project_id=project_id,
        assembly_id=assembly_id,
        status=status,
        gazelka_ref=ref,
    )
    return GazelkaSendResult(
        ok=(status == GazelkaOrderStatus.SENT),
        ref=ref,
        message=message,
        gazelka_order_id=order.id,
    )


# ─── Списки заявок из портала (read) ─────────────────────────────────────────

# Коды статусов портала (best-effort; неизвестные показываем как «Статус N»)
_STATUS_LABELS = {"2": "Запланирована", "3": "Принята в работу", "31": "В маршруте"}
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


async def _linked_map(db: AsyncSession, project_id: int) -> dict[str, tuple[int, str | None, str | None]]:
    """gazelka_ref → (assembly_id, assembly_number, assembly_status): SENT + MATCHED.

    Сортировка по id → последняя запись побеждает (ручной матч поверх старой отправки).
    """
    rows = await db.execute(
        select(
            GazelkaOrder.gazelka_ref,
            GazelkaOrder.assembly_request_id,
            AssemblyRequest.number,
            AssemblyRequest.status,
        )
        .join(AssemblyRequest, AssemblyRequest.id == GazelkaOrder.assembly_request_id, isouter=True)
        .where(
            GazelkaOrder.project_id == project_id,
            GazelkaOrder.status.in_([GazelkaOrderStatus.SENT, GazelkaOrderStatus.MATCHED]),
            GazelkaOrder.gazelka_ref.isnot(None),
        )
        .order_by(GazelkaOrder.id)
    )
    out: dict[str, tuple[int, str | None, str | None]] = {}
    for ref, aid, number, status in rows.all():
        if ref and aid is not None:
            out[str(ref)] = (aid, number, status)
    return out


def _row_from_plan(
    plan: dict,
    marketplaces: list[dict],
    linked: dict[str, tuple[int, str | None, str | None]],
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
        linked_assembly_id=link[0] if link else None,
        linked_assembly_number=link[1] if link else None,
        linked_assembly_status=_assembly_status_label(link[2]) if link else None,
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

    linked = await _linked_map(db, project_id)
    aid = (linked.get(str(plan_id)) or (None, None))[0]
    order = GazelkaOrder(
        project_id=project_id,
        assembly_request_id=aid,
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
    """Если строка не связана — подсказать сборку по № поставки WB из supply_id."""
    if row.linked_assembly_id is not None:
        return
    for num in _extract_wb_numbers(plan.get("supply_id")):
        hit = supply_idx.get(num)
        if hit:
            row.suggested_assembly_id, row.suggested_assembly_number = hit
            return


async def match_order(
    db: AsyncSession, project_id: int, plan_id: str, assembly_id: int, actor: str | None = None
) -> GazelkaMatchResult:
    """Связать существующую заявку портала с нашей сборкой (ручной матч)."""
    await _get_key(db, project_id)  # интеграция должна быть настроена
    ar = await _load_assembly(db, project_id, assembly_id)
    if ar is None:
        raise GazelkaServiceError("Сборка не найдена", status_code=404)

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
            assembly_request_id=ar.id,
            status=GazelkaOrderStatus.MATCHED,
            gazelka_ref=str(plan_id),
            payload={"_match": True},
            created_by=actor,
        )
    )
    await db.commit()
    logger.info("gazelka.match", project_id=project_id, gazelka_id=plan_id, assembly_id=ar.id)
    return GazelkaMatchResult(ok=True, linked_assembly_id=ar.id, linked_assembly_number=ar.number)


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
    db: AsyncSession, project_id: int, search: str | None = None, limit: int = 50
) -> list[GazelkaMatchCandidate]:
    """Наши сборки — кандидаты на ручное сопоставление (поиск по номеру/№ поставки)."""
    linked = await _linked_map(db, project_id)
    assembly_to_gazelka: dict[int, str] = {}
    for gref, (aid, _num, _status) in linked.items():
        if aid is not None:
            assembly_to_gazelka[aid] = gref

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
            assembly_id=aid,
            number=number,
            warehouse_name=wh,
            wb_supply_id=sup,
            delivery_date=ddate,
            pallets_count=pallets,
            status=status,
            already_linked_to=assembly_to_gazelka.get(aid),
        )
        for aid, number, wh, sup, ddate, pallets, status in rows.all()
    ]
