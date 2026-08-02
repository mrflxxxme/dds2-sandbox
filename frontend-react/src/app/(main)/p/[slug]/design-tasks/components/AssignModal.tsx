'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import ModalShell from './ModalShell';

interface MemberOption {
    user_id: number;
    label: string;
}

interface AssignModalProps {
    slug: string;
    currentAssigneeId: number | null;
    onAssign: (userId: number | null) => Promise<void>;
    onClose: () => void;
}

/** Назначение исполнителя (только lead; право проверяет бэк). */
export default function AssignModal({ slug, currentAssigneeId, onAssign, onClose }: AssignModalProps) {
    const [members, setMembers] = useState<MemberOption[] | null>(null);
    const [selected, setSelected] = useState<number | ''>(currentAssigneeId ?? '');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const mountedRef = useRef(true);

    useEffect(() => {
        mountedRef.current = true;
        api.getMembers(slug)
            .then((rows) => {
                if (!mountedRef.current) return;
                setMembers(rows.map((m) => ({
                    user_id: m.user_id,
                    label: [m.first_name, m.last_name].filter(Boolean).join(' ') || m.username,
                })));
            })
            .catch((e) => {
                if (mountedRef.current) setError(e instanceof Error ? e.message : 'Не удалось загрузить участников');
            });
        return () => { mountedRef.current = false; };
    }, [slug]);

    const submit = async (userId: number | null) => {
        setBusy(true);
        setError('');
        try {
            await onAssign(userId);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось назначить');
        } finally {
            if (mountedRef.current) setBusy(false);
        }
    };

    return (
        <ModalShell title="Назначить исполнителя" onClose={onClose}>
            {members === null && !error && <div style={{ color: 'var(--color-muted)' }}>Загрузка участников…</div>}
            {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 8 }}>{error}</div>}
            {members !== null && (
                <>
                    <select
                        className="form-input"
                        style={{ width: '100%' }}
                        value={selected}
                        onChange={(e) => setSelected(e.target.value === '' ? '' : Number(e.target.value))}
                    >
                        <option value="">— не выбран —</option>
                        {members.map((m) => <option key={m.user_id} value={m.user_id}>{m.label}</option>)}
                    </select>
                    <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                        {currentAssigneeId != null && (
                            <button className="btn btn-secondary" disabled={busy} onClick={() => void submit(null)}>
                                Снять исполнителя
                            </button>
                        )}
                        <button
                            className="btn btn-primary"
                            disabled={busy || selected === ''}
                            onClick={() => void submit(selected === '' ? null : selected)}
                        >
                            {busy ? '…' : 'Назначить'}
                        </button>
                    </div>
                </>
            )}
        </ModalShell>
    );
}
