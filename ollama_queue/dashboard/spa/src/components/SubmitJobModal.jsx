import { useEffect, useRef, useState } from 'preact/hooks';
import { settings, submitJob } from '../stores';
import { useActionFeedback } from '../hooks/useActionFeedback.js';
import PrioritySelector from './PrioritySelector.jsx';

const inputStyle = {
    fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)',
    background: 'var(--bg-surface-raised)', color: 'var(--text-primary)',
    border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius)',
    padding: '0.3rem 0.5rem', width: '100%', boxSizing: 'border-box',
};
const labelStyle = {
    fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
    color: 'var(--text-tertiary)', fontWeight: 600,
    textTransform: 'uppercase', letterSpacing: '0.03em',
    marginBottom: '0.2rem', display: 'block',
};

// What it shows: A + floating action button (fixed bottom-right). Tapping it opens a form
//   to submit a one-off job directly from the dashboard without using the CLI.
// Decision it drives: Run a command through the queue right now with a chosen priority and
//   timeout. Pre-fills defaults from Settings so usually only the command field needs typing.
//   After submit, a toast confirms the job_id and the queue list refreshes.
// What it shows: A modal form for submitting a one-off job to the queue — command, source,
//   model, priority, and timeout. When open/onClose props are provided, the component runs
//   in controlled mode (caller drives open state); otherwise it manages its own open state
//   via an internal FAB. The internal FAB is hidden in controlled mode to avoid a duplicate button.
// Decision it drives: Submit a job directly from the dashboard from any tab (Sidebar button or
//   mobile FAB triggers the modal), not just from the Now page.
export default function SubmitJobModal({ onJobSubmitted, open: controlledOpen, onClose: controlledOnClose }) {
    const isControlled = controlledOpen !== undefined;
    const [internalOpen, setInternalOpen] = useState(false);
    const open = isControlled ? controlledOpen : internalOpen;
    const setOpen = isControlled
        ? (val) => { if (!val && controlledOnClose) controlledOnClose(); }
        : setInternalOpen;
    const [command, setCommand] = useState('');
    const [source, setSource] = useState('dashboard');
    const [model, setModel] = useState('');
    const [priority, setPriority] = useState(5);
    const [timeout, setTimeout_] = useState(120);
    const [error, setError] = useState(null);
    const [fb, act] = useActionFeedback();
    const dialogRef = useRef(null);

    // Sync form defaults when settings signal updates
    useEffect(() => {
        const unsubscribe = settings.subscribe((s) => {
            if (s) {
                setPriority(prev => prev === 5 ? (s.default_priority ?? 5) : prev);
                setTimeout_(prev => prev === 120 ? (s.default_timeout_seconds ?? 120) : prev);
            }
        });
        return unsubscribe;
    }, []);

    // Open/close the native dialog via ref
    useEffect(() => {
        const dialog = dialogRef.current;
        if (!dialog) return;
        if (open) {
            dialog.showModal();
        } else if (dialog.open) {
            dialog.close();
        }
    }, [open]);

    // Sync open state when dialog is closed natively (Escape key)
    useEffect(() => {
        const dialog = dialogRef.current;
        if (!dialog) return;
        const handleClose = () => setOpen(false);
        dialog.addEventListener('close', handleClose);
        return () => dialog.removeEventListener('close', handleClose);
    }, []);

    function handleBackdropClick(e) {
        if (e.target === dialogRef.current) {
            setOpen(false);
        }
    }

    function resetForm() {
        setCommand('');
        setSource('dashboard');
        setModel('');
        setPriority(settings.value?.default_priority ?? 5);
        setTimeout_(settings.value?.default_timeout_seconds ?? 120);
        setError(null);
    }

    function validate() {
        if (!command.trim()) return 'COMMAND REQUIRED';
        if (!source.trim()) return 'SOURCE REQUIRED';
        const p = Number(priority);
        if (!Number.isInteger(p) || p < 1 || p > 10) return 'PRIORITY INVALID — MUST BE 1–10';
        const t = Number(timeout);
        if (!Number.isInteger(t) || t < 1) return 'TIMEOUT INVALID — POSITIVE INTEGER REQUIRED';
        return null;
    }

    async function handleSubmit(e) {
        e.preventDefault();
        const validationError = validate();
        if (validationError) {
            setError(validationError);
            return;
        }
        setError(null);

        await act(
            'SUBMITTING',
            async () => {
                const body = {
                    command: command.trim(),
                    source: source.trim(),
                    priority: Number(priority),
                    timeout: Number(timeout),
                };
                if (model.trim()) {
                    body.model = model.trim();
                }
                const result = await submitJob(body);
                if (onJobSubmitted) onJobSubmitted(result.job_id);
                setTimeout(() => {
                    setOpen(false);
                    resetForm();
                }, 1500);
                return result;
            },
            result => `JOB #${result.job_id} QUEUED`,
        );
    }

    function handleCancel() {
        setOpen(false);
        resetForm();
    }

    return (
        <div>
            {/* FAB — hidden in controlled mode; caller (Sidebar/BottomNav) owns the trigger */}
            {!isControlled && <button
                aria-label="Add a new job to the queue"
                onClick={() => setOpen(true)}
                style={{
                    position: 'fixed',
                    bottom: '5rem',
                    right: '1.25rem',
                    width: '44px',
                    height: '44px',
                    borderRadius: '50%',
                    background: 'var(--accent)',
                    color: 'var(--bg-base)',
                    border: 'none',
                    cursor: 'pointer',
                    fontSize: '1.4rem',
                    fontWeight: 700,
                    zIndex: 50,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    lineHeight: 1,
                }}
            >
                +
            </button>}

            {/* Native dialog — sh-crt applies CRT scanlines inside the modal overlay,
                which renders in the top layer (outside the root .sh-crt wrapper) */}
            <dialog
                ref={dialogRef}
                class="sh-crt"
                onClick={handleBackdropClick}
                style={{
                    background: 'var(--bg-surface)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius)',
                    padding: 0,
                    width: 'min(480px, 92vw)',
                    maxHeight: '90vh',
                    overflow: 'auto',
                }}
            >
                <div class="t-frame" data-label="SUBMIT JOB" style={{ margin: 0, border: 'none' }}>
                    <form onSubmit={handleSubmit}>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>

                            {/* Command */}
                            <div>
                                <label style={labelStyle}>COMMAND</label>
                                <p style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)', margin: '0 0 0.25rem' }}>
                                    The exact command the queue will execute — same as typing it in a terminal
                                </p>
                                <textarea
                                    rows={2}
                                    required
                                    placeholder="echo hello"
                                    value={command}
                                    onInput={(e) => setCommand(e.target.value)}
                                    style={{ ...inputStyle, resize: 'vertical' }}
                                />
                            </div>

                            {/* Source */}
                            <div>
                                <label style={labelStyle}>SOURCE</label>
                                <p style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)', margin: '0 0 0.25rem' }}>
                                    A short name for who or what is submitting this job — used for filtering and reports
                                </p>
                                <input
                                    type="text"
                                    required
                                    value={source}
                                    onInput={(e) => setSource(e.target.value)}
                                    style={inputStyle}
                                />
                            </div>

                            {/* Model */}
                            <div>
                                <label style={labelStyle}>MODEL (OPTIONAL)</label>
                                <p style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)', margin: '0 0 0.25rem' }}>
                                    Which AI model this job uses — leave blank for non-AI jobs
                                </p>
                                <input
                                    type="text"
                                    placeholder="qwen2.5:7b"
                                    value={model}
                                    onInput={(e) => setModel(e.target.value)}
                                    style={inputStyle}
                                />
                            </div>

                            {/* Priority */}
                            <div>
                                <label style={labelStyle}>PRIORITY</label>
                                <p style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)', margin: '0 0 0.25rem' }}>
                                    Choose how urgently this job should run relative to others in the queue
                                </p>
                                <PrioritySelector value={priority} onChange={v => setPriority(v)} />
                            </div>

                            {/* Timeout */}
                            <div>
                                <label style={labelStyle}>TIMEOUT (SEC)</label>
                                <p style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)', margin: '0 0 0.25rem' }}>
                                    The job will be killed if it takes longer than this
                                </p>
                                <input
                                    type="number"
                                    min={1}
                                    value={timeout}
                                    onInput={(e) => setTimeout_(e.target.value)}
                                    style={inputStyle}
                                />
                            </div>

                            {/* Inline error */}
                            {error && (
                                <div style={{
                                    color: 'var(--status-error)',
                                    fontFamily: 'var(--font-mono)',
                                    fontSize: 'var(--type-label)',
                                }}>
                                    {'✕ '}{error}
                                </div>
                            )}

                            {/* Action buttons + feedback */}
                            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end', paddingTop: '0.25rem' }}>
                                <button
                                    type="button"
                                    onClick={handleCancel}
                                    style={{
                                        background: 'transparent',
                                        border: '1px solid var(--border-subtle)',
                                        color: 'var(--text-tertiary)',
                                        borderRadius: 'var(--radius)',
                                        padding: '0.4rem 1rem',
                                        cursor: 'pointer',
                                        fontFamily: 'var(--font-mono)',
                                        fontSize: 'var(--type-body)',
                                    }}
                                >
                                    CANCEL
                                </button>
                                <button
                                    type="submit"
                                    disabled={fb.phase === 'loading'}
                                    style={{
                                        background: fb.phase === 'loading' ? 'var(--text-tertiary)' : 'var(--accent)',
                                        color: 'var(--bg-base)',
                                        border: 'none',
                                        borderRadius: 'var(--radius)',
                                        padding: '0.4rem 1rem',
                                        cursor: fb.phase === 'loading' ? 'not-allowed' : 'pointer',
                                        fontWeight: 700,
                                        fontFamily: 'var(--font-mono)',
                                        fontSize: 'var(--type-body)',
                                    }}
                                >
                                    {fb.phase === 'loading' ? 'QUEUING\u2026' : 'QUEUE JOB'}
                                </button>
                            </div>
                            {fb.msg && <div class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</div>}
                        </div>
                    </form>
                </div>
            </dialog>
        </div>
    );
}
