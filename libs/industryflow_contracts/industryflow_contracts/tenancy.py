# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tenant naming: the one mapping from a ``company_id`` to every name derived from it (ADR-0003).

A tenant is its ``company_id``. Every function here parses that value as a UUID first and builds
the name from the parsed UUID's canonical string only (ADR-0003 dec 4), so a value that is not a
UUID raises ``ValueError`` before it can become a schema, a role, a key prefix or a model name. The
result is a name the system constructed, safe to emit as an identifier (quoted where SQL takes
it) or as a path segment. This module is the only place the mapping exists (dec 5); no service
re-derives it (dec 7), and a repository test fails on any service source that builds one inline.

The tenant is spelled two ways, and they are kept as distinct functions because they are not
interchangeable:

  * **The identifier form**, ``tenant_<uuid with underscores>`` (``tenant_token``). It names the
    tenant's database schema and roles, its MLflow experiments and registered models, and its
    artifact keys. Underscores because a hyphen is not legal in an unquoted SQL identifier; the
    tracking plane reuses the same token so a tenant carries one name across the database and the
    model registry, and a kernel-visible name can be traced to the schema it belongs to.
  * **The cold-archive prefix**, ``tenant_<uuid>`` with the canonical hyphens
    (``cold_object_prefix``). It is the cold layer's object-store layout (ADR-0025 dec 5): a storage
    path, never an identifier, so nothing forces underscores on it, and every archive already
    exported sits under it. Re-spelling it in the identifier form would orphan that history from
    every reader. The exporter writes under it and the notebook broker reads under it; both take it
    from here, so the two sides cannot disagree about where a tenant's Parquet is.

Standard library only: this is imported by every service image and shipped to Spark executors.
"""
from __future__ import annotations

import uuid

# The literal every tenant name starts with.
_PREFIX = "tenant_"

# A LIKE pattern selecting the candidate tenant schemas from the catalog (``_`` escaped so it is
# matched literally, not as a wildcard). Bind it as a parameter. It selects candidates only: confirm
# each one with ``company_id_from_schema``, which accepts exactly the names ``schema_name`` builds.
SCHEMA_LIKE = r"tenant\_%"


def canonical_company_id(company_id) -> str:
    """The canonical (lower-case, hyphenated) string of a ``company_id``, or ``ValueError``.

    Accepts anything whose string form parses as a UUID (a ``uuid.UUID``, any spelling Python's
    parser accepts) and returns the one form every name below is built from.
    """
    return str(uuid.UUID(str(company_id)))


def _underscored(company_id) -> str:
    return canonical_company_id(company_id).replace("-", "_")


def tenant_token(company_id) -> str:
    """The tenant's identifier form, ``tenant_<uuid with underscores>``.

    The base the schema, the roles and the MLflow namespace are built from. Use the named function
    for what is being built; this is public for callers that partition by the bare token (the upload
    staging area sits outside every tenant prefix, ADR-0030).
    """
    return f"{_PREFIX}{_underscored(company_id)}"


def schema_name(company_id) -> str:
    """The tenant's database schema (schema-per-tenant, ADR-0003 dec 5)."""
    return tenant_token(company_id)


def reader_role_name(company_id) -> str:
    """The tenant's read-only database role (ADR-0011), ``tenant_reader_<uuid with underscores>``."""
    return f"{_PREFIX}reader_{_underscored(company_id)}"


def sql_login_name(company_id) -> str:
    """The tenant's own SQL-proxy login role (ADR-0015), ``tenant_sqlproxy_<uuid with underscores>``.

    The session user a notebook's SQL connection is opened as; confined by the database to its own
    tenant's reader role.
    """
    return f"{_PREFIX}sqlproxy_{_underscored(company_id)}"


def mlflow_name_prefix(company_id) -> str:
    """The prefix of the tenant's MLflow experiment and registered-model names, ``tenant_<…>.``.

    A dot, not a slash: MLflow forbids ``/`` and ``:`` in registered-model names, and one prefix must
    serve both name spaces (ADR-0019).
    """
    return tenant_token(company_id) + "."


# Where a deleted tenant's experiments are renamed to before they are soft-deleted (ADR-0033 dec 4):
# outside every tenant's name space, because the tracking gateway forces each name a kernel sends
# under ``tenant_<…>.``, so only the operator path can put an experiment here. The tracking server's
# collection hard-deletes exactly the deleted experiments under this root, and never a tenant's own
# deleted (still restorable) entries.
MLFLOW_DELETED_NAME_ROOT = "_deleted."


def mlflow_deleted_name_prefix(company_id) -> str:
    """The prefix a deleted tenant's experiments are renamed under, ``_deleted.tenant_<…>.``."""
    return MLFLOW_DELETED_NAME_ROOT + mlflow_name_prefix(company_id)


def mlflow_artifact_prefix(company_id) -> str:
    """The object-store key prefix of the tenant's MLflow artifacts and admitted uploads, ``tenant_<…>/``.

    A storage path, so a slash; the identifier-form token, so the artifacts sit under the same name as
    the experiments that own them (ADR-0019 dec 5, ADR-0030 dec 4).
    """
    return tenant_token(company_id) + "/"


def cold_object_prefix(company_id) -> str:
    """The tenant's cold-archive prefix, ``tenant_<uuid>`` with the canonical hyphens (ADR-0025 dec 5).

    Without a trailing separator: the exporter appends its partition path, and a reader that tests
    ownership by prefix must append ``/`` itself so ``tenant_<a>`` never matches a longer sibling.
    """
    return f"{_PREFIX}{canonical_company_id(company_id)}"


def company_id_from_schema(name: str) -> str:
    """The canonical ``company_id`` a tenant schema name was built from, or ``ValueError``.

    The inverse of ``schema_name``, for the background jobs that enumerate tenants from the catalog.
    Strict: only a name ``schema_name`` would itself produce is accepted, so a role-shaped, upper-case
    or otherwise look-alike schema is never taken for a tenant.
    """
    if not isinstance(name, str) or not name.startswith(_PREFIX):
        raise ValueError(f"not a tenant schema: {name!r}")
    company_id = canonical_company_id(name[len(_PREFIX):].replace("_", "-"))
    if schema_name(company_id) != name:
        raise ValueError(f"not a tenant schema: {name!r}")
    return company_id
