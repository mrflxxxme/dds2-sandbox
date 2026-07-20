'use client';
import { useEffect, useRef, useState } from 'react';

/** Высота скролл-контейнера = остаток экрана под ним.
 *
 *  Так последняя строка (итоги, последний день) всегда у нижней кромки окна:
 *  прокручивается содержимое таблицы, а не страница. Пересчитываем при скролле
 *  страницы и ресайзе — верх контейнера уезжает вместе с ними.
 */
export function useFitViewport(minHeight = 160, bottomGap = 12) {
    const ref = useRef<HTMLDivElement>(null);
    const [maxHeight, setMaxHeight] = useState<number | undefined>(undefined);

    useEffect(() => {
        const recalc = () => {
            const el = ref.current;
            if (!el) return;
            // Отрицательный top = страницу всё-таки прокрутили и верх контейнера ушёл вверх;
            // считаем от 0, иначе контейнер разрастётся и утащит липкую шапку за экран.
            const top = Math.max(0, el.getBoundingClientRect().top);
            setMaxHeight(Math.max(minHeight, window.innerHeight - top - bottomGap));
        };
        recalc();
        window.addEventListener('resize', recalc);
        window.addEventListener('scroll', recalc, { passive: true });
        // Верх контейнера сдвигают и соседние блоки (свернули хедер, сменили вкладку) —
        // одних resize/scroll мало, следим за высотой страницы.
        //
        // Одного body мало: наш контейнер ограничен maxHeight и прокручивается ВНУТРИ, из-за
        // чего сворачивание блока над ним не меняет высоту страницы — body молчит, а верх
        // контейнера уже уехал вверх. Тогда таблица залипала на минимальной высоте (три
        // строки), пока не тронешь окно. Родитель такой развязки не имеет: в нём лежат и
        // соседний блок, и сам контейнер, так что его высота реагирует на оба.
        const ro = new ResizeObserver(recalc);
        ro.observe(document.body);
        if (ref.current?.parentElement) ro.observe(ref.current.parentElement);
        return () => {
            window.removeEventListener('resize', recalc);
            window.removeEventListener('scroll', recalc);
            ro.disconnect();
        };
    }, [minHeight, bottomGap]);

    return { ref, maxHeight };
}
