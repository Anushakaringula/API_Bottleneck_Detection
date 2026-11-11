"""Diagnosis (Phase 5): which bottleneck caused a detected slowdown? Rules, Random Forest, XGBoost."""

__version__ = "1.0.0"

CLASSES = ("DB_BOTTLENECK", "N_PLUS_ONE", "LARGE_PAYLOAD", "CACHE_OPPORTUNITY", "CPU_BOTTLENECK")
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
