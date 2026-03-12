import { useEffect, useRef, useState } from 'preact/hooks';
import { loadMap, addRecurringJob, fetchSchedule, fetchLoadMap } from '../stores';
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

function parseInterval(str) {
    if (!str) return null;
    const trimmed = str.trim().toLowerCase();
    const match = trimmed.match(/^(\d+(?:\.\d+)?)\s*(d|h|m|s)?$/);
    if (!match) return null;
    const val = parseFloat(match[1]);
    if (val <= 0 || !isFinite(val)) return null;
    const unit = match[2] || 's';
    const multipliers = { d: 86400, h: 3600, m: 60, s: 1 };
    return Math.round(val * multipliers[unit]);
}

function suggestTimes(slots) {
    if (!slots || slots.length < 2) return [];
    const windows = [];
    for (let i = 0; i < slots.length - 1; i++) {
        const load = slots[i] + slots[i + 1];
        const hour = Math.floor(i / 2);
        const half = i % 2 === 0 ? '00' : '30';
        windows.push({ slot: i, load, label: `${String(hour).padStart(2, '0')}:${half}` });
    }
    return windows.sort((a, b) => a.load - b.load).slice(0, 3);
}

export default function AddRecurringJobModal({ onAdded }) {
    const [open, setOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState(null);
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [scheduleMode, setScheduleMode] = useState('interval');
    const [form, setForm] = useState({
        name: '', command: '', interval: '1h', cron: '',
        model: '', priority: 5,
        timeout: 600, tag: '', source: '', max_retries: 0,
        resource_profile: 'ollama', pinned: false,
        check_command: '', max_runs: '',
    });
    const dialogRef = useRef(null);

    function setField(key, value) {
        setForm(prev => ({ ...prev, [key]: value }));
    }

    useEffect(() => {
        const dialog = dialogRef.current;
        if (!dialog) return;
        if (open) {
            dialog.showModal();
        } else if (dialog.open) {
            dialog.close();
        }
    }, [open]);

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

    function openModal() {
        setError(null);
        setShowAdvanced(false);
        setOpen(true);
        fetchLoadMap();
    }

    function applySuggestion(label) {
        setScheduleMode('interval');
        setField('interval', '24h');
    }

    function validate() {
        if (!form.name.trim()) return 'Name is required';
        if (!form.command.trim()) return 'Command is required';
        if (scheduleMode === 'interval') {
            if (parseInterval(form.interval) === null) {
                return 'Interval must be a valid duration (e.g. 4h, 30m, 1d)';
            }
        } else {
            if (!form.cron.trim()) return 'Cron expression is required';
        }
        const p = Number(form.priority);
        if (!Number.isInteger(p) || p < 1 || p > 10) return 'Priority must be a valid level (1\u201310)';
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
        setSubmitting(true);
        try {
            const body = {
                name: form.name.trim(),
                command: form.command.trim(),
                priority: Number(form.priority),
            };
            if (scheduleMode === 'interval') {
                body.interval_seconds = parseInterval(form.interval);
            } else {
                body.cron_expression = form.cron.trim();
            }
            if (form.model.trim()) body.model = form.model.trim();
            if (showAdvanced) {
                body.timeout = Number(form.timeout) || 600;
                if (form.tag.trim()) body.tag = form.tag.trim();
                if (form.source.trim()) body.source = form.source.trim();
                body.max_retries = Number(form.max_retries) || 0;
                body.resource_profile = form.resource_profile;
                body.pinned = form.pinned;
                if (form.check_command.trim()) body.check_command = form.check_command.trim();
                if (form.max_runs) body.max_runs = Number(form.max_runs);
            }
            await addRecurringJob(body);
            await fetchSchedule();
            await fetchLoadMap();
            setOpen(false);
            if (onAdded) onAdded();
        } catch (err) {
            setError(err.message || 'Add job failed');
        } finally {
            setSubmitting(false);
        }
    }

    function handleCancel() {
        setOpen(false);
    }

    const suggestions = suggestTimes(loadMap.value?.slots);

    return (
        <div>
            <button
                onClick={openModal}
                style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 'var(--type-label)',
                    background: 'transparent',
                    border: '1px solid var(--border-subtle)',
                    color: 'var(--accent)',
                    padding: '0.25rem 0.6rem',
                    borderRadius: 'var(--radius)',
                    cursor: 'pointer',
                    whiteSpace: 'nowrap',
                }}
            >
                + Add Job
            </button>

            <dialog
                ref={dialogRef}
                onClick={handleBackdropClick}
                style={{
                    background: 'var(--bg-surface)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius)',
                    padding: 0,
                    width: 'min(520px, 94vw)',
                    maxHeight: '90vh',
                    overflow: 'auto',
                }}
            >
                <div class="t-frame" data-label="Add Recurring Job" style={{ margin: 0, border: 'none' }}>
                    <form onSubmit={handleSubmit}>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>

                            {/* Suggested times */}
                            {suggestions.length > 0 && (
                                <div>
                                    <div style={labelStyle}>Suggested low-load slots</div>
                                    <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                                        {suggestions.map((sug, idx) => (
                                            <button
                                                key={sug.slot}
                                                type="button"
                                                onClick={() => applySuggestion(sug.label)}
                                                style={{
                                                    fontFamily: 'var(--font-mono)',
                                                    fontSize: 'var(--type-label)',
                                                    background: idx === 0 ? 'var(--accent)' : 'var(--bg-surface-raised)',
                                                    color: idx === 0 ? 'var(--bg-base)' : 'var(--text-secondary)',
                                                    border: '1px solid var(--border-subtle)',
                                                    borderRadius: 'var(--radius)',
                                                    padding: '0.2rem 0.5rem',
                                                    cursor: 'pointer',
                                                }}
                                            >
                                                {idx === 0 ? `\u2605 ${sug.label}` : sug.label}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Name */}
                            <div>
                                <label style={labelStyle}>Name</label>
                                <input
                                    type="text"
                                    required
                                    placeholder="my-daily-job"
                                    value={form.name}
                                    onInput={(e) => setField('name', e.target.value)}
                                    style={inputStyle}
                                />
                            </div>

                            {/* Command */}
                            <div>
                                <label style={labelStyle}>Command</label>
                                <textarea
                                    rows={2}
                                    required
                                    placeholder="aria run"
                                    value={form.command}
                                    onInput={(e) => setField('command', e.target.value)}
                                    style={{ ...inputStyle, resize: 'vertical' }}
                                />
                            </div>

                            {/* Schedule mode toggle + input */}
                            <div>
                                <label style={labelStyle}>Schedule</label>
                                <div style={{ display: 'flex', gap: '1rem', marginBottom: '0.4rem' }}>
                                    <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                                        <input
                                            type="radio"
                                            name="scheduleMode"
                                            value="interval"
                                            checked={scheduleMode === 'interval'}
                                            onChange={() => setScheduleMode('interval')}
                                            style={{ marginRight: '0.3rem' }}
                                        />
                                        Interval
                                    </label>
                                    <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                                        <input
                                            type="radio"
                                            name="scheduleMode"
                                            value="cron"
                                            checked={scheduleMode === 'cron'}
                                            onChange={() => setScheduleMode('cron')}
                                            style={{ marginRight: '0.3rem' }}
                                        />
                                        Cron
                                    </label>
                                </div>
                                {scheduleMode === 'interval' ? (
                                    <input
                                        type="text"
                                        placeholder="4h \u00b7 30m \u00b7 1d"
                                        value={form.interval}
                                        onInput={(e) => setField('interval', e.target.value)}
                                        style={inputStyle}
                                    />
                                ) : (
                                    <input
                                        type="text"
                                        placeholder="0 3 * * *"
                                        value={form.cron}
                                        onInput={(e) => setField('cron', e.target.value)}
                                        style={inputStyle}
                                    />
                                )}
                            </div>

                            {/* Model */}
                            <div>
                                <label style={labelStyle}>Model (optional)</label>
                                <input
                                    type="text"
                                    placeholder="qwen2.5:7b"
                                    value={form.model}
                                    onInput={(e) => setField('model', e.target.value)}
                                    style={inputStyle}
                                />
                            </div>

                            {/* Priority */}
                            <div>
                                <label style={labelStyle}>Priority</label>
                                <PrioritySelector value={form.priority} onChange={v => setField('priority', v)} />
                            </div>

                            {/* Advanced toggle */}
                            <button
                                type="button"
                                onClick={() => setShowAdvanced(prev => !prev)}
                                style={{
                                    background: 'none',
                                    border: 'none',
                                    color: 'var(--text-tertiary)',
                                    fontSize: 'var(--type-label)',
                                    fontFamily: 'var(--font-mono)',
                                    cursor: 'pointer',
                                    textAlign: 'left',
                                    padding: '0.1rem 0',
                                }}
                            >
                                {showAdvanced ? '\u25bc Advanced options' : '\u25ba Advanced options'}
                            </button>

                            {/* Advanced section */}
                            {showAdvanced && (
                                <div style={{ borderLeft: '2px solid var(--border-subtle)', paddingLeft: '0.5rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>

                                    {/* Timeout + MaxRetries */}
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                                        <div>
                                            <label style={labelStyle}>Timeout (sec)</label>
                                            <input
                                                type="number"
                                                min={1}
                                                value={form.timeout}
                                                onInput={(e) => setField('timeout', e.target.value)}
                                                style={inputStyle}
                                            />
                                        </div>
                                        <div>
                                            <label style={labelStyle}>Max Retries</label>
                                            <input
                                                type="number"
                                                min={0}
                                                value={form.max_retries}
                                                onInput={(e) => setField('max_retries', e.target.value)}
                                                style={inputStyle}
                                            />
                                        </div>
                                    </div>

                                    {/* Tag + Source */}
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                                        <div>
                                            <label style={labelStyle}>Tag</label>
                                            <input
                                                type="text"
                                                value={form.tag}
                                                onInput={(e) => setField('tag', e.target.value)}
                                                style={inputStyle}
                                            />
                                        </div>
                                        <div>
                                            <label style={labelStyle}>Source</label>
                                            <input
                                                type="text"
                                                value={form.source}
                                                onInput={(e) => setField('source', e.target.value)}
                                                style={inputStyle}
                                            />
                                        </div>
                                    </div>

                                    {/* Resource Profile */}
                                    <div>
                                        <label style={labelStyle}>Resource Profile</label>
                                        <select
                                            value={form.resource_profile}
                                            onChange={(e) => setField('resource_profile', e.target.value)}
                                            style={inputStyle}
                                        >
                                            <option value="ollama">ollama</option>
                                            <option value="embed">embed</option>
                                            <option value="heavy">heavy</option>
                                        </select>
                                    </div>

                                    {/* Check Command */}
                                    <div>
                                        <label style={labelStyle}>Check Command</label>
                                        <input
                                            type="text"
                                            value={form.check_command}
                                            onInput={(e) => setField('check_command', e.target.value)}
                                            style={inputStyle}
                                        />
                                    </div>

                                    {/* Max Runs + Pinned */}
                                    <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-end' }}>
                                        <div style={{ flex: 1 }}>
                                            <label style={labelStyle}>Max Runs</label>
                                            <input
                                                type="number"
                                                min={1}
                                                value={form.max_runs}
                                                onInput={(e) => setField('max_runs', e.target.value)}
                                                placeholder="unlimited"
                                                style={inputStyle}
                                            />
                                        </div>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', paddingBottom: '0.3rem' }}>
                                            <input
                                                type="checkbox"
                                                id="pinned-checkbox"
                                                checked={form.pinned}
                                                onChange={(e) => setField('pinned', e.target.checked)}
                                            />
                                            <label
                                                for="pinned-checkbox"
                                                style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', cursor: 'pointer' }}
                                            >
                                                Pinned
                                            </label>
                                        </div>
                                    </div>
                                </div>
                            )}

                            {/* Inline error */}
                            {error && (
                                <div style={{
                                    color: 'var(--status-error)',
                                    fontFamily: 'var(--font-mono)',
                                    fontSize: 'var(--type-label)',
                                }}>
                                    {'\u2715 '}{error}
                                </div>
                            )}

                            {/* Action buttons */}
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
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    disabled={submitting}
                                    style={{
                                        background: submitting ? 'var(--text-tertiary)' : 'var(--accent)',
                                        color: 'var(--bg-base)',
                                        border: 'none',
                                        borderRadius: 'var(--radius)',
                                        padding: '0.4rem 1rem',
                                        cursor: submitting ? 'not-allowed' : 'pointer',
                                        fontWeight: 700,
                                        fontFamily: 'var(--font-mono)',
                                        fontSize: 'var(--type-body)',
                                    }}
                                >
                                    {submitting ? 'Adding\u2026' : 'Add Job'}
                                </button>
                            </div>
                        </div>
                    </form>
                </div>
            </dialog>
        </div>
    );
}
