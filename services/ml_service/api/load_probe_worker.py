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
  * **it starts with deserialisation already off.** MLflow's loader deserialises objects unless told
    otherwise; the probe's environment says so before any import, as the service does for its own
    life (ADR-0037 dec 3).
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
    from mlflow.types import DataType

    schema = signature.inputs
    if schema.is_tensor_spec():
        # One sample of each declared tensor, in its declared dtype: the batch dimension (and any
        # other unknown, -1) is 1. MLflow allows a single unnamed tensor, or named ones as a dict.
        tensors = {spec.name: np.zeros(tuple(1 if d in (None, -1) else int(d) for d in spec.shape),
                                       dtype=spec.type)
                   for spec in schema.inputs}
        return tensors if schema.has_input_names() else next(iter(tensors.values()))

    row = {}
    for spec in schema.inputs:
        if spec.type == DataType.string:
            row[spec.name] = pd.Series([""], dtype=object)
        elif spec.type == DataType.binary:
            row[spec.name] = pd.Series([b""], dtype=object)
        elif spec.type == DataType.datetime:
            row[spec.name] = pd.Series([pd.Timestamp(0)])
        elif isinstance(spec.type, DataType):
            # The column's own dtype: MLflow will not narrow a float64/int64 column to float32/int32.
            row[spec.name] = pd.Series(np.zeros(1, dtype=spec.type.to_numpy()))
        else:
            raise ValueError(f"cannot build an input for column {spec.name!r} of type {spec.type}")
    return pd.DataFrame(row)


def _probe(model_uri: str):
    """``(report, loaded model)``; the model is None unless the load succeeded."""
    from mlflow.models import Model

    try:
        meta = Model.load(model_uri)
    except Exception as exc:  # noqa: BLE001 — any failure here is "we could not read it"
        return {"ok": False, "stage": "manifest", "error": f"{type(exc).__name__}: {exc}"}, None

    if meta.signature is None:
        # dec 7 scores it "against its declared signature". Without one there is nothing to build an
        # input from, so the check cannot run — and a check that cannot run is not a pass.
        return {"ok": False, "stage": "signature",
                "error": "the artifact declares no input signature, so nothing can establish that "
                         "this environment is able to score it"}, None

    # Only uploaded artifacts are probed, and they get no trust grant (ADR-0027 dec 13): a skops file
    # that needs a type outside skops' defaults (every tree-based sklearn model) is refused here.
    # The trust list the artifact itself declares is never read.
    import skops_trust
    import uploaded_model

    try:
        model = skops_trust.load_pyfunc(model_uri, uploaded_model.PROVENANCE_UPLOADED)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, skops_trust.UnsafeArtifact):
            return {"ok": False, "stage": "trust",
                    "error": "loading this model would run code its author chose, which an uploaded "
                             f"model may never do (ADR-0030 dec 4): {exc}"}, None
        if skops_trust.is_untrusted_types_error(exc):
            return {"ok": False, "stage": "trust",
                    "error": "the model deserialises into types this platform trusts only for models "
                             "trained in its own kernels, never for uploaded ones (ADR-0027 dec 13, "
                             f"ADR-0030 dec 4): {exc}"}, None
        return {"ok": False, "stage": "load", "error": f"{type(exc).__name__}: {exc}"}, None

    try:
        frame = _synthetic_frame(meta.signature)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "signature", "error": f"{type(exc).__name__}: {exc}"}, None

    try:
        prediction = model.predict(frame)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "score", "error": f"{type(exc).__name__}: {exc}"}, None

    return {"ok": True, "stage": "score", "output_type": type(prediction).__name__}, model


def probe(model_uri: str) -> dict:
    """What loading and scoring ``model_uri`` here showed."""
    return _probe(model_uri)[0]


def score_sample(model, sample: dict) -> dict:
    """The detector's scores on engineered sample rows, in this load (ADR-0035 dec 2), or why not.

    The detector is the platform's or an operator extension's, loaded as the service loads it; the rows
    were engineered by the service from the model's feature configuration.
    """
    import asyncio
    import os

    import numpy as np

    import extensions

    try:
        extensions.load_extension_modules(
            [m for m in os.environ.get("EXTENSION_MODULES", "").split(",") if m.strip()])
        detector = extensions.get_detector(sample.get("detector") or "")
        if detector is None:
            return {"sample_error": f"no detector named {sample.get('detector')!r} is loaded"}
        ctx = extensions.DetectorContext(**(sample.get("context") or {}))
        rows = [np.asarray(r, dtype="float64").reshape(1, -1) for r in sample.get("rows") or []]

        async def run():
            return [(await detector(row, model, 0.5, ctx)).score for row in rows]

        return {"scores": [float(v) for v in asyncio.run(run())]}
    except Exception as exc:  # noqa: BLE001 — the reason is reported, the verdict on the load stands
        return {"sample_error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "stage": "invoke", "error": "expected one model URI"}))
        return 2
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        sample = json.loads(raw) if raw.strip() else None
    except ValueError:
        sample = None
    result, model = _probe(sys.argv[1])
    # The verdict first, on a line of its own: what scoring the sample does next cannot change it.
    print(json.dumps(result), flush=True)
    if result["ok"] and sample:
        print(json.dumps(score_sample(model, sample)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
