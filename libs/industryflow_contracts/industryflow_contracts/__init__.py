# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What every IndustryFlow deployment unit must agree on, defined once.

The services are separate images, but some facts cannot differ between them without breaking
isolation or meaning:

  * ``tenancy`` — every name derived from a tenant's ``company_id``: its schema, its roles, its
    MLflow namespace and its object-store prefixes (ADR-0003 dec 4, 5, 7). Two services that
    spelled a tenant differently would address different data, or another tenant's.
  * ``windows`` — the width and allowed lateness of each aggregation granularity (ADR-0006 dec 7).
    The Spark job that writes the windows, the provider that serves features from them and the
    offline re-derivation that trains on them must use the same numbers (ADR-0023 dec 9).

Each image installs this package (or, for the Spark jobs, ships it with ``--py-files``) from the
one source in ``libs/industryflow_contracts``; no service carries its own copy. Standard library
only, so it installs anywhere and imports under any interpreter the images run.
"""
