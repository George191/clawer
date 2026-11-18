# Read-only table export snapshot preview receiver

This local sandbox profile is distinct from both export writer receivers and the sender. It is off by default. No application migration provisions it. Use an isolated UTF-8 PostgreSQL listener on its own numeric loopback port and a dedicated database and role. Do not reuse an old receiver's or the sender's port, database, role or marker. The client sends no sender values, IDs or digests to this listener.

As the owner of the isolated preview database, provision exactly these permanent PostgreSQL heaps and native B-tree primary keys:

```sql
CREATE SCHEMA ts_table_export_snapshot_receiver;
CREATE TABLE ts_table_export_snapshot_receiver.identity (marker uuid PRIMARY KEY);
CREATE TABLE ts_table_export_snapshot_receiver.records (business_key bigint PRIMARY KEY, caption text);
-- Insert exactly one identity row with a new random marker UUID.
```

Set up an independent, non-owning LOGIN role (no memberships, elevated role attributes or RLS bypass). Revoke public database privileges (including TEMP); grant only CONNECT to this role, USAGE on the fixed schema and SELECT on its two tables. Do not grant the role or PUBLIC schema creation, table maintenance or mutation privileges. Use the separate owner to load synthetic rows. No other indexes, constraints, defaults, triggers, rules, RLS, partitioning or inheritance belong on either table.

To compute the physical pin, connect as the enrollment owner, use `search_path=pg_catalog`, begin a read-only transaction and take `LOCK TABLE ONLY ts_table_export_snapshot_receiver.identity, ONLY ts_table_export_snapshot_receiver.records IN ACCESS SHARE MODE NOWAIT`. In that transaction, call `await compute_snapshot_relation_proof(connection)` from `app.web.services.table_export_snapshot_receiver`. The returned SHA-256 includes the exact schema, heap and index identities and physical file nodes. Set it as `TABLE_EXPORT_SNAPSHOT_PREVIEW_PROOF_SHA256`. An index rebuild, table rewrite or replacement requires deliberate re-enrollment with the new pin and a new deployment UUID. Configure that UUID as `TABLE_EXPORT_SNAPSHOT_PREVIEW_CONFIG_REVISION` (distinct from the marker). The app exposes a separate opaque scope revision, not the raw deployment UUID, pin, or marker.

After configuring the remaining `TABLE_EXPORT_SNAPSHOT_PREVIEW_*` settings, explicit local mode and existing export sandbox must also be enabled. Maximums are 100 rows, 8 KiB of UTF-8 per caption and 64 KiB total (caption bytes plus eight bytes per bigint; null captions count zero). Larger samples are refused, not truncated. The preview makes no writes, tickets or exports.
