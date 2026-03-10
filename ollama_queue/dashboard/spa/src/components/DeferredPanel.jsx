import { h } from 'preact';
import { deferredJobs, resumeDeferred } from '../store';
import { useActionFeedback } from '../hooks/useActionFeedback';

// What it shows: Jobs that the system has paused because they can't run right now —
//   GPU too hot, not enough memory, system overloaded, or user-deferred.
// Decision it drives: Shows what work is waiting and why, so the user can understand
//   why their job isn't running and when it will resume.

const REASON_LABELS = {
    resource: 'Resources',
    thermal: 'GPU Hot',
    burst: 'Burst',
    manual: 'Manual',
};

function DeferredRow({ entry }) {
    const [fb, act] = useActionFeedback();
    const reason = REASON_LABELS[entry.reason] || entry.reason || 'Unknown';
    const scheduledFor = entry.scheduled_for
        ? new Date(entry.scheduled_for * 1000).toLocaleTimeString()
        : 'Awaiting slot';

    return (
        <div class="deferred-row">
            <span class="deferred-row__model">{entry.model || `Job #${entry.job_id}`}</span>
            <span class={`deferred-row__reason deferred-reason--${entry.reason || 'unknown'}`}>
                {reason}
            </span>
            <span class="deferred-row__scheduled">{scheduledFor}</span>
            <button
                class="deferred-row__resume"
                disabled={fb.phase === 'loading'}
                onClick={() => act(
                    'Resuming…',
                    () => resumeDeferred(entry.id),
                    () => `Job #${entry.job_id} resumed`
                )}
            >
                Resume
            </button>
            {fb.msg && <span class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</span>}
        </div>
    );
}

export function DeferredPanel() {
    const entries = deferredJobs.value;
    if (!entries || entries.length === 0) return null;

    return (
        <div class="deferred-panel">
            <h3 class="deferred-panel__title">
                Deferred ({entries.length})
            </h3>
            <div class="deferred-panel__list">
                {entries.map(entry => (
                    <DeferredRow key={entry.id} entry={entry} />
                ))}
            </div>
        </div>
    );
}
