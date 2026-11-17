-- 002: projects
--
-- PROJECTS  ||--o{ PROJECT_DOCUMENTS : uses
-- DOCUMENTS ||--o{ PROJECT_DOCUMENTS : belongs_to
-- PROJECTS  ||--o{ REQUIREMENTS      : owns
-- PROJECTS  ||--o{ SRS_RUNS          : produces
--
-- Schema only, no data changes. Existing requirements and SRS runs get
-- project_id = NULL, meaning "not assigned to a project yet". Which document
-- belongs to which project is a human decision, so the backfill is a separate,
-- reviewed step: python -m database.project_backfill (database/spec.md §8).
--
-- project_id is nullable on purpose:
--   * SQLite's ALTER TABLE ADD COLUMN only accepts a REFERENCES column whose
--     default is NULL (NOT NULL would need a full table rebuild);
--   * NULL is the review-queue state for rows the backfill could not assign.
-- The repository enforces the rule a foreign key cannot express: every
-- passage a requirement cites must come from a document of its project.

CREATE TABLE projects (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE CHECK (trim(name) != ''),
    description  TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- PROJECTS }o--o{ DOCUMENTS. A document can be shared by several projects
-- (e.g. a common glossary). Deleting either side removes the link; a document
-- whose passages are cited as evidence cannot be deleted anyway.
CREATE TABLE project_documents (
    project_id   TEXT NOT NULL REFERENCES projects(id)  ON DELETE CASCADE,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (project_id, document_id)
);

CREATE INDEX idx_project_documents_document
    ON project_documents(document_id);

-- A project that still owns requirements or SRS runs cannot be deleted.
ALTER TABLE requirements ADD COLUMN project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT;
ALTER TABLE srs_runs     ADD COLUMN project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT;

CREATE INDEX idx_requirements_project
    ON requirements(project_id);

CREATE INDEX idx_srs_runs_project
    ON srs_runs(project_id);
