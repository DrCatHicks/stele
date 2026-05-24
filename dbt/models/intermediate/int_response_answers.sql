-- The routing-fidelity core: fan every submission across the full question set of
-- the version it answered (from the snapshot), then attach the answer and the
-- shown-set. This yields the three states distinctly — never collapsed to
-- "missing" (design-doc §3.5, CLAUDE.md "silent defaults"):
--   shown & answered : was_shown = true,  answered = true
--   shown & skipped  : was_shown = true,  answered = false
--   routed past      : was_shown = false, answered = false
-- was_shown comes straight from the API-captured shown_questions (invariant 3);
-- routing is never reconstructed from visibleIf in SQL.
--
-- Matrix sub-questions (M5.3) live in a nested payload object and share the matrix
-- element's shown-set entry:
--   shown_name : the matrix name (a sub-question is shown iff its matrix is) —
--                coalesce(matrix_name, stable_name) so plain questions use their own.
--   answer     : payload -> matrix_name ->> matrix_row              ('matrix')
--                payload -> matrix_name -> matrix_row ->> column     ('matrixdropdown')
--   The navigation collapses to payload ->> stable_name for a plain question.

with responses as (
    select
        raw_response_id,
        respondent_id,
        survey_id,
        survey_version,
        submitted_at,
        payload,
        shown_questions
    from {{ ref('stg_raw_responses') }}
),

questions as (
    select
        survey_id,
        survey_version,
        stable_name,
        question_type,
        pii_risk,
        matrix_name,
        matrix_row,
        matrix_column
    from {{ ref('int_survey_questions') }}
),

navigated as (
    select
        r.raw_response_id,
        r.respondent_id,
        r.survey_id,
        r.survey_version,
        r.submitted_at,
        q.stable_name,
        q.question_type,
        q.pii_risk,
        r.shown_questions,
        -- The shown-set entry governing this (sub-)question's visibility.
        coalesce(q.matrix_name, q.stable_name) as shown_name,
        -- The jsonb object holding this answer and the key within it.
        case
            when q.matrix_name is null then r.payload
            when q.matrix_column is null then r.payload -> q.matrix_name
            else r.payload -> q.matrix_name -> q.matrix_row
        end as answer_base,
        case
            when q.matrix_name is null then q.stable_name
            when q.matrix_column is null then q.matrix_row
            else q.matrix_column
        end as answer_key
    from responses as r
    inner join questions as q
        on r.survey_id = q.survey_id
        and r.survey_version = q.survey_version
)

select
    raw_response_id,
    respondent_id,
    survey_id,
    survey_version,
    submitted_at,
    stable_name,
    -- question_type and pii_risk ride along so fact_response_item can gate
    -- value_text on them (free-text + pii_risk='low') without re-parsing.
    question_type,
    pii_risk,
    coalesce(jsonb_exists(shown_questions, shown_name), false) as was_shown,
    coalesce(jsonb_exists(answer_base, answer_key), false) as answered,
    -- Scalar text form: single-select option lookup and free-text value_text.
    answer_base ->> answer_key as answer_value,
    -- Raw jsonb form, type preserved: multi-select answers are arrays that
    -- int_response_selections fans out (jsonb_array_elements_text). Carried here so
    -- the warehouse parses the payload once (invariant 4).
    answer_base -> answer_key as answer_json
from navigated
