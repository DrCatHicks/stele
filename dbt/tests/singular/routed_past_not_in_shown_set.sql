-- Routed-past integrity (design-doc §3.5, invariant 3): the complement of
-- shown_set_integrity. Every fact row marked was_shown = false must have its
-- question ABSENT from the originating submission's shown_questions — i.e. a
-- routed-past row is genuinely routed past, never a shown question that the
-- derivation silently dropped.
--
-- Paired with shown_set_integrity (which guards the was_shown = true direction),
-- this pins was_shown to an exact bijection with presence in the API-captured
-- shown-set, across the surrogate-key join through dim_question. A regression
-- that mislabels a shown question as routed-past — collapsing shown-skipped and
-- routed-past, the very distinction M4 protects — fails the build here.
-- Passes when it returns zero rows.

with raw_shown as (
    select
        respondent_id,
        {{ surrogate_key(['survey_id', 'survey_version']) }} as survey_version_id,
        sq #>> '{}' as stable_name
    from {{ ref('stg_raw_responses') }},
        lateral jsonb_array_elements(shown_questions) as sq
),

fact_routed_past as (
    select
        fri.fact_id,
        fri.respondent_id,
        fri.survey_version_id,
        -- Resolve a matrix cell sub-question against its matrix's name (the
        -- shown-set entry); a plain question uses its own stable_name (M5.3).
        coalesce(dq.matrix_name, dq.stable_name) as shown_name
    from {{ ref('fact_response_item') }} as fri
    inner join {{ ref('dim_question') }} as dq
        on fri.question_id = dq.question_id
    where not fri.was_shown
)

select frp.fact_id
from fact_routed_past as frp
inner join raw_shown as rs
    on frp.respondent_id = rs.respondent_id
    and frp.survey_version_id = rs.survey_version_id
    and frp.shown_name = rs.stable_name
