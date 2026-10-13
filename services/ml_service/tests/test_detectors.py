# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the anomaly-detector contract (ADR-0010, completed by ADR-0028).

The tests that matter here are the ones nobody wrote before: **what does a NORMAL reading score?**
Nothing asserted that, which is how #236 — every IsolationForest prediction scoring 1.0 — survived in
the platform's default detector family.
"""

import sys
from pathlib import Path

import numpy as np
import pytest
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.neural_network import MLPRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from extensions import (  # noqa: E402
    ANOMALY_PROBABILITY,
    OUTLIER_SCORE,
    RECONSTRUCTION_ERROR,
    DetectorContext,
    UninterpretableModel,
    detector_capabilities,
    detectors_for_flavor,
    get_detector,
)
import extensions.builtins_detectors  # noqa: E402,F401  (registers the built-ins)

RNG = np.random.default_rng(20260714)
TRAIN = RNG.normal(size=(300, 4))
NORMAL = np.zeros((1, 4))          # dead centre of the training distribution
OUTLIER = np.full((1, 4), 9.0)     # far outside it


class TestIssue236:
    """The regression net for the bug this ADR was written around.

    IsolationForest returns +1 for NORMAL and -1 for anomaly; XGBoost returns +1 for ANOMALY. The old
    detector read that label and mapped BOTH +1 and -1 to score 1.0 — so an IsolationForest fired an
    anomaly on every reading, including one from the middle of its own training data.
    """

    @pytest.fixture
    def forest(self):
        return IsolationForest(random_state=0).fit(TRAIN)

    @pytest.mark.asyncio
    async def test_a_normal_reading_does_not_score_as_an_anomaly(self, forest):
        detector = get_detector("sklearn")
        result = await detector(NORMAL, forest, 0.5, DetectorContext())
        # THE assertion that was missing. Under the old code this was 1.0.
        assert result.score < 0.5, f"a point in the training distribution scored {result.score}"
        assert result.is_anomaly is False

    @pytest.mark.asyncio
    async def test_an_outlier_still_scores_as_an_anomaly(self, forest):
        detector = get_detector("sklearn")
        result = await detector(OUTLIER, forest, 0.5, DetectorContext())
        assert result.score > 0.5
        assert result.is_anomaly is True

    @pytest.mark.asyncio
    async def test_the_outlier_scores_higher_than_the_normal_point(self, forest):
        # Ordering is the property that actually matters — a threshold is only meaningful if the
        # score is monotone in "how anomalous".
        detector = get_detector("sklearn")
        normal = await detector(NORMAL, forest, 0.5, DetectorContext())
        outlier = await detector(OUTLIER, forest, 0.5, DetectorContext())
        assert outlier.score > normal.score

    @pytest.mark.asyncio
    async def test_it_reads_the_continuous_score_not_the_label(self, forest):
        detector = get_detector("sklearn")
        result = await detector(OUTLIER, forest, 0.5, DetectorContext())
        assert result.detail["semantics"] == OUTLIER_SCORE
        assert "raw_outlier_score" in result.detail  # the sklearn score itself, not a ±1 verdict

    @pytest.mark.asyncio
    async def test_the_score_is_usable_at_the_PLATFORM_default_threshold(self, forest):
        """The test whose absence hid a second bug — and it was found on the box, not in CI.

        The first cut of this fix squashed `decision_function` directly. That value is O(0.1), so every
        score landed in ~[0.45, 0.56] — and the platform's DEFAULT threshold is **0.85**
        (`InferenceRequest.threshold`, and `anomaly_threshold` in the alert worker). A far outlier
        scored 0.54 and would never have fired: #236 inverted, from all-false-positives to
        all-false-NEGATIVES.

        The tests passed anyway, because they asserted against a threshold of 0.5 — MY assumption
        rather than the platform's. So this asserts against the number the product actually ships.
        """
        PLATFORM_DEFAULT = 0.85  # services/ml_service/api/routers/inference.py: InferenceRequest
        detector = get_detector("sklearn")

        outlier = await detector(OUTLIER, forest, PLATFORM_DEFAULT, DetectorContext())
        assert outlier.score >= PLATFORM_DEFAULT, (
            f"a clear outlier scored {outlier.score:.3f}, under the platform's own default threshold "
            f"of {PLATFORM_DEFAULT} — it could never fire an alert"
        )
        assert outlier.is_anomaly is True

        normal = await detector(NORMAL, forest, PLATFORM_DEFAULT, DetectorContext())
        assert normal.score < 0.5, f"a normal reading scored {normal.score:.3f}"
        assert normal.is_anomaly is False


class TestClassifierIsUnaffected:
    """A binary classifier's probability was always read correctly; it must stay that way."""

    @pytest.mark.asyncio
    async def test_a_classifier_scores_from_its_probability(self):
        y = (np.linalg.norm(TRAIN, axis=1) > 2.5).astype(int)  # 1 == anomalous
        clf = RandomForestClassifier(n_estimators=10, random_state=0).fit(TRAIN, y)
        detector = get_detector("sklearn")
        result = await detector(NORMAL, clf, 0.5, DetectorContext())
        assert result.detail["semantics"] == ANOMALY_PROBABILITY
        assert 0.0 <= result.score <= 1.0
        assert result.is_anomaly is False


class TestAutoencoder:
    """The case that proves semantics cannot be inferred: the signal is not IN the output."""

    @pytest.fixture
    def autoencoder(self):
        # Trained to reproduce its input — the output is a reconstruction, not a verdict.
        return MLPRegressor(hidden_layer_sizes=(2,), max_iter=800, random_state=0).fit(TRAIN, TRAIN)

    @pytest.mark.asyncio
    async def test_an_outlier_reconstructs_worse_than_a_normal_point(self, autoencoder):
        detector = get_detector("autoencoder")
        ctx = DetectorContext(reconstruction_scale=0.5)
        normal = await detector(NORMAL, autoencoder, 0.9, ctx)
        outlier = await detector(OUTLIER, autoencoder, 0.9, ctx)
        assert outlier.score > normal.score
        assert outlier.detail["reconstruction_error"] > normal.detail["reconstruction_error"]
        assert outlier.detail["semantics"] == RECONSTRUCTION_ERROR

    @pytest.mark.asyncio
    async def test_without_a_scale_it_refuses_rather_than_inventing_one(self, autoencoder):
        # An MSE of 0.4 is catastrophic for one model and unremarkable for another. A made-up scale
        # is a made-up alert, so the detector declines.
        detector = get_detector("autoencoder")
        with pytest.raises(UninterpretableModel, match="reconstruction-error scale"):
            await detector(NORMAL, autoencoder, 0.5, DetectorContext())

    @pytest.mark.asyncio
    async def test_a_model_that_does_not_reconstruct_is_refused(self):
        # An estimator whose output shape does not match its input is not an autoencoder, whatever the
        # model record claims.
        y = np.linalg.norm(TRAIN, axis=1)
        regressor = MLPRegressor(hidden_layer_sizes=(3,), max_iter=200, random_state=0).fit(TRAIN, y)
        detector = get_detector("autoencoder")
        with pytest.raises(UninterpretableModel, match="reconstruct"):
            await detector(NORMAL, regressor, 0.5, DetectorContext(reconstruction_scale=0.5))


class TestRefusalRatherThanGuessing:
    """ADR-0028 dec 2. A confident wrong score is worse than an honest refusal."""

    @pytest.mark.asyncio
    async def test_a_bare_label_with_no_score_is_refused(self):
        class LabelOnlyModel:
            def predict(self, X):
                return np.array([1])   # 1 == normal? anomalous? nothing here can say.

        detector = get_detector("sklearn")
        with pytest.raises(UninterpretableModel, match="cannot be established"):
            await detector(NORMAL, LabelOnlyModel(), 0.5, DetectorContext())


class TestTheRegistryIsTheDeclaration:
    """ADR-0028 dec 1/4/5 — semantics and flavors are declared, and discoverable."""

    def test_every_builtin_declares_its_semantics(self):
        for cap in detector_capabilities():
            assert cap["semantics"], f"detector {cap['name']} declares no semantics"

    def test_detectors_can_be_found_by_the_flavor_they_score(self):
        assert "sklearn" in detectors_for_flavor("mlflow.sklearn")
        assert "autoencoder" in detectors_for_flavor("mlflow.sklearn")

    def test_nothing_here_claims_to_score_torch(self):
        # The honest state of the world: this image cannot serve a torch model, and says so, rather
        # than loading it and guessing at what its output means.
        assert detectors_for_flavor("mlflow.pytorch") == []


class TestThroughMlflowsWrapper:
    """Detectors meet models as MLflow loads them, not as bare estimators. Every test above hands
    them a bare estimator, which is how an xgboost classifier came to be scored without ever being
    asked for its probability: the private reach into the wrapper found sklearn's estimator and
    stopped at xgboost's wrapper, which exposes only `predict`."""

    @pytest.fixture
    def xgb_classifier(self, tmp_path):
        xgboost = pytest.importorskip("xgboost")
        import mlflow.pyfunc
        import mlflow.xgboost

        y = (np.linalg.norm(TRAIN, axis=1) > 2.5).astype(int)  # 1 == anomalous
        clf = xgboost.XGBClassifier(n_estimators=10, max_depth=2, random_state=0).fit(TRAIN, y)
        path = str(tmp_path / "xgb")
        mlflow.xgboost.save_model(clf, path)
        return clf, mlflow.pyfunc.load_model(path)

    @pytest.mark.asyncio
    async def test_an_xgboost_classifier_is_scored_from_its_probability(self, xgb_classifier):
        clf, loaded = xgb_classifier
        result = await get_detector("sklearn")(OUTLIER, loaded, 0.5, DetectorContext())
        assert result.detail["semantics"] == ANOMALY_PROBABILITY
        assert result.score == pytest.approx(float(clf.predict_proba(OUTLIER)[0][1]), abs=1e-6)

    @pytest.mark.asyncio
    async def test_an_sklearn_model_is_still_reached_through_its_wrapper(self, tmp_path):
        import mlflow.pyfunc
        import mlflow.sklearn

        forest = IsolationForest(random_state=0).fit(TRAIN)
        path = str(tmp_path / "if")
        mlflow.sklearn.save_model(forest, path, serialization_format="skops",
                                  skops_trusted_types=["sklearn.tree._tree.Tree"])
        import skops_trust  # the serving loader, with the kernel's grant
        loaded = skops_trust.load_pyfunc(path, "kernel")
        result = await get_detector("sklearn")(NORMAL, loaded, 0.5, DetectorContext())
        assert result.detail["semantics"] == OUTLIER_SCORE
        assert result.is_anomaly is False


class TestAFloatIsNotADeclaration:
    """ADR-0028 dec 2: the platform never infers semantics from a prediction's dtype. A float output
    was read as a 0-1 anomaly score and clamped — a regressor's 37.2 became a certain anomaly."""

    class FloatModel:
        def __init__(self, value):
            self.value = value

        def predict(self, X):
            return np.array([self.value])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [0.3, 37.2, -4.0])
    async def test_the_compatibility_detector_refuses_an_undeclared_float(self, value):
        with pytest.raises(UninterpretableModel):
            await get_detector("sklearn")(NORMAL, self.FloatModel(value), 0.5, DetectorContext())

    @pytest.mark.asyncio
    async def test_a_model_registered_as_direct_is_scored_from_its_output(self):
        result = await get_detector("direct")(NORMAL, self.FloatModel(0.9), 0.85, DetectorContext())
        assert result.score == pytest.approx(0.9)
        assert result.is_anomaly is True
        assert result.detail["semantics"] == "direct_score"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [1.5, -0.1, float("nan")])
    async def test_a_direct_score_outside_0_1_is_refused_not_clamped(self, value):
        with pytest.raises(UninterpretableModel, match="not a score in that range"):
            await get_detector("direct")(NORMAL, self.FloatModel(value), 0.5, DetectorContext())

    def test_direct_score_is_now_implemented_by_something(self):
        """It was in the vocabulary with no detector behind it, so an upload declaring it could only
        ever be refused."""
        from extensions import detectors_for
        assert "direct" in detectors_for("direct_score", "mlflow.sklearn")
