import { h } from 'preact';
import { useEffect, useState, useRef } from 'preact/hooks';
import {
    models, modelCatalog, API,
    fetchModels, fetchModelCatalog,
    startModelPull, cancelModelPull,
} from '../store';
import { ModelBadge } from '../components/ModelBadge';

function useDebounce(value, delay) {
    const [debounced, setDebounced] = useState(value);
    useEffect(() => {
        const t = setTimeout(() => setDebounced(value), delay);
        return () => clearTimeout(t);
    }, [value, delay]);
    return debounced;
}

// What it shows: The model inventory — every AI model installed locally with its disk size
//   and resource profile (embed/heavy/ollama), plus a searchable catalog of downloadable
//   models. Active downloads show live progress bars.
// Decision it drives: Which models are installed and how much space do they use? Is a model
//   missing that I need for a job? Should I start a pull or cancel one in progress?
export default function ModelsTab() {
    const [searchQuery, setSearchQuery] = useState('');
    const [activePulls, setActivePulls] = useState({});
    const [pullError, setPullError] = useState(null);
    const [sortCol, setSortCol] = useState('size_bytes');
    const [sortDir, setSortDir] = useState('desc');
    // Map keyed by pullId — all active poll intervals tracked here for cleanup on unmount.
    const pullIntervalsRef = useRef({});

    const debouncedSearch = useDebounce(searchQuery, 300);

    useEffect(() => {
        fetchModels();
        return () => {
            // Clear all active pull intervals when component unmounts.
            Object.values(pullIntervalsRef.current).forEach(iv => clearInterval(iv));
            pullIntervalsRef.current = {};
        };
    }, []);

    useEffect(() => {
        fetchModelCatalog(debouncedSearch);
    }, [debouncedSearch]);

    function handleSort(col) {
        if (sortCol === col) {
            setSortDir(d => d === 'asc' ? 'desc' : 'asc');
        } else {
            setSortCol(col);
            setSortDir('desc');
        }
    }

    async function handlePull(modelName) {
        setPullError(null);
        try {
            const pullId = await startModelPull(modelName);
            setActivePulls(prev => ({ ...prev, [pullId]: { model: modelName, progress: 0, status: 'pulling', startedAt: Date.now() } }));
            const iv = setInterval(async () => {
                try {
                    const resp = await fetch(`${API}/models/pull/${pullId}`);
                    if (resp.ok) {
                        const data = await resp.json();
                        setActivePulls(prev => ({
                            ...prev,
                            [pullId]: { model: modelName, progress: data.progress_pct, status: data.status, startedAt: prev[pullId]?.startedAt },
                        }));
                        if (data.status !== 'pulling') {
                            clearInterval(pullIntervalsRef.current[pullId]);
                            delete pullIntervalsRef.current[pullId];
                            if (data.status === 'completed') fetchModels();
                        }
                    }
                } catch (pollErr) { console.warn('Pull poll error:', pollErr); }
            }, 2000);
            pullIntervalsRef.current[pullId] = iv;
        } catch (err) {
            setPullError(`Download failed: ${err.message}`);
        }
    }

    async function handleCancel(pullId) {
        try {
            await cancelModelPull(pullId);
            clearInterval(pullIntervalsRef.current[pullId]);
            delete pullIntervalsRef.current[pullId];
            setActivePulls(prev => {
                const next = { ...prev };
                delete next[pullId];
                return next;
            });
        } catch (err) {
            setPullError(`Could not cancel download: ${err.message}`);
        }
    }

    const installedNames = new Set(models.value.map(mdl => mdl.name));

    const sortedModels = [...models.value].sort((a, b) => {
        let av = a[sortCol] ?? 0;
        let bv = b[sortCol] ?? 0;
        if (typeof av === 'string') { av = av.toLowerCase(); bv = (b[sortCol] ?? '').toLowerCase(); }
        if (av < bv) return sortDir === 'asc' ? -1 : 1;
        if (av > bv) return sortDir === 'asc' ? 1 : -1;
        return 0;
    });

    const curatedNames = new Set(modelCatalog.value.curated.map(m => m.name));
    const allCatalogModels = [
        ...modelCatalog.value.curated,
        ...modelCatalog.value.search_results
            .filter(r => !curatedNames.has(r.name))
            .map(catalogResult => ({ ...catalogResult, recommended: false })),
    ];

    const thStyle = {
        textAlign: 'left', padding: '0.5rem 0.75rem',
        fontSize: 'var(--type-label)', fontWeight: 600,
        color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)',
        textTransform: 'uppercase', letterSpacing: '0.05em',
    };

    return (
        <div class="flex flex-col gap-4 animate-page-enter">
            <h2 style={{ margin: 0, fontFamily: 'var(--font-mono)', fontWeight: 700,
                         fontSize: 'var(--type-headline)', color: 'var(--text-primary)' }}>
                AI Models
            </h2>

            {pullError && (
                <div style={{ padding: '0.5rem', background: 'var(--status-error)',
                              color: 'var(--accent-text)', borderRadius: 'var(--radius)',
                              fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)' }}>
                    {pullError}
                </div>
            )}

            {/* Active Pulls */}
            {Object.entries(activePulls).map(([pullId, pull]) => (
                <div key={pullId} class="t-frame" style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', flex: 1 }}>Downloading: {pull.model}</span>
                    <div style={{ flex: 2, background: 'var(--bg-inset)',
                                  borderRadius: 'var(--radius)', height: 8, overflow: 'hidden' }}>
                        <div style={{ width: `${pull.progress || 0}%`, height: '100%',
                                      background: 'var(--accent)', transition: 'width 0.5s' }} />
                    </div>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                                   color: 'var(--text-secondary)', minWidth: '3rem' }}>
                        {pull.status === 'completed' ? '✓' : `${Math.round(pull.progress || 0)}%`}
                    </span>
                    {pull.startedAt && pull.status === 'pulling' && (
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                            {formatElapsed(pull.startedAt)}
                        </span>
                    )}
                    {pull.status === 'pulling' && (
                        <button class="t-btn t-btn-secondary"
                                style={{ fontSize: 'var(--type-label)', padding: '0.2rem 0.6rem' }}
                                onClick={() => handleCancel(pullId)}>
                            Cancel Download
                        </button>
                    )}
                </div>
            ))}

            {/* Installed Models */}
            <section>
                <h3 style={{ fontFamily: 'var(--font-mono)', fontWeight: 700,
                             fontSize: 'var(--type-label)', color: 'var(--text-secondary)',
                             textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 0.5rem' }}>
                    Installed on This Machine ({models.value.length})
                </h3>
                <div class="t-frame" style={{ padding: 0, overflow: 'hidden' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--type-body)' }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--border-subtle)',
                                         background: 'var(--bg-surface-raised)' }}>
                                <th onClick={() => handleSort('name')}
                                    style={{ cursor: 'pointer', userSelect: 'none', ...thStyle }}>
                                    Model Name {sortCol === 'name' ? (sortDir === 'asc' ? '↑' : '↓') : ''}
                                </th>
                                <th style={thStyle}>Category</th>
                                <th onClick={() => handleSort('size_bytes')}
                                    style={{ cursor: 'pointer', userSelect: 'none', ...thStyle }}>
                                    Disk Space {sortCol === 'size_bytes' ? (sortDir === 'asc' ? '↑' : '↓') : ''}
                                </th>
                                <th style={thStyle}>GPU Memory</th>
                                <th style={thStyle}>Avg. Job Time</th>
                                <th style={thStyle}>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sortedModels.map(model => (
                                <tr key={model.name} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                    <td style={{ padding: '0.5rem 0.75rem', fontFamily: 'var(--font-mono)',
                                                 color: 'var(--text-primary)' }}>{model.name}</td>
                                    <td style={{ padding: '0.5rem 0.75rem' }}>
                                        <ModelBadge profile={model.resource_profile} typeTag={model.type_tag} />
                                    </td>
                                    <td style={{ padding: '0.5rem 0.75rem', fontFamily: 'var(--font-mono)',
                                                 color: 'var(--text-secondary)' }}>
                                        {model.size_bytes ? `${(model.size_bytes / 1e9).toFixed(1)} GB` : '—'}
                                    </td>
                                    <td style={{ padding: '0.5rem 0.75rem', fontFamily: 'var(--font-mono)',
                                                 color: 'var(--text-secondary)' }}>
                                        {model.vram_mb ? `${(model.vram_mb / 1024).toFixed(1)} GB` : '—'}
                                    </td>
                                    <td style={{ padding: '0.5rem 0.75rem', fontFamily: 'var(--font-mono)',
                                                 color: 'var(--text-tertiary)' }}>
                                        {model.avg_duration_seconds
                                            ? `~${Math.round(model.avg_duration_seconds / 60)}m`
                                            : '—'}
                                    </td>
                                    <td style={{ padding: '0.5rem 0.75rem' }}>
                                        {model.loaded
                                            ? <span style={{ color: 'var(--status-healthy)', fontFamily: 'var(--font-mono)',
                                                             fontSize: 'var(--type-label)' }} title="This model is currently loaded in GPU memory and ready to use">● in memory</span>
                                            : <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)' }} title="This model is installed but not currently loaded — it will load automatically when needed">idle</span>}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </section>

            {/* Download Panel */}
            <section>
                <h3 style={{ fontFamily: 'var(--font-mono)', fontWeight: 700,
                             fontSize: 'var(--type-label)', color: 'var(--text-secondary)',
                             textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 0.5rem' }}>
                    Browse & Download More Models
                </h3>

                <input
                    type="text" placeholder="Search by name — e.g. llama3, mistral, qwen…"
                    value={searchQuery}
                    onInput={ev => setSearchQuery(ev.target.value)}
                    style={{ width: '100%', boxSizing: 'border-box', fontFamily: 'var(--font-mono)',
                             fontSize: 'var(--type-body)', background: 'var(--bg-inset)',
                             color: 'var(--text-primary)', border: '1px solid var(--border-subtle)',
                             borderRadius: 'var(--radius)', padding: '0.4rem 0.75rem',
                             outline: 'none', marginBottom: '1rem' }}
                />

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '0.75rem' }}>
                    {allCatalogModels.map(catalogModel => {
                        const isInstalled = installedNames.has(catalogModel.name);
                        const vramMap = { light: 'GPU memory: < 4 GB', medium: 'GPU memory: ~8 GB', heavy: 'GPU memory: 16 GB+' };
                        const vramLabel = catalogModel.resource_profile && catalogModel.resource_profile !== 'ollama'
                            ? vramMap[catalogModel.resource_profile]
                            : null;
                        return (
                            <div key={catalogModel.name} class="t-frame"
                                 style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                                    <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700,
                                                   color: 'var(--text-primary)', fontSize: 'var(--type-body)' }}>
                                        {catalogModel.name}
                                    </span>
                                    {catalogModel.recommended && (
                                        <span style={{ background: 'var(--status-healthy)', color: 'var(--accent-text)',
                                                       fontSize: 'var(--type-label)', padding: '0.1rem 0.4rem',
                                                       borderRadius: 'var(--radius)', fontFamily: 'var(--font-mono)',
                                                       fontWeight: 700 }}>★ Best choice</span>
                                    )}
                                </div>
                                <p style={{ margin: 0, fontSize: 'var(--type-label)',
                                            color: 'var(--text-secondary)' }}>
                                    {catalogModel.description}
                                </p>
                                <ModelBadge
                                    profile={catalogModel.resource_profile || 'ollama'}
                                    typeTag={catalogModel.type_tag || 'general'}
                                />
                                {vramLabel && (
                                    <span style={{
                                        fontSize: 'var(--type-micro)',
                                        color: 'var(--text-tertiary)',
                                        background: 'var(--bg-inset)',
                                        border: '1px solid var(--border-subtle)',
                                        borderRadius: 4,
                                        padding: '1px 5px',
                                        alignSelf: 'flex-start',
                                    }}>
                                        {vramLabel}
                                    </span>
                                )}
                                <button
                                    class={`t-btn ${isInstalled ? 't-btn-secondary' : 't-btn-primary'}`}
                                    style={{ fontSize: 'var(--type-label)', padding: '0.3rem 0.75rem',
                                             opacity: isInstalled ? 0.5 : 1 }}
                                    disabled={isInstalled}
                                    onClick={() => !isInstalled && handlePull(catalogModel.name)}>
                                    {isInstalled ? '✓ Already installed' : '↓ Download'}
                                </button>
                            </div>
                        );
                    })}
                </div>

                {debouncedSearch && allCatalogModels.length === 0 && (
                    <div style={{ textAlign: 'center', padding: '1.5rem', color: 'var(--text-tertiary)', fontSize: 'var(--type-body)' }}>
                        No models found matching "{debouncedSearch}" — try a different name or clear the search
                        <br />
                        <button class="t-btn t-btn-secondary"
                                style={{ marginTop: '0.5rem', padding: '4px 12px', fontSize: 'var(--type-label)' }}
                                onClick={() => setSearchQuery('')}>Clear Search</button>
                    </div>
                )}
            </section>
        </div>
    );
}

function formatElapsed(ms) {
    if (!ms) return '';
    const secs = Math.round((Date.now() - ms) / 1000);
    if (secs < 60) return `${secs}s`;
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `${mins}m ${secs % 60}s`;
    return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}
