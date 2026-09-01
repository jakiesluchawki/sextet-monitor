"""Atomic, idempotent ingestion with explicit provenance and fenced leases.

No similarity match merges records. Rule v1 uses provider IDs, original IDs and
same-sender CAP references only. Historical observations survive corrections.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import random
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .contracts import NormalizedEvent, ProviderBatch, utcnow
from .db import json_value
from .lifecycle import effective_event_state

RULE_VERSION = "identity-v1"
NORMALIZER_VERSION = "1"


class LeaseLost(RuntimeError):
    pass


class IdentityConflict(ValueError):
    pass


@dataclass(frozen=True)
class Lease:
    source_id: str
    owner: UUID
    cursor: dict
    first_fetch: bool
    poll_interval: int


def as_time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value if isinstance(value, datetime) and value.tzinfo is not None else None


def claim_source(conn, source_id=None, *, force=False, now=None, lease_seconds=900):
    now = now or utcnow()
    row = conn.execute(text("""
        SELECT * FROM sources WHERE enabled
        AND (lease_until IS NULL OR lease_until <= :now)
        AND (:force OR next_due_at <= :now)
        AND (CAST(:source AS text) IS NULL OR id=:source)
        ORDER BY next_due_at,id LIMIT 1 FOR UPDATE SKIP LOCKED
    """), {"now": now, "force": force, "source": source_id}).mappings().first()
    if not row:
        return None
    owner = uuid4()
    conn.execute(text("""
        UPDATE ingestion_runs SET status='lease_expired',finished_at=:now,
          error='Poprzedni worker nie zakończył próby przed końcem dzierżawy.'
        WHERE source_id=:source AND status='running'
    """), {"source": row["id"], "now": now})
    conn.execute(text("""
        UPDATE sources SET lease_owner=:owner,lease_until=:until,last_attempt_at=:now
        WHERE id=:source
    """), {"owner": owner, "until": now + timedelta(seconds=lease_seconds),
           "now": now, "source": row["id"]})
    conn.execute(text("""
        INSERT INTO ingestion_runs(id,source_id,started_at,status)
        VALUES(:owner,:source,:now,'running')
    """), {"owner": owner, "source": row["id"], "now": now})
    return Lease(row["id"], owner, row["cursor"], row["last_success_at"] is None,
                 row["spec"]["poll_interval_seconds"])


def check_lease(conn, lease, now):
    row = conn.execute(text("SELECT * FROM sources WHERE id=:id FOR UPDATE"),
                       {"id": lease.source_id}).mappings().one()
    if row["lease_owner"] != lease.owner or row["lease_until"] is None or row["lease_until"] <= now:
        raise LeaseLost("Wynik spóźnionego workera odrzucony; dzierżawa już nie należy do niego.")
    return row


def independent_origins(normals: list[dict]) -> tuple[int, list[str]]:
    """Unknown lineage is never a second confirmation. Mirrors collapse."""
    known = set()
    for normal in normals:
        families = set()
        for origin in normal.get("origins") or []:
            origin = str(origin).strip().lower()
            if not origin or origin.startswith("unknown:"):
                continue
            if origin == "usgs" or origin.startswith("usgs:"):
                origin = "usgs"
            if origin in {"gwis", "firms", "nasa:firms"}:
                origin = "nasa:firms"
            families.add(origin)
        # A publisher listing several upstreams is not several observed confirmations.
        if len(families) == 1:
            known.update(families)
    return max(1, len(known)), sorted(known)


def _keys(event: NormalizedEvent):
    # GLIDE can cover a wider disaster than a provider record; retain it as metadata, not a merge key.
    result = {key for key in event.external_ids if not key.startswith("glide:")}
    result.add("record:" + event.source_id + ":" + event.provider_record_id)
    if event.source_id == "meteoalarm" and event.supersedes:
        sender = event.raw.get("sender")
        if not isinstance(sender, str) or not sender:
            raise IdentityConflict("CAP reference wymaga znanego nadawcy.")
        result.update("cap:" + sender + ":" + ref for ref in event.supersedes)
    elif event.supersedes:
        raise IdentityConflict("Nieobsługiwany typ referencji między rekordami.")
    if any(not key or len(key) > 1200 for key in result):
        raise IdentityConflict("Nieprawidłowy identyfikator pochodzenia.")
    return sorted(result)


def _source_rank(normal, raw=None):
    # An explicit current GDACS episode outranks a superseded episode.
    raw = raw or normal.get("provider_revision") or {}
    current = bool(raw.get("is_current")) if normal.get("source_id") == "gdacs" else True
    instant = as_time(normal.get("source_updated_at")) or as_time(normal.get("issued_at"))
    instant = instant or as_time(normal.get("occurred_start"))
    stamp = instant.timestamp() if instant else float("-inf")
    episode = str(raw.get("episode_id", "0"))
    return (current, stamp, int(episode) if episode.isdigit() else 0)


def _newer_normalizer(incoming, previous):
    """Normalizer generations are monotonic integers; unknown versions never override chronology."""
    try:
        return int(incoming) > int(previous)
    except (TypeError, ValueError):
        return False


def _geo(conn, normal):
    result = dict(normal)
    geometry = result.get("geometry")
    countries = set(result.get("countries") or [])
    tags = set(result.get("tags") or [])
    if geometry:
        valid = conn.execute(text("""
            SELECT ST_AsGeoJSON(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(:geom),4326)))
        """), {"geom": json_value(geometry)}).scalar_one()
        result["geometry"] = json.loads(valid) if valid else None
        if result["geometry"] and not countries and geometry.get("type") == "Point":
            codes = conn.execute(text("""
                SELECT iso2 FROM countries
                WHERE ST_Covers(geom,ST_SetSRID(ST_GeomFromGeoJSON(:geom),4326))
            """), {"geom": json_value(result["geometry"])}).scalars()
            countries.update(codes)
            if countries:
                tags.add("country_from_natural_earth")
    elif countries and result["location_precision"] in {"country", "area"}:
        polygon = conn.execute(text("""
            SELECT ST_AsGeoJSON(ST_Union(geom)) FROM countries WHERE iso2=ANY(:codes)
        """), {"codes": sorted(countries)}).scalar_one()
        if polygon:
            result["geometry"] = json.loads(polygon)
            result["location_precision"] = "country"
            tags.add("country_geometry_not_extent")
            if result["category"] == "aviation":
                tags.add("country_geometry_not_fir")
    result["countries"] = sorted(countries)
    result["tags"] = sorted(tags)
    result["identity_rule"] = RULE_VERSION
    result["normalizer_version"] = NORMALIZER_VERSION
    return result


def _write_event(conn, event_id, normal, now, first_seen, change_type, sources, independent):
    args = {**normal, "id": event_id, "now": now, "first": first_seen,
            "normal": json_value(normal), "geo": json_value(normal["geometry"]) if normal.get("geometry") else None,
            "sources": sources, "independent": independent, "change": change_type}
    for field in ("occurred_start", "occurred_end", "issued_at", "source_updated_at", "valid_from", "valid_to"):
        args[field] = as_time(args.get(field))
    conn.execute(text("""
        INSERT INTO events(id,kind,category,title,description,occurred_start,occurred_end,issued_at,
          source_updated_at,first_seen_at,last_seen_at,last_changed_at,valid_from,valid_to,
          lifecycle_status,verification_status,severity,location_precision,countries,geom,normal,
          source_ids,independent_source_count,change_type)
        VALUES(:id,:kind,:category,:title,:description,:occurred_start,:occurred_end,:issued_at,
          :source_updated_at,:first,:now,:now,:valid_from,:valid_to,:lifecycle_status,:verification_status,
          :severity,:location_precision,:countries,
          CASE WHEN CAST(:geo AS text) IS NULL THEN NULL ELSE ST_SetSRID(ST_GeomFromGeoJSON(:geo),4326) END,
          CAST(:normal AS jsonb),:sources,:independent,:change)
        ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,category=excluded.category,title=excluded.title,
          description=excluded.description,occurred_start=excluded.occurred_start,occurred_end=excluded.occurred_end,
          issued_at=excluded.issued_at,source_updated_at=excluded.source_updated_at,last_seen_at=excluded.last_seen_at,
          last_changed_at=excluded.last_changed_at,valid_from=excluded.valid_from,valid_to=excluded.valid_to,
          lifecycle_status=excluded.lifecycle_status,verification_status=excluded.verification_status,
          severity=excluded.severity,location_precision=excluded.location_precision,countries=excluded.countries,
          geom=excluded.geom,normal=excluded.normal,source_ids=excluded.source_ids,
          independent_source_count=excluded.independent_source_count,change_type=excluded.change_type
    """), args)


def _revision(conn, event_id, normal, now, change_type, summary):
    conn.execute(text("""
        INSERT INTO event_revisions(id,event_id,recorded_at,change_type,summary,snapshot)
        VALUES(:id,:event,:now,:change,:summary,CAST(:snapshot AS jsonb))
    """), {"id": uuid4(), "event": event_id, "now": now, "change": change_type,
           "summary": summary, "snapshot": json_value(normal)})


def _record(conn, event, now, first_fetch, *, known_only=False, source_snapshot_at=None):
    if event.source_updated_at and event.source_updated_at > now + timedelta(days=1):
        raise ValueError("Przyszła data aktualizacji źródła.")
    if event.source_id == "usgs" and event.occurred_start and event.occurred_start > now + timedelta(hours=1):
        raise ValueError("Przyszła data obserwacji sejsmicznej.")
    keys = _keys(event)
    existing = conn.execute(text("""
        SELECT p.*,o.normalized,o.raw,o.payload_hash,o.normalizer_version
        FROM provider_records p JOIN observations o ON o.id=p.latest_observation_id
        WHERE p.source_id=:source AND p.provider_record_id=:record
    """), {"source": event.source_id, "record": event.provider_record_id}).mappings().first()
    if known_only and not existing:
        # USGS can change the preferred ID. Only its explicit IDs can establish
        # that this conditional withdrawal concerns an event we already know.
        usgs_ids = [key for key in event.external_ids if key.startswith("usgs:")]
        matches = set(conn.execute(text(
            "SELECT event_id FROM event_external_ids WHERE external_id=ANY(:keys)"
        ), {"keys": usgs_ids}).scalars())
        if not matches:
            return None, False, False
    else:
        matches = set(conn.execute(text("SELECT event_id FROM event_external_ids WHERE external_id=ANY(:keys)"),
                                   {"keys": keys}).scalars())
    if existing:
        matches.add(existing["event_id"])
        if event.source_id == "meteoalarm":
            old_caps = {key for key in existing["normalized"].get("external_ids", []) if key.startswith("cap:")}
            new_caps = {key for key in event.external_ids if key.startswith("cap:")}
            if old_caps and new_caps and not old_caps.intersection(new_caps):
                raise IdentityConflict("CAP identifier ma innego nadawcę niż zapisany komunikat.")
    override = conn.execute(text("""
        SELECT event_id FROM identity_overrides WHERE source_id=:source AND provider_record_id=:record
    """), {"source": event.source_id, "record": event.provider_record_id}).scalar_one_or_none()
    if override:
        if existing and existing["event_id"] != override:
            raise IdentityConflict("Ręczna decyzja tożsamości jest niespójna z rekordem źródła.")
        matches = {override}
    if len(matches) > 1:
        raise IdentityConflict("Identyfikatory wskazują różne zdarzenia; wymagają ręcznej oceny.")
    event_id = next(iter(matches)) if matches else uuid4()
    is_new = not matches
    normal = event.model_dump(mode="json", exclude={"raw"})
    normal["normalizer_version"] = NORMALIZER_VERSION
    if event.source_id == "gdacs":
        normal["provider_revision"] = {key: event.raw.get(key) for key in ("is_current", "episode_id", "version")}
    digest = hashlib.sha256(json_value(event.raw).encode()).hexdigest()
    same_current_payload = bool(existing and digest == existing["payload_hash"])
    newer_normalizer = bool(existing and same_current_payload and
                            _newer_normalizer(NORMALIZER_VERSION, existing["normalizer_version"]))
    fresh_snapshot = False
    if event.source_id == "cisa_kev":
        previous_snapshot = existing["source_snapshot_at"] if existing else None
        if previous_snapshot and (source_snapshot_at is None or source_snapshot_at < previous_snapshot):
            raise IdentityConflict("CISA: starszy lub niedatowany snapshot nie może zastąpić nowszego materiału.")
        if previous_snapshot and source_snapshot_at == previous_snapshot and not same_current_payload:
            raise IdentityConflict("CISA: różna treść przy tej samej dacie snapshotu; zachowano poprzedni materiał.")
        fresh_snapshot = bool(source_snapshot_at and
                              (previous_snapshot is None or source_snapshot_at > previous_snapshot))
    observation_id = uuid4()
    if is_new:
        initial = _geo(conn, normal)
        count, origins = independent_origins([normal])
        initial["independent_origins"] = origins
        _write_event(conn, event_id, initial, now, now, "initial_import" if first_fetch else "new",
                     [event.source_id], count)
    inserted = conn.execute(text("""
        INSERT INTO observations(id,source_id,provider_record_id,payload_hash,normalizer_version,raw,normalized,
          retrieved_at,source_updated_at)
        VALUES(:id,:source,:record,:hash,:version,CAST(:raw AS jsonb),CAST(:normal AS jsonb),:now,:updated)
        ON CONFLICT(source_id,provider_record_id,payload_hash,normalizer_version) DO NOTHING RETURNING id
    """), {"id": observation_id, "source": event.source_id, "record": event.provider_record_id,
           "hash": digest, "version": NORMALIZER_VERSION, "raw": json_value(event.raw), "normal": json_value(normal),
           "now": now, "updated": event.source_updated_at}).scalar_one_or_none()
    if inserted is None:
        observation_id = conn.execute(text("""
            SELECT id FROM observations WHERE source_id=:source AND provider_record_id=:record AND payload_hash=:hash
              AND normalizer_version=:version
        """), {"source": event.source_id, "record": event.provider_record_id, "hash": digest, "version": NORMALIZER_VERSION}).scalar_one()
    conn.execute(text("""
        INSERT INTO event_evidence(event_id,observation_id) VALUES(:event,:observation) ON CONFLICT DO NOTHING
    """), {"event": event_id, "observation": observation_id})
    for key in keys:
        conn.execute(text("""
            INSERT INTO event_external_ids(external_id,event_id) VALUES(:key,:event) ON CONFLICT DO NOTHING
        """), {"key": key, "event": event_id})
    replace = (not existing or newer_normalizer or fresh_snapshot or
               _source_rank(normal, event.raw) >= _source_rank(existing["normalized"], existing["raw"]))
    # Only a newer authoritative snapshot or a newer normalizer of the CURRENT raw
    # can make a historical payload current again. An old feed is not a correction.
    if (existing and inserted is None and observation_id != existing["latest_observation_id"]
            and not (newer_normalizer or fresh_snapshot)):
        replace = False
    if not existing:
        conn.execute(text("""
            INSERT INTO provider_records(source_id,provider_record_id,event_id,latest_observation_id,last_seen_at,
              source_snapshot_at)
            VALUES(:source,:record,:event,:observation,:now,:snapshot)
        """), {"source": event.source_id, "record": event.provider_record_id, "event": event_id,
               "observation": observation_id, "now": now, "snapshot": source_snapshot_at})
    else:
        conn.execute(text("""
            UPDATE provider_records SET last_seen_at=:now,
              latest_observation_id=CASE WHEN :replace THEN :observation ELSE latest_observation_id END,
              source_snapshot_at=CASE WHEN :replace AND CAST(:snapshot AS timestamptz) IS NOT NULL
                THEN :snapshot ELSE source_snapshot_at END
            WHERE source_id=:source AND provider_record_id=:record
        """), {"source": event.source_id, "record": event.provider_record_id, "now": now,
               "replace": replace, "observation": observation_id, "snapshot": source_snapshot_at})
    conn.execute(text("UPDATE events SET last_seen_at=:now WHERE id=:id"), {"now": now, "id": event_id})
    return event_id, is_new, bool(inserted)


def _assert_cap_acyclic(normals):
    """Validate every CAP reference component, including a cycle beside a valid leaf."""
    cap_records = {normal["provider_record_id"]: normal for normal in normals
                   if normal.get("source_id") == "meteoalarm"}
    edges = {key: set(normal.get("supersedes") or []) & cap_records.keys()
             for key, normal in cap_records.items()}
    incoming = dict.fromkeys(edges, 0)
    for references in edges.values():
        for target in references:
            incoming[target] += 1
    ready = [key for key, count in incoming.items() if count == 0]
    visited = 0
    while ready:
        key = ready.pop()
        visited += 1
        for target in edges[key]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    if visited != len(edges):
        raise IdentityConflict("Cykl referencji CAP; odrzucono rekord zamykający cykl.")


def rebuild_event(conn, event_id, now, *, is_new=False, first_fetch=False, created_in_batch=False):
    previous = conn.execute(text("SELECT * FROM events WHERE id=:id FOR UPDATE"),
                            {"id": event_id}).mappings().one()
    rows = list(conn.execute(text("""
        SELECT o.id,o.normalized,o.raw,o.retrieved_at FROM provider_records p
        JOIN observations o ON o.id=p.latest_observation_id WHERE p.event_id=:id
    """), {"id": event_id}).mappings())
    if not rows:
        raise IdentityConflict("Brak aktualnego materiału źródłowego.")
    _assert_cap_acyclic([row["normalized"] for row in rows])
    # Prefer the original seismic source over a humanitarian republisher.
    priority = {"usgs": 0, "meteoalarm": 0, "easa_czib": 0, "cisa_kev": 0, "cloudflare_radar": 0, "gdacs": 1}
    superseded = {ref for row in rows if row["normalized"]["source_id"] == "meteoalarm"
                  for ref in row["normalized"].get("supersedes", [])}
    candidates = [row for row in rows if row["normalized"]["source_id"] != "meteoalarm"
                  or row["normalized"]["provider_record_id"] not in superseded]
    if not candidates:
        raise IdentityConflict("Cykl referencji CAP; aktualny stan wymaga oceny.")
    candidates.sort(key=lambda row: (
        -priority.get(row["normalized"]["source_id"], 2),
        *_source_rank(row["normalized"], row["raw"]),
        row["normalized"]["lifecycle_status"] == "withdrawn",
        row["retrieved_at"],
    ), reverse=True)
    normal = dict(candidates[0]["normalized"])
    normal["evidence_version_ids"] = sorted(str(row["id"]) for row in rows)
    if normal["lifecycle_status"] == "withdrawn" and normal.get("supersedes"):
        carried = False
        for field in ("geometry", "occurred_start", "occurred_end", "valid_from", "valid_to", "description"):
            if not normal.get(field) and previous["normal"].get(field):
                normal[field] = previous["normal"][field]
                carried = True
        if normal.get("geometry") and previous["normal"].get("geometry") == normal["geometry"]:
            normal["location_precision"] = previous["location_precision"]
        if carried:
            normal["tags"] = sorted(set(normal["tags"] + ["fields_carried_from_cap_reference"]))
    normal = _geo(conn, normal)
    has_override = conn.execute(text("SELECT EXISTS(SELECT 1 FROM identity_overrides WHERE event_id=:id)"),
                                {"id": event_id}).scalar_one()
    if has_override:
        normal["tags"] = sorted(set(normal["tags"] + ["manual_identity_override"]))
    normal = effective_event_state(normal, now)
    origins_count, origins = independent_origins([row["normalized"] for row in rows])
    normal["independent_origins"] = origins
    sources = sorted({row["normalized"]["source_id"] for row in rows})
    normal["source_urls"] = sorted({row["normalized"]["source_url"] for row in rows})
    changed = (is_new or normal != previous["normal"] or sources != previous["source_ids"]
               or origins_count != previous["independent_source_count"])
    if not changed:
        return False
    newly_recorded = is_new or created_in_batch
    change = "initial_import" if newly_recorded and first_fetch else "new" if newly_recorded else "updated"
    if not is_new and normal["lifecycle_status"] == "withdrawn" and previous["lifecycle_status"] != "withdrawn":
        change = "withdrawn"
    elif not is_new and normal["lifecycle_status"] == "expired" and previous["lifecycle_status"] != "expired":
        change = "expired"
    _write_event(conn, event_id, normal, now, previous["first_seen_at"], change, sources, origins_count)
    descriptions = {"initial_import": "Pierwszy import materiału; nie oznacza nowego incydentu.",
                    "new": "Nowy materiał w lokalnej bazie.",
                    "updated": "Korekta treści lub materiału dowodowego ze źródła.",
                    "withdrawn": "Źródło wycofało komunikat.",
                    "expired": "Upłynął zadeklarowany przez źródło termin ważności."}
    summary = descriptions[change]
    if change == "updated" and normal == effective_event_state(previous["normal"], now):
        summary = "Przeliczono stan effective/onset CAP z zegara; materiał źródłowy nie zmienił się."
    _revision(conn, event_id, normal, now, change, summary + " Reguła " + RULE_VERSION + ".")
    return True


def relate_events(conn, changed_ids, now):
    for event_id in changed_ids:
        # Relations are recalculated after a correction; they never merge records.
        conn.execute(text("DELETE FROM event_relations WHERE event_a=:id OR event_b=:id"), {"id": event_id})
        conn.execute(text("""
            INSERT INTO event_relations(event_a,event_b,relation_type,reason,distance_km,time_delta_hours,created_at)
            SELECT LEAST(a.id,b.id),GREATEST(a.id,b.id),
              CASE WHEN a.category='earthquake' AND b.category='earthquake'
                THEN 'possible_same_event' ELSE 'near_in_space_and_time' END,
              CASE WHEN a.category='earthquake' AND b.category='earthquake'
                THEN 'Reguła relation-v1: dwa raporty sejsmiczne do 25 km i 10 minut. Brak wspólnego pewnego ID; nie scalono ani nie dodano potwierdzenia.'
                ELSE 'Reguła relation-v1: różne kategorie do 100 km i 6 godzin. Zbieżność czasu i miejsca nie dowodzi przyczyny.' END,
              ST_Distance(a.geom::geography,b.geom::geography)/1000,
              abs(EXTRACT(EPOCH FROM(a.occurred_start-b.occurred_start)))/3600,:now
            FROM events a JOIN events b ON a.id<>b.id
            WHERE a.id=:id AND a.location_precision='point' AND b.location_precision='point'
              AND GeometryType(a.geom)='POINT' AND GeometryType(b.geom)='POINT'
              AND a.occurred_start IS NOT NULL AND b.occurred_start IS NOT NULL

              AND (a.normal->>'time_precision') IN ('second','minute')
              AND (b.normal->>'time_precision') IN ('second','minute')
              AND (
                (a.category='earthquake' AND b.category='earthquake'
                 AND abs(EXTRACT(EPOCH FROM(a.occurred_start-b.occurred_start)))<=600
                 AND ST_DWithin(a.geom::geography,b.geom::geography,25000))
                OR (a.category<>b.category
                 AND abs(EXTRACT(EPOCH FROM(a.occurred_start-b.occurred_start)))<=21600
                 AND ST_DWithin(a.geom::geography,b.geom::geography,100000))
              )
              ORDER BY abs(EXTRACT(EPOCH FROM(a.occurred_start-b.occurred_start))) LIMIT 20
            ON CONFLICT DO NOTHING
        """), {"id": event_id, "now": now})


def persist_batch(conn, lease: Lease, batch: ProviderBatch, *, now=None):
    # Serialize short identity writes across providers; never hold this during HTTP.
    conn.execute(text("SELECT pg_advisory_xact_lock(61704001)"))
    now = now or utcnow()
    row = check_lease(conn, lease, now)
    touched = set()
    created = set()
    changed_ids = set()
    rejected = batch.rejected_count
    warnings = list(batch.warnings)
    observations = 0
    accepted = []
    generated = as_time(batch.metadata.get("provider_timestamp"))
    snapshot_at = generated if lease.source_id == "cisa_kev" else None
    prior_snapshot = as_time(row["cursor"].get("latest_snapshot_at")) if lease.source_id == "cisa_kev" else None
    invalid_snapshot = bool(lease.source_id == "cisa_kev" and (
        snapshot_at is None or snapshot_at > now + timedelta(minutes=5) or
        (prior_snapshot and snapshot_at < prior_snapshot)
    ))
    records = [(event, False) for event in batch.events]
    if lease.source_id == "usgs":
        records.extend((event, True) for event in batch.metadata.get("reclassifications", []))
    if invalid_snapshot:
        # An undated first catalog may still be retained as explicitly partial;
        # once a dated catalog was accepted, an older/missing clock cannot roll it back.
        warnings.append("CISA: brak poprawnej daty snapshotu, data przyszła lub starsza od ostatnio przyjętej; wynik częściowy.")
        if prior_snapshot or snapshot_at is not None:
            rejected += len(records)
            records = []
        snapshot_at = None
    for item, known_only in records:
        try:
            event = NormalizedEvent.model_validate(item) if known_only else item
            if event.source_id != lease.source_id:
                raise ValueError("Adapter zwrócił inny identyfikator źródła.")
            if known_only and (event.lifecycle_status != "withdrawn" or "usgs_reclassification" not in event.tags):
                raise ValueError("Nieprawidłowy sygnał przeklasyfikowania USGS.")
            with conn.begin_nested():
                event_id, is_new, inserted = _record(
                    conn, event, now, lease.first_fetch, known_only=known_only,
                    source_snapshot_at=snapshot_at,
                )
                if event_id is None:
                    continue
                changed = rebuild_event(
                    conn, event_id, now, is_new=is_new, first_fetch=lease.first_fetch,
                    created_in_batch=event_id in created,
                )
            # A bad CAP edge/geometry rolls back this record AND its rebuild only.
            # Correct independent records from the batch can still be committed.
            touched.add(event_id)
            if is_new:
                created.add(event_id)
            if changed:
                changed_ids.add(event_id)
            observations += int(inserted)
            accepted.append(event)
        except (IdentityConflict, ValueError, DBAPIError) as exc:
            rejected += 1
            if len(warnings) < 20:
                warnings.append(f"Zapis: rekord odrzucony ({type(exc).__name__}); pozostałe dane zachowano.")
    relate_events(conn, sorted(changed_ids, key=str), now)
    partial = bool(rejected or warnings or batch.metadata.get("partial") or batch.metadata.get("truncated"))
    stale_feed = bool(lease.source_id == "usgs" and generated and now - generated > timedelta(minutes=20))
    if lease.source_id == "usgs" and generated and generated > now + timedelta(minutes=5):
        partial = True
        warnings.append("USGS: czas wygenerowania feedu jest w przyszłości; sprawdź zegary źródła i hosta.")
    # A syntactically valid empty feed is not an error; malformed/partial is not empty success.
    status = "partial" if partial else "stale" if stale_feed else "ok" if accepted else "ok_empty"
    cursor = dict(row["cursor"])
    if snapshot_at and (accepted or not partial):
        # This is an accepted provenance high-water mark, not a complete-fetch cursor.
        # Per-record clocks allow the same partial snapshot to be retried safely.
        cursor["latest_snapshot_at"] = max(snapshot_at, prior_snapshot or snapshot_at).isoformat()
    if not partial and not stale_feed:
        cursor.update(initialized=True, last_complete_at=now.isoformat())
        if batch.metadata.get("repair_window") == "week":
            cursor["last_repair_at"] = now.isoformat()
    newest = max((t for event in accepted for t in (
        event.source_updated_at, event.issued_at, event.occurred_start
    ) if t is not None and t <= now), default=None)
    record_count = len({event.provider_record_id for event in accepted})
    retry_after = batch.metadata.get("retry_after_seconds", 0)
    retry_after = min(86400, max(0, retry_after)) if isinstance(retry_after, int) else 0
    next_delay = max(lease.poll_interval, retry_after)
    message = " · ".join(warnings)[:3500] if partial else None
    if stale_feed:
        message = "USGS: feed wygenerowano ponad 20 minut temu; poprawny HTTP nie oznacza aktualnej treści."
    if partial and not message:
        message = "Odczyt częściowy; kursor nie został przesunięty."
    conn.execute(text("""
        UPDATE sources SET status=:status,last_success_at=CASE WHEN :has_data OR NOT :partial THEN :now
          ELSE last_success_at END,newest_content_at=GREATEST(:newest,newest_content_at),
          record_count=CASE WHEN :has_data OR NOT :partial THEN :count ELSE record_count END,
          cursor=CAST(:cursor AS jsonb),next_due_at=:next,error=:error,
          failures=CASE WHEN :partial THEN failures ELSE 0 END,lease_owner=NULL,lease_until=NULL
        WHERE id=:source
    """), {"source": lease.source_id, "status": status, "now": now, "newest": newest,
           "has_data": bool(touched), "partial": partial, "count": record_count, "cursor": json_value(cursor),
           "next": now + timedelta(seconds=next_delay), "error": message})
    conn.execute(text("""
        UPDATE ingestion_runs SET finished_at=:now,status=:status,record_count=:count,
          rejected_count=:rejected,changed_count=:changed,error=:error WHERE id=:id
    """), {"id": lease.owner, "now": now, "status": status, "count": record_count, "rejected": rejected,
           "changed": len(changed_ids), "error": message})
    return {"source": lease.source_id, "status": status, "records": record_count,
            "new_observations": observations, "changed_events": len(changed_ids), "rejected": rejected}


def fail_source(conn, lease, error, *, now=None, retry_after=None, needs_credentials=False):
    now = now or utcnow()
    row = check_lease(conn, lease, now)
    failures = row["failures"] + 1
    delay = min(21600, lease.poll_interval * 2 ** min(failures - 1, 6))
    delay = max(delay, min(86400, retry_after or 0)) + random.randint(0, 20)
    status = "needs_credentials" if needs_credentials else "error"
    conn.execute(text("""
        UPDATE sources SET status=:status,error=:error,failures=:failures,next_due_at=:next,
          lease_owner=NULL,lease_until=NULL WHERE id=:source
    """), {"source": lease.source_id, "status": status, "error": str(error)[:1500],
           "failures": failures, "next": now + timedelta(seconds=delay)})
    conn.execute(text("""
        UPDATE ingestion_runs SET finished_at=:now,status=:status,error=:error WHERE id=:id
    """), {"id": lease.owner, "now": now, "status": status, "error": str(error)[:1500]})
    return {"source": lease.source_id, "status": status, "retry_seconds": delay}


def expire_advisories(conn, now=None):
    """Apply clock-driven expiry/activation without pretending another source read occurred."""
    conn.execute(text("SELECT pg_advisory_xact_lock(61704001)"))
    now = now or utcnow()
    rows = list(conn.execute(text("""
        SELECT * FROM events WHERE
          (lifecycle_status IN ('active','unknown') AND valid_to IS NOT NULL AND valid_to <= :now)
          OR (normal->>'source_id'='meteoalarm' AND
              ((lifecycle_status='unknown' AND valid_from <= :now AND valid_to > :now)
               OR (normal->'tags' ? 'hazard_onset_in_future' AND occurred_start <= :now)))
          OR (normal->>'source_id'='noaa_swpc' AND normal->>'kind'='advisory'
              AND lifecycle_status='unknown' AND valid_from <= :now AND valid_to > :now)
        FOR UPDATE
    """), {"now": now}).mappings())
    changed = 0
    for row in rows:
        normal = effective_event_state(row["normal"], now)
        if normal == row["normal"]:
            continue
        change = "expired" if normal["lifecycle_status"] == "expired" and row["lifecycle_status"] != "expired" else "updated"
        _write_event(conn, row["id"], normal, now, row["first_seen_at"], change,
                     row["source_ids"], row["independent_source_count"])
        conn.execute(text("UPDATE events SET last_seen_at=:seen WHERE id=:id"),
                     {"seen": row["last_seen_at"], "id": row["id"]})
        summary = ("Upłynął termin valid_to zadeklarowany przez źródło; to nie nowy odczyt."
                   if change == "expired" else
                   "Nadszedł zapisany termin ważności lub onset; przeliczono stan z zegara, bez nowego odczytu.")
        _revision(conn, row["id"], normal, now, change, summary)
        changed += 1
    return changed


def apply_retention(conn, now=None):
    conn.execute(text("SELECT pg_advisory_xact_lock(61704001)"))
    now = now or utcnow()
    raw_cutoff, event_cutoff = now - timedelta(days=30), now - timedelta(days=180)
    raw_count = conn.execute(text("UPDATE observations SET raw=NULL WHERE raw IS NOT NULL AND retrieved_at<:cutoff"),
                             {"cutoff": raw_cutoff}).rowcount
    conn.execute(text("DELETE FROM ingestion_runs WHERE finished_at<:cutoff"), {"cutoff": raw_cutoff})
    conn.execute(text("""
        DELETE FROM event_revisions r WHERE r.recorded_at<:cutoff AND EXISTS(
          SELECT 1 FROM event_revisions newer WHERE newer.event_id=r.event_id AND newer.recorded_at>r.recorded_at)
    """), {"cutoff": event_cutoff})
    deleted = conn.execute(text("""
        DELETE FROM events WHERE last_seen_at<:cutoff AND
        (lifecycle_status IN ('expired','withdrawn')
         OR (kind='incident' AND occurred_start<:cutoff AND
           (category='earthquake' OR (lifecycle_status<>'active' AND occurred_end<:cutoff))))
    """), {"cutoff": event_cutoff}).rowcount
    conn.execute(text("""
        DELETE FROM observations o WHERE o.retrieved_at<:cutoff
        AND NOT EXISTS(SELECT 1 FROM event_evidence ee WHERE ee.observation_id=o.id)
        AND NOT EXISTS(SELECT 1 FROM provider_records p WHERE p.latest_observation_id=o.id)
    """), {"cutoff": event_cutoff})
    return {"raw_payloads_expired": raw_count, "old_events_removed": deleted}


def release_interrupted_lease(conn, lease, now=None):
    """Release only this worker's lease without turning an intentional stop into a source failure."""
    now = now or utcnow()
    released = conn.execute(text("""
        UPDATE sources SET lease_owner=NULL,lease_until=NULL,next_due_at=:now
        WHERE id=:source AND lease_owner=:owner RETURNING id
    """), {"source": lease.source_id, "owner": lease.owner, "now": now}).scalar_one_or_none()
    if released:
        conn.execute(text("""
            UPDATE ingestion_runs SET finished_at=:now,status='interrupted',
              error='Przerwano odczyt przy zatrzymaniu workera; ostatnie dane zachowano.'
            WHERE id=:owner AND status='running'
        """), {"owner": lease.owner, "now": now})
    return bool(released)
