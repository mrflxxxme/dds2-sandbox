"""
Tests for wb_finance_helpers.parse_review_target — extract nm_id from
"Списание за отзыв ...: акция №N, товар N" bonus_type_name strings.

Plus integration test for wb_finance_sync._load_nm_meta + _upsert_batch
which together discover (brand, subject, sa_name) from existing rows
and write them back into the deduction rows on upsert.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from backend.models.auth import Project, User
from backend.models.wb_finance import WbFinanceRow
from backend.services.wb_finance_helpers import (
    enrich_review_rows,
    parse_review_target,
)
from backend.services.wb_finance_sync import (
    _load_nm_meta,
    _row_to_values,
    _upsert_batch,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Tests: parse_review_target
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseReviewTarget:
    """parse_review_target извлекает nm_id товара из bonus_type_name."""

    def test_canonical_format(self):
        """Каноничный формат WB: 'Списание за отзыв XXX: акция №N, товар N'."""
        s = "Списание за отзыв XS6uAHfGmFvCUP66YdR9: акция №1777436, товар 742354645"
        assert parse_review_target(s) == 742354645

    def test_short_review_id(self):
        """Короткий ID отзыва."""
        s = "Списание за отзыв abc: акция №1, товар 99"
        assert parse_review_target(s) == 99

    def test_large_nm_id(self):
        """Большой nm_id (12 цифр) — должен парситься как int."""
        s = "Списание за отзыв XXX: акция №1, товар 999999999999"
        assert parse_review_target(s) == 999999999999

    def test_only_review_strings(self):
        """Регэксп НЕ должен матчить рекламу/кредиты/прочее."""
        assert parse_review_target("Оказание услуг «WB Продвижение», документ №290723728") is None
        assert parse_review_target("Оказание услуг «WB Медиа», документ №290676607") is None
        assert parse_review_target("Удержание по кредиту") is None
        assert parse_review_target("Предоставление услуг по подписке «Джем», документ №291656790") is None

    def test_review_without_tovar(self):
        """Если 'товар N' отсутствует — возвращаем None."""
        s = "Списание за отзыв abc: акция №1"
        assert parse_review_target(s) is None

    def test_none_input(self):
        """None → None (не падать)."""
        assert parse_review_target(None) is None

    def test_empty_string(self):
        """Пустая строка → None."""
        assert parse_review_target("") is None

    def test_whitespace_only(self):
        """Только пробелы → None."""
        assert parse_review_target("   ") is None

    def test_review_lowercase_prefix(self):
        """'списание за отзыв' (lowercase) — не матчит, мы строго проверяем заглавную 'С'."""
        s = "списание за отзыв XXX: акция №1, товар 123"
        # Префикс начинается с заглавной — это формат WB. Lowercase не наш кейс.
        assert parse_review_target(s) is None

    def test_review_inside_other_text(self):
        """'товар N' в произвольном тексте без префикса 'Списание за отзыв' — None."""
        s = "Какой-то рандомный текст про товар 12345"
        assert parse_review_target(s) is None

    def test_zero_nm_id(self):
        """товар 0 → None (защита от мусора)."""
        s = "Списание за отзыв XXX: акция №1, товар 0"
        assert parse_review_target(s) is None

    def test_negative_or_garbage(self):
        """Не-числовой 'товар' → None."""
        assert parse_review_target("Списание за отзыв XXX: акция №1, товар abc") is None
        assert parse_review_target("Списание за отзыв XXX: акция №1, товар -5") is None


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: enrich_review_rows (bulk обогащение)
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnrichReviewRows:
    """enrich_review_rows обогащает batch строк перед upsert."""

    def _row(self, **overrides) -> dict:
        """Базовая строка — пустые товарные поля, deduction != 0."""
        base = {
            "nm_id": 0,
            "sa_name": "",
            "brand_name": "",
            "subject_name": "",
            "bonus_type_name": None,
            "deduction": 100,
        }
        base.update(overrides)
        return base

    def test_enriches_review_row_with_lookup(self):
        """Строка-отзыв с известным nm_id — обогащается из lookup."""
        rows = [
            self._row(
                bonus_type_name="Списание за отзыв XXX: акция №1, товар 742354645",
                deduction=7320,
            )
        ]
        lookup = {
            742354645: {
                "brand_name": "Уютопия",
                "subject_name": "Чехлы для мебели",
                "sa_name": "dorogoy_180x200_коричневый",
            }
        }
        result = enrich_review_rows(rows, lookup)
        assert len(result) == 1
        r = result[0]
        assert r["nm_id"] == 742354645
        assert r["brand_name"] == "Уютопия"
        assert r["subject_name"] == "Чехлы для мебели"
        assert r["sa_name"] == "dorogoy_180x200_коричневый"

    def test_skips_non_review_rows(self):
        """Обычные строки продаж — не трогаем."""
        rows = [
            {
                "nm_id": 12345,
                "sa_name": "ART-1",
                "brand_name": "BrandX",
                "subject_name": "Футболки",
                "bonus_type_name": None,
                "deduction": 0,
            }
        ]
        result = enrich_review_rows(rows, lookup={})
        assert result[0]["nm_id"] == 12345
        assert result[0]["brand_name"] == "BrandX"

    def test_skips_ad_deduction_rows(self):
        """Рекламные удержания не должны обогащаться (формат не подходит)."""
        rows = [
            self._row(
                bonus_type_name="Оказание услуг «WB Продвижение», документ №290723728",
                deduction=2715180,
            )
        ]
        result = enrich_review_rows(rows, lookup={742354645: {"brand_name": "X", "subject_name": "Y", "sa_name": "Z"}})
        assert result[0]["nm_id"] == 0
        assert result[0]["brand_name"] == ""

    def test_review_row_without_lookup_match(self):
        """nm_id извлечён, но lookup не содержит этот nm_id — ставим только nm_id, остальное пусто."""
        rows = [
            self._row(
                bonus_type_name="Списание за отзыв XXX: акция №1, товар 999",
                deduction=100,
            )
        ]
        result = enrich_review_rows(rows, lookup={})
        assert result[0]["nm_id"] == 999
        # brand/subject/sa_name остаются пустыми — мы знаем nm_id, но не знаем имени
        assert result[0]["brand_name"] == ""
        assert result[0]["subject_name"] == ""
        assert result[0]["sa_name"] == ""

    def test_does_not_overwrite_existing_nm_id(self):
        """Если nm_id уже не 0 — мы НЕ переписываем (это валидная строка-продажа)."""
        rows = [
            {
                "nm_id": 555,
                "sa_name": "EXISTING",
                "brand_name": "ExistingBrand",
                "subject_name": "ExistingSubject",
                "bonus_type_name": "Списание за отзыв XXX: акция №1, товар 742354645",
                "deduction": 100,
            }
        ]
        lookup = {742354645: {"brand_name": "Wrong", "subject_name": "Wrong", "sa_name": "Wrong"}}
        result = enrich_review_rows(rows, lookup)
        assert result[0]["nm_id"] == 555
        assert result[0]["brand_name"] == "ExistingBrand"
        assert result[0]["sa_name"] == "EXISTING"

    def test_extract_lookup_keys(self):
        """Хелпер для сбора всех nm_id из batch (для последующего SELECT)."""
        from backend.services.wb_finance_helpers import collect_review_nm_ids

        rows = [
            self._row(bonus_type_name="Списание за отзыв A: акция №1, товар 111"),
            self._row(bonus_type_name="Списание за отзыв B: акция №1, товар 222"),
            self._row(bonus_type_name="Оказание услуг «WB Продвижение», документ №1"),
            self._row(bonus_type_name=None, deduction=0),
            self._row(bonus_type_name="Списание за отзыв C: акция №1, товар 111"),  # дубль
        ]
        nm_ids = collect_review_nm_ids(rows)
        assert nm_ids == {111, 222}

    def test_collect_empty_batch(self):
        """Пустая пачка — пустое множество."""
        from backend.services.wb_finance_helpers import collect_review_nm_ids

        assert collect_review_nm_ids([]) == set()

    def test_enrich_mixed_batch(self):
        """Смешанная пачка: 2 продажи, 2 отзыва (1 в lookup, 1 нет), 1 реклама."""
        rows = [
            {
                "nm_id": 100,
                "sa_name": "S1",
                "brand_name": "B1",
                "subject_name": "Sub1",
                "bonus_type_name": None,
                "deduction": 0,
            },
            self._row(bonus_type_name="Списание за отзыв A: акция №1, товар 200"),
            self._row(bonus_type_name="Списание за отзыв B: акция №1, товар 300"),
            self._row(bonus_type_name="Оказание услуг «WB Продвижение», документ №1", deduction=5000),
            {
                "nm_id": 400,
                "sa_name": "S4",
                "brand_name": "B4",
                "subject_name": "Sub4",
                "bonus_type_name": None,
                "deduction": 0,
            },
        ]
        lookup = {200: {"brand_name": "BrandA", "subject_name": "Cat A", "sa_name": "art-a"}}
        result = enrich_review_rows(rows, lookup)
        # Продажи не тронуты
        assert result[0]["nm_id"] == 100
        assert result[4]["nm_id"] == 400
        # Отзыв 200 — обогащён
        assert result[1]["nm_id"] == 200
        assert result[1]["brand_name"] == "BrandA"
        # Отзыв 300 — nm_id есть, но brand/subject/sa_name пустые (нет в lookup)
        assert result[2]["nm_id"] == 300
        assert result[2]["brand_name"] == ""
        # Реклама — не тронута
        assert result[3]["nm_id"] == 0
        assert result[3]["bonus_type_name"].startswith("Оказание")


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: _load_nm_meta + _upsert_batch (real DB)
# ═══════════════════════════════════════════════════════════════════════════════


async def _make_project(db_session, label: str) -> tuple[int, int]:
    suffix = uuid.uuid4().hex[:8]
    user = User(
        username=f"wbrenrich_{label}_{suffix}",
        email=f"wbrenrich_{label}_{suffix}@test.local",
        password_hash="x",
    )
    db_session.add(user)
    await db_session.flush()
    proj = Project(name=f"test_{label}", slug=f"wbrenrich-{label}-{suffix}", owner_id=user.id)
    db_session.add(proj)
    await db_session.flush()
    return proj.id, user.id


def _sale_row(project_id: int, rrd_id: int, nm_id: int, brand: str, subject: str, sa: str) -> WbFinanceRow:
    return WbFinanceRow(
        project_id=project_id,
        rrd_id=rrd_id,
        realizationreport_id=1,
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 7),
        rr_dt=date(2026, 4, 5),
        sale_dt=date(2026, 4, 4),
        sa_name=sa,
        nm_id=nm_id,
        brand_name=brand,
        subject_name=subject,
        doc_type_name="Продажа",
        supplier_oper_name="Продажа",
        bonus_type_name=None,
        quantity=1,
        retail_price_withdisc_rub=Decimal("1000"),
        retail_amount=Decimal("1000"),
        ppvz_for_pay=Decimal("800"),
        ppvz_sales_commission=Decimal("200"),
        delivery_rub=Decimal("0"),
        penalty=Decimal("0"),
        additional_payment=Decimal("0"),
        storage_fee=Decimal("0"),
        acceptance=Decimal("0"),
        deduction=Decimal("0"),
        ppvz_reward=Decimal("0"),
        rebill_logistic_cost=Decimal("0"),
        ppvz_vw=Decimal("0"),
        ppvz_vw_nds=Decimal("0"),
    )


def _review_api_row(rrd_id: int, nm_id: int) -> dict:
    """Имитация строки списания за отзыв из WB API: товарные поля пустые."""
    return {
        "rrd_id": rrd_id,
        "realizationreport_id": 2,
        "date_from": "2026-04-01",
        "date_to": "2026-04-07",
        "rr_dt": "2026-04-06",
        "sale_dt": "2026-04-06",
        "sa_name": "",
        "nm_id": 0,
        "brand_name": "",
        "subject_name": "",
        "doc_type_name": "",
        "supplier_oper_name": "Удержание",
        "bonus_type_name": f"Списание за отзыв abc{rrd_id}: акция №999, товар {nm_id}",
        "quantity": 0,
        "deduction": 7320,
    }


async def _cleanup(db_session, project_ids, user_ids) -> None:
    await db_session.execute(delete(WbFinanceRow).where(WbFinanceRow.project_id.in_(project_ids)))
    await db_session.execute(delete(Project).where(Project.id.in_(project_ids)))
    await db_session.execute(delete(User).where(User.id.in_(user_ids)))
    await db_session.commit()


@pytest.mark.asyncio
async def test_load_nm_meta_picks_latest_sale(db_session):
    """_load_nm_meta берёт самые свежие brand/subject/sa_name по nm_id."""
    pid, uid = await _make_project(db_session, "lookup")
    try:
        # Две продажные строки одного товара — берём свежую (sale_dt позже)
        old = _sale_row(pid, 11001, nm_id=12345, brand="OldBrand", subject="OldCat", sa="old-art")
        old.sale_dt = date(2026, 1, 1)
        new = _sale_row(pid, 11002, nm_id=12345, brand="NewBrand", subject="NewCat", sa="new-art")
        new.sale_dt = date(2026, 4, 4)
        # Другой товар — должен подхватываться
        other = _sale_row(pid, 11003, nm_id=99999, brand="OtherBrand", subject="OtherCat", sa="other")
        # Товар, которого НЕТ в lookup — не должен попасть
        db_session.add_all([old, new, other])
        await db_session.commit()

        meta = await _load_nm_meta(db_session, pid, {12345, 99999, 77777})

        assert 12345 in meta
        assert meta[12345]["brand_name"] == "NewBrand"
        assert meta[12345]["subject_name"] == "NewCat"
        assert meta[12345]["sa_name"] == "new-art"
        assert 99999 in meta
        assert meta[99999]["brand_name"] == "OtherBrand"
        assert 77777 not in meta
    finally:
        await _cleanup(db_session, [pid], [uid])


@pytest.mark.asyncio
async def test_load_nm_meta_isolates_projects(db_session):
    """_load_nm_meta не вытаскивает данные другого проекта (project_id фильтр)."""
    pid_a, uid_a = await _make_project(db_session, "iso_a")
    pid_b, uid_b = await _make_project(db_session, "iso_b")
    try:
        db_session.add(_sale_row(pid_a, 12001, nm_id=55555, brand="BrandA", subject="CatA", sa="a"))
        db_session.add(_sale_row(pid_b, 12002, nm_id=55555, brand="BrandB", subject="CatB", sa="b"))
        await db_session.commit()

        meta_a = await _load_nm_meta(db_session, pid_a, {55555})
        meta_b = await _load_nm_meta(db_session, pid_b, {55555})

        assert meta_a[55555]["brand_name"] == "BrandA"
        assert meta_b[55555]["brand_name"] == "BrandB"
    finally:
        await _cleanup(db_session, [pid_a, pid_b], [uid_a, uid_b])


@pytest.mark.asyncio
async def test_upsert_batch_enriches_review_rows_end_to_end(db_session):
    """End-to-end: продажи + строки-отзывы → после upsert у отзывов заполнены товарные поля."""
    pid, uid = await _make_project(db_session, "e2e")
    try:
        # 1. Сначала кладём продажные строки — это lookup-источник
        db_session.add_all(
            [
                _sale_row(pid, 13001, nm_id=742354645, brand="Уютопия", subject="Чехлы для мебели", sa="dorogoy"),
                _sale_row(pid, 13002, nm_id=399583909, brand="НУ-НУ", subject="Ковры", sa="kover-grey"),
            ]
        )
        await db_session.commit()

        # 2. Имитируем пачку из WB API: 2 строки-отзыва на эти же товары + 1 на неизвестный товар
        api_batch = [
            _review_api_row(rrd_id=14001, nm_id=742354645),
            _review_api_row(rrd_id=14002, nm_id=399583909),
            _review_api_row(rrd_id=14003, nm_id=88888),  # нет в lookup
        ]
        db_batch = [_row_to_values(row, project_id=pid) for row in api_batch]

        # 3. Прогоняем upsert (внутри вызывается обогащение)
        await _upsert_batch(db_session, db_batch, pid)
        await db_session.commit()

        # 4. Проверяем БД — отзывы получили правильный nm_id и метаданные
        result = await db_session.execute(
            select(WbFinanceRow)
            .where(WbFinanceRow.project_id == pid)
            .where(WbFinanceRow.bonus_type_name.like("Списание за отзыв%"))
            .order_by(WbFinanceRow.rrd_id)
        )
        review_rows = result.scalars().all()
        assert len(review_rows) == 3

        # Первый отзыв — обогащён из lookup (Уютопия/Чехлы)
        assert review_rows[0].nm_id == 742354645
        assert review_rows[0].brand_name == "Уютопия"
        assert review_rows[0].subject_name == "Чехлы для мебели"
        assert review_rows[0].sa_name == "dorogoy"

        # Второй — обогащён (НУ-НУ/Ковры)
        assert review_rows[1].nm_id == 399583909
        assert review_rows[1].brand_name == "НУ-НУ"
        assert review_rows[1].subject_name == "Ковры"

        # Третий — nm_id есть, но lookup пустой → товарные поля пусты
        assert review_rows[2].nm_id == 88888
        assert review_rows[2].brand_name == ""
        assert review_rows[2].subject_name == ""
    finally:
        await _cleanup(db_session, [pid], [uid])


@pytest.mark.asyncio
async def test_upsert_batch_does_not_touch_sales(db_session):
    """_upsert_batch не должен трогать обычные строки продаж."""
    pid, uid = await _make_project(db_session, "noop")
    try:
        api_row = {
            "rrd_id": 15001,
            "realizationreport_id": 1,
            "date_from": "2026-04-01",
            "date_to": "2026-04-07",
            "rr_dt": "2026-04-05",
            "sale_dt": "2026-04-04",
            "sa_name": "ART-X",
            "nm_id": 11111,
            "brand_name": "BrandX",
            "subject_name": "SubjectX",
            "doc_type_name": "Продажа",
            "supplier_oper_name": "Продажа",
            "bonus_type_name": None,
            "quantity": 1,
            "retail_price_withdisc_rub": 500,
            "retail_amount": 500,
            "ppvz_for_pay": 400,
            "ppvz_sales_commission": 100,
        }
        await _upsert_batch(db_session, [_row_to_values(api_row, pid)], pid)
        await db_session.commit()

        result = await db_session.execute(select(WbFinanceRow).where(WbFinanceRow.project_id == pid))
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].nm_id == 11111
        assert rows[0].brand_name == "BrandX"
        assert rows[0].sa_name == "ART-X"
    finally:
        await _cleanup(db_session, [pid], [uid])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
