"""Deterministic local fixtures; never dispatch model-selected code."""

from typing import Any

FIXTURES = {"harbor": "amber", "orchard": "violet", "station": "cobalt"}
TOOL_SCHEMAS = {
    "lookup_fixture": {
        "type": "object",
        "properties": {"key": {"type": "string", "enum": list(FIXTURES)}},
        "required": ["key"],
        "additionalProperties": False,
    },
    "add_integers": {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    },
}


def lookup_fixture(key: str) -> str:
    if type(key) is not str or key not in FIXTURES:
        raise ValueError("Unknown fixture key")
    return FIXTURES[key]


def add_integers(a: int, b: int) -> int:
    if type(a) is not int or type(b) is not int:
        raise ValueError("Arguments must be integers without coercion")
    return a + b


def execute_tool(name: str, arguments: Any) -> str | int:
    if name not in TOOL_SCHEMAS or type(arguments) is not dict:
        raise ValueError("Only registered mock tools with object arguments may execute")
    if set(arguments) != set(TOOL_SCHEMAS[name]["required"]):
        raise ValueError("Arguments must exactly match the registered schema")
    return {"lookup_fixture": lookup_fixture, "add_integers": add_integers}[name](
        **arguments
    )


def tool_definition(name: str, protocol: str) -> dict:
    function = {
        "name": name,
        "description": "Read a deterministic local test fixture.",
        "parameters": TOOL_SCHEMAS[name],
    }
    return (
        {"type": "function", "function": function}
        if protocol == "chat"
        else {"type": "function", **function}
    )
