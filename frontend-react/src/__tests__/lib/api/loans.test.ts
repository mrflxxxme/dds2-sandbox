import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiClient } from '@/lib/api/client';
import { addLoanMethods } from '@/lib/api/loans';

const localStorageMock = (() => {
    let store: Record<string, string> = {};
    return {
        getItem: (key: string) => store[key] ?? null,
        setItem: (key: string, value: string) => { store[key] = value; },
        removeItem: (key: string) => { delete store[key]; },
        clear: () => { store = {}; },
    };
})();

Object.defineProperty(global, 'window', {
    value: { document: {}, location: { href: '' } },
    writable: true,
});
Object.defineProperty(global, 'localStorage', { value: localStorageMock, writable: true });

function makeApi() {
    const client = new ApiClient();
    return { client, ...addLoanMethods(client) };
}

describe('loans.listLoans', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('GETs without query string when no params provided', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ items: [], total: 0, totals_by_direction: {} }),
        } as Response);

        const api = makeApi();
        await api.listLoans();

        expect(fetchSpy).toHaveBeenCalledOnce();
        const [url] = fetchSpy.mock.calls[0];
        expect(String(url).endsWith('/loans')).toBe(true);
    });

    it('serializes direction, status, counterparty_id via URLSearchParams', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ items: [], total: 0, totals_by_direction: {} }),
        } as Response);

        const api = makeApi();
        await api.listLoans({
            direction: 'INCOMING',
            status: 'ACTIVE',
            counterparty_id: 42,
        });

        const [url] = fetchSpy.mock.calls[0];
        const urlStr = String(url);
        expect(urlStr).toContain('direction=INCOMING');
        expect(urlStr).toContain('status=ACTIVE');
        expect(urlStr).toContain('counterparty_id=42');
    });

    it('serializes limit and offset params', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ items: [], total: 0, totals_by_direction: {} }),
        } as Response);

        const api = makeApi();
        await api.listLoans({ limit: 25, offset: 50 });

        const [url] = fetchSpy.mock.calls[0];
        const urlStr = String(url);
        expect(urlStr).toContain('limit=25');
        expect(urlStr).toContain('offset=50');
    });

    it('handles counterparty_id=0 (falsy but valid)', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ items: [], total: 0, totals_by_direction: {} }),
        } as Response);

        const api = makeApi();
        await api.listLoans({ counterparty_id: 0 });

        const [url] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('counterparty_id=0');
    });
});

describe('loans.getLoan', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('GETs /api/v1/loans/{id}', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({
                id: 1,
                counterparty_id: 5,
                direction: 'INCOMING',
                principal: 1000000,
                currency: 'RUB',
                rate: 0.15,
                contract_number: 'L-2025-01',
                contract_date: '2025-01-01',
                start_date: '2025-01-01',
                maturity_date: '2026-01-01',
                status: 'ACTIVE',
                notes: null,
                payments: [],
                schedule_summary: { principal_paid: 0, interest_paid: 0, penalty_paid: 0, remaining: 1000000 },
            }),
        } as Response);

        const api = makeApi();
        const loan = await api.getLoan(1);

        const [url] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('/api/v1/loans/1');
        expect(loan.id).toBe(1);
        expect(loan.principal).toBe(1000000);
    });
});

describe('loans.createLoan', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('POSTs loan data as JSON', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 201,
            json: async () => ({ id: 99 }),
        } as Response);

        const api = makeApi();
        await api.createLoan({
            counterparty_id: 5,
            direction: 'INCOMING',
            principal: 500000,
            currency: 'RUB',
            rate: 0.185,
            contract_number: 'L-2025-02',
            contract_date: '2025-02-01',
            start_date: '2025-02-01',
            maturity_date: '2026-02-01',
            status: 'ACTIVE',
            notes: null,
        });

        const [url, init] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('/api/v1/loans');
        expect((init as RequestInit).method).toBe('POST');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.counterparty_id).toBe(5);
        expect(body.principal).toBe(500000);
        expect(body.contract_number).toBe('L-2025-02');
    });
});

describe('loans.updateLoan', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('PATCHes /api/v1/loans/{id} with partial data', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ id: 3, status: 'CLOSED' }),
        } as Response);

        const api = makeApi();
        await api.updateLoan(3, { status: 'CLOSED' });

        const [url, init] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('/api/v1/loans/3');
        expect((init as RequestInit).method).toBe('PATCH');
        expect(JSON.parse((init as RequestInit).body as string)).toEqual({ status: 'CLOSED' });
    });
});

describe('loans.matchLoanTransaction', () => {
    afterEach(() => { vi.restoreAllMocks(); localStorageMock.clear(); });

    it('POSTs match payload to /api/v1/loans/{id}/payments/match', async () => {
        const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
            ok: true, status: 201,
            json: async () => ({
                id: 1, loan_id: 7, transaction_id: 123, payment_type: 'PRINCIPAL_REPAY',
                amount: 100000, currency: 'RUB', paid_at: '2025-03-01',
            }),
        } as Response);

        const api = makeApi();
        const payment = await api.matchLoanTransaction(7, {
            transaction_id: 123,
            payment_type: 'PRINCIPAL_REPAY',
            amount: 100000,
        });

        const [url, init] = fetchSpy.mock.calls[0];
        expect(String(url)).toContain('/api/v1/loans/7/payments/match');
        expect((init as RequestInit).method).toBe('POST');
        expect(payment.payment_type).toBe('PRINCIPAL_REPAY');
        const body = JSON.parse((init as RequestInit).body as string);
        expect(body.transaction_id).toBe(123);
        expect(body.amount).toBe(100000);
    });
});
