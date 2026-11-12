/* PostgreSQL interruption hooks without changing upstream equations. */
#include "postgres.h"
#include "miscadmin.h"
#define SGP4_CHECK_FOR_INTERRUPTS() CHECK_FOR_INTERRUPTS()
#include "vendor/SGP4.c"
