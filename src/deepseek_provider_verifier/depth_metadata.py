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
