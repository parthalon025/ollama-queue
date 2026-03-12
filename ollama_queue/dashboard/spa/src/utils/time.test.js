import { formatDuration } from './time.js';

test('formats seconds under a minute', () => {
  expect(formatDuration(45)).toBe('45s');
  expect(formatDuration(0)).toBe('0s');
});

test('formats minutes and seconds', () => {
  expect(formatDuration(90)).toBe('1m 30s');
  expect(formatDuration(60)).toBe('1m 0s');
});

test('formats hours', () => {
  expect(formatDuration(3720)).toBe('1h 2m');
});

test('handles null, undefined, negative', () => {
  expect(formatDuration(null)).toBe('--');
  expect(formatDuration(undefined)).toBe('--');
  expect(formatDuration(-1)).toBe('--');
});

test('handles zero', () => {
  expect(formatDuration(0)).toBe('0s');
});

test('formats exactly 1 hour', () => {
  expect(formatDuration(3600)).toBe('1h 0m');
});

test('handles very large values', () => {
  expect(formatDuration(7380)).toBe('2h 3m');
});
