import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addImportMethods } from '@/lib/api/imports';

const localStorageMock = (() => {
    let store: Record<string, string> = {};
    return {
        getItem: (key: string) => store[key] ?? null,
        setItem: (key: string, value: string) => { store[key] = value; },
        removeItem: (key: string) => { delete store[key]; },
        clear: () => { store = {}; },
    };
})();
Object.defineProperty(global, 'window', { value: { document: {}, location: { href: '' } }, writable: true });
Object.defineProperty(global, 'localStorage', { value: localStorageMock, writable: true });

function makeApi() {
    const client = new ApiClient();
    return { client, ...addImportMethods(client) };
}

function mockFetch(body: unknown) {
    return vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: true, status: 200, json: async () => body,
    } as Response);
}

afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

describe('imports.getImportLogs', () => {
    it('GETs /api/v1/import/logs', async () => {
        const spy = mockFetch([
            {
                id: 1,
                filename: 'vtb_export.csv',
                source_type: 'vtb',
                imported_at: '2025-01-15T10:00:00',
                rows_raw: 100,
                rows_inserted: 95,
                rows_skipped: 5,
                status: 'success',
            },
        ]);
        const api = makeApi();
        const result = await api.getImportLogs();
        expect(result).toHaveLength(1);
        expect(result[0].filename).toBe('vtb_export.csv');
        expect(result[0].status).toBe('success');
        expect(spy.mock.calls[0][0]).toContain('/api/v1/import/logs');
        expect((spy.mock.calls[0][1] as RequestInit).method).toBe('GET');
    });

    it('returns empty list when no imports', async () => {
        mockFetch([]);
        const api = makeApi();
        const result = await api.getImportLogs();
        expect(result).toHaveLength(0);
    });
});

describe('imports.uploadFile', () => {
    it('calls uploadFormData with file, source_type, and account_no', async () => {
        const api = makeApi();
        const uploadSpy = vi.spyOn(api.client, 'uploadFormData').mockResolvedValue({
            imported: 85,
            skipped: 5,
            errors: [],
            message: 'OK',
        });

        const file = new File(['csv data'], 'export.csv', { type: 'text/csv' });
        await api.uploadFile(file, 'vtb', 'RUB-001');

        expect(uploadSpy).toHaveBeenCalledOnce();
        const [path, formData] = uploadSpy.mock.calls[0];
        expect(path).toBe('/api/v1/import/upload');
        expect(formData.get('file')).toBe(file);
        expect(formData.get('source_type')).toBe('vtb');
        expect(formData.get('account_no')).toBe('RUB-001');
    });

    it('omits account_no from FormData when empty string', async () => {
        const api = makeApi();
        const uploadSpy = vi.spyOn(api.client, 'uploadFormData').mockResolvedValue({
            imported: 10, skipped: 0, errors: [], message: 'OK',
        });

        const file = new File(['data'], 'wb_report.xlsx');
        await api.uploadFile(file, 'wb', '');

        const [, formData] = uploadSpy.mock.calls[0];
        // account_no is only appended if truthy
        expect(formData.get('account_no')).toBeNull();
        expect(formData.get('source_type')).toBe('wb');
    });

    it('throws error when uploadFormData fails', async () => {
        const api = makeApi();
        vi.spyOn(api.client, 'uploadFormData').mockRejectedValue(new Error('Unsupported file format'));

        const file = new File(['data'], 'bad.txt');
        await expect(api.uploadFile(file, 'vtb', '')).rejects.toThrow('Unsupported file format');
    });
});
