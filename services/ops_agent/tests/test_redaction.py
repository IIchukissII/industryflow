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


import pytest  # noqa: E402

UUID = "0b2f503a-1c2d-4e5f-8a9b-0c1d2e3f4a5b"


@pytest.mark.parametrize("raw,secret", [
    ("postgresql://ingest:S3cretPass@timescaledb:5432/industryflow", "S3cretPass"),
    ("redis://:hunter22@redis:6379/0", "hunter22"),
    ("POSTGRES_PASSWORD=pa55word", "pa55word"),
    ("MINIO_ROOT_PASSWORD=minio-root-pw", "minio-root-pw"),
    ("access_token=abc123def", "abc123def"),
    ("refresh_token: zyx987", "zyx987"),
    ("client_secret: s3cr3t-value", "s3cr3t-value"),
    ("aws_secret_access_key = wJalrXUtnFEMI", "wJalrXUtnFEMI"),
    ('password="hunter2xx"', "hunter2xx"),
    ("password='hunter2yy'", "hunter2yy"),
    ('{"token":"abc"}', '"abc"'),
    ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
    ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnop"),
    ("key AKIAIOSFODNN7EXAMPLE used", "AKIAIOSFODNN7EXAMPLE"),
])
def test_credentials_in_their_common_shapes_are_replaced(raw, secret):
    out = Redactor()(raw)
    assert secret not in out and "<secret-1>" in out, out


@pytest.mark.parametrize("raw,value,category", [
    ("from fe80::1 to", "fe80::1", "address"),
    ("peer 2001:db8::8a2e:370:7334 closed", "2001:db8::8a2e:370:7334", "address"),
    ("listening on [::1]:8000", "::1", "address"),
    (f"cursor cold_read_tenant_{UUID.replace('-', '_')}_2026-10-31", UUID.replace("-", "_"), "tenant"),
    (f"user_{UUID} logged in", UUID, "tenant"),
    ("company 0b2f503a1c2d4e5f8a9b0c1d2e3f4a5b deleted", "0b2f503a1c2d4e5f8a9b0c1d2e3f4a5b", "tenant"),
    ("Uppercase 0B2F503A-1C2D-4E5F-8A9B-0C1D2E3F4A5B", "0B2F503A", "tenant"),
    ("login bob%40example.com failed", "bob%40example.com", "email"),
])
def test_identifiers_hidden_inside_names_or_encodings_are_replaced(raw, value, category):
    out = Redactor()(raw)
    assert value not in out and f"<{category}-1>" in out, out


def test_clock_times_versions_and_ports_are_not_addresses():
    line = "2026-10-31 10:42:31 spark 4.1.3 on port 8000 retried 3x; ratio 1.2.3"
    assert Redactor()(line) == line
