-- Row-count parity (design-doc §3.7): every closed-ended *selection* in the raw
-- payload yields exactly one fact_response_item row carrying an option_key. A
-- single-select answer is one selection; a multi-select (checkbox) answer is one
-- selection per array element, fanned out in fact_response_item — so the raw side
-- must count array elements, not answers, or the fan-out would read as a
-- mismatch. int_response_answers is derived 1:1 from the raw payload, so it is
-- the faithful count of raw selections for the slice. A mismatch means either
-- fan-out is wrong or an answer failed to resolve to a defined option. Free-text
-- answers resolve to value_text, not option_key, so they are excluded here (their
-- routing is covered by free_text_redaction_parity).
-- Passes when it returns zero rows.

with raw_selections as (
    select coalesce(sum(
        case
            -- multi-select fans out to one row per chosen option; an answered
            -- checkbox with a non-array / empty answer contributes no option rows.
            when question_type = 'checkbox' then
                case
                    when answered and jsonb_typeof(answer_json) = 'array'
                        then jsonb_array_length(answer_json)
                    else 0
                end
            -- single-select: one option row per answered question.
            when answered and question_type not in ('text', 'comment') then 1
            else 0
        end
    ), 0) as n
    from {{ ref('int_response_answers') }}
),

fact_selections as (
    select count(*) as n
    from {{ ref('fact_response_item') }}
    where option_key is not null
)

select raw_selections.n as raw_n, fact_selections.n as fact_n
from raw_selections, fact_selections
where raw_selections.n != fact_selections.n
