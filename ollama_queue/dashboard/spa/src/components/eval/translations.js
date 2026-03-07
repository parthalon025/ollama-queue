// What it shows: nothing — pure data
// Decision it drives: All eval UI labels use these translations to stay jargon-free
//   (L1/L2 plain English, L3 shows technical term with tooltip)

export const EVAL_TRANSLATIONS = {
  f1:                { label: 'Quality score',              tooltip: 'Combined accuracy + completeness. Higher is better.' },
  recall:            { label: 'Catches right patterns',     tooltip: 'How often the principle matches the correct target.' },
  precision:         { label: 'Avoids false matches',       tooltip: 'How often a match is actually correct.' },
  actionability:     { label: 'Useful for preventing bugs', tooltip: 'Whether the principle gives specific, actionable guidance.' },
  temperature:       { label: 'Creativity',                 tooltip: '0=focused, 1=varied. Lower values give more consistent output.' },
  num_ctx:           { label: 'Memory window',              tooltip: 'How much text the model reads at once, in tokens.' },
  judge_model:       { label: 'Scorer AI',                  tooltip: 'Model used to evaluate generated principles.' },
  judge_backend:     { label: 'Scorer provider',            tooltip: 'ollama = local model, openai = GPT-4o-mini via API.' },
  judge_temperature: { label: 'Scorer consistency',         tooltip: 'Low values (0.1) make the scorer more deterministic.' },
  error_budget:      { label: 'Failure tolerance',          tooltip: 'Run pauses if this fraction of jobs fail. Default 30%.' },
  per_cluster:       { label: 'Items per group',            tooltip: 'How many lessons to sample from each cluster per run.' },
  f1_threshold:      { label: 'Promotion threshold',        tooltip: 'Minimum quality score required to promote a configuration.' },
  stability_window:  { label: 'Stability window',           tooltip: 'Number of recent runs averaged to determine stability.' },
  auto_promote:                 { label: 'Auto-promote',        tooltip: 'Automatically promote the winner when all quality gates pass. Off by default.' },
  auto_promote_min_improvement: { label: 'Min improvement',     tooltip: 'Minimum quality score gain over current production required to auto-promote.' },
  'zero-shot-causal':{ label: 'Figure it out',              tooltip: 'Model reasons from cause to effect without examples.' },
  fewshot:           { label: 'Learn from examples first',  tooltip: 'Model sees examples before generating.' },
  chunked:           { label: 'Show examples in groups',    tooltip: 'Examples grouped by type for context.' },
  generating:        { label: 'Writing principles\u2026',   tooltip: null },
  judging:           { label: 'Scoring results\u2026',      tooltip: null },
  pending:           { label: 'Waiting to start',           tooltip: null },
  complete:          { label: 'Done',                       tooltip: null },
  failed:            { label: 'Failed',                     tooltip: null },
  cancelled:         { label: 'Cancelled',                  tooltip: null },
  batch:             { label: 'Full speed',                 tooltip: 'Submit all jobs now. Fastest option.' },
  opportunistic:     { label: 'One at a time',              tooltip: 'One job at a time, only when queue is idle.' },
  'fill-open-slots': { label: 'Fill open slots',            tooltip: 'Use all available slots until time or run limit.' },
  scheduled:         { label: 'Scheduled',                  tooltip: 'Start at a specific time or on a recurring schedule.' },
};
