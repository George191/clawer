-- Synthetic source only. Provision as a deployment-owned role in an isolated PostgreSQL.
-- Run once with psql --single-transaction; do NOT use the saved reader role as owner.
CREATE SCHEMA batch_sync_stream;
REVOKE ALL ON SCHEMA batch_sync_stream FROM PUBLIC;

CREATE TABLE batch_sync_stream.stream_head (
    singleton boolean PRIMARY KEY CHECK (singleton),
    ordinal bigint NOT NULL CHECK (ordinal >= 0)
);
INSERT INTO batch_sync_stream.stream_head VALUES (true, 0);

CREATE TABLE batch_sync_stream.batches (
    ordinal bigint PRIMARY KEY CHECK (ordinal > 0),
    row_count integer NOT NULL CHECK (row_count BETWEEN 1 AND 50),
    manifest text NOT NULL CHECK (manifest ~ '^[0-9a-f]{64}$')
);
CREATE TABLE batch_sync_stream.batch_rows (
    ordinal bigint NOT NULL REFERENCES batch_sync_stream.batches,
    id bigint NOT NULL,
    value text NOT NULL,
    PRIMARY KEY (ordinal, id)
);

CREATE FUNCTION batch_sync_stream.publish(payload jsonb)
RETURNS bigint
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path TO pg_catalog
AS $publish$
DECLARE
    next_ordinal bigint;
    input_count integer;
    distinct_ids integer;
    byte_size bigint;
    manifest_hash text;
BEGIN
    IF current_setting('batch_sync_stream.published', true) = 'yes' THEN
        RAISE EXCEPTION 'Publish at most one synthetic batch per source transaction' USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(payload) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Expected an array of synthetic after-images' USING ERRCODE = '22023';
    END IF;
    input_count := jsonb_array_length(payload);
    IF input_count NOT BETWEEN 1 AND 50 THEN
        RAISE EXCEPTION 'Synthetic after-image count is outside the publication limit' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(payload) AS item(value)
        WHERE CASE WHEN jsonb_typeof(item.value) = 'object'
            THEN NOT (item.value ?& ARRAY['id','value'])
                 OR (SELECT count(*) FROM jsonb_object_keys(item.value)) <> 2
                 OR jsonb_typeof(item.value->'id') NOT IN ('number','string')
                 OR (item.value->>'id') !~ '^-?(0|[1-9][0-9]{0,18})$'
                 OR jsonb_typeof(item.value->'value') <> 'string'
            ELSE true END
    ) THEN
        RAISE EXCEPTION 'Unsupported synthetic after-image' USING ERRCODE = '22023';
    END IF;
    SELECT count(DISTINCT id), sum(octet_length(convert_to(value, 'UTF8'))::bigint + 8)
    INTO distinct_ids, byte_size
    FROM (
        SELECT (item->>'id')::bigint AS id, item->>'value' AS value FROM jsonb_array_elements(payload) AS item
    ) entries;
    IF distinct_ids <> input_count OR byte_size > 65536 THEN
        RAISE EXCEPTION 'Synthetic after-images have duplicate keys or exceed 64 KiB' USING ERRCODE = '22023';
    END IF;
    SELECT encode(sha256(convert_to(
        string_agg(id::text || ':' || encode(convert_to(value, 'UTF8'), 'hex'), ';' ORDER BY id),
        'UTF8')), 'hex')
    INTO manifest_hash FROM (
        SELECT (item->>'id')::bigint AS id, item->>'value' AS value FROM jsonb_array_elements(payload) AS item
    ) entries;

    -- A row lock, not a sequence: a rolled back transaction leaves no ordinal gap.
    SELECT ordinal + 1 INTO STRICT next_ordinal FROM batch_sync_stream.stream_head WHERE singleton FOR UPDATE;
    INSERT INTO batch_sync_stream.batches VALUES (next_ordinal, input_count, manifest_hash);
    INSERT INTO batch_sync_stream.batch_rows (ordinal, id, value)
        SELECT next_ordinal, (item->>'id')::bigint, item->>'value'
        FROM jsonb_array_elements(payload) AS item;
    UPDATE batch_sync_stream.stream_head SET ordinal = next_ordinal WHERE singleton;
    PERFORM set_config('batch_sync_stream.published', 'yes', true);
    RETURN next_ordinal;
END
$publish$;
REVOKE ALL ON FUNCTION batch_sync_stream.publish(jsonb) FROM PUBLIC;
