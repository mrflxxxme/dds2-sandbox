# ruff: noqa: RUF002, RUF003
"""Google-таблица для сводки «Проблемные товары».

Вместо отдельного рендера под Sheets API загружаем УЖЕ собранный xlsx в Drive
с конвертацией в Google Sheets «на месте» (files.update по одному и тому же
file_id): ссылка постоянная, согласованный дизайн xlsx сохраняется, кода
минимум. Таблица принадлежит ПОЛЬЗОВАТЕЛЮ и должна быть расшарена на e-mail
сервисного аккаунта (редактор): у SA нет собственной квоты Диска
(storageQuotaExceeded на files.create), владеть файлами он не может —
поэтому в настройке обязателен sheet_id уже расшаренной таблицы.

Ключ сервисного аккаунта — файл по пути из env GOOGLE_SA_JSON_PATH
(в репозиторий и настройки НЕ кладём). Нет ключа/ошибка Google — сводка
работает как раньше (только xlsx-файл в Telegram), рассылку не роняем.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from jose import jwt

from backend.config import settings

logger = logging.getLogger("dds.funnel.problem_digest_gsheet")

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GSHEET_MIME = "application/vnd.google-apps.spreadsheet"

# Кэш access-токена: (token, valid_until_unix). Токен живёт час, обновляем заранее.
_token_cache: tuple[str, float] | None = None


def load_credentials() -> dict[str, Any] | None:
    """Ключ сервисного аккаунта из GOOGLE_SA_JSON_PATH; None = интеграция выключена."""
    path = getattr(settings, "GOOGLE_SA_JSON_PATH", "") or ""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        logger.warning("problem digest gsheet: не удалось прочитать ключ по GOOGLE_SA_JSON_PATH=%s", path)
        return None
    if not isinstance(data, dict) or not data.get("client_email") or not data.get("private_key"):
        logger.warning("problem digest gsheet: в ключе нет client_email/private_key — пропускаем")
        return None
    return dict(data)


def build_jwt_claims(creds: dict[str, Any], now: float) -> dict[str, Any]:
    """Claims для OAuth JWT-bearer (чистая функция — тестируется без сети)."""
    return {
        "iss": creds["client_email"],
        "scope": SCOPE,
        "aud": creds.get("token_uri", "https://oauth2.googleapis.com/token"),
        "iat": int(now),
        "exp": int(now) + 3600,
    }


async def _access_token(creds: dict[str, Any]) -> str:
    global _token_cache
    now = time.time()
    if _token_cache and _token_cache[1] > now + 60:
        return _token_cache[0]
    assertion = jwt.encode(build_jwt_claims(creds, now), creds["private_key"], algorithm="RS256")
    async with httpx.AsyncClient(timeout=30) as cli:
        resp = await cli.post(
            creds.get("token_uri", "https://oauth2.googleapis.com/token"),
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
        )
    resp.raise_for_status()
    token = str(resp.json()["access_token"])
    _token_cache = (token, now + 3300)
    return token


async def check_access(creds: dict[str, Any], sheet_id: str) -> bool:
    """Есть ли у сервисного аккаунта доступ к таблице (расшарена ли она на него)."""
    token = await _access_token(creds)
    async with httpx.AsyncClient(timeout=30) as cli:
        resp = await cli.get(
            f"{DRIVE_API}/files/{sheet_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": "id,capabilities/canEdit"},
        )
    if resp.status_code != 200:
        return False
    return bool(resp.json().get("capabilities", {}).get("canEdit"))


async def upload_xlsx(creds: dict[str, Any], sheet_id: str, xlsx: bytes) -> None:
    """Заменить содержимое Google-таблицы конвертированным xlsx (file_id сохраняется)."""
    token = await _access_token(creds)
    async with httpx.AsyncClient(timeout=120) as cli:
        resp = await cli.patch(
            f"{DRIVE_UPLOAD}/files/{sheet_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": XLSX_MIME},
            params={"uploadType": "media"},
            content=xlsx,
        )
    resp.raise_for_status()


def sheet_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"


async def update_digest_sheet(db: Any, project_id: int, cfg: dict[str, Any], kind: str, xlsx: bytes) -> str | None:
    """Обновить (при первом запуске — создать и расшарить) таблицу сводки.

    Возвращает URL таблицы или None (нет ключа / sheet_id / ошибка Google) —
    тогда сводка уходит только xlsx-файлом. sheet_id / sheet_id_weekly — id
    пользовательских таблиц, расшаренных на SA (у недельной сводки своя,
    чтобы ежедневная её не перетирала).
    """
    creds = load_credentials()
    if not creds:
        return None
    key = "sheet_id" if kind == "daily" else "sheet_id_weekly"
    sid = str(cfg.get(key) or "")
    if not sid:
        logger.info("problem digest gsheet: %s не задан — шлём только xlsx (project=%s)", key, project_id)
        return None
    try:
        await upload_xlsx(creds, sid, xlsx)
        return sheet_url(sid)
    except Exception:
        logger.exception("problem digest gsheet: обновление таблицы не удалось (project=%s, kind=%s)", project_id, kind)
        return None
