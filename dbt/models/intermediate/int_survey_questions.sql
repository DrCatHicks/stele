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
--
-- value_kind (M5.5) is the single source of which value column a question feeds in
-- fact_response_item, derived once here so the answer side never re-derives it:
--   'option'  → resolves to an option_key via dim_option (all choice + matrix cells)
--   'text'    → value_text, PII-gated (free-text text/comment)
--   'numeric' → value_numeric (rating, boolean→1/0, and text inputType number/range)
--   'date'    → value_date (text inputType date)
-- A panel cell keeps plain option/text semantics — numeric/date cells inside panels
-- and matrices are deferred — so value_kind is only 'numeric'/'date' at top level.
--
-- A paneldynamic question (M5.4) likewise decomposes into one sub-question per
-- TEMPLATE ELEMENT (stable_name = "panel.element"), but its occurrences are NOT
-- fixed in the definition — they're driven by the respondent's answer array — so
-- this model emits one row per element (no occurrence here); the array position
-- becomes the fact grain's `occurrence` on the answer side. panel_name /
-- panel_element carry the decomposition forward exactly as matrix_* do: the panel
-- name resolves the shown-set (a cell is shown iff its panel is) and the element
-- name navigates each occurrence object. Unlike a matrix cell, a panel cell keeps
-- its OWN type (radiogroup/dropdown → option_key; text/comment → value_text) and
-- pii_risk. matrix_* are null for a panel sub-question and vice versa.

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
        -- See the header note: rating/boolean → numeric, text inputType number/range
        -- → numeric and date → date, other free text → text, everything else option.
        case
            when question_type in ('rating', 'boolean') then 'numeric'
            -- inputType diverts a `text` only; a `comment` is inherently free text
            -- (SurveyJS ignores inputType on it), so a stray inputType must not
            -- route it off the value_text/PII path (the safe direction).
            when question_type = 'text'
                and (element ->> 'inputType') in ('number', 'range') then 'numeric'
            when question_type = 'text'
                and (element ->> 'inputType') = 'date' then 'date'
            when question_type in ('text', 'comment') then 'text'
            else 'option'
        end as value_kind,
        cast(null as text) as matrix_name,
        cast(null as text) as matrix_row,
        cast(null as text) as matrix_column,
        cast(null as text) as panel_name,
        cast(null as text) as panel_element
    from elements
    where question_type not in ('matrix', 'matrixdropdown', 'paneldynamic')
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
        -- Every matrix cell resolves to an option_key (single-select over columns).
        'option' as value_kind,
        e.stable_name as matrix_name,
        {{ matrix_value('mrow.value') }} as matrix_row,
        cast(null as text) as matrix_column,
        cast(null as text) as panel_name,
        cast(null as text) as panel_element
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
        -- Supported matrixdropdown cells are option-based (MATRIX_CELL_TYPES) → option_key.
        'option' as value_kind,
        e.stable_name as matrix_name,
        {{ matrix_value('mrow.value') }} as matrix_row,
        mcol.value ->> 'name' as matrix_column,
        cast(null as text) as panel_name,
        cast(null as text) as panel_element
    from elements as e
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(e.element -> 'rows') = 'array' then e.element -> 'rows' else '[]'::jsonb end
    ) as mrow(value)
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(e.element -> 'columns') = 'array' then e.element -> 'columns' else '[]'::jsonb end
    ) as mcol(value)
    where e.question_type = 'matrixdropdown'
),

paneldynamic_questions as (
    -- 'paneldynamic': one sub-question per template element. The element keeps its
    -- own type + pii_risk (a panel cell is a radiogroup/dropdown → option_key, or a
    -- text/comment → value_text). Occurrence is answer-driven, so it is NOT here.
    select
        e.survey_id,
        e.survey_version,
        e.definition_hash,
        e.published_at,
        {{ subquestion_name(['e.stable_name', "tmpl.value ->> 'name'"]) }} as stable_name,
        tmpl.value ->> 'type' as question_type,
        tmpl.value ->> 'pii_risk' as pii_risk,
        coalesce(e.element ->> 'title', e.stable_name)
            || ' — '
            || coalesce(tmpl.value ->> 'title', tmpl.value ->> 'name') as prompt_text,
        e.display_order,
        -- A panel cell keeps plain semantics: free-text → text, option cell → option.
        -- Numeric/date inputType inside a panel is deferred (treated as plain text).
        case
            when tmpl.value ->> 'type' in ('text', 'comment') then 'text'
            else 'option'
        end as value_kind,
        cast(null as text) as matrix_name,
        cast(null as text) as matrix_row,
        cast(null as text) as matrix_column,
        e.stable_name as panel_name,
        tmpl.value ->> 'name' as panel_element
    from elements as e
    cross join lateral jsonb_array_elements(
        case
            when jsonb_typeof(e.element -> 'templateElements') = 'array'
                then e.element -> 'templateElements'
            else '[]'::jsonb
        end
    ) as tmpl(value)
    where e.question_type = 'paneldynamic'
)

select * from scalar_questions
union all
select * from matrix_questions
union all
select * from matrixdropdown_questions
union all
select * from paneldynamic_questions
