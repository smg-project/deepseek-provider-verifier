"""JSON wire decoding without Python's permissive NaN/Infinity extension."""

import json
from typing import Any


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Non-JSON numeric constant: {value}")


def strict_json_loads(value: str | bytes) -> Any:
    """Decode JSON or raise ValueError; never repair a partial/malformed value."""
    return json.loads(value, parse_constant=_invalid_constant)
