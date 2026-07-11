# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Put the repo ``services`` directory on the path so the exporter imports as the ``cold_export``
package (its modules use relative imports). The orchestration tests need no DB or object store —
they drive ``cold_export.exporter`` against in-memory fakes.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
