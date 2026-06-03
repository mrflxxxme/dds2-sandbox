import { describe, it, expect } from 'vitest';

import { draftRowKey, dropCommittedRows } from '@/lib/utils/assemblyDraftReconcile';
import type { AssemblyDraftRow, PackageType } from '@/types/api';

function row(nm_id: number, package_type?: PackageType, tgtQty = 1): AssemblyDraftRow {
    return {
        nm_id,
        barcode: `BC${nm_id}`,
        vendor_code: `V${nm_id}`,
        src: { '1': tgtQty },
        tgt: { 'Коледино': tgtQty },
        package_type,
    };
}

describe('draftRowKey', () => {
    it('defaults missing package_type to BOX', () => {
        expect(draftRowKey({ nm_id: 5 })).toBe('5-BOX');
        expect(draftRowKey({ nm_id: 5, package_type: 'BOX' })).toBe('5-BOX');
    });

    it('distinguishes package types', () => {
        expect(draftRowKey({ nm_id: 5, package_type: 'MONOPALLET' })).toBe('5-MONOPALLET');
        expect(draftRowKey({ nm_id: 5, package_type: 'SUPERSAFE' })).toBe('5-SUPERSAFE');
    });
});

describe('dropCommittedRows', () => {
    it('keeps all rows when server still has every key (no commit)', () => {
        const local = [row(1), row(2, 'MONOPALLET')];
        const server = [row(1), row(2, 'MONOPALLET')];
        expect(dropCommittedRows(local, server)).toEqual(local);
    });

    it('drops committed BOX rows, keeps the MONOPALLET leftover', () => {
        const local = [row(1, 'BOX'), row(2, 'BOX'), row(3, 'MONOPALLET')];
        const server = [row(3, 'MONOPALLET')]; // BOX rows committed → gone server-side
        expect(dropCommittedRows(local, server).map(r => r.nm_id)).toEqual([3]);
    });

    it('preserves local edits on surviving rows', () => {
        const local = [row(3, 'MONOPALLET', 99)]; // locally edited qty
        const server = [row(3, 'MONOPALLET', 1)]; // stale server qty
        const out = dropCommittedRows(local, server);
        expect(out).toHaveLength(1);
        expect(out[0].tgt['Коледино']).toBe(99); // local edit kept, not server's
    });

    it('treats undefined package_type as BOX when matching', () => {
        const local = [row(1, undefined)]; // implicit BOX
        const server = [row(1, 'BOX')]; // explicit BOX
        expect(dropCommittedRows(local, server)).toHaveLength(1);
    });

    it('BOX+SUPERSAFE committed together under the Короб tab are both dropped', () => {
        const local = [row(1, 'BOX'), row(2, 'SUPERSAFE'), row(3, 'MONOPALLET')];
        const server = [row(3, 'MONOPALLET')];
        expect(dropCommittedRows(local, server).map(r => r.nm_id)).toEqual([3]);
    });

    it('drops everything when the server draft is empty (full commit)', () => {
        const local = [row(1), row(2, 'MONOPALLET')];
        expect(dropCommittedRows(local, [])).toEqual([]);
    });
});
