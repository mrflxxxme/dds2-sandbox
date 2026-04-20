import { describe, it, expect, vi } from 'vitest';
import { addReportMethods } from '@/lib/api/reports';
import type { ApiClient } from '@/lib/api/client';

function makeClient() {
    const requestMock = vi.fn().mockResolvedValue({});
    const client = { request: requestMock } as unknown as ApiClient;
    const methods = addReportMethods(client);
    return { methods, requestMock };
}

function capturedUrl(mock: ReturnType<typeof vi.fn>): string {
    return mock.mock.calls[0][1] as string;
}

describe('getOpiu URL encoding', () => {
    it('encodes brand with ampersand (H&M)', () => {
        const { methods, requestMock } = makeClient();
        methods.getOpiu('2024-01-01', '2024-01-31', 'H&M');
        const url = capturedUrl(requestMock);
        expect(url).toContain('brand=H%26M');
        expect(url).not.toContain('brand=H&M');
    });

    it('encodes article with space', () => {
        const { methods, requestMock } = makeClient();
        methods.getOpiu('2024-01-01', '2024-01-31', undefined, 'ABC 123');
        const url = capturedUrl(requestMock);
        // URLSearchParams encodes space as + (application/x-www-form-urlencoded)
        const decoded = decodeURIComponent(url.replace(/\+/g, ' '));
        expect(decoded).toContain('article=ABC 123');
    });

    it('encodes brand with plus sign (C++)', () => {
        const { methods, requestMock } = makeClient();
        methods.getOpiu('2024-01-01', '2024-01-31', 'C++');
        const url = capturedUrl(requestMock);
        // + must be encoded so it is not misinterpreted as a space on the server
        expect(url).not.toMatch(/brand=C\+\+(?:&|$)/);
        expect(url).toContain('brand=C%2B%2B');
    });

    it('omits optional params when not provided', () => {
        const { methods, requestMock } = makeClient();
        methods.getOpiu('2024-01-01', '2024-01-31');
        const url = capturedUrl(requestMock);
        expect(url).not.toContain('brand=');
        expect(url).not.toContain('article=');
        expect(url).toContain('date_from=2024-01-01');
        expect(url).toContain('date_to=2024-01-31');
    });
});

describe('getWbBdr URL encoding', () => {
    it('encodes brand with ampersand (H&M)', () => {
        const { methods, requestMock } = makeClient();
        methods.getWbBdr('2024-01-01', '2024-01-31', 'H&M');
        const url = capturedUrl(requestMock);
        expect(url).toContain('brand=H%26M');
        expect(url).not.toContain('brand=H&M');
    });

    it('encodes article with space', () => {
        const { methods, requestMock } = makeClient();
        methods.getWbBdr('2024-01-01', '2024-01-31', undefined, 'ABC 123');
        const url = capturedUrl(requestMock);
        const decoded = decodeURIComponent(url.replace(/\+/g, ' '));
        expect(decoded).toContain('article=ABC 123');
    });

    it('encodes brand with plus sign (C++)', () => {
        const { methods, requestMock } = makeClient();
        methods.getWbBdr('2024-01-01', '2024-01-31', 'C++');
        const url = capturedUrl(requestMock);
        expect(url).toContain('brand=C%2B%2B');
    });

    it('includes group_by when provided', () => {
        const { methods, requestMock } = makeClient();
        methods.getWbBdr('2024-01-01', '2024-01-31', undefined, undefined, 'week');
        const url = capturedUrl(requestMock);
        expect(url).toContain('group_by=week');
    });
});

describe('getOrderGeography URL encoding', () => {
    it('encodes brand with ampersand (H&M)', () => {
        const { methods, requestMock } = makeClient();
        methods.getOrderGeography('2024-01-01', '2024-01-31', 'H&M');
        const url = capturedUrl(requestMock);
        expect(url).toContain('brand=H%26M');
        expect(url).not.toContain('brand=H&M');
    });

    it('encodes article with space', () => {
        const { methods, requestMock } = makeClient();
        methods.getOrderGeography('2024-01-01', '2024-01-31', undefined, undefined, 'ABC 123');
        const url = capturedUrl(requestMock);
        const decoded = decodeURIComponent(url.replace(/\+/g, ' '));
        expect(decoded).toContain('article=ABC 123');
    });

    it('encodes brand with plus sign (C++)', () => {
        const { methods, requestMock } = makeClient();
        methods.getOrderGeography('2024-01-01', '2024-01-31', 'C++');
        const url = capturedUrl(requestMock);
        expect(url).toContain('brand=C%2B%2B');
    });

    it('encodes category with special chars', () => {
        const { methods, requestMock } = makeClient();
        methods.getOrderGeography('2024-01-01', '2024-01-31', undefined, 'Toys & Games');
        const url = capturedUrl(requestMock);
        expect(url).toContain('category=Toys+%26+Games');
    });
});
