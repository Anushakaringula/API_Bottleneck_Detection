"""Random Forest and XGBoost diagnosis classifiers (Phase 5 comparators to the rules).

Inputs: baseline-relative features (relative.py) of one telemetry level. Classes are balanced
by weights; DB and cache faults have fewer runs (no severity levels). A model abstains
(INSUFFICIENT_EVIDENCE) when its highest class probability is below the chosen threshold.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.utils.class_weight import compute_sample_weight
from xgboost import XGBClassifier

from . import INSUFFICIENT

MODELS = ("random_forest", "xgboost")


def matrix(rows: list[dict[str, float]], names: list[str]) -> np.ndarray:
    return np.array([[row.get(n, 0.0) for n in names] for row in rows], dtype=float)


def fit_predict(kind: str, x_train: np.ndarray, y_train: list[str], x_test: np.ndarray, seed: int = 0) -> tuple[list[str], np.ndarray, list[str]]:
    """Train on (x_train, y_train), return (predicted class, class probabilities, class order) for x_test."""
    classes = sorted(set(y_train))
    if len(classes) == 1:  # degenerate fold: only one class to learn
        return [classes[0]] * len(x_test), np.ones((len(x_test), 1)), classes
    index = {c: i for i, c in enumerate(classes)}
    y = np.array([index[c] for c in y_train])
    weights = compute_sample_weight("balanced", y)
    if kind == "random_forest":
        model = RandomForestClassifier(n_estimators=300, random_state=seed, n_jobs=1)
    elif kind == "xgboost":
        model = XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.1, subsample=0.9, colsample_bytree=0.9,
                              random_state=seed, n_jobs=1, eval_metric="mlogloss", verbosity=0)
    else:
        raise ValueError(kind)
    model.fit(x_train, y, sample_weight=weights)
    proba = model.predict_proba(x_test)
    return [classes[i] for i in proba.argmax(axis=1)], proba, classes


def with_abstention(predicted: list[str], proba: np.ndarray, threshold: float) -> list[str]:
    confidence = proba.max(axis=1)
    return [p if c >= threshold else INSUFFICIENT for p, c in zip(predicted, confidence)]


def importances(kind: str, x: np.ndarray, y: list[str], names: list[str], top: int = 8) -> list[tuple[str, float]]:
    """Feature importances of a model trained on all given runs (for reporting which evidence the model uses)."""
    classes = sorted(set(y))
    if len(classes) < 2:
        return []
    index = {c: i for i, c in enumerate(classes)}
    yy = np.array([index[c] for c in y])
    model = (RandomForestClassifier(n_estimators=300, random_state=0, n_jobs=1) if kind == "random_forest"
             else XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.1, random_state=0, n_jobs=1, verbosity=0))
    model.fit(x, yy, sample_weight=compute_sample_weight("balanced", yy))
    pairs = sorted(zip(names, model.feature_importances_), key=lambda p: -p[1])
    return [(n, float(v)) for n, v in pairs[:top]]


def to_python(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value
