# ruff: noqa: RUF002 — русские комментарии и docstring
"""Schemas: /reviews — WB customer feedbacks (отзывы покупателей)."""

from __future__ import annotations

from pydantic import BaseModel


class ReviewItem(BaseModel):
    """Один отзыв покупателя WB."""

    id: str
    text: str
    rating: int  # productValuation, 1..5 (0 если WB не прислал)
    created_date: str | None = None
    user_name: str | None = None
    pros: str | None = None
    cons: str | None = None
    nm_id: int | None = None
    product_name: str | None = None
    article: str | None = None
    brand: str | None = None
    is_answered: bool = False


class ReviewsListResponse(BaseModel):
    """Ответ списка отзывов + агрегаты."""

    items: list[ReviewItem]
    # Всего отзывов в текущем срезе (по фильтру is_answered) — для пагинации «показано N из M»
    total: int = 0
    count_unanswered: int = 0
    count_archive: int = 0
    average_rating: float | None = None
    # False → у проекта не настроен активный WB-ключ (фронт покажет подсказку)
    has_key: bool = True


# ─── Сводная аналитика отзывов (из зеркала БД) ──────────────────────────────


class ReviewsSummary(BaseModel):
    """Верхние KPI сводки отзывов."""

    average_rating: float | None = None  # средняя оценка (1..5), None если нет оценок
    total: int = 0  # всего отзывов
    count_no_text: int = 0  # только оценка, без текста/плюсов/минусов
    count_with_text: int = 0  # с текстом
    count_unanswered: int = 0  # без ответа продавца
    count_positive: int = 0  # оценка 4..5
    count_negative: int = 0  # оценка 1..2


class MonthlyRatingPoint(BaseModel):
    """Точка графика «средний рейтинг по бакету» (месяц или день — см. granularity)."""

    month: str  # YYYY-MM (granularity=month) либо YYYY-MM-DD (granularity=day)
    avg_rating: float | None = None
    count: int = 0


class MonthlyVolumePoint(BaseModel):
    """Точка графика «объём отзывов за бакет с разбивкой по оценкам»."""

    month: str  # YYYY-MM либо YYYY-MM-DD (см. granularity)
    r1: int = 0
    r2: int = 0
    r3: int = 0
    r4: int = 0
    r5: int = 0


class GroupRating(BaseModel):
    """Карточка сводного рейтинга по категории / бренду: средняя + распределение 1..5."""

    name: str
    avg_rating: float | None = None
    count: int = 0
    r1: int = 0
    r2: int = 0
    r3: int = 0
    r4: int = 0
    r5: int = 0


class ComplaintCandidate(BaseModel):
    """Отзыв-кандидат на жалобу (низкая оценка) + текущий статус жалобы."""

    wb_feedback_id: str
    nm_id: int | None = None
    rating: int
    text: str = ""
    cons: str | None = None
    created_date: str | None = None
    user_name: str | None = None
    product_name: str | None = None
    brand: str | None = None
    complaint_status: str | None = None  # None | pending | removed | rejected


class ComplaintCandidatesResponse(BaseModel):
    """Кандидаты на жалобу (низкооценённые отзывы)."""

    items: list[ComplaintCandidate] = []
    total_open: int = 0  # всего накопившихся кандидатов без жалобы (не только загруженных)
    has_key: bool = True


class ComplaintAgentItem(BaseModel):
    """ИИ-агент подготовки жалоб."""

    id: int
    name: str
    enabled: bool = True
    subject: str | None = None
    brand: str | None = None
    nm_ids: str | None = None
    star_levels: str = "1,2,3"
    rules: str = ""
    examples: str | None = None
    llm_provider: str = "openai_compatible"
    llm_model: str = "deepseek-chat"
    llm_base_url: str | None = None
    last_run_at: str | None = None


class ComplaintAgentSave(BaseModel):
    """Создание/обновление агента (частичное — все поля опциональны при PATCH)."""

    name: str | None = None
    enabled: bool | None = None
    subject: str | None = None
    brand: str | None = None
    nm_ids: str | None = None
    star_levels: str | None = None
    rules: str | None = None
    examples: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None


class ComplaintAgentRunResult(BaseModel):
    """Итог прогона агента."""

    checked: int = 0  # проверено отзывов
    qualified: int = 0  # признано основанием
    created: int = 0  # создано жалоб
    errors: int = 0  # ошибок LLM/создания
    limit: int = 0  # кап за прогон


class ComplaintCreate(BaseModel):
    """Запрос на подачу жалобы на отзыв."""

    wb_feedback_id: str
    reason: str = "not_related"
    text: str


class ComplaintBulkCreate(BaseModel):
    """Массовая подача жалоб на все накопившиеся низкооценённые отзывы."""

    reason: str = "not_related"
    text: str  # один текст на все жалобы (шаблон не привязан к конкретному отзыву)
    max_rating: int = 3


class ComplaintBulkResult(BaseModel):
    """Итог массовой подачи."""

    created: int = 0  # сколько жалоб зафиксировано
    truncated: bool = False  # упёрлись в лимит за прогон (остались ещё)


class ComplaintStatusUpdate(BaseModel):
    """Смена статуса жалобы (исход)."""

    status: str  # removed | rejected | pending
    note: str | None = None


class ComplaintItem(BaseModel):
    """Поданная жалоба + снапшот отзыва."""

    id: int
    wb_feedback_id: str
    nm_id: int | None = None
    rating: int = 0
    reason: str = "not_related"
    status: str = "pending"
    text: str = ""
    note: str | None = None
    created_at: str | None = None
    resolved_at: str | None = None
    product_name: str | None = None
    review_text: str | None = None  # текст отзыва (для контекста)


class ComplaintStats(BaseModel):
    """Оцифровка эффективности жалоб."""

    filed: int = 0  # всего подано
    removed: int = 0  # удалено
    rejected: int = 0  # не удалено (отклонено)
    pending: int = 0  # в ожидании
    removal_rate: float | None = None  # % удалённых от закрытых


class ComplaintsResponse(BaseModel):
    """Список поданных жалоб + KPI."""

    items: list[ComplaintItem] = []
    stats: ComplaintStats = ComplaintStats()
    has_key: bool = True


class ReviewBreakdownRow(BaseModel):
    """Строка детальной таблицы: группа (период/предмет/бренд/артикул) + распределение оценок."""

    key: str
    label: str
    total: int = 0
    avg_rating: float | None = None
    r1: int = 0
    r2: int = 0
    r3: int = 0
    r4: int = 0
    r5: int = 0


class ReviewBreakdownResponse(BaseModel):
    """Детальная таблица отзывов с группировкой + итог + опции фильтров."""

    group_by: str = "month"
    rows: list[ReviewBreakdownRow] = []
    totals: ReviewBreakdownRow = ReviewBreakdownRow(key="__total__", label="Итого")
    subjects: list[str] = []  # опции фильтра «Предмет»
    brands: list[str] = []  # опции фильтра «Бренд»
    truncated: bool = False  # список групп усечён лимитом
    has_key: bool = True


class NewcomerReview(BaseModel):
    """Проблемная новинка: товар недавно на продаже, но рейтинг ниже порога."""

    nm_id: int
    name: str  # название товара (снапшот WB) либо «nmID …»
    brand: str
    subject: str  # предмет/категория
    first_date: str  # YYYY-MM-DD — эффективная дата старта (продажа или первый отзыв)
    date_source: str = "review"  # "sale" — Nomenclature.first_sale_date; "review" — дата первого отзыва (прокси)
    days_on_sale: int  # сколько дней «на продаже» по first_date
    avg_rating: float | None = None
    count: int = 0  # всего отзывов
    count_unanswered: int = 0
    neg_unanswered: int = 0  # негатив (1–2★) без ответа — «горит, нужен ответ»
    r1: int = 0
    r2: int = 0
    r3: int = 0
    r4: int = 0
    r5: int = 0
    tags: list[str] = []  # имена ярлыков товара (для фильтра списка по ярлыку)


class NewcomerGroup(BaseModel):
    """Разрез проблемных новинок по категории / бренду / ярлыку."""

    name: str
    products: int = 0  # число проблемных новинок в группе
    avg_rating: float | None = None  # средняя по их отзывам (rating>0)
    count: int = 0  # всего отзывов по этим новинкам
    r1: int = 0
    r2: int = 0
    r3: int = 0
    r4: int = 0
    r5: int = 0


class ComplaintTerm(BaseModel):
    """Частая тема жалоб: слово из негативных отзывов + частота."""

    term: str
    count: int


class NewcomersResponse(BaseModel):
    """Раздел «Проблемные новинки»: новинки с рейтингом ниже порога."""

    items: list[NewcomerReview] = []
    # Распределение проблемных новинок по разрезам (для карточек)
    by_category: list[NewcomerGroup] = []
    by_brand: list[NewcomerGroup] = []
    by_tag: list[NewcomerGroup] = []
    # Всего новинок в окне (любой рейтинг) — для доли проблемных в KPI
    total_newcomers: int = 0
    # Частые темы жалоб по негативным отзывам проблемных новинок
    complaint_terms: list[ComplaintTerm] = []
    days: int = 30  # окно «новинки» (дней на продаже)
    max_rating: float = 4.6  # порог «плохого» рейтинга
    # False → у проекта не настроен активный WB-ключ (фронт покажет подсказку)
    has_key: bool = True


class ReviewsSummaryResponse(BaseModel):
    """Полная сводка отзывов проекта (все блоки сводной страницы)."""

    summary: ReviewsSummary = ReviewsSummary()
    monthly_rating: list[MonthlyRatingPoint] = []
    monthly_volume: list[MonthlyVolumePoint] = []
    by_category: list[GroupRating] = []
    by_brand: list[GroupRating] = []
    # Гранулярность временных рядов: "day" (короткие периоды) либо "month"
    granularity: str = "month"
    # Применённый период выборки (2w/1m/3m/6m/1y/all) — эхо запроса для фронта
    period: str = "1y"
    # False → у проекта не настроен активный WB-ключ (фронт покажет подсказку)
    has_key: bool = True


# ─── Вопросы покупателей (зеркало wb_questions) ─────────────────────────────


class QuestionItem(BaseModel):
    """Один вопрос покупателя WB из зеркала."""

    id: str  # wb_id
    nm_id: int | None = None
    text: str | None = None
    answer_text: str | None = None
    is_answered: bool = False
    created_date: str | None = None
    user_name: str | None = None
    subject: str | None = None
    product_name: str | None = None
    article: str | None = None
    brand: str | None = None
    has_stock_watch: bool = False  # True — следим за поступлением (wb_stock_watches)


class QuestionsListResponse(BaseModel):
    """Список вопросов проекта + счётчики."""

    items: list[QuestionItem] = []
    count_unanswered: int = 0
    count_archive: int = 0
    has_key: bool = True


class QuestionsSyncResult(BaseModel):
    """Итог on-demand синка вопросов."""

    rows_fetched: int = 0
    rows_upserted: int = 0
    has_key: bool = True


# ─── ИИ-агенты автоответов ───────────────────────────────────────────────────


class ReplyAgentItem(BaseModel):
    """ИИ-агент автоответов на отзывы/вопросы."""

    id: int
    name: str
    enabled: bool = True
    target: str = "both"  # feedback|question|both
    star_levels: str = "1,2,3,4,5"
    nm_ids: str | None = None
    auto_send: bool = False
    rules: str = ""
    examples: str | None = None
    llm_provider: str = "openai_compatible"
    llm_model: str = "deepseek-chat"
    llm_base_url: str | None = None
    last_run_at: str | None = None


class ReplyAgentSave(BaseModel):
    """Создание/обновление агента автоответов (частичное при PATCH)."""

    name: str | None = None
    enabled: bool | None = None
    target: str | None = None  # feedback|question|both
    star_levels: str | None = None
    nm_ids: str | None = None
    auto_send: bool | None = None
    rules: str | None = None
    examples: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None


class ReplyAgentRunResult(BaseModel):
    """Итог прогона агента автоответов."""

    checked: int = 0  # проверено целей
    drafted: int = 0  # создано черновиков
    needs_info: int = 0  # из них без фактов КБ — на ручную доработку
    errors: int = 0  # ошибок LLM/сохранения
    limit: int = 0  # кап за прогон
    auto_send: bool = False  # автоотправка отключена: всегда False (ручное одобрение)


# ─── Ответы на отзывы/вопросы (wb_feedback_replies) ──────────────────────────


class ReplyTarget(BaseModel):
    """Данные цели ответа из зеркала (для UI)."""

    text: str | None = None
    rating: int | None = None  # только для отзывов
    nm_id: int | None = None
    product_name: str | None = None
    brand: str | None = None
    subject: str | None = None
    user_name: str | None = None
    created_date: str | None = None


class ReplyItem(BaseModel):
    """Один ответ/черновик продавца."""

    id: int
    target_type: str  # feedback|question
    target_wb_id: str
    draft_text: str
    final_text: str | None = None
    text: str  # финальный текст (final_text или draft_text)
    status: str  # draft|approved|sent|error|rejected
    source: str  # agent|manual
    agent_id: int | None = None
    needs_info: bool = False  # True — в КБ нет фактов для ответа, ждёт ручной доработки
    generation: str | None = None  # llm|kb_direct|template|None (ручной/needs_info-заглушка)
    is_stock_reply: bool = False  # True — черновик «товар появился в наличии» (wb_stock_watches)
    error: str | None = None
    sent_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    target: ReplyTarget | None = None


class RepliesListResponse(BaseModel):
    """Список ответов проекта + счётчики по статусам."""

    items: list[ReplyItem] = []
    total: int = 0
    counts: dict[str, int] = {}


class ReplyCreate(BaseModel):
    """Ручной черновик ответа."""

    target_type: str  # feedback|question
    target_wb_id: str
    text: str


class ReplyUpdate(BaseModel):
    """Редактирование/модерация ответа: text — правка; action — approve|reject|reopen."""

    text: str | None = None
    action: str | None = None


class ReplySendResult(BaseModel):
    """Итог отправки approved-ответов."""

    sent: int = 0
    errors: int = 0
    pending: int = 0  # сколько approved осталось в очереди


# ─── База знаний товаров (wb_product_kb) ─────────────────────────────────────


class KbProductItem(BaseModel):
    """Товар проекта с числом записей базы знаний."""

    nm_id: int
    kb_count: int = 0
    product_name: str | None = None
    article: str | None = None
    brand: str | None = None
    card_synced_at: str | None = None  # когда синкнуто зеркало карточки (None — нет карточки)


class KbProductsResponse(BaseModel):
    """Список товаров с записями КБ."""

    items: list[KbProductItem] = []
    total: int = 0


class KbItem(BaseModel):
    """Одна запись базы знаний товара."""

    id: int
    nm_id: int
    topic: str
    question_example: str | None = None
    answer: str
    source: str = "manual"  # manual|import|card
    enabled: bool = True
    created_at: str | None = None
    updated_at: str | None = None


class KbListResponse(BaseModel):
    """Записи базы знаний + total."""

    items: list[KbItem] = []
    total: int = 0


class KbSave(BaseModel):
    """Создание/обновление записи КБ (частичное при PATCH)."""

    nm_id: int | None = None
    topic: str | None = None
    question_example: str | None = None
    answer: str | None = None
    enabled: bool | None = None


class KbImportResult(BaseModel):
    """Итог импорта КБ из архива отвеченных вопросов."""

    source_questions: int = 0  # отвеченных вопросов в зеркале
    created: int = 0  # создано записей КБ
    skipped_dupe: int = 0  # пропущено дублей
    skipped_empty: int = 0  # пропущено пустых текстов
    nm_count: int = 0  # затронуто товаров (nm_id)


# ─── Зеркало карточек WB (wb_product_cards) ──────────────────────────────────


class CardItem(BaseModel):
    """Карточка товара из зеркала (публичный API WB)."""

    nm_id: int
    title: str | None = None
    brand: str | None = None
    subject: str | None = None
    description: str | None = None
    contents: str | None = None  # комплектация
    characteristics: list[dict] = []  # [{"name": "...", "value": "..."}]
    photo_urls: list[str] = []  # URL big-фото (байты не скачиваем)
    synced_at: str | None = None


class CardSyncResult(BaseModel):
    """Итог on-demand синка карточек WB."""

    cards_total: int = 0  # сколько nm_id взято в прогон
    synced: int = 0  # карточек скачано и upsert'нуто
    not_found: int = 0  # 404 — карточки нет на WB
    errors: int = 0  # прочие сбои (сеть, HTTP≠200/404)


class KbCardImportResult(BaseModel):
    """Итог импорта КБ из зеркала карточек."""

    cards_total: int = 0  # карточек в зеркале
    created: int = 0  # создано записей КБ (source='card')
    updated: int = 0  # обновлено изменившихся значений
    unchanged: int = 0  # без изменений


# ─── Слежение за поступлением товара (wb_stock_watches) ──────────────────────


class StockWatchItem(BaseModel):
    """Одно слежение «вопрос → ждём поступление товара»."""

    id: int
    nm_id: int
    question_wb_id: str
    status: str  # watching|drafted|dismissed
    reply_id: int | None = None
    last_qty: int | None = None  # остаток (totalQuantity) при последней проверке тика
    created_at: str | None = None
    resolved_at: str | None = None
    question_text: str | None = None
    product_name: str | None = None


class StockWatchListResponse(BaseModel):
    """Список watches проекта + счётчики по статусам."""

    items: list[StockWatchItem] = []
    total: int = 0
    counts: dict[str, int] = {}


class StockWatchScanResult(BaseModel):
    """Итог on-demand скана вопросов о наличии."""

    scanned: int = 0  # неотвеченных вопросов о наличии найдено
    created: int = 0  # создано watches
    dismissed: int = 0  # снято слежение (вопрос отвечен)


class StockWatchTickResult(BaseModel):
    """Итог ручного прогона проверки остатков (stock_watch_tick)."""

    checked: int = 0  # проверено watching-watches
    drafted: int = 0  # создано черновиков «товар появился»
    waiting: int = 0  # всё ещё ждут поступления
    errors: int = 0  # сетевые/прочие ошибки (не валят тик)
