// Клиентская генерация PDF со стикерами коробов WB (QR + Code128 + подпись).
// Всё считается в браузере из уже известных кодов коробов (boxcode вида "WB_1618014827").
// Библиотеки (bwip-js, jspdf) грузятся динамически ВНУТРИ функции — чтобы не падать на SSR.

/** Рендерит штрихкод/QR в offscreen-canvas и отдаёт PNG data-URL с пропорциями. */
async function renderBarcode(
    bcid: 'qrcode' | 'code128',
    text: string,
    opts: { scale: number; height?: number; includetext?: boolean },
): Promise<{ dataUrl: string; ratio: number }> {
    const bwipjs = (await import('bwip-js/browser')).default;
    const canvas = document.createElement('canvas');
    // Шлём ТОЛЬКО заданные опции: bwip-js (новые версии) отвергает undefined
    // (`invalidOptionType: height: not a realtype: undefined`) — для QR height не задаём.
    const render: Record<string, unknown> = { bcid, text, scale: opts.scale, textxalign: 'center' };
    if (opts.height !== undefined) render.height = opts.height;
    if (opts.includetext !== undefined) render.includetext = opts.includetext;
    bwipjs.toCanvas(canvas, render as unknown as Parameters<typeof bwipjs.toCanvas>[1]);
    return { dataUrl: canvas.toDataURL('image/png'), ratio: canvas.height / canvas.width };
}

/**
 * PDF со стикерами коробов (`WB_…`) — формат кабинета WB: один ярлык на страницу
 * A4 (по эталонному PDF кабинета). Пустой список → ничего не делает.
 */
export async function downloadBoxStickers(codes: string[], fileName: string): Promise<void> {
    if (codes.length === 0) return;

    const { jsPDF } = await import('jspdf');
    const doc = new jsPDF({ unit: 'mm', format: 'a4', orientation: 'portrait' });

    for (let i = 0; i < codes.length; i++) {
        if (i > 0) doc.addPage();
        await drawWbLabel(doc, codes[i]);
    }

    doc.save(fileName);
}

/**
 * Один ярлык на страницу A4 — как печатает кабинет WB (проверено по эталонным
 * PDF кабинета): крупный QR (~63 мм) слева-сверху, справа от него Code128 и сам
 * код текстом ~18pt. Ярлык занимает верхнюю треть листа, остальное — пусто.
 */
async function drawWbLabel(
    doc: import('jspdf').jsPDF,
    code: string,
): Promise<void> {
    // Геометрия эталона WB: белое поле 595×340 pt ≈ 210×120 мм (вся ширина A4).
    const LABEL_H = 120;
    const QR = 63;
    const QR_X = 55;
    const QR_Y = 12;

    // QR-код — основной носитель кода (его и сканирует склад).
    const qr = await renderBarcode('qrcode', code, { scale: 6 });
    doc.addImage(qr.dataUrl, 'PNG', QR_X, QR_Y, QR, QR, undefined, 'FAST');

    // Code128 под QR — на случай ручного линейного сканера.
    const bc = await renderBarcode('code128', code, { scale: 4, height: 14, includetext: false });
    const bcW = 120;
    const bcH = Math.min(bcW * bc.ratio, 22);
    doc.addImage(bc.dataUrl, 'PNG', (210 - bcW) / 2, QR_Y + QR + 5, bcW, bcH, undefined, 'FAST');

    // Сам код текстом — 18pt, как в эталоне WB.
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(18);
    doc.setTextColor(0);
    doc.text(code, 210 / 2, QR_Y + QR + 5 + bcH + 9, { align: 'center' });

    // Разделительная линия низа ярлыка (в кабинете — край белого поля).
    doc.setDrawColor(220);
    doc.setLineWidth(0.2);
    doc.line(0, LABEL_H, 210, LABEL_H);
}

/**
 * PDF со ШК пропуска поставки (`WB-GI-<barcodeId>`) — формат кабинета WB:
 * один ярлык на страницу A4. Пустой `passBarcode` → ничего не делает (мягко).
 *
 * ВАЖНО: у поставки WB нет собственного «ШК поставки» — на складе сканируют
 * ШК ПРОПУСКА (`WB-GI-…`) и ШК КОРОБОВ (`WB_…`, см. downloadBoxStickers).
 */
export async function downloadPassSticker(opts: {
    passBarcode: string | null;
    fileName: string;
}): Promise<void> {
    const { passBarcode, fileName } = opts;
    if (!passBarcode) return;

    const { jsPDF } = await import('jspdf');
    const doc = new jsPDF({ unit: 'mm', format: 'a4', orientation: 'portrait' });
    await drawWbLabel(doc, passBarcode);
    doc.save(fileName);
}
