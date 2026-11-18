"""Apply the Neo4j schema and seed the knowledge graph from the current scheme rules.

Idempotent: running it twice leaves the graph unchanged; entities from older rule versions are pruned.

    .venv/Scripts/python.exe scripts/load_knowledge_graph.py [--rules-dir DIR] [--districts GEOJSON]

Connection: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE (see neo4j_client.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_modules.knowledge_graph import (  # noqa: E402
    Neo4jClient,
    Neo4jQueries,
    Neo4jSettings,
    apply_schema,
    build_graph_spec,
    load_districts,
    seed_graph,
    unavailable_reason,
)
from ai_modules.rule_engine import DEFAULT_RULES_DIR, RuleEngine  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rules-dir", type=Path, default=DEFAULT_RULES_DIR)
    parser.add_argument("--districts", type=Path, default=None, help="MP districts GeoJSON")
    args = parser.parse_args(argv)

    settings = Neo4jSettings.from_env()
    reason = unavailable_reason(settings, timeout=settings.connect_timeout)
    if reason:
        print(f"Neo4j at {settings.uri} is not reachable: {reason}", file=sys.stderr)
        return 1

    rules = RuleEngine(args.rules_dir).schemes()  # one version per scheme, effective today
    districts = load_districts(args.districts)
    if not districts:
        print("warning: no district list found; seeding without District nodes", file=sys.stderr)
    spec = build_graph_spec(rules, districts)

    with Neo4jClient(settings) as client:
        print(f"Applied {apply_schema(client)} schema statements")
        seed_graph(client, spec)
        counts = Neo4jQueries(client).counts()

    print(f"Seeded {len(rules)} schemes, {len(districts)} districts (run {spec.run_id[:12]})")
    print("Nodes:")
    for label, count in counts.nodes.items():
        print(f"  {label:<14} {count}")
    print("Relationships:")
    for rel, count in counts.relationships.items():
        print(f"  {rel:<24} {count}")
    print(f"Total: {counts.total_nodes} nodes, {counts.total_relationships} relationships")

    expected = spec.counts()
    if counts != expected:
        print(f"Mismatch with the expected graph: {expected.to_dict()}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
