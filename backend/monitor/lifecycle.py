"""Clock-derived display state without mutating immutable source observations."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _instant(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value if isinstance(value, datetime) and value.tzinfo is not None else None


def effective_event_state(normal: dict, now: datetime) -> dict:
    """Return current lifecycle/tags; provider timestamps and evidence stay unchanged."""
    result = dict(normal)
    tags = set(result.get("tags") or [])
    status = result.get("lifecycle_status", "unknown")
    end = _instant(result.get("valid_to"))
    if result.get("source_id") == "meteoalarm":
        onset = _instant(result.get("occurred_start"))
        if status not in {"withdrawn", "expired"} and onset and onset > now:
            tags.add("hazard_onset_in_future")
        else:
            tags.discard("hazard_onset_in_future")
        # A current terminal state is not an as-of reconstruction. An earlier read
        # clock must not revive it; a real source correction supplies a new normal.
        if status not in {"withdrawn", "expired"}:
            start = _instant(result.get("valid_from"))
            if end and end <= now:
                status = "expired"
            elif end and start and start <= now:
                status = "active"
            else:
                status = "unknown"
    elif status in {"active", "unknown"} and end and end <= now:
        status = "expired"
    result["lifecycle_status"] = status
    result["tags"] = sorted(tags)
    return result
