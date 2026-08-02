# ruff: noqa: RUF002 — русские комментарии и docstring
"""
DEV-утилита (не для прода): on-demand синк отзывов/вопросов WB через SOCKS5-прокси.

Зачем: на dev-машине прямой TLS-egress из docker-контейнера режется сетевым
фильтром (DPI), а httpx в контейнере собран без socksio (pip недоступен).
Скрипт тянет страницы feedbacks/questions через хостовый SOCKS5 (raw TLS-сокет)
и записывает их в зеркала wb_feedbacks/wb_questions ТЕМИ ЖЕ сервисными
функциями парсинга/upsert, что и штатный синк — проверка end-to-end данных.

Использование:
    docker compose exec -e WB_KEY_TO_REGISTER=... backend \
        python scripts/dev_wb_socks_sync.py --project-id 1000039 \
        --proxy-host host.docker.internal --proxy-port 1080 --take 100

Ключ НЕ сохраняется в файле (берётся из env), в логи не выводится.
"""

import argparse
import asyncio
import json
import socket
import ssl
import sys

_WB_HOST = "feedbacks-api.wildberries.ru"


def _wb_get(proxy_host: str, proxy_port: int, api_key: str, path: str) -> dict:
    """GET к feedbacks-api через SOCKS5 + TLS (raw HTTP/1.1, Connection: close)."""
    s = socket.create_connection((proxy_host, proxy_port), timeout=30)
    try:
        s.sendall(b"\x05\x01\x00")
        if s.recv(2) != b"\x05\x00":
            raise RuntimeError("SOCKS5: нет no-auth метода")
        h = _WB_HOST.encode()
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(h)]) + h + (443).to_bytes(2, "big"))
        reply = s.recv(10)
        if len(reply) < 2 or reply[1] != 0:
            raise RuntimeError(f"SOCKS5 connect failed: status={reply[1] if len(reply) > 1 else '?'}")
        tls = ssl.create_default_context().wrap_socket(s, server_hostname=_WB_HOST)
        req = (
            f"GET {path} HTTP/1.1\r\nHost: {_WB_HOST}\r\n"
            f"Authorization: {api_key}\r\nConnection: close\r\n\r\n"
        )
        tls.sendall(req.encode())
        buf = b""
        while True:
            chunk = tls.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    head, _, body = buf.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1])
    # WB отдаёт большие страницы chunked — собираем тело из чанков
    if b"transfer-encoding: chunked" in head.lower():
        body = _dechunk(body)
    if status != 200:
        raise RuntimeError(f"WB HTTP {status}: {body[:200]!r}")
    data = json.loads(body)
    return data.get("data") or {}


def _dechunk(raw: bytes) -> bytes:
    """Склеить тело HTTP/1.1 chunked encoding в один буфер."""
    out = b""
    pos = 0
    while True:
        eol = raw.find(b"\r\n", pos)
        if eol == -1:
            break
        size_line = raw[pos:eol].split(b";")[0].strip()
        try:
            size = int(size_line, 16)
        except ValueError:
            break
        if size == 0:
            break
        pos = eol + 2
        out += raw[pos : pos + size]
        pos += size + 2  # данные + CRLF
    return out


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--proxy-host", default="host.docker.internal")
    parser.add_argument("--proxy-port", type=int, default=1080)
    parser.add_argument("--take", type=int, default=100, help="размер страницы (по 1 странице каждого типа)")
    args = parser.parse_args()

    api_key = (os_environ := __import__("os").environ).get("WB_KEY_TO_REGISTER", "").strip()
    if not api_key:
        print("ERROR: передайте ключ через env WB_KEY_TO_REGISTER", file=sys.stderr)
        return 2

    from backend.database import AsyncSessionLocal
    from backend.services.reply_service import _row_from_question, _upsert_question_rows
    from backend.services.wb_reviews_sync import _row_from_feedback, _upsert_rows
    from backend.utils.time import utcnow

    # Синхронный raw-fetch — в отдельном потоке, чтобы не блокировать loop
    fb_false = await asyncio.to_thread(
        _wb_get, args.proxy_host, args.proxy_port, api_key,
        f"/api/v1/feedbacks?isAnswered=false&take={args.take}&skip=0",
    )
    fb_true = await asyncio.to_thread(
        _wb_get, args.proxy_host, args.proxy_port, api_key,
        f"/api/v1/feedbacks?isAnswered=true&take={args.take}&skip=0",
    )
    q_false = await asyncio.to_thread(
        _wb_get, args.proxy_host, args.proxy_port, api_key,
        f"/api/v1/questions?isAnswered=false&take={args.take}&skip=0&order=dateDesc",
    )
    q_true = await asyncio.to_thread(
        _wb_get, args.proxy_host, args.proxy_port, api_key,
        f"/api/v1/questions?isAnswered=true&take={args.take}&skip=0&order=dateDesc",
    )

    now = utcnow()
    fb_rows: dict[str, dict] = {}
    for src in (fb_false, fb_true):
        for fb in src.get("feedbacks") or []:
            row = _row_from_feedback(args.project_id, fb, now)
            if row:
                fb_rows[row["wb_id"]] = row
    q_rows: dict[str, dict] = {}
    for src in (q_false, q_true):
        for q in src.get("questions") or []:
            row = _row_from_question(args.project_id, q, now)
            if row:
                q_rows[row["wb_id"]] = row

    async with AsyncSessionLocal() as db:
        fb_up = await _upsert_rows(db, list(fb_rows.values()))
        q_up = await _upsert_question_rows(db, list(q_rows.values()))
        await db.commit()

    print(
        f"OK: feedbacks fetched={len(fb_rows)} upserted={fb_up} "
        f"(unanswered_total={fb_false.get('countUnanswered')}, archive_total={fb_true.get('countArchive')}); "
        f"questions fetched={len(q_rows)} upserted={q_up} "
        f"(unanswered_total={q_false.get('countUnanswered')}, archive_total={q_true.get('countArchive')})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
