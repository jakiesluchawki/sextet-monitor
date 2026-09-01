#!/usr/bin/env python3
"""Local lifecycle commands. No shell interpolation, no cloud deployment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configuration():
    path = ROOT / ".env"
    if not path.is_file():
        raise SystemExit("Brak .env. Najpierw: python3 scripts/manage.py init")
    result = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, sep, value = line.partition("=")
            if not sep:
                raise SystemExit("Nieprawidłowy format .env")
            result[key.strip()] = value.strip()
    for key in ("DB_ADMIN_PASSWORD", "DB_WORKER_PASSWORD", "DB_READER_PASSWORD"):
        if not result.get(key) or result[key].startswith("GENERATE_"):
            raise SystemExit("Nieprawidłowa konfiguracja haseł; użyj manage.py init.")
    return result


def environment():
    env = os.environ.copy()
    socket = Path.home() / ".colima/default/docker.sock"
    if not env.get("DOCKER_HOST") and not env.get("DOCKER_CONTEXT") and sys.platform == "darwin" and socket.exists():
        env["DOCKER_HOST"] = "unix://" + str(socket)
    config_dir = ROOT.parent / "work/docker-config"
    if not env.get("DOCKER_CONFIG") and config_dir.is_dir():
        env["DOCKER_CONFIG"] = str(config_dir)
    return env


def compose_argv(*args):
    program = shutil.which("docker-compose")
    base = [program] if program else [shutil.which("docker") or "docker", "compose"]
    return base + ["--project-directory", str(ROOT), "-f", str(ROOT / "compose.yaml")] + list(args)


def compose(*args, capture=False, input=None, extra_env=None, stdout=None, stdin=None):
    env = environment()
    env.update(extra_env or {})
    return subprocess.run(
        compose_argv(*args), cwd=ROOT, env=env, check=True, input=input, stdin=stdin,
        stdout=subprocess.PIPE if capture else stdout,
        stderr=subprocess.PIPE if capture else None,
    )


def admin_url(db="monitor"):
    from urllib.parse import quote
    password = quote(configuration()["DB_ADMIN_PASSWORD"], safe="")
    return f"postgresql+psycopg://monitor_admin:{password}@db:5432/{db}"


def admin_module(*args):
    return compose("run", "--rm", "--no-deps", "--env", "DATABASE_URL", "api",
                   "python", "-m", "monitor.cli", *args,
                   extra_env={"DATABASE_URL": admin_url()})


def sql(query, db="monitor"):
    return compose("exec", "-T", "db", "psql", "-U", "monitor_admin", "-d", db,
                   "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                   "-c", query, capture=True).stdout.decode().strip()


BACKUP_TABLES = (
    "sources", "ingestion_runs", "countries", "events", "observations", "event_evidence",
    "provider_records", "event_external_ids", "identity_overrides", "event_revisions",
    "event_relations", "briefing_runs", "alembic_version",
)


def fingerprint_query():
    pairs = []
    for table in BACKUP_TABLES:
        pairs.append(
            "'" + table + "',(SELECT json_build_object('count',count(*),'row_checksum',md5(COALESCE("
            "string_agg(md5(row_to_json(t)::text),'' ORDER BY md5(row_to_json(t)::text)),''))) "
            f"FROM {table} t)"
        )
    return "SELECT json_build_object(" + ",".join(pairs) + ")"


def fingerprint(db="monitor"):
    return json.loads(sql(fingerprint_query(), db))


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup():
    """Export a database snapshot and fingerprint that exact same snapshot."""
    folder = ROOT / "backups"
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    path = folder / ("monitor-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(2) + ".dump")
    snapshot = subprocess.Popen(
        compose_argv("exec", "-T", "db", "psql", "-U", "monitor_admin", "-d", "monitor",
                     "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1"),
        cwd=ROOT, env=environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    try:
        snapshot.stdin.write("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSELECT pg_export_snapshot();\n")
        snapshot.stdin.flush()
        snapshot_id = snapshot.stdout.readline().strip()
        if not re.fullmatch(r"[0-9A-Fa-f-]+", snapshot_id):
            raise RuntimeError("Nie udało się otworzyć spójnego snapshotu bazy.")
        snapshot.stdin.write(fingerprint_query() + ";\n")
        snapshot.stdin.flush()
        expected = json.loads(snapshot.stdout.readline())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as file:
            compose("exec", "-T", "db", "pg_dump", "-U", "monitor_admin", "-d", "monitor",
                    "--format=custom", "--no-owner", "--snapshot=" + snapshot_id, stdout=file)
        manifest = {
            "format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "source_database": "monitor", "snapshot_id": snapshot_id,
            "dump_sha256": file_sha256(path), "dump_bytes": path.stat().st_size,
            "fingerprint": expected,
        }
        manifest_path = path.with_suffix(".manifest.json")
        fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(manifest, file, ensure_ascii=False, indent=2)
            file.write("\n")
    except Exception:
        if path.exists():
            path.rename(path.with_suffix(".incomplete"))
        raise
    finally:
        try:
            snapshot.stdin.write("ROLLBACK;\n\\q\n")
            snapshot.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        snapshot.stdin.close()
        try:
            snapshot.wait(timeout=10)
        except subprocess.TimeoutExpired:
            snapshot.terminate()
            snapshot.wait(timeout=5)
        snapshot.stdout.close()
        snapshot.stderr.close()
    print(json.dumps({"backup": str(path), "manifest": str(manifest_path),
                      "bytes": path.stat().st_size, "consistent_snapshot": True}, ensure_ascii=False))
    return path


def restore_check(path: Path):
    """Validate an own backup in a new database, then remove only that test DB."""
    manifest_path = path.with_suffix(".manifest.json")
    if not path.is_file() or path.suffix != ".dump" or not manifest_path.is_file():
        raise SystemExit("Wskaż własny plik .dump wraz z .manifest.json z manage.py backup")
    manifest = json.loads(manifest_path.read_text())
    if file_sha256(path) != manifest.get("dump_sha256"):
        raise RuntimeError("Suma SHA-256 kopii nie zgadza się z manifestem. Nie rozpoczęto odtwarzania.")
    name = "monitor_restore_" + secrets.token_hex(6)
    compose("exec", "-T", "db", "createdb", "-U", "monitor_admin", name)
    try:
        with path.open("rb") as file:
            compose("exec", "-T", "db", "pg_restore", "-U", "monitor_admin", "-d", name,
                    "--no-owner", "--no-acl", "--exit-on-error", stdin=file, capture=True)
        actual = fingerprint(name)
        matches = actual == manifest.get("fingerprint")
        if not matches:
            raise RuntimeError("Odtworzona kopia różni się od snapshotu opisanego w manifeście.")
    finally:
        compose("exec", "-T", "db", "dropdb", "-U", "monitor_admin", name)
    print(json.dumps({
        "restore_matches_backup": True, "dump_sha256_verified": True,
        "table_counts": {table: value["count"] for table, value in actual.items()},
        "temporary_database_removed": name, "live_database_modified": False,
    }, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=(
        "init", "build", "db", "migrate", "seed", "up", "ingest", "status", "logs",
        "stop", "backup", "restore-check", "fingerprint", "check", "test", "detach-source",
    ))
    parser.add_argument("argument", nargs="?")
    parser.add_argument("--event-id")
    parser.add_argument("--source-id")
    parser.add_argument("--reason", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "init":
        path = ROOT / ".env"
        if path.exists():
            configuration()
            print("Istniejąca .env zachowana. Hasła nie są wypisywane.")
            return
        values = {key: secrets.token_hex(24) for key in (
            "DB_ADMIN_PASSWORD", "DB_WORKER_PASSWORD", "DB_READER_PASSWORD")}
        values["CLOUDFLARE_RADAR_TOKEN"] = ""
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write("".join(key + "=" + value + "\n" for key, value in values.items()))
        print("Utworzono prywatną .env (0600), losowe hasła i wyłączony Radar.")
        return
    configuration()
    command = args.command
    if command == "build":
        compose("build", *([args.argument] if args.argument else []))
    elif command == "db":
        compose("up", "-d", "--build", "--wait", "db")
    elif command == "migrate":
        compose("run", "--rm", "--no-deps", "--env", "DATABASE_URL", "api",
                "alembic", "upgrade", "head", extra_env={"DATABASE_URL": admin_url()})
    elif command == "seed":
        admin_module("seed")
    elif command == "up":
        compose("up", "-d", "--wait")
        print("Prywatny panel: http://localhost:3180")
    elif command == "ingest":
        compose("run", "--rm", "--no-deps", "worker",
                "python", "-m", "monitor.worker", "--once",
                *(["--source", args.argument] if args.argument else []))
    elif command == "status":
        compose("ps")
        admin_module("status")
    elif command == "logs":
        compose("logs", "--tail=60", *([args.argument] if args.argument else []))
    elif command == "stop":
        compose("stop")  # Never deletes the data volume.
    elif command == "backup":
        backup()
    elif command == "restore-check":
        if not args.argument:
            raise SystemExit("Podaj ścieżkę kopii .dump")
        restore_check(Path(args.argument).expanduser().resolve())
    elif command == "fingerprint":
        print(json.dumps(fingerprint(), ensure_ascii=False))
    elif command == "check":
        compose("config", "--quiet")  # Raw config would reveal passwords.
        admin_module("check")
    elif command == "detach-source":
        if not args.event_id or not args.source_id:
            raise SystemExit("Podaj --event-id i --source-id. Bez --apply polecenie wykonuje tylko podgląd.")
        if args.apply and not args.reason.strip():
            raise SystemExit("Zapis decyzji wymaga niepustego --reason.")
        if args.apply:
            backup()
        admin_module("detach-source", "--event-id", args.event_id, "--source-id", args.source_id,
                     "--reason", args.reason, *(["--apply"] if args.apply else []))
    elif command == "test":
        compose("run", "--rm", "--no-deps", "--env", "DATABASE_URL", "api",
                "python", "-m", "monitor.integration_check",
                extra_env={"DATABASE_URL": admin_url()})

if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        # Do not print commands/env: they can contain a database URL.
        print(f"Polecenie zakończyło się błędem (kod {exc.returncode}).", file=sys.stderr)
        sys.exit(exc.returncode)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
