-- paneldynamic occurrence integrity (M5.4). fact_response_item.occurrence is the
-- 1-based panel instance for a paneldynamic cell sub-question, and exactly 1 for
-- every other question. This test pins two properties:
--
--   1. occurrence != 1 only on a panel cell row (panel_name not null). An
--      occurrence other than 1 on a plain or matrix row means the answer-side
--      array fan-out (int_response_answers' LEFT JOIN LATERAL) leaked onto a
--      non-panel question.
--   2. within one (respondent, survey_version, panel) the occurrences present form
--      a contiguous 1..N with no gaps — exactly what jsonb_array_elements WITH
--      ORDINALITY yields from the submitted panel array. A gap or a start past 1
--      means the fan-out ordinality drifted from the submitted instances. (A panel
--      that is shown-skipped or routed-past contributes a single occurrence-1
--      routing row, which satisfies 1..N trivially.)
--
-- panel_name comes from dim_question, keeping this test in the marts layer (no
-- answer-side JSON). Passes when it returns zero rows.

with fact as (
    select
        fri.fact_id,
        fri.respondent_id,
        fri.survey_version_id,
        fri.occurrence,
        dq.panel_name
    from {{ ref('fact_response_item') }} as fri
    inner join {{ ref('dim_question') }} as dq
        on fri.question_id = dq.question_id
),

occurrence_on_non_panel as (
    select fact_id as offending_key
    from fact
    where occurrence != 1
        and panel_name is null
),

panel_occurrence_gaps as (
    select
        respondent_id::text || ':' || survey_version_id || ':' || panel_name
            as offending_key
    from fact
    where panel_name is not null
    group by respondent_id, survey_version_id, panel_name
    having min(occurrence) != 1
        or max(occurrence) != count(distinct occurrence)
)

select offending_key from occurrence_on_non_panel
union all
select offending_key from panel_occurrence_gaps
