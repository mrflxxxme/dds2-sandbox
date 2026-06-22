# ruff: noqa: RUF001, RUF003
"""Сопоставление имён складов сдачи (ФФ-заявка ↔ наш документ).

Один физический склад WB подписан по-разному в migfull («ВБ | Екатеринбург
Перспективный») и у нас («Екатеринбург - Перспективная 14»). Стем-токенный
матчер должен их сводить, не сливая разные склады (улицы/порядковые номера).
"""
from backend.services import fulfillment_service as fs


class TestWhStem:
    def test_gender_endings_collapse_to_same_stem(self):
        assert fs._wh_stem("перспективный") == fs._wh_stem("перспективная")
        assert fs._wh_stem("центральный") == "центральн"
        assert fs._wh_stem("центральная") == "центральн"

    def test_short_and_non_adjective_untouched(self):
        assert fs._wh_stem("белая") == "белая"  # стем <4 → не трогаем
        assert fs._wh_stem("коледино") == "коледино"
        assert fs._wh_stem("екатеринбург") == "екатеринбург"
        assert fs._wh_stem("казань") == "казань"


class TestWhNamesMatch:
    def test_migfull_vs_our_perspektivnaya_14(self):
        """Сам баг: «ВБ | Екатеринбург Перспективный» ↔ «Екатеринбург - Перспективная 14»."""
        a = "ВБ | Екатеринбург Перспективный"
        b = "Екатеринбург - Перспективная 14"
        assert fs._wh_names_match(a, b) is True
        assert fs._wh_names_match(b, a) is True

    def test_marketplace_prefix_and_punctuation_ignored(self):
        assert fs._wh_names_match("Коледино", "МСК Коледино") is True
        assert fs._wh_names_match("Москва (Коледино)", "Коледино") is True
        assert fs._wh_names_match("WB Казань", "Казань") is True

    def test_same_city_different_street_no_match(self):
        assert fs._wh_names_match("Екатеринбург Испытателей 14", "Екатеринбург - Перспективная 14") is False

    def test_street_address_numbers_differ_but_same_warehouse(self):
        # «Целиком» пишет улицу-адрес, WB — своё имя того же склада: номера дома
        # расходятся (14 vs 12/2), но общий ориентир — улица (прилагательное).
        a = "Екатеринбург - Перспективная 14"
        b = "Екатеринбург - Перспективный 12/2"
        assert fs._wh_names_match(a, b) is True
        assert fs._wh_names_match(b, a) is True

    def test_same_toponym_different_ordinal_no_match(self):
        # Топоним + порядковый номер: число различает склады (улицы-прилагательного нет).
        assert fs._wh_names_match("Чехов 1", "Чехов 2") is False
        assert fs._wh_names_match("Чехов 1", "Чехов 1") is True
        assert fs._wh_names_match("Подольск 3", "Подольск 4") is False
        assert fs._wh_names_match("Обухово 2", "Обухово 2") is True

    def test_one_sided_number_ignored(self):
        # Номер дома есть только у нас — не должен ломать совпадение.
        assert fs._wh_names_match("Чехов", "Чехов 1") is True

    def test_different_city_no_match(self):
        assert fs._wh_names_match("Казань", "Коледино") is False

    def test_empty_ff_side_matches_all(self):
        # Склад ФФ-заявки неизвестен → фильтровать не по чему.
        assert fs._wh_names_match(None, "Коледино") is True
        assert fs._wh_names_match("", "Коледино") is True
        assert fs._wh_names_match("ВБ", "Коледино") is True  # только маркер → слов нет

    def test_empty_doc_side_no_match(self):
        assert fs._wh_names_match("Коледино", None) is False
        assert fs._wh_names_match("Коледино", "") is False


class TestMatchWhOption:
    def test_picks_option_by_stem_token(self):
        opts = [{"id": 5, "name": "Казань"}, {"id": 7, "name": "ВБ | Екатеринбург Перспективный"}]
        match = fs._match_wh_option(opts, "Екатеринбург - Перспективная 14")
        assert match is not None and match["id"] == 7

    def test_no_match_returns_none(self):
        opts = [{"id": 1, "name": "Казань"}]
        assert fs._match_wh_option(opts, "Коледино") is None

    def test_blank_hint_returns_none(self):
        opts = [{"id": 1, "name": "Казань"}]
        assert fs._match_wh_option(opts, "") is None
        assert fs._match_wh_option(opts, "ВБ |") is None
