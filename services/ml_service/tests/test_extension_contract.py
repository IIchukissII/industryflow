# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The extension contract, through the reference extension (ADR-0008 dec 1/6, ADR-0010 dec 1/3/5/6).

The reference extension is loaded as the service loads one: named in EXTENSION_MODULES and imported by
the platform's loader, in a fresh interpreter, so each case meets only the registrations its module
makes. Each case asserts from inside that interpreter and prints one JSON line the test reads.

- the configured module registers its transform and detector, which the capabilities route lists;
- its configuration is accepted where configurations are written only when the module is loaded;
- that configuration computes through the engine and the detector dispatches by name, with the
  feature names in its context;
- a definition the transform cannot interpret is refused (ADR-0044 dec 2);
- a module targeting another major, or a newer minor, is refused at load; an older minor loads;
- nothing the platform imports loads an extension it is not configured to load.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
API = REPO / "services" / "ml_service" / "api"
REFERENCE = REPO / "extensions" / "reference"
CONFIG = REFERENCE / "reference_config.json"


def _run(body, modules="", extra_path=()):
    head = textwrap.dedent("""
        import asyncio, json, os, sys
        sys.path[:0] = {path!r}
        from extensions import load_extension_modules
        CONFIG = json.load(open({config!r}))
    """).format(path=[str(API), str(REFERENCE), *map(str, extra_path)], config=str(CONFIG))
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["EXTENSION_MODULES"] = modules
    return subprocess.run([sys.executable, "-c", head + textwrap.dedent(body)], capture_output=True, text=True,
                          env=env, cwd=REPO, timeout=120)


def _ok(r):
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


LOAD = 'load_extension_modules(os.environ["EXTENSION_MODULES"].split(","))'


def test_the_configured_extension_registers_and_is_listed():
    out = _ok(_run(f"""
        from extensions import get_detector, get_transform
        from routers.capabilities import get_capabilities
        before = [get_transform("reference_pressure_margin") is None, get_detector("reference_margin_rule") is None]
        loaded = {LOAD}
        caps = asyncio.run(get_capabilities())
        print(json.dumps({{"before": before, "loaded": loaded, "transforms": caps["transforms"],
                          "detectors": {{d["name"]: d for d in caps["detectors"]}},
                          "api": caps["extension_api_version"]}}))
    """, modules=" reference_extension ,"))
    assert out["before"] == [True, True]
    assert out["loaded"] == ["reference_extension"]
    assert {"reference_pressure_margin", "identity"} <= set(out["transforms"])
    assert out["detectors"]["reference_margin_rule"] == {"name": "reference_margin_rule", "semantics": "direct_score",
                                                         "handles_flavors": []}
    assert out["api"] == "0.5.0"


@pytest.mark.parametrize("modules, accepted", [("reference_extension", True), ("", False)])
def test_its_configuration_is_accepted_where_configurations_are_written_only_when_loaded(modules, accepted):
    out = _ok(_run(f"""
        from pydantic import ValidationError
        from routers import feature_configs
        {LOAD}
        try:
            feature_configs.FeatureConfigCreate(**CONFIG)
            print(json.dumps({{"accepted": True}}))
        except ValidationError as e:
            print(json.dumps({{"accepted": False, "error": str(e)}}))
    """, modules=modules))
    assert out["accepted"] is accepted, out
    if not accepted:
        assert "reference_pressure_margin" in out["error"]


def test_its_configuration_computes_and_the_detector_dispatches_by_name():
    out = _ok(_run(f"""
        import scoring
        from feature_engineering import FeatureEngineeringEngine
        {LOAD}
        engine = FeatureEngineeringEngine(feature_config=CONFIG)
        # Scored as inference scores a model record naming the detector (ADR-0010 dec 6).
        spec = scoring.Scoring.of({{"detector": "reference_margin_rule"}}, engine.get_feature_names(), "pump-1")
        scores = {{}}
        for pressure in (6.0, 9.0, 10.5):
            vector = asyncio.run(engine.transform({{"discharge_pressure": pressure, "flow_rate": 40.0,
                                                   "motor_temperature": 55.0}}))
            [result] = asyncio.run(scoring.score(spec, None, [vector], 0.5))
            scores[str(pressure)] = [result.score, result.is_anomaly, result.detail["margin"]]
        print(json.dumps({{"names": engine.get_feature_names(), "scores": scores}}))
    """, modules="reference_extension"))
    assert out["names"] == ["discharge_pressure", "flow_rate", "motor_temperature", "pressure_margin"]
    assert out["scores"] == {"6.0": [0.0, False, 4.0], "9.0": [0.5, True, 1.0], "10.5": [1.0, True, -0.5]}


def test_the_detector_refuses_features_that_carry_no_margin():
    out = _ok(_run(f"""
        from extensions import DetectorContext, UninterpretableModel, get_detector
        {LOAD}
        try:
            asyncio.run(get_detector("reference_margin_rule")([[1.0, 2.0]], None, 0.5,
                                                              DetectorContext(feature_names=["a", "b"])))
            print(json.dumps({{"refused": False}}))
        except UninterpretableModel as e:
            print(json.dumps({{"refused": str(e)}}))
    """, modules="reference_extension"))
    assert "pressure_margin" in out["refused"]


@pytest.mark.parametrize("definition, reason", [
    ({"sensor": "discharge_pressure", "params": {}}, "params.limit"),
    ({"sensor": "discharge_pressure", "params": {"limit": "high"}}, "params.limit"),
    ({"sensor": "discharge_pressure", "params": {"limit": True}}, "params.limit"),
    ({"params": {"limit": 10.0}}, "names no sensor"),
])
def test_a_definition_the_transform_cannot_interpret_is_refused(definition, reason):
    # ADR-0044 dec 2: no default stands in for a limit or sensor the configuration does not name.
    out = _ok(_run(f"""
        from extensions import InvalidFeature, TransformContext, get_transform
        {LOAD}
        definition = dict(json.loads({json.dumps(definition)!r}), name="margin", type="reference_pressure_margin")
        try:
            value = asyncio.run(get_transform("reference_pressure_margin")(definition, {{"discharge_pressure": 6.0}},
                                                                           TransformContext()))
            print(json.dumps({{"value": value}}))
        except InvalidFeature as e:
            print(json.dumps({{"refused": str(e)}}))
    """, modules="reference_extension"))
    assert reason in out.get("refused", ""), out


@pytest.mark.parametrize("target, loads", [("0.5.0", True), ("0.4.9", True), ("0.6.0", False),
                                           ("1.0.0", False), ("1.5.0", False), ("four", False)])
def test_an_extension_is_loaded_only_against_a_compatible_api(tmp_path, target, loads):
    # ADR-0010 dec 3: the major matches and the target minor is not above the platform's.
    (tmp_path / "versioned_ext.py").write_text(textwrap.dedent(f"""
        from extensions import check_api_version, register_transform
        check_api_version({target!r})

        @register_transform("versioned_ext_value")
        async def value(transformation, sensor_data, ctx):
            return 1.0
    """))
    out = _ok(_run(f"""
        from extensions import get_transform
        try:
            {LOAD}
        except ValueError as e:
            print(json.dumps({{"refused": str(e), "registered": get_transform("versioned_ext_value") is not None}}))
        else:
            print(json.dumps({{"registered": get_transform("versioned_ext_value") is not None}}))
    """, modules="versioned_ext", extra_path=[tmp_path]))
    if loads:
        assert out == {"registered": True}
    else:
        assert target in out.get("refused", ""), out
        assert out["registered"] is False


def test_the_platform_loads_no_extension_it_is_not_configured_to_load():
    # ADR-0008 dec 6 / ADR-0010 dec 1: with the reference on the import path but not configured.
    out = _ok(_run(f"""
        import extensions, feature_engineering, routers
        loaded = {LOAD}
        from extensions import get_transform
        print(json.dumps({{"loaded": loaded, "imported": "reference_extension" in sys.modules,
                          "registered": get_transform("reference_pressure_margin") is not None}}))
    """))
    assert out == {"loaded": [], "imported": False, "registered": False}


def test_no_platform_source_names_the_reference_module():
    offenders = [str(p.relative_to(REPO)) for root in ("services", "libs", "clients")
                 for p in (REPO / root).rglob("*.py")
                 if "tests" not in p.parts and "reference_extension" in p.read_text(errors="ignore")]
    assert offenders == []
