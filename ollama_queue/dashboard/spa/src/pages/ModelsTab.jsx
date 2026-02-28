import { h } from 'preact';
import { useEffect, useState, useRef } from 'preact/hooks';
import {
    models, modelCatalog, API,
    fetchModels, fetchModelCatalog,
    startModelPull, cancelModelPull,
    scheduleJobs, fetchSchedule, assignModelToJob,
} from '../store';
import { ModelBadge } from '../components/ModelBadge';

export default function ModelsTab() {
    const [searchQuery, setSearchQuery] = useState('');
    const [activePulls, setActivePulls] = useState({});
    const [pullError, setPullError] = useState(null);
    // Map keyed by pullId — all active poll intervals tracked here for cleanup on unmount.
    const pullIntervalsRef = useRef({});

    useEffect(() => {
        fetchModels();
        fetchModelCatalog();
        fetchSchedule();
        return () => {
            // Clear all active pull intervals when component unmounts.
            Object.values(pullIntervalsRef.current).forEach(iv => clearInterval(iv));
            pullIntervalsRef.current = {};
        };
    }, []);

    async function handlePull(modelName) {
        setPullError(null);
        try {
            const pullId = await startModelPull(modelName);
            setActivePulls(prev => ({ ...prev, [pullId]: { model: modelName, progress: 0, status: 'pulling' } }));
            const iv = setInterval(async () => {
                try {
                    const resp = await fetch(`${API}/models/pull/${pullId}`);
                    if (resp.ok) {
                        const data = await resp.json();
                        setActivePulls(prev => ({
                            ...prev,
                            [pullId]: { model: modelName, progress: data.progress_pct, status: data.status },
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
            setPullError(`Pull failed: ${err.message}`);
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
            setPullError(`Cancel failed: ${err.message}`);
        }
    }

    async function handleAssign(rjId, modelName) {
        try {
            await assignModelToJob(rjId, modelName);
            await fetchSchedule();
        } catch (err) {
            setPullError(`Assign failed: ${err.message}`);
        }
    }

    const installedNames = new Set(models.value.map(mdl => mdl.name));

    return (
        <div class="flex flex-col gap-4 animate-page-enter">
            <h2 style={{ margin: 0, fontFamily: 'var(--font-mono)', fontWeight: 700,
                         fontSize: 'var(--type-headline)', color: 'var(--text-primary)' }}>
                Models
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
                    <span style={{ fontFamily: 'var(--font-mono)', flex: 1 }}>{pull.model}</span>
                    <div style={{ flex: 2, background: 'var(--bg-inset)',
                                  borderRadius: 'var(--radius)', height: 8, overflow: 'hidden' }}>
                        <div style={{ width: `${pull.progress || 0}%`, height: '100%',
                                      background: 'var(--accent)', transition: 'width 0.5s' }} />
                    </div>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                                   color: 'var(--text-secondary)', minWidth: '3rem' }}>
                        {pull.status === 'completed' ? '✓' : `${Math.round(pull.progress || 0)}%`}
                    </span>
                    {pull.status === 'pulling' && (
                        <button class="t-btn t-btn-secondary"
                                style={{ fontSize: 'var(--type-label)', padding: '0.2rem 0.6rem' }}
                                onClick={() => handleCancel(pullId)}>
                            Cancel
                        </button>
                    )}
                </div>
            ))}

            {/* Installed Models */}
            <section>
                <h3 style={{ fontFamily: 'var(--font-mono)', fontWeight: 700,
                             fontSize: 'var(--type-label)', color: 'var(--text-secondary)',
                             textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 0.5rem' }}>
                    Installed ({models.value.length})
                </h3>
                <div class="t-frame" style={{ padding: 0, overflow: 'hidden' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--type-body)' }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--border-subtle)',
                                         background: 'var(--bg-surface-raised)' }}>
                                {['Name', 'Type', 'Size', 'VRAM', 'Avg Duration', 'Status', 'Assign to Job'].map(col => (
                                    <th key={col} style={{ textAlign: 'left', padding: '0.5rem 0.75rem',
                                                           fontSize: 'var(--type-label)', fontWeight: 600,
                                                           color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)',
                                                           textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                                        {col}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {models.value.map(model => (
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
                                            ? <span style={{ color: 'var(--status-ok)', fontFamily: 'var(--font-mono)',
                                                             fontSize: 'var(--type-label)' }}>● loaded</span>
                                            : <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)' }}>idle</span>}
                                    </td>
                                    <td style={{ padding: '0.5rem 0.75rem' }}>
                                        <select
                                            style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                                                     background: 'var(--bg-inset)', color: 'var(--text-primary)',
                                                     border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius)',
                                                     padding: '0.2rem 0.4rem' }}
                                            onChange={ev => {
                                                const rjId = parseInt(ev.target.value, 10);
                                                if (rjId) handleAssign(rjId, model.name);
                                                ev.target.value = '';
                                            }}>
                                            <option value="">Assign to…</option>
                                            {scheduleJobs.value.map(rj => (
                                                <option key={rj.id} value={rj.id}>{rj.name}</option>
                                            ))}
                                        </select>
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
                    Download Models
                </h3>

                <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
                    <input
                        type="text" placeholder="Search ollama.com…"
                        value={searchQuery}
                        onInput={ev => setSearchQuery(ev.target.value)}
                        style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)',
                                 background: 'var(--bg-inset)', color: 'var(--text-primary)',
                                 border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius)',
                                 padding: '0.4rem 0.75rem', outline: 'none' }}
                    />
                    <button class="t-btn t-btn-primary px-4 py-2 text-sm"
                            onClick={() => fetchModelCatalog(searchQuery)}>
                        Search
                    </button>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '0.75rem' }}>
                    {[
                        ...modelCatalog.value.curated,
                        ...modelCatalog.value.search_results.map(catalogResult => ({ ...catalogResult, recommended: false })),
                    ].map(catalogModel => {
                        const isInstalled = installedNames.has(catalogModel.name);
                        return (
                            <div key={catalogModel.name} class="t-frame"
                                 style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                                    <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700,
                                                   color: 'var(--text-primary)', fontSize: 'var(--type-body)' }}>
                                        {catalogModel.name}
                                    </span>
                                    {catalogModel.recommended && (
                                        <span style={{ background: 'var(--status-ok)', color: 'var(--accent-text)',
                                                       fontSize: 'var(--type-label)', padding: '0.1rem 0.4rem',
                                                       borderRadius: 'var(--radius)', fontFamily: 'var(--font-mono)',
                                                       fontWeight: 700 }}>★ rec</span>
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
                                <button
                                    class={`t-btn ${isInstalled ? 't-btn-secondary' : 't-btn-primary'}`}
                                    style={{ fontSize: 'var(--type-label)', padding: '0.3rem 0.75rem',
                                             opacity: isInstalled ? 0.5 : 1 }}
                                    disabled={isInstalled}
                                    onClick={() => !isInstalled && handlePull(catalogModel.name)}>
                                    {isInstalled ? '✓ Installed' : '↓ Download'}
                                </button>
                            </div>
                        );
                    })}
                </div>
            </section>
        </div>
    );
}
