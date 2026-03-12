/**
 * What it shows: Your library of prompt variants — each displayed as a card
 *   showing its score, stability, provider, and key settings. Like a deck of
 *   recipe cards, each for a different way to ask the AI to find lessons.
 * Decision it drives: "Which variant should I promote? Which one to clone for
 *   the next round of testing? Which ones to compare side-by-side?"
 */
import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import { evalVariants, fetchEvalVariants, focusVariantId } from '../stores/eval.js';
import VariantCard from '../components/eval/VariantCard.jsx';
import ConfigDiffPanel from '../components/eval/ConfigDiffPanel.jsx';

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function EvalVariants() {
  const [selected, setSelected] = useState([]);

  useEffect(() => {
    fetchEvalVariants();
  }, []);

  // Sort variants by latest_f1 descending; variants without a score go last
  const variants = [...evalVariants.value].sort((a, b) =>
    (b.latest_f1 ?? -1) - (a.latest_f1 ?? -1)
  );

  function toggleSelect(id, checked) {
    setSelected(prev => checked ? [...prev, id] : prev.filter(x => x !== id));
  }

  return (
    <div class="eval-variants">
      <div class="eval-variants__toolbar">
        <button onClick={() => { /* open create form */ }}>+ Create</button>
        {selected.length >= 2 && <span>{selected.length} selected for compare</span>}
      </div>

      {selected.length >= 2 && <ConfigDiffPanel />}

      <div class="variant-grid">
        {variants.map(v => (
          <VariantCard
            key={v.id}
            variant={v}
            selected={selected.includes(v.id)}
            onSelect={checked => toggleSelect(v.id, checked)}
            onClone={() => { /* clone logic */ }}
            onEdit={() => { focusVariantId.value = v.id; }}
            onDelete={() => fetchEvalVariants()}
          />
        ))}
      </div>
    </div>
  );
}
