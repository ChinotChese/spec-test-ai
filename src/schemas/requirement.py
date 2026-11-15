"""Requirement schemas: one requirement extracted from source passages.

A requirement flows through the pipeline as:

    RequirementDraft  -- extractor output: content + evidence, no id/status yet
    Requirement       -- stored record: draft + id + review status (+ timestamps)

The fields come from the SRS template (templates/standard_srs.yaml):
- functional    -> placed in section 5 under `feature`; may carry one
                   stimulus/response pair (5.N.2) it answers.
- nonfunctional -> placed in section 6 under `nfr_category`
                   (Performance, Security, or an additional category), with a
                   verification method and a measurable acceptance target.

The positional SRS ids (REQ-1.1, NF-2.1) are NOT assigned here; the SRS
generator numbers requirements when it composes the document.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .srs import Contract, Priority, Text, VerificationMethod

RequirementType = Literal["functional", "nonfunctional"]
RequirementStatus = Literal["pending", "approved", "rejected"]


class Evidence(Contract):
    """One source passage that supports the requirement."""

    passage_id: Text
    quote: Text | None = None  # exact excerpt from the passage, if available


class RequirementDraft(Contract):
    type: RequirementType
    statement: Text  # "shall" form, e.g. "Upon <event>, the system shall <action>."
    priority: Priority
    evidence: list[Evidence] = Field(min_length=1)

    # functional only (section 5)
    feature: Text | None = None
    stimulus_or_condition: Text | None = None
    response: Text | None = None

    # nonfunctional only (section 6)
    nfr_category: Text | None = None  # "Performance", "Security", or another category
    verification_method: VerificationMethod | None = None
    acceptance_measure: Text | None = None

    @model_validator(mode="after")
    def check_type_fields(self):
        functional = (self.feature, self.stimulus_or_condition, self.response)
        nonfunctional = (self.nfr_category, self.verification_method, self.acceptance_measure)

        if self.type == "functional":
            if self.feature is None:
                raise ValueError("Functional requirement requires a feature")
            if (self.stimulus_or_condition is None) != (self.response is None):
                raise ValueError("stimulus_or_condition and response must be given together")
            if any(v is not None for v in nonfunctional):
                raise ValueError(
                    "Functional requirement cannot set nfr_category, "
                    "verification_method or acceptance_measure"
                )
        else:
            if any(v is None for v in nonfunctional):
                raise ValueError(
                    "Nonfunctional requirement requires nfr_category, "
                    "verification_method and acceptance_measure"
                )
            if any(v is not None for v in functional):
                raise ValueError(
                    "Nonfunctional requirement cannot set feature, "
                    "stimulus_or_condition or response"
                )

        passage_ids = [e.passage_id for e in self.evidence]
        if len(passage_ids) != len(set(passage_ids)):
            raise ValueError("A passage may be cited only once per requirement")
        return self

    @property
    def passage_ids(self) -> list[str]:
        return [e.passage_id for e in self.evidence]


class Requirement(RequirementDraft):
    """A stored requirement under review. Only `approved` ones enter an SRS."""

    id: Text
    status: RequirementStatus = "pending"
    created_at: str | None = None  # ISO 8601 UTC, set by the database
    updated_at: str | None = None

    @property
    def is_approved(self) -> bool:
        return self.status == "approved"
