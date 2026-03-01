// ollama_queue/dashboard/spa/src/components/GanttChart.test.js
import { sourceColor, formatDuration, assignLanes, buildTooltip, buildDensityBuckets } from './GanttChart.jsx';

describe('sourceColor', () => {
    it('returns accent for aria', () => {
        expect(sourceColor('aria')).toBe('var(--accent)');
    });
    it('returns orange for telegram', () => {
        expect(sourceColor('telegram')).toBe('#f97316');
    });
    it('returns purple for notion', () => {
        expect(sourceColor('notion')).toBe('#a78bfa');
    });
    it('returns tertiary for unknown source', () => {
        expect(sourceColor('unknown')).toBe('var(--text-tertiary)');
    });
    it('is case-insensitive', () => {
        expect(sourceColor('Aria')).toBe('var(--accent)');
    });
    it('handles null/undefined', () => {
        expect(sourceColor(null)).toBe('var(--text-tertiary)');
        expect(sourceColor(undefined)).toBe('var(--text-tertiary)');
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

    it('returns 24 buckets', () => {
        expect(buildDensityBuckets([], now, windowSecs)).toHaveLength(24);
    });

    it('all buckets zero for empty jobs', () => {
        const buckets = buildDensityBuckets([], now, windowSecs);
        expect(buckets.every(b => b === 0)).toBe(true);
    });

    it('counts a job that spans the first bucket', () => {
        const jobs = [{ next_run: now, estimated_duration: 3600 }];
        const buckets = buildDensityBuckets(jobs, now, windowSecs);
        expect(buckets[0]).toBe(1);
        expect(buckets[1]).toBe(0);
    });

    it('counts a job spanning multiple buckets', () => {
        const jobs = [{ next_run: now, estimated_duration: 7200 }];
        const buckets = buildDensityBuckets(jobs, now, windowSecs);
        expect(buckets[0]).toBe(1);
        expect(buckets[1]).toBe(1);
        expect(buckets[2]).toBe(0);
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
        expect(buckets[0]).toBe(1); // 600s < 3600s so only bucket 0
    });
});
