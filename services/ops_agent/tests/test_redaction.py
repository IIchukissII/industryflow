# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Nothing of the named categories reaches a model (ADR-0046 dec 8), and the record keeps counts only."""

from redaction import Redactor

TENANT = "0b2f503a-1c2d-4e5f-8a9b-0c1d2e3f4a5b"


def test_each_category_is_replaced():
    r = Redactor()
    text = r(
        f"tenant {TENANT} schema tenant_{TENANT.replace('-', '_')} user ops@example.com "
        "from 192.168.178.55 password=hunter22 Authorization: Bearer abcdefghijklmnop "
        "jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0NTY3.abcdefghijklmno key sk-abcdefghijklmnopqrstu"
    )
    for raw in (TENANT, "ops@example.com", "192.168.178.55", "hunter22", "abcdefghijklmnop", "eyJhbGci",
                "sk-abcdefghijklmnopqrstu", TENANT.replace("-", "_")):
        assert raw not in text, raw
    for placeholder in ("<tenant-1>", "<email-1>", "<address-1>", "<secret-1>"):
        assert placeholder in text


def test_a_value_keeps_one_placeholder_within_a_note():
    r = Redactor()
    assert r(f"{TENANT} failed") == "<tenant-1> failed"
    assert r(f"retry {TENANT}; other 11111111-2222-4333-8444-555555555555") == "retry <tenant-1>; other <tenant-2>"


def test_placeholders_mean_nothing_outside_their_note():
    assert Redactor()("a@b.io") == Redactor()("c@d.io") == "<email-1>"


def test_the_record_holds_counts_never_values():
    r = Redactor()
    r(f"{TENANT} {TENANT} ops@example.com")
    assert r.record() == {"email": 1, "tenant": 2}
    assert TENANT not in repr(r.record())


def test_plain_operational_text_is_untouched():
    line = "consumer group alert-detector lag 1200 on sensor-data-raw.measurements; disk 84% used"
    assert Redactor()(line) == line
