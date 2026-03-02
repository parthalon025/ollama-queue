import { h } from 'preact';

const PROFILE_COLORS = {
    embed:  { bg: 'var(--status-healthy)',      label: 'embed' },
    ollama: { bg: 'var(--accent)',         label: 'llm' },
    heavy:  { bg: 'var(--status-warning)', label: 'heavy' },
};
const TYPE_COLORS = {
    coding:    'var(--accent)',
    reasoning: 'var(--status-warning)',
    embed:     'var(--status-healthy)',
    general:   'var(--text-tertiary)',
};

export function ModelBadge({ profile, typeTag }) {
    const pc = PROFILE_COLORS[profile] || PROFILE_COLORS.ollama;
    const tc = TYPE_COLORS[typeTag] || TYPE_COLORS.general;
    return (
        <span style={{ display: 'inline-flex', gap: '0.25rem', alignItems: 'center' }}>
            <span style={{
                background: pc.bg, color: 'var(--accent-text)',
                fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)',
                fontWeight: 700, padding: '0.1rem 0.4rem',
                borderRadius: 'var(--radius)',
            }}>{pc.label}</span>
            {typeTag && typeTag !== 'general' && (
                <span style={{
                    color: tc, fontSize: 'var(--type-label)',
                    fontFamily: 'var(--font-mono)',
                }}>{typeTag}</span>
            )}
        </span>
    );
}
