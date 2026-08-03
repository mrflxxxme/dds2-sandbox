import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Регресс на «стекло без размытия во всём приложении».
//
// Прод-сборка гонит CSS через Lightning CSS (внутри @tailwindcss/postcss).
// Его обработчик свойств схлопывает префиксную и непрефиксную формы одного
// свойства в ОДНО объявление и оставляет ту, что идёт ПОСЛЕДНЕЙ. Префикс к
// стандартному свойству он дописывает сам, а восстановить стандартное из
// префиксного не умеет. Значит при порядке «backdrop-filter → -webkit-» из
// бандла выпадает именно стандартная форма — а -webkit-backdrop-filter убран
// из актуальных Chrome, и размытие исчезает на всех стеклянных поверхностях.
//
// Поймать это ни tsc, ни ревью исходника не могут — дефект рождается только
// при сборке. Поэтому инвариант закреплён здесь, на уровне исходного CSS.
//
// Проверяем только backdrop-filter: это единственное свойство в файле с таким
// поведением. Пара background-clip / -webkit-background-clip объединяет
// префиксы вместо перезаписи и к порядку нечувствительна (проверено на
// собранном бандле), поэтому под правило не подпадает.

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CSS_PATH = path.resolve(HERE, '../app/globals.css');

/** Тела CSS-правил без комментариев (комментарии сами упоминают свойства). */
function ruleBodies(css: string): string[] {
    const stripped = css.replace(/\/\*[\s\S]*?\*\//g, '');
    return [...stripped.matchAll(/\{([^{}]*)\}/g)].map((m) => m[1]);
}

const BODIES = ruleBodies(readFileSync(CSS_PATH, 'utf8'));

const WEBKIT = /-webkit-backdrop-filter\s*:/;
/** Стандартная форма: перед ней начало тела или `;`, но не `-webkit-`. */
const STANDARD = /(?:^|;)\s*backdrop-filter\s*:/;

describe('globals.css — порядок объявлений backdrop-filter', () => {
    it('в файле есть правила с backdrop-filter (иначе тест бесполезен)', () => {
        const withAny = BODIES.filter((b) => WEBKIT.test(b) || STANDARD.test(b));
        expect(withAny.length).toBeGreaterThan(0);
    });

    it('нет правил только с -webkit-backdrop-filter — стандартная форма обязательна', () => {
        const webkitOnly = BODIES.filter((b) => WEBKIT.test(b) && !STANDARD.test(b));
        expect(webkitOnly).toEqual([]);
    });

    it('где объявлены обе формы, -webkit- идёт перед стандартной', () => {
        const wrongOrder = BODIES.filter((b) => {
            if (!WEBKIT.test(b) || !STANDARD.test(b)) return false;
            return b.search(WEBKIT) > b.search(STANDARD);
        });
        expect(wrongOrder).toEqual([]);
    });
});
