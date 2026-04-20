/**
 * Tests for `src/lib/sanitize.ts` — DOMPurify wrapper used by AI chat UIs.
 *
 * These cases cover:
 *  - XSS payloads that MUST be neutralised (script, onerror, javascript: href,
 *    svg/onload, iframe)
 *  - legitimate markup that MUST pass through (strong, code, anchor)
 *  - link hardening hook (target="_blank" + rel="noopener noreferrer")
 *  - event-handler stripping on allow-listed tags
 *
 * Runs under vitest's jsdom environment (see `vitest.config.ts`), which
 * provides the DOM DOMPurify needs.
 */
import { describe, it, expect } from 'vitest';
import { sanitizeAIHtml } from '@/lib/sanitize';

describe('sanitizeAIHtml — XSS payloads', () => {
    it('strips <script>alert(1)</script>', () => {
        const out = sanitizeAIHtml('<script>alert(1)</script>');
        expect(out).not.toMatch(/<script/i);
        expect(out).not.toMatch(/alert/i);
    });

    it('neutralises <img src=x onerror=alert(1)>', () => {
        const out = sanitizeAIHtml('<img src=x onerror="alert(1)">');
        // DOMPurify may keep the <img> but MUST drop `onerror` and the bogus src.
        expect(out).not.toMatch(/onerror/i);
        expect(out).not.toMatch(/alert\(/);
        // the `src=x` is not a valid http(s)/data:image URL → our hook removes it
        expect(out).not.toMatch(/src=["']?x["']?/i);
    });

    it('blocks javascript: pseudo-protocol on <a href>', () => {
        const out = sanitizeAIHtml('<a href="javascript:alert(1)">click</a>');
        expect(out).not.toMatch(/javascript:/i);
        expect(out).not.toMatch(/alert/i);
    });

    it('strips <svg onload=alert(1)>', () => {
        const out = sanitizeAIHtml('<svg onload="alert(1)"><circle r=10 /></svg>');
        // <svg> is in FORBID_TAGS → whole element is dropped.
        expect(out).not.toMatch(/<svg/i);
        expect(out).not.toMatch(/onload/i);
        expect(out).not.toMatch(/alert/i);
    });

    it('strips <iframe src="...">', () => {
        const out = sanitizeAIHtml('<iframe src="https://evil.example.com"></iframe>');
        expect(out).not.toMatch(/<iframe/i);
        expect(out).not.toMatch(/evil\.example\.com/i);
    });

    it('strips event handlers even on allowed tags (<p onclick=...>)', () => {
        const out = sanitizeAIHtml('<p onclick="alert(1)">hello</p>');
        expect(out).toMatch(/<p[^>]*>hello<\/p>/i);
        expect(out).not.toMatch(/onclick/i);
        expect(out).not.toMatch(/alert/i);
    });

    it('strips data:text/html URLs from images (only data:image/* allowed)', () => {
        const out = sanitizeAIHtml(
            '<img src="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==" alt="x">',
        );
        expect(out).not.toMatch(/data:text\/html/i);
    });
});

describe('sanitizeAIHtml — legitimate markup', () => {
    it('keeps <strong>ok</strong>', () => {
        const out = sanitizeAIHtml('<strong>ok</strong>');
        expect(out).toBe('<strong>ok</strong>');
    });

    it('keeps <p><a href="https://example.com">link</a></p> and adds target+rel', () => {
        const out = sanitizeAIHtml('<p><a href="https://example.com">link</a></p>');
        expect(out).toMatch(/<a [^>]*href="https:\/\/example\.com"[^>]*>link<\/a>/i);
        expect(out).toMatch(/target="_blank"/);
        expect(out).toMatch(/rel="noopener noreferrer"/);
    });

    it('keeps Telegram-style <b>, <i>, <code>, <pre>, <u>, <s>', () => {
        const out = sanitizeAIHtml(
            '<b>bold</b> <i>italic</i> <code>c</code> <pre>p</pre> <u>u</u> <s>s</s>',
        );
        expect(out).toContain('<b>bold</b>');
        expect(out).toContain('<i>italic</i>');
        expect(out).toContain('<code>c</code>');
        expect(out).toContain('<pre>p</pre>');
        expect(out).toContain('<u>u</u>');
        expect(out).toContain('<s>s</s>');
    });

    it('keeps lists and headings', () => {
        const out = sanitizeAIHtml('<h2>Title</h2><ul><li>a</li><li>b</li></ul>');
        expect(out).toContain('<h2>Title</h2>');
        expect(out).toContain('<ul>');
        expect(out).toContain('<li>a</li>');
        expect(out).toContain('<li>b</li>');
    });

    it('keeps tables', () => {
        const out = sanitizeAIHtml(
            '<table><thead><tr><th>H</th></tr></thead><tbody><tr><td>D</td></tr></tbody></table>',
        );
        expect(out).toContain('<table>');
        expect(out).toContain('<th>H</th>');
        expect(out).toContain('<td>D</td>');
    });

    it('returns empty string for falsy input', () => {
        expect(sanitizeAIHtml('')).toBe('');
    });

    it('preserves plain text without any tags', () => {
        expect(sanitizeAIHtml('hello world')).toBe('hello world');
    });

    it('allows data:image/png in <img src>', () => {
        const png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAA';
        const out = sanitizeAIHtml(`<img src="${png}" alt="pixel">`);
        expect(out).toMatch(/<img[^>]+src="data:image\/png/);
        expect(out).toMatch(/referrerpolicy="no-referrer"/);
        expect(out).toMatch(/loading="lazy"/);
    });

    it('preserves relative internal links (e.g. /p/slug/reports) with target+rel', () => {
        const out = sanitizeAIHtml('<a href="/p/acme/reports">Reports</a>');
        expect(out).toMatch(/href="\/p\/acme\/reports"/);
        expect(out).toMatch(/target="_blank"/);
        expect(out).toMatch(/rel="noopener noreferrer"/);
    });
});
