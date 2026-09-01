"""Local-only HTTP API. This process reads data and may insert briefings only."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__, db
from .briefing import build_briefing
from .config import Settings
from .contracts import EventQuery, utcnow
from .db import get_engine
from .query import _datetime, _relations, _unique, build_answer, parse_question

MAX_BODY_BYTES = 16 * 1024
MAX_QUERY_BYTES = 8 * 1024
ALLOWED_ORIGINS = frozenset({"http://localhost:3180", "http://127.0.0.1:3180"})
ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "api"})
READ_TRANSACTION = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
Category = Literal["earthquake", "disaster", "weather", "aviation", "cyber", "internet", "space_weather"]
Lifecycle = Literal["active", "expired", "withdrawn", "unknown"]
SourceState = Literal["pending", "ok", "ok_empty", "partial", "error", "stale", "needs_credentials", "disabled"]


class OutputModel(BaseModel):
    # The database normal form also contains raw inputs; list endpoints do not.
    model_config = ConfigDict(extra="ignore")


class SourceStatus(OutputModel):
    id: str
    name: str
    status: SourceState
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    newest_content_at: datetime | None = None
    next_due_at: datetime | None = None
    record_count: int | None = Field(default=None, ge=0)
    error: str | None = None
    poll_interval_seconds: int = Field(ge=1)
    coverage: str
    license_name: str
    license_url: str
    attribution: str
    requires_key: bool
    enabled: bool


class EventSummary(OutputModel):
    id: UUID
    kind: Literal["incident", "advisory", "vulnerability_notice", "measurement"]
    category: Category
    title: str
    description: str = ""
    occurred_start: datetime | None = None
    occurred_end: datetime | None = None
    issued_at: datetime | None = None
    source_updated_at: datetime | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_changed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    countries: list[str] = Field(default_factory=list)
    geometry: dict[str, Any] | None = None
    location_precision: Literal["point", "area", "country", "unknown"] = "unknown"
    time_precision: Literal["second", "minute", "hour", "day", "unknown"] = "unknown"
    severity: int = Field(ge=0, le=4)
    severity_label: str
    severity_reason: str = ""
    original_severity: str | None = None
    lifecycle_status: Lifecycle
    verification_status: str
    anomaly_score: None = None
    source_ids: list[str]
    source_count: int = Field(ge=0)
    independent_source_count: int = Field(ge=0)
    source_url: str
    tags: list[str] = Field(default_factory=list)
    change_type: str


class Evidence(OutputModel):
    id: UUID
    source_id: str
    source_name: str
    provider_record_id: str
    source_url: str
    retrieved_at: datetime
    issued_at: datetime | None = None
    source_updated_at: datetime | None = None
    source_snapshot_at: datetime | None = None
    origins: list[str]
    payload_hash: str
    raw: dict[str, Any] | None
    raw_retained: bool
    attribution: str
    license_url: str


class Revision(OutputModel):
    id: UUID
    recorded_at: datetime
    change_type: str
    summary: str | dict[str, Any]


class Relation(OutputModel):
    event_id: UUID
    title: str
    relation_type: str
    reason: str
    distance_km: float | None = None
    time_delta_hours: float | None = None


class EventDetail(EventSummary):
    evidence: list[Evidence]
    revisions: list[Revision]
    relations: list[Relation]


class EventList(OutputModel):
    items: list[EventSummary]
    total: int = Field(ge=0)
    shown: int = Field(ge=0)
    mapped: int = Field(ge=0)
    unlocated: int = Field(ge=0)
    truncated: bool
    query: EventQuery
    source_health: list[SourceStatus]
    generated_at: datetime
    limitations: list[str] = Field(default_factory=list)


class SourceList(OutputModel):
    items: list[SourceStatus]
    generated_at: datetime


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000, strict=True)

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Pytanie nie może być puste.")
        return value.strip()


class Fact(OutputModel):
    text: str
    event_id: str
    source_urls: list[str]


class QueryResponse(OutputModel):
    supported: bool
    answer: str
    interpretation: EventQuery | None
    query_explanation: str
    events: list[EventSummary]
    facts: list[Fact]
    inferences: list[str]
    limitations: list[str]
    source_health: list[SourceStatus]
    generated_at: datetime
    total: int | None = None
    shown: int | None = None
    truncated: bool = False


class BriefingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_hours: int = Field(default=24, ge=1, le=720, strict=True)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$", strict=True)


class SectionItem(OutputModel):
    event_id: str
    text: str


class BriefingSection(OutputModel):
    title: str
    items: list[SectionItem]


class BriefingResponse(OutputModel):
    id: UUID
    answer: str
    since: datetime
    until: datetime
    sections: list[BriefingSection]
    facts: list[Fact]
    inferences: list[str]
    limitations: list[str]
    source_health: list[SourceStatus]
    generated_at: datetime
    scope: BriefingRequest
    first_briefing: bool
    total: int = Field(ge=0)
    shown: int = Field(ge=0)
    truncated: bool
    initial_import_background_count: int = Field(default=0, ge=0)
    # Nullable for older saved briefings whose counters cannot be reconstructed.
    processed_count: int | None = Field(default=None, ge=0)
    citable_count: int | None = Field(default=None, ge=0)
    omitted_fact_count: int | None = Field(default=None, ge=0)
    historical_count: int | None = Field(default=None, ge=0)
    uncitable_count: int | None = Field(default=None, ge=0)


class HealthResponse(OutputModel):
    status: Literal["ok", "error"]
    version: str
    database: Literal["ok", "unavailable"]
    ai_mode: Literal["off"] = "off"
    timezone: str


class LocalRequestGuard:
    """Check browser boundaries and enforce the size cap before JSON parsing."""

    def __init__(
        self, app: ASGIApp, *, testing: bool = False, allowed_origins: tuple[str, ...] | None = None
    ):
        self.app = app
        self.hosts = ALLOWED_HOSTS | ({"testserver"} if testing else set())
        self.origins = ALLOWED_ORIGINS if allowed_origins is None else ALLOWED_ORIGINS.intersection(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def guarded_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                additions = {
                    b"cache-control": b"no-store",
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"referrer-policy": b"no-referrer",
                }
                headers = [(key, value) for key, value in headers if key.lower() not in additions]
                message = {**message, "headers": [*headers, *additions.items()]}
            await send(message)

        async def reject(code: int, detail: str) -> None:
            await JSONResponse({"detail": detail}, status_code=code)(scope, receive, guarded_send)

        headers: dict[bytes, list[bytes]] = {}
        for name, value in scope.get("headers", []):
            headers.setdefault(name.lower(), []).append(value)
        hosts = headers.get(b"host", [])
        if len(hosts) != 1:
            await reject(400, "Wymagany jest jeden poprawny nagłówek Host.")
            return
        try:
            host_value = hosts[0].decode("ascii")
            host = urlsplit("//" + host_value)
            valid_host = (
                not any(character.isspace() for character in host_value)
                and host.hostname in self.hosts and not host.username and not host.password
                and not host.path and not host.query and not host.fragment
            )
            # Accessing port validates its syntax and range.
            _ = host.port
        except (ValueError, UnicodeDecodeError):
            valid_host = False
        if not valid_host:
            await reject(403, "Ten Host nie jest dozwolony dla lokalnego API.")
            return

        origins = headers.get(b"origin", [])
        if len(origins) > 1 or (origins and origins[0].decode("latin-1") not in self.origins):
            await reject(403, "Niedozwolone pochodzenie żądania.")
            return
        if len(scope.get("query_string", b"")) > MAX_QUERY_BYTES:
            await reject(414, "Parametry zapytania są zbyt długie.")
            return
        is_post = scope.get("method") == "POST"
        if is_post:
            if headers.get(b"x-monitor-request") != [b"1"]:
                await reject(403, "Żądanie POST wymaga X-Monitor-Request: 1.")
                return
            if headers.get(b"sec-fetch-site") == [b"cross-site"]:
                await reject(403, "Żądania POST z obcych witryn są niedozwolone.")
                return
            content_types = headers.get(b"content-type", [])
            if len(content_types) != 1 or content_types[0].decode("latin-1").split(";", 1)[0].strip().lower() != "application/json":
                await reject(415, "Wymagany jest Content-Type: application/json.")
                return

        lengths = headers.get(b"content-length", [])
        if len(lengths) > 1:
            await reject(400, "Niejednoznaczny rozmiar treści żądania.")
            return
        if lengths:
            try:
                length_text = lengths[0].decode("ascii")
                if not length_text.isdigit():
                    raise ValueError
                length = int(length_text)
            except (ValueError, UnicodeDecodeError):
                await reject(400, "Niepoprawny Content-Length.")
                return
            if length > MAX_BODY_BYTES:
                await reject(413, "Treść żądania przekracza limit 16 KiB.")
                return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                await reject(400, "Niepoprawna treść żądania.")
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                await reject(413, "Treść żądania przekracza limit 16 KiB.")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, guarded_send)


def get_settings(request: Request) -> Settings:
    settings = request.app.state.settings
    try:
        settings = settings if settings is not None else Settings.from_env()
        ZoneInfo(settings.timezone)
    except (RuntimeError, ZoneInfoNotFoundError, ValueError):
        raise HTTPException(503, "Konfiguracja lokalnego API nie jest gotowa.") from None
    return settings


def reader_engine(settings: Annotated[Settings, Depends(get_settings)]) -> Engine:
    try:
        url = make_url(settings.database_url)
        if url.username != "monitor_reader" or url.get_backend_name() != "postgresql":
            raise HTTPException(503, "API wymaga skonfigurowanej roli PostgreSQL monitor_reader.")
        return get_engine(settings.database_url)
    except (SQLAlchemyError, ValueError):
        raise HTTPException(503, "Połączenie z lokalną bazą nie jest skonfigurowane poprawnie.") from None


@contextmanager
def read_connection(engine: Engine):
    with engine.connect() as connection:
        connection.exec_driver_sql(READ_TRANSACTION)
        yield connection


def event_query(request: Request) -> EventQuery:
    values: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key in values:
            raise HTTPException(422, f"Parametr {key} został podany więcej niż raz.")
        values[key] = value
    # Publication/validity histories include completed records unless explicitly filtered.
    if values.get("time_basis") in {"changed", "published", "validity"} and "include_inactive" not in values:
        values["include_inactive"] = "true"
    try:
        query = EventQuery.model_validate(values)
    except ValidationError as exc:
        raise HTTPException(422, [
            {"loc": ["query", *error["loc"]], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors(include_input=False, include_context=False, include_url=False)
        ]) from None
    until = query.until or utcnow()
    since = query.since or until - timedelta(hours=query.window_hours)
    if since >= until or until - since > timedelta(hours=720):
        raise HTTPException(422, "Przedział musi być dodatni i nie dłuższy niż 720 godzin.")
    return query



def add_cited_relations(connection, result: dict) -> None:
    """Read only edges between narrative facts, before the caller closes its snapshot."""
    fact_ids = {fact["event_id"] for fact in result["facts"]}
    edges = db.relations_for_ids(connection, sorted(fact_ids))
    context = [{"id": event_id, "relations": relations} for event_id, relations in edges.items()]
    result["inferences"] = _relations(context, fact_ids)
    if sum(len(relations) for relations in edges.values()) > 20:
        result["limitations"] = _unique([
            *result["limitations"],
            "Część opisowa pokazuje maksymalnie 10 relacji między cytowanymi faktami. Relacja nie oznacza przyczyny ani dodatkowego potwierdzenia.",
        ])


def create_app(settings: Settings | None = None, *, testing: bool = False) -> FastAPI:
    application = FastAPI(
        title="Sextet Monitor", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
    )
    application.state.settings = settings
    application.add_middleware(
        LocalRequestGuard, testing=testing, allowed_origins=settings.allowed_origins if settings else None,
    )

    @application.exception_handler(RequestValidationError)
    async def input_error(_request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]})

    @application.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, _exc: SQLAlchemyError):
        # No connection strings, SQL parameters or provider credentials in errors.
        return JSONResponse(status_code=503, content={"detail": "Lokalna baza jest niedostępna. Spróbuj ponownie."})

    @application.exception_handler(ValidationError)
    @application.exception_handler(ResponseValidationError)
    async def output_error(_request: Request, _exc: ResponseValidationError):
        return JSONResponse(status_code=500, content={"detail": "Dane nie odpowiadają kontraktowi odpowiedzi API."})

    @application.get("/api/health", response_model=HealthResponse)
    def health(request: Request):
        timezone_name = settings.timezone if settings else "Europe/Warsaw"
        result = {
            "status": "ok", "version": __version__, "database": "ok",
            "ai_mode": "off", "timezone": timezone_name,
        }
        try:
            configured = get_settings(request)
            result["timezone"] = configured.timezone
            engine = reader_engine(configured)
            with read_connection(engine) as connection:
                connection.execute(text("SELECT 1")).scalar_one()
        except (SQLAlchemyError, HTTPException):
            result.update(status="error", database="unavailable")
            return JSONResponse(result, status_code=503)
        return result

    @application.get("/api/sources", response_model=SourceList)
    def sources(engine: Annotated[Engine, Depends(reader_engine)]):
        now = utcnow()
        with read_connection(engine) as connection:
            return {"items": db.get_source_health(connection, now), "generated_at": now}

    @application.get("/api/events", response_model=EventList)
    def events(
        query: Annotated[EventQuery, Depends(event_query)],
        engine: Annotated[Engine, Depends(reader_engine)],
    ):
        with read_connection(engine) as connection:
            return db.select_events(connection, query, now=utcnow())

    @application.get("/api/events/{event_id}", response_model=EventDetail)
    def detail(event_id: UUID, engine: Annotated[Engine, Depends(reader_engine)]):
        with read_connection(engine) as connection:
            found = db.event_detail(connection, str(event_id), now=utcnow())
        if found is None:
            raise HTTPException(404, "Nie znaleziono zdarzenia.")
        return found

    @application.post("/api/query", response_model=QueryResponse)
    def query(
        payload: QueryRequest,
        configured: Annotated[Settings, Depends(get_settings)],
        engine: Annotated[Engine, Depends(reader_engine)],
    ):
        now = utcnow()
        interpreted = parse_question(payload.question, now=now, timezone_name=configured.timezone)
        with read_connection(engine) as connection:
            if not interpreted.supported or interpreted.query is None:
                return build_answer(payload.question, interpreted, [], db.get_source_health(connection, now), now)
            snapshot = db.select_events(connection, interpreted.query, now=now)
            interpreted = interpreted.model_copy(update={
                "limitations": _unique([*interpreted.limitations, *snapshot.get("limitations", [])]),
            })
            result = build_answer(payload.question, interpreted, snapshot["items"], snapshot["source_health"], now)
            add_cited_relations(connection, result)
        result.update(total=snapshot["total"], shown=snapshot["shown"], truncated=snapshot["truncated"])
        if snapshot["truncated"]:
            result["limitations"] = _unique([
                *result["limitations"],
                f"Wynik ograniczony: pokazano {snapshot['shown']} z {snapshot['total']} dopasowań. Zawęź zakres, aby zobaczyć pozostałe.",
            ])
        return result

    @application.get("/api/briefings/latest", response_model=BriefingResponse | None)
    def latest(engine: Annotated[Engine, Depends(reader_engine)]):
        with read_connection(engine) as connection:
            return db.latest_briefing(connection)

    @application.post("/api/briefings", response_model=BriefingResponse)
    def briefings(payload: BriefingRequest, engine: Annotated[Engine, Depends(reader_engine)]):
        with engine.begin() as connection:
            # A writer timestamps changes before committing. Wait for that commit
            # before sampling the cursor, then read a fresh statement snapshot.
            # REPEATABLE READ would retain the pre-wait snapshot taken by SELECT lock.
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            connection.execute(text("SELECT pg_advisory_xact_lock(61704001)"))
            until = utcnow()
            previous = db.latest_briefing(
                connection, country=payload.country, window_hours=payload.window_hours,
            )
            first = previous is None
            since = until - timedelta(hours=payload.window_hours) if first else _datetime(previous.get("until"))
            if since is None or since >= until:
                raise HTTPException(409, "Poprzedni briefing ma niespójny kursor czasu; nie zapisano nowego punktu.")
            query = EventQuery(
                window_hours=payload.window_hours, time_basis="changed", since=since, until=until,
                country=payload.country, include_inactive=True, limit=1000,
            )
            snapshot = db.select_briefing_events(connection, query, first_briefing=first, now=until, stream=True)
            items = snapshot["items"]
            try:
                result = build_briefing(items, snapshot["source_health"], since, until, first_briefing=first)
            finally:
                if hasattr(items, "close"):
                    items.close()
            if snapshot["truncated"] or result["processed_count"] != snapshot["total"]:
                raise HTTPException(
                    409,
                    "Nie odczytano całego spójnego zakresu zmian. "
                    "Nie zapisano briefingu ani nie przesunięto kursora; spróbuj ponownie.",
                )
            add_cited_relations(connection, result)
            background_count = snapshot.get("initial_import_background_count", 0)
            if background_count:
                result["sections"].append({
                    "title": f"Import historyczny pominięty w zapytaniu: {background_count} rekordów",
                    "items": [],
                })
                result["answer"] += f" Dodatkowo {background_count} rekordów pierwszego importu zachowano wyłącznie jako tło."
                result["limitations"].append(
                    f"{background_count} historycznych lub niedatowanych rekordów pierwszego importu nie jest liczbą nowych zdarzeń ani ataków."
                )
            result["limitations"] = _unique([*result["limitations"], *snapshot.get("limitations", [])])
            result.update(
                scope=payload.model_dump(), first_briefing=first, total=snapshot["total"], shown=len(result["facts"]),
                truncated=result["omitted_fact_count"] > 0, initial_import_background_count=background_count,
            )
            saved = db.save_briefing(
                connection, result, country=payload.country, window_hours=payload.window_hours,
            )
            # Validate before leaving the transaction, so invalid output rolls back.
            return BriefingResponse.model_validate(saved)

    return application


app = create_app()
