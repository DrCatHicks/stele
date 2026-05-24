/**
 * Headless round-trip oracle for the publish gate (M4.2, design doc §3.6 / FR-2).
 *
 * The publish gate's third stage runs synthetic respondents through the SAME
 * survey-core engine the browser uses, across the survey's branches, and fails
 * publication if the routing is broken. Keeping the engine identical to the
 * runtime is the whole point — re-implementing visibleIf semantics in Python (or
 * SQL) would drift from what respondents actually experience. This file is the
 * single module that touches survey-core for the gate; the Python adapter
 * (api/survey_engine/round_trip.py) shells out to it and reads the JSON verdict.
 *
 * What it checks, conservatively (a publish gate must never false-reject a valid
 * survey):
 *   - survey-core can load the definition at all;
 *   - setting synthetic answers across every enumerable branch never throws an
 *     expression error;
 *   - no question is *unreachable* — i.e. gated by a visibleIf that no synthetic
 *     answer can satisfy. This catches the common LLM-authoring bug (a visibleIf
 *     pointing at a value the driver can never take). Only flagged when every
 *     driver is a choice question (so the branch space is fully enumerable) and
 *     the space is within bound; text/number-driven or oversized surveys are
 *     never failed for unreachability, only load/expression errors.
 *
 * Scope (complemented by the M4.1 Python lint, not duplicated here): this walks
 * question-level `visibleIf` only. Page/panel-level visibleIf and `enableIf` are
 * not enumerated — a question inside a hidden panel can still read as visible, so
 * the gate may false-PASS such a case (it never false-rejects). Dangling
 * visibleIf/enableIf references are already caught by the publish lint stage.
 *
 * Usage (CLI): definition JSON on stdin → verdict JSON on stdout.
 *   echo '<definition>' | node frontend/scripts/roundTrip.mjs
 * Verdict: { ok, bounded, checkedBranches, reachable[], unreachable[], errors[] }
 */
import { readFileSync, writeSync } from 'node:fs';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

import pkg from 'survey-core';

const { Model } = pkg;

// `{token}` references inside a SurveyJS expression; mirrors the API-side lint.
const BRACE_REF = /\{([^{}]+)\}/g;
// Context variables that are not question names (dynamic panel/matrix rows, self).
const CONTEXT_VARS = new Set(['row', 'panel', 'composite', 'self', 'parent']);
// Question types whose branch space is not scalar-enumerable. A checkbox answer
// is a subset (power set, exponential); a ranking answer is an ordered
// permutation; a matrix answer is a nested object ({row: col} / {row: {col:
// val}}, M5.3); a paneldynamic answer is an array of per-occurrence objects
// ([{element: val}, …], M5.4). Setting a single scalar where survey-core expects
// an array or object would mis-evaluate a `{q} contains x` / `{q.row} = x` /
// `{panel[0].element} = x` driver, so these are never enumerated as drivers — a
// question gated by one is never flagged unreachable (only load/expression errors
// are caught). A nested reference like `{sat.product}` or `{household[0].name}`
// resolves to its base (`sat` / `household`), which lands here. Never false-reject.
const NON_ENUMERABLE_DRIVER_TYPES = new Set([
  'checkbox',
  'ranking',
  'matrix',
  'matrixdropdown',
  'paneldynamic',
]);
// Cap on the enumerated branch space. Past this we still load + render once, but
// skip unreachability analysis rather than risk a slow run or a false reject.
const MAX_BRANCHES = 512;

/** Base question names referenced inside a SurveyJS expression string. */
function expressionRefs(expr) {
  if (typeof expr !== 'string') return [];
  const refs = [];
  // matchAll (not exec in a loop) so the global regex's lastIndex is never
  // shared across calls — a missed {ref} would under-enumerate drivers and let
  // an unreachable branch falsely pass.
  for (const match of expr.matchAll(BRACE_REF)) {
    const base = match[1].trim().split(/[.[]/, 1)[0].trim();
    if (base && !CONTEXT_VARS.has(base)) refs.push(base);
  }
  return refs;
}

/** Cartesian product of a list of candidate-value lists. */
function cartesian(lists) {
  return lists.reduce(
    (acc, list) => acc.flatMap((combo) => list.map((item) => [...combo, item])),
    [[]],
  );
}

export function analyzeSurvey(definition) {
  let model;
  try {
    model = new Model(definition);
  } catch (err) {
    return {
      ok: false,
      bounded: true,
      checkedBranches: 0,
      reachable: [],
      unreachable: [],
      errors: ['survey-core could not load the definition: ' + (err?.message ?? String(err))],
    };
  }

  const questions = model.getAllQuestions();
  const byName = new Map(questions.map((q) => [q.name, q]));
  const choiceValues = (q) => (q && Array.isArray(q.choices) ? q.choices.map((c) => c.value) : []);

  // Questions hidden behind a visibleIf, and the drivers they reference.
  const gated = questions.filter((q) => typeof q.visibleIf === 'string' && q.visibleIf.trim());
  const driverNames = new Set();
  for (const q of gated) for (const ref of expressionRefs(q.visibleIf)) driverNames.add(ref);

  // Only single-select choice drivers are enumerable; candidates are each option
  // value plus "unanswered" (undefined). Array-valued drivers (checkbox M5.1,
  // ranking M5.2) are deliberately excluded — see NON_ENUMERABLE_DRIVER_TYPES.
  // A question gated by one is therefore never flagged unreachable; only
  // load/expression errors are caught for it (never-false-reject contract).
  const enumerable = [...driverNames].filter(
    (name) =>
      byName.has(name) &&
      !NON_ENUMERABLE_DRIVER_TYPES.has(byName.get(name).getType()) &&
      choiceValues(byName.get(name)).length > 0,
  );
  const candidates = enumerable.map((name) => [undefined, ...choiceValues(byName.get(name))]);

  const space = candidates.reduce((acc, list) => acc * list.length, 1);
  const bounded = space <= MAX_BRANCHES;
  // Over-cap: a single default render still catches load/expression errors.
  const combos = bounded ? cartesian(candidates) : [[]];

  const reachable = new Set();
  const errors = [];
  let checkedBranches = 0;

  for (const combo of combos) {
    const data = {};
    combo.forEach((value, i) => {
      if (value !== undefined) data[enumerable[i]] = value;
    });
    try {
      model.data = data;
    } catch (err) {
      errors.push(
        'expression error for ' + JSON.stringify(data) + ': ' + (err?.message ?? String(err)),
      );
      continue;
    }
    checkedBranches += 1;
    for (const q of model.getAllQuestions()) if (q.isVisible) reachable.add(q.name);
  }

  // Flag a gated question unreachable only when every driver was enumerable (so
  // its absence from `reachable` is real, not an artifact of un-enumerable text
  // input) and the branch space was within bound. Never false-reject.
  const unreachable = [];
  if (bounded) {
    for (const q of gated) {
      const refs = expressionRefs(q.visibleIf);
      const allEnumerable = refs.length > 0 && refs.every((r) => enumerable.includes(r));
      if (allEnumerable && !reachable.has(q.name)) unreachable.push(q.name);
    }
  }
  if (unreachable.length > 0) {
    errors.push(
      'unreachable question(s) — no synthetic answer makes them visible: ' + unreachable.join(', '),
    );
  }

  return {
    ok: errors.length === 0,
    bounded,
    checkedBranches,
    reachable: [...reachable],
    unreachable,
    errors,
  };
}

// CLI entry: read a definition JSON from stdin, write the verdict to stdout.
// Compare via pathToFileURL so paths with spaces/encoding match correctly.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let input;
  try {
    input = readFileSync(0, 'utf8');
  } catch {
    writeSync(2, 'round-trip oracle: could not read definition from stdin\n');
    process.exit(2);
  }
  let verdict;
  try {
    verdict = analyzeSurvey(JSON.parse(input));
  } catch (err) {
    verdict = {
      ok: false,
      bounded: true,
      checkedBranches: 0,
      reachable: [],
      unreachable: [],
      errors: ['definition is not valid JSON: ' + (err?.message ?? String(err))],
    };
  }
  writeSync(1, JSON.stringify(verdict));
}
