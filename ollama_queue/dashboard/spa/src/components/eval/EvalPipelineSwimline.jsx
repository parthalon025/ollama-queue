// What it shows: A horizontal pipeline with four nodes — Queue, Generate, Score, Done —
//   where the current stage is highlighted, completed stages get a checkmark, and
//   a one-line summary below shows which model is working and how far through the phase it is.
//   Internal fetch stages (fetch_items, fetch_targets) are collapsed into adjacent nodes
//   so the user always sees meaningful, model-centric progress — never a "Fetching…" spinner.
// Decision it drives: User knows exactly where in the eval pipeline the run is,
//   which AI model is active, and how many items remain in the current phase.

import { h } from 'preact';

// NOTE: .map() callbacks use descriptive param names — never 'h' (shadows JSX factory).

// Four user-facing stages. Instantaneous backend stages (fetch_items, fetch_targets)
// are collapsed into these nodes by normalizeStage below.
const STAGES = [
  { id: 'queued',     label: 'Queue' },
  { id: 'generating', label: 'Generate' },
  { id: 'judging',    label: 'Score' },
  { id: 'done',       label: 'Done' },
];

// Ordered list used to determine "is this stage before/at/after current"
const STAGE_ORDER = ['queued', 'generating', 'judging', 'done'];

// Map raw stage/status values to the 4 display nodes.
// fetch_items and fetch_targets are instantaneous backend steps — collapse them
// into the adjacent user-visible node so the pipeline starts on Generate, not Fetch.
function normalizeStage(stage, status) {
  if (status === 'complete') return 'done';
  if (status === 'queued' && !stage) return 'queued';
  if (!stage || stage === 'fetch_items') return 'generating'; // fetching items is pre-generate setup
  if (stage === 'fetch_targets') return 'judging';            // fetching targets is pre-score setup
  if (stage === 'generating') return 'generating';
  if (stage === 'judging') return 'judging';
  return 'queued';
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
  const isJudging = current === 'judging';
  const model = isJudging ? judge_model : gen_model;
  const count = isJudging ? (judged ?? 0) : (generated ?? 0);
  const phaseLabel = isJudging ? 'Scoring' : 'Generating';
  const showInfo = current === 'generating' || current === 'judging';

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
