# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# TEMPORARY probe — verifies that a red required check blocks merge on main. This test fails on
# purpose; the PR carrying it must NOT be merged and will be closed after the block is confirmed.


def test_ci_protection_probe_intentional_failure():
    assert False, "intentional failure to verify branch protection blocks merge"
