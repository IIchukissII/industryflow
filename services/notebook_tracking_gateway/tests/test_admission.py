# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for structural artifact admission (ADR-0030 dec 4). Pure rule, no I/O — mirroring the
policy/scoping scoping tests.

The manifests here are the ones MLflow 3.14 **actually writes** (probed against real
``save_model`` output on the serving runtime), not invented shapes: an artifact refused by a rule
built against a guessed layout would be a rule that passes its tests and fails on the first real
upload.

Two properties matter beyond "pickle is refused":

  * the rule names **no framework** — a safe format nobody has thought of yet must pass, or the
    refusal becomes a closed list in a core path (ADR-0008 dec 1, ADR-0027's "an instance, not the
    rule");
  * neither half is trusted to speak for the other — a manifest that *declares* safety while a file
    *is* an object stream is still refused, and vice versa.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import admission  # noqa: E402

PICKLE_HEAD = b"\x80\x05\x95\x02I\x00\x00\x00"   # real head of a model.pkl
ZIP_HEAD = b"PK\x03\x04\x14\x00\x00\x00"          # real head of a model.skops
UBJ_HEAD = b"{L\x00\x00\x00\x00\x00\x00"          # real head of a model.ubj


# --- the real manifests, as MLflow 3.14 writes them ------------------------------------------

SAFE_ZIP_MANIFEST = """
flavors:
  python_function:
    loader_module: mlflow.sklearn
    model_path: model.skops
    predict_fn: predict
    python_version: 3.14.6
  sklearn:
    pickled_model: model.skops
    serialization_format: skops
    sklearn_version: 1.9.0
mlflow_version: 3.14.0
"""

PICKLE_MANIFEST = """
flavors:
  python_function:
    loader_module: mlflow.sklearn
    model_path: model.pkl
  sklearn:
    pickled_model: model.pkl
    serialization_format: pickle
    sklearn_version: 1.9.0
mlflow_version: 3.14.0
"""

CLOUDPICKLE_MANIFEST = PICKLE_MANIFEST.replace("serialization_format: pickle",
                                               "serialization_format: cloudpickle")

# A custom pyfunc: cloudpickle by construction, and it declares NO serialization_format at all —
# the case a "look for serialization_format: pickle" rule sails straight past.
PYFUNC_MANIFEST = """
flavors:
  python_function:
    cloudpickle_version: 3.1.2
    loader_module: mlflow.pyfunc.model
    python_model: python_model.pkl
    python_version: 3.14.6
mlflow_version: 3.14.0
"""

NATIVE_BOOSTER_MANIFEST = """
flavors:
  python_function:
    data: model.ubj
    loader_module: mlflow.xgboost
  xgboost:
    data: model.ubj
    model_class: xgboost.sklearn.XGBClassifier
    model_format: ubj
    xgb_version: 3.3.0
mlflow_version: 3.14.0
"""


# --- admitted --------------------------------------------------------------------------------

def test_admits_a_manifest_declaring_a_non_executing_serialisation():
    v = admission.evaluate(SAFE_ZIP_MANIFEST, [("MLmodel", b"flavors:"), ("model.skops", ZIP_HEAD)])
    assert v.admitted, v.reason


def test_admits_a_native_binary_format():
    v = admission.evaluate(NATIVE_BOOSTER_MANIFEST, [("model.ubj", UBJ_HEAD)])
    assert v.admitted, v.reason


def test_admits_a_safe_format_the_rule_has_never_heard_of():
    """The agnosticism property, as a test. A future framework's safe native format must pass on
    structure — the gate does not keep a list of what it likes, and whether this deployment can
    SERVE it is a different question, asked by a discovered registry at registration."""
    unknown = """
flavors:
  python_function:
    loader_module: mlflow.somethingnew
    data: model.bin
  somethingnew:
    data: model.bin
    serialization_format: arrow-ipc
    somethingnew_version: 9.9.9
mlflow_version: 3.14.0
"""
    v = admission.evaluate(unknown, [("model.bin", b"ARROW1\x00\x00")])
    assert v.admitted, v.reason


# --- refused: the manifest declares it ---------------------------------------------------------

def test_refuses_declared_pickle():
    v = admission.evaluate(PICKLE_MANIFEST, [("model.pkl", PICKLE_HEAD)])
    assert v.refused
    assert "execute author-supplied code" in v.reason


def test_refuses_declared_cloudpickle():
    v = admission.evaluate(CLOUDPICKLE_MANIFEST, [("model.pkl", PICKLE_HEAD)])
    assert v.refused


def test_refuses_custom_pyfunc_which_declares_no_serialization_format():
    # The case that motivates walking the manifest generically: nothing here says "pickle".
    v = admission.evaluate(PYFUNC_MANIFEST, [("python_model.pkl", PICKLE_HEAD)])
    assert v.refused
    assert "object stream" in v.reason


def test_refuses_declared_pickle_even_when_no_file_head_gives_it_away():
    # Protocols 0/1 carry no framing to detect; a stream MLflow never declares is one it never
    # loads, so the manifest half is what closes this.
    v = admission.evaluate(PICKLE_MANIFEST, [("model.pkl", b"(dp0\nS'x'\n")])
    assert v.refused


# --- refused: the bytes are it, whatever the manifest claims -----------------------------------

def test_refuses_an_object_stream_wearing_a_safe_name():
    # Manifest declares a non-executing format; the file is a pickle anyway.
    v = admission.evaluate(SAFE_ZIP_MANIFEST, [("model.skops", PICKLE_HEAD)])
    assert v.refused
    assert "whatever the manifest says" in v.reason


def test_refuses_an_object_stream_hiding_in_a_side_file():
    v = admission.evaluate(SAFE_ZIP_MANIFEST,
                           [("model.skops", ZIP_HEAD), ("extra/helper.bin", PICKLE_HEAD)])
    assert v.refused


# --- refused: we cannot establish what it is ---------------------------------------------------

def test_refuses_missing_manifest():
    assert admission.evaluate(None, [("model.skops", ZIP_HEAD)]).refused
    assert admission.evaluate("", [("model.skops", ZIP_HEAD)]).refused


def test_refuses_unreadable_manifest():
    assert admission.evaluate("\tnot: [valid", [("m", ZIP_HEAD)]).refused


def test_refuses_manifest_that_is_not_a_mapping():
    assert admission.evaluate("- just\n- a\n- list", [("m", ZIP_HEAD)]).refused


def test_refuses_manifest_with_no_flavors():
    assert admission.evaluate("mlflow_version: 3.14.0\nflavors: {}", [("m", ZIP_HEAD)]).refused


# --- the parser itself must not be the hole ----------------------------------------------------

def test_manifest_parsing_does_not_construct_python_objects():
    """A gate that refuses code execution while its own parser constructs arbitrary Python would
    hand back exactly what it exists to deny."""
    hostile = "flavors:\n  x: !!python/object/apply:os.system ['echo pwned']\n"
    assert admission.parse_manifest(hostile) is None   # safe_load refuses the tag, never runs it
    assert admission.evaluate(hostile, []).refused


# --- stream framing --------------------------------------------------------------------------

def test_object_stream_framing_is_detected_by_bytes_not_extension():
    assert admission.is_executable_object_stream(b"\x80\x05\x95")
    assert admission.is_executable_object_stream(b"\x80\x02")
    assert not admission.is_executable_object_stream(ZIP_HEAD)
    assert not admission.is_executable_object_stream(UBJ_HEAD)
    assert not admission.is_executable_object_stream(b"")
    assert not admission.is_executable_object_stream(b"\x80")        # truncated
    assert not admission.is_executable_object_stream(b"\x80\x09")    # not a real protocol
