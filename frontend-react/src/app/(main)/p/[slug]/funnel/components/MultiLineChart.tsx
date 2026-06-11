'use client';
import { useEffect, useRef, useState } from 'react';

/* ─── Multi-line overlay chart (all selected metrics on one canvas) ── */

// Trailing moving average over `window` points (window=7 → сглаживание недельной пилы)
function movingAvg(values: number[], window: number): number[] {
    return values.map((_, i) => {
        const from = Math.max(0, i - window + 1);
        const slice = values.slice(from, i + 1);
        return slice.reduce((s, v) => s + v, 0) / slice.length;
    });
}

export function MultiLineChart({ data, lines }: {
    data: any[];
    lines: { field: string; label: string; color: string }[];
}) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const [smooth, setSmooth] = useState(false);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas || !data.length || !lines.length) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const dpr = window.devicePixelRatio || 1;
        const W = canvas.clientWidth;
        const H = canvas.clientHeight;
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        ctx.scale(dpr, dpr);

        // Get all unique dates
        const dateSet = new Set<string>();
        data.forEach(r => dateSet.add(r.date));
        const dates = Array.from(dateSet).sort();
        if (!dates.length) return;

        const padTop = 20, padBottom = 35, padLeft = 60, padRight = 20;
        const chartW = W - padLeft - padRight;
        const chartH = H - padTop - padBottom;
        const xStep = dates.length > 1 ? chartW / (dates.length - 1) : chartW;

        ctx.clearRect(0, 0, W, H);

        // Weekend shading — вертикальные полосы сб/вс объясняют недельную сезонность
        dates.forEach((d, i) => {
            const day = new Date(d + 'T00:00:00').getDay();
            if (day !== 0 && day !== 6) return;
            const x0 = Math.max(padLeft, padLeft + i * xStep - xStep / 2);
            const x1 = Math.min(W - padRight, padLeft + i * xStep + xStep / 2);
            ctx.fillStyle = 'rgba(255,255,255,0.045)';
            ctx.fillRect(x0, padTop, x1 - x0, chartH);
        });

        // Grid lines
        ctx.strokeStyle = 'rgba(255,255,255,0.06)';
        ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i++) {
            const y = padTop + (chartH * i) / 4;
            ctx.beginPath();
            ctx.moveTo(padLeft, y);
            ctx.lineTo(W - padRight, y);
            ctx.stroke();
        }

        // Draw each line with its own normalization
        lines.forEach((line) => {
            const byDate: Record<string, number> = {};
            data.forEach(r => {
                byDate[r.date] = (byDate[r.date] || 0) + (r[line.field] || 0);
            });
            let values = dates.map(d => byDate[d] || 0);
            if (smooth) values = movingAvg(values, 7);
            const maxVal = Math.max(...values, 1);
            const minVal = Math.min(...values, 0);
            const range = maxVal - minVal || 1;

            // Line path
            ctx.beginPath();
            values.forEach((v, i) => {
                const x = padLeft + i * xStep;
                const y = padTop + chartH - ((v - minVal) / range) * chartH;
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            });
            ctx.strokeStyle = line.color;
            ctx.lineWidth = 2;
            ctx.stroke();

            // Subtle fill under
            const gradient = ctx.createLinearGradient(0, padTop, 0, H - padBottom);
            gradient.addColorStop(0, line.color + '18');
            gradient.addColorStop(1, line.color + '02');
            ctx.lineTo(padLeft + (values.length - 1) * xStep, padTop + chartH);
            ctx.lineTo(padLeft, padTop + chartH);
            ctx.closePath();
            ctx.fillStyle = gradient;
            ctx.fill();

            // Dots — при сглаживании точки сырых значений не рисуем
            if (!smooth) {
                values.forEach((v, i) => {
                    const x = padLeft + i * xStep;
                    const y = padTop + chartH - ((v - minVal) / range) * chartH;
                    ctx.beginPath();
                    ctx.arc(x, y, 2.5, 0, Math.PI * 2);
                    ctx.fillStyle = line.color;
                    ctx.fill();
                });
            }
        });

        // X labels
        const labelEvery = Math.max(1, Math.floor(dates.length / 14));
        ctx.fillStyle = 'rgba(255,255,255,0.45)';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        dates.forEach((d, i) => {
            if (i % labelEvery === 0 || i === dates.length - 1) {
                const x = padLeft + i * xStep;
                ctx.fillText(d.slice(5), x, H - padBottom + 14);
            }
        });

        // Legend in top-right corner
        ctx.textAlign = 'left';
        ctx.font = '11px sans-serif';
        let legendX = W - padRight - 10;
        ctx.textAlign = 'right';
        lines.slice().reverse().forEach((line, i) => {
            const y = padTop + 4 + i * 16;
            ctx.fillStyle = line.color;
            ctx.fillRect(legendX - ctx.measureText(line.label).width - 16, y - 4, 10, 10);
            ctx.fillStyle = 'rgba(255,255,255,0.7)';
            ctx.fillText(line.label, legendX, y + 5);
        });
    }, [data, lines, smooth]);

    return (
        <div className="glass-card" style={{ marginBottom: 12, padding: '12px 16px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-dim)' }}>
                    Динамика по дням <span style={{ fontSize: 11, opacity: 0.7 }}>(серые полосы — выходные)</span>
                </div>
                <button onClick={() => setSmooth(v => !v)}
                    title="Скользящая средняя за 7 дней — тренд без недельной пилы"
                    style={{
                        padding: '3px 10px', fontSize: 11, fontWeight: 500, cursor: 'pointer', borderRadius: 12,
                        border: '1px solid ' + (smooth ? 'var(--color-accent)' : 'var(--color-border)'),
                        background: smooth ? 'var(--color-accent)' : 'transparent',
                        color: smooth ? '#fff' : 'var(--color-text-dim)',
                    }}>MA-7</button>
            </div>
            <canvas ref={canvasRef}
                style={{ width: '100%', height: 200, borderRadius: 8 }} />
        </div>
    );
}
