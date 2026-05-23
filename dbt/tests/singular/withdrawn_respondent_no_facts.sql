-- Withdrawal propagation (design-doc §3.8, M2.3): a withdrawn respondent must
-- leave no trace in the warehouse. The tombstone workflow NULLs the content
-- columns (incl. definition_snapshot) on the respondent's raw_responses rows;
-- stg_raw_responses then drops them via `where definition_snapshot is not null`,
-- so they never reach the marts. This test proves that filter is load-bearing:
-- every fact_response_item row must trace back to a (respondent, survey_version)
-- still present in staging. A fact whose pair is absent from staging would be a
-- withdrawn (or otherwise tombstoned) respondent leaking through — the inverse
-- of version_coverage. dbt can't see pii.withdrawals (stele_etl has no pii
-- access), so the staging filter, not a join to the withdrawal record, is the
-- mechanism under test. Passes when it returns zero rows.

with fact_pairs as (
    select distinct respondent_id, survey_version_id
    from {{ ref('fact_response_item') }}
),

raw_pairs as (
    select distinct
        respondent_id,
        {{ surrogate_key(['survey_id', 'survey_version']) }} as survey_version_id
    from {{ ref('stg_raw_responses') }}
)

select fp.respondent_id, fp.survey_version_id
from fact_pairs as fp
left join raw_pairs as rp
    on fp.respondent_id = rp.respondent_id
    and fp.survey_version_id = rp.survey_version_id
where rp.respondent_id is null
