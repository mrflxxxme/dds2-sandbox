# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests for backend/services/funnel/article_parser.py — parse_size / parse_color.

Примеры артикулов взяты из реальной выгрузки номенклатуры (ковры, шторы,
покрывала, коврики, пледы, мозаика) — покрывают форматы с латинской x,
кириллической х, разделителями _ / *, слепленный с узором цвет и категории
без размера/цвета.
"""

import pytest

from backend.services.funnel.article_parser import parse_color, parse_size


class TestParseSize:
    @pytest.mark.parametrize(
        "article,expected",
        [
            ("120x170_винтажкрасныйяркий", "120x170"),  # латинская x
            ("120х160_бежевый", "120x160"),  # кириллическая х
            ("200x300_бежевый", "200x300"),
            ("BLACKOUT_150х240_кофейный", "150x240"),  # размер в середине
            ("ELKA_160х200/50*70/молочный", "160x200"),  # первое совпадение, не 50*70
            ("100х60_60х50_мрамор_бежевый", "100x60"),  # два размера → первый
            ("INDI_180x230", "180x230"),  # без цвета
            ("50x80_рельф_коричневый", "50x80"),
        ],
    )
    def test_extracts_size(self, article, expected):
        assert parse_size(article) == expected

    @pytest.mark.parametrize(
        "article",
        [
            "mosaic_animals_mem",  # мозаика — нет размера
            "12345е666644",  # цифры без разделителя x/х/*
            "case_2seat_babochki_коричневый",  # размер словами
            "",
            None,
        ],
    )
    def test_no_size_returns_none(self, article):
        assert parse_size(article) is None


class TestParseColor:
    @pytest.mark.parametrize(
        "article,expected",
        [
            ("200x300_бежевый", "бежевый"),
            ("BLACKOUT_150х240_темно-серый", "темно-серый"),  # составной бьёт «серый»
            ("BLACKOUT_150х240_светло-серый", "светло-серый"),
            ("120х160_кроликбежевый", "бежевый"),  # слеплено с узором
            ("120x170_винтажромбсиний", "синий"),
            ("ELKA_160х200/50*70/изумруд", "изумрудный"),  # alias → канон
            ("50х80_40х50_мрамор_серебро", "серебристый"),  # alias, узор «мрамор» игнор
            ("120х160_вишня", "вишневый"),
            ("FLANEL_240х220/50*70_тёмно-серый", "темно-серый"),  # ё-нормализация
        ],
    )
    def test_extracts_color(self, article, expected):
        assert parse_color(article) == expected

    @pytest.mark.parametrize(
        "article",
        [
            "INDI_180x230",  # плед без цвета
            "mosaic_animals_mem",
            "30х60/-30шт/Матовые",  # панель — «матовые» не цвет
            "",
            None,
        ],
    )
    def test_no_color_returns_none(self, article):
        assert parse_color(article) is None
