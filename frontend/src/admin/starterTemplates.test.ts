import { describe, expect, it } from 'vitest';

import { STARTER_TEMPLATES } from './starterTemplates';

// The starters are offered to authors as known-good beginnings, so guard their
// shape: each must round-trip through JSON and carry at least one page. (The
// branching starter additionally exercises a visibleIf reference.)
describe('STARTER_TEMPLATES', () => {
  it('exposes unique ids and labels', () => {
    const ids = STARTER_TEMPLATES.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(STARTER_TEMPLATES.every((t) => t.label.length > 0)).toBe(true);
  });

  it.each(STARTER_TEMPLATES)('$id is valid JSON with at least one page', (template) => {
    const round = JSON.parse(JSON.stringify(template.definition)) as {
      pages?: { elements?: unknown[] }[];
    };
    expect(Array.isArray(round.pages)).toBe(true);
    expect(round.pages?.length).toBeGreaterThanOrEqual(1);
  });

  it('branching starter references its driver via visibleIf', () => {
    const branching = STARTER_TEMPLATES.find((t) => t.id === 'branching');
    expect(JSON.stringify(branching?.definition)).toContain('visibleIf');
  });
});
