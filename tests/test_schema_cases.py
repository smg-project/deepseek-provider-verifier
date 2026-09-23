"""Local-only bounded schema validation and observable capability classification."""

import json
import socket

import httpx
import pytest
from test_depth_catalog import depth_manifest
from test_runner import run
from test_workflows import fixture_body, selected

from deepseek_provider_verifier.schema_cases import (
    schema_matches,
    validate_local_schema,
)


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.invalid/schema"},
        {"$id": "https://example.invalid/schema", "type": "object"},
        {"$defs": {"self": {"$ref": "#/$defs/self"}}, "$ref": "#/$defs/self"},
        {
            "$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}},
            "$ref": "#/$defs/a",
        },
        {"$ref": "#/$defs/missing"},
        {"$dynamicRef": "https://example.invalid/schema"},
        {"type": "not-a-json-type"},
    ],
)
def test_remote_recursive_and_invalid_schemas_rejected_without_network(
    monkeypatch, schema
):
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *a, **k: pytest.fail("Schema resolution touched network"),
    )
    with pytest.raises(ValueError):
        validate_local_schema(schema)


def test_null_optional_and_numeric_types_are_distinct():
    schema = {
        "type": "object",
        "properties": {"n": {"type": ["integer", "null"]}},
        "additionalProperties": False,
    }
    validate_local_schema(schema)
    assert schema_matches(schema, {})
    assert schema_matches(schema, {"n": None})
    assert not schema_matches(schema, {"n": True})
    assert not schema_matches(schema, {"extra": 1})
    assert not schema_matches({"oneOf": [{"type": "number"}, {"type": "integer"}]}, 1)
    assert schema_matches(
        {"$defs": {"a/b": {"type": "integer"}}, "$ref": "#/$defs/a~1b"}, 3
    )


def test_work_is_bounded_before_validator_recursion():
    deep = {"type": "string"}
    for _ in range(40):
        deep = {"type": "object", "properties": {"child": deep}}
    with pytest.raises(ValueError, match="bound|limit"):
        validate_local_schema(deep)
    with pytest.raises(ValueError, match="bound|limit"):
        validate_local_schema({"description": "x" * 65537})
    with pytest.raises(ValueError, match="bound|limit"):
        validate_local_schema({"enum": list(range(3000))})
    assert not schema_matches({"type": "string"}, "x" * 65537)
    # An acyclic graph may still expand exponentially through repeated refs.
    defs = {"n0": {"type": "integer"}}
    for i in range(1, 20):
        defs[f"n{i}"] = {
            "allOf": [{"$ref": f"#/$defs/n{i - 1}"}, {"$ref": f"#/$defs/n{i - 1}"}]
        }
    with pytest.raises(ValueError, match="bound|limit"):
        validate_local_schema({"$defs": defs, "$ref": "#/$defs/n19"})


def test_every_keyword_has_valid_and_invalid_values():
    m = depth_manifest("schemas")
    assert len(m.cases) == 128
    assert m.request_ceiling == 128
    assert sum(c.oracle.get("applicable") is False for c in m.cases) == 32
    features = {}
    for c in m.cases:
        features.setdefault(c.oracle["depth"]["family"], c.oracle)
    assert len(features) == 16
    for oracle in features.values():
        assert schema_matches(oracle["schema"], oracle["expected_value"])
        assert not schema_matches(oracle["schema"], oracle["invalid_value"])


def test_full_matrix_has_96_calls_and_32_explicit_skips():
    m = depth_manifest("schemas")
    selected_cases = iter(c for c in m.cases if c.oracle.get("applicable") is not False)
    calls = []

    def handler(r):
        c = next(selected_cases)
        p = json.loads(r.content)
        calls.append(p)
        value = c.oracle["expected_value"]
        expect = (
            {"tools": [{"name": "record_schema_fixture", "arguments": value}]}
            if c.oracle["target"] == "tool"
            else {"text": json.dumps(value)}
        )
        if c.oracle["target"] == "tool":
            fn = p["tools"][0].get("function", p["tools"][0])
            assert fn["name"] == "record_schema_fixture"
            assert ("strict" in fn) == ("strict" in c.oracle)
        return httpx.Response(
            200, json=fixture_body(c.protocol, expect, reasoning=False)
        )

    r = run(m, handler)
    assert len(calls) == 96
    assert r.counts["PASS"] == 96 and r.counts["SKIP"] == 32
    assert r.exit_code == 0


@pytest.mark.parametrize("status", [400, 422, 401, 402, 429, 500])
@pytest.mark.parametrize("case_id,mandatory", [("S01", True), ("S05", False)])
def test_rejection_is_not_feature_support(case_id, mandatory, status):
    m = selected("schemas", case_id)
    r = run(
        m, lambda req: httpx.Response(status, json={"error": "fixture rejection"})
    ).case_results[0]
    expected = "ERROR" if status not in (400, 422) else "FAIL" if mandatory else "PASS"
    assert r.status == expected
    capability = next(a.observed for a in r.assertions if a.id == "SCHEMA_CAPABILITY")
    assert capability == ("ERROR" if expected == "ERROR" else "REJECTED")
    assert not any(
        o.scored and o.value == 1
        for o in r.metric_observations
        if o.name in ("schema_validity", "feature_support")
    )


@pytest.mark.parametrize(
    "invalid", ["malformed", "wrong_fixture", "wrong_type", "wrong_name"]
)
def test_accepted_invalid_results_fail_in_all_reports(invalid):
    from deepseek_provider_verifier.reports import render_report

    m = selected("schemas", "S01")
    exp = {
        "tools": [
            {
                "name": "record_schema_fixture",
                "arguments": {"label": "amber", "count": 1},
            }
        ]
    }
    if invalid == "wrong_fixture":
        exp["tools"][0]["arguments"]["label"] = "wrong"
    if invalid == "wrong_type":
        exp["tools"][0]["arguments"]["count"] = True
    if invalid == "wrong_name":
        exp["tools"][0]["name"] = "unadvertised"
    b = fixture_body("chat", exp, reasoning=False)
    if invalid == "malformed":
        b["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{broken"
    r = run(m, lambda req: httpx.Response(200, json=b))
    assert r.case_results[0].status == "FAIL"
    assert (
        next(
            a.observed
            for a in r.case_results[0].assertions
            if a.id == "SCHEMA_CAPABILITY"
        )
        == "ACCEPTED_INVALID"
    )
    for fmt in ("json", "markdown", "junit"):
        assert render_report(r, fmt)


def test_oversized_tool_value_is_bounded_before_any_schema_validation(monkeypatch):
    from jsonschema import Draft202012Validator

    original = Draft202012Validator.is_valid

    def bounded(self, instance, *args, **kwargs):
        if isinstance(instance, dict) and len(str(instance.get("label", ""))) > 65536:
            pytest.fail("Unbounded value reached a schema validator")
        return original(self, instance, *args, **kwargs)

    monkeypatch.setattr(Draft202012Validator, "is_valid", bounded)
    m = selected("schemas", "S01")
    body = fixture_body(
        "chat",
        {
            "tools": [
                {
                    "name": "record_schema_fixture",
                    "arguments": {"label": "x" * 70000, "count": 1},
                }
            ]
        },
        reasoning=False,
    )
    result = run(m, lambda req: httpx.Response(200, json=body))
    assert result.case_results[0].status == "FAIL"
