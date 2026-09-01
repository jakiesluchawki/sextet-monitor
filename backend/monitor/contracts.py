from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NormalizedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    provider_record_id: str
    kind: Literal["incident", "advisory", "vulnerability_notice", "measurement"]
    category: Literal["earthquake", "disaster", "weather", "aviation", "cyber", "internet", "space_weather"]
    title: str = Field(min_length=1, max_length=800)
    description: str = Field(default="", max_length=12000)
    source_url: str
    occurred_start: datetime | None = None
    occurred_end: datetime | None = None
    issued_at: datetime | None = None
    source_updated_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    countries: list[str] = Field(default_factory=list)
    geometry: dict[str, Any] | None = None
    location_precision: Literal["point", "area", "country", "unknown"] = "unknown"
    time_precision: Literal["second", "minute", "hour", "day", "unknown"] = "unknown"
    severity: int = Field(default=0, ge=0, le=4)
    original_severity: str | None = None
    severity_reason: str = ""
    lifecycle_status: Literal["active", "expired", "withdrawn", "unknown"] = "active"
    verification_status: str = "reported"
    origins: list[str] = Field(default_factory=list)
    external_ids: list[str] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def timestamps_have_offsets(self):
        for field in ("occurred_start", "occurred_end", "issued_at", "source_updated_at", "valid_from", "valid_to"):
            value = getattr(self, field)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{field} requires timezone")
        if self.geometry is None and self.location_precision == "point":
            self.location_precision = "unknown"
        self.countries = sorted(set(c.upper() for c in self.countries))
        return self


class ProviderBatch(BaseModel):
    events: list[NormalizedEvent]
    warnings: list[str] = Field(default_factory=list)
    rejected_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceSpec(BaseModel):
    id: str
    name: str
    poll_interval_seconds: int
    coverage: str
    license_name: str
    license_url: str
    attribution: str
    requires_key: bool = False


@dataclass
class FetchedDocument:
    body: bytes
    content_type: str
    url: str
    status: int = 200
    fetched_at: datetime | None = None
    not_modified: bool = False


class Fetcher(Protocol):
    async def get(self, url: str, headers: dict[str, str] | None = None) -> FetchedDocument: ...


class EventQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_hours: int = Field(default=24, ge=1, le=720)
    time_basis: Literal["occurred", "changed", "published", "validity"] = "occurred"
    since: datetime | None = None
    until: datetime | None = None
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region: Literal["europe"] | None = None
    category: Literal["earthquake", "disaster", "weather", "aviation", "cyber", "internet", "space_weather"] | None = None
    severity_min: int = Field(default=0, ge=0, le=4)
    min_sources: int = Field(default=1, ge=1, le=10)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float | None = Field(default=None, gt=0, le=20000)
    include_inactive: bool = False
    limit: int = Field(default=300, ge=1, le=1000)

    @model_validator(mode="after")
    def coherent_filters(self):
        radius = (self.lat, self.lon, self.radius_km)
        if any(v is not None for v in radius) and not all(v is not None for v in radius):
            raise ValueError("lat, lon and radius_km are required together")
        for value in (self.since, self.until):
            if value is not None and value.tzinfo is None:
                raise ValueError("Query timestamps require timezone")
        if self.since and self.until and self.since >= self.until:
            raise ValueError("since must precede until")
        return self


class QueryInterpretation(BaseModel):
    supported: bool
    query: EventQuery | None = None
    explanation: str
    limitations: list[str] = Field(default_factory=list)
