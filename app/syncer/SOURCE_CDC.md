# Continuous CDC for saved Data Sources

Open a saved source's details and use **Continuous CDC**. This section appears for
PostgreSQL, MongoDB, MySQL, Oracle and experimental Milvus. Dameng has a separate
**Log CDC · DMDRS** output-binding section. S3 sources have a separate **Object
event notifications · MinIO** section. Elasticsearch, ClickHouse and Redis expose
neither configuration. Installing dmPython enables Dameng connections, not CDC.

The API is administrator-only:

- `GET /api/etl/data-connections/{id}/cdc`: source requirements, installed connector
  availability, connector/task states and topic prefix.
- `POST /api/etl/data-connections/{id}/cdc`: create capture using the saved credentials.
  Send `expectedUpdatedAt`, explicit qualified `tables`, and `snapshot` (`initial`
  or `no_data`). PostgreSQL also requires `publication`; MySQL requires `serverId`;
  Oracle requires `oracleDatabase` and optionally `oraclePdb`.
- `POST /api/etl/data-connections/{id}/cdc/control`: send `expectedUpdatedAt` and
  `action` (`pause`, `resume`, `restart`, `remove`). Restart targets failed tasks.

Milvus also requires `milvus: {kafkaBrokers, etcdEndpoints, etcdRoot, pchannels}`.
Supply **all** single-partition pchannels used by the collections. Every pchannel
gets a separate connector, sharing the source's output namespace. The root
configuration records the complete list. If registration is interrupted, status
reports missing channels as INCOMPLETE; restart creates those channels from the
saved configuration. Remove deletes child connectors before the root, preserving
recoverability if an operation fails midway. No cluster-wide ordering is provided
across separate channel connectors; consumers must retain source TSO/version data.

One task captures the selected tables/collections of one saved connection. Names
must be explicit `schema.table` or `database.collection` identifiers using letters,
digits, underscores and dollar signs; regex selection is deliberately disallowed.
MySQL and MongoDB selections must stay inside the saved database.

## Source preparation

- PostgreSQL: enable `wal_level=logical`, allocate replication slots/WAL senders,
  grant the saved account replication and snapshot/SELECT privileges. An administrator
  must create a publication containing the selected tables before starting capture.
  Use `REPLICA IDENTITY FULL` when full before images are required. The connector
  creates its own replication slot; it does not modify publications automatically.
- MongoDB: use a replica set or sharded cluster. Grant snapshot and Change Streams
  access. The connector discovers topology from the saved seed host. Updates use
  full-document lookup, which can reflect a later concurrent write; pre-images are
  not enabled. Native change descriptions and resume tokens remain in the events.
- MySQL: configure ROW binlog, FULL row images, sufficient binlog retention and
  snapshot/replication permissions. Choose a replication `serverId` unique among
  all clients of that server. Grant snapshot locking permissions required by Debezium.
- Oracle: enable archive logging and supplemental logging and grant Debezium's
  snapshot/LogMiner privileges. Supply the CDB/non-CDB name and, for multitenant
  databases, the PDB name. The saved database field remains the service name used
  for the connection. Verified TCPS uses the Connect JVM trust store; private CAs
  must be installed there. Wallet/mTLS configuration is not provided by this form.
- Milvus: install the incubating Debezium Milvus connector (introduced in 3.7),
  and use a compatible self-hosted Milvus deployment backed by Kafka, with access
  to its etcd and gRPC endpoints. This form supports single-partition physical
  channels, unauthenticated source MQ and etcd endpoints (HTTP or HTTPS), and saved
  Milvus password/token authentication. It does not support Pulsar, RocksMQ,
  Woodpecker, Milvus Lite, or custom MQ/etcd authentication. Upserts appear as
  delete/insert pairs. The existing Compose Connect 3.4 image does not supply this
  plugin. Prepare and validate a compatible worker environment before enabling it;
  this change does not upgrade the existing production workers automatically.

The database connection-test account must also have CDC privileges to run capture.
Successful connection testing alone does not establish CDC readiness. Connector
validation runs before registration; source authorization/topology failures may
subsequently produce a FAILED task, visible in this section. Raw failure traces and
credentials are not returned to the browser; inspect protected Connect logs.

## Runtime and delivery contract

Use the existing `SPIDER_CDC_CONNECT_URL` and Kafka Connect deployment. Install the
corresponding Debezium 3.4 connector on **every worker**, including Oracle's required
JDBC driver (Milvus requires its compatible 3.7+ environment). The panel checks plugin availability and disables starting capture
when the plugin is missing. MySQL/Oracle schema-history clients use the configured
Kafka broker/security settings; referenced CA paths must also exist on Connect.
Protect the Connect REST endpoint and its internal configuration topics: connector
configuration necessarily includes database credentials.

Kafka Connect stores capture configuration and offsets in its existing durable
internal Kafka topics. No application scheduler or polling scan performs capture.
Keep those topics and their replication settings intact across restarts.

Events use Debezium's native schema-bearing JSON key/value envelopes. They retain
snapshot/read/create/update/delete operations, source positions and tombstones.
MongoDB document values use Debezium's MongoDB representation, rather than relational
row structs. Consumers must understand their connector's event representation and
apply changes idempotently: delivery is at least once. These topics are separate
from the existing internal business/ETL topics; no warehouse writes or mappings are
created implicitly.

Task selection and topic prefix remain fixed during pause, resume and worker restart.
Ensure source log retention covers any outage. Missing historical logs cause a
failure requiring an explicit new capture; there is no polling fallback or automatic
offset reset. Connector RUNNING does not alone prove that snapshotting has finished
or that a downstream consumer has applied the data.

Remove the task before editing, pausing or deleting its saved connection. The source
row lock serializes these mutations with task creation/control. If Connect cannot
be checked, source mutations fail explicitly rather than risk orphaning capture.
After a timed-out operation, refresh the status before retrying: Connect might have
accepted it before the HTTP timeout.

Removal deletes the connector but preserves Kafka data/history and PostgreSQL slots.
Drop an abandoned PostgreSQL slot explicitly after confirming it is no longer used,
otherwise it can retain WAL. A newly created task gets a new namespace and slot,
so it cannot accidentally reuse a removed task's position. New downstream consumers
must subscribe to that namespace and handle their own target initialization.

## Validation

Run `python -m unittest app.web.tests.test_source_cdc app.web.tests.test_source_capture_modes app.web.tests.test_data_connections`
with `SPIDER_CDC_CONNECT_URL` empty for isolated unit tests, and `npm run build` plus
`node --test tests/source_capture_modes.mjs tests/data_connections_feedback.mjs` in
`web-panel`. Rebuild the web image to install `dmPython==2.6.1`; the Docker build
requires the official binary wheel and checks `import dmPython` in the runtime stage.

Before production use, exercise initial snapshots with concurrent writes, inserts,
updates, deletes, pause/resume and worker restart against disposable instances of
each source database. Compare keys and final values after replay, including duplicate
delivery. Unit tests validate configuration/lifecycle contracts; they do not replace
these database/Connect integration checks.

## Dameng DMDRS: database log CDC, externally operated

DMDRS is a separate product that must be installed/licensed by the operator. Prepare
archive/logical logs, capture permissions, selected tables, initial loading and a
Kafka output in DMDRS. This application binds that existing output, rather than
starting a proprietary DMDRS process or inferring its status from an ordinary
database connection. Its Kafka topic must exist on the project's configured Kafka
cluster. Message format and table coverage must be verified in DMDRS; the binding
does not transform DMDRS events into Debezium envelopes.

Install binding storage with the normal Alembic upgrade after installing the source
registry, or run `python tools/ontology_dameng_cdc.py --check` / `--apply` when the
optional registry was installed after Alembic ran. This only creates binding storage;
it does not install DMDRS or read source business rows.

The administrator API is `GET/POST /api/etl/data-connections/{id}/dmdrs` and
`POST .../dmdrs/remove`. Bind sends `expectedUpdatedAt`, an existing Kafka `topic`
and explicit qualified `tables`. Removal sends `expectedUpdatedAt`. Binding reports
`bound`, `captureHealth=externally_managed` and `deliveryVerified=false`, never a
fabricated RUNNING state. Remove does **not** stop DMDRS or delete Kafka data. Its
log position, snapshot and start/pause/recovery controls remain with DMDRS.

## MinIO: object notifications, not row CDC

The administrator must configure a MinIO server Kafka target and its authentication,
topic and persistent delivery queue before using this form. The configured ARN has
the form `arn:minio:sqs::target-id:kafka`. The application's existing saved S3 keys
need `s3:GetBucketNotification` and `s3:PutBucketNotification`; the connection's
ordinary bucket-list permission alone is insufficient. Source edit/delete also
checks notifications to avoid orphaning a subscription, so those operations require
notification read access. No MinIO objects, template versions or object keys are
created or changed by this configuration.

`GET /api/etl/data-connections/{id}/object-events` returns the owned notification
and a hash of the bucket notification configuration. `POST` sends
`expectedUpdatedAt`, `expectedConfigurationHash`, `targetArn`, `events` and optional
`prefix`/`suffix`. Events are `s3:ObjectCreated:*` and/or `s3:ObjectRemoved:*`.
`POST .../object-events/remove` sends the two expected revision/hash fields.

Only the notification ID associated with the saved source is modified. Other queue,
topic, Lambda and EventBridge settings are preserved and verified after writing.
A stale hash rejects the write. S3's notification API replaces a whole document
without compare-and-swap, so source row locking coordinates this application's
edits, not external administrators: avoid concurrent bucket-notification writers.
If read-back verification fails, refresh and reconcile; do not blindly retry.

The result reports configured/unconfigured and `deliveryVerified=false`. Neither
means Kafka has received an event. No snapshot of existing objects, row images,
object contents or source-log replay is supplied. Overwrites are object-created
events. Downstream must handle duplicate/out-of-order events and version IDs /
sequencers, and fetch a specific object version separately when exact content is
needed. This form does not change bucket versioning or lifecycle rules. Removing a
subscription stops future notifications and creates a gap; it is not resumable CDC
pause. Existing Kafka events and the server Kafka target are preserved.

AWS S3 / other vendors are not implemented by this MinIO-specific form.

References: [Debezium PostgreSQL](https://debezium.io/documentation/reference/3.4/connectors/postgresql.html),
[MongoDB](https://debezium.io/documentation/reference/3.4/connectors/mongodb.html),
[MySQL](https://debezium.io/documentation/reference/3.4/connectors/mysql.html),
[Oracle](https://debezium.io/documentation/reference/3.4/connectors/oracle.html),
[Milvus (incubating)](https://debezium.io/documentation/reference/stable/connectors/milvus.html),
[DMDRS](https://eco.dameng.com/info/products/dmdrs),
[MinIO Kafka notifications](https://docs.min.io/aistor/administration/bucket-notifications/publish-events-to-kafka/),
[official dmPython wheels](https://pypi.org/project/dmpython/).
