-- Run on the dedicated system database after restoring the selected public objects.
-- The application tables, their owned sequences and their constraints move together.
\set ON_ERROR_STOP on

BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL search_path TO pg_catalog;

DO $verify_restore$
BEGIN
    IF current_database() <> 'www.astral.io' THEN
        RAISE EXCEPTION 'Connect to the restored www.astral.io system database';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_extension
        WHERE extnamespace = to_regnamespace('public')
    ) OR EXISTS (
        SELECT 1
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE left(n.nspname, 3) = 'ts_' AND c.relkind IN ('r', 'p')
    ) THEN
        RAISE EXCEPTION 'The restore contains warehouse objects';
    END IF;

    IF (SELECT count(*) FROM pg_class
        WHERE relnamespace = to_regnamespace('public') AND relkind IN ('r', 'p')) <> 105 THEN
        RAISE EXCEPTION 'Expected all 105 restored application tables in public';
    END IF;
END
$verify_restore$;

DO $move_objects$
DECLARE
    object record;
BEGIN
    IF to_regnamespace('app') IS NULL OR EXISTS (
        SELECT 1 FROM pg_depend
        WHERE refclassid = 'pg_namespace'::regclass AND refobjid = to_regnamespace('app')
    ) THEN
        RAISE EXCEPTION 'Expected an empty app schema';
    END IF;

    FOR object IN SELECT typname AS name FROM pg_type
        WHERE typnamespace = to_regnamespace('public') AND typtype = 'e'
    LOOP
        EXECUTE format('ALTER TYPE public.%I SET SCHEMA app', object.name);
    END LOOP;

    FOR object IN SELECT relname AS name FROM pg_class
        WHERE relnamespace = to_regnamespace('public') AND relkind IN ('r', 'p')
    LOOP
        EXECUTE format('ALTER TABLE public.%I SET SCHEMA app', object.name);
    END LOOP;

    FOR object IN SELECT relname AS name FROM pg_class
        WHERE relnamespace = to_regnamespace('public') AND relkind = 'S'
    LOOP
        EXECUTE format('ALTER SEQUENCE public.%I SET SCHEMA app', object.name);
    END LOOP;

    FOR object IN SELECT proname AS name, pg_get_function_identity_arguments(oid) AS args
        FROM pg_proc WHERE pronamespace = to_regnamespace('public') AND prokind = 'f'
    LOOP
        EXECUTE format('ALTER FUNCTION public.%I(%s) SET SCHEMA app', object.name, object.args);
    END LOOP;

    IF EXISTS (
        SELECT 1 FROM pg_depend
        WHERE refclassid = 'pg_namespace'::regclass AND refobjid = to_regnamespace('public')
    ) THEN
        RAISE EXCEPTION 'One or more restored objects still belong to public';
    END IF;
END
$move_objects$;

-- Moving objects updates catalog references. Literal schema references in
-- the restored PL/pgSQL functions and their search paths need an explicit update.
DO $update_functions$
DECLARE
    definition text;
    updated_definition text;
BEGIN
    FOR definition IN
        SELECT pg_get_functiondef(p.oid)
        FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang
        WHERE p.pronamespace = to_regnamespace('app') AND l.lanname = 'plpgsql'
    LOOP
        updated_definition := replace(definition, 'public.', 'app.');
        updated_definition := replace(
            updated_definition,
            'SET search_path TO ''pg_catalog'', ''public'', ''pg_temp''',
            'SET search_path TO ''pg_catalog'', ''app'', ''pg_temp'''
        );
        IF updated_definition <> definition THEN
            EXECUTE updated_definition;
        END IF;
    END LOOP;
END
$update_functions$;

COMMIT;
