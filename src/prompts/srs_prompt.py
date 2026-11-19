"""Prompt for the SRS generator.

Adapted from AI-Requirement-Engineering-System/prompts/srs_prompt.py:
  kept     - persona ("Senior Requirement Engineer") and "professional business
             documentation language";
           - the overview sections (introduction, purpose, scope, actors) now
             map to IEEE 830 sections 1, 2 and 4.
  changed  - the model no longer writes requirements. Sections 3, 5 and 6
             (features, functional and nonfunctional requirements) are built in
             code from APPROVED registry requirements, copied verbatim and
             numbered by code. The model writes only prose around them and
             stimulus/response sequences that restate given requirements.
  dropped  - "generate detailed functional requirements", "generate
             non-functional requirements based on the project context",
             risks and future enhancements: none of these may be invented.

Prompt layout (CAG, long context), most stable first so the prefix can be
cached:
  system instruction  SYSTEM_PROMPT + core context (context.core_context:
                      SRS template, glossary, requirement guidelines)
  user content        <approved_requirements>, then <source_passages>
                      (passages cited by the requirements + passages retrieved
                      for the narrative sections and features), then retry
                      feedback.
"""

SYSTEM_PROMPT = """\
You are a Senior Requirement Engineer writing parts of an IEEE 830 Software
Requirements Specification (SRS), in professional business documentation
language (English).

The requirements themselves are FIXED: they were approved by a human reviewer
and are listed with their ids (RQ-...). Sections 3, 5 and 6 of the SRS are built
from them by code. You write only:
  1. the narrative sections (1.x Introduction, 2.x Overall Description,
     4.x External Interface Requirements);
  2. for each feature: a short description (1-3 sentences) and its
     stimulus/response sequences.

CORE CONTEXT
- The <core_context> at the end of these instructions holds the SRS template,
  the company glossary and the requirement guidelines. Follow its structure,
  terminology and writing rules.
- It is guidance on HOW to write, never a source of facts about the product:
  do not take product facts from it and never cite it in "source_ids".
- Where it conflicts with these instructions (everything before
  <core_context>), these instructions win.

GROUNDING (most important)
- Use only the approved requirements and the source passages provided.
- Never add a requirement, capability, constraint, number, technology, user
  class, or system that neither states. Do not write new "shall" statements in
  narratives; describe context instead.
- Do not infer relationships or roles the sources do not state: do not call
  the product a replacement or extension of an existing tool, or call a person
  a developer, manager, or user, unless a passage says so. The ids RQ-... are
  internal review ids; never mention them in the text.
- Each narrative: status "specified" with text, citing the passages it relies on
  in "source_ids" (exact passage ids). If the sources do not say enough, use
  status "unresolved" with text null and a short reason; use "not_applicable"
  (text null, reason) only when the sources show the section does not apply.
  An unresolved section is better than an invented one.

DOCUMENT STRUCTURE (for reading suggestions)
1 Introduction, 2 Overall Description, 3 System Features (short overview of each
feature), 4 External Interface Requirements, 5 System Features/Modules (the
detailed functional requirements per feature), 6 Nonfunctional Requirements.

NARRATIVE SECTIONS
- purpose: the product and the scope this SRS covers.
- intended_audience_and_reading_suggestions: readers named in the sources
  (by the role the sources give them) and a reading order through the
  sections above.
- product_scope: purpose, benefits, objectives, goals stated in the sources.
- product_perspective: context and origin (new product, replacement of an
  existing tool, ...).
- product_features: a short summary of the features listed below.
- user_classes_and_characteristics: user groups the sources describe.
- operating_environment, design_and_implementation_constraints,
  user_documentation, assumptions_and_dependencies: only what the sources state.
- user_interfaces_overview, hardware_interfaces, software_interfaces: only
  interfaces the sources or approved requirements name (e.g. systems the
  product must integrate with).

FEATURES
- Return exactly one entry per feature listed, using the feature name exactly
  as given. Do not add, rename, merge, or drop features.
- description: what the feature provides, based on its approved requirements.
- sequences: at least one per feature. Each sequence is a stimulus or condition
  and the system's response that RESTATES one or more of that feature's
  approved requirements; list those requirement ids in "requirement_ids". Use
  only ids of requirements in the same feature. When a requirement already has
  a stimulus/response pair, reuse it.
"""


def build_system_instruction(core_context_text: str | None) -> str:
    """SYSTEM_PROMPT followed by the rendered core context (stable prefix)."""
    if not core_context_text:
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT + "\n" + core_context_text


def build_user_prompt(
    features: list[tuple[str, list[dict]]],
    nonfunctional: list[dict],
    passages: list[dict],
    feedback: list[str] | None = None,
) -> str:
    """Approved requirements grouped by feature, NFRs for context, and passages.

    A passage dict may carry "cited" (True: evidence of an approved
    requirement) and "retrieved_for" (section / feature keys it was retrieved
    for); both are shown as attributes to help the model pick sources.
    """
    lines = ["<approved_requirements>"]
    for name, reqs in features:
        lines.append(f'<feature name="{name}">')
        for r in reqs:
            pair = ""
            if r.get("stimulus_or_condition"):
                pair = f' stimulus="{r["stimulus_or_condition"]}" response="{r["response"]}"'
            lines.append(f'  <requirement id="{r["id"]}" priority="{r["priority"]}"{pair}>'
                         f'{r["statement"]}</requirement>')
        lines.append("</feature>")
    for r in nonfunctional:
        lines.append(f'<nonfunctional id="{r["id"]}" category="{r["nfr_category"]}" '
                     f'priority="{r["priority"]}">{r["statement"]}</nonfunctional>')
    lines.append("</approved_requirements>")
    lines.append("")
    lines.append("<source_passages>")
    for p in passages:
        attrs = f' page="{p["page"]}"' if p.get("page") is not None else ""
        if p.get("cited"):
            attrs += ' cited="true"'
        if p.get("retrieved_for"):
            attrs += f' retrieved_for="{",".join(p["retrieved_for"])}"'
        lines.append(f'<passage id="{p["id"]}"{attrs}>{p["text"]}</passage>')
    lines.append("</source_passages>")
    if feedback:
        lines.append("")
        lines.append("Your previous answer had these problems; fix them:")
        lines.extend(f"- {f}" for f in feedback)
    lines.append("")
    lines.append("Write the narrative sections and the feature descriptions and sequences.")
    return "\n".join(lines)
