"""Load the authoritative bundled original dataset, including in installed wheels."""

import hashlib
import json
from importlib.resources import files
from pathlib import Path

from .records import BehavioralPrompt, CaseTemplate


def content_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def _resource(name: str):
    bundled = files("deepseek_provider_verifier").joinpath("cases", name)
    if bundled.is_file():
        return bundled
    return Path(__file__).resolve().parents[2] / "cases" / name


def load_prompts() -> list[BehavioralPrompt]:
    prompts = [
        BehavioralPrompt.model_validate_json(line)
        for line in _resource("behavior.jsonl").read_text().splitlines()
        if line.strip()
    ]
    for prompt in prompts:
        if (
            content_hash(prompt.model_dump(mode="json", exclude={"content_hash"}))
            != prompt.content_hash
        ):
            raise ValueError("Original prompt content hash mismatch")
    if len({p.id for p in prompts}) != len(prompts):
        raise ValueError("Duplicate prompt ID")
    return prompts


def load_cases(
    protocols: list[str] | None = None, case_ids: list[str] | None = None
) -> list[CaseTemplate]:
    prompts = {p.id: p for p in load_prompts()}
    templates = []
    selected_protocols = protocols or ["chat", "responses"]
    if any(p not in ("chat", "responses") for p in selected_protocols):
        raise ValueError("Unknown catalog protocol")
    names = [f"{p}.jsonl" for p in selected_protocols]
    if case_ids is not None:
        names.append("compatibility.jsonl")
    for name in names:
        for line in _resource(name).read_text().splitlines():
            value = json.loads(line)
            if value["protocol"] not in selected_protocols:
                continue
            if case_ids is not None and value["id"] not in case_ids:
                continue
            for step in value["steps"]:
                if "prompt_id" in step:
                    prompt = prompts[step["prompt_id"]]
                    step.update(content=prompt.content, prompt_hash=prompt.content_hash)
            templates.append(CaseTemplate.model_validate(value))
    if case_ids is not None:
        depth_ids = [id for id in case_ids if id.startswith(("R", "W", "S", "L"))]
        if depth_ids:
            from .depth_catalog import expand_depth_cases

            templates.extend(expand_depth_cases(depth_ids, selected_protocols))
    return templates
