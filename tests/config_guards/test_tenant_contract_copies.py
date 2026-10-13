# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
No service or script re-derives what this package defines (ADR-0003 dec 5, 7; ADR-0006 dec 7).

The tenant mapping was once copied into thirteen modules across nine images, each held equal to the
others by nothing. A copy that drifts addresses another schema, another role, another tenant's
objects — and every test of the copy still passes. This reads every service's source and the
repository's operator scripts, and fails on any string literal that spells a tenant name (``tenant_…`` in a schema, role, prefix or LIKE
pattern) and on any restatement of the window contract (an aggregation table name, or a table keyed
by the granularities). Docstrings and comments may describe the names; code may not build them.

Both sides: the same scan must find the definitions in this package, or it is proving nothing; and a
script that cannot import the package is an explicit, justified exception whose own pattern is held
to the package's names here.
"""
import ast
import importlib.util
import re
import uuid
from pathlib import Path

from _repo import ROOT
from industryflow_contracts import tenancy, windows

REPO = ROOT
SERVICES = REPO / "services"
SCRIPTS = REPO / "scripts"
PACKAGE = ROOT / "libs" / "industryflow_contracts" / "industryflow_contracts"

# A tenant name, or the start of one: `tenant_` (optionally `reader_`/`sqlproxy_`) starting a word and
# followed by the end of the literal (`'tenant_' + c`), a placeholder or pattern character
# (`f"tenant_{c}"`, `'tenant_%'`, `'tenant\_%'`, `tenant_<uuid>`), a regular expression's class or
# group (`r"tenant_[0-9a-f]{8}…"`), or a UUID's first group. Not a word that merely begins with it,
# such as a `tenant_failures=` log field.
TENANT_NAME = re.compile(
    r"(?<![A-Za-z0-9])tenant_(?:reader_|sqlproxy_)?(?:$|[{%\\<\[(]|[0-9a-fA-F]{8}[-_])"
)
TABLES = {w.table for w in windows.WINDOWS.values()}
GRANULARITIES = set(windows.WINDOWS)


# Sources that may spell a tenant name themselves, each with the reason it cannot import the package.
# An entry must still need its exception (see below), so the list cannot outlive its reasons.
EXCEPTIONS = {
    "scripts/migrate_tenant_artifact_locations.py":
        "piped over stdin into the MLflow container, whose image does not install the package; its "
        "pattern is held to the package's MLflow names by "
        "test_the_artifact_migration_reads_mlflow_names_as_the_package_spells_them",
}


def _service_sources():
    for path in sorted(SERVICES.rglob("*.py")):
        rel = path.relative_to(REPO).parts
        if "tests" in rel or "node_modules" in rel or ".venv" in rel:
            continue
        yield path


def _script_sources():
    # The operator scripts at the top of scripts/; their own tests live in scripts/tests.
    return sorted(SCRIPTS.glob("*.py"))


def _docstrings(tree):
    nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                nodes.add(id(body[0].value))
    return nodes


def _string_literals(tree):
    """Every string constant that is not a docstring, f-string fragments included."""
    skip = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            yield node


def violations(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    found = []
    for node in _string_literals(tree):
        if TENANT_NAME.search(node.value):
            found.append(f"{path}:{node.lineno}: builds a tenant name inline: {node.value!r}")
        if node.value in TABLES:
            found.append(f"{path}:{node.lineno}: restates an aggregation table: {node.value!r}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and node.keys and all(
            isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys
        ) and {k.value for k in node.keys} == GRANULARITIES:
            found.append(f"{path}:{node.lineno}: restates the window table keyed by granularity")
    return found


def test_the_scan_reads_every_service():
    scanned = {p.relative_to(SERVICES).parts[0] for p in _service_sources()}
    assert {
        "api_gateway", "ingestion_service", "alert_service", "ml_service", "spark_jobs",
        "notebook_sql_proxy", "notebook_tracking_gateway", "notebook_cold_broker", "cold_export",
    } <= scanned


def test_no_service_or_script_builds_a_tenant_name_or_restates_the_window_contract():
    found = [v for path in [*_service_sources(), *_script_sources()]
             if path.relative_to(REPO).as_posix() not in EXCEPTIONS for v in violations(path)]
    assert not found, (
        "These build a name or value that industryflow_contracts defines (ADR-0003 dec 5, 7; "
        "ADR-0006 dec 7). Import it from the package instead:\n  " + "\n  ".join(found)
    )


def test_every_exception_is_still_needed():
    assert _script_sources(), "the scan reads no script — it is not looking"
    for rel in EXCEPTIONS:
        assert (REPO / rel).exists(), f"{rel} is excepted but no longer exists"
        assert violations(REPO / rel), f"{rel} no longer spells a tenant name — drop its exception"


def _load_script(rel: str):
    spec = importlib.util.spec_from_file_location(Path(rel).stem, REPO / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_artifact_migration_reads_mlflow_names_as_the_package_spells_them():
    """Both sides of the one copy that cannot import the package: every name the package gives a
    tenant's MLflow objects is recognised, the token it extracts is the package's, the location it
    rewrites to is the package's artifact prefix — and nothing that is not such a name is matched."""
    migrate = _load_script("scripts/migrate_tenant_artifact_locations.py")
    for cid in ["0b2f503a-6e59-4f4f-b0cd-b82547a20cf2", *(str(uuid.uuid4()) for _ in range(20))]:
        name = tenancy.mlflow_name_prefix(cid) + "experiment"
        m = migrate.TENANT_NAME.match(name)
        assert m and m.group(1) == tenancy.tenant_token(cid), name
        (moved,), _ = migrate.plan([("7", name, f"{migrate.SCHEME}7")], lambda prefix: 0)
        assert moved[2] == f"{migrate.SCHEME}{tenancy.mlflow_artifact_prefix(cid)}7"
    cid = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
    for not_a_tenant_name in (
        "experiment",
        tenancy.cold_object_prefix(cid) + ".experiment",
        tenancy.reader_role_name(cid) + ".experiment",
        tenancy.mlflow_name_prefix(cid).upper() + "experiment",
        tenancy.tenant_token(cid) + "/experiment",
    ):
        assert not migrate.TENANT_NAME.match(not_a_tenant_name), not_a_tenant_name


def test_the_scan_finds_the_definitions_it_forbids_elsewhere():
    """The detector run on the package itself: it must flag the tenant names and the window table,
    or a service copy would pass it just as silently."""
    assert any("builds a tenant name" in v for v in violations(PACKAGE / "tenancy.py"))
    found = violations(PACKAGE / "windows.py")
    assert any("restates an aggregation table" in v for v in found)
    assert any("window table keyed by granularity" in v for v in found)


# The database names tenants too — it creates the schema, the reader role and the SQL login — and SQL
# cannot import this package, so its derivations are held to the shapes that agree with it instead:
# `uuid::text` is the canonical lower-case hyphenated form, so `'tenant_' || replace(id::text, '-',
# '_')` is ``schema_name``; `substr(schema, 8)` drops exactly `tenant_` (7 characters).
INIT_SCRIPTS = REPO / "infrastructure" / "timescaledb" / "init-scripts"
SQL_DERIVATION = re.compile(r"'tenant_[a-z_]*'\s*\|\|[^;\n]*", re.IGNORECASE)
SQL_SHAPES = [
    re.compile(r"'tenant_(?:reader_|sqlproxy_)?'\s*\|\|\s*replace\(\s*\w+::text\s*,\s*'-'\s*,\s*'_'\s*\)", re.I),
    re.compile(r"'tenant_reader_'\s*\|\|\s*substr\(\s*\w+\s*,\s*8\s*\)", re.I),
    re.compile(r"'tenant_(?:reader_|sqlproxy_)'\s*\|\|\s*v_suffix\s*$", re.I),
]
SQL_SUFFIX = re.compile(r"v_suffix\s+TEXT\s*:=\s*replace\(\s*\w+::text\s*,\s*'-'\s*,\s*'_'\s*\)", re.I)


def test_the_database_derives_tenant_names_the_same_way():
    found = []
    for path in sorted(INIT_SCRIPTS.glob("*")):
        if path.suffix not in (".sql", ".sh"):
            continue
        text = path.read_text()
        for m in SQL_DERIVATION.finditer(text):
            expr = m.group(0).strip()
            found.append(expr)
            assert any(s.match(expr) for s in SQL_SHAPES), f"{path.name}: unrecognised tenant derivation `{expr}`"
            if "v_suffix" in expr:
                assert SQL_SUFFIX.search(text), f"{path.name}: v_suffix is not the underscored UUID"
    assert len(found) >= 5, "the scan found too few SQL derivations to be checking anything"
    cid = "550E8400-E29B-41D4-A716-446655440000"
    as_uuid_text = cid.lower()  # what PostgreSQL's uuid::text yields
    assert tenancy.schema_name(cid) == "tenant_" + as_uuid_text.replace("-", "_")
    assert tenancy.reader_role_name(cid) == "tenant_reader_" + tenancy.schema_name(cid)[len("tenant_"):]
    assert tenancy.sql_login_name(cid) == "tenant_sqlproxy_" + as_uuid_text.replace("-", "_")


def test_a_reintroduced_copy_is_caught(tmp_path):
    """The shapes the deleted copies had, each of which must fail the scan."""
    copies = {
        "schema": "def f(c):\n    return f\"tenant_{c.replace('-', '_')}\"\n",
        "concat": "def f(c):\n    return 'tenant_' + c\n",
        "role": "def f(c):\n    return f'tenant_reader_{c}'\n",
        "inverse": "def f(s):\n    return s.removeprefix('tenant_')\n",
        "scan": "Q = \"SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'tenant_%'\"\n",
        "pattern": "P = r'^(tenant_[0-9a-f]{8}_[0-9a-f]{4})'\n",
        "table": "T = {'1min': 'sensor_aggregations_1min'}\n",
        "widths": "W = {'1min': 60, '5min': 300, '1hour': 3600}\n",
    }
    for name, src in copies.items():
        path = tmp_path / f"{name}.py"
        path.write_text(src)
        assert violations(path), f"the scan missed the {name} copy"
    described = tmp_path / "described.py"
    described.write_text('"""Routes to ``tenant_<uuid>.sensor_measurements``."""\n# tenant_<uuid>\n')
    assert not violations(described), "a docstring or comment that describes a name is not a copy"
