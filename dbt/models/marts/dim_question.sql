-- Stable question dimension: one row per (survey_id, stable_name). question_id is
-- keyed on survey_id + stable_name, so it pools a question across VERSIONS of one
-- survey (same name across v1/v2 = the same question; per-version detail lives in
-- dim_question_version), but NEVER across different surveys. Keying on stable_name
-- alone silently merged unrelated questions that happened to share a name (e.g.
-- two surveys' "q1") into one id — silent cross-survey pooling, invariant 5.
-- survey_id is constant across a survey's versions, so it's a clean attribute of
-- this grain. Cross-rename / cross-instrument pooling, when genuinely wanted, is
-- the explicit parent_question_id opt-in below.
--
-- parent_question_id / parent_question_rationale capture cross-version
-- equivalence. They are NEVER auto-populated (invariant 5) — that is a
-- researcher judgment — so both are emitted as explicit nulls here.

select
    {{ surrogate_key(['survey_id', 'stable_name']) }} as question_id,
    survey_id,
    stable_name,
    min(question_type) as question_type,
    -- Pooled PII-risk for the stable question. min() resolves to 'high' if any
    -- version is high (the safe direction); per-version truth is in
    -- int_survey_questions. Null for non-free-text / untagged questions.
    min(pii_risk) as pii_risk,
    min(published_at) as first_published_at,
    -- Matrix decomposition (M5.3): for a matrix cell sub-question, the matrix
    -- element's own name — the shown-set entry governing it, which
    -- shown_set_integrity resolves was_shown against (not the composite
    -- stable_name); null for a plain question. Deterministic per stable_name.
    min(matrix_name) as matrix_name,
    -- Repeating-group decomposition (M5.4): for a paneldynamic cell sub-question,
    -- the panel element's own name — the shown-set entry the integrity tests
    -- resolve was_shown against (a cell is shown iff its panel is); null for a
    -- plain or matrix question. Deterministic per stable_name.
    min(panel_name) as panel_name,
    cast(null as text) as parent_question_id,
    cast(null as text) as parent_question_rationale
from {{ ref('int_survey_questions') }}
group by survey_id, stable_name
