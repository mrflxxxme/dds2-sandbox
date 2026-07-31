# ruff: noqa: RUF001
"""
Воронка с произвольной цепочкой группировок.

Вместо семи зашитых веток (day/sku/brand/subject/tag/imt/size) — упорядоченный
список измерений любой длины: «предмет → артикул → неделя», «бренд → месяц» и т.п.
Строки грузятся один раз, дерево собирается в памяти теми же примитивами, что и
одноуровневые группировки (`_new_group_agg` / `_accumulate_row` / `_finalize_groups`),
поэтому цифры в дереве и в старых группировках считаются одинаково.

Каждый узел агрегируется независимо, поэтому родитель равен сумме детей по
аддитивным метрикам и корректно пересчитывает производные (маржа, ДРР, СПП).
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily
from backend.services.funnel.article_parser import parse_size
from backend.services.funnel.bdr_rates import BdrRatesLookup
from backend.services.funnel.queries_grouping import (
    _accumulate_row,
    _finalize_groups,
    _load_funnel_rows,
    _new_group_agg,
)
from backend.services.tariff_service import get_avg_buyout_map, get_tariff_map

logger = logging.getLogger("dds.funnel")

# Глубже 4 уровней дерево становится нечитаемым, а число узлов растёт как
# произведение мощностей измерений — держим потолок.
MAX_CHAIN = 4
# Потолок детей на узел: защищает ответ от раздувания на широких измерениях.
MAX_CHILDREN = 500

MONTHS_RU = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


class UnknownDimension(ValueError):
    """Неизвестное, повторяющееся или слишком длинное измерение в цепочке."""


def week_bucket(d: date) -> tuple[str, str]:
    """Неделя пн–вс: ключ — понедельник в ISO, подпись — «13.07–19.07»."""
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), f"{monday:%d.%m}–{sunday:%d.%m}"


def month_bucket(d: date) -> tuple[str, str]:
    """Месяц: ключ «2026-07», подпись «июль 2026»."""
    return f"{d.year}-{d.month:02d}", f"{MONTHS_RU[d.month - 1]} {d.year}"


@dataclass
class Dimension:
    """Одно измерение цепочки.

    keys — (ключ, подпись) для строки; список, потому что измерение бывает
    многозначным (у товара несколько ярлыков — он попадает в несколько веток).
    extra — поля, которые узел этого уровня кладёт в ответ поверх метрик,
    чтобы фронт мог показать артикул/дату так же, как в одноуровневых режимах.
    collect_nm — узлы уровня несут список своих товаров (нужны миниатюры карточек
    у склейки, а сами артикулы лежат уровнями ниже и приезжают отдельным запросом).
    """

    key: str
    label: str
    keys: Callable[[WbFunnelDaily, dict], list[tuple[str, str]]]
    extra: Callable[[WbFunnelDaily], dict] | None = None
    collect_nm: bool = False


# Сколько товаров узел отдаёт на миниатюры (в UI видно 5, шестой — запас на битые фото).
NM_SAMPLE = 6


def _one(key: str | None, fallback: str) -> list[tuple[str, str]]:
    v = (key or "").strip() or fallback
    return [(v, v)]


# ABC — доля товара в выручке периода: A даёт первые 80 %, B — следующие 15 %, C — хвост.
ABC_LABELS = {
    "A": "Категория A (80% выручки)",
    "B": "Категория B (15% выручки)",
    "C": "Категория C (5% выручки)",
}


def _abc_keys(r: WbFunnelDaily, ctx: dict) -> list[tuple[str, str]]:
    cat = ctx["nm_abc"].get(r.nm_id, "C")
    return [(cat, ABC_LABELS[cat])]


DIMENSIONS: dict[str, Dimension] = {
    "day": Dimension(
        "day", "По дням",
        lambda r, ctx: [(r.date.isoformat(), r.date.isoformat())],
        lambda r: {"date": r.date.isoformat()},
    ),
    "week": Dimension("week", "По неделям", lambda r, ctx: [week_bucket(r.date)]),
    "month": Dimension("month", "По месяцам", lambda r, ctx: [month_bucket(r.date)]),
    "subject": Dimension(
        "subject", "По предметам",
        lambda r, ctx: _one(r.subject, "Без предмета"),
        lambda r: {"subject": r.subject},
    ),
    "brand": Dimension(
        "brand", "По брендам",
        lambda r, ctx: _one(r.brand, "Без бренда"),
        lambda r: {"brand": r.brand},
    ),
    "nm": Dimension(
        "nm", "По артикулам",
        lambda r, ctx: [(str(r.nm_id), r.vendor_code or str(r.nm_id))],
        lambda r: {"nm_id": r.nm_id, "vendor_code": r.vendor_code, "brand": r.brand, "subject": r.subject},
    ),
    "size": Dimension(
        "size", "По размерам",
        lambda r, ctx: _one(parse_size(r.vendor_code), "Без размера"),
        lambda r: {"size": parse_size(r.vendor_code) or "Без размера"},
    ),
    "tag": Dimension(
        "tag", "По ярлыкам",
        lambda r, ctx: [(t, t) for t in ctx["nm_tags"].get(r.nm_id, ["Без ярлыка"])],
    ),
    "imt": Dimension(
        "imt", "По склейкам",
        lambda r, ctx: _one(ctx["nm_imt"].get(r.nm_id), "Без склейки"),
        collect_nm=True,
    ),
    "abc": Dimension("abc", "Категория ABC", _abc_keys),
}


def validate_chain(dims: list[str]) -> list[Dimension]:
    """Разбирает цепочку измерений; бросает UnknownDimension на любом кривом вводе."""
    if not dims:
        raise UnknownDimension("Цепочка группировок пуста")
    if len(dims) > MAX_CHAIN:
        raise UnknownDimension(f"Не больше {MAX_CHAIN} уровней группировки")
    if len(set(dims)) != len(dims):
        raise UnknownDimension("Измерение указано дважды")
    out = []
    for d in dims:
        dim = DIMENSIONS.get(d)
        if dim is None:
            raise UnknownDimension(f"Неизвестное измерение: {d}")
        out.append(dim)
    return out


async def _build_context(db: AsyncSession, pid: int, dims: list[Dimension]) -> dict:
    """Догружает справочники только для тех измерений, что реально в цепочке."""
    ctx: dict = {"nm_tags": {}, "nm_imt": {}, "nm_abc": {}}
    keys = {d.key for d in dims}

    if "tag" in keys:
        from backend.models.refs import ProductTag, ProductTagMap

        res = await db.execute(
            select(ProductTagMap.nm_id, ProductTag.name)
            .join(ProductTag, ProductTag.id == ProductTagMap.tag_id)
            .where(ProductTagMap.project_id == pid, ProductTag.is_deleted.is_(False))
        )
        tags: dict[int, list[str]] = defaultdict(list)
        for nm_id, name in res:
            tags[nm_id].append(name)
        ctx["nm_tags"] = tags

    if "imt" in keys:
        from backend.models.cost import Nomenclature
        from backend.models.refs import ImtAlias

        nom = await db.execute(
            select(Nomenclature.article_wb, Nomenclature.imt_id).where(
                Nomenclature.project_id == pid, Nomenclature.imt_id.isnot(None)
            )
        )
        alias_res = await db.execute(select(ImtAlias.imt_id, ImtAlias.name).where(ImtAlias.project_id == pid))
        aliases = {r.imt_id: r.name for r in alias_res}
        ctx["nm_imt"] = {
            art: (aliases.get(imt) or f"#{imt}") for art, imt in nom if art and imt
        }

    return ctx


def _build_abc_map(
    rows: list[WbFunnelDaily],
    has_bdr: bool,
    bdr_rates_map: BdrRatesLookup | None,
    tariff_map: dict,
    buyout_map: dict,
    tax_info: dict,
    tax_rate: float,
) -> dict[int, str]:
    """nm_id → категория ABC по выручке за период.

    Выручку считаем теми же примитивами, что и любой узел дерева, поэтому граница
    категорий совпадает с колонкой «Выручка» и со старой вкладкой ABC.
    """
    from backend.services.funnel.ad_campaigns_service import _assign_abc

    aggs: dict[str, dict] = {}
    for r in rows:
        key = str(r.nm_id)
        agg = aggs.get(key)
        if agg is None:
            agg = _new_group_agg(has_bdr)
            agg["label"] = key
            aggs[key] = agg
        _accumulate_row(agg, r, r.nm_id, bdr_rates_map, tariff_map, buyout_map, tax_info)

    # limit=len(aggs): усечения быть не должно — иначе хвост товаров остался бы без категории
    finalized = _finalize_groups(aggs, tax_rate, "label", max(1, len(aggs)))
    _assign_abc(finalized, "revenue", "abc")
    return {int(row["label"]): row["abc"] for row in finalized}


class _Node:
    """Узел дерева: собственная агрегация + дети по следующему измерению.

    В агрегацию кладём КЛЮЧ, а не подпись: подписи не уникальны (два предмета
    могут звать одинаково), а по ключу узел находится однозначно. Наружу ключ
    уходит как `sort_key` — по нему фронт строит хронологию недель и месяцев,
    где подпись («13.07–19.07») сортировке не поддаётся.
    """

    __slots__ = ("agg", "key", "label", "extra", "children", "nm_rev")

    def __init__(self, key: str, label: str, has_bdr: bool):
        self.agg = _new_group_agg(has_bdr)
        self.agg["label"] = key
        self.key = key
        self.label = label
        self.extra: dict = {}
        self.children: dict[str, _Node] = {}
        # Заполняется только у измерений с collect_nm: nm_id → сумма заказов
        self.nm_rev: dict[int, float] | None = None


def _finalize(
    nodes: dict[str, _Node],
    dim_index: int,
    dims: list[Dimension],
    tax_rate: float,
    limit: int,
    levels_left: int,
) -> list[dict]:
    """Рекурсивно превращает узлы в строки ответа (метрики считает _finalize_groups).

    levels_left — сколько уровней ещё материализуем. Глубже узлы не строятся и не
    отдаются: у строки остаётся только `has_children`, а дети приезжают отдельным
    запросом при раскрытии (иначе ответ разрастается до десятков мегабайт).
    """
    if not nodes:
        return []
    aggs = {k: n.agg for k, n in nodes.items()}
    rows = _finalize_groups(aggs, tax_rate, "label", limit)

    dim = dims[dim_index]
    deeper = dim_index + 1 < len(dims)
    out = []
    for row in rows:
        node = nodes.get(row["label"])   # в label лежит ключ узла — см. _Node
        if node is None:
            continue
        row["sort_key"] = node.key
        row["label"] = node.label
        row["dim"] = dim.key
        row.update(node.extra)
        row["has_children"] = deeper
        row["children"] = (
            _finalize(node.children, dim_index + 1, dims, tax_rate, limit, levels_left - 1)
            if deeper and levels_left > 1
            else []
        )
        if node.nm_rev:
            top = sorted(node.nm_rev.items(), key=lambda kv: kv[1], reverse=True)
            row["nm_ids"] = [nm for nm, _ in top[:NM_SAMPLE]]
            row["nm_total"] = len(node.nm_rev)
        out.append(row)
    return out


async def get_funnel_tree(
    db: AsyncSession,
    pid: int,
    tax_info: dict,
    date_from: str | None,
    date_to: str | None,
    brand: str | None,
    subject: str | None,
    dims: list[str],
    bdr_rates_map: BdrRatesLookup | None = None,
    limit: int = MAX_CHILDREN,
    nm_ids: set[int] | None = None,
    vendor_code: str | None = None,
    path: list[str] | None = None,
    depth: int = 1,
) -> list[dict]:
    """Воронка деревом по произвольной цепочке измерений.

    dims — упорядоченный список ключей из DIMENSIONS: первый ключ даёт верхний
    уровень, последний — листья. Порядок задаёт форму дерева: «бренд → предмет»
    и «предмет → бренд» дают разные разрезы одних и тех же данных.

    path — ключи узлов-предков (по одному на первые len(path) измерений): строки
    отфильтруются по ним, а дерево соберётся от следующего уровня. depth — сколько
    уровней материализовать. Так раскрытие строки стоит один узкий запрос, а не
    выгрузку всего дерева (25 тыс. узлов ≈ 20 МБ на реальных данных).
    """
    chain = validate_chain(dims)
    limit = max(1, min(limit, MAX_CHILDREN))
    path = list(path or [])
    if len(path) >= len(chain):
        raise UnknownDimension("Путь длиннее цепочки группировок")
    depth = max(1, min(depth, len(chain) - len(path)))

    rows = await _load_funnel_rows(db, pid, date_from, date_to, brand, subject, nm_ids=nm_ids, vendor_code=vendor_code)
    ctx = await _build_context(db, pid, chain)

    tariff_map = await get_tariff_map(db, pid)
    buyout_map = await get_avg_buyout_map(db, pid)
    tax_rate = tax_info.get("usn_rate", 0) + tax_info.get("nds_rate", 0)
    has_bdr = bool(bdr_rates_map)

    # ABC зависит от самих строк периода, а не от справочника — считаем после загрузки
    if any(d.key == "abc" for d in chain):
        ctx["nm_abc"] = _build_abc_map(rows, has_bdr, bdr_rates_map, tariff_map, buyout_map, tax_info, tax_rate)

    ancestors = chain[: len(path)]      # уровни, по которым фильтруем
    build = chain[len(path): len(path) + depth]   # уровни, которые материализуем

    roots: dict[str, _Node] = {}
    for r in rows:
        # Отбор по пути: строка нужна, если на каждом уровне-предке её ключ совпал
        # с выбранным узлом (многозначные измерения дают несколько ключей на строку).
        if ancestors:
            skip = False
            for dim, want in zip(ancestors, path):
                if want not in (k for k, _ in dim.keys(r, ctx)):
                    skip = True
                    break
            if skip:
                continue

        # Многозначные измерения (ярлыки) размножают строку по веткам — как в
        # одноуровневой группировке по ярлыку, где товар с N ярлыками виден N раз.
        branches: list[list[tuple[str, str]]] = [[]]
        for dim in build:
            variants = dim.keys(r, ctx)
            branches = [p + [v] for p in branches for v in variants]

        for branch in branches:
            level = roots
            for lvl, (key, label) in enumerate(branch):
                node = level.get(key)
                if node is None:
                    node = _Node(key, label, has_bdr)
                    extra = build[lvl].extra
                    if extra:
                        node.extra = extra(r)
                    if build[lvl].collect_nm:
                        node.nm_rev = {}
                    level[key] = node
                if node.nm_rev is not None:
                    node.nm_rev[r.nm_id] = node.nm_rev.get(r.nm_id, 0.0) + float(r.orders_sum_rub or 0)
                _accumulate_row(node.agg, r, r.nm_id, bdr_rates_map, tariff_map, buyout_map, tax_info)
                level = node.children

    return _finalize(roots, len(path), chain, tax_rate, limit, depth)
