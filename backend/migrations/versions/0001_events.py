"""Initial normalized events and provenance."""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DDL = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE sources (
 id text PRIMARY KEY, spec jsonb NOT NULL, enabled boolean NOT NULL,
 status text NOT NULL DEFAULT 'pending', last_attempt_at timestamptz,
 last_success_at timestamptz, newest_content_at timestamptz,
 next_due_at timestamptz NOT NULL DEFAULT now(), record_count integer NOT NULL DEFAULT 0,
 error text, failures integer NOT NULL DEFAULT 0, lease_owner uuid, lease_until timestamptz,
 cursor jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE ingestion_runs (
 id uuid PRIMARY KEY, source_id text NOT NULL REFERENCES sources(id),
 started_at timestamptz NOT NULL, finished_at timestamptz, status text NOT NULL,
 record_count integer NOT NULL DEFAULT 0, rejected_count integer NOT NULL DEFAULT 0,
 changed_count integer NOT NULL DEFAULT 0, error text
);
CREATE TABLE countries (
 iso2 text PRIMARY KEY, name text NOT NULL, geom geometry(MultiPolygon,4326) NOT NULL
);
CREATE INDEX countries_geom ON countries USING gist(geom);
CREATE TABLE events (
 id uuid PRIMARY KEY, kind text NOT NULL, category text NOT NULL,
 title text NOT NULL, description text NOT NULL,
 occurred_start timestamptz, occurred_end timestamptz, issued_at timestamptz,
 source_updated_at timestamptz, first_seen_at timestamptz NOT NULL,
 last_seen_at timestamptz NOT NULL, last_changed_at timestamptz NOT NULL,
 valid_from timestamptz, valid_to timestamptz,
 lifecycle_status text NOT NULL, verification_status text NOT NULL DEFAULT 'reported',
 severity integer NOT NULL CHECK (severity BETWEEN 0 AND 4),
 location_precision text NOT NULL, countries text[] NOT NULL DEFAULT '{}',
 geom geometry(Geometry,4326), normal jsonb NOT NULL,
 source_ids text[] NOT NULL DEFAULT '{}', independent_source_count integer NOT NULL DEFAULT 1,
 change_type text NOT NULL DEFAULT 'new'
);
CREATE INDEX event_geometry ON events USING gist(geom);
CREATE INDEX event_occurred ON events(occurred_start DESC);
CREATE INDEX event_changed ON events(last_changed_at DESC);
CREATE INDEX event_countries ON events USING gin(countries);
CREATE INDEX event_category ON events(category,severity);
CREATE TABLE observations (
 id uuid PRIMARY KEY, source_id text NOT NULL REFERENCES sources(id),
 provider_record_id text NOT NULL, payload_hash text NOT NULL,
 normalizer_version text NOT NULL DEFAULT '1', raw jsonb, normalized jsonb NOT NULL, retrieved_at timestamptz NOT NULL,
 source_updated_at timestamptz,
 UNIQUE(source_id,provider_record_id,payload_hash,normalizer_version)
);
CREATE TABLE event_evidence (
 event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
 observation_id uuid NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
 PRIMARY KEY(event_id,observation_id)
);
CREATE TABLE provider_records (
 source_id text NOT NULL REFERENCES sources(id), provider_record_id text NOT NULL,
 event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
 latest_observation_id uuid NOT NULL REFERENCES observations(id),
 last_seen_at timestamptz NOT NULL,
 PRIMARY KEY(source_id,provider_record_id)
);
CREATE TABLE event_external_ids (
 external_id text PRIMARY KEY, event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE
);
CREATE TABLE identity_overrides (
 source_id text NOT NULL REFERENCES sources(id), provider_record_id text NOT NULL,
 event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
 reason text NOT NULL, created_at timestamptz NOT NULL,
 PRIMARY KEY(source_id,provider_record_id),
 FOREIGN KEY(source_id,provider_record_id) REFERENCES provider_records(source_id,provider_record_id) ON DELETE CASCADE
);
CREATE TABLE event_revisions (
 id uuid PRIMARY KEY, event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
 recorded_at timestamptz NOT NULL, change_type text NOT NULL,
 summary text NOT NULL, snapshot jsonb NOT NULL
);
CREATE INDEX revisions_event ON event_revisions(event_id,recorded_at DESC);
CREATE TABLE event_relations (
 event_a uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
 event_b uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
 relation_type text NOT NULL, reason text NOT NULL,
 distance_km double precision, time_delta_hours double precision,
 created_at timestamptz NOT NULL, PRIMARY KEY(event_a,event_b,relation_type),
 CHECK(event_a < event_b)
);
CREATE TABLE briefing_runs (
 id uuid PRIMARY KEY, created_at timestamptz NOT NULL,
 since_at timestamptz NOT NULL, until_at timestamptz NOT NULL,
 scope jsonb NOT NULL, result jsonb NOT NULL
);
GRANT SELECT ON ALL TABLES IN SCHEMA public TO monitor_reader;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO monitor_worker;
REVOKE INSERT,UPDATE,DELETE ON countries,identity_overrides FROM monitor_worker;
GRANT INSERT ON briefing_runs TO monitor_reader;
"""


def upgrade():
    for statement in DDL.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    raise RuntimeError("Destructive schema downgrades require explicit review and backup")
