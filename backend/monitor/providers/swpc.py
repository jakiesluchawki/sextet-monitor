"""Public SWPC bulletins: observed measurements and explicit forecast validity.

No regional outage is inferred from the product's generic potential-impact prose.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from monitor.contracts import Fetcher, FetchedDocument, NormalizedEvent, ProviderBatch
from .common import ProviderError, ensure_document, metadata, observed_now, plain, reject, warn

URL = "https://services.swpc.noaa.gov/products/alerts.json"
MAX_RECORDS = 400
UTC = timezone.utc
_MONTHS = {name: index for index, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1,
)}
_HEADLINE = re.compile(
    r"^(?:(CONTINUED|EXTENDED|CANCEL|CANCELLED)\s+)?(ALERT|WARNING|WATCH|SUMMARY)\s*:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def _field(message, name):
    values = re.findall(r"^" + re.escape(name) + r":\s*([^\n]*)$", message, re.MULTILINE | re.IGNORECASE)
    values = {value.strip() for value in values}
    if len(values) > 1:
        raise ValueError("ambiguous SWPC field")
    return next(iter(values), None)


def _utc_text(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(\d{4})\s+([A-Za-z]{3})\s+(\d{1,2})\s+(\d{2})(\d{2})\s+UTC", value.strip())
    if not match:
        return None
    try:
        return datetime(int(match[1]), _MONTHS[match[2].title()], int(match[3]),
                        int(match[4]), int(match[5]), tzinfo=UTC)
    except (ValueError, KeyError, OverflowError):
        return None


def _message_time(message, name, batch, *, observed_by=None):
    value = _field(message, name)
    instant = _utc_text(value)
    if value and instant is None:
        warn(batch.warnings, "SWPC " + name + ": niepoprawna data UTC; pozostawiono nieznaną")
    if instant and observed_by and instant > observed_by + timedelta(minutes=5):
        warn(batch.warnings, "SWPC " + name + ": przyszły czas nie jest obserwacją")
        return None
    return instant


def _issued(record, message):
    minute = _utc_text(_field(message, "Issue Time"))
    if minute is None:
        raise ValueError("missing unambiguous SWPC UTC issue time")
    value = record.get("issue_datetime")
    if value in (None, ""):
        return minute, minute, "minute"
    if (not isinstance(value, str) or len(value) > 64
            or not re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", value)):
        raise ValueError("invalid SWPC JSON issue time")
    try:
        instant = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid SWPC JSON issue time") from exc
    # The technical JSON clock can lag the bulletin by a few minutes. It is
    # only a consistency check; the explicit UTC Issue Time is the source clock.
    instant = instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant.astimezone(UTC)
    if not timedelta(0) <= instant - minute < timedelta(minutes=5):
        raise ValueError("SWPC JSON and explicit UTC issue times disagree")
    return minute, minute, "minute"


def _identity(code, serial, issued_minute):
    # Serial numbers and product IDs alone do not identify a bulletin across years.
    return code + ":" + serial + ":" + issued_minute.strftime("%Y%m%dT%H%MZ")


def _references(message, code, issued, batch):
    references = []
    for name in ("Continuation of Serial Number", "Extension to Serial Number", "Cancel Serial Number"):
        value = _field(message, name)
        if value is not None:
            if not re.fullmatch(r"\d{1,12}", value):
                raise ValueError("invalid SWPC reference")
            original = _message_time(message, "Original Issue Time", batch)
            if original and original >= issued:
                raise ValueError("SWPC reference is not older than its bulletin")
            references.append((code, value, original))
    return references


def _parse_record(record, batch, now):
    if not isinstance(record, dict):
        raise ValueError("invalid SWPC record")
    product = record.get("product_id")
    message = record.get("message")
    if not isinstance(product, str) or not re.fullmatch(r"[A-Z0-9]{1,16}", product):
        raise ValueError("invalid SWPC product ID")
    if not isinstance(message, str) or not 1 <= len(message) <= 20000:
        raise ValueError("invalid or oversized SWPC message")
    message = message.replace("\r\n", "\n").replace("\r", "\n")
    code, serial = _field(message, "Space Weather Message Code"), _field(message, "Serial Number")
    if not code or not re.fullmatch(r"[A-Z0-9]{1,20}", code):
        raise ValueError("invalid SWPC message code")
    if not serial or not re.fullmatch(r"\d{1,12}", serial):
        raise ValueError("invalid SWPC serial")
    issued, minute, issue_precision = _issued(record, message)
    if issued > now + timedelta(minutes=5):
        raise ValueError("future SWPC publication")
    if issued < now - timedelta(days=30):
        batch.metadata["excluded_outside_window"] += 1
        return None
    headlines = list(_HEADLINE.finditer(message))
    if len(headlines) != 1:
        raise ValueError("missing or ambiguous SWPC headline")
    prefix, bulletin_type, title = headlines[0].groups()
    prefix, bulletin_type = (prefix or "").upper(), bulletin_type.upper()
    cancelled = prefix in {"CANCEL", "CANCELLED"}
    forecast = bulletin_type in {"WARNING", "WATCH"}
    record_id = _identity(code, serial, minute)
    references = _references(message, code, minute, batch)
    if any(ref_code == code and ref_serial == serial for ref_code, ref_serial, _ in references):
        raise ValueError("SWPC bulletin refers to its own serial")

    start = end = valid_from = valid_to = None
    tags = ["swpc_bulletin", "unlocated_space_weather", "issue_time_precision:" + issue_precision]
    if forecast:
        tags.extend(["forecast", "advisory", bulletin_type.lower()])
        valid_from = _message_time(message, "Valid From", batch)
        valid_to = (_message_time(message, "Now Valid Until", batch)
                    or _message_time(message, "Valid To", batch))
        if valid_from and valid_to and valid_from >= valid_to:
            raise ValueError("invalid SWPC validity interval")
        status = ("expired" if valid_to and valid_to <= now else
                  "active" if valid_from and valid_to and valid_from <= now < valid_to else "unknown")
    else:
        tags.append("observed_alert" if bulletin_type == "ALERT" else "observed_summary")
        start = (_message_time(message, "Threshold Reached", batch, observed_by=issued)
                 or _message_time(message, "Begin Time", batch, observed_by=issued))
        end = _message_time(message, "End Time", batch, observed_by=issued)
        if start and end and start > end:
            raise ValueError("invalid SWPC observation interval")
        # Issuance of an alert is not proof that the condition remains present.
        status = "expired" if end else "unknown"
    if cancelled:
        status = "withdrawn"
        tags.append("source_cancellation")
    if "THIS SUPERSEDES ANY/ALL PRIOR WATCHES IN EFFECT" in message.upper():
        tags.append("supersedes_prior_watches_without_ids")
    original_scale = _field(message, "NOAA Scale")
    if not original_scale:
        scales = re.findall(r"\b[GRS][1-5]\b", title)
        original_scale = ", ".join(sorted(set(scales))) or None
    event = NormalizedEvent(
        source_id="noaa_swpc", provider_record_id=record_id,
        kind="advisory" if forecast else "measurement", category="space_weather",
        title=plain(headlines[0][0], 800), description=plain(message), source_url=URL,
        occurred_start=start, occurred_end=end, issued_at=issued, source_updated_at=issued,
        valid_from=valid_from, valid_to=valid_to, countries=[], geometry=None,
        location_precision="unknown", time_precision="minute" if start else issue_precision,
        severity=0, original_severity=plain(original_scale, 300) if original_scale else None,
        severity_reason="Zachowano skalę NOAA G/R/S, jeśli podana; nie przeliczono jej na lokalną wagę zagrożenia.",
        lifecycle_status=status, verification_status="official_bulletin", origins=["noaa:swpc"],
        external_ids=["swpc:" + record_id], tags=tags, raw=record,
    )
    return event, (code, serial), minute, references


def parse(doc: FetchedDocument) -> ProviderBatch:
    ensure_document(doc, "NOAA SWPC")
    try:
        def invalid_constant(value):
            raise ValueError("non-finite JSON value")
        records = json.loads(doc.body, parse_constant=invalid_constant)
    except (ValueError, UnicodeError) as exc:
        raise ProviderError("NOAA SWPC: nieprawidłowy JSON") from exc
    if not isinstance(records, list):
        raise ProviderError("NOAA SWPC: oczekiwano tablicy biuletynów")
    batch = ProviderBatch(events=[], metadata=metadata(
        doc, len(records), window_days=30, record_limit=MAX_RECORDS,
        excluded_outside_window=0, duplicate_records=0, provider_timestamp=None,
    ))
    if len(records) >= MAX_RECORDS:
        batch.metadata["partial"] = True
        warn(batch.warnings, "SWPC: limit 400 biuletynów; odczyt nie obejmuje pełnego archiwum")
    now, candidates, raw_seen, conflicts = observed_now(doc), {}, {}, set()
    for index, record in enumerate(records[:MAX_RECORDS]):
        try:
            parsed = _parse_record(record, batch, now)
            if parsed is None:
                continue
            event = parsed[0]
            key = event.provider_record_id
            encoded = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False)
            if key in raw_seen:
                if key not in conflicts and raw_seen[key] == encoded:
                    batch.metadata["duplicate_records"] += 1
                    continue
                if key in candidates:
                    del candidates[key]
                    batch.rejected_count += 1
                conflicts.add(key)
                raise ValueError("conflicting duplicate SWPC bulletin")
            raw_seen[key] = encoded
            candidates[key] = parsed
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reject(batch, "NOAA SWPC", index, exc)

    by_serial = {}
    for key, (_, code_serial, minute, _) in candidates.items():
        by_serial.setdefault(code_serial, []).append((minute, key))
    for key, (event, _, minute, references) in candidates.items():
        for code, serial, original in references:
            if original:
                target = _identity(code, serial, original)
            else:
                prior = [item for item in by_serial.get((code, serial), []) if item[0] < minute]
                target = prior[0][1] if len(prior) == 1 else None
            if target is None or target in conflicts:
                event.tags.append("unresolved_source_reference")
                warn(batch.warnings, "SWPC: brak jednoznacznego biuletynu wskazanego numerem; nie połączono zdarzeń")
                continue
            event.external_ids.append("swpc:" + target)
        event.external_ids = sorted(set(event.external_ids))
    # Older bulletins establish identities first, then explicit continuations/cancellations.
    batch.events = [item[0] for item in sorted(candidates.values(), key=lambda item: (item[2], item[0].provider_record_id))]
    return batch


async def collect(fetcher: Fetcher, config: dict[str, str]) -> ProviderBatch:
    return parse(await fetcher.get(URL))
