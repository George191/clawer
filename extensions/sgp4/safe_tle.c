/* Hardened boundary around the upstream parser; see PROVENANCE.md. */
#include "safe_tle.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

#ifndef SGP4_CHECK_FOR_INTERRUPTS
#define SGP4_CHECK_FOR_INTERRUPTS() ((void) 0)
#endif

static int digits(const char *s, int start, int count)
{
    int i;
    for (i = start; i < start + count; i++)
        if (s[i] < '0' || s[i] > '9') return 0;
    return 1;
}

static int number(const char *s, int start, int count, double *out)
{
    char field[20], *end;
    int i;
    if (count >= (int) sizeof(field)) return 0;
    memcpy(field, s + start, count);
    field[count] = 0;
    for (i = 0; i < count; i++)
        if (!(field[i] == ' ' || field[i] == '.' || field[i] == '-' ||
              field[i] == '+' || (field[i] >= '0' && field[i] <= '9'))) return 0;
    *out = strtod(field, &end);
    if (end == field || !isfinite(*out)) return 0;
    while (*end == ' ') end++;
    return *end == 0;
}

static int exponent(const char *s, int start)
{
    return (s[start] == ' ' || s[start] == '+' || s[start] == '-') &&
        digits(s, start + 1, 5) &&
        (s[start + 6] == '+' || s[start + 6] == '-') && digits(s, start + 7, 1);
}

int sgp4_parse(const char *a, size_t na, const char *b, size_t nb, TLE *tle)
{
    char l1[70], l2[70];
    double day, value;
    int i, year, leap;
    memset(tle, 0, sizeof(*tle));
    if (na != 69 || nb != 69) return -1;
    for (i = 0; i < 69; i++)
        if ((unsigned char) a[i] < 32 || (unsigned char) a[i] > 126 ||
            (unsigned char) b[i] < 32 || (unsigned char) b[i] > 126) return -1;
    if (a[0] != '1' || b[0] != '2' || a[1] != ' ' || b[1] != ' ' ||
        memcmp(a + 2, b + 2, 5) || !digits(a, 18, 5) || a[23] != '.' ||
        !digits(a, 24, 8) || !digits(a, 68, 1) || !digits(b, 68, 1) ||
        !exponent(a, 44) || !exponent(a, 53) || !digits(b, 26, 7)) return -1;
    year = (a[18] - '0') * 10 + a[19] - '0';
    year += year > 56 ? 1900 : 2000;
    leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    if (!number(a, 20, 12, &day) || day < 1 || day >= 366 + leap ||
        !number(a, 33, 10, &value) || !number(a, 64, 4, &value) ||
        !number(b, 63, 5, &value)) return -1;
    if (!number(b, 8, 8, &value) || value < 0 || value > 180) return -1;
    if (!number(b, 17, 8, &value) || value < 0 || value >= 360) return -1;
    if (!number(b, 34, 8, &value) || value < 0 || value >= 360) return -1;
    if (!number(b, 43, 8, &value) || value < 0 || value >= 360) return -1;
    if (!number(b, 52, 11, &value) || value <= 0) return -1;
    memcpy(l1, a, 69); l1[69] = 0;
    memcpy(l2, b, 69); l2[69] = 0;
    parseLines(tle, l1, l2);
    /* Propagation reports the applicable Vallado error at the requested time. */
    return 0;
}

int sgp4_state(TLE *tle, double minutes, double r[3], double v[3])
{
    int i, ok;
    for (i = 0; i < 3; i++) r[i] = v[i] = NAN;
    /* Bound the resonance integrator to <= 138890 steps per call. */
    if (!isfinite(minutes) || fabs(minutes) > 100000000.0) return -2;
    SGP4_CHECK_FOR_INTERRUPTS();
    tle->rec.error = 0;
    ok = sgp4(&tle->rec, minutes, r, v);
    if (!ok || tle->rec.error) return tle->rec.error ? tle->rec.error : -2;
    for (i = 0; i < 3; i++)
        if (!isfinite(r[i]) || !isfinite(v[i])) return -2;
    return 0;
}

int sgp4_geodetic(const double r[3], double jd, double fraction, double result[3])
{
    const double a = 6378.137, e2 = 6.6943799901413165e-3;
    double angle, x, y, p, phi, next, n, h;
    int i;
    if (!isfinite(jd) || !isfinite(fraction) || !isfinite(r[0]) ||
        !isfinite(r[1]) || !isfinite(r[2])) return -3;
    angle = gstime(jd + fraction);
    x = r[0] * cos(angle) + r[1] * sin(angle);
    y = -r[0] * sin(angle) + r[1] * cos(angle);
    p = hypot(x, y);
    if (p < 1e-12) {
        if (fabs(r[2]) < 1e-12) return -3;
        result[0] = 0;
        result[1] = copysign(90.0, r[2]);
        result[2] = fabs(r[2]) - a * sqrt(1 - e2);
        return 0;
    }
    phi = atan2(r[2], p);
    for (i = 0; i < 32; i++) {
        SGP4_CHECK_FOR_INTERRUPTS();
        n = a / sqrt(1 - e2 * sin(phi) * sin(phi));
        next = atan2(r[2] + e2 * n * sin(phi), p);
        if (!isfinite(next)) return -3;
        if (fabs(next - phi) < 1e-12) {
            phi = next;
            n = a / sqrt(1 - e2 * sin(phi) * sin(phi));
            h = p * cos(phi) + r[2] * sin(phi) - n * (1 - e2 * sin(phi) * sin(phi));
            result[0] = atan2(y, x) / deg2rad;
            result[1] = phi / deg2rad;
            result[2] = h;
            return isfinite(h) ? 0 : -3;
        }
        phi = next;
    }
    return -3;
}
