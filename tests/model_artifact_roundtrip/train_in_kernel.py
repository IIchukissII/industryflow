# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Half one of ADR-0027's empirical gate: TRAIN, inside the real authoring kernel image.

This runs in `services/notebook_hub/Dockerfile.authoring` — the actual image a data scientist gets
at spawn — and does what the blessed `getting-started.ipynb` does: fits an estimator and logs it with
`mlflow.sklearn.log_model`. It then records what the model predicts for a fixed input, so the other
half can check that the far end gets the same answer.

The counterpart (`load_in_serving.py`) runs in the real `ml_service` image and loads what this wrote.
Together they are the authority ADR-0027 dec 5 asks for: not "do two version strings match" but "does
the artifact survive the journey". A version rule alone is the class of check ADR-0026 was written
about — it describes the artifact instead of running it.

Deliberately trivial models. This is a test of the BOUNDARY, not of the model: what must be exercised
is the pickle crossing a process, an image and an interpreter — not anyone's accuracy.
"""

from __future__ import annotations

import json
import os
import sys

import mlflow
import mlflow.sklearn
import mlflow.xgboost
import numpy as np
import sklearn
import skops
import xgboost
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ARTIFACTS = os.environ.get("ROUNDTRIP_DIR", "/artifacts")

# The kernel logs to a real MLflow server over HTTP — which is what it does in production, where the
# tracking gateway (ADR-0019) sits in front of exactly this API. It is NOT a shortcut around a file
# store; it is the shorter of the two paths to being faithful, and the file store turned out to be a
# trap in its own right:
#
#   mlflow's FileStore is BROKEN ON PYTHON 3.14 — it writes a run and then cannot read it back
#   ("Run '<id>' not found" out of create_run). Same family as #222 (mlflow's *server* dies on 3.14),
#   and the kernel is now a 3.14 image. Nothing in production touches a file store, so this costs the
#   product nothing — but a harness built on one would have been red for a reason that has nothing to
#   do with the boundary it is supposed to be testing.
TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")

# Fixed data and fixed seeds: the far end must reproduce these predictions exactly, so nothing here
# may depend on the wall clock, the platform's RNG, or thread scheduling.
RNG = np.random.default_rng(20260714)
X = RNG.normal(size=(200, 4))
y = X[:, 0] * 2.0 - X[:, 1] + RNG.normal(scale=0.1, size=200)

# The input the two halves agree to compare on. Held in the manifest rather than recomputed on the
# far side, so a divergence in numpy's RNG could never masquerade as agreement.
PROBE = RNG.normal(size=(5, 4))


def main() -> int:
    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment("adr-0027-roundtrip")

    print(f"kernel: python {sys.version.split()[0]}, numpy {np.__version__}, "
          f"scikit-learn {sklearn.__version__}, xgboost {xgboost.__version__}, "
          f"skops {skops.__version__}")

    manifest: dict[str, dict] = {}

    # Two flavors, because both are on the artifact path and they are serialized COMPLETELY
    # DIFFERENTLY — a fact that is easy to get wrong from the outside, and that ADR-0027's first draft
    # did get wrong (it assumed both were pickles):
    #
    #   sklearn -> `mlflow.sklearn`, which in mlflow 3 defaults to serialization_format="skops".
    #       The artifact is `model.skops`, a structured, version-checked format — NOT a pickle.
    #   xgboost -> `mlflow.xgboost`, which writes a NATIVE BOOSTER (json/ubj). Not a pickled wrapper.
    #       Routing an XGBRegressor through the sklearn flavor does not merely work-badly, it is
    #       REFUSED: skops rejects xgboost's types as untrusted. (test_untrusted below asserts that,
    #       so a future default flip cannot silently turn our artifacts back into pickles.)
    #
    # Each flavor gets logged the way a real notebook would log it, then loaded at the far end the way
    # inference.py loads it. That is the whole gate.
    sk_model = Pipeline(
        [("scale", StandardScaler()), ("rf", RandomForestRegressor(n_estimators=8, random_state=0))]
    ).fit(X, y)
    xgb_model = xgboost.XGBRegressor(n_estimators=8, max_depth=3, random_state=0).fit(X, y)

    flavors = [
        ("sklearn", sk_model, mlflow.sklearn),
        ("xgboost", xgb_model, mlflow.xgboost),
    ]

    for name, estimator, flavor in flavors:
        expected = estimator.predict(PROBE)

        with mlflow.start_run(run_name=f"roundtrip-{name}") as run:
            # log_model, not save_model: the point is to cross the registry the way a real notebook
            # does (ADR-0019), not to hand-place a file the far end then reads.
            flavor.log_model(estimator, name="model")
            manifest[name] = {
                "run_id": run.info.run_id,
                "expected": expected.tolist(),
            }
        print(f"  logged {name} via {flavor.__name__}: run {manifest[name]['run_id']}")

    # Assert the sklearn artifact really is skops and not a pickle. Not pedantry: mlflow chooses this
    # format by DEFAULT, and if a future version flips the default back — or if skops silently fails
    # to import and mlflow falls back — the artifacts quietly become pickles again, the serving path
    # goes back to executing arbitrary code from a notebook, and every version assumption in ADR-0027
    # changes underneath us. That is worth one line and a loud failure.
    sk_uri = mlflow.artifacts.download_artifacts(
        run_id=manifest["sklearn"]["run_id"], artifact_path="model"
    )
    written = sorted(os.listdir(sk_uri))
    if not any(f.endswith(".skops") for f in written):
        print(f"FAIL: the sklearn flavor did not write a .skops artifact — it wrote {written}.")
        print("      mlflow's sklearn serialization default has changed. ADR-0027 assumes skops;")
        print("      re-read the ADR before changing this test to accept whatever it wrote.")
        return 1
    print(f"  sklearn artifact is skops (not a pickle): {[f for f in written if f.endswith('.skops')]}")

    payload = {
        "probe": PROBE.tolist(),
        "trained_with": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "scikit-learn": sklearn.__version__,
            "xgboost": xgboost.__version__,
            "skops": skops.__version__,
        },
        "models": manifest,
    }
    with open(f"{ARTIFACTS}/manifest.json", "w") as fh:
        json.dump(payload, fh, indent=2)

    print(f"wrote {ARTIFACTS}/manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
