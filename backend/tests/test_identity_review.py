"""Administrative identity review; integration cases require a disposable PostGIS DB."""
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from monitor import identity_review
from monitor.ingestion import IdentityConflict
from test_storage import NOW, URL, db as storage_db, event, ingest

# Re-export the existing rollback fixture under its pytest name.
db = storage_db

integration = pytest.mark.skipif(not URL, reason="Use manage.py test for an isolated PostGIS database.")


class NoDatabase:
    def in_transaction(self):
        return False

    def __getattr__(self, name):
        raise AssertionError("Invalid input must not access the database: " + name)


@pytest.mark.parametrize("event_id,source,reason,when,message", [
    ("bad-uuid", "usgs", "Ocena", NOW, "UUID"),
    (str(UUID(int=1)), "not-configured", "Ocena", NOW, "Źródło"),
    (str(UUID(int=1)), "usgs", "", NOW, "Powód"),
    (str(UUID(int=1)), "usgs", "  ", NOW, "Powód"),
    (str(UUID(int=1)), "usgs", None, NOW, "Powód"),
    (str(UUID(int=1)), "usgs", "a" * 501, NOW, "500"),
    (str(UUID(int=1)), "usgs", "Ocena", datetime(2026, 8, 26), "strefę"),
    (str(UUID(int=1)), "usgs", "Ocena", "2026-08-26", "strefę"),
])
def test_invalid_admin_input_fails_before_sql(event_id, source, reason, when, message):
    with pytest.raises(ValueError, match=message):
        identity_review.split_source(NoDatabase(), event_id, source, reason, now=when)


def test_apply_requires_caller_transaction_even_for_valid_reason_at_boundary():
    with pytest.raises(ValueError, match="transakcji"):
        identity_review.split_source(NoDatabase(), UUID(int=1), "usgs", "ą" * 500, now=NOW)


def _merged(conn):
    original = event()
    ingest(conn, "usgs", [original])
    first = event(
        "EQ:one", "gdacs", title="Pierwszy raport GDACS",
        source_url="https://www.gdacs.org/report.aspx?eventid=one",
        origins=["usgs"], external_ids=["usgs:fixture-1", "gdacs:EQ:one"],
    )
    second = event(
        "EQ:two", "gdacs", title="Drugi rekord GDACS",
        source_url="https://www.gdacs.org/report.aspx?eventid=two",
        origins=["usgs"], external_ids=["usgs:fixture-1", "gdacs:EQ:two"],
    )
    ingest(conn, "gdacs", [first, second], now=NOW + timedelta(seconds=10))
    current = event(
        "EQ:one", "gdacs", title="Poprawiony raport GDACS", source_updated_at=NOW,
        source_url=first.source_url, origins=["usgs"],
        external_ids=["usgs:fixture-1", "gdacs:EQ:one"],
    )
    ingest(conn, "gdacs", [current, second], now=NOW + timedelta(minutes=1))
    event_id = conn.execute(text(
        "SELECT event_id FROM provider_records WHERE source_id='usgs' AND provider_record_id='fixture-1'"
    )).scalar_one()
    return event_id, original, [current, second]


def _snapshot(conn):
    # Fixed allowlist: no user-controlled SQL identifiers.
    tables = (
        "events", "observations", "event_evidence", "provider_records",
        "event_external_ids", "event_revisions", "identity_overrides", "event_relations",
    )
    return {table: [dict(row) for row in conn.execute(text(
        "SELECT * FROM " + table + " ORDER BY 1,2"
    )).mappings()] for table in tables}


@integration
def test_preview_is_read_only_and_lists_entire_source_group(db):
    event_id, _original, _mirrors = _merged(db)
    before = _snapshot(db)
    with db.begin_nested():
        db.execute(text("SET LOCAL ROLE monitor_reader"))
        preview = identity_review.preview_split(db, str(event_id), "gdacs")
        db.execute(text("RESET ROLE"))
    assert preview["can_split"]
    assert preview["provider_record_ids"] == ["EQ:one", "EQ:two"]
    assert preview["provider_record_count"] == 2 and preview["observation_count"] == 3
    assert preview["remaining_source_ids"] == ["usgs"]
    assert preview["move_external_ids"] == [
        "gdacs:EQ:one", "gdacs:EQ:two", "record:gdacs:EQ:one", "record:gdacs:EQ:two",
    ]
    assert preview["keep_external_ids"] == ["record:usgs:fixture-1", "usgs:fixture-1"]
    assert preview["override_record_count"] == 3
    assert _snapshot(db) == before


@integration
@pytest.mark.parametrize("source", ["gdacs", "usgs"])
def test_split_preserves_history_keys_times_and_both_sides_survive_two_polls(db, source):
    old_id, original, mirrors = _merged(db)
    before = _snapshot(db)
    old_row = next(row for row in before["events"] if row["id"] == old_id)
    moved_observations = [row for row in before["observations"] if row["source_id"] == source]
    moving_records = [row for row in before["provider_records"] if row["source_id"] == source]
    decision_time = NOW + timedelta(minutes=5)
    result = identity_review.split_source(db, old_id, source, "  Zweryfikowano błędne wspólne ID.  ", now=decision_time)
    new_id = UUID(result["new_event_id"])
    assert new_id != old_id
    after = _snapshot(db)
    assert after["observations"] == before["observations"]
    # Every old revision, including its snapshot and timestamp, remains byte-for-byte equivalent.
    old_revisions = {row["id"]: row for row in before["event_revisions"]}
    assert {row["id"]: row for row in after["event_revisions"] if row["id"] in old_revisions} == old_revisions
    assert len(after["events"]) == 2
    assert result["observations_moved"] == len(moved_observations)
    new_event = next(row for row in after["events"] if row["id"] == new_id)
    old_event = next(row for row in after["events"] if row["id"] == old_id)
    expected_other = "usgs" if source == "gdacs" else "gdacs"
    assert new_event["source_ids"] == [source] and old_event["source_ids"] == [expected_other]
    assert new_event["independent_source_count"] == old_event["independent_source_count"] == 1
    assert new_event["title"] == (mirrors[0].title if source == "gdacs" else original.title)
    assert old_event["title"] == (original.title if source == "gdacs" else mirrors[0].title)
    assert old_event["first_seen_at"] == old_row["first_seen_at"]
    assert old_event["last_seen_at"] == old_row["last_seen_at"]
    assert new_event["first_seen_at"] == min(row["retrieved_at"] for row in moved_observations)
    assert new_event["last_seen_at"] == max(row["last_seen_at"] for row in moving_records)
    for row in (old_event, new_event):
        assert row["last_changed_at"] == decision_time and row["change_type"] == "identity_split"
        assert "manual_identity_override" in row["normal"]["tags"]
        audit = [revision for revision in after["event_revisions"]
                 if revision["event_id"] == row["id"] and revision["change_type"] == "identity_split"]
        assert len(audit) == 1
        assert "Zweryfikowano błędne wspólne ID." in audit[0]["summary"]
        assert audit[0]["snapshot"]["identity_review"]["new_event_id"] == str(new_id)
    evidence_owner = {row["observation_id"]: row["event_id"] for row in after["event_evidence"]}
    for row in before["observations"]:
        assert evidence_owner[row["id"]] == (new_id if row["source_id"] == source else old_id)
    keys = {row["external_id"]: row["event_id"] for row in after["event_external_ids"]}
    usgs_id = new_id if source == "usgs" else old_id
    gdacs_id = new_id if source == "gdacs" else old_id
    assert keys["usgs:fixture-1"] == keys["record:usgs:fixture-1"] == usgs_id
    assert keys["gdacs:EQ:one"] == keys["record:gdacs:EQ:one"] == gdacs_id
    assert keys["gdacs:EQ:two"] == keys["record:gdacs:EQ:two"] == gdacs_id
    assert len(after["identity_overrides"]) == 3
    for override in after["identity_overrides"]:
        assert override["event_id"] == (new_id if override["source_id"] == source else old_id)
        assert override["reason"] == "Zweryfikowano błędne wspólne ID."
    for minute in (6, 7):
        for publisher, records in (("usgs", [original]), ("gdacs", mirrors)):
            poll = ingest(db, publisher, records, now=NOW + timedelta(minutes=minute))
            assert poll["status"] == "ok" and poll["rejected"] == 0
            assert poll["new_observations"] == poll["changed_events"] == 0
    assert db.execute(text("SELECT count(*) FROM events")).scalar_one() == 2
    assert db.execute(text("SELECT event_id FROM provider_records WHERE source_id='usgs'")).scalar_one() == usgs_id
    assert set(db.execute(text(
        "SELECT event_id FROM provider_records WHERE source_id='gdacs'"
    )).scalars()) == {gdacs_id}


@integration
@pytest.mark.parametrize("source,missing,message", [
    ("usgs", False, "ostatniego"), ("gdacs", False, "nie zawiera"), ("usgs", True, "Nie znaleziono"),
])
def test_empty_or_last_source_cannot_be_split(db, source, missing, message):
    ingest(db, "usgs", [event()])
    event_id = uuid4() if missing else db.execute(text("SELECT id FROM events")).scalar_one()
    before = _snapshot(db)
    with pytest.raises(IdentityConflict, match=message):
        identity_review.preview_split(db, event_id, source)
    with pytest.raises(IdentityConflict, match=message):
        identity_review.split_source(db, event_id, source, "Ocena", now=NOW + timedelta(minutes=1))
    assert _snapshot(db) == before


@integration
@pytest.mark.parametrize("conflicting_source,record", [("gdacs", "EQ:one"), ("usgs", "fixture-1")])
def test_existing_override_conflict_on_either_side_refuses_before_mutation(db, conflicting_source, record):
    old_id, _original, _mirrors = _merged(db)
    ingest(db, "usgs", [event("other")], now=NOW + timedelta(minutes=2))
    other_id = db.execute(text(
        "SELECT event_id FROM provider_records WHERE source_id='usgs' AND provider_record_id='other'"
    )).scalar_one()
    db.execute(text("""
        INSERT INTO identity_overrides(source_id,provider_record_id,event_id,reason,created_at)
        VALUES(:source,:record,:event,'Sprzeczny zapis testowy',:now)
    """), {"source": conflicting_source, "record": record, "event": other_id, "now": NOW})
    before = _snapshot(db)
    with pytest.raises(IdentityConflict, match="ręczna decyzja"):
        identity_review.split_source(db, old_id, "gdacs", "Ocena", now=NOW + timedelta(minutes=3))
    assert _snapshot(db) == before


@integration
def test_owned_hard_key_pointing_elsewhere_is_not_stolen(db):
    old_id, _original, _mirrors = _merged(db)
    ingest(db, "usgs", [event("other")], now=NOW + timedelta(minutes=2))
    other_id = db.execute(text(
        "SELECT event_id FROM provider_records WHERE source_id='usgs' AND provider_record_id='other'"
    )).scalar_one()
    db.execute(text("UPDATE event_external_ids SET event_id=:other WHERE external_id='gdacs:EQ:one'"),
               {"other": other_id})
    before = _snapshot(db)
    with pytest.raises(IdentityConflict, match="Twardy klucz"):
        identity_review.split_source(db, old_id, "gdacs", "Ocena", now=NOW + timedelta(minutes=3))
    assert _snapshot(db) == before


@integration
def test_failure_after_reassignment_rolls_back_savepoint_and_keeps_outer_transaction_usable(db, monkeypatch):
    old_id, _original, _mirrors = _merged(db)
    before = _snapshot(db)

    def reject_rebuild(*_args, **_kwargs):
        raise IdentityConflict("Kontrolowany błąd odbudowy")

    monkeypatch.setattr(identity_review, "rebuild_event", reject_rebuild)
    with pytest.raises(IdentityConflict, match="Kontrolowany"):
        identity_review.split_source(db, old_id, "gdacs", "Ocena", now=NOW + timedelta(minutes=3))
    assert _snapshot(db) == before
    assert db.in_transaction()


@integration
def test_worker_cannot_persist_administrative_overrides(db):
    old_id, _original, _mirrors = _merged(db)
    before = _snapshot(db)
    with db.begin_nested():
        db.execute(text("SET LOCAL ROLE monitor_worker"))
        with pytest.raises(DBAPIError):
            identity_review.split_source(db, old_id, "gdacs", "Ocena", now=NOW + timedelta(minutes=3))
        db.execute(text("RESET ROLE"))
    assert _snapshot(db) == before
