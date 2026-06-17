# ruff: noqa: RUF001, RUF002, RUF003, E712
"""
Service: fulfillment — интеграция с внешними фулфилментами (skladbot, wmscelicom, migfull).

Слой read-only зеркала: остатки (FulfillmentStock, полная замена при синке)
и заявки (FulfillmentRequest, UPSERT с сохранением ручных связей).
Документный контур (WarehouseStock / StockMovement) НЕ трогаем.

Провайдеры различаются формой API; внутри сервиса всё сводится к
нормализованным dict'ам (см. _apply_stocks / _apply_requests), дальше
путь общий. Деталка заявки: skladbot — живой HTTP-вызов, wmscelicom —
из raw зеркала (by-id эндпоинта у провайдера нет, состав приходит в списке),
migfull — сборки из raw (planned/shipped_lines приходят в списке целиком,
сверено с *_lines_count живьём), приёмки — живые lines/incoming+received.

Синк дополнительно: обогащает зеркало skladbot живой деталкой (total_qty /
dest_warehouse — списочный метод их не отдаёт) и автоматически переводит
связанные заявки на сборку IN_PROGRESS → READY, когда стадия ФФ говорит
«груз собран» (см. _assembly_ready_signal).
"""

import html
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import and_, delete, exists, func, select, text, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.cache import invalidate_cache
from backend.integrations.migfull_client import MigfullApiError, MigfullClient, normalize_tenant_guid
from backend.integrations.resilience import CircuitOpenError, RateLimitError
from backend.integrations.skladbot_client import (
    ASSEMBLY_TYPE_IDS,
    ASSEMBLY_WIP_STAGE_CODES,
    ASSEMBLY_WIP_TITLE_MARKERS,
    DELIVERY_REQUEST_TYPE_ID,
    INBOUND_TYPE_IDS,
    SkladbotApiError,
    SkladbotClient,
    decode_jwt_exp,
)
from backend.integrations.wmscelicom_client import WmsCelicomClient, normalize_base_url
from backend.models import (
    FfRequestKind,
    FulfillmentBoxOverride,
    FulfillmentRequest,
    FulfillmentStatusEvent,
    FulfillmentStock,
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    IntegrationKey,
    Nomenclature,
    SyncLog,
    Warehouse,
    WarehouseStock,
)
from backend.models.assembly import (
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
    AssemblyStatusHistory,
)
from backend.models.wb_fbo import WbFboSupply
from backend.schemas.assembly import AssemblyItemCreate, AssemblyRequestCreate
from backend.schemas.fulfillment import FfCreateFormResponse, FfCreateRequestPayload
from backend.services.assembly.crud import create_assembly_request
from backend.utils.crypto import decrypt as _decrypt, encrypt as _encrypt
from backend.utils.time import utcnow

logger = logging.getLogger("dds.fulfillment")

FF_SERVICES = ("skladbot", "migfull", "wmscelicom")
# Провайдеры с реализованным pull-синком
SYNCABLE_FF_SERVICES = ("skladbot", "wmscelicom", "migfull")
STOCKS_LIMIT = 5000
REQUESTS_LIMIT = 500

# Обогащение skladbot живой деталкой: cap вызовов show за один синк
# (rate limit /v1/requests/* — 120 req/min) и потолок чтения зеркала.
_ENRICH_DETAIL_CAP = 100
_MIRROR_SELECT_LIMIT = 10_000
# wmscelicom: терминальные заявки на отгрузку зеркалим за это окно (активные —
# всегда). Шире = сервер материализует больший набор до пагинации → медленно.
_WMS_DISPATCH_TERMINAL_DAYS = 90
# migfull: штрихкоды отдаются только карточкой товара — cap detail-вызовов
# guid→barcode за один синк (~1050 товаров, из них ~300 с остатком; хвост
# дотягивается следующими синками). Кэш — снапшот fulfillment_stocks.
_MIGFULL_BARCODE_CAP = 300
# Служебные позиции migfull (учёт грузомест склада) — не товары
_MIGFULL_SERVICE_ITEM_MARKER = "фф грузовое место"
# Короб → россыпь: кол-во штук в коробе из названия товара («… короб 20 шт.»)
_MIGFULL_BOX_UNITS_RE = re.compile(r"короб\s+(\d+)\s*шт", re.IGNORECASE)
_QTY_MAX = 2**31 - 1  # Integer-колонка: мусорная сумма провайдера не должна валить flush
_DEST_WAREHOUSE_MAX = 300  # String(300): значение длиннее уронит транзакцию синка


def _safe_int(value: object) -> int:
    """PHP-API коэрсия: '2' → 2, '12,5'/'abc'/None/false/контейнер → 0 — мусор не валит синк."""
    if not isinstance(value, int | float | str):
        return 0
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


def _coerce_dest(value: object) -> str | None:
    """Склад отгрузки от провайдера → str с клампом длины; пусто/false → None."""
    if not value:
        return None
    return str(value).strip()[:_DEST_WAREHOUSE_MAX] or None


def _escape_like(value: str) -> str:
    """Экранировать спецсимволы LIKE/ILIKE в пользовательском вводе."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Эвристика подбора кандидатов для несвязанных ФФ-заявок (overview):
# база — близость дат |external_created_at − created_at| в днях → score,
# дальше кандидат отбрасывается.
_SUGGEST_DATE_SCORES = {0: 70, 1: 55, 2: 40}
_SUGGEST_MIN_SCORE = 30
_SUGGEST_TOP_N = 3
_SUGGEST_CANDIDATES_LIMIT = 500
_SUGGEST_CANDIDATE_STATUSES = (
    AssemblyStatus.IN_PROGRESS.value,
    AssemblyStatus.READY.value,
    AssemblyStatus.VEHICLE_ASSIGNED.value,
)

# Кандидаты для модалки «Связать» (get_link_candidates). Когда состав
# ФФ-заявки доступен, главный сигнал — «подходит под наполнение»:
# Jaccard множеств ШК × 60 + qty-бонус 20 (±10%) + дата-бонус 20/15/10
# (0/±1/±2 дн), порог 40 и обязательное пересечение ШК. Без состава —
# фолбэк на эвристику дат (_SUGGEST_DATE_SCORES, qty-бонус 10, порог 30).
_LINK_CANDIDATES_LIMIT = 300
_CAND_COMP_DATE_SCORES = {0: 20, 1: 15, 2: 10}

# Состав ФФ-заявки для кнопки «Состав» в Telegram (get_ff_request_goods).
# Telegram-лимит сообщения 4096 символов; берём +1, чтобы честно показать
# «…и ещё позиции», если строк больше.
_FF_GOODS_LIMIT = 50

# Закреплённое авто-табло заявок ФФ (build_ff_board_text). Порядок вывода секций
# — от срочного: машина назначена → готово → в работе. Топ-N строк на статус
# (+ «…ещё M»), бюджет символов держим под лимит Telegram (4096).
_BOARD_STATUSES = (
    AssemblyStatus.VEHICLE_ASSIGNED.value,
    AssemblyStatus.READY.value,
    AssemblyStatus.IN_PROGRESS.value,
)
_BOARD_STATUS_LABEL = {
    AssemblyStatus.IN_PROGRESS.value: "🔧 В работе",
    AssemblyStatus.READY.value: "✅ Готово к отгрузке",
    AssemblyStatus.VEHICLE_ASSIGNED.value: "🚚 Машина назначена",
}
_BOARD_FETCH_LIMIT = 500
_BOARD_ROWS_PER_STATUS = 15
_BOARD_CHAR_BUDGET = 3800  # запас под Telegram-лимит 4096 (теги + заголовки)
_BOARD_AGE_ORANGE = 3  # 🟠 стареет, дней
_BOARD_AGE_RED = 5  # 🔴 давно висит (для готовых/с машиной без просрочки плана)
_MSK_OFFSET = timedelta(hours=3)
_CAND_COMP_MIN_SCORE = 40


_PROVIDER_LABELS = {
    "skladbot": "skladbot.ru",
    "wmscelicom": "WMS Celicom",
    "migfull": "Натали (migfull.app)",
}


def _provider_human(provider: str) -> str:
    """Имя провайдера для пользовательских сообщений об ошибках."""
    return _PROVIDER_LABELS.get(provider, provider)


# wmscelicom: стадий нет, статус отгрузки FBO кладётся в stage_title. «Ожидает
# отгрузки» = короб собран и ждёт машину = наш READY (терминальные статусы
# отгрузки → is_completed). «Новая»/«На сборке» — ещё WIP.
WMS_ASSEMBLY_READY_TITLES = frozenset({"Собрана", "Ожидает отгрузки"})


def _assembly_ready_signal(
    provider: str,
    stage_code: str | None,
    stage_title: str | None,
    is_completed: bool,
) -> bool:
    """Стадия ФФ говорит «сборка готова» → нашу заявку можно переводить в READY.

    skladbot (тип 851): deny-list — стадии 1–2 (забор груза, указание объёма)
    это WIP, любая ДРУГАЯ непустая стадия трактуется как «готов» (стадий после
    сборки много: виды работ, водитель, погрузка, отгрузка — и код стадии 3
    неизвестен, allowlist не собрать). Осознанный риск: новая РАННЯЯ стадия
    провайдера даст ложный READY (обратимо: READY → IN_PROGRESS разрешён) —
    при появлении пополнить ASSEMBLY_WIP_STAGE_CODES/MARKERS. Пустая стадия —
    НЕ сигнал. wmscelicom: «Ожидает отгрузки» (короб собран) или терминальный
    статус отгрузки FBO (is_completed). migfull: stage_code = слаг статуса
    отгрузки (uploaded → ready → closed) — ready («Собран») и есть сигнал.
    """
    if is_completed:
        return True
    if provider == "migfull":
        return (stage_code or "").strip().lower() == "ready"
    if provider == "wmscelicom":
        return (stage_title or "").strip() in WMS_ASSEMBLY_READY_TITLES
    if provider != "skladbot":
        return False
    code = (stage_code or "").strip()
    title = (stage_title or "").strip()
    if not code and not title:
        return False
    if code in ASSEMBLY_WIP_STAGE_CODES:
        return False
    title_low = title.lower()
    return not any(marker in title_low for marker in ASSEMBLY_WIP_TITLE_MARKERS)


def _transition_assembly_to_ready(
    db: AsyncSession,
    project_id: int,
    doc: AssemblyRequest,
    ff_req: FulfillmentRequest,
) -> None:
    """IN_PROGRESS → READY по сигналу стадии ФФ: статус + actual_ready_date + история.

    Намеренно мимо assembly.status.mark_ready: его пред-условия (FBO-supply,
    палеты) не применимы к авто-переходу по внешнему сигналу. Переход
    IN_PROGRESS → READY разрешён ASSEMBLY_TRANSITIONS; историю пишем напрямую
    (changed_by=ff_sync), не тянем пакет services.assembly.
    """
    old_status = doc.status
    doc.status = AssemblyStatus.READY.value
    doc.actual_ready_date = date.today()
    db.add(
        AssemblyStatusHistory(
            project_id=project_id,
            assembly_request_id=doc.id,
            old_status=old_status,
            new_status=AssemblyStatus.READY.value,
            changed_at=utcnow(),
            changed_by="ff_sync",
            comment=f"ФФ {ff_req.number or ff_req.external_id}: стадия «{ff_req.stage_title or 'завершена'}»",
        )
    )


def _inbound_accept_signal(is_completed: bool) -> bool:
    """ФФ принял приёмку на остатки → нашу приёмку EXPECTED/DRAFT можно ACCEPT.

    Сигнал прихода на остатки у всех провайдеров — is_completed: skladbot
    (тип 852/2644) при завершении приёмки очищает стадию «Приемка» и ставит
    is_completed; wmscelicom (терминальный статус разгрузки) и migfull
    (submission closed) — так же. Строгий сигнал (в отличие от сборки,
    _assembly_ready_signal): accept_receipt ПОСТИТ сток на склад, ложный приём
    дороже ложного READY (откат — лишь ACCEPTED→CANCELLED со встречными
    движениями). Отменённые приёмки приходят archived=True и отсекаются синком.
    """
    return bool(is_completed)


def _ff_status_code(
    provider: str,
    kind: str,
    stage_code: str | None,
    stage_title: str | None,
    is_completed: bool,
    archived: bool,
    expired: bool,
) -> str:
    """Нормализованный высокоуровневый статус ФФ-заявки для колонки «Статус ФФ».

    Единый словарь поверх стадий разных провайдеров (см. _assembly_ready_signal —
    «готово» определяется тем же сигналом, что и авто-READY связанной сборки):
      assembling — сборка идёт (wms «Новая»/«На сборке», migfull «Новый», skladbot WIP)
      ready      — собрано, ждёт отгрузки (wms «Ожидает отгрузки», migfull «Собран», skladbot пост-WIP)
      shipped    — отгружено/завершено (терминальные статусы)
      expected   — приёмка ожидается (inbound не завершён)
      accepted   — приёмка принята на остатки (inbound завершён)
      archived   — архив у провайдера; expired — просрочена (оверлей-предупреждение)
    """
    if archived:
        return "archived"
    if kind == FfRequestKind.INBOUND.value:
        if is_completed:
            return "accepted"
        return "expired" if expired else "expected"
    # assembly
    if is_completed:
        return "shipped"
    if expired:
        return "expired"
    if _assembly_ready_signal(provider, stage_code, stage_title, is_completed):
        return "ready"
    return "assembling"


# Advisory lock namespace для синка (pg_advisory_xact_lock(ns, project_id)).
# Лок по project_id (не warehouse_id): uq заявок — (project_id, provider,
# external_id), и два склада одного проекта на один кабинет ФФ не должны
# вставлять один external_id параллельно.
_FF_SYNC_LOCK_NS = 0x46465359  # 'FFSY' ≈ FulFillment SYnc


# ─── Connection ──────────────────────────────────────────────────────────────


async def get_integration(db: AsyncSession, project_id: int, warehouse_id: int) -> IntegrationKey | None:
    """Active fulfillment key bound to the warehouse, or None."""
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service.in_(FF_SERVICES),
            IntegrationKey.warehouse_id == warehouse_id,
            IntegrationKey.is_active.is_(True),
            IntegrationKey.is_deleted == False,
        )
    )
    return result.scalars().first()


async def get_status(db: AsyncSession, project_id: int, warehouse_id: int) -> dict:
    """Connection status for the warehouse (FulfillmentStatus shape)."""
    key = await get_integration(db, project_id, warehouse_id)
    if not key:
        return {"connected": False}
    config = key.config or {}
    return {
        "connected": True,
        "provider": key.service,
        "key_preview": "***" + _decrypt(key.encrypted_key)[-4:],
        "customer_id": config.get("customer_id"),
        "customer_name": config.get("customer_name"),
        "token_expires_at": config.get("token_expires_at"),
        "api_base_url": config.get("api_base_url"),
        "tenant_guid": config.get("tenant_guid"),
        "last_sync_at": key.last_sync_at,
    }


async def connect(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    provider: str,
    token: str,
    base_url: str | None = None,
    tenant_guid: str | None = None,
    customer_id: int | None = None,
) -> dict:
    """Validate the token and bind a fulfillment key to the warehouse.

    Для wmscelicom обязателен base_url — адрес клиентского инстанса
    ({client}.wmscelicom.ru), API живёт на нём. Для migfull обязателен
    tenant_guid — GUID кабинета клиента (хост фиксированный). Для skladbot
    customer_id обязателен, когда токен видит несколько клиентов (FF-operator
    токен видит весь tenant) — иначе остатки/заявки уйдут не тому кабинету;
    для селлер-токена (1 клиент) можно не указывать. Возвращает статус
    (FulfillmentStatus shape). Raises ValueError при невалидном токене,
    провайдере, отсутствующем складе или неоднозначном/неверном customer_id.
    """
    result = await db.execute(
        select(Warehouse.id).where(
            Warehouse.id == warehouse_id,
            Warehouse.project_id == project_id,
            Warehouse.is_deleted == False,
        )
    )
    if result.scalar_one_or_none() is None:
        raise ValueError("Склад не найден в проекте")

    # Один склад — один активный провайдер: иначе get_integration() неоднозначен
    current = await get_integration(db, project_id, warehouse_id)
    if current and current.service != provider:
        raise ValueError(f"К складу уже подключён {_provider_human(current.service)} — сначала отключите его")

    if provider == "skladbot":
        skl_client = SkladbotClient(token, project_id=project_id)
        try:
            customer = await skl_client.test_connection()
        except CircuitOpenError as e:
            raise ValueError(f"skladbot.ru временно недоступен, попробуйте позже ({e})") from e
        if customer is None:
            raise ValueError("Токен невалидный: skladbot.ru не вернул данные customer. Проверьте токен.")

        # Выбор кабинета. Если задан customer_id — пинним именно его (валидируя,
        # что токен его видит); это обязательно для FF-operator токена, который
        # видит весь tenant и чей customers[0] — произвольный клиент. Без
        # customer_id допускаем только токен с единственным клиентом (селлер).
        chosen: dict | None
        try:
            if customer_id is not None:
                if customer.get("id") == customer_id:
                    chosen = customer
                else:
                    chosen = await skl_client.find_customer(customer_id)
                if chosen is None:
                    raise ValueError(f"Клиент {customer_id} не найден среди кабинетов этого токена")
            else:
                total = await skl_client.count_customers()
                if total > 1:
                    raise ValueError(
                        f"Токен видит {total} кабинетов (FF-оператор) — укажите customer_id вашего кабинета"
                    )
                chosen = customer
        except CircuitOpenError as e:
            raise ValueError(f"skladbot.ru временно недоступен, попробуйте позже ({e})") from e

        expires_at = decode_jwt_exp(token)
        config = {
            "customer_id": chosen.get("id"),
            "customer_name": chosen.get("name"),
            "token_expires_at": expires_at.isoformat() if expires_at else None,
        }
    elif provider == "wmscelicom":
        api_base = normalize_base_url(base_url or "")
        wms_client = WmsCelicomClient(api_base, token, project_id=project_id)
        try:
            ok = await wms_client.test_connection()
        except CircuitOpenError as e:
            raise ValueError(f"WMS Celicom временно недоступен, попробуйте позже ({e})") from e
        if not ok:
            raise ValueError("WMS Celicom не ответил на тестовый запрос. Проверьте адрес инстанса и токен.")

        config = {
            "api_base_url": api_base,
            # «Кабинет» в UI — домен инстанса (другого имени API не отдаёт)
            "customer_name": api_base.removeprefix("https://"),
        }
    elif provider == "migfull":
        guid = normalize_tenant_guid(tenant_guid or "")
        mig_client = MigfullClient(guid, token, project_id=project_id)
        try:
            ok = await mig_client.test_connection()
        except CircuitOpenError as e:
            raise ValueError(f"migfull.app временно недоступен, попробуйте позже ({e})") from e
        if not ok:
            raise ValueError("migfull.app не ответил на тестовый запрос. Проверьте токен и GUID кабинета.")

        config = {
            "tenant_guid": guid,
            # Имени кабинета API не отдаёт — «кабинет» в UI = укороченный GUID
            "customer_name": f"migfull.app · {guid[:8]}",
        }
    else:
        raise ValueError(f"Неподдерживаемый провайдер фулфилмента: {provider}")

    label = f"warehouse:{warehouse_id}"

    # UniqueConstraint(project_id, service, label) НЕ учитывает is_deleted:
    # после soft_delete строка занимает уникальный слот. Ищем существующую
    # ВКЛЮЧАЯ soft-deleted → restore() + обновление полей, НЕ новый INSERT.
    key_result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == provider,
            IntegrationKey.label == label,
        )
    )
    key = key_result.scalars().first()
    if key:
        key.restore()
        key.encrypted_key = _encrypt(token)
        key.config = config
        key.is_active = True
        key.warehouse_id = warehouse_id
    else:
        key = IntegrationKey(
            project_id=project_id,
            service=provider,
            label=label,
            encrypted_key=_encrypt(token),
            is_active=True,
            warehouse_id=warehouse_id,
            config=config,
        )
        db.add(key)
    try:
        await db.commit()
    except IntegrityError as e:
        # TOCTOU: конкурентный connect вставил ключ между select и commit
        await db.rollback()
        raise ValueError("Подключение уже выполняется параллельно — обновите страницу") from e
    await db.refresh(key)

    return {
        "connected": True,
        "provider": provider,
        "key_preview": "***" + token[-4:],
        "customer_id": config.get("customer_id"),
        "customer_name": config.get("customer_name"),
        "token_expires_at": config.get("token_expires_at"),
        "api_base_url": config.get("api_base_url"),
        "tenant_guid": config.get("tenant_guid"),
        "last_sync_at": key.last_sync_at,
    }


async def disconnect(db: AsyncSession, project_id: int, warehouse_id: int) -> bool:
    """Soft-delete the fulfillment key. Зеркальные данные ff_* НЕ трогаем."""
    key = await get_integration(db, project_id, warehouse_id)
    if not key:
        return False
    key.soft_delete()
    await db.commit()
    return True


# ─── Sync ────────────────────────────────────────────────────────────────────


async def sync_warehouse(db: AsyncSession, project_id: int, warehouse_id: int) -> dict:
    """Sync stocks + requests from the fulfillment provider (FfSyncResult shape).

    Используется и роутером (ручной синк), и scheduler-job.
    Raises ValueError если фулфилмент не подключён.

    Порядок важен: сначала читаем ключ и ЗАКРЫВАЕМ транзакцию, потом ходим
    в skladbot (retry/backoff может занять минуты — нельзя держать
    idle-in-transaction коннект PgBouncer), и только потом пишем — под
    advisory xact-lock по (NS, project_id), сериализующим конкурентные синки
    (ручной + scheduler, любые склады проекта), иначе full-replace и UPSERT
    заявок ловят IntegrityError по uq.
    """
    key = await get_integration(db, project_id, warehouse_id)
    if not key:
        raise ValueError("Фулфилмент не подключён к этому складу")

    key_id = key.id
    provider = key.service
    token = _decrypt(key.encrypted_key)
    config = key.config or {}
    customer_id = config.get("customer_id")
    api_base = str(config.get("api_base_url") or "")
    tenant_guid = str(config.get("tenant_guid") or "")
    if provider == "skladbot":
        if not customer_id:
            raise ValueError("В конфигурации ключа нет customer_id — переподключите фулфилмент")
    elif provider == "wmscelicom":
        if not api_base:
            raise ValueError("В конфигурации ключа нет адреса инстанса — переподключите фулфилмент")
    elif provider == "migfull":
        if not tenant_guid:
            raise ValueError("В конфигурации ключа нет GUID кабинета — переподключите фулфилмент")
    else:
        raise ValueError(f"Синк для провайдера «{provider}» не реализован")

    # Снимок обогащения зеркала — читаем ДО закрытия транзакции: по нему
    # решаем, каким заявкам нужна живая деталка (skladbot — деталка сборок,
    # migfull — lines/incoming приёмок; бэкфилл total_qty)
    mirror_enrichment: dict[str, tuple[int | None, str | None]] = {}
    if provider in ("skladbot", "migfull"):
        enrich_result = await db.execute(
            select(
                FulfillmentRequest.external_id,
                FulfillmentRequest.total_qty,
                FulfillmentRequest.dest_warehouse,
            )
            .where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.warehouse_id == warehouse_id,
                FulfillmentRequest.provider == provider,
            )
            .limit(_MIRROR_SELECT_LIMIT)
        )
        mirror_enrichment = {ext: (qty, dest) for ext, qty, dest in enrich_result.all()}

    # migfull: персистентный кэш guid→barcode = прошлый снапшот остатков
    # (штрихкоды отдаются только детальной карточкой товара)
    barcode_by_guid: dict[str, str] = {}
    if provider == "migfull":
        bc_result = await db.execute(
            select(FulfillmentStock.external_product_id, FulfillmentStock.barcode)
            .where(
                FulfillmentStock.project_id == project_id,
                FulfillmentStock.warehouse_id == warehouse_id,
                FulfillmentStock.provider == "migfull",
                FulfillmentStock.external_product_id.is_not(None),
            )
            .limit(_MIRROR_SELECT_LIMIT)
        )
        barcode_by_guid = {guid: barcode for guid, barcode in bc_result.all() if guid and barcode}

    # migfull: ручные сопоставления короб→россыпь (override побеждает авто-вывод)
    box_overrides: dict[str, tuple[str, int]] = {}
    if provider == "migfull":
        box_overrides = await _load_box_overrides(db, project_id, warehouse_id)
    await db.commit()  # закрыть read-транзакцию до внешних HTTP-вызовов

    human = _provider_human(provider)
    try:
        if provider == "skladbot":
            skl_client = SkladbotClient(token, project_id=project_id)
            stock_items = [_normalize_skladbot_stock(i) for i in await skl_client.fetch_all_products(customer_id)]
            fetched_requests: list[tuple[int, list[dict]]] = []
            for type_id in sorted(ASSEMBLY_TYPE_IDS | INBOUND_TYPE_IDS):
                fetched_requests.append((type_id, await skl_client.fetch_requests(type_id)))
            request_rows = _normalize_skladbot_requests(fetched_requests)
            await _enrich_skladbot_requests(skl_client, request_rows, mirror_enrichment)
        elif provider == "migfull":
            mig_client = MigfullClient(tenant_guid, token, project_id=project_id)
            products = [p for p in await mig_client.fetch_all_products() if not _is_migfull_service_item(p)]
            await _resolve_migfull_barcodes(mig_client, products, barcode_by_guid)
            stock_items = [_normalize_migfull_stock(p, barcode_by_guid, box_overrides) for p in products]
            request_rows = [_normalize_migfull_shipment(r) for r in await mig_client.fetch_shipments()]
            request_rows += [_normalize_migfull_submission(r) for r in await mig_client.fetch_submissions()]
            await _enrich_migfull_submissions(mig_client, request_rows, mirror_enrichment)
        else:
            wms_client = WmsCelicomClient(api_base, token, project_id=project_id)
            stock_items = [_normalize_wms_stock(i) for i in await wms_client.fetch_all_items()]
            # Сборка = заявки на отгрузку (dispatchorders): склад отгрузки + состав.
            # Терминальные тянем за окно _WMS_DISPATCH_TERMINAL_DAYS (старее — не
            # зеркалим осознанно, иначе сервер материализует весь архив → таймаут).
            today = utcnow().date()
            dispatch_from = (today - timedelta(days=_WMS_DISPATCH_TERMINAL_DAYS)).isoformat()
            dispatch_rows = await wms_client.fetch_dispatch_orders(dispatch_from, today.isoformat())
            request_rows = [_normalize_wms_dispatch(r) for r in dispatch_rows]
            request_rows += [_normalize_wms_unloading(r) for r in await wms_client.fetch_unloading_orders()]
    except CircuitOpenError as e:
        raise ValueError(f"{human} временно недоступен, попробуйте позже ({e})") from e
    except RateLimitError as e:
        raise ValueError(f"{human} ограничил частоту запросов — повторите синк через минуту") from e
    except httpx.HTTPError as e:
        raise ValueError(f"Сетевая ошибка при обращении к {human}: {e}") from e

    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :project_id)"),
        {"ns": _FF_SYNC_LOCK_NS, "project_id": project_id},
    )
    stocks_synced, unmatched = await _apply_stocks(db, project_id, warehouse_id, provider, stock_items)
    if provider == "wmscelicom":
        await _purge_legacy_wms_shipments(db, project_id, warehouse_id)
    requests_synced = await _apply_requests(db, project_id, warehouse_id, provider, request_rows)

    # Авто-READY связанных сборок: flush, чтобы пере-SELECT зеркала видел
    # свежие стадии после UPSERT (новые INSERT'ы — тоже)
    await db.flush()
    linked_result = await db.execute(
        select(FulfillmentRequest)
        .where(
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.warehouse_id == warehouse_id,
            FulfillmentRequest.provider == provider,
            FulfillmentRequest.kind == FfRequestKind.ASSEMBLY.value,
            FulfillmentRequest.assembly_request_id.is_not(None),
            FulfillmentRequest.archived == False,
            # expired (просрочена) НЕ исключаем: заявка с истёкшим сроком всё
            # ещё активна и проходит стадии — её сборку тоже надо авто-READY
            FulfillmentRequest.local_archived == False,
        )
        .limit(_MIRROR_SELECT_LIMIT)
    )
    marked_ready = await _mark_linked_assemblies_ready(db, project_id, list(linked_result.scalars().all()))
    assemblies_marked_ready = len(marked_ready)

    # Кандидаты на авто-ACCEPT приёмок собираем ПОД синк-транзакцией (нужны
    # свежие is_completed после UPSERT), но сам приём — после commit (ниже):
    # accept_receipt постит сток, лочит строку и коммитит сам, внутри открытой
    # синк-транзакции его звать нельзя. expired не исключаем (приёмка активна).
    inbound_linked = await db.execute(
        select(FulfillmentRequest)
        .where(
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.warehouse_id == warehouse_id,
            FulfillmentRequest.provider == provider,
            FulfillmentRequest.kind == FfRequestKind.INBOUND.value,
            FulfillmentRequest.inbound_receipt_id.is_not(None),
            FulfillmentRequest.is_completed == True,
            FulfillmentRequest.archived == False,
            FulfillmentRequest.local_archived == False,
        )
        .limit(_MIRROR_SELECT_LIMIT)
    )
    inbound_reqs = list(inbound_linked.scalars().all())
    # Детали для Telegram-уведомления о принятой приёмке (по нашему receipt_id):
    # номер ФФ-заявки, склад сдачи, кол-во — те же поля, что в «Истории».
    accept_info = {
        r.inbound_receipt_id: (r.number or r.external_id, r.dest_warehouse, r.total_qty, r.id)
        for r in inbound_reqs
        if r.inbound_receipt_id is not None
    }
    inbound_accept_ids = await _collect_inbound_accept_candidates(db, project_id, inbound_reqs)

    synced_at = utcnow()
    key = await db.get(IntegrationKey, key_id)
    if key:
        key.last_sync_at = synced_at
    await db.commit()

    # Авто-ACCEPT приёмок (постинг стока) — ПОСЛЕ commit синка, каждая своей
    # транзакцией. accept_receipt идемпотентен (row-lock + guard «сток уже
    # применён»), повторный синк не задвоит. Ошибку приёмки не даём свалить синк.
    # slug проекта (deep-link) + имя ФФ-склада (строка «Склад ФФ» в уведомлении):
    # по одному get, только если есть что слать.
    notify_slug: str | None = None
    notify_wh_name: str | None = None
    if marked_ready or inbound_accept_ids:
        from backend.models.auth import Project

        _proj = await db.get(Project, project_id)
        notify_slug = _proj.slug if _proj else None
        _wh = await db.get(Warehouse, warehouse_id)
        notify_wh_name = _wh.name if _wh else None

    inbound_receipts_accepted = 0
    accept_items: list[dict[str, object]] = []
    if inbound_accept_ids:
        from backend.database import AsyncSessionLocal
        from backend.services.fulfillment_notify import build_accept_item
        from backend.services.warehouse_inbound import accept_receipt

        # Каждый приём — в СВОЕЙ сессии: accept_receipt сам коммитит/рефрешит,
        # а на ошибке (напр. приёмка без позиций) откат не должен затрагивать
        # сессию синка/вызывающего — иначе ловим expired-attribute на их объектах.
        for receipt_id in inbound_accept_ids:
            try:
                async with AsyncSessionLocal() as accept_db:
                    await accept_receipt(accept_db, project_id, receipt_id)
                inbound_receipts_accepted += 1
                info = accept_info.get(receipt_id)
                if info:
                    ff_no, dest, qty, ff_id = info
                    accept_items.append(
                        build_accept_item(
                            notify_slug, warehouse_id, ff_id, ff_no, dest, qty, warehouse_name=notify_wh_name
                        )
                    )
            except Exception as e:  # — best-effort, синк уже зафиксирован
                logger.warning("FF auto-accept: приёмка %s пропущена (%s)", receipt_id, e)

    if assemblies_marked_ready:
        await invalidate_cache("reports:assembly_flow")

    # Telegram-уведомления о смене статуса (HTML + кнопка «Открыть заявку»),
    # best-effort, синк уже зафиксирован; только в чаты проекта с ff_notify_enabled.
    from backend.services.fulfillment_notify import build_ready_item, notify_ff_events, refresh_ff_board

    ready_items = [
        build_ready_item(
            notify_slug,
            warehouse_id,
            d["ff_id"],
            d["assembly_number"],
            d["ff_number"],
            d["dest"],
            d["qty"],
            warehouse_name=notify_wh_name,
            wb_number=d.get("wb_number"),
        )
        for d in marked_ready
    ]
    if ready_items or accept_items:
        await notify_ff_events(db, project_id, ready_items + accept_items)

    # Закреплённое авто-табло (если включено в чате проекта) — обновляем на
    # каждом синке: оно отражает ТЕКУЩЕЕ состояние всех сборок, не только дельту.
    await refresh_ff_board(db, project_id)

    logger.info(
        "Fulfillment sync: project=%s warehouse=%s stocks=%d requests=%d unmatched=%d "
        "marked_ready=%d inbound_accepted=%d",
        project_id,
        warehouse_id,
        stocks_synced,
        requests_synced,
        unmatched,
        assemblies_marked_ready,
        inbound_receipts_accepted,
    )
    return {
        "stocks_synced": stocks_synced,
        "requests_synced": requests_synced,
        "unmatched_barcodes": unmatched,
        "assemblies_marked_ready": assemblies_marked_ready,
        "inbound_receipts_accepted": inbound_receipts_accepted,
        "synced_at": synced_at,
    }


async def _finalize_sync_log(
    db: AsyncSession,
    log_id: int,
    status: str,
    *,
    rows_fetched: int = 0,
    rows_inserted: int = 0,
    error_msg: str | None = None,
) -> None:
    """Закрыть строку sync_log (OK/ERROR). Сбой записи лога не должен валить синк."""
    try:
        await db.execute(
            update(SyncLog)
            .where(SyncLog.id == log_id)
            .values(
                status=status,
                rows_fetched=rows_fetched,
                rows_inserted=rows_inserted,
                finished_at=utcnow(),
                error_msg=error_msg,
            )
        )
        await db.commit()
    except Exception as e:  # — лог второстепенен, синк уже отработал
        logger.warning("Fulfillment sync: failed to finalize sync_log %s — %s", log_id, e)


async def sync_warehouse_logged(db: AsyncSession, project_id: int, warehouse_id: int) -> dict:
    """sync_warehouse + журналируем прогон в sync_log (вкладка «ФФ синхронизация»).

    Ручной синк — единичная операция в рамках запроса, поэтому лог ведём в той же
    сессии (scheduler-job крутит цикл по складам и пишет лог отдельными сессиями,
    чтобы строка не зависала RUNNING при падении одного склада). sync_warehouse
    делает commit ДО внешних HTTP-вызовов, так что строка RUNNING фиксируется
    рано, а провайдер-ошибки (ValueError) приходят на чистой сессии → ERROR
    дописывается штатно.
    """
    key = await get_integration(db, project_id, warehouse_id)
    if not key:
        raise ValueError("Фулфилмент не подключён к этому складу")

    sync_log = SyncLog(
        integration_id=key.id,
        service=key.service,
        sync_type="fulfillment",
        started_at=utcnow(),
        status="RUNNING",
    )
    db.add(sync_log)
    await db.flush()
    log_id = sync_log.id

    try:
        result = await sync_warehouse(db, project_id, warehouse_id)
    except Exception as e:
        await _finalize_sync_log(db, log_id, "ERROR", error_msg=str(e)[:1000])
        raise

    await _finalize_sync_log(
        db,
        log_id,
        "OK",
        rows_fetched=result["stocks_synced"] + result["requests_synced"],
        rows_inserted=result["stocks_synced"],
    )
    return result


# ─── Нормализация провайдер → общий вид ─────────────────────────────────────
# Остатки: {barcode, name, vendor_code, external_product_id, qty_good,
#           qty_reserve, qty_defect, qty_nominal}
# Заявки:  {external_id, kind, number, type_id, type_name, status, stage_code,
#           stage_title, is_completed, archived, expired, total_qty,
#           dest_warehouse, external_created_at, raw}
# total_qty/dest_warehouse = None означает «нет данных» (UPDATE их не затирает);
# skladbot получает их позже из живой деталки (_enrich_skladbot_requests).


def _normalize_skladbot_stock(item: dict) -> dict:
    """skladbot /v1/products item → нормализованный остаток."""
    pdid = item.get("product_data_id")
    return {
        "barcode": item.get("barcode"),
        "name": item.get("name"),
        "vendor_code": item.get("vendor_code"),
        "external_product_id": str(pdid) if pdid is not None else None,
        "qty_good": item.get("amount"),
        "qty_reserve": item.get("reserve_amount"),
        "qty_defect": item.get("repair_amount"),
        "qty_nominal": item.get("nominale_amount"),
    }


def _normalize_skladbot_requests(fetched_by_type: list[tuple[int, list[dict]]]) -> list[dict]:
    """skladbot /v1/requests rows → нормализованные заявки (kind по type_id)."""
    rows: list[dict] = []
    for type_id, items in fetched_by_type:
        kind = FfRequestKind.ASSEMBLY.value if type_id in ASSEMBLY_TYPE_IDS else FfRequestKind.INBOUND.value
        for row in items:
            rows.append(
                {
                    "external_id": str(row.get("id")),
                    "kind": kind,
                    "number": row.get("delivery_number"),
                    "type_id": type_id,
                    "type_name": row.get("type"),
                    "status": row.get("status"),
                    "stage_code": row.get("stage_code"),
                    "stage_title": row.get("stage_title"),
                    "is_completed": bool(row.get("is_completed")),
                    "archived": bool(row.get("archived")),
                    "expired": bool(row.get("expired")),
                    "total_qty": None,  # списочный метод состава не отдаёт — из деталки
                    "dest_warehouse": None,
                    "external_created_at": _parse_date(row.get("created_at")),
                    "raw": row,
                }
            )
    return rows


def _normalize_wms_stock(item: dict) -> dict:
    """wmscelicom items/get item → нормализованный остаток.

    Barcodes — массив (версии ШК); снапшот ключуется одним barcode — берём
    первый непустой. Count → good, CountVirtual → nominal; reserve/defect API не отдаёт.
    """
    barcodes = item.get("Barcodes") or []
    barcode = next((str(b).strip() for b in barcodes if b), "")
    item_id = item.get("Id")
    return {
        "barcode": barcode,
        "name": item.get("Name"),
        "vendor_code": item.get("Article"),
        "external_product_id": str(item_id) if item_id is not None else None,
        "qty_good": item.get("Count"),
        "qty_reserve": 0,
        "qty_defect": 0,
        "qty_nominal": item.get("CountVirtual"),
    }


def _wms_shipment_completed(status: str) -> bool:
    """Терминальные статусы отгрузки FBO (в т.ч. «Принята в СЦ…» с латинской «c»)."""
    return status in {"Отгружена", "Вручена получателю"} or status.startswith("Принята в СЦ")


# Английский статус заявки на отгрузку (когда FBO-отгрузки ещё нет, и
# shipment_status пуст) → русский ярлык для колонки статуса. «Ожидает сборки»
# (waitforcombine) — это до-сборочный WIP, НЕ сигнал готовности (см.
# WMS_ASSEMBLY_READY_TITLES — туда входят «Собрана»/«Ожидает отгрузки»).
_WMS_DISPATCH_STATUS_RU = {
    "new": "Новая",
    "combinig": "На сборке",
    "waitforcombine": "Ожидает сборки",
    "waitingdelivery": "Ожидает отгрузки",
    "combined": "Собрана",
    "ondelivery": "В доставке",
    "waitclientapproval": "На проверке клиентом",
    "delivered": "Доставлена",
    "annuled": "Аннулирована",
    "unknown": None,
}


def _normalize_wms_dispatch(row: dict) -> dict:
    """wmscelicom dispatchorders/list row → нормализованная заявка kind=assembly.

    DispatchOrder («заявка на отгрузку», зОГ) — родитель отгрузки FBO; именно
    здесь живут склад отгрузки (город МП, поле `warehouse`) и состав (`items[]`),
    тогда как в shipmentsfbo `shipped_target` — лишь площадка (Wildberries).
    external_id = orderid (стабилен весь жизненный цикл, в отличие от shipmentid,
    появляющегося лишь при отгрузке). Статус — русский shipment_status (если
    отгрузка уже создана), иначе ярлык из английского статуса заявки. зОГ-номер
    API не отдаёт (ярлык UI Целиком) → number=None.
    """
    eng_status = str(row.get("status") or "").strip()
    ship_status = str(row.get("shipment_status") or "").strip()  # PHP-API: бывает false/""
    status = ship_status or _WMS_DISPATCH_STATUS_RU.get(eng_status) or eng_status or None
    total = 0
    for item in row.get("items") or []:  # PHP-API: в массиве бывают null-элементы
        if isinstance(item, dict):
            total += _safe_int(item.get("count"))
    total = min(total, _QTY_MAX)
    # Завершённость: статус отгрузки FBO (если есть) приоритетнее, иначе по
    # английскому статусу заявки (ondelivery/delivered — уже уехало со склада ФФ)
    is_completed = _wms_shipment_completed(ship_status) if ship_status else eng_status in {"ondelivery", "delivered"}
    return {
        "external_id": str(row.get("orderid") or ""),
        "kind": FfRequestKind.ASSEMBLY.value,
        "number": None,
        "type_id": None,
        "type_name": "Заявка на отгрузку",
        "status": status,
        "stage_code": None,
        "stage_title": status,
        "is_completed": is_completed,
        "archived": eng_status == "annuled" or ship_status == "Аннулирована",
        "expired": False,
        "total_qty": total or None,
        "dest_warehouse": _coerce_dest(row.get("warehouse")),  # ГОРОД МП (PHP false → None)
        "external_created_at": _parse_date(row.get("date_time")),
        "raw": row,
    }


def _normalize_wms_unloading(row: dict) -> dict:
    """wmscelicom unloadingorders/list row → нормализованная заявка kind=inbound."""
    status = row.get("status")
    total = 0
    for item in row.get("items") or []:  # PHP-API: в массиве бывают null-элементы
        if not isinstance(item, dict):
            continue
        total += _safe_int(item.get("count"))
    total = min(total, _QTY_MAX)
    return {
        "external_id": str(row.get("unloading_order_id") or ""),
        "kind": FfRequestKind.INBOUND.value,
        "number": None,
        "type_id": None,
        "type_name": "Заявка на приёмку",
        "status": status,
        "stage_code": None,
        "stage_title": row.get("unloading_status") or status,
        "is_completed": bool(row.get("unloading_close_date")),
        "archived": False,
        "expired": False,
        "total_qty": total or None,
        "dest_warehouse": None,
        "external_created_at": _parse_date(row.get("create_date_time")),
        "raw": row,
    }


def _is_migfull_service_item(item: dict) -> bool:
    """Служебные позиции migfull («ФФ грузовое место — короб…») — учёт
    грузомест самого склада, не товары: в снапшот/тоталы не попадают."""
    return str(item.get("name") or "").strip().lower().startswith(_MIGFULL_SERVICE_ITEM_MARKER)


def _migfull_primary_barcode(detail: dict) -> str | None:
    """Карточка товара → штрихкод: is_primary, иначе первый непустой."""
    barcodes = [b for b in (detail.get("barcodes") or []) if isinstance(b, dict)]
    ordered = sorted(barcodes, key=lambda b: not b.get("is_primary"))
    return next((str(b.get("value") or "").strip() for b in ordered if b.get("value")), None)


async def _resolve_migfull_barcodes(
    client: MigfullClient,
    products: list[dict],
    barcode_by_guid: dict[str, str],
) -> None:
    """Дозаполнить кэш guid→barcode из детальных карточек товара (in-place).

    В списке /products штрихкоды пустые у ВСЕХ товаров (sku/gtin) — они
    отдаются только GET /products/{guid}. Детальку зовём ТОЛЬКО для
    незакэшированных guid с ненулевым остатком (кэш = прошлый снапшот
    fulfillment_stocks), cap _MIGFULL_BARCODE_CAP вызовов за синк — хвост
    дотянется следующими синками. Товар с остатком, у которого карточка БЕЗ
    штрихкодов, в кэш не попадает и ре-фетчится каждый синк — осознанно:
    склад может дозаполнить ШК позже (cap ограничивает ущерб). Ошибки резолва
    синк НЕ валят: 429/circuit — прекращаем резолв, прочее — пропуск товара.
    """
    targets = [
        p
        for p in products
        if str(p.get("guid") or "") not in barcode_by_guid
        and (_safe_int(p.get("stock_actual")) or _safe_int(p.get("stock_locked")))
    ]
    if len(targets) > _MIGFULL_BARCODE_CAP:
        logger.warning(
            "Fulfillment migfull: barcode detail cap %d exceeded, %d products deferred",
            _MIGFULL_BARCODE_CAP,
            len(targets) - _MIGFULL_BARCODE_CAP,
        )
        targets = targets[:_MIGFULL_BARCODE_CAP]

    for product in targets:
        guid = str(product.get("guid") or "")
        try:
            detail = await client.fetch_product(guid)
        except (RateLimitError, CircuitOpenError) as e:
            logger.warning("Fulfillment migfull: barcode resolve stopped (%s)", e)
            break
        except (MigfullApiError, httpx.HTTPError, ValueError) as e:
            logger.warning("Fulfillment migfull: product %s barcode skipped (%s)", guid, e)
            continue
        barcode = _migfull_primary_barcode(detail)
        if barcode:
            barcode_by_guid[guid] = barcode


def _ean13_check_digit(body12: str) -> str:
    """Контрольная цифра EAN13 по первым 12 цифрам."""
    total = sum((3 if i % 2 else 1) * int(c) for i, c in enumerate(body12))
    return str((10 - total % 10) % 10)


def _itf14_to_ean13(barcode: str) -> str | None:
    """ШК короба (ITF14, 14 цифр) → ШК россыпи (EAN13).

    GTIN-14 = индикатор-цифра упаковки + общее 12-значное тело + контрольная.
    Россыпь — тот же товар на уровне единицы: тело то же, индикатор отброшен,
    контрольная цифра EAN13 пересчитана. Не ITF14 (длина ≠ 14 / не цифры) → None.
    """
    if len(barcode) != 14 or not barcode.isdigit():
        return None
    body = barcode[1:13]
    return body + _ean13_check_digit(body)


def _migfull_box_pack(barcode: str, name: str | None) -> tuple[str, int] | None:
    """Короб migfull → (base_barcode россыпи, units_per_box) или None.

    Короб распознаём по ШК-короба (ITF14) и наличию «короб N шт.» в названии.
    base_barcode выводим по GTIN-14, кол-во в коробе — из названия. Проверено
    на живых данных: 151/151 коробов дают EAN13, совпадающий с номенклатурой,
    и кол-во парсится у всех. Не короб / кол-во не выводимо → None (строка
    остаётся как есть, без свода к россыпи).
    """
    base = _itf14_to_ean13(barcode)
    if base is None:
        return None
    match = _MIGFULL_BOX_UNITS_RE.search(name or "")
    if not match:
        return None
    units = int(match.group(1))
    if units <= 1:
        return None
    return base, units


def _normalize_migfull_stock(
    item: dict,
    barcode_by_guid: dict[str, str],
    box_overrides: dict[str, tuple[str, int]] | None = None,
) -> dict:
    """migfull /products item → нормализованный остаток.

    barcode — из кэша guid→barcode (нерезолвленные строки отсеет _apply_stocks
    по пустому barcode). stock_actual → good, stock_locked → reserve,
    stock_available → nominal; брака в остатках API не отдаёт. Если barcode —
    ШК короба, проставляем base_barcode (россыпь) и units_per_box — остатки
    короба сведутся к россыпи в list_stocks. Приоритет: ручной override
    (box_overrides) → авто-вывод (ITF14→EAN13 по GTIN-14 + «короб N шт.»).
    """
    guid = str(item.get("guid") or "")
    barcode = barcode_by_guid.get(guid, "")
    base_barcode: str | None = None
    units_per_box = 1
    override = (box_overrides or {}).get(barcode) if barcode else None
    if override:
        base_barcode, units_per_box = override
    else:
        pack = _migfull_box_pack(barcode, item.get("name")) if barcode else None
        if pack:
            base_barcode, units_per_box = pack
    return {
        "barcode": barcode,
        "base_barcode": base_barcode,
        "units_per_box": units_per_box,
        "name": item.get("name"),
        "vendor_code": item.get("sku"),
        "external_product_id": guid or None,
        "qty_good": _safe_int(item.get("stock_actual")),
        "qty_reserve": _safe_int(item.get("stock_locked")),
        "qty_defect": 0,
        "qty_nominal": _safe_int(item.get("stock_available")),
    }


_MIGFULL_SHIPMENT_TYPE_NAMES = {"fbo": "Отгрузка FBO", "client": "Отгрузка клиенту"}


def _normalize_migfull_shipment(row: dict) -> dict:
    """migfull /shipments row → нормализованная заявка kind=assembly.

    Слаг статуса (uploaded → ready → closed; canceled) кладём в stage_code —
    по нему работает _assembly_ready_signal; человекочитаемый status_display —
    в status/stage_title. planned/shipped_lines приходят в списке целиком
    (сверено с *_lines_count) и остаются в raw — деталка строится из зеркала.
    """
    status = str(row.get("status") or "")
    dest = row.get("destination_marketplace")
    dest_name = dest.get("name") if isinstance(dest, dict) else None
    return {
        "external_id": str(row.get("guid") or ""),
        "kind": FfRequestKind.ASSEMBLY.value,
        "number": row.get("reference") or None,
        "type_id": None,
        "type_name": _MIGFULL_SHIPMENT_TYPE_NAMES.get(str(row.get("shipment_type") or ""), "Отгрузка"),
        "status": row.get("status_display") or status or None,
        "stage_code": status or None,
        "stage_title": row.get("status_display") or status or None,
        "is_completed": status == "closed",
        "archived": status == "canceled",
        "expired": False,
        "total_qty": min(_safe_int(row.get("planned_quantity_total")), _QTY_MAX) or None,
        "dest_warehouse": _coerce_dest(dest_name),
        "external_created_at": _parse_date(row.get("created_at")),
        "raw": row,
    }


def _normalize_migfull_submission(row: dict) -> dict:
    """migfull /submissions row → нормализованная заявка kind=inbound.

    Состава в списке нет (только submission_lines_count) — total_qty
    дотягивает _enrich_migfull_submissions из lines/incoming.
    """
    status = str(row.get("status") or "")
    return {
        "external_id": str(row.get("guid") or ""),
        "kind": FfRequestKind.INBOUND.value,
        "number": row.get("reference") or None,
        "type_id": None,
        "type_name": "Приёмка",
        "status": row.get("status_display") or status or None,
        "stage_code": status or None,  # processing → send → closed; canceled
        "stage_title": row.get("status_display") or status or None,
        "is_completed": status == "closed",
        "archived": status == "canceled",
        "expired": False,
        "total_qty": None,
        "dest_warehouse": None,
        "external_created_at": _parse_date(row.get("submission_date") or row.get("created_at")),
        "raw": row,
    }


async def _enrich_migfull_submissions(
    client: MigfullClient,
    rows: list[dict],
    mirror: dict[str, tuple[int | None, str | None]],
) -> None:
    """Обогатить inbound-строки заявленным количеством из lines/incoming in-place.

    Активным приёмкам lines нужны каждый синк (заявленное меняется),
    закрытым — разовый бэкфилл, пока в зеркале total_qty IS NULL. Отменённые
    не бэкфиллим: их пустые lines давали бы вечный NULL → пере-fetch каждый
    синк и голодание cap'а. Служебные позиции («ФФ грузовое место») в тотал
    не входят. Cap _ENRICH_DETAIL_CAP; ошибки обогащения синк НЕ валят:
    429/circuit — стоп, прочее — пропуск.
    """
    active_targets: list[dict] = []
    backfill_targets: list[dict] = []
    for row in rows:
        if row.get("kind") != FfRequestKind.INBOUND.value:
            continue
        if not row.get("archived") and not row.get("is_completed"):
            active_targets.append(row)
        elif not row.get("archived") and mirror.get(row["external_id"], (None, None))[0] is None:
            backfill_targets.append(row)

    targets = active_targets + backfill_targets  # при cap'е бэкфилл жертвуем первым
    if len(targets) > _ENRICH_DETAIL_CAP:
        logger.warning(
            "Fulfillment migfull: submission lines cap %d exceeded, %d skipped",
            _ENRICH_DETAIL_CAP,
            len(targets) - _ENRICH_DETAIL_CAP,
        )
        targets = targets[:_ENRICH_DETAIL_CAP]

    for row in targets:
        try:
            lines = await client.fetch_submission_lines(row["external_id"], "incoming")
        except (RateLimitError, CircuitOpenError) as e:
            logger.warning("Fulfillment migfull: enrich stopped, remaining submissions skipped (%s)", e)
            break
        except (MigfullApiError, httpx.HTTPError, ValueError) as e:
            logger.warning("Fulfillment migfull: submission %s skipped (%s)", row["external_id"], e)
            continue
        total = min(
            sum(
                _safe_int(line.get("quantity"))
                for line in lines
                if isinstance(line, dict) and not _is_migfull_service_item(line.get("product") or {})
            ),
            _QTY_MAX,
        )
        # 0/пусто = «нет данных»: не затираем прежнее значение нулём
        row["total_qty"] = total or None


async def _enrich_skladbot_requests(
    client: SkladbotClient,
    rows: list[dict],
    mirror: dict[str, tuple[int | None, str | None]],
) -> None:
    """Обогатить assembly-строки живой деталкой (total_qty, dest_warehouse) in-place.

    Списочный /v1/requests не отдаёт ни состав, ни склад МП — только
    недокументированный show. Активным заявкам деталка нужна каждый синк
    (заявленное количество меняется), завершённым/архивным — разовый бэкфилл,
    пока в зеркале total_qty IS NULL. Cap _ENRICH_DETAIL_CAP вызовов за синк
    (rate limit /v1/requests/* — 120 req/min). Ошибки обогащения синк НЕ валят:
    429/circuit — прекращаем обогащение, прочее — пропуск строки.
    """
    active_targets: list[dict] = []
    backfill_targets: list[dict] = []
    for row in rows:
        if row.get("kind") != FfRequestKind.ASSEMBLY.value:
            continue
        if not row.get("archived") and not row.get("is_completed"):
            active_targets.append(row)
        elif mirror.get(row["external_id"], (None, None))[0] is None:
            backfill_targets.append(row)

    targets = active_targets + backfill_targets  # при cap'е бэкфилл жертвуем первым
    if len(targets) > _ENRICH_DETAIL_CAP:
        logger.warning(
            "Fulfillment enrich: detail cap %d exceeded, %d requests skipped",
            _ENRICH_DETAIL_CAP,
            len(targets) - _ENRICH_DETAIL_CAP,
        )
        targets = targets[:_ENRICH_DETAIL_CAP]

    for row in targets:
        try:
            detail = await client.fetch_request_detail(row["external_id"])
        except (RateLimitError, CircuitOpenError) as e:
            logger.warning("Fulfillment enrich: stopped, remaining details skipped (%s)", e)
            break
        except (SkladbotApiError, httpx.HTTPError, ValueError) as e:
            logger.warning("Fulfillment enrich: request %s skipped (%s)", row["external_id"], e)
            continue
        if not isinstance(detail, dict):
            # show недокументирован: Laravel может отдать data списком — форма дрейфует
            logger.warning("Fulfillment enrich: request %s — unexpected detail shape, skipped", row["external_id"])
            continue
        products = detail.get("products") or []
        total = min(sum(_safe_int(p.get("amount")) for p in products if isinstance(p, dict)), _QTY_MAX)
        # 0/пусто = «нет данных»: при дрейфе формы ответа не затираем прежнее значение нулём
        row["total_qty"] = total or None
        dest = next(
            (
                f.get("value")
                for f in (detail.get("fields") or [])
                if isinstance(f, dict) and f.get("field") == "marketplace_warehouse"
            ),
            None,
        )
        row["dest_warehouse"] = _coerce_dest(dest)


async def _apply_stocks(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    provider: str,
    items: list[dict],
) -> tuple[int, int]:
    """Aggregate normalized provider stock and fully replace the snapshot.

    Returns (rows_written, unmatched_barcodes).
    """
    # Агрегация по barcode: один barcode встречается в нескольких item'ах
    # (версии товара под WB/OZON) — количества суммируем, name/vendor_code/
    # external_product_id берём от первого. Пустой barcode пропускаем.
    aggregated: dict[str, dict] = {}
    for item in items:
        barcode = str(item.get("barcode") or "").strip()
        if not barcode:
            continue
        agg = aggregated.get(barcode)
        if agg is None:
            agg = aggregated[barcode] = {
                "name": item.get("name"),
                "vendor_code": item.get("vendor_code"),
                "external_product_id": item.get("external_product_id"),
                # Короб → россыпь: base_barcode (россыпь) и штук в коробе;
                # для россыпи base_barcode=None, units_per_box=1 (один товар
                # на barcode — берём от первого item'а).
                "base_barcode": item.get("base_barcode"),
                "units_per_box": int(item.get("units_per_box") or 1),
                "qty_good": 0,
                "qty_reserve": 0,
                "qty_defect": 0,
                "qty_nominal": 0,
            }
        agg["qty_good"] += int(item.get("qty_good") or 0)
        agg["qty_reserve"] += int(item.get("qty_reserve") or 0)
        agg["qty_defect"] += int(item.get("qty_defect") or 0)
        agg["qty_nominal"] += int(item.get("qty_nominal") or 0)

    # Резолв номенклатуры одним запросом по эффективному ШК (россыпь для
    # коробов: base_barcode, иначе сам barcode) — короб матчится к нашему товару.
    def _effective_barcode(barcode: str, agg: dict) -> str:
        return agg.get("base_barcode") or barcode

    nom_by_barcode: dict[str, int] = {}
    if aggregated:
        effective = {_effective_barcode(bc, agg) for bc, agg in aggregated.items()}
        result = await db.execute(
            select(Nomenclature.id, Nomenclature.barcode).where(
                Nomenclature.project_id == project_id,
                Nomenclature.barcode.in_(list(effective)),
            )
        )
        nom_by_barcode = {barcode: nom_id for nom_id, barcode in result.all()}

    # Полная замена снапшота этого (project, warehouse).
    # FulfillmentStock — не SoftDelete-модель: hard delete по дизайну.
    await db.execute(
        delete(FulfillmentStock).where(
            FulfillmentStock.project_id == project_id,
            FulfillmentStock.warehouse_id == warehouse_id,
        )
    )

    synced_at = utcnow()
    rows = [
        FulfillmentStock(
            project_id=project_id,
            warehouse_id=warehouse_id,
            provider=provider,
            barcode=barcode,
            base_barcode=agg["base_barcode"],
            units_per_box=agg["units_per_box"],
            nomenclature_id=nom_by_barcode.get(_effective_barcode(barcode, agg)),
            name=agg["name"],
            vendor_code=agg["vendor_code"],
            qty_good=agg["qty_good"],
            qty_reserve=agg["qty_reserve"],
            qty_defect=agg["qty_defect"],
            qty_nominal=agg["qty_nominal"],
            external_product_id=agg["external_product_id"],
            synced_at=synced_at,
        )
        for barcode, agg in aggregated.items()
    ]
    db.add_all(rows)

    unmatched = sum(1 for barcode, agg in aggregated.items() if _effective_barcode(barcode, agg) not in nom_by_barcode)
    return len(rows), unmatched


# Поля, смену которых журналируем в историю синка (FulfillmentStatusEvent).
# Обогащение (total_qty/dest_warehouse), даты и raw НЕ считаются сменой статуса.
def _status_fields_from_row(row: dict) -> dict:
    return {
        "status": row["status"],
        "stage_code": row["stage_code"],
        "stage_title": row["stage_title"],
        "is_completed": bool(row["is_completed"]),
        "archived": bool(row["archived"]),
    }


def _status_fields_from_model(req: FulfillmentRequest) -> dict:
    return {
        "status": req.status,
        "stage_code": req.stage_code,
        "stage_title": req.stage_title,
        "is_completed": bool(req.is_completed),
        "archived": bool(req.archived),
    }


def _build_status_event(
    project_id: int,
    warehouse_id: int,
    provider: str,
    req: FulfillmentRequest,
    old: dict | None,
    new: dict,
    changed_at: datetime,
) -> FulfillmentStatusEvent:
    """Строка журнала: old=None → заявка только появилась (event_type=created)."""
    return FulfillmentStatusEvent(
        project_id=project_id,
        warehouse_id=warehouse_id,
        provider=provider,
        fulfillment_request_id=req.id,
        external_id=req.external_id,
        number=req.number,
        kind=req.kind,
        event_type="created" if old is None else "changed",
        old_status=old["status"] if old else None,
        new_status=new["status"],
        old_stage_code=old["stage_code"] if old else None,
        new_stage_code=new["stage_code"],
        old_stage_title=old["stage_title"] if old else None,
        new_stage_title=new["stage_title"],
        old_is_completed=old["is_completed"] if old else None,
        new_is_completed=new["is_completed"],
        old_archived=old["archived"] if old else None,
        new_archived=new["archived"],
        changed_at=changed_at,
    )


async def _purge_legacy_wms_shipments(db: AsyncSession, project_id: int, warehouse_id: int) -> None:
    """Снести легаси-зеркало склада wmscelicom (бывшие отгрузки FBO).

    До перехода на dispatchorders «ФФ сборка» строилась из shipmentsfbo
    (external_id = shipment_fbo_id, raw с ключом `shipment_fbo_id`, без `orderid`)
    — иной площадки/города/состава, чем заявки на отгрузку. Теперь источник —
    dispatchorders (external_id = orderid), поэтому старые строки стали дублями.
    Удаляем ТОЛЬКО несвязанные (без нашего assembly/inbound): связанные единичны,
    их связь дороже дубля — оставляем. Hard delete (модель без soft-delete, как
    FulfillmentStock); журнал статусов уходит каскадом (ondelete=CASCADE).
    Идемпотентно: после первого синка таких строк не остаётся.
    """
    await db.execute(
        delete(FulfillmentRequest).where(
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.warehouse_id == warehouse_id,
            FulfillmentRequest.provider == "wmscelicom",
            FulfillmentRequest.assembly_request_id.is_(None),
            FulfillmentRequest.inbound_receipt_id.is_(None),
            # JSONB «ключ существует» (SQLAlchemy сам экранирует ?): легаси-raw
            # отгрузки FBO содержит shipment_fbo_id, у dispatchorder его нет.
            FulfillmentRequest.raw.has_key("shipment_fbo_id"),
        )
    )


async def _apply_requests(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    provider: str,
    rows: list[dict],
) -> int:
    """UPSERT the request mirror from normalized provider rows.

    Существующие строки: обновляем status/stage/number/type_name/даты/флаги/
    synced_at/raw; ручные связи (assembly_request_id/inbound_receipt_id) НЕ трогаем.
    Параллельно ведём журнал смены статусов (FulfillmentStatusEvent): новая
    заявка → событие `created`, изменение стадии/статуса/флагов → `changed`.
    """
    # Дедуп по external_id в рамках батча (защита от пересечения выборок);
    # строки без id (битый ответ провайдера) пропускаем.
    by_external: dict[str, dict] = {}
    for row in rows:
        external_id = row.get("external_id")
        if not external_id or external_id == "None":
            continue
        if external_id not in by_external:
            by_external[external_id] = row

    if not by_external:
        return 0

    result = await db.execute(
        select(FulfillmentRequest).where(
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.provider == provider,
            FulfillmentRequest.external_id.in_(list(by_external)),
        )
    )
    existing = {r.external_id: r for r in result.scalars().all()}

    synced_at = utcnow()
    # (request, old_fields|None, new_fields) — событие создаём после flush,
    # чтобы у новых заявок уже был id для FK
    pending_events: list[tuple[FulfillmentRequest, dict | None, dict]] = []
    for external_id, row in by_external.items():
        new_fields = _status_fields_from_row(row)
        req = existing.get(external_id)
        if req:
            old_fields = _status_fields_from_model(req)  # снимок ДО перезаписи
            req.number = row["number"] or req.number
            req.type_name = row["type_name"] or req.type_name
            req.status = row["status"]
            req.stage_code = row["stage_code"]
            req.stage_title = row["stage_title"]
            req.archived = row["archived"]
            req.expired = row["expired"]
            req.is_completed = row["is_completed"]
            # None = «нет данных» (деталка не запрашивалась) — старое не затираем
            if row.get("total_qty") is not None:
                req.total_qty = row["total_qty"]
            if row.get("dest_warehouse") is not None:
                req.dest_warehouse = row["dest_warehouse"]
            req.external_created_at = row["external_created_at"] or req.external_created_at
            req.synced_at = synced_at
            req.raw = row["raw"]
            if old_fields != new_fields:
                pending_events.append((req, old_fields, new_fields))
        else:
            new_req = FulfillmentRequest(
                project_id=project_id,
                warehouse_id=warehouse_id,
                provider=provider,
                external_id=external_id,
                number=row["number"],
                kind=row["kind"],
                type_id=row["type_id"],
                type_name=row["type_name"],
                status=row["status"],
                stage_code=row["stage_code"],
                stage_title=row["stage_title"],
                is_completed=row["is_completed"],
                archived=row["archived"],
                expired=row["expired"],
                total_qty=row.get("total_qty"),
                dest_warehouse=row.get("dest_warehouse"),
                external_created_at=row["external_created_at"],
                raw=row["raw"],
                synced_at=synced_at,
            )
            db.add(new_req)
            pending_events.append((new_req, None, new_fields))

    if pending_events:
        await db.flush()  # проставить id новым заявкам до записи событий (FK)
        for ev_req, ev_old, ev_new in pending_events:
            db.add(_build_status_event(project_id, warehouse_id, provider, ev_req, ev_old, ev_new, synced_at))
    return len(by_external)


async def _mark_linked_assemblies_ready(
    db: AsyncSession,
    project_id: int,
    ff_requests: list[FulfillmentRequest],
) -> list[dict[str, object]]:
    """Перевести связанные сборки IN_PROGRESS → READY по сигналу стадии ФФ.

    Берём живые связанные assembly-строки (не archived/local_archived;
    expired/просрочена — всё ещё активна, не исключаем) с
    _assembly_ready_signal() == True; наши заявки выбираются одним запросом
    (project_id + is_deleted + только IN_PROGRESS — прочие статусы не трогаем,
    переход выполняем напрямую, история changed_by=ff_sync). Возвращает список
    словарей по каждому переходу (assembly_number/ff_number/dest/qty) для
    Telegram-уведомлений. Commit — на стороне вызывающего.
    """
    ff_by_assembly: dict[int, FulfillmentRequest] = {}
    for req in ff_requests:
        if req.kind != FfRequestKind.ASSEMBLY.value or req.assembly_request_id is None:
            continue
        if req.archived:  # expired (просрочена) — активна, не исключаем
            continue
        if not _assembly_ready_signal(req.provider, req.stage_code, req.stage_title, req.is_completed):
            continue
        ff_by_assembly[req.assembly_request_id] = req
    if not ff_by_assembly:
        return []

    result = await db.execute(
        select(AssemblyRequest)
        # wb_fbo_supply — для номера поставки ВБ в Telegram-уведомлении (nullable).
        # selectinload шлёт отдельный SELECT, FOR UPDATE на нём не вешается — ок,
        # лочим только строку сборки.
        .options(selectinload(AssemblyRequest.wb_fbo_supply))
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.id.in_(list(ff_by_assembly)),
            AssemblyRequest.is_deleted == False,
            AssemblyRequest.status == AssemblyStatus.IN_PROGRESS.value,
        )
        # row-lock: ручной переход/линк в параллельной транзакции не должен
        # дать lost update или дубль строки истории
        .with_for_update(of=AssemblyRequest)
    )
    docs = list(result.scalars().all())
    transitioned: list[dict[str, object]] = []
    for doc in docs:
        ff = ff_by_assembly[doc.id]
        _transition_assembly_to_ready(db, project_id, doc, ff)
        supply = doc.wb_fbo_supply
        transitioned.append(
            {
                "assembly_number": doc.number,
                "ff_number": ff.number or ff.external_id,
                "dest": ff.dest_warehouse,
                "qty": ff.total_qty,
                "ff_id": ff.id,
                "wb_number": supply.wb_supply_id if supply else None,
            }
        )
    return transitioned


async def get_ff_request_goods(db: AsyncSession, project_id: int, ff_request_id: int) -> str | None:
    """HTML-список позиций ФФ-заявки (ШК · артикул продавца · кол-во) для кнопки «Состав».

    Читает из НАШИХ зеркал-документов (сборка/приёмка) — без HTTP к провайдеру
    (в отличие от get_request_detail). Скоуп по project_id (заявка чужого проекта
    → None). Возвращает None, если заявки нет, она не связана с документом или
    состав пуст. Кол-во: для сборки — quantity, для приёмки — expected_qty. Лимит
    _FF_GOODS_LIMIT позиций (+ строка «…и ещё», если строк больше).
    """
    result = await db.execute(
        select(FulfillmentRequest).where(
            FulfillmentRequest.id == ff_request_id,
            FulfillmentRequest.project_id == project_id,
        )
    )
    req = result.scalar_one_or_none()
    if not req:
        return None

    if req.assembly_request_id is not None:
        rows_result = await db.execute(
            select(
                AssemblyRequestItem.barcode,
                Nomenclature.article_seller,
                func.sum(AssemblyRequestItem.quantity),
            )
            .outerjoin(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
            .where(
                AssemblyRequestItem.project_id == project_id,
                AssemblyRequestItem.assembly_request_id == req.assembly_request_id,
            )
            .group_by(AssemblyRequestItem.barcode, Nomenclature.article_seller)
            .order_by(func.sum(AssemblyRequestItem.quantity).desc())
            .limit(_FF_GOODS_LIMIT + 1)
        )
    elif req.inbound_receipt_id is not None:
        rows_result = await db.execute(
            select(
                InboundReceiptItem.barcode,
                Nomenclature.article_seller,
                func.sum(InboundReceiptItem.expected_qty),
            )
            .outerjoin(Nomenclature, Nomenclature.id == InboundReceiptItem.nomenclature_id)
            .where(
                InboundReceiptItem.project_id == project_id,
                InboundReceiptItem.receipt_id == req.inbound_receipt_id,
            )
            .group_by(InboundReceiptItem.barcode, Nomenclature.article_seller)
            .order_by(func.sum(InboundReceiptItem.expected_qty).desc())
            .limit(_FF_GOODS_LIMIT + 1)
        )
    else:
        return None

    rows = [(bc, art, int(qty or 0)) for bc, art, qty in rows_result.all()]
    if not rows:
        return None

    e = html.escape
    ff_no = req.number or req.external_id
    shown = rows[:_FF_GOODS_LIMIT]
    lines = [f"📦 <b>Состав заявки</b> <code>{e(str(ff_no))}</code>", ""]
    for barcode, article, qty in shown:
        art_part = f" · {e(article)}" if article else ""
        lines.append(f"<code>{e(barcode)}</code>{art_part} · <b>{qty} шт</b>")
    if len(rows) > _FF_GOODS_LIMIT:
        lines.append(f"\n…и ещё позиции (показаны первые {_FF_GOODS_LIMIT})")
    else:
        lines.append(f"\n<b>Итого:</b> {len(shown)} поз. · {sum(q for _, _, q in shown)} шт")
    return "\n".join(lines)


def _board_fmt_qty(n: int) -> str:
    """1416 → «1 416» (пробел-разделитель тысяч для читаемости)."""
    return f"{n:,}".replace(",", " ")


def _board_city(fbo_city: str | None, manual_city: str | None, ff_wh: str | None) -> str | None:
    """Склад назначения МП: поставка ВБ → ручной → склад ФФ (как в list_unlinked)."""
    return fbo_city or manual_city or ff_wh or None


def _board_age_days(today: date, status: str, created_at: datetime | None, act_date: date | None) -> int:
    """Возраст в днях. Для готовых считаем от факт-готовности (сколько стоит без
    машины = деньги), иначе от создания. created_at — UTC, переводим в МСК-дату."""
    if status == AssemblyStatus.READY.value and act_date is not None:
        base = act_date
    elif created_at is not None:
        base = (created_at + _MSK_OFFSET).date()
    else:
        return 0
    return max(0, (today - base).days)


def _board_pickup_line(it: dict, today: date) -> str:
    """Вторая строка заявки с машиной: когда забор · слот · цена · бренд."""
    pickup_date = it["pickup_date"]
    if pickup_date is None:
        return "└ <i>дата забора не задана</i>"
    if pickup_date == today:
        when = "<b>сегодня</b>"
    elif pickup_date == today + timedelta(days=1):
        when = "завтра"
    else:
        when = f"{pickup_date:%d.%m}"
    parts = [when, html.escape(str(it["pickup_slot"])) if it["pickup_slot"] else "<i>слот не задан</i>"]
    if it["pickup_cost"] is not None:
        parts.append(f"<b>{_board_fmt_qty(int(it['pickup_cost']))} ₽</b>")
    if it["vehicle_brand"]:
        parts.append(html.escape(str(it["vehicle_brand"])))
    return "└ " + " · ".join(parts)


def _board_render_item(it: dict, today: date, scoped: bool) -> str:
    """Одна заявка табло: 2 строки для «машины», иначе компактная однострочная."""
    code = f"<code>{html.escape(it['number'])}</code>"
    city = f" · <b>{html.escape(it['city'])}</b>" if it["city"] else ""
    qty = f" · {_board_fmt_qty(it['qty'])} шт"
    if it["status"] == AssemblyStatus.VEHICLE_ASSIGNED.value:
        return f"{code}{city}{qty}\n{_board_pickup_line(it, today)}"
    marker = {"red": "🔴 ", "orange": "🟠 "}.get(it["severity"], "")
    if it["overdue"] and it["est_date"] is not None:
        age = f" · <s>план {it['est_date']:%d.%m}</s>"
    else:
        age = f" · {it['age']}д"
    hint = f" · <i>{html.escape(it['ff_hint'])}</i>" if (not scoped and it["ff_hint"]) else ""
    return f"{marker}{code}{city}{qty}{age}{hint}"


def _board_sort(status: str, items: list[dict], today: date) -> list[dict]:
    """Внутри секции — самое срочное наверх."""
    if status == AssemblyStatus.VEHICLE_ASSIGNED.value:
        # ближайший забор выше; без даты — в конец
        return sorted(items, key=lambda it: (it["pickup_date"] is None, it["pickup_date"] or today))
    rank = {"red": 0, "orange": 1, "": 2}
    return sorted(items, key=lambda it: (rank[it["severity"]], -it["age"]))


async def build_ff_board_text(db: AsyncSession, project_id: int, warehouse_id: int | None = None) -> str | None:
    """HTML-табло активных сборок для закреплённого сообщения в Telegram.

    Шапка-сводка (тоталы + сплит статусов) → строка-светофор (🔴 просрочено /
    🟠 стареет / 🚚 забор сегодня · Σ₽) → секции «машина → готово → в работе»,
    срочное наверх, тело каждой в сворачиваемом <blockquote expandable>. Строка:
    «[маркер] номер · город · кол-во · возраст/план · склад ФФ». Только наши
    документы, без HTTP. None — активных сборок нет (вызывающий табло не трогает).

    warehouse_id=None — все склады проекта (общее табло); иначе только заявки
    этого склада ФФ (чат конкретного ФФ, напр. Газпром) — без хвоста-склада,
    с именем склада в заголовке.
    """
    conditions = [
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,
        Warehouse.is_deleted == False,
        AssemblyRequest.status.in_(_BOARD_STATUSES),
    ]
    if warehouse_id is not None:
        conditions.append(AssemblyRequest.warehouse_id == warehouse_id)
    result = await db.execute(
        select(
            AssemblyRequest.status,
            Warehouse.name,
            AssemblyRequest.number,
            AssemblyRequest.created_at,
            AssemblyRequest.estimated_ready_date,
            AssemblyRequest.actual_ready_date,
            AssemblyRequest.pickup_date,
            AssemblyRequest.pickup_time_slot,
            AssemblyRequest.pickup_cost,
            AssemblyRequest.vehicle_brand,
            WbFboSupply.warehouse_name,
            AssemblyRequest.wb_warehouse_name_manual,
            func.coalesce(func.sum(AssemblyRequestItem.quantity), 0),
        )
        .join(Warehouse, Warehouse.id == AssemblyRequest.warehouse_id)
        .outerjoin(
            WbFboSupply,
            and_(
                WbFboSupply.id == AssemblyRequest.wb_fbo_supply_id,
                WbFboSupply.project_id == project_id,
            ),
        )
        .outerjoin(AssemblyRequestItem, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .where(*conditions)
        .group_by(AssemblyRequest.id, Warehouse.name, WbFboSupply.warehouse_name)
        .order_by(AssemblyRequest.created_at.desc(), AssemblyRequest.id.desc())
        .limit(_BOARD_FETCH_LIMIT)
    )
    rows = result.all()
    if not rows:
        return None

    now_msk = utcnow() + _MSK_OFFSET
    today = now_msk.date()
    scoped = warehouse_id is not None
    board_wh_name: str | None = None

    by_status: dict[str, list[dict]] = {s: [] for s in _BOARD_STATUSES}
    for (
        status,
        ff_wh,
        number,
        created_at,
        est_date,
        act_date,
        pickup_date,
        pickup_slot,
        pickup_cost,
        vehicle_brand,
        fbo_city,
        manual_city,
        qty,
    ) in rows:
        if board_wh_name is None:
            board_wh_name = ff_wh
        age = _board_age_days(today, status, created_at, act_date)
        overdue = status == AssemblyStatus.IN_PROGRESS.value and est_date is not None and est_date < today
        severity = "red" if (overdue or age >= _BOARD_AGE_RED) else ("orange" if age >= _BOARD_AGE_ORANGE else "")
        city = _board_city(fbo_city, manual_city, ff_wh)
        by_status.setdefault(status, []).append(
            {
                "status": status,
                "number": str(number),
                "qty": int(qty or 0),
                "age": age,
                "overdue": overdue,
                "severity": severity,
                "est_date": est_date,
                "city": city,
                "ff_hint": ff_wh if (ff_wh and ff_wh != city) else None,
                "pickup_date": pickup_date,
                "pickup_slot": pickup_slot,
                "pickup_cost": pickup_cost,
                "vehicle_brand": vehicle_brand,
            }
        )

    all_items = [it for items in by_status.values() for it in items]
    total_qty = sum(it["qty"] for it in all_items)
    n_prog = len(by_status.get(AssemblyStatus.IN_PROGRESS.value, []))
    n_ready = len(by_status.get(AssemblyStatus.READY.value, []))
    veh_items = by_status.get(AssemblyStatus.VEHICLE_ASSIGNED.value, [])
    n_overdue = sum(1 for it in all_items if it["overdue"])
    n_aging = sum(1 for it in all_items if it["severity"] and not it["overdue"])
    pickup_today = [it for it in veh_items if it["pickup_date"] == today]
    sum_pickup_today = sum(int(it["pickup_cost"]) for it in pickup_today if it["pickup_cost"] is not None)

    title = "📋 <b>Заявки ФФ</b>"
    if scoped and board_wh_name:
        title = f"📋 <b>Заявки ФФ — {html.escape(board_wh_name)}</b>"
    lines = [
        f"{title} · {len(all_items)} · {_board_fmt_qty(total_qty)} шт",
        f"🔧 {n_prog} · ✅ {n_ready} · 🚚 {len(veh_items)} · <i>обновлено {now_msk:%d.%m %H:%M} МСК</i>",
        "",
    ]
    sig: list[str] = []
    if n_overdue:
        sig.append(f"🔴 <b>Просрочено {n_overdue}</b>")
    if n_aging:
        sig.append(f"🟠 стареет {n_aging}")
    if pickup_today:
        seg = f"🚚 забор сегодня {len(pickup_today)}"
        if sum_pickup_today:
            seg += f" · <b>{_board_fmt_qty(sum_pickup_today)} ₽</b>"
        sig.append(seg)
    lines.append("<blockquote>" + (" · ".join(sig) if sig else "✅ всё по графику") + "</blockquote>")
    lines.append("")

    for status in _BOARD_STATUSES:
        items = by_status.get(status, [])
        if not items:
            continue
        items = _board_sort(status, items, today)
        header = f"{_BOARD_STATUS_LABEL[status]} · {len(items)} · {_board_fmt_qty(sum(it['qty'] for it in items))} шт"
        used = len("\n".join(lines))
        body: list[str] = []
        for it in items:
            rendered = _board_render_item(it, today, scoped)
            projected = used + len(header) + len("\n".join([*body, rendered])) + 40
            if len(body) >= _BOARD_ROWS_PER_STATUS or projected > _BOARD_CHAR_BUDGET:
                break
            body.append(rendered)
        if len(body) < len(items):
            body.append(f"…ещё {len(items) - len(body)}")
        lines.append(f"{header}\n<blockquote expandable>" + "\n".join(body) + "</blockquote>")
        lines.append("")
    return "\n".join(lines).strip()


async def _collect_inbound_accept_candidates(
    db: AsyncSession,
    project_id: int,
    ff_requests: list[FulfillmentRequest],
) -> list[int]:
    """receipt_id'ы наших EXPECTED/DRAFT приёмок, чьи ФФ-заявки приняты на остатки.

    Только СБОР id под синк-транзакцией; сам приём (accept_receipt — постит
    сток, лочит строку, коммитит) делается ПОСЛЕ commit синка, отдельными
    транзакциями. expired/просрочена не исключаем (как и в авто-READY сборок —
    приёмка активна). ACCEPTED/CANCELLED приёмки отфильтровываем здесь, чтобы
    зря не дёргать accept_receipt (он на них бросит ValueError).
    """
    receipt_ids = {
        req.inbound_receipt_id
        for req in ff_requests
        if req.inbound_receipt_id is not None and _inbound_accept_signal(req.is_completed)
    }
    if not receipt_ids:
        return []
    result = await db.execute(
        select(InboundReceipt.id).where(
            InboundReceipt.project_id == project_id,
            InboundReceipt.id.in_(receipt_ids),
            InboundReceipt.is_deleted == False,
            InboundReceipt.status.in_((InboundStatus.DRAFT.value, InboundStatus.EXPECTED.value)),
        )
    )
    return [row[0] for row in result.all()]


def _parse_date(value: object) -> date | None:
    """'2026-06-10' → date; мусор/None → None."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ─── Stocks view ─────────────────────────────────────────────────────────────


async def list_stocks(db: AsyncSession, project_id: int, warehouse_id: int) -> dict:
    """UNION остатков ФФ и нашего склада по barcode (FfStocksResponse shape).

    diff = ff_good - our_quantity; сортировка diff desc, затем barcode.
    """
    result = await db.execute(
        select(FulfillmentStock)
        .where(
            FulfillmentStock.project_id == project_id,
            FulfillmentStock.warehouse_id == warehouse_id,
        )
        .limit(STOCKS_LIMIT)
    )
    ff_rows = list(result.scalars().all())

    ws_result = await db.execute(
        select(WarehouseStock)
        .where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id == warehouse_id,
            (WarehouseStock.quantity > 0) | (WarehouseStock.defect_quantity > 0),
        )
        .limit(STOCKS_LIMIT)
    )
    our_rows = list(ws_result.scalars().all())

    # article_seller / subject / brand одним запросом для всех номенклатур (без N+1)
    nom_ids = {r.nomenclature_id for r in ff_rows if r.nomenclature_id}
    nom_ids |= {r.nomenclature_id for r in our_rows if r.nomenclature_id}
    nom_by_id: dict[int, tuple[str | None, str | None, str | None]] = {}
    if nom_ids:
        nom_result = await db.execute(
            select(
                Nomenclature.id,
                Nomenclature.article_seller,
                Nomenclature.subject,
                Nomenclature.brand,
            ).where(
                Nomenclature.project_id == project_id,
                Nomenclature.id.in_(nom_ids),
            )
        )
        nom_by_id = {row.id: (row.article_seller, row.subject, row.brand) for row in nom_result.all()}

    def _nom_fields(nom_id: int | None) -> tuple[str | None, str | None, str | None]:
        return nom_by_id.get(nom_id, (None, None, None)) if nom_id else (None, None, None)

    def _new_row(barcode: str, nom_id: int | None) -> dict:
        article, subject, brand = _nom_fields(nom_id)
        return {
            "barcode": barcode,
            "name": None,
            "vendor_code": None,
            "nomenclature_id": nom_id,
            "article_seller": article,
            "subject": subject,
            "brand": brand,
            "ff_good": 0,
            "ff_reserve": 0,
            "ff_defect": 0,
            "ff_nominal": 0,
            "ff_box_units": 0,  # из них пришло коробами (в штуках россыпи)
            "ff_box_count": 0,  # сколько коробов годного
            "our_quantity": 0,
            "our_defect": 0,
            "diff": 0,
        }

    # Свод по эффективному ШК: остаток короба (ITF14) пересчитываем в россыпь
    # (qty × units_per_box) и складываем со строкой россыпи под её ШК (EAN13).
    rows: dict[str, dict] = {}
    ff_keys: set[str] = set()  # ключи со стоком ФФ — для подсчёта unmatched
    for r in ff_rows:
        units = r.units_per_box or 1
        is_box = bool(r.base_barcode)
        key = r.base_barcode or r.barcode
        ff_keys.add(key)
        row = rows.get(key) or rows.setdefault(key, _new_row(key, r.nomenclature_id))
        row["ff_good"] += r.qty_good * units
        row["ff_reserve"] += r.qty_reserve * units
        row["ff_defect"] += r.qty_defect * units
        row["ff_nominal"] += r.qty_nominal * units
        if is_box:
            row["ff_box_units"] += r.qty_good * units
            row["ff_box_count"] += r.qty_good
        # Номенклатура: дозаполняем, если короб появился раньше россыпи
        if row["nomenclature_id"] is None and r.nomenclature_id is not None:
            row["nomenclature_id"] = r.nomenclature_id
            row["article_seller"], row["subject"], row["brand"] = _nom_fields(r.nomenclature_id)
        # Имя/vendor_code: россыпная строка — авторитет, короб лишь заполняет пустое
        if not is_box:
            row["name"] = r.name or row["name"]
            row["vendor_code"] = r.vendor_code or row["vendor_code"]
        else:
            row["name"] = row["name"] or r.name
            row["vendor_code"] = row["vendor_code"] or r.vendor_code

    for wr in our_rows:
        ws_row = rows.get(wr.barcode)
        if ws_row is None:
            ws_row = rows[wr.barcode] = _new_row(wr.barcode, wr.nomenclature_id)
        elif ws_row["nomenclature_id"] is None:
            ws_row["nomenclature_id"] = wr.nomenclature_id
            ws_row["article_seller"], ws_row["subject"], ws_row["brand"] = _nom_fields(wr.nomenclature_id)
        ws_row["our_quantity"] = wr.quantity
        ws_row["our_defect"] = wr.defect_quantity

    for row in rows.values():
        row["diff"] = row["ff_good"] - row["our_quantity"]

    out_rows = sorted(rows.values(), key=lambda x: (-x["diff"], x["barcode"]))
    totals = {
        "ff_good": sum(r["ff_good"] for r in out_rows),
        "ff_reserve": sum(r["ff_reserve"] for r in out_rows),
        "ff_defect": sum(r["ff_defect"] for r in out_rows),
        "ff_box_units": sum(r["ff_box_units"] for r in out_rows),
        "our_quantity": sum(r["our_quantity"] for r in out_rows),
        "diff": sum(r["diff"] for r in out_rows),
        "unmatched": sum(1 for k in ff_keys if rows[k]["nomenclature_id"] is None),
    }
    synced_at = max((r.synced_at for r in ff_rows), default=None)
    return {
        "rows": out_rows,
        "totals": totals,
        "synced_at": synced_at,
        "subjects": sorted({r["subject"] for r in out_rows if r["subject"]}),
        "brands": sorted({r["brand"] for r in out_rows if r["brand"]}),
    }


def _is_itf14(barcode: str | None) -> bool:
    """ШК короба: 14-значный ITF14 (по нему ловим коробá, не выведенные авто)."""
    return barcode is not None and len(barcode) == 14 and barcode.isdigit()


async def _load_box_overrides(db: AsyncSession, project_id: int, warehouse_id: int) -> dict[str, tuple[str, int]]:
    """{box_barcode: (base_barcode, units_per_box)} ручных сопоставлений склада."""
    result = await db.execute(
        select(
            FulfillmentBoxOverride.box_barcode,
            FulfillmentBoxOverride.base_barcode,
            FulfillmentBoxOverride.units_per_box,
        ).where(
            FulfillmentBoxOverride.project_id == project_id,
            FulfillmentBoxOverride.warehouse_id == warehouse_id,
        )
    )
    return {bc: (base, units) for bc, base, units in result.all()}


async def list_box_packs(db: AsyncSession, project_id: int, warehouse_id: int) -> list[dict]:
    """Сопоставление короб→россыпь: все коробá склада (ITF14 или с base_barcode).

    Read-only вид: ШК короба → ШК россыпи → штук в коробе → наша номенклатура.
    source: auto — выведено автоматически; manual — ручной override; unmapped —
    короб (ITF14) без сопоставления (надо указать вручную). По убыванию остатка
    в штуках. Прочие провайдеры — пусто (нет коробов).
    """
    result = await db.execute(
        select(FulfillmentStock)
        .where(
            FulfillmentStock.project_id == project_id,
            FulfillmentStock.warehouse_id == warehouse_id,
        )
        .limit(STOCKS_LIMIT)
    )
    rows = [r for r in result.scalars().all() if r.base_barcode or _is_itf14(r.barcode)]

    overrides = await _load_box_overrides(db, project_id, warehouse_id)

    nom_ids = {r.nomenclature_id for r in rows if r.nomenclature_id}
    nom_by_id: dict[int, tuple[str | None, str | None]] = {}
    if nom_ids:
        nom_result = await db.execute(
            select(Nomenclature.id, Nomenclature.article_seller, Nomenclature.subject).where(
                Nomenclature.project_id == project_id,
                Nomenclature.id.in_(nom_ids),
            )
        )
        nom_by_id = {row.id: (row.article_seller, row.subject) for row in nom_result.all()}

    out: list[dict] = []
    for r in rows:
        article, subject = nom_by_id.get(r.nomenclature_id, (None, None)) if r.nomenclature_id else (None, None)
        if r.barcode in overrides:
            source = "manual"
        elif r.base_barcode:
            source = "auto"
        else:
            source = "unmapped"
        out.append(
            {
                "box_barcode": r.barcode,
                "base_barcode": r.base_barcode,
                "units_per_box": r.units_per_box,
                "name": r.name,
                "nomenclature_id": r.nomenclature_id,
                "article_seller": article,
                "subject": subject,
                "box_qty": r.qty_good,
                "units_qty": r.qty_good * r.units_per_box if r.base_barcode else 0,
                "matched": r.nomenclature_id is not None,
                "source": source,
            }
        )
    out.sort(key=lambda x: (-x["units_qty"], -x["box_qty"], x["box_barcode"]))
    return out


async def search_nomenclature(db: AsyncSession, project_id: int, query: str, limit: int = 20) -> list[dict]:
    """Поиск нашей номенклатуры с ШК (для ручной привязки короба). Только с barcode."""
    q = (query or "").strip()
    stmt = select(Nomenclature.id, Nomenclature.barcode, Nomenclature.article_seller, Nomenclature.subject).where(
        Nomenclature.project_id == project_id,
        Nomenclature.barcode.is_not(None),
        Nomenclature.barcode != "",
    )
    if q:
        like = f"%{_escape_like(q)}%"
        stmt = stmt.where(Nomenclature.article_seller.ilike(like) | Nomenclature.barcode.ilike(like))
    stmt = stmt.order_by(Nomenclature.article_seller).limit(min(limit, 50))
    result = await db.execute(stmt)
    return [{"id": nid, "barcode": bc, "article_seller": art, "subject": subj} for nid, bc, art, subj in result.all()]


async def set_box_override(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    box_barcode: str,
    nomenclature_id: int,
    units_per_box: int,
) -> dict | None:
    """Ручное сопоставление короба с нашей номенклатурой (+ штук в коробе).

    Привязка только к существующей номенклатуре с ШК (россыпь). Применяется
    сразу к текущему остатку (пересинк не нужен) и побеждает авто-вывод при
    следующих синках. Возвращает обновлённую строку box-pack (или None — короб
    не найден в остатках, но override сохранён для будущих синков).
    """
    box_barcode = (box_barcode or "").strip()
    if not box_barcode:
        raise ValueError("Не указан ШК короба")
    if units_per_box < 1:
        raise ValueError("Кол-во в коробе должно быть ≥ 1")

    nom = await db.execute(
        select(Nomenclature.id, Nomenclature.barcode).where(
            Nomenclature.project_id == project_id,
            Nomenclature.id == nomenclature_id,
        )
    )
    nom_row = nom.first()
    if nom_row is None:
        raise ValueError("Номенклатура не найдена")
    base_barcode = (nom_row.barcode or "").strip()
    if not base_barcode:
        raise ValueError("У выбранной номенклатуры нет ШК (россыпи) — заполните его сначала")

    existing = await db.execute(
        select(FulfillmentBoxOverride).where(
            FulfillmentBoxOverride.project_id == project_id,
            FulfillmentBoxOverride.warehouse_id == warehouse_id,
            FulfillmentBoxOverride.box_barcode == box_barcode,
        )
    )
    override = existing.scalar_one_or_none()
    if override is None:
        override = FulfillmentBoxOverride(
            project_id=project_id,
            warehouse_id=warehouse_id,
            box_barcode=box_barcode,
            nomenclature_id=nomenclature_id,
            base_barcode=base_barcode,
            units_per_box=units_per_box,
        )
        db.add(override)
    else:
        override.nomenclature_id = nomenclature_id
        override.base_barcode = base_barcode
        override.units_per_box = units_per_box

    # Применить сразу к текущему остатку короба (если он есть в снапшоте)
    await db.execute(
        update(FulfillmentStock)
        .where(
            FulfillmentStock.project_id == project_id,
            FulfillmentStock.warehouse_id == warehouse_id,
            FulfillmentStock.barcode == box_barcode,
        )
        .values(base_barcode=base_barcode, units_per_box=units_per_box, nomenclature_id=nomenclature_id)
    )
    await db.commit()
    return await _box_pack_row(db, project_id, warehouse_id, box_barcode)


async def delete_box_override(db: AsyncSession, project_id: int, warehouse_id: int, box_barcode: str) -> dict | None:
    """Снять ручное сопоставление — короб вернётся к авто-выводу."""
    box_barcode = (box_barcode or "").strip()
    await db.execute(
        delete(FulfillmentBoxOverride).where(
            FulfillmentBoxOverride.project_id == project_id,
            FulfillmentBoxOverride.warehouse_id == warehouse_id,
            FulfillmentBoxOverride.box_barcode == box_barcode,
        )
    )
    # Вернуть текущий остаток к авто-выводу (по ШК короба + названию)
    stock = await db.execute(
        select(FulfillmentStock).where(
            FulfillmentStock.project_id == project_id,
            FulfillmentStock.warehouse_id == warehouse_id,
            FulfillmentStock.barcode == box_barcode,
        )
    )
    row = stock.scalar_one_or_none()
    if row is not None:
        pack = _migfull_box_pack(row.barcode, row.name)
        base_barcode = pack[0] if pack else None
        units = pack[1] if pack else 1
        nom_id = None
        if base_barcode:
            nom = await db.execute(
                select(Nomenclature.id).where(
                    Nomenclature.project_id == project_id, Nomenclature.barcode == base_barcode
                )
            )
            nom_id = nom.scalar_one_or_none()
        row.base_barcode = base_barcode
        row.units_per_box = units
        row.nomenclature_id = nom_id
    await db.commit()
    return await _box_pack_row(db, project_id, warehouse_id, box_barcode)


async def _box_pack_row(db: AsyncSession, project_id: int, warehouse_id: int, box_barcode: str) -> dict | None:
    """Одна строка сопоставления (после мутации) или None — короба нет в остатках."""
    packs = await list_box_packs(db, project_id, warehouse_id)
    return next((p for p in packs if p["box_barcode"] == box_barcode), None)


# ─── Requests view + linking ─────────────────────────────────────────────────


def _request_to_dict(
    req: FulfillmentRequest,
    assembly_map: dict | None = None,
    inbound_map: dict | None = None,
) -> dict:
    """FulfillmentRequest → FfRequestRow-shaped dict с обогащением связи."""
    linked_number = linked_status = None
    if req.assembly_request_id and assembly_map and req.assembly_request_id in assembly_map:
        linked_number, linked_status = assembly_map[req.assembly_request_id]
    elif req.inbound_receipt_id and inbound_map and req.inbound_receipt_id in inbound_map:
        linked_number, linked_status = inbound_map[req.inbound_receipt_id]
    return {
        "id": req.id,
        "external_id": req.external_id,
        "number": req.number,
        "kind": req.kind,
        "type_name": req.type_name,
        "status": req.status,
        "stage_code": req.stage_code,
        "stage_title": req.stage_title,
        "is_completed": req.is_completed,
        "archived": req.archived,
        "expired": req.expired,
        "ff_status": _ff_status_code(
            req.provider,
            req.kind,
            req.stage_code,
            req.stage_title,
            bool(req.is_completed),
            bool(req.archived),
            bool(req.expired),
        ),
        "local_archived": req.local_archived,
        "local_archived_at": req.local_archived_at,
        "total_qty": req.total_qty,
        "dest_warehouse": req.dest_warehouse,
        "external_created_at": req.external_created_at,
        "synced_at": req.synced_at,
        "assembly_request_id": req.assembly_request_id,
        "inbound_receipt_id": req.inbound_receipt_id,
        "linked_number": linked_number,
        "linked_status": linked_status,
    }


async def list_requests(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    kind: str | None = None,
    show_archived: bool = False,
) -> list[dict]:
    """Зеркало заявок ФФ с обогащением linked_number/linked_status (без N+1).

    show_archived=False (дефолт) — только НЕ архивные локально;
    show_archived=True — только локальный архив (вид «Архив»).
    """
    q = select(FulfillmentRequest).where(
        FulfillmentRequest.project_id == project_id,
        FulfillmentRequest.warehouse_id == warehouse_id,
        FulfillmentRequest.local_archived == show_archived,
    )
    if kind:
        q = q.where(FulfillmentRequest.kind == kind)
    q = q.order_by(
        FulfillmentRequest.external_created_at.desc().nullslast(),
        FulfillmentRequest.id.desc(),
    ).limit(REQUESTS_LIMIT)
    result = await db.execute(q)
    requests = list(result.scalars().all())

    assembly_ids = {r.assembly_request_id for r in requests if r.assembly_request_id}
    inbound_ids = {r.inbound_receipt_id for r in requests if r.inbound_receipt_id}

    assembly_map: dict[int, tuple] = {}
    if assembly_ids:
        result = await db.execute(
            select(AssemblyRequest.id, AssemblyRequest.number, AssemblyRequest.status).where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.id.in_(assembly_ids),
                AssemblyRequest.is_deleted == False,
            )
        )
        assembly_map = {row[0]: (row[1], row[2]) for row in result.all()}

    inbound_map: dict[int, tuple] = {}
    if inbound_ids:
        result = await db.execute(
            select(InboundReceipt.id, InboundReceipt.number, InboundReceipt.status).where(
                InboundReceipt.project_id == project_id,
                InboundReceipt.id.in_(inbound_ids),
                InboundReceipt.is_deleted == False,
            )
        )
        inbound_map = {row[0]: (row[1], row[2]) for row in result.all()}

    return [_request_to_dict(r, assembly_map, inbound_map) for r in requests]


# ─── История смены статусов (журнал синка) ──────────────────────────────────

STATUS_EVENTS_LIMIT = 1000


def _status_event_to_dict(e: FulfillmentStatusEvent, req_info: dict | None = None) -> dict:
    info = req_info or {}
    return {
        "id": e.id,
        "fulfillment_request_id": e.fulfillment_request_id,
        "external_id": e.external_id,
        "number": e.number,
        "kind": e.kind,
        "provider": e.provider,
        "event_type": e.event_type,
        "old_status": e.old_status,
        "new_status": e.new_status,
        "old_stage_code": e.old_stage_code,
        "new_stage_code": e.new_stage_code,
        "old_stage_title": e.old_stage_title,
        "new_stage_title": e.new_stage_title,
        "old_is_completed": e.old_is_completed,
        "new_is_completed": e.new_is_completed,
        "old_archived": e.old_archived,
        "new_archived": e.new_archived,
        "changed_at": e.changed_at,
        # Обогащение из текущей заявки ФФ (склад сдачи / кол-во / наша заявка)
        "dest_warehouse": info.get("dest_warehouse"),
        "total_qty": info.get("total_qty"),
        "linked_number": info.get("linked_number"),
    }


async def list_status_events(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    kind: str | None = None,
    ff_request_id: int | None = None,
) -> list[dict]:
    """Журнал смены статусов заявок ФФ склада, новые сверху (FfStatusEvent shape).

    kind — фильтр по типу заявки (assembly|inbound|other); ff_request_id —
    история конкретной заявки (для деталки).
    """
    q = select(FulfillmentStatusEvent).where(
        FulfillmentStatusEvent.project_id == project_id,
        FulfillmentStatusEvent.warehouse_id == warehouse_id,
    )
    if kind:
        q = q.where(FulfillmentStatusEvent.kind == kind)
    if ff_request_id is not None:
        q = q.where(FulfillmentStatusEvent.fulfillment_request_id == ff_request_id)
    q = q.order_by(
        FulfillmentStatusEvent.changed_at.desc(),
        FulfillmentStatusEvent.id.desc(),
    ).limit(STATUS_EVENTS_LIMIT)
    result = await db.execute(q)
    events = list(result.scalars().all())

    # Обогащение колонок истории (склад сдачи / кол-во / наша заявка) из ТЕКУЩЕЙ
    # заявки ФФ — событие хранит лишь снимок стадии. Линк резолвим как в
    # list_requests (один запрос на тип, без N+1).
    req_info = await _events_request_info(db, project_id, {e.fulfillment_request_id for e in events})
    return [_status_event_to_dict(e, req_info.get(e.fulfillment_request_id)) for e in events]


async def _events_request_info(db: AsyncSession, project_id: int, req_ids: set[int]) -> dict[int, dict]:
    """{ff_request_id: {dest_warehouse, total_qty, linked_number}} для строк истории."""
    if not req_ids:
        return {}
    rows = (
        await db.execute(
            select(
                FulfillmentRequest.id,
                FulfillmentRequest.dest_warehouse,
                FulfillmentRequest.total_qty,
                FulfillmentRequest.assembly_request_id,
                FulfillmentRequest.inbound_receipt_id,
            ).where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.id.in_(req_ids),
            )
        )
    ).all()

    assembly_ids = {r.assembly_request_id for r in rows if r.assembly_request_id}
    inbound_ids = {r.inbound_receipt_id for r in rows if r.inbound_receipt_id}
    assembly_map: dict[int, str] = {}
    if assembly_ids:
        ares = await db.execute(
            select(AssemblyRequest.id, AssemblyRequest.number).where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.id.in_(assembly_ids),
                AssemblyRequest.is_deleted == False,
            )
        )
        assembly_map = {row[0]: row[1] for row in ares.all()}
    inbound_map: dict[int, str] = {}
    if inbound_ids:
        ires = await db.execute(
            select(InboundReceipt.id, InboundReceipt.number).where(
                InboundReceipt.project_id == project_id,
                InboundReceipt.id.in_(inbound_ids),
                InboundReceipt.is_deleted == False,
            )
        )
        inbound_map = {row[0]: row[1] for row in ires.all()}

    info: dict[int, dict] = {}
    for r in rows:
        linked = assembly_map.get(r.assembly_request_id) if r.assembly_request_id else None
        if linked is None and r.inbound_receipt_id:
            linked = inbound_map.get(r.inbound_receipt_id)
        info[r.id] = {
            "dest_warehouse": r.dest_warehouse,
            "total_qty": r.total_qty,
            "linked_number": linked,
        }
    return info


SYNC_RUNS_LIMIT = 100


def _sync_run_to_dict(log: SyncLog) -> dict:
    duration = None
    if log.finished_at and log.started_at:
        duration = (log.finished_at - log.started_at).total_seconds()
    stocks = log.rows_inserted or 0
    total = log.rows_fetched or 0
    return {
        "id": log.id,
        "service": log.service,
        "status": log.status,
        "started_at": log.started_at,
        "finished_at": log.finished_at,
        "stocks_synced": stocks,
        "requests_synced": max(total - stocks, 0),
        "duration_seconds": duration,
        "error_msg": log.error_msg,
    }


async def list_sync_runs(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    limit: int = SYNC_RUNS_LIMIT,
) -> list[dict]:
    """Журнал прогонов ФФ-синка склада (sync_log), новые сверху (FfSyncRun shape).

    sync_log без своего project_id — изоляция арендатора через родительский
    integration_keys (Iron rule #1: JOIN по integration_id, фильтр project_id).
    is_deleted ключа НЕ фильтруем намеренно: переподключение восстанавливает ту
    же строку ключа (integration_id стабилен), и историю прогонов показываем
    целиком, даже если интеграцию когда-то отключали.
    """
    q = (
        select(SyncLog)
        .join(IntegrationKey, SyncLog.integration_id == IntegrationKey.id)
        .where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.warehouse_id == warehouse_id,
            SyncLog.sync_type == "fulfillment",
        )
        .order_by(SyncLog.started_at.desc(), SyncLog.id.desc())
        .limit(limit)
    )
    result = await db.execute(q)
    return [_sync_run_to_dict(log) for log in result.scalars().all()]


# ─── Overview: сводка по всем складам с активной ФФ-интеграцией ─────────────


def _wms_packages(raw: dict | None) -> list[dict]:
    """Короба wms-отгрузки FBO из зеркального raw.

    Состав приходит в `Packages` (заглавная: один товар-dict на короб в `items`);
    lowercase `packages` провайдер отдаёт с пустыми `items` (служебная мета) —
    оставлен fallback'ом на случай дрейфа формата.
    """
    if not raw:
        return []
    packages = raw.get("Packages") or raw.get("packages") or {}
    rows = packages.values() if isinstance(packages, dict) else packages
    return [p for p in rows if isinstance(p, dict)]


def _wms_package_items(pkg: dict) -> list[dict]:
    """Позиции короба: `items` — один dict (Packages) либо список (fallback)."""
    items = pkg.get("items")
    if isinstance(items, dict):
        return [items]
    if isinstance(items, list):
        return [i for i in items if isinstance(i, dict)]
    return []


def _raw_assembly_composition(provider: str, raw: dict | None) -> dict[str, int]:
    """{barcode: qty} из raw зеркала assembly-заявки.

    Только wmscelicom: dispatchorders отдают состав в top-level `items[]`. Если
    его нет — fallback на легаси-формат shipmentsfbo (`Packages` → items), на
    случай ещё не вычищенных старых строк. У skladbot состава в списке нет
    (только в живой деталке) → пустой dict.
    """
    if provider != "wmscelicom" or not raw:
        return {}
    out: dict[str, int] = {}
    items = raw.get("items")
    if isinstance(items, list):  # DispatchOrder
        for item in items:
            if not isinstance(item, dict):  # PHP-API: бывают null-элементы
                continue
            barcode = str(item.get("barcode") or "").strip()
            if barcode:
                out[barcode] = out.get(barcode, 0) + _safe_int(item.get("count"))
        return out
    for pkg in _wms_packages(raw):  # legacy shipmentsfbo
        for item in _wms_package_items(pkg):
            barcode = str(item.get("barcode") or "").strip()
            if not barcode:
                continue
            out[barcode] = out.get(barcode, 0) + _safe_int(item.get("count"))
    return out


def _suggest_for_request(
    ff_created: date,
    ff_comp: dict[str, int],
    candidates: list[AssemblyRequest],
    items_by_candidate: dict[int, dict[str, int]],
) -> list[dict]:
    """Топ-кандидаты мэтчинга для одной ФФ-заявки (FfMatchSuggestion shapes).

    score = date_score (0/1/2 дн → 70/55/40, дальше отсев)
          + barcode-бонус (доля пересечения ШК, Jaccard × 30; только при
            составе с обеих сторон)
          + qty-бонус 10 (суммарное qty в ±10%, когда qty есть в raw);
    cap 100, порог _SUGGEST_MIN_SCORE, топ _SUGGEST_TOP_N по score.
    """
    ff_total = sum(ff_comp.values())
    scored: list[tuple[int, int, int, dict]] = []
    for cand in candidates:
        if cand.created_at is None:
            continue
        diff_days = abs((ff_created - cand.created_at.date()).days)
        date_score = _SUGGEST_DATE_SCORES.get(diff_days)
        if date_score is None:
            continue

        score = date_score
        reason_parts = ["дата совпадает" if diff_days == 0 else f"дата ±{diff_days} дн"]

        cand_items = items_by_candidate.get(cand.id, {})
        if ff_comp and cand_items:
            inter = set(ff_comp) & set(cand_items)
            share = len(inter) / len(set(ff_comp) | set(cand_items))
            bonus = round(share * 30)
            if bonus:
                score += bonus
                reason_parts.append(f"ШК {round(share * 100)}%")

        cand_total = sum(cand_items.values())
        if ff_total > 0 and cand_total > 0 and abs(cand_total - ff_total) <= 0.1 * ff_total:
            score += 10
            reason_parts.append("кол-во ±10%")

        score = min(100, score)
        if score < _SUGGEST_MIN_SCORE:
            continue
        scored.append(
            (
                score,
                diff_days,
                -cand.id,
                {
                    "assembly_request_id": cand.id,
                    "number": cand.number,
                    "status": cand.status,
                    "created_at": cand.created_at,
                    "total_qty": cand_total,
                    "score": score,
                    "reason": ", ".join(reason_parts),
                },
            )
        )
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [t[3] for t in scored[:_SUGGEST_TOP_N]]


async def _load_match_suggestions(
    db: AsyncSession,
    project_id: int,
    requests: list[FulfillmentRequest],
) -> dict[int, list[dict]]:
    """{ff_request_id: [FfMatchSuggestion]} для несвязанных активных assembly-заявок.

    Эвристика работает ТОЛЬКО по зеркалу и нашей БД (без HTTP к провайдерам);
    кандидаты и их позиции грузятся пачками — без N+1.
    """
    targets = [
        r
        for r in requests
        if r.kind == FfRequestKind.ASSEMBLY.value
        and r.assembly_request_id is None
        and not r.archived
        and not r.local_archived
        and not r.is_completed
        and r.external_created_at is not None
    ]
    if not targets:
        return {}

    # Кандидаты: активные сборки тех же складов, ещё не связанные ни с одной ФФ-заявкой
    linked_subq = select(FulfillmentRequest.assembly_request_id).where(
        FulfillmentRequest.project_id == project_id,
        FulfillmentRequest.assembly_request_id.is_not(None),
    )
    result = await db.execute(
        select(AssemblyRequest)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.warehouse_id.in_({r.warehouse_id for r in targets}),
            AssemblyRequest.is_deleted == False,
            AssemblyRequest.status.in_(_SUGGEST_CANDIDATE_STATUSES),
            AssemblyRequest.id.not_in(linked_subq),
        )
        .limit(_SUGGEST_CANDIDATES_LIMIT)
    )
    candidates = list(result.scalars().all())
    if not candidates:
        return {}

    candidates_by_wh: dict[int, list[AssemblyRequest]] = {}
    for cand in candidates:
        candidates_by_wh.setdefault(cand.warehouse_id, []).append(cand)

    items_result = await db.execute(
        select(
            AssemblyRequestItem.assembly_request_id,
            AssemblyRequestItem.barcode,
            func.sum(AssemblyRequestItem.quantity),
        )
        .where(
            AssemblyRequestItem.project_id == project_id,
            AssemblyRequestItem.assembly_request_id.in_([c.id for c in candidates]),
        )
        .group_by(AssemblyRequestItem.assembly_request_id, AssemblyRequestItem.barcode)
    )
    items_by_candidate: dict[int, dict[str, int]] = {}
    for cand_id, barcode, qty in items_result.all():
        items_by_candidate.setdefault(cand_id, {})[barcode] = int(qty or 0)

    out: dict[int, list[dict]] = {}
    for r in targets:
        ff_created = r.external_created_at
        if ff_created is None:  # сужение типа для mypy: отфильтровано выше
            continue
        out[r.id] = _suggest_for_request(
            ff_created,
            _raw_assembly_composition(r.provider, r.raw),
            candidates_by_wh.get(r.warehouse_id, []),
            items_by_candidate,
        )
    return out


async def get_overview(
    db: AsyncSession,
    project_id: int,
    kind: str = FfRequestKind.ASSEMBLY.value,
    warehouse_id: int | None = None,
    only_unlinked: bool = False,
) -> dict:
    """Сводка ФФ по всем складам проекта с активной интеграцией (FfOverviewResponse shape).

    warehouses — ВСЕ интегрированные склады с каунтами активных assembly-заявок
    (независимо от фильтров); requests — зеркало по этим складам с фильтрами
    kind / warehouse_id / only_unlinked (сортировка external_created_at desc,
    limit REQUESTS_LIMIT) + suggestions для несвязанных активных assembly-заявок.

    Каунты и список фильтруются по парам (warehouse_id, provider) АКТИВНЫХ
    ключей: после смены провайдера зеркальные строки старого не синкаются и
    не должны инфлировать requests_total/unlinked.
    """
    integrations = (
        await db.execute(
            select(
                IntegrationKey.warehouse_id,
                IntegrationKey.service,
                IntegrationKey.last_sync_at,
                Warehouse.name,
            )
            .join(Warehouse, Warehouse.id == IntegrationKey.warehouse_id)
            .where(
                IntegrationKey.project_id == project_id,
                IntegrationKey.service.in_(FF_SERVICES),
                IntegrationKey.is_active.is_(True),
                IntegrationKey.is_deleted == False,
                Warehouse.project_id == project_id,
                Warehouse.is_deleted == False,
            )
            .order_by(Warehouse.name, IntegrationKey.warehouse_id)
            .limit(500)
        )
    ).all()
    # Пары (warehouse_id, provider) активных ключей: зеркальные строки старого
    # провайдера (после смены ключа) в каунты/список не попадают.
    wh_provider_pairs = [(row.warehouse_id, row.service) for row in integrations]

    # Каунты активных assembly-заявок по складам — одним агрегатом (без N+1)
    counts: dict[tuple[int, str], tuple[int, int]] = {}
    if wh_provider_pairs:
        counts_result = await db.execute(
            select(
                FulfillmentRequest.warehouse_id,
                FulfillmentRequest.provider,
                func.count(FulfillmentRequest.id),
                func.count(FulfillmentRequest.id).filter(FulfillmentRequest.assembly_request_id.is_(None)),
            )
            .where(
                FulfillmentRequest.project_id == project_id,
                tuple_(FulfillmentRequest.warehouse_id, FulfillmentRequest.provider).in_(wh_provider_pairs),
                FulfillmentRequest.kind == FfRequestKind.ASSEMBLY.value,
                FulfillmentRequest.archived == False,
                FulfillmentRequest.is_completed == False,
                FulfillmentRequest.local_archived == False,
            )
            .group_by(FulfillmentRequest.warehouse_id, FulfillmentRequest.provider)
        )
        counts = {(wid, prov): (total, unlinked) for wid, prov, total, unlinked in counts_result.all()}

    warehouses = [
        {
            "warehouse_id": row.warehouse_id,
            "warehouse_name": row.name,
            "provider": row.service,
            "provider_label": _provider_human(row.service),
            "last_sync_at": row.last_sync_at,
            "requests_total": counts.get((row.warehouse_id, row.service), (0, 0))[0],
            "requests_unlinked": counts.get((row.warehouse_id, row.service), (0, 0))[1],
        }
        for row in integrations
    ]

    # Заявки зеркала — только по интегрированным складам (активный провайдер), с фильтрами
    target_pairs = [(wid, svc) for wid, svc in wh_provider_pairs if warehouse_id is None or wid == warehouse_id]
    requests: list[FulfillmentRequest] = []
    if target_pairs:
        q = select(FulfillmentRequest).where(
            FulfillmentRequest.project_id == project_id,
            tuple_(FulfillmentRequest.warehouse_id, FulfillmentRequest.provider).in_(target_pairs),
            FulfillmentRequest.kind == kind,
            FulfillmentRequest.local_archived == False,
        )
        if only_unlinked:
            if kind == FfRequestKind.INBOUND.value:
                q = q.where(FulfillmentRequest.inbound_receipt_id.is_(None))
            elif kind == FfRequestKind.ASSEMBLY.value:
                q = q.where(FulfillmentRequest.assembly_request_id.is_(None))
            else:
                q = q.where(
                    FulfillmentRequest.assembly_request_id.is_(None),
                    FulfillmentRequest.inbound_receipt_id.is_(None),
                )
        q = q.order_by(
            FulfillmentRequest.external_created_at.desc().nullslast(),
            FulfillmentRequest.id.desc(),
        ).limit(REQUESTS_LIMIT)
        requests = list((await db.execute(q)).scalars().all())

    # Обогащение связями — как в list_requests (по одному запросу на тип)
    assembly_ids = {r.assembly_request_id for r in requests if r.assembly_request_id}
    inbound_ids = {r.inbound_receipt_id for r in requests if r.inbound_receipt_id}

    assembly_map: dict[int, tuple] = {}
    if assembly_ids:
        result = await db.execute(
            select(AssemblyRequest.id, AssemblyRequest.number, AssemblyRequest.status).where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.id.in_(assembly_ids),
                AssemblyRequest.is_deleted == False,
            )
        )
        assembly_map = {row[0]: (row[1], row[2]) for row in result.all()}

    inbound_map: dict[int, tuple] = {}
    if inbound_ids:
        result = await db.execute(
            select(InboundReceipt.id, InboundReceipt.number, InboundReceipt.status).where(
                InboundReceipt.project_id == project_id,
                InboundReceipt.id.in_(inbound_ids),
                InboundReceipt.is_deleted == False,
            )
        )
        inbound_map = {row[0]: (row[1], row[2]) for row in result.all()}

    suggestions_by_request = await _load_match_suggestions(db, project_id, requests)

    wh_name_by_id = {row.warehouse_id: row.name for row in integrations}
    out_requests = []
    for r in requests:
        row_dict = _request_to_dict(r, assembly_map, inbound_map)
        row_dict.update(
            {
                "warehouse_id": r.warehouse_id,
                "warehouse_name": wh_name_by_id.get(r.warehouse_id, ""),
                "provider": r.provider,
                "suggestions": suggestions_by_request.get(r.id, []),
            }
        )
        out_requests.append(row_dict)

    return {"warehouses": warehouses, "requests": out_requests}


# ─── Несвязанные наши заявки на сборку (обратный линк ФФ → ASM) ──────────────

UNLINKED_ASSEMBLIES_LIMIT = 500
_UNLINKED_ASSEMBLY_STATUSES = (
    AssemblyStatus.IN_PROGRESS.value,
    AssemblyStatus.READY.value,
    AssemblyStatus.VEHICLE_ASSIGNED.value,
)


async def list_unlinked_assemblies(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    limit: int = UNLINKED_ASSEMBLIES_LIMIT,
) -> list[dict]:
    """Активные сборки склада без связанной ФФ-заявки (FfUnlinkedAssembly shape).

    Статусы IN_PROGRESS/READY/VEHICLE_ASSIGNED, у которых НЕ существует
    FulfillmentRequest с assembly_request_id == ar.id (project-scoped,
    коррелированный ~exists). total_qty/brands — двумя batch-агрегатами по
    выбранным id (без N+1); brand берётся из Nomenclature позиций, как в
    assembly._build_items_with_stock. Сортировка created_at desc, limit.
    """
    linked = exists().where(
        FulfillmentRequest.project_id == project_id,
        FulfillmentRequest.assembly_request_id == AssemblyRequest.id,
    )
    result = await db.execute(
        select(
            AssemblyRequest.id,
            AssemblyRequest.number,
            AssemblyRequest.status,
            AssemblyRequest.estimated_ready_date,
            AssemblyRequest.created_at,
            # Склад сдачи МП: warehouse_name связанной FBO-поставки, иначе ручной
            WbFboSupply.warehouse_name,
            AssemblyRequest.wb_warehouse_name_manual,
        )
        .outerjoin(
            WbFboSupply,
            and_(
                WbFboSupply.id == AssemblyRequest.wb_fbo_supply_id,
                WbFboSupply.project_id == project_id,
            ),
        )
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.warehouse_id == warehouse_id,
            AssemblyRequest.is_deleted == False,
            AssemblyRequest.status.in_(_UNLINKED_ASSEMBLY_STATUSES),
            ~linked,
        )
        .order_by(AssemblyRequest.created_at.desc(), AssemblyRequest.id.desc())
        .limit(limit)
    )
    docs = result.all()
    if not docs:
        return []

    doc_ids = [d.id for d in docs]

    # total_qty: сумма quantity позиций по заявке — одним агрегатом
    qty_result = await db.execute(
        select(
            AssemblyRequestItem.assembly_request_id,
            func.sum(AssemblyRequestItem.quantity),
        )
        .where(
            AssemblyRequestItem.project_id == project_id,
            AssemblyRequestItem.assembly_request_id.in_(doc_ids),
        )
        .group_by(AssemblyRequestItem.assembly_request_id)
    )
    qty_by_doc = {doc_id: int(qty or 0) for doc_id, qty in qty_result.all()}

    # brands: distinct бренд номенклатуры позиций по заявке — одним JOIN-агрегатом
    brand_result = await db.execute(
        select(
            AssemblyRequestItem.assembly_request_id,
            Nomenclature.brand,
        )
        .join(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
        .where(
            AssemblyRequestItem.project_id == project_id,
            AssemblyRequestItem.assembly_request_id.in_(doc_ids),
            Nomenclature.brand.is_not(None),
        )
        .distinct()
    )
    brands_by_doc: dict[int, set[str]] = {}
    for doc_id, brand in brand_result.all():
        if brand:
            brands_by_doc.setdefault(doc_id, set()).add(brand)

    return [
        {
            "id": d.id,
            "number": d.number,
            "status": d.status,
            "brands": ", ".join(sorted(brands_by_doc.get(d.id, set()))) or None,
            "total_qty": qty_by_doc.get(d.id, 0),
            "dest_warehouse": d.warehouse_name or d.wb_warehouse_name_manual,
            "estimated_ready_date": d.estimated_ready_date,
            "created_at": d.created_at,
        }
        for d in docs
    ]


def _coerce_name(value: object) -> str | None:
    """Провайдер отдаёт исполнителя/создателя то строкой, то объектом {name}."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get("name")
    return str(value)


def _wms_detail_parts(req: FulfillmentRequest) -> tuple[list[dict], list[dict], str | None]:
    """Состав/поля/создатель wmscelicom-заявки из зеркального raw.

    Returns (products, fields, creator); products — {barcode, name, qty,
    comment}. Для отгрузок FBO товары лежат внутри коробов (packages),
    агрегируем по barcode.
    """
    raw = req.raw or {}
    products: list[dict] = []
    fields: list[tuple[str, object]] = []

    if req.kind == FfRequestKind.ASSEMBLY.value:
        by_barcode: dict[str, dict] = {}
        items = raw.get("items")
        if isinstance(items, list):  # DispatchOrder: состав в top-level items[]
            for item in items:
                if not isinstance(item, dict):
                    continue
                barcode = str(item.get("barcode") or "").strip()
                agg = by_barcode.get(barcode)
                if agg is None:
                    agg = by_barcode[barcode] = {"barcode": barcode or None, "name": item.get("name"), "qty": 0}
                agg["qty"] += _safe_int(item.get("count"))
            fields = [
                ("Склад отгрузки", raw.get("warehouse")),
                ("Статус заявки", raw.get("status")),
                ("Статус отгрузки", raw.get("shipment_status")),
                ("Отгрузка создана", raw.get("shipment_create_date")),
                ("Передана", raw.get("shipment_shipped_datetime")),
                ("Внешний ID (СДЭК/Яндекс)", raw.get("externalid")),
            ]
        else:  # legacy shipmentsfbo: состав в коробах (Packages → items)
            pkg_rows = _wms_packages(raw)
            for pkg in pkg_rows:
                for item in _wms_package_items(pkg):
                    barcode = str(item.get("barcode") or "").strip()
                    agg = by_barcode.get(barcode)
                    if agg is None:
                        agg = by_barcode[barcode] = {"barcode": barcode or None, "name": item.get("name"), "qty": 0}
                    agg["qty"] += _safe_int(item.get("count"))
            fields = [
                ("Площадка", raw.get("shipped_target")),
                ("Внешний заказ", raw.get("external_order")),
                ("План отгрузки", raw.get("dispatch_date")),
                ("Передана", raw.get("shipped_datetime")),
                ("Вручена", raw.get("delivered_date_time")),
                ("Коробов", len(pkg_rows) or None),
            ]
        products = list(by_barcode.values())
    else:
        for item in raw.get("items") or []:
            if not isinstance(item, dict):
                continue
            barcode = str(item.get("barcode") or "").strip()
            products.append(
                {
                    "barcode": barcode or None,
                    "name": item.get("name"),
                    "qty": int(item.get("count") or 0),
                    "comment": item.get("comment") or None,
                }
            )
        fields = [
            ("Дата поставки", raw.get("delivery_date_time")),
            ("Статус приёмки", raw.get("unloading_status")),
            ("Приёмка начата", raw.get("unloading_time_start")),
            ("Приёмка завершена", raw.get("unloading_time_end")),
            ("Закрыта", raw.get("unloading_close_date")),
        ]

    user = raw.get("user") or {}
    creator = " ".join(filter(None, (user.get("first_name"), user.get("last_name")))) or None
    out_fields = [{"name": name, "field": None, "value": str(value)} for name, value in fields if value]
    return products, out_fields, creator


def _migfull_line_rows(value: object) -> list[dict]:
    """raw-поле со строками → список dict (защита от мусора/None)."""
    rows = value if isinstance(value, list) else []
    return [r for r in rows if isinstance(r, dict)]


def _migfull_products_from_lines(
    base_lines: list[dict],
    fact_lines: list[dict],
    *,
    fact_field: str,
) -> list[dict]:
    """Строки заявки migfull → позиции по товарам (ключ — product_guid).

    base_lines — заявленное (planned/incoming) → qty; fact_lines — факт
    (shipped → delivery_qty / received → accepted_qty), брак received
    (is_defective) → defect_qty. Служебные позиции отфильтрованы.
    """
    by_guid: dict[str, dict] = {}

    def _slot(line: dict) -> dict | None:
        product = line.get("product")
        if not isinstance(product, dict):
            product = {}
        if _is_migfull_service_item(product):
            return None
        guid = str(line.get("product_guid") or product.get("guid") or "")
        if not guid:
            return None
        slot = by_guid.get(guid)
        if slot is None:
            slot = by_guid[guid] = {
                "guid": guid,
                "name": product.get("name"),
                "color": product.get("color"),
                "size": product.get("size"),
                "qty": 0,
                "accepted_qty": 0,
                "delivery_qty": 0,
                "defect_qty": 0,
            }
        return slot

    for line in base_lines:
        slot = _slot(line)
        if slot is not None:
            slot["qty"] += _safe_int(line.get("quantity"))
    for line in fact_lines:
        slot = _slot(line)
        if slot is None:
            continue
        if line.get("is_defective"):
            slot["defect_qty"] += _safe_int(line.get("quantity"))
        else:
            slot[fact_field] += _safe_int(line.get("quantity"))
    return list(by_guid.values())


def _migfull_detail_fields(req: FulfillmentRequest) -> list[dict]:
    """Динамические поля деталки migfull-заявки из зеркального raw."""
    raw = req.raw or {}
    fields: list[tuple[str, object]]
    if req.kind == FfRequestKind.ASSEMBLY.value:
        marketplace = raw.get("marketplace") if isinstance(raw.get("marketplace"), dict) else {}
        fields = [
            ("Маркетплейс", (marketplace or {}).get("name")),
            ("Склад МП", req.dest_warehouse),
            ("Дата отгрузки", raw.get("shipment_date")),
            ("Прогноз отгрузки", raw.get("shipment_forecast")),
            ("Коробов", raw.get("containers_count")),
            ("Паллет", raw.get("pallets_count")),
            ("План, шт", raw.get("planned_quantity_total")),
            ("Факт, шт", raw.get("shipped_quantity_total")),
            ("Номер поставки", raw.get("client_shipment_number")),
        ]
    else:
        fields = [
            ("Дата поставки", raw.get("submission_date")),
            ("Заметка клиента", raw.get("client_reference")),
            ("Комментарий", raw.get("client_comment")),
            ("Строк в приёмке", raw.get("submission_lines_count")),
        ]
    return [{"name": name, "field": None, "value": str(value)} for name, value in fields if value]


async def _migfull_guid_barcodes(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    guids: set[str],
) -> dict[str, tuple[str, int]]:
    """{product_guid: (barcode, units_per_box)} из зеркала остатков.

    Штрихкоды только в карточке товара — при синке оседают в
    fulfillment_stocks.external_product_id. Для короба отдаём ШК россыпи
    (base_barcode) и штук в коробе → состав заявки сводится к россыпи так же,
    как остатки; для россыпи — сам barcode и units=1.
    """
    guids.discard("")
    if not guids:
        return {}
    result = await db.execute(
        select(
            FulfillmentStock.external_product_id,
            FulfillmentStock.barcode,
            FulfillmentStock.base_barcode,
            FulfillmentStock.units_per_box,
        ).where(
            FulfillmentStock.project_id == project_id,
            FulfillmentStock.warehouse_id == warehouse_id,
            FulfillmentStock.external_product_id.in_(list(guids)),
        )
    )
    out: dict[str, tuple[str, int]] = {}
    for guid, barcode, base_barcode, units in result.all():
        effective = base_barcode or barcode
        if guid and effective:
            out[guid] = (effective, int(units or 1))
    return out


async def _resolve_noms(db: AsyncSession, project_id: int, barcodes: set[str]) -> dict[str, tuple[int, str | None]]:
    """{barcode: (nomenclature_id, article_seller)} одним запросом."""
    barcodes.discard("")
    if not barcodes:
        return {}
    result = await db.execute(
        select(Nomenclature.id, Nomenclature.barcode, Nomenclature.article_seller).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(list(barcodes)),
        )
    )
    return {barcode: (nom_id, article) for nom_id, barcode, article in result.all()}


async def _load_linked_doc_items(
    db: AsyncSession,
    project_id: int,
    req: FulfillmentRequest,
    has_assembly: bool,
    has_inbound: bool,
) -> dict[str, int] | None:
    """Состав связанного нашего документа {barcode: qty}. None — связи нет.

    has_assembly/has_inbound — связанный документ существует и не удалён
    (проверено выборкой assembly_map/inbound_map); сами items без is_deleted.
    """
    if req.assembly_request_id and has_assembly:
        result = await db.execute(
            select(AssemblyRequestItem.barcode, func.sum(AssemblyRequestItem.quantity))
            .where(
                AssemblyRequestItem.project_id == project_id,
                AssemblyRequestItem.assembly_request_id == req.assembly_request_id,
            )
            .group_by(AssemblyRequestItem.barcode)
        )
    elif req.inbound_receipt_id and has_inbound:
        result = await db.execute(
            select(InboundReceiptItem.barcode, func.sum(InboundReceiptItem.expected_qty))
            .where(
                InboundReceiptItem.project_id == project_id,
                InboundReceiptItem.receipt_id == req.inbound_receipt_id,
            )
            .group_by(InboundReceiptItem.barcode)
        )
    else:
        return None
    return {barcode: int(qty or 0) for barcode, qty in result.all()}


def _build_match(
    products: list[dict],
    our_by_barcode: dict[str, int],
    nom_by_barcode: dict[str, tuple[int, str | None]],
) -> dict:
    """Сверка состава ФФ-заявки с нашим документом (FfRequestMatch shape).

    Обе стороны по barcode: qty отличается / есть только у ФФ / есть только
    у нас. Позиции ФФ без barcode сверке не подлежат и в тоталы не входят.
    """
    ff_by_barcode: dict[str, int] = {}
    name_by_barcode: dict[str, str | None] = {}
    for p in products:
        barcode = p.get("barcode")
        if not barcode:
            continue
        ff_by_barcode[barcode] = ff_by_barcode.get(barcode, 0) + p["qty"]
        name_by_barcode.setdefault(barcode, p.get("name"))

    mismatch_rows: list[tuple[int, str, dict]] = []
    for barcode in set(ff_by_barcode) | set(our_by_barcode):
        ff_qty = ff_by_barcode.get(barcode, 0)
        our_qty = our_by_barcode.get(barcode, 0)
        if ff_qty == our_qty:
            continue
        _nom_id, article = nom_by_barcode.get(barcode, (None, None))
        row = {
            "barcode": barcode,
            "article_seller": article,
            "name": name_by_barcode.get(barcode),
            "ff_qty": ff_qty,
            "our_qty": our_qty,
            "diff": ff_qty - our_qty,
        }
        mismatch_rows.append((-abs(ff_qty - our_qty), barcode, row))
    mismatches = [row for _, _, row in sorted(mismatch_rows, key=lambda t: (t[0], t[1]))]

    return {
        "matched": not mismatches,
        "ff_positions": len(ff_by_barcode),
        "our_positions": len(our_by_barcode),
        "ff_total": sum(ff_by_barcode.values()),
        "our_total": sum(our_by_barcode.values()),
        "mismatches": mismatches,
    }


async def get_ff_link_for_assembly(db: AsyncSession, project_id: int, assembly_request_id: int) -> dict | None:
    """Зеркальная ФФ-заявка, привязанная к нашей заявке на сборку (или None)."""
    result = await db.execute(
        select(
            FulfillmentRequest.id,
            FulfillmentRequest.number,
            FulfillmentRequest.external_id,
            FulfillmentRequest.stage_title,
            FulfillmentRequest.warehouse_id,
        )
        .where(
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.assembly_request_id == assembly_request_id,
        )
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    return {
        "ff_request_id": row.id,
        "ff_request_number": row.number or row.external_id,
        "ff_stage_title": row.stage_title,
        "ff_warehouse_id": row.warehouse_id,
    }


async def get_request_detail(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    ff_request_id: int,
) -> dict | None:
    """Деталка ФФ-заявки: зеркальная шапка + состав.

    skladbot: ЖИВОЙ состав — GET /v1/requests/show/{external_id} при каждом
    открытии (не кэшируем: принятые количества меняются на стороне ФФ).
    wmscelicom: из зеркального raw — by-id эндпоинта у провайдера нет, состав
    приходит уже в списочных методах (актуальность = последний синк).
    migfull: сборки — из raw (planned/shipped_lines в списке целиком),
    приёмки — живые lines/incoming + received.
    None — заявка не найдена; ValueError — не подключено / провайдер недоступен.
    """
    result = await db.execute(
        select(FulfillmentRequest).where(
            FulfillmentRequest.id == ff_request_id,
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.warehouse_id == warehouse_id,
        )
    )
    req = result.scalar_one_or_none()
    if not req:
        return None

    # Связанный документ — до закрытия транзакции
    assembly_map: dict[int, tuple] = {}
    inbound_map: dict[int, tuple] = {}
    if req.assembly_request_id:
        result = await db.execute(
            select(AssemblyRequest.id, AssemblyRequest.number, AssemblyRequest.status).where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.id == req.assembly_request_id,
                AssemblyRequest.is_deleted == False,
            )
        )
        assembly_map = {row[0]: (row[1], row[2]) for row in result.all()}
    elif req.inbound_receipt_id:
        result = await db.execute(
            select(InboundReceipt.id, InboundReceipt.number, InboundReceipt.status).where(
                InboundReceipt.project_id == project_id,
                InboundReceipt.id == req.inbound_receipt_id,
                InboundReceipt.is_deleted == False,
            )
        )
        inbound_map = {row[0]: (row[1], row[2]) for row in result.all()}

    # Состав связанного нашего документа — для сверки (None, если связи нет)
    our_by_barcode = await _load_linked_doc_items(db, project_id, req, bool(assembly_map), bool(inbound_map))

    if req.provider == "wmscelicom":
        wms_products, wms_fields, creator = _wms_detail_parts(req)
        match_barcodes = {p["barcode"] or "" for p in wms_products} | set(our_by_barcode or {})
        nom_by_barcode = await _resolve_noms(db, project_id, match_barcodes)
        products = []
        for p in wms_products:
            barcode = p["barcode"]
            nom_id, article = nom_by_barcode.get(barcode, (None, None)) if barcode else (None, None)
            products.append(
                {
                    "barcode": barcode,
                    "vendor_code": None,
                    "name": p.get("name"),
                    "nomenclature_id": nom_id,
                    "article_seller": article,
                    "qty": p["qty"],
                    "accepted_qty": 0,
                    "delivery_qty": 0,
                    "defect_qty": 0,
                    "color": None,
                    "size": None,
                    "comment": p.get("comment"),
                    "image": None,
                    "our_qty": our_by_barcode.get(barcode or "", 0) if our_by_barcode is not None else None,
                }
            )
        row = _request_to_dict(req, assembly_map, inbound_map)
        row.update(
            {
                "comment": None,
                "customer_name": None,
                "executor": None,
                "creator": creator,
                "stage_description": None,
                "total_qty": sum(p["qty"] for p in products),
                "total_accepted": 0,
                "products": products,
                "stage_logs": [],
                "fields": wms_fields,
                "match": _build_match(products, our_by_barcode, nom_by_barcode) if our_by_barcode is not None else None,
            }
        )
        return row

    if req.provider == "migfull":
        raw = req.raw or {}
        if req.kind == FfRequestKind.ASSEMBLY.value:
            # Состав отгрузки уже в зеркале: planned/shipped_lines из списочного метода
            mig_products = _migfull_products_from_lines(
                _migfull_line_rows(raw.get("planned_lines")),
                _migfull_line_rows(raw.get("shipped_lines")),
                fact_field="delivery_qty",
            )
        else:
            # Приёмки: состава в списке нет — ЖИВЫЕ lines/incoming + received
            key = await get_integration(db, project_id, warehouse_id)
            if not key:
                raise ValueError("Фулфилмент не подключён к этому складу")
            config = key.config or {}
            tenant_guid = str(config.get("tenant_guid") or "")
            if not tenant_guid:
                raise ValueError("В конфигурации ключа нет GUID кабинета — переподключите фулфилмент")
            token = _decrypt(key.encrypted_key)
            external_id = req.external_id
            await db.commit()  # закрыть read-транзакцию до внешних HTTP-вызовов

            mig_client = MigfullClient(tenant_guid, token, project_id=project_id)
            try:
                incoming = await mig_client.fetch_submission_lines(external_id, "incoming")
                received = await mig_client.fetch_submission_lines(external_id, "received")
            except CircuitOpenError as e:
                raise ValueError(f"migfull.app временно недоступен, попробуйте позже ({e})") from e
            except RateLimitError as e:
                raise ValueError("migfull.app ограничил частоту запросов — откройте деталку через минуту") from e
            except MigfullApiError as e:
                raise ValueError(f"migfull.app не отдал строки приёмки (HTTP {e.status_code})") from e
            except httpx.HTTPError as e:
                raise ValueError(f"Сетевая ошибка при обращении к migfull.app: {e}") from e
            except ValueError as e:
                raise ValueError(f"migfull.app вернул ошибку сервера, попробуйте позже ({str(e)[:100]})") from e
            mig_products = _migfull_products_from_lines(incoming, received, fact_field="accepted_qty")

        # ШК только в карточке товара → guid→(ШК россыпи, штук в коробе) из зеркала
        # остатков. Короб сводится к россыпи: ШК россыпи для матча, qty × units_per_box.
        guid_barcodes = await _migfull_guid_barcodes(db, project_id, warehouse_id, {p["guid"] for p in mig_products})
        match_barcodes = {bc for bc, _ in guid_barcodes.values()} | set(our_by_barcode or {})
        nom_by_barcode = await _resolve_noms(db, project_id, match_barcodes)
        products = []
        for p in mig_products:
            barcode, units = guid_barcodes.get(p["guid"], (None, 1))
            nom_id, article = nom_by_barcode.get(barcode, (None, None)) if barcode else (None, None)
            products.append(
                {
                    "barcode": barcode,
                    "vendor_code": None,
                    "name": p.get("name"),
                    "nomenclature_id": nom_id,
                    "article_seller": article,
                    "qty": p["qty"] * units,
                    "accepted_qty": p["accepted_qty"] * units,
                    "delivery_qty": p["delivery_qty"] * units,
                    "defect_qty": p["defect_qty"] * units,
                    "units_per_box": units,
                    "box_qty": p["qty"] if units > 1 else 0,
                    "color": p.get("color"),
                    "size": p.get("size"),
                    "comment": None,
                    "image": None,
                    "our_qty": our_by_barcode.get(barcode or "", 0) if our_by_barcode is not None else None,
                }
            )
        row = _request_to_dict(req, assembly_map, inbound_map)
        row.update(
            {
                "comment": raw.get("notes") or raw.get("client_comment") or None,
                "customer_name": None,
                "executor": None,
                "creator": _coerce_name(raw.get("processor")),
                "stage_description": None,
                "total_qty": sum(p["qty"] for p in products),
                "total_accepted": sum(p["accepted_qty"] for p in products),
                "products": products,
                "stage_logs": [],
                "fields": _migfull_detail_fields(req),
                "match": _build_match(products, our_by_barcode, nom_by_barcode) if our_by_barcode is not None else None,
            }
        )
        return row

    key = await get_integration(db, project_id, warehouse_id)
    if not key:
        raise ValueError("Фулфилмент не подключён к этому складу")
    token = _decrypt(key.encrypted_key)
    external_id = req.external_id
    await db.commit()  # закрыть read-транзакцию до внешнего HTTP-вызова

    client = SkladbotClient(token, project_id=project_id)
    try:
        detail = await client.fetch_request_detail(external_id)
    except CircuitOpenError as e:
        raise ValueError(f"skladbot.ru временно недоступен, попробуйте позже ({e})") from e
    except RateLimitError as e:
        raise ValueError("skladbot.ru ограничил частоту запросов — откройте деталку через минуту") from e
    except SkladbotApiError as e:
        # 404 особенно вероятен: роут /v1/requests/show недокументированный
        raise ValueError(f"skladbot.ru не отдал деталку заявки (HTTP {e.status_code})") from e
    except httpx.HTTPError as e:
        raise ValueError(f"Сетевая ошибка при обращении к skladbot.ru: {e}") from e
    except ValueError as e:
        raise ValueError(f"skladbot.ru вернул ошибку сервера, попробуйте позже ({str(e)[:100]})") from e

    raw_products = detail.get("products") or []
    match_barcodes = {str(p.get("barcode") or "").strip() for p in raw_products} | set(our_by_barcode or {})
    nom_by_barcode = await _resolve_noms(db, project_id, match_barcodes)

    products = []
    for p in raw_products:
        barcode = str(p.get("barcode") or "").strip() or None
        nom_id, article = nom_by_barcode.get(barcode, (None, None)) if barcode else (None, None)
        products.append(
            {
                "barcode": barcode,
                "vendor_code": p.get("vendorCode"),
                "name": p.get("name"),
                "nomenclature_id": nom_id,
                "article_seller": article,
                "qty": int(p.get("amount") or 0),
                "accepted_qty": int(p.get("acceptedAmount") or 0),
                "delivery_qty": int(p.get("delivery_amount") or 0),
                "defect_qty": int(p.get("repairAmount") or 0),
                "color": p.get("color"),
                "size": p.get("size"),
                "comment": p.get("comment") or None,
                "image": p.get("image"),
                "our_qty": our_by_barcode.get(barcode or "", 0) if our_by_barcode is not None else None,
            }
        )

    stage = detail.get("stage") or {}
    customer = detail.get("customer") or {}
    fields = [
        {
            "name": f.get("name"),
            "field": f.get("field"),
            "value": str(f["value"]) if f.get("value") is not None else None,
        }
        for f in (detail.get("fields") or [])
    ]
    stage_logs = [
        {
            "stage": log.get("stage"),
            "executor": _coerce_name(log.get("executor")),
            "created_at": log.get("created_at"),
            "spent_time": log.get("spent_time") or None,
        }
        for log in (detail.get("stageLogs") or [])
    ]

    row = _request_to_dict(req, assembly_map, inbound_map)
    row.update(
        {
            "comment": detail.get("comment"),
            "customer_name": customer.get("name"),
            "executor": _coerce_name(detail.get("executor")),
            "creator": _coerce_name(detail.get("creator")),
            "stage_description": stage.get("description"),
            "total_qty": sum(p["qty"] for p in products),
            "total_accepted": sum(p["accepted_qty"] for p in products),
            "products": products,
            "stage_logs": stage_logs,
            "fields": fields,
            "match": _build_match(products, our_by_barcode, nom_by_barcode) if our_by_barcode is not None else None,
        }
    )
    return row


async def link_request(
    db: AsyncSession,
    project_id: int,
    ff_request_id: int,
    assembly_request_id: int | None = None,
    inbound_receipt_id: int | None = None,
    warehouse_id: int | None = None,
) -> dict | None:
    """Привязать ФФ-заявку к нашему документу (ровно один из двух id).

    Returns None если ФФ-заявка не найдена; ValueError при нарушении правил
    (оба/ни одного id, чужой kind, чужой склад, несуществующий документ,
    двойная привязка). warehouse_id (из path) дополнительно скоупит ФФ-заявку.
    """
    if (assembly_request_id is None) == (inbound_receipt_id is None):
        raise ValueError("Укажите ровно один из assembly_request_id / inbound_receipt_id")

    q = select(FulfillmentRequest).where(
        FulfillmentRequest.id == ff_request_id,
        FulfillmentRequest.project_id == project_id,
    )
    if warehouse_id is not None:
        q = q.where(FulfillmentRequest.warehouse_id == warehouse_id)
    result = await db.execute(q)
    req = result.scalar_one_or_none()
    if not req:
        return None

    assembly_map: dict[int, tuple] = {}
    inbound_map: dict[int, tuple] = {}
    marked_ready = False

    if assembly_request_id is not None:
        if req.kind != FfRequestKind.ASSEMBLY.value:
            raise ValueError("assembly_request_id можно привязать только к ФФ-заявке типа assembly")
        asm_result = await db.execute(
            select(AssemblyRequest)
            .where(
                AssemblyRequest.id == assembly_request_id,
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted == False,
            )
            # row-lock: сериализация с авто-READY синка (advisory lock линк не берёт)
            .with_for_update()
        )
        doc = asm_result.scalar_one_or_none()
        if not doc:
            raise ValueError("Заявка на сборку не найдена в проекте")
        if doc.warehouse_id != req.warehouse_id:
            raise ValueError("Заявка на сборку принадлежит другому складу")
        conflict = await db.execute(
            select(FulfillmentRequest.id)
            .where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.assembly_request_id == assembly_request_id,
                FulfillmentRequest.id != ff_request_id,
            )
            .limit(1)
        )
        if conflict.scalar_one_or_none() is not None:
            raise ValueError("Заявка на сборку уже связана с другой ФФ-заявкой")
        req.assembly_request_id = assembly_request_id
        # Авто-READY при привязке: стадия ФФ уже «готов», наша заявка ещё
        # IN_PROGRESS → переводим сразу (та же логика, что при синке)
        if (
            not req.archived
            # expired (просрочена) — активна, авто-READY как при синке
            and doc.status == AssemblyStatus.IN_PROGRESS.value
            and _assembly_ready_signal(req.provider, req.stage_code, req.stage_title, req.is_completed)
        ):
            _transition_assembly_to_ready(db, project_id, doc, req)
            marked_ready = True
        assembly_map = {doc.id: (doc.number, doc.status)}  # после перехода — статус уже новый
    else:
        if req.kind != FfRequestKind.INBOUND.value:
            raise ValueError("inbound_receipt_id можно привязать только к ФФ-заявке типа inbound")
        inb_result = await db.execute(
            select(InboundReceipt).where(
                InboundReceipt.id == inbound_receipt_id,
                InboundReceipt.project_id == project_id,
                InboundReceipt.is_deleted == False,
            )
        )
        inb_doc = inb_result.scalar_one_or_none()
        if not inb_doc:
            raise ValueError("Приёмка не найдена в проекте")
        if inb_doc.warehouse_id != req.warehouse_id:
            raise ValueError("Приёмка принадлежит другому складу")
        conflict = await db.execute(
            select(FulfillmentRequest.id)
            .where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.inbound_receipt_id == inbound_receipt_id,
                FulfillmentRequest.id != ff_request_id,
            )
            .limit(1)
        )
        if conflict.scalar_one_or_none() is not None:
            raise ValueError("Приёмка уже связана с другой ФФ-заявкой")
        req.inbound_receipt_id = inbound_receipt_id
        inbound_map = {inb_doc.id: (inb_doc.number, inb_doc.status)}

    await db.commit()
    if marked_ready:
        await invalidate_cache("reports:assembly_flow")
    return _request_to_dict(req, assembly_map, inbound_map)


async def unlink_request(
    db: AsyncSession,
    project_id: int,
    ff_request_id: int,
    warehouse_id: int | None = None,
) -> dict | None:
    """Снять обе связи с ФФ-заявки. None если не найдена."""
    q = select(FulfillmentRequest).where(
        FulfillmentRequest.id == ff_request_id,
        FulfillmentRequest.project_id == project_id,
    )
    if warehouse_id is not None:
        q = q.where(FulfillmentRequest.warehouse_id == warehouse_id)
    result = await db.execute(q)
    req = result.scalar_one_or_none()
    if not req:
        return None
    req.assembly_request_id = None
    req.inbound_receipt_id = None
    await db.commit()
    return _request_to_dict(req)


# ─── Локальный архив (local_archived — пометка DDS, синк её не трогает) ──────


async def _get_request(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    ff_request_id: int,
) -> FulfillmentRequest | None:
    """ФФ-заявка по id в скоупе (project, warehouse) или None."""
    result = await db.execute(
        select(FulfillmentRequest).where(
            FulfillmentRequest.id == ff_request_id,
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.warehouse_id == warehouse_id,
        )
    )
    return result.scalar_one_or_none()


async def archive_request(
    db: AsyncSession,
    project_id: int,
    ff_request_id: int,
    warehouse_id: int,
) -> dict | None:
    """Локальный архив: пометить заявку в DDS (зеркальный archived не трогаем).

    Идемпотентно (повторный вызов не сдвигает local_archived_at).
    None — заявка не найдена.
    """
    req = await _get_request(db, project_id, warehouse_id, ff_request_id)
    if not req:
        return None
    if not req.local_archived:
        req.local_archived = True
        req.local_archived_at = utcnow()
        await db.commit()
    return _request_to_dict(req)


async def unarchive_request(
    db: AsyncSession,
    project_id: int,
    ff_request_id: int,
    warehouse_id: int,
) -> dict | None:
    """Вернуть заявку из локального архива. None — заявка не найдена."""
    req = await _get_request(db, project_id, warehouse_id, ff_request_id)
    if not req:
        return None
    if req.local_archived:
        req.local_archived = False
        req.local_archived_at = None
        await db.commit()
    return _request_to_dict(req)


# ─── Кандидаты для модалки «Связать» + создание сборки из ФФ-заявки ──────────


async def _fetch_ff_composition(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    req: FulfillmentRequest,
) -> dict[str, int] | None:
    """Состав ФФ-заявки {barcode: qty > 0}. None — состав недоступен.

    wmscelicom: из зеркального raw (assembly — packages → items, inbound —
    items); пустой результат = «нет данных» (ограничение провайдера) → None.
    skladbot: живая деталка /v1/requests/show — read-транзакция закрывается
    ДО HTTP-вызова (PgBouncer: retry/backoff не должен держать
    idle-in-transaction коннект); любая ошибка провайдера → None, эндпоинт
    не валим. {} — деталка получена, но состав пуст.
    migfull: сборки — из зеркального raw (planned_lines), ШК — guid→barcode
    из зеркала остатков; guid без ШК выпадают из состава (дотянутся синком
    остатков). Приёмки — строк в списке нет (только живые lines/*), HTTP ради
    скоринга не тянем → None (фолбэк на эвристику дат).
    """
    if req.provider == "wmscelicom":
        comp: dict[str, int]
        if req.kind == FfRequestKind.ASSEMBLY.value:
            comp = _raw_assembly_composition(req.provider, req.raw)
        else:
            comp = {}
            for item in (req.raw or {}).get("items") or []:  # PHP-API: бывают null-элементы
                if not isinstance(item, dict):
                    continue
                barcode = str(item.get("barcode") or "").strip()
                if not barcode:
                    continue
                comp[barcode] = comp.get(barcode, 0) + _safe_int(item.get("count"))
        comp = {barcode: qty for barcode, qty in comp.items() if qty > 0}
        return comp or None

    if req.provider == "migfull":
        if req.kind != FfRequestKind.ASSEMBLY.value:
            return None
        raw = req.raw or {}
        guid_qty: dict[str, int] = {}
        for p in _migfull_products_from_lines(
            _migfull_line_rows(raw.get("planned_lines")), [], fact_field="delivery_qty"
        ):
            if p["qty"] > 0:
                guid_qty[p["guid"]] = guid_qty.get(p["guid"], 0) + p["qty"]
        if not guid_qty:
            return None
        guid_barcodes = await _migfull_guid_barcodes(db, project_id, warehouse_id, set(guid_qty))
        mig_comp: dict[str, int] = {}
        for guid, qty in guid_qty.items():
            resolved = guid_barcodes.get(guid)
            if resolved:
                mig_barcode, units = resolved
                mig_comp[mig_barcode] = mig_comp.get(mig_barcode, 0) + qty * units
        return mig_comp or None

    if req.provider != "skladbot":
        return None

    key = await get_integration(db, project_id, warehouse_id)
    if not key:
        return None
    token = _decrypt(key.encrypted_key)
    external_id = req.external_id
    await db.commit()  # закрыть read-транзакцию до внешнего HTTP-вызова

    client = SkladbotClient(token, project_id=project_id)
    try:
        detail = await client.fetch_request_detail(external_id)
    except (RateLimitError, CircuitOpenError, SkladbotApiError, httpx.HTTPError, ValueError) as e:
        logger.warning("FF composition: request %s detail failed (%s)", external_id, e)
        return None
    if not isinstance(detail, dict):
        # show недокументирован: Laravel может отдать data списком — форма дрейфует
        return None
    live_comp: dict[str, int] = {}
    for p in detail.get("products") or []:
        if not isinstance(p, dict):
            continue
        barcode = str(p.get("barcode") or "").strip()
        if not barcode:
            continue
        live_comp[barcode] = live_comp.get(barcode, 0) + _safe_int(p.get("amount"))
    return {barcode: qty for barcode, qty in live_comp.items() if qty > 0}


def _candidate_date_diff(ff_created: date | None, cand_created: datetime | None) -> int | None:
    """|дата ФФ-заявки − дата документа| в днях; None — дат(ы) нет."""
    if ff_created is None or cand_created is None:
        return None
    return abs((ff_created - cand_created.date()).days)


def _date_reason(diff_days: int) -> str:
    return "дата совпадает" if diff_days == 0 else f"дата ±{diff_days} дн"


def _score_by_composition(
    ff_comp: dict[str, int],
    cand_items: dict[str, int],
    diff_days: int | None,
) -> tuple[int | None, str | None]:
    """Скоринг «подходит под наполнение»: Jaccard ШК ×60 + qty 20 + дата 20/15/10.

    score/reason только при пересечении ШК (comp > 0) и score ≥ 40.
    """
    union = set(ff_comp) | set(cand_items)
    share = len(set(ff_comp) & set(cand_items)) / len(union) if union else 0.0
    comp_score = round(share * 60)
    if comp_score <= 0:
        return None, None
    score = comp_score
    reason_parts = [f"ШК {round(share * 100)}%"]

    ff_total = sum(ff_comp.values())
    cand_total = sum(cand_items.values())
    if ff_total > 0 and cand_total > 0 and abs(cand_total - ff_total) <= 0.1 * ff_total:
        score += 20
        reason_parts.append("кол-во ±10%")

    if diff_days is not None and diff_days in _CAND_COMP_DATE_SCORES:
        score += _CAND_COMP_DATE_SCORES[diff_days]
        reason_parts.append(_date_reason(diff_days))

    score = min(100, score)
    if score < _CAND_COMP_MIN_SCORE:
        return None, None
    return score, ", ".join(reason_parts)


def _score_by_date(
    ff_total: int | None,
    cand_total: int,
    diff_days: int | None,
) -> tuple[int | None, str | None]:
    """Фолбэк-скоринг без состава: дата 70/55/40 + qty-бонус 10, порог 30."""
    if diff_days is None:
        return None, None
    date_score = _SUGGEST_DATE_SCORES.get(diff_days)
    if date_score is None:
        return None, None
    score = date_score
    reason_parts = [_date_reason(diff_days)]
    if ff_total and cand_total and abs(cand_total - ff_total) <= 0.1 * ff_total:
        score += 10
        reason_parts.append("кол-во ±10%")
    if score < _SUGGEST_MIN_SCORE:
        return None, None
    return min(100, score), ", ".join(reason_parts)


async def _assembly_candidates(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    ff_request_id: int,
) -> tuple[list[AssemblyRequest], dict[int, dict[str, int]]]:
    """Заявки на сборку склада, не связанные с ДРУГИМИ ФФ-заявками, + их позиции."""
    linked_subq = select(FulfillmentRequest.assembly_request_id).where(
        FulfillmentRequest.project_id == project_id,
        FulfillmentRequest.assembly_request_id.is_not(None),
        FulfillmentRequest.id != ff_request_id,
    )
    result = await db.execute(
        select(AssemblyRequest)
        .options(selectinload(AssemblyRequest.wb_fbo_supply))
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.warehouse_id == warehouse_id,
            AssemblyRequest.is_deleted == False,
            AssemblyRequest.status != AssemblyStatus.CANCELLED.value,
            AssemblyRequest.id.not_in(linked_subq),
        )
        .order_by(AssemblyRequest.created_at.desc(), AssemblyRequest.id.desc())
        .limit(_LINK_CANDIDATES_LIMIT)
    )
    docs = list(result.scalars().all())
    items_by_doc: dict[int, dict[str, int]] = {}
    if docs:
        items_result = await db.execute(
            select(
                AssemblyRequestItem.assembly_request_id,
                AssemblyRequestItem.barcode,
                func.sum(AssemblyRequestItem.quantity),
            )
            .where(
                AssemblyRequestItem.project_id == project_id,
                AssemblyRequestItem.assembly_request_id.in_([d.id for d in docs]),
            )
            .group_by(AssemblyRequestItem.assembly_request_id, AssemblyRequestItem.barcode)
        )
        for doc_id, barcode, qty in items_result.all():
            items_by_doc.setdefault(doc_id, {})[barcode] = int(qty or 0)
    return docs, items_by_doc


async def _inbound_candidates(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    ff_request_id: int,
) -> tuple[list[InboundReceipt], dict[int, dict[str, int]]]:
    """Приёмки склада, не связанные с ДРУГИМИ ФФ-заявками, + их позиции (expected_qty)."""
    linked_subq = select(FulfillmentRequest.inbound_receipt_id).where(
        FulfillmentRequest.project_id == project_id,
        FulfillmentRequest.inbound_receipt_id.is_not(None),
        FulfillmentRequest.id != ff_request_id,
    )
    result = await db.execute(
        select(InboundReceipt)
        .where(
            InboundReceipt.project_id == project_id,
            InboundReceipt.warehouse_id == warehouse_id,
            InboundReceipt.is_deleted == False,
            InboundReceipt.id.not_in(linked_subq),
        )
        .order_by(InboundReceipt.created_at.desc(), InboundReceipt.id.desc())
        .limit(_LINK_CANDIDATES_LIMIT)
    )
    docs = list(result.scalars().all())
    items_by_doc: dict[int, dict[str, int]] = {}
    if docs:
        items_result = await db.execute(
            select(
                InboundReceiptItem.receipt_id,
                InboundReceiptItem.barcode,
                func.sum(InboundReceiptItem.expected_qty),
            )
            .where(
                InboundReceiptItem.project_id == project_id,
                InboundReceiptItem.receipt_id.in_([d.id for d in docs]),
            )
            .group_by(InboundReceiptItem.receipt_id, InboundReceiptItem.barcode)
        )
        for doc_id, barcode, qty in items_result.all():
            items_by_doc.setdefault(doc_id, {})[barcode] = int(qty or 0)
    return docs, items_by_doc


async def get_link_candidates(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    ff_request_id: int,
) -> dict | None:
    """Кандидаты для связывания ФФ-заявки (FfLinkCandidatesResponse shape).

    kind=assembly → наши заявки на сборку склада, kind=inbound → приёмки;
    уже связанные с другими ФФ-заявками — исключаются. Скоринг: при доступном
    составе — пересечение ШК (см. _score_by_composition), иначе фолбэк по
    датам. None — ФФ-заявка не найдена; ValueError — kind=other.
    """
    req = await _get_request(db, project_id, warehouse_id, ff_request_id)
    if not req:
        return None
    if req.kind not in (FfRequestKind.ASSEMBLY.value, FfRequestKind.INBOUND.value):
        raise ValueError("Подбор кандидатов доступен только для заявок типа «сборка» и «приёмка»")

    kind = req.kind
    ff_number = req.number or req.external_id
    ff_created = req.external_created_at
    mirror_total = req.total_qty

    # skladbot: внутри — живой HTTP (транзакция закрывается до вызова)
    comp = await _fetch_ff_composition(db, project_id, warehouse_id, req)
    composition_available = bool(comp)

    docs: list[AssemblyRequest] | list[InboundReceipt]
    if kind == FfRequestKind.ASSEMBLY.value:
        docs, items_by_doc = await _assembly_candidates(db, project_id, warehouse_id, ff_request_id)
    else:
        docs, items_by_doc = await _inbound_candidates(db, project_id, warehouse_id, ff_request_id)

    candidates = []
    for doc in docs:
        cand_items = items_by_doc.get(doc.id, {})
        cand_total = sum(cand_items.values())
        diff_days = _candidate_date_diff(ff_created, doc.created_at)
        if comp:
            score, reason = _score_by_composition(comp, cand_items, diff_days)
        else:
            score, reason = _score_by_date(mirror_total, cand_total, diff_days)

        fbo_supply_number = dest_warehouse = None
        if isinstance(doc, AssemblyRequest):
            supply = doc.wb_fbo_supply
            fbo_supply_number = supply.wb_supply_id if supply else None
            dest_warehouse = doc.wb_warehouse_name_manual or (supply.warehouse_name if supply else None)

        candidates.append(
            {
                "doc_id": doc.id,
                "number": doc.number,
                "status": doc.status,
                "created_at": doc.created_at,
                "total_qty": cand_total,
                "fbo_supply_number": fbo_supply_number,
                "dest_warehouse": dest_warehouse,
                "score": score,
                "reason": reason,
            }
        )

    return {
        "kind": kind,
        "ff_number": ff_number,
        "ff_total_qty": sum(comp.values()) if comp else mirror_total,
        "composition_available": composition_available,
        "candidates": candidates,
    }


async def create_assembly_from_ff(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    ff_request_id: int,
) -> dict | None:
    """Создать нашу заявку на сборку из состава ФФ-заявки и связать их.

    Состав — через _fetch_ff_composition (wmscelicom — raw, skladbot — живая
    деталка); в сборку попадают только ШК, найденные в номенклатуре проекта,
    остальные возвращаются в skipped_barcodes. После автосвязи применяется
    тот же авто-READY, что и в link_request (стадия ФФ уже «готов» →
    IN_PROGRESS → READY сразу, не дожидаясь синка). None — ФФ-заявка не найдена.
    ValueError — не assembly / уже связана / состав недоступен или пуст /
    ни один ШК не известен / ошибки create_assembly_request (дефицит
    доступного стока, не-FULFILLMENT склад).

    НЕ атомарно: create_assembly_request коммитит сборку ДО связывания.
    Если ФФ-заявку связали параллельно (окно между созданием и пере-SELECT
    с row-lock) — ValueError, созданная сборка остаётся НЕсвязанной (видна
    в списке заявок на сборку; пользователь связывает или удаляет вручную).
    """
    req = await _get_request(db, project_id, warehouse_id, ff_request_id)
    if not req:
        return None
    if req.kind != FfRequestKind.ASSEMBLY.value:
        raise ValueError("Создать заявку на сборку можно только из ФФ-заявки типа «сборка»")
    if req.assembly_request_id is not None:
        raise ValueError("ФФ-заявка уже связана с заявкой на сборку")

    ff_label = req.number or req.external_id
    comp = await _fetch_ff_composition(db, project_id, warehouse_id, req)
    if comp is None:
        raise ValueError("Состав ФФ-заявки недоступен — попробуйте позже")
    if not comp:
        raise ValueError("Состав ФФ-заявки пуст — создавать нечего")
    # migfull: ШК берутся из зеркала остатков (guid→barcode); пока часть guid
    # не разрезолвлена, состав неполный — молча создавать урезанную сборку нельзя
    if req.provider == "migfull" and req.total_qty and sum(comp.values()) < req.total_qty:
        raise ValueError(
            "Состав ФФ-заявки разрезолвлен не полностью (ШК части товаров ещё не синкованы) — "
            "запустите синк остатков склада и повторите"
        )

    nom_by_barcode = await _resolve_noms(db, project_id, set(comp))
    skipped_barcodes = sorted(barcode for barcode in comp if barcode not in nom_by_barcode)
    items = [
        AssemblyItemCreate(barcode=barcode, quantity=qty) for barcode, qty in comp.items() if barcode in nom_by_barcode
    ]
    if not items:
        raise ValueError("Ни один ШК из состава ФФ-заявки не найден в номенклатуре проекта")

    payload = AssemblyRequestCreate(
        warehouse_id=warehouse_id,
        pallets_count=1,
        pallet_weight_kg=Decimal("1.00"),
        comment=f"Создана из ФФ-заявки {ff_label} (паллеты/вес — заглушка 1×1 кг, уточните вручную)",
        items=items,
    )
    # ValueError (дефицит доступного стока и т.п.) пробрасываем как есть —
    # текст человекочитаемый; commit + invalidate_cache внутри.
    doc = await create_assembly_request(db, project_id, payload)

    # Пере-SELECT под row-lock: параллельный link мог занять ФФ-заявку,
    # пока создавалась сборка (create_assembly_request коммитит транзакцию)
    locked = await db.execute(
        select(FulfillmentRequest)
        .where(
            FulfillmentRequest.id == ff_request_id,
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.warehouse_id == warehouse_id,
        )
        .with_for_update()
    )
    req = locked.scalar_one_or_none()
    if req is None or req.assembly_request_id is not None:
        await db.rollback()
        raise ValueError(
            f"ФФ-заявку уже связали параллельно — созданная заявка на сборку {doc.number} осталась без связи"
        )
    req.assembly_request_id = doc.id
    # Авто-READY как в link_request: стадия ФФ уже «готов» → созданную
    # IN_PROGRESS-сборку переводим сразу, не дожидаясь ежечасного синка
    marked_ready = False
    if (
        not req.archived
        and not req.expired
        and not req.local_archived
        and doc.status == AssemblyStatus.IN_PROGRESS.value
        and _assembly_ready_signal(req.provider, req.stage_code, req.stage_title, req.is_completed)
    ):
        _transition_assembly_to_ready(db, project_id, doc, req)
        marked_ready = True
    await db.commit()
    if marked_ready:
        await invalidate_cache("reports:assembly_flow")

    return {
        "request": _request_to_dict(req, {doc.id: (doc.number, doc.status)}),
        "assembly_request_id": doc.id,
        "assembly_number": doc.number,
        "items_created": len(items),
        "skipped_barcodes": skipped_barcodes,
    }


# ─── PUSH: наша заявка на сборку → заявка ФФ (skladbot тип 851) ───────────────

_DEFAULT_MARKETPLACE_NAME = "Wildberries"


def _norm_wh_name(value: str | None) -> str:
    """Нормализация имени склада для сопоставления (наш WB-склад vs склад МП ФФ).

    Имя WB-склада в заявке («Коледино») и в справочнике skladbot («МСК Коледино»,
    «Москва (Коледино)») не совпадают буквально — приводим к lower и оставляем
    только буквы/цифры, чтобы матчить по вхождению.
    """
    return re.sub(r"[^0-9a-zа-яё]+", "", (value or "").lower())


def _parse_ff_form_options(
    form_data: dict,
    customer_id: int,
    marketplace_name: str = _DEFAULT_MARKETPLACE_NAME,
) -> tuple[int, str, list[dict], list[dict]]:
    """Из ответа GET /v1/requests/form-data вытащить справочники для диалога 851.

    Возвращает (marketplace_id, marketplace_name, warehouses, delivery_types):
    marketplace — выбранный по имени (Wildberries) с фолбэком на id=1; warehouses
    — активные склады МП этого маркетплейса, видимые клиенту (customer null/свой);
    delivery_types — типы поставки. skladbot принимает по этим полям integer id /
    строковый ключ из `value`, поэтому отдаём именно value (не text).
    """
    utils_raw = form_data.get("utils")
    utils: dict = utils_raw if isinstance(utils_raw, dict) else form_data

    marketplaces = utils.get("marketplaces") or []
    mp_id = 1
    mp_name = marketplace_name
    for m in marketplaces:
        if not isinstance(m, dict):
            continue
        if marketplace_name.lower() in str(m.get("text") or "").lower():
            mp_id = _safe_int(m.get("value")) or mp_id
            mp_name = str(m.get("text") or marketplace_name)
            break

    warehouses: list[dict] = []
    for w in utils.get("marketplaceWarehouses") or []:
        if not isinstance(w, dict):
            continue
        wid = _safe_int(w.get("value"))
        if not wid:
            continue
        if not _truthy(w.get("is_active", 1)):
            continue
        w_mp = w.get("marketplace")
        if w_mp is not None and _safe_int(w_mp) != mp_id:
            continue
        w_cust = w.get("customer")
        if w_cust is not None and _safe_int(w_cust) != int(customer_id):
            continue
        warehouses.append({"id": wid, "name": str(w.get("text") or f"Склад #{wid}")})

    delivery_types: list[dict] = []
    for d in utils.get("marketplaceDeliveryTypes") or []:
        if not isinstance(d, dict):
            continue
        val = str(d.get("value") or "").strip()
        if not val:
            continue
        d_mp = d.get("marketplace")
        if d_mp is not None and _safe_int(d_mp) != mp_id:
            continue
        delivery_types.append({"value": val, "name": str(d.get("text") or val)})
    if not delivery_types:
        delivery_types = [{"value": "straight", "name": "Прямая"}, {"value": "cross_dock", "name": "Cross dock"}]

    return mp_id, mp_name, warehouses, delivery_types


def _truthy(value: object) -> bool:
    """1/'1'/True/'true' → True; 0/''/None/'false' → False (PHP-API отдаёт по-разному)."""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


async def get_ff_create_form(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    assembly_request_id: int,
) -> FfCreateFormResponse | None:
    """Справочники для диалога создания заявки 851 из сборки (живой form-data).

    None — сборка не найдена в проекте. ValueError — не skladbot / чужой склад /
    нет customer_id / ошибки провайдера. Подбирает склад МП по имени WB-склада
    заявки и предзаполняет даты плановой готовностью.
    """
    key = await get_integration(db, project_id, warehouse_id)
    if not key:
        raise ValueError("Фулфилмент не подключён к этому складу")
    if key.service != "skladbot":
        raise ValueError(
            f"Создание заявки на ФФ поддерживается только для skladbot (подключён {_provider_human(key.service)})"
        )
    config = key.config or {}
    customer_id = config.get("customer_id")
    if not customer_id:
        raise ValueError("В конфигурации ключа нет customer_id — переподключите фулфилмент")

    asm = await db.execute(
        select(
            AssemblyRequest.id,
            AssemblyRequest.warehouse_id,
            AssemblyRequest.estimated_ready_date,
            AssemblyRequest.wb_warehouse_name_manual,
            WbFboSupply.warehouse_name.label("fbo_warehouse_name"),
        )
        .outerjoin(WbFboSupply, AssemblyRequest.wb_fbo_supply_id == WbFboSupply.id)
        .where(
            AssemblyRequest.id == assembly_request_id,
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted == False,
        )
    )
    row = asm.first()
    if row is None:
        return None
    if row.warehouse_id != warehouse_id:
        raise ValueError("Заявка на сборку принадлежит другому складу")

    token = _decrypt(key.encrypted_key)
    client = SkladbotClient(token, project_id=project_id)
    try:
        form_data = await client.fetch_form_data()
    except CircuitOpenError as e:
        raise ValueError(f"skladbot.ru временно недоступен, попробуйте позже ({e})") from e
    except RateLimitError as e:
        raise ValueError("skladbot.ru ограничил частоту запросов — повторите через минуту") from e
    except SkladbotApiError as e:
        raise ValueError(f"skladbot.ru не отдал справочники формы (HTTP {e.status_code})") from e
    except httpx.HTTPError as e:
        raise ValueError(f"Сетевая ошибка при обращении к skladbot.ru: {e}") from e

    mp_id, mp_name, warehouses, delivery_types = _parse_ff_form_options(form_data, customer_id)

    hint = row.fbo_warehouse_name or row.wb_warehouse_name_manual
    suggested_id: int | None = None
    norm_hint = _norm_wh_name(hint)
    if norm_hint:
        for w in warehouses:
            norm_w = _norm_wh_name(w["name"])
            if norm_w and (norm_hint in norm_w or norm_w in norm_hint):
                suggested_id = w["id"]
                break

    default_date = row.estimated_ready_date or date.today()
    return FfCreateFormResponse(
        marketplace_id=mp_id,
        marketplace_name=mp_name,
        warehouses=warehouses,  # type: ignore[arg-type]
        delivery_types=delivery_types,  # type: ignore[arg-type]
        suggested_warehouse_id=suggested_id,
        suggested_warehouse_hint=hint,
        collection_date=default_date,
        unloading_date=default_date,
        delivery_type="straight",
    )


async def create_ff_request_from_assembly(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    assembly_request_id: int,
    payload: FfCreateRequestPayload,
) -> dict | None:
    """Создать заявку «Доставка на склад МП» (851) у skladbot из нашей сборки.

    Состав берётся из позиций AssemblyRequest (агрегация по ШК), резолвится в
    product_data_id через живой GET /v1/requests/products (только товары с
    остатком под этот тип заявки; нерезолвленные → skipped_barcodes), затем
    POST /v1/requests создаёт РЕАЛЬНЫЙ заказ у ФФ. Созданную заявку зеркалим в
    FulfillmentRequest и связываем с нашей сборкой (assembly_request_id).

    Идемпотентность: если сборка уже связана с заявкой ФФ — ValueError (повторно
    не создаём). None — сборка не найдена в проекте. ValueError — не skladbot /
    чужой склад / отменена / нет позиций / ни один ШК не доступен у ФФ / ошибки
    провайдера. Только skladbot (wmscelicom/migfull push не поддержан).

    НЕ полностью атомарно: заказ у ФФ создаётся ДО записи зеркала. При сбое после
    POST (или гонке связывания) заказ у провайдера уже существует — текст ошибки
    это поясняет, заявку видно после следующего синка.
    """
    key = await get_integration(db, project_id, warehouse_id)
    if not key:
        raise ValueError("Фулфилмент не подключён к этому складу")
    if key.service != "skladbot":
        raise ValueError(
            f"Создание заявки на ФФ поддерживается только для skladbot (подключён {_provider_human(key.service)})"
        )
    config = key.config or {}
    customer_id = config.get("customer_id")
    if not customer_id:
        raise ValueError("В конфигурации ключа нет customer_id — переподключите фулфилмент")

    asm_result = await db.execute(
        select(AssemblyRequest)
        .options(selectinload(AssemblyRequest.items))
        .where(
            AssemblyRequest.id == assembly_request_id,
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted == False,
        )
    )
    assembly = asm_result.scalar_one_or_none()
    if assembly is None:
        return None
    if assembly.warehouse_id != warehouse_id:
        raise ValueError("Заявка на сборку принадлежит другому складу")
    if assembly.status == AssemblyStatus.CANCELLED.value:
        raise ValueError("Нельзя отправить на ФФ отменённую заявку")

    # Идемпотентность: сборка уже связана с заявкой ФФ
    existing = await db.execute(
        select(FulfillmentRequest.number, FulfillmentRequest.external_id)
        .where(
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.assembly_request_id == assembly_request_id,
        )
        .limit(1)
    )
    ex = existing.first()
    if ex is not None:
        raise ValueError(f"Заявка уже отправлена на ФФ ({ex.number or ex.external_id}) — повторное создание запрещено")

    # Состав: агрегируем позиции сборки по ШК
    qty_by_barcode: dict[str, int] = {}
    for it in assembly.items:
        bc = (it.barcode or "").strip()
        if bc:
            qty_by_barcode[bc] = qty_by_barcode.get(bc, 0) + int(it.quantity or 0)
    qty_by_barcode = {bc: q for bc, q in qty_by_barcode.items() if q > 0}
    if not qty_by_barcode:
        raise ValueError("В заявке на сборку нет позиций для отправки")

    # Локальные снимки до commit (объект assembly после commit истекает)
    asm_id = assembly.id
    asm_number = assembly.number
    asm_status = assembly.status
    token = _decrypt(key.encrypted_key)
    await db.commit()  # закрыть read-транзакцию до внешних HTTP-вызовов

    client = SkladbotClient(token, project_id=project_id)
    # Валидируем склад МП/маркетплейс по живым справочникам ДО создания реального
    # заказа: поля 851 у skladbot — select по integer id, имя не принимается
    # (иначе «Склад МП» уезжает в заявку пустым). Заодно получаем имя для зеркала.
    try:
        form_data = await client.fetch_form_data()
    except CircuitOpenError as e:
        raise ValueError(f"skladbot.ru временно недоступен, попробуйте позже ({e})") from e
    except RateLimitError as e:
        raise ValueError("skladbot.ru ограничил частоту запросов — повторите через минуту") from e
    except SkladbotApiError as e:
        raise ValueError(f"skladbot.ru не отдал справочники формы (HTTP {e.status_code})") from e
    except httpx.HTTPError as e:
        raise ValueError(f"Сетевая ошибка при обращении к skladbot.ru: {e}") from e

    _, _, wh_options, dt_options = _parse_ff_form_options(form_data, customer_id)
    wh_name = next((w["name"] for w in wh_options if w["id"] == payload.marketplace_warehouse_id), None)
    if wh_name is None:
        raise ValueError("Выбранный склад МП недоступен у ФФ — обновите форму создания заявки и выберите склад заново")
    valid_delivery = {d["value"] for d in dt_options} or {"straight", "cross_dock"}
    if payload.delivery_type not in valid_delivery:
        raise ValueError("Недопустимый тип поставки — выберите из списка формы")

    try:
        resolved = await client.resolve_products(customer_id, DELIVERY_REQUEST_TYPE_ID, list(qty_by_barcode))
    except CircuitOpenError as e:
        raise ValueError(f"skladbot.ru временно недоступен, попробуйте позже ({e})") from e
    except RateLimitError as e:
        raise ValueError("skladbot.ru ограничил частоту запросов — повторите через минуту") from e
    except SkladbotApiError as e:
        raise ValueError(f"skladbot.ru не отдал товары для заявки (HTTP {e.status_code})") from e
    except httpx.HTTPError as e:
        raise ValueError(f"Сетевая ошибка при обращении к skladbot.ru: {e}") from e

    skipped_barcodes = sorted(bc for bc in qty_by_barcode if bc not in resolved)
    products = [
        {
            "product_data_id": resolved[bc]["product_data_id"],
            "amount": qty,
            "barcode": bc,
            "services": [],
            "packages": [],
        }
        for bc, qty in qty_by_barcode.items()
        if bc in resolved
    ]
    if not products:
        raise ValueError(
            "Ни один ШК заявки не найден в доступных остатках ФФ — отправлять нечего "
            "(проверьте остатки склада у ФФ и синхронизацию)"
        )

    create_payload = {
        "customer_id": customer_id,
        "request_type_id": DELIVERY_REQUEST_TYPE_ID,
        "products": products,
        # marketplace / marketplace_warehouse — integer id из form-data (НЕ имя!);
        # marketplace_delivery_type — строковый ключ; даты — Y-m-d.
        "fields": {
            "marketplace": {"value": payload.marketplace_id},
            "marketplace_delivery_type": {"value": payload.delivery_type},
            "marketplace_warehouse": {"value": payload.marketplace_warehouse_id},
            "collection_date": {"value": payload.collection_date.isoformat()},
            "unloading_date": {"value": payload.unloading_date.isoformat()},
        },
        "comment": payload.comment or f"Заявка на сборку {asm_number} (DDS)",
        "notify": bool(payload.notify),
    }
    try:
        created = await client.create_request(create_payload)
    except CircuitOpenError as e:
        raise ValueError(f"skladbot.ru временно недоступен, попробуйте позже ({e})") from e
    except RateLimitError as e:
        raise ValueError("skladbot.ru ограничил частоту запросов — повторите через минуту") from e
    except SkladbotApiError as e:
        raise ValueError(f"skladbot.ru отклонил создание заявки (HTTP {e.status_code}): {e}") from e
    except httpx.HTTPError as e:
        raise ValueError(f"Сетевая ошибка при создании заявки в skladbot.ru: {e}") from e

    external_id = str(created.get("id") or created.get("request_id") or "").strip()
    if not external_id or external_id == "None":
        raise ValueError("skladbot.ru не вернул id созданной заявки — проверьте кабинет ФФ вручную")

    total_qty = sum(qty for bc, qty in qty_by_barcode.items() if bc in resolved)
    created_row = {
        "external_id": external_id,
        "kind": FfRequestKind.ASSEMBLY.value,
        "number": created.get("delivery_number") or created.get("number"),
        "type_id": DELIVERY_REQUEST_TYPE_ID,
        "type_name": created.get("type") or "3. Доставка на склад МП",
        "status": created.get("status"),
        "stage_code": created.get("stage_code"),
        "stage_title": created.get("stage_title"),
        "is_completed": bool(created.get("is_completed")),
        "archived": bool(created.get("archived")),
        "expired": bool(created.get("expired")),
        "total_qty": total_qty,
        "dest_warehouse": wh_name,
        "external_created_at": _parse_date(created.get("created_at")) or date.today(),
        "raw": created,
    }

    # Зеркало + связь — под advisory-локом (сериализация с конкурентным синком,
    # uq заявок = project_id+provider+external_id)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :project_id)"),
        {"ns": _FF_SYNC_LOCK_NS, "project_id": project_id},
    )
    await _apply_requests(db, project_id, warehouse_id, "skladbot", [created_row])
    await db.flush()
    new_result = await db.execute(
        select(FulfillmentRequest).where(
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.provider == "skladbot",
            FulfillmentRequest.external_id == external_id,
        )
    )
    ff_req = new_result.scalar_one_or_none()
    if ff_req is None:
        await db.rollback()
        raise ValueError(
            f"Заявка создана у ФФ ({created_row['number'] or external_id}), но зеркало сохранить не удалось — "
            "обновите страницу после синхронизации"
        )

    # Гонка связывания: сборку успели связать с другой ФФ-заявкой между проверкой и сейчас
    conflict = await db.execute(
        select(FulfillmentRequest.id)
        .where(
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.assembly_request_id == assembly_request_id,
            FulfillmentRequest.id != ff_req.id,
        )
        .limit(1)
    )
    if conflict.scalar_one_or_none() is not None:
        await db.commit()  # зеркало сохранено, но НЕсвязанным
        raise ValueError(
            f"Заявка создана у ФФ ({ff_req.number or external_id}), но сборка уже связана с другой "
            "ФФ-заявкой — свяжите вручную на вкладке «ФФ сборка»"
        )
    ff_req.assembly_request_id = assembly_request_id
    await db.commit()
    ff_number = ff_req.number or external_id

    # Best-effort: уведомить чат склада ФФ об отправленной заявке (никогда не бросает).
    from backend.services.fulfillment_notify import notify_ff_request_pushed

    await notify_ff_request_pushed(
        db,
        project_id,
        warehouse_id,
        ff_number=ff_number,
        items_sent=len(products),
        total_qty=total_qty,
        dest=wh_name,
        collection_date=payload.collection_date,
        unloading_date=payload.unloading_date,
    )

    return {
        "request": _request_to_dict(ff_req, {asm_id: (asm_number, asm_status)}),
        "external_id": external_id,
        "ff_number": ff_req.number,
        "items_sent": len(products),
        "total_qty": total_qty,
        "skipped_barcodes": skipped_barcodes,
    }
