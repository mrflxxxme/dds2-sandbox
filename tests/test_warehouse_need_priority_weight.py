# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты _hamilton_priority_weight — весов priority-weighted Hamilton (шаг 4.6).

Фиксируют фиксы аудита распределения по ФО (2026-07-09):
  1. Штраф «воришки» 0.6 — только за top-2 слоты в цепочках чужого ФО
     (глубокие fallback-слоты не штрафуются): Шушары больше не душатся
     за слоты #4/#6 у Читы/Алматы.
  2. Знаменатель priority_score — локализуемые города ФО: скор якоря СЗФО
     не размывается 6 городами, куда WB возит только чужими складами.
  3. Дыры справочников закрыты: «Тверь» → central, «Белая Дача» (регистр),
     «Тула»/«Рязань» матчатся со speed-именами «Алексин (Тула)» /
     «Рязань (Тюшевское)».

Числа откалиброваны по backend/data/wb_warehouse_speed.json v2026-05-14 —
при обновлении speed-карты пороги могут потребовать пересмотра.
"""

from __future__ import annotations

import pytest

from backend.services.warehouse_need_service import _hamilton_priority_weight as weight


class TestNorthwestAnchor:
    def test_shushary_no_longer_penalized(self) -> None:
        """Шушары — якорь СЗФО: топ-1 у 3 из 4 локализуемых городов, штрафа нет.

        Было (до фикса): (1 + 0.300) × 0.6 = 0.78 — ниже нейтрального 1.0.
        Стало: (1 + 3/4) × 1.0 = 1.75.
        """
        assert weight("СПБ Шушары") == pytest.approx(1.75, abs=0.01)

    def test_shushary_on_par_with_other_anchors(self) -> None:
        """Якорь СЗФО в одном ряду с якорями других ФО (Невинномысск ≈1.76)."""
        assert abs(weight("СПБ Шушары") - weight("Невинномысск")) < 0.1

    def test_shushary_beats_neutral_and_thieves(self) -> None:
        """Якорь СЗФО обязан весить больше нейтральных (1.0) и воришек ЦФО."""
        w = weight("СПБ Шушары")
        assert w > 1.0
        assert w > weight("Электросталь")
        assert w > weight("Коледино")
        assert w > weight("Владимир")

    def test_kaliningrad_anchor_above_neutral(self) -> None:
        """Калининград — топ-1 своего города (1 из 4 локализуемых): 1.25."""
        assert weight("Калининград") == pytest.approx(1.25, abs=0.01)


class TestThievesStillPenalized:
    @pytest.mark.parametrize(
        "wh",
        [
            "Электросталь",  # top-1/2 у 7 городов СЗФО
            "Подольск",  # top-слоты СЗФО и Сибири
            "Коледино",  # top-2 far_east (Брест)
            "Владимир",  # top-слоты СЗФО/ПФО/Сибири
        ],
    )
    def test_central_thieves_below_neutral(self, wh: str) -> None:
        assert weight(wh) < 1.0

    def test_tula_matches_speed_name_and_gets_penalty(self) -> None:
        """«Тула» (имя WB API) теперь матчится с «Алексин (Тула)» из speed-карты.

        Алексин — top-слоты у городов СЗФО (Нарьян-Мар#2) и Сибири/Беларуси →
        воришка → вес < 1.0. До фикса алиаса вес был нейтральный 1.0.
        """
        assert weight("Тула") == pytest.approx(0.89, abs=0.01)

    def test_ryazan_matches_speed_name_no_penalty(self) -> None:
        """«Рязань» матчится с «Рязань (Тюшевское)»: скор > 0 (top-1 города
        Рязань), но воришкой по top-2 не является (все чужие слоты глубокие)."""
        assert weight("Рязань") == pytest.approx(1.12, abs=0.01)
        assert weight("Рязань") > 1.0


class TestNeutralAndUnknown:
    def test_unknown_warehouse_neutral(self) -> None:
        assert weight("Несуществующий склад") == 1.0

    def test_abroad_neutral(self) -> None:
        assert weight("Атакент") == 1.0

    def test_tver_now_central_neutral(self) -> None:
        """«Тверь» больше не unknown (central), в speed-карте её нет → 1.0.

        Ключевое отличие от «до»: склад попадает в district-pooling ЦФО
        и min-stock bump; вес нейтральный, но якорь СЗФО (1.75) теперь
        стабильно выигрывает у него cap — инверсия «unknown лучше якоря»
        устранена.
        """
        from backend.services.warehouse_district import warehouse_to_district

        assert warehouse_to_district("Тверь") == "central"
        assert weight("Тверь") == 1.0
        assert weight("СПБ Шушары") > weight("Тверь")


class TestCrossDistrictParity:
    def test_ural_anchor_no_longer_dominates_northwest(self) -> None:
        """ЕКБ (скор 1.0, штраф за Сибирь → 1.2) больше не получает 2.3×
        больше cap, чем Шушары, при равном need: теперь Шушары (1.75) выше."""
        assert weight("СПБ Шушары") > weight("Екатеринбург - Перспективная 14")

    def test_kazan_penalized_for_syktyvkar(self) -> None:
        """Казань — якорь ПФО, но top-1 Сыктывкара (СЗФО) → штраф остаётся."""
        assert weight("Казань") == pytest.approx(0.975, abs=0.01)
