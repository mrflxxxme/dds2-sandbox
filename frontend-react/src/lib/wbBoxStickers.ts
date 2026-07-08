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
    bwipjs.toCanvas(canvas, {
        bcid,
        text,
        scale: opts.scale,
        height: opts.height,
        includetext: opts.includetext,
        textxalign: 'center',
    });
    return { dataUrl: canvas.toDataURL('image/png'), ratio: canvas.height / canvas.width };
}

/**
 * Собирает PDF (A4, портрет) со стикерами коробов и запускает скачивание.
 * @param codes    коды коробов (уже отфильтрованные от null)
 * @param fileName имя файла для скачивания
 */
export async function downloadBoxStickers(codes: string[], fileName: string): Promise<void> {
    if (codes.length === 0) return;

    const { jsPDF } = await import('jspdf');
    const doc = new jsPDF({ unit: 'mm', format: 'a4', orientation: 'portrait' });

    // Геометрия страницы и сетки 2×3.
    const PAGE_W = 210;
    const PAGE_H = 297;
    const MARGIN = 10;
    const GAP = 10;
    const COLS = 2;
    const ROWS = 3;
    const PER_PAGE = COLS * ROWS;
    const CELL_W = (PAGE_W - 2 * MARGIN - GAP * (COLS - 1)) / COLS; // 90 мм
    const CELL_H = (PAGE_H - 2 * MARGIN - GAP * (ROWS - 1)) / ROWS; // ~85 мм
    const PAD = 4;

    const total = codes.length;

    for (let i = 0; i < total; i++) {
        const code = codes[i];
        const posOnPage = i % PER_PAGE;
        if (i > 0 && posOnPage === 0) doc.addPage();

        const col = posOnPage % COLS;
        const row = Math.floor(posOnPage / COLS);
        const x = MARGIN + col * (CELL_W + GAP);
        const y = MARGIN + row * (CELL_H + GAP);

        // Рамка стикера.
        doc.setDrawColor(200);
        doc.setLineWidth(0.2);
        doc.rect(x, y, CELL_W, CELL_H);

        // Заголовок «Короб N из M».
        doc.setFont('helvetica', 'bold');
        doc.setFontSize(11);
        doc.setTextColor(20);
        doc.text(`Box ${i + 1} / ${total}`, x + CELL_W / 2, y + PAD + 4, { align: 'center' });

        // QR-код (квадрат), по центру.
        const qr = await renderBarcode('qrcode', code, { scale: 4 });
        const QR = 26;
        doc.addImage(qr.dataUrl, 'PNG', x + (CELL_W - QR) / 2, y + PAD + 8, QR, QR, undefined, 'FAST');

        // Штрихкод Code128 с человекочитаемым текстом.
        const bc = await renderBarcode('code128', code, { scale: 3, height: 12, includetext: true });
        const bcW = CELL_W - 2 * PAD;
        const bcH = Math.min(bcW * bc.ratio, 20);
        doc.addImage(bc.dataUrl, 'PNG', x + PAD, y + PAD + 8 + QR + 3, bcW, bcH, undefined, 'FAST');

        // Подпись — сам код короба.
        doc.setFont('helvetica', 'normal');
        doc.setFontSize(10);
        doc.setTextColor(60);
        doc.text(code, x + CELL_W / 2, y + CELL_H - PAD, { align: 'center' });
    }

    doc.save(fileName);
}
