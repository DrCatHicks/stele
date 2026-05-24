-- One row per choice per question per version. SurveyJS choices are either plain
-- scalars ("a") or objects ({"value": "a", "text": "Apple"}); normalize both to a
-- value + label. `#>> '{}'` extracts a scalar jsonb element as unquoted text.
--
-- CROSS JOIN LATERAL (not LEFT): a choice-less element — free-text, html, etc. —
-- yields zero rows from jsonb_array_elements and is dropped entirely. A LEFT JOIN
-- would instead preserve such an element as a spurious NULL-value option row,
-- silently inflating dim_option with rows no answer can ever resolve to (guarded
-- by the not_null test on dim_option.value).
--
-- Matrix options (M5.3) are scoped to the per-cell sub-question, NOT the matrix
-- element — the stable_name here must equal int_survey_questions' sub-question
-- name (subquestion_name macro) so dim_option.question_version_id matches the
-- answer's. For 'matrix' the shared `columns` are each row's options; for
-- 'matrixdropdown' each column's `choices` (falling back to the matrix-level
-- shared `choices`) are that cell's options.
--
-- Paneldynamic options (M5.4) are scoped the same way: the option-typed template
-- elements (radiogroup/dropdown) contribute one row per choice keyed by the
-- "panel.element" sub-question name. A free-text panel cell has no `choices`, so
-- the CROSS JOIN LATERAL yields zero rows for it (its answer is value_text, never
-- an option_key) — exactly the choice-less drop the plain branch relies on.

with elements as (
    select
        survey_id,
        survey_version,
        stable_name,
        element,
        element ->> 'type' as question_type
    from {{ ref('int_survey_elements') }}
),

plain_options as (
    select
        e.survey_id,
        e.survey_version,
        e.stable_name,
        case
            when jsonb_typeof(choice.value) = 'object' then choice.value ->> 'value'
            else choice.value #>> '{}'
        end as value,
        case
            when jsonb_typeof(choice.value) = 'object'
                then coalesce(choice.value ->> 'text', choice.value ->> 'value')
            else choice.value #>> '{}'
        end as label,
        choice.ordinality::int as display_order
    from elements as e
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(e.element -> 'choices') = 'array' then e.element -> 'choices' else '[]'::jsonb end
    ) with ordinality as choice(value, ordinality)
    where e.question_type not in ('matrix', 'matrixdropdown', 'paneldynamic')
),

matrix_options as (
    -- Each row sub-question's options are the shared columns.
    select
        e.survey_id,
        e.survey_version,
        {{ subquestion_name(['e.stable_name', matrix_value('mrow.value')]) }} as stable_name,
        {{ matrix_value('mcol.value') }} as value,
        coalesce(mcol.value ->> 'text', {{ matrix_value('mcol.value') }}) as label,
        mcol.ordinality::int as display_order
    from elements as e
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(e.element -> 'rows') = 'array' then e.element -> 'rows' else '[]'::jsonb end
    ) as mrow(value)
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(e.element -> 'columns') = 'array' then e.element -> 'columns' else '[]'::jsonb end
    ) with ordinality as mcol(value, ordinality)
    where e.question_type = 'matrix'
),

matrixdropdown_options as (
    -- Each (row, column) cell sub-question's options are that column's choices,
    -- falling back to the matrix-level shared `choices`.
    select
        e.survey_id,
        e.survey_version,
        {{ subquestion_name(['e.stable_name', matrix_value('mrow.value'), "mcol.value ->> 'name'"]) }}
            as stable_name,
        case
            when jsonb_typeof(choice.value) = 'object' then choice.value ->> 'value'
            else choice.value #>> '{}'
        end as value,
        case
            when jsonb_typeof(choice.value) = 'object'
                then coalesce(choice.value ->> 'text', choice.value ->> 'value')
            else choice.value #>> '{}'
        end as label,
        choice.ordinality::int as display_order
    from elements as e
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(e.element -> 'rows') = 'array' then e.element -> 'rows' else '[]'::jsonb end
    ) as mrow(value)
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(e.element -> 'columns') = 'array' then e.element -> 'columns' else '[]'::jsonb end
    ) as mcol(value)
    cross join lateral jsonb_array_elements(
        case
            when jsonb_typeof(mcol.value -> 'choices') = 'array' then mcol.value -> 'choices'
            when jsonb_typeof(e.element -> 'choices') = 'array' then e.element -> 'choices'
            else '[]'::jsonb
        end
    ) with ordinality as choice(value, ordinality)
    where e.question_type = 'matrixdropdown'
),

paneldynamic_options as (
    -- Each option-typed template element's options, keyed by "panel.element".
    -- Free-text cells (no `choices`) yield zero rows here.
    select
        e.survey_id,
        e.survey_version,
        {{ subquestion_name(['e.stable_name', "tmpl.value ->> 'name'"]) }} as stable_name,
        case
            when jsonb_typeof(choice.value) = 'object' then choice.value ->> 'value'
            else choice.value #>> '{}'
        end as value,
        case
            when jsonb_typeof(choice.value) = 'object'
                then coalesce(choice.value ->> 'text', choice.value ->> 'value')
            else choice.value #>> '{}'
        end as label,
        choice.ordinality::int as display_order
    from elements as e
    cross join lateral jsonb_array_elements(
        case
            when jsonb_typeof(e.element -> 'templateElements') = 'array'
                then e.element -> 'templateElements'
            else '[]'::jsonb
        end
    ) as tmpl(value)
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(tmpl.value -> 'choices') = 'array' then tmpl.value -> 'choices' else '[]'::jsonb end
    ) with ordinality as choice(value, ordinality)
    where e.question_type = 'paneldynamic'
)

select * from plain_options
union all
select * from matrix_options
union all
select * from matrixdropdown_options
union all
select * from paneldynamic_options
