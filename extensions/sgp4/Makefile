EXTENSION = sgp4
MODULE_big = sgp4
OBJS = pg_sgp4.o pg_core.o pg_safe_tle.o vendor/TLE.o
DATA = sgp4--0.2.sql
SHLIB_LINK = -lm
PG_CONFIG = /usr/lib/postgresql/16/bin/pg_config
PGXS := $(shell $(PG_CONFIG) --pgxs)
include $(PGXS)
