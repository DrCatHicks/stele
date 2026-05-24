-- One row per (survey version, question): the specific rendering, with prompt
-- text and response type. Joins to dim_question via question_id.

select distinct
    {{ surrogate_key(['survey_id', 'survey_version', 'stable_name']) }} as question_version_id,
    {{ surrogate_key(['stable_name']) }} as question_id,
    prompt_text,
    question_type as response_type,
    -- Matrix decomposition (M5.3): the matrix element name + the cell's row and
    -- (for matrixdropdown) column, so an analyst can pivot a matrix without
    -- parsing the composite stable_name. All null for a plain question.
    matrix_name,
    matrix_row,
    matrix_column,
    -- Repeating-group decomposition (M5.4): the panel element name + the cell's
    -- template-element name, so an analyst can pivot a panel without parsing the
    -- composite stable_name. Both null for a plain or matrix question.
    panel_name,
    panel_element
from {{ ref('int_survey_questions') }}
