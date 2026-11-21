# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An identifier in a request: a UUID, as every identifier column of the tenant tables is.

A value that is not one is refused 422 at the route instead of reaching the database, which refuses it
with an error the route would answer 500. The route receives the canonical text, which binds to a
UUID column and to a text comparison alike.
"""
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator

Uuid = Annotated[UUID, AfterValidator(str)]
