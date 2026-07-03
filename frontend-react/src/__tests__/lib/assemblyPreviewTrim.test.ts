import { describe, it, expect } from 'vitest';
import { trimLinesToWholePallets, type PreviewLine } from '@/lib/utils/assemblyPreview';

// upp по (nm): 100,101 → 160 шт/пал (короб). Высота склада в тесте не важна — uppOf
// возвращает фикс. значение; null → SKU без габаритов (не режется).
const UPP: Record<number, number | null> = { 100: 160, 101: 160, 999: null };
const uppOf = (nm: number) => UPP[nm] ?? null;

const line = (ff: number, wb: string, nm: number, qty: number, pkg: PreviewLine['pkg'] = 'BOX'): PreviewLine => ({
    ffId: ff, wbName: wb, nmId: nm, vendor: `VC-${nm}`, barcode: `bc-${nm}`, pkg, isNew: false, qty,
});
const newLine = (ff: number, wb: string, nm: number, qty: number, pkg: PreviewLine['pkg'] = 'BOX'): PreviewLine =>
    ({ ...line(ff, wb, nm, qty, pkg), isNew: true });
const sum = (ls: PreviewLine[]) => ls.reduce((s, l) => s + l.qty, 0);

describe('trimLinesToWholePallets', () => {
    it('неполная отгрузка ФФ→склад (0.5 пал) убирается целиком', () => {
        const r = trimLinesToWholePallets([line(1, 'Казань', 100, 80)], uppOf);
        expect(r.kept).toHaveLength(0);
        expect(r.droppedUnits).toBe(80);
        expect(r.removedSupplies).toBe(1);
    });

    it('отгрузка 2.8 пал → 2 целых, хвост снят', () => {
        const r = trimLinesToWholePallets([line(1, 'Казань', 100, 450)], uppOf);
        expect(r.kept).toHaveLength(1);
        expect(r.kept[0].qty).toBe(320);
        expect(r.droppedUnits).toBe(130);
        expect(r.removedSupplies).toBe(0);
    });

    it('ТА ЖЕ нагрузка, но split по 2 ФФ: КАЖДАЯ отгрузка целая отдельно', () => {
        // Один город Казань, но из 2 ФФ по 0.5 паллеты — НЕ суммируются: обе убираются
        // (в отличие от среза по городу, где 160 вместе были бы 1 целой).
        const r = trimLinesToWholePallets([
            line(1, 'Казань', 100, 80),
            line(2, 'Казань', 100, 80),
        ], uppOf);
        expect(r.kept).toHaveLength(0);
        expect(r.droppedUnits).toBe(160);
        expect(r.removedSupplies).toBe(2);
    });

    it('смешанная паллета: 2 SKU в одной отгрузке вместе = 1 целая → остаются', () => {
        const r = trimLinesToWholePallets([
            line(1, 'Казань', 100, 80),
            line(1, 'Казань', 101, 80),
        ], uppOf);
        expect(sum(r.kept)).toBe(160);
        expect(r.droppedUnits).toBe(0);
        expect(r.removedSupplies).toBe(0);
    });

    it('одна отгрузка целая, другая нет — целая остаётся, неполная убрана', () => {
        const r = trimLinesToWholePallets([
            line(1, 'Казань', 100, 160),       // 1 целая
            line(1, 'Сарапул', 100, 80),       // 0.5 — убрать
        ], uppOf);
        expect(r.kept).toHaveLength(1);
        expect(r.kept[0].wbName).toBe('Казань');
        expect(r.kept[0].qty).toBe(160);
        expect(r.removedSupplies).toBe(1);
        expect(r.droppedUnits).toBe(80);
    });

    it('SKU без габаритов (б/габ) — снимается целиком (строгий режим)', () => {
        const r = trimLinesToWholePallets([line(1, 'Казань', 999, 50)], uppOf);
        expect(r.kept).toHaveLength(0);
        expect(r.droppedUnits).toBe(50);
        expect(r.removedSupplies).toBe(1);
    });

    it('целая паллета + б/габ хвост: паллета остаётся, б/габ снят', () => {
        const r = trimLinesToWholePallets([
            line(1, 'Казань', 100, 160),  // 1 целая паллета
            line(1, 'Казань', 999, 30),   // б/габ — снять
        ], uppOf);
        expect(r.kept).toHaveLength(1);
        expect(r.kept[0].nmId).toBe(100);
        expect(r.kept[0].qty).toBe(160);
        expect(r.droppedUnits).toBe(30);
        expect(r.removedSupplies).toBe(0); // отгрузка осталась (целая паллета есть)
    });

    it('моно ≤3: SKU одной отгрузки собираются в целую паллету (правило WB max 3 арт.)', () => {
        // 2 моно-SKU по 0.5 пал в одной отгрузке складываются в 1 ЦЕЛУЮ паллету (≤3 арт.)
        // → 160 шт едут, ничего не снято.
        const r = trimLinesToWholePallets([
            line(1, 'Казань', 100, 80, 'MONOPALLET'),
            line(1, 'Казань', 101, 80, 'MONOPALLET'),
        ], uppOf);
        expect(sum(r.kept)).toBe(160);
        expect(r.droppedUnits).toBe(0);
    });

    it('моно: одиночный SKU < паллеты остаётся на ФФ (не с кем собраться)', () => {
        const r = trimLinesToWholePallets([
            line(1, 'Казань', 100, 80, 'MONOPALLET'),
        ], uppOf);
        expect(r.kept).toHaveLength(0);
        expect(r.droppedUnits).toBe(80);
    });

    it('новинка без габаритов — едет РОССЫПЬЮ (гибрид: не снимается)', () => {
        const r = trimLinesToWholePallets([newLine(1, 'Казань', 999, 50)], uppOf);
        expect(r.kept).toHaveLength(1);
        expect(r.kept[0].nmId).toBe(999);
        expect(r.kept[0].qty).toBe(50);
        expect(r.droppedUnits).toBe(0);
        expect(r.removedSupplies).toBe(0);
    });

    it('гибрид: новинка б/габ остаётся, ОБЫЧНЫЙ б/габ снимается', () => {
        const r = trimLinesToWholePallets([
            newLine(1, 'Казань', 999, 50),  // новинка б/габ → россыпью
            line(1, 'Казань', 998, 40),     // обычный б/габ (998∉UPP→null) → снять
        ], uppOf);
        expect(r.kept).toHaveLength(1);
        expect(r.kept[0].nmId).toBe(999);
        expect(r.droppedUnits).toBe(40);
    });

    it('новинка С геометрией режется до целых паллет как обычная (sub-pallet снят)', () => {
        const r = trimLinesToWholePallets([newLine(1, 'Казань', 100, 80)], uppOf); // upp=160
        expect(r.kept).toHaveLength(0);
        expect(r.droppedUnits).toBe(80);
    });
});
