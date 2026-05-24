# Survey patterns

Annotated, gate-valid SurveyJS definitions. Two audiences:

- **Researchers** authoring surveys — copy a pattern, adapt the questions, preview
  in the draft editor.
- **The LLM** assisting with JSON authoring — these files are context. Generating
  from a known-good pattern keeps the invention surface small, which is the whole
  point of having them (design doc §3.6, §4 "LLM-generated JSON with subtle logic
  errors").

Every file here is a **validated fixture**, not just documentation:
`api/tests/test_patterns.py` runs each one through the real publish gate
(`validate_definition` always; the survey-core round-trip oracle when Node is
available). If the gate tightens or a supported type changes, the patterns fail
the test until they're brought back in line — they can't quietly rot into
examples that would be rejected at publish time.

## What you can publish today

The gate accepts a deliberately narrow type surface. A name-bearing element of any
other type is rejected at publish (you can't ship a type the runtime, gate, and
dbt staging don't all handle — CLAUDE.md §"New question type = three places").

| Type | Kind | Answer lands in |
|---|---|---|
| `radiogroup` | single-select | `option_key` (via `dim_option`) |
| `dropdown` | single-select | `option_key` (via `dim_option`) |
| `text` | free-text (single line) | `value_text`, routed by `pii_risk` |
| `comment` | free-text (multi-line) | `value_text`, routed by `pii_risk` |

Multi-select, matrix, ranking, rating, boolean, numeric, and date types are **not
yet publishable** — they each arrive in M5 alongside their dbt staging and tests.
Until then they'd land as all-null fact rows, silently indistinguishable from
"shown & skipped".

## The patterns

| File | Demonstrates |
|---|---|
| [single_select.json](single_select.json) | `radiogroup` and `dropdown`; scalar choices vs. `{value, text}` object choices |
| [free_text_pii.json](free_text_pii.json) | `text`/`comment` and the `pii_risk` routing (default `high`; `low` needs a rationale) |
| [branching.json](branching.json) | `visibleIf` conditional routing, including complementary branches |
| [multi_page.json](multi_page.json) | multiple pages; a `visibleIf` referencing an answer from an earlier page |
| [calculated_values.json](calculated_values.json) | a `calculatedValue` as a reusable, named `visibleIf` condition |

Each file annotates itself inline: the survey- and question-level `title` and
`description` fields are real SurveyJS properties (they render in the runtime), so
the explanations travel with the definition and stay valid JSON.

## Things the gate enforces (so the patterns observe them)

- **Unique question names.** Names are the join key into the warehouse; a
  duplicate is rejected.
- **Non-empty choices, unique option values.** `dim_option` keys on the choice
  `value`, normalized the same way dbt extracts it (`true`/`false` for booleans,
  the raw string otherwise) — duplicates collide.
- **No dangling `visibleIf`/`enableIf`.** Every `{name}` referenced must be a
  defined question or a `calculatedValue`. This is a *reference* check; the
  round-trip oracle exercises the expression semantics.
- **PII defaults to `high`.** Omit `pii_risk` on free text and it's treated as
  identifying. Downgrading to `low` requires a non-empty `pii_risk_rationale` —
  the researcher's recorded judgment, never a heuristic (CLAUDE.md §"silent
  defaults").
- **Every gated question must be reachable.** The round-trip oracle walks the
  enumerable `visibleIf` branch space and rejects a question no synthetic answer
  can ever make visible (the classic LLM bug: `visibleIf` on a value the driver
  can't take). It is conservative — it only flags unreachability when every driver
  is a choice question and the branch space is within bound, so it never
  false-rejects.

### A note on `calculatedValues` and the round-trip oracle

The oracle enumerates **choice-question** drivers. When a `visibleIf` is gated on a
`calculatedValue` (as in `calculated_values.json`), the calc value isn't a choice
question, so the oracle can't enumerate that branch and conservatively leaves it
alone — it neither verifies nor rejects the gated question's reachability. The
M4.1 lint still confirms the reference resolves. If you gate visibility on a
`calculatedValue`, **self-test those branches in the draft preview**; the
automated round-trip won't cover them for you.
