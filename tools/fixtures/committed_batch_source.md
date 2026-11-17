# Local synthetic committed-batch source

Provision in a dedicated UTF-8 PostgreSQL database on numeric loopback. Do not
install the fixture on a business, production, or existing CDC database. Run
`psql --single-transaction --file tools/fixtures/committed_batch_source.sql`
as the trusted deployment owner. The fixture deliberately creates no login,
exposes no app producer endpoint, and changes no existing tables or schedules.

Use the same operator-provisioned `batch_sync_identity.source_identity` marker
and separate non-superuser, non-owner saved connection role as the sandbox
PostgreSQL source-identity pilot. Substitute that pre-existing role below:

```sql
GRANT USAGE ON SCHEMA batch_sync_stream TO saved_fixture_reader;
GRANT SELECT ON batch_sync_stream.stream_head,
    batch_sync_stream.batches, batch_sync_stream.batch_rows TO saved_fixture_reader;
```

Do not grant write privileges, role membership in the owner, schema CREATE,
or publish-function access to the saved reader. Keep the marker out of scripts,
logs, UI payloads, and tracked files. The existing identity schema requires
USAGE and SELECT on its identity table for the same role.

As the trusted fixture owner, publish **one** synthetic batch per transaction
with a bound JSON value:

```sql
BEGIN;
SELECT batch_sync_stream.publish('[{"id":7,"value":"demo"}]'::jsonb);
COMMIT;
```

Returned ordinals are provisional until COMMIT. Rollback leaves no visible
ordinal. Publish accepts 1–50 unique signed bigint keys and UTF-8 values with
a combined budget of 64 KiB (UTF-8 bytes plus eight per key). Large bigint
keys can be passed as decimal strings. The fixture supports after-images only,
with no deletes and no owner updates to prior publications. A verified source
is not a continuous health certificate; the reader rechecks identity and
physical bindings on every read. A copied operator marker or an owner who
manually rewrites the fixture is not a trusted source publisher.
