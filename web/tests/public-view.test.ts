import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { EventDetail } from "../lib/contracts";
import { eventSourceNames, filterViewEvents, snapshotCalendarDay } from "../lib/public-view";
import SignalRows, { PinButton } from "../components/SignalRows";

test("snapshot stamp and export day follow Warsaw across UTC midnight and seasonal offsets",()=>{
  assert.equal(snapshotCalendarDay("2026-09-04T22:49:00Z"),"2026-09-05");
  assert.equal(snapshotCalendarDay("2026-01-04T23:49:00Z"),"2026-01-05");
  assert.equal(snapshotCalendarDay("2026-01-04T22:49:00Z"),"2026-01-04");
  assert.equal(snapshotCalendarDay("2026-09-05T00:49:00+02:00"),"2026-09-05");
  assert.equal(snapshotCalendarDay("not-a-date"),null);
  assert.equal(snapshotCalendarDay(null),null);
});

function record(overrides: Partial<EventDetail> = {}): EventDetail {
  return {
    id: "fixture-1", kind: "incident", category: "earthquake", title: "Original title", description: "Source description",
    occurred_start: "2026-09-05T10:00:00Z", occurred_end: null, issued_at: null, source_updated_at: null,
    first_seen_at: "2026-09-05T12:00:00Z", last_seen_at: "2026-09-05T12:00:00Z", last_changed_at: "2026-09-05T12:00:00Z",
    valid_from: null, valid_to: null, countries: [], geometry: null, location_precision: "unknown", time_precision: "second",
    severity: 0, severity_label: "nieokreślona", severity_reason: "Brak skali", original_severity: null,
    lifecycle_status: "active", verification_status: "reported", anomaly_score: null,
    source_ids: ["usgs"], source_count: 1, independent_source_count: 1,
    source_url: "https://earthquake.usgs.gov/earthquakes/eventpage/fixture", tags: [], change_type: "initial_import",
    evidence: [], revisions: [], relations: [], ...overrides,
  };
}
function html(events: EventDetail[], props: Partial<React.ComponentProps<typeof SignalRows>> = {}): string {
  return renderToStaticMarkup(React.createElement(SignalRows, { events, selectedId: null, pinnedIds: [],
    onSelect: () => undefined, onPin: () => undefined, ...props }));
}

test("view search folds case and Polish diacritics in titles and descriptions", () => {
  const first = record({ id: "pl", title: "Żółty ALERT", description: "Łódź: ostrzeżenie o nawałnicy" });
  const second = record({ id: "other", title: "Other record" });
  for (const search of ["zolty lodz", "ŻÓŁTY ŁÓDŹ", "  alert  nawalnicy ", "ostrzezenie"]) {
    assert.deepEqual(filterViewEvents([first, second], { search }).map(({ id }) => id), ["pl"], search);
  }
});

test("view search accepts country codes and stable Polish country labels", () => {
  const records = [record({ id: "pl", countries: ["PL"] }), record({ id: "tr", countries: ["TR"] }),
    record({ id: "de", countries: ["DE"] }), record({ id: "unknown" })];
  for (const [search, id] of [["Polska", "pl"], ["PL", "pl"], ["turcja", "tr"], ["niemcy", "de"]]) {
    assert.deepEqual(filterViewEvents(records, { search }).map(({ id }) => id), [id], search);
  }
  assert.equal(filterViewEvents(records, { search: "Obszar nieustalony" }).length, 0);
});

test("source names are searchable across multiple channels without inventing confirmation", () => {
  const records = [record({ id: "nasa", source_ids: ["nasa_eonet", "gdacs"], source_count: 2 }),
    record({ id: "noaa", source_ids: ["noaa_swpc"] }), record({ id: "custom", source_ids: ["literal_source"] })];
  for (const [search, id] of [["nasa eonet", "nasa"], ["gdacs", "nasa"], ["NOAA SWPC", "noaa"], ["literal_source", "custom"]]) {
    assert.deepEqual(filterViewEvents(records, { search }).map(({ id }) => id), [id], search);
  }
  assert.equal(eventSourceNames(records[0]), "NASA EONET · GDACS");
  assert.equal(eventSourceNames(records[2]), "literal_source");
});

test("search is all-words across public fields and treats regex or executable-looking text literally", () => {
  const first = record({ id: "first", title: "HTTP.* 500 [incident]", description: "API calls affected", source_ids: ["github_status"], countries: ["PL"] });
  const second = record({ id: "second", title: "HTTP 500", description: "API affected" });
  const records = [first, second];
  assert.deepEqual(filterViewEvents(records, { search: "github polska calls" }).map(({ id }) => id), ["first"]);
  assert.deepEqual(filterViewEvents(records, { search: ".*" }).map(({ id }) => id), ["first"]);
  assert.deepEqual(filterViewEvents(records, { search: "[incident]" }).map(({ id }) => id), ["first"]);
  assert.equal(filterViewEvents(records, { search: "API missingword" }).length, 0);
  assert.equal(filterViewEvents(records, { search: "(a+)+$" }).length, 0);
  assert.equal(filterViewEvents(records, { search: "javascript:alert(1)" }).length, 0);
});

test("category, source and selected IDs intersect, including an explicit empty ID selection", () => {
  const records = [
    record({ id: "match", category: "internet", source_ids: ["github_status"] }),
    record({ id: "other-category", category: "cyber", source_ids: ["github_status"] }),
    record({ id: "other-source", category: "internet", source_ids: ["cloudflare_status"] }),
  ];
  assert.deepEqual(filterViewEvents(records, { category: "internet", sourceId: "github_status", onlyIds: ["match", "other-category"] }).map(({ id }) => id), ["match"]);
  assert.equal(filterViewEvents(records, { onlyIds: [] }).length, 0);
  assert.equal(filterViewEvents(records, { onlyIds: ["absent"] }).length, 0);
  assert.equal(filterViewEvents(records, { sourceId: "absent" }).length, 0);
  assert.equal(filterViewEvents(records, { search: "   " }).length, records.length);
});

test("filtering does not mutate the input order, nested values or frozen ID filters", () => {
  const first = record({ id: "z", title: "Żółty", countries: ["PL"], source_ids: ["gdacs", "nasa_eonet"] });
  const second = record({ id: "a", title: "Żółty" });
  const records = [first, second], before = JSON.stringify(records);
  for (const item of records) { Object.freeze(item.countries); Object.freeze(item.source_ids); Object.freeze(item); }
  Object.freeze(records);
  const ids = Object.freeze(["z", "a"]);
  const filtered = filterViewEvents(records, { search: "zolty", onlyIds: ids });
  assert.deepEqual(filtered.map(({ id }) => id), ["a", "z"]);
  assert.equal(JSON.stringify(records), before);
  assert.equal(filtered[0], second);
  assert.notEqual(filtered, records);
});

test("view sorting uses category-specific source clocks rather than import or occurrence clocks for every category", () => {
  const records = [
    record({ id: "earthquake", occurred_start: "2026-09-05T07:00:00Z", issued_at: "2026-09-05T11:55:00Z" }),
    record({ id: "disaster", category: "disaster", occurred_start: "2026-09-05T08:00:00Z" }),
    record({ id: "weather", category: "weather", valid_from: "2026-09-05T09:00:00Z", occurred_start: "2026-09-05T11:55:00Z" }),
    record({ id: "aviation", category: "aviation", valid_from: "2026-09-05T09:30:00Z", occurred_start: "2026-09-05T11:55:00Z" }),
    record({ id: "cyber", category: "cyber", issued_at: "2026-09-05T10:00:00Z", occurred_start: "2026-09-05T11:55:00Z" }),
    record({ id: "internet", category: "internet", issued_at: "2026-09-05T10:30:00Z" }),
    record({ id: "space-weather", category: "space_weather", issued_at: "2026-09-05T11:00:00Z" }),
    record({ id: "unknown", category: "cyber", issued_at: null, source_updated_at: "2026-09-05T11:59:00Z" }),
  ];
  assert.deepEqual(filterViewEvents(records, {}).map(({ id }) => id), ["space-weather", "internet", "cyber", "aviation", "weather", "disaster", "earthquake", "unknown"]);
});

test("source clock sorting compares offsets as instants and breaks exact time ties by stable ID", () => {
  const records = [
    record({ id: "z", occurred_start: "2026-09-05T11:00:00Z" }),
    record({ id: "a", occurred_start: "2026-09-05T13:00:00+02:00" }),
    record({ id: "newest", occurred_start: "2026-09-05T10:00:00-02:00" }),
  ];
  assert.deepEqual(filterViewEvents(records, {}).map(({ id }) => id), ["newest", "a", "z"]);
});

test("known dates before Unix epoch still sort before unknown source dates", () => {
  const records = [record({ id: "unknown", occurred_start: null }), record({ id: "known", occurred_start: "1965-01-01T00:00:00Z" })];
  assert.deepEqual(filterViewEvents(records, {}).map(({ id }) => id), ["known", "unknown"]);
});

test("rows escape source titles, reason text and URL-shaped source fields", () => {
  const title = '<img src=x onerror="alert(1)"> & "quoted"';
  const first = record({ title, source_url: "javascript:alert(1)", source_ids: ['<a href="javascript:alert(1)">bad</a>'] });
  const rendered = html([first], { reasons: { [first.id]: "<script>untrusted()</script>" } });
  assert.match(rendered, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(rendered, /&lt;script&gt;untrusted\(\)&lt;\/script&gt;/);
  assert.match(rendered, /&lt;a href=&quot;javascript:alert\(1\)&quot;&gt;/);
  assert.doesNotMatch(rendered, /<img|<script|<a\s|\shref="javascript:|\sonerror="/);
});

test("pin controls have explicit accessible states and pass the original event ID to the callback", () => {
  const first = record({ id: "stable-public-id", title: "Źródłowy tytuł" }), calls: string[] = [];
  const unpinned = PinButton({ event: first, pinned: false, onPin: (id) => calls.push(id) });
  const pinned = PinButton({ event: first, pinned: true, onPin: (id) => calls.push(id) });
  const firstMarkup = renderToStaticMarkup(unpinned), secondMarkup = renderToStaticMarkup(pinned);
  assert.match(firstMarkup, /aria-label="Przypnij: Źródłowy tytuł"/);
  assert.match(firstMarkup, /aria-pressed="false"/);
  assert.match(firstMarkup, /na tym urządzeniu/);
  assert.match(secondMarkup, /aria-label="Odepnij: Źródłowy tytuł"/);
  assert.match(secondMarkup, /aria-pressed="true"/);
  unpinned.props.onClick(); pinned.props.onClick();
  assert.deepEqual(calls, [first.id, first.id]);
});

test("rows display publication day precision without a fabricated hour and leave unknown source times unknown", () => {
  const dated = record({ category: "cyber", kind: "vulnerability_notice", issued_at: "2026-09-04T00:00:00Z", time_precision: "day", tags: ["date_only_utc_anchor"] });
  const rendered = html([dated]);
  assert.match(rendered, /<time dateTime="2026-09-04T00:00:00Z">04.09.2026<\/time>/);
  assert.match(rendered, /signal-time-basis">publikacja/);
  assert.doesNotMatch(rendered, />00:00|>02:00/);
  const unknown = html([{ ...dated, issued_at: null }]);
  assert.match(unknown, /<time>Nie ustalono<\/time>/);
  assert.doesNotMatch(unknown, /<time dateTime=/);
});

test("rows use validity start for aviation and distinguish missing map position from country labels", () => {
  const first = record({ category: "aviation", kind: "advisory", valid_from: "2026-09-04T08:00:00Z", countries: ["PL", "TR"],
    occurred_start: "2020-01-01T01:00:00Z", issued_at: "2022-01-01T01:00:00Z" });
  const rendered = html([first]);
  assert.match(rendered, /<time dateTime="2026-09-04T08:00:00Z">/);
  assert.match(rendered, /signal-time-basis">ważne od/);
  assert.match(rendered, /Polska, Turcja · bez pozycji/);
  assert.doesNotMatch(rendered, /dateTime="2020|dateTime="2022/);
});

test("empty, selected, pinned, cached and changed states remain distinct and precise", () => {
  assert.match(html([]), /Brak zapisu w podłączonych źródłach nie oznacza braku zdarzenia/);
  const first = record({ tags: ["cached_public_data"] });
  const rendered = html([first], { selectedId: first.id, pinnedIds: [first.id], changedIds: [first.id], compact: true });
  assert.match(rendered, /signal-row is-selected/);
  assert.match(rendered, /Zmieniony zapis/);
  assert.match(rendered, /starszy odczyt/);
  assert.match(rendered, /signal-index" aria-hidden="true">01/);
  assert.match(rendered, /aria-label="Odepnij:/);
  const added = html([first], { addedIds: [first.id], changedIds: [first.id] });
  assert.match(added, /Nowy w zestawie/);
  assert.doesNotMatch(added, /Zmieniony zapis/);
});

test("resolved incident rows say completed in the snapshot without implying an active incident", () => {
  const incident = record({ category: "internet", kind: "incident", title: "Resolved service interruption",
    issued_at: "2026-09-05T10:00:00Z", occurred_end: "2026-09-05T11:00:00Z", lifecycle_status: "expired" });
  const rendered = html([incident]);
  assert.match(rendered, /signal-lifecycle">Zakończone · stan w zestawie<\/span>/);
  assert.doesNotMatch(rendered, /signal-lifecycle">Aktywne|signal-lifecycle">Wygasłe/);
});
