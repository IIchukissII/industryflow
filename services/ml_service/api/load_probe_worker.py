# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The isolated half of the empirical check (ADR-0030 dec 7): load an artifact and score it, in a
process of its own, and report what happened as JSON on stdout.

Run as a subprocess of the serving image — so the environment it proves is the one that will serve,
which is the entire point (ADR-0027 dec 5: version equality is a proxy; loading the artifact is the
fact). It is a separate *process* rather than a function call for three reasons, each load-bearing:

  * **an unknown artifact's load path is not a thing to run in the API.** It executes library code
    chosen by a stranger's manifest. Structure was refused earlier, so this is not the last line of
    defence — but a process boundary is cheap and the alternative is trusting that it never was.
  * **it can be given its own environment.** ADR-0030 dec 4 refuses object-serialisation on the
    upload path and deliberately leaves the notebook path alone; MLflow's loader falls back to
    deserialising objects unless told otherwise, and that switch is process-wide. Set here, it binds
    the upload path only — set in the service, it would silently change the rule for kernels too,
    which no decision made.
  * **it can fail without taking the service with it.** A load that segfaults, hangs, or exhausts
    memory is a refusal, not an outage.

Everything it reports is a *fact it observed*, never an opinion: the caller decides what a failure
means.
"""
from __future__ import annotations

import json
import sys


def _synthetic_frame(signature):
    """One row shaped like what the model says it takes.

    The values are meaningless and that is fine — the question is whether this environment can put an
    input of the declared shape through this artifact and get a number back, not whether the number
    is any good. Nobody can answer the second question about a model they have never seen.
    """
    import numpy as np
    import pandas as pd

    inputs = signature.inputs.to_dict()
    row = {}
    for spec in inputs:
        name = spec.get("name")
        t = str(spec.get("type", "double"))
        if name is None:            # a tensor-spec artifact declares no column names
            return np.zeros((1, 1), dtype=np.float64)
        if t in ("double", "float"):
            row[name] = [0.0]
        elif t in ("integer", "long"):
            row[name] = [0]
        elif t == "boolean":
            row[name] = [False]
        else:
            row[name] = [""]
    return pd.DataFrame(row)


def probe(model_uri: str) -> dict:
    import mlflow
    from mlflow.models import Model

    try:
        meta = Model.load(model_uri)
    except Exception as exc:  # noqa: BLE001 — any failure here is "we could not read it"
        return {"ok": False, "stage": "manifest", "error": f"{type(exc).__name__}: {exc}"}

    if meta.signature is None:
        # dec 7 scores it "against its declared signature". Without one there is nothing to build an
        # input from, so the check cannot run — and a check that cannot run is not a pass.
        return {"ok": False, "stage": "signature",
                "error": "the artifact declares no input signature, so nothing can establish that "
                         "this environment is able to score it"}

    try:
        model = mlflow.pyfunc.load_model(model_uri)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "load", "error": f"{type(exc).__name__}: {exc}"}

    try:
        frame = _synthetic_frame(meta.signature)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "signature", "error": f"{type(exc).__name__}: {exc}"}

    try:
        prediction = model.predict(frame)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "score", "error": f"{type(exc).__name__}: {exc}"}

    return {"ok": True, "stage": "score", "output_type": type(prediction).__name__}


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "stage": "invoke", "error": "expected one model URI"}))
        return 2
    print(json.dumps(probe(sys.argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
