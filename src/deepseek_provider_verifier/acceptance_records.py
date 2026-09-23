"""Versioned acceptance records, separate from canonical evidence formats."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .records import Case, ResultStatus

# Every executed trial keeps its raw verdict and a mandatory protocol facet.
FAMILY_FACETS = {
    "core": ("protocol", "functional", "raw_contract"),
    "contract": ("protocol", "raw_contract"),
    "quality": ("protocol", "quality", "raw_contract"),
    "functional": ("protocol", "functional", "raw_contract"),
    "workflow": ("protocol", "functional", "raw_contract"),
    "workflow.thinking": ("protocol", "functional", "reasoning", "raw_contract"),
    "schema.core": ("protocol", "schema", "raw_contract"),
    "schema.optional": ("protocol", "capability", "raw_contract"),
    "size.input": ("protocol", "retrieval", "format", "raw_contract"),
    "size.output": ("protocol", "functional", "budget", "visible", "raw_contract"),
}
MANDATORY = {"protocol", "functional", "schema", "retrieval", "budget"}
FUNCTIONAL = {"functional", "schema", "retrieval", "budget", "capability", "quality"}


def case_family(case: Case) -> str:
    if "depth" not in case.oracle:
        oracle = case.oracle
        expected_status = oracle.get("status_class_by_mode", {}).get(
            case.mode, oracle.get("status_class")
        )
        kind = oracle.get("kind")
        # Envelope/status observations do not demonstrate a positive task.
        semantic = (
            kind in ("exact_text", "conversation", "consistency")
            and oracle.get("value") is not None
            or kind in ("schema", "json_object")
            and bool(oracle.get("schema"))
            or kind in ("named_tool", "nested_tool", "multiple_tools", "tool_required")
            and "expected_arguments" in oracle
        )
        return (
            "core"
            if semantic and expected_status is None and not oracle.get("compatibility")
            else "contract"
        )
    kind = case.oracle.get("kind")
    family = case.oracle["depth"].get("family", "")
    if kind == "schema_probe":
        return "schema.core" if case.oracle["required_support"] else "schema.optional"
    if kind == "workflow":
        return "workflow.thinking" if case.mode == "thinking" else "workflow"
    if kind in ("large_input", "large_output"):
        return "size.input" if kind == "large_input" else "size.output"
    if family == "repeatability.instruction":
        return "quality"
    if family in (
        "repeatability.lookup",
        "repeatability.continuation",
        "repeatability.structured",
    ):
        return "functional"
    raise ValueError("Unclassified acceptance case family")


class AcceptanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class Facet(AcceptanceRecord):
    endpoint: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: ResultStatus
    source_assertion_ids: list[str]
    evidence_hashes: list[str]
    reason: str


class GateSelector(AcceptanceRecord):
    family: str
    facet: str
    required: bool = Field(strict=True)


class AcceptancePolicy(AcceptanceRecord):
    version: Literal[1]
    id: str = Field(min_length=1)
    mode: Literal["compatibility", "strict"]
    selectors: list[GateSelector] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_selectors(self):
        seen = set()
        for selector in self.selectors:
            key = (selector.family, selector.facet)
            if selector.facet not in FAMILY_FACETS.get(selector.family, ()):
                raise ValueError("Unknown acceptance family or facet")
            if key in seen:
                raise ValueError("Duplicate acceptance selector")
            seen.add(key)
            if selector.facet in MANDATORY and not selector.required:
                raise ValueError("Fundamental acceptance gates cannot be waived")
            if self.mode == "strict" and not selector.required:
                raise ValueError("Strict assessment requires every facet")
        for family in {s.family for s in self.selectors}:
            if {f for group, f in seen if group == family} != set(
                FAMILY_FACETS[family]
            ):
                raise ValueError(
                    "Policy must classify every facet in each selected family"
                )
        if not any(s.required and s.facet in FUNCTIONAL for s in self.selectors):
            raise ValueError("Policy requires at least one functional gate")
        return self


class AcceptanceResult(AcceptanceRecord):
    version: Literal[1] = 1
    source_manifest_hash: str
    source_scorer_revision: str
    policy_hash: str
    policy: AcceptancePolicy
    scorer_revision: str = Field(min_length=1)
    integrity: Literal["verified", "summary-only", "unavailable"]
    verdict: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    exit_code: Literal[0, 1, 2]
    required_facets: list[Facet]
    diagnostic_facets: list[Facet]
    uncertified_capabilities: list[str]
    reasons: list[str]
