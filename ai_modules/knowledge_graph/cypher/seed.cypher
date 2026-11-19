// Seeding statements used by graph_builder.seed_graph(). Every statement is parametrised:
//   $rows  list of maps produced by graph_builder.build_graph_spec() (node: {key, props};
//          relationship: {start, end, props})
//   $run   deterministic hash of the whole graph spec; stamped on every seeded node/relationship
// Each block starts with "// @name <statement>" and ends with ';'. Re-running with the same rules
// leaves the graph unchanged (MERGE + SET); entities from older rule versions are pruned by
// prune_nodes / prune_relationships because their seed_run differs.

// ---------- Reference data: document types, profile fields, regions ----------

// @name node_DocumentType
UNWIND $rows AS row
MERGE (n:DocumentType {doc_id: row.key})
SET n += row.props, n.seed_run = $run;

// @name node_Field
UNWIND $rows AS row
MERGE (n:Field {name: row.key})
SET n += row.props, n.seed_run = $run;

// @name node_Region_country
UNWIND $rows AS row
MERGE (n:Region {region_id: row.key})
SET n:Country, n += row.props, n.seed_run = $run;

// @name node_Region_state
UNWIND $rows AS row
MERGE (n:Region {region_id: row.key})
SET n:State, n += row.props, n.seed_run = $run;

// @name node_Region_district
UNWIND $rows AS row
MERGE (n:Region {region_id: row.key})
SET n:District, n += row.props, n.seed_run = $run;

// @name rel_PROVIDES_FIELD
UNWIND $rows AS row
MATCH (a:DocumentType {doc_id: row.start}), (b:Field {name: row.end})
MERGE (a)-[r:PROVIDES_FIELD]->(b)
SET r += row.props, r.seed_run = $run;

// @name rel_PART_OF
UNWIND $rows AS row
MATCH (a:Region {region_id: row.start}), (b:Region {region_id: row.end})
MERGE (a)-[r:PART_OF]->(b)
SET r += row.props, r.seed_run = $run;

// ---------- Scheme rules ----------

// @name node_Scheme
UNWIND $rows AS row
MERGE (n:Scheme {scheme_id: row.key})
SET n += row.props, n.seed_run = $run;

// @name node_Condition
UNWIND $rows AS row
MERGE (n:Condition {key: row.key})
SET n += row.props, n.seed_run = $run;

// @name node_Benefit
UNWIND $rows AS row
MERGE (n:Benefit {key: row.key})
SET n += row.props, n.seed_run = $run;

// @name rel_HAS_CONDITION
UNWIND $rows AS row
MATCH (a:Scheme {scheme_id: row.start}), (b:Condition {key: row.end})
MERGE (a)-[r:HAS_CONDITION]->(b)
SET r += row.props, r.seed_run = $run;

// @name rel_HAS_PART
UNWIND $rows AS row
MATCH (a:Condition {key: row.start}), (b:Condition {key: row.end})
MERGE (a)-[r:HAS_PART]->(b)
SET r += row.props, r.seed_run = $run;

// @name rel_ON_FIELD
UNWIND $rows AS row
MATCH (a:Condition {key: row.start}), (b:Field {name: row.end})
MERGE (a)-[r:ON_FIELD]->(b)
SET r += row.props, r.seed_run = $run;

// @name rel_RESOLVED_BY
UNWIND $rows AS row
MATCH (a:Condition {key: row.start}), (b:DocumentType {doc_id: row.end})
MERGE (a)-[r:RESOLVED_BY]->(b)
SET r += row.props, r.seed_run = $run;

// @name rel_REQUIRES_DOCUMENT
UNWIND $rows AS row
MATCH (a:Scheme {scheme_id: row.start}), (b:DocumentType {doc_id: row.end})
MERGE (a)-[r:REQUIRES_DOCUMENT]->(b)
SET r += row.props, r.seed_run = $run;

// @name rel_PROVIDES
UNWIND $rows AS row
MATCH (a:Scheme {scheme_id: row.start}), (b:Benefit {key: row.end})
MERGE (a)-[r:PROVIDES]->(b)
SET r += row.props, r.seed_run = $run;

// @name rel_AVAILABLE_IN
UNWIND $rows AS row
MATCH (a:Scheme {scheme_id: row.start}), (b:Region {region_id: row.end})
MERGE (a)-[r:AVAILABLE_IN]->(b)
SET r += row.props, r.seed_run = $run;

// @name rel_COMPLEMENTED_BY
UNWIND $rows AS row
MATCH (a:Scheme {scheme_id: row.start}), (b:Scheme {scheme_id: row.end})
MERGE (a)-[r:COMPLEMENTED_BY]->(b)
SET r += row.props, r.seed_run = $run;

// @name rel_TOPS_UP
UNWIND $rows AS row
MATCH (a:Scheme {scheme_id: row.start}), (b:Scheme {scheme_id: row.end})
MERGE (a)-[r:TOPS_UP]->(b)
SET r += row.props, r.seed_run = $run;

// @name rel_PREREQUISITE_FOR
UNWIND $rows AS row
MATCH (a:Scheme {scheme_id: row.start}), (b:Scheme {scheme_id: row.end})
MERGE (a)-[r:PREREQUISITE_FOR]->(b)
SET r += row.props, r.seed_run = $run;

// @name rel_MUTUALLY_EXCLUSIVE_WITH
UNWIND $rows AS row
MATCH (a:Scheme {scheme_id: row.start}), (b:Scheme {scheme_id: row.end})
MERGE (a)-[r:MUTUALLY_EXCLUSIVE_WITH]->(b)
SET r += row.props, r.seed_run = $run;

// ---------- Pruning of entities left over from previous rule versions ----------

// @name prune_relationships
MATCH ()-[r]->()
WHERE type(r) IN $types AND coalesce(r.seed_run, '') <> $run
DELETE r;

// @name prune_nodes
MATCH (n)
WHERE any(label IN labels(n) WHERE label IN $labels) AND coalesce(n.seed_run, '') <> $run
DETACH DELETE n;

// @name set_meta
MERGE (m:GraphMeta {id: 'seed'})
SET m.seed_run = $run, m.scheme_count = $scheme_count;

// @name get_meta
MATCH (m:GraphMeta {id: 'seed'})
RETURN m.seed_run AS seed_run;
