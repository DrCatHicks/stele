-- question_id must be survey-scoped (invariant 5: no silent cross-survey pooling).
-- dim_question.question_id is keyed on (survey_id, stable_name), so a question name
-- reused across unrelated surveys (e.g. two surveys' "q1") stays two distinct
-- questions instead of silently collapsing into one pooled id.
--
-- This pins the key by recomputing it from the row's own survey_id + stable_name
-- and comparing to the stored question_id. It fires even on a single-survey
-- database (where (survey_id, stable_name) and (stable_name) would otherwise be
-- indistinguishable by row counts): a regression to a stable_name-only key makes
-- the stored id diverge from this recomputation. Passes when it returns zero rows.

select
    question_id,
    survey_id,
    stable_name
from {{ ref('dim_question') }}
where question_id != {{ surrogate_key(['survey_id', 'stable_name']) }}
