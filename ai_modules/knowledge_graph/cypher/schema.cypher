// Knowledge-graph schema for Neo4j 5 Community. Idempotent (IF NOT EXISTS); applied by
// graph_builder.apply_schema() and scripts/load_knowledge_graph.py. One statement per ';'.
// Citizen nodes are keyed by an opaque random id only: no name, Aadhaar or Samagra numbers.

CREATE CONSTRAINT scheme_id_unique IF NOT EXISTS FOR (n:Scheme) REQUIRE n.scheme_id IS UNIQUE;
CREATE CONSTRAINT condition_key_unique IF NOT EXISTS FOR (n:Condition) REQUIRE n.key IS UNIQUE;
CREATE CONSTRAINT benefit_key_unique IF NOT EXISTS FOR (n:Benefit) REQUIRE n.key IS UNIQUE;
CREATE CONSTRAINT document_type_id_unique IF NOT EXISTS FOR (n:DocumentType) REQUIRE n.doc_id IS UNIQUE;
CREATE CONSTRAINT field_name_unique IF NOT EXISTS FOR (n:Field) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT region_id_unique IF NOT EXISTS FOR (n:Region) REQUIRE n.region_id IS UNIQUE;
CREATE CONSTRAINT citizen_id_unique IF NOT EXISTS FOR (n:Citizen) REQUIRE n.citizen_id IS UNIQUE;
CREATE CONSTRAINT graph_meta_id_unique IF NOT EXISTS FOR (n:GraphMeta) REQUIRE n.id IS UNIQUE;

CREATE INDEX scheme_level IF NOT EXISTS FOR (n:Scheme) ON (n.level);
CREATE INDEX condition_scheme IF NOT EXISTS FOR (n:Condition) ON (n.scheme_id);
CREATE INDEX region_kind IF NOT EXISTS FOR (n:Region) ON (n.kind);
