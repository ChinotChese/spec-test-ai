-- 003: every requirement and SRS run belongs to a project (project_id NOT NULL)
--
-- 002 added project_id as a nullable column (ALTER TABLE ADD COLUMN cannot add
-- NOT NULL without a non-NULL default). Projects are now created first and the
-- whole pipeline passes project_id, so NULL is no longer a valid state.
-- (002's comments mention a backfill command; it was removed before any
-- requirement existed without a project. 002 is left as applied.)
--
-- SQLite cannot add NOT NULL to an existing column, so both tables are rebuilt
-- (the procedure from https://sqlite.org/lang_altertable.html#otheralter):
-- create the new table, copy, drop the old one, rename, recreate indexes.
-- The runner has foreign keys OFF during this, so requirement_evidence and
-- srs_run_requirements keep pointing at the table name, and it runs
-- PRAGMA foreign_key_check before committing.
--
-- If any row still has project_id NULL, the INSERT fails with
-- "NOT NULL constraint failed", the whole migration is rolled back and the
-- database stays at version 2. Assign those rows a project, then run it again.

-- ------------------------------------------------------------ requirements

CREATE TABLE requirements_new (
    id                     TEXT PRIMARY KEY,
    project_id             TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
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
    feature                TEXT,
    stimulus_or_condition  TEXT,
    response               TEXT,

    -- nonfunctional only
    nfr_category           TEXT,
    verification_method    TEXT
                           CONSTRAINT valid_verification_method
                           CHECK (verification_method IN
                                  ('test', 'inspection', 'analysis', 'demonstration')),
    acceptance_measure     TEXT,

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

-- rowid is copied so list order (created_at, rowid) is unchanged.
INSERT INTO requirements_new (
    rowid, id, project_id, type, statement, status, priority,
    feature, stimulus_or_condition, response,
    nfr_category, verification_method, acceptance_measure,
    created_at, updated_at
)
SELECT
    rowid, id, project_id, type, statement, status, priority,
    feature, stimulus_or_condition, response,
    nfr_category, verification_method, acceptance_measure,
    created_at, updated_at
FROM requirements;

DROP TABLE requirements;
ALTER TABLE requirements_new RENAME TO requirements;

CREATE INDEX idx_requirements_status
    ON requirements(status);

CREATE INDEX idx_requirements_feature
    ON requirements(feature) WHERE feature IS NOT NULL;

CREATE INDEX idx_requirements_nfr_category
    ON requirements(nfr_category) WHERE nfr_category IS NOT NULL;

CREATE INDEX idx_requirements_project
    ON requirements(project_id);

-- ---------------------------------------------------------------- srs_runs

CREATE TABLE srs_runs_new (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    srs_json     TEXT NOT NULL CHECK (json_valid(srs_json)),
    output_path  TEXT,                         -- NULL until the SRS is exported
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO srs_runs_new (rowid, id, project_id, srs_json, output_path, created_at)
SELECT rowid, id, project_id, srs_json, output_path, created_at
FROM srs_runs;

DROP TABLE srs_runs;
ALTER TABLE srs_runs_new RENAME TO srs_runs;

CREATE INDEX idx_srs_runs_project
    ON srs_runs(project_id);
