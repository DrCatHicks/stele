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
--
-- Paneldynamic sub-questions (M5.4) repeat per occurrence: the answer is an ARRAY
-- at `payload -> panel_name`, one object per occurrence. A LEFT JOIN LATERAL fans
-- that array out WITH ORDINALITY → one row per occurrence (occurrence = the 1-based
-- ordinal), with the occurrence object as the answer_base and the element name as
-- the key. A panel that's unanswered or has no instances yields zero array rows, so
-- the LEFT JOIN preserves a single routing row (occurrence 1, answered = false) —
-- shown-skipped / routed-past is never lost. Every non-panel question takes the
-- same path with a one-element synthetic fan (occurrence fixed at 1).
--   shown_name : the panel name (a cell is shown iff its panel is).
--   answer     : (payload -> panel_name)[occurrence] ->> element_name

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
        value_kind,
        pii_risk,
        matrix_name,
        matrix_row,
        matrix_column,
        panel_name,
        panel_element
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
        q.value_kind,
        q.pii_risk,
        r.payload,
        r.shown_questions,
        q.matrix_name,
        q.matrix_row,
        q.matrix_column,
        q.panel_name,
        q.panel_element,
        -- The shown-set entry governing this (sub-)question's visibility: the panel
        -- or matrix element's own name, else the question's own stable_name.
        coalesce(q.panel_name, q.matrix_name, q.stable_name) as shown_name
    from responses as r
    inner join questions as q
        on r.survey_id = q.survey_id
        and r.survey_version = q.survey_version
),

occurrences as (
    select
        n.*,
        coalesce(inst.ordinality, 1)::int as occurrence,
        -- The jsonb object holding this answer and the key within it. A panel cell
        -- reads the occurrence object (inst.value); a matrix/plain question keeps
        -- the M5.3 navigation.
        case
            when n.panel_name is not null then inst.value
            when n.matrix_name is null then n.payload
            when n.matrix_column is null then n.payload -> n.matrix_name
            else n.payload -> n.matrix_name -> n.matrix_row
        end as answer_base,
        case
            when n.panel_name is not null then n.panel_element
            when n.matrix_name is null then n.stable_name
            when n.matrix_column is null then n.matrix_row
            else n.matrix_column
        end as answer_key
    from navigated as n
    -- Fan a paneldynamic answer array into one row per occurrence; a zero-row
    -- lateral (non-panel, or an unanswered/empty panel) is preserved as a single
    -- occurrence-1 routing row by the LEFT JOIN.
    left join lateral jsonb_array_elements(
        case
            when n.panel_name is not null and jsonb_typeof(n.payload -> n.panel_name) = 'array'
                then n.payload -> n.panel_name
            else '[]'::jsonb
        end
    ) with ordinality as inst(value, ordinality) on true
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
    -- value_kind (M5.5) routes the answer to its value column downstream.
    value_kind,
    pii_risk,
    -- 1-based panel occurrence; 1 for every non-panel question (invariant 7 grain).
    occurrence,
    coalesce(jsonb_exists(shown_questions, shown_name), false) as was_shown,
    coalesce(jsonb_exists(answer_base, answer_key), false) as answered,
    -- Scalar text form: single-select option lookup and free-text value_text.
    answer_base ->> answer_key as answer_value,
    -- Raw jsonb form, type preserved: multi-select answers are arrays that
    -- int_response_selections fans out (jsonb_array_elements_text). Carried here so
    -- the warehouse parses the payload once (invariant 4).
    answer_base -> answer_key as answer_json
from occurrences
