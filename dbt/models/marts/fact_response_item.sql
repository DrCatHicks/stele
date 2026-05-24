-- Selection-grain fact: (respondent, survey_version, question, occurrence,
-- selected_option) — invariant 7. A single-select answer resolves to one row with
-- an option_key; a multi-select (checkbox) answer fans out to one row per chosen
-- option, each carrying its own option_key (M5.1) — the grain that lets analysts
-- count selections without conflating them with respondents.
--
-- Unanswered questions still produce a single row so routing fidelity is
-- queryable (a multi-select with no selection collapses to this same row):
--   shown & answered : option_key set, was_shown = true
--   shown & skipped  : option_key null, was_shown = true
--   routed past      : option_key null, was_shown = false
--
-- value_text carries free-text answers for pii_risk='low' questions, AND for
-- high-risk answers a reviewer has individually promoted (design-doc §3.9,
-- invariant 6). Otherwise high-risk free text is redacted here (value_text null,
-- value_text_redacted true) and lives in pii.free_text_responses for the
-- reviewer; the default is high, so the safe path is the default. Promotion is a
-- per-response decision (pii.free_text_review_decisions, keyed by raw_response_id
-- + question_name) — the one ETL input that isn't raw, and it carries no content,
-- only the decision. The value_text CASE references pii_risk inline so the
-- invariant-6 lint binds the guard to this statement. occurrence is fixed at 1
-- until repeating groups land (a later M5 story).

with answers as (
    select
        a.raw_response_id,
        a.stable_name,
        a.respondent_id,
        a.answer_value,
        a.answer_json,
        a.answered,
        a.was_shown,
        a.question_type,
        a.pii_risk,
        {{ surrogate_key(['a.survey_id', 'a.survey_version']) }} as survey_version_id,
        {{ surrogate_key(['a.stable_name']) }} as question_id,
        {{ surrogate_key(['a.survey_id', 'a.survey_version', 'a.stable_name']) }} as question_version_id
    from {{ ref('int_response_answers') }} as a
),

-- Expand each answer to its selection grain. A multi-select answer fans out to
-- one row per chosen option (jsonb_array_elements_text over the answer array);
-- every other type and every unanswered question stays a single row. LEFT JOIN
-- LATERAL on the (possibly empty) array guarantees that single row even when the
-- lateral yields nothing — a non-checkbox question, an unanswered checkbox, or a
-- checkbox answered with an empty array — so the routing row (shown-skipped /
-- routed-past) is never lost. option_lookup_value is the value joined to
-- dim_option: the per-selection value for checkbox, the scalar answer otherwise.
-- Assumes a well-formed checkbox array carries distinct values (SurveyJS
-- guarantees it; raw_responses is append-only from the API) — a duplicated value
-- would fan out to two rows sharing an option_key and collide on fact_id.
selections as (
    select
        a.raw_response_id,
        a.stable_name,
        a.respondent_id,
        a.answer_value,
        a.was_shown,
        a.question_type,
        a.pii_risk,
        a.survey_version_id,
        a.question_id,
        a.question_version_id,
        case
            when a.question_type = 'checkbox' then sel.value
            else a.answer_value
        end as option_lookup_value
    from answers as a
    left join lateral jsonb_array_elements_text(
        case
            when a.question_type = 'checkbox'
                and a.answered
                and jsonb_typeof(a.answer_json) = 'array'
                then a.answer_json
            else '[]'::jsonb
        end
    ) as sel(value) on true
),

-- Reviewer promote/reject decisions, per response+question. 'promoted' lets a
-- specific high-risk answer reach the marts; absent or 'rejected' keeps it
-- redacted. No content here — just the decision (design-doc §3.10).
review_decisions as (
    select
        raw_response_id,
        question_name,
        status
    from {{ source('pii', 'free_text_review_decisions') }}
),

-- CTE name carries the `fact_response_item` token so the invariant-6 lint (which
-- scans for a value_text-writing statement referencing both fact_response_item
-- and pii_risk) binds to the gated SELECT below.
fact_response_item_rows as (
    select
        -- option_key is part of the grain, so each fanned-out multi-select
        -- selection gets a distinct fact_id; an unanswered row keys on '' once.
        {{ surrogate_key([
            's.respondent_id', 's.survey_version_id', 's.question_id',
            '1', "coalesce(o.option_key, '')"
        ]) }} as fact_id,
        s.respondent_id,
        s.survey_version_id,
        s.question_id,
        s.question_version_id,
        1 as occurrence,
        o.option_key,
        cast(null as numeric) as value_numeric,
        cast(null as date) as value_date,
        case
            when s.question_type in ('text', 'comment')
                and (s.pii_risk = 'low' or d.status = 'promoted')
                then s.answer_value
        end as value_text,
        case
            when s.question_type in ('text', 'comment')
                and coalesce(s.pii_risk, 'high') = 'high'
                and coalesce(d.status, '') != 'promoted'
                then true
            else false
        end as value_text_redacted,
        s.was_shown,
        cast(null as int) as rank
    from selections as s
    left join {{ ref('dim_option') }} as o
        on s.question_version_id = o.question_version_id
        and s.option_lookup_value = o.value
    left join review_decisions as d
        on s.raw_response_id = d.raw_response_id
        and s.stable_name = d.question_name
)

select * from fact_response_item_rows
