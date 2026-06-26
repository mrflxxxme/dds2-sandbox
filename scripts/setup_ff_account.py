# ruff: noqa: RUF002, RUF003, T201
"""
Idempotent setup of an external fulfillment-operator account (Хамза) for the FF-портал.

Creates/verifies:
  - User(username, is_external=True) with a password (NEVER hardcoded — env or prompt);
  - ProjectMember(role='fulfillment') in EACH given project (existing projects only — no auto-create);
  - a FULFILLMENT warehouse (default name «Хамза») in each project (created if missing);
  - ProjectSetting `ff_warehouses_{user_id}` = [warehouse_id] mapping the operator to his warehouse(s).

Password source (in order): --password-stdin | env FF_PASSWORD | interactive getpass.

Usage:
  docker compose exec backend python scripts/setup_ff_account.py \
      --username хамза --projects default,вяткин --dry-run
  FF_PASSWORD=*** docker compose exec -T backend python scripts/setup_ff_account.py \
      --username хамза --projects default,вяткин --commit
"""

import argparse
import asyncio
import getpass
import json
import os
import sys

from sqlalchemy import func, select

from backend.auth import hash_password
from backend.database import get_db
from backend.ff_context import ff_warehouses_key
from backend.models.auth import Project, ProjectMember, User
from backend.models.refs import ProjectSetting
from backend.models.warehouse import Warehouse, WarehouseType

FF_ROLE = "fulfillment"


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        pw = sys.stdin.readline().strip()
    else:
        pw = os.environ.get("FF_PASSWORD") or getpass.getpass("Password for FF account: ")
    if len(pw) < 8:
        print("ERROR: password must be at least 8 characters", file=sys.stderr)
        sys.exit(2)
    return pw


async def _upsert_user(db, username: str, password: str) -> User:
    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if user is None:
        user = User(username=username, password_hash=hash_password(password), role="member", is_external=True)
        db.add(user)
        await db.flush()
        print(f"  + created user {username!r} (id={user.id}, is_external=True)")
    else:
        user.is_external = True
        user.password_hash = hash_password(password)
        print(f"  ~ user {username!r} exists (id={user.id}) → set is_external=True, password updated")
    return user


async def _ensure_warehouse(db, project_id: int, name: str) -> Warehouse:
    wh = (
        await db.execute(
            select(Warehouse).where(
                Warehouse.project_id == project_id,
                func.lower(Warehouse.name) == name.lower(),
                Warehouse.warehouse_type == WarehouseType.FULFILLMENT.value,
                Warehouse.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if wh is None:
        wh = Warehouse(project_id=project_id, name=name, warehouse_type=WarehouseType.FULFILLMENT.value)
        db.add(wh)
        await db.flush()
        print(f"    + created FULFILLMENT warehouse {name!r} (id={wh.id})")
    else:
        print(f"    ~ warehouse {name!r} exists (id={wh.id})")
    return wh


async def _ensure_member(db, project_id: int, user_id: int) -> None:
    # ProjectMember has SoftDeleteMixin + unique (project_id, user_id): search INCLUDING
    # soft-deleted and restore, never blind INSERT (see learnings: SoftDelete + Unique).
    member = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        db.add(ProjectMember(project_id=project_id, user_id=user_id, role=FF_ROLE))
        print("    + project member (role=fulfillment)")
    else:
        if member.is_deleted:
            member.restore()
        member.role = FF_ROLE
        print("    ~ project member exists → role=fulfillment")


async def _set_ff_warehouses(db, project_id: int, user_id: int, warehouse_ids: list[int]) -> None:
    key = ff_warehouses_key(user_id)
    value = json.dumps(sorted(set(warehouse_ids)))
    row = (
        await db.execute(
            select(ProjectSetting).where(ProjectSetting.project_id == project_id, ProjectSetting.key == key)
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(ProjectSetting(project_id=project_id, key=key, value=value))
    else:
        row.value = value
    print(f"    = ff_warehouses mapping: {key} = {value}")


async def main(username: str, slugs: list[str], wh_name: str, password: str, commit: bool) -> int:
    async for db in get_db():
        print(f"User: {username!r}")
        user = await _upsert_user(db, username, password)

        for slug in slugs:
            proj = (
                await db.execute(
                    select(Project).where(Project.slug == slug, Project.is_deleted == False)  # noqa: E712
                )
            ).scalar_one_or_none()
            if proj is None:
                print(f"ERROR: project slug {slug!r} not found (no auto-create)", file=sys.stderr)
                await db.rollback()
                return 1
            print(f"Project {slug!r} (id={proj.id}):")
            wh = await _ensure_warehouse(db, proj.id, wh_name)
            await _ensure_member(db, proj.id, user.id)
            await _set_ff_warehouses(db, proj.id, user.id, [wh.id])

        if commit:
            await db.commit()
            print("\nCOMMITTED.")
        else:
            await db.rollback()
            print("\nDRY-RUN — rolled back. Re-run with --commit to apply.")
        return 0
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Setup external FF operator account (Хамза)")
    ap.add_argument("--username", required=True)
    ap.add_argument("--projects", default="default,вяткин", help="comma-separated project slugs")
    ap.add_argument("--warehouse-name", default="Хамза")
    ap.add_argument("--password-stdin", action="store_true", help="read password from stdin (else FF_PASSWORD env / prompt)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pw = _read_password(args.password_stdin)
    slug_list = [s.strip() for s in args.projects.split(",") if s.strip()]
    rc = asyncio.run(main(args.username, slug_list, args.warehouse_name, pw, commit=args.commit))
    sys.exit(rc)
