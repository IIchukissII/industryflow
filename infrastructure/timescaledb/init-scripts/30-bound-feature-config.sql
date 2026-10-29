-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: definition

-- Bound Feature Configuration (ADR-0041 dec 1-4)
-- Purpose: A model's feature configuration is fixed once bound.
--
--          Serving, the deployment gate and drift read a model's configuration by reference at
--          every use, so an edit to its sensors or transforms changed the input vector of every
--          model bound to it on its next score, and ON DELETE SET NULL left deployed models
--          unbound. The rule is the database's (dec 3): every writer of the shared record meets
--          it, and a check made in the API before the write could be raced by a concurrent bind.

-- =============================================================================
-- THE RULES, AS TRIGGER FUNCTIONS
-- =============================================================================

-- Dec 2: a configuration bound by any model keeps what it computes. Name, description and
-- equipment type stay editable; nothing computes from them. Qualified throughout and with a
-- fixed search_path: it runs as whichever role writes the row.
CREATE OR REPLACE FUNCTION public.refuse_bound_feature_config_change()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    bound_by TEXT;
BEGIN
    IF NEW.id IS NOT DISTINCT FROM OLD.id
       AND NEW.base_sensors IS NOT DISTINCT FROM OLD.base_sensors
       AND NEW.transformations IS NOT DISTINCT FROM OLD.transformations THEN
        RETURN NEW;
    END IF;
    -- A statement-fresh snapshot (READ COMMITTED): a bind that committed while this row lock was
    -- awaited is seen here, so the bind and the edit cannot both succeed.
    EXECUTE format('SELECT string_agg(model_id::text, '', '' ORDER BY model_id) FROM %I.ml_models WHERE feature_config_id = $1',
                   TG_TABLE_SCHEMA)
        INTO bound_by USING OLD.id;
    IF bound_by IS NOT NULL THEN
        RAISE EXCEPTION 'feature configuration % is bound by models %; its sensors and transforms are fixed (ADR-0041 dec 2)',
            OLD.id, bound_by
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

-- Dec 1: a model's binding is fixed at registration. The bind also takes a share lock on the
-- configuration row, which conflicts with an edit's row lock: one waits for the other, and the
-- edit's check above then sees the bind.
CREATE OR REPLACE FUNCTION public.hold_feature_config_binding()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.feature_config_id IS DISTINCT FROM OLD.feature_config_id THEN
        RAISE EXCEPTION 'model % is bound to feature configuration %; a binding is fixed at registration (ADR-0041 dec 1)',
            OLD.model_id, OLD.feature_config_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.feature_config_id IS NOT NULL THEN
        EXECUTE format('SELECT 1 FROM %I.feature_engineering_configs WHERE id = $1 FOR SHARE', TG_TABLE_SCHEMA)
            USING NEW.feature_config_id;
    END IF;
    RETURN NEW;
END;
$$;

-- =============================================================================
-- APPLIED PER TENANT
-- =============================================================================

CREATE OR REPLACE FUNCTION add_bound_feature_config_rules(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    fk_name TEXT;
BEGIN
    -- Dec 2: deleting a bound configuration is refused, not answered by unbinding its models.
    -- The constraint was created unnamed, so it is found by what it references.
    FOR fk_name IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
        WHERE c.contype = 'f'
          AND c.conrelid = format('%I.ml_models', p_schema_name)::regclass
          AND c.confrelid = format('%I.feature_engineering_configs', p_schema_name)::regclass
          AND a.attname = 'feature_config_id'
          AND c.confdeltype <> 'r'
    LOOP
        EXECUTE format('ALTER TABLE %I.ml_models DROP CONSTRAINT %I', p_schema_name, fk_name);
    END LOOP;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = format('%I.ml_models', p_schema_name)::regclass
          AND conname = 'ml_models_feature_config_id_fkey'
    ) THEN
        EXECUTE format('
            ALTER TABLE %I.ml_models
            ADD CONSTRAINT ml_models_feature_config_id_fkey
                FOREIGN KEY (feature_config_id)
                REFERENCES %I.feature_engineering_configs(id) ON DELETE RESTRICT
        ', p_schema_name, p_schema_name);
    END IF;

    EXECUTE format('DROP TRIGGER IF EXISTS refuse_bound_feature_config_change ON %I.feature_engineering_configs',
                   p_schema_name);
    EXECUTE format('
        CREATE TRIGGER refuse_bound_feature_config_change
        BEFORE UPDATE ON %I.feature_engineering_configs
        FOR EACH ROW EXECUTE FUNCTION public.refuse_bound_feature_config_change()
    ', p_schema_name);

    EXECUTE format('DROP TRIGGER IF EXISTS hold_feature_config_binding ON %I.ml_models', p_schema_name);
    EXECUTE format('
        CREATE TRIGGER hold_feature_config_binding
        BEFORE INSERT OR UPDATE OF feature_config_id ON %I.ml_models
        FOR EACH ROW EXECUTE FUNCTION public.hold_feature_config_binding()
    ', p_schema_name);

    RAISE NOTICE '  ✓ bound feature configuration rules in schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_bound_feature_config_rules IS
    'A model''s feature configuration is fixed once bound (ADR-0041)';

-- =============================================================================
-- APPLY TO ALL EXISTING TENANT SCHEMAS
-- =============================================================================

DO $$
DECLARE
    schema_name TEXT;
    migrated_count INTEGER := 0;
BEGIN
    FOR schema_name IN
        SELECT nspname
        FROM pg_namespace
        WHERE nspname LIKE 'tenant_%'
        ORDER BY nspname
    LOOP
        IF EXISTS (SELECT 1 FROM information_schema.tables
                   WHERE table_schema = schema_name AND table_name = 'ml_models')
           AND EXISTS (SELECT 1 FROM information_schema.tables
                   WHERE table_schema = schema_name AND table_name = 'feature_engineering_configs') THEN
            PERFORM add_feature_config_columns_to_ml_models(schema_name);
            PERFORM add_bound_feature_config_rules(schema_name);
            migrated_count := migrated_count + 1;
        ELSE
            RAISE NOTICE '  ! Skipping % - ml_models or feature_engineering_configs not found', schema_name;
        END IF;
    END LOOP;
    RAISE NOTICE 'Bound feature configuration (ADR-0041): % schemas updated', migrated_count;
END $$;
