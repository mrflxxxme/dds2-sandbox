"""
One-shot backfill: разнести «Прочие удержания» (Списания за отзыв) по артикулам.

WB API возвращает строки удержаний за отзывы с пустыми товарными полями
(`nm_id=0`, `sa_name=''`, `brand_name=''`, `subject_name=''`). ID товара
зашит ТОЛЬКО внутри текста `bonus_type_name`:

    "Списание за отзыв XS6uAH...: акция №1777436, товар 742354645"

После деплоя `wb_finance_sync._upsert_batch` обогащает такие строки на лету,
но уже залитые в БД исторические данные остаются «магазинными». Этот скрипт
проходит по всей таблице, парсит nm_id из текста и проставляет
brand/subject/sa_name из самосогласованного источника (других строк
`wb_finance_rows` того же товара).

Запуск:
    docker compose exec backend python -m scripts.backfill_review_deductions
    docker compose exec backend python -m scripts.backfill_review_deductions --dry-run
    docker compose exec backend python -m scripts.backfill_review_deductions --project-id 4

Скрипт идемпотентен: повторный прогон ничего не меняет (фильтр `nm_id = 0`).
"""

import argparse
import asyncio
import logging
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_project_reports
from backend.database import AsyncSessionLocal
from backend.services.wb_finance_helpers import parse_review_target

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("backfill_review_deductions")


_SELECT_CANDIDATES_SQL = text("""
    SELECT id, project_id, bonus_type_name
    FROM wb_finance_rows
    WHERE nm_id = 0
      AND deduction != 0
      AND bonus_type_name LIKE 'Списание за отзыв%%'
    ORDER BY project_id, id
""")


_LOOKUP_SQL = text("""
    SELECT DISTINCT ON (nm_id)
        nm_id, brand_name, subject_name, sa_name
    FROM wb_finance_rows
    WHERE project_id = :project_id
      AND nm_id = ANY(:nm_ids)
      AND subject_name IS NOT NULL
      AND subject_name != ''
    ORDER BY nm_id, sale_dt DESC NULLS LAST, rr_dt DESC NULLS LAST
""")


_UPDATE_SQL = text("""
    UPDATE wb_finance_rows
    SET nm_id = :nm_id,
        brand_name = :brand_name,
        subject_name = :subject_name,
        sa_name = :sa_name
    WHERE id = :id
""")


async def _load_candidates(db: AsyncSession, project_id: int | None) -> list[tuple[int, int, int]]:
    """Вернуть список (row_id, project_id, nm_id) — все строки-отзывы с распарсенным nm_id."""
    result = await db.execute(_SELECT_CANDIDATES_SQL)
    out: list[tuple[int, int, int]] = []
    for row in result:
        if project_id is not None and row.project_id != project_id:
            continue
        nm = parse_review_target(row.bonus_type_name)
        if nm is None:
            continue
        out.append((row.id, row.project_id, nm))
    return out


async def _load_lookup(db: AsyncSession, project_id: int, nm_ids: set[int]) -> dict[int, dict]:
    if not nm_ids:
        return {}
    result = await db.execute(_LOOKUP_SQL, {"project_id": project_id, "nm_ids": list(nm_ids)})
    return {
        row.nm_id: {
            "brand_name": row.brand_name or "",
            "subject_name": row.subject_name or "",
            "sa_name": row.sa_name or "",
        }
        for row in result
    }


async def backfill(project_id: int | None = None, dry_run: bool = False) -> dict:
    """Обработать все строки-отзывы; вернуть статистику."""
    stats = {
        "projects": 0,
        "candidate_rows": 0,
        "updated_rows": 0,
        "missing_lookup": 0,
    }

    async with AsyncSessionLocal() as db:
        candidates = await _load_candidates(db, project_id)
        stats["candidate_rows"] = len(candidates)
        if not candidates:
            logger.info("Нет строк для обогащения — ничего не делаю.")
            return stats

        # Группируем по проектам
        by_project: dict[int, list[tuple[int, int]]] = {}
        for row_id, pid, nm in candidates:
            by_project.setdefault(pid, []).append((row_id, nm))

        stats["projects"] = len(by_project)
        logger.info(
            "Найдено %d строк-отзывов с пустыми товарными полями в %d проектах",
            len(candidates),
            len(by_project),
        )

        for pid, rows in by_project.items():
            unique_nm_ids = {nm for _, nm in rows}
            lookup = await _load_lookup(db, pid, unique_nm_ids)
            logger.info(
                "  project=%d: %d строк, %d уникальных nm_id, %d найдено в lookup",
                pid,
                len(rows),
                len(unique_nm_ids),
                len(lookup),
            )

            for row_id, nm in rows:
                info = lookup.get(nm)
                params = {
                    "id": row_id,
                    "nm_id": nm,
                    "brand_name": (info or {}).get("brand_name", ""),
                    "subject_name": (info or {}).get("subject_name", ""),
                    "sa_name": (info or {}).get("sa_name", ""),
                }
                if info is None:
                    stats["missing_lookup"] += 1
                if not dry_run:
                    await db.execute(_UPDATE_SQL, params)
                stats["updated_rows"] += 1

            if not dry_run:
                await db.commit()
                await invalidate_project_reports(pid)
                logger.info("  ✅ project=%d: коммит выполнен, кэш отчётов сброшен", pid)
            else:
                logger.info("  🔎 project=%d: dry-run, ничего не записано", pid)

    return stats


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill review deduction rows with nm_id/brand/subject/sa_name.")
    parser.add_argument("--project-id", type=int, default=None, help="Если указан — только этот проект.")
    parser.add_argument("--dry-run", action="store_true", help="Ничего не пишем, только считаем.")
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    logger.info(
        "Старт backfill: project_id=%s, dry_run=%s",
        args.project_id if args.project_id is not None else "ALL",
        args.dry_run,
    )
    stats = await backfill(project_id=args.project_id, dry_run=args.dry_run)
    logger.info("─" * 60)
    logger.info("Готово. Статистика:")
    logger.info("  проектов: %d", stats["projects"])
    logger.info("  кандидатов в строках: %d", stats["candidate_rows"])
    logger.info("  обновлено: %d", stats["updated_rows"])
    logger.info("  без lookup-данных (только nm_id): %d", stats["missing_lookup"])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
