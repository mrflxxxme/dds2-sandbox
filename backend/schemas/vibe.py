# ruff: noqa: RUF001
"""Schemas for the Vibecoding tab."""

from datetime import date

from pydantic import BaseModel


class VibeAuthorRef(BaseModel):
    """Вайбкодер для селектора. Один человек = один user_id, сколько бы git-почт у него
    ни было (у Дениса их две, и самый крупный коммит недели — под второй)."""

    user_id: int
    name: str
    # Кто из списка — сам запрашивающий. Без флага фронт не отличит себя от тёзки и
    # вынужден был бы держать отдельный пункт «Моя статистика» — а он дублирует
    # строку с собственным именем.
    is_me: bool = False


class VibeShipment(BaseModel):
    """Одна поставка на прод."""

    sha: str
    short: str
    day: date
    ctype: str
    scope: str
    section: str  # человеческое имя раздела («Управление рекламой»)
    title: str
    added: int
    deleted: int
    files: int
    is_product: bool


class VibeRhythm(BaseModel):
    """Дней с поставкой в скользящем окне.

    НЕ стрик подряд: стрик обнуляется за выходной и за день, ушедший в чужой баг, и
    подталкивает коммитить мелочь ради цифры. Окно к паузе равнодушно.
    """

    hit: int
    denom: int
    window: int
    start: date
    end: date


class VibeAreaVolume(BaseModel):
    """Объём по области кода (фронтенд/бэкенд/тесты/миграции)."""

    area: str
    files: int
    added: int
    deleted: int


class VibeDayVolume(BaseModel):
    """Строки за конкретный день. Дни без поставок тоже приходят — нулями."""

    day: date
    shipments: int
    added: int
    deleted: int


class VibeScale(BaseModel):
    """Опись объёма: сколько сделано. Не «сколько стоит» — это разные вопросы."""

    files: int
    new_files: int
    components: int
    migrations: int
    sections: int
    added: int
    deleted: int
    by_area: list[VibeAreaVolume]


class VibeStats(BaseModel):
    """Ответ вкладки «Вайбкодинг» для текущего пользователя."""

    display_name: str
    since: date
    until: date
    shipments_total: int
    shipments_product: int
    rhythm: VibeRhythm
    scale: VibeScale
    by_day: list[VibeDayVolume]
    by_section: list[dict]  # [{section, count}] — поставки по разделам
    shipments: list[VibeShipment]
    last_ingest: date | None = None  # когда CI последний раз обновлял данные


class VibeIngestFile(BaseModel):
    """Файл поставки. Без него не собрать «Масштаб»: из агрегата по коммиту не выводятся
    ни уникальные файлы, ни новые, ни разбивка по областям."""

    path: str
    added: int = 0
    deleted: int = 0
    is_new: bool = False


class VibeIngestCommit(BaseModel):
    """Одна строка от CI. UPSERT по sha — повторный прогон не задваивает."""

    sha: str
    author_email: str
    authored_on: date
    ctype: str
    scope: str = ""
    title: str
    added: int = 0
    deleted: int = 0
    files: int = 0
    is_product: bool = False
    files_list: list[VibeIngestFile] = []


class VibeIngestRequest(BaseModel):
    commits: list[VibeIngestCommit]


class VibeIngestResult(BaseModel):
    received: int
    inserted: int
    updated: int
    files: int = 0
