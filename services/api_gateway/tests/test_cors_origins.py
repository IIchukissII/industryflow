# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The CORS allowlist never combines a wildcard with credentials (ADR-0004 dec 6).

The gateway allows credentials, so a ``*`` entry in ``CORS_ORIGINS`` fails the settings at startup
rather than reaching the middleware.
"""
import pytest
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

import config
import main


@pytest.mark.parametrize("origins", ["*", " * ", "https://app.example.com, *"])
def test_a_wildcard_origin_is_refused_at_startup(origins):
    with pytest.raises(ValidationError, match="ADR-0004 dec 6"):
        config.Settings(CORS_ORIGINS=origins)


def test_an_explicit_allowlist_is_accepted():
    s = config.Settings(CORS_ORIGINS="https://app.example.com, https://ops.example.com")
    assert config.cors_origin_list(s.CORS_ORIGINS) == ["https://app.example.com", "https://ops.example.com"]


def test_the_middleware_gets_the_allowlist_with_credentials():
    cors = next(m for m in main.app.user_middleware if m.cls is CORSMiddleware)
    assert cors.kwargs["allow_credentials"] is True
    assert "*" not in cors.kwargs["allow_origins"]
    assert cors.kwargs["allow_origins"] == config.cors_origin_list(main.settings.CORS_ORIGINS)
