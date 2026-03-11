// OnboardingOverlay.jsx
// What it shows: A 5-step modal that walks first-time users through the dashboard's
//   core concepts — job queue, submit button, priority order, and tab navigation.
// Decision it drives: User knows what the dashboard does and how to use it without
//   reading docs. Dismissed permanently via localStorage so it never re-appears.
import { h } from 'preact';
import { useSignal } from '@preact/signals';

const STORAGE_KEY = 'oq_onboarding_done';

const STEPS = [
    {
        title: 'Welcome to ollama-queue',
        body: "This dashboard shows your AI job queue — what's running, what's waiting, and how your system is holding up.",
    },
    {
        title: 'The Now tab',
        body: 'Your command center. See the running job, what\'s queued next, system resources, and key stats — all at a glance.',
    },
    {
        title: 'Submit a job',
        body: 'Use the [+ Submit] button (sidebar or bottom-right) to queue an Ollama model request. Set model, prompt, priority, and timeout.',
    },
    {
        title: 'Queue management',
        body: 'Jobs process in priority order. Critical (1) runs first, Background (9) runs last. Drag to reorder. Click a job to cancel it.',
    },
    {
        title: "You're ready",
        body: 'Explore the Plan tab for scheduling, History for past runs, and Settings to tune thresholds. Press 1-5 to switch tabs fast.',
    },
];

export default function OnboardingOverlay() {
    // Check localStorage on mount — skip rendering entirely if already done.
    let initiallyDone = false;
    try {
        initiallyDone = localStorage.getItem(STORAGE_KEY) === '1';
    } catch (_e) {
        // localStorage blocked (private mode, etc.) — treat as not done
    }

    const visible = useSignal(!initiallyDone);
    const step = useSignal(0);  // 0-indexed step index

    // Dismiss: set localStorage key and hide modal.
    function dismiss() {
        try {
            localStorage.setItem(STORAGE_KEY, '1');
        } catch (_e) {
            // ignore
        }
        visible.value = false;
    }

    function handleNext() {
        if (step.value < STEPS.length - 1) {
            step.value = step.value + 1;
        }
    }

    function handleBackdropClick(e) {
        // Only close if the click lands on the backdrop itself, not the modal card
        if (e.target === e.currentTarget) {
            dismiss();
        }
    }

    if (!visible.value) return null;

    const current = STEPS[step.value];
    const isLast = step.value === STEPS.length - 1;
    const stepNumber = step.value + 1;

    return (
        <div
            class="onboarding-backdrop"
            onClick={handleBackdropClick}
            style={{
                position: 'fixed',
                inset: 0,
                zIndex: 1000,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                background: 'var(--bg-surface-overlay, rgba(0,0,0,0.6))',
            }}
        >
            <div
                class="onboarding-card"
                role="dialog"
                aria-modal="true"
                aria-labelledby="onboarding-title"
                style={{
                    background: 'var(--bg-surface-raised)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius-md)',
                    maxWidth: 480,
                    width: '90%',
                    padding: '2rem',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '1rem',
                }}
            >
                {/* Step indicator */}
                <div style={{
                    fontSize: 'var(--type-micro)',
                    color: 'var(--text-tertiary)',
                }}>
                    {`Step ${stepNumber} of ${STEPS.length}`}
                </div>

                {/* Headline */}
                <h2 id="onboarding-title" style={{
                    margin: 0,
                    fontSize: 'var(--type-lg, 1.25rem)',
                    color: 'var(--text-primary)',
                    fontWeight: 600,
                }}>
                    {current.title}
                </h2>

                {/* Body */}
                <p style={{
                    margin: 0,
                    fontSize: 'var(--type-sm)',
                    color: 'var(--text-secondary)',
                    lineHeight: 1.6,
                }}>
                    {current.body}
                </p>

                {/* Actions row */}
                <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    marginTop: '0.5rem',
                }}>
                    {/* Skip link */}
                    <button
                        onClick={dismiss}
                        style={{
                            background: 'none',
                            border: 'none',
                            cursor: 'pointer',
                            fontSize: 'var(--type-sm)',
                            color: 'var(--text-tertiary)',
                            padding: 0,
                            textDecoration: 'underline',
                        }}
                    >
                        Skip
                    </button>

                    {/* Next / Got it */}
                    {isLast ? (
                        <button
                            onClick={dismiss}
                            style={{
                                background: 'var(--accent)',
                                border: 'none',
                                borderRadius: 'var(--radius-md)',
                                color: 'var(--text-on-accent, #fff)',
                                cursor: 'pointer',
                                fontSize: 'var(--type-sm)',
                                fontWeight: 600,
                                padding: '0.5rem 1.25rem',
                            }}
                        >
                            Got it
                        </button>
                    ) : (
                        <button
                            onClick={handleNext}
                            style={{
                                background: 'var(--accent)',
                                border: 'none',
                                borderRadius: 'var(--radius-md)',
                                color: 'var(--text-on-accent, #fff)',
                                cursor: 'pointer',
                                fontSize: 'var(--type-sm)',
                                fontWeight: 600,
                                padding: '0.5rem 1.25rem',
                            }}
                        >
                            Next
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}
