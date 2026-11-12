CREATE FUNCTION satellite_geographic_position(
    line1 VARCHAR, line2 VARCHAR, at_datetime TIMESTAMP WITHOUT TIME ZONE
) RETURNS TEXT
AS '$libdir/sgp4', 'SGP4_satellite_geographic_position'
LANGUAGE C STRICT IMMUTABLE PARALLEL SAFE;

COMMENT ON FUNCTION satellite_geographic_position(VARCHAR,VARCHAR,TIMESTAMP WITHOUT TIME ZONE)
IS 'UTC timestamp; WKT longitude/latitude degrees, altitude km. TEME->PEF uses GMST with UTC approximating UT1; no polar motion.';

CREATE FUNCTION satellite_geographic_position_utc(
    line1 TEXT, line2 TEXT, at_datetime TIMESTAMP WITH TIME ZONE
) RETURNS TEXT LANGUAGE SQL STRICT IMMUTABLE PARALLEL SAFE
RETURN satellite_geographic_position(line1::VARCHAR, line2::VARCHAR, at_datetime AT TIME ZONE 'UTC');

CREATE FUNCTION sgp4_teme(line1 TEXT, line2 TEXT, minutes_after_epoch DOUBLE PRECISION)
RETURNS DOUBLE PRECISION[]
AS '$libdir/sgp4', 'SGP4_teme'
LANGUAGE C STRICT IMMUTABLE PARALLEL SAFE;

COMMENT ON FUNCTION sgp4_teme(TEXT,TEXT,DOUBLE PRECISION)
IS 'TEME [x,y,z,vx,vy,vz], km and km/s, WGS72; invalid propagation raises 22023. Absolute time limit 1e8 minutes.';
