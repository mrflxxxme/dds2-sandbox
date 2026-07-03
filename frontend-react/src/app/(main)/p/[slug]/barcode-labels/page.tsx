'use client';
import { useCallback, useRef, useState } from 'react';
import PageGuard from '@/components/PageGuard';
import {
    LabelRow, LabelSettings, LabelSizeKey, LabelTextAlign,
    LABEL_SIZES, EMPTY_ROW, COLUMN_KEYS,
    parseClipboardTable, applyGridAt, isRowEmpty, parseQuantity, validateRows,
    buildLabelPdf, renderBarcodePng, generateLabelsZip,
    LabelFonts, GenerateProgress,
} from '@/lib/labels/generator';

const COLUMNS: { key: keyof LabelRow; label: string; width: number; placeholder?: string }[] = [
    { key: 'barcode', label: 'Штрихкод', width: 160, placeholder: '2050263290036' },
    { key: 'article', label: 'Артикул', width: 130, placeholder: '946288655' },
    { key: 'color', label: 'Цвет', width: 100, placeholder: 'Серый' },
    { key: 'size', label: 'Размер', width: 90, placeholder: '210x60' },
    { key: 'productName', label: 'Название товара', width: 190, placeholder: 'Диван бескаркасный' },
    { key: 'sellerName', label: 'Наименование продавца', width: 170, placeholder: 'ООО «ПлюсВайб»' },
    { key: 'freeText', label: 'Свободная надпись', width: 150, placeholder: 'divan_darkgrey' },
    { key: 'quantity', label: 'Кол-во', width: 60, placeholder: '1' },
];

const cellInputStyle: React.CSSProperties = {
    width: '100%', border: 'none', outline: 'none', background: 'transparent',
    fontSize: 13, color: '#111827', padding: '8px 10px',
};

let fontsCache: LabelFonts | null = null;
async function loadFonts(): Promise<LabelFonts> {
    if (fontsCache) return fontsCache;
    const [regular, bold] = await Promise.all([
        fetch('/fonts/DejaVuSans.ttf').then(r => { if (!r.ok) throw new Error('Не удалось загрузить шрифт'); return r.arrayBuffer(); }),
        fetch('/fonts/DejaVuSans-Bold.ttf').then(r => { if (!r.ok) throw new Error('Не удалось загрузить шрифт'); return r.arrayBuffer(); }),
    ]);
    fontsCache = { regular, bold };
    return fontsCache;
}

export default function BarcodeLabelsPage() {
    const [rows, setRows] = useState<LabelRow[]>([{ ...EMPTY_ROW }]);
    const [settings, setSettings] = useState<LabelSettings>({
        barcodeFormat: 'CODE128', labelSize: '100x70', textAlign: 'center', sameSeller: false, sellerName: '',
    });
    const [errors, setErrors] = useState<string[]>([]);
    const [progress, setProgress] = useState<GenerateProgress | null>(null);
    const [busy, setBusy] = useState<'zip' | 'preview' | null>(null);
    const zipUrlRef = useRef<string | null>(null);

    const setCell = useCallback((rowIdx: number, key: keyof LabelRow, value: string) => {
        setRows(prev => prev.map((r, i) => (i === rowIdx ? { ...r, [key]: value } : r)));
    }, []);

    // Вставка из Google Таблиц: многоячеечный буфер заполняет таблицу от текущей ячейки
    const handleCellPaste = useCallback((e: React.ClipboardEvent, rowIdx: number, colIdx: number) => {
        const text = e.clipboardData.getData('text/plain');
        if (!text.includes('\t') && !text.includes('\n')) return; // обычная вставка в одну ячейку
        e.preventDefault();
        const grid = parseClipboardTable(text);
        setRows(prev => applyGridAt(prev, grid, rowIdx, colIdx));
    }, []);

    const addRow = useCallback(() => setRows(prev => [...prev, { ...EMPTY_ROW }]), []);
    const deleteRow = useCallback((idx: number) => {
        setRows(prev => (prev.length === 1 ? [{ ...EMPTY_ROW }] : prev.filter((_, i) => i !== idx)));
    }, []);
    const clearAll = useCallback(() => { setRows([{ ...EMPTY_ROW }]); setErrors([]); }, []);

    const activeRows = rows.filter(r => !isRowEmpty(r));
    const totalLabels = activeRows.reduce((a, r) => a + parseQuantity(r.quantity), 0);

    const runValidated = useCallback(async (action: () => Promise<void>, kind: 'zip' | 'preview') => {
        const errs = validateRows(rows);
        setErrors(errs);
        if (errs.length > 0 || activeRows.length === 0) {
            if (activeRows.length === 0) setErrors(['Заполните хотя бы одну строку']);
            return;
        }
        setBusy(kind);
        setProgress(null);
        try {
            await action();
        } catch (e) {
            setErrors([e instanceof Error ? e.message : 'Ошибка генерации']);
        } finally {
            setBusy(null);
            setProgress(null);
        }
    }, [rows, activeRows.length]);

    const downloadZip = useCallback(() => runValidated(async () => {
        const fonts = await loadFonts();
        const blob = await generateLabelsZip(rows, settings, fonts, renderBarcodePng, setProgress);
        if (zipUrlRef.current) URL.revokeObjectURL(zipUrlRef.current);
        const url = URL.createObjectURL(blob);
        zipUrlRef.current = url;
        const a = document.createElement('a');
        a.href = url;
        a.download = `наклейки_${settings.labelSize}мм_${activeRows.length}шт.zip`;
        a.click();
    }, 'zip'), [rows, settings, activeRows.length, runValidated]);

    const previewFirst = useCallback(() => runValidated(async () => {
        const fonts = await loadFonts();
        const row = rows.find(r => !isRowEmpty(r));
        if (!row) return;
        const png = await renderBarcodePng(row.barcode.trim());
        const pdf = await buildLabelPdf(row, settings, fonts, png);
        const blob = new Blob([pdf as BlobPart], { type: 'application/pdf' });
        window.open(URL.createObjectURL(blob), '_blank');
    }, 'preview'), [rows, settings, runValidated]);

    return (
        <PageGuard page="barcode-labels">
            <div className="animate-in">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
                    <div>
                        <h1 style={{ fontSize: 28, fontWeight: 700, margin: 0 }}>🏷️ Генератор ШК</h1>
                        <p style={{ fontSize: 13, color: 'var(--color-text-dim)', margin: '6px 0 0' }}>
                            Наклейки со штрихкодами для Wildberries/Ozon. Вставьте данные из Google Таблиц (Ctrl+V) прямо в таблицу — колонки в том же порядке.
                        </p>
                    </div>
                </div>

                {/* Настройки */}
                <div className="glass-card" style={{ padding: '14px 20px', marginBottom: 12, display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'center' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--color-text-dim)' }}>
                        Формат ШК:
                        <select value={settings.barcodeFormat} disabled
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' }}>
                            <option value="CODE128">CODE-128</option>
                        </select>
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--color-text-dim)' }}>
                        Размер наклейки:
                        <select value={settings.labelSize}
                            onChange={e => setSettings(s => ({ ...s, labelSize: e.target.value as LabelSizeKey }))}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' }}>
                            {(Object.keys(LABEL_SIZES) as LabelSizeKey[]).map(k => <option key={k} value={k}>{k.replace('x', '×')} мм</option>)}
                        </select>
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--color-text-dim)' }}>
                        Положение текста:
                        <select value={settings.textAlign}
                            onChange={e => setSettings(s => ({ ...s, textAlign: e.target.value as LabelTextAlign }))}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' }}>
                            <option value="center">По центру</option>
                            <option value="left">Слева</option>
                        </select>
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--color-text)', cursor: 'pointer' }}>
                        <input type="checkbox" checked={settings.sameSeller}
                            onChange={e => setSettings(s => ({ ...s, sameSeller: e.target.checked }))} />
                        Одинаковое название поставщика на всех наклейках
                    </label>
                    {settings.sameSeller && (
                        <input value={settings.sellerName} placeholder="ООО «ПлюсВайб»"
                            onChange={e => setSettings(s => ({ ...s, sellerName: e.target.value }))}
                            style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)', width: 220 }} />
                    )}
                </div>

                {/* Ошибки */}
                {errors.length > 0 && (
                    <div className="glass-card" style={{ padding: '12px 20px', marginBottom: 12, border: '1px solid var(--color-danger)' }}>
                        {errors.map((e, i) => <div key={i} style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {e}</div>)}
                    </div>
                )}

                {/* Таблица */}
                <div className="glass-card" style={{ padding: 0, overflow: 'hidden', marginBottom: 12 }}>
                    <div style={{ overflowX: 'auto' }}>
                        <table className="data-table" style={{ borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#ffffff', width: '100%', minWidth: 1100 }}>
                            <thead>
                                <tr>
                                    <th style={{ width: 36, background: '#f9fafb', color: '#4b5563', fontSize: 11, borderBottom: '2px solid #e5e7eb', padding: '8px 6px' }}>#</th>
                                    {COLUMNS.map(c => (
                                        <th key={c.key} style={{ minWidth: c.width, background: '#f9fafb', color: '#4b5563', fontSize: 11, textAlign: 'left', borderBottom: '2px solid #e5e7eb', padding: '8px 10px' }}>{c.label}</th>
                                    ))}
                                    <th style={{ width: 40, background: '#f9fafb', borderBottom: '2px solid #e5e7eb' }} />
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row, ri) => (
                                    <tr key={ri} style={{ background: ri % 2 === 0 ? '#ffffff' : '#f9fafb' }}>
                                        <td style={{ textAlign: 'center', fontSize: 12, color: '#9ca3af', borderBottom: '1px solid #f3f4f6' }}>{ri + 1}</td>
                                        {COLUMNS.map((c, ci) => (
                                            <td key={c.key} style={{ borderBottom: '1px solid #f3f4f6', borderLeft: '1px solid #f3f4f6', padding: 0 }}>
                                                <input
                                                    value={row[c.key]}
                                                    placeholder={ri === 0 ? c.placeholder : undefined}
                                                    onChange={e => setCell(ri, c.key, e.target.value)}
                                                    onPaste={e => handleCellPaste(e, ri, ci)}
                                                    style={cellInputStyle}
                                                />
                                            </td>
                                        ))}
                                        <td style={{ textAlign: 'center', borderBottom: '1px solid #f3f4f6' }}>
                                            <button onClick={() => deleteRow(ri)} title="Удалить строку"
                                                style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#ef4444', fontSize: 14 }}>✕</button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <div style={{ padding: '10px 16px', borderTop: '1px solid #e5e7eb', background: '#f9fafb', display: 'flex', gap: 12, alignItems: 'center' }}>
                        <button onClick={addRow} className="btn btn-secondary btn-sm" style={{ fontSize: 13 }}>+ Добавить строку</button>
                        <button onClick={clearAll} className="btn btn-secondary btn-sm" style={{ fontSize: 13 }}>Очистить</button>
                        <span style={{ fontSize: 12, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>
                            Строк: {activeRows.length} · Наклеек: {totalLabels}
                        </span>
                    </div>
                </div>

                {/* Действия */}
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                    <button onClick={downloadZip} disabled={busy !== null} className="btn btn-primary"
                        style={{ fontSize: 14, opacity: busy ? 0.6 : 1 }}>
                        {busy === 'zip'
                            ? (progress ? `Генерация ${progress.done}/${progress.total}…` : 'Генерация…')
                            : `📦 Скачать ZIP (${totalLabels} накл.)`}
                    </button>
                    <button onClick={previewFirst} disabled={busy !== null} className="btn btn-secondary"
                        style={{ fontSize: 14, opacity: busy ? 0.6 : 1 }}>
                        {busy === 'preview' ? 'Генерация…' : '👁 Предпросмотр'}
                    </button>
                    <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                        ZIP: отдельный PDF на каждую строку, имя файла — из «Свободной надписи»; кол-во — страницы в PDF.
                    </span>
                </div>
            </div>
        </PageGuard>
    );
}
