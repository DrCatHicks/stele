"""Run the ETL: wrap ``dbt build``, log the run to ops.etl_runs, archive artifacts.

The operational entry point for design doc §3.7 / FR-11. Thin wrapper over
:func:`api.etl.runner.run_etl`; the logic lives there (and is unit-tested). M6.3
wires this behind ``make etl``.

Connection. Reads ``STELE_ETL_DATABASE_URL`` (the stele_etl role — the same role
dbt uses). For local dev where it's unset, opt into the fallback explicitly with
``STELE_ALLOW_DEV_FALLBACK=1``.

Examples:
    uv run python scripts/run_etl.py
    uv run python scripts/run_etl.py -- --select dim_question   # pass-through to dbt
"""

from __future__ import annotations

import argparse
import sys

from api.etl.runner import run_etl


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "dbt_args",
        nargs="*",
        help="Extra arguments passed through to `dbt build` (e.g. --select dim_question).",
    )
    args = parser.parse_args(argv)
    return run_etl(extra_args=args.dbt_args or None)


if __name__ == "__main__":
    sys.exit(main())
