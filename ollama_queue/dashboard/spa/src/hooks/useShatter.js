// What it does: Returns a ref + fire function for tiered shatter effects on buttons.
// Decision it drives: Every action button in the SPA shatters on click — fragment
//   count communicates intent (earned > complete > routine).

import { useRef, useCallback } from 'preact/hooks';
import { shatterElement } from 'superhot-ui';
import { canFireEffect } from '../stores/atmosphere.js';

const TIER_PRESETS = {
  earned:   { fragments: 7 },
  complete: { fragments: 6 },
  routine:  { fragments: 3 },
};

export function useShatter(tier = 'routine') {
  const ref = useRef(null);

  const fire = useCallback(() => {
    if (!ref.current) return;
    // Routine tier skips effect budget — too fast and small to count
    if (tier !== 'routine') {
      const cleanup = canFireEffect('shatter-' + tier);
      if (!cleanup) return;
      // cleanup is called automatically when fragment animation ends
    }
    shatterElement(ref.current, TIER_PRESETS[tier] || TIER_PRESETS.routine);
  }, [tier]);

  return [ref, fire];
}
