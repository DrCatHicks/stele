-- Invariant 8 (M5.5), the per-kind half: each populated value slot must match the
-- question's value_kind. polymorphic_value_invariant already catches "more than one
-- slot populated"; this pins WHICH slot a value may occupy, so a numeric answer
-- can't leak into value_text (or vice versa) and a value_kind misclassification
-- surfaces at build instead of as silently mis-typed analytics.
--   value_numeric set ⟹ value_kind = 'numeric'  (rating, boolean, numeric text)
--   value_date    set ⟹ value_kind = 'date'     (date text)
--   value_text    set ⟹ value_kind = 'text'     (free text)
--   option_key    set ⟹ value_kind = 'option'   (choice / matrix / panel option)
-- A singular test passes when it returns zero rows.

select f.fact_id, qv.value_kind
from {{ ref('fact_response_item') }} as f
inner join {{ ref('dim_question_version') }} as qv
    on f.question_version_id = qv.question_version_id
where (f.value_numeric is not null and qv.value_kind != 'numeric')
    or (f.value_date is not null and qv.value_kind != 'date')
    or (f.value_text is not null and qv.value_kind != 'text')
    or (f.option_key is not null and qv.value_kind != 'option')
