-- Row-count parity (design-doc §3.7): every closed-ended *selection* yields
-- exactly one fact_response_item row carrying an option_key. int_response_selections
-- is the selection-grain expansion of the raw payload (a single-select answer is
-- one selection; a multi-select fans out to one per chosen option), so a
-- closed-ended row there with a non-null option_lookup_value is exactly one raw
-- selection. Comparing that to the fact's option_key count catches a fan-out bug
-- OR an answer that failed to resolve to a defined option (option_lookup_value set
-- but no matching dim_option → counted raw, absent from fact). Free-text answers
-- resolve to value_text, not option_key, so they are excluded here (their routing
-- is covered by free_text_redaction_parity). Counting from int_response_selections
-- (not raw jsonb) keeps the test portable, matching the JSON-confinement rule.
-- Passes when it returns zero rows.

with raw_selections as (
    select count(*) as n
    from {{ ref('int_response_selections') }}
    -- value_kind = 'option' is exactly the option-bearing selections: it excludes
    -- free text (value_text) AND scalars (value_numeric/value_date, M5.5), all of
    -- which carry a non-null option_lookup_value but never resolve to an option_key.
    where option_lookup_value is not null
        and value_kind = 'option'
),

fact_selections as (
    select count(*) as n
    from {{ ref('fact_response_item') }}
    where option_key is not null
)

select raw_selections.n as raw_n, fact_selections.n as fact_n
from raw_selections, fact_selections
where raw_selections.n != fact_selections.n
