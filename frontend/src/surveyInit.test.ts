import { Serializer } from 'survey-core';
import { describe, expect, it } from 'vitest';

import './surveyInit';

describe('surveyInit', () => {
  // Regression test for GH #49: on iOS Safari the default longTap=true makes
  // the initial touch on a ranking item scroll the page instead of starting
  // a drag. Disabling longTap is what restores tap-and-drag reorder on mobile,
  // so a future bump of survey-core that resets the default would silently
  // re-break iPhone reordering — this test catches that.
  it('defaults ranking longTap to false so touch drag starts without a long-press', () => {
    expect(Serializer.findProperty('ranking', 'longTap').defaultValue).toBe(false);
  });
});
