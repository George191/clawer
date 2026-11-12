#ifndef SPIDER_SAFE_TLE_H
#define SPIDER_SAFE_TLE_H
#include <stddef.h>
#include "vendor/TLE.h"

/* Negative codes are adapter validation errors, positive codes are Vallado. */
int sgp4_parse(const char *line1, size_t len1, const char *line2, size_t len2, TLE *tle);
int sgp4_state(TLE *tle, double minutes, double r[3], double v[3]);
int sgp4_geodetic(const double r[3], double jd, double fraction, double result[3]);
#endif
