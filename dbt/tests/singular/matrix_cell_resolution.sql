-- Matrix cell resolution (M5.3). A matrix decomposes into one single-select cell
-- sub-question per row [× column]; each answered cell must resolve to a defined
-- option (the chosen column / cell choice), exactly like a single-select. This
-- localizes two matrix-specific failure modes the aggregate
-- option_fact_row_count_parity would only surface as a count mismatch:
--   - the sub-question composite stable_name drifting between int_survey_questions
--     and int_survey_options (so question_version_id wouldn't match dim_option);
--   - int_response_answers navigating the nested payload to the wrong key (so the
--     cell value isn't among that cell's defined options).
-- An unanswered cell (shown-skipped / routed-past) legitimately has no option, so
-- only answered cells with a non-null looked-up value are checked.
-- Passes when it returns zero rows.

select
    s.raw_response_id,
    s.stable_name,
    s.option_lookup_value
from {{ ref('int_response_selections') }} as s
left join {{ ref('dim_option') }} as o
    on {{ surrogate_key(['s.survey_id', 's.survey_version', 's.stable_name']) }} = o.question_version_id
    and s.option_lookup_value = o.value
where s.question_type in ('matrix', 'matrixdropdown')
    and s.answered
    and s.option_lookup_value is not null
    and o.option_key is null
