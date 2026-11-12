# Source provenance and local changes

This is a local safety fork of the SQL API from
[gabriel-russo/postgresql-sgp4](https://github.com/gabriel-russo/postgresql-sgp4)
at commit `bcf6ea2b45285856f1b7b68c9da78ba129a5249d` (2026-03-11).
Its MIT license and authorship notices are preserved in LICENSE.
The SQL geographic function signature remains compatible; local version is 0.2.

The propagation and TLE parser are restored from
[aholinch/sgp4](https://github.com/aholinch/sgp4)
at commit `552cb1489a52c3023ae70cb6c7e239e84c5950fe` (2025-10-03),
`src/c/SGP4.c`, `SGP4.h`, `TLE.c`, `TLE.h`.
The upstream license is preserved as vendor/UNLICENSE.
Original upstream Git blob IDs:

| File | Git blob SHA-1 |
| --- | --- |
| src/c/SGP4.c | 1a58ef0bcff7095950923537847382836610fed0 |
| src/c/SGP4.h | 7e3f4d4ac8721a67e54b067f177393d0a6b8cfb4 |
| src/c/TLE.c | 030fc92299a676dc01b0ad551127bb8c3f6ba5dc |
| src/c/TLE.h | 20f78c4e3e42657d278e065f74c6a4ea2519600f |
| data/SGP4-VER.TLE | 59b0cd847f0beee56aca8cdfd8835772b2892aae |
| data/tcppver.out | 6ce77d666e196c6ca9f4e7de30d9c429a430ae94 |

Source files were fetched by the GitHub connector at the exact commit.
Line endings are normalized by workspace editing; the blob IDs describe upstream,
not the locally modified files. Local SHA-256 hashes are in SHA256SUMS.

Scientific source: Vallado, Crawford, Hujsak, Kelso,
*Revisiting Spacetrack Report #3*, AIAA 2006-6753:
https://celestrak.org/publications/AIAA/2006-6753/
The upstream verification vector blob IDs are recorded above.

Local changes:

- SGP4.c: restore upstream GMST in core initialization. The only addition to this
  restored core is a host interrupt hook in its resonance integration loop.
- TLE.c: zero initialize the complete record and correct two string terminators.
- safe_tle.c: validate lengths before any fixed-column access; reject embedded
  control/non-ASCII bytes, mismatched catalog IDs, malformed numeric fields,
  invalid calendar days/angles/mean motion. Checksum characters must be digits;
  checksum arithmetic is deliberately not enforced, as official error-test TLEs
  have incorrect checksums. TLE input columns remain text/varchar.
- Propagation errors never yield coordinates. Non-finite times/states are rejected.
  A 1e8-minute limit bounds each resonance integration to fewer than 140000 steps;
  PostgreSQL cancellation is checked on every step. This is a runtime bound,
  not a claim that TLE predictions remain accurate across that interval.
- The old millisecond/timegm conversion is removed. Timestamp computation uses
  split Julian dates with microseconds, explicit finite checks and the Gregorian
  1900-2099 interval supported by the upstream calendar formula.
- Geodetic conversion uses GMST with UTC as an approximation to UT1, WGS84
  ellipsoid, 32-iteration limit, finite checks, interruption and polar handling.
  It omits polar motion and DUT1; it is not precision ITRF. Z remains kilometers,
  matching the original API. TEME verification does not depend on this conversion.
- SQL functions keep original API and add a UTC timestamptz wrapper plus TEME
  six-component position/velocity array for independent verification.

No source file downloads, Python packages, network calls or external services
are used by the installed extension. Linux x86_64 with 64-bit long is required.
