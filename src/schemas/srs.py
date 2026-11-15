"""IEEE 830 SRS schema, matching templates/standard_srs.yaml key for key.

Section layout follows templates/IEEE_830_SRS_Template.md:
front matter (document control, TOC, revision history) + sections 1-6.

Rules:
- Every field is required; unknown fields are rejected.
- Narrative content the sources do not support is marked `unresolved` (or
  `not_applicable` with a reason), never invented.
- Section 3 and section 5 list the same features in the same order.
- FR ids are REQ-<feature ordinal>.<n>, NFR ids are NF-<category ordinal>.<n>.
- The table of contents is composed by `build_toc`, not written by a model.

Keep in sync with contracts/models.py (SRS part), which this mirrors. Test
artifacts (stories, acceptance criteria, test cases) are not part of the SRS.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
IsoDate = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
Priority = Literal["must", "should", "could", "wont"]
ContentStatus = Literal["specified", "unresolved", "not_applicable"]
VerificationMethod = Literal["test", "inspection", "analysis", "demonstration"]

FR_ID_PATTERN = r"^REQ-\d+\.\d+$"
NFR_ID_PATTERN = r"^NF-\d+\.\d+$"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# --------------------------------------------------------------------- shared


class Narrative(Contract):
    """A free-text subsection (1.x, 2.x, 4.x).

    specified       -> text required
    unresolved / NA -> text must be null and a reason is required
    """

    status: ContentStatus
    text: Text | None
    reason: Text | None
    source_ids: list[Text]

    @model_validator(mode="after")
    def check_content(self):
        if self.status == "specified" and self.text is None:
            raise ValueError("Specified content requires text")
        if self.status != "specified" and (self.text is not None or not self.reason):
            raise ValueError("Unresolved/N/A content requires null text and a reason")
        return self


# --------------------------------------------------------------- front matter


class DocumentControl(Contract):
    project_name: Text
    version: Text
    prepared_by: list[Text] = Field(min_length=1)
    organization: Text
    date_created: IsoDate


class TocEntry(Contract):
    section_number: Text
    title: Text


class Revision(Contract):
    name: Text
    date: IsoDate
    reason_for_changes: Text
    version: Text


# ------------------------------------------------------------ 1. Introduction


class Reference(Contract):
    reference_id: Text
    title: Text
    author: Text | None
    version: Text | None
    date: Text | None
    location: Text | None
    source_ids: list[Text]


class Introduction(Contract):
    purpose: Narrative = Field(description="1.1. Purpose")
    document_conventions: Narrative = Field(description="1.2. Document Conventions")
    intended_audience_and_reading_suggestions: Narrative = Field(
        description="1.3. Intended Audience and Reading Suggestions"
    )
    product_scope: Narrative = Field(description="1.4. Product Scope")
    references: list[Reference] = Field(description="1.5. References")


# ---------------------------------------------------- 2. Overall Description


class OverallDescription(Contract):
    product_perspective: Narrative = Field(description="2.1. Product Perspective")
    product_features: Narrative = Field(description="2.2. Product Features")
    user_classes_and_characteristics: Narrative = Field(
        description="2.3. User Classes and Characteristics"
    )
    operating_environment: Narrative = Field(description="2.4. Operating Environment")
    design_and_implementation_constraints: Narrative = Field(
        description="2.5. Design and Implementation Constraints"
    )
    user_documentation: Narrative = Field(description="2.6. User Documentation")
    assumptions_and_dependencies: Narrative = Field(
        description="2.7. Assumptions and Dependencies"
    )


# -------------------------------------------------------- 3. System Features


class FeatureOverview(Contract):
    """3.N: short (1-3 sentence) introduction of a feature."""

    feature_id: Text
    ordinal: int = Field(ge=1)
    name: Text
    description: Text
    source_ids: list[Text]


# ------------------------------------------ 4. External Interface Requirements


class ExternalInterfaces(Contract):
    user_interfaces_overview: Narrative = Field(description="4.1. User Interfaces Overview")
    hardware_interfaces: Narrative = Field(description="4.2. Hardware Interfaces")
    software_interfaces: Narrative = Field(description="4.3. Software Interfaces")


# ------------------------------------------------- 5. System Features/Modules


class DescriptionAndPriority(Contract):
    """5.N.1"""

    description: Text
    priority: Priority


class StimulusResponse(Contract):
    """5.N.2: one stimulus/condition and the system's response."""

    sequence_id: Text
    stimulus_or_condition: Text
    response: Text
    source_ids: list[Text]


class FunctionalRequirement(Contract):
    """5.N.3: one "shall" statement, id REQ-<feature ordinal>.<n>."""

    requirement_id: Annotated[str, Field(pattern=FR_ID_PATTERN)]
    statement: Text
    priority: Priority
    stimulus_response_ids: list[Text]  # empty for FRs not tied to an S/R pair
    source_ids: list[Text] = Field(min_length=1)


class FeatureModule(Contract):
    """5.N: detailed functional requirements for the feature introduced in 3.N."""

    feature_id: Text
    ordinal: int = Field(ge=1)
    name: Text
    description_and_priority: DescriptionAndPriority = Field(description="5.N.1")
    stimulus_response_sequences: list[StimulusResponse] = Field(
        min_length=1, description="5.N.2"
    )
    functional_requirements: list[FunctionalRequirement] = Field(
        min_length=1, description="5.N.3"
    )


# ---------------------------------------------- 6. Nonfunctional Requirements


class NonfunctionalRequirement(Contract):
    """6.N.x: id NF-<category ordinal>.<n>, with a measurable acceptance target."""

    requirement_id: Annotated[str, Field(pattern=NFR_ID_PATTERN)]
    statement: Text
    priority: Priority
    verification_method: VerificationMethod
    acceptance_measure: Text
    source_ids: list[Text] = Field(min_length=1)


class NonfunctionalGroup(Contract):
    """6.N: one NFR category (Performance, Security, ...)."""

    ordinal: int = Field(ge=1)
    name: Text
    status: ContentStatus
    reason: Text | None
    requirements: list[NonfunctionalRequirement]

    @model_validator(mode="after")
    def check_applicability(self):
        if self.status == "specified" and not self.requirements:
            raise ValueError("Specified NFR category requires requirements")
        if self.status != "specified" and (self.requirements or not self.reason):
            raise ValueError(
                "Unresolved/N/A NFR category requires empty requirements and a reason"
            )
        return self


class NonfunctionalRequirements(Contract):
    performance: NonfunctionalGroup = Field(description="6.1. Performance")
    security: NonfunctionalGroup = Field(description="6.2. Security")
    additional_categories: list[NonfunctionalGroup] = Field(description="6.N, N >= 3")


# ------------------------------------------------------------------ document


class SRS(Contract):
    """The complete SRS document.

    No communications-interface subsection is added: the template ends
    section 4 at 4.3. TOC and numbering are composed by code.
    """

    document_control: DocumentControl
    table_of_contents: list[TocEntry]
    revision_history: list[Revision] = Field(min_length=1)
    introduction: Introduction = Field(description="1. Introduction")
    overall_description: OverallDescription = Field(description="2. Overall Description")
    system_features: list[FeatureOverview] = Field(min_length=1, description="3. System Features")
    external_interface_requirements: ExternalInterfaces = Field(
        description="4. External Interface Requirements"
    )
    system_features_modules: list[FeatureModule] = Field(
        min_length=1, description="5. System Features/Modules"
    )
    nonfunctional_requirements: NonfunctionalRequirements = Field(
        description="6. Nonfunctional Requirements"
    )

    @model_validator(mode="after")
    def check_consistency(self):
        if self.document_control.version != self.revision_history[-1].version:
            raise ValueError("Document version must match the last revision")

        overviews = [(f.feature_id, f.ordinal, f.name) for f in self.system_features]
        modules = [(f.feature_id, f.ordinal, f.name) for f in self.system_features_modules]
        if overviews != modules:
            raise ValueError("Section 3 and section 5 features must match in order, ID and name")
        if [f.ordinal for f in self.system_features] != list(range(1, len(overviews) + 1)):
            raise ValueError("Feature numbering must be sequential")

        groups = self.nfr_groups()
        if groups[0].name != "Performance" or groups[1].name != "Security":
            raise ValueError("Sections 6.1/6.2 must be named Performance/Security")
        if [g.ordinal for g in groups] != list(range(1, len(groups) + 1)):
            raise ValueError("NFR category numbering must be sequential, performance=1/security=2")

        identifiers = [f.feature_id for f in self.system_features]
        for module in self.system_features_modules:
            sequences = {s.sequence_id for s in module.stimulus_response_sequences}
            covered: set[str] = set()
            identifiers.extend(s.sequence_id for s in module.stimulus_response_sequences)
            for req in module.functional_requirements:
                if not req.requirement_id.startswith(f"REQ-{module.ordinal}."):
                    raise ValueError("FR prefix must match its feature ordinal")
                if not set(req.stimulus_response_ids) <= sequences:
                    raise ValueError("FR references an unknown stimulus/response sequence")
                covered.update(req.stimulus_response_ids)
                identifiers.append(req.requirement_id)
            if covered != sequences:
                raise ValueError("Every stimulus/response sequence requires an FR")

        for group in groups:
            for req in group.requirements:
                if not req.requirement_id.startswith(f"NF-{group.ordinal}."):
                    raise ValueError("NFR prefix must match its category ordinal")
                identifiers.append(req.requirement_id)

        if len(identifiers) != len(set(identifiers)):
            raise ValueError("SRS IDs must be unique")
        if [entry.model_dump() for entry in self.table_of_contents] != self.expected_toc():
            raise ValueError("Table of contents must match the template and feature numbering")
        return self

    def expected_toc(self) -> list[dict]:
        return build_toc(
            [(f.ordinal, f.name) for f in self.system_features],
            [(g.ordinal, g.name) for g in self.nfr_groups()],
        )

    def nfr_groups(self) -> list[NonfunctionalGroup]:
        nfr = self.nonfunctional_requirements
        return [nfr.performance, nfr.security, *nfr.additional_categories]

    def requirement_ids(self) -> set[str]:
        """All FR and NFR ids in the document."""
        return {
            r.requirement_id for m in self.system_features_modules for r in m.functional_requirements
        } | {r.requirement_id for g in self.nfr_groups() for r in g.requirements}

    def source_ids(self) -> set[str]:
        """Every source id cited anywhere in the document."""
        found: set[str] = set()

        def walk(value):
            if isinstance(value, dict):
                found.update(value.get("source_ids", []))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(self.model_dump())
        return found


# ----------------------------------------------------------------------- TOC


def build_toc(
    features: list[tuple[int, str]], nfr_groups: list[tuple[int, str]]
) -> list[dict]:
    """Compose the table of contents from feature and NFR category names."""
    entries: list[dict] = []

    def add(number: str, title: str) -> None:
        entries.append({"section_number": number, "title": title})

    add("1", "Introduction")
    for i, title in enumerate([
        "Purpose", "Document Conventions", "Intended Audience and Reading Suggestions",
        "Product Scope", "References",
    ], 1):
        add(f"1.{i}", title)

    add("2", "Overall Description")
    for i, title in enumerate([
        "Product Perspective", "Product Features", "User Classes and Characteristics",
        "Operating Environment", "Design and Implementation Constraints", "User Documentation",
        "Assumptions and Dependencies",
    ], 1):
        add(f"2.{i}", title)

    add("3", "System Features")
    for ordinal, name in features:
        add(f"3.{ordinal}", name)

    add("4", "External Interface Requirements")
    for i, title in enumerate(
        ["User Interfaces Overview", "Hardware Interfaces", "Software Interfaces"], 1
    ):
        add(f"4.{i}", title)

    add("5", "System Features/Modules")
    for ordinal, name in features:
        add(f"5.{ordinal}", name)
        for i, title in enumerate([
            "Description and Priority", "Stimulus/Response Sequences", "Functional Requirements",
        ], 1):
            add(f"5.{ordinal}.{i}", title)

    add("6", "Nonfunctional Requirements")
    for ordinal, name in nfr_groups:
        add(f"6.{ordinal}", name)
    return entries
