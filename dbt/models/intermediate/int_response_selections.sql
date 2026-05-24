-- Selection-grain expansion of int_response_answers: a multi-select (checkbox)
-- answer fans out to one row per chosen option; every other answered question and
-- every unanswered question stays a single row. This model owns the multi-select
-- fan-out AND all the Postgres-specific JSON handling for answers, so the marts
-- (fact_response_item) stay portable — the same staging+intermediate confinement
-- documented in stg_raw_responses (CLAUDE.md dbt portability; design-doc §5).
--
-- A LEFT JOIN LATERAL over the (possibly empty) answer array guarantees the
-- single row survives when the lateral yields nothing — a non-checkbox question,
-- an unanswered checkbox, or a checkbox answered with an empty array — so the
-- routing row (shown-skipped / routed-past) is never lost.
--
-- option_lookup_value is the value the marts join to dim_option: the per-selection
-- value for checkbox, the scalar answer otherwise. selection_ordinal is the
-- 1-based position of a multi-select selection within its array (null for the
-- single-row cases); the marts use it as a fact_id tiebreaker so two selections
-- that don't resolve to an option can't collide on the surrogate key.

select
    a.raw_response_id,
    a.respondent_id,
    a.survey_id,
    a.survey_version,
    a.stable_name,
    a.question_type,
    a.pii_risk,
    a.was_shown,
    a.answered,
    a.answer_value,
    case
        when a.question_type = 'checkbox' then sel.value
        else a.answer_value
    end as option_lookup_value,
    sel.ordinality as selection_ordinal
from {{ ref('int_response_answers') }} as a
left join lateral jsonb_array_elements_text(
    case
        when a.question_type = 'checkbox'
            and a.answered
            and jsonb_typeof(a.answer_json) = 'array'
            then a.answer_json
        else '[]'::jsonb
    end
) with ordinality as sel(value, ordinality) on true
