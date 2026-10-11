# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Logging sklearn models from a notebook kernel (ADR-0027 dec 13).

skops, the format mlflow writes sklearn models in, does not trust the internal tree structures that
tree-based estimators (random forests, gradient boosting, isolation forests, ...) are built from.
mlflow test-loads a model as it saves it, so logging one of them fails unless the call names those
types:

    mlflow.sklearn.log_model(model, name="model", skops_trusted_types=SKOPS_TRUSTED_TYPES)

The list is the one the serving side grants to models trained in these kernels. Serving decides trust
itself and ignores the list a model carries; this copy only lets the save-time check pass.
"""

SKOPS_TRUSTED_TYPES = [
    "sklearn.tree._tree.Tree",
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
]
