# M3 — Administration & access control: manual test plan

End-to-end manual acceptance for M3.1–M3.5 (auth foundation → RBAC → admin UI →
GDPR/PII console → DB-credential provisioning). Complements the automated suites
(pytest/vitest/dbt) and the per-slice runbooks (`m3.4-gdpr-pii-console.md`,
`m3.5-db-credentials.md`) with a human-driven pass across roles.

Legend: 🖥️ = browser, ⌨️ = terminal. Run in the dev container. Check each `[ ]`
as you go.

## Part 0 — One-time setup ⌨️

```bash
# 0.1 DB up + migrated
docker compose -f .devcontainer/docker-compose.yml up -d
cd /workspace && uv run alembic upgrade head

# 0.2 REQUIRED for login over plain http (cookie defaults to Secure=true).
#     Export in the SAME shell that starts uvicorn.
export STELE_COOKIE_SECURE=false

# 0.3 Start the API (terminal 1)
uv run uvicorn api.main:app --reload --port 8000

# 0.4 Start the frontend (terminal 2) — http://localhost:5173
cd frontend && npm run dev

# 0.5 Create one operator of each role (terminal 3)
uv run python -c "
import asyncio
from api.auth.service import create_user
from api.db import SessionLocal
async def main():
    async with SessionLocal() as s:
        for role in ['admin','researcher','reviewer']:
            await create_user(s, f'{role}@test.local', 'password123', [role])
            print('created', role)
asyncio.run(main())
"

# 0.6 Seed a survey + responses (incl. high-risk free-text + one promotion)
uv run python scripts/seed_example_survey.py
```

- [ ] 0.a API responds: `curl -s localhost:8000/health` → `{"status":"ok"}`
- [ ] 0.b Frontend loads at http://localhost:5173/admin (redirects to login)
- [ ] 0.c Three users exist (roles live in app.user_roles since M9.1): `psql -d stele -c "select u.email, array_agg(ur.role order by ur.role) from app.users u join app.user_roles ur on ur.user_id = u.id group by u.email order by u.email;"`

> Note: the seed assigns each respondent a random UUID. Grab one for the GDPR
> test: `psql -d stele -c "select distinct respondent_id from app.raw_responses where payload is not null limit 1;"`

---

## Part A — M3.1 Auth foundation

### A1 — Login / me / logout happy path ⌨️
```bash
curl -i -c jar.txt -X POST localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@test.local","password":"password123"}'
curl -s -b jar.txt localhost:8000/auth/me
curl -i -b jar.txt -X POST localhost:8000/auth/logout
```
- [ ] A1.a login → 200, body has `roles:["admin"]`, `Set-Cookie: stele_session=…`
- [ ] A1.b /auth/me → 200 with same user
- [ ] A1.c logout → 204; a follow-up /auth/me with the jar → 401

### A2 — Uniform failure (no email enumeration) ⌨️
```bash
curl -s -X POST localhost:8000/auth/login -H 'Content-Type: application/json' -d '{"email":"admin@test.local","password":"wrong"}'
curl -s -X POST localhost:8000/auth/login -H 'Content-Type: application/json' -d '{"email":"ghost@test.local","password":"x"}'
```
- [ ] A2.a Both → 401 with the **identical** detail `"invalid email or password"`

### A3 — Logout is idempotent ⌨️
- [ ] A3.a `curl -i -X POST localhost:8000/auth/logout` with no cookie → 204
- [ ] A3.b Calling logout twice with a jar → 204 both times

### A4 — Case-insensitive email ⌨️
- [ ] A4.a Login with `ADMIN@TEST.LOCAL` / `password123` → 200

### A5 — Session expiry (server-side) ⌨️
```bash
# Log in (jar.txt), then expire the row:
psql -d stele -c "update app.sessions set expires_at = now() - interval '1s' where user_id=(select id from app.users where email='admin@test.local');"
curl -s -b jar.txt localhost:8000/auth/me
```
- [ ] A5.a /auth/me → 401 after expiry
- [ ] A5.b Expired row was reaped: `psql -d stele -c "select count(*) from app.sessions;"` (the expired one is gone on next resolve)

### A6 — Disabled-user mid-session revocation ⌨️
```bash
# Fresh login (jar2.txt), confirm 200, then disable:
psql -d stele -c "update app.users set disabled=true where email='researcher@test.local';"
curl -s -b jar2.txt localhost:8000/auth/me
psql -d stele -c "update app.users set disabled=false where email='researcher@test.local';"  # restore
```
- [ ] A6.a /auth/me with a live cookie → 401 immediately after disable
- [ ] A6.b Re-enable, re-login → 200

---

## Part B — M3.2 RBAC endpoint gating

Use cookie jars per role (login each via A1's curl). Across gated routes:
**no session → 401**, **wrong role → 403**, **right role → the handler's real status.**

| # | Method · path | admin | researcher | reviewer | anon |
|---|---|---|---|---|---|
| B1 | GET `/surveys` | 200 | 200 | 403 | 401 |
| B2 | POST `/surveys` | 201 | 201 | 403 | 401 |
| B3 | POST `/surveys/{id}/versions/1/publish` | 404* | 404* | 403 | 401 |
| B4 | POST `/respondents/{uuid}/withdrawal` | 200 | 403 | 403 | 401 |
| B5 | GET `/admin/withdrawals` | 200 | 403 | 403 | 401 |
| B6 | GET `/admin/pii/free-text` | 403 | 403 | 200 | 401 |
| B7 | POST `/admin/pii/free-text/1/promote` | 403 | 403 | 404† | 401 |
| B8 | GET `/admin/db-credentials` | 200 | 403 | 403 | 401 |

\* 404 because the survey id is fake — proves the gate cleared. † 404 = gate cleared, id missing.

- [ ] B1–B8: spot-check at least the **admin can't screen PII** (B6/B7 = 403) and **reviewer can't reach GDPR** (B4/B5 = 403) crossovers — the M3.4 separation.

### Public exemptions (must NOT require auth) ⌨️
- [ ] B9 GET `/surveys/{real_id}/versions/1` with no cookie → 200 (respondent fetch)
- [ ] B10 POST `/surveys/{fake}/versions/1/responses` no cookie → 404 (ran without auth, not 401)

---

## Part C — M3.3 Admin UI 🖥️

### C1 — Route guard + login
- [ ] C1.a Visit `/admin` logged out → brief "Loading…", then redirect to `/admin/login`
- [ ] C1.b Bad creds → inline "Invalid email or password.", stay on login
- [ ] C1.c Login as **researcher** → lands on survey list; header shows `researcher@test.local (researcher)` and a **Surveys** nav link only (no GDPR / PII Review)

### C2 — Create → edit → publish
- [ ] C2.a Click "New survey" → navigates into the editor on a draft (starter JSON)
- [ ] C2.b Paste a valid definition (below), click Save → "Saved."
- [ ] C2.c Click Preview → SurveyJS renders; completing it shows the built-in thank-you page and **submits nothing** (no response row created)
- [ ] C2.d Click Publish → "Published (hash …)", status badge → published, textarea becomes **read-only**, Save/Publish disabled, immutability notice shown

Valid definition:
```json
{"pages":[{"name":"p1","elements":[{"type":"comment","name":"open_feedback"}]}]}
```

### C3 — Publish gate surfaces 422
Paste an invalid definition and Publish:
```json
{"pages":[{"name":"p1","elements":[{"type":"text","name":"c1","pii_risk":"low"}]}]}
```
- [ ] C3.a Publish → inline error quoting the API detail (low-risk needs a `pii_risk_rationale`); status stays draft
- [ ] C3.b Add `"pii_risk_rationale":"screened non-identifying"`, Publish → succeeds

### C4 — Mid-session 401 + logout
- [ ] C4.a While logged in, delete the session (`psql -d stele -c "delete from app.sessions;"`), trigger any UI action → bounced to `/admin/login`; after re-login you return to where you were
- [ ] C4.b Click "Log out" → returns to login; back button doesn't restore the session

---

## Part D — M3.4 GDPR / PII console

### D1 — Admin GDPR console 🖥️
Login as **admin** → click **GDPR** (`/admin/gdpr`).
- [ ] D1.a Paste a real respondent UUID (from Part 0 note), optional reason, click "Erase respondent" → a confirm dialog appears (irreversible warning)
- [ ] D1.b Confirm → result line shows counts (raw rows tombstoned / responses purged / PII rows deleted); the audit table gains a row
- [ ] D1.c Erase the **same** UUID again → "Already withdrawn — no further data to erase."; audit keeps the original timestamp
- [ ] D1.d As researcher, navigate to `/admin/gdpr` directly → "Only admins can access the GDPR console." (and the nav link is absent)

### D2 — Reviewer PII screening 🖥️
Login as **reviewer** → you're auto-redirected to `/admin/pii-review`.
- [ ] D2.a Pending tab lists high-risk free-text answers showing respondent, question, and the **answer text** (reviewer is PII-cleared)
- [ ] D2.b Click "Promote" on one → row leaves the pending tab; note says it reaches marts on the next ETL build
- [ ] D2.c Click "Reject" on another → leaves pending
- [ ] D2.d "promoted" / "rejected" tabs show the decided rows (no decision buttons)
- [ ] D2.e As admin (or researcher), navigate to `/admin/pii-review` → "Only reviewers can screen free-text PII."

### D3 — Promotion reaches the marts ⌨️ (the round-trip)
```bash
cd /workspace/dbt && DBT_USER=stele_etl DBT_PASSWORD=dev DBT_DBNAME=stele dbt build 2>&1 | tail -5
PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -c "
  select dq.stable_name, fri.value_text, fri.value_text_redacted
  from marts.fact_response_item fri
  join marts.dim_question dq on dq.question_id=fri.question_id
  where dq.stable_name='ft_high' order by fri.value_text nulls last;"
```
- [ ] D3.a `dbt build` PASS (incl. `promoted_free_text_in_marts` + `free_text_redaction_parity`)
- [ ] D3.b A promoted `ft_high` row shows `value_text` populated, `value_text_redacted=f`; unpromoted high-risk rows stay null/`t`
- [ ] D3.c Trust boundary: `PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -c "select 1 from pii.free_text_responses;"` → **permission denied** (ETL can read the decision table but never the PII text)

---

## Part E — M3.5 Analyst & reviewer DB-credential provisioning ⌨️

No UI beyond the read-only API. Use the dev fallback for the elevated connection.

### E1 — Provision + NOINHERIT access model
```bash
export STELE_ALLOW_DEV_FALLBACK=1   # uses the stele_dev superuser conn in dev
uv run python scripts/provision_db_credential.py provision --access analyst  --subject analyst1@test.local
uv run python scripts/provision_db_credential.py provision --access reviewer --subject reviewer1@test.local
# Each prints login role + a one-time password on the terminal (/dev/tty). Record them.
```
- [ ] E1.a Two roles created; passwords shown once, never on stdout/log
- [ ] E1.b Analyst, **before** SET ROLE — denied: `psql "postgresql://<analyst_role>:<pw>@localhost:5432/stele" -c "select count(*) from marts.dim_question;"` → permission denied (NOINHERIT)
- [ ] E1.c Analyst, **after** SET ROLE — allowed: `… -c "set role stele_analyst; select count(*) from marts.dim_question;"` → count
- [ ] E1.d Analyst cannot cross schemas: `… -c "set role stele_analyst; select 1 from pii.free_text_responses;"` → permission denied
- [ ] E1.e Reviewer credential: after `set role stele_pii_reviewer` can read `pii.free_text_responses`; cannot read `marts.*`

### E2 — Registry API (admin-only) ⌨️
`/admin/db-credentials` is **not** proxied to the SPA — hit the API directly.
- [ ] E2.a `curl -s -b admin_jar.txt localhost:8000/admin/db-credentials` → 200, lists both grants (`status:"active"`), **no password field**
- [ ] E2.b Same call with a researcher jar → 403

### E3 — Rotate + revoke ⌨️
```bash
uv run python scripts/provision_db_credential.py rotate <analyst_role>   # new password on /dev/tty
uv run python scripts/provision_db_credential.py revoke <reviewer_role>
uv run python scripts/provision_db_credential.py list
```
- [ ] E3.a After rotate, the **old** password fails to connect; the new one works (after SET ROLE)
- [ ] E3.b After revoke, the role can't connect; `list` (and the API) show `status:"revoked"` with `revoked_at` set

---

## Part F — Cleanup (optional) ⌨️
- [ ] F.a Revoke any leftover provisioned roles (E3 revoke)
- [ ] F.b To reset all data, from the host: `docker compose -f .devcontainer/docker-compose.yml down -v` then re-run Part 0
