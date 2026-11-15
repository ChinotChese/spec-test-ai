-- SpecTestAI SQLite schema
--
-- DOCUMENTS        ||--o{ SOURCE_PASSAGES : contains
-- SOURCE_PASSAGES  }o--o{ REQUIREMENTS    : supports      (junction: requirement_evidence)
-- REQUIREMENTS     }o--o{ SRS_RUNS        : included_in   (junction: srs_run_requirements)
--
-- Foreign keys are only enforced when each connection runs PRAGMA foreign_keys = ON
-- (the repository does this).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    file_hash   TEXT NOT NULL UNIQUE,          -- same file content is ingested once
    file_path   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS source_passages (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page         INTEGER CHECK (page IS NULL OR page >= 1),   -- NULL for unpaged sources
    text         TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_source_passages_document
    ON source_passages(document_id, page);

-- Matches schemas/requirement.py (RequirementDraft / Requirement).
-- functional    -> SRS section 5: feature (+ optional stimulus/response pair)
-- nonfunctional -> SRS section 6: nfr_category, verification_method, acceptance_measure
CREATE TABLE IF NOT EXISTS requirements (
    id                     TEXT PRIMARY KEY,
    type                   TEXT NOT NULL
                           CONSTRAINT valid_type CHECK (type IN ('functional', 'nonfunctional')),
    statement              TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'pending'
                           CONSTRAINT valid_status
                           CHECK (status IN ('pending', 'approved', 'rejected')),
    priority               TEXT NOT NULL
                           CONSTRAINT valid_priority
                           CHECK (priority IN ('must', 'should', 'could', 'wont')),

    -- functional only
    feature                TEXT,               -- feature name, groups FRs into SRS 3.N / 5.N
    stimulus_or_condition  TEXT,               -- SRS 5.N.2 stimulus/condition
    response               TEXT,               -- SRS 5.N.2 system response

    -- nonfunctional only
    nfr_category           TEXT,               -- 'Performance', 'Security', or another category
    verification_method    TEXT
                           CONSTRAINT valid_verification_method
                           CHECK (verification_method IN
                                  ('test', 'inspection', 'analysis', 'demonstration')),
    acceptance_measure     TEXT,               -- measurable target, e.g. 'p95 <= 2 s'

    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    CONSTRAINT functional_fields CHECK (
        type != 'functional'
        OR (feature IS NOT NULL
            AND nfr_category IS NULL
            AND verification_method IS NULL
            AND acceptance_measure IS NULL)
    ),
    CONSTRAINT nonfunctional_fields CHECK (
        type != 'nonfunctional'
        OR (nfr_category IS NOT NULL
            AND verification_method IS NOT NULL
            AND acceptance_measure IS NOT NULL
            AND feature IS NULL
            AND stimulus_or_condition IS NULL
            AND response IS NULL)
    ),
    CONSTRAINT stimulus_response_pair CHECK (
        (stimulus_or_condition IS NULL) = (response IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_requirements_status
    ON requirements(status);

CREATE INDEX IF NOT EXISTS idx_requirements_feature
    ON requirements(feature) WHERE feature IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_requirements_nfr_category
    ON requirements(nfr_category) WHERE nfr_category IS NOT NULL;

CREATE TABLE IF NOT EXISTS srs_runs (
    id           TEXT PRIMARY KEY,
    srs_json     TEXT NOT NULL CHECK (json_valid(srs_json)),
    output_path  TEXT,                         -- NULL until the SRS is exported
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- SOURCE_PASSAGES }o--o{ REQUIREMENTS : supports
-- Deleting a requirement removes its evidence links.
-- A passage cited as evidence cannot be deleted (nor its document).
CREATE TABLE IF NOT EXISTS requirement_evidence (
    requirement_id  TEXT NOT NULL REFERENCES requirements(id)    ON DELETE CASCADE,
    passage_id      TEXT NOT NULL REFERENCES source_passages(id) ON DELETE RESTRICT,
    quote           TEXT,                      -- exact excerpt from the passage, if any
    PRIMARY KEY (requirement_id, passage_id)
);

CREATE INDEX IF NOT EXISTS idx_requirement_evidence_passage
    ON requirement_evidence(passage_id);

-- REQUIREMENTS }o--o{ SRS_RUNS : included_in
-- Deleting an SRS run removes its links.
-- A requirement included in any SRS run cannot be deleted.
CREATE TABLE IF NOT EXISTS srs_run_requirements (
    srs_run_id      TEXT    NOT NULL REFERENCES srs_runs(id)     ON DELETE CASCADE,
    requirement_id  TEXT    NOT NULL REFERENCES requirements(id) ON DELETE RESTRICT,
    position        INTEGER NOT NULL CHECK (position >= 0),      -- order inside the SRS
    PRIMARY KEY (srs_run_id, requirement_id),
    UNIQUE (srs_run_id, position)
);

CREATE INDEX IF NOT EXISTS idx_srs_run_requirements_requirement
    ON srs_run_requirements(requirement_id);
