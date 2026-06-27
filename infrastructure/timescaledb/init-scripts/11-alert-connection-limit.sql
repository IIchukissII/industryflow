-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Migration: raise alert_service_user's connection limit (existing deployments).
--
-- alert-service-api runs 4 uvicorn workers, each opening an asyncpg pool (min 5 / max 20),
-- alongside the alert detector — all as alert_service_user. The original CONNECTION LIMIT 10
-- could not satisfy even startup (4 x min 5 = 20 > 10), so the API failed to boot with
-- "too many connections for role". Align it with api_gateway_user (50). Idempotent.

ALTER ROLE alert_service_user CONNECTION LIMIT 50;
