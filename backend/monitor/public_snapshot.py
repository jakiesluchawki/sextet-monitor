"""Build a public snapshot from a NEW disposable database; never export an existing one."""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import NAMESPACE_URL, uuid5

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from . import __version__
from .api import EventDetail, SourceStatus
from .config import Settings
from .contracts import utcnow
from .db import event_detail, get_source_health, load_countries, seed_sources
from .ingestion import claim_source, expire_advisories
from .network import SafeHTTPClient
from .worker import run_source, transaction

PUBLIC_SOURCE_IDS = ("usgs", "meteoalarm", "cisa_kev")
MAX_EVENTS = 10_000
MAX_BYTES = 16 * 1024 * 1024
PUBLIC_LIMITATIONS = [
    "Publiczny zestaw pobrano niezależnie od prywatnego monitora. Nie zawiera jego bazy, pytań, briefingów, historii ani konfiguracji.",
    "To datowany odczyt źródeł, nie obraz na żywo. Statusy i liczby opisują przygotowanie zestawu; brak nowej publikacji zwiększa wiek danych.",
    "USGS: dostępne tygodniowe okno; MeteoAlarm: bieżący kanał Polski, do 200 dokumentów CAP; CISA: katalog, nie lista geolokalizowanych ataków.",
    "Zachowano źródłowe daty i geometrię. Brak czasu lub pozycji pozostaje nieznany; przypisania krajów wykorzystują uproszczone granice Natural Earth.",
    "Surowe payloady i historia zmian nie są publikowane. Pełne komunikaty są dostępne pod odnośnikami źródeł; pola zestawu są przetworzone.",
    "Nie publikuje się GDACS, EASA ani Radar. Brak wyniku nie potwierdza braku zagrożenia. Podgląd nie służy do decyzji operacyjnych o bezpieczeństwie.",
]


def validate_admin_url(value: str):
    if not value:
        raise ValueError("PUBLIC_BUILD_ADMIN_URL is required; no fallback to the private DATABASE_URL.")
    url = make_url(value)
    if url.drivername != "postgresql+psycopg" or url.database != "postgres" or url.query:
        raise ValueError("Public builds require an administrative connection to postgres, never an existing monitor database.")
    return url


def public_event_ids(events: list[dict]) -> dict[str, str]:
    result = {}
    for event in events:
        evidence = event.get("evidence", [])
        if not evidence or any(item.get("source_id") not in PUBLIC_SOURCE_IDS for item in evidence):
            raise ValueError("Public records require evidence from the explicit public-source allowlist.")
        key = json.dumps(sorted((item["source_id"], item["provider_record_id"]) for item in evidence),
                         ensure_ascii=True, separators=(",", ":"))
        result[str(event["id"])] = str(uuid5(NAMESPACE_URL, "mieszko-monitor/public/event/" + key))
    if len(set(result.values())) != len(events):
        raise ValueError("Public event identifiers must be unique.")
    return result


def sanitize_event(event: dict, public_ids: dict[str, str]) -> dict:
    source_ids = event.get("source_ids", [])
    if not source_ids or any(source not in PUBLIC_SOURCE_IDS for source in source_ids):
        raise ValueError("A non-public source cannot be exported.")
    public_id = public_ids[str(event["id"])]
    evidence = []
    for record in event["evidence"]:
        if record["source_id"] not in PUBLIC_SOURCE_IDS:
            raise ValueError("A non-public evidence record cannot be exported.")
        record_key = json.dumps([record["source_id"], record["provider_record_id"], record["payload_hash"]])
        evidence.append({
            **record, "id": str(uuid5(NAMESPACE_URL, "mieszko-monitor/public/evidence/" + record_key)),
            "raw": None, "raw_retained": False,
            "attribution": (
                "MeteoAlarm/EUMETNET; IMGW-PIB jako wystawca ostrzeżeń dla Polski; dane przetworzone, CC BY 4.0."
                if record["source_id"] == "meteoalarm" else record["attribution"]
            ),
        })
    relations = [{**edge, "event_id": public_ids[str(edge["event_id"])]}
                 for edge in event.get("relations", []) if str(edge["event_id"]) in public_ids]
    # Pydantic's explicit output model discards internal normal form / revision payloads.
    return EventDetail.model_validate({
        **event, "id": public_id, "evidence": evidence, "revisions": [], "relations": relations,
    }).model_dump(mode="json")


def encode_snapshot(events: list[dict], sources: list[dict], generated_at) -> bytes:
    if len(events) > MAX_EVENTS:
        raise ValueError("Snapshot exceeds the reviewed event limit; no partial artifact was emitted.")
    if {item["id"] for item in sources} != set(PUBLIC_SOURCE_IDS) or len(sources) != len(PUBLIC_SOURCE_IDS):
        raise ValueError("The public snapshot must report each approved source exactly once.")
    if any(item["status"] not in {"ok", "ok_empty", "partial", "stale"} for item in sources):
        raise ValueError("A public source failed. Keep the previous dated publication rather than publish an incomplete replacement.")
    ids = public_event_ids(events)
    public_sources = []
    for item in sources:
        source = SourceStatus.model_validate({
            **item, "next_due_at": None, "poll_interval_seconds": 3600,
            "error": "Niepełny odczyt przy przygotowaniu zestawu; pokrycie może być ograniczone." if item.get("error") else None,
            "attribution": (
                "MeteoAlarm/EUMETNET; IMGW-PIB; dane przetworzone, CC BY 4.0."
                if item["id"] == "meteoalarm" else item["attribution"]
            ),
        }).model_dump(mode="json")
        public_sources.append(source)
    payload = {
        "format": 1, "version": __version__, "generated_at": generated_at.isoformat(),
        "sources": public_sources, "events": [sanitize_event(event, ids) for event in events],
        "limitations": PUBLIC_LIMITATIONS,
    }
    encoded = (json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()
    if len(encoded) > MAX_BYTES:
        raise ValueError("Snapshot exceeds 16 MiB; no partial artifact was emitted.")
    return encoded


async def collect_public_sources(engine, settings):
    with engine.begin() as conn:
        seed_sources(conn, radar_enabled=False)
        conn.execute(text("UPDATE sources SET enabled=(id=ANY(:ids)),status=CASE WHEN id=ANY(:ids) THEN 'pending' ELSE 'disabled' END"),
                     {"ids": list(PUBLIC_SOURCE_IDS)})
        load_countries(conn, Path(settings.data_dir) / "countries.geojson")
    async with SafeHTTPClient() as fetcher:
        async def one(source):
            lease = await asyncio.to_thread(transaction, engine, claim_source, source, force=True)
            if lease is None:
                raise RuntimeError("Could not obtain a lease in the new public-build database.")
            return await run_source(engine, fetcher, settings, lease)
        await asyncio.gather(*(one(source) for source in PUBLIC_SOURCE_IDS))
    with engine.begin() as conn:
        expire_advisories(conn)


def build() -> bytes:
    """Own the entire DB lifecycle; no caller may name a database to export."""
    url = validate_admin_url(os.getenv("PUBLIC_BUILD_ADMIN_URL", ""))
    name = "monitor_public_" + secrets.token_hex(8)
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    engine = None
    created = False
    original_url = os.environ.get("DATABASE_URL")
    original_token = os.environ.get("CLOUDFLARE_RADAR_TOKEN")
    try:
        with admin.connect() as conn:
            if conn.execute(text("SELECT current_database()")).scalar_one() != "postgres":
                raise ValueError("Administrative connection did not reach the expected postgres database.")
            conn.execute(text('CREATE DATABASE "' + name + '"'))
        created = True
        public_url = url.set(database=name)
        engine = create_engine(public_url)
        with engine.connect() as conn:
            if conn.execute(text("SELECT current_database()")).scalar_one() != name:
                raise ValueError("Public-build connection did not reach its newly created database.")
        os.environ["DATABASE_URL"] = public_url.render_as_string(hide_password=False)
        os.environ.pop("CLOUDFLARE_RADAR_TOKEN", None)
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        command.upgrade(config, "head")
        settings = Settings(database_url=os.environ["DATABASE_URL"], radar_token="",
                            data_dir=os.getenv("MONITOR_DATA_DIR", "/app/data"))
        asyncio.run(collect_public_sources(engine, settings))
        with engine.connect() as conn:
            conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            now = utcnow()
            count = conn.execute(text("SELECT count(*) FROM events")).scalar_one()
            if count > MAX_EVENTS:
                raise ValueError("Too many public events; no artifact was emitted.")
            ids = conn.execute(text("SELECT id FROM events ORDER BY id")).scalars().all()
            events = [event_detail(conn, str(event_id), now=now) for event_id in ids]
            sources = [source for source in get_source_health(conn, now) if source["id"] in PUBLIC_SOURCE_IDS]
            return encode_snapshot(events, sources, now)
    finally:
        if original_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_url
        if original_token is not None:
            os.environ["CLOUDFLARE_RADAR_TOKEN"] = original_token
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as conn:
                conn.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        admin.dispose()


def main():
    try:
        # Worker status messages and migrations belong to diagnostics, not the JSON artifact.
        with redirect_stdout(sys.stderr):
            payload = build()
        sys.stdout.buffer.write(payload)
    except Exception as exc:
        # Never expose engine URLs, SQL parameters, secrets or provider payloads.
        print(f"Public snapshot build failed ({type(exc).__name__}); previous publication is unchanged.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
