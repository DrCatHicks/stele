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
-- + question_name + occurrence) — the one ETL input that isn't raw, and it carries
-- no content, only the decision. The value_text CASE references pii_risk inline so
-- the invariant-6 lint binds the guard to this statement. occurrence is the 1-based
-- panel instance for a paneldynamic cell (M5.4), 1 for every other question.

-- The multi-select fan-out and all answer-side JSON parsing live in
-- int_response_selections (keeping this marts model portable — JSON confinement
-- per stg_raw_responses / CLAUDE.md). One row per chosen option for a checkbox,
-- one row otherwise, one routing row for an unanswered question.
with selections as (
    select
        s.raw_response_id,
        s.stable_name,
        s.respondent_id,
        s.answer_value,
        s.was_shown,
        s.question_type,
        s.pii_risk,
        s.option_lookup_value,
        s.selection_ordinal,
        s.occurrence,
        {{ surrogate_key(['s.survey_id', 's.survey_version']) }} as survey_version_id,
        {{ surrogate_key(['s.stable_name']) }} as question_id,
        {{ surrogate_key(['s.survey_id', 's.survey_version', 's.stable_name']) }} as question_version_id
    from {{ ref('int_response_selections') }} as s
),

-- Reviewer promote/reject decisions, per response+question+occurrence. 'promoted'
-- lets a specific high-risk answer reach the marts; absent or 'rejected' keeps it
-- redacted. occurrence distinguishes a panel cell's repeated answers (M5.4). No
-- content here — just the decision (design-doc §3.10).
review_decisions as (
    select
        raw_response_id,
        question_name,
        occurrence,
        status
    from {{ source('pii', 'free_text_review_decisions') }}
),

-- CTE name carries the `fact_response_item` token so the invariant-6 lint (which
-- scans for a value_text-writing statement referencing both fact_response_item
-- and pii_risk) binds to the gated SELECT below.
fact_response_item_rows as (
    select
        -- option_key is part of the grain, so each fanned-out multi-select
        -- selection that resolves gets a distinct fact_id; an unanswered row keys
        -- on '' once. A multi-select selection that does NOT resolve to an option
        -- (e.g. an unexpected value from the public submit endpoint) has a null
        -- option_key, so two such selections would otherwise share a key — the
        -- selection_ordinal disambiguates them, keeping fact_id unique by
        -- construction while the row-count parity test still flags the unresolved
        -- value. (A duplicated *resolving* value — which SurveyJS never produces —
        -- would still collide; raw_responses is append-only from the API.)
        {{ surrogate_key([
            's.respondent_id', 's.survey_version_id', 's.question_id', 's.occurrence',
            "coalesce(o.option_key, case when s.selection_ordinal is not null "
            "then 'unresolved:' || s.selection_ordinal::text else '' end)"
        ]) }} as fact_id,
        s.respondent_id,
        s.survey_version_id,
        s.question_id,
        s.question_version_id,
        s.occurrence,
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
        -- rank is populated only for a ranking question, where the selection's
        -- 1-based array position is its rank (M5.2). Null for every other type —
        -- a checkbox selection has an ordinal too, but it's an arbitrary
        -- tiebreaker there, not a rank, so it stays null.
        case
            when s.question_type = 'ranking' then s.selection_ordinal::int
        end as rank
    from selections as s
    left join {{ ref('dim_option') }} as o
        on s.question_version_id = o.question_version_id
        and s.option_lookup_value = o.value
    left join review_decisions as d
        on s.raw_response_id = d.raw_response_id
        and s.stable_name = d.question_name
        and s.occurrence = d.occurrence
)

select * from fact_response_item_rows
