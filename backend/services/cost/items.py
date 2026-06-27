# ruff: noqa: RUF002, RUF003
"""
Cost — Cost Order Items (get, upload Excel, recalculate).
"""

from decimal import Decimal

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.etl.cost_parsers import detect_and_normalize_excel
from backend.models import CostOrder, CostOrderItem, DutyBasis, DutyException, DutyRule, Nomenclature
from backend.models.supply_chain import FactoryOrderItem
from backend.services.cost.helpers import DEFAULT_VAT_RATE, parse_box_volume_m3, safe_decimal
from backend.utils.time import utcnow


def _calc_duty_rub(
    basis,
    rate,
    *,
    cost_rub: Decimal,
    delivery_rub: Decimal,
    area_m2: Decimal | None,
    weight_kg: Decimal | None,
    rate_eur: Decimal,
) -> Decimal:
    """Per-unit duty in RUB for a given basis+rate.

    Shared by upload and recalculate paths, and by both category rules and
    article-level exceptions (which carry the same basis/rate shape).
    """
    rate = Decimal(str(rate))
    if basis == DutyBasis.WEIGHT:
        return (weight_kg or Decimal(0)) * rate * rate_eur
    if basis == DutyBasis.AREA:
        return (area_m2 or Decimal(0)) * rate * rate_eur
    if basis == DutyBasis.INVOICE:
        return (cost_rub + delivery_rub / 2) * rate / 100
    return Decimal(0)


async def get_cost_order_items(db: AsyncSession, project_id: int, order_no: str):
    """Get items with nomenclature lookup for article_wb."""
    # Verify order belongs to project_id (CostOrderItem has no project_id)
    order_check = await db.execute(
        select(CostOrder.id).where(
            CostOrder.order_no == order_no,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,
        )
    )
    if not order_check.scalar_one_or_none():
        return [], {}

    result = await db.execute(
        select(CostOrderItem)
        .where(CostOrderItem.order_no == order_no, CostOrderItem.is_deleted == False)
        .order_by(CostOrderItem.subject, CostOrderItem.article_seller)
    )
    items = result.scalars().all()

    barcodes = [i.barcode for i in items if i.barcode]
    nom_map = {}
    if barcodes:
        nom_result = await db.execute(
            select(Nomenclature).where(
                Nomenclature.project_id == project_id,
                Nomenclature.barcode.in_(barcodes),
            )
        )
        nom_map = {n.barcode: n.article_wb for n in nom_result.scalars().all()}

    return items, nom_map


async def upload_order_items(
    db: AsyncSession, project_id: int, order_no: str, data: bytes, vat_rate: Decimal | None = None
):
    """Upload Excel items, calculate cost/duty/delivery per unit. Returns (inserted, unrecognized)."""
    # Check order exists
    result = await db.execute(
        select(CostOrder).where(
            CostOrder.order_no == order_no, CostOrder.project_id == project_id, CostOrder.is_deleted == False
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        return None, None, f"Заказ {order_no} не найден"

    try:
        df = detect_and_normalize_excel(data)
    except ValueError as e:
        return None, None, str(e)

    # Re-upload replaces all items: soft-delete the existing active rows (Iron rule 3).
    # CostOrderItem has no unique constraint on (order_no, barcode), so re-inserting the
    # same barcodes below cannot collide with the soft-deleted ones. All read-paths filter
    # is_deleted == False, so the old rows become invisible while staying recoverable.
    await db.execute(
        update(CostOrderItem)
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.order_no == order_no,
            CostOrderItem.is_deleted == False,
        )
        .values(is_deleted=True, deleted_at=utcnow())
    )

    # Load nomenclature and duty rules
    nom_result = await db.execute(select(Nomenclature).where(Nomenclature.project_id == project_id))
    nom_map = {n.barcode: n for n in nom_result.scalars().all()}

    duty_result = await db.execute(
        select(DutyRule).where(DutyRule.project_id == project_id, DutyRule.is_deleted == False)
    )
    duty_map = {r.subject: r for r in duty_result.scalars().all()}

    # Article-level duty exceptions (override category basis+rate, keyed by article_seller)
    exc_result = await db.execute(
        select(DutyException).where(DutyException.project_id == project_id, DutyException.is_deleted == False)
    )
    exc_map = {e.article_seller: e for e in exc_result.scalars().all()}

    # Calculate totals for delivery split
    if "volume_m3" in df.columns and "qty" in df.columns:
        vol_series = pd.to_numeric(df["volume_m3"], errors="coerce").fillna(0)
        qty_series = pd.to_numeric(df["qty"], errors="coerce").fillna(1).astype(int)
        total_volume = float((vol_series * qty_series).sum())
    else:
        total_volume = 0.0
    total_qty_all = (
        int(pd.to_numeric(df.get("qty", 1), errors="coerce").fillna(1).sum()) if "qty" in df.columns else len(df)
    )
    delivery_rub_total = Decimal(str(order.delivery_cost_cny)) * Decimal(str(order.rate_cny)) + Decimal(
        str(order.delivery_cost_usd)
    ) * Decimal(str(order.rate_usd))

    inserted = 0
    unrecognized = 0
    vat_rate_dec = Decimal(str(vat_rate / 100)) if vat_rate else DEFAULT_VAT_RATE

    # Per-barcode metric propagation (see recalculate_order_items): a weight/area present
    # on one row applies to other rows of the same barcode that leave it blank, so
    # WEIGHT/AREA-basis duty is not undercounted when a barcode spans multiple rows.
    sib_weight: dict[str, Decimal] = {}
    sib_area: dict[str, Decimal] = {}
    for _, row in df.iterrows():
        bc0 = str(row.get("barcode", "")).strip()
        if not bc0 or bc0 == "nan" or bc0 == "0":
            continue
        w0 = safe_decimal(row.get("weight_kg", 0))
        if w0 and w0 > sib_weight.get(bc0, Decimal(0)):
            sib_weight[bc0] = w0
        a0 = safe_decimal(row.get("area_m2", 0))
        if a0 and a0 > sib_area.get(bc0, Decimal(0)):
            sib_area[bc0] = a0

    for _, row in df.iterrows():
        bc = str(row.get("barcode", "")).strip()
        if not bc or bc == "nan" or bc == "0":
            continue

        qty = int(row.get("qty", 1) or 1)
        price_cny = safe_decimal(row.get("price_cny", 0))
        weight_kg = safe_decimal(row.get("weight_kg", 0))
        area_m2 = safe_decimal(row.get("area_m2", 0))
        volume_m3 = safe_decimal(row.get("volume_m3", 0))

        nom = nom_map.get(bc)
        # Fallback: own value → sibling row (same barcode) → Nomenclature reference.
        if not area_m2:
            area_m2 = sib_area.get(bc) or (nom.area_m2 if nom and nom.area_m2 else None) or Decimal(0)
        if not weight_kg:
            weight_kg = sib_weight.get(bc) or (nom.weight_kg if nom and nom.weight_kg else None) or Decimal(0)
        subject = nom.subject if nom else None
        article_seller = nom.article_seller if nom else None
        is_unrecognized = nom is None
        if is_unrecognized:
            unrecognized += 1

        cost_rub_unit = price_cny * order.rate_cny

        if total_volume > 0 and volume_m3 > 0:
            delivery_rub_unit = delivery_rub_total * volume_m3 / Decimal(str(total_volume))
        elif total_qty_all > 0:
            delivery_rub_unit = delivery_rub_total / total_qty_all
        else:
            delivery_rub_unit = Decimal(0)

        duty_rub_unit = Decimal(0)
        util_rub_unit = Decimal(0)
        cat_rule = duty_map.get(subject) if subject else None
        # Утиль-сбор всегда из правила категории (исключение его не переопределяет).
        if cat_rule:
            util_rub_unit = cat_rule.util_collect_rub
        # Пошлина: исключение по артикулу важнее правила категории.
        duty_src = (exc_map.get(article_seller) if article_seller else None) or cat_rule
        if duty_src is not None:
            duty_rub_unit = _calc_duty_rub(
                duty_src.basis,
                duty_src.rate,
                cost_rub=cost_rub_unit,
                delivery_rub=delivery_rub_unit,
                area_m2=area_m2,
                weight_kg=weight_kg,
                rate_eur=order.rate_eur,
            )

        vat_base = cost_rub_unit + duty_rub_unit + delivery_rub_unit / 2
        vat_rub_unit = vat_base * vat_rate_dec

        total_rub_unit = cost_rub_unit + delivery_rub_unit + duty_rub_unit + vat_rub_unit + util_rub_unit
        total_cny_unit = total_rub_unit / order.rate_cny if order.rate_cny > 0 else Decimal(0)

        item = CostOrderItem(
            project_id=project_id,
            order_no=order_no,
            barcode=bc,
            subject=subject,
            article_seller=article_seller,
            qty=qty,
            price_cny=price_cny,
            weight_kg=weight_kg,
            area_m2=area_m2,
            volume_m3=volume_m3,
            cost_rub=cost_rub_unit,
            delivery_rub=delivery_rub_unit,
            duty_rub=duty_rub_unit,
            vat_rub=vat_rub_unit,
            util_rub=util_rub_unit,
            total_rub=total_rub_unit,
            total_cny=total_cny_unit,
            unrecognized=is_unrecognized,
        )
        db.add(item)
        inserted += 1

    await db.commit()
    return inserted, unrecognized, None


async def recalculate_order_items(db: AsyncSession, project_id: int, order_no: str, vat_rate: Decimal | None = None):
    """Recalculate cost/duty/vat/delivery for existing items in an order."""
    result = await db.execute(
        select(CostOrder).where(
            CostOrder.order_no == order_no, CostOrder.project_id == project_id, CostOrder.is_deleted == False
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        return None, f"Заказ {order_no} не найден"

    # Load items
    items_result = await db.execute(
        select(CostOrderItem).where(CostOrderItem.order_no == order_no, CostOrderItem.is_deleted == False)
    )
    items = items_result.scalars().all()
    if not items:
        return 0, None

    # Load nomenclature and duty rules
    nom_result = await db.execute(select(Nomenclature).where(Nomenclature.project_id == project_id))
    nom_map = {n.barcode: n for n in nom_result.scalars().all()}

    duty_result = await db.execute(
        select(DutyRule).where(DutyRule.project_id == project_id, DutyRule.is_deleted == False)
    )
    duty_map = {r.subject: r for r in duty_result.scalars().all()}

    # Article-level duty exceptions (override category basis+rate, keyed by article_seller)
    exc_result = await db.execute(
        select(DutyException).where(DutyException.project_id == project_id, DutyException.is_deleted == False)
    )
    exc_map = {e.article_seller: e for e in exc_result.scalars().all()}

    # Always recompute volume_m3 from effective box dimensions (override > FO mix > FO).
    # Items without FO link and without override keep their existing volume_m3 (e.g. Excel-uploaded).
    try:
        fo_ids = [item.factory_order_item_id for item in items if item.factory_order_item_id]
        fo_box_map: dict[int, tuple] = {}
        if fo_ids:
            fo_result = await db.execute(
                select(
                    FactoryOrderItem.id,
                    FactoryOrderItem.box_size,
                    FactoryOrderItem.pcs_per_box,
                    FactoryOrderItem.mix_group_id,
                    FactoryOrderItem.mix_box_size,
                    FactoryOrderItem.mix_pcs_per_box,
                ).where(FactoryOrderItem.id.in_(fo_ids))
            )
            for row in fo_result:
                fo_box_map[row[0]] = (row[1], row[2], row[3], row[4], row[5])

        for item in items:
            has_override = bool(item.box_size_override or item.pcs_per_box_override)
            if not item.factory_order_item_id and not has_override:
                continue
            fo_bs = None
            fo_ppb = None
            if item.factory_order_item_id:
                fo_data = fo_box_map.get(item.factory_order_item_id)
                if fo_data:
                    fo_bs = fo_data[3] if fo_data[2] and fo_data[3] else fo_data[0]
                    fo_ppb = fo_data[4] if fo_data[2] and fo_data[4] else fo_data[1]
            effective_bs = item.box_size_override or fo_bs
            effective_ppb = item.pcs_per_box_override or fo_ppb
            vol = parse_box_volume_m3(effective_bs, effective_ppb)
            if vol > 0:
                item.volume_m3 = vol
    except Exception:
        import logging

        logging.getLogger("dds").warning("Failed to recompute volume_m3, using existing values", exc_info=True)

    # Calculate total volume for delivery split
    total_volume = sum(float(item.volume_m3 or 0) * int(item.qty or 1) for item in items)
    total_qty_all = sum(int(item.qty or 1) for item in items)

    # For Russian vehicles we use flat RUB delivery and skip duty/vat/util entirely.
    # For China, delivery_rub is derived from CNY/USD legs at the order's FX rates.
    is_russia = getattr(order, "country", "CHINA") == "RUSSIA"
    if is_russia:
        delivery_rub_total = Decimal(str(order.delivery_cost_rub or 0))
    else:
        delivery_rub_total = Decimal(str(order.delivery_cost_cny)) * Decimal(str(order.rate_cny)) + Decimal(
            str(order.delivery_cost_usd)
        ) * Decimal(str(order.rate_usd))

    vat_rate_dec = Decimal(str(vat_rate / 100)) if vat_rate else DEFAULT_VAT_RATE
    updated = 0

    # Per-barcode metric propagation. A barcode can arrive from several factory-order
    # sources (separate CostOrderItem rows). weight_kg/area_m2 is a per-unit property
    # of the SKU, so a value declared on ONE source applies to sibling sources that
    # left it blank. Without this, WEIGHT/AREA-basis duty on the blank rows computes to
    # 0 and the barcode's duty is silently undercounted (halved with two sources, etc).
    # Same invariant the factory-order merge enforces (один баркод ⇒ один вес).
    sib_weight: dict[str, Decimal] = {}
    sib_area: dict[str, Decimal] = {}
    for it in items:
        if not it.barcode:
            continue
        if it.weight_kg and it.weight_kg > 0:
            w = Decimal(str(it.weight_kg))
            if w > sib_weight.get(it.barcode, Decimal(0)):
                sib_weight[it.barcode] = w
        if it.area_m2 and it.area_m2 > 0:
            a = Decimal(str(it.area_m2))
            if a > sib_area.get(it.barcode, Decimal(0)):
                sib_area[it.barcode] = a

    for item in items:
        # Self-heal предмет/артикул из номенклатуры, если снимок пуст. Новый SKU мог
        # попасть в машину до появления карточки ВБ (subject/article_seller легли NULL);
        # карточку подтянул суточный синк позже — без этого предмет/артикул и пошлина
        # навсегда остаются пустыми (recalc матчит правило пошлины по subject). Уже
        # заполненный снимок (ручная правка / значение из заказа) не перетираем.
        nom_meta = nom_map.get(item.barcode)
        if nom_meta:
            if not item.subject and nom_meta.subject:
                item.subject = nom_meta.subject
            if not item.article_seller and nom_meta.article_seller:
                item.article_seller = nom_meta.article_seller

        # For Russia, price_cny is stored in RUB (rate_cny is forced to 1 anyway)
        cost_rub_unit = Decimal(str(item.price_cny or 0)) if is_russia else item.price_cny * order.rate_cny

        vol = Decimal(str(float(item.volume_m3 or 0)))
        if total_volume > 0 and vol > 0:
            delivery_rub_unit = delivery_rub_total * vol / Decimal(str(total_volume))
        elif total_qty_all > 0:
            delivery_rub_unit = delivery_rub_total / total_qty_all
        else:
            delivery_rub_unit = Decimal(0)

        duty_rub_unit = Decimal(0)
        util_rub_unit = Decimal(0)
        vat_rub_unit = Decimal(0)

        if not is_russia:
            subject = item.subject
            # Effective metric: own value → sibling source (same barcode) → Nomenclature.
            # A metric declared on a sibling source is real shipment data and outranks
            # the generic Nomenclature reference.
            nom = nom_map.get(item.barcode)
            area_m2 = item.area_m2
            if not area_m2:
                area_m2 = sib_area.get(item.barcode) or (nom.area_m2 if nom and nom.area_m2 else None)
            weight_kg = item.weight_kg
            if not weight_kg:
                weight_kg = sib_weight.get(item.barcode) or (nom.weight_kg if nom and nom.weight_kg else None)
            cat_rule = duty_map.get(subject) if subject else None
            # Утиль-сбор всегда из правила категории (исключение его не переопределяет).
            if cat_rule:
                util_rub_unit = cat_rule.util_collect_rub
            # Пошлина: исключение по артикулу важнее правила категории.
            duty_src = (exc_map.get(item.article_seller) if item.article_seller else None) or cat_rule
            if duty_src is not None:
                duty_rub_unit = _calc_duty_rub(
                    duty_src.basis,
                    duty_src.rate,
                    cost_rub=cost_rub_unit,
                    delivery_rub=delivery_rub_unit,
                    area_m2=area_m2,
                    weight_kg=weight_kg,
                    rate_eur=order.rate_eur,
                )

            vat_base = cost_rub_unit + duty_rub_unit + delivery_rub_unit / 2
            vat_rub_unit = vat_base * vat_rate_dec

        total_rub_unit = cost_rub_unit + delivery_rub_unit + duty_rub_unit + vat_rub_unit + util_rub_unit

        if is_russia:
            # rate_cny is forced to 1 on RU orders, so CNY == RUB
            total_cny_unit = total_rub_unit
        else:
            total_cny_unit = total_rub_unit / order.rate_cny if order.rate_cny > 0 else Decimal(0)

        item.cost_rub = cost_rub_unit
        item.delivery_rub = delivery_rub_unit
        item.duty_rub = duty_rub_unit
        item.vat_rub = vat_rub_unit
        item.util_rub = util_rub_unit
        item.total_rub = total_rub_unit
        item.total_cny = total_cny_unit
        updated += 1

    await db.commit()
    return updated, None
