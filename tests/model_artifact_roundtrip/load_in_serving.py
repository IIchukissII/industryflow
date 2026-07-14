# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Half two of ADR-0027's empirical gate: LOAD, inside the real ml_service image.

This runs in the actual serving image and loads what the authoring kernel trained, through the same
call the inference path makes — `mlflow.pyfunc.load_model` on a `runs:/<id>/model` URI
(`services/ml_service/api/routers/inference.py`). It then scores the probe input and demands the same
answer the kernel got.

Why "it loaded" is not the assertion. An unpickle across a numpy major does not reliably raise; it can
succeed and score WRONG, which is the failure mode that actually matters — the drift lane (ADR-0021)
trusts these outputs enough to alert on them, so a model that loads and quietly lies is worse than one
that crashes. Hence: compare the predictions, not the exit code.
"""

from __future__ import annotations

import json
import os
import sys
import warnings

import mlflow
import numpy as np
import sklearn
import skops
import xgboost

ARTIFACTS = os.environ.get("ROUNDTRIP_DIR", "/artifacts")

# See train_in_kernel.py: mlflow 3.14 raises on a file-store backend unless this is set. The file
# store stands in for the tracking backend; the pickle it carries is the thing under test.
os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

# The kernel and this image are held to numpy-major / sklearn-major.minor equality, so a correct
# round trip should be exact. The tolerance is here for float summation order across BLAS builds,
# not to paper over a version gap — set it loose enough to hide one and the gate stops meaning
# anything.
TOLERANCE = 1e-9


def main() -> int:
    mlflow.set_tracking_uri(f"file://{ARTIFACTS}/mlruns")

    with open(f"{ARTIFACTS}/manifest.json") as fh:
        manifest = json.load(fh)

    probe = np.asarray(manifest["probe"])
    trained_with = manifest["trained_with"]

    print(f"trained in: python {trained_with['python']}, numpy {trained_with['numpy']}, "
          f"scikit-learn {trained_with['scikit-learn']}, xgboost {trained_with['xgboost']}, "
          f"skops {trained_with.get('skops')}")
    print(f"serving in: python {sys.version.split()[0]}, numpy {np.__version__}, "
          f"scikit-learn {sklearn.__version__}, xgboost {xgboost.__version__}, "
          f"skops {skops.__version__}")
    print()

    failures = []
    for flavor, entry in manifest["models"].items():
        uri = f"runs:/{entry['run_id']}/model"

        # sklearn raises InconsistentVersionWarning on ANY version difference at unpickle time, even
        # a patch. ADR-0027 permits patch drift, so this is surfaced rather than fatal — but it is
        # never swallowed: it is the far end telling us, in its own words, that the pins moved.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                model = mlflow.pyfunc.load_model(uri)
                actual = np.asarray(model.predict(probe)).ravel()
            except Exception as exc:  # noqa: BLE001 — any failure to load IS the finding
                print(f"  FAIL {flavor}: could not load {uri}")
                print(f"       {type(exc).__name__}: {exc}")
                failures.append(flavor)
                continue

            for w in caught:
                if "InconsistentVersion" in type(w.message).__name__:
                    print(f"  WARN {flavor}: {w.message}")

        expected = np.asarray(entry["expected"])
        drift = np.abs(actual - expected).max()
        if drift > TOLERANCE:
            print(f"  FAIL {flavor}: loaded, but SCORES DIFFERENTLY (max delta {drift:.3e})")
            print(f"       kernel : {expected.tolist()}")
            print(f"       serving: {actual.tolist()}")
            print("       This is the dangerous case: the artifact crossed without raising and the")
            print("       model now lies. Alerts are raised on these numbers (ADR-0021).")
            failures.append(flavor)
        else:
            print(f"  ok   {flavor}: loaded and reproduces the kernel's predictions "
                  f"(max delta {drift:.1e})")

    print()
    if failures:
        print(
            f"ADR-0027 round-trip FAILED for: {', '.join(failures)}.\n"
            "A model trained in the authoring kernel image does not survive the journey into the\n"
            "serving image. This is the gate, and it is the authority — a green declarative parity\n"
            "check next to a red round trip means the version rule is wrong, not this test."
        )
        return 1

    print("Every flavor trained in the authoring kernel loads and scores identically in the serving "
          "image (ADR-0027 dec 5).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
