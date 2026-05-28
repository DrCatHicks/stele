# Roadmap & backlog

Status as of 2026-05-27. M0–M8 are merged to `main` (full planned roadmap delivered:
guardrails → vertical slice → PII/withdrawal → admin/access-control → publish gate →
question-type breadth → ETL maturity → deployment → operator/respondent UI). Interim
post-M8 enhancements also shipped (custom short codes + copy-link, admin-triggered ETL with
live feedback, reviewer field-level PII scrub). This document tracks the **active milestone**
and the **queued backlog** that future milestones draw from.

For the authoritative architecture, see `survey-engine-design-doc.md` (esp. §5 deferred
decisions). For per-story progress detail, see the delivery-plan memory.

---

## Active milestone

### M9 — User administration & multi-role access control

Adds an admin UI to manage operator accounts — create users, grant/revoke roles, enable/disable,
reset passwords — plus a read-only view of the analyst/reviewer DB-credential registry. Builds on
the M3 auth foundation (`app.users`/`app.sessions`, `require_role`, the lazy-loaded `/admin/*`
shell). Today the only way to create a user is the `scripts/bootstrap_admin.py` CLI; M9 closes that
gap. Extends M3.5's read-only `GET /admin/db-credentials` into the UI.

**Decisions** (user-confirmed):
- **Multi-role per user** (chosen over keeping one role). New `app.user_roles` join table replaces
  the single `app.users.role` column; `AuthenticatedUser.role` → `roles`, `require_role` checks set
  intersection, frontend `user.role` → `user.roles`. A user can hold e.g. researcher + reviewer.
- **Admin sets the initial password** at create time (no SMTP/invite infra exists); Argon2-hashed
  like `bootstrap_admin`. No self-service password change yet.
- Extra scope included: **reset password** (revokes that user's sessions), **re-enable** disabled
  users, **DB-credential grants** surfaced **read-only** (provisioning stays the out-of-band CLI).
  A standalone force-logout button was **not** taken — session revocation rides on disable + reset.
- **Safety guards** enforced server-side: last-admin protection (can't remove the `admin` role from,
  or disable, the only remaining enabled admin → 409); disable kills live sessions immediately
  (`resolve_session` already rejects disabled users); role changes take effect on the next request.

**Stories** (branch + PR each):

| Story | Scope |
|---|---|
| M9.1 | Multi-role auth refactor — migration (`app.user_roles` + backfill + drop `users.role`; grant `stele_api`, revoke `stele_etl`); `AuthenticatedUser.roles`, `require_role`, `create_user(roles)`, `UserOut.roles`, `bootstrap_admin`; frontend `User.roles`; update RBAC/auth tests. Self-contained + releasable. |
| M9.2 | Admin user-management API — `api/admin/users_router.py` (`/admin/users`, admin-gated): list, create (409 on dup), set-roles, disable/enable, reset-password. Last-admin guard; reset revokes sessions. `test_admin_users.py`. |
| M9.3 | Admin user-management UI — `UsersView` (table, role editor, create form, disable/enable/reset actions behind confirm) + read-only `DbCredentialsView`; `api.ts` clients; `/admin/users` + `/admin/db-credentials` routes; admin-only nav links. Colocated tests. |

> **Design-doc divergence:** §3.10 documents single-role-per-user. Multi-role diverges; a §3.10
> revision is drafted for review and folded into the E1 sync backlog (docs are read-only by policy).

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
- **D3** — **Separate migrate service** — admin creds currently ride the web env (web-RCE → DB-owner escalation); also the D7 migrate-on-start races at `num_replicas > 1` (alembic isn't concurrent). Narrow at RDS/Cloud SQL, real PII, or the first scale-out.
- **D4** — **AWS / GCP Cloud SQL** OpenTofu modules (additive behind the shared `variables.tf`).
- **D5** — Railway **volume backups + restore drill**.
- **D6** — **Retention-vs-erasure** policy (tie to the M2.2 tombstone).
- ~~**D7** — Shared **prebuilt image in registry** (eliminate the cron build-twice).~~ **Done** — CI builds/tests/pushes one GHCR image; `web` + `etl` both pull it (migrate-on-start + `STELE_ENTRYPOINT=etl` replace the deleted `railway.json` config). See `docs/verification/d7-prebuilt-image.md`. Leftover: web healthcheck + etl restart-policy are now Railway dashboard settings.
- **D8** — Operator-supplied secrets **never born in state** (override vars exist; flip the posture for real prod).
- **D9** — **Operator credential lifecycle** — no password-complexity validation on create/reset (`/admin/users`, M9.2) and no self-service password change (admin-reset only). Consistent with the no-policy login/bootstrap today; deferred deliberately — operators are a small internal set, not public clients. Revisit when the operator base grows or a client engagement demands a password policy.

### E. Process / docs loose ends

- **E1** — Several **design-doc edits drafted in chat but never synced** (docs are read-only by policy): the M3 renumber (§1.3 / §7.3 / §3.10 / §6), the §3.3 wording, and the **M9 §3.10 multi-role revision** (single-role → `app.user_roles` join table). Need an explicit apply.
- **E2** — Open **PR #35** (Railway deploy/operate docs) awaiting merge.

---

## Likely M10+ sequencing (indicative, not committed)

- **Cross-version equivalence (A1)** — highest readiness; the guards already exist.
- **Multi-submission grain fix (C1)** — foundational debt; gates edit/resubmit features.
- **Real-prod hardening (D-group)** — when a client engagement firms up.
