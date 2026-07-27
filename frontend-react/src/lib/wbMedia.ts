/**
 * Ссылки на медиа и карточку товара WB по nm_id.
 *
 * Жило в `ads-manager/components/adsShared.tsx` и было доступно только рекламе;
 * фото и ссылка на карточку нужны везде, где показывается товар (остатки FBS,
 * воронка, отчёты), поэтому переехало в общий слой. `adsShared` реэкспортирует
 * эти функции — импорты рекламы менять не пришлось.
 */

// Известные (неравномерные) границы vol → номер basket-хоста WB.
const WB_BASKET_RANGES: [number, number][] = [
    [143, 1], [287, 2], [431, 3], [719, 4], [1007, 5], [1061, 6], [1115, 7], [1169, 8],
    [1313, 9], [1601, 10], [1655, 11], [1919, 12], [2045, 13], [2189, 14], [2405, 15],
    [2621, 16], [2837, 17], [3053, 18], [3269, 19], [3485, 20], [3701, 21], [3917, 22],
    [4133, 23], [4349, 24], [4565, 25], [4877, 26], [5189, 27], [5501, 28], [5813, 29], [6125, 30],
];

/** URL нашего кэша фото товара (MinIO): резолвится+сохраняется 1 раз, дальше отдаётся из хранилища. */
export function productImageUrl(nmId: number): string {
    const base = process.env.NEXT_PUBLIC_API_URL || '';
    return `${base}/api/v1/media/product-image/${nmId}`;
}

/** URL главного фото товара WB по nm_id (публичный CDN, ключ не нужен).
 *  Хост basket-XX по vol-диапазону; для vol выше таблицы — продолжаем шагом 312
 *  (WB периодически добавляет баскеты). Битую картинку прячем через onError в <WbThumb>. */
export function wbImageUrl(nmId: number, size: 'small' | 'big' | 'c246x328' | 'c516x688' = 'c246x328'): string {
    const vol = Math.floor(nmId / 100000);
    const part = Math.floor(nmId / 1000);
    let n = WB_BASKET_RANGES.find(([max]) => vol <= max)?.[1];
    if (n == null) n = 30 + Math.ceil((vol - 6125) / 312);  // за пределами таблицы
    const host = String(n).padStart(2, '0');
    return `https://basket-${host}.wbbasket.ru/vol${vol}/part${part}/${nmId}/images/${size}/1.webp`;
}

/**
 * Карточка товара на витрине WB. `targetUrl=GP` — тот же параметр, который
 * кабинет ставит в своих ссылках: без него WB иногда подмешивает выдачу поиска
 * вместо прямого перехода на карточку.
 */
export function wbProductUrl(nmId: number): string {
    return `https://www.wildberries.ru/catalog/${nmId}/detail.aspx?targetUrl=GP`;
}
