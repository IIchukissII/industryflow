# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The operator's user CLIs against the deployment (docs/operations/user-management.md; ADR-0004 dec 2,
dec 10): a user seeded from the environment signs in, is listed, has their password reset (the old
password and the old session refused at once, the new one accepted), and is deleted (refused after).

The CLIs run as the runbook runs them: in the gateway's image, with the scripts mounted, against the
running stack. Secrets reach the container through its environment or stdin, never its command line."""
import uuid

from harness import Secret

SCRIPTS = ("docker", "compose", "run", "--rm", "--no-deps", "-T", "-v", "./scripts:/scripts:ro",
           "-e", "PYTHONPATH=/app")


def _cli(env, *argv, input=None, extra=None):
    forward = [a for name in (extra or {}) for a in ("-e", name)]
    return env.run(*SCRIPTS, *forward, "api-gateway", "python", *argv, input=input, env=extra)


def test_a_seeded_user_is_listed_reset_and_deleted(edge, env, tenant):
    email = f"e2e-{tenant['run']}-cli-{uuid.uuid4().hex[:6]}@example.com"
    first, second = Secret(f"E2e-{uuid.uuid4().hex}"), Secret(f"E2e-{uuid.uuid4().hex}")

    _cli(env, "/scripts/seed_users.py", extra={
        "ADMIN_USER_1_EMAIL": email, "ADMIN_USER_1_PASSWORD": first.reveal(),
        "ADMIN_USER_1_COMPANY_ID": tenant["company_id"], "ADMIN_USER_1_ROLE": "observer"})
    session = edge.session(email, first)
    session.get("/api/equipment")

    listed = _cli(env, "/scripts/manage_users.py", "list")
    assert any(line.split()[:1] == [email] and " observer " in line for line in listed.splitlines()), listed

    _cli(env, "/scripts/manage_users.py", "reset-password", "--email", email, "--password-stdin",
         input=second.reveal() + "\n")
    session.get("/api/equipment", expect=(401,))
    assert edge.login(email, first).status_code in (400, 401)
    edge.session(email, second).get("/api/equipment")

    _cli(env, "/scripts/manage_users.py", "delete", "--email", email, "--yes")
    assert edge.login(email, second).status_code in (400, 401)
    assert env.sql(f"SELECT count(*) FROM public.\"user\" WHERE email = '{email}'") == "0"
