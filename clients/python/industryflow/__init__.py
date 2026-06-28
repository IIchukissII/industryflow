# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""IndustryFlow notebook client — the blessed, tenant-scoped data path (ADR-0011 dec 4)."""

from .client import IndustryFlowClient
from .sql import IndustryFlowSQL
from .sql import query as sql_query

__all__ = ["IndustryFlowClient", "IndustryFlowSQL", "sql_query"]
