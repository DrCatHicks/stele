import { describe, expect, it } from 'vitest';

import { formatDate, formatDateTime } from './format';

describe('formatDate', () => {
  it('formats in UTC so a midnight-UTC timestamp keeps its day', () => {
    // The bug being guarded: toLocaleDateString in a negative-offset zone would
    // render 2025-12-31 for this instant. UTC formatting keeps it on the 1st.
    expect(formatDate('2026-01-01T00:00:00Z')).toBe('2026-01-01');
  });
});

describe('formatDateTime', () => {
  it('includes the time and an explicit UTC label', () => {
    expect(formatDateTime('2026-01-31T14:05:00Z')).toBe('2026-01-31, 14:05 UTC');
  });
});
