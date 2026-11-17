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

    passage_id: Text = Field(description="Id of a source passage that states this requirement.")
    quote: Text | None = Field(
        default=None,
        description="Exact words copied from that passage which support the requirement.",
    )


class RequirementDraft(Contract):
    # Field descriptions are part of the JSON schema sent to the model as
    # structured-output instructions; they do not change validation.
    type: RequirementType = Field(
        description="functional: something the system does. "
        "nonfunctional: a quality or constraint on how it does it."
    )
    statement: Text = Field(
        description='One verifiable requirement in English "shall" form, e.g. '
        '"Upon <event>, the system shall <action>."'
    )
    priority: Priority = Field(description="MoSCoW priority as expressed by the source.")
    evidence: list[Evidence] = Field(
        min_length=1, description="Source passages that state this requirement."
    )

    # functional only (section 5)
    feature: Text | None = Field(
        default=None,
        description="Functional only, required: short feature name; "
        "related requirements share exactly the same name.",
    )
    stimulus_or_condition: Text | None = Field(
        default=None,
        description="Functional only, optional: the triggering event or condition. "
        "Give together with response, or omit both.",
    )
    response: Text | None = Field(
        default=None, description="Functional only, optional: the system's response."
    )

    # nonfunctional only (section 6)
    nfr_category: Text | None = Field(
        default=None,
        description='Nonfunctional only, required: "Performance", "Security", '
        "or another short quality category.",
    )
    verification_method: VerificationMethod | None = Field(
        default=None, description="Nonfunctional only, required: how it would be verified."
    )
    acceptance_measure: Text | None = Field(
        default=None,
        description="Nonfunctional only, required: the verifiable condition, "
        "in the source's own terms.",
    )

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
    project_id: Text | None = None  # None = not assigned to a project yet
    created_at: str | None = None  # ISO 8601 UTC, set by the database
    updated_at: str | None = None

    @property
    def is_approved(self) -> bool:
        return self.status == "approved"
