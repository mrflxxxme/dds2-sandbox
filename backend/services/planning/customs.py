"""
Planning — Customs (Topup, Alloc, DT) and FTS PDF parsing.
"""

import io
import re
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CustomsAlloc, CustomsDT, CustomsTopup

# ─── Customs Topup / Alloc ───────────────────────────────────────────────────


async def get_customs_topup(db: AsyncSession, project_id: int):
    result = await db.execute(
        select(CustomsTopup)
        .where(CustomsTopup.project_id == project_id, CustomsTopup.is_deleted == False)
        .order_by(CustomsTopup.date.desc())
    )
    topups = result.scalars().all()

    alloc_result = await db.execute(
        select(
            CustomsAlloc.topup_txn_id,
            func.sum(CustomsAlloc.alloc_amount).label("allocated"),
        )
        .where(CustomsAlloc.project_id == project_id)
        .group_by(CustomsAlloc.topup_txn_id)
    )
    alloc_map = {row.topup_txn_id: Decimal(str(row.allocated or 0)) for row in alloc_result}

    return topups, alloc_map


async def get_customs_alloc(db: AsyncSession, project_id: int, topup_txn_id: str | None = None):
    q = select(CustomsAlloc).where(CustomsAlloc.project_id == project_id)
    if topup_txn_id:
        q = q.where(CustomsAlloc.topup_txn_id == topup_txn_id)
    q = q.order_by(CustomsAlloc.pay_date)
    result = await db.execute(q)
    return result.scalars().all()


async def create_alloc(db: AsyncSession, project_id: int, data: dict):
    obj = CustomsAlloc(project_id=project_id, **data)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def delete_alloc(db: AsyncSession, project_id: int, alloc_id: int):
    result = await db.execute(
        select(CustomsAlloc).where(CustomsAlloc.id == alloc_id, CustomsAlloc.project_id == project_id)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    obj.soft_delete()
    await db.commit()
    return True


# ─── Customs DT ──────────────────────────────────────────────────────────────


async def upload_fts_and_create_dts(db: AsyncSession, project_id: int, parsed: list[dict]):
    """Create CustomsDT records from parsed FTS data, skipping duplicates."""
    created, skipped = 0, 0
    for item in parsed:
        existing = await db.execute(
            select(CustomsDT).where(
                CustomsDT.project_id == project_id,
                CustomsDT.dt_number == item["dt_number"],
                CustomsDT.is_deleted == False,
            )
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        dt = CustomsDT(
            project_id=project_id,
            dt_number=item["dt_number"],
            dt_date=date.fromisoformat(item["dt_date"]),
            amount_rub=Decimal(str(item["amount_rub"])),
        )
        db.add(dt)
        created += 1
    await db.commit()
    return created, skipped


async def get_customs_dt_list(db: AsyncSession, project_id: int):
    result = await db.execute(
        select(CustomsDT)
        .where(CustomsDT.project_id == project_id, CustomsDT.is_deleted == False)
        .order_by(CustomsDT.dt_date.desc())
    )
    return result.scalars().all()


async def update_customs_dt(db: AsyncSession, project_id: int, dt_id: int, payload: dict):
    result = await db.execute(
        select(CustomsDT).where(
            CustomsDT.id == dt_id, CustomsDT.project_id == project_id, CustomsDT.is_deleted == False
        )
    )
    dt = result.scalar_one_or_none()
    if not dt:
        return None
    if "order_no" in payload:
        dt.order_no = payload["order_no"] if payload["order_no"] else None
    if "note" in payload:
        dt.note = payload["note"]
    await db.commit()
    return True


async def delete_customs_dt(db: AsyncSession, project_id: int, dt_id: int):
    result = await db.execute(
        select(CustomsDT).where(
            CustomsDT.id == dt_id, CustomsDT.project_id == project_id, CustomsDT.is_deleted == False
        )
    )
    dt = result.scalar_one_or_none()
    if not dt:
        return None
    dt.soft_delete()
    await db.commit()
    return True


# ─── FTS PDF parsing ────────────────────────────────────────────────────────


def parse_fts_pdf(pdf_bytes: bytes) -> list[dict]:
    """Parse FTS customs report PDF and extract DT lines grouped by DT number."""
    import pdfplumber

    results = {}  # dt_number → {date, total, lines[]}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                m = re.match(
                    r"^\d+\s+"  # row number
                    r"(\d{2}\.\d{2}\.\d{4})\s+"  # operation date
                    r"([\d\s]+[,\.]\d{2})\s+"  # amount
                    r"ДТ\s+"  # doc type = ДТ
                    r"\d+\s+"  # customs code
                    r"\d{2}\.\d{2}\.\d{4}\s+"  # doc date
                    r"(\d{8}/\d{6}/\d{7})",  # DT number
                    line,
                )
                if m:
                    op_date_str = m.group(1)
                    amount_str = m.group(2).replace(" ", "").replace(",", ".")
                    dt_number = m.group(3)
                    try:
                        amount = float(amount_str)
                        op_date = date(int(op_date_str[6:10]), int(op_date_str[3:5]), int(op_date_str[0:2]))
                    except (ValueError, IndexError):
                        continue

                    if dt_number not in results:
                        results[dt_number] = {"dt_date": op_date, "total": 0.0, "lines": []}
                    results[dt_number]["total"] += amount
                    results[dt_number]["lines"].append(amount)

    return [
        {"dt_number": k, "dt_date": v["dt_date"].isoformat(), "amount_rub": round(v["total"], 2), "lines": v["lines"]}
        for k, v in sorted(results.items(), key=lambda x: x[1]["dt_date"])
    ]
