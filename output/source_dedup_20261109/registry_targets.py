"""Explicit physical targets for standalone source migrations only."""
TARGETS = {}
OBJECTS = []
CONNECTION_ID = None


def quote(name):
    if not isinstance(name, str) or not name or '\x00' in name or len(name.encode()) > 63:
        raise ValueError('Invalid PostgreSQL identifier')
    return '"' + name.replace('"', '""') + '"'


def table(layer, data_type):
    row = TARGETS[(layer, data_type)]
    return quote(row['schema_name']) + '.' + quote(row['table_name'])


def render(sql, layer, data_type):
    """Replace only the identifier marker; JSON braces are ordinary SQL data."""
    if sql.count('{table}') != 1:
        raise ValueError('Migration SQL requires exactly one table marker')
    return sql.replace('{table}', table(layer, data_type))


async def connect(application_name, *, timeout=15, command_timeout=150):
    import asyncpg
    from app.storage.business_database import business_connection_options
    from app.storage.postgres_client import get_pg_client
    global CONNECTION_ID
    metadata = get_pg_client()
    try:
        sources = await metadata.fetch_all("""SELECT b.layer, s.id, s.engine, s.config,
                s.credentials_ciphertext, s.paused_at
            FROM app.etl_layer_connections b
            JOIN app.ontology_data_connections s ON s.id=b.connection_id
            WHERE b.layer IN ('rds','ods') AND s.paused_at IS NULL""")
        if ({row['layer'] for row in sources} != {'rds', 'ods'} or len(sources) != 2
                or any(row['engine'] != 'postgresql' for row in sources)
                or sources[0]['id'] != sources[1]['id']):
            raise RuntimeError('Source migration requires RDS and ODS on the same authorized connection')
        url, options = business_connection_options(sources[0])
        rows = await metadata.fetch_all("""SELECT o.id, o.layer, o.data_type, o.schema_name, o.table_name
            FROM app.ontology_objects o
            JOIN app.etl_layer_connections b ON b.layer=o.layer
            WHERE o.is_active AND o.table_role='current' AND o.layer IN ('rds','ods')
              AND b.connection_id=:connection_id""",
            {'connection_id': sources[0]['id']})
        targets = {}
        for row in rows:
            quote(row['schema_name']); quote(row['table_name'])
            if not row['data_type']:
                continue
            key = row['layer'], row['data_type']
            if key in targets:
                raise RuntimeError('Ambiguous current migration object')
            targets[key] = dict(row)
        TARGETS.clear(); TARGETS.update(targets)
        OBJECTS[:] = [dict(row) for row in rows]
        CONNECTION_ID = str(sources[0]['id'])
    finally:
        await metadata.close()
    return await asyncpg.connect(url.replace('postgresql+asyncpg://', 'postgresql://', 1),
        timeout=timeout, command_timeout=command_timeout,
        server_settings={'application_name': application_name}, **options)
