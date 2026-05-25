-- Free-text redaction integrity (invariant 6, design-doc §3.9): cross-check the
-- fact's value_text / value_text_redacted against the question's pii_risk in the
-- snapshot AND the reviewer's promotion decision. The gating lives in
-- fact_response_item; this test re-derives the expectation from
-- int_response_answers + the review decisions so a regression in that gating is
-- caught rather than trusted. Effective risk defaults absent → 'high' (safe).
--
-- A free-text answer is "surfaced" to the marts when it is low-risk OR a reviewer
-- has promoted that specific response (high-risk + status='promoted'). A row
-- fails when:
--   - a surfaced answer is flagged redacted, or (answered) value_text != the raw
--     answer, or (unanswered) value_text is populated;
--   - a non-surfaced (high-risk, not promoted) free-text answer isn't redacted or
--     leaked a value_text;
--   - a non-free-text row carries value_text or is flagged redacted.
-- Passes when it returns zero rows. (Join is on respondent+version+question+
-- occurrence; the per-response promotion is resolved via raw_response_id +
-- occurrence under the one-submission-per-respondent-version assumption —
-- multi-submission is open follow-up. occurrence distinguishes a panel cell's
-- repeated answers, M5.4.)

with decisions as (
    select raw_response_id, question_name, occurrence, status
    from {{ source('pii', 'free_text_review_decisions') }}
),

answers as (
    select
        a.respondent_id,
        a.occurrence,
        {{ surrogate_key(['a.survey_id', 'a.survey_version']) }} as survey_version_id,
        {{ surrogate_key(['a.stable_name']) }} as question_id,
        a.value_kind,
        coalesce(a.pii_risk, 'high') as effective_risk,
        a.answered,
        a.answer_value,
        coalesce(d.status, '') = 'promoted' as promoted
    from {{ ref('int_response_answers') }} as a
    left join decisions as d
        on a.raw_response_id = d.raw_response_id
        and a.stable_name = d.question_name
        and a.occurrence = d.occurrence
),

joined as (
    select
        fri.fact_id,
        fri.value_text,
        fri.value_text_redacted,
        a.value_kind,
        a.effective_risk,
        a.answered,
        a.answer_value,
        -- value_kind = 'text' is free text; a numeric/date `text` input (M5.5) is
        -- value_kind numeric/date and never surfaces / redacts value_text.
        (a.value_kind = 'text' and (a.effective_risk = 'low' or a.promoted)) as surfaced
    from {{ ref('fact_response_item') }} as fri
    inner join answers as a
        on fri.respondent_id = a.respondent_id
        and fri.survey_version_id = a.survey_version_id
        and fri.question_id = a.question_id
        and fri.occurrence = a.occurrence
)

select fact_id
from joined
where
    (
        -- surfaced free text: low-risk, or a promoted high-risk response
        surfaced
        and (
            value_text_redacted = true
            or (answered and value_text is distinct from answer_value)
            or (not answered and value_text is not null)
        )
    )
    or (
        -- high-risk free text that was not promoted: stays redacted
        value_kind = 'text'
        and not surfaced
        and (value_text is not null or value_text_redacted = false)
    )
    or (
        -- non-free-text (option / numeric / date): never a value_text or redaction
        value_kind != 'text'
        and (value_text is not null or value_text_redacted = true)
    )
