-- Parent-question integrity (design-doc §3.7, invariant 5). Cross-version
-- equivalence is opt-in and a researcher judgment — NEVER auto-populated — so
-- dim_question emits parent_question_id / parent_question_rationale as explicit
-- nulls today and this test is a vacuous guard. It becomes load-bearing the
-- moment equivalence is turned on. Passes when it returns zero rows.
--
-- A child row is offending when its parent_question_id is set but any of:
--   * parent_question_rationale is null (the two must co-occur), or
--   * the referenced parent question is absent from dim_question, or
--   * the parent's first_published_at is not strictly before the child's
--     (a parent must predate the version that supersedes it).
-- The reverse — a rationale with no parent_question_id — is also flagged: a
-- dangling rationale means the co-occurrence was written half-way.

with child as (
    select
        question_id,
        parent_question_id,
        parent_question_rationale,
        first_published_at
    from {{ ref('dim_question') }}
)

select
    c.question_id,
    c.parent_question_id,
    c.parent_question_rationale
from child as c
left join child as p
    on c.parent_question_id = p.question_id
where
    -- parent reference set, but the co-occurring rationale / ordering is wrong
    (
        c.parent_question_id is not null
        and (
            c.parent_question_rationale is null
            or p.question_id is null
            or p.first_published_at >= c.first_published_at
        )
    )
    -- dangling rationale with no parent reference
    or (
        c.parent_question_id is null
        and c.parent_question_rationale is not null
    )
