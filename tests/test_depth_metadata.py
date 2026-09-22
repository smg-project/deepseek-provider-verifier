"""Reject malformed opt-in semantics before a manifest can reach the network."""

import pytest

from deepseek_provider_verifier.catalog import load_cases
from deepseek_provider_verifier.depth_metadata import validate_depth_oracle
from deepseek_provider_verifier.records import CaseTemplate


@pytest.mark.parametrize(
    "metadata",
    [
        {"version": 2, "family": "instruction"},
        {"version": True, "family": "instruction"},
        {"version": 1, "family": ""},
        {"version": 1, "family": 7},
        {
            "version": 1,
            "family": "x",
            "resource_limits": {"max_request_bytes": True, "max_response_bytes": 8},
        },
        {
            "version": 1,
            "family": "x",
            "resource_limits": {"max_request_bytes": -1, "max_response_bytes": 8},
        },
    ],
)
def test_invalid_metadata_rejected_in_records(metadata):
    with pytest.raises(ValueError, match="depth|resource"):
        validate_depth_oracle({"depth": metadata})
    value = load_cases(["chat"], ["C01"])[0].model_dump()
    value["oracle"]["depth"] = metadata
    with pytest.raises(ValueError):
        CaseTemplate.model_validate(value)


def test_legacy_oracles_are_untouched():
    validate_depth_oracle({"kind": "exact_text", "text": "amber"})
