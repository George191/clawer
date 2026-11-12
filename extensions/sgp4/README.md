# SGP4 PostgreSQL 16 safety fork

See [PROVENANCE.md](PROVENANCE.md) for pinned sources, changes and coordinate limits.

Build on Ubuntu Linux with PostgreSQL 16 development headers:

```sh
sudo apt-get install build-essential postgresql-server-dev-16
make PG_CONFIG=/usr/lib/postgresql/16/bin/pg_config
```

Install the extension files in the PostgreSQL directories:

```sh
sudo make PG_CONFIG=/usr/lib/postgresql/16/bin/pg_config install
```

Enable the extension in the target database with `CREATE EXTENSION sgp4;`.
No shared_preload_libraries entry or PostgreSQL restart is needed for this extension.

API examples:

```sql
-- Existing API: UTC timestamp without timezone; WKT Z is kilometers.
SELECT satellite_geographic_position(line1::varchar,line2::varchar,
                                     now() AT TIME ZONE 'UTC');
-- Recommended timezone-safe interface.
SELECT satellite_geographic_position_utc(line1,line2,now());
-- TEME x,y,z [km], vx,vy,vz [km/s], WGS72; minutes from TLE epoch.
SELECT sgp4_teme(line1,line2,0);
```

Verification covered all 33 official groups, 666 genuine vectors and
seven known propagation-error cases including deep-space regimes. One legacy
printer artifact for satellite 33334 repeats an earlier satellite on error;
it was checked as error code 3 instead of as a valid state. Component tolerances
were 2e-7 km and 2e-8 km/s. The malformed-input harness exercised exact heap
allocations for every short length and 20000 deterministic mutations under ASAN/UBSAN.

Linux acceptance completed on Ubuntu 24.04 amd64 with PostgreSQL 16.15 and its
matching development headers: ASAN/UBSAN malformed-input tests, standalone
673 cases, PGXS build, CREATE EXTENSION in a separate cluster, SQL boundary/UTC
tests and all 673 SQL cases passed. See [VALIDATION.md](VALIDATION.md).
The release shared object depends on libc/libm, requires glibc >= 2.38, and has
no SSL dependency. This binary targets Ubuntu 24.04 hosts; rebuild from source
for older host libc versions.
