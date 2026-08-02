# ruff: noqa: RUF001, RUF002, RUF003
"""
Backfill: привязка FBS-задания к поставке (``wb_fbs_orders.supply_id``) из raw.

WB кладёт ``supplyId`` прямо в payload задания, и синк его сохраняет — но до
фикса 02.08.2026 upsert намеренно не трогал колонку ``supply_id`` на конфликте
(«владелец — supplies_service»), а единственный, кто её писал
(``supplies_service._pull_missing_order_ids``), берёт активную поставку в
кандидаты только при ``orders_count == 0``. Как только к поставке привязывалось
ПЕРВОЕ задание, она выпадала из досинка навсегда, и всё, что доложили в кабинете
после, оставалось с ``supply_id = NULL``.

Симптом на проде: поставка ``WB-GI-260717413`` (склад ЕКБ) показывала 5 заданий
против 203 в кабинете WB, а зеркало сборки (``assembly_mirror``) собрало бы по
ней заявку на 5 единиц — и заморозило состав навсегда при переходе в SHIPPED.

Код починен в ``orders_service._upsert_orders`` (``coalesce(excluded, existing)``:
заполняем, но не затираем), однако он лечит только те задания, которые синк ещё
перечитывает. Задания вне окна ``sync_orders_recent`` не перечитаются никогда —
их и добирает этот скрипт, из УЖЕ сохранённого raw, без единого запроса к WB.

Идемпотентен: пишет только там, где колонка NULL, а в raw есть непустой
``supplyId`` и поставка существует в нашем зеркале того же проекта. Повторный
прогон находит 0 строк. После записи пересчитывает ``orders_count`` поставок
(тот же ``_recount_orders``, что зовёт синк).

Usage:
    python -m scripts.backfill_fbs_order_supply_link --project-id=4            # dry-run
    python -m scripts.backfill_fbs_order_supply_link --project-id=4 --commit
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import func, select, update

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.database import AsyncSessionLocal  # noqa: E402
from backend.models.wb_fbs import WbFbsOrder, WbFbsSupply  # noqa: E402
from backend.services.wb_fbs.contour import contour_condition  # noqa: E402
from backend.services.wb_fbs.supplies_service import _recount_orders  # noqa: E402
from backend.utils.time import utcnow  # noqa: E402

logger = logging.getLogger("backfill_fbs_order_supply_link")


def _raw_supply_id():
    """``raw->>'supplyId'`` с пустой строкой как отсутствием значения.

    У части заданий WB отдаёт ``supplyId: ""`` (в поставку ещё не положены) —
    это NULL, а не привязка к поставке с пустым идентификатором.
    """
    return func.nullif(WbFbsOrder.raw["supplyId"].astext, "")


def _targets(project_id: int):
    """Задания без привязки, у которых поставка известна и лежит в нашем зеркале.

    Требование «поставка есть в зеркале» — тот же инвариант, что даёт синк:
    он проставляет привязку, идя ОТ поставок. Ссылка на незеркалированную
    поставку сломала бы LEFT JOIN-читателей (`list_orders`, аналитика этапов).
    """
    supply_exists = (
        select(WbFbsSupply.id)
        .where(
            WbFbsSupply.project_id == project_id,
            WbFbsSupply.wb_supply_id == _raw_supply_id(),
        )
        .correlate(WbFbsOrder)
        .exists()
    )
    return (
        WbFbsOrder.project_id == project_id,
        WbFbsOrder.supply_id.is_(None),
        _raw_supply_id().is_not(None),
        contour_condition(WbFbsOrder.raw),
        supply_exists,
    )


async def run(project_id: int, commit: bool) -> int:
    async with AsyncSessionLocal() as db:
        where = _targets(project_id)

        total = await db.scalar(select(func.count()).select_from(WbFbsOrder).where(*where))
        total = int(total or 0)
        logger.info("project=%s заданий без привязки, но с supplyId в raw: %s", project_id, total)

        by_supply = (
            await db.execute(
                select(_raw_supply_id().label("supply"), func.count())
                .where(*where)
                .group_by(_raw_supply_id())
                .order_by(func.count().desc())
                .limit(20)
            )
        ).all()
        for supply, cnt in by_supply:
            logger.info("  %s → +%s заданий", supply, cnt)

        if not commit:
            logger.info("DRY-RUN: ничего не записано. Повтори с --commit")
            return total

        result = await db.execute(
            update(WbFbsOrder).where(*where).values(supply_id=_raw_supply_id(), updated_at=utcnow())
        )
        written = int(result.rowcount or 0)
        # Счётчик поставки производный от привязок — пересчитываем ровно тем же
        # запросом, что синк, иначе «заданий 0» держалось бы до его прогона.
        await _recount_orders(db, project_id)
        await db.commit()
        logger.info("project=%s привязано заданий: %s", project_id, written)
        return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--commit", action="store_true", help="без него — dry-run")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(run(args.project_id, args.commit))


if __name__ == "__main__":
    main()
