-- Construct tag stability across versions of the same question. dim_question
-- aggregates int_survey_questions with min() at the (survey_id, stable_name)
-- grain, so a tag divergence (v1 said 'phq9_q1', v2 said 'gad7_q1') would land
-- silently — min() picks one lexicographically and the disagreement vanishes.
-- The version-by-version truth still lives in raw_responses' embedded snapshot,
-- but the warehouse row would misrepresent at least one version.
--
-- Methodologically, a question changing construct membership across versions is
-- the same kind of event as a rename: the item is no longer "the same item." The
-- design doc says rename → break question_id → use parent_question_id (invariant
-- 5). So a tag change is an authoring smell: either v1 was a tagging mistake to
-- be acknowledged via a re-publish, or it's a genuine identity change that
-- should be modeled as a rename + parent_question_id link. Either way the
-- divergence has to surface — this test does that. Passes when zero rows.
--
-- Scope: only flags rows where *both* versions tagged the question. A v1
-- untagged → v2 tagged (or vice versa) is allowed — that is the legitimate path
-- for backfilling a missing tag on a stable question.

with by_question_version as (
    select
        survey_id,
        stable_name,
        construct_block,
        construct_item
    from {{ ref('int_survey_questions') }}
    where construct_block is not null or construct_item is not null
),

divergent as (
    select
        survey_id,
        stable_name,
        count(distinct construct_block) as distinct_blocks,
        count(distinct construct_item) as distinct_items
    from by_question_version
    group by survey_id, stable_name
)

select
    survey_id,
    stable_name,
    distinct_blocks,
    distinct_items
from divergent
where distinct_blocks > 1 or distinct_items > 1
