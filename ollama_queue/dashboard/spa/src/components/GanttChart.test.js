// ollama_queue/dashboard/spa/src/components/GanttChart.test.js
import { sourceColor, formatDuration } from './GanttChart.jsx';

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
