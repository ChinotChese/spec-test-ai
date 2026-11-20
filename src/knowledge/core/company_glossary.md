# Company Glossary

Canonical terms for SRS documents and requirement work. Use the term in the "Use" column; do not use the variants in the "Avoid" column in requirement statements or SRS text. Quotes in evidence are never changed: they keep the source's own words.

Add company- or product-specific terms as new rows in the matching table, in the same format.

## Specification terms

| Use | Avoid | Meaning |
|---|---|---|
| the system | the app, the tool, the platform, the software, our solution | The product specified by the SRS, in requirement statements |
| shall / shall not | will, must, should, needs to, is able to (in statements) | Binding form of a requirement statement. Priority is expressed by the priority field, not by the verb |
| requirement | feature request, ticket, user story (in the SRS) | One verifiable behaviour or constraint, with evidence |
| functional requirement (FR) | business requirement, use case | Something the system does; SRS section 5 |
| nonfunctional requirement (NFR) | quality requirement, technical requirement | A quality or constraint on how the system does it; SRS section 6 |
| feature | module, epic, function (as a group name) | A named group of related functional requirements (sections 3 and 5) |
| stimulus / response | trigger / action, input / output | A trigger or condition and the system's reaction to it (5.N.2) |
| acceptance measure | acceptance criteria, KPI, target (for an NFR) | The verifiable condition an NFR must meet, in the source's own terms |
| verification method | test type, validation method | How an NFR is verified: test, inspection, analysis, or demonstration |
| priority: must / should / could / wont | high / medium / low, P1 / P2 / P3, critical | MoSCoW priority taken from the source's wording |

## Traceability terms

| Use | Avoid | Meaning |
|---|---|---|
| source document | input file, attachment (in the SRS) | A file ingested for a project (PDF, DOCX, TXT, MD) |
| passage | chunk, paragraph, snippet, segment | One stored, verbatim piece of a source document, with a passage id |
| passage id | chunk id, line number | Identifier of a passage, e.g. `DOC-<id>-P0001`; cited in evidence and `source_ids` |
| evidence | proof, reference, citation (for a requirement) | The passages, with exact quotes, that state a requirement |
| quote | excerpt, summary, paraphrase | Exact words copied from a cited passage, in its original language |
| source_ids | sources, refs | The passage ids an SRS entry or narrative section relies on |
| project | workspace, client folder, case | The unit that owns documents, requirements, and SRS runs. One SRS = one project |

## Review and status terms

| Use | Avoid | Meaning |
|---|---|---|
| pending | draft, new, open (for a requirement) | Extracted, waiting for human review |
| approved | accepted, confirmed, final | Accepted by a reviewer; only approved requirements enter an SRS |
| rejected | declined, removed, deleted | Not accepted by a reviewer; never used in an SRS |
| specified | done, filled, complete (for a section) | The section's content is supported by sources |
| unresolved | TBD, TODO, unknown, N/A (when information is missing) | The sources do not support the section yet; a reason says what is missing |
| not applicable | N/A, none, empty (without reason) | The section does not apply to the product, with a justified reason |

## Roles

| Use | Avoid | Meaning |
|---|---|---|
| stakeholder | client, customer, requester (generically) | A person or party whose statements in the sources define requirements |
| user | end user, customer, operator (unless the source distinguishes them) | A person who uses the system. Name specific user classes as the sources name them (section 2.3) |
| reviewer | approver, checker | A person who approves or rejects extracted requirements |
