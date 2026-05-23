-- Promotion round-trip (design-doc §3.9 / §3.10, invariant 6): a high-risk
-- free-text answer a reviewer has promoted must actually surface in the analyst
-- marts. The redaction-parity test proves the *gating* is internally consistent,
-- but passes trivially when nothing is promoted; this test proves the promotion
-- path is wired end to end — a promoted, answered response carries its value_text
-- into fact_response_item with value_text_redacted=false.
--
-- Fails (returns rows) when a promoted+answered free-text response is missing its
-- text in the marts or is still flagged redacted. Join is on
-- respondent+version+question (one submission per respondent+version assumption).

with promoted as (
    select raw_response_id, question_name
    from {{ source('pii', 'free_text_review_decisions') }}
    where status = 'promoted'
),

promoted_answers as (
    select
        a.respondent_id,
        {{ surrogate_key(['a.survey_id', 'a.survey_version']) }} as survey_version_id,
        {{ surrogate_key(['a.stable_name']) }} as question_id,
        a.answer_value
    from {{ ref('int_response_answers') }} as a
    inner join promoted as p
        on a.raw_response_id = p.raw_response_id
        and a.stable_name = p.question_name
    where a.answered
)

select pa.respondent_id, pa.question_id
from promoted_answers as pa
left join {{ ref('fact_response_item') }} as fri
    on pa.respondent_id = fri.respondent_id
    and pa.survey_version_id = fri.survey_version_id
    and pa.question_id = fri.question_id
where
    fri.fact_id is null
    or fri.value_text is distinct from pa.answer_value
    or fri.value_text_redacted = true
