"""Run the district scheme-coverage analytics and write data/processed/analytics/.

Examples (from the repository root)::

    python scripts/run_district_analytics.py                    # pandas "lite" engine (default)
    python scripts/run_district_analytics.py --engine spark     # PySpark local mode (needs JAVA_HOME)
    python scripts/run_district_analytics.py --regenerate --seed 7

The input dataset is SYNTHETIC demonstration data, generated deterministically when absent.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_modules.analytics import synthetic  # noqa: E402
from ai_modules.analytics.coverage_job import resolve_engine  # noqa: E402
from ai_modules.analytics.outputs import default_output_dir  # noqa: E402
from ai_modules.analytics.pipeline import ensure_synthetic_dataset, run_pipeline  # noqa: E402
from ai_modules.analytics.reference import DEFAULT_GEOJSON, load_districts, load_scheme_catalog  # noqa: E402
from ai_modules.analytics.underserved_detection import UnderservedCriteria  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--engine", choices=("pandas", "spark"), default=None, help="default: $ANALYTICS_ENGINE or pandas"
    )
    parser.add_argument("--input-dir", type=Path, default=synthetic.DEFAULT_INPUT_DIR)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="default: $ANALYTICS_OUTPUT_DIR or data/processed/analytics",
    )
    parser.add_argument("--regenerate", action="store_true", help="rebuild the synthetic input dataset")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=f"synthetic seed (default $SYNTHETIC_SEED or {synthetic.DEFAULT_SEED})",
    )
    parser.add_argument("--no-report", action="store_true", help="skip the Plotly HTML report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    engine = resolve_engine(args.engine)
    output_dir = args.output_dir or default_output_dir()
    seed = (
        args.seed if args.seed is not None else int(os.environ.get("SYNTHETIC_SEED", synthetic.DEFAULT_SEED))
    )
    criteria = UnderservedCriteria.from_env()
    total_start = time.perf_counter()

    schemes = load_scheme_catalog()
    districts = load_districts(DEFAULT_GEOJSON)
    start = time.perf_counter()
    reason = ensure_synthetic_dataset(
        args.input_dir, districts, schemes, synthetic.SyntheticConfig(seed=seed), regenerate=args.regenerate
    )
    if reason:
        elapsed = time.perf_counter() - start
        print(f"Generated SYNTHETIC dataset in {args.input_dir} ({reason}) in {elapsed:.2f}s")
    else:
        print(f"Reusing SYNTHETIC dataset in {args.input_dir} (use --regenerate to rebuild)")

    if engine == "spark":
        from ai_modules.analytics.spark_session import SparkSettings

        settings = SparkSettings.from_env()
        print(
            f"Spark settings: master={settings.master} driver_memory={settings.driver_memory} "
            f"shuffle_partitions={settings.shuffle_partitions} ui=disabled "
            f"JAVA_HOME={os.environ.get('JAVA_HOME', '<unset: using java on PATH>')}"
        )
    else:
        print("Engine: pandas (in-process, lowest memory)")

    run = run_pipeline(
        args.input_dir,
        schemes,
        engine=engine,
        output_dir=output_dir,
        criteria=criteria,
        report=not args.no_report,
    )
    summary = run.documents["summary"]
    print(
        f"[{summary['dataset_label']}] districts={summary['districts']} blocks={summary['blocks']} "
        f"villages={summary['villages']} eligible={summary['eligible']} enrolled={summary['enrolled']} "
        f"gap={summary['gap']} coverage={summary['coverage_pct']}% "
        f"underserved(d/b/v)={summary['underserved']['districts']}/{summary['underserved']['blocks']}/"
        f"{summary['underserved']['villages']} rejected_rows={summary['rejected_rows']}"
    )
    for step, seconds in run.timings.items():
        print(f"  {step}: {seconds:.2f}s")
    print(f"Total {time.perf_counter() - total_start:.2f}s. Outputs: {run.output_dir}")
    if run.report_path:
        print(f"Report: {run.report_path}")
    if engine == "spark":
        from pyspark.sql import SparkSession

        active = SparkSession.getActiveSession()
        if active is not None:
            active.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
