from __future__ import annotations

import json
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, text

from monitor.contracts import EventQuery, utcnow
from monitor.lifecycle import effective_event_state

BRIEFING_BATCH_SIZE = 250

SEVERITY_LABELS = {0: "nieokreślona", 1: "niska", 2: "umiarkowana", 3: "wysoka", 4: "krytyczna"}


@lru_cache(maxsize=4)
def get_engine(url: str):
    return create_engine(url, pool_pre_ping=True, pool_size=4, max_overflow=2,
                         connect_args={"connect_timeout": 10})


def json_value(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)


def seed_sources(conn, radar_enabled: bool = False):
    from monitor.providers import SOURCES
    for source in SOURCES.values():
        enabled = not source.requires_key or radar_enabled
        status = "pending" if enabled else "needs_credentials"
        conn.execute(text("""
            INSERT INTO sources(id,spec,enabled,status) VALUES(:id,CAST(:spec AS jsonb),:enabled,:status)
            ON CONFLICT(id) DO UPDATE SET spec=excluded.spec,enabled=excluded.enabled,
            status=CASE WHEN NOT excluded.enabled THEN 'needs_credentials'
                        WHEN sources.status IN ('disabled','needs_credentials') THEN 'pending'
                        ELSE sources.status END
        """), {"id": source.id, "spec": json_value(source.model_dump()), "enabled": enabled, "status": status})


def load_countries(conn, path: Path):
    if conn.execute(text("SELECT count(*) FROM countries")).scalar_one():
        return
    features = json.loads(path.read_text())["features"]
    for feature in features:
        properties = feature["properties"]
        code = properties.get("ISO_A2_EH") or properties.get("ISO_A2")
        if not isinstance(code, str) or len(code) != 2 or not code.isalpha():
            continue
        conn.execute(text("""
            INSERT INTO countries(iso2,name,geom)
            VALUES(:code,:name,ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(
              ST_GeomFromGeoJSON(:geom),4326)),3)))
            ON CONFLICT(iso2) DO NOTHING
        """), {"code": code.upper(), "name": properties.get("NAME_EN") or properties.get("NAME"),
               "geom": json_value(feature["geometry"])})


def get_source_health(conn, now=None):
    now = now or utcnow()
    items = []
    for row in conn.execute(text("SELECT * FROM sources ORDER BY id")).mappings():
        spec = row["spec"]
        status = row["status"]
        if row["enabled"] and status in ("ok", "ok_empty") and row["last_success_at"]:
            if now - row["last_success_at"] > timedelta(seconds=spec["poll_interval_seconds"] * 3 + 120):
                status = "stale"
        items.append({
            **spec, "enabled": row["enabled"], "status": status,
            "last_attempt_at": row["last_attempt_at"], "last_success_at": row["last_success_at"],
            "newest_content_at": row["newest_content_at"], "next_due_at": row["next_due_at"],
            "record_count": row["record_count"], "error": row["error"],
        })
    return items


def summarize(row, now=None):
    now = now or utcnow()
    result = effective_event_state({
        **row["normal"], "lifecycle_status": row["lifecycle_status"],
        "valid_from": row["valid_from"], "valid_to": row["valid_to"],
        "occurred_start": row["occurred_start"],
    }, now)
    status = result["lifecycle_status"]
    result.update({
        "id": str(row["id"]), "kind": row["kind"], "category": row["category"],
        "title": row["title"], "description": row["description"],
        "occurred_start": row["occurred_start"], "occurred_end": row["occurred_end"],
        "issued_at": row["issued_at"], "source_updated_at": row["source_updated_at"],
        "first_seen_at": row["first_seen_at"], "last_seen_at": row["last_seen_at"],
        "last_changed_at": row["last_changed_at"], "valid_from": row["valid_from"],
        "valid_to": row["valid_to"], "lifecycle_status": status,
        "verification_status": row["verification_status"], "severity": row["severity"],
        "severity_label": SEVERITY_LABELS[row["severity"]], "countries": row["countries"],
        "geometry": json.loads(row["geojson"]) if row.get("geojson") else None,
        "location_precision": row["location_precision"], "source_ids": row["source_ids"],
        "source_count": len(row["source_ids"]),
        "independent_source_count": row["independent_source_count"], "anomaly_score": None,
        "change_type": row["change_type"],
    })
    return result


def _select_events(conn, query: EventQuery, now=None, *, briefing=False, stream=False):
    now = now or utcnow()
    until = query.until or now
    since = query.since or until - timedelta(hours=query.window_hours)
    clock = {"changed": "e.last_changed_at", "published": "e.issued_at", "validity": "e.valid_from"}.get(
        query.time_basis, "e.occurred_start"
    )
    limitations = []
    if query.time_basis == "published":
        # A date-only UTC anchor represents a whole publication day, not midnight.
        day_start = "(date_trunc('day',e.issued_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')"
        time_condition = f"""(e.issued_at IS NOT NULL AND CASE
          WHEN COALESCE(e.normal->'tags' ? 'date_only_utc_anchor',false)
          THEN {day_start} < :until AND {day_start} + interval '24 hours' > :since
          ELSE e.issued_at >= :since AND e.issued_at < :until END)"""
        limitations.append("Filtr publikacji dotyczy issued_at, nie czasu incydentu lub pobrania. Data dzienna jest dopasowana przez przecięcie dnia z oknem; jej dokładna godzina pozostaje nieznana.")
    elif query.time_basis == "validity":
        time_condition = """(e.valid_from IS NOT NULL AND e.valid_from < :until
          AND (e.valid_to IS NULL OR (e.valid_to > :since AND e.valid_to > e.valid_from)))"""
        limitations.append("Przedział ważności zadeklarowany przez źródło przecina okno. Status jest bieżący; to nie odtworzony stan historyczny. Brak końca ważności pozostaje nieznany, a brak początku wyklucza dopasowanie.")
    else:
        time_condition = f"({clock} >= :since AND {clock} < :until)"
    conditions = [time_condition, "e.severity >= :severity", "e.independent_source_count >= :min_sources"]
    args = {"since": since, "until": until, "now": now, "severity": query.severity_min,
            "min_sources": query.min_sources, "limit": query.limit}
    if not query.include_inactive:
        conditions += ["e.lifecycle_status NOT IN ('expired','withdrawn')",
                       "(e.valid_to IS NULL OR e.valid_to > :now)"]
    if query.category:
        conditions.append("e.category=:category")
        args["category"] = query.category
    if query.country:
        conditions.append(":country = ANY(e.countries)")
        args["country"] = query.country
    if query.region == "europe":
        europe = "AL AD AT BE BA BG BY CH CY CZ DE DK EE ES FI FR GB GR HR HU IE IS IT LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS SE SI SK SM UA VA XK".split()
        conditions.append("""(e.countries && CAST(:europe AS text[]) OR
          (e.countries && ARRAY['RU','TR'] AND e.location_precision IN ('point','area')
           AND ST_Intersects(e.geom,ST_MakeEnvelope(-25,34,45,72,4326))))""")
        args["europe"] = europe
        limitations.append("Europa: zapisany zestaw krajów europejskich; Rosja i Turcja tylko z geometrią źródłową przecinającą 25°W–45°E, 34–72°N. To jawny filtr analityczny, nie definicja granic kontynentu. Rekordy bez przypisania geograficznego są pomijane.")
    if query.radius_km is not None:
        conditions += ["e.geom IS NOT NULL", "e.location_precision IN ('point','area')",
                       "NOT (GeometryType(e.geom)='POINT' AND e.location_precision='area')",
                       """ST_DWithin(e.geom::geography,
                        ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography,:radius)"""]
        args.update(lon=query.lon, lat=query.lat, radius=query.radius_km * 1000)
        limitations.append("Promień obejmuje dokładne punkty i obszary źródłowe; pomija lokalizacje krajowe i punkty reprezentatywne.")
    where = " AND ".join(conditions)
    background_count = 0
    if briefing:
        background = """(e.change_type='initial_import' AND NOT COALESCE(
          (e.issued_at >= :since AND e.issued_at < :until) OR
          (e.normal->'tags' ? 'date_only_utc_anchor' AND e.issued_at < :until
           AND e.issued_at + interval '24 hours' > :since) OR
          (e.kind IN ('incident','measurement') AND (
            (e.occurred_start >= :since AND e.occurred_start < :until) OR
            (e.normal->>'time_precision'='day' AND e.occurred_start < :until
             AND e.occurred_start + interval '24 hours' > :since))),false))"""
        background_count = conn.execute(text(f"SELECT count(*) FROM events e WHERE {where} AND {background}"), args).scalar_one()
        where += " AND NOT " + background
    total = conn.execute(text(f"SELECT count(*) FROM events e WHERE {where}"), args).scalar_one()
    select_sql = f"""SELECT e.*,ST_AsGeoJSON(e.geom) AS geojson FROM events e WHERE {where}
        ORDER BY e.severity DESC,{clock} DESC,e.id"""
    normalized_query = query.model_dump(mode="json")
    normalized_query.update(since=since.isoformat(), until=until.isoformat())
    metadata = {
        "total": total, "query": normalized_query, "source_health": get_source_health(conn, now),
        "generated_at": now, "limitations": limitations, "initial_import_background_count": background_count,
    }
    if stream:
        if not briefing:
            raise ValueError("Unbounded streaming is reserved for transactional briefings")

        def items_in_batches():
            # Statement-level options must not leak a server cursor into the later INSERT.
            statement = text(select_sql).execution_options(
                stream_results=True, yield_per=BRIEFING_BATCH_SIZE, max_row_buffer=BRIEFING_BATCH_SIZE,
            )
            with conn.execute(statement, args) as cursor:
                for batch in cursor.mappings().partitions(BRIEFING_BATCH_SIZE):
                    for row in batch:
                        yield summarize(row, now)

        return {**metadata, "items": items_in_batches(), "shown": total, "truncated": False}
    rows = conn.execute(text(select_sql + " LIMIT :limit"), args).mappings()
    items = [summarize(row, now) for row in rows]
    mapped = sum(item["geometry"] is not None for item in items)
    return {**metadata, "items": items, "shown": len(items), "mapped": mapped,
            "unlocated": len(items) - mapped, "truncated": total > len(items)}


def relations_for_ids(conn, event_ids: list[str]) -> dict[str, list[dict]]:
    """Load edges only between cited records, using the caller's existing snapshot."""
    ids = sorted(set(event_ids))
    result = {event_id: [] for event_id in ids}
    if len(ids) < 2:
        return result
    rows = conn.execute(text("""
        SELECT rel.*,a.title AS title_a,b.title AS title_b
        FROM event_relations rel JOIN events a ON a.id=rel.event_a JOIN events b ON b.id=rel.event_b
        WHERE rel.event_a=ANY(CAST(:ids AS uuid[])) AND rel.event_b=ANY(CAST(:ids AS uuid[]))
        ORDER BY rel.event_a,rel.event_b,rel.relation_type
    """), {"ids": ids}).mappings()
    for row in rows:
        event_a, event_b = str(row["event_a"]), str(row["event_b"])
        values = {key: row[key] for key in ("relation_type", "reason", "distance_km", "time_delta_hours")}
        result[event_a].append({**values, "event_id": event_b, "title": row["title_b"]})
        result[event_b].append({**values, "event_id": event_a, "title": row["title_a"]})
    return result


def event_detail(conn, event_id: str, now=None):
    row = conn.execute(text("SELECT *,ST_AsGeoJSON(geom) geojson FROM events WHERE id=:id"),
                       {"id": event_id}).mappings().first()
    if not row:
        return None
    event = summarize(row, now)
    evidence = conn.execute(text("""
        SELECT o.*,s.spec,p.source_snapshot_at
        FROM provider_records p JOIN observations o ON o.id=p.latest_observation_id
        JOIN sources s ON s.id=o.source_id WHERE p.event_id=:id
        ORDER BY o.source_id,o.provider_record_id
    """), {"id": event_id}).mappings()
    event["evidence"] = [{
        "id": str(o["id"]), "source_id": o["source_id"], "source_name": o["spec"]["name"],
        "provider_record_id": o["provider_record_id"], "source_url": o["normalized"]["source_url"],
        "retrieved_at": o["retrieved_at"], "issued_at": o["normalized"].get("issued_at"),
        "source_updated_at": o["source_updated_at"], "source_snapshot_at": o["source_snapshot_at"], "origins": o["normalized"].get("origins", []),
        "payload_hash": o["payload_hash"], "raw": o["raw"], "raw_retained": o["raw"] is not None,
        "attribution": o["spec"]["attribution"], "license_url": o["spec"]["license_url"],
    } for o in evidence]
    event["revisions"] = [dict(r) for r in conn.execute(text("""
        SELECT id,recorded_at,change_type,summary FROM event_revisions
        WHERE event_id=:id ORDER BY recorded_at DESC,id LIMIT 30
    """), {"id": event_id}).mappings()]
    event["relations"] = [dict(r) for r in conn.execute(text("""
        SELECT other.id AS event_id,other.title,rel.relation_type,rel.reason,
               rel.distance_km,rel.time_delta_hours
        FROM event_relations rel JOIN events other
          ON other.id=CASE WHEN rel.event_a=:id THEN rel.event_b ELSE rel.event_a END
        WHERE rel.event_a=:id OR rel.event_b=:id ORDER BY rel.created_at DESC LIMIT 30
    """), {"id": event_id}).mappings()]
    return event

def select_events(conn, query: EventQuery, now=None):
    return _select_events(conn, query, now)


def select_briefing_events(conn, query: EventQuery, *, first_briefing=False, now=None, stream=False):
    # Historical imports are counted separately so they cannot drown current warnings.
    return _select_events(conn, query, now, briefing=True, stream=stream)


def latest_briefing(conn, *, country=None, window_hours=None):
    condition = ""
    params = {}
    if window_hours is not None:
        condition = "WHERE scope=CAST(:scope AS jsonb)"
        params["scope"] = json_value({"country": country, "window_hours": window_hours})
    row = conn.execute(text(
        "SELECT result FROM briefing_runs " + condition + " ORDER BY created_at DESC,id DESC LIMIT 1"
    ), params).mappings().first()
    return row["result"] if row else None


def save_briefing(conn, briefing, *, country, window_hours):
    from uuid import uuid4
    result = dict(briefing)
    result["id"] = str(uuid4())
    conn.execute(text("""
        INSERT INTO briefing_runs(id,created_at,since_at,until_at,scope,result)
        VALUES(:id,:created,:since,:until,CAST(:scope AS jsonb),CAST(:result AS jsonb))
    """), {"id": result["id"], "created": result["generated_at"],
           "since": result["since"], "until": result["until"],
           "scope": json_value({"country": country, "window_hours": window_hours}),
           "result": json_value(result)})
    return result
