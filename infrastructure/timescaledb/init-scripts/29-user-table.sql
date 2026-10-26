-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: migration

-- The platform user table, and no application role creating objects in public (ADR-0034 dec 1)
-- Purpose: public."user" (fastapi-users plus company_id and role) is part of the platform schema, so
--          this sequence defines it and the database owner owns it; the gateway reads and writes
--          rows only. No application role keeps CREATE on public: the owner's SECURITY DEFINER
--          functions and the runner resolve unqualified names through public, so a role able to
--          create there could plant a better-matching overload (CVE-2018-1058) and run it as the
--          owner. A database where the gateway already created the table keeps its rows, and the
--          table changes owner. Idempotent: safe to run repeatedly.

CREATE TABLE IF NOT EXISTS public."user" (
    company_id uuid NOT NULL,
    role character varying(50) NOT NULL,
    id uuid NOT NULL,
    email character varying(320) NOT NULL,
    hashed_password character varying(1024) NOT NULL,
    is_active boolean NOT NULL,
    is_superuser boolean NOT NULL,
    is_verified boolean NOT NULL,
    CONSTRAINT user_pkey PRIMARY KEY (id),
    CONSTRAINT check_role CHECK (role IN ('admin', 'engineer', 'observer'))
);
CREATE INDEX IF NOT EXISTS ix_user_company_id ON public."user" (company_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_user_email ON public."user" (email);

ALTER TABLE public."user" OWNER TO CURRENT_USER;

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'api_gateway_user') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON public."user" TO api_gateway_user;
        -- Granted by 00-create-roles.sh before this script existed; revoked on every database.
        REVOKE CREATE ON SCHEMA public FROM api_gateway_user;
        ALTER DEFAULT PRIVILEGES FOR ROLE api_gateway_user IN SCHEMA public
            REVOKE SELECT ON TABLES FROM alert_service_user, ml_service_user;
    END IF;
END $$;

-- The readers of the user table (formerly granted through the gateway's default privileges).
GRANT SELECT ON public."user" TO alert_service_user, ml_service_user;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
