'use client';
import { useEffect, useRef } from 'react';

/* ─── Day-analysis trend chart ─────────────────────────────────── */

export function DayTrendChart({ data, fields, targetDate }: {
    data: any[];
    fields: { key: string; label: string; color: string }[];
    targetDate: string;
}) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas || data.length === 0) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        const dpr = window.devicePixelRatio || 1;
        const W = canvas.clientWidth, H = canvas.clientHeight;
        canvas.width = W * dpr; canvas.height = H * dpr;
        ctx.scale(dpr, dpr); ctx.clearRect(0, 0, W, H);
        const pad = { top: 20, right: 20, bottom: 28, left: 10 };
        const cw = W - pad.left - pad.right, ch = H - pad.top - pad.bottom;
        ctx.fillStyle = '#666'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
        data.forEach((d, i) => {
            const x = pad.left + (i / (data.length - 1 || 1)) * cw;
            if (i % Math.max(1, Math.floor(data.length / 8)) === 0 || i === data.length - 1)
                ctx.fillText(d.date.slice(5), x, H - 6);
        });
        const targetIdx = data.findIndex((d: any) => d.date === targetDate);
        if (targetIdx >= 0) {
            const x = pad.left + (targetIdx / (data.length - 1 || 1)) * cw;
            ctx.fillStyle = 'rgba(139,92,246,0.12)'; ctx.fillRect(x - 12, pad.top, 24, ch);
        }
        fields.forEach(f => {
            const vals = data.map((d: any) => Number(d[f.key] || 0));
            const max = Math.max(...vals, 1), min = Math.min(...vals, 0), range = max - min || 1;
            ctx.beginPath(); ctx.strokeStyle = f.color; ctx.lineWidth = 2;
            vals.forEach((v, i) => {
                const x = pad.left + (i / (data.length - 1 || 1)) * cw;
                const y = pad.top + ch - ((v - min) / range) * ch;
                i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
            });
            ctx.stroke();
            if (targetIdx >= 0) {
                const x = pad.left + (targetIdx / (data.length - 1 || 1)) * cw;
                const y = pad.top + ch - ((vals[targetIdx] - min) / range) * ch;
                ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fillStyle = f.color; ctx.fill();
            }
        });
    }, [data, fields, targetDate]);
    return (
        <div style={{ position: 'relative' }}>
            <canvas ref={canvasRef} style={{ width: '100%', height: 200, display: 'block' }} />
            <div style={{ display: 'flex', gap: 16, padding: '6px 0', justifyContent: 'center', flexWrap: 'wrap' }}>
                {fields.map(f => (
                    <span key={f.key} style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ width: 10, height: 10, borderRadius: 2, background: f.color, display: 'inline-block' }} />
                        {f.label}
                    </span>
                ))}
            </div>
        </div>
    );
}
