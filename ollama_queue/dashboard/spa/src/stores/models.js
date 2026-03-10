// What it does: Manages model-related reactive signals — installed models, active pulls,
//   and the downloadable model catalog.
// Decision it drives: The Models tab reads these signals to show what's installed,
//   what's downloading, and what's available to pull. Start/cancel pull actions flow
//   through the exported functions.

import { signal } from '@preact/signals';
import { API } from './_shared.js';

export const models = signal([]);
export const modelPulls = signal([]);
export const modelCatalog = signal({ curated: [], search_results: [] });

export async function fetchModels() {
    try {
        const resp = await fetch(`${API}/models`);
        if (resp.ok) models.value = await resp.json();
    } catch (e) {
        console.error('fetchModels failed:', e);
    }
}

export async function fetchModelCatalog(query = '') {
    try {
        const url = query ? `${API}/models/catalog?q=${encodeURIComponent(query)}`
                          : `${API}/models/catalog`;
        const resp = await fetch(url);
        if (resp.ok) modelCatalog.value = await resp.json();
    } catch (e) {
        console.error('fetchModelCatalog failed:', e);
    }
}

export async function startModelPull(modelName) {
    const resp = await fetch(`${API}/models/pull`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelName }),
    });
    if (!resp.ok) throw new Error(`Pull failed: ${resp.status}`);
    const { pull_id } = await resp.json();
    return pull_id;
}

export async function cancelModelPull(pullId) {
    const resp = await fetch(`${API}/models/pull/${pullId}`, { method: 'DELETE' });
    if (!resp.ok) {
        const msg = `Cancel pull failed: ${resp.status}`;
        console.error(msg);
        throw new Error(msg);
    }
}
