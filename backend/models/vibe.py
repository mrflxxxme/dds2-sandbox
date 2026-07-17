"""Vibecoding models: VibeAuthor, VibeCommit.

Внутренняя телеметрия репозитория: что каждый вайбкодер выкатил на прод.

ОСОЗНАННОЕ ИСКЛЮЧЕНИЕ из Iron rule №1 (каждый запрос фильтрует project_id):
у этих данных НЕТ project_id и быть не может. Это статистика по коду самого DDS2,
а не по данным арендатора — коммит не принадлежит проекту клиента. Доступ режется не
по проекту, а по наличию строки в vibe_authors (см. ниже).
"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.time import utcnow


class VibeAuthor(Base):
    """Связка «пользователь DDS2 → git-автор». Одновременно СПИСОК ДОСТУПА к вкладке.

    Почему не роль: role живёт в ProjectMember и она ПРОЕКТНАЯ — клиент-селлер является
    `owner` своего проекта и прошёл бы любую проверку по роли. Нет строки здесь — нет
    вкладки и 403 на API. Один пользователь может иметь несколько git-почт (разные машины).
    """

    __tablename__ = "vibe_authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    # git author email — ключ склейки с vibe_commits.author_email (lower-case).
    git_email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VibeCommit(Base):
    """Одна поставка на прод = один коммит, достижимый из ветки прода.

    Хранится по коммиту, а не агрегатом: разрезы (по дням, по разделам, по типам) потом
    считаются на лету, и новый разрез не требует миграции.
    """

    __tablename__ = "vibe_commits"

    # Полный sha — естественный ключ. UPSERT по нему делает ингест идемпотентным:
    # повторный прогон CI не задваивает статистику.
    sha: Mapped[str] = mapped_column(String(40), primary_key=True)
    author_email: Mapped[str] = mapped_column(String(200), nullable=False)
    # Дата АВТОРА, не коммитера: ребейз на ушедший dev перебивает коммитерскую днём
    # пуша, и работа, сделанная во вторник, уезжала бы в четверг.
    authored_on: Mapped[date] = mapped_column(Date, nullable=False)
    ctype: Mapped[str] = mapped_column(String(20), nullable=False)  # feat / fix / perf...
    scope: Mapped[str] = mapped_column(String(50), nullable=False, server_default="")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    added: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    deleted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    files: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Продуктовая = её видит пользователь DDS2 (feat/fix/perf вне инфра-скоупов).
    is_product: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        # Главный запрос вкладки: «мои поставки за период» — автор + диапазон дат.
        Index("ix_vibe_commits_author_email_authored_on", "author_email", "authored_on"),
    )


class VibeFile(Base):
    """Файл, тронутый поставкой. Нужен для «Масштаба»: агрегата по коммиту не хватает.

    Из `vibe_commits.files` не вывести ни числа УНИКАЛЬНЫХ файлов (один файл правят в
    трёх поставках — это 1 файл, но 3 в сумме счётчиков), ни новых файлов, ни разбивки
    по областям (фронтенд/бэкенд/тесты/миграции). Поэтому строка на файл.
    """

    __tablename__ = "vibe_files"

    sha: Mapped[str] = mapped_column(
        String(40), ForeignKey("vibe_commits.sha", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str] = mapped_column(String(500), primary_key=True)
    added: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    deleted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Создан с нуля этой поставкой (git --diff-filter=A). Из numstat не выводится:
    # у нового файла удалений 0, но и у чисто дополненного тоже.
    is_new: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
