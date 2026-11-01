-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: migration

-- An account may be required to change its password before anything else (ADR-0004 dec 11).
-- Purpose: the first-run superuser is created with a published default password; until its holder
--          sets their own, the account reads itself, changes its password and signs out, and nothing
--          more. Every other account is unaffected (false).

ALTER TABLE public."user" ADD COLUMN IF NOT EXISTS password_change_required boolean NOT NULL DEFAULT false;
