-- Companion fact at respondent-question-occurrence grain (invariant 7): "did this
-- respondent see / answer this (sub-)question" without fanning across options.
-- Carries NO value columns — that distinction is the whole point of having it
-- separate from fact_response_item, so selection counts and respondent counts
-- never get silently conflated. A paneldynamic cell contributes one row per
-- occurrence (M5.4); every other question is occurrence 1.
--
-- Slice assumption: one submission per respondent per version (the seed uses
-- distinct respondents). Deduping multiple submissions to the same version is
-- future work; it has no source distinction at this grain yet.

with answers as (
    select
        respondent_id,
        occurrence,
        was_shown,
        answered,
        {{ surrogate_key(['survey_id', 'survey_version']) }} as survey_version_id,
        {{ surrogate_key(['stable_name']) }} as question_id
    from {{ ref('int_response_answers') }}
)

select
    {{ surrogate_key(['respondent_id', 'survey_version_id', 'question_id', 'occurrence']) }}
        as response_fact_id,
    respondent_id,
    survey_version_id,
    question_id,
    occurrence,
    was_shown,
    answered
from answers
