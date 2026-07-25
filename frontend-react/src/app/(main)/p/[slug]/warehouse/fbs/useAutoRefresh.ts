'use client';
/**
 * Тик автообновления раздела FBS.
 *
 * Данные раздела живые: джоб трансляции остатков ходит раз в 3 минуты, синк
 * новых заданий — раз в 2, поэтому открытая страница за несколько минут
 * устаревает молча. Тик решает это одним счётчиком: вкладки подписываются на
 * него и перезагружают СВОИ данные.
 *
 * Три вещи, без которых автообновление вредит:
 *   • пауза на скрытой вкладке — фоновая страница, открытая на весь день, иначе
 *     бьёт по API сотнями холостых запросов и жжёт лимиты WB на джобах;
 *   • догоняющее обновление при возврате на вкладку — иначе после паузы человек
 *     видит данные той давности, когда он ушёл, и не знает об этом;
 *   • отключаемость — во время ручной работы (проставление количеств) перерисовка
 *     таблицы под руками мешает.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

/** Раз в 3 минуты — тот же такт, что у джоба трансляции остатков. */
export const AUTO_REFRESH_INTERVAL_MS = 180_000;

/** Проверка «пора ли» — раз в 15 c, чтобы возврат на вкладку ловился быстро. */
const CHECK_INTERVAL_MS = 15_000;

/**
 * Возраст данных словами. Отдельная функция (а не JSX-выражение) — её проверяет
 * unit-тест: «обновлено 0 мин назад» читается как поломка, а не как «только что».
 */
export function formatAge(ms: number | null): string {
    if (ms == null) return '';
    const sec = Math.max(0, Math.floor(ms / 1000));
    if (sec < 60) return 'только что';
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min} мин назад`;
    const hours = Math.floor(min / 60);
    return `${hours} ч назад`;
}

export interface AutoRefresh {
    /** Счётчик тиков. 0 — тиков ещё не было: первичную загрузку делают сами вкладки. */
    tick: number;
    enabled: boolean;
    setEnabled: (value: boolean) => void;
    /** Момент последнего обновления данных (тик или ручная перезагрузка). */
    lastAt: number | null;
    /** Сообщить, что данные обновлены вне тика — «возраст» начинает считаться заново. */
    markRefreshed: () => void;
}

export function useAutoRefresh(intervalMs: number = AUTO_REFRESH_INTERVAL_MS): AutoRefresh {
    const [enabled, setEnabled] = useState(true);
    const [tick, setTick] = useState(0);
    const [lastAt, setLastAt] = useState<number | null>(null);

    // Момент последнего обновления держим И в ref: интервал не должен
    // пересоздаваться на каждое обновление (иначе отсчёт всё время сбрасывается
    // и тик не наступает никогда).
    //
    // Стартовое значение — момент МОНТИРОВАНИЯ, а не null: при null первая же
    // проверка (через 15 c) считала возраст бесконечным и била тиком сразу,
    // перезагружая страницу через четверть минуты после открытия вместо трёх.
    const lastAtRef = useRef<number | null>(Date.now());

    const markRefreshed = useCallback(() => {
        const now = Date.now();
        lastAtRef.current = now;
        setLastAt(now);
    }, []);

    useEffect(() => {
        if (!enabled) return;

        const fire = () => {
            const now = Date.now();
            lastAtRef.current = now;
            setLastAt(now);
            setTick(t => t + 1);
        };

        const maybeFire = () => {
            // Скрытая вкладка не обновляется вовсе: догоним при возврате.
            if (document.hidden) return;
            const last = lastAtRef.current;
            if (last == null || Date.now() - last >= intervalMs) fire();
        };

        // Возобновление после выключения тумблера не должно бить тиком сразу:
        // отсчёт начинается заново от момента включения.
        lastAtRef.current = Date.now();

        // Возврат на вкладку — сразу проверяем возраст, не дожидаясь тикера.
        const onVisibility = () => { if (!document.hidden) maybeFire(); };

        const timer = setInterval(maybeFire, CHECK_INTERVAL_MS);
        document.addEventListener('visibilitychange', onVisibility);
        return () => {
            clearInterval(timer);
            document.removeEventListener('visibilitychange', onVisibility);
        };
    }, [enabled, intervalMs]);

    return { tick, enabled, setEnabled, lastAt, markRefreshed };
}
