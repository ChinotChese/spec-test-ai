# Requirement Guidelines

Rules for writing, classifying, prioritising, and tracing requirements in a SpecTestAI SRS (IEEE 830 layout). They apply to requirement extraction, review, and SRS generation alike.

## 1. Source of truth

- Every requirement and every narrative section is backed by source passages of the project. Nothing is added from general knowledge, best practice, or what such a system "usually" needs.
- Extract what the passages state, or what a stakeholder explicitly proposes and the other party does not reject.
- Descriptions of the current situation or of existing tools are context, not requirements, unless a stakeholder says the new system must keep, replace, or change that behaviour.
- Greetings, signatures, scheduling, meeting logistics, team rosters, and questions nobody answered state no requirement.
- In conversations, later messages may change, accept, or reject earlier proposals. Use the latest version and cite both the proposal and the reply.

## 2. Requirement statements

- One requirement = one verifiable behaviour or constraint. A bullet list in the source usually means one requirement per bullet. Never repeat a requirement.
- English, "shall" form: "The system shall ..." or "Upon <event>, the system shall <action>." Translate when the source is in another language.
- Concise, unambiguous, verifiable. Keep the source's concrete values: names, channels, systems, numbers, deadlines. Add no facts, numbers, technologies, or limits the source does not give.
- A prohibition is a requirement in "shall not" form, with the priority the source expresses (usually must).
- Cover invalid inputs and error conditions only when the source states them.

## 3. Functional vs nonfunctional (FR / NFR)

| | Functional (FR) | Nonfunctional (NFR) |
|---|---|---|
| What | Something the system does | A quality or constraint on how it does it |
| SRS section | 5. System Features/Modules, grouped by feature | 6. Nonfunctional Requirements, grouped by category |
| Required fields | `feature`: short name (2-5 words), reused exactly for related requirements | `nfr_category`, `verification_method`, `acceptance_measure` |
| Optional fields | `stimulus_or_condition` + `response`, only when the source describes a trigger and the system's reaction | - |
| SRS id | `REQ-<feature>.<n>` (e.g. REQ-1.1), assigned by the SRS generator | `NF-<category>.<n>` (e.g. NF-2.1), assigned by the SRS generator |

- Never fill in fields that belong to the other type.
- NFR categories: Performance (6.1), Security (6.2), then additional categories as needed, e.g. Usability, Compliance, Localization, Compatibility, Reliability, Maintainability.
- `verification_method` is one of: test, inspection, analysis, demonstration.
- `acceptance_measure` restates the verifiable condition in the source's own terms. Never introduce a number or threshold the source does not state.
- Every stimulus/response sequence in 5.N.2 is linked from at least one FR in 5.N.3. Other FRs may have no sequence.
- Section 3 (System Features) has one short overview per feature, with the same feature id, ordinal, name, and order as section 5.

## 4. Priority (MoSCoW)

Priority comes from the source's wording, never from the writer's opinion.

| Priority | Source wording |
|---|---|
| must | must, shall, required, need, essential, "very important", or the stated core goal |
| should | should, important, preferred; pain points the stakeholder wants solved |
| could | could, nice to have, optional, later phase; proposals the other party has not confirmed yet |
| wont | explicitly rejected, out of scope, or not needed |

- A wont requirement is stated positively ("The system shall provide live chat.") with priority wont. Never combine wont with "shall not".

## 5. Evidence and traceability

- Each requirement cites at least one source passage by its exact passage id.
- Each evidence item carries a quote: the exact words from that passage, in the passage's original language. Do not paraphrase quotes.
- Valid evidence: every cited passage exists, every item has a non-empty quote, and every quote is found in its passage (compared on letters and digits only, case-insensitive).
- A requirement can be approved only when its evidence is valid. Only approved requirements go into an SRS.
- A requirement cites only passages from documents of its own project. One SRS covers one project.
- Narrative sections and SRS entries list the passages they rely on in `source_ids`.

## 6. Unresolved and not applicable

Each narrative section (1.x, 2.x, 4.x) and each NFR category has a status.

| Status | When | Fields |
|---|---|---|
| specified | The sources support the content | `text` (or `requirements` for an NFR category) is set, `source_ids` cite the passages |
| unresolved | The sources do not (yet) support the content | `text` is null, `reason` says what is missing |
| not_applicable | The section does not apply to this product, with justification | `text` is null, `reason` justifies it |

- An unresolved section is better than an invented one. Never fill a section with plausible but unsupported content.
- Use not_applicable only with a justified reason, e.g. a pure web application with no hardware interfaces stated or implied by the sources.

## 7. Review states

- pending: extracted, waiting for human review.
- approved: accepted by a reviewer; requires valid evidence.
- rejected: not accepted; never used in an SRS.
