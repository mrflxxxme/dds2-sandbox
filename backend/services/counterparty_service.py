"""
CounterpartyService — CRUD, upsert, stats, document management.

Architecture: business logic only (routers/counterparty.py is HTTP-only).
All queries are scoped by project_id and filter out is_deleted rows.
"""

from __future__ import annotations

import builtins
import io
import logging
import mimetypes
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_project_reports
from backend.config import settings
from backend.models.counterparty import Counterparty, CounterpartyDocument
from backend.models.loan import Loan
from backend.models.transactions import Transaction
from backend.schemas.counterparty import (
    CounterpartyCategorySummary,
    CounterpartyCreate,
    CounterpartyFilter,
    CounterpartyStats,
    CounterpartyTransactionItem,
    CounterpartyUpdate,
)
from backend.storage import get_minio
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


# ─── Errors ──────────────────────────────────────────────────────────────────


class CounterpartyConflictError(Exception):
    """Raised when a Counterparty with given (project_id, inn|contract_number) exists."""


class CounterpartyNotFoundError(Exception):
    """Raised when a Counterparty is missing in the current project."""


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _escape_ilike(value: str) -> str:
    """Escape % and _ in a user-provided ILIKE pattern (backslash-escape)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ─── Service ─────────────────────────────────────────────────────────────────


class CounterpartyService:
    """All counterparty operations. One instance per request."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ─── upsert_by_inn ──────────────────────────────────────────────────

    async def upsert_by_inn(
        self,
        *,
        inn: str,
        name: str,
        project_id: int,
        defaults: dict | None = None,
    ) -> Counterparty:
        """
        Find-or-create by (project_id, inn).

        If exists: update `name` only if the new one is strictly longer (heuristic
        for "more complete name"). Never overwrite primary_type once set manually.
        If not exists: create with defaults (default primary_type=OTHER),
        mark created_by_import=True.
        """
        defaults = defaults or {}
        existing = await self.db.execute(
            select(Counterparty).where(
                Counterparty.project_id == project_id,
                Counterparty.inn == inn,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
        row = existing.scalar_one_or_none()
        if row is not None:
            # Update name only if new is longer
            if name and len(name) > len(row.name or ""):
                row.name = name
            await self.db.flush()
            return row

        cp = Counterparty(
            project_id=project_id,
            inn=inn,
            name=name,
            primary_type=defaults.get("primary_type", "OTHER"),
            secondary_types=defaults.get("secondary_types") or [],
            kpp=defaults.get("kpp"),
            contract_number=defaults.get("contract_number"),
            notes=defaults.get("notes"),
            contacts=defaults.get("contacts"),
            created_by_import=True,
        )
        self.db.add(cp)
        await self.db.flush()
        return cp

    async def upsert_by_contract(
        self,
        *,
        contract_number: str,
        project_id: int,
        defaults: dict | None = None,
    ) -> Counterparty:
        """Find-or-create by (project_id, contract_number). For Chinese suppliers."""
        defaults = defaults or {}
        existing = await self.db.execute(
            select(Counterparty).where(
                Counterparty.project_id == project_id,
                Counterparty.contract_number == contract_number,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
        row = existing.scalar_one_or_none()
        if row is not None:
            return row

        cp = Counterparty(
            project_id=project_id,
            inn=None,
            name=defaults.get("name") or f"Contract {contract_number}",
            primary_type=defaults.get("primary_type", "SUPPLIER"),
            secondary_types=defaults.get("secondary_types") or [],
            contract_number=contract_number,
            notes=defaults.get("notes"),
            contacts=defaults.get("contacts"),
            created_by_import=True,
        )
        self.db.add(cp)
        await self.db.flush()
        return cp

    # ─── list ───────────────────────────────────────────────────────────

    async def list(
        self,
        *,
        project_id: int,
        filters: CounterpartyFilter,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[list[Counterparty], int, dict[int, dict]]:
        """List counterparties with filters + pagination.

        When date_from/date_to are provided, also computes per-CP turnover
        (sum by currency + tx count). Returns (items, total, turnover_map)
        where turnover_map[cp_id] = {"income_rub", "expense_rub", "income_cny", "expense_cny", "tx_count"}.
        """
        stmt = select(Counterparty).where(
            Counterparty.project_id == project_id,
        )
        if filters.active_only:
            stmt = stmt.where(Counterparty.is_deleted == False)  # noqa: E712
        # Always exclude soft-deleted (we treat active_only=False as "include soft" === "do not include",
        # spec says active_only excludes archived; non-active default = include active only too)
        # For safety: always hide soft-deleted unless an explicit flag is added later
        stmt = stmt.where(Counterparty.is_deleted == False)  # noqa: E712

        if filters.type:
            stmt = stmt.where(Counterparty.primary_type == filters.type)
        if filters.q:
            q = _escape_ilike(filters.q.strip())
            stmt = stmt.where(
                Counterparty.name.ilike(f"%{q}%", escape="\\") | Counterparty.inn.ilike(f"%{q}%", escape="\\")
            )

        # Count total
        total_stmt = select(func.count()).select_from(stmt.subquery())
        total_res = await self.db.execute(total_stmt)
        total = total_res.scalar_one()

        # Fetch page
        stmt = stmt.order_by(Counterparty.name).limit(filters.limit).offset(filters.offset)
        res = await self.db.execute(stmt)
        items = list(res.scalars().all())

        # Compute per-CP turnover for the fetched page if a date range is given
        turnover_map: dict[int, dict] = {}
        if (date_from is not None or date_to is not None) and items:
            cp_ids = [cp.id for cp in items]
            agg_stmt = (
                select(
                    Transaction.counterparty_id.label("cp_id"),
                    Transaction.currency.label("currency"),
                    func.coalesce(func.sum(Transaction.income), 0).label("in_sum"),
                    func.coalesce(func.sum(Transaction.expense), 0).label("out_sum"),
                    func.count(Transaction.id).label("tx_cnt"),
                )
                .where(
                    Transaction.project_id == project_id,
                    Transaction.is_deleted == False,  # noqa: E712
                    Transaction.is_internal == False,  # noqa: E712 - exclude internal transfers
                    Transaction.counterparty_id.in_(cp_ids),
                )
                .group_by(Transaction.counterparty_id, Transaction.currency)
            )
            if date_from is not None:
                agg_stmt = agg_stmt.where(Transaction.date >= date_from)
            if date_to is not None:
                agg_stmt = agg_stmt.where(Transaction.date <= date_to)
            agg_res = await self.db.execute(agg_stmt)
            for row in agg_res.all():
                bucket = turnover_map.setdefault(
                    row.cp_id,
                    {
                        "income_rub": Decimal("0"),
                        "expense_rub": Decimal("0"),
                        "income_cny": Decimal("0"),
                        "expense_cny": Decimal("0"),
                        "tx_count": 0,
                    },
                )
                income = Decimal(str(row.in_sum or 0))
                expense = Decimal(str(row.out_sum or 0))
                if (row.currency or "").upper() == "CNY":
                    bucket["income_cny"] += income
                    bucket["expense_cny"] += expense
                else:
                    bucket["income_rub"] += income
                    bucket["expense_rub"] += expense
                bucket["tx_count"] += int(row.tx_cnt or 0)

        return items, int(total or 0), turnover_map

    # ─── summary_by_type ────────────────────────────────────────────────

    async def summary_by_type(
        self,
        *,
        project_id: int,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> builtins.list[CounterpartyCategorySummary]:
        """Aggregate turnover by primary_type across all non-deleted counterparties."""
        cp_count_stmt = (
            select(
                Counterparty.primary_type.label("pt"),
                func.count(Counterparty.id).label("cps"),
            )
            .where(
                Counterparty.project_id == project_id,
                Counterparty.is_deleted == False,  # noqa: E712
            )
            .group_by(Counterparty.primary_type)
        )
        count_res = await self.db.execute(cp_count_stmt)
        count_by_type: dict[str, int] = {row.pt: int(row.cps or 0) for row in count_res.all()}

        tx_stmt = (
            select(
                Counterparty.primary_type.label("pt"),
                Transaction.currency.label("currency"),
                func.coalesce(func.sum(Transaction.income), 0).label("in_sum"),
                func.coalesce(func.sum(Transaction.expense), 0).label("out_sum"),
                func.count(Transaction.id).label("tx_cnt"),
            )
            .join(Counterparty, Counterparty.id == Transaction.counterparty_id)
            .where(
                Transaction.project_id == project_id,
                Transaction.is_deleted == False,  # noqa: E712
                Transaction.is_internal == False,  # noqa: E712 - exclude internal transfers
                Counterparty.is_deleted == False,  # noqa: E712
                Counterparty.project_id == project_id,
            )
            .group_by(Counterparty.primary_type, Transaction.currency)
        )
        if date_from is not None:
            tx_stmt = tx_stmt.where(Transaction.date >= date_from)
        if date_to is not None:
            tx_stmt = tx_stmt.where(Transaction.date <= date_to)
        tx_res = await self.db.execute(tx_stmt)

        by_type: dict[str, CounterpartyCategorySummary] = {}
        for pt, cnt in count_by_type.items():
            by_type[pt] = CounterpartyCategorySummary(primary_type=pt, count_cps=cnt)

        for row in tx_res.all():
            bucket = by_type.setdefault(
                row.pt,
                CounterpartyCategorySummary(primary_type=row.pt, count_cps=count_by_type.get(row.pt, 0)),
            )
            income = Decimal(str(row.in_sum or 0))
            expense = Decimal(str(row.out_sum or 0))
            if (row.currency or "").upper() == "CNY":
                bucket.income_cny += income
                bucket.expense_cny += expense
            else:
                bucket.income_rub += income
                bucket.expense_rub += expense
            bucket.tx_count += int(row.tx_cnt or 0)

        # Sort: highest RUB turnover first, then alpha
        result = list(by_type.values())
        result.sort(
            key=lambda s: (-(s.expense_rub + s.income_rub), s.primary_type),
        )
        return result

    # ─── stats ──────────────────────────────────────────────────────────

    async def stats(
        self,
        *,
        counterparty_id: int,
        project_id: int,
        date_from: date,
        date_to: date,
        currency: str = "RUB",
    ) -> CounterpartyStats:
        """Aggregate income/expense/count for a counterparty in a date range."""
        stmt = select(
            func.coalesce(func.sum(Transaction.income), 0).label("in_sum"),
            func.coalesce(func.sum(Transaction.expense), 0).label("out_sum"),
            func.count(Transaction.id).label("tx_count"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.counterparty_id == counterparty_id,
            Transaction.is_deleted == False,  # noqa: E712
            Transaction.is_internal == False,  # noqa: E712 - exclude internal transfers
            Transaction.currency == currency,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
        )
        res = await self.db.execute(stmt)
        row = res.one()
        in_sum = Decimal(str(row.in_sum or 0))
        out_sum = Decimal(str(row.out_sum or 0))
        return CounterpartyStats(
            in_sum=in_sum,
            out_sum=out_sum,
            net=in_sum - out_sum,
            tx_count=int(row.tx_count or 0),
        )

    # ─── list_transactions ──────────────────────────────────────────────

    async def list_transactions(
        self,
        *,
        counterparty_id: int,
        project_id: int,
        date_from: date | None = None,
        date_to: date | None = None,
        currency: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[builtins.list[CounterpartyTransactionItem], int]:
        """List bank transactions linked to a counterparty. Newest first."""
        # Verify counterparty belongs to project (prevents cross-tenant leak)
        cp_res = await self.db.execute(
            select(Counterparty.id).where(
                Counterparty.id == counterparty_id,
                Counterparty.project_id == project_id,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
        if cp_res.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Counterparty not found")

        stmt = select(
            Transaction.id,
            Transaction.date,
            Transaction.account,
            Transaction.currency,
            Transaction.income,
            Transaction.expense,
            Transaction.purpose,
            Transaction.event_type2,
            Transaction.loan_payment_type,
            Transaction.contract_number,
        ).where(
            Transaction.project_id == project_id,
            Transaction.counterparty_id == counterparty_id,
            Transaction.is_deleted == False,  # noqa: E712
            Transaction.is_internal == False,  # noqa: E712 - exclude internal transfers
        )
        if date_from is not None:
            stmt = stmt.where(Transaction.date >= date_from)
        if date_to is not None:
            stmt = stmt.where(Transaction.date <= date_to)
        if currency:
            stmt = stmt.where(Transaction.currency == currency.upper())

        total_stmt = select(func.count()).select_from(stmt.subquery())
        total_res = await self.db.execute(total_stmt)
        total = int(total_res.scalar_one() or 0)

        stmt = stmt.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(limit).offset(offset)
        res = await self.db.execute(stmt)
        items = [
            CounterpartyTransactionItem(
                id=row.id,
                date=row.date,
                account=row.account,
                currency=row.currency,
                income=Decimal(str(row.income or 0)),
                expense=Decimal(str(row.expense or 0)),
                purpose=row.purpose,
                event_type2=row.event_type2,
                loan_payment_type=row.loan_payment_type,
                contract_number=row.contract_number,
            )
            for row in res.all()
        ]
        return items, total

    # ─── get ────────────────────────────────────────────────────────────

    async def get(
        self,
        *,
        counterparty_id: int,
        project_id: int,
        date_from: date,
        date_to: date,
    ) -> dict:
        """Return the Counterparty + stats_rub + stats_cny + active_loans + docs_count."""
        res = await self.db.execute(
            select(Counterparty).where(
                Counterparty.id == counterparty_id,
                Counterparty.project_id == project_id,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
        cp = res.scalar_one_or_none()
        if cp is None:
            raise HTTPException(status_code=404, detail="Counterparty not found")

        stats_rub = await self.stats(
            counterparty_id=counterparty_id,
            project_id=project_id,
            date_from=date_from,
            date_to=date_to,
            currency="RUB",
        )
        stats_cny = await self.stats(
            counterparty_id=counterparty_id,
            project_id=project_id,
            date_from=date_from,
            date_to=date_to,
            currency="CNY",
        )

        # Active loans (short list)
        loans_res = await self.db.execute(
            select(Loan)
            .where(
                Loan.project_id == project_id,
                Loan.counterparty_id == counterparty_id,
                Loan.is_deleted == False,  # noqa: E712
                Loan.status == "ACTIVE",
            )
            .limit(100)
        )
        active_loans = [
            {
                "id": loan.id,
                "direction": loan.direction,
                "principal": float(loan.principal),
                "currency": loan.currency,
                "status": loan.status,
                "start_date": loan.start_date.isoformat() if loan.start_date else None,
                "maturity_date": loan.maturity_date.isoformat() if loan.maturity_date else None,
            }
            for loan in loans_res.scalars().all()
        ]

        # Docs count
        docs_res = await self.db.execute(
            select(func.count(CounterpartyDocument.id)).where(
                CounterpartyDocument.counterparty_id == counterparty_id,
                CounterpartyDocument.project_id == project_id,
                CounterpartyDocument.is_deleted == False,  # noqa: E712
            )
        )
        docs_count = int(docs_res.scalar_one() or 0)

        # Warehouses that use this counterparty as their legal entity
        from backend.models.warehouse import Warehouse

        wh_res = await self.db.execute(
            select(Warehouse.id, Warehouse.name, Warehouse.warehouse_type).where(
                Warehouse.project_id == project_id,
                Warehouse.counterparty_id == counterparty_id,
                Warehouse.is_deleted == False,  # noqa: E712
            )
        )
        linked_warehouses = [
            {"id": row.id, "name": row.name, "warehouse_type": row.warehouse_type} for row in wh_res.all()
        ]

        return {
            "id": cp.id,
            "inn": cp.inn,
            "name": cp.name,
            "primary_type": cp.primary_type,
            "secondary_types": cp.secondary_types or [],
            "kpp": cp.kpp,
            "contract_number": cp.contract_number,
            "notes": cp.notes,
            "contacts": cp.contacts,
            "created_by_import": cp.created_by_import,
            "created_at": cp.created_at,
            "updated_at": cp.updated_at,
            "stats_rub": stats_rub,
            "stats_cny": stats_cny,
            "active_loans": active_loans,
            "linked_warehouses": linked_warehouses,
            "linked_suppliers": [],
            "docs_count": docs_count,
        }

    # ─── create ─────────────────────────────────────────────────────────

    async def create(
        self,
        data: CounterpartyCreate,
        *,
        project_id: int,
    ) -> Counterparty:
        """Create a new counterparty. Raises CounterpartyConflictError on duplicates."""
        # Pre-check uniqueness within project
        if data.inn:
            existing = await self.db.execute(
                select(Counterparty.id).where(
                    Counterparty.project_id == project_id,
                    Counterparty.inn == data.inn,
                    Counterparty.is_deleted == False,  # noqa: E712
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise CounterpartyConflictError(f"Counterparty with INN {data.inn} already exists")

        cp = Counterparty(
            project_id=project_id,
            inn=data.inn,
            name=data.name,
            primary_type=data.primary_type,
            secondary_types=data.secondary_types or [],
            kpp=data.kpp,
            contract_number=data.contract_number,
            notes=data.notes,
            contacts=data.contacts,
            created_by_import=False,
        )
        self.db.add(cp)
        try:
            await self.db.commit()
        except IntegrityError as e:
            await self.db.rollback()
            raise CounterpartyConflictError(str(e)) from e
        await self.db.refresh(cp)
        await invalidate_project_reports(project_id)
        return cp

    # ─── update ─────────────────────────────────────────────────────────

    async def update(
        self,
        *,
        counterparty_id: int,
        data: CounterpartyUpdate,
        project_id: int,
    ) -> Counterparty:
        """PATCH update — only provided fields are changed."""
        res = await self.db.execute(
            select(Counterparty).where(
                Counterparty.id == counterparty_id,
                Counterparty.project_id == project_id,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
        cp = res.scalar_one_or_none()
        if cp is None:
            raise HTTPException(status_code=404, detail="Counterparty not found")

        payload = data.model_dump(exclude_unset=True)
        for key, value in payload.items():
            setattr(cp, key, value)
        try:
            await self.db.commit()
        except IntegrityError as e:
            await self.db.rollback()
            raise CounterpartyConflictError(str(e)) from e
        await self.db.refresh(cp)
        await invalidate_project_reports(project_id)
        return cp

    # ─── soft_delete ────────────────────────────────────────────────────

    async def soft_delete(
        self,
        counterparty_id: int,
        *,
        project_id: int,
    ) -> bool:
        """Archive a counterparty (is_deleted=True)."""
        res = await self.db.execute(
            select(Counterparty).where(
                Counterparty.id == counterparty_id,
                Counterparty.project_id == project_id,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
        cp = res.scalar_one_or_none()
        if cp is None:
            return False
        cp.soft_delete()
        await self.db.commit()
        await invalidate_project_reports(project_id)
        return True

    # ─── documents ──────────────────────────────────────────────────────

    async def upload_document(
        self,
        *,
        counterparty_id: int,
        project_id: int,
        file_data: bytes,
        filename: str,
        doc_type: str,
        mime_type: str | None = None,
        uploaded_by_user_id: int | None = None,
    ) -> CounterpartyDocument:
        """Upload a document to MinIO and create metadata row."""
        # Verify counterparty belongs to project
        res = await self.db.execute(
            select(Counterparty).where(
                Counterparty.id == counterparty_id,
                Counterparty.project_id == project_id,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
        cp = res.scalar_one_or_none()
        if cp is None:
            raise HTTPException(status_code=404, detail="Counterparty not found")

        now = utcnow()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        object_name = f"counterparties/{counterparty_id}/docs/{timestamp}_{filename}"

        content_type = mime_type or (mimetypes.guess_type(filename)[0] or "application/octet-stream")

        client = await get_minio()
        if client is None:
            raise HTTPException(status_code=503, detail="File storage (MinIO) unavailable")

        await client.put_object(
            settings.MINIO_BUCKET,
            object_name,
            io.BytesIO(file_data),
            length=len(file_data),
            content_type=content_type,
        )
        logger.info(
            "Uploaded counterparty doc to MinIO: %s (%d bytes)",
            object_name,
            len(file_data),
        )

        doc = CounterpartyDocument(
            project_id=project_id,
            counterparty_id=counterparty_id,
            minio_path=object_name,
            doc_type=doc_type,
            original_filename=filename,
            file_size=len(file_data),
            mime_type=content_type,
            uploaded_by_user_id=uploaded_by_user_id,
            uploaded_at=now,
        )
        self.db.add(doc)
        await self.db.commit()
        await self.db.refresh(doc)
        return doc

    async def list_documents(
        self,
        *,
        counterparty_id: int,
        project_id: int,
    ) -> Sequence[CounterpartyDocument]:
        """List all non-deleted documents for a counterparty."""
        res = await self.db.execute(
            select(CounterpartyDocument)
            .where(
                CounterpartyDocument.counterparty_id == counterparty_id,
                CounterpartyDocument.project_id == project_id,
                CounterpartyDocument.is_deleted == False,  # noqa: E712
            )
            .order_by(CounterpartyDocument.uploaded_at.desc())
            .limit(500)
        )
        return list(res.scalars().all())

    async def delete_document(
        self,
        *,
        doc_id: int,
        counterparty_id: int,
        project_id: int,
    ) -> bool:
        """Soft-delete a document; best-effort remove from MinIO."""
        res = await self.db.execute(
            select(CounterpartyDocument).where(
                CounterpartyDocument.id == doc_id,
                CounterpartyDocument.counterparty_id == counterparty_id,
                CounterpartyDocument.project_id == project_id,
                CounterpartyDocument.is_deleted == False,  # noqa: E712
            )
        )
        doc = res.scalar_one_or_none()
        if doc is None:
            return False
        doc.soft_delete()
        await self.db.commit()

        # Best-effort remove MinIO object
        try:
            client = await get_minio()
            if client is not None:
                await client.remove_object(settings.MINIO_BUCKET, doc.minio_path)
        except Exception as e:
            logger.warning("MinIO remove_object failed: %s", e)

        return True
