# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The ML service's CORS allowlist is explicit (ADR-0004 dec 6): it allows credentials, so a wildcard
would answer every origin with the caller's own and let any site send the session cookie."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import config  # noqa: E402


@pytest.mark.parametrize("value", ["*", " * ", "https://app.example.com,*"])
def test_a_wildcard_origin_is_refused(value):
    with pytest.raises(ValueError, match="ADR-0004 dec 6"):
        config.Config.cors_origins(value)


def test_explicit_origins_are_listed():
    assert config.Config.cors_origins("https://a.example.com, https://b.example.com,") == [
        "https://a.example.com", "https://b.example.com"]
