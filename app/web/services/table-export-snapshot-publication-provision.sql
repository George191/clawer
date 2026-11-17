-- psql -v ON_ERROR_STOP=1 -v snapshot_writer=dedicated_role -v snapshot_marker=PRIVATE-UUID -f this_file.sql
-- Use a NEW disposable local PostgreSQL 17+ UTF8 database, separately owned. No destructive DDL, teardown, or retention.
BEGIN;
CREATE SCHEMA ts_table_export_snapshot_publication;
CREATE TABLE ts_table_export_snapshot_publication.state (
    singleton boolean PRIMARY KEY CHECK (singleton),
    marker uuid NOT NULL,
    current_generation bigint NOT NULL CHECK (current_generation >= 0)
);
CREATE TABLE ts_table_export_snapshot_publication.generations (
    generation bigint PRIMARY KEY,
    previous_generation bigint NOT NULL,
    request_id uuid NOT NULL UNIQUE,
    payload_hash text NOT NULL,
    row_count integer NOT NULL,
    recorded_at timestamptz NOT NULL
);
CREATE TABLE ts_table_export_snapshot_publication.records (
    generation bigint NOT NULL,
    id bigint NOT NULL,
    caption text,
    PRIMARY KEY (generation,id)
);
INSERT INTO ts_table_export_snapshot_publication.state VALUES (true, :'snapshot_marker'::uuid, 0);
GRANT USAGE ON SCHEMA ts_table_export_snapshot_publication TO :"snapshot_writer";
GRANT SELECT ON ts_table_export_snapshot_publication.state TO :"snapshot_writer";
GRANT UPDATE (current_generation) ON ts_table_export_snapshot_publication.state TO :"snapshot_writer";
GRANT SELECT, INSERT ON ts_table_export_snapshot_publication.generations,
    ts_table_export_snapshot_publication.records TO :"snapshot_writer";
-- PostgreSQL 17+ least-scope privilege for the DDL-excluding fixed lock.
GRANT MAINTAIN ON ts_table_export_snapshot_publication.state,
    ts_table_export_snapshot_publication.generations, ts_table_export_snapshot_publication.records
    TO :"snapshot_writer";
COMMIT;
