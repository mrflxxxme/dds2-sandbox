// @vitest-environment node
/**
 * Синхронизация каталога разделов RBAC между бэком и фронтом.
 *
 * Окружение node (а не общий jsdom) — тест читает файлы обоих деревьев через fs.
 *
 * Зачем здесь, а не только в pytest: гарды в tests/test_conventions_sync.py
 * скипаются, когда frontend-react не виден, а backend-CI и `docker compose exec
 * backend pytest` гоняются именно в контейнере без фронта — то есть там они
 * молчат. Этот прогон видит ОБА дерева, поэтому именно он ловит рассинхрон.
 *
 * Что ломается при рассинхроне:
 * - ключ бэкенда без чекбокса → выдать раздел editor/viewer нечем;
 * - чекбокс без ключа → «Сохранить» падает 400 «Неизвестные страницы»;
 * - pageKey меню вне каталога → раздел невидим для editor/viewer навсегда.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const REPO_ROOT = path.resolve(process.cwd(), '..');
const RBAC_PY = path.join(REPO_ROOT, 'backend', 'rbac.py');
const TEAM_PAGE = path.join(process.cwd(), 'src', 'app', '(main)', 'p', '[slug]', 'team', 'page.tsx');
const LAYOUT = path.join(process.cwd(), 'src', 'app', '(main)', 'p', '[slug]', 'layout.tsx');

// `vibecoding` гейтится строкой в `vibe_authors`, а не каталогом страниц.
const SIDEBAR_KEYS_OUTSIDE_CATALOG = new Set(['vibecoding']);

function read(file: string): string {
    if (!fs.existsSync(file)) {
        throw new Error(`Гард синхронизации RBAC не нашёл ${file} — проверь чекаут, гард отключать нельзя`);
    }
    return fs.readFileSync(file, 'utf-8');
}

function block(source: string, pattern: RegExp, label: string): string {
    const match = source.match(pattern);
    if (!match) throw new Error(`Не нашёл ${label} — regex устарел?`);
    return match[1];
}

function backendPages(): { all: Set<string>; neverInherited: Set<string> } {
    const source = read(RBAC_PY);
    const all = block(source, /ALL_PAGES:\s*list\[str\]\s*=\s*\[([\s\S]*?)\n\]/, 'ALL_PAGES');
    const never = block(
        source,
        /PAGES_NEVER_INHERITED:\s*frozenset\[str\]\s*=\s*frozenset\(\s*\{([\s\S]*?)\}\s*\)/,
        'PAGES_NEVER_INHERITED',
    );
    const keys = (text: string) => new Set([...text.matchAll(/"([^"]+)"/g)].map(m => m[1]));
    return { all: keys(all), neverInherited: keys(never) };
}

function checkboxKeys(): Set<string> {
    const source = read(TEAM_PAGE);
    const literal = block(source, /const SECTION_PAGES[\s\S]*?=\s*\{([\s\S]*?)\n\};/, 'SECTION_PAGES фронта');
    return new Set([...literal.matchAll(/key:\s*'([^']+)'/g)].map(m => m[1]));
}

function sidebarKeys(): Set<string> {
    return new Set([...read(LAYOUT).matchAll(/pageKey:\s*'([^']+)'/g)].map(m => m[1]));
}

function frontendNeverInherited(): Set<string> {
    const literal = block(read(TEAM_PAGE), /const NEVER_INHERITED = new Set\(\[([\s\S]*?)\]\)/, 'NEVER_INHERITED');
    return new Set([...literal.matchAll(/'([^']+)'/g)].map(m => m[1]));
}

const sorted = (set: Set<string>) => [...set].sort();

describe('каталог разделов RBAC: бэк ↔ фронт', () => {
    it('каждый раздел бэкенда имеет чекбокс в «Команде»', () => {
        const { all } = backendPages();
        const missing = sorted(new Set([...all].filter(p => !checkboxKeys().has(p))));

        expect(missing, 'разделы бэкенда без чекбокса — выдать их владельцу нечем').toEqual([]);
    });

    it('каждый чекбокс известен бэкенду', () => {
        const { all } = backendPages();
        const unknown = sorted(new Set([...checkboxKeys()].filter(p => !all.has(p))));

        expect(unknown, 'чекбокс вне ALL_PAGES — «Сохранить» упадёт 400').toEqual([]);
    });

    it('каждый pageKey меню лежит в каталоге', () => {
        const { all } = backendPages();
        const orphans = sorted(
            new Set([...sidebarKeys()].filter(p => !all.has(p) && !SIDEBAR_KEYS_OUTSIDE_CATALOG.has(p))),
        );

        expect(orphans, 'pageKey вне каталога — раздел невидим для editor/viewer навсегда').toEqual([]);
    });

    it('подпись «только вручную» совпадает с запретом бэкенда', () => {
        const { neverInherited } = backendPages();

        expect(sorted(frontendNeverInherited())).toEqual(sorted(neverInherited));
    });
});
