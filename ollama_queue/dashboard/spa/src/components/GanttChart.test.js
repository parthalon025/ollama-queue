// ollama_queue/dashboard/spa/src/components/GanttChart.test.js
import { sourceColor, formatDuration, assignLanes, buildTooltip, buildDensityBuckets, findHeavyConflicts, getConflictingPairs, runStatus, alignLoadMapToNow, loadMapSlotColor } from './GanttChart.jsx';

describe('sourceColor', () => {
    it('returns accent for aria', () => {
        expect(sourceColor('aria')).toBe('var(--accent)');
    });
    it('returns accent for aria-full (prefix match)', () => {
        expect(sourceColor('aria-full')).toBe('var(--accent)');
    });
    it('returns accent for aria-intraday (prefix match)', () => {
        expect(sourceColor('aria-intraday')).toBe('var(--accent)');
    });
    it('returns telegram token for telegram', () => {
        expect(sourceColor('telegram')).toBe('var(--source-telegram)');
    });
    it('returns telegram token for telegram-brief-morning (prefix match)', () => {
        expect(sourceColor('telegram-brief-morning')).toBe('var(--source-telegram)');
    });
    it('returns notion token for notion', () => {
        expect(sourceColor('notion')).toBe('var(--source-notion)');
    });
    it('returns notion token for notion-vector-sync (prefix match)', () => {
        expect(sourceColor('notion-vector-sync')).toBe('var(--source-notion)');
    });
    it('returns default for unknown source', () => {
        expect(sourceColor('unknown')).toBe('var(--source-default)');
    });
    it('returns default for "none"', () => {
        expect(sourceColor('none')).toBe('var(--source-default)');
    });
    it('is case-insensitive', () => {
        expect(sourceColor('Aria')).toBe('var(--accent)');
        expect(sourceColor('Aria-Full')).toBe('var(--accent)');
    });
    it('handles null/undefined', () => {
        expect(sourceColor(null)).toBe('var(--source-default)');
        expect(sourceColor(undefined)).toBe('var(--source-default)');
    });
});

describe('formatDuration', () => {
    it('formats seconds under 60 as Xs', () => {
        expect(formatDuration(45)).toBe('45s');
    });
    it('formats seconds as Xm for >= 60', () => {
        expect(formatDuration(90)).toBe('1m 30s');
    });
    it('formats 600 as 10m', () => {
        expect(formatDuration(600)).toBe('10m');
    });
    it('handles null with default', () => {
        expect(formatDuration(null)).toBe('~10m');
    });
});

describe('formatDuration edge cases', () => {
    it('formats 0 as 0s', () => {
        expect(formatDuration(0)).toBe('0s');
    });
    it('formatDuration(59) returns 59s not 60s (floor not round)', () => {
        expect(formatDuration(59)).toBe('59s');
    });
    it('formatDuration(59.9) returns 59s not 60s (floor not round)', () => {
        expect(formatDuration(59.9)).toBe('59s');
    });
});

describe('assignLanes', () => {
    it('returns empty array for no jobs', () => {
        expect(assignLanes([])).toEqual([]);
    });
    it('assigns single job to lane 0', () => {
        const jobs = [{ id: 1, next_run: 1000, estimated_duration: 600 }];
        const result = assignLanes(jobs);
        expect(result[0]._lane).toBe(0);
    });
    it('assigns non-overlapping jobs to lane 0', () => {
        const jobs = [
            { id: 1, next_run: 1000, estimated_duration: 600 },
            { id: 2, next_run: 2000, estimated_duration: 600 },
        ];
        const result = assignLanes(jobs);
        expect(result.every(job => job._lane === 0)).toBe(true);
    });
    it('assigns overlapping jobs to separate lanes', () => {
        const jobs = [
            { id: 1, next_run: 1000, estimated_duration: 600 },
            { id: 2, next_run: 1300, estimated_duration: 600 },
        ];
        const result = assignLanes(jobs);
        const lanes = result.map(job => job._lane);
        expect(new Set(lanes).size).toBe(2);
    });
    it('handles null estimated_duration with 600 default', () => {
        const jobs = [
            { id: 1, next_run: 1000, estimated_duration: null },
            { id: 2, next_run: 1300, estimated_duration: null },
        ];
        const result = assignLanes(jobs);
        const lanes = result.map(job => job._lane);
        expect(new Set(lanes).size).toBe(2);
    });
});

describe('buildTooltip', () => {
    it('includes job name', () => {
        const job = { name: 'aria-morning', source: 'aria', model: null, model_profile: 'ollama', next_run: Date.now() / 1000 + 3600, estimated_duration: 600, last_run: null };
        expect(buildTooltip(job, false)).toContain('aria-morning');
    });
    it('includes source', () => {
        const job = { name: 'test', source: 'telegram', model: null, model_profile: 'ollama', next_run: Date.now() / 1000 + 3600, estimated_duration: 300, last_run: null };
        expect(buildTooltip(job, false)).toContain('telegram');
    });
    it('shows "never" for null last_run', () => {
        const job = { name: 'test', source: 'aria', model: null, model_profile: 'ollama', next_run: Date.now() / 1000 + 3600, estimated_duration: 300, last_run: null };
        expect(buildTooltip(job, false)).toContain('never');
    });
    it('includes concurrent marker when isConcurrent', () => {
        const job = { name: 'test', source: 'aria', model: null, model_profile: 'ollama', next_run: Date.now() / 1000 + 3600, estimated_duration: 300, last_run: null };
        expect(buildTooltip(job, true)).toContain('⟡');
    });
    it('omits concurrent marker when not concurrent', () => {
        const job = { name: 'test', source: 'aria', model: null, model_profile: 'ollama', next_run: Date.now() / 1000 + 3600, estimated_duration: 300, last_run: null };
        expect(buildTooltip(job, false)).not.toContain('⟡');
    });
});

describe('buildDensityBuckets', () => {
    const now = 1000000;
    const windowSecs = 24 * 3600;

    it('returns 48 buckets', () => {
        expect(buildDensityBuckets([], now, windowSecs)).toHaveLength(48);
    });

    it('all buckets zero for empty jobs', () => {
        const buckets = buildDensityBuckets([], now, windowSecs);
        expect(buckets.every(b => b === 0)).toBe(true);
    });

    it('counts a job that spans the first two buckets (3600s = 2×1800s slots)', () => {
        const jobs = [{ next_run: now, estimated_duration: 3600 }];
        const buckets = buildDensityBuckets(jobs, now, windowSecs);
        expect(buckets[0]).toBe(1);
        expect(buckets[1]).toBe(1);
        expect(buckets[2]).toBe(0);
    });

    it('counts a job spanning four buckets (7200s = 4×1800s slots)', () => {
        const jobs = [{ next_run: now, estimated_duration: 7200 }];
        const buckets = buildDensityBuckets(jobs, now, windowSecs);
        expect(buckets[0]).toBe(1);
        expect(buckets[1]).toBe(1);
        expect(buckets[2]).toBe(1);
        expect(buckets[3]).toBe(1);
        expect(buckets[4]).toBe(0);
    });

    it('counts two concurrent jobs in same bucket', () => {
        const jobs = [
            { next_run: now, estimated_duration: 1800 },
            { next_run: now + 900, estimated_duration: 1800 },
        ];
        const buckets = buildDensityBuckets(jobs, now, windowSecs);
        expect(buckets[0]).toBe(2);
    });

    it('uses 600s default when estimated_duration is null', () => {
        const jobs = [{ next_run: now, estimated_duration: null }];
        const buckets = buildDensityBuckets(jobs, now, windowSecs);
        expect(buckets[0]).toBe(1); // 600s fits in one 1800s bucket
    });

    it('scales bucket count with windowSecs (12h window = 24 buckets)', () => {
        const halfDayWindow = 12 * 3600;
        const buckets = buildDensityBuckets([], now, halfDayWindow);
        expect(buckets).toHaveLength(24); // 12h / 1800s = 24 buckets
    });
});

describe('findHeavyConflicts', () => {
    it('returns empty Set for no jobs', () => {
        expect(findHeavyConflicts([])).toEqual(new Set());
    });

    it('returns empty Set for a single heavy job', () => {
        const jobs = [{ id: 1, model_profile: 'heavy', next_run: 1000, estimated_duration: 600 }];
        expect(findHeavyConflicts(jobs).size).toBe(0);
    });

    it('returns empty Set when heavy jobs do not overlap', () => {
        const jobs = [
            { id: 1, model_profile: 'heavy', next_run: 1000, estimated_duration: 600 },
            { id: 2, model_profile: 'heavy', next_run: 2000, estimated_duration: 600 },
        ];
        expect(findHeavyConflicts(jobs).size).toBe(0);
    });

    it('flags both jobs when two heavy jobs overlap', () => {
        const jobs = [
            { id: 1, model_profile: 'heavy', next_run: 1000, estimated_duration: 600 },
            { id: 2, model_profile: 'heavy', next_run: 1300, estimated_duration: 600 },
        ];
        const conflicts = findHeavyConflicts(jobs);
        expect(conflicts.has(1)).toBe(true);
        expect(conflicts.has(2)).toBe(true);
    });

    it('does not flag non-heavy overlapping jobs', () => {
        const jobs = [
            { id: 1, model_profile: 'ollama', next_run: 1000, estimated_duration: 600 },
            { id: 2, model_profile: 'ollama', next_run: 1300, estimated_duration: 600 },
        ];
        expect(findHeavyConflicts(jobs).size).toBe(0);
    });

    it('does not flag heavy job that overlaps with non-heavy', () => {
        const jobs = [
            { id: 1, model_profile: 'heavy', next_run: 1000, estimated_duration: 600 },
            { id: 2, model_profile: 'ollama', next_run: 1300, estimated_duration: 600 },
        ];
        expect(findHeavyConflicts(jobs).size).toBe(0);
    });

    it('handles exactly-touching jobs (end === start) as non-overlapping', () => {
        const jobs = [
            { id: 1, model_profile: 'heavy', next_run: 1000, estimated_duration: 600 },
            { id: 2, model_profile: 'heavy', next_run: 1600, estimated_duration: 600 },
        ];
        // job 1 ends at 1600, job 2 starts at 1600 — strict < means no overlap
        expect(findHeavyConflicts(jobs).size).toBe(0);
    });
});

describe('getConflictingPairs', () => {
    it('returns empty array for empty input', () => {
        expect(getConflictingPairs([])).toEqual([]);
    });

    it('returns empty array for a single job', () => {
        const jobs = [{ id: 1, next_run: 1000, estimated_duration: 600 }];
        expect(getConflictingPairs(jobs)).toEqual([]);
    });

    it('returns [[a, b]] when two jobs overlap', () => {
        const a = { id: 1, next_run: 1000, estimated_duration: 600 };
        const b = { id: 2, next_run: 1300, estimated_duration: 600 };
        const result = getConflictingPairs([a, b]);
        expect(result).toHaveLength(1);
        expect(result[0][0]).toBe(a);
        expect(result[0][1]).toBe(b);
    });

    it('returns empty array when two jobs do not overlap', () => {
        const jobs = [
            { id: 1, next_run: 1000, estimated_duration: 600 },
            { id: 2, next_run: 2000, estimated_duration: 600 },
        ];
        expect(getConflictingPairs(jobs)).toEqual([]);
    });

    it('treats exactly-touching jobs (end === start) as non-overlapping', () => {
        const jobs = [
            { id: 1, next_run: 1000, estimated_duration: 600 },
            { id: 2, next_run: 1600, estimated_duration: 600 },
        ];
        // job 1 ends at 1600, job 2 starts at 1600 — strict < means no overlap
        expect(getConflictingPairs(jobs)).toEqual([]);
    });

    it('uses 600s default when estimated_duration is null', () => {
        const a = { id: 1, next_run: 1000, estimated_duration: null };
        const b = { id: 2, next_run: 1300, estimated_duration: null };
        // both default to 600s: a ends at 1600, b ends at 1900 — overlap
        const result = getConflictingPairs([a, b]);
        expect(result).toHaveLength(1);
    });

    it('does not filter by model_profile — caller decides what to pass', () => {
        // both jobs are ollama profile; getConflictingPairs still detects the overlap
        const a = { id: 1, model_profile: 'ollama', next_run: 1000, estimated_duration: 600 };
        const b = { id: 2, model_profile: 'ollama', next_run: 1300, estimated_duration: 600 };
        const result = getConflictingPairs([a, b]);
        expect(result).toHaveLength(1);
    });

    it('returns all overlapping pairs when three jobs all overlap', () => {
        const a = { id: 1, next_run: 1000, estimated_duration: 600 };
        const b = { id: 2, next_run: 1100, estimated_duration: 600 };
        const c = { id: 3, next_run: 1200, estimated_duration: 600 };
        // a-b, a-c, and b-c all overlap
        const result = getConflictingPairs([a, b, c]);
        expect(result).toHaveLength(3);
    });
});

describe('runStatus', () => {
    const NOW = 2_000_000; // fixed reference

    it('returns never for null last_run', () => {
        expect(runStatus(null, 3600, NOW)).toEqual({ label: 'never run yet', color: 'var(--text-tertiary)' });
    });

    it('returns never for undefined last_run', () => {
        expect(runStatus(undefined, 3600, NOW)).toEqual({ label: 'never run yet', color: 'var(--text-tertiary)' });
    });

    it('returns on-time when drift is within 5% of interval', () => {
        const interval = 3600;
        // 3% drift — within threshold
        const lastRun = NOW - interval - interval * 0.03;
        expect(runStatus(lastRun, interval, NOW).label).toBe('running on schedule');
    });

    it('returns late when drift exceeds 5% of interval', () => {
        const interval = 3600;
        // 10% drift — past threshold
        const lastRun = NOW - interval - interval * 0.10;
        expect(runStatus(lastRun, interval, NOW).label).toBe('running behind');
    });

    it('returns on-time when exactly at the boundary (5%)', () => {
        const interval = 3600;
        // Exactly at threshold — drift = 5% = threshold, so drift <= threshold is true
        const lastRun = NOW - interval - interval * 0.05;
        expect(runStatus(lastRun, interval, NOW).label).toBe('running on schedule');
    });

    it('handles null interval_seconds with 3600 default', () => {
        // No interval — uses 3600 default; ran 3% late relative to 3600
        const lastRun = NOW - 3600 - 3600 * 0.03;
        expect(runStatus(lastRun, null, NOW).label).toBe('running on schedule');
    });
});

describe('alignLoadMapToNow', () => {
    // 2025-01-01 00:00:00 UTC in seconds — midnight, so nowSlot=0, array unchanged
    const MIDNIGHT_UTC = 1735689600;
    // 2025-01-01 01:00:00 UTC — slot 2 (3600s / 1800s = 2)
    const HOUR1_UTC = MIDNIGHT_UTC + 3600;

    it('returns empty array for null slots', () => {
        expect(alignLoadMapToNow(null, MIDNIGHT_UTC)).toEqual([]);
    });

    it('returns empty array for empty slots', () => {
        expect(alignLoadMapToNow([], MIDNIGHT_UTC)).toEqual([]);
    });

    it('does not rotate at midnight (slot 0)', () => {
        const slots = [10, 20, 30, 40];
        expect(alignLoadMapToNow(slots, MIDNIGHT_UTC)).toEqual([10, 20, 30, 40]);
    });

    it('rotates by 2 at 01:00 UTC (slot 2)', () => {
        const slots = [10, 20, 30, 40];
        // nowSlot=2, so result = [slots[2], slots[3], slots[0], slots[1]]
        expect(alignLoadMapToNow(slots, HOUR1_UTC)).toEqual([30, 40, 10, 20]);
    });

    it('preserves array length after rotation', () => {
        const slots = Array.from({ length: 48 }, (_, i) => i);
        const result = alignLoadMapToNow(slots, HOUR1_UTC);
        expect(result).toHaveLength(48);
    });
});

describe('loadMapSlotColor', () => {
    it('returns amber for pinned slot (score >= 999)', () => {
        expect(loadMapSlotColor(999)).toBe('color-mix(in oklch, var(--status-warning) 85%, transparent)');
        expect(loadMapSlotColor(1000)).toBe('color-mix(in oklch, var(--status-warning) 85%, transparent)');
    });

    it('returns bg-inset for zero score', () => {
        expect(loadMapSlotColor(0)).toBe('var(--bg-inset)');
    });

    it('returns bg-inset for negative score', () => {
        expect(loadMapSlotColor(-1)).toBe('var(--bg-inset)');
    });

    it('returns minimum-opacity blue for score 1', () => {
        // intensity = 1/10 = 0.1; opacity = 0.20 + 0.1*0.70 = 0.27
        expect(loadMapSlotColor(1)).toBe('rgba(99,179,237,0.27)');
    });

    it('returns maximum-opacity blue for score 10', () => {
        // intensity = 10/10 = 1.0; opacity = 0.20 + 1.0*0.70 = 0.90
        expect(loadMapSlotColor(10)).toBe('rgba(99,179,237,0.90)');
    });

    it('clamps at max opacity for scores above 10', () => {
        expect(loadMapSlotColor(20)).toBe('rgba(99,179,237,0.90)');
    });
});
