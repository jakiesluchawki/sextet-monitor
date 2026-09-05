import assert from "node:assert/strict";
import test from "node:test";
import type { Category, EventDetail, SourceStatus } from "../lib/contracts";
import { PUBLIC_SOURCE_IDS, PUBLIC_SOURCE_INFO, PUBLIC_TIME_BASIS, type PublicSnapshot } from "../lib/public-snapshot";
import { advisoryState, buildSituation, createBriefingText, formatSituationTime, SITUATION_CATEGORIES } from "../lib/situation";
import { getScopeCountries, getScopeLabel, type ScopeId } from "../lib/areas";

const AT = "2026-09-05T12:00:00Z", NOW = Date.parse(AT), HOUR = 3_600_000;
function event(overrides: Partial<EventDetail> = {}): EventDetail {
  const base: EventDetail = {
    id: "event-1", kind: "incident", category: "earthquake", title: "Original source title", description: "Source description",
    occurred_start: "2026-09-05T10:00:00Z", occurred_end: null, issued_at: null, source_updated_at: null,
    first_seen_at: AT, last_seen_at: AT, last_changed_at: AT, valid_from: null, valid_to: null,
    countries: [], geometry: null, location_precision: "unknown", time_precision: "second",
    severity: 0, severity_label: "nieokreślona", severity_reason: "Nie podano", original_severity: null,
    lifecycle_status: "active", verification_status: "reported", anomaly_score: null,
    source_ids: ["usgs"], source_count: 1, independent_source_count: 1,
    source_url: "https://earthquake.usgs.gov/earthquakes/eventpage/fixture", tags: [], change_type: "initial_import",
    evidence: [{ id: "evidence-1", source_id: "usgs", source_name: "USGS", provider_record_id: "fixture",
      source_url: "https://earthquake.usgs.gov/earthquakes/eventpage/fixture", retrieved_at: AT, issued_at: null,
      source_updated_at: null, origins: ["usgs"], payload_hash: "fixture", raw: null, raw_retained: false,
      attribution: "USGS", license_url: null }], revisions: [], relations: [],
  };
  return { ...base, ...overrides };
}
function source(id: typeof PUBLIC_SOURCE_IDS[number], overrides: Partial<SourceStatus> = {}): SourceStatus {
  return { id, name: PUBLIC_SOURCE_INFO[id].name, status: "ok", enabled: true, requires_key: false,
    last_attempt_at: AT, last_success_at: AT, newest_content_at: AT, next_due_at: null, record_count: 1,
    error: null, poll_interval_seconds: 3600, coverage: "Fixture", license_name: "Fixture", license_url: null,
    attribution: "Fixture", ...overrides };
}
function snapshot(events: EventDetail[], overrides: Partial<PublicSnapshot> = {}): PublicSnapshot {
  return { format: 1, version: "fixture", generated_at: AT, sources: PUBLIC_SOURCE_IDS.map((id) => source(id)),
    events, limitations: [], ...overrides };
}
function forCategory(category: Category, id: string = category): EventDetail {
  return event({ id, category, kind: ["weather", "aviation"].includes(category) ? "advisory" : category === "cyber" ? "vulnerability_notice" : "incident",
    issued_at: "2026-09-05T10:00:00Z", valid_from: "2026-09-05T10:00:00Z", valid_to: "2026-09-05T18:00:00Z" });
}
function freezeDeep(value: object): void {
  for (const item of Object.values(value)) if (item !== null && typeof item === "object") freezeDeep(item);
  Object.freeze(value);
}

test("all selected events are counted beyond the ordinary 300 row limit", () => {
  const saved = snapshot(Array.from({ length: 901 }, (_, index) => event({ id: `e-${index}`, title: `Source title ${index}` })));
  const result = buildSituation(saved, { scope: "world", hours: 24 });
  assert.equal(result.events.length, 901);
  assert.equal(result.categoryCounts.find((item) => item.category === "earthquake")?.count, 901);
  assert.equal(result.kindCounts.find((item) => item.kind === "incident")?.count, 901);
  assert.equal(result.unlocated, 901);
  assert.equal(result.highlights.length, 8);
});

test("scope definitions are explicit and unknown or geometric-only countries do not become regional", () => {
  assert.equal(getScopeCountries("world"), null);
  assert.equal(getScopeLabel("poland"), "Polska");
  assert.equal(getScopeCountries("europe")?.includes("PL"), true);
  assert.equal(getScopeCountries("europe")?.includes("CY"), true);
  assert.equal(getScopeCountries("europe")?.includes("TR"), false);
  const saved = snapshot([
    event({ id: "pl", countries: ["PL"] }), event({ id: "tr", countries: ["TR"] }),
    event({ id: "de", countries: ["DE"] }), event({ id: "ru", countries: ["RU"] }),
    event({ id: "global", geometry: { type: "Point", coordinates: [21, 52] } }),
    event({ id: "lower", countries: ["pl"] }), event({ id: "multiple", countries: ["TR", "PL"] }),
  ]);
  assert.deepEqual(buildSituation(saved, { scope: "poland", hours: 24 }).events.map((item) => item.id), ["multiple", "pl"]);
  assert.deepEqual(buildSituation(saved, { scope: "country:TR", hours: 24 }).events.map((item) => item.id), ["multiple", "tr"]);
  assert.equal(buildSituation(saved, { scope: "europe", hours: 24 }).events.length, 3);
  assert.equal(buildSituation(saved, { scope: "world", hours: 24 }).events.length, 7);
  const regional = buildSituation(saved, { scope: "poland", hours: 24 });
  assert.equal(regional.unknownCountryCount, 1);
  assert.match(regional.limitations.join(" "), /globalne usługi.*nie są automatycznie/);
});

test("the snapshot clock fixes the time window independently of the device clock", () => {
  const saved = snapshot([event()]);
  const first = buildSituation(saved, { scope: "world", hours: 24 });
  const later = buildSituation(saved, { scope: "world", hours: 24, now: NOW + 10 * HOUR });
  assert.equal(first.until, AT);
  assert.equal(first.since, "2026-09-04T12:00:00.000Z");
  assert.equal(later.until, AT);
  assert.deepEqual(first.events, later.events);
  assert.deepEqual(first.highlights, later.highlights);
  assert.deepEqual(first.timeline, later.timeline);
  assert.match(later.limitations.join(" "), /Zestaw ma 10 godzin/);
  assert.equal(later.sourceWarnings.length, PUBLIC_SOURCE_IDS.length);
});

test("a clock behind the snapshot produces a warning, not another interpretation", () => {
  const saved = snapshot([event()]);
  const result = buildSituation(saved, { scope: "world", hours: 24, now: NOW - 2 * HOUR });
  assert.equal(result.events.length, 1);
  assert.match(result.limitations.join(" "), /Zegar urządzenia/);
  assert.match(result.sourceWarnings[0], /wyprzedza zegar/);
});

test("category time bases never fall back to retrieval or modification clocks", () => {
  const records = SITUATION_CATEGORIES.map((category) => forCategory(category));
  const result = buildSituation(snapshot(records), { scope: "world", hours: 24 });
  assert.equal(result.events.length, 7);
  assert.deepEqual(result.categoryCounts.map(({ category, timeBasis }) => [category, timeBasis]), SITUATION_CATEGORIES.map((category) => [category, PUBLIC_TIME_BASIS[category]]));
  const missing = SITUATION_CATEGORIES.map((category) => forCategory(category));
  for (const record of missing) {
    record.occurred_start = null; record.issued_at = null; record.valid_from = null;
    record.source_updated_at = AT; record.last_changed_at = AT;
  }
  const unknown = buildSituation(snapshot(missing), { scope: "world", hours: 24 });
  assert.equal(unknown.events.length, 0);
  assert.equal(unknown.unknownTimeCount, 7);
  assert.equal(unknown.undatedEvents.length, 7);
  assert.match(unknown.limitations.join(" "), /nieznany lub niespójny wymagany czas/);
});

test("undated records stay visible separately without entering regional time counts or briefing", () => {
  const hydro = event({ id: "undated-pl", title: "Undated hydrology fixture", category: "weather", kind: "advisory",
    countries: ["PL"], valid_from: null, valid_to: null, lifecycle_status: "unknown", source_ids: ["imgw_hydro"] });
  const saved = snapshot([hydro, { ...hydro, id: "undated-ua", countries: ["UA"] },
    { ...hydro, id: "undated-global", countries: [] }, event({ id: "dated-pl", countries: ["PL"] })]);
  freezeDeep(saved);
  const result = buildSituation(saved, { scope: "poland", hours: 24 });
  assert.deepEqual(result.undatedEvents.map(({ id }) => id), ["undated-pl"]);
  assert.deepEqual(result.events.map(({ id }) => id), ["dated-pl"]);
  assert.equal(result.activeAdvisories, 0);
  assert.equal(result.categoryCounts.find(({ category }) => category === "weather")?.count, 0);
  assert.equal(result.timeline.reduce((sum, bin) => sum + bin.count, 0), 1);
  const text = createBriefingText(result, saved, [hydro.id]);
  assert.match(text, /Pominięto 1/);
  assert.doesNotMatch(text, /Undated hydrology fixture/);
  assert.deepEqual(buildSituation(saved, { scope: "poland", hours: 168 }).undatedEvents, result.undatedEvents);
});

test("historical KEV entries with a new ingestion clock do not appear as recent events", () => {
  const old = Array.from({ length: 1600 }, (_, index) => event({ id: `cisa-${index}`, category: "cyber", issued_at: "2020-01-01T00:00:00Z", time_precision: "day" }));
  const result = buildSituation(snapshot([...old, forCategory("cyber", "recent-kev"), event({ id: "recent-earthquake" })]), { scope: "world", hours: 24 });
  assert.equal(result.events.length, 2);
  assert.equal(result.highlights.length, 2);
  assert.ok(result.highlights.some(({ event }) => event.id === "recent-kev"));
});

test("a half-open occurrence window includes its beginning and excludes its end and future", () => {
  const result = buildSituation(snapshot([
    event({ id: "start", occurred_start: "2026-09-04T12:00:00Z" }),
    event({ id: "end", occurred_start: AT }),
    event({ id: "future", occurred_start: "2026-09-06T01:00:00Z" }),
    event({ id: "old", occurred_start: "2026-09-04T11:59:59Z" }),
  ]), { scope: "world", hours: 24 });
  assert.deepEqual(result.events.map((item) => item.id), ["start"]);
});

test("daily precision intersects the source day without inventing midnight as an exact publication", () => {
  const record = forCategory("cyber");
  record.issued_at = "2026-09-05T00:00:00Z"; record.time_precision = "day"; record.tags = ["date_only_utc_anchor"];
  const result = buildSituation(snapshot([record]), { scope: "world", hours: 1 });
  assert.equal(result.events.length, 1);
  assert.equal(result.timeline[0].byBasis.published, 1);
  assert.match(formatSituationTime(record, "published"), /05.09.2026/);
  assert.doesNotMatch(formatSituationTime(record, "published"), /00:00|02:00/);
});

test("offsets are compared as instants for selection and not as lexical strings", () => {
  const saved = snapshot([
    event({ id: "inside", occurred_start: "2026-09-05T13:00:00+02:00" }),
    event({ id: "outside", occurred_start: "2026-09-05T11:00:00-02:00" }),
  ]);
  assert.deepEqual(buildSituation(saved, { scope: "world", hours: 24 }).events.map((item) => item.id), ["inside"]);
});

test("validity overlap includes an older currently active advisory and recent expired advisory distinctly", () => {
  const active = forCategory("aviation", "active"), expired = forCategory("weather", "expired");
  active.valid_from = "2026-08-01T00:00:00Z"; active.issued_at = "2026-08-01T00:00:00Z";
  expired.valid_to = "2026-09-05T11:00:00Z";
  const result = buildSituation(snapshot([active, expired]), { scope: "world", hours: 24 });
  assert.equal(result.events.length, 2); assert.equal(result.activeAdvisories, 1); assert.equal(result.expiredAdvisories, 1);
  assert.equal(advisoryState(active, AT), "active"); assert.equal(advisoryState(expired, AT), "expired");
  assert.match(result.highlights.find(({ event }) => event.id === "expired")!.reason, /wygasło/);
  assert.equal(result.highlights[0].event.id, "expired");
});

test("invalid, disjoint and boundary-touching validity periods are not included", () => {
  const weather = (id: string, start: string | null, end: string | null) => ({ ...forCategory("weather", id), valid_from: start, valid_to: end });
  const result = buildSituation(snapshot([
    weather("old", "2026-09-03T12:00:00Z", "2026-09-04T12:00:00Z"),
    weather("future", AT, "2026-09-06T12:00:00Z"),
    weather("reversed", "2026-09-05T11:00:00Z", "2026-09-05T10:00:00Z"),
    weather("no-start", null, "2026-09-06T12:00:00Z"),
  ]), { scope: "world", hours: 24 });
  assert.equal(result.events.length, 0); assert.equal(result.unknownTimeCount, 2);
});

test("an active source can have an unknown end; unknown or expired status does not make it continue forever", () => {
  const open = { ...forCategory("aviation", "open"), valid_from: "2026-08-01T00:00:00Z", valid_to: null };
  const result = buildSituation(snapshot([
    open,
    { ...open, id: "old-unknown", lifecycle_status: "unknown" },
    { ...open, id: "old-expired", lifecycle_status: "expired" },
    { ...open, id: "recent-unknown", lifecycle_status: "unknown", valid_from: "2026-09-05T10:00:00Z" },
  ]), { scope: "world", hours: 24 });
  assert.deepEqual(result.events.map(({ id }) => id), ["recent-unknown", "open"]);
  assert.equal(result.activeAdvisories, 1);
  assert.equal(advisoryState(result.events[0], AT), "unknown");
  assert.match(result.limitations.join(" "), /nie ma końca ważności/);
  assert.match(buildSituation(snapshot([open]), { scope: "world", hours: 24 }).highlights[0].reason, /Końca ważności nie ustalono/);
  const untilRevoked = { ...open, tags: ["until_revoked"] };
  assert.match(formatSituationTime(untilRevoked, "validity"), /do odwołania według źródła/);
  assert.match(buildSituation(snapshot([untilRevoked]), { scope: "world", hours: 24 }).highlights[0].reason, /ważność do odwołania/);
  assert.equal(buildSituation(snapshot([{ ...untilRevoked, lifecycle_status: "unknown" }]), { scope: "world", hours: 24 }).events.length, 0);
});

test("withdrawn advisories remain withdrawn despite validity bounds, and missing start stays unknown", () => {
  const withdrawn = { ...forCategory("weather"), lifecycle_status: "withdrawn" as const };
  assert.equal(advisoryState(withdrawn, AT), "withdrawn");
  assert.equal(advisoryState({ ...withdrawn, lifecycle_status: "active", valid_from: null }, AT), "unknown");
  assert.equal(buildSituation(snapshot([withdrawn]), { scope: "world", hours: 24 }).withdrawnAdvisories, 1);
  assert.equal(advisoryState({ ...withdrawn, lifecycle_status: "active", valid_from: "2026-09-06T00:00:00Z", valid_to: "2026-09-07T00:00:00Z" }, AT), "upcoming");
});

test("timeline counts interval overlaps, not fabricated starts, and every count has a time basis", () => {
  const weather = { ...forCategory("weather"), valid_from: "2026-09-03T10:00:00Z", valid_to: "2026-09-06T10:00:00Z" };
  const quake = event({ occurred_start: "2026-09-05T11:00:00Z" });
  const result = buildSituation(snapshot([weather, quake]), { scope: "world", hours: 3 });
  assert.equal(result.timeline.length, 3);
  assert.deepEqual(result.timeline.map((bin) => bin.count), [1, 1, 2]);
  assert.equal(result.timeline.reduce((total, bin) => total + bin.byBasis.occurred, 0), 1);
  assert.equal(result.timeline.reduce((total, bin) => total + bin.byBasis.validity, 0), 3);
  assert.ok(result.timeline.every((bin) => Object.values(bin.byBasis).reduce((sum, count) => sum + count, 0) === bin.count));
  assert.match(result.limitations.join(" "), /suma słupków nie jest liczbą unikalnych/);
});

test("a nonstandard window is covered by contiguous bounded timeline bins", () => {
  const result = buildSituation(snapshot([]), { scope: "world", hours: 25 });
  assert.equal(result.timeline[0].start, result.since);
  assert.equal(Date.parse(result.timeline.at(-1)!.end), Date.parse(result.until));
  for (let index = 1; index < result.timeline.length; index++) assert.equal(result.timeline[index].start, result.timeline[index - 1].end);
  assert.ok(result.timeline.every((bin) => Date.parse(bin.start) < Date.parse(bin.end)));
});

test("geometry counts preserve exact provider points and polygons without fabricated centroids", () => {
  const point = event({ id: "point", geometry: { type: "Point", coordinates: [21, 52] } });
  const polygon = event({ id: "polygon", geometry: { type: "Polygon", coordinates: [[[20, 50], [22, 50], [22, 52], [20, 50]]] } });
  const result = buildSituation(snapshot([point, polygon, event(), event({ id: "bad", geometry: { type: "Point", coordinates: [NaN, 52] } })]), { scope: "world", hours: 24 });
  assert.equal(result.mapped, 2); assert.equal(result.unlocated, 2);
  assert.equal(result.events.find(({ id }) => id === "polygon")?.geometry, polygon.geometry);
});

test("highlights are bounded and diverse even with many recent high-volume records", () => {
  const records = Array.from({ length: 1000 }, (_, index) => event({ id: `q-${index}`, title: `Quake ${index}`, severity: 4, original_severity: "M7" }));
  records.push(...SITUATION_CATEGORIES.filter((category) => category !== "earthquake").map((category) => forCategory(category)));
  const result = buildSituation(snapshot(records), { scope: "world", hours: 24 });
  assert.equal(result.highlights.length, 8);
  assert.equal(new Set(result.highlights.map(({ event }) => event.category)).size, 7);
  assert.ok(result.highlights.every(({ event, reason, timeBasis }) => event.evidence.length && reason && timeBasis === PUBLIC_TIME_BASIS[event.category]));
});

test("source weight is used only with original severity; no guessed risk or causes enter reasons", () => {
  const result = buildSituation(snapshot([
    event({ id: "unknown-original", title: "Unknown original", severity: 4 }),
    event({ id: "source-high", title: "Source high", severity: 3, original_severity: "Orange" }),
  ]), { scope: "world", hours: 24 });
  assert.equal(result.highlights[0].event.id, "source-high");
  assert.match(result.highlights[0].reason, /Wysoka waga według źródła/);
  assert.doesNotMatch(result.highlights[1].reason, /Wysoka/);
  assert.ok(result.highlights.every(({ reason }) => !/AI|prawdopodobieństwo|przyczyn/i.test(reason)));
});

test("old EASA validity is contextual, not a new event that displaces recent evidence", () => {
  const old = Array.from({ length: 400 }, (_, index) => ({ ...forCategory("aviation", `easa-${index}`),
    issued_at: "2026-01-01T00:00:00Z", valid_from: "2026-01-01T00:00:00Z", valid_to: "2026-10-01T00:00:00Z", severity: 4, original_severity: "Do not fly" }));
  const result = buildSituation(snapshot([...old, event({ id: "recent" }), forCategory("weather")]), { scope: "world", hours: 24 });
  assert.equal(result.highlights[0].event.id, "recent");
  assert.ok(result.highlights.some(({ event }) => event.id === "weather"));
  assert.equal(result.highlights.filter(({ event }) => event.category === "aviation").length, 1);
  assert.ok(result.highlights.filter(({ event }) => event.category === "aviation").every(({ reason }) => /Ważne w chwili/.test(reason)));
});

test("records without matched source evidence are counted but never auto-highlighted", () => {
  const base = event();
  const result = buildSituation(snapshot([
    { ...base, id: "none", evidence: [] },
    { ...base, id: "mismatch", evidence: [{ ...base.evidence[0], source_id: "nasa_eonet" }] },
    { ...base, id: "future", evidence: [{ ...base.evidence[0], retrieved_at: "2026-09-06T12:00:00Z" }] },
  ]), { scope: "world", hours: 24 });
  assert.equal(result.events.length, 3); assert.equal(result.highlights.length, 0);
  assert.match(result.limitations.join(" "), /bez pasujących dowodów/);
});

test("explicit possible-same-event relations and repeated titles do not fill the highlight list or merge the underlying records", () => {
  const first = event({ id: "a", title: "Original earthquake" });
  const related = event({ id: "b", title: "Aggregated earthquake", relations: [{ event_id: "a", title: first.title,
    relation_type: "possible_same_event", reason: "Existing source relation, not confirmed", distance_km: 0, time_delta_hours: 0 }] });
  const repeat = event({ id: "c", title: first.title });
  const another = event({ id: "d", title: "Another earthquake" });
  const result = buildSituation(snapshot([related, repeat, another, first]), { scope: "world", hours: 24 });
  assert.equal(result.events.length, 4);
  assert.deepEqual(result.highlights.map(({ event }) => event.id), ["a", "d"]);
  assert.match(result.limitations.join(" "), /Nie scala to rekordów ani nie potwierdza/);
});

test("health warnings distinguish partial, failed, missing, old and successful empty sources", () => {
  const saved = snapshot([event({ tags: ["cached_public_data"] })], { sources: [
    source("usgs", { status: "error", last_success_at: "2026-09-01T12:00:00Z" }),
    source("nasa_eonet", { status: "partial" }), source("github_status", { last_success_at: null }),
    source("cloudflare_status", { status: "ok_empty", record_count: 0 }),
  ] });
  const result = buildSituation(saved, { scope: "world", hours: 24 });
  assert.equal(result.cachedCount, 1);
  assert.equal(result.sourceWarnings.filter((warning) => warning.includes("brak metadanych")).length, PUBLIC_SOURCE_IDS.length - saved.sources.length);
  assert.match(result.sourceWarnings.find((warning) => warning.startsWith("USGS"))!, /błąd pobierania.*opóźniony/);
  assert.match(result.sourceWarnings.find((warning) => warning.startsWith("NASA"))!, /częściowe dane/);
  assert.match(result.sourceWarnings.find((warning) => warning.startsWith("GitHub"))!, /brak daty/);
  assert.ok(!result.sourceWarnings.some((warning) => warning.startsWith("Cloudflare")));
  assert.match(result.limitations.join(" "), /poprzedniego publicznego odczytu/);
});

test("inputs remain unchanged, frozen inputs work, ordering is deterministic across input orders", () => {
  const saved = snapshot([event({ id: "z" }), event({ id: "a" }), forCategory("cyber")]);
  const before = JSON.stringify(saved); freezeDeep(saved);
  const first = buildSituation(saved, { scope: "world", hours: 24 });
  const reversed = buildSituation({ ...saved, events: [...saved.events].reverse() }, { scope: "world", hours: 24 });
  assert.equal(JSON.stringify(saved), before);
  assert.deepEqual(first, reversed);
  assert.deepEqual(first, buildSituation(saved, { scope: "world", hours: 24 }));
});

test("malformed scope, clock and out-of-range windows fail explicitly", () => {
  const saved = snapshot([]);
  for (const hours of [0, -1, NaN, Infinity, 1.5, 721]) assert.throws(() => buildSituation(saved, { scope: "world", hours }));
  assert.throws(() => buildSituation(saved, { scope: "__proto__" as "world", hours: 24 }));
  assert.throws(() => buildSituation(saved, { scope: "world", hours: 24, now: NaN }));
  assert.throws(() => buildSituation({ ...saved, generated_at: "unknown" }, { scope: "world", hours: 24 }));
});

test("briefing defaults to highlights but an explicit empty selection remains empty", () => {
  const saved = snapshot([event()]), result = buildSituation(saved, { scope: "world", hours: 24 });
  assert.match(createBriefingText(result, saved), /1\. Original source title/);
  const empty = createBriefingText(result, saved, []);
  assert.match(empty, /Nie wybrano rekordów/); assert.doesNotMatch(empty, /1\. Original source title/);
});

test("briefing selection deduplicates, bounds to twelve, and does not leak records outside the scope", () => {
  const saved = snapshot(Array.from({ length: 20 }, (_, index) => event({ id: `pl-${index}`, title: `Item ${index}`, countries: ["PL"] })));
  saved.events.push(event({ id: "outside", title: "OUTSIDE SECRET", countries: ["TR"] }));
  const result = buildSituation(saved, { scope: "poland", hours: 24 });
  const text = createBriefingText(result, saved, [...saved.events.map(({ id }) => id), "pl-1", "missing"]);
  assert.match(text, /Wybrano do briefingu: 12 z 20; limit 12/);
  assert.match(text, /Pominięto 2 wybranych identyfikatorów/);
  assert.doesNotMatch(text, /OUTSIDE SECRET|13\. Item/);
});

test("briefing keeps original titles, known dates and explicit unknowns without false independence", () => {
  const record = forCategory("cyber"); record.occurred_start = null; record.title = "Original Vendor Security Advisory";
  record.source_count = 2; record.independent_source_count = 1; record.source_ids = ["usgs", "nasa_eonet"];
  const saved = snapshot([record]), result = buildSituation(saved, { scope: "world", hours: 24 });
  const text = createBriefingText(result, saved);
  assert.match(text, /Original Vendor Security Advisory/);
  assert.match(text, /Czas wystąpienia: Nie ustalono/);
  assert.match(text, /Kraje według źródła: nie ustalono/);
  assert.match(text, /Nie są niezależnymi potwierdzeniami/);
  assert.doesNotMatch(text, /2 niezależne/);
  assert.match(text, /Europe\/Warsaw/);
});

test("briefing exports only safe HTTP links without executing source title control characters", () => {
  const record = event({ title: "Title\nFake heading\u0000", source_url: "javascript:alert(1)" });
  record.evidence[0].source_url = "https://user:secret@example.com";
  const saved = snapshot([record]), result = buildSituation(saved, { scope: "world", hours: 24 });
  const text = createBriefingText(result, saved, undefined, "data:text/html,bad");
  assert.match(text, /1\. Title Fake heading \[Zdarzenie\]/);
  assert.match(text, /Oryginalny adres: nie ustalono/);
  assert.doesNotMatch(text, /javascript:|user:secret|data:text|\u0000/);
  assert.match(createBriefingText(result, saved, undefined, "https://example.com/monitor"), /Pełny monitor: https:\/\/example.com\/monitor/);
});

test("briefing explicitly records expired status, daily end boundaries, cache and stale warnings", () => {
  const record = { ...forCategory("aviation"), issued_at: "2026-09-04T00:00:00Z", valid_from: "2026-09-04T00:00:00Z",
    valid_to: "2026-09-05T00:00:00Z", time_precision: "day", tags: ["valid_to_exclusive_day_boundary", "cached_public_data"] };
  const saved = snapshot([record]), result = buildSituation(saved, { scope: "world", hours: 24, now: NOW + 8 * HOUR });
  const text = createBriefingText(result, saved);
  assert.match(text, /Status: wygasłe/);
  assert.match(text, /04.09.2026 \(koniec dnia\)/);
  assert.match(text, /dane z wcześniejszego publicznego odczytu/);
  assert.match(text, /Zestaw ma 8 godzin/);
  assert.match(text, /ostatni odczyt jest opóźniony/);
});

test("a briefing cannot pair a situation and snapshot from different preparation clocks", () => {
  const saved = snapshot([event()]), result = buildSituation(saved, { scope: "world", hours: 24 });
  assert.throws(() => createBriefingText(result, { ...saved, generated_at: "2026-09-05T13:00:00Z" }));
});

test("a resolved incident in the briefing is explicitly completed rather than active or an expired advisory", () => {
  const incident = event({ category: "internet", kind: "incident", title: "Resolved service interruption",
    issued_at: "2026-09-05T10:00:00Z", occurred_end: "2026-09-05T11:00:00Z", lifecycle_status: "expired" });
  const saved = snapshot([incident]), result = buildSituation(saved, { scope: "world", hours: 24 });
  const text = createBriefingText(result, saved);
  assert.match(text, /Stan rekordu w zestawie: zakończone\./);
  assert.doesNotMatch(text, /Stan rekordu w zestawie: aktywne|Status: ważne|Stan rekordu w zestawie: wygasłe/);
  assert.equal(result.activeAdvisories, 0);
});

test("any supported country scope filters explicit source codes without assigning unknown or neighboring records", () => {
  const saved = snapshot([
    event({ id: "japan", countries: ["JP"] }), event({ id: "france", countries: ["FR"] }),
    event({ id: "kosovo", countries: ["XK"] }), event({ id: "guadeloupe", countries: ["GP"] }),
    event({ id: "unlocated", geometry: { type: "Point", coordinates: [139.7, 35.7] } }),
  ]);
  for (const [scope, id] of [["country:JP", "japan"], ["country:FR", "france"], ["country:XK", "kosovo"], ["country:GP", "guadeloupe"]] as const) {
    const result = buildSituation(saved, { scope, hours: 24 });
    assert.deepEqual(result.events.map(({ id }) => id), [id]);
    assert.equal(result.scope, scope);
    assert.equal(result.scopeLabel, getScopeLabel(scope));
    assert.match(createBriefingText(result, saved), /Zakres obejmuje wyłącznie jawne kody krajów/);
  }
});

test("situation canonicalizes Poland and old Turkey scopes and rejects unsupported country scopes", () => {
  const saved = snapshot([event({ id: "pl", countries: ["PL"] }), event({ id: "tr", countries: ["TR"] })]);
  const poland = buildSituation(saved, { scope: "country:pl", hours: 24 });
  assert.equal(poland.scope, "poland");
  assert.deepEqual(poland.events.map(({ id }) => id), ["pl"]);
  const turkey = buildSituation(saved, { scope: "turkey" as ScopeId, hours: 24 });
  assert.equal(turkey.scope, "country:TR");
  assert.equal(turkey.scopeLabel, "Turcja");
  assert.deepEqual(turkey.events.map(({ id }) => id), ["tr"]);
  for (const scope of ["country:ZZ", "country:EU", "country:UK", "country:__proto__", "country:", "country:TR,PL"] as ScopeId[]) {
    assert.throws(() => buildSituation(saved, { scope, hours: 24 }), /Nieznany zakres/);
  }
});
