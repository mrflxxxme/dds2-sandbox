'use client';

import { useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import PageGuard from '@/components/PageGuard';
import PageHeader from '@/components/PageHeader';
import WbThumb from '@/components/WbThumb';
import { DESIGN_WORK_TYPE_LABEL, isValidHttpUrl } from '@/lib/design';
import type { DesignProductSuggestion, DesignWorkType } from '@/types/api';
import ProductSuggest from '../components/ProductSuggest';
import PeriodPicker from '@/components/PeriodPicker';

const WORK_TYPES = Object.keys(DESIGN_WORK_TYPE_LABEL) as DesignWorkType[];
const MAX_FILE_MB = 20;

type PendingMaterial =
    | { kind: 'FILE'; file: File }
    | { kind: 'LINK'; url: string; caption: string }
    | { kind: 'NM'; ref_nm_id: number; caption: string };

function materialLabel(m: PendingMaterial): string {
    if (m.kind === 'FILE') return m.file.name;
    if (m.kind === 'LINK') return m.caption ? `${m.caption} — ${m.url}` : m.url;
    return `Артикул ${m.ref_nm_id}${m.caption ? ` — ${m.caption}` : ''}`;
}

export default function DesignTaskCreatePage() {
    const params = useParams<{ slug: string }>();
    const router = useRouter();

    const [title, setTitle] = useState('');
    const [nmId, setNmId] = useState<number | null>(null);
    const [picked, setPicked] = useState<DesignProductSuggestion | null>(null);
    const [description, setDescription] = useState('');
    const [sheetUrl, setSheetUrl] = useState('');
    const [workType, setWorkType] = useState<DesignWorkType>('FULL_SET');
    const [isUrgent, setIsUrgent] = useState(false);
    const [dueDate, setDueDate] = useState('');

    const [materials, setMaterials] = useState<PendingMaterial[]>([]);
    const [matTab, setMatTab] = useState<'FILE' | 'LINK' | 'NM'>('FILE');
    const [linkUrl, setLinkUrl] = useState('');
    const [linkCaption, setLinkCaption] = useState('');
    const [nmInput, setNmInput] = useState('');
    const fileInputRef = useRef<HTMLInputElement | null>(null);

    const [submitting, setSubmitting] = useState(false);
    const [formError, setFormError] = useState('');
    const [created, setCreated] = useState<{ id: number; number: string; failures: string[] } | null>(null);

    const addFiles = (list: FileList | null) => {
        if (!list) return;
        const next: PendingMaterial[] = [];
        for (const f of Array.from(list)) {
            if (f.size > MAX_FILE_MB * 1024 * 1024) {
                setFormError(`Файл «${f.name}» больше ${MAX_FILE_MB} МБ`);
                continue;
            }
            next.push({ kind: 'FILE', file: f });
        }
        if (next.length) setMaterials((m) => [...m, ...next]);
        if (fileInputRef.current) fileInputRef.current.value = '';
    };

    const addLink = () => {
        const url = linkUrl.trim();
        if (!isValidHttpUrl(url)) {
            setFormError('Ссылка материала должна быть корректным http(s)-URL');
            return;
        }
        setMaterials((m) => [...m, { kind: 'LINK', url, caption: linkCaption.trim() }]);
        setLinkUrl('');
        setLinkCaption('');
        setFormError('');
    };

    const addNm = (ref: number, caption = '') => {
        if (!Number.isInteger(ref) || ref < 1) {
            setFormError('Артикул WB — положительное число');
            return;
        }
        setMaterials((m) => [...m, { kind: 'NM', ref_nm_id: ref, caption }]);
        setNmInput('');
        setFormError('');
    };

    const validate = (): string | null => {
        if (!title.trim()) return 'Укажите название товара';
        if (description.trim().length < 10) return 'Суть заявки — минимум 10 символов';
        if (!isValidHttpUrl(sheetUrl.trim())) return 'Ссылка на ТЗ-таблицу обязательна и должна быть корректным http(s)-URL';
        return null;
    };

    const handleSubmit = async () => {
        const err = validate();
        if (err) {
            setFormError(err);
            return;
        }
        setFormError('');
        setSubmitting(true);
        try {
            const task = await api.createDesignTask({
                title: title.trim(),
                description: description.trim(),
                sheet_url: sheetUrl.trim(),
                nm_id: nmId,
                work_type: workType,
                is_urgent: isUrgent,
                due_date: dueDate || null,
            });

            // Материалы — отдельными ручками после создания задачи.
            const failures: string[] = [];
            for (const m of materials) {
                try {
                    if (m.kind === 'FILE') {
                        await api.uploadDesignMaterialFile(task.id, m.file);
                    } else if (m.kind === 'LINK') {
                        await api.addDesignMaterial(task.id, { kind: 'LINK', url: m.url, caption: m.caption || null });
                    } else {
                        await api.addDesignMaterial(task.id, { kind: 'NM', ref_nm_id: m.ref_nm_id, caption: m.caption || null });
                    }
                } catch (e) {
                    failures.push(`${materialLabel(m)}: ${e instanceof Error ? e.message : 'ошибка'}`);
                }
            }

            if (failures.length) {
                setCreated({ id: task.id, number: task.number, failures });
            } else {
                router.push(`/p/${params.slug}/design-tasks/${task.id}`);
            }
        } catch (e) {
            setFormError(e instanceof Error ? e.message : 'Не удалось создать заявку');
        } finally {
            setSubmitting(false);
        }
    };

    if (created) {
        return (
            <PageGuard page="design-tasks">
                <PageHeader title="🖌️ Новая заявка" />
                <div className="glass-card">
                    <p style={{ marginBottom: 8 }}>
                        Заявка <b>{created.number}</b> создана, но часть материалов не загрузилась — добавьте их в карточке:
                    </p>
                    <ul style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 16, paddingLeft: 20 }}>
                        {created.failures.map((f, i) => <li key={i}>{f}</li>)}
                    </ul>
                    <button className="btn btn-primary" onClick={() => router.push(`/p/${params.slug}/design-tasks/${created.id}`)}>
                        Открыть задачу
                    </button>
                </div>
            </PageGuard>
        );
    }

    return (
        <PageGuard page="design-tasks">
            <PageHeader
                title="🖌️ Новая заявка на дизайн"
                subtitle="Исполнителя назначит ведущий дизайнер после просмотра заявки"
                actions={
                    <button className="btn btn-secondary btn-sm" onClick={() => router.push(`/p/${params.slug}/design-tasks`)}>
                        ← К доске
                    </button>
                }
            />

            <div className="glass-card" style={{ maxWidth: 720 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                    <div>
                        <label style={{ display: 'block', fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 6 }}>
                            Название товара * <span style={{ color: 'var(--color-text-dim)' }}>(подсказка привяжет артикул, но не обязывает)</span>
                        </label>
                        <ProductSuggest
                            value={title}
                            onChange={setTitle}
                            autoFocus
                            placeholder="Например: Ковёр детский 120×180"
                            onPick={(s) => {
                                setNmId(s.nm_id);
                                setPicked(s);
                                if (!title.trim()) setTitle(s.article_seller || String(s.nm_id));
                            }}
                        />
                        {nmId != null && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
                                <WbThumb nmId={nmId} size={28} height={36} />
                                <span style={{ fontSize: 13 }}>
                                    Привязан артикул <b>{nmId}</b>
                                    {picked?.brand ? ` · ${picked.brand}` : ''}
                                    {picked?.subject ? ` · ${picked.subject}` : ''}
                                </span>
                                <button className="btn btn-sm btn-secondary" onClick={() => { setNmId(null); setPicked(null); }}>
                                    Отвязать
                                </button>
                            </div>
                        )}
                    </div>

                    <div>
                        <label style={{ display: 'block', fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 6 }}>
                            Суть заявки * <span style={{ color: 'var(--color-text-dim)' }}>(2–3 строки, минимум 10 символов)</span>
                        </label>
                        <textarea
                            className="form-input"
                            style={{ width: '100%', minHeight: 90, resize: 'vertical' }}
                            value={description}
                            maxLength={5000}
                            placeholder="Что нужно сделать и на что обратить внимание"
                            onChange={(e) => setDescription(e.target.value)}
                        />
                    </div>

                    <div>
                        <label style={{ display: 'block', fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 6 }}>
                            Ссылка на ТЗ-таблицу (Google Sheets) *
                        </label>
                        <input
                            className="form-input"
                            style={{ width: '100%' }}
                            value={sheetUrl}
                            placeholder="https://docs.google.com/spreadsheets/…"
                            onChange={(e) => setSheetUrl(e.target.value)}
                        />
                    </div>

                    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                        <div>
                            <label style={{ display: 'block', fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 6 }}>Тип работы</label>
                            <select className="form-input" value={workType} onChange={(e) => setWorkType(e.target.value as DesignWorkType)}>
                                {WORK_TYPES.map((w) => <option key={w} value={w}>{DESIGN_WORK_TYPE_LABEL[w]}</option>)}
                            </select>
                        </div>
                        <div>
                            <label style={{ display: 'block', fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 6 }}>Срок</label>
                            <PeriodPicker
                                mode="single"
                                from={dueDate}
                                to={dueDate}
                                onApply={(d) => setDueDate(d)}
                                placeholder="Срок не задан"
                                minWidth={180}
                            />
                        </div>
                        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 14, cursor: 'pointer', paddingBottom: 8 }}>
                            <input type="checkbox" checked={isUrgent} onChange={(e) => setIsUrgent(e.target.checked)} />
                            🔥 Срочно
                        </label>
                    </div>

                    <div>
                        <label style={{ display: 'block', fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 6 }}>Исходные материалы</label>
                        <div style={{ display: 'flex', gap: 4, marginBottom: 8 }}>
                            {([['FILE', 'Файл'], ['LINK', 'Ссылка'], ['NM', 'Артикул-пример']] as const).map(([k, label]) => (
                                <button
                                    key={k}
                                    className={`btn btn-sm ${matTab === k ? 'btn-primary' : 'btn-secondary'}`}
                                    onClick={() => setMatTab(k)}
                                >
                                    {label}
                                </button>
                            ))}
                        </div>

                        {matTab === 'FILE' && (
                            <div>
                                <input
                                    ref={fileInputRef}
                                    type="file"
                                    multiple
                                    accept="image/*,.pdf,.zip,.rar"
                                    style={{ display: 'none' }}
                                    onChange={(e) => addFiles(e.target.files)}
                                />
                                <button className="btn btn-secondary btn-sm" onClick={() => fileInputRef.current?.click()}>
                                    📎 Выбрать файлы (до {MAX_FILE_MB} МБ: изображения, PDF, ZIP, RAR)
                                </button>
                            </div>
                        )}
                        {matTab === 'LINK' && (
                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                                <input className="form-input" style={{ flex: 2, minWidth: 220 }} placeholder="https://…" value={linkUrl} onChange={(e) => setLinkUrl(e.target.value)} />
                                <input className="form-input" style={{ flex: 1, minWidth: 140 }} placeholder="Подпись (необязательно)" value={linkCaption} onChange={(e) => setLinkCaption(e.target.value)} />
                                <button className="btn btn-secondary btn-sm" onClick={addLink}>Добавить</button>
                            </div>
                        )}
                        {matTab === 'NM' && (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                                <ProductSuggest
                                    value={nmInput}
                                    onChange={setNmInput}
                                    placeholder="Поиск товара-примера или номер артикула"
                                    onPick={(s) => addNm(s.nm_id, [s.brand, s.subject].filter(Boolean).join(' · '))}
                                />
                                {/^\d+$/.test(nmInput.trim()) && (
                                    <button className="btn btn-secondary btn-sm" style={{ alignSelf: 'flex-start' }} onClick={() => addNm(Number(nmInput.trim()))}>
                                        Добавить артикул {nmInput.trim()}
                                    </button>
                                )}
                            </div>
                        )}

                        {materials.length > 0 && (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 10 }}>
                                {materials.map((m, i) => (
                                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px' }}>
                                        <span>{m.kind === 'FILE' ? '📎' : m.kind === 'LINK' ? '🔗' : '🛒'}</span>
                                        {m.kind === 'NM' && <WbThumb nmId={m.ref_nm_id} size={22} height={28} />}
                                        <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{materialLabel(m)}</span>
                                        <button
                                            className="btn btn-sm btn-secondary"
                                            style={{ marginLeft: 'auto' }}
                                            onClick={() => setMaterials((list) => list.filter((_, j) => j !== i))}
                                        >
                                            ✕
                                        </button>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {formError && <div style={{ color: 'var(--color-danger)', fontSize: 13 }}>{formError}</div>}

                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-primary" onClick={() => void handleSubmit()} disabled={submitting}>
                            {submitting ? 'Создание…' : 'Создать заявку'}
                        </button>
                        <button className="btn btn-secondary" onClick={() => router.push(`/p/${params.slug}/design-tasks`)} disabled={submitting}>
                            Отмена
                        </button>
                    </div>
                </div>
            </div>
        </PageGuard>
    );
}
