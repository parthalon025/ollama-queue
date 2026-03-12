// What it does: Pure utility functions and constants shared across Plan sub-components —
//   time formatting, interval parsing, traffic intensity calculation, job grouping, and
//   column definitions for the recurring-jobs table.
// Decision it drives: Keeps formatting and grouping logic out of render code so components
//   stay focused on layout. All functions are stateless and side-effect-free.

// Note: local vars named 'hrs'/'mins' to avoid shadowing the injected 'h' JSX factory.
export function formatCountdown(next_run) {
    const diff = next_run - Date.now() / 1000;
    if (diff < 0) return 'overdue';
    const hrs = Math.floor(diff / 3600);
    const mins = Math.floor((diff % 3600) / 60);
    const secs = Math.floor(diff % 60);
    if (hrs > 0) return `${hrs}h ${mins}m ${secs}s`;
    if (mins > 0) return `${mins}m ${secs}s`;
    return `${secs}s`;
}

export function formatInterval(seconds) {
    if (!seconds) return '\u2014';
    if (seconds % 86400 === 0) return `${seconds / 86400}d`;
    if (seconds % 3600 === 0) return `${seconds / 3600}h`;
    if (seconds % 60 === 0) return `${seconds / 60}m`;
    return `${seconds}s`;
}

// Parse shorthand like "4h", "30m", "1d", "7d", "90s", or plain seconds
export function parseInterval(str) {
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

export function formatDuration(secs) {
    if (secs === null || secs === undefined || secs < 0) return '--';
    const s = Math.round(secs);
    if (s < 60) return `${s}s`;
    const mins = Math.floor(s / 60);
    const rem = s % 60;
    if (mins < 60) return `${mins}m ${rem}s`;
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m`;
}

// Traffic intensity ρ = sum(estimated_duration) / 86400.
// Research threshold: keep ρ < 0.80 (Kingman's formula diverges as ρ → 1).
// Includes ALL jobs (enabled + disabled) — represents maximum scheduled load.
// Heavy-model fallback: 1800s; others: 600s (10m default for LLM tasks).
export function computeRho(jobList) {
    if (jobList.length === 0) return 0;
    const totalSecs = jobList.reduce((sum, j) => {
        const fallback = j.model_profile === 'heavy' ? 1800 : 600;
        return sum + (j.estimated_duration || fallback);
    }, 0);
    return totalSecs / 86400;
}

export function rhoStatus(rho) {
    if (rho < 0.60) return { label: 'light load', color: 'var(--status-healthy)' };
    if (rho < 0.80) return { label: 'moderate load', color: 'var(--status-warning)' };
    return { label: 'very busy', color: 'var(--status-error)' };
}

// Priority design token colors (theme-aware)
export const CATEGORY_COLORS = {
    critical:   'var(--status-error)',
    high:       'var(--status-warning)',
    normal:     'var(--accent)',
    low:        'var(--text-tertiary)',
    background: 'var(--text-tertiary)',
};

export function priorityCategory(p) {
    if (p <= 2) return 'critical';
    if (p <= 4) return 'high';
    if (p <= 6) return 'normal';
    if (p <= 8) return 'low';
    return 'background';
}

// Non-color encoding for priority — Treisman (1980): combine color + independent channel
// for colorblind safety. Border thickness is independent of hue.
export function priorityBorderWidth(priority) {
    if (priority <= 2) return '4px';  // Critical
    if (priority <= 4) return '3px';  // High
    if (priority <= 6) return '2px';  // Normal
    if (priority <= 8) return '1px';  // Low
    return '1px';                      // Background (opacity handled separately)
}

export function relativeTimeLog(ts) {
    if (!ts) return '\u2014';
    const diff = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    const dateObj = new Date(ts * 1000);
    return `${dateObj.toLocaleDateString()} ${dateObj.toLocaleTimeString()}`;
}

// --- Grouping ---

const TAG_ORDER = ['aria', 'telegram', 'lessons', 'notion', 'embeddings'];

export function groupJobsByTag(jobList) {
    const groups = {};
    for (const job of jobList) {
        const tag = job.tag || 'other';
        if (!groups[tag]) groups[tag] = [];
        groups[tag].push(job);
    }
    const ordered = TAG_ORDER.filter(tag => groups[tag]).map(tag => ({ tag, jobs: groups[tag] }));
    const extra = Object.keys(groups)
        .filter(tag => !TAG_ORDER.includes(tag) && tag !== 'other')
        .sort();
    for (const tag of extra) ordered.push({ tag, jobs: groups[tag] });
    if (groups['other']) ordered.push({ tag: 'other', jobs: groups['other'] });
    return ordered;
}

export function groupNextDue(groupJobs) {
    let min = Infinity;
    for (const rj of groupJobs) {
        if (rj.enabled && rj.next_run < min) min = rj.next_run;
    }
    return min === Infinity ? null : min;
}

// --- Table layout ---

export const COLUMN_DEFS = [
    { label: 'Name',      title: 'Job name — set when the recurring job was created' },
    { label: 'Model',     title: 'Ollama model this job uses (overrides the system default)' },
    { label: 'GPU Mem',   title: 'Memory profile: light · standard · heavy. Heavy needs \u226516GB VRAM and cannot overlap another heavy job' },
    { label: 'Repeats',   title: 'How often this job runs — interval (e.g. 4h) or cron expression' },
    { label: 'Priority',  title: '1=highest, 10=lowest. Lower number dequeues first when multiple jobs are waiting' },
    { label: 'Due In',    title: 'Time until the next scheduled run' },
    { label: 'Est. Time', title: 'Estimated run duration based on recent run history' },
    { label: '\u2713',    title: 'Number of completed successful runs' },
    { label: 'Limit',     title: 'Max retry attempts before the job is moved to the Dead Letter Queue (DLQ)' },
    { label: '\u{1F4CC}', title: "Pinned slot — the rebalancer will not move this job's scheduled run time" },
    { label: 'On',        title: 'Enable or disable this recurring job' },
    { label: '',          title: undefined },
];
export const COLUMNS = COLUMN_DEFS.map(d => d.label);
export const COL_COUNT = COLUMNS.length;

export const STATUS_COLORS = {
    completed: 'var(--status-success)',
    failed: 'var(--status-error)',
    killed: 'var(--status-error)',
    pending: 'var(--text-tertiary)',
    running: 'var(--accent)',
};

// Shared styles for detail panel form
export const labelStyle = {
    fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
    color: 'var(--text-tertiary)', fontWeight: 600,
    textTransform: 'uppercase', letterSpacing: '0.03em',
    marginBottom: '0.2rem', display: 'block',
};

export const inputStyle = {
    fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)',
    background: 'var(--bg-surface-raised)', color: 'var(--text-primary)',
    border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius)',
    padding: '0.3rem 0.5rem', width: '100%',
};

export const isMobileScreen = () => typeof window !== 'undefined' && window.innerWidth <= 640;
