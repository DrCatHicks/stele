# Roadmap & backlog

Status as of 2026-05-27. M0–M7 are merged to `main` (full planned roadmap delivered:
guardrails → vertical slice → PII/withdrawal → admin/access-control → publish gate →
question-type breadth → ETL maturity → deployment). This document tracks the **active
milestone** and the **queued backlog** that future milestones draw from.

For the authoritative architecture, see `survey-engine-design-doc.md` (esp. §5 deferred
decisions). For per-story progress detail, see the delivery-plan memory.

---

## Active milestone

### M8 — Operator & respondent UI

First UI-centric milestone. The operator UI shipped in M3.3 is intentionally bare (no CSS,
raw-JSON `<textarea>` editor, survey-core rendered with no theme imported). Scope: all four UI
areas, sequenced **demo-first** (the M7 client-demo is the driver).

**Decisions:**
- Respondent runner + preview lean on **SurveyJS's own theming** — importing
  `survey-core/defaultV2.min.css` applies its default theme (the `#19b394` brand the chrome
  mirrors); no `applyTheme` call needed.
- Operator chrome = **Tailwind**, with the `@theme` palette aligned to SurveyJS `--sjs-*` CSS
  variables (one source of truth; avoids two-palette drift).
- Survey-editor code editor = **CodeMirror 6** (locked; chosen over Monaco for bundle size).
- The SurveyJS **Creator** (visual drag-drop builder) stays deferred (§5). M8 improves UX *around*
  the LLM-assisted JSON workflow, it does not replace it.

**Stories** (branch + PR each; demo-facing first):

| Story | Scope |
|---|---|
| M8.1 | Styling foundation + app shell — Tailwind setup, shared primitives, restyle AdminLayout + Login. No behavior change. |
| M8.2 | Respondent runner polish — SurveyJS theme + entry/completion/invalid/error/loading screens. |
| M8.3 | Survey list / dashboard — status badges, version history, response counts (needs a count endpoint), create-from-starter. |
| M8.4 | Review consoles polish — PII queue + GDPR console restyle, clearer pending/decided states. RBAC unchanged. |
| M8.5 | Authoring editor — CodeMirror 6 (highlight/fold/error gutter); publish-gate/lint `detail` → inline field errors; `patterns/*.json` as in-UI starter templates. |
| M8.6 | Interactive preview (client-side only — walk branches, complete a test response, **nothing submitted**, invariant 1 preserved) + M8 verification runbook. |

---

## Queued backlog (future milestones draw from here)

Coarse by design — distant work stays at this level until it's the next 1–3 milestones, then it's
decomposed into PR-sized stories.

### A. Deferred decisions (design doc §5 — trigger-gated)

| ID | Item | State / trigger |
|---|---|---|
| A1 | **Cross-version equivalence / pooling** (FR-9) — `parent_question_id`/`canonical_question_id` + rationale | Most shovel-ready. Invariant 5, the lint, and M6.1's `parent_question_integrity` test are all *vacuous guards waiting to go live* the moment the columns are populated. The "natural deferral." |
| A2 | **Org/delivery dimension** | Org as an attribute on `dim_respondent`; add org/delivery field to `raw_responses` + API capture + dim. Design settled (Model A). Trigger: a survey delivered to >1 org. |
| A3 | **DuckDB migration target** | SQL kept portable (`adapter.dispatch`, md5 keys); no target ever wired. Trigger: query latency / `.parquet` distribution / collaborator workflow. |
| A4 | **Incremental dbt materializations** | Trigger: full rebuild > a few minutes (full-refresh today). |
| A5 | **Automated PII first-pass on free-text** | Trigger: free-text volume exceeds reviewer capacity. |
| A6 | **Full routing-trace dimension** | Beyond `was_shown` + raw `shown_questions`. Backfillable without re-collection. |
| A7 | **SurveyJS Creator (visual designer) license** | Trigger: non-technical authoring patterns the JSON workflow can't serve. |

*(Scheduled ETL — formerly §5 — was delivered in M7.5.)*

### B. Question-type breadth gaps (left explicit from M5)

- **B1** — `matrixdropdown` free-text / scalar / checkbox cells (only option-based cells supported; others rejected at publish).
- **B2** — Panel/matrix numeric/date cells (a panel text cell is forced to `value_text` regardless of `inputType`).
- **B3** — `rating` with text-valued `rateValues` (numeric-only today; text rejected at publish).
- **B4** — Nested panels (rejected at publish).

### C. Known correctness follow-ups (documented tech debt)

- **C1** — **Fact-grain multi-submission**: `fact_response`/`_item` surrogate keys omit `raw_response_id`, so one respondent submitting twice to a version breaks the unique tests; also touches the PII promotion join. Foundational — worth its own milestone before any edit/resubmit feature.
- **C2** — `value_text_redacted` set on **unanswered** high-risk rows: conflates "text withheld" vs "no answer existed"; gate on `answered`. Mild tension with the anti-collapse ethos.
- **C3** — **Withdrawal concurrency race**: `SELECT`-then-`INSERT` → 500 instead of idempotent 200 under concurrent requests. Harden with try/except IntegrityError → re-select.

### D. Production hardening (demo→prod checklist, from M7.4/M7.6 runbooks)

- **D1** — Encrypted **remote tofu state** (local gitignored backend today; stub is commented).
- **D2** — **EU data residency** for real PII (`region` var exists; "the one thing to revisit before real PII").
- **D3** — **Separate migrate service** — admin creds currently ride the web env (web-RCE → DB-owner escalation); narrow at RDS/Cloud SQL or real PII.
- **D4** — **AWS / GCP Cloud SQL** OpenTofu modules (additive behind the shared `variables.tf`).
- **D5** — Railway **volume backups + restore drill**.
- **D6** — **Retention-vs-erasure** policy (tie to the M2.2 tombstone).
- **D7** — Shared **prebuilt image in registry** (eliminate the cron build-twice).
- **D8** — Operator-supplied secrets **never born in state** (override vars exist; flip the posture for real prod).

### E. Process / docs loose ends

- **E1** — Several **design-doc edits drafted in chat but never synced** (docs are read-only by policy): the M3 renumber (§1.3 / §7.3 / §3.10 / §6) and §3.3 wording. Need an explicit apply.
- **E2** — Open **PR #35** (Railway deploy/operate docs) awaiting merge.

---

## Likely M9+ sequencing (indicative, not committed)

- **Cross-version equivalence (A1)** — highest readiness; the guards already exist.
- **Multi-submission grain fix (C1)** — foundational debt; gates edit/resubmit features.
- **Real-prod hardening (D-group)** — when a client engagement firms up.
