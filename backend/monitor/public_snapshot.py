"""Build a public snapshot from a NEW disposable database; never export an existing one."""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timedelta
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from alembic import command
from alembic.config import Config
import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from . import __version__
from .api import EventDetail, Evidence, Relation, SourceStatus
from .config import Settings
from .contracts import utcnow
from .db import event_detail, get_source_health, load_countries, seed_sources
from .ingestion import claim_source, expire_advisories
from .network import SafeHTTPClient, validate_addresses
from .worker import run_source, transaction

PUBLIC_SOURCE_IDS = (
    "usgs", "meteoalarm", "cisa_kev", "gdacs", "easa_czib",
    "nasa_eonet", "noaa_swpc", "github_status", "cloudflare_status",
)
MAX_EVENTS = 10_000
MAX_BYTES = 16 * 1024 * 1024
PUBLIC_LIMITATIONS = [
    "Publiczny zestaw pobrano niezależnie od prywatnego monitora. Nie zawiera jego bazy, pytań, briefingów, historii ani konfiguracji.",
    "To datowany odczyt źródeł, nie obraz na żywo. Statusy i liczby opisują przygotowanie zestawu; brak nowej publikacji zwiększa wiek danych.",
    "USGS: dostępne tygodniowe okno; MeteoAlarm: bieżący kanał Polski, do 200 dokumentów CAP; CISA: katalog, nie lista geolokalizowanych ataków.",
    "GDACS: katastrofy i automatyczne oceny, nie krajowe ostrzeżenia. EASA: biuletyny ryzyka lotniczego, nie pozycje lotów, NOTAM ani daty ataków.",
    "NASA EONET: do 400 wpisów o pożarach, wulkanach i burzach z okna 30 dni; osiągnięcie limitu oznacza niepełny odczyt. Przybliżone miejsce i czas, z zachowaniem pochodzenia danych.",
    "NOAA SWPC: obserwowane alerty i podsumowania oddzielono od prognoz i ostrzeżeń; brak mapy zakłóceń GPS. Stan prognozy dotyczy jej okresu ważności, nie potwierdzenia zjawiska.",
    "GitHub Status i Cloudflare Status: metadane ostatnich 50 incydentów każdego operatora, nie pełne archiwa ani pomiar całego Internetu. Pełne komunikaty pozostają w źródle.",
    "Zachowano źródłowe daty i geometrię. Brak czasu lub pozycji pozostaje nieznany; przypisania krajów wykorzystują uproszczone granice Natural Earth.",
    "Surowe payloady i historia zmian nie są publikowane. Pełne komunikaty są dostępne pod odnośnikami źródeł; pola zestawu są przetworzone.",
    "Przy awarii źródła można zachować jego poprzedni publiczny odczyt: jest oznaczony, ma oryginalne daty, a źródło pozostaje w stanie błędu. Brak rekordu na mapie nie oznacza braku danych na liście.",
    "Cloudflare Radar nie jest publikowany. Brak wyniku nie potwierdza braku zagrożenia. Podgląd nie służy do decyzji operacyjnych o bezpieczeństwie.",
]


def previous_snapshot_url(site_url: str) -> str:
    """Only an explicit public GitHub Pages origin; never localhost or a file."""
    parts = urlsplit(site_url)
    if (parts.scheme != "https" or parts.username or parts.password or parts.port is not None
            or parts.query or parts.fragment
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?\.github\.io", parts.hostname or "")
            or not re.fullmatch(r"/(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*/)?", parts.path)):
        raise ValueError("Previous publication must be an explicit public GitHub Pages site URL.")
    return site_url + "snapshot.json"


def _public_link(value: str) -> None:
    parts = urlsplit(value)
    host = parts.hostname or ""
    if (parts.scheme not in {"http", "https"} or parts.username or parts.password
            or parts.port not in {None, 80, 443} or "." not in host
            or host.endswith((".local", ".localhost", ".internal", ".invalid", ".test", ".lan"))):
        raise ValueError("Previous publication contains a non-public link.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("Previous publication contains a private address.")


def _strict_model(model, value):
    if not isinstance(value, dict) or set(value) - model.model_fields.keys():
        raise ValueError("Previous publication has unknown fields.")
    result = model.model_validate(value)
    if any(isinstance(item, datetime) and item.utcoffset() is None for item in result.__dict__.values()):
        raise ValueError("Previous publication contains an ambiguous timestamp.")
    return result


def _public_geometry(value, budget: list[int], depth: int = 0) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict) or depth > 8:
        return False
    if value.get("type") == "GeometryCollection":
        parts = value.get("geometries")
        return (set(value) == {"type", "geometries"} and isinstance(parts, list)
                and 0 < len(parts) <= 200
                and all(_public_geometry(part, budget, depth + 1) for part in parts))
    levels = {"Point": 0, "MultiPoint": 1, "LineString": 1, "MultiLineString": 2, "Polygon": 2, "MultiPolygon": 3}
    level = levels.get(value.get("type"))
    if set(value) != {"type", "coordinates"} or level is None:
        return False

    def coordinates(part, remaining):
        if remaining:
            return isinstance(part, list) and bool(part) and all(coordinates(item, remaining - 1) for item in part)
        budget[0] += 1
        return (budget[0] <= 1_000_000 and isinstance(part, list) and 2 <= len(part) <= 3
                and all(type(item) in {int, float} and math.isfinite(item) for item in part)
                and abs(part[0]) <= 180 and abs(part[1]) <= 90)

    return coordinates(value.get("coordinates"), level)


def validate_previous_snapshot(value: dict, now: datetime) -> dict:
    """Accept only the already-public contract, including reproducible public IDs."""
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > MAX_BYTES:
        raise ValueError("Previous publication exceeds its size limit.")
    if (not isinstance(value, dict)
            or set(value) != {"format", "version", "generated_at", "sources", "events", "limitations"}
            or type(value["format"]) is not int or value["format"] != 1
            or not isinstance(value["version"], str) or len(value["version"]) > 40
            or not isinstance(value["sources"], list) or not 1 <= len(value["sources"]) <= len(PUBLIC_SOURCE_IDS)
            or not isinstance(value["events"], list) or len(value["events"]) > MAX_EVENTS
            or not isinstance(value["limitations"], list) or len(value["limitations"]) > 30
            or any(not isinstance(item, str) or len(item) > 2000 for item in value["limitations"])):
        raise ValueError("Invalid previous public snapshot contract.")
    generated = datetime.fromisoformat(value["generated_at"].replace("Z", "+00:00"))
    if generated.utcoffset() is None or generated > now + timedelta(minutes=5):
        raise ValueError("Previous publication has an invalid clock.")
    sources, source_ids = [], set()
    for raw in value["sources"]:
        source = _strict_model(SourceStatus, raw)
        if source.id not in PUBLIC_SOURCE_IDS or source.id in source_ids or source.requires_key or not source.enabled:
            raise ValueError("Previous publication has unexpected sources.")
        _public_link(source.license_url)
        source_ids.add(source.id)
        sources.append(source.model_dump(mode="json"))
    ids = public_event_ids(value["events"])
    events, provider_keys, geometry_budget = [], set(), [0]
    for raw in value["events"]:
        event = _strict_model(EventDetail, raw)
        if (str(event.id) != ids[str(event.id)] or event.revisions
                or not 1 <= len(event.evidence) <= 20
                or len(set(event.source_ids)) != len(event.source_ids)
                or set(event.source_ids) != {item.source_id for item in event.evidence}
                or not set(event.source_ids) <= source_ids
                or event.source_count != len(event.source_ids)
                or event.independent_source_count > event.source_count
                or not _public_geometry(event.geometry, geometry_budget)):
            raise ValueError("Previous publication contains non-public event identity or history.")
        _public_link(event.source_url)
        for raw_evidence in raw["evidence"]:
            evidence = _strict_model(Evidence, raw_evidence)
            pair = (evidence.source_id, evidence.provider_record_id)
            key = json.dumps([evidence.source_id, evidence.provider_record_id, evidence.payload_hash])
            if (evidence.raw is not None or evidence.raw_retained or pair in provider_keys
                    or not 1 <= len(evidence.provider_record_id) <= 512
                    or not re.fullmatch(r"[a-f0-9]{64}", evidence.payload_hash)
                    or str(evidence.id) != str(uuid5(NAMESPACE_URL, "mieszko-monitor/public/evidence/" + key))):
                raise ValueError("Previous publication contains private or duplicate evidence.")
            _public_link(evidence.source_url)
            _public_link(evidence.license_url)
            provider_keys.add(pair)
        for raw_relation in raw["relations"]:
            relation = _strict_model(Relation, raw_relation)
            if str(relation.event_id) not in ids:
                raise ValueError("Previous publication has a non-public relation target.")
        events.append(event.model_dump(mode="json"))
    return {**value, "sources": sources, "events": events}


async def load_previous_snapshot(site_url: str, now: datetime) -> dict | None:
    """Bounded HTTPS read; failed/invalid cache never substitutes private local data."""
    if not site_url:
        return None
    try:
        url = previous_snapshot_url(site_url)
        async with asyncio.timeout(20):
            await asyncio.to_thread(validate_addresses, urlsplit(url).hostname)
            async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", url, headers={"Cache-Control": "no-cache"}) as response:
                    if response.status_code != 200:
                        raise ValueError("No previous publication")
                    if int(response.headers.get("content-length", "0")) > MAX_BYTES:
                        raise ValueError("Previous publication is too large")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ValueError("Previous publication is too large")
                        chunks.append(chunk)
        return validate_previous_snapshot(json.loads(b"".join(chunks)), now)
    except Exception:
        print("Previous public snapshot unavailable or invalid; using only new public reads.", file=sys.stderr)
        return None


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


def _retain_previous_reads(events: list[dict], sources: list[dict], previous: dict | None) -> list[dict]:
    failed = {source["id"] for source in sources if source["status"] == "error"}
    old_sources = {source["id"]: source for source in (previous or {}).get("sources", [])}
    known_ids = {event["id"] for event in events}
    known_pairs = {(item["source_id"], item["provider_record_id"])
                   for event in events for item in event["evidence"]}
    retained = []
    for event in (previous or {}).get("events", []):
        pairs = {(item["source_id"], item["provider_record_id"]) for item in event["evidence"]}
        # Mixed records are not projected onto another source: their title, clocks
        # and severity may have come from the now-successful member of that group.
        if (set(event["source_ids"]) <= failed and event["id"] not in known_ids and not pairs & known_pairs
                and all(old_sources[source].get("last_success_at") for source in event["source_ids"])):
            retained.append({**event, "tags": sorted(set(event["tags"]) | {"cached_public_data"})})
            known_ids.add(event["id"])
            known_pairs.update(pairs)
    merged = events + retained
    for source in sources:
        if source["id"] not in failed:
            continue
        old = old_sources.get(source["id"], {})
        count = len({item["provider_record_id"] for event in retained for item in event["evidence"]
                     if item["source_id"] == source["id"]})
        source.update(
            record_count=count, last_success_at=old.get("last_success_at"),
            newest_content_at=old.get("newest_content_at") if count else None,
            error=("Błąd odczytu. Zachowano poprzednie publiczne dane z oryginalnymi datami; nie są bieżącym odczytem."
                   if count else "Błąd odczytu. Brak danych tego źródła w bieżącym publicznym zestawie."),
        )
    for event in merged:
        event["relations"] = [relation for relation in event["relations"] if relation["event_id"] in known_ids]
    return merged


def encode_snapshot(events: list[dict], sources: list[dict], generated_at, previous: dict | None = None) -> bytes:
    if len(events) > MAX_EVENTS:
        raise ValueError("Snapshot exceeds the reviewed event limit; no partial artifact was emitted.")
    if {item["id"] for item in sources} != set(PUBLIC_SOURCE_IDS) or len(sources) != len(PUBLIC_SOURCE_IDS):
        raise ValueError("The public snapshot must report each approved source exactly once.")
    # In a fresh DB a partial batch with no accepted records has no usable read.
    # Do not let total CAP/parser failure replace a good publication with emptiness.
    sources = [{**item, "status": "error"}
               if item["status"] == "partial" and (not item.get("last_success_at") or not item.get("record_count"))
               else item for item in sources]
    successful_states = {"ok", "ok_empty", "partial", "stale"}
    if not any(item["status"] in successful_states for item in sources):
        raise ValueError("All public sources failed; keep the previous dated publication.")
    if any(not item["enabled"] or item["requires_key"] for item in sources):
        raise ValueError("Every public source must be enabled and require no credentials.")
    if previous is not None:
        previous = validate_previous_snapshot(previous, generated_at)
    ids = public_event_ids(events)
    public_sources = []
    for item in sources:
        source = SourceStatus.model_validate({
            **item, "next_due_at": None, "poll_interval_seconds": 3600,
            "status": item["status"] if item["status"] in successful_states else "error",
            "error": "Niepełny odczyt przy przygotowaniu zestawu; pokrycie może być ograniczone." if item.get("error") else None,
            "attribution": (
                "MeteoAlarm/EUMETNET; IMGW-PIB; dane przetworzone, CC BY 4.0."
                if item["id"] == "meteoalarm" else item["attribution"]
            ),
        }).model_dump(mode="json")
        public_sources.append(source)
    public_events = _retain_previous_reads([sanitize_event(event, ids) for event in events], public_sources, previous)
    if len(public_events) > MAX_EVENTS:
        raise ValueError("Snapshot exceeds the reviewed event limit; no partial artifact was emitted.")
    payload = {
        "format": 1, "version": __version__, "generated_at": generated_at.isoformat(),
        "sources": public_sources, "events": public_events,
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
    previous = asyncio.run(load_previous_snapshot(os.getenv("MONITOR_PUBLIC_SITE_URL", ""), utcnow()))
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
            return encode_snapshot(events, sources, now, previous)
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
