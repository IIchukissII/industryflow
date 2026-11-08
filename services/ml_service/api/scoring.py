# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Scoring engineered rows with the detector a model record names (ADR-0010 dec 6, ADR-0028 dec 1).

Inference, prediction drift, the deployment gate and the isolated load probe all score here: the
detector is resolved from the record, and its context is built, in this module only. The probe runs
in another process, so what it needs travels as ``Scoring.as_json()``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Sequence

from extensions import DetectionResult, DetectorContext, get_detector

logger = logging.getLogger(__name__)


class Unscorable(Exception):
    """The record cannot be scored in this deployment."""


class NoDetector(Unscorable):
    """The record names no detector, so nothing declares what its output means (ADR-0028)."""


class DetectorNotLoaded(Unscorable):
    """The record names a detector this deployment has not loaded (an extension module was removed)."""


def detector_name(record: Dict[str, Any]) -> str:
    """The detector the record names, refusing rather than assuming one."""
    name = record.get("detector")
    if not name:
        raise NoDetector("the model records no detector, so nothing declares what its output means")
    if get_detector(name) is None:
        raise DetectorNotLoaded(f"no anomaly detector named {name!r} is loaded")
    return name


def _reconstruction_scale(record: Dict[str, Any]) -> Any:
    """The reconstruction error the model saw in training. It rides in its training metrics; where it
    lives in the schema is still open (ADR-0028 Deferred). Metrics that are not a JSON object carry no
    scale, and the one detector that needs it refuses without one."""
    metrics = record.get("training_metrics") or {}
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except ValueError:
            metrics = None
    if not isinstance(metrics, dict):
        logger.warning("model %s: training metrics are not a JSON object; no reconstruction scale",
                       record.get("model_id") or record.get("id"))
        return None
    return metrics.get("reconstruction_scale")


@dataclass(frozen=True)
class Scoring:
    """Everything a detector is called with besides the rows and the loaded model."""

    detector: str
    feature_names: List[str]
    equipment_id: Any = None
    reconstruction_scale: Any = None

    @classmethod
    def of(cls, record: Dict[str, Any], feature_names: Sequence[str], equipment_id: Any) -> "Scoring":
        return cls(detector=detector_name(record), feature_names=list(feature_names),
                   equipment_id=equipment_id, reconstruction_scale=_reconstruction_scale(record))

    def as_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Scoring":
        return cls(**data)


async def score(scoring: Scoring, model: Any, rows: Sequence[Any], threshold: float) -> List[DetectionResult]:
    """Each row through the record's detector, with the context the contract declares."""
    detector = get_detector(scoring.detector)
    if detector is None:
        raise DetectorNotLoaded(f"no anomaly detector named {scoring.detector!r} is loaded")
    ctx = DetectorContext(feature_names=list(scoring.feature_names), equipment_id=scoring.equipment_id,
                          reconstruction_scale=scoring.reconstruction_scale)
    return [await detector(row, model, threshold, ctx) for row in rows]


# A detector's threshold decides only ``is_anomaly``; the score is the same at any threshold.
_ANY_THRESHOLD = 0.5


def scores_blocking(scoring: Scoring, model: Any, rows: Sequence[Any]) -> List[float]:
    """The rows' scores alone, on an event loop of its own: for a worker thread or the probe process.
    A detector is async by contract but does its model work synchronously, and hundreds of rows must
    not hold the service's loop."""
    return [float(r.score) for r in asyncio.run(score(scoring, model, rows, _ANY_THRESHOLD))]
