import { describe, expect, it } from 'vitest';

import { analyzeSurvey } from './roundTrip.mjs';

const radio = (name, choices, extra = {}) => ({ type: 'radiogroup', name, choices, ...extra });
const checkbox = (name, choices, extra = {}) => ({ type: 'checkbox', name, choices, ...extra });
const ranking = (name, choices, extra = {}) => ({ type: 'ranking', name, choices, ...extra });
const survey = (...elements) => ({ pages: [{ name: 'p1', elements }] });

describe('analyzeSurvey', () => {
  it('passes a valid branching survey and marks the gated question reachable', () => {
    const def = survey(
      radio('q1', ['a', 'b']),
      radio('q2', ['x', 'y'], { visibleIf: "{q1} = 'a'" }),
    );
    const verdict = analyzeSurvey(def);
    expect(verdict.ok).toBe(true);
    expect(verdict.reachable).toContain('q2');
    expect(verdict.unreachable).toEqual([]);
  });

  it('fails when a branch references a value the driver can never take', () => {
    // q1 only offers a/b, so q2's visibleIf {q1} = 'z' can never be satisfied.
    const def = survey(
      radio('q1', ['a', 'b']),
      radio('q2', ['x', 'y'], { visibleIf: "{q1} = 'z'" }),
    );
    const verdict = analyzeSurvey(def);
    expect(verdict.ok).toBe(false);
    expect(verdict.unreachable).toContain('q2');
    expect(verdict.errors.join(' ')).toMatch(/unreachable/);
  });

  it('passes a survey with no branching (single default render)', () => {
    const verdict = analyzeSurvey(survey(radio('q1', ['a', 'b'])));
    expect(verdict.ok).toBe(true);
    expect(verdict.checkedBranches).toBe(1);
  });

  it('does not flag unreachable when the driver is a non-enumerable text question', () => {
    // We can't enumerate free-text values, so reachability is never asserted —
    // the gate must not false-reject a text-driven branch.
    const def = {
      pages: [
        {
          name: 'p1',
          elements: [
            { type: 'text', name: 'name' },
            radio('q2', ['x', 'y'], { visibleIf: '{name} notempty' }),
          ],
        },
      ],
    };
    const verdict = analyzeSurvey(def);
    expect(verdict.ok).toBe(true);
    expect(verdict.unreachable).toEqual([]);
  });

  it('loads a multi-select (checkbox) survey without errors', () => {
    const verdict = analyzeSurvey(survey(checkbox('langs', ['py', 'sql', 'ts'])));
    expect(verdict.ok).toBe(true);
  });

  it('does not flag unreachable when the driver is a multi-select question', () => {
    // A checkbox's branch space is the option power set (exponential) and its
    // answer is an array, so the oracle does not enumerate it — a checkbox-gated
    // question must not be false-rejected even when its visibleIf is unsatisfiable
    // under scalar enumeration. Only load/expression errors are caught here.
    const def = survey(
      checkbox('langs', ['py', 'sql']),
      radio('followup', ['x', 'y'], { visibleIf: "{langs} contains 'rust'" }),
    );
    const verdict = analyzeSurvey(def);
    expect(verdict.ok).toBe(true);
    expect(verdict.unreachable).toEqual([]);
  });

  it('loads a ranked (ranking) survey without errors', () => {
    const verdict = analyzeSurvey(survey(ranking('priorities', ['speed', 'cost', 'quality'])));
    expect(verdict.ok).toBe(true);
  });

  it('does not flag unreachable when the driver is a ranking question', () => {
    // A ranking answer is an ordered permutation array, not a scalar; like
    // checkbox it isn't enumerated as a driver, so a ranking-gated question is
    // never false-rejected even under an unsatisfiable-looking visibleIf.
    const def = survey(
      ranking('priorities', ['speed', 'cost']),
      radio('followup', ['x', 'y'], { visibleIf: "{priorities} contains 'rust'" }),
    );
    const verdict = analyzeSurvey(def);
    expect(verdict.ok).toBe(true);
    expect(verdict.unreachable).toEqual([]);
  });

  const matrix = (name, rows, columns, extra = {}) => ({ type: 'matrix', name, rows, columns, ...extra });

  it('loads a matrix survey without errors', () => {
    const def = survey(matrix('sat', ['price', 'quality'], ['low', 'high']));
    const verdict = analyzeSurvey(def);
    expect(verdict.ok).toBe(true);
  });

  it('loads a matrixdropdown survey without errors', () => {
    const def = survey({
      type: 'matrixdropdown',
      name: 'devices',
      rows: ['laptop', 'phone'],
      columns: [{ name: 'brand', cellType: 'dropdown', choices: ['apple', 'dell'] }],
    });
    const verdict = analyzeSurvey(def);
    expect(verdict.ok).toBe(true);
  });

  it('does not flag unreachable when the driver is a matrix question', () => {
    // A matrix answer is a nested object ({row: col}), not a scalar; a sub-question
    // reference like {sat.price} resolves to base `sat` (the matrix), which is not
    // enumerated — so a matrix-gated question is never false-rejected.
    const def = survey(
      matrix('sat', ['price'], ['low', 'high']),
      radio('followup', ['x', 'y'], { visibleIf: "{sat.price} = 'nope'" }),
    );
    const verdict = analyzeSurvey(def);
    expect(verdict.ok).toBe(true);
    expect(verdict.unreachable).toEqual([]);
  });

  it('returns a structured verdict on degenerate input without throwing', () => {
    // survey-core tolerates a null/empty definition (yields an empty model);
    // the M4.1 schema gate is what rejects empty surveys. The oracle must still
    // return a well-formed verdict rather than crash the publish call.
    const verdict = analyzeSurvey(null);
    expect(typeof verdict.ok).toBe('boolean');
    expect(Array.isArray(verdict.errors)).toBe(true);
  });
});
