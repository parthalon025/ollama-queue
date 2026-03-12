// Priority encoding utilities — Treisman (1980): combine color + independent channel
// for colorblind safety. Border thickness is independent of hue.

export function priorityBorderWidth(priority) {
  if (priority <= 2) return '4px';  // Critical
  if (priority <= 4) return '3px';  // High
  if (priority <= 6) return '2px';  // Normal
  if (priority <= 8) return '1px';  // Low
  return '1px';                      // Background (opacity handled separately)
}

export function priorityBorderOpacity(priority) {
  return priority >= 9 ? '0.65' : '1';
}
