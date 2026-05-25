-- Selection-grain expansion of int_response_answers: an array-valued answer
-- (multi-select checkbox, M5.1; ranking, M5.2) fans out to one row per chosen
-- option; every other answered question and every unanswered question stays a
-- single row. A matrix sub-question (M5.3) is a single-select scalar by the time
-- it reaches here (int_response_answers already navigated the nested payload to
-- the chosen cell value), so it takes the single-row path like any single-select.
-- This model owns that fan-out AND all the Postgres-specific JSON handling for
-- answers, so the marts (fact_response_item) stay portable — the same
-- staging+intermediate confinement documented in stg_raw_responses (CLAUDE.md
-- dbt portability; design-doc §5).
--
-- A LEFT JOIN LATERAL over the (possibly empty) answer array guarantees the
-- single row survives when the lateral yields nothing — a scalar/non-array
-- question, or an array-valued question that is unanswered or answered with an
-- empty array — so the routing row (shown-skipped / routed-past) is never lost.
--
-- option_lookup_value is the value the marts join to dim_option: the per-selection
-- value for an array-valued type, the scalar answer otherwise. selection_ordinal
-- is the 1-based position of a selection within its array (null for the single-row
-- cases). It serves two roles downstream: a fact_id tiebreaker so two selections
-- that don't resolve to an option can't collide on the surrogate key, and — for a
-- ranking question, where array order *is* the rank — the source of
-- fact_response_item.rank. jsonb_array_elements_text WITH ORDINALITY numbers rows
-- in array order, so ordinal 1 is the top-ranked item.

select
    a.raw_response_id,
    a.respondent_id,
    a.survey_id,
    a.survey_version,
    a.stable_name,
    a.question_type,
    a.value_kind,
    a.pii_risk,
    -- Panel occurrence (M5.4) rides through to the fact grain; 1 for non-panel.
    -- A panel cell is single-select/free-text, so it never also fans out as a
    -- multi-selection — occurrence and selection_ordinal stay independent.
    a.occurrence,
    a.was_shown,
    a.answered,
    a.answer_value,
    case
        when a.question_type in ('checkbox', 'ranking') then sel.value
        else a.answer_value
    end as option_lookup_value,
    sel.ordinality as selection_ordinal,
    -- Scalar coercion (M5.5) lives here with the other Postgres-specific answer
    -- handling, so the marts (fact_response_item) stay portable. A boolean maps
    -- true/false → 1/0; a rating / numeric `text` casts when the answer is a valid
    -- number; a date `text` casts a YYYY-MM-DD answer. A malformed value (only
    -- reachable via the public submit endpoint, never SurveyJS) coerces to null —
    -- answered stays true but the value column is null, the same conservative
    -- treatment an unresolved checkbox value gets. value_kind='option'/'text' rows
    -- (and every fanned-out array selection) carry null in both, so invariant 8
    -- holds: at most one populated value slot per fact row.
    case
        when a.value_kind = 'numeric' and a.question_type = 'boolean'
            then case a.answer_value when 'true' then 1 when 'false' then 0 end
        when a.value_kind = 'numeric' and a.answer_value ~ '^-?[0-9]+(\.[0-9]+)?$'
            then a.answer_value::numeric
    end as value_numeric,
    case
        when a.value_kind = 'date' and a.answer_value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
            then a.answer_value::date
    end as value_date
from {{ ref('int_response_answers') }} as a
left join lateral jsonb_array_elements_text(
    case
        when a.question_type in ('checkbox', 'ranking')
            and a.answered
            and jsonb_typeof(a.answer_json) = 'array'
            then a.answer_json
        else '[]'::jsonb
    end
) with ordinality as sel(value, ordinality) on true
