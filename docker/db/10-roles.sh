#!/usr/bin/env bash
set -euo pipefail
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
\getenv worker_password MONITOR_WORKER_PASSWORD
\getenv reader_password MONITOR_READER_PASSWORD
SELECT format('CREATE ROLE monitor_worker LOGIN PASSWORD %L', :'worker_password') \gexec
SELECT format('CREATE ROLE monitor_reader LOGIN PASSWORD %L', :'reader_password') \gexec
CREATE EXTENSION IF NOT EXISTS postgis;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO monitor_worker, monitor_reader;
SQL
