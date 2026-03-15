# ShShatter Timing Fix Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two broken shatter animations in `History.jsx` — DLQ dismiss and clear-all both fire the API call concurrently with the animation, causing Preact to unmount rows mid-shatter.

**Architecture:** Two targeted edits to `History.jsx`. The fix in both cases is the same principle: move the API call (and resulting signal update) into `shatterElement`'s `onComplete` callback so DOM removal only happens after the 650ms animation completes. Design system rule: "Never remove DOM elements synchronously. Play exit animation first."

**Tech Stack:** Preact 10, `@preact/signals`, `shatterElement` from `superhot-ui` (600ms default, `onComplete` fires at 650ms). No new deps, no new files.

---

## Pre-flight

```bash
cd ~/Documents/projects/ollama-queue
git checkout -b fix/shatter-timing
cd ollama_queue/dashboard/spa && npm run build   # must be clean before starting
```

---

## Chunk 1: DLQ Row Dismiss

### Task 1: Fix DLQ dismiss — shatter before API call

**File:** `ollama_queue/dashboard/spa/src/pages/History.jsx`

**Context:** `DLQRow` dismiss button (lines ~380–387). Current code fires `shatterElement` and `dismissAct` concurrently. `dismissAct` calls `onAction('dismiss', id)` which calls `fetchDLQ()`, which updates `dlqEntries.value`, which causes Preact to unmount the row DOM node ~100ms later — mid-animation.

**The rule (design system §7.5):** Never remove DOM elements synchronously. Play exit animation first.

- [ ] **Step 1: Read the current dismiss button code**

  Open `ollama_queue/dashboard/spa/src/pages/History.jsx` and find the dismiss button `onClick` (search for `shatterElement(rowRef.current)`). It currently looks like:

  ```jsx
  onClick={() => {
      if (rowRef.current) shatterElement(rowRef.current);
      dismissAct(
          'Dismissing…',
          () => onAction('dismiss', entry.id),
          `DLQ #${entry.id} dismissed`,
      );
  }}
  ```

- [ ] **Step 2: Rewrite dismiss onClick**

  Replace the onClick with a sequenced version: shatter fires first, API call moves into `onComplete`. Add a fallback path for when `rowRef.current` is null (element already gone).

  ```jsx
  onClick={() => {
      if (rowRef.current) {
          shatterElement(rowRef.current, {
              onComplete: () => dismissAct(
                  'Dismissing…',
                  () => onAction('dismiss', entry.id),
                  `DLQ #${entry.id} dismissed`,
              ),
          });
      } else {
          dismissAct(
              'Dismissing…',
              () => onAction('dismiss', entry.id),
              `DLQ #${entry.id} dismissed`,
          );
      }
  }}
  ```

  **Why the fallback matters:** `shatterElement` checks `element.parentNode` at entry — if null, it calls `onComplete()` directly and returns. So technically the fallback branch is redundant, but it makes the intent explicit and defends against a null ref.

- [ ] **Step 3: Verify build passes**

  ```bash
  cd ollama_queue/dashboard/spa && npm run build
  ```

  Expected: exits 0, no errors.

- [ ] **Step 4: Commit**

  ```bash
  git add ollama_queue/dashboard/spa/src/pages/History.jsx
  git commit -m "fix(spa): sequence DLQ dismiss — shatter completes before API call removes row"
  ```

---

## Chunk 2: Clear All DLQ Cascade

### Task 2: Fix cascade shatter in handleClearDLQ — Promise on last onComplete

**File:** `ollama_queue/dashboard/spa/src/pages/History.jsx`

**Context:** `handleClearDLQ` (lines ~83–103). Current code staggers shatters over `rows.length × 80ms`, then waits a hardcoded `300ms`, then calls the API. With 5 rows the last shatter starts at 320ms and takes 650ms to complete — total ~970ms — but the API fires at 300ms, causing `fetchDLQ()` to unmount all rows ~400ms before the last animation ends.

**The fix:** Wrap the entire stagger in a `Promise` that resolves only when the last row's `onComplete` fires. The API call happens after the `await`, guaranteeing all animations finish first.

- [ ] **Step 1: Read the current handleClearDLQ**

  Find `handleClearDLQ` in `History.jsx`. It currently looks like:

  ```jsx
  async function handleClearDLQ() {
      if (!window.confirm('Permanently delete all failed jobs? This cannot be undone.')) return;
      await clearAct(
          'Clearing DLQ…',
          async () => {
              // Stagger-shatter all visible DLQ row elements before clearing
              if (dlqListRef.current) {
                  const rows = Array.from(dlqListRef.current.children);
                  rows.forEach((row, i) => {
                      setTimeout(() => shatterElement(row), i * 80);
                  });
              }
              // Wait for animations to be visible before making the API call
              await new Promise(resolve => setTimeout(resolve, 300));
              const res = await fetch(`${API}/dlq`, { method: 'DELETE' });
              if (!res.ok) throw new Error(`Clear failed: ${res.status}`);
              await fetchDLQ();
          },
          'All failed jobs deleted',
      );
  }
  ```

- [ ] **Step 2: Rewrite the inner async function**

  Replace the hardcoded 300ms wait with a Promise that resolves on the last row's `onComplete`. Keep the same outer `clearAct` structure.

  ```jsx
  async function handleClearDLQ() {
      if (!window.confirm('Permanently delete all failed jobs? This cannot be undone.')) return;
      await clearAct(
          'Clearing DLQ…',
          async () => {
              if (dlqListRef.current) {
                  const rows = Array.from(dlqListRef.current.children);
                  if (rows.length > 0) {
                      await new Promise(resolve => {
                          rows.forEach((row, i) => {
                              setTimeout(() => {
                                  shatterElement(row, {
                                      onComplete: i === rows.length - 1 ? resolve : undefined,
                                  });
                              }, i * 80);
                          });
                      });
                  }
              }
              const res = await fetch(`${API}/dlq`, { method: 'DELETE' });
              if (!res.ok) throw new Error(`Clear failed: ${res.status}`);
              await fetchDLQ();
          },
          'All failed jobs deleted',
      );
  }
  ```

  **Key details:**
  - `rows.length > 0` guard: if the DLQ list is empty (count badge showed stale data), skip the animation Promise entirely and go straight to API.
  - Only the last row (`i === rows.length - 1`) gets `onComplete: resolve`. Earlier rows get `undefined` — their animations run to completion independently, and `shatterElement` skips the callback safely.
  - The stagger is `i * 80ms` per row. For 10 rows: last starts at 720ms, completes at ~1370ms. The Promise resolves at exactly that point — no hardcoded estimate needed.

- [ ] **Step 3: Verify build passes**

  ```bash
  cd ollama_queue/dashboard/spa && npm run build
  ```

  Expected: exits 0, no errors.

- [ ] **Step 4: Commit**

  ```bash
  git add ollama_queue/dashboard/spa/src/pages/History.jsx
  git commit -m "fix(spa): cascade DLQ clear waits for last shatter onComplete before API call"
  ```

---

## Smoke Test

With `ollama-queue serve` running, navigate to the History tab:

1. **DLQ dismiss (if DLQ entries exist):** Click "Delete" on a DLQ entry. The row should fragment into triangular shards that drift and fade (~650ms) **before** the row disappears from the list. Previously the row would vanish immediately (DOM unmount cut animation).

2. **DLQ clear all:** Click "Delete all", confirm. All rows should stagger-shatter (80ms apart) and the list should only empty **after** the last animation completes. Previously the list would clear at ~300ms, cutting off rows that started shatter after that point.

If no DLQ entries exist, submit a job with `max_retries=0` and a bad command (`ollama-queue submit --source test --model nonexistent -- false`) to generate one quickly.

---

## Finish

```bash
cd ~/Documents/projects/ollama-queue
git log --oneline -3   # verify both commits are clean
```

Then invoke the `superpowers:finishing-a-development-branch` skill to PR and merge.
