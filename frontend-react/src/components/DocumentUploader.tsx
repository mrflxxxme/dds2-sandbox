'use client';

import { useCallback, useState } from 'react';
import type { DocType } from '@/types/api';

interface DocumentUploaderProps {
    onUpload: (file: File, docType: DocType) => Promise<void>;
    disabled?: boolean;
}

const DOC_TYPES: { value: DocType; label: string }[] = [
    { value: 'CONTRACT', label: 'Договор' },
    { value: 'CERTIFICATE', label: 'Сертификат' },
    { value: 'INVOICE', label: 'Счёт' },
    { value: 'OTHER', label: 'Прочее' },
];

const MAX_SIZE_MB = 20;
const ALLOWED_MIME = ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'image/jpeg', 'image/png'];
const ALLOWED_EXT = '.pdf,.docx,.xlsx,.jpg,.jpeg,.png';

export default function DocumentUploader({ onUpload, disabled }: DocumentUploaderProps) {
    const [dragging, setDragging] = useState(false);
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    const [docType, setDocType] = useState<DocType>('CONTRACT');
    const [uploading, setUploading] = useState(false);
    const [error, setError] = useState('');
    const [progress, setProgress] = useState(0);

    const validateFile = (file: File): string | null => {
        if (file.size > MAX_SIZE_MB * 1024 * 1024) {
            return `Файл слишком большой. Максимум ${MAX_SIZE_MB}MB`;
        }
        if (!ALLOWED_MIME.includes(file.type) && file.type !== '') {
            return `Неподдерживаемый тип файла. Разрешены: PDF, DOCX, XLSX, JPG, PNG`;
        }
        return null;
    };

    const handleFile = useCallback((file: File) => {
        const err = validateFile(file);
        if (err) { setError(err); return; }
        setError('');
        setSelectedFile(file);
    }, []);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setDragging(false);
        const file = e.dataTransfer.files[0];
        if (file) handleFile(file);
    }, [handleFile]);

    const handleDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setDragging(true);
    }, []);

    const handleDragLeave = useCallback(() => setDragging(false), []);

    const handleInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file) handleFile(file);
    }, [handleFile]);

    const handleUpload = async () => {
        if (!selectedFile) return;
        setUploading(true);
        setProgress(30);
        setError('');
        try {
            setProgress(60);
            await onUpload(selectedFile, docType);
            setProgress(100);
            setSelectedFile(null);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setUploading(false);
            setProgress(0);
        }
    };

    return (
        <div>
            {/* Drag & Drop zone */}
            <div
                onDrop={handleDrop}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                style={{
                    border: `2px dashed ${dragging ? 'var(--color-accent)' : 'var(--color-border)'}`,
                    borderRadius: 12,
                    padding: 24,
                    textAlign: 'center',
                    cursor: disabled ? 'not-allowed' : 'pointer',
                    background: dragging ? 'rgba(0,113,227,0.04)' : 'transparent',
                    transition: 'all 0.2s',
                    marginBottom: 12,
                }}
                onClick={() => !disabled && document.getElementById('doc-uploader-input')?.click()}
            >
                <input
                    id="doc-uploader-input"
                    type="file"
                    accept={ALLOWED_EXT}
                    style={{ display: 'none' }}
                    onChange={handleInputChange}
                    disabled={disabled}
                />
                {selectedFile ? (
                    <div>
                        <div style={{ fontSize: 24, marginBottom: 8 }}>📄</div>
                        <div style={{ fontSize: 14, fontWeight: 500 }}>{selectedFile.name}</div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                            {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
                        </div>
                    </div>
                ) : (
                    <div>
                        <div style={{ fontSize: 32, marginBottom: 8 }}>📎</div>
                        <div style={{ fontSize: 14, color: 'var(--color-text-muted)' }}>
                            Перетащите файл или нажмите для выбора
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                            PDF, DOCX, XLSX, JPG, PNG до {MAX_SIZE_MB}MB
                        </div>
                    </div>
                )}
            </div>

            {/* Doc type selector + upload button */}
            {selectedFile && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
                    <select
                        className="form-input"
                        style={{ flex: 1 }}
                        value={docType}
                        onChange={e => setDocType(e.target.value as DocType)}
                        disabled={uploading}
                    >
                        {DOC_TYPES.map(t => (
                            <option key={t.value} value={t.value}>{t.label}</option>
                        ))}
                    </select>
                    <button
                        className="btn btn-primary btn-sm"
                        onClick={handleUpload}
                        disabled={uploading || disabled}
                    >
                        {uploading ? 'Загрузка...' : '📤 Загрузить'}
                    </button>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => { setSelectedFile(null); setError(''); }}
                        disabled={uploading}
                    >
                        ✕
                    </button>
                </div>
            )}

            {/* Progress bar */}
            {uploading && progress > 0 && (
                <div style={{ height: 4, background: 'var(--color-border)', borderRadius: 2, overflow: 'hidden', marginBottom: 8 }}>
                    <div
                        style={{
                            height: '100%',
                            width: `${progress}%`,
                            background: 'var(--color-accent)',
                            transition: 'width 0.3s',
                            borderRadius: 2,
                        }}
                    />
                </div>
            )}

            {error && (
                <div style={{ color: 'var(--color-danger)', fontSize: 13 }}>{error}</div>
            )}
        </div>
    );
}
