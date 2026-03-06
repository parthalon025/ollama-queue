// What it shows: A horizontal pipeline with four nodes — Fetch, Generate, Judge, Done —
//   where the current stage is highlighted, completed stages get a checkmark, and
//   a one-line summary below shows which model is working and how far through the phase it is.
// Decision it drives: User knows exactly where in the eval pipeline the run is,
//   which AI model is active, and how many items remain in the current phase.

import { h } from 'preact';

// NOTE: .map() callbacks use descriptive param names — never 'h' (shadows JSX factory).

const STAGES = [
  { id: 'fetch_items', label: 'Fetch' },
  { id: 'generating',  label: 'Generate' },
  { id: 'judging',     label: 'Judge' },
  { id: 'done',        label: 'Done' },
];

// Ordered list used to determine "is this stage before/at/after current"
const STAGE_ORDER = ['fetch_items', 'generating', 'judging', 'done'];

// Map raw stage/status values to the 4 display nodes
function normalizeStage(stage, status) {
  if (status === 'complete') return 'done';
  if (status === 'cancelled' || status === 'failed') return stage || 'generating'; // show last-known stage
  if (stage === 'fetch_targets') return 'judging'; // instantaneous — collapse into judging
  return stage || 'fetch_items';
}

// Returns 'done' | 'active' | 'pending' for a node given the current pipeline position
function nodeState(nodeId, currentStage) {
  const curr = STAGE_ORDER.indexOf(currentStage);
  const node = STAGE_ORDER.indexOf(nodeId);
  if (curr < 0 || node < 0) return 'pending';
  if (node < curr) return 'done';
  if (node === curr) return 'active';
  return 'pending';
}

export default function EvalPipelineSwimline({ stage, status, generated, judged, total, pct, gen_model, judge_model }) {
  const current = normalizeStage(stage, status);
  // isJudging drives phaseLabel/model — only rendered when showInfo is true (not for 'done')
  const isJudging = current === 'judging' || current === 'done';
  const model = isJudging ? judge_model : gen_model;
  const count = isJudging ? (judged ?? 0) : (generated ?? 0);
  const phaseLabel = isJudging ? 'Scoring' : 'Writing';
  const showInfo = current !== 'fetch_items' && current !== 'done';

  return (
    <div class="eval-swimlane-wrap">
      {/* Horizontal stage nodes with connecting lines */}
      <div class="eval-swimlane">
        {STAGES.map((stg, idx) => {
          const state = nodeState(stg.id, current);
          const isLast = idx === STAGES.length - 1;
          // Connector after this node is "done" when the node itself is done
          const connDone = state === 'done';
          return [
            <div key={stg.id} class={`eval-swimlane-node eval-swimlane-node--${state}`}>
              <div class="eval-swimlane-node-icon">
                {state === 'done' ? '✓' : state === 'active' ? '◎' : '○'}
              </div>
              <div class="eval-swimlane-node-label">{stg.label}</div>
            </div>,
            !isLast ? (
              <div
                key={`conn-${idx}`}
                class={`eval-swimlane-connector${connDone ? ' eval-swimlane-connector--done' : ''}`}
              />
            ) : null,
          ];
        })}
      </div>

      {/* Info line: phase label · model · N / total (pct%) */}
      {showInfo && (
        <div class="eval-info-line">
          <span>
            {phaseLabel}
            {model && <span class="eval-model-badge"> · {model}</span>}
            {total > 0 && <span> · {count} / {total} ({pct}%)</span>}
          </span>
        </div>
      )}
    </div>
  );
}
