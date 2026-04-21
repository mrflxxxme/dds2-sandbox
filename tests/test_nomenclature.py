"""
Tests for cost/nomenclature — get and upload nomenclature.
Uses DB fixtures from conftest.py.
"""

import io

import pandas as pd
import pytest

from backend.models import Nomenclature
from backend.services.cost.nomenclature import (
    get_nomenclature,
    get_nomenclature_subjects,
    upload_nomenclature,
)

# ═══════════════════════════════════════════════════════════════════════════════
# get_nomenclature
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetNomenclature:
    @pytest.mark.asyncio
    async def test_empty_for_new_project(self, db_session, project):
        """New project has no nomenclature."""
        result = await get_nomenclature(db_session, project.id)
        assert result == []

    @pytest.mark.asyncio
    async def test_respects_limit(self, db_session, project):
        """Limit parameter works."""
        result = await get_nomenclature(db_session, project.id, limit=5)
        assert len(result) <= 5

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Nomenclature is isolated by project."""
        result_a = await get_nomenclature(db_session, project.id)
        result_b = await get_nomenclature(db_session, other_project.id)
        assert result_a == []
        assert result_b == []


# ═══════════════════════════════════════════════════════════════════════════════
# get_nomenclature_subjects
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetNomenclatureSubjects:
    @pytest.mark.asyncio
    async def test_empty_for_new_project(self, db_session, project):
        result = await get_nomenclature_subjects(db_session, project.id)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_distinct_sorted(self, db_session, project):
        """Duplicates collapsed, result sorted; nulls and empty strings excluded."""
        for bc, subj in [
            ("9000000000001", "Шторы интерьерные"),
            ("9000000000002", "Шторы интерьерные"),
            ("9000000000003", "Ковры"),
            ("9000000000004", "Вазы"),
            ("9000000000005", None),
            ("9000000000006", ""),
        ]:
            db_session.add(Nomenclature(project_id=project.id, barcode=bc, subject=subj))
        await db_session.commit()

        result = await get_nomenclature_subjects(db_session, project.id)
        assert result == ["Вазы", "Ковры", "Шторы интерьерные"]

    @pytest.mark.asyncio
    async def test_not_limited_by_row_count(self, db_session, project):
        """Subject at alphabet tail still returned even if >1000 rows precede it."""
        for i in range(1001):
            db_session.add(Nomenclature(project_id=project.id, barcode=f"8{i:012d}", subject="Ковры"))
        db_session.add(Nomenclature(project_id=project.id, barcode="8999999999999", subject="Шторы интерьерные"))
        await db_session.commit()

        result = await get_nomenclature_subjects(db_session, project.id)
        assert "Шторы интерьерные" in result

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        db_session.add(Nomenclature(project_id=project.id, barcode="7000000000001", subject="Только_A"))
        db_session.add(Nomenclature(project_id=other_project.id, barcode="7000000000002", subject="Только_B"))
        await db_session.commit()

        result_a = await get_nomenclature_subjects(db_session, project.id)
        result_b = await get_nomenclature_subjects(db_session, other_project.id)
        assert "Только_A" in result_a and "Только_B" not in result_a
        assert "Только_B" in result_b and "Только_A" not in result_b


# ═══════════════════════════════════════════════════════════════════════════════
# upload_nomenclature
# ═══════════════════════════════════════════════════════════════════════════════


class TestUploadNomenclature:
    def _make_excel(self, rows: list[dict]) -> bytes:
        """Create an Excel file from a list of dicts."""
        df = pd.DataFrame(rows)
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        return buf.getvalue()

    @pytest.mark.asyncio
    async def test_insert_new(self, db_session, project):
        """Upload new nomenclature items."""
        data = self._make_excel(
            [
                {
                    "Баркод": "2000000000001",
                    "Бренд": "TestBrand",
                    "Предмет": "Футболки",
                    "Артикул продавца": "ART-001",
                    "Артикул WB": 12345,
                    "Объем, л": 0.5,
                },
                {
                    "Баркод": "2000000000002",
                    "Бренд": "TestBrand",
                    "Предмет": "Штаны",
                    "Артикул продавца": "ART-002",
                    "Артикул WB": 12346,
                    "Объем, л": 1.0,
                },
            ]
        )
        inserted, updated = await upload_nomenclature(db_session, project.id, data)
        assert inserted == 2
        assert updated == 0

        # Verify data is in DB
        items = await get_nomenclature(db_session, project.id)
        barcodes = {n.barcode for n in items}
        assert "2000000000001" in barcodes
        assert "2000000000002" in barcodes

    @pytest.mark.asyncio
    async def test_update_existing(self, db_session, project):
        """Re-uploading updates existing barcodes."""
        data1 = self._make_excel(
            [
                {
                    "Баркод": "3000000000001",
                    "Бренд": "Old",
                    "Предмет": "X",
                    "Артикул продавца": "A1",
                    "Артикул WB": 1,
                    "Объем, л": 0.1,
                },
            ]
        )
        await upload_nomenclature(db_session, project.id, data1)

        data2 = self._make_excel(
            [
                {
                    "Баркод": "3000000000001",
                    "Бренд": "New",
                    "Предмет": "Y",
                    "Артикул продавца": "A2",
                    "Артикул WB": 2,
                    "Объем, л": 0.2,
                },
            ]
        )
        inserted, updated = await upload_nomenclature(db_session, project.id, data2)
        assert inserted == 0
        assert updated == 1

    @pytest.mark.asyncio
    async def test_skips_empty_barcode(self, db_session, project):
        """Rows with empty or nan barcode are skipped."""
        data = self._make_excel(
            [
                {"Баркод": "", "Бренд": "B", "Предмет": "S", "Артикул продавца": "", "Артикул WB": None, "Объем, л": 0},
            ]
        )
        inserted, updated = await upload_nomenclature(db_session, project.id, data)
        assert inserted == 0
        assert updated == 0

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Upload to project A does not affect project B."""
        data = self._make_excel(
            [
                {
                    "Баркод": "4000000000001",
                    "Бренд": "Isolated",
                    "Предмет": "Test",
                    "Артикул продавца": "X",
                    "Артикул WB": 99,
                    "Объем, л": 0.5,
                },
            ]
        )
        await upload_nomenclature(db_session, project.id, data)

        items_a = await get_nomenclature(db_session, project.id)
        items_b = await get_nomenclature(db_session, other_project.id)
        assert len(items_a) >= 1
        assert all(n.barcode != "4000000000001" for n in items_b)
