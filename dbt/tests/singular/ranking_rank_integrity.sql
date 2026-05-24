-- rank integrity (M5.2). fact_response_item.rank is the ordered position of a
-- ranking selection (1 = top-ranked), populated ONLY for ranking questions; every
-- other type leaves it null (a checkbox selection has an ordinal too, but it's an
-- arbitrary fan-out tiebreaker, not a rank). This test pins two properties:
--
--   1. rank is non-null only on a ranking row — a stray rank on a single-select,
--      checkbox, or free-text row means the populating CASE leaked.
--   2. within one (respondent, survey_version, question) the ranks form a
--      contiguous 1..N with no gaps or ties — exactly what an ordered answer
--      array yields via WITH ORDINALITY. A duplicate or gap means the fan-out
--      ordinality drifted from the submitted order.
--
-- response_type (the per-version question type) comes from dim_question_version,
-- keeping this test in the marts layer (no answer-side JSON). Passes when it
-- returns zero rows.

with fact as (
    select
        fri.fact_id,
        fri.respondent_id,
        fri.survey_version_id,
        fri.question_id,
        fri.rank,
        qv.response_type
    from {{ ref('fact_response_item') }} as fri
    inner join {{ ref('dim_question_version') }} as qv
        on fri.question_version_id = qv.question_version_id
),

rank_on_non_ranking as (
    select fact_id as offending_key
    from fact
    where rank is not null
        and response_type != 'ranking'
),

ranking_groups as (
    select
        respondent_id::text || ':' || survey_version_id || ':' || question_id
            as offending_key
    from fact
    where rank is not null
    group by respondent_id, survey_version_id, question_id
    having count(*) != count(distinct rank)
        or min(rank) != 1
        or max(rank) != count(*)
)

select offending_key from rank_on_non_ranking
union all
select offending_key from ranking_groups
