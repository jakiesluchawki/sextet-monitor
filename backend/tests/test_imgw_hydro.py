"""Synthetic IMGW schema fixtures; no live provider requests in this suite."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import json

import pytest

from monitor.contracts import FetchedDocument
from monitor.providers import imgw_hydro
from monitor.providers.common import ProviderError

NOW = datetime(2026, 9, 4, 23, tzinfo=timezone.utc)


def warning(**changes):
    result = {
        "id": 7774, "number": 188, "degree": 2,
        "releaseDate": "2026-09-04T09:59:09Z",
        "dateFrom": "2026-09-04T10:00:00Z", "dateTo": "2026-09-05T08:00:00Z",
        "office": "SOWR", "officeDescription": "Biuro testowe IMGW",
        "event": "W_PSO", "eventDescription": "Wezbranie z przekroczeniem stanów ostrzegawczych",
        "run": "Opis testowy ostrzeżenia.", "comment": "Komunikat może ulec zmianie.",
        "areaDescription": "Przykładowa zlewnia", "probability": 70,
        "provinces": [{"name": "warmińsko-mazurskie", "areas": ["Z_O_WM_584_A"]}],
        "statusIsCurrent": True, "statusIsFuture": False, "statusIsPast": False,
        "statusIsRevoked": False, "statusIsToConfirm": False, "isUntilRevoke": False,
        "isUpdate": False, "isDelete": False, "referenceDate": None,
    }
    result.update(changes)
    return result


def document(records=None, *, body=None, status=200, not_modified=False):
    return FetchedDocument(
        body=body if body is not None else json.dumps([warning()] if records is None else records).encode(),
        content_type="application/json", url=imgw_hydro.URL, status=status,
        fetched_at=NOW, not_modified=not_modified,
    )


def event(**changes):
    return imgw_hydro.parse(document([warning(**changes)])).events[0]


def test_warning_uses_explicit_source_time_country_and_origin_without_invented_geometry():
    result = event()
    assert result.provider_record_id == "7774"
    assert result.kind == "advisory" and result.category == "weather"
    assert result.origins == ["imgw"] and result.countries == ["PL"]
    assert result.geometry is None and result.location_precision == "country"
    assert result.issued_at == datetime(2026, 9, 4, 9, 59, 9, tzinfo=timezone.utc)
    assert result.source_updated_at == result.issued_at
    assert result.valid_from == datetime(2026, 9, 4, 10, tzinfo=timezone.utc)
    assert result.valid_to == datetime(2026, 9, 5, 8, tzinfo=timezone.utc)
    assert result.occurred_start is None and result.occurred_end is None
    assert result.lifecycle_status == "active" and result.time_precision == "second"
    assert result.external_ids == ["imgw:hydro:7774"]
    assert imgw_hydro.ATTRIBUTION in result.description
    assert result.source_url == "https://hydro.imgw.pl/#/warnings/hydro"


@pytest.mark.parametrize(("degree", "expected"), [(1, 2), (2, 3), (3, 4), (-1, 0)])
def test_native_degree_does_not_become_provider_confidence(degree, expected):
    result = event(degree=degree)
    assert result.severity == expected and result.original_severity == str(degree)
    if degree == -1:
        assert "nie brak zagrożenia" in result.severity_reason
        assert "hydrological_drought" in result.tags


def test_until_revoked_is_not_a_year_9999_date_and_not_a_new_incident():
    result = event(degree=-1, isUntilRevoke=True, dateTo=None,
                   releaseDate="2026-05-01T08:00:00Z", dateFrom="2026-05-01T08:01:00Z")
    assert result.valid_to is None and result.lifecycle_status == "active"
    assert "until_revoked" in result.tags
    assert "do odwołania" in result.description
    assert result.issued_at.year == 2026 and result.issued_at.month == 5
    assert result.occurred_start is None


def test_naive_times_are_unknown_and_original_text_is_visible():
    batch = imgw_hydro.parse(document([warning(
        releaseDate="2026-09-04 09:59:09", dateFrom="2026-09-04 10:00:00",
        dateTo="2026-09-05 08:00:00",
    )]))
    result = batch.events[0]
    assert result.issued_at is result.source_updated_at is result.valid_from is result.valid_to is None
    assert result.lifecycle_status == "unknown" and result.time_precision == "unknown"
    assert "2026-09-04 09:59:09" in result.description and "czas nieustalony" in result.description
    assert batch.warnings


def test_open_end_sentinel_stays_null_and_explicitly_warns():
    batch = imgw_hydro.parse(document([warning(isUntilRevoke=True, dateTo="9999-12-31T23:59:59Z")]))
    assert batch.events[0].valid_to is None
    assert any("9999" in value for value in batch.warnings)


@pytest.mark.parametrize("changes", [
    {"dateFrom": "2026-09-06T00:00:00Z", "dateTo": "2026-09-05T00:00:00Z"},
    {"isUntilRevoke": True},
])
def test_contradictory_validity_cannot_look_current(changes):
    batch = imgw_hydro.parse(document([warning(**changes)]))
    result = batch.events[0]
    assert result.lifecycle_status == "unknown"
    assert result.valid_from is result.valid_to is None
    assert batch.warnings


def test_update_without_predecessor_id_does_not_fabricate_merge():
    batch = imgw_hydro.parse(document([warning(isUpdate=True, referenceDate="2026-08-31T08:00:00Z")]))
    result = batch.events[0]
    assert result.supersedes == [] and result.external_ids == ["imgw:hydro:7774"]
    assert "update_reference_unresolved" in result.tags
    assert "2026-08-31T08:00:00Z" == result.raw["referenceDate"]
    assert batch.warnings == [] and batch.metadata["unlinked_updates"] == 1
    assert batch.metadata["current_list_complete"] is True


def test_revocation_overrides_current_status():
    assert event(statusIsRevoked=True).lifecycle_status == "withdrawn"
    assert event(isDelete=True).lifecycle_status == "withdrawn"


def test_ended_and_future_periods_are_not_current_hazards():
    assert event(dateTo="2026-09-04T20:00:00Z").lifecycle_status == "expired"
    result = event(statusIsCurrent=False, statusIsFuture=True,
                   dateFrom="2026-09-05T02:00:00Z", dateTo="2026-09-05T03:00:00Z")
    assert result.lifecycle_status == "unknown"
    assert "hazard_onset_in_future" in result.tags


def test_future_publication_is_not_relabelled_as_fetch_time():
    batch = imgw_hydro.parse(document([warning(releaseDate="2026-09-06T09:59:09Z")]))
    assert batch.events[0].issued_at is batch.events[0].source_updated_at is None
    assert "2026-09-06T09:59:09Z" in batch.events[0].description
    assert batch.warnings


def test_unconfirmed_material_is_excluded_and_malformed_record_does_not_hide_valid():
    batch = imgw_hydro.parse(document([warning(), warning(id=7775, statusIsToConfirm=True), {"id": 7776}]))
    assert len(batch.events) == 1 and batch.rejected_count == 1
    assert batch.metadata["excluded_unconfirmed"] == 1
    assert batch.warnings


@pytest.mark.parametrize("changes", [
    {"id": True}, {"id": -4}, {"degree": True}, {"degree": "2"},
    {"statusIsCurrent": "false"}, {"releaseDate": {}},
    {"provinces": [{"name": "test", "areas": ["../../evil"]}]},
])
def test_bad_identity_or_schema_is_rejected(changes):
    batch = imgw_hydro.parse(document([warning(**changes)]))
    assert batch.events == [] and batch.rejected_count == 1


@pytest.mark.parametrize("body", [b"", b"{}", b"null", b"<html>bad</html>", b"[NaN]", b"[1e999]"])
def test_invalid_payload_never_becomes_successful_empty_feed(body):
    with pytest.raises(ProviderError):
        imgw_hydro.parse(document(body=body))


def test_http_errors_and_uncached_304_are_rejected():
    for doc in (document(status=503), document(body=b"", status=304, not_modified=True)):
        with pytest.raises(ProviderError):
            imgw_hydro.parse(doc)
    assert len(imgw_hydro.parse(document(status=304, not_modified=True)).events) == 1


def test_true_empty_list_is_allowed_and_batch_limits_are_visible():
    empty = imgw_hydro.parse(document([]))
    assert empty.events == [] and empty.warnings == [] and empty.metadata["records_seen"] == 0
    records = [warning(id=index + 1) for index in range(imgw_hydro.MAX_RECORDS + 1)]
    batch = imgw_hydro.parse(document(records))
    assert len(batch.events) == imgw_hydro.MAX_RECORDS
    assert batch.metadata["records_seen"] == imgw_hydro.MAX_RECORDS + 1
    assert batch.warnings


def test_duplicate_payloads_collapse_but_revisions_are_ordered_by_source_clock():
    older = warning(releaseDate="2026-09-03T09:59:09Z", run="Wcześniejsza wersja.")
    batch = imgw_hydro.parse(document([warning(), copy.deepcopy(older), warning(), older]))
    assert len(batch.events) == 2
    assert batch.events[0].issued_at < batch.events[1].issued_at
    assert batch.events[0].provider_record_id == batch.events[1].provider_record_id
    assert batch.metadata["provider_timestamp"] == "2026-09-04T09:59:09+00:00"


def test_conflicting_same_clock_revisions_do_not_depend_on_list_order():
    first, second = warning(run="Pierwsza treść."), warning(run="Druga treść.")
    for records in ([first, second], [second, first]):
        batch = imgw_hydro.parse(document(records))
        assert batch.events == [] and batch.rejected_count == 2
        assert batch.metadata["current_list_complete"] is False
        assert batch.warnings


def test_complete_current_list_has_a_fetch_clock_not_the_latest_publication_clock():
    batch = imgw_hydro.parse(document())
    assert batch.metadata["current_list_complete"] is True
    assert batch.metadata["current_list_fetched_at"] == NOW.isoformat()
    assert batch.metadata["current_list_fetched_at"] != batch.metadata["provider_timestamp"]


def test_raw_does_not_keep_unneeded_author_fields_or_invent_permalinks():
    result = event(author="PRIVATE TEST AUTHOR", authorIsAdmin=True)
    assert "author" not in result.raw and "authorIsAdmin" not in result.raw
    assert "PRIVATE TEST AUTHOR" not in result.model_dump_json()
    assert result.raw["api_url"] == imgw_hydro.URL


def test_collect_uses_only_the_public_offset_aware_endpoint():
    class Fetcher:
        calls = []

        async def get(self, url, headers=None):
            self.calls.append((url, headers))
            return document()

    fetcher = Fetcher()
    batch = asyncio.run(imgw_hydro.collect(fetcher, {}))
    assert len(batch.events) == 1
    assert fetcher.calls == [(imgw_hydro.URL, {"Accept": "application/json"})]
