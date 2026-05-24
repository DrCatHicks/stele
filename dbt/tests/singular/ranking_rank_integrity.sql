-- rank integrity (M5.2). fact_response_item.rank is the ordered position of a
-- ranking selection (1 = top-ranked), populated ONLY for ranking questions; every
-- other type leaves it null (a checkbox selection has an ordinal too, but it's an
-- arbitrary fan-out tiebreaker, not a rank). This test pins three properties:
--
--   1. rank is non-null only on a ranking row — a stray rank on a single-select,
--      checkbox, or free-text row means the populating CASE leaked.
--   2. every *resolved* ranking selection HAS a rank — a ranking row that resolved
--      to an option but carries no rank means the populating CASE stopped firing
--      (e.g. a regression that drops rank to null). Without this check the other
--      two pass vacuously when rank is never populated, since both gate on
--      `rank is not null` (caught in review). An unresolved selection (option_key
--      null) and an unanswered routing row are excluded — the former still has an
--      ordinal but no option, the latter has neither.
--   3. within one (respondent, survey_version, question) the ranks form a
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
        fri.option_key,
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

resolved_ranking_missing_rank as (
    select fact_id as offending_key
    from fact
    where response_type = 'ranking'
        and option_key is not null
        and rank is null
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
select offending_key from resolved_ranking_missing_rank
union all
select offending_key from ranking_groups
