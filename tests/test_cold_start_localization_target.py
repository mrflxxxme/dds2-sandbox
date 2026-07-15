# ruff: noqa: RUF002, RUF003
"""Cold-start: концентрация раздачи новинки «до целевой локализации» (75%).

Новинка (нет истории продаж) сидируется НЕ тонким слоем по всем ФО, а
концентрированно: округа берутся в порядке убывания bench_share и наполняются,
пока кумулятивно не покрыто ~target% benchmark-спроса. Хвост дальних/мелких
округов сверх target — НЕ сидируется (seed остаётся на ФФ). Anchor-склады /
схема воришек (priority_score) и min_pack (целые пачки) сохраняются.

Тесты — на чистую функцию `concentrate_share_to_target` + на интеграцию в
`compute_cold_start_table` / `compute_distribution` (localization_target).
"""

from typing import Any

import pytest

from backend.services import cold_start_distribution_service as cs
from backend.services.cold_start_distribution_service import concentrate_share_to_target


@pytest.mark.unit
class TestConcentrateShareToTarget:
    """Pure-logic: концентрация долей ФО до target% (хвост обнуляется)."""

    def test_keeps_only_top_districts_until_target(self) -> None:
        """central 0.30 покрывает 30% < 75 → берём; +south 0.18 = 48 < 75 → берём;
        +volga 0.16 = 64 < 75 → берём; +ural 0.09 = 73 < 75 → берём;
        +far 0.13 = 86 ≥ 75 → far ПОСЛЕДНИЙ включённый (cum пересёк порог);
        nw 0.09 — хвост сверх target → обнуляется."""
        share = {
            "central": 0.30,
            "south_caucasus": 0.18,
            "volga": 0.16,
            "far_east_siberia": 0.13,
            "ural": 0.09,
            "northwest": 0.09,
        }
        out = concentrate_share_to_target(share, target_pct=75)
        # хвостовой (последний по share) северо-западный — не сидируется
        assert out.get("northwest", 0.0) == 0.0
        # сумма сохранена (нормировка к исходной базе) — товар не теряется
        assert abs(sum(out.values()) - sum(share.values())) < 1e-9
        # топовые округа присутствуют
        assert out["central"] > 0
        assert out["volga"] > 0

    def test_concentration_shifts_mass_to_top(self) -> None:
        """После концентрации доля топ-округа РАСТЁТ (хвост влит в базу нормировки)."""
        share = {"central": 0.30, "volga": 0.16, "northwest": 0.04}
        out = concentrate_share_to_target(share, target_pct=75)
        # central покрывает 0.30/0.50=60% < 75 → берём; +volga = 92% ≥75 → volga последний;
        # northwest хвост → 0; central доля вырастает относительно исходной нормированной
        assert out.get("northwest", 0.0) == 0.0
        assert out["central"] > 0.30  # перенормировка концентрирует массу
        assert abs(sum(out.values()) - sum(share.values())) < 1e-9

    def test_abroad_unknown_excluded_from_target_base(self) -> None:
        """abroad/unknown не сидируемы → не входят в базу target, но и не теряются
        (концентрация работает только по локализуемым ФО)."""
        share = {"central": 0.6, "volga": 0.3, "abroad": 0.1}
        out = concentrate_share_to_target(share, target_pct=75)
        # central=0.6/0.9=66% < 75 → берём; +volga=100% → volga последний.
        assert out["central"] > 0
        assert out["volga"] > 0
        # abroad не сидируется
        assert out.get("abroad", 0.0) == 0.0

    def test_target_100_keeps_all(self) -> None:
        """target=100 → концентрация не режет (все локализуемые ФО остаются)."""
        share = {"central": 0.5, "volga": 0.3, "northwest": 0.2}
        out = concentrate_share_to_target(share, target_pct=100)
        assert out["central"] > 0
        assert out["volga"] > 0
        assert out["northwest"] > 0

    def test_empty_share(self) -> None:
        assert concentrate_share_to_target({}, target_pct=75) == {}


@pytest.mark.unit
class TestColdStartTableLocalizationTarget:
    """Интеграция: localization_target концентрирует раздачу в таблице новинок."""

    @pytest.fixture(autouse=True)
    def _no_acceptance_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _empty(*_a: Any, **_kw: Any) -> set[str]:
            return set()

        monkeypatch.setattr(
            "backend.services.warehouse_acceptance_service.get_acceptance_blocked_warehouses",
            _empty,
        )

    def _wire(self, monkeypatch: pytest.MonkeyPatch, share: dict[str, float], rf_qty: int) -> None:
        async def fake_excluded(*_a: Any, **_kw: Any) -> list[str]:
            return []

        async def fake_district_share(*_a: Any, **_kw: Any) -> tuple[dict[str, float], int]:
            return (share, 1000)

        async def fake_traffic(*_a: Any, **_kw: Any) -> dict[str, int]:
            return {}

        async def fake_segment(*_a: Any, **_kw: Any) -> list[dict]:
            return [
                {
                    "internal_id": 1,
                    "nm_id": 111,
                    "article_seller": "NEW",
                    "subject": "X",
                    "brand": "Y",
                    "barcode": "",
                    "rf_qty": rf_qty,
                    "rf_by_warehouse": {},
                    "wb_qty": 0,
                    "wb_by_warehouse": {},
                    "asm_qty": 0,
                    "sales_14d": 0,
                    "revenue_30d": 0.0,
                    "is_newcomer": True,
                }
            ]

        async def fake_active_asm(*_a: Any, **_kw: Any) -> dict[str, int]:
            return {}

        async def fake_rf_wh(*_a: Any, **_kw: Any) -> list:
            return []

        # детерминированные speed-anchor: один склад на ФО, имя = ключ ФО.
        def fake_anchors(d: str) -> tuple[tuple[str, int], ...]:
            return ((f"WH_{d}", 1),)

        monkeypatch.setattr(cs, "get_excluded_warehouses", fake_excluded)
        monkeypatch.setattr(cs, "fetch_district_share", fake_district_share)
        monkeypatch.setattr(cs, "fetch_warehouse_traffic", fake_traffic)
        monkeypatch.setattr(cs, "fetch_cold_start_segment", fake_segment)
        monkeypatch.setattr(cs, "fetch_active_assemblies_for_sku", fake_active_asm)
        monkeypatch.setattr(cs, "fetch_rf_warehouses", fake_rf_wh)
        monkeypatch.setattr(cs, "get_anchors_for_okrug", fake_anchors)
        # канонизация имён — identity (наши синтетические WH_* не в карте алиасов).
        monkeypatch.setattr(cs, "_canonicalize_asm_warehouse", lambda n: n)
        # _is_spec_warehouse — наши WH_* не спец.
        monkeypatch.setattr(cs, "_is_spec_warehouse", lambda n: False)

    @pytest.mark.asyncio
    async def test_tail_districts_not_seeded_at_target_75(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Хвостовой ФО (мелкая доля сверх 75%) не получает seed — остаётся на ФФ.

        ИСКЛЮЧЕНИЕ — Северо-Западный: гарантия СЗФО (аудит 2026-07-09) даёт ему
        ровно min_pack поверх концентрации при партии ≥ 4×min_pack — иначе
        новинки никогда не заезжали в СЗФО и его локализация не набиралась.
        Прочие хвостовые ФО (здесь ural) по-прежнему не сидируются.
        """
        share = {
            "central": 0.30,
            "south_caucasus": 0.18,
            "volga": 0.16,
            "far_east_siberia": 0.13,
            "ural": 0.09,
            "northwest": 0.05,
        }
        self._wire(monkeypatch, share, rf_qty=1000)

        resp = await cs.compute_cold_start_table(
            db=None,  # type: ignore[arg-type]
            project_id=1,
            window_days=14,
            min_pack=5,
            bench_from_project_id=None,
            ship_fraction=1.0,  # изолируем концентрацию от капа «сеять 55%»
            localization_target=75,
        )
        row = resp.rows[0]
        # cum (нормированный): central .33 → south .53 → volga .70 → far_east .85
        # ≥ .75 → kept; хвост = ural + northwest. УФО получает ТОЛЬКО переброс
        # ДВ-излишка (FAR_EAST_MAX_SHARE=6%: сверх-капа доля ДВ уходит на Урал —
        # «ворота в Сибирь», существующая механика _cap_far_east_share), а не
        # свою хвостовую долю: ДВ зажат ровно в 6% партии.
        assert row.allocations.get("WH_far_east_siberia", 0) == 60, f"ДВ-кап 6%: {row.allocations}"
        # СЗФО — гарантия (аудит 2026-07-09): ровно min_pack поверх концентрации,
        # НЕ полная доля (0.05×1.18×1000 ≈ 59) и НЕ ноль.
        assert row.allocations.get("WH_northwest", 0) == 5, f"гарантия СЗФО = min_pack: {row.allocations}"
        # топовый central — сидируется; консервация партии соблюдена.
        assert row.allocations.get("WH_central", 0) > 0
        assert sum(row.allocations.values()) == 1000

    @pytest.mark.asyncio
    async def test_target_100_seeds_all_districts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """localization_target=100 → концентрация не режет, сидируются все ФО (как раньше)."""
        share = {
            "central": 0.30,
            "south_caucasus": 0.18,
            "volga": 0.16,
            "far_east_siberia": 0.13,
            "ural": 0.09,
            "northwest": 0.05,
        }
        self._wire(monkeypatch, share, rf_qty=1000)

        resp = await cs.compute_cold_start_table(
            db=None,  # type: ignore[arg-type]
            project_id=1,
            window_days=14,
            min_pack=5,
            bench_from_project_id=None,
            ship_fraction=1.0,
            localization_target=100,
        )
        row = resp.rows[0]
        assert row.allocations.get("WH_northwest", 0) > 0, "при target=100 хвост тоже сидируется"

    @pytest.mark.asyncio
    async def test_nw_anchor_keeps_preconcentration_share_in_meta(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Гарантированный СЗФО-якорь в main_warehouses несёт ИСХОДНУЮ долю (до концентрации).

        Фронтовый засев (coldStartSeed.seedNewcomerWholeBoxes — экран «Распределение
        машины» и коробочные новинки черновика) сеет по share_pct якорей: при 0 его
        СЗФО-гарантия (фильтр sf>0) физически не может сработать → новинки никогда
        не заезжали в Питер этими путями, гарантия жила только в бэковом
        allocations-пути. Прочий хвост БЕЗ гарантии (ural) остаётся с 0 —
        концентрация для него в силе.
        """
        # ДВ мал (< FAR_EAST_MAX_SHARE) — без переброса излишка на Урал, чтобы
        # хвостовой ural остался чистым контролем «без гарантии → 0».
        share = {
            "central": 0.30,
            "south_caucasus": 0.18,
            "volga": 0.16,
            "ural": 0.09,
            "northwest": 0.05,
            "far_east_siberia": 0.04,
        }
        self._wire(monkeypatch, share, rf_qty=1000)

        resp = await cs.compute_cold_start_table(
            db=None,  # type: ignore[arg-type]
            project_id=1,
            window_days=14,
            min_pack=5,
            bench_from_project_id=None,
            ship_fraction=1.0,
            localization_target=75,
        )
        by_wh = {m["warehouse"]: m for m in resp.main_warehouses}
        # СЗФО отрезан концентрацией (кум. база central+south+volga ≈ .78 ≥ .75),
        # но гарантия сохраняет исходную долю в мете: 0.05 → 5%.
        assert by_wh["WH_northwest"]["share_pct"] == pytest.approx(5.0), by_wh["WH_northwest"]
        # Урал — хвост без гарантии: как и раньше, 0 (не сидируется фронтом).
        assert by_wh["WH_ural"]["share_pct"] == 0, by_wh["WH_ural"]

    @pytest.mark.asyncio
    async def test_conservation_seed_le_free_ff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Σ seed ≤ свободный ФФ (консервация: товар не создаётся из воздуха)."""
        share = {
            "central": 0.30,
            "south_caucasus": 0.18,
            "volga": 0.16,
            "far_east_siberia": 0.13,
            "ural": 0.09,
            "northwest": 0.05,
        }
        self._wire(monkeypatch, share, rf_qty=137)

        resp = await cs.compute_cold_start_table(
            db=None,  # type: ignore[arg-type]
            project_id=1,
            window_days=14,
            min_pack=5,
            bench_from_project_id=None,
            ship_fraction=1.0,
            localization_target=75,
        )
        row = resp.rows[0]
        assert row.total_allocated <= 137, f"Σ seed {row.total_allocated} > свободного ФФ 137"


@pytest.mark.unit
class TestTransitNettingNotDoubled:
    """Транзит (SHIPPED) учитывается ОДИН раз: статус-фильтр уже включает SHIPPED."""

    def test_active_assembly_query_includes_shipped(self) -> None:
        """fetch_active_assemblies_for_sku фильтрует по статусам, включая SHIPPED
        (в пути) → новинка не перетаривается поверх уже едущего."""
        import inspect

        # Статусы вынесены в константы (2026-07-10, «один мир» черновика).
        assert "SHIPPED" in cs._ACTIVE_ASM_STATUSES, "SHIPPED должен входить в active-статусы (учёт транзита)"
        assert "VEHICLE_ASSIGNED" in cs._ACTIVE_ASM_STATUSES
        # PRE_DISTRIBUTED — резерв машины: вычитается из per-склад ЦЕЛЕЙ,
        # но НЕ из свободного ФФ (товара ещё нет на ФФ).
        assert "PRE_DISTRIBUTED" not in cs._ACTIVE_ASM_STATUSES
        assert "PRE_DISTRIBUTED" in cs._ACTIVE_ASM_STATUSES_WITH_PD
        # тот же active-набор в сегментном запросе (вычет из свободного ФФ, без PD)
        seg_src = inspect.getsource(cs.fetch_cold_start_segment)
        assert "SHIPPED" in seg_src
        assert "PRE_DISTRIBUTED" not in seg_src
