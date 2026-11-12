# Linux validation

Environment: an isolated application-host Docker container, Ubuntu 24.04 amd64
image digest `sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852`.
Read-only source bind mount, private output volume, 2 CPUs and 2 GiB memory.
No production PostgreSQL connections or data mounts; host APT was not modified.

Compiler: GCC 13; PostgreSQL server and headers `16.15-0ubuntu0.24.04.1`.
Runtime libc: `2.39-0ubuntu8.9`. The shared object links only libc/libm and
requires at most `GLIBC_2.38`; it does not link SSL or the sanitizer runtimes.
PG16.15 module loading and SQL API were tested directly. Production installation
must check the host architecture/libc and PostgreSQL major version; a PostgreSQL
16 build from the PGDG Jammy repository running on Ubuntu 24.04 is not itself
an SSL dependency of this module.

Executed tests:

- ASAN and UBSAN: all short-line heap boundaries, embedded controls/high bytes,
  mismatched IDs, bad epochs/motion, non-finite times and 20000 deterministic
  malformed TLE mutations.
- Standalone 666 genuine vectors from all 33 official Vallado groups, including
  near-earth/deep-space/resonant/long-horizon cases; seven expected errors
  `[1,1,6,6,4,3,6]`. The single 33334 legacy output-printer artifact is validated
  as its actual error, not accepted as a state vector.
- Worst absolute component disagreement with the published reference:
  position `1.1549946066224948e-7 km`, velocity
  `4.996998370643269e-10 km/s`. These are numerical verification errors,
  not claims about physical TLE orbit accuracy.
- PGXS native shared library plus LLVM bitcode build; CREATE EXTENSION sgp4 0.2
  in an independent PG16 cluster using a private Unix socket and no TCP listener.
- SQL invalid-input, STRICT/null and timezone-independent UTC wrapper checks;
  all 673 vector/error SQL cases. SQL maximum position disagreement
  `1.1548127076821402e-7 km`, velocity `4.996953961722284e-10 km/s`.

Preserved upstream warnings concern unused parser locals, an existing missing
prototype and C90 declaration placement; no unrelated vendor cleanup was done.
The first SQL test attempt exposed the client's psycopg2 transaction-context
behavior after expected errors. Removing that test-client context fixed it;
no production algorithm change was needed.

Production PostgreSQL was not modified by this validation work.
