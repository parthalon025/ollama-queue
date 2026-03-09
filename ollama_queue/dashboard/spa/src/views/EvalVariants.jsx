import { h } from 'preact';
import { useEffect } from 'preact/hooks';
import { fetchEvalVariants, fetchEvalTemplates } from '../store.js';
import VariantToolbar from '../components/eval/VariantToolbar.jsx';
import VariantTable from '../components/eval/VariantTable.jsx';
import TemplateSection from '../components/eval/TemplateSection.jsx';
import ConfigDiffPanel from '../components/eval/ConfigDiffPanel.jsx';
// What it shows: The Configurations view — all variant configs and prompt templates,
//   plus a config diff comparison panel for side-by-side variant analysis.
//   Toolbar for creating/generating/exporting configs, the full variant table,
//   and a collapsible template section below.
// Decision it drives: User manages which configs exist, compares differences between
//   two configs, and selects them for runs.
//   Cloning and editing allows fine-tuning without losing system defaults.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function EvalVariants() {
  useEffect(() => {
    // Load both when view mounts
    fetchEvalVariants();
    fetchEvalTemplates();
  }, []);

  return (
    <div class="flex flex-col gap-4 animate-page-enter">
      <VariantToolbar />
      <ConfigDiffPanel />
      <VariantTable />
      <TemplateSection />
    </div>
  );
}
