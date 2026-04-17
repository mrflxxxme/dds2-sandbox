"""
One-shot cleanup: remove legacy defect movements (pre-wh03) and related transfers.

Before wh03 migration, defect operations created only stock_movements without a parent
InboundReceipt/OutboundShipment document. This script wipes that legacy data cleanly.

What it does (in one transaction):
  1. Back up affected stock_movements and stock_transfers to *_backup_<date> tables
  2. Roll back defect_quantity with GREATEST(0, ...) clamp to avoid negatives
  3. Reset defect_in_transit = 0 on affected rows
  4. Delete stock_movements with reference_type='DEFECT' or DEFECT_TRANSFER_*
  5. Soft-delete stock_transfers with is_defect=true

Safety:
  - All inside a single transaction — rollback on ANY error
  - Creates backup tables first; you can restore with INSERT from backup
  - Idempotent: if no legacy rows exist, prints stats and exits

Usage:
  docker compose exec backend python scripts/cleanup_legacy_defect.py --dry-run
  docker compose exec backend python scripts/cleanup_legacy_defect.py --commit
"""

import argparse
import asyncio
import sys
from datetime import date

from sqlalchemy import text

from backend.database import get_db


async def main(commit: bool) -> int:
    today = date.today().strftime("%Y%m%d")
    backup_movements = f"stock_movements_defect_legacy_backup_{today}"
    backup_transfers = f"stock_transfers_defect_legacy_backup_{today}"

    async for db in get_db():
        # Preview counts
        row = (
            await db.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM stock_movements
                         WHERE reference_type='DEFECT'
                            OR movement_type IN ('DEFECT_TRANSFER_OUT','DEFECT_TRANSFER_IN')) AS legacy_movements,
                      (SELECT COUNT(*) FROM stock_transfers
                         WHERE is_defect=true AND is_deleted=false) AS defect_transfers
                    """
                )
            )
        ).first()
        legacy_moves = row.legacy_movements
        defect_transfers = row.defect_transfers

        print(f"Legacy defect movements to delete: {legacy_moves}")
        print(f"Defect transfers to soft-delete:   {defect_transfers}")

        if legacy_moves == 0 and defect_transfers == 0:
            print("Nothing to clean up — exiting.")
            return 0

        if not commit:
            print("\n[DRY RUN] — pass --commit to actually execute.")
            return 0

        try:
            await db.execute(
                text(f"""
                CREATE TABLE {backup_movements} AS
                SELECT * FROM stock_movements
                WHERE reference_type='DEFECT'
                   OR movement_type IN ('DEFECT_TRANSFER_OUT','DEFECT_TRANSFER_IN')
            """)
            )
            print(f"✓ Backup created: {backup_movements}")

            await db.execute(
                text(f"""
                CREATE TABLE {backup_transfers} AS
                SELECT * FROM stock_transfers WHERE is_defect=true
            """)
            )
            print(f"✓ Backup created: {backup_transfers}")

            await db.execute(
                text("""
                UPDATE warehouse_stock ws
                SET defect_quantity = GREATEST(0, defect_quantity - agg.total_delta),
                    defect_in_transit = 0,
                    updated_at = NOW()
                FROM (
                  SELECT warehouse_id, nomenclature_id, SUM(defect_delta) AS total_delta
                  FROM stock_movements
                  WHERE reference_type='DEFECT'
                     OR movement_type IN ('DEFECT_TRANSFER_OUT','DEFECT_TRANSFER_IN')
                  GROUP BY 1, 2
                ) agg
                WHERE ws.warehouse_id=agg.warehouse_id
                  AND ws.nomenclature_id=agg.nomenclature_id
            """)
            )
            print("✓ defect_quantity rolled back (clamped at 0)")

            await db.execute(
                text("""
                DELETE FROM stock_movements
                WHERE reference_type='DEFECT'
                   OR movement_type IN ('DEFECT_TRANSFER_OUT','DEFECT_TRANSFER_IN')
            """)
            )
            print("✓ legacy stock_movements deleted")

            await db.execute(
                text("""
                UPDATE stock_transfers
                SET is_deleted=true, deleted_at=NOW()
                WHERE is_defect=true AND is_deleted=false
            """)
            )
            print("✓ defect stock_transfers soft-deleted")

            stats = (
                await db.execute(
                    text(
                        """
                        SELECT
                          (SELECT COUNT(*) FROM warehouse_stock WHERE defect_quantity > 0) AS positions,
                          (SELECT COALESCE(SUM(defect_quantity),0) FROM warehouse_stock WHERE defect_quantity > 0) AS total
                        """
                    )
                )
            ).first()
            print(f"\nAfter cleanup: {stats.positions} positions, {stats.total} total defect pcs")

            await db.commit()
            print("✓ COMMITTED")
            return 0

        except Exception as e:
            await db.rollback()
            print(f"✗ ROLLED BACK: {e}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="Actually execute (default: dry run)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only (default)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(commit=args.commit)))
