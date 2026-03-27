/* ─── Container Load Packer ────────────────────────────────────────────── */

export interface ContainerSpec {
    name: string;
    l: number;  // meters
    w: number;
    h: number;
}

export interface BoxType {
    l: number;  // cm
    w: number;
    h: number;
    qty: number;
    label: string;
}

export interface PlacedBox {
    x: number;
    y: number;
    z: number;
    l: number;  // meters
    w: number;
    h: number;
    typeIdx: number;
    label: string;
}

export interface FreeSpace {
    x: number;
    y: number;
    z: number;
    l: number;
    w: number;
    h: number;
}

export interface PackResult {
    placed: PlacedBox[];
    freeSpaces: FreeSpace[];
    containerVol: number;
    boxVol: number;
    fillPercent: string;
    totalPlaced: number;
    totalRequested: number;
    placedPerType: Record<number, number>;
    maxPerType: Record<number, number>;
    freeVol: number;
    largestFree: FreeSpace | null;
    freeLength: number;
    maxBoxX: number;
    freeCount: number;
}

/* ─── Data ─────────────────────────────────────────────────────────────── */

export const CONTAINERS: Record<string, ContainerSpec> = {
    '40ft':    { name: '40 фут',           l: 12.03, w: 2.35, h: 2.39 },
    '40ft_hc': { name: '40 фут HC (расш.)', l: 12.03, w: 2.35, h: 2.69 },
    '20ft':    { name: '20 фут',           l: 5.90,  w: 2.35, h: 2.39 },
    'truck1':  { name: 'Авто 13.5м',       l: 13.5,  w: 2.45, h: 2.58 },
    'truck2':  { name: 'Авто 13.6м',       l: 13.6,  w: 2.48, h: 2.68 },
};

export const BOX_COLORS = [
    '#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6',
    '#1abc9c', '#e67e22', '#ec407a', '#26c6da', '#66bb6a',
    '#ffa726', '#ab47bc', '#ef5350', '#42a5f5', '#26a69a',
];

/* ─── Helpers ──────────────────────────────────────────────────────────── */

interface Orientation { l: number; w: number; h: number; }

function getOrientations(box: { l: number; w: number; h: number }): Orientation[] {
    const { l, w, h } = box;
    const all: Orientation[] = [
        { l, w, h }, { l, h, w: h }, { w, l: w, h },
        { l: w, w: h, h: l }, { l: h, w: l, h: w }, { l: h, w, h: l },
    ];
    // Fix: proper orientations
    const allCorrect: Orientation[] = [
        { l, w, h },
        { l, w: h, h: w },
        { l: w, w: l, h },
        { l: w, w: h, h: l },
        { l: h, w: l, h: w },
        { l: h, w, h: l },
    ];
    const unique: Orientation[] = [];
    const seen = new Set<string>();
    for (const o of allCorrect) {
        const k = `${o.l}-${o.w}-${o.h}`;
        if (!seen.has(k)) { seen.add(k); unique.push(o); }
    }
    return unique;
}

/* ─── Bin-Packing ──────────────────────────────────────────────────────── */

export function packBoxes(
    container: ContainerSpec,
    boxTypes: BoxType[],
): { placed: PlacedBox[]; freeSpaces: FreeSpace[] } {
    const placed: PlacedBox[] = [];
    const spaces: FreeSpace[] = [
        { x: 0, y: 0, z: 0, l: container.l, w: container.w, h: container.h },
    ];

    // Build flat list of all boxes, sorted by volume descending
    const allBoxes: (BoxType & { typeIdx: number; id: string })[] = [];
    boxTypes.forEach((bt, typeIdx) => {
        for (let i = 0; i < bt.qty; i++) {
            allBoxes.push({ ...bt, typeIdx, id: `${typeIdx}-${i}` });
        }
    });
    allBoxes.sort((a, b) => (b.l * b.w * b.h) - (a.l * a.w * a.h));

    for (const box of allBoxes) {
        let bestFit: number | null = null;
        let bestSI = -1;
        let bestO: Orientation | null = null;
        const bm = { l: box.l / 100, w: box.w / 100, h: box.h / 100 };

        for (let si = 0; si < spaces.length; si++) {
            const sp = spaces[si];
            for (const o of getOrientations(bm)) {
                if (o.l <= sp.l + 0.001 && o.w <= sp.w + 0.001 && o.h <= sp.h + 0.001) {
                    const left = (sp.l * sp.w * sp.h) - (o.l * o.w * o.h);
                    if (bestFit === null || left < bestFit) {
                        bestFit = left; bestSI = si; bestO = { ...o };
                    }
                }
            }
        }

        if (bestSI >= 0 && bestO) {
            const sp = spaces[bestSI];
            const o = bestO;
            placed.push({
                x: sp.x, y: sp.y, z: sp.z,
                l: o.l, w: o.w, h: o.h,
                typeIdx: box.typeIdx,
                label: box.label || `Коробка ${box.typeIdx + 1}`,
            });
            spaces.splice(bestSI, 1);
            if (sp.l - o.l > 0.001) spaces.push({ x: sp.x + o.l, y: sp.y, z: sp.z, l: sp.l - o.l, w: sp.w, h: sp.h });
            if (sp.h - o.h > 0.001) spaces.push({ x: sp.x, y: sp.y + o.h, z: sp.z, l: o.l, w: sp.w, h: sp.h - o.h });
            if (sp.w - o.w > 0.001) spaces.push({ x: sp.x, y: sp.y, z: sp.z + o.w, l: o.l, w: sp.w - o.w, h: o.h });
            spaces.sort((a, b) => (a.y - b.y) || (a.x - b.x) || (a.z - b.z));
        }
    }

    const freeSpaces = spaces.filter(s => s.l * s.w * s.h > 0.001);
    return { placed, freeSpaces };
}

export function packMaxSingleType(container: ContainerSpec, box: BoxType): number {
    const spaces: FreeSpace[] = [
        { x: 0, y: 0, z: 0, l: container.l, w: container.w, h: container.h },
    ];
    const bm = { l: box.l / 100, w: box.w / 100, h: box.h / 100 };
    let count = 0;
    let iter = 5000;

    while (iter-- > 0) {
        let bestFit: number | null = null;
        let bestSI = -1;
        let bestO: Orientation | null = null;

        for (let si = 0; si < spaces.length; si++) {
            const sp = spaces[si];
            for (const o of getOrientations(bm)) {
                if (o.l <= sp.l + 0.001 && o.w <= sp.w + 0.001 && o.h <= sp.h + 0.001) {
                    const left = (sp.l * sp.w * sp.h) - (o.l * o.w * o.h);
                    if (bestFit === null || left < bestFit) {
                        bestFit = left; bestSI = si; bestO = { ...o };
                    }
                }
            }
        }

        if (bestSI < 0 || !bestO) break;
        const sp = spaces[bestSI];
        const o = bestO;
        count++;
        spaces.splice(bestSI, 1);
        if (sp.l - o.l > 0.001) spaces.push({ x: sp.x + o.l, y: sp.y, z: sp.z, l: sp.l - o.l, w: sp.w, h: sp.h });
        if (sp.h - o.h > 0.001) spaces.push({ x: sp.x, y: sp.y + o.h, z: sp.z, l: o.l, w: sp.w, h: sp.h - o.h });
        if (sp.w - o.w > 0.001) spaces.push({ x: sp.x, y: sp.y, z: sp.z + o.w, l: o.l, w: sp.w - o.w, h: o.h });
        spaces.sort((a, b) => (a.y - b.y) || (a.x - b.x) || (a.z - b.z));
    }

    return count;
}

/* ─── Full Calculation ─────────────────────────────────────────────────── */

export function calculatePack(containerKey: string, boxes: BoxType[]): PackResult {
    const container = CONTAINERS[containerKey];
    const { placed, freeSpaces } = packBoxes(container, boxes);
    const cVol = container.l * container.w * container.h;
    const bVol = placed.reduce((s, b) => s + b.l * b.w * b.h, 0);
    const totReq = boxes.reduce((s, b) => s + b.qty, 0);

    const ppt: Record<number, number> = {};
    placed.forEach(b => { ppt[b.typeIdx] = (ppt[b.typeIdx] || 0) + 1; });

    const mpt: Record<number, number> = {};
    boxes.forEach((b, i) => { mpt[i] = packMaxSingleType(container, b); });

    const fVol = freeSpaces.reduce((s, sp) => s + sp.l * sp.w * sp.h, 0);
    let largest: FreeSpace | null = null;
    let lv = 0;
    freeSpaces.forEach(s => {
        const v = s.l * s.w * s.h;
        if (v > lv) { lv = v; largest = s; }
    });

    const maxBX = placed.length > 0 ? Math.max(...placed.map(b => b.x + b.l)) : 0;

    return {
        placed,
        freeSpaces,
        containerVol: cVol,
        boxVol: bVol,
        fillPercent: ((bVol / cVol) * 100).toFixed(1),
        totalPlaced: placed.length,
        totalRequested: totReq,
        placedPerType: ppt,
        maxPerType: mpt,
        freeVol: fVol,
        largestFree: largest,
        freeLength: container.l - maxBX,
        maxBoxX: maxBX,
        freeCount: freeSpaces.length,
    };
}
