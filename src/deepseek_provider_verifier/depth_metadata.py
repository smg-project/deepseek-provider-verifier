"""Validated opt-in metadata, without changing legacy serialized records."""


def validate_depth_oracle(oracle: dict) -> None:
    if "depth" not in oracle:
        return
    depth = oracle["depth"]
    if (
        not isinstance(depth, dict)
        or type(depth.get("version")) is not int
        or depth["version"] != 1
    ):
        raise ValueError("unknown depth metadata version")
    if not isinstance(depth.get("family"), str) or not depth["family"].strip():
        raise ValueError("depth family must be a nonempty string")
    limits = depth.get("resource_limits")
    names = {"max_request_bytes", "max_response_bytes"}
    if (
        not isinstance(limits, dict)
        or set(limits) != names
        or any(type(v) is not int or v <= 0 for v in limits.values())
    ):
        raise ValueError(
            "depth resource_limits must contain positive integer request and response byte caps"
        )


def validate_depth_steps(steps: list[dict], oracle: dict) -> None:
    if oracle.get("kind") == "schema_probe":
        from .schema_cases import schema_matches, validate_local_schema

        if (
            oracle.get("target") not in ("tool", "output")
            or type(oracle.get("required_support")) is not bool
            or ("strict" in oracle and type(oracle["strict"]) is not bool)
        ):
            raise ValueError("invalid schema probe metadata")
        validate_local_schema(oracle.get("schema"))
        if not schema_matches(oracle["schema"], oracle.get("expected_value")):
            raise ValueError("schema fixture does not satisfy its schema")
    if oracle.get("kind") != "workflow":
        return
    if "depth" not in oracle or oracle.get("bounded_steps") is not True:
        raise ValueError("workflow requires bounded depth metadata")
    from .mock_tools import execute_tool

    for step in steps:
        expect = step.get("expect")
        if not isinstance(expect, dict) or set(expect) not in ({"text"}, {"tools"}):
            raise ValueError("workflow step requires a text or tools expectation")
        if "text" in expect:
            if not isinstance(expect["text"], str):
                raise ValueError("workflow text expectation must be a string")
        else:
            if not isinstance(expect["tools"], list) or not expect["tools"]:
                raise ValueError("workflow tools expectation must be nonempty")
            for call in expect["tools"]:
                if not isinstance(call, dict) or set(call) != {"name", "arguments"}:
                    raise ValueError("invalid workflow tool expectation")
                execute_tool(call["name"], call["arguments"])


def resource_limits(case) -> tuple[int | None, int | None]:
    validate_depth_oracle(case.oracle)
    depth = case.oracle.get("depth")
    if depth is None:
        return None, None
    limits = depth["resource_limits"]
    return limits["max_request_bytes"], limits["max_response_bytes"]


def plan_resource_summary(manifest) -> dict:
    """Derived caps, not token estimates; absent legacy caps stay unbounded."""
    import json

    rows = []
    multiplier = (manifest.budgets.retries + 1) * len(manifest.endpoints)
    for case in manifest.cases:
        outgoing, incoming = resource_limits(case)
        authored = 0
        for step in case.steps:
            content = step.get("content", "")
            authored += len(
                (
                    content
                    if isinstance(content, str)
                    else json.dumps(content, ensure_ascii=False)
                ).encode()
            )
        rows.append(
            {
                "case_id": case.id,
                "authored_input_bytes": authored,
                "max_request_bytes": outgoing,
                "max_response_bytes": incoming,
                "request_byte_ceiling": None
                if outgoing is None
                else outgoing * case.max_requests * multiplier,
                "capture_byte_ceiling": None
                if incoming is None
                else incoming * case.max_requests * multiplier,
            }
        )

    def total(key):
        return None if any(r[key] is None for r in rows) else sum(r[key] for r in rows)

    return {
        "schema_version": 1,
        "cases": rows,
        "aggregate_request_byte_ceiling": total("request_byte_ceiling"),
        "aggregate_capture_byte_ceiling": total("capture_byte_ceiling"),
        "measurement": "Authored UTF-8 bytes; wire JSON and accumulated history are checked at each send; aggregate byte ceilings are conservative, not expected usage",
    }
