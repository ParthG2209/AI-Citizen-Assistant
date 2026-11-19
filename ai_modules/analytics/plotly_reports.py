"""Self-contained Plotly HTML report for the district analytics outputs (works offline; plotly.js is inlined).

Charts: district coverage (with the under-served threshold), eligible vs enrolled per district,
scheme adoption, and a map of district centroids over the MP district outlines. Every chart title and the
page banner carry the SYNTHETIC label when the dataset is synthetic.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from ai_modules.analytics.reference import DEFAULT_GEOJSON


def _district_outlines(geojson_path: Path) -> tuple[list[float | None], list[float | None]]:
    """Polygon rings as one line trace (None separates rings); Point-only districts are skipped."""
    with Path(geojson_path).open(encoding="utf-8") as fh:
        collection = json.load(fh)
    lats: list[float | None] = []
    lngs: list[float | None] = []
    for feature in collection["features"]:
        geometry = feature["geometry"]
        if geometry["type"] == "Polygon":
            polygons = [geometry["coordinates"]]
        elif geometry["type"] == "MultiPolygon":
            polygons = geometry["coordinates"]
        else:
            continue
        for polygon in polygons:
            for ring in polygon:
                lngs += [point[0] for point in ring] + [None]
                lats += [point[1] for point in ring] + [None]
    return lats, lngs


def build_figures(documents: dict[str, Any], geojson_path: Path = DEFAULT_GEOJSON) -> list:
    import plotly.graph_objects as go

    summary = documents["summary"]
    tag = " — SYNTHETIC DATA" if summary["is_synthetic"] else ""
    threshold = documents["metadata"]["thresholds"]["max_coverage_pct"]
    districts = sorted(
        documents["districts"]["items"], key=lambda d: (d["coverage_pct"] is None, d["coverage_pct"] or 0)
    )
    names = [d["name"] for d in districts]

    coverage = go.Figure(
        go.Bar(
            x=names,
            y=[d["coverage_pct"] for d in districts],
            marker_color=["#c0392b" if d["underserved"] else "#2e86c1" for d in districts],
            text=[f"{d['coverage_pct']}%" if d["coverage_pct"] is not None else "n/a" for d in districts],
            textposition="outside",
            hovertext=[
                f"gap {d['gap']:,} · under-served: {'yes' if d['underserved'] else 'no'}" for d in districts
            ],
        )
    )
    coverage.add_hline(
        y=threshold,
        line_dash="dash",
        annotation_text=f"under-served threshold {threshold}%",
        annotation_position="top left",
    )
    coverage.update_layout(
        title=f"Coverage by district (enrolled / eligible){tag}",
        yaxis_title="coverage %",
        yaxis_range=[0, 105],
    )

    volumes = go.Figure(
        [
            go.Bar(name="Eligible", x=names, y=[d["eligible"] for d in districts], marker_color="#95a5a6"),
            go.Bar(name="Enrolled", x=names, y=[d["enrolled"] for d in districts], marker_color="#27ae60"),
        ]
    )
    volumes.update_layout(
        barmode="group", title=f"Eligible vs enrolled by district{tag}", yaxis_title="assessments"
    )

    schemes = sorted(documents["scheme_adoption"]["items"], key=lambda s: s["eligible"], reverse=True)
    adoption = go.Figure(
        [
            go.Bar(
                name="Eligible",
                x=[s["name"] for s in schemes],
                y=[s["eligible"] for s in schemes],
                marker_color="#95a5a6",
            ),
            go.Bar(
                name="Enrolled",
                x=[s["name"] for s in schemes],
                y=[s["enrolled"] for s in schemes],
                marker_color="#8e44ad",
                text=[f"{s['coverage_pct']}%" if s["coverage_pct"] is not None else "n/a" for s in schemes],
                textposition="outside",
            ),
        ]
    )
    adoption.update_layout(
        barmode="group",
        title=f"Scheme adoption (statewide sample){tag}",
        yaxis_title="assessments",
        height=650,
    )

    outline_lats, outline_lngs = _district_outlines(geojson_path)
    max_eligible = max([d["eligible"] for d in districts] or [1]) or 1
    geo_map = go.Figure(
        [
            go.Scattergeo(
                lat=outline_lats,
                lon=outline_lngs,
                mode="lines",
                line={"width": 0.6, "color": "#7f8c8d"},
                hoverinfo="skip",
                name="District boundaries (DataMeet, CC BY 2.5 IN)",
            ),
            go.Scattergeo(
                lat=[d["position"][0] for d in districts],
                lon=[d["position"][1] for d in districts],
                text=[
                    f"{d['name']}<br>coverage {d['coverage_pct']}%<br>eligible {d['eligible']:,} · "
                    f"enrolled {d['enrolled']:,} · gap {d['gap']:,}"
                    for d in districts
                ],
                hoverinfo="text",
                mode="markers",
                marker={
                    "size": [12 + 28 * d["eligible"] / max_eligible for d in districts],
                    "color": [d["coverage_pct"] for d in districts],
                    "colorscale": "RdYlGn",
                    "cmin": 0,
                    "cmax": 100,
                    "colorbar": {"title": "coverage %"},
                    "line": {"width": [3 if d["underserved"] else 0.5 for d in districts], "color": "#000"},
                },
                name="Districts (size = eligible)",
            ),
        ]
    )
    geo_map.update_geos(fitbounds="locations", visible=False, projection_type="mercator")
    geo_map.update_layout(
        title=f"District coverage map (thick outline = under-served){tag}",
        height=600,
        legend={"orientation": "h", "y": -0.05},
    )
    return [coverage, volumes, adoption, geo_map]


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def write_report(documents: dict[str, Any], path: Path, geojson_path: Path = DEFAULT_GEOJSON) -> Path:
    """Write the HTML report and return its path."""
    summary, metadata = documents["summary"], documents["metadata"]
    figures = build_figures(documents, geojson_path)
    charts = "".join(
        fig.to_html(full_html=False, include_plotlyjs=(i == 0), default_width="100%")
        for i, fig in enumerate(figures)
    )
    banner = (
        f"<div class='banner'>SYNTHETIC DEMONSTRATION DATA ({html.escape(summary['dataset_label'])}) — not "
        "government statistics. Only district names and centroids are real.</div>"
        if summary["is_synthetic"]
        else ""
    )
    kpis = _table(
        [
            "Districts",
            "Blocks",
            "Villages",
            "Eligible",
            "Enrolled",
            "Gap",
            "Coverage %",
            "Avg district coverage %",
            "Under-served districts / blocks / villages",
        ],
        [
            [
                summary["districts"],
                summary["blocks"],
                summary["villages"],
                f"{summary['eligible']:,}",
                f"{summary['enrolled']:,}",
                f"{summary['gap']:,}",
                summary["coverage_pct"],
                summary["avg_district_coverage_pct"],
                "{districts} / {blocks} / {villages}".format(**summary["underserved"]),
            ]
        ],
    )
    underserved_rows = [
        [d["name"], b["name"], b["coverage_pct"], f"{b['gap']:,}"]
        for d in documents["districts"]["items"]
        for b in d["underserved_blocks"]
    ]
    docs_rows = [
        [d["name"], ", ".join(f"{doc['label']} ({doc['citizens']:,})" for doc in d["top_missing_documents"])]
        for d in documents["districts"]["items"]
    ]
    thresholds = metadata["thresholds"]
    title_tag = " (SYNTHETIC)" if summary["is_synthetic"] else ""
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>District analytics report{title_tag}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; color: #222; }}
.banner {{ background: #fdecea; border: 2px solid #c0392b; padding: 12px; font-weight: 600;
  margin-bottom: 16px; }}
table {{ border-collapse: collapse; margin: 8px 0 24px; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; }}
small {{ color: #555; }}
</style></head><body>
{banner}
<h1>District scheme coverage report</h1>
<p><small>Generated {html.escape(summary["generated_at"])} · engine {html.escape(summary["engine"])} ·
under-served rule: coverage &lt; {thresholds["max_coverage_pct"]}% and
gap ≥ {thresholds["min_gap_district"]} (district),
{thresholds["min_gap_block"]} (block), {thresholds["min_gap_village"]} (village) ·
{html.escape(metadata["geo_attribution"])}</small></p>
{kpis}
{charts}
<h2>Under-served blocks</h2>
{_table(["District", "Block", "Coverage %", "Gap"], underserved_rows) if underserved_rows else "<p>None.</p>"}
<h2>Top missing documents by district</h2>
{_table(["District", "Documents (citizens lacking them)"], docs_rows)}
</body></html>
"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path
