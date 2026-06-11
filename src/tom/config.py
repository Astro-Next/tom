from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from tom.models import (
    AgentSettings,
    DevSettings,
    PatrolSettings,
    RetroSettings,
    ReviewSettings,
    Settings,
)

_INTERVAL_RE = re.compile(r"^(\d+)([mhd])$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_interval_seconds(value: str) -> int:
    m = _INTERVAL_RE.match(value)
    if not m:
        raise ValueError(f"Invalid interval format: {value!r} (expected e.g. '30m', '2h', '1d')")
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        raise ValueError(f"Interval must be positive: {value!r}")
    multiplier = {"m": 60, "h": 3600, "d": 86400}
    return n * multiplier[unit]


def validate_time(value: str) -> str:
    if not _TIME_RE.match(value):
        raise ValueError(f"Invalid time format: {value!r} (expected HH:MM, e.g. '22:00')")
    return value


def _positive_int(value: object, name: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return value


def load_settings(path: Path) -> Settings:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("Config must be a JSON object")
    if "id" not in raw:
        raise ValueError("Config missing required field: 'id'")

    patrol_raw = raw.get("patrol", {})
    patrol = PatrolSettings(
        interval=patrol_raw.get("interval", "30m"),
    )
    parse_interval_seconds(patrol.interval)

    retro_raw = raw.get("retro", {})
    retro = RetroSettings(
        interval=retro_raw.get("interval", "1d"),
        time=retro_raw.get("time", "22:00"),
    )
    parse_interval_seconds(retro.interval)
    validate_time(retro.time)

    agent_raw = raw.get("agent", {})
    agent = AgentSettings(
        timeout=agent_raw.get("timeout", "30m"),
        max_retries=_positive_int(agent_raw.get("maxRetries", 2), "agent.maxRetries"),
    )
    parse_interval_seconds(agent.timeout)

    dev_raw = raw.get("dev", {})
    dev = DevSettings(
        concurrent=_positive_int(dev_raw.get("concurrent", 2), "dev.concurrent"),
    )

    review_raw = raw.get("review", {})
    review = ReviewSettings(
        concurrent=_positive_int(review_raw.get("concurrent", 2), "review.concurrent"),
    )

    return Settings(
        id=raw["id"],
        patrol=patrol,
        retro=retro,
        agent=agent,
        dev=dev,
        review=review,
    )


def generate_default_settings() -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "patrol": {"interval": "30m"},
        "retro": {"interval": "1d", "time": "22:00"},
        "agent": {"timeout": "30m", "maxRetries": 2},
        "dev": {"concurrent": 2},
        "review": {"concurrent": 2},
    }
