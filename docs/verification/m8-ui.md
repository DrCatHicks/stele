# M8 — Operator & respondent UI: verification

M8 made the operator chrome and the respondent runner presentable (Tailwind +
SurveyJS theming) and turned the authoring + review surfaces into usable tools.
This runbook maps each story's acceptance criteria to a reproducible check.

All frontend checks run from `frontend/`:

```bash
pnpm install
pnpm typecheck && pnpm lint && pnpm format:check
pnpm test          # vitest
pnpm build         # tsc -b && vite build (proves Tailwind + CodeMirror compile)
```

Backend (the M8.3 count endpoint) from the repo root:

```bash
uv run pytest api/tests/test_surveys.py
uv run mypy api/ && uv run ruff check .
```

## Decisions (recap)

- **Respondent runner + preview** lean on SurveyJS's own theming (`defaultV2.min.css`,
  imported in `main.tsx`); its `#19b394` primary is the brand the operator chrome mirrors.
- **Operator chrome** uses **Tailwind v4** (`@tailwindcss/vite`), with `@theme` tokens in
  `src/index.css` aligned to that palette — one shared visual language, no second source of truth.
- **Authoring editor** uses **CodeMirror 6** (`@uiw/react-codemirror` + `@codemirror/lang-json`).
- The SurveyJS **Creator** (visual drag-drop builder) stays deferred (design doc §5).

## Per-story criteria

| Story | Criterion | Where it's verified |
|---|---|---|
| M8.1 | Tailwind compiles; shared primitives exist; AdminLayout + Login restyled with names preserved | `pnpm build` (CSS emitted); `src/ui/*` + `Button.test`/`Badge.test`; `AdminLayout.test`/`LoginView.test` unchanged and green |
| M8.2 | Runner is themed; entry/loading/completion/404/error screens | `SurveyRunner.test` (generic vs 404 screens), `RespondentEntry.test` (no-survey prompt); theme via `main.tsx` import |
| M8.3 | Per-version live response count; dashboard groups versions with status badges | `test_surveys.py::test_list_surveys_reports_live_response_counts`; `SurveyListView.test` (grouped layout + count) |
| M8.4 | PII queue + GDPR console restyled; RBAC + confirm + result text intact | `PiiReviewView.test`, `GdprView.test` (unchanged assertions still pass) |
| M8.5 | CodeMirror editor with inline JSON lint; starter templates; gate errors surfaced | `JsonEditor.test` (mounts), `starterTemplates.test`, `SurveyEditorView.test` (save/publish/parse via stub) |
| M8.6 | Preview is interactive (walk branches, complete) and **never submits**; shows shown-set + payload | `SurveyPreview.test` (no fetch on completion; captured shown-set/payload) |

## Load-bearing guarantees

- **Preview never writes a response.** `SurveyPreview` is client-side only — no
  `submitResponse`/API call — so no synthetic row reaches the append-only
  `app.raw_responses` (invariant 1). Pinned by `SurveyPreview.test`'s no-network assertion.
- **Response counts exclude withdrawn respondents.** The count joins `app.raw_responses`
  with `payload IS NOT NULL`, the live-row filter the tombstone workflow relies on.
- **Accessible names preserved.** The restyle kept every label/role/text the existing
  tests assert (login fields, nav links, console buttons, the erasure result string), so
  the behaviour suites carried over unchanged.

## Known follow-up

- The SPA is a single bundle, so CodeMirror's weight (~430 kB) loads on the public
  respondent path too. A route-level code split of `/admin/*` would trim it. Out of M8
  scope; noted here and in the M8.5 commit.
