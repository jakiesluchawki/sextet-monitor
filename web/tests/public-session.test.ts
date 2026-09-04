import assert from "node:assert/strict";
import test from "node:test";
import type { EventDetail, SourceStatus } from "../lib/contracts";
import type { PublicSnapshot } from "../lib/public-snapshot";
import {
  BASELINE_STORAGE_KEY, WATCH_STORAGE_KEY, MAX_BASELINE_BYTES, MAX_BASELINE_EVENTS,
  MAX_SHARE_HASH_LENGTH, MAX_WATCH_IDS, buildShareUrl, compareSnapshots, makeBaseline,
  parseSharedView, readBaseline, readWatchState, serializeSharedView, writeBaseline, writeWatchState,
  type PublicStorage, type SharedView,
} from "../lib/public-session";

const id = (n: number) => `10000000-0000-5000-8000-${n.toString(16).padStart(12, "0")}`;
const BEFORE = "2026-08-27T12:00:00Z";
const AFTER = "2026-08-27T13:00:00Z";

class MemoryStorage implements PublicStorage {
  readonly values = new Map<string, string>();
  writes = 0;
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); this.writes++; }
}

function event(overrides: Partial<EventDetail> = {}): EventDetail {
  return {
    id: id(1), kind: "incident", category: "earthquake", title: "Publiczny rekord testowy", description: "Opis testowy.",
    occurred_start: "2026-08-27T10:00:00Z", occurred_end: null, issued_at: "2026-08-27T10:01:00Z", source_updated_at: null,
    first_seen_at: "2026-08-27T11:01:00Z", last_seen_at: "2026-08-27T11:01:00Z", last_changed_at: "2026-08-27T11:01:00Z",
    valid_from: null, valid_to: null, countries: [], geometry: null, location_precision: "unknown", time_precision: "second",
    severity: 0, severity_label: "unknown", severity_reason: "", original_severity: null, lifecycle_status: "active",
    verification_status: "reported", anomaly_score: null, source_ids: ["usgs"], source_count: 1, independent_source_count: 1,
    source_url: "https://example.invalid/event", tags: [], change_type: "initial_import",
    evidence: [{
      id: "public-evidence", source_id: "usgs", source_name: "USGS", provider_record_id: "record-1",
      source_url: "https://example.invalid/event", retrieved_at: "2026-08-27T11:01:00Z", issued_at: "2026-08-27T10:01:00Z",
      source_updated_at: null, origins: ["usgs"], payload_hash: "source-payload", raw: null, raw_retained: false,
      attribution: "Fixture", license_url: null,
    }], revisions: [], relations: [], ...overrides,
  };
}

function snapshot(events: EventDetail[] = [event()], generatedAt = BEFORE): PublicSnapshot {
  const source: SourceStatus = {
    id: "usgs", name: "USGS", status: "ok", enabled: true, requires_key: false, last_attempt_at: BEFORE,
    last_success_at: BEFORE, newest_content_at: "2026-08-27T10:01:00Z", next_due_at: null, record_count: events.length,
    error: null, poll_interval_seconds: 300, coverage: "Test", license_name: "Public domain", license_url: null, attribution: "Fixture",
  };
  return { format: 1, version: "test", generated_at: generatedAt, sources: [source], events, limitations: [] };
}

test("shared views round-trip only explicit approved public fields", () => {
  const view: SharedView = { scope: "poland", hours: 72, view: "explore", category: "earthquake", sourceId: "usgs", eventId: id(1), search: "Łódź & alert + 2" };
  const hash = serializeSharedView({ ...view, access_token: "do-not-copy", privateApi: "http://localhost:8080" } as SharedView);
  assert.deepEqual(parseSharedView(hash), view);
  assert.ok(hash.startsWith("#scope=poland&hours=72&view=explore"));
  assert.ok(hash.includes("%26"));
  assert.doesNotMatch(hash, /access_token|do-not-copy|privateApi|localhost/);
  assert.equal(serializeSharedView({}), "");
});

test("shared parsing accepts no implicit dates, private sources, unknown enum values or non-UUID selection", () => {
  assert.deepEqual(parseSharedView("#scope=moon&hours=24.0&view=admin&eventId=private:123&category=military&sourceId=cloudflare_radar&token=secret"), {});
  assert.deepEqual(parseSharedView("#scope=europe&hours=168&view=briefing&category=space_weather&sourceId=noaa_swpc"), {
    scope: "europe", hours: 168, view: "briefing", category: "space_weather", sourceId: "noaa_swpc",
  });
  assert.deepEqual(parseSharedView(`#eventId=${id(11).toUpperCase()}`), { eventId: id(11) });
  assert.deepEqual(parseSharedView("#hours=024&scope=turkey"), { scope: "country:TR" });
});

test("malformed, ambiguous and oversized shared hashes fail closed", () => {
  for (const hash of ["scope=poland", "#scope=world&scope=poland", "#scope=world&%73cope=poland", "#search=%", "#search=%FF", "#search=%0x", `#${"x".repeat(MAX_SHARE_HASH_LENGTH)}`]) {
    assert.deepEqual(parseSharedView(hash), {}, hash.slice(0, 70));
  }
  assert.deepEqual(parseSharedView("#search=%00secret&scope=world"), { scope: "world" });
  assert.deepEqual(parseSharedView("#search=%E2%80%AEhidden&scope=world"), { scope: "world" });
  assert.deepEqual(parseSharedView(`#search=${"a".repeat(201)}&scope=world`), { scope: "world" });
  assert.deepEqual(parseSharedView("#search=++Warszawa++"), { search: "Warszawa" });
});

test("serializer enforces encoded hash budget while keeping safe view controls", () => {
  const hash = serializeSharedView({ scope: "poland", hours: 24, view: "overview", search: "漢".repeat(200) });
  assert.ok(hash.length <= MAX_SHARE_HASH_LENGTH);
  assert.equal(parseSharedView(hash).scope, "poland");
  assert.deepEqual(parseSharedView(serializeSharedView({ search: "hello\nsecret", sourceId: "usgs" })), { sourceId: "usgs" });
});

test("share URLs strip existing query and fragment instead of forwarding credentials or arbitrary parameters", () => {
  const result = buildShareUrl("https://example.invalid/sextet-monitor/?access_token=secret&api=http%3A%2F%2Flocalhost#token=secret", { scope: "poland", view: "briefing" });
  assert.equal(result, "https://example.invalid/sextet-monitor/#scope=poland&view=briefing");
  assert.equal(buildShareUrl("http://localhost:3180/", {}), "http://localhost:3180/");
  for (const url of ["javascript:alert(1)", "data:text/html,hello", "file:///private/file", "https://user:password@example.invalid/", "//example.invalid/", "/relative"]) {
    assert.throws(() => buildShareUrl(url, {}));
  }
});

test("shared countries round-trip with canonical codes and migrate old Turkey or duplicate Poland scopes", () => {
  for (const scope of ["country:JP", "country:FR", "country:TR", "country:XK", "country:GP"] as const) {
    const view: SharedView = { scope, hours: 72, view: "overview" };
    assert.deepEqual(parseSharedView(serializeSharedView(view)), view);
    assert.ok(serializeSharedView(view).includes("country%3A"));
  }
  assert.deepEqual(parseSharedView("#scope=country%3Atr&view=explore"), { scope: "country:TR", view: "explore" });
  assert.deepEqual(parseSharedView("#scope=turkey&hours=24"), { scope: "country:TR", hours: 24 });
  assert.deepEqual(parseSharedView("#scope=country%3Apl&hours=24"), { scope: "poland", hours: 24 });
  assert.equal(serializeSharedView({ scope: "country:PL" }), "#scope=poland");
  assert.equal(serializeSharedView({ scope: "turkey" } as unknown as SharedView), "#scope=country%3ATR");
});

test("country links reject unsupported, aggregate, malformed and injected codes without forwarding favorites", () => {
  for (const scope of ["country:ZZ", "country:EU", "country:UN", "country:UK", "country:XA", "country:US-CA", "country:__proto__", "country:PL,TR", "country:PL\n"]) {
    assert.deepEqual(parseSharedView(`#scope=${encodeURIComponent(scope)}&hours=24`), { hours: 24 }, scope);
    assert.equal(serializeSharedView({ scope } as SharedView), "", scope);
  }
  assert.deepEqual(parseSharedView("#scope=country%3AJP&favorites=TR,FR&country=PL"), { scope: "country:JP" });
  assert.equal(buildShareUrl("https://example.invalid/?favorites=PL,TR#scope=turkey", { scope: "country:JP" }), "https://example.invalid/#scope=country%3AJP");
});

test("watch persistence contains only bounded public UUID selection and schema metadata", () => {
  const storage = new MemoryStorage();
  assert.deepEqual(readWatchState(storage), { version: 1, ids: [], updatedAt: null });
  assert.equal(writeWatchState(storage, [id(2), id(1), id(2).toUpperCase()], Date.parse(BEFORE)), true);
  assert.deepEqual(readWatchState(storage), { version: 1, ids: [id(2), id(1)], updatedAt: "2026-08-27T12:00:00.000Z" });
  const stored = JSON.parse(storage.getItem(WATCH_STORAGE_KEY)!);
  assert.deepEqual(Object.keys(stored), ["version", "ids", "updatedAt"]);
  assert.equal(writeWatchState(storage, [], Date.parse(AFTER)), true);
  assert.deepEqual(readWatchState(storage).ids, []);
});

test("watch writes reject invalid IDs and cap overflow without replacing the existing selection", () => {
  const storage = new MemoryStorage();
  assert.equal(writeWatchState(storage, [id(1)]), true);
  const prior = storage.getItem(WATCH_STORAGE_KEY);
  for (const ids of [["private:record"], ["https://example.invalid"], Array.from({ length: MAX_WATCH_IDS + 1 }, (_, n) => id(n))]) {
    assert.equal(writeWatchState(storage, ids), false);
    assert.equal(storage.getItem(WATCH_STORAGE_KEY), prior);
  }
  assert.equal(writeWatchState(storage, [id(2)], NaN), false);
});

test("watch reads ignore corrupted, incompatible, duplicate or augmented storage", () => {
  const storage = new MemoryStorage();
  const valid = { version: 1, ids: [id(1)], updatedAt: BEFORE };
  for (const value of ["{", "x".repeat(5000), JSON.stringify({ ...valid, version: 2 }), JSON.stringify({ ...valid, raw: "unexpected" }), JSON.stringify({ ...valid, ids: [id(1), id(1).toUpperCase()] }), JSON.stringify({ ...valid, updatedAt: "2026-02-30T10:00:00Z" }), JSON.stringify({ ...valid, updatedAt: "2026-08-27T12:00:00" })]) {
    storage.values.set(WATCH_STORAGE_KEY, value);
    assert.deepEqual(readWatchState(storage), { version: 1, ids: [], updatedAt: null });
  }
});

test("watch updates stay ordered after equal millisecond writes or a backwards device clock", () => {
  const storage = new MemoryStorage();
  const now = Date.parse(BEFORE);
  assert.equal(writeWatchState(storage, [id(1)], now), true);
  assert.equal(writeWatchState(storage, [id(2)], now), true);
  assert.equal(Date.parse(readWatchState(storage).updatedAt!), now + 1);
  assert.equal(writeWatchState(storage, [id(3)], now - 10000), true);
  assert.equal(Date.parse(readWatchState(storage).updatedAt!), now + 2);
  const writes = storage.writes;
  assert.equal(writeWatchState(storage, [id(3)], now), true);
  assert.equal(storage.writes, writes, "unchanged selections do not trigger cross-tab storage churn");
});

test("disabled, blocked and quota-exhausted storage never throws or claims a successful write", () => {
  const blocked: PublicStorage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  const quota: PublicStorage = { getItem() { return null; }, setItem() { throw new Error("quota"); } };
  for (const storage of [null, blocked, quota]) {
    assert.deepEqual(readWatchState(storage).ids, []);
    assert.equal(readBaseline(storage), null);
    assert.equal(writeWatchState(storage, [id(1)]), false);
    assert.equal(writeBaseline(storage, makeBaseline(snapshot())), false);
  }
});

test("baseline serialization never stores event content, evidence URLs, coordinates or raw payloads", () => {
  const storage = new MemoryStorage();
  const baseline = makeBaseline(snapshot([event({ geometry: { type: "Point", coordinates: [21, 52] } })]));
  assert.equal(writeBaseline(storage, baseline), true);
  const serialized = storage.getItem(BASELINE_STORAGE_KEY)!;
  assert.doesNotMatch(serialized, /Publiczny|testowy|example|coordinates|payload|source_id|retrieved_at|title|geometry/);
  assert.equal(baseline.entries[0][0], id(1));
  assert.match(baseline.entries[0][1], /^[a-f0-9]{32}$/);
  assert.deepEqual(readBaseline(storage), baseline);
});

test("baseline rejects private-source or raw-bearing snapshots even when a caller skips the public loader", () => {
  const privateSource = snapshot();
  privateSource.sources[0].id = "cloudflare_radar";
  assert.throws(() => makeBaseline(privateSource), /publiczny/);
  const raw = snapshot();
  raw.events[0].evidence[0].raw = { private: "not-for-storage" };
  assert.throws(() => makeBaseline(raw), /publiczny/);
  assert.throws(() => makeBaseline(snapshot([event({ id: id(11) }), event({ id: id(11).toUpperCase() })])), /identyfikator/);
});

test("baseline reads and writes fail closed on malformed, future-schema or oversized records", () => {
  const storage = new MemoryStorage();
  const valid = makeBaseline(snapshot());
  const invalid = [
    { ...valid, version: 2 }, { ...valid, generatedAt: "2026-02-30T12:00:00Z" }, { ...valid, raw: "private" },
    { ...valid, generatedAt: new Date(Date.now() + 86400000).toISOString() },
    { ...valid, entries: [[id(1), "not-a-fingerprint"]] }, { ...valid, entries: [["private:id", "0".repeat(32)]] },
    { ...valid, entries: [valid.entries[0], valid.entries[0]] },
    { ...valid, entries: Array.from({ length: MAX_BASELINE_EVENTS + 1 }, (_, n) => [id(n), "0".repeat(32)]) },
  ];
  for (const value of invalid) {
    storage.values.set(BASELINE_STORAGE_KEY, JSON.stringify(value));
    assert.equal(readBaseline(storage), null);
    assert.equal(writeBaseline(storage, value as typeof valid), false);
  }
  for (const value of ["{", "x".repeat(MAX_BASELINE_BYTES + 1)]) {
    storage.values.set(BASELINE_STORAGE_KEY, value);
    assert.equal(readBaseline(storage), null);
  }
});

test("the maximum public snapshot count fits the compact baseline storage bound", () => {
  const storage = new MemoryStorage();
  const baseline = { version: 1 as const, generatedAt: BEFORE, entries: Array.from({ length: MAX_BASELINE_EVENTS }, (_, n): [string, string] => [id(n), "0".repeat(32)]) };
  assert.equal(writeBaseline(storage, baseline), true);
  assert.ok(storage.getItem(BASELINE_STORAGE_KEY)!.length < MAX_BASELINE_BYTES);
  assert.equal(readBaseline(storage)?.entries.length, MAX_BASELINE_EVENTS);
});

test("first visit does not call every imported record a new incident", () => {
  const result = compareSnapshots(snapshot(), null);
  assert.equal(result.status, "first_visit");
  assert.equal(result.comparedAt, null);
  assert.deepEqual(result.addedIds, []);
  assert.deepEqual(result.changedIds, []);
  assert.equal(result.missingCount, 0);
  assert.match(result.limitations.join(" "), /nie potwierdzony nowy incydent/);
});

test("a newer snapshot distinguishes added, meaningfully changed, unchanged and missing records", () => {
  const before = snapshot([event(), event({ id: id(2) }), event({ id: id(3) })]);
  const after = snapshot([event({ title: "Zmiana komunikatu" }), event({ id: id(2) }), event({ id: id(4) })], AFTER);
  const result = compareSnapshots(after, makeBaseline(before));
  assert.equal(result.status, "newer_snapshot");
  assert.equal(result.comparedAt, BEFORE);
  assert.equal(result.snapshotAt, AFTER);
  assert.deepEqual(result.addedIds, [id(4)]);
  assert.deepEqual(result.changedIds, [id(1)]);
  assert.equal(result.missingCount, 1);
  assert.match(result.limitations.join(" "), /nie oznacza zakończenia/);
});

test("reimport clocks, source freshness, raw payload hashes and cached transport labels are not event changes", () => {
  const before = snapshot();
  const after = structuredClone(before);
  after.generated_at = AFTER;
  after.sources[0].last_success_at = AFTER;
  after.sources[0].status = "partial";
  Object.assign(after.events[0], { first_seen_at: AFTER, last_seen_at: AFTER, last_changed_at: AFTER, change_type: "source_update", tags: ["cached_public_data"] });
  Object.assign(after.events[0].evidence[0], { retrieved_at: AFTER, payload_hash: "new-import-hash", source_snapshot_at: BEFORE });
  const result = compareSnapshots(after, makeBaseline(before));
  assert.equal(result.status, "newer_snapshot");
  assert.deepEqual(result.addedIds, []);
  assert.deepEqual(result.changedIds, []);
});

test("meaningful source content, dates, lifecycle, precision and geometry changes alter the fingerprint", () => {
  const baseline = makeBaseline(snapshot());
  const patches: Partial<EventDetail>[] = [
    { title: "Updated title" }, { description: "Updated description" }, { severity: 3 }, { lifecycle_status: "withdrawn" },
    { source_updated_at: "2026-08-27T12:30:00Z" }, { issued_at: "2026-08-27T12:00:00Z" },
    { occurred_start: "2026-08-27T10:30:00Z" }, { valid_to: "2026-08-28T00:00:00Z" },
    { countries: ["PL"] }, { geometry: { type: "Point", coordinates: [21, 52] } }, { location_precision: "country" },
    { time_precision: "day" }, { independent_source_count: 0 }, { tags: ["date_only_utc_anchor"] },
  ];
  for (const patch of patches) assert.deepEqual(compareSnapshots(snapshot([event(patch)], AFTER), baseline).changedIds, [id(1)], JSON.stringify(patch));
  const updatedEvidence = snapshot([event()], AFTER);
  updatedEvidence.events[0].evidence[0].source_updated_at = "2026-08-27T12:01:00Z";
  assert.deepEqual(compareSnapshots(updatedEvidence, baseline).changedIds, [id(1)]);
});

test("record order, unordered provenance, object key order and equivalent UTC offsets do not invent changes", () => {
  const before = snapshot([event({
    countries: ["PL", "TR"], tags: ["one", "two"], geometry: { type: "Point", coordinates: [21, 52] },
    original_severity: { scale: "magnitude", value: 3 },
  }), event({ id: id(2) })]);
  before.events[0].evidence[0].origins = ["usgs", "gdacs"];
  const after = structuredClone(before);
  after.generated_at = AFTER;
  Object.assign(after.events[0], {
    countries: ["TR", "PL"], tags: ["two", "one"], occurred_start: "2026-08-27T12:00:00+02:00",
    original_severity: { value: 3, scale: "magnitude" }, geometry: { coordinates: [21, 52], type: "Point" },
  });
  after.events[0].evidence[0].origins.reverse();
  after.events.reverse();
  assert.deepEqual(compareSnapshots(after, makeBaseline(before)).changedIds, []);
});

test("same publication is not a new snapshot just because it was read later or has another timezone representation", () => {
  const before = snapshot();
  const after = snapshot([event()], "2026-08-27T14:00:00+02:00");
  const result = compareSnapshots(after, makeBaseline(before));
  assert.equal(result.status, "same_snapshot");
  assert.deepEqual(result.changedIds, []);
  const conflicting = compareSnapshots(snapshot([event({ severity: 4 })]), makeBaseline(before));
  assert.equal(conflicting.status, "same_snapshot");
  assert.deepEqual(conflicting.changedIds, []);
  assert.match(conflicting.limitations.join(" "), /ten sam czas publikacji, lecz różną treść/);
});

test("out-of-order snapshots produce no reverse change claims and cannot regress persisted baseline", () => {
  const storage = new MemoryStorage();
  const newer = makeBaseline(snapshot([event({ title: "Newer" }), event({ id: id(2) })], AFTER));
  const older = makeBaseline(snapshot());
  assert.equal(writeBaseline(storage, newer), true);
  const result = compareSnapshots(snapshot(), readBaseline(storage));
  assert.equal(result.status, "out_of_order");
  assert.deepEqual(result.addedIds, []);
  assert.deepEqual(result.changedIds, []);
  assert.equal(result.missingCount, 0);
  assert.equal(writeBaseline(storage, older), false);
  assert.deepEqual(readBaseline(storage), newer);
});

test("equal-time baseline writes are idempotent only when content agrees; newer writes progress", () => {
  const storage = new MemoryStorage();
  const original = makeBaseline(snapshot());
  assert.equal(writeBaseline(storage, original), true);
  const writes = storage.writes;
  assert.equal(writeBaseline(storage, { ...original, generatedAt: "2026-08-27T14:00:00+02:00" }), true);
  assert.equal(storage.writes, writes);
  assert.equal(writeBaseline(storage, makeBaseline(snapshot([event({ title: "Ambiguous update" })]))), false);
  assert.deepEqual(readBaseline(storage), original);
  const newer = makeBaseline(snapshot([event({ title: "Known newer update" })], AFTER));
  assert.equal(writeBaseline(storage, newer), true);
  assert.deepEqual(readBaseline(storage), newer);
});
