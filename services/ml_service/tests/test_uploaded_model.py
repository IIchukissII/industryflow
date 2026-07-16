# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for what the registration gate asks of an externally-authored model (ADR-0030 dec 5/6/8).

The refusals here are the ones a kernel-authored model never needed, because the platform watched it
being made. The property under test throughout: **an assertion by a stranger is not a record**, and
the gate says so by refusing rather than by defaulting.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import uploaded_model as um  # noqa: E402

VOCAB = ("anomaly_probability", "outlier_score", "reconstruction_error", "direct_score")


def _registry(**by_semantics_flavor):
    """A fake detector registry: {(semantics, flavor): [names]}."""
    def detectors_for(semantics, flavor):
        return by_semantics_flavor.get(f"{semantics}|{flavor}", [])
    return detectors_for


REAL = _registry(**{
    "anomaly_probability|mlflow.sklearn": ["classifier"],
    "anomaly_probability|mlflow.xgboost": ["classifier"],
    "outlier_score|mlflow.sklearn": ["isolation_forest"],
    "reconstruction_error|mlflow.pyfunc": ["autoencoder"],
})


def _judge(declared, flavor, detectors_for=REAL):
    return um.judge_semantics(declared, flavor, vocabulary=VOCAB, detectors_for=detectors_for)


# --- provenance is read, never asked (dec 8) ---------------------------------------------------

def test_a_model_with_a_run_is_kernel_authored():
    assert um.provenance_of(mlflow_run_id="abc123", artifact_uri=None) == um.PROVENANCE_KERNEL


def test_a_model_with_no_run_is_uploaded():
    # dec 8 forbids inventing a run, so the absence IS the fact.
    assert um.provenance_of(mlflow_run_id=None, artifact_uri="s3://b/tenant_x/uploads/u1") == um.PROVENANCE_UPLOADED
    assert um.provenance_of(mlflow_run_id="", artifact_uri="s3://b/x") == um.PROVENANCE_UPLOADED


def test_provenance_is_not_a_field_a_caller_could_lie_about():
    # The signature has no `provenance` in it: it is derived from how the model arrived. This test
    # exists so that adding one would have to delete it.
    import inspect
    assert set(inspect.signature(um.provenance_of).parameters) == {"mlflow_run_id", "artifact_uri"}


# --- semantics: absent -> refused, never defaulted (dec 5, ADR-0028 dec 2) ---------------------

def test_an_undeclared_semantics_is_refused_not_defaulted():
    v = _judge(None, "mlflow.sklearn")
    assert v.refused
    assert "will not guess" in v.reason


def test_an_empty_semantics_is_refused():
    assert _judge("", "mlflow.sklearn").refused


def test_an_unknown_semantics_is_refused_and_says_what_is_known():
    v = _judge("vibes", "mlflow.sklearn")
    assert v.refused
    assert "not a score semantics this platform knows" in v.reason
    assert "outlier_score" in v.reason      # an operator can act on it


# --- semantics: known is not enough — something must be able to ACT on it ----------------------

def test_a_known_semantics_with_no_detector_for_that_flavor_is_refused():
    """The conjunction is the point. Both halves are known here — the semantics is in the vocabulary
    and the flavor is one other detectors load — and it is still refused, because no *single*
    detector claims both. Understanding what a number means is no use against an artifact you cannot
    read, and reading it is no use if you would read the output as something else.

    (The pairing is the fake registry's, not this deployment's: the built-in autoencoder does claim
    `mlflow.sklearn`. The rule is what is under test, not the built-ins' current claims.)
    """
    v = _judge("reconstruction_error", "mlflow.sklearn")
    assert v.refused
    assert "nothing in this deployment can score" in v.reason


def test_a_known_semantics_for_an_unservable_flavor_is_refused():
    v = _judge("anomaly_probability", "mlflow.pytorch")
    assert v.refused


def test_a_missing_flavor_is_refused():
    v = _judge("anomaly_probability", None)
    assert v.refused
    assert "does not declare a flavor" in v.reason


def test_a_detector_that_claims_no_flavors_matches_nothing():
    # "makes no claim" is not "handles everything".
    v = _judge("anomaly_probability", "mlflow.sklearn", detectors_for=_registry())
    assert v.refused


# --- admitted ----------------------------------------------------------------------------------

def test_a_declared_semantics_a_detector_implements_for_that_flavor_is_admitted():
    v = _judge("outlier_score", "mlflow.sklearn")
    assert v.admitted, v.reason
    assert v.detector == "isolation_forest"


def test_the_same_semantics_is_admitted_for_every_flavor_a_detector_claims():
    assert _judge("anomaly_probability", "mlflow.sklearn").admitted
    assert _judge("anomaly_probability", "mlflow.xgboost").admitted


def test_the_rule_asks_the_registry_and_keeps_no_list_of_its_own():
    """A deployment that installed an adapter must be able to admit what the adapter handles, with
    no change here — an open framework set cannot be judged by a closed list (ADR-0027 dec 2)."""
    with_adapter = _registry(**{"direct_score|mlflow.somethingnew": ["new_adapter"]})
    v = um.judge_semantics("direct_score", "mlflow.somethingnew",
                           vocabulary=VOCAB, detectors_for=with_adapter)
    assert v.admitted, v.reason
    assert v.detector == "new_adapter"


def test_the_vocabulary_is_injected_so_it_cannot_drift_from_its_owner():
    import inspect
    params = inspect.signature(um.judge_semantics).parameters
    assert "vocabulary" in params and "detectors_for" in params
