# ruff: noqa: RUF001, RUF003
"""Маппинг WB-склада → федеральный округ (для отчёта «Индекс локализации»).

Эталонный справочник от 23.03.2026 (раздел WB «Локализация»). Группы:
  - 6 ФО РФ: central, northwest, south_caucasus, volga, ural, far_east_siberia
  - abroad: Армения, Беларусь, Грузия, Казахстан, Узбекистан

Используется для расчёта local/non-local заказов: заказ «локальный», если
ФО склада-источника == ФО доставки (oblast_okrug_name из WB API
/api/v1/supplier/orders).

Объединение Дальневосточного и Сибирского ФО — как в WB-кабинете.
"""

from decimal import Decimal

# ─── Канонические ключи ФО (используем в API ответах + UI) ────────────────────

DISTRICT_CENTRAL = "central"
DISTRICT_NORTHWEST = "northwest"
DISTRICT_SOUTH_CAUCASUS = "south_caucasus"
DISTRICT_VOLGA = "volga"
DISTRICT_URAL = "ural"
DISTRICT_FAR_EAST_SIBERIA = "far_east_siberia"
DISTRICT_ABROAD = "abroad"
DISTRICT_UNKNOWN = "unknown"  # склад/округ нет в справочнике

# Порядок отображения в UI (как в эталонном Google Sheet и кабинете WB).
DISTRICT_ORDER: list[str] = [
    DISTRICT_CENTRAL,
    DISTRICT_SOUTH_CAUCASUS,
    DISTRICT_VOLGA,
    DISTRICT_URAL,
    DISTRICT_FAR_EAST_SIBERIA,
    DISTRICT_NORTHWEST,
    DISTRICT_ABROAD,
]

DISTRICT_LABELS: dict[str, str] = {
    DISTRICT_CENTRAL: "Центральный",
    DISTRICT_NORTHWEST: "Северо-Западный",
    DISTRICT_SOUTH_CAUCASUS: "Южный и Северо-Кавказский",
    DISTRICT_VOLGA: "Приволжский",
    DISTRICT_URAL: "Уральский",
    DISTRICT_FAR_EAST_SIBERIA: "Дальневосточный и Сибирский",
    DISTRICT_ABROAD: "Зарубеж",
    DISTRICT_UNKNOWN: "Неизвестно",
}

# ─── Нормализация oblast_okrug_name из WB API → ключ ФО ──────────────────────
# WB API отдаёт строки вида "Приволжский федеральный округ" и т.п.
# Также «Северо-Кавказский ФО» и «Южный ФО» в WB идут отдельно — объединяем
# в единый south_caucasus согласно эталонному калькулятору локализации.
# «Сибирский» и «Дальневосточный» аналогично объединяем в far_east_siberia.

OKRUG_NAME_TO_KEY: dict[str, str] = {
    # Центральный
    "Центральный федеральный округ": DISTRICT_CENTRAL,
    # Северо-Западный
    "Северо-Западный федеральный округ": DISTRICT_NORTHWEST,
    # Южный + Северо-Кавказский
    "Южный федеральный округ": DISTRICT_SOUTH_CAUCASUS,
    "Северо-Кавказский федеральный округ": DISTRICT_SOUTH_CAUCASUS,
    # Приволжский
    "Приволжский федеральный округ": DISTRICT_VOLGA,
    # Уральский
    "Уральский федеральный округ": DISTRICT_URAL,
    # Дальневосточный + Сибирский
    "Дальневосточный федеральный округ": DISTRICT_FAR_EAST_SIBERIA,
    "Сибирский федеральный округ": DISTRICT_FAR_EAST_SIBERIA,
}


def okrug_to_district(okrug_name: str | None, country_name: str | None = None) -> str:
    """Нормализовать oblast_okrug_name (или country_name для зарубежья) → ключ ФО.

    Приоритет: country != Россия → abroad. Иначе по карте OKRUG_NAME_TO_KEY.
    """
    if country_name and country_name.strip() and country_name.strip() != "Россия":
        return DISTRICT_ABROAD
    if not okrug_name:
        return DISTRICT_UNKNOWN
    return OKRUG_NAME_TO_KEY.get(okrug_name.strip(), DISTRICT_UNKNOWN)


# ─── warehouseName → ФО ───────────────────────────────────────────────────────
# Источник: WB-кабинет «Логистика → Склады → Коэффициенты по коробам» (2026-04).

WAREHOUSE_TO_DISTRICT: dict[str, str] = {
    # ── Центральный ──────────────────────────────────────────────────────────
    "Пушкино": DISTRICT_CENTRAL,
    "Вёшки": DISTRICT_CENTRAL,
    "Иваново": DISTRICT_CENTRAL,
    "Подольск 3": DISTRICT_CENTRAL,
    "Радумля 1": DISTRICT_CENTRAL,
    "Подольск 4": DISTRICT_CENTRAL,
    "Обухово 2": DISTRICT_CENTRAL,
    "Чашниково": DISTRICT_CENTRAL,
    "Воронеж СГТ": DISTRICT_CENTRAL,
    "Истра": DISTRICT_CENTRAL,
    "Коледино: Горючее": DISTRICT_CENTRAL,
    "Домодедово 2": DISTRICT_CENTRAL,
    "Никольское": DISTRICT_CENTRAL,
    "Никольское СГТ": DISTRICT_CENTRAL,
    "Обухово СГТ новый": DISTRICT_CENTRAL,
    "Тверь СГТ": DISTRICT_CENTRAL,
    "Климовск СГТ": DISTRICT_CENTRAL,
    "СК Домодедово Шинный": DISTRICT_CENTRAL,
    "Щеглово (Холмогоры) СГТ": DISTRICT_CENTRAL,
    "Домодедово 2: Питание": DISTRICT_CENTRAL,
    "Чашниково СГТ": DISTRICT_CENTRAL,
    "Домодедово 2 СГТ": DISTRICT_CENTRAL,
    "Чашниково: Горючее": DISTRICT_CENTRAL,
    "Голицыно СГТ": DISTRICT_CENTRAL,
    "Радумля СГТ": DISTRICT_CENTRAL,
    "Софьино СГТ": DISTRICT_CENTRAL,
    "Софрино СГТ": DISTRICT_CENTRAL,
    "Ярославль СГТ": DISTRICT_CENTRAL,
    "Подольск 3 СГТ": DISTRICT_CENTRAL,
    "Цифровой склад": DISTRICT_CENTRAL,
    "Рязань": DISTRICT_CENTRAL,
    "Рязань (Тюшевское)": DISTRICT_CENTRAL,
    "Рязань (Тюшевское): Питание": DISTRICT_CENTRAL,
    "Сабурово": DISTRICT_CENTRAL,
    "Владимир": DISTRICT_CENTRAL,
    "Владимир (Воршинское): Питание": DISTRICT_CENTRAL,
    "Тула": DISTRICT_CENTRAL,
    "Алексин (Тула)": DISTRICT_CENTRAL,
    "Воронеж": DISTRICT_CENTRAL,
    "Котовск": DISTRICT_CENTRAL,
    "Котовск: Питание": DISTRICT_CENTRAL,
    "Электросталь": DISTRICT_CENTRAL,
    "Тверь Эммаусское": DISTRICT_CENTRAL,
    # Новый склад WB (активен с 2026-05-15); в orders/stocks API приходит
    # коротким именем «Тверь». Без записи улетал в unknown → нейтральный вес
    # в Hamilton и выпадение из district-pooling (аудит 2026-07-09).
    "Тверь": DISTRICT_CENTRAL,
    "Воронеж: Питание": DISTRICT_CENTRAL,
    "Электросталь: Питание": DISTRICT_CENTRAL,
    "Обухово": DISTRICT_CENTRAL,
    "Коледино": DISTRICT_CENTRAL,
    "Белая дача": DISTRICT_CENTRAL,
    "Подольск": DISTRICT_CENTRAL,
    "Щербинка": DISTRICT_CENTRAL,
    "Чехов 1": DISTRICT_CENTRAL,
    "Чехов 2": DISTRICT_CENTRAL,
    "Белые Столбы": DISTRICT_CENTRAL,
    "Белые Столбы: Горючее": DISTRICT_CENTRAL,
    # ── Северо-Западный ──────────────────────────────────────────────────────
    "СЦ Вологда 2": DISTRICT_NORTHWEST,
    "СЦ Шушары": DISTRICT_NORTHWEST,
    "Красный Бор СГТ": DISTRICT_NORTHWEST,
    "Санкт-Петербург СГТ": DISTRICT_NORTHWEST,
    "Шушары: Питание": DISTRICT_NORTHWEST,
    "СПБ Шушары": DISTRICT_NORTHWEST,
    "Санкт-Петербург Уткина Заводь": DISTRICT_NORTHWEST,
    "Калининград": DISTRICT_NORTHWEST,
    # ── Южный и Северо-Кавказский ────────────────────────────────────────────
    "Крыловская": DISTRICT_SOUTH_CAUCASUS,
    "Краснодар СГТ": DISTRICT_SOUTH_CAUCASUS,
    "Волгоград": DISTRICT_SOUTH_CAUCASUS,
    "Волгоград: Питание": DISTRICT_SOUTH_CAUCASUS,
    "Невинномысск": DISTRICT_SOUTH_CAUCASUS,
    "Краснодар": DISTRICT_SOUTH_CAUCASUS,
    "Краснодар (Тихорецкая): Питание": DISTRICT_SOUTH_CAUCASUS,
    # ── Приволжский ──────────────────────────────────────────────────────────
    "СЦ Ижевск": DISTRICT_VOLGA,
    "СЦ Кузнецк": DISTRICT_VOLGA,
    "СЦ Оренбург Центральная": DISTRICT_VOLGA,
    "Пермь 4": DISTRICT_VOLGA,
    "Пенза СГТ": DISTRICT_VOLGA,
    "Кузнецк СГТ": DISTRICT_VOLGA,
    "Пенза": DISTRICT_VOLGA,
    "Казань СГТ": DISTRICT_VOLGA,
    "СЦ Нижний Новгород Ларина": DISTRICT_VOLGA,
    "Самара (Новосемейкино)": DISTRICT_VOLGA,
    "Новосемейкино: Питание": DISTRICT_VOLGA,
    "Сарапул": DISTRICT_VOLGA,
    "Казань": DISTRICT_VOLGA,
    "Казань: Питание": DISTRICT_VOLGA,
    # ── Уральский ────────────────────────────────────────────────────────────
    "Екатеринбург СГТ": DISTRICT_URAL,
    "Нижний Тагил Восточное": DISTRICT_URAL,
    "Нижний Тагил (Восточное) СГТ": DISTRICT_URAL,
    "Челябинск СГТ": DISTRICT_URAL,
    "Екатеринбург - Испытателей 14г": DISTRICT_URAL,
    "Екатеринбург - Перспективная 14": DISTRICT_URAL,
    "Екатеринбург (Перспективная): Питание": DISTRICT_URAL,
    # ── Дальневосточный и Сибирский ──────────────────────────────────────────
    "СЦ Хабаровск": DISTRICT_FAR_EAST_SIBERIA,
    "СЦ Барнаул": DISTRICT_FAR_EAST_SIBERIA,
    "Владивосток": DISTRICT_FAR_EAST_SIBERIA,
    "Юрга": DISTRICT_FAR_EAST_SIBERIA,
    "Новосибирск СГТ": DISTRICT_FAR_EAST_SIBERIA,
    "Владивосток СГТ": DISTRICT_FAR_EAST_SIBERIA,
    "Хабаровск": DISTRICT_FAR_EAST_SIBERIA,
    "Новосибирск": DISTRICT_FAR_EAST_SIBERIA,
    # ── Виртуальные склады WB (отгрузка через FBS-сеть в нужный город) ──────
    "Виртуальный Москва Радумля": DISTRICT_CENTRAL,
    "Виртуальный Владимир": DISTRICT_CENTRAL,
    "СЦ Курск": DISTRICT_CENTRAL,
    "Виртуальный Краснодар": DISTRICT_SOUTH_CAUCASUS,
    "Виртуальный Крым": DISTRICT_SOUTH_CAUCASUS,
    "Виртуальный Махачкала": DISTRICT_SOUTH_CAUCASUS,
    "Виртуальный Самара": DISTRICT_VOLGA,
    "Виртуальный Оренбург": DISTRICT_VOLGA,
    "Виртуальный Челябинск": DISTRICT_URAL,
    # ── Зарубеж ──────────────────────────────────────────────────────────────
    "СЦ Ереван": DISTRICT_ABROAD,
    "СК Ереван": DISTRICT_ABROAD,
    "Минск": DISTRICT_ABROAD,
    "СЦ Брест": DISTRICT_ABROAD,
    "СЦ Гродно": DISTRICT_ABROAD,
    "СК Великий Камень": DISTRICT_ABROAD,
    "СЦ Тбилиси 3 Бонд": DISTRICT_ABROAD,
    "СЦ Байсерке": DISTRICT_ABROAD,
    "Атакент": DISTRICT_ABROAD,
    "Актобе": DISTRICT_ABROAD,
    "Астана Карагандинское шоссе": DISTRICT_ABROAD,
    "Ташкент": DISTRICT_ABROAD,
    "Ташкент 2": DISTRICT_ABROAD,
    "СЦ Душанбе": DISTRICT_ABROAD,
}

# Лог-коэффициент склада (% к базовой логистике короба, 2026-03-23).
# `None` для СЦ — товар не лежит, перевалка. Используется для будущих
# расчётов рублёвой логистики; пока для отчёта локализации не нужен.
WAREHOUSE_LOGISTICS_COEF: dict[str, Decimal | None] = {
    # ── Центральный ──
    "Пушкино": None,
    "Вёшки": None,
    "Иваново": None,
    "Подольск 3": None,
    "Радумля 1": None,
    "Подольск 4": None,
    "Обухово 2": None,
    "Чашниково": None,
    "Воронеж СГТ": None,
    "Истра": None,
    "Коледино: Горючее": None,
    "Домодедово 2": None,
    "Никольское": None,
    "Никольское СГТ": None,
    "Обухово СГТ новый": None,
    "Тверь СГТ": None,
    "Климовск СГТ": None,
    "СК Домодедово Шинный": None,
    "Щеглово (Холмогоры) СГТ": None,
    "Домодедово 2: Питание": None,
    "Чашниково СГТ": None,
    "Домодедово 2 СГТ": None,
    "Чашниково: Горючее": None,
    "Голицыно СГТ": Decimal("0.75"),
    "Радумля СГТ": Decimal("0.95"),
    "Софьино СГТ": Decimal("0.95"),
    "Софрино СГТ": Decimal("0.95"),
    "Ярославль СГТ": Decimal("1.00"),
    "Подольск 3 СГТ": Decimal("1.00"),
    "Цифровой склад": Decimal("1.00"),
    "Рязань (Тюшевское)": Decimal("1.20"),
    "Рязань (Тюшевское): Питание": Decimal("1.20"),
    "Сабурово": Decimal("1.30"),
    "Владимир": Decimal("1.40"),
    "Тула": Decimal("1.50"),
    "Воронеж": Decimal("1.50"),
    "Котовск": Decimal("1.50"),
    "Котовск: Питание": Decimal("1.50"),
    "Электросталь": Decimal("1.60"),
    "Тверь Эммаусское": Decimal("1.60"),
    "Воронеж: Питание": Decimal("1.60"),
    "Электросталь: Питание": Decimal("1.60"),
    "Обухово": Decimal("1.70"),
    "Коледино": Decimal("1.95"),
    "Белая дача": Decimal("1.95"),
    "Подольск": Decimal("2.00"),
    "Щербинка": Decimal("2.00"),
    "Чехов 1": Decimal("2.05"),
    "Чехов 2": Decimal("2.05"),
    "Белые Столбы": Decimal("2.80"),
    "Белые Столбы: Горючее": Decimal("2.80"),
    # ── Северо-Западный ──
    "СЦ Вологда 2": None,
    "СЦ Шушары": None,
    "Красный Бор СГТ": None,
    "Санкт-Петербург СГТ": Decimal("1.05"),
    "Шушары: Питание": Decimal("2.20"),
    "СПБ Шушары": Decimal("2.20"),
    "Санкт-Петербург Уткина Заводь": Decimal("2.70"),
    # ── Южный и Северо-Кавказский ──
    "Крыловская": None,
    "Краснодар СГТ": None,
    "Волгоград": Decimal("1.10"),
    "Волгоград: Питание": Decimal("1.10"),
    "Невинномысск": Decimal("1.30"),
    "Краснодар": Decimal("1.60"),
    "Краснодар (Тихорецкая): Питание": Decimal("1.60"),
    # ── Приволжский ──
    "СЦ Ижевск": None,
    "СЦ Кузнецк": None,
    "СЦ Оренбург Центральная": None,
    "Пермь 4": None,
    "Пенза СГТ": None,
    "Кузнецк СГТ": Decimal("1.00"),
    "Пенза": Decimal("1.00"),
    "Казань СГТ": Decimal("1.00"),
    "СЦ Нижний Новгород Ларина": Decimal("1.50"),
    "Самара (Новосемейкино)": Decimal("2.00"),
    "Новосемейкино: Питание": Decimal("2.00"),
    "Сарапул": Decimal("2.10"),
    "Казань": Decimal("2.20"),
    "Казань: Питание": Decimal("2.20"),
    # ── Уральский ──
    "Екатеринбург СГТ": None,
    "Нижний Тагил Восточное": None,
    "Нижний Тагил (Восточное) СГТ": None,
    "Челябинск СГТ": Decimal("1.00"),
    "Екатеринбург - Испытателей 14г": Decimal("2.20"),
    "Екатеринбург - Перспективная 14": Decimal("2.25"),
    "Екатеринбург (Перспективная): Питание": Decimal("2.25"),
    # ── Дальневосточный и Сибирский ──
    "СЦ Хабаровск": None,
    "СЦ Барнаул": None,
    "Владивосток": None,
    "Юрга": None,
    "Новосибирск СГТ": None,
    "Владивосток СГТ": None,
    "Хабаровск": Decimal("2.20"),
    "Новосибирск": Decimal("4.45"),
    # ── Зарубеж ──
    "СЦ Ереван": Decimal("1.55"),
    "СК Ереван": Decimal("1.55"),
    "Минск": None,
    "СЦ Брест": None,
    "СЦ Гродно": None,
    "СК Великий Камень": None,
    "СЦ Тбилиси 3 Бонд": Decimal("1.30"),
    "СЦ Байсерке": None,
    "Атакент": Decimal("1.35"),
    "Актобе": Decimal("1.45"),
    "Астана Карагандинское шоссе": Decimal("1.45"),
    "Ташкент": Decimal("1.00"),
    "Ташкент 2": Decimal("1.00"),
}


def warehouse_to_district(warehouse_name: str | None) -> str:
    """Маппинг warehouse_name (как отдаёт WB API) → ключ ФО.

    Возвращает DISTRICT_UNKNOWN если склад не найден в справочнике
    (новые СЦ от WB — нужно дописывать в WAREHOUSE_TO_DISTRICT).
    """
    if not warehouse_name:
        return DISTRICT_UNKNOWN
    return WAREHOUSE_TO_DISTRICT.get(warehouse_name.strip(), DISTRICT_UNKNOWN)


# ─── Aliases: 3rd-party короткие имена → канон WB API ────────────────────────
# Источник: таблица скоростей доставки складов от tg:@POSTAVLENOru_BOT
# (файл backend/data/wb_warehouse_speed.json). Бот использует сокращённые
# имена; для lookup в WAREHOUSE_TO_DISTRICT и сопоставления с warehouseName
# из /api/v1/supplier/orders нужна нормализация.
WAREHOUSE_NAME_ALIASES: dict[str, str] = {
    "Новосемейкино": "Самара (Новосемейкино)",
    "Перспективный": "Екатеринбург - Перспективная 14",
    "Барнаул СЦ 2": "СЦ Барнаул",
    "СК Тверь Эммаусское": "Тверь Эммаусское",
    "Склад СПБ Шушары Московское": "СПБ Шушары",
    "СЦ Оренбург": "СЦ Оренбург Центральная",
    "Чехов": "Чехов 1",
    "СК Великий Камень (Белоруссия)": "СК Великий Камень",
    "Алматы Атакент": "Атакент",
    "Склад Астана Карагандинское шоссе": "Астана Карагандинское шоссе",
    "Склад Чашниково": "Чашниково",
    # Регистр-рассинхрон: speed JSON пишет «Белая Дача» (заглавная Д),
    # WB API и WAREHOUSE_TO_DISTRICT — «Белая дача». Без алиаса склад
    # выпадал из speed-механики целиком: не помечался воришкой (top-1
    # Петропавловска-Камчатского) и терял priority-score (аудит 2026-07-09).
    "Белая Дача": "Белая дача",
    # WB orders/stocks отдают короткие имена «Тула»/«Рязань», а speed-карта —
    # полные «Алексин (Тула)»/«Рязань (Тюшевское)». Без алиасов need-ключи
    # после paren-strip не матчились с цепочками: score=0, thief-флаг
    # не срабатывал (аудит 2026-07-09).
    "Тула": "Алексин (Тула)",
    "Рязань": "Рязань (Тюшевское)",
}


def canonical_warehouse_name(name: str | None) -> str | None:
    """Привести имя склада к каноническому виду WB API.

    Двусторонняя канонизация: 3rd-party источники (POSTAVLENO bot)
    используют сокращения, наш WAREHOUSE_TO_DISTRICT и WB API — длинные
    имена. Без нормализации matrix-lookup промахивается.
    """
    if not name:
        return None
    n = name.strip()
    return WAREHOUSE_NAME_ALIASES.get(n, n)
