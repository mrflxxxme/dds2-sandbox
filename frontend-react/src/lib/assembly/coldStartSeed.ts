/**
 * Засев новинок (cold-start) ЦЕЛЫМИ коробами по главным складам округов — с учётом
 * УЖЕ имеющегося покрытия (остаток на WB + в сборке + в пути), чтобы не перетарить.
 *
 * По требованию пользователя: новинку лучше разложить целыми коробами по топ-складам
 * округов (по доле спроса), чем держать на ФФ — НО если на складе уже лежит/едет
 * примерно столько же, туда слать не нужно. Логика:
 *   1. idealShare_i = доля_округа × остаток_к_засеву  — сколько пришлось бы на склад;
 *   2. shortfall_i = max(0, idealShare_i − уже_на_складе_i)  — за вычетом покрытия
 *      (остаток WB + в сборке + в пути); перетаренный склад → 0 (не слать);
 *   3. режем Σshortfall в целые коробы `ppb` и раскладываем по складам пропорционально
 *      shortfall (largest-remainder).
 * Хвост < короба и доля перетаренных складов остаются на ФФ.
 *
 * Используется в «Потребности по складам» (новинки) и в предраспределении машины.
 * Чистая функция — полностью юнит-тестируется.
 */

export interface SeedAnchor {
    /** Имя WB-склада-анкера округа. */
    warehouse: string;
    /** Доля спроса округа (%). Может быть 0. */
    share_pct: number;
    /** Уже на этом складе/едет/в сборке (остаток WB + assembly + transit). Вычитается. */
    existing?: number;
    /** Ключ ФО анкера (district_key из cold_start_table.main_warehouses). Нужен для
     *  гарантии СЗФО — без него гарантия просто не срабатывает (поведение прежнее). */
    district?: string;
}

/** Гарантия СЗФО: при партии ≥ этого числа коробов открытый непокрытый якорь
 *  СЗФО получает минимум 1 короб (short-доля СЗФО ~9% проигрывала largest-remainder
 *  крупным ФО до ~6-8 коробов → новинки системно не заезжали в СЗФО и его
 *  локализация не набиралась; порог согласован с пользователем — аудит 2026-07-09). */
export const NW_GUARANTEE_MIN_BOXES = 4;
const NW_DISTRICT_KEY = 'northwest';

export function seedNewcomerWholeBoxes(
    totalQty: number,
    ppb: number | null | undefined,
    anchors: SeedAnchor[],
): Record<string, number> {
    const k = ppb && ppb > 0 ? Math.floor(ppb) : 0;
    const qty = Math.max(0, Math.floor(Number(totalQty) || 0));
    if (k <= 0 || qty < k) return {};

    // Уникальные анкеры; доли/покрытие коэрсим в число.
    const seen = new Set<string>();
    const open: { warehouse: string; share: number; existing: number; district?: string }[] = [];
    for (const a of anchors) {
        if (!a.warehouse || seen.has(a.warehouse)) continue;
        seen.add(a.warehouse);
        open.push({
            warehouse: a.warehouse,
            share: Math.max(0, Number(a.share_pct) || 0),
            existing: Math.max(0, Math.floor(Number(a.existing) || 0)),
            district: a.district,
        });
    }
    if (open.length === 0) return {};

    const shareSum = open.reduce((s, a) => s + a.share, 0);

    // Сколько из НАШЕГО остатка пришлось бы на склад по доле спроса, МИНУС уже
    // имеющееся там (остаток WB + едет + в сборке). Перетаренный склад (existing ≥
    // его доли) → 0 (туда не слать). Хвост остаётся на ФФ.
    const shortfalls = open.map(a => {
        const idealShare = shareSum > 0 ? qty * (a.share / shareSum) : qty / open.length;
        return { wh: a.warehouse, sf: Math.max(0, idealShare - a.existing), district: a.district };
    });
    const sfSum = shortfalls.reduce((s, x) => s + x.sf, 0);
    if (sfSum <= 0) return {}; // всё покрыто → ничего не слать (не перетаривать)

    const shipTotal = Math.min(qty, sfSum);
    // +eps гасит float-дребезг суммы долей (Σ idealShare = qty математически, но в
    // double может дать qty−1e-13 → floor срезал бы целый короб).
    const totalBoxes = Math.floor(shipTotal / k + 1e-6);
    if (totalBoxes <= 0) return {};

    // Целые коробы по складам пропорционально shortfall (largest-remainder).
    const ranked = [...shortfalls].sort((a, b) => b.sf - a.sf);
    const ideal = ranked.map(x => {
        const want = totalBoxes * (x.sf / sfSum);
        return { wh: x.wh, base: Math.floor(want), rem: want - Math.floor(want) };
    });
    const boxesByWh = new Map<string, number>();
    let assigned = 0;
    for (const w of ideal) { boxesByWh.set(w.wh, w.base); assigned += w.base; }
    let leftover = totalBoxes - assigned;
    const byRem = [...ideal].sort((a, b) => b.rem - a.rem);
    for (let i = 0; leftover > 0 && i < byRem.length; i++, leftover--) {
        boxesByWh.set(byRem[i].wh, (boxesByWh.get(byRem[i].wh) || 0) + 1);
    }
    for (let i = 0; leftover > 0; i++, leftover--) {
        const wh = ranked[i % ranked.length].wh;
        boxesByWh.set(wh, (boxesByWh.get(wh) || 0) + 1);
    }

    // Гарантия СЗФО: партия ≥ NW_GUARANTEE_MIN_BOXES коробов, есть открытый
    // НЕПОКРЫТЫЙ (sf>0 — не перетарен) якорь СЗФО, но largest-remainder дал ему
    // 0 коробов → передаём 1 короб от донора. Донор — склад с максимумом коробов
    // (tie-break: наименьший shortfall — жертвуем наименее нуждающимся); СЗФО
    // с sf=0 (уже покрыт стоком/транзитом) гарантию не получает. Закрытые по
    // приёмке якоря отфильтрованы callers'ами ДО вызова. Σ коробов сохраняется.
    if (totalBoxes >= NW_GUARANTEE_MIN_BOXES) {
        const nw = shortfalls
            .filter(x => x.district === NW_DISTRICT_KEY && x.sf > 0)
            .sort((a, b) => b.sf - a.sf)[0];
        if (nw && (boxesByWh.get(nw.wh) || 0) === 0) {
            const donor = shortfalls
                .filter(x => x.wh !== nw.wh && (boxesByWh.get(x.wh) || 0) > 0)
                .sort((a, b) => {
                    const d = (boxesByWh.get(b.wh) || 0) - (boxesByWh.get(a.wh) || 0);
                    return d !== 0 ? d : a.sf - b.sf;
                })[0];
            if (donor) {
                boxesByWh.set(donor.wh, (boxesByWh.get(donor.wh) || 0) - 1);
                boxesByWh.set(nw.wh, 1);
            }
        }
    }

    const out: Record<string, number> = {};
    for (const [wh, b] of boxesByWh) if (b > 0) out[wh] = b * k;
    return out;
}
