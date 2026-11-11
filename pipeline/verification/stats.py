"""Run-level statistics for before/after verification (protocol v1 §8, amended 11 Nov 2026).

Runs are independent (each starts from a reset lab), so groups are compared unpaired, on the
log of the server-side run p50 (latency effects are multiplicative):
- improvement: one-sided Welch t-test (after < before), Holm-corrected across pairs, plus the
  exact Mann-Whitney test as a check, and the ratio of geometric means;
- back to healthy: TOST equivalence. The 90 % CI of the geometric-mean ratio must lie within
  the margin (± 10 %).
"""
from __future__ import annotations

import math
import statistics
from typing import Any

from scipy import stats


def _logs(values: list[float]) -> list[float]:
    return [math.log(v) for v in values if v is not None and v > 0]


def welch_log(before: list[float], after: list[float], confidence: float = 0.90) -> dict[str, Any]:
    """Ratio of geometric means (after/before) with its CI and the one-sided p (after < before)."""
    a, b = _logs(before), _logs(after)
    if len(a) < 2 or len(b) < 2:
        return {"ratio": None, "ci": None, "p_one_sided": None}
    diff = statistics.fmean(b) - statistics.fmean(a)
    va, vb = statistics.variance(a) / len(a), statistics.variance(b) / len(b)
    se = math.sqrt(va + vb)
    if se == 0:
        return {"ratio": math.exp(diff), "ci": [math.exp(diff), math.exp(diff)], "p_one_sided": 0.0 if diff < 0 else 1.0}
    df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    t_crit = stats.t.ppf(0.5 + confidence / 2, df)
    p = stats.t.cdf(diff / se, df)  # P(T <= t): small when after < before
    return {"ratio": math.exp(diff), "ci": [math.exp(diff - t_crit * se), math.exp(diff + t_crit * se)], "p_one_sided": float(p), "df": float(df)}


def mann_whitney_less(before: list[float], after: list[float]) -> float | None:
    if len(before) < 2 or len(after) < 2:
        return None
    return float(stats.mannwhitneyu(after, before, alternative="less", method="exact").pvalue)


def holm(p_values: dict[str, float | None]) -> dict[str, float | None]:
    """Holm-adjusted p-values (family-wise error control across pairs)."""
    items = sorted((p, k) for k, p in p_values.items() if p is not None)
    adjusted, running = {}, 0.0
    m = len(items)
    for i, (p, k) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        adjusted[k] = running
    return {k: adjusted.get(k) for k in p_values}


def equivalent(reference: list[float], candidate: list[float], margin: float = 0.10) -> dict[str, Any]:
    """TOST at alpha 0.05: equivalent if the 90 % CI of the geometric-mean ratio candidate/reference lies within [1 - margin, 1 + margin]."""
    w = welch_log(reference, candidate, confidence=0.90)
    if w["ci"] is None:
        return {"ratio": None, "ci90": None, "equivalent": None}
    return {"ratio": w["ratio"], "ci90": w["ci"], "equivalent": (1 - margin) <= w["ci"][0] and w["ci"][1] <= (1 + margin)}
