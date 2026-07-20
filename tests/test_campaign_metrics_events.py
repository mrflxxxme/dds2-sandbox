"""События посуточных метрик кампании: смена цены, остановка по бюджету, пауза/запуск.

Событие объясняет ИЗЛОМ в цифрах дня (почему CTR/ДРР скакнули), поэтому вешается на
поздний день пары и рисуется разделителем под ним. Плюс флаг `is_partial` у сегодняшнего
дня — его цифры неполные, сравнивать с прошлым нельзя.
"""

from datetime import date, datetime, timedelta

import pytz

from backend.models import WbAdCampaign, WbAdCampaignEvent, WbFunnelDaily
from backend.models.integrations import WbAdCampaignDaily
from backend.services.funnel import ads_manager as am
from backend.utils.time import msk_today

CID = 778200
NM = 5501

MSK = pytz.timezone("Europe/Moscow")


def _utc_at(d: date, hour: int, minute: int = 0) -> datetime:
    """МСК-момент дня → naive UTC (как хранит WbAdCampaignEvent.created_at)."""
    return MSK.localize(datetime(d.year, d.month, d.day, hour, minute)).astimezone(pytz.UTC).replace(tzinfo=None)


async def _seed_campaign(db, project_id):
    db.add(WbAdCampaign(project_id=project_id, campaign_id=CID, name="Стандарт 180x200",
                        campaign_type="cpm", status=9, nm_ids=[NM]))
    await db.commit()


async def _seed_day(db, project_id, d: date, *, price: float, spend: float = 1000.0, orders: int = 10):
    db.add(WbAdCampaignDaily(project_id=project_id, campaign_id=CID, date=d,
                             views=5000, clicks=250, spend=spend))
    db.add(WbFunnelDaily(project_id=project_id, nm_id=NM, date=d, open_card=1000, add_to_cart=80,
                         orders_count=orders, orders_sum_rub=price * orders, avg_price=price))


# ─── Смена цены (чистая функция) ─────────────────────────────────────────────

def test_price_event_on_later_day():
    """Событие принадлежит дню, чьи цифры УЖЕ про новую цену."""
    ev = am._price_change_events(
        [(date(2026, 6, 27) + timedelta(days=i), p, None)
         for i, p in enumerate([2563.0, 2563.0, 2563.0, 2080.0, 2080.0, 2080.0])])
    assert len(ev) == 1
    assert ev[0]["date"] == "2026-06-30"
    assert ev[0]["kind"] == "price"
    assert "2 563" in ev[0]["text"].replace(" ", " ")
    assert "-19%" in ev[0]["text"]


def test_price_event_ignores_rounding_noise():
    """Дрейф средней цены на доли процента — не событие."""
    assert am._price_change_events(
        [(date(2026, 7, 1) + timedelta(days=i), p, None)
         for i, p in enumerate([2080.0, 2081.5, 2079.0, 2080.5, 2081.0, 2080.0])]) == []


def test_price_event_survives_day_without_sales():
    """День без заказов (цены нет) не разрывает ряд — иначе он маскирует смену цены."""
    ev = am._price_change_events([
        (date(2026, 7, 1), 2563.0, None), (date(2026, 7, 2), 2563.0, None),
        (date(2026, 7, 3), 2563.0, None), (date(2026, 7, 4), None, None),
        (date(2026, 7, 5), 2080.0, None), (date(2026, 7, 6), 2080.0, None),
        (date(2026, 7, 7), 2080.0, None),
    ])
    assert [e["date"] for e in ev] == ["2026-07-05"]


def test_price_event_up_is_signed():
    ev = am._price_change_events(
        [(date(2026, 7, 1) + timedelta(days=i), p, None)
         for i, p in enumerate([2080.0, 2080.0, 2080.0, 2563.0, 2563.0, 2563.0])])
    assert "+23%" in ev[0]["text"]


# ─── Кто дал скидку: мы или маркетплейс ──────────────────────────────────────

def test_spp_change_is_separate_kind():
    """Цена клиенту упала, а наша цена не менялась — это скидка ВБ, не наша."""
    ev = am._price_change_events(
        [(date(2026, 7, 6) + timedelta(days=i), 2080.0, v)
         for i, v in enumerate([35.5, 35.5, 35.5, 39.2, 39.2, 39.2])])
    assert len(ev) == 1
    assert ev[0]["kind"] == "spp"
    assert "СПП" in ev[0]["text"]
    assert "клиенту" in ev[0]["text"]  # видно, во что это вылилось для покупателя


def test_one_day_spike_is_not_an_event():
    """СПП дёрнулся на день и вернулся — ВБ ничего не решал, день просто выбился.

    До сглаживания это давало ДВА события подряд («33% → 30%» и назавтра «30% → 33%»),
    и на полугодовом окне такие пары шли пачками.
    """
    days = [(date(2026, 5, 1) + timedelta(days=i)) for i in range(7)]
    spp = [33.0, 33.0, 30.0, 33.0, 33.0, 33.0, 33.0]  # выброс на третий день
    assert am._price_change_events([(d, 2080.0, v) for d, v in zip(days, spp)]) == []


def test_sustained_shift_is_an_event_on_the_day_it_moved():
    """Уровень переехал и остался — событие есть, и стоит на ПЕРВОМ дне нового уровня."""
    days = [(date(2026, 7, 6) + timedelta(days=i)) for i in range(7)]
    spp = [36.0, 36.0, 36.0, 41.0, 41.0, 40.0, 41.0]  # переезд на четвёртый день
    ev = am._price_change_events([(d, 2080.0, v) for d, v in zip(days, spp)])
    assert len(ev) == 1
    assert ev[0]["kind"] == "spp"
    assert ev[0]["date"] == "2026-07-09"  # days[3]


def test_chained_price_cuts_keep_their_own_dates():
    """Цену снижали шагами несколько дней подряд — каждый шаг на своём дне.

    Окно пропуска после предыдущего события съедало настоящий день перехода, и
    разделитель уезжал на сутки вперёд (видно на живых данных: −4% вставало на 25.06,
    хотя новая цена стояла с 24.06).
    """
    days = [(date(2026, 6, 20) + timedelta(days=i)) for i in range(10)]
    prices = [3134.0, 3134.0, 3134.0, 2807.0, 2698.0, 2698.0, 2698.0, 2698.0, 2698.0, 2698.0]
    ev = am._price_change_events([(d, p, None) for d, p in zip(days, prices)])
    dates = [e["date"] for e in ev if e["kind"] == "price"]
    assert "2026-06-23" in dates  # 3134 → 2807
    assert "2026-06-24" in dates  # 2807 → 2698


def test_spp_daily_drift_is_not_an_event():
    """СПП шевелится на доли пункта почти каждый день — это не решение ВБ, а шум."""
    assert am._price_change_events(
        [(date(2026, 7, 1) + timedelta(days=i), 2080.0, v)
         for i, v in enumerate([36.9, 35.5, 36.4, 36.1, 36.8, 35.9])]) == []


def test_our_price_and_spp_events_coexist():
    """В один день могли поменяться оба — показываем оба, они требуют разной реакции."""
    ev = am._price_change_events(
        [(date(2026, 6, 27) + timedelta(days=i), p, v) for i, (p, v) in enumerate(
            [(2563.0, 36.0), (2563.0, 36.0), (2563.0, 36.0),
             (2080.0, 41.0), (2080.0, 41.0), (2080.0, 41.0)])])
    assert {e["kind"] for e in ev} == {"price", "spp"}


# ─── Сбор событий из БД ──────────────────────────────────────────────────────

async def test_metrics_events_price_and_budget(db_session, project):
    """Смена цены и остановка по бюджету попадают в ответ метрик."""
    await _seed_campaign(db_session, project.id)
    d1, d2 = date(2026, 6, 29), date(2026, 6, 30)
    await _seed_day(db_session, project.id, d1, price=2563.0)
    await _seed_day(db_session, project.id, d2, price=2080.0)
    # Бюджет кончился 30.06 в 18:40 МСК
    db_session.add(WbAdCampaignEvent(project_id=project.id, campaign_id=CID, event_type="budget_change",
                                     old_value="500", new_value="0", created_at=_utc_at(d2, 18, 40)))
    await db_session.commit()

    res = await am.get_campaign_metrics(db_session, project.id, CID,
                                        date_from=d1.isoformat(), date_to=d2.isoformat())
    kinds = {e["kind"]: e for e in res["events"]}
    assert kinds["price"]["date"] == "2026-06-30"
    assert kinds["budget"]["date"] == "2026-06-30"
    assert "18:40" in kinds["budget"]["text"]
    # Короткая подпись — она идёт прямо в метку дня, полный текст остаётся в подсказке
    assert (kinds["budget"]["short"], kinds["budget"]["value"]) == ("стоп", "18:40")
    assert (kinds["price"]["short"], kinds["price"]["value"]) == ("цена", "-19%")


async def test_budget_topup_after_runout_clears_event(db_session, project):
    """Пополнение после обнуления — день не считается остановленным (зеркало _runout_by_day)."""
    await _seed_campaign(db_session, project.id)
    d = date(2026, 6, 30)
    await _seed_day(db_session, project.id, d, price=2080.0)
    db_session.add(WbAdCampaignEvent(project_id=project.id, campaign_id=CID, event_type="budget_change",
                                     old_value="500", new_value="0", created_at=_utc_at(d, 14, 0)))
    db_session.add(WbAdCampaignEvent(project_id=project.id, campaign_id=CID, event_type="budget_change",
                                     old_value="0", new_value="3000", created_at=_utc_at(d, 15, 0)))
    await db_session.commit()

    res = await am.get_campaign_metrics(db_session, project.id, CID,
                                        date_from=d.isoformat(), date_to=d.isoformat())
    assert [e for e in res["events"] if e["kind"] == "budget"] == []


async def test_status_changes_are_not_events(db_session, project):
    """Пауза/запуск не попадают в события вовсе.

    У кампаний с «Паузой по расписанию» статус меняется через день (до 92 дней из 180 на
    живых данных) — метка стояла бы почти всюду, ничего не объясняя. Сам простой и так
    виден по нулевым показам строки.
    """
    await _seed_campaign(db_session, project.id)
    d = date(2026, 7, 2)
    await _seed_day(db_session, project.id, d, price=2080.0)
    db_session.add(WbAdCampaignEvent(project_id=project.id, campaign_id=CID, event_type="status_change",
                                     old_value="9", new_value="11", created_at=_utc_at(d, 10, 0)))
    await db_session.commit()

    res = await am.get_campaign_metrics(db_session, project.id, CID,
                                        date_from=d.isoformat(), date_to=d.isoformat())
    assert [e for e in res["events"] if e["kind"] == "status"] == []


async def test_events_scoped_to_project(db_session, project, other_project):
    """События чужого проекта в ответ не попадают (та же campaign_id)."""
    await _seed_campaign(db_session, project.id)
    d = date(2026, 7, 3)
    await _seed_day(db_session, project.id, d, price=2080.0)
    db_session.add(WbAdCampaignEvent(project_id=other_project.id, campaign_id=CID, event_type="budget_change",
                                     old_value="500", new_value="0", created_at=_utc_at(d, 9, 0)))
    await db_session.commit()

    res = await am.get_campaign_metrics(db_session, project.id, CID,
                                        date_from=d.isoformat(), date_to=d.isoformat())
    assert [e for e in res["events"] if e["kind"] == "budget"] == []


# ─── Неполный день ───────────────────────────────────────────────────────────

async def test_today_row_is_partial(db_session, project):
    """Сегодняшний день помечен is_partial, вчерашний — нет."""
    await _seed_campaign(db_session, project.id)
    today = msk_today()
    yday = today - timedelta(days=1)
    await _seed_day(db_session, project.id, yday, price=2080.0)
    await _seed_day(db_session, project.id, today, price=2080.0, spend=400.0, orders=4)
    await db_session.commit()

    res = await am.get_campaign_metrics(db_session, project.id, CID,
                                        date_from=yday.isoformat(), date_to=today.isoformat())
    by_date = {r["date"]: r for r in res["rows"]}
    assert by_date[today.isoformat()]["is_partial"] is True
    assert by_date[yday.isoformat()]["is_partial"] is False
    assert res["totals"]["is_partial"] is False  # итог не «сегодня»


async def test_spp_absent_means_no_customer_price(db_session, project):
    """Нет СПП за день → «Цена Клиенту» пустая, а не равна полной цене.

    Отчёт «Заказы» приходит с лагом, у сегодняшнего дня СПП обычно ещё нет. Ноль вместо
    None означал бы «ВБ скидку не дал» и показывал бы покупателю полную цену — то есть
    врал бы ровно в ту сторону, ради которой колонку и смотрят.
    """
    await _seed_campaign(db_session, project.id)
    d = date(2026, 7, 4)
    await _seed_day(db_session, project.id, d, price=2080.0)
    await db_session.commit()

    # bdr_rates_map не передан — СПП за день неизвестен
    res = await am.get_campaign_metrics(db_session, project.id, CID,
                                        date_from=d.isoformat(), date_to=d.isoformat())
    row = res["rows"][0]
    assert row["spp"] is None
    assert row["customer_price"] is None
    assert row["avg_price"] == 2080.0  # наша цена известна и осталась на месте


def test_every_event_carries_short_label():
    """У каждого события есть подпись для метки дня — фронт её не выводит из текста."""
    days = [(date(2026, 6, 27) + timedelta(days=i)) for i in range(6)]
    ev = am._price_change_events(
        [(d, p, v) for d, (p, v) in zip(days, [
            (2563.0, 36.0), (2563.0, 36.0), (2563.0, 36.0),
            (2080.0, 41.0), (2080.0, 41.0), (2080.0, 41.0)])])
    assert {e["kind"] for e in ev} == {"price", "spp"}
    by_kind = {e["kind"]: e for e in ev}
    assert (by_kind["price"]["short"], by_kind["price"]["value"]) == ("цена", "-19%")
    assert (by_kind["spp"]["short"], by_kind["spp"]["value"]) == ("СПП", "+5 п.п.")


def test_direction_marks_sign_of_change():
    """`dir` задаёт цвет цифры в метке: вниз — красный, вверх — зелёный, час — без цвета."""
    days = [(date(2026, 5, 8) + timedelta(days=i)) for i in range(6)]
    down = am._price_change_events(
        [(d, p, None) for d, p in zip(days, [3134.0, 3134.0, 3134.0, 2160.0, 2160.0, 2160.0])])
    up = am._price_change_events(
        [(d, p, None) for d, p in zip(days, [2160.0, 2160.0, 2160.0, 3134.0, 3134.0, 3134.0])])
    assert down[0]["dir"] == -1 and down[0]["value"].startswith("-")
    assert up[0]["dir"] == 1 and up[0]["value"].startswith("+")


# ─── Простой дня и обнуление с доливом ───────────────────────────────────────

def test_idle_marks_days_that_barely_ran():
    """Показов в разы меньше обычного — день не в счёт, помечаем."""
    days = [date(2026, 7, 1) + timedelta(days=i) for i in range(8)]
    views = [6400, 6500, 6300, 201, 155, 58, 6600, 6400]
    assert am._idle_days(list(zip(days, views)), set()) == {
        date(2026, 7, 4), date(2026, 7, 5), date(2026, 7, 6),
    }


def test_idle_ignores_zero_days():
    """Ровный ноль не метим — там вся строка нулевая и без метки видно."""
    days = [date(2026, 7, 1) + timedelta(days=i) for i in range(6)]
    assert am._idle_days(list(zip(days, [6400, 6500, 0, 0, 6300, 6400])), set()) == set()


def test_idle_does_not_fire_on_working_days():
    """Регресс: журнал статусов у divan_lightgrey показывал «пауза» 05–07.07, тогда как
    в эти дни кампания откручивала 12–22 тыс. показов. Считаем по показам, и ложных
    меток на рабочих днях нет."""
    days = [date(2026, 7, 5) + timedelta(days=i) for i in range(4)]
    views = [12587, 22896, 12736, 18710]
    assert am._idle_days(list(zip(days, views)), set()) == set()


def test_idle_skips_days_already_explained_by_budget():
    """Если день уже помечен «стоп»/«простой N ч», второй значок про то же не нужен."""
    days = [date(2026, 7, 1) + timedelta(days=i) for i in range(6)]
    views = [6400, 6500, 6300, 50, 6600, 6400]
    assert am._idle_days(list(zip(days, views)), {date(2026, 7, 4)}) == set()


def test_intraday_gap_when_budget_refilled(project):
    """Бюджет кончился и его долили — день докрутил, но кампания стояла кусок дня."""
    d = date(2026, 7, 9)
    rows = [
        WbAdCampaignEvent(project_id=project.id, campaign_id=CID, event_type="budget_change",
                          old_value="500", new_value="0", created_at=_utc_at(d, 9, 15)),
        WbAdCampaignEvent(project_id=project.id, campaign_id=CID, event_type="budget_change",
                          old_value="0", new_value="3000", created_at=_utc_at(d, 11, 13)),
    ]
    gaps = am._intraday_budget_gaps(rows, skip_days=set())
    assert set(gaps) == {d}
    zero_at, back_at = gaps[d]
    assert am._fmt_span((back_at - zero_at).total_seconds() / 60) == "1 ч 58 мин"


def test_gap_not_reported_when_day_ended_at_zero(project):
    """День закончился на нуле — там метка «стоп», дублировать «простой» незачем."""
    d = date(2026, 7, 9)
    rows = [
        WbAdCampaignEvent(project_id=project.id, campaign_id=CID, event_type="budget_change",
                          old_value="500", new_value="0", created_at=_utc_at(d, 9, 15)),
    ]
    assert am._intraday_budget_gaps(rows, skip_days={d}) == {}


def test_fmt_span_forms():
    assert am._fmt_span(40) == "40 мин"
    assert am._fmt_span(120) == "2 ч"
    assert am._fmt_span(200) == "3 ч 20 мин"
