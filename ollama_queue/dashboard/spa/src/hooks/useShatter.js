// What it does: Returns a ref + fire function for tiered shatter effects on buttons.
//   Enforces effect budget via isOverBudget check — suppresses shatters when the
//   system already has too many simultaneous visual effects.
// Decision it drives: Every action button in the SPA shatters on click — fragment
//   count communicates intent (earned > complete > routine). Budget prevents overload.

import { useRef, useCallback } from 'preact/hooks';
import { shatterElement } from 'superhot-ui';
import { canFireEffect, isOverBudget } from '../stores/atmosphere.js';

const TIER_PRESETS = {
  earned:   { fragments: 7 },
  complete: { fragments: 6 },
  routine:  { fragments: 3 },
};

export function useShatter(tier = 'routine') {
  const ref = useRef(null);

  // fire() accepts an optional event or element for loop contexts where
  // a single ref can't target the correct button (ref.current always
  // points to the last-rendered element in a .map() loop).
  const fire = useCallback((evOrEl) => {
    const el = evOrEl?.currentTarget || evOrEl || ref.current;
    if (!el) return;

    // Global effect budget gate — suppress all shatters when over budget
    if (isOverBudget()) return;

    // Non-routine tiers consume a budget slot and release on animation complete
    if (tier !== 'routine') {
      const cleanup = canFireEffect('shatter-' + tier);
      if (!cleanup) return;
      // Release budget slot when fragment animation completes
      shatterElement(el, { ...(TIER_PRESETS[tier] || TIER_PRESETS.routine), onComplete: cleanup });
      return;
    }
    shatterElement(el, TIER_PRESETS[tier] || TIER_PRESETS.routine);
  }, [tier]);

  return [ref, fire];
}
