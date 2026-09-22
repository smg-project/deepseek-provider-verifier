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
