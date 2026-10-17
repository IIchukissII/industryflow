# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every reader of the session cookies names the cookies the gateway sets (ADR-0004 dec 3, ADR-0000 dec 3).

The gateway owns the cookie names. ml_service reads the access cookie to authenticate a browser call
and the CSRF cookie to check it; the frontend echoes the CSRF cookie. A reader whose name drifts
does not fail: it finds no cookie, and every same-origin browser call is refused or unchecked.

Both sides: the owner's names must be found, and each reader's must be among them.
"""
import re

from _repo import ROOT

GATEWAY = ROOT / "services" / "api_gateway"
ML = ROOT / "services" / "ml_service" / "api"
FRONTEND = ROOT / "services" / "frontend" / "src"

ASSIGN = re.compile(r'^(ACCESS_COOKIE|REFRESH_COOKIE|CSRF_COOKIE)\s*=\s*"([^"]+)"', re.M)
# A cookie-name literal: `if_` then a word, as a whole string literal.
LITERAL = re.compile(r"""["'](if_[a-z]+)["']""")


def _assigned(path):
    return dict(ASSIGN.findall(path.read_text()))


def _owner():
    names = {}
    names.update(_assigned(GATEWAY / "users.py"))
    names.update(_assigned(GATEWAY / "routers" / "auth_tokens.py"))
    return names


def test_gateway_defines_the_cookie_names():
    names = _owner()
    assert {"ACCESS_COOKIE", "CSRF_COOKIE"} <= names.keys(), names


def test_ml_service_constants_match_the_gateway():
    owner = _owner()
    seen = {}
    for path in (ML / "main.py", ML / "auth.py"):
        for const, value in _assigned(path).items():
            seen[(path.name, const)] = value
            assert value == owner[const], f"{path.name}: {const}={value!r}, gateway sets {owner[const]!r}"
    assert seen, "no cookie-name constant found in ml_service: the guard is reading the wrong files"


def test_ml_service_cookie_parameter_is_the_access_cookie():
    # FastAPI's Cookie() reads the cookie named after the parameter.
    owner = _owner()
    text = (ML / "auth.py").read_text()
    params = re.findall(r"(\w+): Optional\[str\] = Cookie\(", text)
    assert params, "no Cookie() parameter found in ml_service auth"
    assert set(params) == {owner["ACCESS_COOKIE"]}, params


def test_every_cookie_literal_names_a_gateway_cookie():
    owner = set(_owner().values())
    found = []
    files = list(ML.rglob("*.py")) + [
        p for p in FRONTEND.rglob("*.js*") if ".test." not in p.name
    ]
    for path in files:
        for name in LITERAL.findall(path.read_text()):
            found.append(name)
            assert name in owner, f"{path.relative_to(ROOT)} names cookie {name!r}, gateway sets {sorted(owner)}"
    assert found, "no cookie literal found: the guard is reading the wrong files"
