"""Bounded local JSON Schema probes; accepted examples do not prove decoding enforcement."""

import json
from functools import lru_cache

from jsonschema import Draft202012Validator, SchemaError
from referencing import Registry

from .catalog import content_hash
from .compatibility import _status_result
from .json_utils import strict_json_loads
from .records import AssertionResult, CaseResult, MetricObservation

MAX_BYTES = 65536
MAX_NODES = 2048
MAX_DEPTH = 32


def _bounded_json(value):
    stack = [(value, 0)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("Schema/value node or depth limit exceeded")
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    if len(encoded.encode()) > MAX_BYTES:
        raise ValueError("Schema/value byte limit exceeded")
    return encoded


def _pointer(schema, ref):
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise ValueError("Schema refs must be local JSON pointers")
    node = schema
    try:
        for token in ref[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            node = node[int(token)] if isinstance(node, list) else node[token]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ValueError("Unresolved local schema pointer") from exc
    if not isinstance(node, (dict, bool)):
        raise ValueError("Schema pointer must target a schema")  # noqa: TRY004 -- public validation API
    return node


@lru_cache(maxsize=128)
def _validate_encoded(encoded):
    schema = json.loads(encoded)
    visits = 0

    def walk(node, active, depth):
        nonlocal visits
        visits += 1
        if visits > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("Expanded schema work limit exceeded")
        if isinstance(node, (dict, list)):
            if id(node) in active:
                raise ValueError("Recursive schema references are unsupported")
            active = active | {id(node)}
        if isinstance(node, dict):
            if node.get("$id") or any(
                k in node
                for k in ("$dynamicRef", "$recursiveRef", "$anchor", "$dynamicAnchor")
            ):
                raise ValueError("Schema uses unsupported reference scope")
            # Patterns may have unbounded regex work even on a short input.
            if any(k in node for k in ("pattern", "patternProperties")):
                raise ValueError("Regex schemas exceed the bounded local subset")
            if "$ref" in node:
                walk(_pointer(schema, node["$ref"]), active, depth + 1)
            for k, v in node.items():
                if k != "$ref":
                    walk(v, active, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, active, depth + 1)

    walk(schema, set(), 0)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("Invalid JSON schema") from exc


def validate_local_schema(schema: dict) -> None:
    if not isinstance(schema, dict):
        raise ValueError("Schema root must be an object")  # noqa: TRY004 -- public validation API
    _validate_encoded(_bounded_json(schema))


def _no_remote(uri):
    raise ValueError("Remote schema retrieval is prohibited")


def schema_matches(schema: dict, value: object) -> bool:
    validate_local_schema(schema)
    try:
        _bounded_json(value)
        return Draft202012Validator(
            schema, registry=Registry(retrieve=_no_remote)
        ).is_valid(value)
    except (ValueError, TypeError, RecursionError):
        return False


def schema_fixture(feature):
    def obj(properties, required=None):
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False,
        }

    if feature == "required":
        return (
            obj({"label": {"type": "string"}, "count": {"type": "integer"}}),
            {"label": "amber", "count": 1},
            {"label": "amber"},
        )
    if feature in ("optional_absent", "optional_present"):
        schema = obj(
            {"label": {"type": "string"}, "note": {"type": "string"}}, ["label"]
        )
        value = {"label": "amber"}
        if feature == "optional_present":
            value["note"] = "present"
        return schema, value, {"label": "amber", "note": None}
    if feature in ("nullable_null", "nullable_nonnull"):
        return (
            obj({"n": {"type": ["integer", "null"]}}),
            {"n": None if feature == "nullable_null" else 3},
            {"n": True},
        )
    if feature in ("enum", "const"):
        return (
            obj(
                {
                    "label": {"enum": ["amber", "violet"]}
                    if feature == "enum"
                    else {"const": "amber"}
                }
            ),
            {"label": "amber"},
            {"label": "green"},
        )
    if feature == "array":
        return (
            obj(
                {
                    "items": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 2,
                        "maxItems": 3,
                    }
                }
            ),
            {"items": [2, 3]},
            {"items": [2]},
        )
    if feature == "numeric":
        return (
            obj({"n": {"type": "number", "minimum": 2, "exclusiveMaximum": 5}}),
            {"n": 2},
            {"n": 5},
        )
    if feature == "string":
        return (
            obj({"label": {"type": "string", "minLength": 3, "maxLength": 5}}),
            {"label": "amber"},
            {"label": "xx"},
        )
    if feature.startswith("nesting_"):
        schema = {"type": "integer"}
        value = 7
        invalid = True
        for _ in range(int(feature.split("_")[1])):
            schema = obj({"child": schema})
            value = {"child": value}
            invalid = {"child": invalid}
        return schema, value, invalid
    if feature in ("anyOf", "oneOf"):
        return (
            obj(
                {
                    "value": {
                        feature: [
                            {"type": "integer", "minimum": 0},
                            {"type": "string", "enum": ["amber"]},
                        ]
                    }
                }
            ),
            {"value": "amber"},
            {"value": False},
        )
    if feature == "local_ref":
        schema = obj({"value": {"$ref": "#/$defs/label"}})
        schema["$defs"] = {"label": {"type": "string", "enum": ["amber", "violet"]}}
        return schema, {"value": "amber"}, {"value": 4}
    raise ValueError("Unknown schema feature")


def schema_template(descriptor, protocol):
    from .depth_catalog import depth_metadata
    from .records import CaseTemplate

    schema, expected, invalid = schema_fixture(descriptor["feature"])
    validate_local_schema(schema)
    target = descriptor["target"]
    strict = {"strict": True} if descriptor["strict"] else {}
    required = descriptor["feature"] == "required" and (
        (target == "tool" and not strict)
        or (protocol == "responses" and target == "output")
    )
    request = (
        {
            "tools": [
                {
                    "type": "function",
                    **(
                        {
                            "function": {
                                "name": "record_schema_fixture",
                                "parameters": schema,
                                **strict,
                            }
                        }
                        if protocol == "chat"
                        else {
                            "name": "record_schema_fixture",
                            "parameters": schema,
                            **strict,
                        }
                    ),
                }
            ],
            "tool_choice": "auto",
        }
        if target == "tool"
        else {
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "schema_fixture",
                    "schema": schema,
                    **strict,
                }
            }
        }
    )
    content = (
        "Call record_schema_fixture exactly once with these arguments: "
        if target == "tool"
        else "Return only this JSON value: "
    ) + json.dumps(expected, ensure_ascii=False)
    oracle = {
        "kind": "schema_probe",
        "target": target,
        "schema": schema,
        "expected_value": expected,
        "invalid_value": invalid,
        "required_support": required,
        **strict,
        "depth": depth_metadata(descriptor["family"]),
    }
    if protocol == "chat" and target == "output":
        oracle.update(
            applicable=False,
            reason="Chat output JSON Schema is outside this protocol contract",
        )
        request = {}
    return CaseTemplate(
        id=descriptor["id"],
        prompt_id=descriptor["id"],
        dataset_version="depth-v1",
        protocol=protocol,
        modes=["non_thinking"],
        streams=[False],
        rule_ids=[f"{protocol}.depth"],
        steps=[{"kind": "user", "content": content, "request": request}],
        required=True,
        max_requests=1,
        max_output_tokens={"non_thinking": 512},
        oracle=oracle,
    )


def evaluate_schema_probe(case, observations, rules) -> CaseResult:
    spec = case.oracle
    structure = case.model_copy(
        update={
            "oracle": {
                "kind": "structure",
                **(
                    {"applicable": False, "reason": spec["reason"]}
                    if spec.get("applicable") is False
                    else {}
                ),
            }
        }
    )
    result = _status_result(
        structure, observations, rules, not spec["required_support"]
    )
    if result.status == "SKIP":
        return result
    valid = False
    conforms = False
    rejected = bool(observations) and observations[-1].status_code in (400, 422)
    capability = (
        "ERROR"
        if result.status == "ERROR"
        else "REJECTED"
        if rejected
        else "ACCEPTED_INVALID"
    )
    if not rejected and observations and result.status != "ERROR":
        obs = observations[-1]
        try:
            if spec["target"] == "tool":
                tools = list(obs.tools.values())
                if len(tools) != 1 or tools[0].name != "record_schema_fixture":
                    raise ValueError("Expected exactly one fixture function")
                value = strict_json_loads(tools[0].arguments)
            else:
                if obs.tools:
                    raise ValueError("Expected output, not a tool")
                value = strict_json_loads(obs.text)
            conforms = schema_matches(spec["schema"], value)
            valid = (
                conforms
                and content_hash(value) == content_hash(spec["expected_value"])
                and result.status == "PASS"
            )
        except (ValueError, TypeError, RecursionError):
            valid = False
        capability = "ACCEPTED_VALID" if valid else "ACCEPTED_INVALID"
    assertion = AssertionResult(
        id="SCHEMA_CAPABILITY",
        status="ERROR"
        if capability == "ERROR"
        else "PASS"
        if valid
        or (rejected and not spec["required_support"] and result.status == "PASS")
        else "FAIL",
        observed=capability,
        reason="Acceptance must satisfy both the schema and the authored fixture; rejection does not establish support",
        rule_ids=[r.id for r in rules],
        gating=bool(rules)
        and all(r.gating and r.maturity == "calibrated" for r in rules),
    )
    status = (
        "ERROR"
        if result.status == "ERROR"
        else "FAIL"
        if result.status == "FAIL" or assertion.status == "FAIL"
        else result.status
    )
    metrics = [
        *(m for m in result.metric_observations if m.name != "tool_argument_schema"),
        MetricObservation(
            name="feature_support", value=float(valid), scored=capability != "ERROR"
        ),
        MetricObservation(
            name="schema_validity",
            value=float(conforms) if not rejected else None,
            scored=not rejected and capability != "ERROR",
            reason="Explicit rejection; output was not generated" if rejected else None,
        ),
    ]
    if spec["target"] == "tool" and observations and observations[-1].tools:
        for t in observations[-1].tools.values():
            try:
                matches = schema_matches(spec["schema"], strict_json_loads(t.arguments))
            except (ValueError, TypeError, RecursionError):
                matches = False
            metrics.append(
                MetricObservation(
                    name="tool_argument_schema",
                    value=float(matches),
                    scored=capability != "ERROR",
                )
            )
    return result.model_copy(
        update={
            "status": status,
            "assertions": [*result.assertions, assertion],
            "metric_observations": metrics,
        }
    )
