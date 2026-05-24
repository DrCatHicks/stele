-- One row per question per survey version, from the embedded definition snapshot.
-- Feeds dim_question / dim_question_version and the question set that
-- int_response_answers fans every submission across.
--
-- A matrix question (M5.3) is NOT one question here: it decomposes into one
-- single-select sub-question per cell — row × column — so the star schema handles
-- it uniformly with single-select (design-doc §3.5, FR-4 "matrix sub-questions").
--   'matrix'         → one sub-question per row; the shared `columns` are its options.
--   'matrixdropdown' → one sub-question per (row, column); the column's `choices`
--                      (or the matrix-level shared `choices`) are its options.
-- Each sub-question's stable_name is the matrix name joined to the row [and
-- column] by '.' (see the subquestion_name macro). matrix_name / matrix_row /
-- matrix_column carry the decomposition forward: int_response_answers uses them to
-- navigate the nested payload and to resolve the shown-set against the matrix's own
-- name, and the dims expose them so analysts can pivot without parsing stable_name.
-- A plain (non-matrix) question carries all three as null and keeps its own name.

with elements as (
    select
        survey_id,
        survey_version,
        definition_hash,
        published_at,
        stable_name,
        element,
        display_order,
        element ->> 'type' as question_type
    from {{ ref('int_survey_elements') }}
),

scalar_questions as (
    select
        survey_id,
        survey_version,
        definition_hash,
        published_at,
        stable_name,
        question_type,
        -- pii_risk tags free-text questions (design-doc §3.9); null otherwise.
        -- The fact gates value_text on this; the API defaults absent → 'high'.
        element ->> 'pii_risk' as pii_risk,
        coalesce(element ->> 'title', stable_name) as prompt_text,
        display_order,
        cast(null as text) as matrix_name,
        cast(null as text) as matrix_row,
        cast(null as text) as matrix_column
    from elements
    where question_type not in ('matrix', 'matrixdropdown')
),

matrix_questions as (
    -- 'matrix': one single-select sub-question per row, over the shared columns.
    select
        e.survey_id,
        e.survey_version,
        e.definition_hash,
        e.published_at,
        {{ subquestion_name(['e.stable_name', matrix_value('mrow.value')]) }} as stable_name,
        e.question_type,
        cast(null as text) as pii_risk,
        coalesce(e.element ->> 'title', e.stable_name)
            || ' — '
            || coalesce(mrow.value ->> 'text', {{ matrix_value('mrow.value') }}) as prompt_text,
        e.display_order,
        e.stable_name as matrix_name,
        {{ matrix_value('mrow.value') }} as matrix_row,
        cast(null as text) as matrix_column
    from elements as e
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(e.element -> 'rows') = 'array' then e.element -> 'rows' else '[]'::jsonb end
    ) as mrow(value)
    where e.question_type = 'matrix'
),

matrixdropdown_questions as (
    -- 'matrixdropdown': one single-select sub-question per (row, column).
    select
        e.survey_id,
        e.survey_version,
        e.definition_hash,
        e.published_at,
        {{ subquestion_name(['e.stable_name', matrix_value('mrow.value'), "mcol.value ->> 'name'"]) }}
            as stable_name,
        e.question_type,
        cast(null as text) as pii_risk,
        coalesce(e.element ->> 'title', e.stable_name)
            || ' — '
            || coalesce(mrow.value ->> 'text', {{ matrix_value('mrow.value') }})
            || ' / '
            || coalesce(mcol.value ->> 'title', mcol.value ->> 'name') as prompt_text,
        e.display_order,
        e.stable_name as matrix_name,
        {{ matrix_value('mrow.value') }} as matrix_row,
        mcol.value ->> 'name' as matrix_column
    from elements as e
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(e.element -> 'rows') = 'array' then e.element -> 'rows' else '[]'::jsonb end
    ) as mrow(value)
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(e.element -> 'columns') = 'array' then e.element -> 'columns' else '[]'::jsonb end
    ) as mcol(value)
    where e.question_type = 'matrixdropdown'
)

select * from scalar_questions
union all
select * from matrix_questions
union all
select * from matrixdropdown_questions
