"""Shared decision routing for SQL-adjacent Calliope consumers."""
import os


def decision_model():
    return os.getenv("CLOVER_DECISION_MODEL", "typesafe/jev-1.13")


def decision_endpoint():
    base = os.getenv("RVBBIT_CLOVER_BASE_URL", "").strip().rstrip("/")
    return base + "/v1/decisions" if base else "https://openrouter.ai/api/alpha/decisions"


def decision_key():
    return os.getenv("RVBBIT_CLOVER_KEY", "") if os.getenv("RVBBIT_CLOVER_BASE_URL", "").strip() else os.getenv("OPENROUTER_API_KEY", "")
