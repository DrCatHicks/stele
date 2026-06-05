-- Construct-tag integrity. construct_block and construct_item are authored
-- provenance (this question is item N of a reusable scale). They are NOT a
-- pooling key — cross-version/cross-survey pooling stays the parent_question_id
-- opt-in (invariant 5) — but the *pair* still has to make sense: a question
-- tagged with a construct_item must also resolve a construct_block, or "item 3
-- of nothing" rots silently into the warehouse.
--
-- Publish-time validation (api.survey_engine.validation._validate_construct_tags)
-- catches the obvious authoring shapes and reaches the editor as a 422; this
-- singular test backstops a backdoor insert / direct dim_question patch and
-- pins the warehouse-side invariant. Passes when it returns zero rows.

select
    question_id,
    survey_id,
    stable_name,
    construct_block,
    construct_item
from {{ ref('dim_question') }}
where construct_item is not null
    and construct_block is null
