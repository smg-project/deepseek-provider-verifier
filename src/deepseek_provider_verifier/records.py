"""Validated, serializable records shared by planning and later execution."""

from __future__ import annotations

import math
from datetime import date, datetime
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
    contract_model: str | None = Field(default=None, min_length=1)
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
    repetitions: PositiveInt = 1
    execution_order: Literal["sequential", "paired"] = "sequential"
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
        if "compatibility_policy" in self.conditions and self.conditions[
            "compatibility_policy"
        ] not in ("self_hosted", "official_parity"):
            raise ValueError("unknown compatibility_policy")
        if "calibrated_variants" in self.conditions:
            variants = self.conditions["calibrated_variants"]
            if (
                not isinstance(variants, list)
                or not variants
                or any(not isinstance(v, str) or not v.strip() for v in variants)
                or len(variants) != len(set(variants))
            ):
                raise ValueError(
                    "calibrated_variants must be a nonempty list of unique nonempty strings"
                )
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
        policies = {r.conditions.get("compatibility_policy") for r in self.rules} - {
            None
        }
        if len(policies) > 1:
            raise ValueError("profile cannot mix compatibility policies")
        return self


class PlanBudgets(Record):
    repetitions: PositiveInt = 1
    execution_order: Literal["sequential", "paired"] = "sequential"
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
    prompt_id: str | None = None
    dataset_version: str = "original-v1"
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
        _validate_probe(self.steps, self.oracle)
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
    prompt_id: str | None = None
    repetition: int = Field(default=0, ge=0)
    dataset_version: str = "original-v1"
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

    @model_validator(mode="after")
    def validate_probe(self) -> Case:
        _validate_probe(self.steps, self.oracle)
        return self


def _validate_probe(steps: list[dict], oracle: dict) -> None:
    from .depth_metadata import validate_depth_oracle

    validate_depth_oracle(oracle)
    for index, step in enumerate(steps):
        if "replay_from" in step:
            source = step["replay_from"]
            if type(source) is not int or not 0 <= source < index:
                raise ValueError("replay_from must index a prior request step")
    if "compatibility" in oracle:
        spec = oracle["compatibility"]
        if (
            not isinstance(spec, dict)
            or spec.get("feature")
            not in ("identifier", "schema", "reasoning_pair", "forced_tool_choice")
            or spec.get("reference_basis", "observed") not in ("observed", "documented")
            or type(spec.get("allow_rejection")) is not bool
            or type(spec.get("reference_status")) is not int
            or spec["reference_status"] not in (200, 400, 422)
            or not isinstance(spec.get("reference_date"), str)
        ):
            raise ValueError("invalid compatibility probe specification")
        try:
            if (
                date.fromisoformat(spec["reference_date"]).isoformat()
                != spec["reference_date"]
            ):
                raise ValueError("reference date must use YYYY-MM-DD")
        except ValueError as exc:
            raise ValueError("invalid compatibility reference_date") from exc


class Manifest(Record):
    profile_snapshot: Profile | None = None
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
    prompt_id: str | None = None
    retry: int = Field(default=0, ge=0)
    capture: dict[str, Any] = Field(default_factory=dict)
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
    http_exchange_completed: bool | None = None
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AssertionResult(Record):
    id: str
    status: ResultStatus
    reason: str
    rule_ids: list[str] = Field(default_factory=list)
    gating: bool = False
    observed: Any = None


class MetricObservation(Record):
    name: str
    value: float | None = None
    denominator: int = 1
    scored: bool = True
    reason: str | None = None

    @model_validator(mode="after")
    def validate_numeric_observation(self) -> MetricObservation:
        if self.value is not None and not math.isfinite(self.value):
            raise ValueError("metric value must be finite")
        if self.denominator < 0:
            raise ValueError("metric denominator must be nonnegative")
        if self.scored and (self.value is None or self.denominator <= 0):
            raise ValueError("scored metric requires a value and positive denominator")
        return self


class AttemptMetric(Record):
    """Content-free HTTP attempt projection retained in run summaries."""

    endpoint: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    prompt_id: str | None = None
    repetition: int = Field(ge=0)
    step: int = Field(ge=0)
    retry: int = Field(ge=0)
    attempt_number: PositiveInt
    status_code: int | None = Field(default=None, ge=100, le=599)
    timings: dict[str, float] = Field(default_factory=dict)
    http_exchange_completed: bool | None = None
    error_type: str | None = None
    interrupted_reservation: bool = False

    @model_validator(mode="after")
    def validate_timings(self) -> AttemptMetric:
        if any(
            not math.isfinite(value) or value < 0 for value in self.timings.values()
        ):
            raise ValueError("attempt timings must be finite and nonnegative")
        if self.interrupted_reservation and (
            self.status_code is not None or self.http_exchange_completed
        ):
            raise ValueError("interrupted reservation cannot be an HTTP completion")
        return self


class ComparisonPolicy(Record):
    allowed_drops: dict[str, float] = Field(min_length=1)
    minimum_distinct_prompts: int = Field(ge=2)
    minimum_repetitions: PositiveInt
    confidence_level: float
    bootstrap_samples: PositiveInt = 2000
    bootstrap_seed: int

    @model_validator(mode="after")
    def validate_policy(self) -> ComparisonPolicy:
        if not 0 < self.confidence_level < 1:
            raise ValueError("confidence level must be between zero and one")
        if any(
            not name or not math.isfinite(margin) or margin < 0
            for name, margin in self.allowed_drops.items()
        ):
            raise ValueError("allowed drops require named finite nonnegative margins")
        return self


class ManifestDifference(Record):
    field: str = Field(min_length=1)
    reference: Any
    candidate: Any
    compatible: bool = False
    reason: str = Field(min_length=1)


class ComparisonMetric(Record):
    value: float | None = None
    numerator: float = 0
    denominator: int = Field(ge=0)
    unavailable: int = Field(ge=0)
    reference_value: float | None = None
    reference_numerator: float = 0
    reference_denominator: int = Field(ge=0)
    reference_unavailable: int = Field(ge=0)
    difference: float | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    confidence_level: float | None = None
    bootstrap_seed: int | None = None
    paired_observations: int = Field(default=0, ge=0)
    paired_distinct_prompts: int = Field(default=0, ge=0)
    paired_repetitions: int = Field(default=0, ge=0)
    missing_reference: int = Field(default=0, ge=0)
    missing_candidate: int = Field(default=0, ge=0)
    counts: dict[str, int] = Field(default_factory=dict)


class ComparisonReportContext(Record):
    """Portable provenance needed to render a stored comparison result."""

    created_at: datetime
    profile: str | None = None
    profile_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    dataset_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    endpoint_models: dict[str, str] = Field(default_factory=dict)
    case_count: int = Field(ge=0)
    required_case_count: int = Field(ge=0)
    status_counts: dict[ResultStatus, int] = Field(default_factory=dict)
    enabled_gates: list[str] = Field(default_factory=list)
    evidence_links: dict[str, list[str]] = Field(default_factory=dict)
    integrity: Literal["verified", "summary-only", "unavailable"] = "unavailable"
    quality_verdict: Literal["PASS", "FAIL", "INCONCLUSIVE", "REPORT_ONLY"] | None = (
        None
    )
    exit_code: Literal[0, 1, 2] | None = None


class ComparisonResult(Record):
    comparable: bool
    reference_endpoint: str
    candidate_endpoint: str
    manifest_differences: list[ManifestDifference]
    metrics: dict[str, ComparisonMetric]
    policy: ComparisonPolicy | None = None
    metric_gates: dict[str, Literal["PASS", "FAIL", "INCONCLUSIVE"]] = Field(
        default_factory=dict
    )
    quality_gate: Literal["PASS", "FAIL", "INCONCLUSIVE"] | None = None
    reasons: list[str] = Field(default_factory=list)
    report: ComparisonReportContext | None = None


class BehavioralPrompt(Record):
    id: str
    dataset_version: str
    category: Literal["required", "forbidden", "ambiguous", "schema", "follow_up"]
    content: str
    intended_answer: Any
    license: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CaseResult(Record):
    prompt_id: str | None = None
    repetition: int = Field(default=0, ge=0)
    first_attempt_status: ResultStatus | None = None
    eventual_status: ResultStatus | None = None
    completed: bool = True
    case_id: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    status: ResultStatus
    assertions: list[AssertionResult]
    metric_observations: list[MetricObservation]
    attempt_refs: list[str]
    reason: str | None = None


class RunReportContext(Record):
    """Portable provenance needed to render a stored run result."""

    run_id: str | None = None
    created_at: datetime | None = None
    profile: str | None = None
    profile_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    dataset_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    endpoints: dict[str, str] = Field(default_factory=dict)
    required_case_ids: list[str] = Field(default_factory=list)
    evidence_links: dict[str, list[str]] = Field(default_factory=dict)
    integrity: Literal["verified", "summary-only", "unavailable"] = "unavailable"


class RunResult(Record):
    actual_concurrency: PositiveInt = 1
    resume_dispositions: list[dict[str, Any]] = Field(default_factory=list)
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    complete: bool
    case_results: list[CaseResult]
    counts: dict[str, int]
    budget_usage: dict[str, int]
    enabled_gates: list[str]
    exit_code: Literal[0, 1, 2]
    attempt_metrics: list[AttemptMetric] = Field(default_factory=list)
    report: RunReportContext | None = None


class ResumeState(Record):
    manifest_hash: str
    completed_case_ids: list[str] = Field(default_factory=list)
    prior_attempts: list[Attempt] = Field(default_factory=list)
    results: list[CaseResult] = Field(default_factory=list)
    incomplete_records: list[dict[str, Any]] = Field(default_factory=list)
