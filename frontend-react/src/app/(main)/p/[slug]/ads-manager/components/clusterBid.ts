/** Пересчёт целевой цены клика в CPM-ставку кластера.
 *
 *  CPC = CPM / 1000 / (clicks / views) = CPM / (10 × CTR%)
 *  ⇒   CPM = CPC × CTR% × 10
 *
 *  При CTR = 0 (показы есть, кликов нет) цена клика недостижима любой ставкой —
 *  такие фразы вызывающий обязан пропустить, а не ставить им ставку «наугад».
 */
export function cpmForTargetCpc(targetCpc: number, ctrPct: number): number | null {
    if (!Number.isFinite(targetCpc) || !Number.isFinite(ctrPct)) return null;
    if (!(targetCpc > 0) || !(ctrPct > 0)) return null;
    return Math.round(targetCpc * ctrPct * 10);
}
