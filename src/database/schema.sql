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

CREATE TABLE IF NOT EXISTS requirements (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    statement   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_requirements_status
    ON requirements(status);

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
