#!/usr/bin/env python3
"""Связка «пользователь DDS2 → git-почта». Она же — список доступа к вкладке.

Почты в DDS2 и в git НЕ совпадают (проверено: у пользователей icloud/mail.ru, а коммиты
с gmail и локальных хостов), автоматически связать нельзя — только руками. У одного
человека почт бывает несколько: разные машины дают разный git-конфиг.

    docker compose exec -T backend python3 -m scripts.vibe_author list
    docker compose exec -T backend python3 -m scripts.vibe_author add --user OOOPLUSVAIB \\
        --email denlyublyukatyu@gmail.com --name "Денис"
    docker compose exec -T backend python3 -m scripts.vibe_author rm --email old@mail

Нет строки здесь — нет вкладки и 403 на API. Это и есть гейт: проектная роль не годится,
клиент-селлер является `owner` своего проекта и прошёл бы любую проверку по роли.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import delete, select

from backend.database import AsyncSessionLocal
from backend.models.auth import User
from backend.models.vibe import VibeAuthor


async def cmd_list() -> int:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(VibeAuthor, User).join(User, User.id == VibeAuthor.user_id)
                .order_by(VibeAuthor.user_id, VibeAuthor.git_email)
            )
        ).all()
    if not rows:
        print("vibe_authors пуст — вкладка «Вайбкодинг» не видна никому")
        return 0
    for va, user in rows:
        print(f"  {user.username:20} {va.git_email:45} {va.display_name or ''}")
    return 0


async def cmd_add(username: str, email: str, name: str | None) -> int:
    # Нижний регистр обязателен: генератор отдаёт почты в lower, иначе
    # denisdmitriev@MacBook-Air-7.local не склеится со своими коммитами.
    email = email.strip().lower()
    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if not user:
            print(f"пользователь «{username}» не найден", file=sys.stderr)
            return 1
        exists = (
            await db.execute(select(VibeAuthor).where(VibeAuthor.git_email == email))
        ).scalar_one_or_none()
        if exists:
            print(f"почта {email} уже привязана (user_id={exists.user_id})", file=sys.stderr)
            return 1
        db.add(VibeAuthor(user_id=user.id, git_email=email, display_name=name))
        await db.commit()
    print(f"привязано: {username} → {email}")
    return 0


async def cmd_rm(email: str) -> int:
    email = email.strip().lower()
    async with AsyncSessionLocal() as db:
        res = await db.execute(delete(VibeAuthor).where(VibeAuthor.git_email == email))
        await db.commit()
    print(f"удалено строк: {res.rowcount}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Связка пользователь DDS2 → git-почта")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="кто уже привязан")
    a = sub.add_parser("add", help="привязать git-почту к пользователю")
    a.add_argument("--user", required=True, help="username в DDS2")
    a.add_argument("--email", required=True, help="git author email")
    a.add_argument("--name", help="как показывать на вкладке")
    r = sub.add_parser("rm", help="отвязать git-почту")
    r.add_argument("--email", required=True)
    args = p.parse_args()

    if args.cmd == "list":
        return asyncio.run(cmd_list())
    if args.cmd == "add":
        return asyncio.run(cmd_add(args.user, args.email, args.name))
    return asyncio.run(cmd_rm(args.email))


if __name__ == "__main__":
    sys.exit(main())
