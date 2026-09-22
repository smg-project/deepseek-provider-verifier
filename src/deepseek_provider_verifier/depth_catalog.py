"""Lazy expansion of versioned, explicitly selected depth fixtures."""

import json

from .catalog import _resource, content_hash
from .mock_tools import tool_definition
from .records import BehavioralPrompt, CaseTemplate


def depth_metadata(family: str) -> dict:
    return {
        "version": 1,
        "family": family,
        "resource_limits": {
            "max_request_bytes": 8 * 1024 * 1024,
            "max_response_bytes": 8 * 1024 * 1024,
        },
    }


def load_depth_prompts() -> dict[str, BehavioralPrompt]:
    prompts = [
        BehavioralPrompt.model_validate_json(line)
        for line in _resource("depth-prompts.jsonl").read_text().splitlines()
        if line.strip()
    ]
    if len({p.id for p in prompts}) != len(prompts):
        raise ValueError("Duplicate depth prompt ID")
    for p in prompts:
        if (
            content_hash(p.model_dump(mode="json", exclude={"content_hash"}))
            != p.content_hash
        ):
            raise ValueError("Depth prompt content hash mismatch")
    return {p.id: p for p in prompts}


def _repeatability(descriptor, protocol, prompts):
    prompt = prompts[descriptor["prompt_id"]]
    fixture = descriptor["fixture"]
    family = descriptor["family"]
    request = {}
    oracle = {"depth": depth_metadata(family)}
    if family == "repeatability.instruction":
        oracle.update(kind="exact_text", value=prompt.intended_answer)
    elif family in ("repeatability.lookup", "repeatability.continuation"):
        request = {
            "tools": [tool_definition(fixture["name"], protocol)],
            "tool_choice": "auto",
        }
        oracle.update(
            kind="named_tool",
            name=fixture["name"],
            expected_arguments=fixture["arguments"],
            min_calls=1,
        )
        if family == "repeatability.continuation":
            oracle.update(
                kind="exact_text",
                value=str(prompt.intended_answer),
                continue_tools=True,
                bounded_steps=True,
            )
    else:
        schema = {
            "type": "object",
            "properties": {
                "label": {"type": "string", "enum": [prompt.intended_answer["label"]]},
                "count": {"type": "integer", "enum": [prompt.intended_answer["count"]]},
            },
            "required": ["label", "count"],
            "additionalProperties": False,
        }
        oracle.update(kind="schema", schema=schema)
        request = (
            {"response_format": {"type": "json_object"}}
            if protocol == "chat"
            else {
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "repeatability_fixture",
                        "schema": schema,
                        "strict": True,
                    }
                }
            }
        )
    steps = [
        {
            "kind": "user",
            "prompt_id": prompt.id,
            "prompt_hash": prompt.content_hash,
            "content": prompt.content,
            "request": request,
        }
    ]
    if family == "repeatability.continuation":
        steps.append(
            {
                "kind": "user",
                "content": "Return only the integer result from the tool. Do not call any more tools.",
                "request": {"tool_choice": "none"},
            }
        )
    expanded = descriptor["expanded"]
    return CaseTemplate(
        id=descriptor["id"],
        prompt_id=prompt.id,
        dataset_version="depth-v1",
        protocol=protocol,
        modes=["non_thinking", "thinking"] if expanded else ["non_thinking"],
        streams=[False, True] if expanded else [False],
        rule_ids=[f"{protocol}.depth"],
        steps=steps,
        required=True,
        max_requests=len(steps),
        max_output_tokens={"non_thinking": 512, "thinking": 4096},
        oracle=oracle,
    )


def expand_depth_cases(case_ids: list[str], protocols: list[str]) -> list[CaseTemplate]:
    data = json.loads(_resource("depth-descriptors.json").read_text())
    if type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("unknown depth descriptor version")
    by_id = {d["id"]: d for d in data["cases"]}
    if len(by_id) != len(data["cases"]):
        raise ValueError("Duplicate depth descriptor ID")
    missing = set(case_ids) - set(by_id)
    if missing:
        raise ValueError(f"Unknown depth cases: {sorted(missing)}")
    prompts = load_depth_prompts()
    return [
        _expand(by_id[id], protocol, prompts)
        for id in case_ids
        for protocol in protocols
    ]


def _expand(descriptor, protocol, prompts):
    if descriptor["family"].startswith("repeatability."):
        return _repeatability(descriptor, protocol, prompts)
    if descriptor["family"].startswith("workflow."):
        from .workflows import workflow_steps

        steps = workflow_steps(descriptor, protocol)
        return CaseTemplate(
            id=descriptor["id"],
            prompt_id=descriptor["id"],
            dataset_version="depth-v1",
            protocol=protocol,
            modes=["non_thinking", "thinking"],
            streams=[False, True],
            rule_ids=[f"{protocol}.depth"],
            steps=steps,
            required=True,
            max_requests=len(steps),
            max_output_tokens={"non_thinking": 512, "thinking": 4096},
            oracle={
                "kind": "workflow",
                "bounded_steps": True,
                "continue_tools": True,
                "depth": depth_metadata(descriptor["family"]),
            },
        )
    if descriptor["family"].startswith("schema."):
        from .schema_cases import schema_template

        return schema_template(descriptor, protocol)
    if descriptor["family"].startswith("size."):
        from .size_cases import size_template

        return size_template(descriptor, protocol)
    raise ValueError("Unknown depth family")
