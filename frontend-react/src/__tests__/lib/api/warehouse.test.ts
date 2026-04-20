/**
 * Tests for warehouse.ts acceptReceipt — verifies that || null → ?? null fix
 * preserves empty array [] instead of losing it as null (body loss bug cbcb76a).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addWarehouseMethods } from '@/lib/api/warehouse';

// Minimal InboundReceipt stub returned by mocked fetch
const stubReceipt = { id: 42, status: 'ACCEPTED' };

function makeFetchMock(captured: { body: string | undefined }[]) {
    return vi.fn((_url: string, init?: RequestInit) => {
        captured.push({ body: init?.body as string | undefined });
        return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(stubReceipt),
        });
    });
}

function makeApi() {
    const client = new ApiClient();
    // Stub token so Authorization header is set (avoids redirect)
    vi.spyOn(client, 'getToken').mockReturnValue('test-token');
    vi.spyOn(client, 'getProjectId').mockReturnValue(1);
    return { client, warehouse: addWarehouseMethods(client) };
}

describe('acceptReceipt body serialization (|| null → ?? null fix)', () => {
    let captured: { body: string | undefined }[];

    beforeEach(() => {
        captured = [];
        vi.stubGlobal('fetch', makeFetchMock(captured));
    });

    it('sends body with actual quantities array when provided', async () => {
        const { warehouse } = makeApi();
        const quantities = [{ item_id: 1, actual_qty: 5 }, { item_id: 2, actual_qty: 3 }];
        await warehouse.acceptReceipt(42, quantities);

        expect(captured).toHaveLength(1);
        expect(captured[0].body).toBe(JSON.stringify(quantities));
    });

    it('sends [] as body when empty array is passed (was lost as null before fix)', async () => {
        const { warehouse } = makeApi();
        // Before fix: [] || null → null → fetch body undefined → backend gets None
        // After fix:  [] ?? null → []  → fetch body "[]" → backend gets []
        await warehouse.acceptReceipt(42, []);

        expect(captured).toHaveLength(1);
        // client.ts: body ? JSON.stringify(body) : undefined
        // [] is truthy as a value passed through ?? → JSON.stringify([]) === "[]"
        expect(captured[0].body).toBe('[]');
    });

    it('sends no body (undefined) when no argument passed', async () => {
        const { warehouse } = makeApi();
        await warehouse.acceptReceipt(42);

        expect(captured).toHaveLength(1);
        // undefined ?? null → null → client.ts: null is falsy → body: undefined
        expect(captured[0].body).toBeUndefined();
    });

    it('sends no body when null is passed explicitly', async () => {
        const { warehouse } = makeApi();
        // null ?? null → null → client.ts: null is falsy → body: undefined
        await warehouse.acceptReceipt(42, null as any);

        expect(captured).toHaveLength(1);
        expect(captured[0].body).toBeUndefined();
    });
});
