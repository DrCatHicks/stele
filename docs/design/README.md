# Design document archive

The **live, maintained** design document lives at the repository root:
[`survey-engine-design-doc.md`](../../survey-engine-design-doc.md). CLAUDE.md and
tooling reference that path, so the current version always stays there.

This folder holds **frozen snapshots** of prior versions, for history. They are not
maintained — read them to see what the design said at a point in time, not as
guidance for the current system.

## Convention

- Root doc carries a `**Version:** N` header and a Changelog describing what each
  bump reconciled.
- When the root doc takes a new major version, copy the outgoing content here as
  `survey-engine-design-doc.v<N>.md` with an `ARCHIVED SNAPSHOT` banner at the top
  (version + freeze date + a pointer back to the live doc).

## Snapshots

| File | Frozen | Notes |
|---|---|---|
| [`archive/survey-engine-design-doc.v1.md`](archive/survey-engine-design-doc.v1.md) | 2026-05-25 | Original pre-build design + the in-flight syncs made during M0–M6, before the as-built reconciliation. |
