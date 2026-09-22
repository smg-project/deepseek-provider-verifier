"""Validated, serializable records shared by planning and later execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    model_validator,
)

Protocol = Literal["chat", "responses"]
Mode = Literal["non_thinking", "thinking"]
EvidenceStatus = Literal["documented", "observed", "project-policy"]
RuleMaturity = Literal["diagnostic", "calibrated"]
ResultStatus = Literal["PASS", "FAIL", "ERROR", "SKIP", "INCONCLUSIVE"]


class Record(BaseModel):
    """Base for versioned records with strict input and stable JSON output."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1


class Endpoint(Record):
    name: str = Field(min_length=1)
    base_url: AnyHttpUrl
    model: str = Field(min_length=1)
    model_release: str = Field(min_length=1)
    api_key_env: str | None = Field(
        default=None, min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    auth_none: bool = False

    @model_validator(mode="after")
    def require_one_authentication_setting(self) -> Endpoint:
        if (self.api_key_env is None) == (not self.auth_none):
            raise ValueError(
                "endpoint must set exactly one of api_key_env or auth_none"
            )
        if any(
            value is not None
            for value in (
                self.base_url.username,
                self.base_url.password,
                self.base_url.query,
                self.base_url.fragment,
            )
        ):
            raise ValueError("base_url must not contain userinfo, query, or fragment")
        return self


class RunSettings(Record):
    profile: str = Field(min_length=1)
    suite: str = Field(min_length=1)
    protocols: list[Protocol] = Field(min_length=1)
    concurrency: PositiveInt = 1
    max_attempts_per_endpoint: PositiveInt = 40
    retries: int = Field(default=0, ge=0)
    scorer_revision: str = Field(default="unversioned", min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_protocols(self) -> RunSettings:
        if len(self.protocols) != len(set(self.protocols)):
            raise ValueError("run protocols must be unique")
        return self


class Config(Record):
    run: RunSettings
    endpoints: dict[str, Endpoint] = Field(min_length=1)

    @model_validator(mode="after")
    def endpoint_keys_match_names(self) -> Config:
        mismatches = [
            key for key, endpoint in self.endpoints.items() if key != endpoint.name
        ]
        if mismatches:
            raise ValueError("endpoint table names must match endpoint name fields")
        return self


class Rule(Record):
    id: str = Field(min_length=1)
    protocol: Protocol
    conditions: dict[str, Any]
    expectation: str = Field(min_length=1)
    assertion_id: str = Field(min_length=1)
    source_url: AnyHttpUrl
    source_section: str = Field(min_length=1)
    retrieved_at: datetime
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_status: EvidenceStatus
    maturity: RuleMaturity = "diagnostic"
    gating: bool

    @model_validator(mode="after")
    def diagnostic_rules_are_not_gates(self) -> Rule:
        if self.maturity == "diagnostic" and self.gating:
            raise ValueError("diagnostic rule cannot be gating")
        return self


class ProfilePreset(Record):
    max_requests_per_protocol: PositiveInt
    max_requests_per_endpoint: PositiveInt
    max_total_requests: PositiveInt
    max_requests_per_conversation: PositiveInt
    max_retries: int = Field(ge=0)
    concurrency: PositiveInt
    case_deadline_seconds: PositiveInt
    mode_max_output_tokens: dict[Mode, PositiveInt]
    case_ids: list[str] = Field(default_factory=list)
    attached_assertion_case_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_preset_lists(self) -> ProfilePreset:
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("preset case IDs must be unique")
        if len(self.attached_assertion_case_ids) != len(
            set(self.attached_assertion_case_ids)
        ):
            raise ValueError("attached assertion case IDs must be unique")
        if set(self.case_ids) & set(self.attached_assertion_case_ids):
            raise ValueError("request and attached assertion case IDs must be disjoint")
        if self.attached_assertion_case_ids and not self.case_ids:
            raise ValueError("attached assertion case IDs require request case IDs")
        return self


class Profile(Record):
    id: str = Field(min_length=1)
    models: list[str] = Field(min_length=1)
    rules: list[Rule]
    presets: dict[str, ProfilePreset] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_rule_ids(self) -> Profile:
        ids = [rule.id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("profile rule IDs must be unique")
        return self


class PlanBudgets(Record):
    suite: str = Field(min_length=1)
    protocols: list[Protocol] = Field(min_length=1)
    max_requests_per_protocol: PositiveInt
    max_requests_per_endpoint: PositiveInt
    max_total_requests: PositiveInt
    max_requests_per_conversation: PositiveInt
    max_retries: int = Field(ge=0)
    retries: int = Field(ge=0)
    max_concurrency: PositiveInt
    concurrency: PositiveInt
    max_attempts_per_endpoint: PositiveInt
    case_deadline_seconds: PositiveInt
    mode_max_output_tokens: dict[Mode, PositiveInt]
    case_ids: list[str]
    attached_assertion_case_ids: list[str]


class CaseTemplate(Record):
    id: str = Field(min_length=1)
    protocol: Protocol
    modes: list[Mode] = Field(min_length=1)
    streams: list[bool] = Field(min_length=1)
    rule_ids: list[str] = Field(min_length=1)
    steps: list[dict[str, Any]] = Field(min_length=1)
    required: bool
    max_requests: PositiveInt
    max_output_tokens: dict[Mode, PositiveInt]
    oracle: dict[str, Any]

    @model_validator(mode="after")
    def validate_variant_axes(self) -> CaseTemplate:
        for label, values in (
            ("modes", self.modes),
            ("streams", self.streams),
            ("rule IDs", self.rule_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"case template {label} must be unique")
        missing = set(self.modes) - set(self.max_output_tokens)
        if missing:
            raise ValueError(f"missing max_output_tokens for modes: {sorted(missing)}")
        if self.max_requests < len(self.steps):
            raise ValueError(
                "max_requests must cover all request-producing steps; "
                f"got {self.max_requests} for {len(self.steps)} steps"
            )
        return self


class Case(Record):
    id: str = Field(min_length=1)
    protocol: Protocol
    template_id: str = Field(min_length=1)
    mode: Mode
    stream: bool
    rule_ids: list[str] = Field(min_length=1)
    attached_assertion_case_ids: list[str] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(min_length=1)
    required: bool
    max_requests: PositiveInt
    max_output_tokens: PositiveInt
    oracle: dict[str, Any]


class Manifest(Record):
    run_id: str = Field(min_length=1)
    created_at: datetime
    profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scorer_revision: str = Field(min_length=1)
    endpoints: dict[str, Endpoint] = Field(min_length=1)
    cases: list[Case]
    budgets: PlanBudgets
    request_ceiling: int = Field(ge=0)
    output_token_ceiling: int = Field(ge=0)
    gates: list[str]
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class Attempt(Record):
    case_id: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    step: int = Field(ge=0)
    repetition: int = Field(ge=0)
    attempt_number: PositiveInt
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    request: dict[str, Any]
    status_code: int | None = Field(default=None, ge=100, le=599)
    timings: dict[str, float]
    response: dict[str, Any] | None = None
    events: list[dict[str, Any]]
    error: dict[str, Any] | None = None
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CaseResult(Record):
    case_id: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    status: ResultStatus
    assertions: list[dict[str, Any]]
    metric_observations: list[dict[str, Any]]
    attempt_refs: list[str]
    reason: str | None = None


class RunResult(Record):
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    complete: bool
    case_results: list[CaseResult]
    counts: dict[str, int]
    budget_usage: dict[str, int]
    enabled_gates: list[str]
    exit_code: Literal[0, 1, 2]
