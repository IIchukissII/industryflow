# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sensors carry an optional human label next to their machine key, and lists show names, not ids.

sensor_name is the key that feature configs, model features and rule patterns reference; it never
changes. display_name is what a person reads. Every payload that names a sensor carries both, and
the alert-rule list carries the names of what each rule watches, joined at read time, keeping the
ids. The app is driven with the tenant session replaced by a fake that records the SQL and answers
with prepared rows.
"""
import inspect
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from dependencies import get_db_with_tenant
from routers import alert_rules, alerts_history, cache, equipment, websocket
from users import current_active_user

EQ, SENSOR = str(uuid.uuid4()), str(uuid.uuid4())


class _Row:
    def __init__(self, mapping):
        self._mapping = mapping

    def __getattr__(self, name):
        try:
            return self._mapping[name]
        except KeyError:
            raise AttributeError(name)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class Session:
    """Answers each statement with the rows of the first matching prefix; records them all."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.statements = []

    async def execute(self, clause, params=None):
        sql = " ".join(str(clause).split())
        self.statements.append((sql, dict(params or {})))
        for needle, rows in self.answers.items():
            if needle in sql:
                rows = rows(params) if callable(rows) else rows
                return _Result([_Row(r) for r in rows])
        return _Result([])

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def client_for():
    def make(session):
        async def _db():
            yield session

        main.app.dependency_overrides[get_db_with_tenant] = _db
        main.app.dependency_overrides[current_active_user] = lambda: SimpleNamespace(
            id=uuid.uuid4(), email="op@example.com", company_id=uuid.uuid4(), role="engineer",
            is_superuser=False, is_active=True)
        return TestClient(main.app)

    yield make
    main.app.dependency_overrides.clear()


def _echo(params):
    return [{**params, "created_at": None}]


# --- the label itself ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, stored", [
    ("  Root zone temperature ", "Root zone temperature"),
    ("", None), ("   ", None), (None, None),
    ("x" * 100, "x" * 100),
])
def test_display_name_is_trimmed_and_empty_means_unset(raw, stored):
    s = equipment.SensorCreate(sensor_id=SENSOR, sensor_name="temp_root_1", sensor_type="t", display_name=raw)
    assert s.display_name == stored
    assert s.sensor_name == "temp_root_1"


def test_display_name_longer_than_the_column_is_refused():
    with pytest.raises(ValueError):
        equipment.SensorUpdate(display_name="x" * 101)


# --- writes -------------------------------------------------------------------------------------

def test_add_sensor_stores_the_display_name(client_for):
    session = Session({"SELECT 1 FROM equipment": [{"x": 1}], "INSERT INTO sensors": _echo})
    r = client_for(session).post(f"/api/equipment/{EQ}/sensors", json={
        "sensor_id": SENSOR, "sensor_name": "bus_voltage", "sensor_type": "voltage",
        "display_name": " Bus voltage "})
    assert r.status_code == 201
    sql, params = next(s for s in session.statements if s[0].startswith("INSERT"))
    assert ":display_name" in sql and params["display_name"] == "Bus voltage"
    assert params["sensor_name"] == "bus_voltage"


def test_bulk_add_stores_each_display_name(client_for):
    session = Session({"SELECT sensor_count FROM equipment": [{"sensor_count": 2}],
                       "INSERT INTO sensors": _echo})
    r = client_for(session).post(f"/api/equipment/{EQ}/sensors/bulk", json={"sensors": [
        {"sensor_id": SENSOR, "sensor_name": "ppfd", "sensor_type": "light", "display_name": "Light (PPFD)"},
        {"sensor_id": str(uuid.uuid4()), "sensor_name": "co2", "sensor_type": "gas"},
    ]})
    assert r.status_code == 201
    inserts = [p for sql, p in session.statements if sql.startswith("INSERT")]
    assert [p["display_name"] for p in inserts] == ["Light (PPFD)", None]


def test_update_sets_and_clears_the_display_name_and_never_the_key(client_for):
    session = Session({"UPDATE sensors": lambda p: [{"sensor_id": SENSOR, "sensor_name": "co2", **p}]})
    c = client_for(session)
    r = c.patch(f"/api/equipment/{EQ}/sensors/{SENSOR}", json={"display_name": " CO₂ "})
    assert r.status_code == 200 and r.json()["display_name"] == "CO₂"
    r = c.patch(f"/api/equipment/{EQ}/sensors/{SENSOR}", json={"display_name": ""})
    assert r.status_code == 200 and r.json()["display_name"] is None
    sql, params = session.statements[-1]
    assert "sensor_name" not in sql.split("WHERE")[0]
    assert params["equipment_id"] == EQ and params["sensor_id"] == SENSOR


def test_update_refuses_a_rename_of_the_key_and_an_empty_body(client_for):
    c = client_for(Session())
    assert c.patch(f"/api/equipment/{EQ}/sensors/{SENSOR}", json={"sensor_name": "other"}).status_code == 400
    assert c.patch(f"/api/equipment/{EQ}/sensors/{SENSOR}", json={}).status_code == 400
    assert c.patch(f"/api/equipment/{EQ}/sensors/{SENSOR}", json={"display_name": "x" * 101}).status_code == 422


def test_update_of_a_missing_sensor_is_404(client_for):
    assert client_for(Session()).patch(
        f"/api/equipment/{EQ}/sensors/{SENSOR}", json={"display_name": "a"}).status_code == 404


# --- latest values: the cache endpoint and the websocket ------------------------------------------

def _select_columns(sql):
    """The output names of a SELECT list: the alias, else the bare column."""
    body = re.search(r"SELECT(.*?)FROM", sql, re.S).group(1)
    names = []
    for part in body.split(","):
        part = part.strip()
        alias = re.search(r"\bAS\s+(\w+)\s*$", part, re.I)
        names.append(alias.group(1) if alias else re.sub(r"::\w+$", "", part).split(".")[-1])
    return names


def test_both_latest_value_paths_select_every_name_field():
    # The cache endpoint and the websocket build the same entry; each SELECT list must carry every
    # field the entry is given, or sensor_names() raises on the row.
    for fn in (cache.get_cached_sensors, websocket._load_names):
        cols = _select_columns(inspect.getsource(fn))
        assert set(websocket.SENSOR_NAME_FIELDS) <= set(cols), fn.__name__


def test_cache_endpoint_carries_the_display_name(client_for, monkeypatch):
    company = uuid.uuid4()
    session = Session({"FROM sensors s": [{"sensor_id": SENSOR, "sensor_name": "ppfd",
                                           "display_name": "Light", "equipment_id": EQ,
                                           "equipment_name": "Cabinet"}]})

    async def all_sensors():
        return {SENSOR: {"value": 1.0, "company_id": str(company)}}

    monkeypatch.setattr(cache.redis_client, "redis", object())
    monkeypatch.setattr(cache.redis_client, "get_all_sensors", all_sensors)
    c = client_for(session)
    main.app.dependency_overrides[current_active_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(), email="op@example.com", company_id=company, role="engineer",
        is_superuser=False, is_active=True)
    entry = c.get("/api/cache/sensors").json()["sensors"][SENSOR]
    assert entry["sensor_name"] == "ppfd" and entry["display_name"] == "Light"
    assert entry["equipment_name"] == "Cabinet" and entry["value"] == 1.0


@pytest.mark.asyncio
async def test_websocket_update_carries_the_display_name(monkeypatch):
    websocket._names_cache.clear()

    async def load(company_id, sensor_ids):
        return {sid: websocket.sensor_names(SimpleNamespace(
            sensor_name="co2", display_name="CO₂", equipment_name="Cabinet")) for sid in sensor_ids}

    monkeypatch.setattr(websocket, "_load_names", load)
    out = await websocket.enrich_sensors_with_names({SENSOR: {"value": 400}}, str(uuid.uuid4()))
    assert out[SENSOR] == {"value": 400, "sensor_name": "co2", "display_name": "CO₂", "equipment_name": "Cabinet"}


# --- alert history --------------------------------------------------------------------------------

def test_every_alert_column_survives_the_response_model():
    # A column the SELECT reads but AlertResponse lacks is dropped at the door, silently.
    cols = set(_select_columns(alerts_history._ALERT_SELECT))
    fields = set(alerts_history.AlertResponse.model_fields)
    assert {"sensor_display_name", "rule_name", "detection_type"} <= cols
    assert cols <= fields, cols - fields


# --- alert rules ----------------------------------------------------------------------------------

def _rule(**over):
    row = {"rule_id": uuid.uuid4(), "name": "r", "detection_type": "threshold", "enabled": True,
           "sensor_id": None, "equipment_id": None, "model_id": None, "sensor_pattern": None,
           "sensor_name": None, "sensor_display_name": None, "equipment_name": None,
           "model_name": None, "model_version": None}
    row.update(over)
    return row


def test_rule_list_joins_the_target_names_inside_the_tenant_schema(client_for):
    live, dangling, model = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = Session({"FROM alert_rules r": [
        _rule(sensor_id=live, sensor_name="temp_root_1", sensor_display_name="Root zone"),
        _rule(sensor_id=dangling),  # its sensor was deleted: the join finds nothing
        _rule(detection_type="ml", equipment_id=uuid.uuid4(), equipment_name="Pump A",
              model_id=model, model_name="pump-iforest", model_version=3),
    ]})
    rules = client_for(session).get("/api/alert-rules?enabled_only=false").json()
    sql, _ = session.statements[-1]
    # Unqualified table names: they resolve in the tenant's own schema (search_path, ADR-0003).
    for join in ("LEFT JOIN sensors s", "LEFT JOIN equipment e", "LEFT JOIN ml_models m"):
        assert join in sql
    assert '"' not in sql and "public." not in sql

    assert rules[0]["sensor_id"] == str(live) and rules[0]["sensor_display_name"] == "Root zone"
    assert rules[0]["sensor_name"] == "temp_root_1"
    assert rules[1]["sensor_id"] == str(dangling) and rules[1]["sensor_name"] is None
    assert rules[2]["equipment_name"] == "Pump A" and rules[2]["model_name"] == "pump-iforest"
    assert rules[2]["model_id"] == str(model)


def test_rule_list_filters_name_the_rule_table(client_for):
    session = Session()
    client_for(session).get("/api/alert-rules?detection_type=ml")
    sql, params = session.statements[-1]
    assert "r.detection_type = :detection_type" in sql and "r.enabled = true" in sql
    assert params == {"detection_type": "ml"}
    assert set(_select_columns(alert_rules.RULE_LIST_SELECT)) >= {
        "sensor_name", "sensor_display_name", "equipment_name", "model_name"}
