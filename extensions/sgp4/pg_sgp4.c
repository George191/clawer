/* Copyright (C) 2026 Gabriel Russo. MIT; see LICENSE and PROVENANCE.md. */
#include "postgres.h"
#include "fmgr.h"
#include "catalog/pg_type_d.h"
#include "utils/array.h"
#include "utils/builtins.h"
#include "utils/timestamp.h"
#include "safe_tle.h"
#include <math.h>

PG_MODULE_MAGIC;
PG_FUNCTION_INFO_V1(SGP4_satellite_geographic_position);
PG_FUNCTION_INFO_V1(SGP4_teme);

static void checked(int code)
{
    if (code)
        ereport(ERROR, (errcode(ERRCODE_INVALID_PARAMETER_VALUE),
                       errmsg("SGP4 computation failed (code %d)", code),
                       errdetail("-1: invalid 69-byte TLE; -2: invalid propagation time or non-finite state; -3: geodetic conversion failed; 1-6: Vallado propagation error.")));
}

static void read_tle(FunctionCallInfo fcinfo, TLE *tle)
{
    text *a = PG_GETARG_TEXT_PP(0), *b = PG_GETARG_TEXT_PP(1);
    checked(sgp4_parse(VARDATA_ANY(a), VARSIZE_ANY_EXHDR(a),
                       VARDATA_ANY(b), VARSIZE_ANY_EXHDR(b), tle));
}

Datum SGP4_teme(PG_FUNCTION_ARGS)
{
    TLE tle;
    double r[3], v[3];
    Datum values[6];
    int i;
    read_tle(fcinfo, &tle);
    checked(sgp4_state(&tle, PG_GETARG_FLOAT8(2), r, v));
    for (i = 0; i < 3; i++) {
        values[i] = Float8GetDatum(r[i]);
        values[i + 3] = Float8GetDatum(v[i]);
    }
    PG_RETURN_ARRAYTYPE_P(construct_array(values, 6, FLOAT8OID, 8,
                                          FLOAT8PASSBYVAL, 'd'));
}

Datum SGP4_satellite_geographic_position(PG_FUNCTION_ARGS)
{
    TLE tle;
    Timestamp ts = PG_GETARG_TIMESTAMP(2);
    struct pg_tm tm;
    fsec_t fraction;
    double jd, fr, minutes, r[3], v[3], position[3];
    char wkt[192];
    int written;
    read_tle(fcinfo, &tle);
    if (TIMESTAMP_NOT_FINITE(ts) || timestamp2tm(ts, NULL, &tm, &fraction, NULL, NULL))
        checked(-2);
    /* This Vallado calendar conversion is defined for the Gregorian 1900-2100 interval. */
    if (tm.tm_year < 1900 || tm.tm_year > 2099) checked(-2);
    jday(tm.tm_year, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min,
         tm.tm_sec + fraction / 1000000.0, &jd, &fr);
    minutes = ((jd - tle.rec.jdsatepoch) + (fr - tle.rec.jdsatepochF)) * 1440.0;
    checked(sgp4_state(&tle, minutes, r, v));
    checked(sgp4_geodetic(r, jd, fr, position));
    written = snprintf(wkt, sizeof(wkt), "POINT Z(%.10f %.10f %.6f)", position[0], position[1], position[2]);
    if (written < 0 || written >= (int) sizeof(wkt)) checked(-3);
    PG_RETURN_TEXT_P(cstring_to_text(wkt));
}
