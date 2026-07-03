/**
 * Точная геометрия смешанной паллеты для вкладки «Предбронь».
 *
 * Показ предброни (`fillPct`, «нужно N кор», «соберётся M паллет») и дозабор
 * ОБЯЗАНЫ мерить вместимость так же, как авторитет — `snapToWholePallets`
 * (`trimLinesToWholePallets`), решающий, что уедет в черновик, а что осталось в
 * предброни. Тот считает footprint смешанной паллеты как **Σ qty_i / upp_i** —
 * КАЖДЫЙ SKU по своей геометрии короба. Прежний показ брал вместимость по ОДНОМУ
 * репрезентативному SKU (`Σ qty / upp_rep`), что размерно неверно для смешанной
 * паллеты: на живых данных бар/числа врали до 3× (0.78 паллеты показывались как 2.5).
 *
 * Чистые функции — полностью юнит-тестируются против `snapToWholePallets`.
 */

/** Истинный footprint (в паллетах) смешанной BOX-поставки одной отгрузки ФФ→WB.
 *  `uppOf(nmId)` — штук в ПОЛНОЙ паллете SKU на складе-цели (boxesPerPallet×ppb),
 *  0/≤0 — без габаритов (в footprint не входит). Зеркало `snapToWholePallets.fill`. */
export function palletFootprint(
    items: { nmId: number; qty: number }[],
    uppOf: (nmId: number) => number,
): number {
    let fp = 0;
    for (const it of items) {
        const upp = uppOf(it.nmId);
        if (upp > 0 && it.qty > 0) fp += it.qty / upp;
    }
    return fp;
}

/** Направление предброни идёт в под-вкладку «Предзаявка» (а не «Дозабить»), если склад
 *  БЕЗ лимита приёмки (⌛ noLimit). Нет данных / лимит есть → «Дозабить» (не теряем карточку). */
export function isPrebookingByLimit(mark?: { noLimit?: boolean } | null): boolean {
    return mark?.noLimit === true;
}

/** Кандидат дозабора: свободные целые коробы box-SKU на конкретном ФФ. */
export interface TopUpCandidate {
    nmId: number;
    /** boxesPerPallet кандидата на складе-цели. null/≤0 — не палетизируется там. */
    bpp: number | null;
    /** Кратность короба (шт/короб). */
    ppb: number;
    /** Свободных ЦЕЛЫХ коробов на этом ФФ. */
    freeBoxes: number;
}

export interface TopUpPlanRow {
    nmId: number;
    boxes: number;
    units: number;
}

export interface TopUpPlan {
    /** Что и сколько добрать (целыми коробами). */
    rows: TopUpPlanRow[];
    /** Суммарно коробов к добору. */
    needBoxes: number;
    /** Закрытая планом доля паллеты (≈ shortfall, если хватило кандидатов). */
    filledFootprint: number;
    /** Хватило ли кандидатов закрыть shortfall до целой паллеты. */
    feasible: boolean;
}

/**
 * Набрать МИНИМУМ целых коробов кандидатов, чтобы закрыть `shortfallFootprint`
 * (доля паллеты до целой). Каждый короб SKU занимает `1/bpp` паллеты. Жадно в
 * порядке, заданном caller'ом (обычно — крупнейший свободный запас первым).
 *
 * Возвращает план (коробов на SKU) — им же грузит и показ («нужно N кор»), и
 * реальный дозабор (`handleTopUpDirection`): числа на карточке = что уедет.
 */
export function planTopUpBoxes(
    shortfallFootprint: number,
    candidates: TopUpCandidate[],
): TopUpPlan {
    const EPS = 1e-9;
    let rem = Math.max(0, shortfallFootprint);
    const rows: TopUpPlanRow[] = [];
    let needBoxes = 0;
    for (const c of candidates) {
        if (rem <= EPS) break;
        if (c.bpp == null || c.bpp <= 0 || c.freeBoxes <= 0 || c.ppb <= 0) continue;
        const boxFp = 1 / c.bpp; // доля паллеты за один короб этого SKU
        const want = Math.min(c.freeBoxes, Math.ceil(rem / boxFp - EPS));
        if (want <= 0) continue;
        rows.push({ nmId: c.nmId, boxes: want, units: want * c.ppb });
        needBoxes += want;
        rem -= want * boxFp;
    }
    return {
        rows,
        needBoxes,
        filledFootprint: Math.max(0, shortfallFootprint) - Math.max(0, rem),
        feasible: rem <= EPS,
    };
}
