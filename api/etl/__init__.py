"""ETL operational tooling (design doc §3.7).

The runner that wraps ``dbt build``, records each invocation in ``ops.etl_runs``,
and archives dbt's artifacts. See :mod:`api.etl.runner`.
"""
