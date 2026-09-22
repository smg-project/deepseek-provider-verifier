"""Strict TOML and JSON loaders for planner inputs."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from .records import Config, Profile


def load_config(path: Path) -> Config:
    """Load a strict provider configuration without resolving credentials."""

    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    endpoints = payload.get("endpoints")
    if isinstance(endpoints, dict):
        payload["endpoints"] = {
            name: _endpoint_with_name(name, value) for name, value in endpoints.items()
        }
    return Config.model_validate(payload)


def load_profile(path: Path) -> Profile:
    """Load a versioned contract profile from JSON."""

    with path.open("r", encoding="utf-8") as handle:
        return Profile.model_validate(json.load(handle))


def _endpoint_with_name(name: str, value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {"name": name, **value}
