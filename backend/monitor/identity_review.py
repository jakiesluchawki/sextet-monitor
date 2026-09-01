"""Audited administrative reversal of an identity merge; never an HTTP write.

The caller owns the outer transaction. A savepoint makes a rejected split atomic,
and the same transaction advisory lock as ingestion fences identity changes.
Observation payloads and existing revisions are never edited or deleted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from .contracts import utcnow
from .ingestion import (
    IdentityConflict, _geo, _revision, _source_rank, _write_event,
    independent_origins, rebuild_event, relate_events,
)

OWNED_PREFIXES = {
    "usgs": "usgs:",
    "gdacs": "gdacs:",
    "meteoalarm": "cap:",
    "cisa_kev": "cve:",
    "easa_czib": "easa:czib:",
    "cloudflare_radar": "cloudflare:outage:",
}


@dataclass
class _SplitPlan:
    event: dict[str, Any]
    source_id: str
    records: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    external_ids: list[str]
    move_keys: list[str]
    overrides: list[dict[str, Any]]

    @property
    def moving(self) -> list[dict[str, Any]]:
        return [row for row in self.records if row["source_id"] == self.source_id]

    def public(self) -> dict[str, Any]:
        return {
            "can_split": True,
            "event_id": str(self.event["id"]),
            "title": self.event["title"],
            "source_id": self.source_id,
            "provider_record_ids": [row["provider_record_id"] for row in self.moving],
            "provider_record_count": len(self.moving),
            "observation_count": len(self.observations),
            "remaining_source_ids": sorted({
                row["source_id"] for row in self.records if row["source_id"] != self.source_id
            }),
            "move_external_ids": list(self.move_keys),
            "keep_external_ids": [key for key in self.external_ids if key not in self.move_keys],
            "existing_overrides": [{
                "source_id": row["source_id"], "provider_record_id": row["provider_record_id"],
                "event_id": str(row["event_id"]), "reason": row["reason"],
                "created_at": row["created_at"].isoformat(),
            } for row in self.overrides],
            "override_record_count": len(self.records),
            "limitations": [
                "Podgląd nie rezerwuje stanu bazy; zastosowanie ponownie sprawdza wszystkie przypisania.",
                "Decyzja obejmie obie strony rozdzielenia i wszystkie ich obecne rekordy źródłowe. "
                "Nie obejmuje nieznanych jeszcze identyfikatorów przyszłych rekordów.",
                "Obserwacje i wcześniejsze rewizje pozostaną zachowane. Obce wspólne identyfikatory nie są przenoszone.",
            ],
        }


def _arguments(event_id: UUID | str, source_id: str) -> UUID:
    try:
        identity = event_id if isinstance(event_id, UUID) else UUID(str(event_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Wymagany jest poprawny UUID zdarzenia.") from None
    if not isinstance(source_id, str) or source_id not in OWNED_PREFIXES:
        raise ValueError("Źródło nie ma zdefiniowanych reguł własności identyfikatorów.")
    return identity


def _owns_key(source_id: str, key: str) -> bool:
    return key.startswith("record:" + source_id + ":") or key.startswith(OWNED_PREFIXES[source_id])


def _load_plan(conn, event_id: UUID, source_id: str, *, lock: bool = False) -> _SplitPlan:
    row = conn.execute(text(
        "SELECT * FROM events WHERE id=:id" + (" FOR UPDATE" if lock else "")
    ), {"id": event_id}).mappings().first()
    if row is None:
        raise IdentityConflict("Nie znaleziono zdarzenia do rozdzielenia.")
    records = [dict(item) for item in conn.execute(text("""
        SELECT p.*,o.normalized,o.raw,o.retrieved_at,
               o.source_id AS evidence_source_id,o.provider_record_id AS evidence_record_id
        FROM provider_records p JOIN observations o ON o.id=p.latest_observation_id
        WHERE p.event_id=:id ORDER BY p.source_id,p.provider_record_id
    """ + (" FOR UPDATE OF p" if lock else "")), {"id": event_id}).mappings()]
    moving = [record for record in records if record["source_id"] == source_id]
    if not moving:
        raise IdentityConflict("Zdarzenie nie zawiera rekordów wskazanego źródła.")
    if len(moving) == len(records):
        raise IdentityConflict("Nie można odłączyć ostatniego źródła ani utworzyć pustego zdarzenia.")
    for record in records:
        if (
            record["evidence_source_id"] != record["source_id"]
            or record["evidence_record_id"] != record["provider_record_id"]
            or record["normalized"].get("source_id") != record["source_id"]
            or record["normalized"].get("provider_record_id") != record["provider_record_id"]
        ):
            raise IdentityConflict("Aktualna obserwacja jest niespójna z kluczem rekordu źródła.")

    observations = [dict(item) for item in conn.execute(text("""
        SELECT o.id,o.source_id,o.provider_record_id,o.retrieved_at,o.normalized
        FROM observations o JOIN provider_records p
          ON p.source_id=o.source_id AND p.provider_record_id=o.provider_record_id
        WHERE p.event_id=:id AND p.source_id=:source ORDER BY o.retrieved_at,o.id
    """), {"id": event_id, "source": source_id}).mappings()]
    observation_ids = {item["id"] for item in observations}
    links = list(conn.execute(text("""
        SELECT event_id,observation_id FROM event_evidence WHERE observation_id=ANY(:ids)
    """), {"ids": list(observation_ids)}).mappings())
    if (
        not observation_ids
        or any(link["event_id"] != event_id for link in links)
        or {link["observation_id"] for link in links} != observation_ids
        or not {record["latest_observation_id"] for record in moving}.issubset(observation_ids)
    ):
        raise IdentityConflict("Powiązania obserwacji wymagają osobnej oceny przed rozdzieleniem.")

    external_ids = list(conn.execute(text("""
        SELECT external_id FROM event_external_ids WHERE event_id=:id ORDER BY external_id
    """), {"id": event_id}).scalars())
    move_keys = [key for key in external_ids if _owns_key(source_id, key)]
    expected = {"record:" + source_id + ":" + record["provider_record_id"] for record in moving}
    expected.update(
        key for observation in observations for key in observation["normalized"].get("external_ids", [])
        if isinstance(key, str) and _owns_key(source_id, key)
    )
    assignments = dict(conn.execute(text("""
        SELECT external_id,event_id FROM event_external_ids WHERE external_id=ANY(:keys)
    """), {"keys": sorted(expected)}).tuples().all())
    if any(assignments.get(key) != event_id for key in expected):
        raise IdentityConflict("Twardy klucz źródła jest brakujący albo przypisany innemu zdarzeniu.")

    overrides = [dict(item) for item in conn.execute(text("""
        SELECT io.* FROM identity_overrides io JOIN provider_records p
          ON p.source_id=io.source_id AND p.provider_record_id=io.provider_record_id
        WHERE p.event_id=:id ORDER BY io.source_id,io.provider_record_id
    """ + (" FOR UPDATE OF io" if lock else "")), {"id": event_id}).mappings()]
    if any(override["event_id"] != event_id for override in overrides):
        raise IdentityConflict("Istniejąca ręczna decyzja jest sprzeczna z przypisaniem rekordu źródła.")
    return _SplitPlan(dict(row), source_id, records, observations, external_ids, move_keys, overrides)


def preview_split(conn, event_id: UUID | str, source_id: str) -> dict[str, Any]:
    """Read-only plan. Apply must revalidate it after acquiring the identity lock."""
    identity = _arguments(event_id, source_id)
    return _load_plan(conn, identity, source_id).public()


def split_source(
    conn, event_id: UUID | str, source_id: str, reason: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Detach all current records of one source, keeping evidence history intact.

    No commit, backup, HTTP call or account action is performed here. The admin CLI
    must make its backup and explicitly open a transaction before calling this.
    """
    identity = _arguments(event_id, source_id)
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        raise ValueError("Powód rozdzielenia jest wymagany i może mieć najwyżej 500 znaków.")
    reason = reason.strip()
    if now is not None and (
        not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None
    ):
        raise ValueError("Czas decyzji musi zawierać strefę czasową.")
    if not conn.in_transaction():
        raise ValueError("Rozdzielenie wymaga jawnej transakcji administracyjnej.")
    conn.execute(text("SELECT pg_advisory_xact_lock(61704001)"))
    # The briefing cursor uses this same lock: do not timestamp a future commit
    # before waiting, or a concurrent briefing could advance past this change.
    now = (utcnow() if now is None else now).astimezone(timezone.utc)
    with conn.begin_nested():
        plan = _load_plan(conn, identity, source_id, lock=True)
        new_id = uuid4()
        moving = plan.moving
        # Preserve a source's already reconstructed CAP reference fields, but never
        # seed the detached event with a different publisher's preferred contents.
        if plan.event["normal"].get("source_id") == source_id:
            seed = dict(plan.event["normal"])
        else:
            selected = max(moving, key=lambda record: (
                *_source_rank(record["normalized"], record["raw"]),
                record["retrieved_at"], record["provider_record_id"],
            ))
            seed = dict(selected["normalized"])
        for aggregate in ("evidence_version_ids", "source_urls", "independent_origins"):
            seed.pop(aggregate, None)
        seed = _geo(conn, seed)
        independent, origins = independent_origins([record["normalized"] for record in moving])
        seed["independent_origins"] = origins
        first_seen = min(item["retrieved_at"] for item in plan.observations)
        last_seen = max(record["last_seen_at"] for record in moving)
        _write_event(conn, new_id, seed, now, first_seen, "identity_split", [source_id], independent)

        moved_records = conn.execute(text("""
            UPDATE provider_records SET event_id=:new WHERE event_id=:old AND source_id=:source
        """), {"new": new_id, "old": identity, "source": source_id}).rowcount
        if moved_records != len(moving):
            raise IdentityConflict("Lista rekordów źródła zmieniła się w trakcie rozdzielenia.")
        moved_observations = conn.execute(text("""
            UPDATE event_evidence SET event_id=:new
            WHERE event_id=:old AND observation_id=ANY(:ids)
        """), {"new": new_id, "old": identity, "ids": [item["id"] for item in plan.observations]}).rowcount
        if moved_observations != len(plan.observations):
            raise IdentityConflict("Lista dowodów zmieniła się w trakcie rozdzielenia.")
        moved_keys = conn.execute(text("""
            UPDATE event_external_ids SET event_id=:new WHERE event_id=:old AND external_id=ANY(:keys)
        """), {"new": new_id, "old": identity, "keys": plan.move_keys}).rowcount
        if moved_keys != len(plan.move_keys):
            raise IdentityConflict("Przypisanie twardych kluczy zmieniło się w trakcie rozdzielenia.")

        # Both sides need overrides: the surviving publisher may still cite a key
        # owned by the detached source. Re-polling either side must remain valid.
        for record in plan.records:
            target = new_id if record["source_id"] == source_id else identity
            assigned = conn.execute(text("""
                INSERT INTO identity_overrides(source_id,provider_record_id,event_id,reason,created_at)
                SELECT :source,:record,:target,:reason,:now
                WHERE EXISTS(SELECT 1 FROM provider_records
                  WHERE source_id=:source AND provider_record_id=:record AND event_id=:target)
                ON CONFLICT(source_id,provider_record_id) DO UPDATE SET
                  event_id=excluded.event_id,reason=excluded.reason,created_at=excluded.created_at
                WHERE identity_overrides.event_id=:old
                RETURNING event_id
            """), {
                "source": record["source_id"], "record": record["provider_record_id"],
                "target": target, "old": identity, "reason": reason, "now": now,
            }).scalar_one_or_none()
            if assigned != target:
                raise IdentityConflict("Ręczna decyzja koliduje z aktualnym przypisaniem rekordu.")

        # Reuse the ingestion preference rules, lineage count and expiry handling.
        # This is an administrative update, not a newly observed incident.
        for current_id, seen in ((identity, plan.event["last_seen_at"]), (new_id, last_seen)):
            rebuild_event(conn, current_id, now)
            conn.execute(text("""
                UPDATE events SET last_seen_at=:seen,last_changed_at=:now,change_type='identity_split'
                WHERE id=:id
            """), {"id": current_id, "seen": seen, "now": now})
            normal = conn.execute(text("SELECT normal FROM events WHERE id=:id"),
                                  {"id": current_id}).scalar_one()
            snapshot = dict(normal)
            snapshot["identity_review"] = {
                "operation": "split_source", "old_event_id": str(identity), "new_event_id": str(new_id),
                "source_id": source_id, "reason": reason,
                "moved_provider_record_ids": [record["provider_record_id"] for record in moving],
                "retained_records": [{
                    "source_id": record["source_id"], "provider_record_id": record["provider_record_id"],
                } for record in plan.records if record["source_id"] != source_id],
            }
            _revision(
                conn, current_id, snapshot, now, "identity_split",
                f"Ręczne rozdzielenie źródła {source_id}: {identity} → {new_id}. Powód: {reason} "
                "To korekta tożsamości w bazie, nie nowy incydent ani niezależne potwierdzenie.",
            )
        relate_events(conn, [identity, new_id], now)
        return {
            "applied": True, "old_event_id": str(identity), "new_event_id": str(new_id),
            "source_id": source_id, "provider_record_ids": [record["provider_record_id"] for record in moving],
            "provider_record_count": moved_records, "observations_moved": moved_observations,
            "external_ids_moved": list(plan.move_keys), "override_record_count": len(plan.records),
            "remaining_source_ids": plan.public()["remaining_source_ids"],
            "reason": reason, "recorded_at": now.isoformat(),
        }
