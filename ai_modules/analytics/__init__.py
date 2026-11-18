"""District analytics: scheme coverage (eligible vs enrolled), under-served areas, Plotly report.

Lightweight entry point for the backend (stdlib only)::

    from ai_modules.analytics import load_outputs
    outputs = load_outputs()          # data/processed/analytics/ (or $ANALYTICS_OUTPUT_DIR)
    outputs.districts["items"]

Generate the outputs with ``python scripts/run_district_analytics.py [--engine pandas|spark]``.
The bundled dataset is SYNTHETIC (see data/raw/census/README.md); every output carries ``is_synthetic``.
"""

from ai_modules.analytics.outputs import (
    DEFAULT_OUTPUT_DIR,
    AnalyticsOutputs,
    OutputsNotFoundError,
    load_outputs,
)

__all__ = ["DEFAULT_OUTPUT_DIR", "AnalyticsOutputs", "OutputsNotFoundError", "load_outputs"]
